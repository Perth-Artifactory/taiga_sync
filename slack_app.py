import json
import logging
import os
import sys
from pprint import pprint
from taiga import TaigaAPI

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from util import taigalink


# Stand in testing function that simulates issue creation while testing Slack parts
def dummy_issue_creation(board, description, subject, by):
    description = f"{description}\n\nby: {by}"
    print(
        f"Created issue on {board} board with description: {description} and subject: {subject}"
    )
    return True


def issue_creation(board, description, subject, by):
    description = f"{description}\n\nAdded to Taiga by: {by}"
    project_id = project_ids.get(board)
    if not project_id:
        logger.error(f"Project ID not found for board {board}")
        return False

    issue = taigalink.base_create_issue(
        taiga_auth_token=taiga_auth_token,
        project_id=project_id,
        subject=subject,
        description=description,
        config=config,
    )

    if not issue:
        logger.error(f"Failed to create issue on board {board}")
        return False

    issue_info = issue.json()

    return issue_info


def extract_issue_particulars(message):
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
logger = logging.getLogger("issue_sync")

# Load config
try:
    with open("config.json") as f:
        config: dict = json.load(f)
except FileNotFoundError:
    setup_logger.error(
        "config.json not found. Create it using example.config.json as a template"
    )
    sys.exit(1)

# Get auth token for Taiga
# This is used instead of python-taiga's inbuilt user/pass login method since we also need to interact with the api directly
auth_url = f"{config['taiga']['url']}/api/v1/auth"
auth_data = {
    "password": config["taiga"]["password"],
    "type": "normal",
    "username": config["taiga"]["username"],
}
response = requests.post(
    auth_url, headers={"Content-Type": "application/json"}, data=json.dumps(auth_data)
)

if response.status_code == 200:
    taiga_auth_token = response.json().get("auth_token")
else:
    setup_logger.error(f"Failed to get auth token: {response.status_code}")
    sys.exit(1)

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

# Initialize the app with your bot token and signing secret
app = App(token=config["slack"]["bot_token"], logger=slack_logger)


# Event listener for messages that mention the bot
@app.event("app_mention")
def handle_app_mention(event, say, client, respond):
    user = event["user"]
    text = event["text"]
    channel = event["channel"]

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
            issue = issue_creation(
                board=board,
                description=f"From {root_user_display_name} on Slack: {root_text}",
                subject=description,
                by=user_display_name,
            )
            if issue:
                client.chat_postMessage(
                    channel=channel,
                    text=f"The issue has been created on Taiga, thanks!",
                    thread_ts=thread_ts,
                )
    else:
        board, description = extract_issue_particulars(message=text)
        issue = issue_creation(
            board=board,
            description="",
            subject=description,
            by=user_display_name,
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

    issue = issue_creation(
        board=board,
        description="",
        subject=description,
        by=user_display_name,
    )
    if issue:
        say("The issue has been created on Taiga, thanks!")


# Command listener for /issue
@app.command("/issue")
def handle_task_command(ack, respond, command, client):
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

    issue = issue_creation(
        board=board,
        description="",
        subject=description,
        by=user_display_name,
    )

    if issue:
        respond("The issue has been created on Taiga, thanks!")


@app.event("app_home_opened")
def handle_app_home_opened_events(body, client, logger):
    user_id = body["event"]["user"]

    # Define the view payload
    view = {
        "type": "home",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Welcome to Taiga! This is a placeholder message.",
                },
            }
        ],
    }

    try:
        # Publish the view to the App Home
        client.views_publish(user_id=user_id, view=view)
        logger.info("App Home content set successfully.")
    except Exception as e:
        logger.error(f"Error publishing App Home content: {e}")


# Start the app
if __name__ == "__main__":
    handler = SocketModeHandler(app, config["slack"]["app_token"])
    handler.start()
