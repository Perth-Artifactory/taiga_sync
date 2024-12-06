import logging
import platform
import re
import subprocess
import time
from copy import deepcopy as copy
from pprint import pprint
from datetime import datetime, timedelta

import taiga

from editable_resources import strings
from util import blocks, slack_formatters, taigalink, tidyhq

# Set up logging
logger = logging.getLogger("slack_home")


def push_home(
    user_id: str, config: dict, tidyhq_cache: dict, taiga_auth_token: str, slack_app
):
    """Push the app home view to a specified user."""
    # Generate the app home view
    block_list = generate_app_home(
        user_id=user_id,
        config=config,
        tidyhq_cache=tidyhq_cache,
        taiga_auth_token=taiga_auth_token,
    )

    try:
        slack_app.client.views_publish(
            user_id=user_id,
            view={
                "type": "home",
                "blocks": block_list,
            },
        )
        logger.info(f"Set app home for {user_id} ")
        return True
    except Exception as e:
        logger.error(f"Failed to push home view: {e}")
        return False


def generate_app_home(
    user_id: str,
    config: dict,
    tidyhq_cache: dict,
    taiga_auth_token: str,
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

    block_list = []
    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text=strings.header
    )

    # Add a "submit form" button
    block_list = slack_formatters.add_block(block_list, blocks.actions)
    block_list[-1].pop("block_id")
    block_list[-1]["elements"].append(copy(blocks.button))
    block_list[-1]["elements"][-1]["text"]["text"] = "Submit a form"
    block_list[-1]["elements"][-1]["action_id"] = "submit_form"

    if not taiga_id:
        logger.info(f"User {user_id} does not have a Taiga account")
        taiga_id = config["taiga"]["guest_user"]

        # Construct blocks
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.unrecognised
        )
        block_list = slack_formatters.add_block(block_list, blocks.divider)
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.do_instead
        )

    else:
        # We recognise the user
        logger.info(f"User {user_id} has a Taiga account - {taiga_id}")

        # Add create button
        block_list[-1]["elements"].append(copy(blocks.button))
        block_list[-1]["elements"][-1]["text"]["text"] = "Create an item"
        block_list[-1]["elements"][-1]["action_id"] = "create_item"

        # Construct blocks
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.explainer
        )
        block_list = slack_formatters.add_block(block_list, blocks.divider)

    # High frequency users will end up going over the 100 block limit
    at_block_limit = False
    compressed_blocks = False
    items_added = 0

    ##########
    # Stories
    ##########

    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text="Assigned Cards"
    )

    if provided_user_stories:
        user_stories = provided_user_stories
    else:
        # Get all assigned user stories for the user
        user_stories = taigalink.get_stories(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

    if len(user_stories) == 0:
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.no_stories
        )
        block_list = slack_formatters.add_block(block_list, blocks.divider)

    else:
        # Sort the user stories by project
        sorted_stories = taigalink.sort_by_project(user_stories)

        for project in sorted_stories:
            if len(block_list) > 95:
                block_list = slack_formatters.compress_blocks(block_list=block_list)
                compressed_blocks = True
                if len(block_list) > 95:
                    # Check if we're already in a compressed recursion
                    if not compress:
                        return generate_app_home(
                            user_id=user_id,
                            config=config,
                            tidyhq_cache=tidyhq_cache,
                            taiga_auth_token=taiga_auth_token,
                            provided_user_stories=user_stories,
                            compress=True,
                        )
                    at_block_limit = True
                    break
            items_added += 1
            header, body, story_blocks = slack_formatters.format_stories(
                story_list=sorted_stories[project], compressed=compress
            )
            if not compress:
                block_list = slack_formatters.add_block(block_list, blocks.text)
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
            block_list += story_blocks
            block_list = slack_formatters.add_block(block_list, blocks.divider)

        # Remove the last divider
        block_list.pop()

    block_list = slack_formatters.add_block(block_list, blocks.divider)

    ##########
    # Issues
    ##########

    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text="Assigned Issues"
    )

    if provided_issues:
        user_issues = provided_issues
    else:
        # Get all assigned issues for the user
        user_issues = taigalink.get_issues(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

    if len(user_issues) == 0:
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.no_issues
        )
        block_list = slack_formatters.add_block(block_list, blocks.divider)

    else:
        # Sort the user issues by project
        sorted_issues = taigalink.sort_by_project(user_issues)

        for project in sorted_issues:
            if len(block_list) > 95:
                block_list = slack_formatters.compress_blocks(block_list=block_list)
                compressed_blocks = True
                if len(block_list) > 95:
                    # Check if we're already in a compressed recursion
                    if not compress:
                        return generate_app_home(
                            user_id=user_id,
                            config=config,
                            tidyhq_cache=tidyhq_cache,
                            taiga_auth_token=taiga_auth_token,
                            provided_user_stories=user_stories,
                            provided_issues=user_issues,
                            compress=True,
                        )
                    at_block_limit = True
                    break
            items_added += 1
            header, body, issue_blocks = slack_formatters.format_issues(
                issue_list=sorted_issues[project], compressed=compress
            )
            if not compress:
                block_list = slack_formatters.add_block(block_list, blocks.text)
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
            block_list += issue_blocks
            block_list = slack_formatters.add_block(block_list, blocks.divider)

        # Remove the last divider
        block_list.pop()

    block_list = slack_formatters.add_block(block_list, blocks.divider)

    ##########
    # Tasks
    ##########

    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text="Assigned Tasks"
    )

    if provided_tasks:
        tasks = provided_tasks
    else:
        # Get all tasks for the user
        tasks = taigalink.get_tasks(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

    if len(tasks) == 0:
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.no_tasks
        )

    else:

        # Sort the tasks based on user story
        sorted_tasks = taigalink.sort_tasks_by_user_story(tasks)

        # Things will start to break down if there are too many tasks
        for project in sorted_tasks:
            if len(block_list) > 95:
                block_list = slack_formatters.compress_blocks(block_list=block_list)
                compressed_blocks = True
                if len(block_list) > 95:
                    # Check if we're already in a compressed recursion
                    if not compress:
                        return generate_app_home(
                            user_id=user_id,
                            config=config,
                            tidyhq_cache=tidyhq_cache,
                            taiga_auth_token=taiga_auth_token,
                            provided_user_stories=user_stories,
                            provided_issues=user_issues,
                            provided_tasks=tasks,
                            compress=True,
                        )
                    at_block_limit = True
                    break
            items_added += 1
            header, body, task_blocks = slack_formatters.format_tasks(
                task_list=sorted_tasks[project], compressed=compress
            )

            # Skip over tasks assigned in template cards
            if "template" in header.lower():
                continue

            if not compress:
                block_list = slack_formatters.add_block(block_list, blocks.text)
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
            block_list += task_blocks
            block_list = slack_formatters.add_block(block_list, blocks.divider)

        # Remove the last divider
        block_list.pop()

    if at_block_limit:
        block_list = slack_formatters.add_block(block_list, blocks.divider)
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
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

    block_list = slack_formatters.add_block(block_list, blocks.context)
    block_list = slack_formatters.inject_text(
        block_list=block_list,
        text=strings.footer.format(
            branch=branch_name, commit=commit_hash, platform=platform_name
        ),
    )

    return block_list


#####
# Action functions
#####


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
    else:
        raise ValueError(f"Unknown item type {item_type}")

    # Check if the item has an actual description
    if not item.description:
        item.description = "<No description provided>"

    # Convert normal description markdown to slack markdown
    item.description = slack_formatters.convert_markdown(item.description)

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
        block_list = slack_formatters.add_block(block_list, blocks.context)
        block_list = slack_formatters.inject_text(
            block_list=block_list,
            text=strings.view_only,
        )

    # Add the item title
    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text=f"{item_type.capitalize()}: {item.subject}"
    )

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
                    "text": f"The {len(comments)} comment{'s' if len(comments)> 1 else ''} on this issue will be lost on promotion. Are you sure?",
                },
                "confirm": {"type": "plain_text", "text": "Promote"},
                "deny": {"type": "plain_text", "text": "Cancel"},
            }

        block_list = slack_formatters.add_block(block_list, blocks.actions)
        block_list[-1]["elements"].append(button)
        block_list[-1].pop("block_id")

    # Add context of who created the item
    block_list = slack_formatters.add_block(block_list, blocks.context)
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

    # Parent card if task
    if item_type == "task":
        block_list = slack_formatters.add_block(block_list, blocks.divider)
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
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
        block_list = slack_formatters.add_block(block_list, blocks.divider)

    block_list = slack_formatters.add_block(block_list, blocks.text)
    block_list = slack_formatters.inject_text(
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

    block_list[-1]["fields"].append(
        {
            "type": "mrkdwn",
            "text": f"*Creator:* {item.owner_extra_info['full_name_display']}",
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
        )
        if tasks:
            block_list = slack_formatters.add_block(block_list, blocks.divider)
            block_list = slack_formatters.add_block(block_list, blocks.header)
            block_list = slack_formatters.inject_text(
                block_list=block_list, text="Attached Tasks"
            )

            closed = 0

            for task in tasks:

                if task["is_closed"]:
                    closed += 1
                else:
                    task_str = (
                        f"• {task['subject']} ({task['status_extra_info']['name']})"
                    )
                    block_list = slack_formatters.add_block(block_list, blocks.text)
                    block_list = slack_formatters.inject_text(
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
                block_list = slack_formatters.add_block(block_list, blocks.text)
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text="<No open tasks>"
                )

            # Add a button to view all tasks
            block_list = slack_formatters.add_block(block_list, blocks.actions)
            block_list[-1].pop("block_id")
            button = copy(blocks.button)
            button["text"]["text"] = f"View all tasks ({closed}/{len(tasks)})"
            button["action_id"] = f"view_tasks-{item_id}"
            block_list[-1]["elements"].append(button)

    # Files
    block_list = slack_formatters.add_block(block_list, blocks.divider)
    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(block_list=block_list, text="Files")

    if len(item.list_attachments()) == 0:
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
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
            block_list = slack_formatters.add_block(block_list, blocks.text)
            block_list = slack_formatters.inject_text(
                block_list=block_list,
                text=f"• <{attachment.url}|{attachment.description}>",
            )
        else:
            block_list = slack_formatters.add_block(block_list, blocks.text)
            block_list = slack_formatters.inject_text(
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
        block_list = slack_formatters.add_block(block_list, blocks.actions)
        block_list[-1]["elements"] = buttons
        block_list[-1].pop("block_id")

    # Comments
    block_list = slack_formatters.add_block(block_list, blocks.divider)
    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(block_list=block_list, text="Comments")
    for comment in comments:
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=f"{comment['comment']}"
        )
        block_list = slack_formatters.add_block(block_list, blocks.context)
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
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text="<No comments yet>"
        )
        block_list = slack_formatters.add_block(block_list, blocks.divider)

    # Add a comment input field
    block_list = slack_formatters.add_block(block_list, blocks.text_question)

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
    block_list = slack_formatters.add_block(block_list, blocks.actions)
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
        description = ""
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
    block_list = slack_formatters.add_block(block_list, blocks.text_question)
    block_list[-1]["label"]["text"] = "Title"
    block_list[-1]["element"]["initial_value"] = subject
    block_list[-1]["element"]["action_id"] = "subject"
    block_list[-1]["block_id"] = "subject"
    block_list[-1]["element"].pop("placeholder")

    # Status
    block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
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

    if item_type == "issue":
        # Type
        block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
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
        block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
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
        block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
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
    block_list = slack_formatters.add_block(block_list, blocks.text_question)
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
    block_list = slack_formatters.add_block(block_list, blocks.base_input)
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
    block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
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
    block_list = slack_formatters.add_block(block_list, blocks.multi_static_dropdown)
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
    """Generate the blocks for a modal to select the type of item to create and on what project"""

    # Get the user's projects
    user_projects = taiga_cache["users"][taiga_id]["projects"]

    # Set up blocks
    block_list = []

    # Project selector
    block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
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
    block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
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
