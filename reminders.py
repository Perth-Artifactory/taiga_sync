import requests
import logging
import json
import sys
from slack_bolt import App
from datetime import datetime
from pprint import pprint, pformat
import time
from datetime import datetime

from taiga import TaigaAPI

from util import slack, taigalink, tidyhq, slack_formatters, blocks

# Set up logging
logging.basicConfig(level=logging.INFO)
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
tidyhq_cache = tidyhq.fresh_cache(config=config)
setup_logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)

# Connect to slack
app = App(token=config["slack"]["bot_token"], logger=slack_logger)


user_stories = taigacon.user_stories.list()
issues = taigacon.issues.list()

logger.info(f"Found {len(user_stories)} user stories and {len(issues)} issues")

assignees = {"unassigned": {"story": [], "issue": []}}

for item in user_stories:
    if item.is_closed:
        continue
    assigned_to = getattr(item, "assigned_to", "unassigned")
    watchers = item.watchers
    # Remove the assignee from the watchers if they are there
    if assigned_to in watchers:
        watchers.remove(assigned_to)
    if not assigned_to:
        assigned_to = "unassigned"
    if item.due_date:
        if assigned_to not in assignees:
            assignees[assigned_to] = {
                "story": [],
                "issue": [],
            }
        info = taigalink.get_info(
            taiga_auth_token=taiga_auth_token, config=config, story_id=item.id
        )
        assignees[assigned_to]["story"].append(info)
        logger.info(f"{item.subject} is assigned to {assigned_to}")
        for watcher in watchers:
            if watcher not in assignees:
                assignees[watcher] = {
                    "story": [],
                    "issue": [],
                }
            assignees[watcher]["story"].append(info)
            logger.info(f"{item.subject} is watched by {watcher}")

for item in issues:
    if item.is_closed:
        continue
    assigned_to = getattr(item, "assigned_to", "unassigned")
    if not assigned_to:
        assigned_to = "unassigned"
    if item.due_date:
        if assigned_to not in assignees:
            assignees[assigned_to] = {
                "story": [],
                "issue": [],
            }
        info = taigalink.get_info(
            taiga_auth_token=taiga_auth_token, config=config, issue_id=item.id
        )
        assignees[assigned_to]["issue"].append(info)
        logger.debug(f"{item.subject} is assigned to {assigned_to}")

weekly = {}
daily = {}

# TODO: Make this not repeat

for assignee in assignees:
    weekly[assignee] = {"story": [], "issue": []}
    daily[assignee] = {"story": [], "issue": []}

    for item in assignees[assignee]["story"]:
        if assignee == "unassigned":
            # Translate to the appropriate slack channel
            assignee = config["taiga-channel"][item["project_extra_info"]["id"]]
            if assignee not in weekly:
                weekly[assignee] = {"story": [], "issue": []}
                daily[assignee] = {"story": [], "issue": []}

        string = slack_formatters.due_item(
            item=item, item_type="story", for_user=assignee
        )
        due_date = datetime.strptime(item["due_date"], "%Y-%m-%d")
        days = (due_date - datetime.now()).days
        # Look for things that are due in exactly 7 days
        if days == 6:
            daily[assignee]["story"].append(string)

        # Look for things that are due in at most 14 days
        if days <= 14:
            weekly[assignee]["story"].append(string)

    # Sort stories by days until due
    daily[assignee]["story"].sort(key=lambda x: x.split()[1])
    weekly[assignee]["story"].sort(key=lambda x: x.split()[1])

    for item in assignees[assignee]["issue"]:
        if assignee == "unassigned":
            # Translate to the appropriate slack channel
            assignee = config["taiga-channel"][str(item["project_extra_info"]["id"])]
            if assignee not in weekly:
                weekly[assignee] = {"story": [], "issue": []}
                daily[assignee] = {"story": [], "issue": []}
        string = slack_formatters.due_item(
            item=item, item_type="issue", for_user=assignee
        )
        due_date = datetime.strptime(item["due_date"], "%Y-%m-%d")
        days = (due_date - datetime.now()).days
        # Look for things that are due in exactly 7 days
        if days == 6:
            daily[assignee]["issue"].append(string)

        # Look for things that are due in at most 14 days
        if days <= 14:
            weekly[assignee]["issue"].append(string)

    # Sort issues by days until due
    daily[assignee]["issue"].sort(key=lambda x: x.split()[1])
    weekly[assignee]["issue"].sort(key=lambda x: x.split()[1])

working = None

if "--weekly" in sys.argv:
    working = weekly
    message = "These are the items due in the next 14 days that you are watching or assigned to:"


if "--daily" in sys.argv:
    working = daily
    message = "These are the items due in 7 days that you are watching or assigned to:"

if not working:
    logger.error("No working mode specified. Use --weekly or --daily")
    sys.exit()

for assignee in working:
    block_list = []
    block_list += blocks.text
    block_list = slack_formatters.inject_text(block_list, message)
    reminder_blocks = slack_formatters.construct_reminder_section(weekly[assignee])
    if not reminder_blocks:
        continue
    assignee = str(assignee)
    if assignee.startswith("C"):
        app.client.chat_postMessage(
            channel=assignee,
            blocks=block_list + reminder_blocks,
            text="Weekly reminders",
        )
    else:
        # Translate from Taiga ID to slack ID
        slack_id = tidyhq.map_taiga_to_slack(
            tidyhq_cache=tidyhq_cache, taiga_id=assignee, config=config
        )
        if not slack_id:
            logger.error(f"No slack ID found for Taiga user {assignee}")
            continue
        slack.send_dm(
            slack_id=slack_id,
            message="Upcoming due items on Taiga",
            slack_app=app,
            blocks=reminder_blocks,
        )
