import importlib
import json
import logging
import os
import sys
import time
from copy import deepcopy as copy
from datetime import datetime
from pprint import pformat, pprint

import requests
from slack_bolt import App
from taiga import TaigaAPI

from editable_resources import forms
from util import taigalink, tidyhq, misc
from slack import block_formatters
from slack import misc as slack_misc


def div(title: str | None = None):
    """Print a divider with an optional title"""

    # Center the title
    if title:
        title = f" {title} "
        title = title.center(80, "=")
        print(title)
    else:
        print("=" * 80)


# Set up logging
logging.basicConfig(level=logging.DEBUG)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
# Set slack bolt logging level to INFO to reduce noise when individual modules are set to debug
slack_logger = logging.getLogger("slack")
slack_logger.setLevel(logging.INFO)
setup_logger = logging.getLogger("setup")
logger = logging.getLogger("timing")

# Load config
try:
    with open("config.json", "r") as f:
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

# Set up caches
tidyhq_cache = tidyhq.fresh_cache(config=config)
taiga_cache = taigalink.setup_cache(
    config=config, taiga_auth_token=taiga_auth_token, taigacon=taigacon
)

# Write cache to file for debugging
with open("taiga_cache.json", "w") as f:
    json.dump(taiga_cache, f)

##########################
