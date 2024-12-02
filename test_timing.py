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

from util import slack, slack_formatters, slack_home, taigalink, tidyhq


def div():
    print("\n" + "-" * 80 + "\n")


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
if config.get("tidyproxy"):
    logger.info("Setting up TidyHQ cache from tidyproxy")
    os.remove("cache.json")
    start_time = time.time()
    tidyhq_cache = tidyhq.fresh_cache(config=config)
    end_time = time.time()
    assert isinstance(tidyhq_cache, dict), f"tidyhq_cache: {tidyhq_cache}"
    assert (
        "contacts" in tidyhq_cache
    ), f"'Contacts' not found in cache of keys: {tidyhq_cache.keys()}"
    logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
else:
    logger.info("Tidyproxy test skipped as not found in config")
div()

# Test loading the cache from file
logger.info("Setting up TidyHQ cache from file")
start_time = time.time()
tidyhq.fresh_cache(config=config)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
div()

if "--long" in sys.argv:
    # Test loading the cache from TidyHQ
    fake_config = copy(config)
    if "tidyproxy" in fake_config:
        del fake_config["tidyproxy"]
    os.remove("cache.json")
    logger.info("Setting up TidyHQ cache from TidyHQ")
    start_time = time.time()
    tidyhq_cache = tidyhq.fresh_cache(config=fake_config)
    end_time = time.time()
    logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
    div()
else:
    logger.info("Pass --long to test loading the cache from TidyHQ")
    div()

# Test each of the mapping functions in tidyhq
logger.info("Testing mapping functions")

start_time = time.time()
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
end_time = time.time()

assert taiga_to_tidyhq == "1952718", f"taiga_to_tidyhq: {taiga_to_tidyhq}"
assert tidyhq_to_taiga == 5, f"tidyhq_to_taiga: {tidyhq_to_taiga}"
assert slack_to_tidyhq == "1952718", f"slack_to_tidyhq: {slack_to_tidyhq}"
assert slack_to_taiga == 5, f"slack_to_taiga: {slack_to_taiga}"
assert taiga_to_slack == "UC6T4U150", f"taiga_to_slack: {taiga_to_slack}"

logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
div()

# Generate app home block list for a non existent user
logger.info("Generating app home block list for a non existent user")
start_time = time.time()
block_list = slack_home.generate_app_home(
    user_id="UXXX",
    config=config,
    tidyhq_cache=tidyhq_cache,
    taiga_auth_token=taiga_auth_token,
)
end_time = time.time()
assert slack_formatters.validate(blocks=block_list), f"Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
div()

# Generate app home for low frequency Taiga user
logger.info("Generating app home for low frequency Taiga user")
start_time = time.time()
block_list = slack_home.generate_app_home(
    user_id="U06PX5QRKRQ",
    config=config,
    tidyhq_cache=tidyhq_cache,
    taiga_auth_token=taiga_auth_token,
)
end_time = time.time()
assert slack_formatters.validate(blocks=block_list), f"Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
div()

# Generate app home for high frequency Taiga user
logger.info("Generating app home for high frequency Taiga user")
start_time = time.time()
block_list = slack_home.generate_app_home(
    user_id="UC6T4U150",
    config=config,
    tidyhq_cache=tidyhq_cache,
    taiga_auth_token=taiga_auth_token,
)
end_time = time.time()
assert slack_formatters.validate(blocks=block_list), f"Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
div()

# Generate the viewedit modal for a simple task
logger.info("Generating viewedit modal for a simple user story")
start_time = time.time()
block_list = slack_home.viewedit_blocks(
    taigacon=taigacon, project_id=3, item_id=156, item_type="story"
)
end_time = time.time()
assert slack_formatters.validate(blocks=block_list), f"Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
div()

# Generate the viewedit modal for a complex task
logger.info("Generating viewedit modal for an item with lots of tasks")
start_time = time.time()
block_list = slack_home.viewedit_blocks(
    taigacon=taigacon, project_id=1, item_id=36, item_type="story"
)
end_time = time.time()
assert slack_formatters.validate(blocks=block_list), f"Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
div()

# Generate the edit modal for a simple user story
logger.info("Generating edit modal for a simple user story")
start_time = time.time()
block_list = slack_home.edit_info_blocks(
    taigacon=taigacon, project_id=3, item_id=156, item_type="story"
)
end_time = time.time()
assert slack_formatters.validate(blocks=block_list), f"Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
div()

# Generate the edit modal for a complex user story
logger.info("Generating edit modal for a complex user story")
start_time = time.time()
block_list = slack_home.edit_info_blocks(
    taigacon=taigacon, project_id=5, item_id=204, item_type="story"
)
end_time = time.time()
assert slack_formatters.validate(blocks=block_list), f"Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
