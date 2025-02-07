import json
import logging
import sys
import time
from copy import deepcopy as copy
from datetime import datetime
from pprint import pprint

import requests
from slack_bolt import App
from taiga import TaigaAPI

from slack import block_formatters, blocks
from slack import misc as slack_misc
from util import taigalink, tidyhq

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

items = {}


start_time = time.time()
logger.info("Fetching stories from Taiga")
items["story"] = taigacon.user_stories.list(status__is_closed=False)
logger.info(f"Got {len(items['story'])} stories")
logger.info(f"Time taken to fetch stories: {(time.time() - start_time) * 1000:.2f} ms")

start_time = time.time()
logger.info("Fetching issues from Taiga")
items["issue"] = taigacon.issues.list(status__is_closed=False)
logger.info(f"Got {len(items['issue'])} issues")
logger.info(f"Time taken to fetch issues: {(time.time() - start_time) * 1000:.2f} ms")

start_time = time.time()
logger.info("Fetching tasks from Taiga")
items["task"] = taigacon.tasks.list(status__is_closed=False)
logger.info(f"Got {len(items['task'])} tasks")
logger.info(f"Time taken to fetch tasks: {(time.time() - start_time) * 1000:.2f} ms")


for item_type in items:
    print(f"{item_type}: {len(items[item_type])} items")

assignees = {"unassigned": {"story": [], "issue": [], "task": []}}

for item_type in items:
    for item in items[item_type]:
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
                    "task": [],
                }
            info = taigalink.get_info(
                taiga_auth_token=taiga_auth_token,
                config=config,
                item_id=item.id,
                item_type=item_type,
            )
            assignees[assigned_to][item_type].append(copy(info))
            logger.info(f"{item.subject} ({item_type}) is assigned to {assigned_to}")
            for watcher in watchers:
                if watcher not in assignees:
                    assignees[watcher] = {
                        "story": [],
                        "issue": [],
                        "task": [],
                    }
                assignees[watcher][item_type].append(copy(info))
                logger.info(f"{item.subject} is watched by {watcher}")

weekly = {}
daily = {}

for assignee in assignees:
    root_assignee = assignee
    weekly[assignee] = {"story": [], "issue": [], "task": []}
    daily[assignee] = {"story": [], "issue": [], "task": []}

    for item_type in assignees[assignee]:
        assignee = root_assignee
        for item in assignees[assignee][item_type]:
            if assignee == "unassigned":
                # Translate to the appropriate slack channel
                assignee = config["taiga-channel"][
                    str(item["project_extra_info"]["id"])
                ]
                if assignee not in weekly:
                    weekly[assignee] = {"story": [], "issue": [], "task": []}
                    daily[assignee] = {"story": [], "issue": [], "task": []}

            if item["due_date"] is None:
                pprint(item)
                print(f"root_assignee: {root_assignee}")
                print(f"assignee: {assignee}")
                print(f"item_type: {item_type}")
                sys.exit()

            due_date = datetime.strptime(item["due_date"], "%Y-%m-%d")
            days = (due_date - datetime.now()).days
            # Turn negative days into X days ago
            if days < 0:
                days_str = f"{abs(days)} days ago"
            else:
                days_str = f"in {days} days"
            string = f"{item['subject']} ({item['status_extra_info']['name']}) • due {days_str} in {item['project_extra_info']['name']}"

            data = {"string": string, "days": days, "item": item, "item_type": "story"}

            # Look for things that are due in exactly 7 days or today
            if days in (0, 6):
                daily[assignee][item_type].append(data)

            # Look for things that are due in at most 14 days
            if days <= 14:
                weekly[assignee][item_type].append(data)

        # Sort items by days until due
        daily[assignee][item_type].sort(key=lambda x: x["days"])
        weekly[assignee][item_type].sort(key=lambda x: x["days"])

    assignee = root_assignee

working_items = []

if "--weekly" in sys.argv:
    working_items.append(
        {
            "items": weekly,
            "channel_message": "These are the items due in the next 14 days:",
            "personal_message": "These are the items due in the next 14 days that you are watching or assigned to:",
            "footer": "We send these reminders out every Wednesday to give you an idea of what's coming up.",
        }
    )


if "--daily" in sys.argv:
    working_items.append(
        {
            "items": daily,
            "channel_message": "These are the items due either today or *one week* from today:",
            "personal_message": "These are the items due either today or *one week* from today that you are watching or assigned to:",
            "footer": "We send these reminders out on the day the item is due or exactly one week out.",
        }
    )

if not working_items:
    logger.error("No working mode specified. Use --weekly or --daily")
    pprint(daily)
    print("\n" * 3)
    pprint(weekly)
    sys.exit()

for current in working_items:
    working = current["items"]
    for assignee in working:
        block_list = []
        block_list = block_formatters.add_block(block_list, blocks.text)
        reminder_blocks = block_formatters.construct_reminder_section(working[assignee])
        if not reminder_blocks:
            continue
        assignee = str(assignee)

        footer_blocks = []
        footer_blocks = block_formatters.add_block(footer_blocks, blocks.context)
        footer_blocks = block_formatters.inject_text(footer_blocks, current["footer"])

        if assignee.startswith("C"):
            # Inject the header message
            block_list[0]["text"]["text"] = current["channel_message"]

            app.client.chat_postMessage(
                channel=assignee,
                blocks=block_list + reminder_blocks,
                text="Upcoming due items on Taiga",
                unfurl_links=False,
                unfurl_media=False,
            )
        else:
            # Translate from Taiga ID to slack ID
            slack_id = tidyhq.map_taiga_to_slack(
                tidyhq_cache=tidyhq_cache, taiga_id=assignee, config=config
            )
            if not slack_id:
                logger.error(f"No slack ID found for Taiga user {assignee}")
                continue

            block_list[0]["text"]["text"] = current["personal_message"]

            slack_misc.send_dm(
                slack_id=slack_id,
                message="Upcoming due items on Taiga",
                slack_app=app,
                blocks=block_list + reminder_blocks,
                unfurl_links=False,
                unfurl_media=False,
            )
