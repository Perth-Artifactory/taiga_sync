import json
import logging
import os
import sys
from pprint import pprint

import requests
from taiga import TaigaAPI

from util import conditional_closing, intake, taiga_janitor, tasks, tidyhq

# Set up logging
logging.basicConfig(level=logging.INFO)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
setup_logger = logging.getLogger("setup")
loop_logger = logging.getLogger("loop")
postloop_logger = logging.getLogger("postloop")


# Look for the --no-import flag
import_from_tidyhq = False
if "--import" in sys.argv:
    import_from_tidyhq = True


# Look for --force flag
force = False
if "--force" in sys.argv:
    force = True

# Look for a attendee.lock file
if not force:
    try:
        with open("attendee.lock") as f:
            setup_logger.error(
                "attendee.lock found. Exiting to prevent concurrent runs"
            )
            sys.exit(1)
    except FileNotFoundError:
        pass

# Create main.lock file
with open("attendee.lock", "w") as f:
    f.write("")
    setup_logger.info("attendee.lock created")


# Load config
try:
    with open("config.json") as f:
        config = json.load(f)
except FileNotFoundError:
    setup_logger.error(
        "config.json not found. Create it using example.config.json as a template"
    )
    sys.exit(1)

# Check for required Taiga config values
if not all(key in config["taiga"] for key in ["url", "username", "password"]):
    setup_logger.error(
        "Missing required config values in taiga section. Check config.json"
    )
    sys.exit(1)

# Check for required TidyHQ config values
if not all(
    key in config["tidyhq"] for key in ["token", "ids", "group_ids", "training_prefix"]
):
    setup_logger.error(
        "Missing required config values in tidyhq section. Check config.json"
    )
    sys.exit(1)

# Check for cache expiry and set if not present
if "cache_expiry" not in config:
    config["cache_expiry"] = 86400
    setup_logger.error("Cache expiry not set in config. Defaulting to 24 hours")


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


# Find the Attendee project
projects = taigacon.projects.list()
attendee_project = None
for project in projects:
    if project.name == "Attendee":
        attendee_project = project
        break

if not attendee_project:
    setup_logger.error("Attendee project not found")
    sys.exit(1)

setup_logger.debug(f"Attendee project found: {attendee_project.id}")


# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
setup_logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)


# Enter processing loop
email_mapping_changes = False
intake_from_tidyhq = False
template_changes = False
task_changes = False
progress_changes = False
closed_by_status = False
progress_on_signup = False
first = True

loop_logger.info("Starting processing loop")
iteration = 1
while (
    first
    or email_mapping_changes
    or intake_from_tidyhq
    or template_changes
    or task_changes
    or progress_changes
    or closed_by_status
    or progress_on_signup
):
    if not first:
        loop_logger.info(f"Iteration: {iteration}")
        # Show which modules made changes in the last iteration
        loop_logger.info("Changes made in the last iteration:")
        if email_mapping_changes:
            loop_logger.info("Email mapping")
        if intake_from_tidyhq:
            loop_logger.info("Intake from TidyHQ")
        if template_changes:
            loop_logger.info("Templates")
        if task_changes:
            loop_logger.info("Tasks")
        if progress_changes:
            loop_logger.info("Progress")
        if closed_by_status:
            loop_logger.info("Closed by status")
        if progress_on_signup:
            loop_logger.info("Progress on signup")
        loop_logger.info("---")
    else:
        first = False

    # Map email addresses to TidyHQ
    loop_logger.info("Mapping email addresses to TidyHQ")
    email_mapping_changes = tidyhq.email_to_tidyhq(
        config=config,
        tidyhq_cache=tidyhq_cache,
        taigacon=taigacon,
        taiga_auth_token=taiga_auth_token,
        project_id=attendee_project.id,
    )

    # Create new cards based on existing TidyHQ contacts
    if import_from_tidyhq:
        loop_logger.info("Creating cards for TidyHQ contacts")
        intake_from_tidyhq = intake.pull_tidyhq(
            config=config,
            tidyhq_cache=tidyhq_cache,
            taigacon=taigacon,
            taiga_auth_token=taiga_auth_token,
            project_id=attendee_project.id,
        )
    else:
        loop_logger.info("Skipping TidyHQ import due to --no-import flag")

    # Sync templates
    loop_logger.info("Syncing templates")
    template_changes = taiga_janitor.sync_templates(
        taigacon=taigacon, project_id=attendee_project.id
    )

    # Run through tasks
    loop_logger.info("Checking all tasks")
    task_changes = tasks.check_all_tasks(
        taigacon=taigacon,
        taiga_auth_token=taiga_auth_token,
        config=config,
        tidyhq_cache=tidyhq_cache,
        project_id=attendee_project.id,
    )

    # Progress user stories based on task completion
    loop_logger.info("Progressing user stories")
    progress_changes = taiga_janitor.progress_stories(
        taigacon=taigacon,
        project_id=attendee_project.id,
        taiga_auth_token=taiga_auth_token,
        config=config,
    )

    # Close tasks based on story status
    loop_logger.info("Checking for tasks that can be closed based on story status")
    closed_by_status = conditional_closing.close_by_status(
        taigacon=taigacon,
        project_id=attendee_project.id,
        config=config,
        taiga_auth_token=taiga_auth_token,
    )

    # Move tasks from column 1 to 2 if they have a TidyHQ ID
    loop_logger.info(
        "Checking for user stories that can progress to attendee based on TidyHQ signup"
    )
    progress_on_signup = taiga_janitor.progress_on_signup(
        taigacon=taigacon,
        project_id=attendee_project.id,
        taiga_auth_token=taiga_auth_token,
        config=config,
    )

    iteration += 1

# Perform once off housekeeping tasks
# These tasks have no potential to trigger further processing


# Add helper fields to user stories
postloop_logger.info("Adding helper fields to user stories")
taiga_janitor.add_useful_fields(
    taigacon=taigacon,
    project_id=attendee_project.id,
    taiga_auth_token=taiga_auth_token,
    config=config,
    tidyhq_cache=tidyhq_cache,
)

# Delete main.lock
postloop_logger.info("Removing attendee.lock")
os.remove("attendee.lock")