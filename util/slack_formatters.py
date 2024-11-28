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


def format_tasks(task_list):
    # Get the user story info
    project_slug = task_list[0]["project_extra_info"]["slug"]
    project_name = task_list[0]["project_extra_info"]["name"]
    story_ref = task_list[0]["user_story_extra_info"]["ref"]
    story_subject = task_list[0]["user_story_extra_info"]["subject"]
    user_story_str = f"<https://tasks.artifactory.org.au/project/{project_slug}/us/{story_ref}|{story_subject}> (<https://tasks.artifactory.org.au/project/{project_slug}/kanban|{project_name}>)"

    # Construct the base dropdown selector for task actions
    # We can reuse the functions used to create forms
    dropdown_questions = [
        "Attach files",
        "Add comment",
        "Mark complete",
        "Assign to me",
        "Watch",
        "Unwatch",
    ]

    base_dropdown_accessory = copy(blocks.static_dropdown["element"])
    base_dropdown_accessory["options"] = slack_forms.text_to_options(dropdown_questions)
    base_dropdown_accessory["placeholder"]["text"] = "Pick an action"
    base_dropdown_accessory["action_id"] = ""

    task_strs = []
    task_blocks = []
    for task in task_list:
        url = f"https://tasks.artifactory.org.au/project/{task['project_extra_info']['slug']}/task/{task['ref']}"
        task_formatted = (
            f"• <{url}|{task['subject']}> ({task['status_extra_info']['name']})"
        )

        task_strs.append(task_formatted)

        # Set up drop down
        current_dropdown = copy(base_dropdown_accessory)
        current_dropdown["action_id"] = (
            f"homeaction-{task['project_extra_info']['id']}-task-{task['id']}"
        )
        task_blocks = add_block(block_list=task_blocks, block=blocks.text)
        task_blocks = inject_text(block_list=task_blocks, text=task_formatted)
        task_blocks[-1]["accessory"] = current_dropdown

    out_str = "\n".join(task_strs)

    return user_story_str, out_str, task_blocks


def format_stories(story_list):
    """Format a list of stories into a header, a newline formatted string and a list of blocks"""
    project_slug = story_list[0]["project_extra_info"]["slug"]
    header = story_list[0]["project_extra_info"]["name"]
    header_str = (
        f"<https://tasks.artifactory.org.au/project/{project_slug}/kanban|{header}>"
    )

    # Construct the base dropdown selector for story actions
    # We can reuse the functions used to create forms
    dropdown_questions = [
        "Attach files",
        "Add comment",
        "Mark complete",
        "Assign to me",
        "Watch",
        "Unwatch",
    ]

    base_dropdown_accessory = copy(blocks.static_dropdown["element"])
    base_dropdown_accessory["options"] = slack_forms.text_to_options(dropdown_questions)
    base_dropdown_accessory["placeholder"]["text"] = "Pick an action"
    base_dropdown_accessory["action_id"] = ""

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

        # Set up drop down
        current_dropdown = copy(base_dropdown_accessory)
        current_dropdown["action_id"] = (
            f"homeaction-{story['project_extra_info']['id']}-story-{story['id']}"
        )
        story_blocks = add_block(block_list=story_blocks, block=blocks.text)
        story_blocks = inject_text(block_list=story_blocks, text=story_formatted)
        story_blocks[-1]["accessory"] = current_dropdown
    out_str = "\n".join(story_strs)

    return header_str, out_str, story_blocks


def format_issues(issue_list):
    # Get the user story info
    project_slug = issue_list[0]["project_extra_info"]["slug"]
    project_name = issue_list[0]["project_extra_info"]["name"]

    # Construct the base dropdown selector for task actions
    # We can reuse the functions used to create forms
    dropdown_questions = [
        "Attach files",
        "Add comment",
        "Mark complete",
        "Assign to me",
        "Watch",
        "Unwatch",
    ]

    base_dropdown_accessory = copy(blocks.static_dropdown["element"])
    base_dropdown_accessory["options"] = slack_forms.text_to_options(dropdown_questions)
    base_dropdown_accessory["placeholder"]["text"] = "Pick an action"
    base_dropdown_accessory["action_id"] = ""

    issue_strs = []
    issue_blocks = []
    for issue in issue_list:
        url = f"https://tasks.artifactory.org.au/project/{project_slug}/issue/{issue['ref']}"
        issue_formatted = (
            f"• <{url}|{issue['subject']}> ({issue['status_extra_info']['name']})"
        )

        issue_strs.append(issue_formatted)

        # Set up drop down
        current_dropdown = copy(base_dropdown_accessory)
        current_dropdown["action_id"] = (
            f"homeaction-{issue['project_extra_info']['id']}-issue-{issue['id']}"
        )
        issue_blocks = add_block(block_list=issue_blocks, block=blocks.text)
        issue_blocks = inject_text(block_list=issue_blocks, text=issue_formatted)
        issue_blocks[-1]["accessory"] = current_dropdown

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
    return block_list


def app_home(
    user_id: str, config: dict, tidyhq_cache: dict, taiga_auth_token: str
) -> list:
    """Generate the app home view for a specified user and return it as a list of blocks."""
    # Check if the user has a Taiga account
    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq.fresh_cache(config=config, cache=tidyhq_cache),
        config=config,
        slack_id=user_id,
    )

    if not taiga_id:
        logger.info(f"User {user_id} does not have a Taiga account.")
        # We don't recognise the user

        # Construct blocks
        block_list = []
        block_list += blocks.header
        block_list = inject_text(block_list=block_list, text=strings.header)
        block_list += blocks.text  # type: ignore
        block_list = inject_text(block_list=block_list, text=strings.unrecognised)
        block_list += blocks.divider
        block_list += blocks.text
        block_list = inject_text(block_list=block_list, text=strings.do_instead)
        block_list += blocks.context
        block_list = inject_text(block_list=block_list, text=strings.footer)

    else:
        logger.info(f"User {user_id} has a Taiga account. - {taiga_id}")
        # We recognise the user

        # Construct blocks
        block_list = []
        block_list += blocks.header
        block_list = inject_text(block_list=block_list, text=strings.header)
        block_list += blocks.text
        block_list = inject_text(block_list=block_list, text=strings.explainer)
        block_list += blocks.divider

        ##########
        # Stories
        ##########

        block_list += blocks.header
        block_list = inject_text(block_list=block_list, text="Assigned Cards")

        # Get all assigned user stories for the user
        user_stories = taigalink.get_stories(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

        if len(user_stories) == 0:
            block_list += blocks.text
            block_list = inject_text(block_list=block_list, text=strings.no_stories)
            block_list += blocks.divider

        else:
            # Sort the user stories by project
            sorted_stories = taigalink.sort_by_project(user_stories)

            for project in sorted_stories:
                header, body, story_blocks = format_stories(sorted_stories[project])
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=f"*{header}*")
                # block_list += blocks.text
                # block_list = inject_text(block_list=block_list, text=body)
                block_list += story_blocks
                block_list = add_block(block_list, blocks.divider)

            # Remove the last divider
            block_list.pop()

        block_list += blocks.divider

        ##########
        # Issues
        ##########

        block_list += blocks.header
        block_list = inject_text(block_list=block_list, text="Assigned Issues")

        # Get all assigned issues for the user
        user_issues = taigalink.get_issues(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

        if len(user_issues) == 0:
            block_list += blocks.text
            block_list = inject_text(block_list=block_list, text=strings.no_issues)
            block_list += blocks.divider

        else:
            # Sort the user issues by project
            sorted_issues = taigalink.sort_by_project(user_issues)

            for project in sorted_issues:
                header, body, issue_blocks = format_issues(sorted_issues[project])
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=f"*{header}*")
                block_list += issue_blocks
                block_list = add_block(block_list, blocks.divider)

            # Remove the last divider
            block_list.pop()

        block_list += blocks.divider

        ##########
        # Tasks
        ##########

        block_list += blocks.header
        block_list = inject_text(block_list=block_list, text="Assigned Tasks")

        # Get all tasks for the user
        tasks = taigalink.get_tasks(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

        if len(tasks) == 0:
            block_list += blocks.text
            block_list = inject_text(block_list=block_list, text=strings.no_tasks)

        else:

            # Sort the tasks based on user story
            sorted_tasks = taigalink.sort_tasks_by_user_story(tasks)

            # Things will start to break down if there are too many tasks
            displayed_tasks = 0
            trimmed = True
            for project in sorted_tasks:
                if displayed_tasks >= 50:
                    break
                header, body, task_blocks = format_tasks(sorted_tasks[project])

                # Skip over tasks assigned in template cards
                if "template" in header.lower():
                    continue

                displayed_tasks += 1
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=f"*{header}*")
                # block_list += blocks.text
                # block_list = inject_text(block_list=block_list, text=body)
                block_list += task_blocks
                block_list = add_block(block_list, blocks.divider)

            else:
                trimmed = False

            # Remove the last divider
            block_list.pop()

            if trimmed:
                block_list += blocks.divider
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=strings.trimmed)

        # Get details about the current app version from git
        commit_hash = (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
            .decode("ascii")
            .strip()
        )
        branch_name = (
            subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
            .decode("ascii")
            .strip()
        )

        # Get the OS environment
        platform_name = platform.system()

        block_list += blocks.context
        block_list = inject_text(
            block_list=block_list,
            text=strings.footer.format(
                branch=branch_name, commit=commit_hash, platform=platform_name
            ),
        )

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
