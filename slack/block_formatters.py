import json
import logging
import platform
import subprocess
from copy import deepcopy as copy
from datetime import datetime, timedelta
from pprint import pprint
import time
import re

import jsonschema

import taiga

from editable_resources import strings
from slack import blocks, block_formatters
from slack import misc as slack_misc
from util import taigalink, tidyhq, misc, const

# Set up logging
logger = logging.getLogger("slack.block_formatters")


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


def format_attachments(attachments) -> list[dict]:
    """Format a list of taiga attachments into a list of blocks including image blocks as appropriate."""
    block_list = []
    for attachment in attachments:

        filetype = attachment.attached_file.split(".")[-1]

        if filetype in ["jpg", "jpeg", "png", "gif"]:
            block_list = add_block(block_list, blocks.image)
            block_list[-1]["image_url"] = attachment.url
            if attachment.description:
                block_list[-1]["title"] = {
                    "type": "plain_text",
                    "text": attachment.description,
                }
                block_list[-1]["alt_text"] = attachment.description
            else:
                block_list[-1]["title"] = {
                    "type": "plain_text",
                    "text": attachment.name,
                }
                block_list[-1]["alt_text"] = attachment.name
        else:
            if attachment.description:
                block_list = add_block(block_list, blocks.text)
                block_list = inject_text(
                    block_list=block_list,
                    text=f"• <{attachment.url}|{attachment.description}>",
                )
            else:
                block_list = add_block(block_list, blocks.text)
                block_list = inject_text(
                    block_list=block_list,
                    text=f"• <{attachment.url}|{attachment.name}>",
                )

    return block_list


def format_tasks_modal_blocks(
    task_list: list, config: dict, taiga_auth_token: str, taiga_cache: dict, edit=True
) -> list[dict]:
    """Format a list of tasks into the blocks required for a modal view"""
    block_list = []
    # Add information about the user story
    block_list = add_block(block_list, blocks.header)
    block_list = inject_text(
        block_list, task_list[0]["user_story_extra_info"]["subject"]
    )
    block_list = add_block(block_list, blocks.divider)

    # Sort the tasks by closed status
    incomplete_tasks = [task for task in task_list if task["is_closed"] == False]
    complete_tasks = [task for task in task_list if task["is_closed"] == True]
    task_list = incomplete_tasks + complete_tasks

    for task in task_list:
        format_task = f"• *{task['subject']}* ({task['status_extra_info']['name']})"

        block_list = add_block(block_list, blocks.text)
        block_list = inject_text(block_list, format_task)

        # Set up fields
        fields = []
        if task["assigned_to"]:
            fields.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Assigned to:* {task['assigned_to_extra_info']['full_name_display']}",
                }
            )

        if task["due_date"]:
            fields.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Due Date:* {task['due_date']}",
                }
            )

        # Add fields to the block
        if fields:
            block_list[-1]["fields"] = fields

        if edit:
            # Set up buttons
            button_list = []
            # If the task is not closed, add close buttons for each closing status
            if not task["is_closed"]:
                closing_statuses = taiga_cache["boards"][task["project"]][
                    "closing_statuses"
                ]["task"]
                for status in closing_statuses:
                    button = copy(blocks.button)
                    button["text"]["text"] = f"Close as {status['name']}"
                    button["action_id"] = (
                        f"complete-{task['project']}-task-{task['id']}-{status['id']}"
                    )
                    button["confirm"] = {
                        "title": {"type": "plain_text", "text": "Close task"},
                        "text": {
                            "type": "plain_text",
                            "text": f"Are you sure you want to mark this task as {status['name']}?",
                        },
                        "confirm": {
                            "type": "plain_text",
                            "text": f"Mark as {status['name']}",
                        },
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    }
                    button_list.append(button)

            # If we only have one button we can attach it to the text block
            if len(button_list) == 1:
                block_list[-1]["accessory"] = button_list[0]
            elif len(button_list) > 1:
                block_list = add_block(block_list, blocks.actions)
                block_list[-1]["elements"] = button_list
                block_list[-1].pop("block_id")
                block_list = add_block(block_list, blocks.divider)

    return block_list


def construct_reminder_section(reminders: dict) -> list:
    block_list = []

    type_to_header = {"story": "User Stories", "task": "Tasks", "issue": "Issues"}

    for item_type in reminders:
        if item_type not in type_to_header:
            raise ValueError(f"Invalid item type {item_type}")
        if reminders[item_type] != []:
            if block_list != []:
                block_list = add_block(block_list, blocks.divider)
            block_list = add_block(block_list, blocks.header)
            block_list = inject_text(block_list, type_to_header[item_type])

        for reminder in reminders[item_type]:
            block_list = add_block(block_list, blocks.text)
            block_list = inject_text(block_list, reminder["string"])
            button = copy(blocks.button)
            button["text"]["text"] = "View in app"
            button["action_id"] = (
                f"viewedit-{reminder['item']['project_extra_info']['id']}-{item_type}-{reminder['item']['id']}"
            )
            block_list[-1]["accessory"] = button

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
        logger.info(f"Block list too long {len(block_list)}/100")

    return block_list


def compress_blocks(block_list) -> list:
    compressed_blocks = []

    # Remove dividers
    for block in block_list:
        if block["type"] != "divider":
            compressed_blocks.append(block)
    logging.debug(f"Blocks reduced from {len(block_list)} to {len(compressed_blocks)}")

    return compressed_blocks


def render_form_list(form_list: dict, member=False) -> list[dict]:
    """Takes a list of forms and renders them as a list of blocks"""
    block_list = []
    block_list = block_formatters.add_block(block_list, blocks.text)
    block_list = block_formatters.inject_text(
        block_list=block_list, text="Please select a form to fill out:"
    )
    unavailable_forms = []
    for form_id in form_list:
        form = form_list[form_id]
        if form["members_only"] and not member:
            unavailable_forms.append(form_id)
            continue
        block_list = block_formatters.add_block(block_list, blocks.header)
        block_list = block_formatters.inject_text(
            block_list=block_list,
            text=f'{form["title"]}{":artifactory:" if form["members_only"] else ""}',
        )
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=form["description"]
        )
        # Add a button to fill out the form as an attachment
        accessory = copy(blocks.button)
        accessory["text"]["text"] = form["action_name"]
        accessory["value"] = form_id
        accessory["action_id"] = f"form-open-{form_id}"

        block_list[-1]["accessory"] = accessory

    if unavailable_forms:
        block_list = block_formatters.add_block(block_list, blocks.divider)
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list,
            text="We were unable to associate a membership with your Slack account so the following forms are unavailable:",
        )
        unavailable_form_str = ""
        for form_id in unavailable_forms:
            form = form_list[form_id]
            unavailable_form_str += f"• {form['title']}\n"

        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=unavailable_form_str
        )
        block_list = block_formatters.add_block(block_list, blocks.context)
        block_list = block_formatters.inject_text(
            block_list=block_list,
            text="If you believe this is an error please reach out to #it",
        )
    return block_list


def questions_to_blocks(
    questions: list[dict],
    taigacon,
    taiga_cache: dict,
    taiga_project: str | None = None,
    taiga_project_id: int | str | None = None,
) -> list[dict]:
    """Convert a list of questions to a list of blocks"""
    block_list = []

    if taiga_project and not taiga_project_id:
        taiga_project_id = taiga_cache["projects"]["by_name_with_extra"].get(
            taiga_project.lower()
        )

        if not taiga_project_id:
            raise ValueError(f"Could not find project with name {taiga_project}")

    if taiga_project_id:
        taiga_project_id = int(taiga_project_id)

    for question in questions:

        # Some fields will break if they're included but are empty, so we'll remove them now
        for key in ["placeholder", "text", "action_id"]:
            if key in question:
                if not question[key]:
                    question.pop(key)
                    logger.warning(f"Empty {key} field removed from question")

        # See if we need to add a divider before the question
        if "divider" in question:
            if question["divider"] == "before":
                block_list = block_formatters.add_block(block_list, blocks.divider)

        # Check if we're just adding an explainer (divider is okay too)
        params = [param for param in question.keys() if param != "divider"]
        if "text" in question and len(params) == 1:
            block_list = block_formatters.add_block(block_list, blocks.text)
            block_list = block_formatters.inject_text(
                block_list=block_list,
                text=question.get("text", "This is some default text!"),
            )

        # Check if we're adding a short or long question field
        elif question["type"] in ["short", "long"]:
            if "text" not in question:
                raise ValueError("Short question must have a text field")
            if type(question["text"]) != str:
                raise ValueError("Short question text must be a string")
            block_list = block_formatters.add_block(block_list, blocks.text_question)
            block_list[-1]["label"]["text"] = question.get("text")
            # Get a md5 hash of the question text to use as the action_id

            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", misc.hash_question(question["text"])
            )

            # This is the only difference between short and long questions
            if question["type"] == "long":
                block_list[-1]["element"]["multiline"] = True

            # Add optional placeholder
            if "placeholder" in question:
                block_list[-1]["element"]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                block_list[-1]["element"].pop("placeholder")

            if question.get("optional"):
                block_list[-1]["optional"] = True

        # Check if we're adding radio buttons
        elif question["type"] == "radio":
            if "options" not in question:
                logger.info(
                    "No options provided for radio question, defaulting to Y/N/NA"
                )
                question["options"] = ["Yes", "No", "Not applicable"]
            options = text_to_options(question["options"])
            block_list = block_formatters.add_block(block_list, blocks.radio_buttons)
            block_list[-1]["label"]["text"] = question.get("text", "Choose an option")
            block_list[-1]["element"]["options"] = options
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", misc.hash_question(question["text"])
            )
            if question.get("optional"):
                block_list[-1]["optional"] = True

        # Check if we're adding a static dropdown menu
        elif question["type"] == "static_dropdown":
            block_list = block_formatters.add_block(block_list, blocks.static_dropdown)
            block_list[-1]["label"]["text"] = question.get("text", "Choose an option")
            if question.get("action_id"):
                block_list[-1]["element"]["action_id"] = question["action_id"]
            else:
                block_list[-1]["element"]["action_id"] = question.get(
                    "action_id", misc.hash_question(question["text"])
                )

            # Taiga mapping
            if question.get("taiga_map") in ["type", "severity"] and taiga_project_id:
                # This question will be used to map to a Taiga field
                need_query = True
                if question.get("options"):
                    if not taigalink.validate_form_options(
                        project_id=taiga_project_id,
                        option_type=question.get("taiga_map", "type"),
                        options=question.get("options", ["invalid"]),
                        taigacon=taigacon,
                        taiga_cache=taiga_cache,
                    ):
                        logger.warning(
                            f"Invalid options for {question.get('taiga_map', 'type')} mapping"
                        )
                        logger.warning(f"Options: {question.get('options')}")
                        logger.warning("Using all available options instead")
                        need_query = True
                    else:
                        need_query = False
                else:
                    need_query = True

                if need_query:
                    if question.get("taiga_map") == "type":
                        key = "types"
                    elif question.get("taiga_map") == "severity":
                        key = "severities"
                    raw_options = taiga_cache["boards"][taiga_project_id][key]
                    # trim raw options to just names
                    question["options"] = [
                        option["name"] for option in raw_options.values()
                    ]

            # Add fallback options
            if "options" not in question and question.get("taiga_map") not in [
                "type",
                "severity",
            ]:
                logger.info(
                    "No options provided for dropdown question, defaulting to Y/N/NA"
                )
                question["options"] = ["Yes", "No", "Not applicable"]
            options = text_to_options(question["options"])
            block_list[-1]["element"]["options"] = options

            # Add optional placeholder
            if "placeholder" in question:
                block_list[-1]["element"]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                block_list[-1]["element"].pop("placeholder")

            if question.get("optional"):
                block_list[-1]["optional"] = True

        # Check if we're adding a multi slack user select
        elif question["type"] == "multi_users_select":
            block_list = block_formatters.add_block(
                block_list, blocks.multi_users_select
            )
            block_list[-1]["label"]["text"] = question.get("text", "Choose a user")
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", misc.hash_question(question["text"])
            )

            # Placeholders are optional
            if "placeholder" in question:
                block_list[-1]["element"]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                block_list[-1]["element"].pop("placeholder")
            if question.get("optional"):
                block_list[-1]["optional"] = True

        # Check if we're adding a date select
        elif question["type"] == "date":
            block_list = block_formatters.add_block(block_list, blocks.date_select)
            block_list[-1]["label"]["text"] = question.get("text", "Choose a date")
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", misc.hash_question(question["text"])
            )

            # Slack prioritises the initial date over the placeholder so we'll do the same

            # If we have an initial date set, use it
            if "initial_date" in question:
                # Validate the date by turning it into a datetime object
                try:
                    datetime.strptime(question["initial_date"], "%Y-%m-%d")
                except ValueError:
                    raise ValueError(
                        "Invalid date format for initial_date. Dates should be in the format YYYY-MM-DD"
                    )
                block_list[-1]["element"]["initial_date"] = question["initial_date"]
            elif "placeholder" in question:
                block_list[-1]["element"]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                # If we don't have a placeholder we'll use todays date
                block_list[-1]["element"]["initial_date"] = datetime.now().strftime(
                    "%Y-%m-%d"
                )
                # We'll also remove the placeholder present in the base blocks object
                block_list[-1]["element"].pop("placeholder")

            if question.get("optional"):
                block_list[-1]["optional"] = True

        # Check if we're adding a file upload
        elif question["type"] == "file":
            block_list = block_formatters.add_block(block_list, blocks.file_input)
            block_list[-1]["label"]["text"] = question.get("text", "Upload a file")
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", misc.hash_question(question["text"])
            )

            if question.get("optional"):
                block_list[-1]["optional"] = True

            if question.get("file_type"):
                if type(question["file_type"]) != list:
                    raise ValueError("File type must be a list of strings")
                block_list[-1]["element"]["filetypes"] = question["file_type"]

            if question.get("max_files"):
                if type(question["max_files"]) != int:
                    raise ValueError("Max files must be an integer")
                if 11 < question["max_files"] < 1:
                    raise ValueError("Max files must be between 1 and 10")
                block_list[-1]["element"]["max_files"] = question

        # Check if we're adding checkboxes
        elif question["type"] == "checkboxes":
            if "options" not in question:
                raise ValueError("Checkbox question must have options")
            options = text_to_options(question["options"])
            block_list = block_formatters.add_block(block_list, blocks.checkboxes)
            block_list[-1]["label"]["text"] = question.get("text", "Choose an option")
            block_list[-1]["element"]["options"] = options
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", misc.hash_question(question["text"])
            )
            if question.get("optional"):
                block_list[-1]["optional"] = True

        else:
            raise ValueError("Invalid question type")

        # See if we need to add a divider after the question
        if "divider" in question:
            if question["divider"] == "after":
                block_list = block_formatters.add_block(block_list, blocks.divider)

    return block_list


def text_to_options(options: list[str]):
    """Convert a list of strings to a list of option dictionaries"""
    if len(options) > 10:
        logger.warning(f"Too many options ({len(options)}). Truncating to 10")
        options = options[:10]

    formatted_options = []
    for option in options:
        if len(option) > 150:
            logger.warning(
                f"Option '{option}' is too long for value to be set. Truncating to 150 characters"
            )
            option = option[:150]
        formatted_options.append(copy(blocks.option))
        formatted_options[-1]["text"]["text"] = option
        formatted_options[-1]["value"] = option

    return formatted_options


def viewedit_blocks(
    taigacon: taiga.client.TaigaAPI,
    project_id: int | str,
    item_id,
    item_type,
    taiga_cache: dict,
    config: dict,
    taiga_auth_token: str,
    edit=True,
):
    """Generate the blocks for a modal for viewing and editing an item"""

    if item_type == "issue":
        item = taigacon.issues.get(resource_id=item_id)
        history: list = taigacon.history.issue.get(resource_id=item_id)
    elif item_type == "task":
        item = taigacon.tasks.get(resource_id=item_id)
        history: list = taigacon.history.task.get(resource_id=item_id)
    elif item_type in ["story", "userstory"]:
        item = taigacon.user_stories.get(resource_id=item_id)
        history: list = taigacon.history.user_story.get(resource_id=item_id)
        item_type = "story"
    else:
        raise ValueError(f"Unknown item type {item_type}")

    # Check if the item has an actual description
    if not item.description:
        item.description = "<No description provided>"

    # Convert normal description markdown to slack markdown
    item.description = slack_misc.convert_markdown(item.description)

    # Build up a history of comments
    comments = []
    for event in history:
        if event["comment"]:
            # Skip deleted comments
            if event["delete_comment_user"]:
                continue

            name = event["user"]["name"]
            comment = event["comment"]
            image = event["user"]["photo"]

            # Calculate a useful date
            # Comes in format of 2024-11-29T06:09:39.642Z
            comment_date: datetime = datetime.strptime(
                event["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
            )

            # Date is in UTC, convert to WAST by adding 8 hours
            comment_date = comment_date.replace(tzinfo=None)
            comment_date = comment_date + timedelta(hours=8)

            # When we post we add a byline that we want to strip out from display
            if event["comment"].startswith("Posted from Slack"):
                match = re.match(r"Posted from Slack by (.*?): (.*)", event["comment"])
                if match:
                    name = match.group(1)
                    comment = match.group(2)

                    # Look for a Taiga user with the same name
                    found_taiga = False
                    for user_info in taiga_cache["users"].values():
                        if user_info["name"].lower() == name.lower():
                            image = user_info["photo"]
                            found_taiga = True
                    if not found_taiga:
                        image = None

            comments.append(
                {
                    "author": name,
                    "comment": comment,
                    "photo": image,
                    "date": comment_date,
                }
            )

    # We want to show the most recent comments last and the history list is in reverse order
    comments.reverse()

    # Construct the blocks
    block_list = []

    if not edit:
        block_list = block_formatters.add_block(block_list, blocks.context)
        block_list = block_formatters.inject_text(
            block_list=block_list,
            text=strings.view_only,
        )

    # Add the item title
    block_list = block_formatters.add_block(block_list, blocks.header)
    block_list = block_formatters.inject_text(
        block_list=block_list, text=f"{item_type.capitalize()}: {item.subject}"
    )

    # Add context of who created the item
    block_list = block_formatters.add_block(block_list, blocks.context)
    elements = []
    elements.append(
        {
            "type": "image",
            "image_url": item.owner_extra_info["photo"],
            "alt_text": "User photo",
        }
    )
    elements.append(
        {
            "type": "mrkdwn",
            "text": f"*Created by*: {item.owner_extra_info['full_name_display']}",
        }
    )
    block_list[-1]["elements"] = elements

    # Add a promote button if the item is an issue
    if item_type == "issue" and edit:
        button = copy(blocks.button)
        button["text"]["text"] = "Promote to story"
        button["action_id"] = (
            f"promote_issue-{item.project_extra_info['id']}-issue-{item.id}"
        )
        # If there are comments warn that they'll be removed
        if comments:
            button["confirm"] = {
                "title": {"type": "plain_text", "text": "Promote to story"},
                "text": {
                    "type": "plain_text",
                    "text": f"The {len(comments)} comment{'s' if len(comments)> 1 else ''} on this issue will be mirrored to the new story as a single message. Are you sure?",
                },
                "confirm": {"type": "plain_text", "text": "Promote"},
                "deny": {"type": "plain_text", "text": "Cancel"},
            }

        block_list = block_formatters.add_block(block_list, blocks.actions)
        block_list[-1]["elements"].append(button)
        block_list[-1].pop("block_id")

    # Parent card if task
    if item_type == "task":
        block_list = block_formatters.add_block(block_list, blocks.divider)
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list,
            text=f"*Parent card:* {item.user_story_extra_info['subject']}",
        )
        # Add an accessory to view the parent card
        button = copy(blocks.button)
        button["text"]["text"] = "View parent"
        button["action_id"] = (
            f"viewedit-{item.project_extra_info['id']}-story-{item.user_story_extra_info['id']}-update"
        )
        block_list[-1]["accessory"] = button
        block_list = block_formatters.add_block(block_list, blocks.divider)

    block_list = block_formatters.add_block(block_list, blocks.text)
    block_list = block_formatters.inject_text(
        block_list=block_list, text=f"{item.description}"
    )

    # Info fields
    block_list[-1]["fields"] = []

    if item_type == "task":
        block_list[-1]["fields"].append(
            {
                "type": "mrkdwn",
                "text": f"*Parent card:* {item.user_story_extra_info['subject']}",
            }
        )

    block_list[-1]["fields"].append(
        {
            "type": "mrkdwn",
            "text": f"*Status:* {item.status_extra_info['name']}",
        }
    )

    if item.assigned_to:
        block_list[-1]["fields"].append(
            {
                "type": "mrkdwn",
                "text": f"*Assigned to:* {item.assigned_to_extra_info['full_name_display']}",
            }
        )

    if item.watchers:
        watcher_strs = []
        for watcher in item.watchers:
            watcher_strs.append(taiga_cache["users"][watcher]["name"])
        if "Giant Robot" in watcher_strs:
            watcher_strs.remove("Giant Robot")

        if watcher_strs:
            block_list[-1]["fields"].append(
                {
                    "type": "mrkdwn",
                    "text": f"*Watchers:* {', '.join(watcher_strs)}",
                }
            )

    if item.due_date:
        due_datetime = datetime.strptime(item.due_date, "%Y-%m-%d")
        days_until = (due_datetime - datetime.now()).days

        block_list[-1]["fields"].append(
            {
                "type": "mrkdwn",
                "text": f"*Due:* {item.due_date} ({days_until} days)",
            }
        )

    # Issues have some extra fields
    if item_type == "issue":
        block_list[-1]["fields"].append(
            {
                "type": "mrkdwn",
                "text": f"*Type:* {taiga_cache['boards'][item.project]['types'][item.type]['name']}",
            }
        )

        block_list[-1]["fields"].append(
            {
                "type": "mrkdwn",
                "text": f"*Severity:* {taiga_cache['boards'][item.project]['severities'][item.severity]['name']}",
            }
        )

        block_list[-1]["fields"].append(
            {
                "type": "mrkdwn",
                "text": f"*Priority:* {taiga_cache['boards'][item.project]['priorities'][item.priority]['name']}",
            }
        )

    # Attach info field edit button
    if edit:
        button = copy(blocks.button)
        button["text"]["text"] = "Edit"
        button["action_id"] = f"edit_info"
        block_list[-1]["accessory"] = button

    # Tasks
    if item_type == "story":
        tasks = taigalink.get_tasks(
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=False,
            story_id=item_id,
            filters={},
        )
        if tasks:
            block_list = block_formatters.add_block(block_list, blocks.divider)
            block_list = block_formatters.add_block(block_list, blocks.header)
            block_list = block_formatters.inject_text(
                block_list=block_list, text="Attached open tasks"
            )

            closed = 0

            for task in tasks:

                if task["is_closed"]:
                    closed += 1
                else:
                    task_str = (
                        f"• {task['subject']} ({task['status_extra_info']['name']})"
                    )
                    block_list = block_formatters.add_block(block_list, blocks.text)
                    block_list = block_formatters.inject_text(
                        block_list=block_list, text=task_str
                    )
                    button = copy(blocks.button)
                    if edit:
                        button["text"]["text"] = "View/edit"
                    else:
                        button["text"]["text"] = "View"
                    button["action_id"] = (
                        f"viewedit-{item.project_extra_info['id']}-task-{task['id']}-update"
                    )
                    block_list[-1]["accessory"] = button

            # If all of the tasks are closed none of them will be shown
            if closed == len(tasks):
                block_list = block_formatters.add_block(block_list, blocks.text)
                block_list = block_formatters.inject_text(
                    block_list=block_list, text="<No open tasks>"
                )

            # Add a button to view all tasks
            block_list = block_formatters.add_block(block_list, blocks.actions)
            block_list[-1].pop("block_id")
            button = copy(blocks.button)
            button["text"][
                "text"
            ] = f"View all tasks {misc.calculate_circle_emoji(closed,len(tasks))} ({closed}/{len(tasks)})"
            button["action_id"] = f"view_tasks-{item_id}"
            block_list[-1]["elements"].append(button)

    # Files
    block_list = block_formatters.add_block(block_list, blocks.divider)
    block_list = block_formatters.add_block(block_list, blocks.header)
    block_list = block_formatters.inject_text(block_list=block_list, text="Files")

    if len(item.list_attachments()) == 0:
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text="<No files attached>"
        )

    images_attached = 0

    for attachment in item.list_attachments():
        if attachment.is_deprecated:
            continue

        filetype = attachment.attached_file.split(".")[-1]

        if filetype in ["png", "jpg", "jpeg", "gif"]:
            images_attached += 1
        # Display other files as links using the description as the text if possible
        if attachment.description:
            block_list = block_formatters.add_block(block_list, blocks.text)
            block_list = block_formatters.inject_text(
                block_list=block_list,
                text=f"• <{attachment.url}|{attachment.description}>",
            )
        else:
            block_list = block_formatters.add_block(block_list, blocks.text)
            block_list = block_formatters.inject_text(
                block_list=block_list,
                text=f"• <{attachment.url}|{attachment.name}>",
            )

    # Add a button to view images if there are any
    buttons = []
    # Create attach button
    if edit:
        button = copy(blocks.button)
        button["text"]["text"] = "Attach files"
        button["action_id"] = "home-attach_files"
        buttons.append(button)

    # Add view images button if there's at least one image
    if images_attached:
        button = copy(blocks.button)
        button["text"]["text"] = f"View image attachments inline ({images_attached})"
        button["action_id"] = f"view_attachments-{project_id}-{item_type}-{item_id}"
        buttons.append(button)

    if buttons:
        block_list = block_formatters.add_block(block_list, blocks.actions)
        block_list[-1]["elements"] = buttons
        block_list[-1].pop("block_id")

    # Comments
    block_list = block_formatters.add_block(block_list, blocks.divider)
    block_list = block_formatters.add_block(block_list, blocks.header)
    block_list = block_formatters.inject_text(block_list=block_list, text="Comments")
    for comment in comments:
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=f"{comment['comment']}"
        )
        block_list = block_formatters.add_block(block_list, blocks.context)
        elements = []
        if comment["photo"]:
            elements.append(
                {
                    "type": "image",
                    "image_url": comment["photo"],
                    "alt_text": "User photo",
                }
            )
        elements.append(
            {
                "type": "mrkdwn",
                "text": f"*{comment['author']}*",
            }
        )
        elements.append(
            {
                "type": "mrkdwn",
                "text": f"<!date^{int(comment['date'].timestamp())}^{{ago}} ({{date_short_pretty}})|{comment['date'].strftime('%Y-%m-%d %H:%M') }>",
            }
        )
        block_list[-1]["elements"] = elements

    if not comments:
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text="<No comments yet>"
        )
        block_list = block_formatters.add_block(block_list, blocks.divider)

    # Add a comment input field
    block_list = block_formatters.add_block(block_list, blocks.text_question)

    block_list[-1]["element"]["multiline"] = True
    block_list[-1]["block_id"] = "comment_field"
    block_list[-1]["label"]["text"] = "Add a comment"
    block_list[-1]["element"]["placeholder"]["text"] = "Type your comment here"

    # Make the field optional
    block_list[-1]["optional"] = True

    # Add some junk to the end of the action_id so the input isn't preserved in future modals
    block_list[-1]["element"]["action_id"] = f"add_comment{time.time()}"

    # Add a comment button
    # We use this instead of the submit button because we can't push the modal view to the user after a submission event
    block_list = block_formatters.add_block(block_list, blocks.actions)
    block_list[-1]["elements"].append(copy(blocks.button))
    block_list[-1]["elements"][0]["text"]["text"] = "Comment"
    block_list[-1]["elements"][0]["action_id"] = "submit_comment"

    return block_list


def edit_info_blocks(
    taigacon: taiga.client.TaigaAPI,
    project_id: int | str,
    item_id,
    item_type,
    taiga_cache: dict,
    new: bool = False,
    description: str = "",
):
    """Return the blocks required for editing information about an item"""

    # Get the item from Taiga
    if not new:
        if item_type == "issue":
            item = taigacon.issues.get(item_id)
        elif item_type == "task":
            item = taigacon.tasks.get(item_id)
        elif item_type == "story":
            item = taigacon.user_stories.get(item_id)
    raw_statuses: dict = taiga_cache["boards"][int(project_id)]["statuses"][item_type]

    # Trim down the board members to just id:name
    raw_board_members = taiga_cache["boards"][int(project_id)]["members"]
    board_users = {
        user_id: user_info["name"]
        for user_id, user_info in raw_board_members.items()
        if user_info["name"] != "Giant Robot"
    }

    # Turn them into options for later
    user_options = []
    for user in board_users:
        user_options.append(
            {
                "text": {"type": "plain_text", "text": board_users[user]},
                "value": str(user),
            }
        )

    # All item types have status, watchers, assigned_to, due_date, subject, description, tags
    # Issues also have type, severity, priority

    if not new:
        current_status = {item.status: item.status_extra_info["name"]}

    # Trim down the status info to just the status name
    statuses = {
        status_id: status_info["name"]
        for status_id, status_info in raw_statuses.items()
    }

    if item_type == "issue":
        if not new:
            current_type = item.type
            current_severity = item.severity
            current_priority = item.priority

        # Trim down the type info to just the type name
        types = {
            type_id: type_info["name"]
            for type_id, type_info in taiga_cache["boards"][int(project_id)][
                "types"
            ].items()
        }

        # Trim down the severity info to just the severity name
        severities = {
            severity_id: severity_info["name"]
            for severity_id, severity_info in taiga_cache["boards"][int(project_id)][
                "severities"
            ].items()
        }

        # Trim down the priority info to just the priority name
        priorities = {
            priority_id: priority_info["name"]
            for priority_id, priority_info in taiga_cache["boards"][int(project_id)][
                "priorities"
            ].items()
        }

    if new:
        subject = ""
        due_date = ""
        current_watchers = []
        current_assigned = None
    else:
        current_watchers = item.watchers
        if 6 in current_watchers:
            current_watchers.remove(6)

        current_assigned = item.assigned_to
        due_date = item.due_date
        subject = item.subject
        description = item.description

    # Set up blocks
    block_list = []

    # Subject
    block_list = block_formatters.add_block(block_list, blocks.text_question)
    block_list[-1]["label"]["text"] = "Title"
    block_list[-1]["element"]["initial_value"] = subject
    block_list[-1]["element"]["action_id"] = "subject"
    block_list[-1]["block_id"] = "subject"
    block_list[-1]["element"].pop("placeholder")

    # Status
    block_list = block_formatters.add_block(block_list, blocks.static_dropdown)
    block_list[-1]["label"]["text"] = "Status"
    block_list[-1]["element"]["options"] = []
    for status in statuses:
        block_list[-1]["element"]["options"].append(
            {
                "text": {"type": "plain_text", "text": statuses[status]},
                "value": str(status),
            }
        )
    block_list[-1]["element"]["action_id"] = "status"
    block_list[-1]["block_id"] = "status"
    if new:
        block_list[-1]["element"]["placeholder"]["text"] = "Select a status"
    else:
        block_list[-1]["element"]["placeholder"]["text"] = "Change the status"
        block_list[-1]["element"]["initial_option"] = {
            "text": {"type": "plain_text", "text": current_status[item.status]},
            "value": str(item.status),
        }
        # Add closing options if the status is open
        if not item.is_closed:
            closing_statuses = taiga_cache["boards"][item.project]["closing_statuses"][
                item_type
            ]
            button_list = []
            for status in closing_statuses:
                button = copy(blocks.button)
                button["text"]["text"] = f"Close as {status['name']}"
                button["action_id"] = (
                    f"complete-{item.project}-{item_type}-{item.id}-{status['id']}"
                )
                button["confirm"] = {
                    "title": {"type": "plain_text", "text": f"Close {item_type}"},
                    "text": {
                        "type": "plain_text",
                        "text": f"Are you sure you want to mark this {item_type} as {status['name']}?",
                    },
                    "confirm": {
                        "type": "plain_text",
                        "text": f"Mark as {status['name']}",
                    },
                    "deny": {"type": "plain_text", "text": "Cancel"},
                }
                button_list.append(button)

            block_list = block_formatters.add_block(block_list, blocks.actions)
            block_list[-1]["elements"] = button_list
            block_list[-1].pop("block_id")

    if item_type == "issue":
        # Type
        block_list = block_formatters.add_block(block_list, blocks.static_dropdown)
        block_list[-1]["label"]["text"] = "Type"
        block_list[-1]["element"]["options"] = []
        block_list[-1]["element"]["placeholder"]["text"] = "Change the type"
        for type_id in types:
            block_list[-1]["element"]["options"].append(
                {
                    "text": {"type": "plain_text", "text": types[type_id]},
                    "value": str(type_id),
                }
            )
        block_list[-1]["element"]["action_id"] = "type"
        block_list[-1]["block_id"] = "type"
        if new:
            block_list[-1]["element"]["placeholder"]["text"] = "Select a type"
        else:
            block_list[-1]["element"]["placeholder"]["text"] = "Change the type"
            block_list[-1]["element"]["initial_option"] = {
                "text": {"type": "plain_text", "text": types[current_type]},
                "value": str(current_type),
            }

        # Severity
        block_list = block_formatters.add_block(block_list, blocks.static_dropdown)
        block_list[-1]["label"]["text"] = "Severity"
        block_list[-1]["element"]["options"] = []
        for severity_id in severities:
            block_list[-1]["element"]["options"].append(
                {
                    "text": {"type": "plain_text", "text": severities[severity_id]},
                    "value": str(severity_id),
                }
            )
        block_list[-1]["element"]["action_id"] = "severity"
        block_list[-1]["block_id"] = "severity"
        if new:
            block_list[-1]["element"]["placeholder"]["text"] = "Select a severity"
        else:
            block_list[-1]["element"]["placeholder"]["text"] = "Change the severity"
            block_list[-1]["element"]["initial_option"] = {
                "text": {"type": "plain_text", "text": severities[current_severity]},
                "value": str(current_severity),
            }

        # Priority
        block_list = block_formatters.add_block(block_list, blocks.static_dropdown)
        block_list[-1]["label"]["text"] = "Priority"
        block_list[-1]["element"]["options"] = []
        for priority_id in priorities:
            block_list[-1]["element"]["options"].append(
                {
                    "text": {"type": "plain_text", "text": priorities[priority_id]},
                    "value": str(priority_id),
                }
            )
        block_list[-1]["element"]["action_id"] = "priority"
        block_list[-1]["block_id"] = "priority"
        if new:
            block_list[-1]["element"]["placeholder"]["text"] = "Select a priority"
        else:
            block_list[-1]["element"]["placeholder"]["text"] = "Change the priority"
            block_list[-1]["element"]["initial_option"] = {
                "text": {"type": "plain_text", "text": priorities[current_priority]},
                "value": str(current_priority),
            }

    # Description
    block_list = block_formatters.add_block(block_list, blocks.text_question)
    block_list[-1]["label"]["text"] = "Description"
    block_list[-1]["element"]["multiline"] = True
    block_list[-1]["element"]["action_id"] = "description"
    block_list[-1]["block_id"] = "description"
    block_list[-1]["optional"] = True
    if description:
        block_list[-1]["element"]["initial_value"] = description
        block_list[-1]["element"].pop("placeholder")
    else:
        block_list[-1]["element"]["placeholder"]["text"] = "Enter a description"

    # Due date
    block_list = block_formatters.add_block(block_list, blocks.base_input)
    block_list[-1]["label"]["text"] = "Due date"
    block_list[-1]["block_id"] = "due_date"
    block_list[-1]["optional"] = True
    cal = copy(blocks.cal_select)
    cal["action_id"] = "due_date"
    cal.pop("placeholder")
    if due_date:
        cal["initial_date"] = due_date
    block_list[-1]["element"] = cal

    # Assigned to
    block_list = block_formatters.add_block(block_list, blocks.static_dropdown)
    block_list[-1]["label"]["text"] = "Assigned to"
    block_list[-1]["element"]["options"] = user_options
    block_list[-1]["element"]["action_id"] = "assigned_to"
    block_list[-1]["block_id"] = "assigned_to"
    block_list[-1]["optional"] = True
    block_list[-1]["element"]["placeholder"]["text"] = "Assign the item"
    if current_assigned:
        block_list[-1]["element"]["initial_option"] = {
            "text": {"type": "plain_text", "text": board_users[current_assigned]},
            "value": str(current_assigned),
        }

    # Watchers
    block_list = block_formatters.add_block(block_list, blocks.multi_static_dropdown)
    block_list[-1]["label"]["text"] = "Watchers"
    block_list[-1]["element"]["options"] = user_options
    block_list[-1]["element"]["action_id"] = "watchers"
    block_list[-1]["block_id"] = "watchers"
    block_list[-1]["optional"] = True
    block_list[-1]["element"]["placeholder"]["text"] = "Users watching the item"
    if len(current_watchers) > 0:
        block_list[-1]["element"]["initial_options"] = []
        for watcher in current_watchers:
            block_list[-1]["element"]["initial_options"].append(
                {
                    "text": {"type": "plain_text", "text": board_users[watcher]},
                    "value": str(watcher),
                }
            )

    return block_list


def new_item_selector_blocks(taiga_id: int, taiga_cache: dict):
    """Generate the blocks for a modal to select the type of item to create and on what project

    Will show an optional description field if passed "description" """

    # Get the user's projects
    user_projects = taiga_cache["users"][taiga_id]["projects"]

    # Set up blocks
    block_list = []

    # Project selector
    block_list = block_formatters.add_block(block_list, blocks.static_dropdown)
    block_list[-1]["label"]["text"] = "Project"
    block_list[-1]["element"]["options"] = []
    for project_id in user_projects:
        block_list[-1]["element"]["options"].append(
            {
                "text": {
                    "type": "plain_text",
                    "text": taiga_cache["boards"][project_id]["name"],
                },
                "value": str(project_id),
            }
        )
    block_list[-1]["element"]["action_id"] = "project"
    block_list[-1]["block_id"] = "project"
    block_list[-1]["element"]["placeholder"]["text"] = "Select a project"

    # Item type selector
    block_list = block_formatters.add_block(block_list, blocks.static_dropdown)
    block_list[-1]["label"]["text"] = "Item type"
    for item_type in ["story", "issue"]:
        block_list[-1]["element"]["options"].append(
            {
                "text": {"type": "plain_text", "text": item_type.capitalize()},
                "value": item_type,
            }
        )
    block_list[-1]["element"]["action_id"] = "item_type"
    block_list[-1]["block_id"] = "item_type"
    block_list[-1]["element"]["placeholder"]["text"] = "Select an item type"

    return block_list


def app_home(
    user_id: str,
    config: dict,
    tidyhq_cache: dict,
    taiga_cache: dict,
    taiga_auth_token: str,
    private_metadata: str | None,
    provided_user_stories: list = [],
    provided_issues: list = [],
    provided_tasks: list = [],
    compress=False,
) -> list:
    """Generate the blocks for the app home view for a specified user and return it as a list of blocks."""
    # Check if the user has a Taiga account

    if compress:
        logger.info(f"Compressing blocks for user {user_id}")

    taiga_id = tidyhq.map_slack_to_taiga(
        tidyhq_cache=tidyhq_cache,
        config=config,
        slack_id=user_id,
    )

    filters = {}
    raw_filters = ""
    if private_metadata:
        raw_filters = json.loads(private_metadata)
        # Clean up the filters for use
        for key in raw_filters:
            filters[key] = []
            for option in raw_filters[key][key]["selected_options"]:
                filters[key].append(option["value"])

    block_list = []
    block_list = block_formatters.add_block(block_list, blocks.header)
    block_list = block_formatters.inject_text(
        block_list=block_list, text=strings.header
    )

    # Add submit form
    block_list = block_formatters.add_block(block_list, blocks.actions)
    block_list[-1].pop("block_id")
    block_list[-1]["elements"].append(copy(blocks.button))
    block_list[-1]["elements"][-1]["text"]["text"] = "Submit a form"
    block_list[-1]["elements"][-1]["action_id"] = "submit_form"

    if not taiga_id:
        logger.info(f"User {user_id} does not have a Taiga account")

        # Add filter button
        block_list[-1]["elements"].append(copy(blocks.button))
        block_list[-1]["elements"][-1]["text"]["text"] = "Filter"
        block_list[-1]["elements"][-1]["action_id"] = "filter_home_modal"

        # Add clear filter button if the filter is not the default
        if raw_filters != const.base_filter:
            block_list[-1]["elements"].append(copy(blocks.button))
            block_list[-1]["elements"][-1]["text"]["text"] = "Reset filter"
            block_list[-1]["elements"][-1]["action_id"] = "clear_filter"

        taiga_id = config["taiga"]["guest_user"]

        # Construct blocks
        block_list = block_formatters.add_block(block_list, blocks.text)

        # Figure out why we don't know the user
        reasons = []
        if not tidyhq.map_slack_to_tidyhq(
            tidyhq_cache=tidyhq_cache, slack_id=user_id, config=config
        ):
            reasons.append(strings.unrecognised_no_tidyhq)
        else:
            reasons.append(strings.unrecognised_no_taiga_match)
            reasons.append(strings.unrecognised_no_taiga)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=strings.unrecognised + "\n" + "\n".join(reasons)
        )
        block_list = block_formatters.add_block(block_list, blocks.divider)
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=strings.do_instead
        )

    else:
        # We recognise the user
        logger.info(f"User {user_id} has a Taiga account - {taiga_id}")

        # Add create button
        block_list[-1]["elements"].append(copy(blocks.button))
        block_list[-1]["elements"][-1]["text"]["text"] = "Create an item"
        block_list[-1]["elements"][-1]["action_id"] = "create_item"

        # Add filter button
        block_list[-1]["elements"].append(copy(blocks.button))
        block_list[-1]["elements"][-1]["text"]["text"] = "Filter"
        block_list[-1]["elements"][-1]["action_id"] = "filter_home_modal"

        # Add clear filter button if the filter is not the default
        if raw_filters != const.base_filter:
            block_list[-1]["elements"].append(copy(blocks.button))
            block_list[-1]["elements"][-1]["text"]["text"] = "Reset filter"
            block_list[-1]["elements"][-1]["action_id"] = "clear_filter"
            block_list[-1]["elements"][-1]["style"] = "danger"

        # Construct blocks
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=strings.explainer
        )
        block_list = block_formatters.add_block(block_list, blocks.divider)

    # High frequency users will end up going over the 100 block limit
    at_block_limit = False
    compressed_blocks = False
    items_added = 0

    ##########
    # Stories
    ##########

    block_list = block_formatters.add_block(block_list, blocks.header)
    block_list = block_formatters.inject_text(block_list=block_list, text="Stories")

    if provided_user_stories:
        user_stories = provided_user_stories
    else:
        # Get all assigned user stories for the user
        user_stories = taigalink.get_stories(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
            filters=filters,
        )

    if len(user_stories) == 0:
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=strings.no_stories
        )
        block_list = block_formatters.add_block(block_list, blocks.divider)

    else:
        # Sort the user stories by project
        sorted_stories = taigalink.sort_by_project(user_stories)

        for project in sorted_stories:
            if len(block_list) > 95:
                block_list = block_formatters.compress_blocks(block_list=block_list)
                compressed_blocks = True
                if len(block_list) > 95:
                    # Check if we're already in a compressed recursion
                    if not compress:
                        return app_home(
                            user_id=user_id,
                            config=config,
                            tidyhq_cache=tidyhq_cache,
                            taiga_cache=taiga_cache,
                            taiga_auth_token=taiga_auth_token,
                            provided_user_stories=user_stories,
                            compress=True,
                            private_metadata=private_metadata,
                        )
                    at_block_limit = True
                    break
            items_added += 1
            header, body, story_blocks = block_formatters.format_stories(
                story_list=sorted_stories[project], compressed=compress
            )
            if not compress:
                block_list = block_formatters.add_block(block_list, blocks.text)
                block_list = block_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
            block_list += story_blocks
            block_list = block_formatters.add_block(block_list, blocks.divider)

        # Remove the last divider
        block_list.pop()

    block_list = block_formatters.add_block(block_list, blocks.divider)

    ##########
    # Issues
    ##########

    block_list = block_formatters.add_block(block_list, blocks.header)
    block_list = block_formatters.inject_text(block_list=block_list, text="Issues")

    if provided_issues:
        user_issues = provided_issues
    else:
        # Get all assigned issues for the user
        user_issues = taigalink.get_issues(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
            filters=filters,
        )

    if len(user_issues) == 0:
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=strings.no_issues
        )
        block_list = block_formatters.add_block(block_list, blocks.divider)

    else:
        # Sort the user issues by project
        sorted_issues = taigalink.sort_by_project(user_issues)

        for project in sorted_issues:
            if len(block_list) > 95:
                block_list = block_formatters.compress_blocks(block_list=block_list)
                compressed_blocks = True
                if len(block_list) > 95:
                    # Check if we're already in a compressed recursion
                    if not compress:
                        return app_home(
                            user_id=user_id,
                            config=config,
                            tidyhq_cache=tidyhq_cache,
                            taiga_cache=taiga_cache,
                            taiga_auth_token=taiga_auth_token,
                            provided_user_stories=user_stories,
                            provided_issues=user_issues,
                            compress=True,
                            private_metadata=private_metadata,
                        )
                    at_block_limit = True
                    break
            items_added += 1
            header, body, issue_blocks = block_formatters.format_issues(
                issue_list=sorted_issues[project], compressed=compress
            )
            if not compress:
                block_list = block_formatters.add_block(block_list, blocks.text)
                block_list = block_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
            block_list += issue_blocks
            block_list = block_formatters.add_block(block_list, blocks.divider)

        # Remove the last divider
        block_list.pop()

    block_list = block_formatters.add_block(block_list, blocks.divider)

    ##########
    # Tasks
    ##########

    block_list = block_formatters.add_block(block_list, blocks.header)
    block_list = block_formatters.inject_text(block_list=block_list, text="Tasks")

    if provided_tasks:
        tasks = provided_tasks
    else:
        # Get all tasks for the user
        tasks = taigalink.get_tasks(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
            filters=filters,
        )

    if len(tasks) == 0:
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=strings.no_tasks
        )

    else:

        # Sort the tasks based on user story
        sorted_tasks = taigalink.sort_tasks_by_user_story(tasks)

        # Things will start to break down if there are too many tasks
        for project in sorted_tasks:
            if len(block_list) > 95:
                block_list = block_formatters.compress_blocks(block_list=block_list)
                compressed_blocks = True
                if len(block_list) > 95:
                    # Check if we're already in a compressed recursion
                    if not compress:
                        return app_home(
                            user_id=user_id,
                            config=config,
                            tidyhq_cache=tidyhq_cache,
                            taiga_cache=taiga_cache,
                            taiga_auth_token=taiga_auth_token,
                            provided_user_stories=user_stories,
                            provided_issues=user_issues,
                            provided_tasks=tasks,
                            compress=True,
                            private_metadata=private_metadata,
                        )
                    at_block_limit = True
                    break
            items_added += 1
            header, body, task_blocks = block_formatters.format_tasks(
                task_list=sorted_tasks[project], compressed=compress
            )

            # Skip over tasks assigned in template cards
            if "template" in header.lower():
                continue

            if not compress:
                block_list = block_formatters.add_block(block_list, blocks.text)
                block_list = block_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
            block_list += task_blocks
            block_list = block_formatters.add_block(block_list, blocks.divider)

        # Remove the last divider
        block_list.pop()

    popped = 0
    while len(block_list) > 97:
        block_list = block_formatters.compress_blocks(block_list=block_list)
        block_list.pop()
        popped += 1
    logger.info(f"Popped {popped} blocks")

    if at_block_limit:
        block_list = block_formatters.add_block(block_list, blocks.divider)
        block_list = block_formatters.add_block(block_list, blocks.text)
        block_list = block_formatters.inject_text(
            block_list=block_list, text=strings.trimmed.format(items=items_added)
        )
        if compressed_blocks:
            block_list[-1]["text"]["text"] += " " + strings.compressed

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

    block_list = block_formatters.add_block(block_list, blocks.context)
    block_list = block_formatters.inject_text(
        block_list=block_list,
        text=strings.footer.format(
            branch=branch_name, commit=commit_hash, platform=platform_name
        ),
    )

    return block_list


def home_filters(taiga_id: int | None, current_state: str, taiga_cache: dict):
    """Generate the blocks for the app home filter modal"""

    # Convert the current state string from json to a dict
    current_state_dict = {}
    if current_state:
        current_state_dict = json.loads(current_state)

    block_list = []

    project_dropdown = copy(blocks.multi_static_dropdown)
    project_dropdown["element"]["placeholder"][
        "text"
    ] = "Select projects that you want to see"
    project_dropdown["label"]["text"] = "By Project"
    project_dropdown["element"]["action_id"] = "project_filter"
    project_dropdown["block_id"] = "project_filter"
    project_dropdown["element"]["options"] = []

    # Add project options
    if taiga_id:
        user_projects: list = taiga_cache["users"][taiga_id]["projects"]
        for project_id in user_projects:
            project_dropdown["element"]["options"].append(
                {
                    "text": {
                        "type": "plain_text",
                        "text": taiga_cache["boards"][project_id]["name"],
                    },
                    "value": str(project_id),
                }
            )
    else:
        # Add all public projects
        for project_id in taiga_cache["boards"]:
            if not taiga_cache["boards"][project_id]["private"]:
                project_dropdown["element"]["options"].append(
                    {
                        "text": {
                            "type": "plain_text",
                            "text": taiga_cache["boards"][project_id]["name"],
                        },
                        "value": str(project_id),
                    }
                )
    # Sort the options
    project_dropdown["element"]["options"] = sorted(
        project_dropdown["element"]["options"], key=lambda x: x["text"]["text"]
    )
    # Add an option for all projects
    project_dropdown["element"]["options"].insert(
        0,
        {
            "text": {"type": "plain_text", "text": "All projects"},
            "value": "all",
        },
    )

    # Set initial options based on current state
    if "project_filter" in current_state_dict:
        project_dropdown["element"]["initial_options"] = current_state_dict[
            "project_filter"
        ]["project_filter"]["selected_options"]
        if project_dropdown["element"]["initial_options"] == []:
            project_dropdown["element"].pop("initial_options")

    block_list.append(project_dropdown)

    # Add checkboxes to filter by watched/assigned status
    related_dropdown = copy(blocks.checkboxes)
    related_dropdown["label"]["text"] = "Filter to only:"
    related_dropdown["element"]["action_id"] = "related_filter"
    related_dropdown["block_id"] = "related_filter"
    related_dropdown["optional"] = True
    related_dropdown["element"]["options"] = [
        {"text": {"type": "plain_text", "text": "Watched"}, "value": "watched"},
        {"text": {"type": "plain_text", "text": "Assigned"}, "value": "assigned"},
    ]

    # Set initial options based on current state
    if "related_filter" in current_state_dict:
        related_dropdown["element"]["initial_options"] = current_state_dict[
            "related_filter"
        ]["related_filter"]["selected_options"]

        if related_dropdown["element"]["initial_options"] == []:
            related_dropdown["element"].pop("initial_options")

    block_list.append(related_dropdown)

    # Add checkboxes to filter by item type
    type_dropdown = copy(blocks.checkboxes)
    type_dropdown["label"]["text"] = "Filter to only:"
    type_dropdown["element"]["action_id"] = "type_filter"
    type_dropdown["block_id"] = "type_filter"
    type_dropdown["optional"] = True
    type_dropdown["element"]["options"] = [
        {"text": {"type": "plain_text", "text": "User Stories"}, "value": "story"},
        {"text": {"type": "plain_text", "text": "Issues"}, "value": "issue"},
        {"text": {"type": "plain_text", "text": "Tasks"}, "value": "task"},
    ]

    # Set initial options based on current state
    if "type_filter" in current_state_dict:
        type_dropdown["element"]["initial_options"] = current_state_dict["type_filter"][
            "type_filter"
        ]["selected_options"]

        if type_dropdown["element"]["initial_options"] == []:
            type_dropdown["element"].pop("initial_options")

    block_list.append(type_dropdown)

    # Add checkboxes to filter by open/closed
    closed_dropdown = copy(blocks.checkboxes)
    closed_dropdown["label"]["text"] = "Filter to only:"
    closed_dropdown["element"]["action_id"] = "closed_filter"
    closed_dropdown["block_id"] = "closed_filter"
    closed_dropdown["optional"] = True
    closed_dropdown["element"]["options"] = [
        {"text": {"type": "plain_text", "text": "Open"}, "value": "open"},
        {"text": {"type": "plain_text", "text": "Closed"}, "value": "closed"},
    ]

    # Set initial options based on current state
    if "closed_filter" in current_state_dict:
        closed_dropdown["element"]["initial_options"] = current_state_dict[
            "closed_filter"
        ]["closed_filter"]["selected_options"]

        if closed_dropdown["element"]["initial_options"] == []:
            closed_dropdown["element"].pop("initial_options")

    block_list.append(closed_dropdown)

    return block_list
