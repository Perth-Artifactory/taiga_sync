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


questions = []

form = "injury"

if form == "injury":
    questions.append(
        {"text": "Please answer the following questions to the best of your ability."}
    )
    questions.append(
        {
            "type": "long",
            "text": "What happened?",
            "placeholder": "In the event of a near miss, what was the potential outcome?",
        }
    )
    questions.append({"type": "multi_users_select", "text": "Who was involved?"})
    questions.append({"type": "date", "text": "When did it happen?"})
    questions.append(
        {
            "type": "short",
            "text": "Where in the space did the incident occur?",
            "placeholder": "e.g. Machine Room, Project Area etc",
        }
    )
    questions.append(
        {"type": "multi_users_select", "text": "Did anyone witness the incident?"}
    )
    questions.append(
        {
            "type": "long",
            "text": "Were there any injuries?",
            "placeholder": "Include a description of injuries if applicable",
        }
    )
    questions.append(
        {
            "type": "long",
            "text": "Was there any damage to property?",
            "placeholder": "e.g. tools, equipment, buildings, personal belongings",
        }
    )

    questions.append(
        {
            "type": "long",
            "text": "What factors contributed to the incident?",
            "placeholder": "e.g. environmental conditions, equipment failure, human error",
        }
    )

    questions.append(
        {
            "type": "long",
            "text": "Were there any immediate corrective actions taken at the time of the incident?",
            "placeholder": "e.g. first aid, stopping work, isolating equipment",
        }
    )

    questions.append(
        {
            "type": "long",
            "text": "What controls could be put in place to prevent this from happening again?",
            "placeholder": "e.g. training, signage, engineering controls",
        }
    )

    questions.append(
        {
            "type": "static_dropdown",
            "text": "Would you like us to contact you regarding the outcome of this report?",
            "options": ["Yes", "No"],
            "action_id": "contact",
        }
    )


else:
    questions.append({"text": "This is some explainer text"})

block_list = slack_forms.questions_to_blocks(questions)

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
