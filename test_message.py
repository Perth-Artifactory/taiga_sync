import requests
import logging
import json
import sys
from slack_bolt import App
from datetime import datetime
from pprint import pprint, pformat
import time
import urllib.parse

from taiga import TaigaAPI

from util import slack, taigalink, blocks, slack_formatters, slack_forms
from editable_resources import forms

# Set up logging
logging.basicConfig(level=logging.INFO)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
# Set slack bolt logging level to INFO to reduce noise when individual modules are set to debug
slack_logger = logging.getLogger("slack")
slack_logger.setLevel(logging.INFO)
setup_logger = logging.getLogger("setup")
logger = logging.getLogger("block validation")

# Check for testing mode
testing = False
if "--testing" in sys.argv:
    testing = True
    logger.info("Running in test mode")

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


questions = []

form = "injury"

if form == "injury":
    form = forms.forms["injury"]


block_list = slack_forms.questions_to_blocks(
    form["questions"], taigacon=taigacon, taiga_project=form.get("taiga_project")
)

# save as blocks.json
with open("test.blocks.json", "w") as f:
    json.dump(block_list, f, indent=4)

# Validate the blocks
if slack_formatters.validate(blocks=block_list):
    logger.info("Blocks are valid")

# Convert blocks to url encoded json
string = json.dumps({"blocks": block_list})
encoded_string = urllib.parse.quote(string)

url = f"https://app.slack.com/block-kit-builder/T0LQE2JNR#{encoded_string}"
print(f"View: {url}")
