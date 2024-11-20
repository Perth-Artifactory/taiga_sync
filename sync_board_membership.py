import requests
import logging
import json
import sys
from slack_bolt import App
from datetime import datetime
from pprint import pprint, pformat
import time

from taiga import TaigaAPI

from util import slack, taigalink, tidyhq

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

# Connect to Slack
app = App(token=config["slack"]["bot_token"], logger=slack_logger)

# Map Taiga users to Slack users
url = f"{config['taiga']['url']}/api/v1/users"

response = requests.get(url, headers={"Authorization": f"Bearer {taiga_auth_token}"})

if response.status_code == 200:
    taiga_users_raw = response.json()

else:
    setup_logger.error(f"Failed to get Taiga users: {response.status_code}")
    sys.exit(1)

setup_logger.info(f"Got {len(taiga_users_raw)} Taiga users")

taiga_users = {}

for taiga_user in taiga_users_raw:
    logger.debug(f"Mapping Taiga user {taiga_user['id']} to Slack user")
    slack_id = tidyhq.map_taiga_to_slack(
        tidyhq_cache=tidyhq_cache, taiga_id=taiga_user["id"], config=config
    )

    if slack_id:
        setup_logger.debug(
            f"Mapped Taiga user {taiga_user['id']} to Slack user {slack_id}"
        )
        taiga_users[taiga_user["id"]] = {
            "slack_id": slack_id,
            "project_ids": [],
            "project_names": [],
        }
    else:
        setup_logger.info(f"No Slack user found for Taiga user {taiga_user['id']}")

# Get all Taiga projects via the Taiga api
project_map = {}
url = f"{config['taiga']['url']}/api/v1/projects"
response = requests.get(url, headers={"Authorization": f"Bearer {taiga_auth_token}"})

if response.status_code != 200:
    setup_logger.error(f"Failed to get Taiga projects: {response.status_code}")
    sys.exit(1)

for project in response.json():
    project_map[project["id"]] = {"name": project["slug"]}
    project_map[project["id"]]["private"] = project["is_private"]
    setup_logger.debug(f"Checking project {project['id']}")
    for member in project["members"]:
        if member in taiga_users:
            taiga_users[member]["project_ids"].append(project["id"])
            taiga_users[member]["project_names"].append(project["slug"])


taiga_slack_users = [
    user_info["slack_id"]
    for user_info in taiga_users.values()
    if "slack_id" in user_info
]

# Check over slack channels and look for ones that have corresponding boards
slack_channels = app.client.conversations_list()["channels"]
for channel in slack_channels:
    logger.debug(f"Checking channel {channel['name']}")
    if channel["name"] not in [
        project_info["name"] for project_info in project_map.values()
    ]:
        continue

    # Find the project ID that matches the channel name
    project_id = None
    for pid, project_info in project_map.items():
        if project_info["name"] == channel["name"]:
            project_id = pid
            break

    logger.info(
        f"Found channel {channel['name']} with corresponding board ({project_id})"
    )

    # Get the channel's members
    channel_members = app.client.conversations_members(channel=channel["id"])["members"]

    # Check if any of the channel's members exist in taiga_users
    for member in channel_members:
        if member in taiga_slack_users:
            member_name = slack.name_mapper(slack_id=member, slack_app=app)
            logger.debug(
                f"Found Taiga user {member_name} ({member}) in channel #{channel['name']}"
            )

            # Get the taiga ID from the slack ID
            # This is computationally expensive but easier to understand
            taiga_id = tidyhq.map_slack_to_taiga(
                tidyhq_cache=tidyhq_cache, slack_id=member, config=config
            )
            logger.debug(f"Taiga ID for user {member_name} ({member}) is {taiga_id}")

            # Check if the user is a member of the project
            if channel["name"] in taiga_users[taiga_id]["project_names"]:
                logger.debug(
                    f"User {member_name} ({member}) is a member of the project"
                )
                continue
            logger.info(f"User {member_name} ({member}) is not a member of the project")
            # Check if the board is private
            if project_map[project_id]["private"]:
                logger.info(f"Project {channel['name']} is private")

                # Check if the channel is also private
                if not channel["is_private"]:
                    logger.info(f"Channel #{channel['name']} not private")
                    continue

                logger.info("Both the board and channel are private")

            logger.info(
                f"Adding user {member_name} ({member}) to project {channel['name']}"
            )

            # Add the user to the project
            # "Dry run" totally isn't just because I haven't written this part yet
            logger.warning("This is a dry run. No changes have been made")
