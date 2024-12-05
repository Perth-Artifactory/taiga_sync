import importlib
import json
import logging
import os
import re
import sys
import time
from pprint import pprint

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from taiga import TaigaAPI

from editable_resources import forms, strings
from util import (
    blocks,
    slack,
    slack_formatters,
    slack_forms,
    slack_home,
    taigalink,
    tidyhq,
)


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


def extract_issue_particulars(message) -> tuple[None, None] | tuple[str, str]:
    # Discard everything before the bot is mentioned, including the mention itself
    try:
        message = message[message.index(">") + 1 :]
    except ValueError:
        # This just means the bot wasn't mentioned in the message (e.g. a direct message or command)
        pass

    # The board name should be the first word after the bot mention
    try:
        board = message.split()[0].strip().lower()
    except IndexError:
        logger.error("No board name found in message")
        return None, None

    # The description should be everything after the board name
    try:
        description = message[len(board) + 1 :].strip()
    except IndexError:
        logger.error("No description found in message")
        return None, None

    return board, description


# Set up logging
logging.basicConfig(level=logging.INFO)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
# Set slack bolt logging level to INFO to reduce noise when individual modules are set to debug
slack_logger = logging.getLogger("slack")
slack_logger.setLevel(logging.WARN)
setup_logger = logging.getLogger("setup")
logger = logging.getLogger("slack_app")
response_logger = logging.getLogger("response")

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


# Event listener for messages that mention the bot
@app.event("app_mention")
def handle_app_mention(event, ack, client, respond):
    start_time = time.time()
    """Respond to a mention of the bot with a message"""
    user = event["user"]
    text = event["text"]
    channel = event["channel"]

    user_info = client.users_info(user=user)
    user_display_name = user_info["user"]["profile"].get(
        "real_name", user_info["user"]["profile"]["display_name"]
    )

    # This command is used elsewhere to silence notifications
    if text.startswith("MUTE"):
        ack()
        return

    board, description = extract_issue_particulars(message=text)
    if board not in taiga_cache["projects"]["by_name_with_extra"] or not description:
        client.chat_postEphemeral(
            channel=event["channel"],
            user=event["user"],
            text=(
                "Sorry, I couldn't understand your message. Please try again.\n"
                "It should be in the format of <board name> <description>\n"
                "Valid board names are: `3d`, `infra`, `it`, `lasers`, `committee`"
            ),
            thread_ts=event["thread_ts"] if "thread_ts" in event else None,
        )
        return

    # Determine whether this is a root message or a reply to a thread
    if "thread_ts" in event:
        thread_ts = event["thread_ts"]

        # Get the thread's root message
        response = client.conversations_replies(channel=channel, ts=thread_ts)
        root_message = response["messages"][0] if response["messages"] else None

        if root_message:
            root_text = root_message["text"]
            # Get the display name of the user who created the thread
            root_user_display_name = slack.name_mapper(
                slack_id=root_message["user"], slack_app=app
            )

            board, description = extract_issue_particulars(message=text)
            if not board or not description:
                client.chat_postEphemeral(
                    channel=channel,
                    user=user,
                    text=(
                        "Sorry, I couldn't understand your message. Please try again.\n"
                        "It should be in the format of <board name> <description>\n"
                        "Valid board names are: `3d`, `infra`, `it`, `lasers`, `committee`"
                    ),
                    thread_ts=thread_ts,
                )
                return

            issue = taigalink.create_slack_issue(
                board=board,
                description=f"From {root_user_display_name} on Slack: {root_text}",
                subject=description,
                by_slack=user_info,
                project_ids=taiga_cache["projects"]["by_name_with_extra"],
                config=config,
                taiga_auth_token=taiga_auth_token,
                slack_team_id=slack_team_id,
            )

            if issue:
                client.chat_postMessage(
                    channel=channel,
                    text=f"The issue has been created on Taiga, thanks!",
                    thread_ts=thread_ts,
                )
    else:
        board, description = extract_issue_particulars(message=text)
        if not board or not description:
            client.chat_postEphemeral(
                channel=channel,
                user=user,
                text=(
                    "Sorry, I couldn't understand your message. Please try again.\n"
                    "It should be in the format of <board name> <description>\n"
                    "Valid board names are: `3d`, `infra`, `it`, `lasers`, `committee`"
                ),
            )
            return

        issue = taigalink.create_slack_issue(
            board=board,
            description="",
            subject=description,
            by_slack=user_info,
            project_ids=taiga_cache["projects"]["by_name_with_extra"],
            config=config,
            taiga_auth_token=taiga_auth_token,
            slack_team_id=slack_team_id,
        )
        if issue:
            client.chat_postMessage(
                channel=channel,
                text="The issue has been created on Taiga, thanks!",
                thread_ts=event["ts"],
            )
        log_time(start_time, time.time(), response_logger, cause="Issue creation")


# Event listener for direct messages to the bot
@app.event("message")
def handle_message(event, say, client, ack):
    """Respond to direct messages sent to the bot"""
    start_time = time.time()
    if event.get("channel_type") != "im":
        ack()
        return
    user = event["user"]
    text = event["text"]

    user_info = client.users_info(user=user)
    user_display_name = user_info["user"]["profile"].get(
        "real_name", user_info["user"]["profile"]["display_name"]
    )

    board, description = extract_issue_particulars(message=text)
    if (
        board not in taiga_cache["projects"]["by_name_with_extra"]
        or not description
        or not board
    ):
        client.chat_postEphemeral(
            channel=event["channel"],
            user=event["user"],
            text=(
                "Sorry, I couldn't understand your message. Please try again.\n"
                "It should be in the format of <board name> <description>\n"
                "Valid board names are: `3d`, `infra`, `it`, `lasers`, `committee`"
            ),
            thread_ts=event["thread_ts"] if "thread_ts" in event else None,
        )
        return

    issue = taigalink.create_slack_issue(
        board=board,
        description="",
        subject=description,
        by_slack=user_info,
        project_ids=taiga_cache["projects"]["by_name_with_extra"],
        config=config,
        taiga_auth_token=taiga_auth_token,
        slack_team_id=slack_team_id,
    )
    if issue:
        say("The issue has been created on Taiga, thanks!")
    log_time(start_time, time.time(), response_logger, cause="Issue creation")


# Command listener for /issue
@app.command("/issue")
def handle_issue_command(ack, respond, command, client):
    """Raise issues on Taiga via /issue"""
    start_time = time.time()
    logger.info(f"Received /issue command")
    ack()
    user = command["user_id"]

    user_info = client.users_info(user=user)
    user_display_name = user_info["user"]["profile"].get(
        "real_name", user_info["user"]["profile"]["display_name"]
    )

    board, description = extract_issue_particulars(message=command["text"])

    if (
        board not in taiga_cache["projects"]["by_name_with_extra"]
        or not description
        or not board
    ):
        respond(
            "Sorry, I couldn't understand your message. Please try again.\n"
            "It should be in the format of `/issue <board name> <description>`\n"
            "Valid board names are: `3d`, `infra`, `it`, `lasers`, `committee`"
        )
        return

    issue = taigalink.create_slack_issue(
        board=board,
        description="",
        subject=description,
        by_slack=user_info,
        project_ids=taiga_cache["projects"]["by_name_with_extra"],
        config=config,
        taiga_auth_token=taiga_auth_token,
        slack_team_id=slack_team_id,
    )

    if issue:
        respond("The issue has been created on Taiga, thanks!")

    log_time(start_time, time.time(), response_logger, cause="Issue creation")


# Command listener for form selection
@app.shortcut("form-selector-shortcut")
@app.action("submit_form")
def handle_form_command(ack, respond, command, client, body):
    """Load the form selection modal"""
    start_time = time.time()
    logger.info(f"Received form selection shortcut or button")
    ack()
    user = body["user"]

    # Reload forms from file
    importlib.reload(forms)

    global tidyhq_cache

    artifactory_member = False

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
            artifactory_member = True

    # If they're not an AF member refresh the cache and try again
    refreshed_cache = False
    if not artifactory_member:
        refreshed_cache = True
        tidyhq_cache = tidyhq.fresh_cache(config=config, cache=tidyhq_cache)
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
                artifactory_member = True

    # Render the blocks for the form selection modal
    block_list = slack_forms.render_form_list(
        form_list=forms.forms, member=artifactory_member
    )

    if refreshed_cache:
        log_time(
            start_time,
            time.time(),
            response_logger,
            cause="TidyHQ cache refresh after not matching user, form selection modal generation",
        )
    else:
        log_time(
            start_time,
            time.time(),
            response_logger,
            cause="Form selection modal generation",
        )

    if refreshed_cache:
        log_time(
            start_time,
            time.time(),
            response_logger,
            cause="TidyHQ cache refresh after not matching user, form selection modal generation",
        )
    else:
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
def handle_form_open_button(ack, body, client):
    """Open the selected form in a modal"""
    start_time = time.time()
    ack()
    form_name = body["actions"][0]["value"]

    # Reload forms from file
    importlib.reload(forms)

    # Get the form details
    form = forms.forms[form_name]

    # Convert the form questions to blocks
    block_list = slack_forms.questions_to_blocks(
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
                "callback_id": "form_submission",
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
                "private_metadata": form_name,
            },
        )
    except SlackApiError as e:
        logger.error(e)
        logger.error(f"Failed to push modal: {e.response['error']}")


@app.view("form_submission")
def handle_form_submissions(ack, body, logger):
    """Process form submissions"""
    start_time = time.time()
    description, files = slack_forms.form_submission_to_description(
        submission=body, slack_app=app
    )
    project_id, taiga_type_id, taiga_severity_id = (
        slack_forms.form_submission_to_metadata(
            submission=body, taigacon=taigacon, taiga_cache=taiga_cache
        )
    )

    # Reload forms from file
    importlib.reload(forms)

    form = forms.forms[body["view"]["private_metadata"]]

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
        downloaded_file = slack.download_file(url=filelink, config=config)

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

    slack.send_dm(slack_id=body["user"]["id"], message=message, slack_app=app)

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
def watch_button(ack, body, respond):
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
def handle_reaction_added_events(ack):
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

    slack_home.push_home(
        user_id=user_id,
        config=config,
        tidyhq_cache=tidyhq_cache,
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
def handle_viewedit_actions(ack, body):
    """Listen for view in app and view/edit actions"""
    start_time = time.time()

    # Retrieve action details if applicable
    value_string = body["actions"][0]["action_id"]

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

    else:
        view_title = f"View/edit {item_type}"
        edit = True

    # Generate the blocks for the view/edit modal
    block_list = slack_home.viewedit_blocks(
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
                    "private_metadata": value_string,
                    "submit": {"type": "plain_text", "text": "Finish"},
                    "clear_on_close": True,
                },
            )
            logger.info(
                f"View/edit modal for {item_type} {item_id} in project {project_id} opened for {body['user']['id']} ({taiga_id})"
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
                    "private_metadata": value_string,
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
                    "private_metadata": value_string,
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
@app.action("submit_comment")
def handle_comment_addition(ack, body, logger):
    """Handle comment additions"""
    start_time = time.time()
    ack()

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

    # Get the item details from the private metadata
    project_id, item_type, item_id = body["view"]["private_metadata"].split("-")[1:]

    # Post the comment to Taiga
    print(f"Posting comment {comment} to {item_type} {item_id} in project {project_id}")

    # Get the item direct from Taiga, this isn't cached since it changes so often
    if item_type == "task":
        item = taigacon.tasks.get(item_id)
    elif item_type == "story":
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
        poster_name = slack.name_mapper(slack_id=user_id, slack_app=app)

    # Add byline
    comment = f"Posted from Slack by {poster_name}: {comment}"

    # Post the comment
    commenting = item.add_comment(comment)

    # Regenerate the view/edit modal
    block_list = slack_home.viewedit_blocks(
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
    logging.info("Opening new modal")
    try:
        client.views_update(
            view_id=body["view"]["root_view_id"],
            view={
                "type": "modal",
                "callback_id": "finished_editing",
                "title": {"type": "plain_text", "text": f"View/edit {item_type}"},
                "blocks": block_list,
                "private_metadata": body["view"]["private_metadata"],
                "submit": {"type": "plain_text", "text": "Finish"},
                "clear_on_close": True,
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")


@app.action("home-attach_files")
def attach_files_modal(ack, body):
    """Open a modal to submit files for later attachment"""
    ack()

    block_list = []
    # Create upload field
    block_list = slack_formatters.add_block(block_list, blocks.file_input)
    block_list[-1]["block_id"] = "upload_section"
    block_list[-1]["element"]["action_id"] = "upload_file"
    block_list[-1]["label"]["text"] = "Upload files"

    # Push a new modal
    try:
        client.views_push(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "submit_files",
                "title": {"type": "plain_text", "text": "Upload files"},
                "blocks": block_list,
                "private_metadata": body["view"]["private_metadata"],
                "submit": {"type": "plain_text", "text": "Attach"},
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.action(re.compile(r"^view_tasks-.*"))
def view_tasks(ack, body, logger):
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
    )

    # Attempt to identify the user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=body["user"]["id"],
    )

    edit = False
    if taiga_id:
        edit = True

    block_list = slack_formatters.format_tasks_modal_blocks(
        task_list=tasks, config=config, taiga_auth_token=taiga_auth_token, edit=edit
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
                "private_metadata": body["view"]["private_metadata"],
            },
        )
        logger.info(f"Pushed tasks modal for user story {story_id}")
        logger.info(f"Task modal for story {story_id} pushed for {body['user']['id']}")
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view("submit_files")
def attach_files(ack, body):
    """Take the submitted files, uploads them to Taiga and updates the view/edit modal"""
    start_time = time.time()
    ack()
    files = body["view"]["state"]["values"]["upload_section"]["upload_file"]["files"]

    if not files:
        return

    # Get the item details from the private metadata
    project_id, item_type, item_id = body["view"]["private_metadata"].split("-")[1:]

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

    block_list = slack_home.viewedit_blocks(
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
                "private_metadata": body["view"]["private_metadata"],
                "submit": {"type": "plain_text", "text": "Finish"},
                "clear_on_close": True,
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")


@app.action("edit_info")
def send_info_modal(ack, body, logger):
    """Open a modal to edit the details of an item"""
    start_time = time.time()
    ack()

    # Get the item details from the private metadata
    project_id, item_type, item_id = body["view"]["private_metadata"].split("-")[1:]

    block_list = slack_home.edit_info_blocks(
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
                "callback_id": "edited_info",
                "title": {"type": "plain_text", "text": f"Edit {item_type}"},
                "blocks": block_list,
                "private_metadata": body["view"]["private_metadata"],
                "submit": {"type": "plain_text", "text": "Update"},
                "close": {"type": "plain_text", "text": "Cancel"},
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


@app.view("edited_info")
def edit_info(ack, body, logger):
    """Update the details of an item"""
    start_time = time.time()
    ack()

    # Get the item details from the private metadata
    project_id, item_type, item_id = body["view"]["private_metadata"].split("-")[1:]

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

    log_time(
        start_time,
        time.time(),
        response_logger,
        cause=f"Item update ({len(body['view']['state']['values'])} fields)",
    )


@app.view("finished_editing")
def finished_editing(ack, body):
    """Acknowledge the view submission"""
    ack()


@app.action(re.compile(r"^complete-.*"))
def complete_task(ack, body, client):
    """Mark a task a complete"""
    start_time = time.time()
    ack()

    # Get the item details from the action ID
    project_id, item_type, item_id = body["actions"][0]["action_id"].split("-")[1:]

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
        taiga_cache=taiga_cache,
    )

    if not complete:
        logger.error(f"Failed to mark {item_type} {item_id} as complete")
        return

    if item_type == "task":
        # Get the tasks for the modal
        tasks = taigalink.get_tasks(
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=False,
            story_id=item["user_story"],
        )

        # Regenerate the task view modal
        block_list = slack_formatters.format_tasks_modal_blocks(
            task_list=tasks, config=config, taiga_auth_token=taiga_auth_token
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
                    "private_metadata": body["view"]["private_metadata"],
                },
            )
            logger.info(
                f"Task modal for story {item['user_story']} updated for {body['user']['id']}"
            )
        except SlackApiError as e:
            logger.error(f"Failed to push modal: {e.response['error']}")
            logger.error(e.response["response_metadata"]["messages"])

    # Update the view/edit modal
    block_list = slack_home.viewedit_blocks(
        taigacon=taigacon,
        project_id=project_id,
        item_type="story",
        item_id=item["user_story"],
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
                "private_metadata": body["view"]["private_metadata"],
                "submit": {"type": "plain_text", "text": "Finish"},
                "clear_on_close": True,
            },
        )
    except SlackApiError as e:
        logger.error(f"Failed to push modal: {e.response['error']}")
        logger.error(e.response["response_metadata"]["messages"])


# The cron mode renders the app home for every user in the workspace
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

    for user in users:
        user_id = user["id"]
        slack_home.push_home(
            user_id=user_id,
            config=config,
            tidyhq_cache=tidyhq_cache,
            taiga_auth_token=taiga_auth_token,
            slack_app=app,
        )

        logger.info(
            f"Updated home for {user_id} - {user['profile']['real_name_normalized']} ({x}/{len(users)})"
        )
        x += 1
    logger.info(f"All homes updated ({x})")
    sys.exit(0)


# Start the app
if __name__ == "__main__":
    handler = SocketModeHandler(app, config["slack"]["app_token"])
    handler.start()
