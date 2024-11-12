import logging
from pprint import pprint

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def extract_message_vars(message) -> dict:
    """
    Extracts the variables embedded in a message by an external workflow
    """

    # Get the appropriate block kit
    blocks = message["blocks"][0]["elements"][0]["elements"]
    variables = {}
    variable_name = ""
    for block in blocks:
        if block.get("style", {"bold": False})["bold"]:
            variable_name = block["text"]
            if variable_name not in variables:
                variables[variable_name] = ""
        elif variable_name:
            if "text" in block:
                variables[variable_name] += block["text"]
            elif block.get("type") == "user":
                variables[variable_name] += block["user_id"]

    # Strip newlines from the beginning and end of each variable
    for key, value in variables.items():
        variables[key] = value.strip()

    return variables


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


def send_dm(slack_id: str, message: str, slack_app) -> bool:
    """
    Send a direct message to a user including conversation creation
    """

    # Create a conversation
    conversation = slack_app.client.conversations_open(users=[slack_id])
    conversation_id = conversation["channel"]["id"]

    # Send the message
    m = slack_app.client.chat_postMessage(channel=conversation_id, text=message)

    if not m["ok"]:
        logger.error(f"Failed to send message to {slack_id}")
        logger.error(m)
        return False

    logger.info(f"Sent message to {slack_id}")
    return True
