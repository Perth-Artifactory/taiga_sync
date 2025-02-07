import json
import logging
import os
import sys

import requests
from taiga import TaigaAPI

# Set up logging
logging.basicConfig(level=logging.INFO)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
setup_logger = logging.getLogger("setup")
logger = logging.getLogger("main")


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

# Map out what the base status of each task should be based on the template
task_base = {}
task_assignee = {}

# Iterate over the project's user stories
stories = taigacon.user_stories.list(project=attendee_project.id)
for story in stories:
    # Check if the story is a template story
    if story.subject == "Template":
        # Get the tasks for the template story
        raw_tasks = taigacon.tasks.list(user_story=story.id)
        for task in raw_tasks:
            if task.subject in task_base:
                setup_logger.error(
                    f"Duplicate task subject found in template: {task.subject}"
                )
                sys.exit(1)
            task_base[task.subject] = task.status
            if task.assigned_to:
                task_assignee[task.subject] = task.assigned_to


setup_logger.info(f"Retrieved {len(task_base)} tasks from templates")
setup_logger.info(f"{len(task_assignee)} tasks have an assignee")

# Map out the names of each task status
task_status = {}
for status in taigacon.task_statuses.list(project=attendee_project.id):
    task_status[status.id] = status.name.lower()

# Get all tasks in the project
# Yes the param is status__is_closed has an extra _ because ⭐️ Taiga ⭐️
all_tasks = taigacon.tasks.list(project=attendee_project.id, status__is_closed=False)

setup_logger.info(f"Retrieved {len(all_tasks)} tasks from project")

# Really we should be looking for the bot-managed tag on the user story here
# but that seems pretty intensive so we're ignoring it for now

changes = 0

for task in all_tasks:
    logger.debug(f"Checking task: {task.subject} for story: {task.user_story}")
    # Skip the task if it's already assigned to someone

    if task.subject not in task_base:
        logger.debug(f"Task not found in templates: {task.subject}")
        continue

    if task.assigned_to:
        logger.debug(f"Task already assigned to {task.assigned_to}")
        continue

    if task.subject not in task_assignee:
        logger.debug(f"Task not found in assignee list: {task.subject}")
        continue

    # Assign the task to the user specified in the template
    # Completed via the Taiga api directly
    task_assigned_to = task_assignee[task.subject]
    logger.info(f"Assigning {task.subject} to {task_assigned_to}")
    url = f"{config['taiga']['url']}/api/v1/tasks/{task.id}"
    data = {"assigned_to": int(task_assigned_to), "version": task.version}
    response = requests.patch(
        url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        json=data,
    )

    if response.status_code != 200:
        logger.error(f"Failed to assign {task.subject}: {response.status_code}")
        logger.error(response.text)
        sys.exit()
        continue

    logger.info(f"Assigned {task.subject} to {task_assigned_to}")
    changes += 1


logger.info(f"Assigned {changes} tasks")

# Clean up the lock file
os.remove("attendee.lock")
