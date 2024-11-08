import json
import logging
import sys
from pprint import pprint

import requests
from taiga import TaigaAPI

from util import taiga_janitor, tidyhq, tasks

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

# Find the Attendee project
projects = taigacon.projects.list()
attendee_project = None
for project in projects:
    if project.name == "Attendee":
        attendee_project = project
        break

if not attendee_project:
    logging.error("Attendee project not found")
    sys.exit(1)

logging.debug(f"Attendee project found: {attendee_project.id}")

# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
logging.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)

# Enter processing loop
template_changes = False
email_mapping_changes = False
task_changes = False
progress_changes = False
first = True

logging.info("Starting processing loop")
iteration = 1
while (
    first
    or template_changes
    or email_mapping_changes
    or task_changes
    or progress_changes
):
    if not first:
        logging.info(f"Iteration: {iteration}")
    else:
        first = False

    # Sync templates
    logging.info("Syncing templates")
    logging.getLogger().setLevel(logging.ERROR)
    template_changes = taiga_janitor.sync_templates(
        taigacon=taigacon, project_id=attendee_project.id
    )
    logging.getLogger().setLevel(logging.INFO)

    # Map email addresses to TidyHQ
    logging.info("Mapping email addresses to TidyHQ")
    logging.getLogger().setLevel(logging.ERROR)
    email_mapping_changes = tidyhq.email_to_tidyhq(
        config=config,
        tidyhq_cache=tidyhq_cache,
        taigacon=taigacon,
        taiga_auth_token=taiga_auth_token,
        project_id=attendee_project.id,
    )
    logging.getLogger().setLevel(logging.INFO)

    # Run through tasks
    logging.info("Checking all tasks")

    logging.getLogger().setLevel(logging.DEBUG)
    task_changes = tasks.check_all_tasks(
        taigacon=taigacon,
        taiga_auth_token=taiga_auth_token,
        config=config,
        tidyhq_cache=tidyhq_cache,
        project_id=attendee_project.id,
    )
    logging.getLogger().setLevel(logging.INFO)

    logging.info("Progressing user stories")
    logging.getLogger().setLevel(logging.ERROR)
    progress_changes = taiga_janitor.progress_stories(
        taigacon=taigacon,
        project_id=attendee_project.id,
        taiga_auth_token=taiga_auth_token,
        config=config,
    )
    logging.getLogger().setLevel(logging.INFO)

    iteration += 1
