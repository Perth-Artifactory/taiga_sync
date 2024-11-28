import json
import logging
import os
import re
import sys
from pprint import pprint

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from taiga import TaigaAPI
import importlib

from editable_resources import strings, forms
from util import blocks, slack, slack_formatters, slack_forms, taigalink, tidyhq


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
slack_logger.setLevel(logging.INFO)
setup_logger = logging.getLogger("setup")
logger = logging.getLogger("slack_app")

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

# Map project names to IDs
projects = taigacon.projects.list()
project_ids = {project.name.lower(): project.id for project in projects}
actual_ids = {project.name.lower(): project.id for project in projects}

# Duplicate similar board names for QoL
project_ids["infra"] = project_ids["infrastructure"]
project_ids["laser"] = project_ids["lasers"]
project_ids["printer"] = project_ids["3d"]
project_ids["printers"] = project_ids["3d"]

# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
setup_logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)

# Set up slack app
app = App(token=config["slack"]["bot_token"], logger=slack_logger)

# Get the ID for our team via the API
auth_test = app.client.auth_test()
slack_team_id = auth_test["team_id"]

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
    if board not in project_ids or not description:
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
            root_user_info = client.users_info(user=root_message["user"])
            root_user_display_name = root_user_info["user"]["profile"].get(
                "real_name", root_user_info["user"]["profile"]["display_name"]
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
                project_ids=project_ids,
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
            project_ids=project_ids,
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


# Event listener for direct messages to the bot
@app.event("message")
def handle_message(event, say, client, ack):
    """Respond to direct messages sent to the bot"""
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
    if board not in project_ids or not description:
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
        project_ids=project_ids,
        config=config,
        taiga_auth_token=taiga_auth_token,
        slack_team_id=slack_team_id,
    )
    if issue:
        say("The issue has been created on Taiga, thanks!")


# Command listener for /issue
@app.command("/issue")
def handle_issue_command(ack, respond, command, client):
    """Raise issues on Taiga via /issue"""
    logger.info(f"Received /issue command")
    ack()
    user = command["user_id"]

    user_info = client.users_info(user=user)
    user_display_name = user_info["user"]["profile"].get(
        "real_name", user_info["user"]["profile"]["display_name"]
    )

    board, description = extract_issue_particulars(message=command["text"])

    if board not in project_ids or not description:
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
        project_ids=project_ids,
        config=config,
        taiga_auth_token=taiga_auth_token,
        slack_team_id=slack_team_id,
    )

    if issue:
        respond("The issue has been created on Taiga, thanks!")


# Command listener for form selection
@app.shortcut("form-selector-shortcut")
def handle_form_command(ack, respond, command, client, body):
    """Load the form selection modal"""
    logger.info(f"Received form selection shortcut")
    ack()
    user = body["user"]

    # Reload forms from file
    importlib.reload(forms)

    global tidyhq_cache
    tidyhq_cache = tidyhq.fresh_cache(config=config, cache=tidyhq_cache)

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

    # Render the blocks for the form selection modal
    block_list = slack_forms.render_form_list(
        form_list=forms.forms, member=artifactory_member
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


@app.action(re.compile(r"^tlink.*"))
def ignore_link_button_presses(ack):
    """Dummy function to ignore link button presses"""
    ack()


@app.action(re.compile(r"^form-open-.*"))
def handle_form_open_button(ack, body, client):
    """Open the selected form in a modal"""
    ack()
    form_name = body["actions"][0]["value"]

    # Reload forms from file
    importlib.reload(forms)

    # Get the form details
    form = forms.forms[form_name]

    # Convert the form questions to blocks
    block_list = slack_forms.questions_to_blocks(
        form["questions"], taigacon=taigacon, taiga_project=form.get("taiga_project")
    )

    # Form title can only be 25 characters long
    if len(form["title"]) > 25:
        if not form.get("short_title"):
            form_title = form["title"][:25]
        else:
            form_title = form["short_title"]
    else:
        form_title = form["title"]

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
    description, files = slack_forms.form_submission_to_description(
        submission=body, slack_app=app
    )
    project_id, taiga_type_id, taiga_severity_id = (
        slack_forms.form_submission_to_metadata(submission=body, taigacon=taigacon)
    )

    # Reload forms from file
    importlib.reload(forms)

    form = forms.forms[body["view"]["private_metadata"]]

    if "taiga_type" in form and project_id:
        if taiga_type_id:
            # If the form doesn't have a type set via a question then we don't need to log that we're override it
            logger.debug("Overriding type with form-specific type")
        try:
            taiga_type_id = int(form["taiga_type"])
        except ValueError:
            # IDs are ints, if it's not then we need map from a name
            taiga_type_id = taigalink.item_mapper(
                item=form["taiga_type"],
                field_type="type",
                project_id=project_id,
                taiga_auth_token=taiga_auth_token,
                taigacon=taigacon,
                config=config,
            )
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


@app.event("reaction_added")
def handle_reaction_added_events(ack):
    """Dummy function to ignore emoji reactions to messages"""
    ack()


@app.event("app_home_opened")
def handle_app_home_opened_events(body, client, logger):
    """Render app homes"""
    user_id = body["event"]["user"]

    # Get user details for more helpful console messages
    user_info = client.users_info(user=user_id)

    block_list = slack_formatters.app_home(
        user_id=user_id,
        config=config,
        tidyhq_cache=tidyhq_cache,
        taiga_auth_token=taiga_auth_token,
    )

    view = {
        "type": "home",
        "blocks": block_list,
    }

    try:
        # Publish the view to the App Home
        client.views_publish(user_id=user_id, view=view)
        logger.info(f"Set app home for {user_info['user']['name']} ({user_id}) ")
    except Exception as e:
        pprint(block_list)
        logger.error(f"Error publishing App Home content: {e}")


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
        block_list = slack_formatters.app_home(
            user_id=user_id,
            config=config,
            tidyhq_cache=tidyhq_cache,
            taiga_auth_token=taiga_auth_token,
        )

        view = {
            "type": "home",
            "blocks": block_list,
        }

        try:
            # Publish the view to the App Home
            logger.debug("Setting app home for {user_id}")
            client.views_publish(user_id=user_id, view=view)
        except Exception as e:
            pprint(block_list)
            logger.error(f"Error publishing App Home content: {e}")

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
