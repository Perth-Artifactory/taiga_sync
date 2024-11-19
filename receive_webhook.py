import hashlib
import hmac
import json
import logging
import os
import sys
from copy import deepcopy as copy
from pprint import pprint

import requests
from flask import Flask, request
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from taiga import TaigaAPI
from waitress import serve
from werkzeug.middleware.proxy_fix import ProxyFix

from util import blocks, slack, slack_formatters, taigalink, tidyhq


def verify_signature(key, data, signature):
    mac = hmac.new(key.encode("utf-8"), msg=data, digestmod=hashlib.sha1)
    return mac.hexdigest() == signature


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

# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
setup_logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)

# Initialize the app with your bot token and signing secret
slack_app = App(token=config["slack"]["bot_token"], logger=slack_logger)

flask_app = Flask(__name__)


@flask_app.route("/taiga/incoming", methods=["POST"])
def incoming():
    # Get the verification header
    signature = request.headers.get("X-Taiga-Webhook-Signature")

    if not signature:
        return "No signature", 401

    if not verify_signature(
        key=config["taiga"]["webhook_secret"], data=request.data, signature=signature
    ):
        return "Invalid signature", 401

    logger.debug("Data received from taiga and verified")

    data = request.get_json()

    # We only perform actions in three scenarios:
    # 1. The webhook is for a new issue or user story
    new_thing = False
    # 2. The webhook is for a user story tagged with "important"
    important = False
    # 3. The webhook is for a user story that is watched by someone other than the user who initiated the action
    watched = False

    send_to = []
    project_id = str(data["data"]["project"]["id"])
    # Map the project ID to a slack channel
    slack_channel = None
    if project_id in config["taiga-channel"]:
        slack_channel = config["taiga-channel"][project_id]

    if data["action"] == "create":
        new_thing = True
        assigned_to = data["data"]["assigned_to"]
        if assigned_to:
            watchers = [assigned_to["id"]]
        # Add the corresponding slack channel as a recipient if it exists
        if slack_channel:
            send_to.append(slack_channel)

    elif "important" in data["data"]["tags"]:
        important = True
        # Add the corresponding slack channel as a recipient if it exists
        if slack_channel:
            send_to.append(slack_channel)
        else:
            logger.error(
                f"No slack channel found for project {project_id} and it's marked as important"
            )

    if data["action"] == "change":
        by = data["by"]["id"]
        watchers = data["data"]["watchers"]
        # Check if the user who's assigned the issue is watching it (and pretend they are if they aren't)
        assigned_to = data["data"]["assigned_to"]
        if assigned_to:
            if assigned_to["id"] not in watchers:
                watchers.append(assigned_to["id"])

        # Remove the user who initiated the action from the list of watchers if present
        if by in watchers:
            watchers.remove(by)

        if len(watchers) > 0:
            watched = True
            send_to += [str(watcher) for watcher in watchers]

    logger.info(f"New: {new_thing}, Important: {important}, Watched: {watched}")

    if not new_thing and not important and not watched:
        return "No action required", 200

    # Construction the message
    message = taigalink.parse_webhook_action_into_str(
        data=data,
        tidyhq_cache=tidyhq_cache,
        config=config,
        taiga_auth_token=taiga_auth_token,
    )

    # Check if there's an image to attach
    image = data["by"].get("photo", None)
    block_list = []
    block_list += blocks.text
    block_list = slack_formatters.inject_text(block_list, message)
    if image and data["action"] == "create":
        # Create an image accessory
        accessory = copy(blocks.accessory_image)
        accessory["image_url"] = image
        accessory["alt_text"] = f"Photo of {data['by']['full_name']}"
        # Attach the accessory to the last block
        block_list[-1]["accessory"] = accessory

    # Check if there's a url to attach
    url = data["data"].get("permalink", None)
    if url:
        button = copy(blocks.button)
        button["text"]["text"] = "View in Taiga"
        button["url"] = url
        button["action_id"] = "view_in_taiga"
        block_list += blocks.actions
        block_list[-1]["elements"].append(button)

    # map recipients to slack IDs
    recipients = slack.map_recipients(
        list_of_recipients=send_to, tidyhq_cache=tidyhq_cache, config=config
    )

    for user in recipients["user"]:
        slack.send_dm(
            slack_id=user, message=message, slack_app=slack_app, blocks=block_list
        )

    for channel in recipients["channel"]:
        try:
            slack_app.client.chat_postMessage(
                channel=channel, text=message, blocks=block_list
            )
        except SlackApiError as e:
            logger.error(f"Failed to send message to channel {channel}")
            logger.error(e.response["error"])
            pprint(block_list)

    return "Actioned!", 200


@flask_app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def catch_all(path):
    print(f"Route: /{path}")
    return "", 404


flask_app.wsgi_app = ProxyFix(flask_app.wsgi_app, x_for=1, x_proto=1, x_host=1)

if __name__ == "__main__":
    serve(flask_app, host="0.0.0.0", port=32000)
