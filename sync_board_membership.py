import requests
import logging
import json
import sys
from slack_bolt import App
from datetime import datetime
from pprint import pprint, pformat
import time

from taiga import TaigaAPI

from util import tidyhq, taigalink
from slack import misc as slack_misc

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
        config = json.load(f)
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

# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
setup_logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)

# Set up Taiga cache
taiga_cache = taigalink.setup_cache(
    config=config, taiga_auth_token=taiga_auth_token, taigacon=taigacon
)

# Connect to Slack
app = App(token=config["slack"]["bot_token"], logger=slack_logger)

# Check over slack channels and look for ones that have corresponding boards
slack_channels = app.client.conversations_list(
    types="public_channel,private_channel", exclude_archived=True, limit=1000
)["channels"]

# Sort channels by name
slack_channels = sorted(slack_channels, key=lambda x: x["name"])

for channel in slack_channels:
    if not channel["is_member"]:
        logger.debug(f"We are not a member of channel {channel['name']}, skipping")
        continue

    logger.debug(f"Checking channel {channel['name']}")
    for c_project_id, c_channel_id in config["taiga-channel"].items():
        if c_channel_id == channel["id"]:
            print("------------------")
            project_id = int(c_project_id)
            logger.info(
                f"Found channel #{channel['name']} in config. Maps to Taiga project {taiga_cache['boards'][project_id]['name']}"
            )
            break
    else:
        logger.debug(f"Channel {channel['name']} not in config")
        continue

    # Get the channel's members
    channel_members = app.client.conversations_members(channel=channel["id"])["members"]

    # Check if any of the channel's members exist in taiga_users
    for slack_id in channel_members:
        taiga_id = tidyhq.map_slack_to_taiga(
            tidyhq_cache=tidyhq_cache, slack_id=slack_id, config=config
        )

        if not taiga_id:
            logger.debug(f"No Taiga ID found for user {slack_id}")
            continue

        # Check if the slack user is a member of the project
        if taiga_id in taiga_cache["boards"][project_id]["members"]:
            logger.info(
                f"User {slack_misc.name_mapper(slack_id=slack_id, slack_app=app)} ({slack_id}) is already a member of project: {taiga_cache['boards'][project_id]['name']}"
            )
            continue
        else:
            logger.info(
                f"User {slack_misc.name_mapper(slack_id=slack_id, slack_app=app)} ({slack_id}) is not a member of project: {taiga_cache['boards'][project_id]['name']} and may need to be added"
            )

        private = False
        # Check if the board is private
        if taiga_cache["boards"][project_id]["private"]:
            logger.info(
                f"Project {taiga_cache['boards'][project_id]['name']} is private"
            )

            # Check if the channel is also private
            if not channel["is_private"]:
                logger.info(f"Channel #{channel['name']} not private, will not add")
                continue

            logger.info("Both the board and channel are private, will add")
            role_key = "highest_role"
            private = True
        else:
            logger.info(
                f"Project {taiga_cache['boards'][project_id]['name']} is public, will add"
            )
            role_key = "lowest_role"

        logger.info(
            f"Adding {slack_misc.name_mapper(slack_id=slack_id, slack_app=app)}/{taiga_cache['users'][taiga_id]['name']} to project {taiga_cache['boards'][project_id]['name']} (ID:{project_id})"
        )

        logger.info(
            f"Adding as {'lowest' if not private else 'highest'} role in project ({taiga_cache['boards'][project_id][role_key]['name']})"
        )

        response = requests.post(
            f"{config['taiga']['url']}/api/v1/memberships",
            headers={
                "Authorization": f"Bearer {taiga_auth_token}",
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "role": taiga_cache["boards"][project_id][role_key]["id"],
                    "project": project_id,
                    "username": taiga_cache["users"][taiga_id]["username"],
                }
            ),
        )

        if response.status_code == 201:
            logger.info(
                f"Added {slack_id}/{taiga_id} to {taiga_cache['boards'][project_id]['name']}"
            )
