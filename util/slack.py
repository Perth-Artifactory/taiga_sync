import logging
from pprint import pprint

import requests

from util import tidyhq

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
    unfurl_links: bool = True,
    unfurl_media: bool = True,
    username: str | None = None,
    photo: str | None = None,
) -> bool:
    """
    Send a direct message to a user including conversation creation
    """

    # Create a conversation
    conversation = slack_app.client.conversations_open(users=[slack_id])
    conversation_id = conversation["channel"]["id"]

    # Send the message
    m = slack_app.client.chat_postMessage(
        channel=conversation_id,
        text=message,
        blocks=blocks,
        unfurl_links=unfurl_links,
        unfurl_media=unfurl_media,
        username=username,
        icon_url=photo,
    )

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
        elif recipient[0] == "C":
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
