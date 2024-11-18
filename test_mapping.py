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
logging.basicConfig(level=logging.DEBUG)
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

# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
setup_logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)

# Test each of the mapping functions in tidyhq

taiga_to_tidyhq = tidyhq.map_taiga_to_tidyhq(
    tidyhq_cache=tidyhq_cache, config=config, taiga_id=5
)
tidyhq_to_taiga = tidyhq.map_tidyhq_to_taiga(
    tidyhq_cache=tidyhq_cache, config=config, tidyhq_id=1952718
)
slack_to_tidyhq = tidyhq.map_slack_to_tidyhq(
    tidyhq_cache=tidyhq_cache, config=config, slack_id="UC6T4U150"
)
slack_to_taiga = tidyhq.map_slack_to_taiga(
    tidyhq_cache=tidyhq_cache, config=config, slack_id="UC6T4U150"
)
taiga_to_slack = tidyhq.map_taiga_to_slack(
    tidyhq_cache=tidyhq_cache, config=config, taiga_id=5
)


print(f"taiga_to_tidyhq: {taiga_to_tidyhq}")
assert taiga_to_tidyhq == "1952718"
print(f"tidyhq_to_taiga: {tidyhq_to_taiga}")
assert tidyhq_to_taiga == 5
print(f"slack_to_tidyhq: {slack_to_tidyhq}")
assert slack_to_tidyhq == "1952718"
print(f"slack_to_taiga: {slack_to_taiga}")
assert slack_to_taiga == 5
print(f"taiga_to_slack: {taiga_to_slack}")
assert taiga_to_slack == "UC6T4U150"
