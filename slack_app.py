import importlib
import json
import logging
import os
import re
import sys
import time
from copy import deepcopy as copy
from pprint import pprint

import requests
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from taiga import TaigaAPI

from editable_resources import forms, strings
from slack import blocks, block_formatters
from slack import misc as slack_misc
from slack import forms as slack_forms
from util import taigalink, tidyhq, const, taiga_links


def log_time(
    start_time: float, end_time: float, logger: logging.Logger, cause: str | None = None
) -> None:
    """Log the time taken for a command to return to Slack. Optionally log a likely cause for the delay if provided and the time taken is over 1000ms

    Sub 1000ms: Debug
    1000-2000ms: Info
    2000ms+: Warning

    """
    time_taken = end_time - start_time
    # Convert time taken to ms
    time_taken *= 1000
    if time_taken < 1000:
        logger.debug(f"Command took {time_taken:.2f}ms to return to slack")
    elif time_taken > 2000:
        logger.warning(f"Command took {time_taken:.2f}ms to return to slack")
        if cause:
            logger.warning(f"Likely due to: {cause}")
    elif time_taken > 1000:
        logger.info(f"Command took {time_taken:.2f}ms to return to slack")
        if cause:
            logger.info(f"Likely due to: {cause}")


# Set up logging
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler("app.log", mode="a"),
        logging.StreamHandler(sys.stdout),
    ],
)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
# Set slack bolt logging level to INFO to reduce noise when individual modules are set to debug
slack_logger = logging.getLogger("slack")
slack_logger.setLevel(logging.WARN)
setup_logger = logging.getLogger("setup")
logger = logging.getLogger("slack_app")
response_logger = logging.getLogger("response")

setup_logger.info("Application starting")

# Load config
try:
    with open("config.json") as f:
        config: dict = json.load(f)
except FileNotFoundError:
    setup_logger.error(
        "config.json not found. Create it using example.config.json as a template"
    )
    sys.exit(1)

if not config["taiga"].get("auth_token"):
    # Get auth token for Taiga
    # This is used instead of python-taiga's inbuilt user/pass login method since we also need to interact with the api directly
    auth_url = f"{config['taiga']['url']}/api/v1/auth"
    auth_data = {
        "password": config["taiga"]["password"],
        "type": "normal",
        "username": config["taiga"]["username"],
    }
    response = requests.post(
        auth_url,
        headers={"Content-Type": "application/json"},
        data=json.dumps(auth_data),
    )

    if response.status_code == 200:
        taiga_auth_token = response.json().get("auth_token")
    else:
        setup_logger.error(f"Failed to get auth token: {response.status_code}")
        sys.exit(1)

else:
    taiga_auth_token = config["taiga"]["auth_token"]

taigacon = TaigaAPI(host=config["taiga"]["url"], token=taiga_auth_token)

# Set up Taiga cache
taiga_cache = taigalink.setup_cache(
    config=config, taiga_auth_token=taiga_auth_token, taigacon=taigacon
)

# Write the cache to a file
# We never actually load this back in but it's useful for debugging
with open("taiga_cache.json", "w") as f:
    json.dump(taiga_cache, f)


# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
setup_logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)

# Set up slack app
app = App(token=config["slack"]["bot_token"], logger=slack_logger)

# Get the ID for our team via the API
auth_test = app.client.auth_test()
slack_team_id: str = auth_test["team_id"]
slack_bot_id = auth_test["bot_id"]
slack_app_id = "A081HR32FKK"
slack_workspace_title = app.client.team_info()["team"]["name"]

# Join every public channel the bot is not already in
client = WebClient(token=config["slack"]["bot_token"])
channels = client.conversations_list(types="public_channel")["channels"]

for channel in channels:
    # Skip archived channels
    if channel["is_archived"]:
        setup_logger.debug(f"Skipping archived channel {channel['name']}")
        continue
    # Check if the bot is already in the channel
    if channel["is_member"]:
        setup_logger.debug(f"Already in channel {channel['name']}")
        continue

    # Join the channel if not already in and not archived
    try:
        setup_logger.info(f"Joining channel {channel['name']}")
        client.conversations_join(channel=channel["id"])
    except SlackApiError as e:
        logger.error(f"Failed to join channel {channel['name']}: {e.response['error']}")

# Function naming scheme
# ignore_ - Acknowledge the event but do nothing
# handle_ - Acknowledge the event and do something
# modal_ - Open a modal
# submodal_ - Open a submodal


# Event listener for messages that mention the bot
@app.event("app_mention")
def ignore_app_mention(ack):
    """Dummy function to acknowledge the mention"""
    ack()


# Event listener for direct messages to the bot
@app.event("message")
def ignore_message(ack):
    """Ignore messages sent to the bot"""
    ack()


# Event listener for links being shared within slack
@app.event("link_shared")
def handle_link_unfurls(body):

    # Get the link details
    links = body["event"]["links"]

    link_info = {}
    final_info = {}

    for link in links:
        url = link["url"]
        project_id, item_type, item_id = taiga_links.get_info_from_url(
            url=url,
            taiga_auth_token=taiga_auth_token,
            taiga_cache=taiga_cache,
            config=config,
        )

        # Typically root URL
        if not project_id:
            final_info[url] = {
                "preview": {
                    "title": {
                        "type": "plain_text",
                        "text": f"Taiga | {slack_workspace_title} Issue Tracker",
                    }
                }
            }
            final_info[url]["blocks"] = block_formatters.inject_text(
                copy(blocks.text),
                f"This service is used to track issues related to the {slack_workspace_title}.",
            )
            continue

        project_id = int(project_id)  # type: ignore

        if not taiga_links.safe_to_send(
            config=config,
            project_id=project_id,
            slack_id=body["event"]["user"],
            channel_id=body["event"]["channel"],
            taiga_cache=taiga_cache,
            tidyhq_cache=tidyhq_cache,
        ):
            continue

        if item_type:
            # Convert the item type to a nice version
            item_types = {
                "us": "story",
                "task": "task",
                "issue": "issue",
                "kanban": "story",
                "issues": "issue",
                "epics": "epic",
            }
            if item_type == "us":
                item_type = "story"

        if not item_id:
            item_type_plural: str = item_types.get(item_type)  # type: ignore
            if item_type_plural[-1] == "y":
                item_type_plural = item_type_plural[:-1] + "ies"
            elif item_type_plural[-1] == "s":
                pass
            else:
                item_type_plural += "s"
            final_info[url] = {
                "preview": {
                    "title": {
                        "type": "plain_text",
                        "text": f"Issue Tracker | {taiga_cache['boards'][project_id]['name']} {item_type_plural}",
                    }
                }
            }
            final_info[url]["blocks"] = block_formatters.inject_text(
                copy(blocks.text),
                f"{taiga_cache['boards'][project_id]['name']} {item_type_plural}",
            )
            # Add accesory link button
            button = copy(blocks.button)
            button["text"]["text"] = ":eyes: View in app"
            button["action_id"] = f"tlink-{project_id}-{item_type}"
            button["url"] = (
                f"slack://app?team={slack_team_id}&id={slack_app_id}&tab=home"
            )
            final_info[url]["blocks"][-1]["accessory"] = button
        else:
            # Get the item details
            item = taigalink.get_info(
                taiga_auth_token=taiga_auth_token,
                config=config,
                item_id=item_id,
                item_type=item_type,
            )
            if not item:
                continue

            final_info[url] = {
                "preview": {
                    "title": {
                        "type": "plain_text",
                        "text": item["subject"],
                    }
                }
            }
            block_list = block_formatters.add_block([], blocks.text)
            block_list = block_formatters.inject_text(
                block_list,
                f"{taiga_cache['boards'][project_id]['name']} {item_type} | {item['subject']}",  # type: ignore
            )
            if item.get("description"):
                block_list[-1]["text"]["text"] += f"\n\n{item['description']}"

            # Add accesory link button
            button = copy(blocks.button)
            button["text"]["text"] = ":eyes: View in app"
            button["action_id"] = f"viewedit-{project_id}-{item_type}-{item_id}"
            block_list[-1]["accessory"] = button
            final_info[url]["blocks"] = block_list

            # Info fields
            block_list[-1]["fields"] = []

            block_list[-1]["fields"].append(
                {
                    "type": "mrkdwn",
                    "text": f"*Status:* {item['status_extra_info']['name']}",
                }
            )

            if item["assigned_to"]:
                # Check if the item has a assigned_users attribute
                if item.get("assigned_users", []) != []:
                    assigned_to_str = ", ".join(
                        [
                            taiga_cache["users"][user]["name"]
                            for user in item["assigned_users"]
                        ]
                    )
                else:
                    assigned_to_str = item["assigned_to_extra_info"][
                        "full_name_display"
                    ]

                block_list[-1]["fields"].append(
                    {
                        "type": "mrkdwn",
                        "text": f"*Assigned to:* {assigned_to_str}",
                    }
                )

            if item.get("watchers"):
                watcher_strs = []
                for watcher in item["watchers"]:
                    watcher_strs.append(taiga_cache["users"][watcher]["name"])
                if "Giant Robot" in watcher_strs:
                    watcher_strs.remove("Giant Robot")

                if watcher_strs:
                    block_list[-1]["fields"].append(
                        {
                            "type": "mrkdwn",
                            "text": f"*Watchers:* {', '.join(watcher_strs)}",
                        }
                    )

            if item.get("due_date"):
                due_datetime = datetime.strptime(item["due_date"], "%Y-%m-%d")
                days_until = (due_datetime - datetime.now()).days

                block_list[-1]["fields"].append(
                    {
                        "type": "mrkdwn",
                        "text": f"*Due:* {item['due_date']} ({days_until} days)",
                    }
                )

            if item.get("tags"):
                block_list[-1]["fields"].append(
                    {
                        "type": "mrkdwn",
                        "text": f"*Tags:* {', '.join([f'`{tag[0]}`' for tag in item['tags']])}",
                    }
                )

            # Issues have some extra fields
            if item_type == "issue":
                block_list[-1]["fields"].append(
                    {
                        "type": "mrkdwn",
                        "text": f"*Type:* {taiga_cache['boards'][project_id]['types'][item['type']]['name']}",
                    }
                )

                block_list[-1]["fields"].append(
                    {
                        "type": "mrkdwn",
                        "text": f"*Severity:* {taiga_cache['boards'][project_id]['severities'][item['severity']]['name']}",
                    }
                )

                block_list[-1]["fields"].append(
                    {
                        "type": "mrkdwn",
                        "text": f"*Priority:* {taiga_cache['boards'][project_id]['priorities'][item['priority']]['name']}",
                    }
                )

    # Add the icon url to every unfurl url that includes a preview section
    for url, info in final_info.items():
        if "preview" in info:
            final_info[url]["preview"][
                "image_url"
            ] = "https://replicate.delivery/pbxt/JF3foGR90vm9BXSEXNaYkaeVKHYbJPinmpbMFvRtlDpH4MMk/out-0-1.png"

    try:
        app.client.chat_unfurl(
            source=body["event"]["source"],
            unfurl_id=body["event"]["unfurl_id"],
            unfurls=final_info,
        )
    except SlackApiError as e:
        logger.error(f"Failed to unfurl links: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])
        pprint(final_info)


# Command listener for form selection
@app.shortcut("form-selector-shortcut")
@app.action("submit_form")
def modal_form_selector(ack, client, body):
    """Load the form selection modal"""
    start_time = time.time()
    logger.info(f"Received form selection shortcut or button")
    ack()
    user = body["user"]

    # Reload forms from file
    importlib.reload(forms)

    org_member = False

    # Check if the user is registered in TidyHQ
    tidyhq_id = tidyhq.map_slack_to_tidyhq(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=user["id"],
    )

    if tidyhq_id:
        # Get the type of membership held
        membership_type = tidyhq.get_membership_type(
            contact_id=tidyhq_id, tidyhq_cache=tidyhq_cache
        )
        if membership_type in ["Concession", "Full", "Sponsor"]:
            org_member = True

    # Render the blocks for the form selection modal
    block_list = block_formatters.render_form_list(
        form_list=forms.forms, member=org_member, emoji=slack_workspace_title
    )

    log_time(
        start_time,
        time.time(),
        response_logger,
        cause="Form selection modal generation",
    )

    # Open the modal
    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "form_selection",
                "title": {"type": "plain_text", "text": "Select a form"},
                "blocks": block_list,
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to open modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])
        pprint(block_list)


@app.action(re.compile(r"^tlink.*"))
def ignore_link_button_presses(ack):
    """Dummy function to ignore link button presses"""
    ack()


@app.action(re.compile(r"^form-open-.*"))
def submodal_specific_form(ack, body, client):
    """Open the selected form in a modal"""
    start_time = time.time()
    ack()
    form_name = body["actions"][0]["value"]

    # Reload forms from file
    importlib.reload(forms)

    # Get the form details
    form = forms.forms[form_name]

    # Convert the form questions to blocks
    block_list = block_formatters.questions_to_blocks(
        form["questions"],
        taigacon=taigacon,
        taiga_project=form.get("taiga_project"),
        taiga_cache=taiga_cache,
    )

    # Form title can only be 25 characters long
    if len(form["title"]) > 25:
        if not form.get("short_title"):
            form_title = form["title"][:25]
        else:
            form_title = form["short_title"]
    else:
        form_title = form["title"]

    log_time(start_time, time.time(), response_logger)

    # Open the modal
    try:
        client.views_push(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": f"form_submission-{form_name}",
                "title": {"type": "plain_text", "text": form_title},
                "blocks": block_list,
                "close": {
                    "type": "plain_text",
                    "text": "Cancel",
                },
                "submit": {
                    "type": "plain_text",
                    "text": form["action_name"],
                },
            },
        )
    except SlackApiError as e:
        logger.error(e)
        logger.error(f"Failed to push modal: {e.response['error']}")


@app.view(re.compile(r"^form_submission-.*"))
def handle_form_submissions(ack, body, logger):
    """Process form submissions"""
    start_time = time.time()

    # Get form name
    form_name = body["view"]["callback_id"].split("-")[-1]

    description, files = slack_forms.form_submission_to_description(
        submission=body, slack_app=app
    )
    project_id, taiga_type_id, taiga_severity_id = (
        slack_forms.form_submission_to_metadata(
            submission=body,
            taigacon=taigacon,
            taiga_cache=taiga_cache,
            form_name=form_name,
        )
    )

    # Reload forms from file
    importlib.reload(forms)

    form = forms.forms[form_name]

    if "taiga_type" in form and project_id:
        if taiga_type_id:
            # If the form doesn't have a type set via a question then we don't need to log that we're overriding it
            logger.debug("Overriding type with form-specific type")
        try:
            taiga_type_id = int(form["taiga_type"])
        except ValueError:
            # IDs are ints, if it's not then we need map from a name
            types = taiga_cache["boards"][project_id]["types"]
            for current_type_id, current_type in types.items():
                if current_type["name"].lower() == form["taiga_type"].lower():
                    taiga_type_id = current_type_id
                    break
            else:
                # If we get here then we didn't find the type
                logger.error(f"Failed to resolve type {form['taiga_type']} to an ID")
            logger.info(f"Resolved {form['taiga_type']} to {taiga_type_id}")

    # Get the user's name from their Slack ID
    user_info = app.client.users_info(user=body["user"]["id"])
    slack_name = user_info["user"]["profile"].get(
        "real_name_normalized", user_info["user"]["profile"]["display_name_normalized"]
    )

    issue_title = form["taiga_issue_title"].format(slack_name=slack_name)

    issue = taigalink.base_create_issue(
        taiga_auth_token=taiga_auth_token,
        project_id=project_id,
        config=config,
        subject=issue_title,
        description=description,
        type_id=taiga_type_id,
        severity_id=taiga_severity_id,
        tags=["slack", "form"],
    )

    if issue:
        # We only have a certain amount of time to acknowledge the submission. This way the user gets an error if the submission fails
        # and we get a log of which files are missing the next part fails
        ack()
    else:
        logger.error("Failed to create issue")
        return

    upload_success = True
    for filelink in files:
        downloaded_file = slack_misc.download_file(url=filelink, config=config)

        if not downloaded_file:
            logger.error(f"Failed to download file {filelink}")

        # Upload the file to Taiga
        upload = taigalink.attach_file(
            taiga_auth_token=taiga_auth_token,
            config=config,
            project_id=project_id,
            item_type="issue",
            item_id=issue["id"],
            url=filelink,
        )

        if not upload:
            logger.error(f"Failed to upload file {filelink}")
            upload_success = False

    # DM the user to let them know their form was submitted successfully
    message = strings.form_submission_success.format(form_name=form["title"])
    if not upload_success:
        message += "\n\n" + strings.file_upload_failure

    slack_misc.send_dm(slack_id=body["user"]["id"], message=message, slack_app=app)

    if len(files) > 0:
        log_time(
            start_time,
            time.time(),
            response_logger,
            cause="File upload, issue creation",
        )
    else:
        log_time(start_time, time.time(), response_logger, cause="Issue creation")


@app.view("form_submitted")
def ignore_form_submitted(ack):
    """Dummy function to ignore form submitted views"""
    ack()


@app.action(re.compile(r"^twatch.*"))
def handle_watch_button(ack, body):
    """Watch items on Taiga via a button

    Watch button values are a dict with:
    * project_id: The ID of the Taiga project the item is in
    * item_id: The ID of the item
    * type: The type of item (e.g. userstory, issue)
    * permalink: The permalink to the URL in Taiga, if available"""
    start_time = time.time()
    ack()
    watch_target = json.loads(body["actions"][0]["value"])

    global tidyhq_cache
    tidyhq_cache = tidyhq.fresh_cache(config=config, cache=tidyhq_cache)

    # Check if the Slack user can be mapped to a Taiga user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    # If the Slack user can't be mapped to a Taiga user the best we can do is tell them to watch it themselves
    if not taiga_id:
        message = """Sorry, I can't watch this item for you as I don't know who you are in Taiga\nIf you think this is an error please reach out to #it."""
        if watch_target.get("permalink"):
            message += f"\n\nYou can view the item yourself <{watch_target['permalink']}|here>."
        client.chat_postEphemeral(
            channel=body["channel"]["id"], user=body["user"]["id"], text=message
        )
        return

    # Get the item in Taiga

    # Translate the type field to an argument get_info can use
    type_to_arg = {
        "issue": "issue_id",
        "userstory": "story_id",
        "task": "task_id",
        # Add other types as needed
    }

    item_info = taigalink.get_info(
        taiga_auth_token=taiga_auth_token,
        config=config,
        **{type_to_arg.get(watch_target["type"], "story_id"): watch_target["item_id"]},
    )

    # Add a catch for get_info screwing up
    if not item_info:
        message = "Sorry, I'm having trouble accessing Taiga right now. Please try again later."
        if watch_target.get("permalink"):
            message += f"\n\nYou can view the item yourself <{watch_target['permalink']}|here>."
        client.chat_postEphemeral(
            channel=body["channel"]["id"], user=body["user"]["id"], text=message
        )
        return

    # Check if the user is already watching the item
    if int(taiga_id) in item_info["watchers"]:
        message = f"You're already watching this {watch_target['type']} in Taiga!"
        client.chat_postEphemeral(
            channel=body["channel"]["id"], user=body["user"]["id"], text=message
        )
        return

    # Add the user to the watchers list
    add_watcher_response = taigalink.watch(
        type_str=watch_target["type"],
        item_id=watch_target["item_id"],
        watchers=item_info["watchers"],
        taiga_id=taiga_id,
        taiga_auth_token=taiga_auth_token,
        config=config,
        version=item_info["version"],
    )

    if not add_watcher_response:
        message = "Sorry, I'm having trouble accessing Taiga right now. Please try again later."
        if watch_target.get("permalink"):
            message += f"\n\nYou can view the item yourself <{watch_target['permalink']}|here>."
        client.chat_postEphemeral(
            channel=body["channel"]["id"], user=body["user"]["id"], text=message
        )
        return

    message = f"You're now watching this {watch_target['type']} in Taiga!"
    client.chat_postEphemeral(
        channel=body["channel"]["id"], user=body["user"]["id"], text=message
    )

    log_time(
        start_time,
        time.time(),
        response_logger,
        cause="Item retrieval, watcher addition",
    )


@app.event("reaction_added")
def ignore_reaction_added_events(ack):
    """Dummy function to ignore emoji reactions to messages"""
    ack()


@app.event("app_home_opened")
def handle_app_home_opened_events(body, client, logger):
    """Regenerate the app home when it's opened by a user"""
    start_time = time.time()
    user_id = body["event"]["user"]

    # Get user details for more helpful console messages
    user_info = client.users_info(user=user_id)

    global tidyhq_cache
    tidyhq_cache = tidyhq.fresh_cache(config=config, cache=tidyhq_cache)

    slack_misc.push_home(
        user_id=user_id,
        config=config,
        tidyhq_cache=tidyhq_cache,
        taiga_cache=taiga_cache,
        taiga_auth_token=taiga_auth_token,
        slack_app=app,
    )

    log_time(
        start_time,
        time.time(),
        response_logger,
        cause="TidyHQ cache refresh, app home generation",
    )


@app.action(re.compile(r"^viewedit-.*"))
def modal_viewedit(ack, body):
    """Listen for view in app and view/edit actions"""
    start_time = time.time()

    # Retrieve action details if applicable
    value_string = body["actions"][0]["action_id"]

    # Backwards compatibility for old some old view buttons
    if "userstory" in value_string:
        value_string = value_string.replace("userstory", "story")

    # Sometimes we attach the view method to the action ID
    modal_method = "open"
    if value_string.count("-") == 4:
        modal_method = value_string.split("-")[-1]
        value_string = "-".join(value_string.split("-")[:-1])

    project_id, item_type, item_id = value_string.split("-")[1:]

    logger.info(f"Received view/edit for {item_type} {item_id} in project {project_id}")
    ack()

    # Attempt to map the Slack user to a Taiga user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    if not taiga_id:
        logger.error(f"Failed to map Slack user {body['user']['id']} to Taiga user")
        view_title = f"View {item_type}"
        edit = False
    elif taiga_id not in taiga_cache["boards"][int(project_id)]["members"]:
        logger.error(f"User {taiga_id} is not a member of project {project_id}")
        view_title = f"View {item_type}"
        edit = False

    else:
        view_title = f"View/edit {item_type}"
        edit = True

    # Confirm the user is allowed to view the item

    # Generate the blocks for the view/edit modal
    block_list = block_formatters.viewedit_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type=item_type,
        item_id=item_id,
        taiga_cache=taiga_cache,
        config=config,
        taiga_auth_token=taiga_auth_token,
        edit=edit,
    )

    if taiga_id:
        log_time(
            start_time, time.time(), response_logger, cause="View/edit modal generation"
        )
    else:
        log_time(
            start_time, time.time(), response_logger, cause="View modal generation"
        )

    if modal_method == "open":
        # Open the modal
        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": "finished_editing",
                    "title": {"type": "plain_text", "text": view_title},
                    "blocks": block_list,
                    "submit": {"type": "plain_text", "text": "Finish"},
                    "clear_on_close": True,
                },
            )
            logger.info(
                f"View/edit modal for {item_type} {item_id} in project {project_id} opened for {body['user']['id']} ({taigalink.name_mapper(taiga_id, taiga_cache)})"
            )
        except SlackApiError as e:
            logger.error(f"Failed to open modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])
            pprint(block_list)

    elif modal_method == "update":
        # Update the modal
        try:
            client.views_update(
                view_id=body["view"]["root_view_id"],
                view={
                    "type": "modal",
                    "callback_id": "finished_editing",
                    "title": {"type": "plain_text", "text": view_title},
                    "blocks": block_list,
                    "submit": {"type": "plain_text", "text": "Finish"},
                    "clear_on_close": True,
                },
            )
            logger.info(
                f"View/edit modal for {item_type} {item_id} in project {project_id} updated for {body['user']['id']}"
            )
        except SlackApiError as e:
            logger.error(f"Failed to update modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])
            pprint(block_list)

    elif modal_method == "push":

        # Push a new modal onto the stack
        try:
            client.views_push(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": "finished_editing",
                    "title": {"type": "plain_text", "text": view_title},
                    "blocks": block_list,
                    "submit": {"type": "plain_text", "text": "Finish"},
                    "clear_on_close": True,
                },
            )
            logger.info(
                f"View/edit modal for {item_type} {item_id} in project {project_id} pushed to {body['user']['id']}"
            )
        except SlackApiError as e:
            logger.error(f"Failed to update modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])
            pprint(block_list)


# Comment
@app.action(re.compile(r"^submit_comment-.*"))
def handle_comment_addition(ack, body, client):
    """Handle comment additions"""
    start_time = time.time()
    ack()

    # Give immediate feedback
    view = slack_misc.loading_button(body)

    try:
        client.views_update(view_id=body["view"]["id"], view=view)
        logger.info("Temporary loading button pushed")
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])

    user_id = body["user"]["id"]

    # Get the comment text
    # We've added some junk data to the block ID to make it unique (so it doesn't get prefilled)
    # Yes I know next/iter exists
    comment = body["view"]["state"]["values"]["comment_field"]
    comment = comment[list(comment.keys())[0]]["value"]

    # Check if the comment is empty
    if not comment or comment.isspace():
        logger.info("Comment is empty, ignoring")
        return

    # Get the item details from the action id
    project_id, item_type, item_id = body["actions"][0]["action_id"].split("-")[1:]

    # Post the comment to Taiga
    print(f"Posting comment {comment} to {item_type} {item_id} in project {project_id}")

    # Get the item direct from Taiga, this isn't cached since it changes so often
    if item_type == "task":
        item = taigacon.tasks.get(item_id)
    elif item_type in ["story", "userstory"]:
        item = taigacon.user_stories.get(item_id)
    elif item_type == "issue":
        item = taigacon.issues.get(item_id)

    # Add who the comment is from

    # Map to the appropriate Taiga user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=user_id,
    )

    if taiga_id:
        # Get the user's name from their Taiga ID
        taiga_user_info = taiga_cache["users"][taiga_id]
        poster_name = taiga_user_info["name"]
    else:
        poster_name = slack_misc.name_mapper(slack_id=user_id, slack_app=app)

    # Add byline
    comment = f"Posted from Slack by {poster_name}: {comment}"

    # Post the comment
    commenting = item.add_comment(comment)

    if not commenting:
        logger.info(
            f"Failed to add comment to {item_type} {item_id} in project {project_id} by {user_id}"
        )
        logger.info(":".join(comment.split(":")[1:]))
        return

    else:
        logger.info(
            f"Comment added to {item_type} {item_id} in project {project_id} by {user_id}"
        )
        logger.info(":".join(comment.split(":")[1:]))

    # Regenerate the view/edit modal
    block_list = block_formatters.viewedit_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type=item_type,
        item_id=item_id,
        taiga_cache=taiga_cache,
        config=config,
        taiga_auth_token=taiga_auth_token,
    )

    log_time(
        start_time,
        time.time(),
        response_logger,
        cause="Comment addition, view/edit modal regeneration",
    )

    # Push the modal
    try:
        client.views_update(
            view_id=body["view"]["root_view_id"],
            view={
                "type": "modal",
                "callback_id": "finished_editing",
                "title": {"type": "plain_text", "text": f"View/edit {item_type}"},
                "blocks": block_list,
                "submit": {"type": "plain_text", "text": "Finish"},
                "clear_on_close": True,
            },
        )
        logger.info(f"Updated view/edit modal for {item_type} {item_id} for {user_id}")
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.action(re.compile(r"^home_attach_files-.*"))
def submodal_attach_files(ack, body):
    """Open a modal to submit files for later attachment"""
    ack()

    # Get the item details from the action id
    project_id, item_type, item_id = body["actions"][0]["action_id"].split("-")[1:]

    block_list = []
    # Create upload field
    block_list = block_formatters.add_block(block_list, blocks.file_input)
    block_list[-1]["block_id"] = "upload_section"
    block_list[-1]["element"]["action_id"] = "upload_file"
    block_list[-1]["label"]["text"] = "Upload files"

    # Push a new modal
    try:
        client.views_push(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": f"submit_files-{project_id}-{item_type}-{item_id}",
                "title": {"type": "plain_text", "text": "Upload files"},
                "blocks": block_list,
                "submit": {"type": "plain_text", "text": "Attach"},
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.action(re.compile(r"^view_tasks-.*"))
def submodal_tasks(ack, body):
    """Push a modal to view tasks attached to a specific user story"""
    start_time = time.time()
    ack()

    value_string = body["actions"][0]["action_id"]
    story_id = value_string.split("-")[1]

    # Get the tasks for the user story
    tasks = taigalink.get_tasks(
        config=config,
        taiga_auth_token=taiga_auth_token,
        exclude_done=False,
        story_id=story_id,
        filters={},
    )

    # Attempt to identify the user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    edit = False
    if taiga_id and taiga_id in taiga_cache["boards"][tasks[0]["project"]]["members"]:
        edit = True

    block_list = block_formatters.format_tasks_modal_blocks(
        task_list=tasks,
        edit=edit,
        taiga_cache=taiga_cache,
    )

    log_time(start_time, time.time(), response_logger, cause="Task retrieval")

    # Push a new modal
    try:
        client.views_push(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "view_tasks",
                "title": {"type": "plain_text", "text": "View Tasks"},
                "close": {"type": "plain_text", "text": "Back"},
                "blocks": block_list,
            },
        )
        logger.info(f"Pushed tasks modal for user story {story_id}")
        logger.info(f"Task modal for story {story_id} pushed for {body['user']['id']}")
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view(re.compile(r"^submit_files-.*"))
def handle_submitted_files(ack, body):
    """Take the submitted files, uploads them to Taiga and updates the view/edit modal"""
    start_time = time.time()
    ack()
    files = body["view"]["state"]["values"]["upload_section"]["upload_file"]["files"]

    if not files:
        return

    # Get the item details from the action id
    project_id, item_type, item_id = body["view"]["callback_id"].split("-")[1:]

    # Upload the files to Taiga
    for file in files:
        file_url = file["url_private"]
        upload = taigalink.attach_file(
            taiga_auth_token=taiga_auth_token,
            config=config,
            project_id=project_id,
            item_type=item_type,
            item_id=item_id,
            url=file_url,
        )

        if not upload:
            logger.error(f"Failed to upload file {file_url}")

    # Unlike trigger IDs (3s expiry) we seem to be able to update the view as required

    block_list = block_formatters.viewedit_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type=item_type,
        item_id=item_id,
        taiga_cache=taiga_cache,
        config=config,
        taiga_auth_token=taiga_auth_token,
    )

    log_time(
        start_time,
        time.time(),
        response_logger,
        cause=f"File upload ({len(files)} files), view/edit modal regeneration",
    )

    # Push the modal
    logging.info("Refreshing root view modal")
    try:
        client.views_update(
            view_id=body["view"]["root_view_id"],
            view={
                "type": "modal",
                "callback_id": "finished_editing",
                "title": {"type": "plain_text", "text": f"View/edit {item_type}"},
                "blocks": block_list,
                "submit": {"type": "plain_text", "text": "Finish"},
                "clear_on_close": True,
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")


@app.action(re.compile(r"^edit_info-.*"))
def submodal_edit_info(ack, body):
    """Open a modal to edit the details of an item"""
    start_time = time.time()
    ack()

    # Get the item details from the action id
    project_id, item_type, item_id = body["actions"][0]["action_id"].split("-")[1:]

    block_list = block_formatters.edit_info_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type=item_type,
        item_id=item_id,
        taiga_cache=taiga_cache,
    )

    log_time(start_time, time.time(), response_logger, cause="Edit modal generation")

    # Push the modal
    logging.info("Opening new modal")
    try:
        client.views_push(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": f"edited_info-{project_id}-{item_type}-{item_id}",
                "title": {"type": "plain_text", "text": f"Edit {item_type}"},
                "blocks": block_list,
                "submit": {"type": "plain_text", "text": "Update"},
                "close": {"type": "plain_text", "text": "Cancel"},
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view(re.compile(r"^edited_info-.*"))
def handle_edited_info(ack, body):
    """Update the details of an item"""
    start_time = time.time()
    ack()

    # Get the item details from callback id
    project_id, item_type, item_id = body["view"]["callback_id"].split("-")[1:]

    # Get the item from Taiga, this isn't cached since it changes so often
    if item_type == "task":
        item = taigacon.tasks.get(item_id)
    elif item_type == "story":
        item = taigacon.user_stories.get(item_id)
    elif item_type == "issue":
        item = taigacon.issues.get(item_id)

    for field in body["view"]["state"]["values"]:
        data = body["view"]["state"]["values"][field][field]

        # Patching everything at the same time didn't seem to work

        if field == "subject":
            if data["value"] != item.subject and data["value"]:
                if data["value"].strip() != "":
                    logger.info(
                        f"Updating subject from {item.subject} to {data['value']}"
                    )
                    item.patch(
                        fields=["subject"], subject=data["value"], version=item.version
                    )

        elif field == "description":
            if not data["value"]:
                data["value"] = ""
            if data["value"] != item.description:
                logger.info(
                    f"Updating description from {item.description} to {data['value']}"
                )
                item.patch(
                    fields=["description"],
                    description=data["value"],
                    version=item.version,
                )
        elif field == "due_date":
            if data["selected_date"] != item.due_date:
                logger.info(
                    f"Updating due date from {item.due_date} to {data['selected_date']}"
                )
                item.patch(
                    fields=["due_date"],
                    due_date=data["selected_date"],
                    version=item.version,
                )
        elif field == "assigned_to":
            assigned = data["selected_option"]["value"]
            if int(assigned) != item.assigned_to:
                logger.info(f"Updating assigned from {item.assigned_to} to {assigned}")
                item.patch(
                    fields=["assigned_to"],
                    assigned_to=int(assigned),
                    version=item.version,
                )
        elif field == "watchers":
            current_watchers = item.watchers
            watchers = [int(watcher["value"]) for watcher in data["selected_options"]]
            remove_watchers = [
                int(watcher)
                for watcher in item.watchers
                if watcher not in watchers
                and watcher != item.owner
                and watcher != item.assigned_to
                and watcher != 6
            ]
            add_watchers = [
                int(watcher) for watcher in watchers if watcher not in item.watchers
            ]
            if remove_watchers:
                logger.info(f"Removing watchers {remove_watchers}")
                current_watchers = [
                    watcher
                    for watcher in current_watchers
                    if watcher not in remove_watchers
                ]
            if add_watchers:
                logger.info(f"Adding watchers {add_watchers}")
                current_watchers = current_watchers + add_watchers
            logging.info(
                f"Updating watchers from {item.watchers} to {current_watchers}"
            )
            item.patch(fields=["watchers"], watchers=watchers, version=item.version)
        elif field == "status":
            status = data["selected_option"]["value"]
            if int(status) != item.status:
                logger.info(f"Updating status from {item.status} to {status}")
                item.patch(fields=["status"], status=int(status), version=item.version)

        # Issue specific fields
        elif field == "type":
            type_id = data["selected_option"]["value"]
            if int(type_id) != item.type:
                logger.info(f"Updating type from {item.type} to {type_id}")
                item.patch(fields=["type"], type=int(type_id), version=item.version)
        elif field == "severity":
            severity_id = data["selected_option"]["value"]
            if int(severity_id) != item.severity:
                logger.info(f"Updating severity from {item.severity} to {severity_id}")
                item.patch(
                    fields=["severity"], severity=int(severity_id), version=item.version
                )
        elif field == "priority":
            priority = data["selected_option"]["value"]
            if int(priority) != item.priority:
                logger.info(f"Updating priority from {item.priority} to {priority}")
                item.patch(
                    fields=["priority"], priority=int(priority), version=item.version
                )

    # Update view/edit modal
    block_list = block_formatters.viewedit_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type=item_type,
        item_id=item_id,
        taiga_cache=taiga_cache,
        config=config,
        taiga_auth_token=taiga_auth_token,
    )

    try:
        client.views_update(
            view_id=body["view"]["root_view_id"],
            view={
                "type": "modal",
                "callback_id": "finished_editing",
                "title": {"type": "plain_text", "text": f"View/edit {item_type}"},
                "blocks": block_list,
                "submit": {"type": "plain_text", "text": "Finish"},
                "clear_on_close": True,
            },
        )
        logger.info(
            f"Updated view/edit modal for {item_type} {item_id} for {body['user']['id']}"
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])

    log_time(
        start_time,
        time.time(),
        response_logger,
        cause=f"Item update ({len(body['view']['state']['values'])} fields)",
    )


@app.view("finished_editing")
def ignore_finished_editing(ack):
    """Acknowledge the view submission"""
    ack()


@app.action(re.compile(r"^complete-.*"))
def handle_complete_item(ack, body, client):
    """Mark an item as complete"""
    start_time = time.time()
    ack()

    # Give immediate feedback
    view = slack_misc.loading_button(body)

    try:
        client.views_update(view_id=body["view"]["id"], view=view)
        logger.info("Temporary loading button pushed")
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])

    # Get the item details from the action ID
    project_id, item_type, item_id, status_id = body["actions"][0]["action_id"].split(
        "-"
    )[1:]

    # Get the item from Taiga
    item = taigalink.get_info(
        taiga_auth_token=taiga_auth_token,
        config=config,
        item_id=item_id,
        item_type=item_type,
    )
    if not item:
        logger.error(f"Failed to get item {item_type} {item_id}")
        return

    complete = taigalink.mark_complete(
        config=config,
        taiga_auth_token=taiga_auth_token,
        item_id=item_id,
        item_type=item_type,
        item=item,
        status_id=status_id,
        taiga_cache=taiga_cache,
    )

    if not complete:
        logger.error(f"Failed to mark {item_type} {item_id} as complete")
        return

    if item_type == "task" and body["view"]["title"]["text"] == "View Tasks":
        # Get the tasks for the modal
        tasks = taigalink.get_tasks(
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=False,
            story_id=item["user_story"],
            filters={},
        )

        # Regenerate the task view modal
        block_list = block_formatters.format_tasks_modal_blocks(
            task_list=tasks,
            taiga_cache=taiga_cache,
        )

        log_time(
            start_time,
            time.time(),
            response_logger,
            cause="Task completion, task view modal regeneration",
        )

        # Update the current modal
        try:
            client.views_update(
                view_id=body["view"]["id"],
                view={
                    "type": "modal",
                    "callback_id": "view_tasks",
                    "title": {"type": "plain_text", "text": "View Tasks"},
                    "close": {"type": "plain_text", "text": "Back"},
                    "blocks": block_list,
                },
            )
            logger.info(
                f"Task modal for story {item['user_story']} updated for {body['user']['id']}"
            )
        except SlackApiError as e:
            logger.error(f"Failed to push modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])

        # Update the view/edit modal
        block_list = block_formatters.viewedit_blocks(
            taigacon=taigacon,
            project_id=project_id,
            item_type="story",
            item_id=item["user_story"],
            taiga_cache=taiga_cache,
            config=config,
            taiga_auth_token=taiga_auth_token,
        )

    elif body["view"]["title"]["text"].startswith("Edit"):
        # We're in the edit modal

        # Regenerate the edit modal
        block_list = block_formatters.edit_info_blocks(
            taigacon=taigacon,
            project_id=project_id,
            item_type=item_type,
            item_id=item_id,
            taiga_cache=taiga_cache,
        )

        try:
            client.views_update(
                view_id=body["view"]["id"],
                view={
                    "type": "modal",
                    "callback_id": f"edited_info-{project_id}-{item_type}-{item_id}",
                    "title": {"type": "plain_text", "text": f"Edit {item_type}"},
                    "blocks": block_list,
                    "submit": {"type": "plain_text", "text": "Update"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                },
            )
            logger.info(
                f"Edit modal for {item_type} {item_id} in project {project_id} updated for {body['user']['id']}"
            )
        except SlackApiError as e:
            logger.error(f"Failed to push modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])

        # Update the root view/edit modal
        block_list = block_formatters.viewedit_blocks(
            taigacon=taigacon,
            project_id=project_id,
            item_type=item_type,
            item_id=item_id,
            taiga_cache=taiga_cache,
            config=config,
            taiga_auth_token=taiga_auth_token,
        )

        try:
            client.views_update(
                view_id=body["view"]["root_view_id"],
                view={
                    "type": "modal",
                    "callback_id": "finished_editing",
                    "title": {"type": "plain_text", "text": f"View/edit {item_type}"},
                    "blocks": block_list,
                    "submit": {"type": "plain_text", "text": "Finish"},
                    "clear_on_close": True,
                },
            )
        except SlackApiError as e:
            logger.error(f"Failed to push modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])

    else:
        # Update the view/edit modal
        block_list = block_formatters.viewedit_blocks(
            taigacon=taigacon,
            project_id=project_id,
            item_type=item_type,
            item_id=item_id,
            taiga_cache=taiga_cache,
            config=config,
            taiga_auth_token=taiga_auth_token,
        )

    try:
        client.views_update(
            view_id=body["view"]["root_view_id"],
            view={
                "type": "modal",
                "callback_id": "finished_editing",
                "title": {"type": "plain_text", "text": f"View/edit {item_type}"},
                "blocks": block_list,
                "submit": {"type": "plain_text", "text": "Finish"},
                "clear_on_close": True,
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.action(re.compile(r"^promote_issue-.*"))
def handle_promote_issue(ack, body, client, respond):
    """Promote an issue to a user story"""
    start_time = time.time()
    ack()
    logger.info("Received promote issue action")

    # Get the item details from the action ID
    project_id, item_type, item_id = body["actions"][0]["action_id"].split("-")[1:]

    # Attempt to map the Slack user to a Taiga user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    if not taiga_id:
        logger.error(f"Failed to map Slack user {body['user']['id']} to Taiga user")
        app.client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=body["user"]["id"],
            text="Sorry, I can't promote this issue to a user story as your Slack account is not linked to a Taiga account.\nIf you think this is an error please reach out to #it.",
        )
        return

    # Check that the Taiga user is a member of the project
    if not taigalink.check_project_membership(
        taiga_cache=taiga_cache, project_id=project_id, taiga_id=taiga_id
    ):
        logger.error(
            f"Taiga user {taigalink.name_mapper(taiga_id, taiga_cache)} is not a member of project {project_id}"
        )
        app.client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=body["user"]["id"],
            text="Sorry, I can't promote this issue to a user story as you are not a member of the project it's attached to.\nIf you think this is an error please reach out to #it.",
        )
        return

    # Promote the issue
    story_id = taigalink.promote_issue(
        config=config,
        taiga_auth_token=taiga_auth_token,
        issue_id=item_id,
    )

    if not story_id:
        logger.error(f"Failed to promote issue {item_id}")
        return

    log_time(start_time, time.time(), response_logger, cause="Issue promotion")

    # Check if we're in a message or modal
    if "view" in body:
        # Update the view/edit modal
        block_list = block_formatters.viewedit_blocks(
            taigacon=taigacon,
            project_id=project_id,
            item_type="story",
            item_id=story_id,
            taiga_cache=taiga_cache,
            config=config,
            taiga_auth_token=taiga_auth_token,
        )

        try:
            client.views_update(
                view_id=body["view"]["root_view_id"],
                view={
                    "type": "modal",
                    "callback_id": "finished_editing",
                    "title": {"type": "plain_text", "text": f"View/edit story"},
                    "blocks": block_list,
                    "submit": {"type": "plain_text", "text": "Finish"},
                    "clear_on_close": True,
                },
            )
        except SlackApiError as e:
            logger.error(f"Failed to push modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])

    else:
        if story_id:
            respond(f"Issue promoted to user story by <@{body['user']['id']}>")

        else:
            # Send an ephemeral message
            client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=body["user"]["id"],
                text=f"Issue already promoted (or can't be found)",
            )


@app.action(re.compile(r"^view_attachments-.*"))
def submodal_view_attachments(ack, body):
    ack()
    start_time = time.time()

    project_id, item_type, item_id = body["actions"][0]["action_id"].split("-")[1:]

    # Get attachments
    if item_type in ["story", "userstory"]:
        attachments = taigacon.user_story_attachments.list(
            project=project_id, object_id=item_id
        )
    if item_type == "issue":
        attachments = taigacon.issue_attachments.list(
            project=project_id, object_id=item_id
        )
    if item_type == "task":
        attachments = taigacon.task_attachments.list(
            project=project_id, object_id=item_id
        )

    # Generate blocks to display attachments
    block_list = block_formatters.format_attachments(attachments)

    log_time(
        start_time,
        time.time(),
        response_logger,
        cause="Attachment retrieval, attachment modal generation",
    )

    # Push a new modal
    try:
        client.views_push(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": f"view_attachments-{project_id}-{item_type}-{item_id}",
                "title": {"type": "plain_text", "text": "View Attachments"},
                "close": {"type": "plain_text", "text": "Back"},
                "blocks": block_list,
            },
        )
        logger.info(
            f"Pushed attachments modal for {item_type} {item_id} in project {project_id} to {body['user']['id']}"
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])
        pprint(block_list)


@app.action("create_item")
def modal_create_item(ack, body, client):
    """Bring up a modal that allows the user to select the item type and what project to create it in"""
    ack()

    user_id = body["user"]["id"]
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=user_id,
    )
    # We don't need to be particularly verbose here as this button _should_ only be presented to Taiga users.
    if not taiga_id:
        logger.error(f"Failed to map Slack user {user_id} to Taiga user")
        return

    selector_blocks = block_formatters.new_item_selector_blocks(
        taiga_id=taiga_id, taiga_cache=taiga_cache
    )

    # Open the modal
    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "new_item",
                "title": {"type": "plain_text", "text": "Create a new item"},
                "blocks": selector_blocks,
                "submit": {"type": "plain_text", "text": "Next"},
            },
        )
        logger.info(f"Opened new item select modal for {user_id}")

    except SlackApiError as e:
        logger.error(f"Failed to open modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.shortcut("create-from-message")
def modal_create_from_message(ack, body):
    """Bring up a modal that allows the user to select the item type and what project to create it in.

    Sets the initial value of the description field to the message"""
    ack()

    # Retrieve message particulars
    message = body["message"]["blocks"][0]["elements"][0]["elements"][0]["text"]
    author_id = body["message"]["user"]
    author_name = slack_misc.name_mapper(slack_id=author_id, slack_app=app)

    # Attempt to map the Slack user to a Taiga user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    if not taiga_id:
        logger.error(f"Failed to map Slack user {body['user']['id']} to Taiga user")

        slack_misc.send_dm(
            slack_app=app,
            slack_id=body["user"]["id"],
            message="Sorry, I can't create a new item as your Slack account is not linked to a Taiga account.\nIf you think this is an error please reach out to #it.",
        )
        return

    # Quote the message
    description = ""
    for line in message.split("\n"):
        description += f"> {line}\n"

    # Remove the last newline
    description = description[:-1]

    # Add the author to the message

    # Figure out the number of characters required to add the author and truncate if required
    byline = f"\n> \n> ~ {author_name} ({author_id}) on Slack"
    if len(description) + len(byline) > 2980:
        description = description[: 2990 - len(byline)]
        description += "...message truncated"
    description += byline

    # Generate blocks
    selector_blocks = block_formatters.new_item_selector_blocks(
        taiga_id=taiga_id, taiga_cache=taiga_cache, description=description
    )

    # Open the modal
    try:
        app.client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "new_item",
                "title": {"type": "plain_text", "text": "Create a new item"},
                "blocks": selector_blocks,
                "submit": {"type": "plain_text", "text": "Next"},
            },
        )
        logger.info(f"Opened new item select modal for {body['user']['id']}")
    except SlackApiError as e:
        logger.error(f"Failed to open modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view_submission("new_item")
def submodal_new_item(ack, body, client):
    """Open a modal to create a new item"""
    ack()

    project_id = body["view"]["state"]["values"]["project"]["project"][
        "selected_option"
    ]["value"]
    item_type = body["view"]["state"]["values"]["item_type"]["item_type"][
        "selected_option"
    ]["value"]
    if "description" in body["view"]["state"]["values"]:
        description = body["view"]["state"]["values"]["description"]["description"][
            "value"
        ]

    # Check for private metadata
    description = ""
    # TODO: Get description from not the private metadata

    logging.info(f"Creating new {item_type} in project {project_id}")

    edit_blocks = block_formatters.edit_info_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type=item_type,
        item_id="0",
        taiga_cache=taiga_cache,
        new=True,
        description=description,
    )

    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": f"write_item-{project_id}-{item_type}-0",
                "title": {"type": "plain_text", "text": f"Create new {item_type}"},
                "blocks": edit_blocks,
                "submit": {"type": "plain_text", "text": f"Create {item_type}"},
                "close": {"type": "plain_text", "text": "Cancel"},
            },
        )
        logger.info(f"Opened new item creation modal for {body['user']['id']}")
    except SlackApiError as e:
        logger.error(f"Failed to open modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view_submission(re.compile(r"^write_item-.*"))
def handle_write_item(ack, body, client):
    """Write the new item to Taiga"""
    start_time = time.time()
    ack()

    # Get the project_id and item type from the callback id
    project_id, item_type, story_id = body["view"]["callback_id"].split("-")[1:]

    new_item_details = {}

    if story_id != "0":
        new_item_details["user_story"] = int(story_id)

    for field in body["view"]["state"]["values"]:
        data = body["view"]["state"]["values"][field][field]

        # Patching everything at the same time didn't seem to work

        if field == "subject":
            new_item_details["subject"] = data["value"]

        elif field == "description":
            if data["value"]:
                if data["value"].strip() != "":
                    new_item_details["description"] = data["value"]

        elif field == "due_date":
            if data["selected_date"]:
                new_item_details["due_date"] = data["selected_date"]

        elif field == "assigned_to":
            if data["selected_option"]:
                assigned = data["selected_option"]["value"]
                new_item_details["assigned_to"] = int(assigned)

        elif field == "watchers":
            new_item_details["watchers"] = [
                int(watcher["value"]) for watcher in data["selected_options"]
            ]

        elif field == "status":
            status = data["selected_option"]["value"]
            new_item_details["status"] = int(status)

        # Issue specific fields
        elif field == "type":
            type_id = data["selected_option"]["value"]
            new_item_details["type"] = int(type_id)

        elif field == "severity":
            severity_id = data["selected_option"]["value"]
            new_item_details["severity"] = int(severity_id)

        elif field == "priority":
            priority = data["selected_option"]["value"]
            new_item_details["priority"] = int(priority)

    # Create the new item
    item_id, version = taigalink.create_item(
        config=config,
        taiga_auth_token=taiga_auth_token,
        project_id=project_id,
        item_type=item_type,
        **new_item_details,
    )

    if not item_id:
        logger.error(f"Failed to create new {item_type}")
        return

    if body["view"]["title"]["text"] == "Create new task":
        # Regenerate the story view/edit modal as tasks are only created from there
        block_list = block_formatters.viewedit_blocks(
            taigacon=taigacon,
            project_id=project_id,
            item_type="story",
            item_id=story_id,
            taiga_cache=taiga_cache,
            config=config,
            taiga_auth_token=taiga_auth_token,
        )

        try:
            client.views_update(
                view_id=body["view"]["root_view_id"],
                view={
                    "type": "modal",
                    "callback_id": "finished_editing",
                    "title": {"type": "plain_text", "text": f"View/edit story"},
                    "blocks": block_list,
                    "submit": {"type": "plain_text", "text": "Finish"},
                    "clear_on_close": True,
                },
            )
        except SlackApiError as e:
            logger.error(f"Failed to push modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])

    # This process typically takes longer than the 3s we have to respond to the view submission
    # So we can't open the view/edit modal
    # Instead we'll send a DM to the user to let them know the item has been created

    block_list = []
    block_list = block_formatters.add_block(block_list, blocks.text)
    block_list[-1]["text"][
        "text"
    ] = f"Created new {item_type}: {new_item_details['subject']}"

    # Add a button to view the new item
    button = copy(blocks.button)
    button["text"]["text"] = "View/Edit"
    button["action_id"] = f"viewedit-{project_id}-{item_type}-{item_id}"
    block_list[-1]["accessory"] = button

    slack_misc.send_dm(
        slack_app=app,
        slack_id=body["user"]["id"],
        message=f"Created new {item_type}: {new_item_details['subject']}",
        blocks=block_list,
    )


@app.action("filter_home_modal")
def modal_home_filter(ack, body):
    """Open a modal to filter the home view"""
    ack()
    # Get Taiga ID
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    if body["view"]["private_metadata"]:
        current_state = body["view"]["private_metadata"]
    else:
        current_state = json.dumps(const.base_filter)

    blocks = block_formatters.home_filters(
        taiga_id=taiga_id,
        taiga_cache=taiga_cache,
        current_state=current_state,
    )

    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "filter_home",
                "title": {"type": "plain_text", "text": "Filter items"},
                "blocks": blocks,
                "private_metadata": body["view"]["private_metadata"],
                "submit": {"type": "plain_text", "text": "Filter"},
                "close": {"type": "plain_text", "text": "Cancel"},
            },
        )
        logger.info(f"Opened home filter modal for {body['user']['id']}")
    except SlackApiError as e:
        logger.error(f"Failed to open modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view_submission("filter_home")
def handle_filter_home(ack, body):
    """Regenerate the app home with filters applied"""
    ack()

    # Get Taiga ID
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    # Get the current filters
    filters = body["view"]["state"]["values"]

    logger.info(f"Regenerating home for {body['user']['id']} with filters")

    # Regenerate app home
    slack_misc.push_home(
        user_id=body["user"]["id"],
        config=config,
        tidyhq_cache=tidyhq_cache,
        taiga_cache=taiga_cache,
        taiga_auth_token=taiga_auth_token,
        slack_app=app,
        private_metadata=json.dumps(filters),
    )


@app.action("clear_filter")
def handle_clear_filter(ack, body):
    """Clear the filters from the app home"""
    ack()

    # Regenerate app home
    slack_misc.push_home(
        user_id=body["user"]["id"],
        config=config,
        tidyhq_cache=tidyhq_cache,
        taiga_cache=taiga_cache,
        taiga_auth_token=taiga_auth_token,
        slack_app=app,
        private_metadata=json.dumps(const.base_filter),
    )
    logger.info(f"Cleared filters for {body['user']['id']}")


@app.action(re.compile(r"^create_task-.*"))
def handle_create_task(ack, body):
    """Create a task"""
    ack()

    project_id, story_id = body["actions"][0]["action_id"].split("-")[1:]
    item_type = "task"

    logging.info(f"Creating new task in project for {story_id}")

    edit_blocks = block_formatters.edit_info_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type=item_type,
        item_id="0",
        taiga_cache=taiga_cache,
        new=True,
    )

    try:
        client.views_push(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": f"write_item-{project_id}-{item_type}-{story_id}",
                "title": {"type": "plain_text", "text": f"Create new {item_type}"},
                "blocks": edit_blocks,
                "submit": {"type": "plain_text", "text": f"Create {item_type}"},
                "close": {"type": "plain_text", "text": "Cancel"},
            },
        )
        logger.info(f"Opened new item creation modal for {body['user']['id']}")
    except SlackApiError as e:
        logger.error(f"Failed to open modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.action("select_project_for_search")
def modal_search_project(ack, body):
    """Open a modal to select a project to search in"""
    ack()

    # Get Taiga ID
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    if not taiga_id:
        taiga_id = config["taiga"]["guest_user"]

    blocks = block_formatters.project_selector(
        taiga_id=taiga_id,
        taiga_cache=taiga_cache,
        private_metadata=body["view"]["private_metadata"],
    )

    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "search_items",
                "title": {"type": "plain_text", "text": "Select a project"},
                "blocks": blocks,
                "submit": {"type": "plain_text", "text": "Select"},
                "close": {"type": "plain_text", "text": "Cancel"},
            },
        )
        logger.info(f"Opened search project modal for {body['user']['id']}")
    except SlackApiError as e:
        logger.error(f"Failed to open modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view("search_items")
def modal_search(ack, body):
    """Open a modal to search for items"""
    ack()

    # Pull out the projects
    projects = []
    for selected_option in body["view"]["state"]["values"]["projects"][
        "project_select"
    ]["selected_options"]:
        projects.append(selected_option["value"])

    # Get Taiga ID
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    if not taiga_id:
        taiga_id = config["taiga"]["guest_user"]

    blocks = block_formatters.search_blocks(
        taiga_id=taiga_id, taiga_cache=taiga_cache, projects=projects
    )

    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "load_searched_item",
                "title": {"type": "plain_text", "text": "Search items"},
                "blocks": blocks,
                "submit": {"type": "plain_text", "text": "View"},
                "close": {"type": "plain_text", "text": "Cancel"},
            },
        )
        logger.info(f"Updated search modal for {body['user']['id']}")
    except SlackApiError as e:
        logger.error(f"Failed to update modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view("load_searched_item")
def modal_searched_item(ack, body):

    start_time = time.time()

    # Get the action_id for our search field
    for block in body["view"]["blocks"]:
        if block["block_id"] == "search":
            search_action_id = block["element"]["action_id"]

    # Get the selected item
    selected_option = body["view"]["state"]["values"]["search"][search_action_id][
        "selected_option"
    ]["value"]

    # Backwards compatibility for old some old view buttons
    if "userstory" in selected_option:
        selected_option = selected_option.replace("userstory", "story")

    # Sometimes we attach the view method to the action ID

    project_id, item_type, item_id = selected_option.split("-")[1:]

    logger.info(
        f"Received view/edit for {item_type} {item_id} in project {project_id} based on search"
    )
    ack()

    # Attempt to map the Slack user to a Taiga user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    if not taiga_id:
        logger.error(f"Failed to map Slack user {body['user']['id']} to Taiga user")
        view_title = f"View {item_type}"
        edit = False
    elif taiga_id not in taiga_cache["boards"][int(project_id)]["members"]:
        logger.error(f"User {taiga_id} is not a member of project {project_id}")
        view_title = f"View {item_type}"
        edit = False

    else:
        view_title = f"View/edit {item_type}"
        edit = True

    # Confirm the user is allowed to view the item

    # Generate the blocks for the view/edit modal
    block_list = block_formatters.viewedit_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type=item_type,
        item_id=item_id,
        taiga_cache=taiga_cache,
        config=config,
        taiga_auth_token=taiga_auth_token,
        edit=edit,
    )

    if taiga_id:
        log_time(
            start_time, time.time(), response_logger, cause="View/edit modal generation"
        )
    else:
        log_time(
            start_time, time.time(), response_logger, cause="View modal generation"
        )

    # Open the modal
    try:
        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "finished_editing",
                "title": {"type": "plain_text", "text": view_title},
                "blocks": block_list,
                "submit": {"type": "plain_text", "text": "Finish"},
                "clear_on_close": True,
            },
        )
        logger.info(
            f"View/edit modal for {item_type} {item_id} in project {project_id} opened for {body['user']['id']} ({taigalink.name_mapper(taiga_id, taiga_cache)})"
        )
    except SlackApiError as e:
        logger.error(f"Failed to open modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])
        pprint(block_list)


@app.options(re.compile(r"^search_items-.*"))
def handle_search_options(ack, body):

    # Get the searching projects from the action ID
    search_projects = body["action_id"].split("-")[1:]

    # Get the search term
    search_term = body["value"]

    logging.info(f"Searching for {search_term} in projects {search_projects}")

    search_results = taigalink.search(
        config=config,
        projects=search_projects,
        search_str=search_term,
        taiga_auth_token=taiga_auth_token,
    )

    option_groups = slack_misc.search_results_to_options(
        search_results=search_results, taiga_cache=taiga_cache
    )

    ack(option_groups=option_groups)


# The cron mode renders the app home for every user in the workspace and resets filters
if "--cron" in sys.argv:
    # Update homes for all slack users
    logger.info("Updating homes for all users")

    # Get a list of all users from slack
    slack_response = app.client.users_list()
    slack_users = []
    while slack_response.data.get("response_metadata", {}).get("next_cursor"):  # type: ignore
        slack_users += slack_response.data["members"]  # type: ignore
        slack_response = app.client.users_list(cursor=slack_response.data["response_metadata"]["next_cursor"])  # type: ignore
    slack_users += slack_response.data["members"]  # type: ignore

    users = []

    # Convert slack response to list of users since it comes as an odd iterable
    for user in slack_users:
        if user["is_bot"] or user["deleted"]:
            continue
        users.append(user)
    logger.info(f"Found {len(users)} users")

    x = 1

    home_no_tidyhq = None
    home_no_taiga = None

    updates = {}

    def gen_home(user_id: str, x: int, home_no_taiga: list, home_no_tidyhq: list):
        taiga_id = tidyhq.map_slack_to_taiga(
            tidyhq_cache=tidyhq_cache,
            config=config,
            slack_id=user_id,
        )
        tidyhq_id = tidyhq.map_slack_to_tidyhq(
            tidyhq_cache=tidyhq_cache, config=config, slack_id=user_id
        )
        if taiga_id:
            block_list = block_formatters.app_home(
                user_id=user_id,
                config=config,
                tidyhq_cache=tidyhq_cache,
                taiga_cache=taiga_cache,
                taiga_auth_token=taiga_auth_token,
                private_metadata=json.dumps(const.base_filter),
            )
            block_list = copy(block_list)
            logger.info(
                f"{x}/{len(users)} {user_id}: Generating individual home for Taiga user - {taigalink.name_mapper(taiga_id, taiga_cache)}"
            )
        elif tidyhq_id:
            if not home_no_taiga:
                home_no_taiga = block_formatters.app_home(
                    user_id=user_id,
                    config=config,
                    tidyhq_cache=tidyhq_cache,
                    taiga_cache=taiga_cache,
                    taiga_auth_token=taiga_auth_token,
                    private_metadata=json.dumps(const.base_filter),
                )
            block_list = home_no_taiga
            logger.info(
                f"{x}/{len(users)} {user_id}: Generating generalised home for TidyHQ user"
            )
        else:
            if not home_no_tidyhq:
                home_no_tidyhq = block_formatters.app_home(
                    user_id=user_id,
                    config=config,
                    tidyhq_cache=tidyhq_cache,
                    taiga_cache=taiga_cache,
                    taiga_auth_token=taiga_auth_token,
                    private_metadata=json.dumps(const.base_filter),
                )
            block_list = home_no_tidyhq
            logger.info(
                f"{x}/{len(users)} {user_id}: Generating generalised home for non-TidyHQ user"
            )
        return user_id, block_list

    x = 1
    user_id, home_no_taiga = gen_home(users[0]["id"], x, [], [])
    user_id, home_no_tidyhq = gen_home(users[2]["id"], x, [], [])

    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = []
        for user in users:
            user_id = user["id"]
            futures.append(
                executor.submit(
                    gen_home,
                    user_id=user_id,
                    x=x,
                    home_no_taiga=home_no_taiga,
                    home_no_tidyhq=home_no_tidyhq,
                )
            )
            x += 1

        for future in as_completed(futures):
            try:
                user_id, block_list = future.result()
                updates[user_id] = block_list
                logger.info(f"Generated app home for {user_id}")
            except Exception as e:
                logger.error(f"Error updating home: {e}")

    x = 1

    input("Ready for next step...")

    threads = []

    private_metadata = json.dumps(const.base_filter)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for user_id in updates:
            print(len(updates[user_id]))
            futures.append(
                executor.submit(
                    slack_misc.push_home,
                    config=config,
                    tidyhq_cache=tidyhq_cache,
                    taiga_cache=taiga_cache,
                    taiga_auth_token=taiga_auth_token,
                    slack_app=app,
                    block_list=updates[user_id],
                    user_id=user_id,
                    private_metadata=private_metadata,
                )
            )

        for future in as_completed(futures):
            try:
                future.result()
                x += 1
                logger.info(f"Updated home for {user_id} ({x}/{len(users)})")
            except Exception as e:
                logger.error(f"Error updating home: {e}")

    logger.info(f"All homes updated ({x-1})")
    sys.exit(0)


# Start the app
if __name__ == "__main__":
    handler = SocketModeHandler(app, config["slack"]["app_token"])
    handler.start()
