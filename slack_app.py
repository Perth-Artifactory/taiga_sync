import json
import logging
import os
import sys
from pprint import pprint
from taiga import TaigaAPI
import re

import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from util import taigalink, tidyhq, slack_formatters, slack, strings, blocks


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

# Initialize the app with your bot token and signing secret
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
def handle_app_mention(event, say, client, respond):
    """Respond to a mention of the bot with a message"""
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
def handle_task_command(ack, respond, command, client):
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


@app.action(re.compile(r"^tlink.*"))
def ignore_link_button_presses(ack):
    """Dummy function to ignore link button presses"""
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

    # Check if the Slack user can be mapped to a Taiga user
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq.fresh_cache(config=config, cache=tidyhq_cache),
        config=config,
        slack_id=body["user"]["id"],
    )

    # If the Slack user can't be mapped to a Taiga user the best we can do is tell them to watch it themselves
    if not taiga_id:
        message = """Sorry, I can't watch this item for you as I don't know who you are in Taiga\nIf you think this is an error please reach out to #it."""
        if watch_target.get("permalink"):
            message += f"\n\nYou can view the item yourself [here]({watch_target['permalink']})"
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
            message += f"\n\nYou can view the item yourself [here]({watch_target['permalink']})"
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
            message += f"\n\nYou can view the item yourself [here]({watch_target['permalink']})"
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

    # Check if the user has a Taiga account
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq.fresh_cache(config=config, cache=tidyhq_cache),
        config=config,
        slack_id=user_id,
    )

    if not taiga_id:
        # We don't recognise the user

        # Construct blocks
        block_list = []
        block_list += blocks.header
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.header
        )
        block_list += blocks.text  # type: ignore
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.unrecognised
        )
        block_list += blocks.divider
        block_list += blocks.text
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.do_instead
        )
        block_list += blocks.context
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.footer
        )

    else:
        # We recognise the user

        # Construct blocks
        block_list = []
        block_list += blocks.header
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.header
        )
        block_list += blocks.text
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.explainer
        )
        block_list += blocks.divider

        block_list += blocks.header
        block_list = slack_formatters.inject_text(
            block_list=block_list, text="Assigned Cards"
        )

        # Get all assigned user stories for the user
        user_stories = taigalink.get_stories(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

        if len(user_stories) == 0:
            block_list += blocks.text
            block_list = slack_formatters.inject_text(
                block_list=block_list, text=strings.no_stories
            )
            block_list += blocks.divider

        else:
            # Sort the user stories by project
            sorted_stories = taigalink.sort_stories_by_project(user_stories)

            for project in sorted_stories:
                header, body = slack_formatters.stories(sorted_stories[project])
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=body
                )

        block_list += blocks.divider

        block_list += blocks.header
        block_list = slack_formatters.inject_text(
            block_list=block_list, text="Assigned Tasks"
        )

        # Get all tasks for the user
        tasks = taigalink.get_tasks(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

        if len(tasks) == 0:
            block_list += blocks.text
            block_list = slack_formatters.inject_text(
                block_list=block_list, text=strings.no_tasks
            )
            block_list += blocks.divider
            block_list += blocks.context
            block_list = slack_formatters.inject_text(
                block_list=block_list, text=strings.footer
            )

        else:

            # Sort the tasks based on user story
            sorted_tasks = taigalink.sort_tasks_by_user_story(tasks)

            # Things will start to break down if there are too many tasks
            displayed_tasks = 0
            trimmed = True
            for project in sorted_tasks:
                if displayed_tasks >= 50:
                    break
                header, body = slack_formatters.tasks(sorted_tasks[project])

                # Skip over tasks assigned in template cards
                if "template" in header.lower():
                    continue

                displayed_tasks += 1
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=body
                )
            else:
                trimmed = False

            if trimmed:
                block_list += blocks.divider
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=strings.trimmed
                )
        block_list += blocks.context
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.footer
        )

    view = {
        "type": "home",
        "blocks": block_list,
    }

    try:
        # Publish the view to the App Home
        client.views_publish(user_id=user_id, view=view)
        logger.info("App Home content set successfully.")
    except Exception as e:
        pprint(block_list)
        logger.error(f"Error publishing App Home content: {e}")


# Start the app
if __name__ == "__main__":
    handler = SocketModeHandler(app, config["slack"]["app_token"])
    handler.start()
