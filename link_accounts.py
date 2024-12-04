import requests
import logging
import json
import sys
from slack_bolt import App
from datetime import datetime
from pprint import pprint, pformat
import time

from taiga import TaigaAPI

from util import slack, taigalink, tidyhq, blocks, slack_formatters

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


def notify(message: str, blocks: list, slack_app):
    slack_app.client.chat_postMessage(
        channel=config["taiga-channel"]["4"],
        text=message,
        blocks=blocks,
        unfurl_links=False,
        unfurl_media=False,
    )


def construct_link_blocks(tidyhq_id, tidyhq_name, taiga_username, method):
    block_list = []
    block_list = slack_formatters.add_block(block_list=block_list, block=blocks.text)
    block_list = slack_formatters.inject_text(
        block_list=block_list,
        text=f"Linking TidyHQ contact <{tidyhq_user_url.format(contact_id=tidyhq_id)}|{tidyhq_name}> to Taiga user <{taiga_user_url.format(username=taiga_username)}|@{taiga_username}>",
    )
    block_list = slack_formatters.add_block(block_list=block_list, block=blocks.context)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text=f"This match was performed by: {method}"
    )
    return block_list


# Load config
try:
    with open("config.json") as f:
        config = json.load(f)
except FileNotFoundError:
    setup_logger.error(
        "config.json not found. Create it using example.config.json as a template"
    )
    sys.exit(1)

# Look for cron mode
cron_mode = False
if "--cron" in sys.argv:
    cron_mode = True

# Look for fresh mode
if "--fresh" in sys.argv:
    config["cache_expiry"] = 1

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

# Set up slack
app = App(token=config["slack"]["bot_token"], logger=slack_logger)

# Calculate URLs for users
taiga_user_url = f"{config['taiga']['url']}/profile/{{username}}"
# Get the TidyHQ domain prefix
r = requests.get(
    url="https://api.tidyhq.com/v1/organization",
    headers={"Authorization": f"Bearer {config['tidyhq']['token']}"},
)
if r.status_code == 200:
    tidyhq_domain = r.json()["domain_prefix"]
else:
    setup_logger.error(f"Failed to get TidyHQ domain: {r.status_code}")
    sys.exit(1)
tidyhq_user_url = f"https://{tidyhq_domain}.tidyhq.com/contacts/{{contact_id}}"

# Get all Taiga users via the Taiga api
url = f"{config['taiga']['url']}/api/v1/users"

response = requests.get(url, headers={"Authorization": f"Bearer {taiga_auth_token}"})

if response.status_code == 200:
    taiga_users_raw = response.json()

else:
    setup_logger.error(f"Failed to get Taiga users: {response.status_code}")
    sys.exit(1)

setup_logger.info(f"Got {len(taiga_users_raw)} Taiga users")

taiga_users = {}

for user in taiga_users_raw:
    # Get more detailed information about the user
    url = f"{config['taiga']['url']}/api/v1/users/{user['id']}"
    response = requests.get(
        url, headers={"Authorization": f"Bearer {taiga_auth_token}"}
    )
    if response.status_code == 200:
        user_info = response.json()
    else:
        setup_logger.error(
            f"Failed to get user info for {user['id']}: {response.status_code}"
        )
        continue

    # Check if the user has an email address
    if user_info.get("email"):
        taiga_users[user_info["email"]] = {
            "taiga": user["id"],
            "username": user["username"],
        }

first_count = len(taiga_users)

# Iterate through all TidyHQ contacts looking for ones that have a Taiga ID set
for contact in tidyhq_cache["contacts"]:
    taiga_field = tidyhq.get_custom_field(
        config=config,
        contact_id=contact["id"],
        cache=tidyhq_cache,
        field_map_name="taiga",
    )
    if not taiga_field:
        continue

    if contact["id"] == 1952718:
        continue

    # IDs are stored as strings in TidyHQ
    taiga_id = int(taiga_field["value"])

    # Look for the Taiga user in the Taiga users list and remove it if it's already assigned to a TidyHQ contact
    for email, user in taiga_users.items():
        if user["taiga"] == taiga_id:
            taiga_users.pop(email)
            break

logger.info(
    f"Taiga users that need to be linked to TidyHQ contacts: {len(taiga_users)}/{first_count}"
)

# Search through TidyHQ contacts for ones that have a matching email address
for contact in tidyhq_cache["contacts"]:
    email = contact["email_address"]
    if email in taiga_users:
        logger.info(f"Linking {email} to Taiga user {taiga_users[email]['taiga']}")
        # Set the Taiga ID field on the contact
        setting = tidyhq.set_custom_field(
            config=config,
            contact_id=contact["id"],
            field_map_name="taiga",
            value=taiga_users[email]["taiga"],
        )

        if setting:
            logger.info(f"Set Taiga ID on contact {contact['id']}")
            # Remove the user from the list
            taiga_users.pop(email)
            notify_blocks = construct_link_blocks(
                tidyhq_id=contact["id"],
                tidyhq_name=contact["display_name"],
                taiga_username=taiga_users[email]["username"],
                method="Email address matching",
            )
            notify(
                message=f"Linking TidyHQ contact {contact['display_name']} to Taiga user {taiga_users[email]['taiga']}",
                blocks=notify_blocks,
                slack_app=app,
            )
        else:
            logger.error(f"Failed to set Taiga ID on contact {contact['id']}")

logger.info(f"Auto linking complete")
logger.info(f"Taiga users remaining: {len(taiga_users)}/{first_count}")

# If we're running in cron mode, that's it
if cron_mode:
    sys.exit(0)

removing = []
for user in taiga_users:
    logger.info(f"Taiga user {user} not linked to a TidyHQ contact")
    tidyhq_id = input(
        "Enter the TidyHQ contact ID to link this user to (Leave blank to skip): "
    )
    if not tidyhq_id:
        continue

    # Get the contact info using the ID
    for current_contact in tidyhq_cache["contacts"]:
        if current_contact["id"] == int(tidyhq_id):
            contact = current_contact
            break
    else:
        logger.error(f"Contact {tidyhq_id} not found")
        continue

    tidyhq_id = tidyhq_id.strip()
    setting = tidyhq.set_custom_field(
        config=config,
        contact_id=tidyhq_id,
        field_map_name="taiga",
        value=taiga_users[user]["taiga"],
    )
    if setting:
        logger.info(f"Set Taiga ID on contact {tidyhq_id}")
        removing.append(user)
        notify_blocks = construct_link_blocks(
            tidyhq_id=tidyhq_id,
            tidyhq_name=contact["display_name"],
            taiga_username=taiga_users[user]["username"],
            method="Manual linking",
        )
        notify(
            message=f"Linking TidyHQ contact {contact['display_name']} to Taiga user {taiga_users[user]['username']}",
            blocks=notify_blocks,
            slack_app=app,
        )
    else:
        logger.error(f"Failed to set Taiga ID on contact {tidyhq_id}")

for user in removing:
    taiga_users.pop(user)

logger.info(f"Manual linking complete")
logger.info(f"Taiga users remaining: {len(taiga_users)}/{first_count}")
