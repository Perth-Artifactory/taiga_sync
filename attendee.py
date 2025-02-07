import json
import logging
import os
import sys

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

# Reconstruct status IDs because the Taiga API endpoint for them doesn't work
statuses = {}

# Get all user stories in the Attendee project
stories = taigacon.user_stories.list(project=attendee_project.id)
for story in stories:
    statuses[story.status] = None

# Get information about each status
for status in statuses.keys():
    status_info = taigacon.user_story_statuses.get(status)
    statuses[status] = status_info.to_dict()

story_statuses = statuses

# Get possible task statuses

statuses = taigacon.task_statuses.list(project=attendee_project.id)
task_statuses = {task.id: task.name for task in statuses}

# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
setup_logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)


# Enter processing loop
email_mapping_changes = 0
intake_from_tidyhq = 0
template_changes = 0
task_changes = 0
progress_changes = 0
closed_by_order = 0
progress_on_tidyhq = 0
progress_on_membership = 0
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
    or closed_by_order
    or progress_on_tidyhq
    or progress_on_membership
):
    if not first:
        loop_logger.info(f"Iteration: {iteration}")
        # Show which modules made changes in the last iteration
        loop_logger.info("Changes made in the last iteration:")
        if email_mapping_changes:
            loop_logger.info(
                f"Cards with emails mapped to TidyHQ contacts ({email_mapping_changes} changes)"
            )
        if intake_from_tidyhq:
            loop_logger.info(
                f"TidyHQ members added as cards({intake_from_tidyhq} changes)"
            )
        if template_changes:
            loop_logger.info(
                f"Tasks added to cards that have progressed to a new column ({template_changes} changes)"
            )
        if task_changes:
            loop_logger.info(f"Tasks ticked off via code ({task_changes} changes)")
        if progress_changes:
            loop_logger.info(
                f"Cards moved to a new column due to task completion ({progress_changes} changes)"
            )
        if closed_by_order:
            loop_logger.info(
                f"Tasks closed because a card has progressed to a specific column ({closed_by_order} changes)"
            )
        if progress_on_tidyhq:
            loop_logger.info(
                f"Cards progressed to a new column based on being registered in TidyHQ({progress_on_tidyhq} changes)"
            )
        if progress_on_membership:
            loop_logger.info(
                f"Cards progressed based on TidyHQ memberships ({progress_on_membership} changes)"
            )
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
    loop_logger.info(f"Changes: {email_mapping_changes}")

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
        loop_logger.info(f"Changes: {intake_from_tidyhq}")
    else:
        loop_logger.info("Skipping TidyHQ import due to --no-import flag")

    # Sync templates
    loop_logger.info("Syncing templates")
    template_changes = taiga_janitor.sync_templates(
        taigacon=taigacon, project_id=attendee_project.id
    )
    loop_logger.info(f"Changes: {template_changes}")

    # Run through tasks
    loop_logger.info("Checking all tasks")
    task_changes = tasks.check_all_tasks(
        taigacon=taigacon,
        taiga_auth_token=taiga_auth_token,
        config=config,
        tidyhq_cache=tidyhq_cache,
        project_id=attendee_project.id,
        task_statuses=task_statuses,
    )
    loop_logger.info(f"Changes: {task_changes}")

    # Progress user stories based on task completion
    loop_logger.info("Progressing user stories")
    progress_changes = taiga_janitor.progress_stories(
        taigacon=taigacon,
        project_id=attendee_project.id,
        taiga_auth_token=taiga_auth_token,
        config=config,
        story_statuses=story_statuses,
        task_statuses=task_statuses,
    )
    loop_logger.info(f"Changes: {progress_changes}")

    # Close tasks based on story status
    loop_logger.info("Checking for tasks that can be closed based on story order")
    closed_by_order = conditional_closing.close_by_order(
        taigacon=taigacon,
        project_id=attendee_project.id,
        config=config,
        taiga_auth_token=taiga_auth_token,
        story_statuses=story_statuses,
    )
    loop_logger.info(f"Changes: {closed_by_order}")

    # Move stories from column 2 to 3 if they have a TidyHQ ID
    loop_logger.info(
        "Checking for user stories that can progress to attendee based on TidyHQ signup"
    )
    progress_on_tidyhq = taiga_janitor.progress_on_tidyhq(
        taigacon=taigacon,
        project_id=attendee_project.id,
        taiga_auth_token=taiga_auth_token,
        config=config,
        story_statuses=story_statuses,
    )
    loop_logger.info(f"Changes: {progress_on_tidyhq}")

    # Move stories from column 3 to 4 if they have a membership
    loop_logger.info(
        "Checking for user stories that can progress to attendee based on TidyHQ membership"
    )
    progress_on_membership = taiga_janitor.progress_on_membership(
        taigacon=taigacon,
        project_id=attendee_project.id,
        taiga_auth_token=taiga_auth_token,
        config=config,
        story_statuses=story_statuses,
        tidyhq_cache=tidyhq_cache,
    )
    loop_logger.info(f"Changes: {progress_on_membership}")
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
