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

div("TidyHQ Caches")
# Set up TidyHQ cache
if config.get("tidyproxy"):
    logger.info("Setting up TidyHQ cache from tidyproxy")
    try:
        os.remove("cache.json")
    except FileNotFoundError:
        pass
    except PermissionError:
        logger.error("Failed to remove cache.json")
    start_time = time.time()
    tidyhq_cache = tidyhq.fresh_cache(config=config)
    end_time = time.time()
    assert isinstance(tidyhq_cache, dict), f"tidyhq_cache: {tidyhq_cache}"
    assert "contacts" in tidyhq_cache, (
        f"'Contacts' not found in cache of keys: {tidyhq_cache.keys()}"
    )
    logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
else:
    logger.info("Tidyproxy test skipped as not found in config")

# Test loading the cache from file
logger.info("Setting up TidyHQ cache from file")
start_time = time.time()
tidyhq.fresh_cache(config=config)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

if "--long" in sys.argv:
    # Test loading the cache from TidyHQ
    fake_config = copy(config)
    if "tidyproxy" in fake_config:
        del fake_config["tidyproxy"]
    try:
        os.remove("cache.json")
    except FileNotFoundError:
        pass
    logger.info("Setting up TidyHQ cache from TidyHQ")
    start_time = time.time()
    tidyhq_cache = tidyhq.fresh_cache(config=fake_config)
    end_time = time.time()
    logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")
else:
    logger.info("Skipped loading cache direct from TidyHQ, use --long to enable")

div("Taiga cache")
# Set up Taiga cache
logger.info("Setting up Taiga cache")
start_time = time.time()
taiga_cache = taigalink.setup_cache(
    config=config, taiga_auth_token=taiga_auth_token, taigacon=taigacon
)
end_time = time.time()
assert isinstance(taiga_cache, dict), f"taiga_cache: {taiga_cache}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Write cache to file for debugging
with open("taiga_cache.json", "w") as f:
    json.dump(taiga_cache, f)

################## Placeholder for test functions ##################


################## Placeholder for test functions ##################


div("Account mapping")
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

div("App homes")
# Generate app home block list for a non existent user
logger.info("Generating app home block list for a non existent user")
start_time = time.time()
block_list = block_formatters.app_home(
    user_id="UXXX",
    config=config,
    tidyhq_cache=tidyhq_cache,
    taiga_cache=taiga_cache,
    taiga_auth_token=taiga_auth_token,
    private_metadata="",
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Generate app home for low frequency Taiga user
logger.info("Generating app home for low frequency Taiga user")
start_time = time.time()
block_list = block_formatters.app_home(
    user_id="U06PX5QRKRQ",
    config=config,
    tidyhq_cache=tidyhq_cache,
    taiga_cache=taiga_cache,
    taiga_auth_token=taiga_auth_token,
    private_metadata="",
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Generate app home for high frequency Taiga user
logger.info("Generating app home for high frequency Taiga user")
start_time = time.time()
block_list = block_formatters.app_home(
    user_id="UC6T4U150",
    config=config,
    tidyhq_cache=tidyhq_cache,
    taiga_cache=taiga_cache,
    taiga_auth_token=taiga_auth_token,
    private_metadata="",
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

div("View/edit modal")
# Generate the viewedit modal for a simple task
logger.info("Generating viewedit modal for a simple user story")
start_time = time.time()
block_list = block_formatters.viewedit_blocks(
    taigacon=taigacon,
    project_id=3,
    item_id=156,
    item_type="story",
    taiga_cache=taiga_cache,
    config=config,
    taiga_auth_token=taiga_auth_token,
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Generate the viewedit modal for a complex task
logger.info("Generating viewedit modal for an item with lots of tasks")
start_time = time.time()
block_list = block_formatters.viewedit_blocks(
    taigacon=taigacon,
    project_id=1,
    item_id=36,
    item_type="story",
    taiga_cache=taiga_cache,
    config=config,
    taiga_auth_token=taiga_auth_token,
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

div("Edit modal")
# Generate the edit modal for a simple user story
logger.info("Generating edit modal for a simple user story")
start_time = time.time()
block_list = block_formatters.edit_info_blocks(
    taigacon=taigacon,
    project_id=3,
    item_id=156,
    item_type="story",
    taiga_cache=taiga_cache,
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Generate the edit modal for a complex user story
logger.info("Generating edit modal for a complex user story")
start_time = time.time()
block_list = block_formatters.edit_info_blocks(
    taigacon=taigacon,
    project_id=5,
    item_id=204,
    item_type="story",
    taiga_cache=taiga_cache,
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

div("Forms")
# Test reloading of forms
logger.info("Reloading forms")
start_time = time.time()
importlib.reload(forms)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Render the form modal for a non member
logger.info("Rendering form modal for a non member")
start_time = time.time()
block_list = block_formatters.render_form_list(
    form_list=forms.forms, member=False, emoji="artifactory"
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Render the form modal for a member
logger.info("Rendering form modal for a member")
start_time = time.time()
block_list = block_formatters.render_form_list(
    form_list=forms.forms, member=True, emoji="artifactory"
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Render a specific form modal
logger.info("Rendering a specific form modal")
start_time = time.time()
block_list = block_formatters.questions_to_blocks(
    questions=forms.forms["infra"]["questions"],
    taigacon=taigacon,
    taiga_cache=taiga_cache,
    taiga_project="Infrastructure",
)
end_time = time.time()
assert slack_misc.validate(blocks=block_list), "Generated block list invalid"
assert len(block_list) > 2, f"Block list too short: {len(block_list)}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

div("Membership info")
# Retrieve the type of membership held by a member
logger.info("Retrieving membership type for a member")
start_time = time.time()
membership_type = tidyhq.get_membership_type(
    contact_id="1952718", tidyhq_cache=tidyhq_cache
)
end_time = time.time()
assert membership_type != None, f"membership_type: {membership_type}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

# Retrieve the type of membership held by a non member
logger.info("Retrieving membership type for a non member")
start_time = time.time()
membership_type = tidyhq.get_membership_type(
    contact_id="17801", tidyhq_cache=tidyhq_cache
)
end_time = time.time()
assert membership_type in [None, "Expired"], f"membership_type: {membership_type}"
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms")

div("Taiga retrieval (python-taiga vs direct)")
# Find a project
logger.info("Finding a project")
# python-taiga
start_time = time.time()
projects = taigacon.projects.list()
for p in projects:
    if p.id == 5:
        project = p
        break
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (python-taiga)")
# Direct
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/projects/5",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
)
project_r = response.json()
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (direct)")
assert project.id == project_r["id"], (
    f"python-taiga: {project.id} direct: {project_r['id']}"
)

# Get the statuses of that project
logger.info("Getting statuses for a project")
# python-taiga
start_time = time.time()
statuses = taigacon.user_story_statuses.list(project=project.id)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (python-taiga)")
# Direct
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/userstory-statuses",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
    params={"project": 5},
)
statuses_r = response.json()
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (direct)")
assert len(statuses) == len(statuses_r), (
    f"python-taiga: {len(statuses)} direct: {len(statuses_r)}"
)

# Getting the user stories for a small project
logger.info("Getting user stories for a small project")
# python-taiga
start_time = time.time()
user_stories = taigacon.user_stories.list(project=3)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (python-taiga)")
# Direct
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/userstories",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
    params={"project": 3},
)
stories_r = response.json()
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (direct)")
assert len(user_stories) == len(stories_r), (
    f"python-taiga: {len(user_stories)} direct: {len(stories_r)}"
)

# Getting the user stories for a medium sized project
logger.info("Getting user stories for a normal project")
# python-taiga
start_time = time.time()
user_stories = taigacon.user_stories.list(project=2)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (python-taiga)")
# Direct
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/userstories",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
    params={"project": 2},
)
stories_r = response.json()
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (direct)")
assert len(user_stories) == len(stories_r), (
    f"python-taiga: {len(user_stories)} direct: {len(stories_r)}"
)

# Getting the user stories for a large project
logger.info("Getting user stories for a large project")
# python-taiga
start_time = time.time()
user_stories = taigacon.user_stories.list(project=1)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (python-taiga)")
# Direct
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/userstories",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
    params={"project": 1},
)
stories_r = response.json()
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (direct)")
assert len(user_stories) == len(stories_r), (
    f"python-taiga: {len(user_stories)} direct: {len(stories_r)}"
)

# Get the comments for a user story
logger.info("Getting comments for a user story")
# python-taiga
start_time = time.time()
comments: list = taigacon.history.user_story.get(resource_id=204)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (python-taiga)")
# Direct
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/history/userstory/204",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
)
comments_r = response.json()
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (direct)")
assert len(comments) == len(comments_r), (
    f"python-taiga: {len(comments)} direct: {len(comments_r)}"
)

# Get the info for a user story
logger.info("Getting info for a user story")
# python-taiga
start_time = time.time()
story = taigacon.user_stories.get(204)
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (python-taiga)")
# Direct
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/userstories/204",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
)
story_r = response.json()
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (direct)")

div("Local processing vs API params")
logger.info("Get all tasks assigned to user in a project")
# Remote processing
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/tasks",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
    params={"assigned_to": 5},
)
remote_tasks = response.json()
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (remote)")
# Local processing
start_time = time.time()
response = requests.get(
    f"{config['taiga']['url']}/api/v1/tasks",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
)
all_tasks = response.json()
local_tasks = [t for t in all_tasks if t["assigned_to"] == 5]
end_time = time.time()
logger.info(f"Time taken: {(end_time - start_time) * 1000:.2f}ms (local)")
assert len(remote_tasks) == len(local_tasks), (
    f"remote_tasks: {len(remote_tasks)} local_tasks: {len(local_tasks)}"
)

div("Taiga DB validation")
# Validate that all items with an assigned_users field also have an assigned_to field
logger.info("Validate assigned_to field")
response = requests.get(
    f"{config['taiga']['url']}/api/v1/userstories",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
)
stories = response.json()
for story in stories:
    if story.get("assigned_users"):
        url = f"{config['taiga']['url']}/project/{story['project_extra_info']['slug']}/us/{story['ref']}"
        if not story.get("assigned_to") and story["assigned_users"]:
            print(url)
