import json
import logging
from pprint import pprint
import requests

import jsonschema
import mistune

from util import tidyhq
from slack import block_formatters

# Set up logging
logger = logging.getLogger("slack.misc")


class mrkdwnRenderer(mistune.HTMLRenderer):
    def paragraph(self, text):
        return text + "\n"

    def heading(self, text, level):
        return f"*{text}*\n"

    def list(self, body, ordered, level, start=None):
        return body

    def list_item(self, text, level):
        return f"• {text}\n"

    def block_quote(self, text):
        quoted_lines = [f"> {line}" for line in text.split("\n")[:-1]]
        return "\n".join(quoted_lines) + "\n"

    def codespan(self, text):
        return f"`{text}`"

    def link(self, link, title, text):
        return f"<{link}|{title}>"

    def strong(self, text):
        return f"*{text}*"

    def emphasis(self, text):
        return f"_{text}_"


mrkdwnconvert = mistune.create_markdown(renderer=mrkdwnRenderer())


def convert_markdown(text: str) -> str:
    """Convert normal markdown to slack markdown"""
    text = text.replace("<br>", "\n")
    result = mrkdwnconvert(text)
    result = result.strip()
    # Remove <p> tags
    result = result.replace("<p>", "").replace("</p>", "")
    return result


def validate(blocks, surface: str | None = "modal"):
    if surface not in ["modal", "home", "message", "msg"]:
        raise ValueError(f"Invalid surface type: {surface}")
    # We want our own logger for this function
    schemalogger = logging.getLogger("block-kit validator")

    if surface in ["modal", "home"]:
        if len(blocks) > 100:
            schemalogger.error(f"Block list too long {len(blocks)}/100")
            return False
    elif surface in ["message", "msg"]:
        if len(blocks) > 50:
            schemalogger.error(f"Block list too long {len(blocks)}/50")
            return False

    # Recursively search for all fields called "text" and ensure they don't have an empty string
    for block in blocks:
        if not check_for_empty_text(block, schemalogger):
            return False

    # Load the schema from file
    with open("block-kit-schema.json") as f:
        schema = json.load(f)

    try:
        jsonschema.validate(instance=blocks, schema=schema)
    except jsonschema.exceptions.ValidationError as e:  # type: ignore
        schemalogger.error(e)
        return False
    return True


def check_for_empty_text(block, logger):
    for key, value in block.items():
        if key == "text" and value == "":
            logger.error(f"Empty text field found in block {block}")
            return False
        if isinstance(value, dict):
            if not check_for_empty_text(value, logger):
                return False
    return True


def push_home(
    user_id: str, config: dict, tidyhq_cache: dict, taiga_auth_token: str, slack_app
):
    """Push the app home view to a specified user."""
    # Generate the app home view
    block_list = block_formatters.app_home(
        user_id=user_id,
        config=config,
        tidyhq_cache=tidyhq_cache,
        taiga_auth_token=taiga_auth_token,
    )

    try:
        slack_app.client.views_publish(
            user_id=user_id,
            view={
                "type": "home",
                "blocks": block_list,
            },
        )
        logger.info(f"Set app home for {user_id} ")
        return True
    except Exception as e:
        logger.error(f"Failed to push home view: {e}")
        return False


def name_mapper(slack_id: str, slack_app) -> str:
    """
    Returns the slack name(s) of a user given their ID
    """

    slack_id = slack_id.strip()

    # Catch edge cases caused by parsing
    if slack_id == "Unknown":
        return "Unknown"
    elif "No one" in slack_id:
        return "No one"
    elif slack_id == "":
        return ""

    # Check if there's multiple IDs
    if "," in slack_id:
        names = []
        for id in slack_id.split(","):
            names.append(name_mapper(id, slack_app))
        return ", ".join(names)

    user_info = slack_app.client.users_info(user=slack_id)

    # Real name is best
    if user_info["user"].get("real_name", None):
        return user_info["user"]["real_name"]

    # Display is okay
    return user_info["user"]["profile"]["display_name"]


def send_dm(
    slack_id: str,
    message: str,
    slack_app,
    blocks: list = [],
    unfurl_links: bool = False,
    unfurl_media: bool = False,
    username: str | None = None,
    photo: str | None = None,
) -> bool:
    """
    Send a direct message to a user including conversation creation
    """

    # Create a conversation
    conversation = slack_app.client.conversations_open(users=[slack_id])
    conversation_id = conversation["channel"]["id"]

    # Photos are currently bugged for DMs
    photo = None

    # Send the message
    try:
        m = slack_app.client.chat_postMessage(
            channel=conversation_id,
            text=message,
            blocks=blocks,
            unfurl_links=unfurl_links,
            unfurl_media=unfurl_media,
            username=username,
            icon_url=photo,
        )

    except slack_sdk.errors.SlackApiError as e:  # type: ignore
        logger.error(f"Failed to send message to {slack_id}")
        logger.error(e)
        return False

    if not m["ok"]:
        logger.error(f"Failed to send message to {slack_id}")
        logger.error(m)
        return False

    logger.info(f"Sent message to {slack_id}")
    return True


def map_recipients(list_of_recipients: list, tidyhq_cache: dict, config: dict) -> dict:
    """
    Maps a list of slack recipients to the appropriate pathways
    """

    recipients = {"user": [], "channel": []}
    for recipient in list_of_recipients:
        if recipient[0] == "U":
            recipients["user"].append(recipient)
        elif recipient[0] in ["C", "G"]:
            recipients["channel"].append(recipient)
        else:
            # Assume it's a Taiga user ID
            slack_id = tidyhq.map_taiga_to_slack(
                tidyhq_cache=tidyhq_cache, taiga_id=recipient, config=config
            )
            if slack_id:
                recipients["user"].append(slack_id)
            else:
                logger.error(f"No slack ID found for Taiga user {recipient}")

    return recipients


def download_file(url, config):
    file_data = requests.get(
        url=url,
        headers={"Authorization": f'Bearer {config["slack"]["bot_token"]}'},
    )
    return file_data.content
