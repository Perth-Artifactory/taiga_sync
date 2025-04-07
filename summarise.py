import json
import logging
import sys
from pprint import pprint

import openai
import requests
from taiga import TaigaAPI

from util import taigalink, tidyhq


def div(title: str | None = None) -> None:
    """Print a divider with an optional title"""

    # Center the title
    if title:
        title = f" {title} "
        title = title.center(80, "=")
        print(title)
    else:
        print("=" * 80)


# Set up logging
logging.basicConfig(level=logging.INFO)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
# Set slack bolt logging level to INFO to reduce noise when individual modules are set to debug
slack_logger = logging.getLogger("slack")
slack_logger.setLevel(logging.INFO)
setup_logger = logging.getLogger("setup")
logger = logging.getLogger("timing")

logging.getLogger("httpcore").setLevel(logging.INFO)
logging.getLogger("openai").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

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

# Set up openai connection
openai_client = openai.OpenAI(
    api_key=config["openai"]["key"],
    organization=config["openai"]["org"],
)

##########################

# Get timeline for a project

project_id = 2

response = requests.get(
    url=f"{config['taiga']['url']}/api/v1/timeline/project/{project_id}",
    headers={
        "Authorization": f"Bearer {taiga_auth_token}",
        "x-disable-pagination": "True",
    },
)

timeline = response.json()

# Reverse the timeline
timeline.reverse()

# Use only the last 100 events
timeline = timeline[:100]

info = []

item_infos = {"task": [], "userstory": [], "issue": []}

for event in timeline:
    if event["event_type"].startswith("projects") or event["event_type"].startswith(
        "epics"
    ):
        continue

    if "task" in event["data"]:
        item_infos["task"].append(event["data"]["task"]["id"])
        item_infos["userstory"].append(event["data"]["task"]["userstory"]["id"])
        info.append(f"In story: {event['data']['task']['userstory']['subject']}")
        info.append(f"{event['event_type']} {event['data']['task']['subject']}")
    elif "userstory" in event["data"]:
        item_infos["userstory"].append(event["data"]["userstory"]["id"])
        info.append(f"{event['event_type']} {event['data']['userstory']['subject']}")
    elif "issue" in event["data"]:
        item_infos["issue"].append(event["data"]["issue"]["id"])
        info.append(f"{event['event_type']} {event['data']['issue']['subject']}")
    else:
        pprint(event)
        input()

    if event["data"]["values_diff"]:
        for key in ["kanban_order"]:
            if key in event["data"]["values_diff"]:
                event["data"]["values_diff"].pop(key)
        info.append("Changes:")
        for field, change in event["data"]["values_diff"].items():
            if isinstance(change, list):
                continue
            if len(change) > 1:
                pprint(change)
                info.append(f"{field}: {change[1]} (was {change[0]})")
            else:
                info.append(f"{field}: {change[0]}")
    info.append("")

# Dedupe item infos
for key, value in item_infos.items():
    item_infos[key] = list(set(value))

info += "Here is some more information about the items mentioned in the timeline:"

# Get more info about the items
for item_type, item_ids in item_infos.items():
    for item_id in item_ids:
        item_info = taigalink.get_info(
            taiga_auth_token=taiga_auth_token,
            item_id=item_id,
            item_type=item_type,
            config=config,
        )
        if not item_info:
            continue

        line = f"{item_type}: {item_info['subject']} ({item_info['status']}) {item_info['description']}"
        info.append(line)
        if item_type == "userstory":
            tasks = taigalink.get_tasks(
                config=config,
                taiga_auth_token=taiga_auth_token,
                story_id=item_id,
                filters={},
            )
            if tasks:
                info.append("Attached tasks:")
                for task in tasks:
                    info.append(
                        f"Task: {task['subject']} ({task['status']}) {task.get('description')} {'Assigned to: ' + task['assigned_to_extra_info']['full_name_display'] if task.get('assigned_to') else ''}"
                    )
        info.append("")

logging.info(f"Turned {len(timeline)} events into {len(info)} lines of info")

# Feed the timeline into chatgpt

prompt = f"""
The following are the recent events in the project timeline for the {taiga_cache["boards"][project_id]["name"]} project:
"""
prompt += "\n".join(info)
prompt += "Summarise the recent events in a format appropriate for a project update."

response = openai_client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {
            "role": "system",
            "content": "You are the project manager for a small makerspace. I'm going to provide you with a summary of recent events in a project timeline. Respond with a summary of the recent events in a format appropriate for a project update. Use the 'get_item_info' function to get more information about a specific item.",
        },
        {"role": "user", "content": prompt},
    ],
)

message = response.choices[0].message.content
print(message)
