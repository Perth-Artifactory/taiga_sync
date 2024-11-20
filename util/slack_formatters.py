from copy import deepcopy as copy
from pprint import pprint
from datetime import datetime

from util import blocks


def tasks(task_list):
    # Get the user story info
    project_slug = task_list[0]["project_extra_info"]["slug"]
    project_name = task_list[0]["project_extra_info"]["name"]
    story_ref = task_list[0]["user_story_extra_info"]["ref"]
    story_subject = task_list[0]["user_story_extra_info"]["subject"]
    user_story_str = f"<https://tasks.artifactory.org.au/project/{project_slug}/us/{story_ref}|{story_subject}> (<https://tasks.artifactory.org.au/project/{project_slug}/kanban|{project_name}>)"
    task_strs = []
    for task in task_list:
        url = f"https://tasks.artifactory.org.au/project/{task['project_extra_info']['slug']}/task/{task['ref']}"
        task_strs.append(
            f"• <{url}|{task['subject']}> ({task['status_extra_info']['name']})"
        )
    out_str = "\n".join(task_strs)

    return user_story_str, out_str


def stories(story_list):
    project_slug = story_list[0]["project_extra_info"]["slug"]
    header = story_list[0]["project_extra_info"]["name"]
    header_str = (
        f"<https://tasks.artifactory.org.au/project/{project_slug}/kanban|{header}>"
    )
    story_strs = []
    for story in story_list:
        story_url = (
            f"https://tasks.artifactory.org.au/project/{project_slug}/us/{story['ref']}"
        )
        story_name = story["subject"]
        story_status = story["status_extra_info"]["name"]
        story_strs.append(f"• <{story_url}|{story_name}> ({story_status})")
    out_str = "\n".join(story_strs)

    return header_str, out_str


def due_item(item: dict, item_type: str, for_user: str):
    assigned_info = " (Watching)"
    if for_user in item.get("assigned_users", []):
        assigned_info = " (Assigned)"
    elif for_user.startswith("C"):
        assigned_info = ""

    due_date = datetime.strptime(item["due_date"], "%Y-%m-%d")
    days = (due_date - datetime.now()).days
    project_slug = item["project_extra_info"]["slug"]
    ref = item["ref"]

    if item_type == "story":
        story_url = f"https://tasks.artifactory.org.au/project/{project_slug}/us/{ref}"
    elif item_type == "issue":
        story_url = (
            f"https://tasks.artifactory.org.au/project/{project_slug}/issue/{ref}"
        )
    else:
        raise ValueError(f"Invalid item: must be 'story' or 'issue' got {item_type}")
    story_name = item["subject"]
    story_status = item["status_extra_info"]["name"]
    string = (
        f"• {days} days: <{story_url}|{story_name}> ({story_status}){assigned_info}"
    )
    return string


def construct_reminder_section(reminders: dict) -> list:
    block_list = []
    if reminders["story"] != []:
        block_list += blocks.header
        block_list = inject_text(block_list, "Cards")
        block_list += blocks.text
        block_list = inject_text(block_list, "\n".join(reminders["story"]))
    if reminders["issue"] != []:
        if block_list != []:
            block_list += blocks.divider
        block_list += blocks.header
        block_list = inject_text(block_list, "Issues")
        block_list += blocks.text
        block_list = inject_text(block_list, "\n".join(reminders["issue"]))

    return block_list


def inject_text(block_list, text):
    block_list = copy(block_list)
    if block_list[-1]["type"] in ["section", "header", "button"]:
        block_list[-1]["text"]["text"] = text
    elif block_list[-1]["type"] in ["context"]:
        block_list[-1]["elements"][0]["text"] = text
    elif block_list[-1]["type"] == "modal":
        block_list[-1]["title"]["text"] = text
    elif block_list[-1]["type"] == "rich_text":
        block_list[-1]["elements"][0]["elements"][0]["text"] = text

    return block_list
