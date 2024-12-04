import json
import logging
import platform
import subprocess
from copy import deepcopy as copy
from datetime import datetime
from pprint import pprint

import jsonschema

from editable_resources import strings
from util import blocks, taigalink, tidyhq, slack_forms

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slack_formatters")

# Specify actions used for app home dropdowns (final)
dropdown_questions = [
    "Comments",
    "Attach files",
    "Change status",
    "Assign",
    "Add watchers",
]

# These are the only options implemented so far
dropdown_questions = ["Comments"]


def format_tasks(task_list, compressed=False):
    # Get the user story info
    project_slug = task_list[0]["project_extra_info"]["slug"]
    project_name = task_list[0]["project_extra_info"]["name"]
    story_ref = task_list[0]["user_story_extra_info"]["ref"]
    story_subject = task_list[0]["user_story_extra_info"]["subject"]
    user_story_str = f"<https://tasks.artifactory.org.au/project/{project_slug}/us/{story_ref}|{story_subject}> (<https://tasks.artifactory.org.au/project/{project_slug}/kanban|{project_name}>)"

    task_strs = []
    task_blocks = []
    for task in task_list:
        url = f"https://tasks.artifactory.org.au/project/{task['project_extra_info']['slug']}/task/{task['ref']}"
        task_formatted = (
            f"• <{url}|{task['subject']}> ({task['status_extra_info']['name']})"
        )

        # If we're compressing add the header to each task
        if compressed:
            task_formatted = task_formatted[1:]
            task_formatted = f"• {user_story_str} - {task_formatted}"

        task_strs.append(task_formatted)

        task_blocks = add_block(block_list=task_blocks, block=blocks.text)
        task_blocks = inject_text(block_list=task_blocks, text=task_formatted)

        # Set up button
        button = copy(blocks.button)
        button["text"]["text"] = "View/Edit"
        button["action_id"] = (
            f"viewedit-{task['project_extra_info']['id']}-task-{task['id']}"
        )
        task_blocks[-1]["accessory"] = button

    out_str = "\n".join(task_strs)

    return user_story_str, out_str, task_blocks


def format_stories(story_list, compressed=False):
    """Format a list of stories into a header, a newline formatted string and a list of blocks"""
    project_slug = story_list[0]["project_extra_info"]["slug"]
    header = story_list[0]["project_extra_info"]["name"]
    header_str = (
        f"<https://tasks.artifactory.org.au/project/{project_slug}/kanban|{header}>"
    )

    story_strs = []
    story_blocks = []
    for story in story_list:
        story_url = (
            f"https://tasks.artifactory.org.au/project/{project_slug}/us/{story['ref']}"
        )
        story_name = story["subject"]
        story_status = story["status_extra_info"]["name"]
        story_formatted = f"• <{story_url}|{story_name}> ({story_status})"
        story_strs.append(story_formatted)

        # If we're compressing add the header to each task
        if compressed:
            story_formatted = story_formatted[1:]
            story_formatted = f"• {header_str} - {story_formatted}"

        story_blocks = add_block(block_list=story_blocks, block=blocks.text)
        story_blocks = inject_text(block_list=story_blocks, text=story_formatted)

        # Set up button
        button = copy(blocks.button)
        button["text"]["text"] = "View/Edit"
        button["action_id"] = (
            f"viewedit-{story['project_extra_info']['id']}-story-{story['id']}"
        )
        story_blocks[-1]["accessory"] = button

    out_str = "\n".join(story_strs)

    return header_str, out_str, story_blocks


def format_issues(issue_list, compressed=False):
    # Get the user story info
    project_slug = issue_list[0]["project_extra_info"]["slug"]
    project_name = issue_list[0]["project_extra_info"]["name"]

    issue_strs = []
    issue_blocks = []
    for issue in issue_list:
        url = f"https://tasks.artifactory.org.au/project/{project_slug}/issue/{issue['ref']}"
        issue_formatted = (
            f"• <{url}|{issue['subject']}> ({issue['status_extra_info']['name']})"
        )

        issue_strs.append(issue_formatted)

        if compressed:
            issue_formatted = issue_formatted[1:]
            issue_formatted = f"• {project_name} - {issue_formatted}"

        issue_blocks = add_block(block_list=issue_blocks, block=blocks.text)
        issue_blocks = inject_text(block_list=issue_blocks, text=issue_formatted)

        # Set up button
        button = copy(blocks.button)
        button["text"]["text"] = "View/Edit"
        button["action_id"] = (
            f"viewedit-{issue['project_extra_info']['id']}-issue-{issue['id']}"
        )
        issue_blocks[-1]["accessory"] = button

    out_str = "\n".join(issue_strs)

    return project_name, out_str, issue_blocks


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
        block_list = add_block(block_list, blocks.header)
        block_list = inject_text(block_list, "Cards")
        block_list = add_block(block_list, blocks.text)
        block_list = inject_text(block_list, "\n".join(reminders["story"]))
    if reminders["issue"] != []:
        if block_list != []:
            block_list = add_block(block_list, blocks.divider)
        block_list = add_block(block_list, blocks.header)
        block_list = inject_text(block_list, "Issues")
        block_list = add_block(block_list, blocks.text)
        block_list = inject_text(block_list, "\n".join(reminders["issue"]))

    return block_list


def inject_text(block_list: list, text: str) -> list[dict]:
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


def add_block(block_list: list, block: dict | list) -> list[dict]:
    """Adds a block to the block list and returns the updated list."""
    block = copy(block)
    block_list = copy(block_list)
    if type(block) == list:
        block_list += block
    elif type(block) == dict:
        block_list.append(block)

    if len(block_list) > 100:
        raise ValueError("Block list too long")

    return block_list


def validate(blocks):
    # We want our own logger for this function
    schemalogger = logging.getLogger("block-kit validator")

    # Load the schema from file
    with open("block-kit-schema.json") as f:
        schema = json.load(f)

    try:
        jsonschema.validate(instance=blocks, schema=schema)
    except jsonschema.exceptions.ValidationError as e:  # type: ignore
        schemalogger.error(e)
        return False
    return True


def compress_blocks(block_list) -> list:
    compressed_blocks = []

    # Remove dividers
    for block in block_list:
        if block["type"] != "divider":
            compressed_blocks.append(block)
    logging.debug(f"Blocks reduced from {len(block_list)} to {len(compressed_blocks)}")

    return compressed_blocks
