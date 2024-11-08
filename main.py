import json
import logging
import sys
from pprint import pprint

import requests
from taiga import TaigaAPI

from util import templates, tidyhq

# Set up logging
logging.basicConfig(level=logging.INFO)
# Set urllib3 logging level to INFO
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)

# Load config
with open("config.json") as f:
    config = json.load(f)

# This is used instead of python-taiga's inbuilt user/pass login method since we also need to interact with the api directly
# Get auth token
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
    logging.error(f"Failed to get auth token: {response.status_code}")
    sys.exit(1)

taigacon = TaigaAPI(host=config["taiga"]["url"], token=taiga_auth_token)

# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
logging.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)

# Sync templates
logging.info("Syncing templates")
templates.sync_templates(taigacon=taigacon)

# Map email addresses to TidyHQ
logging.info("Mapping email addresses to TidyHQ")
# change log level to debug to see more detailed output
tidyhq.email_to_tidyhq(
    config=config,
    tidyhq_cache=tidyhq_cache,
    taigacon=taigacon,
    taiga_auth_token=taiga_auth_token,
)
