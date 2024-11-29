import logging
import platform
import re
import subprocess
import time
from copy import deepcopy as copy
from pprint import pprint
from datetime import datetime

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
    user_id: str, config: dict, tidyhq_cache: dict, taiga_auth_token: str
) -> list:
    """Generate the blocks for the app home view for a specified user and return it as a list of blocks."""
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
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.header
        )
        block_list += blocks.text  # type: ignore
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.unrecognised
        )
        block_list += blocks.divider
        block_list += blocks.text
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.do_instead
        )
        block_list += blocks.context
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.footer
        )

    else:
        logger.info(f"User {user_id} has a Taiga account. - {taiga_id}")
        # We recognise the user

        # Construct blocks
        block_list = []
        block_list += blocks.header
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.header
        )
        block_list += blocks.text
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=strings.explainer
        )
        block_list += blocks.divider

        ##########
        # Stories
        ##########

        block_list += blocks.header
        block_list = slack_formatters.inject_text(
            block_list=block_list, text="Assigned Cards"
        )

        # Get all assigned user stories for the user
        user_stories = taigalink.get_stories(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

        if len(user_stories) == 0:
            block_list += blocks.text
            block_list = slack_formatters.inject_text(
                block_list=block_list, text=strings.no_stories
            )
            block_list += blocks.divider

        else:
            # Sort the user stories by project
            sorted_stories = taigalink.sort_by_project(user_stories)

            for project in sorted_stories:
                header, body, story_blocks = slack_formatters.format_stories(
                    sorted_stories[project]
                )
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
                # block_list += blocks.text
                # block_list = inject_text(block_list=block_list, text=body)
                block_list += story_blocks
                block_list = slack_formatters.add_block(block_list, blocks.divider)

            # Remove the last divider
            block_list.pop()

        block_list += blocks.divider

        ##########
        # Issues
        ##########

        block_list += blocks.header
        block_list = slack_formatters.inject_text(
            block_list=block_list, text="Assigned Issues"
        )

        # Get all assigned issues for the user
        user_issues = taigalink.get_issues(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

        if len(user_issues) == 0:
            block_list += blocks.text
            block_list = slack_formatters.inject_text(
                block_list=block_list, text=strings.no_issues
            )
            block_list += blocks.divider

        else:
            # Sort the user issues by project
            sorted_issues = taigalink.sort_by_project(user_issues)

            for project in sorted_issues:
                header, body, issue_blocks = slack_formatters.format_issues(
                    sorted_issues[project]
                )
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
                block_list += issue_blocks
                block_list = slack_formatters.add_block(block_list, blocks.divider)

            # Remove the last divider
            block_list.pop()

        block_list += blocks.divider

        ##########
        # Tasks
        ##########

        block_list += blocks.header
        block_list = slack_formatters.inject_text(
            block_list=block_list, text="Assigned Tasks"
        )

        # Get all tasks for the user
        tasks = taigalink.get_tasks(
            taiga_id=taiga_id,
            config=config,
            taiga_auth_token=taiga_auth_token,
            exclude_done=True,
        )

        if len(tasks) == 0:
            block_list += blocks.text
            block_list = slack_formatters.inject_text(
                block_list=block_list, text=strings.no_tasks
            )

        else:

            # Sort the tasks based on user story
            sorted_tasks = taigalink.sort_tasks_by_user_story(tasks)

            # Things will start to break down if there are too many tasks
            displayed_tasks = 0
            trimmed = True
            for project in sorted_tasks:
                if displayed_tasks >= 50:
                    break
                header, body, task_blocks = slack_formatters.format_tasks(
                    sorted_tasks[project]
                )

                # Skip over tasks assigned in template cards
                if "template" in header.lower():
                    continue

                displayed_tasks += 1
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=f"*{header}*"
                )
                # block_list += blocks.text
                # block_list = inject_text(block_list=block_list, text=body)
                block_list += task_blocks
                block_list = slack_formatters.add_block(block_list, blocks.divider)

            else:
                trimmed = False

            # Remove the last divider
            block_list.pop()

            if trimmed:
                block_list += blocks.divider
                block_list += blocks.text
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=strings.trimmed
                )

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
    taigacon: taiga.client.TaigaAPI, project_id: int | str, item_id, item_type
):
    """Generate the blocks for a modal for viewing and editing an item"""

    if item_type == "issue":
        item = taigacon.issues.get(resource_id=item_id)
        history: list = taigacon.history.issue.get(resource_id=item_id)
    elif item_type == "task":
        item = taigacon.tasks.get(resource_id=item_id)
        history: list = taigacon.history.task.get(resource_id=item_id)
    elif item_type == "story":
        item = taigacon.user_stories.get(resource_id=item_id)
        history: list = taigacon.history.user_story.get(resource_id=item_id)

    # Check if the item has an actual description
    if not item.description:
        item.description = "<No description provided>"

    # Build up a history of comments
    comments = []
    for event in history:
        if event["comment"]:

            # Skip deleted comments
            if event["delete_comment_user"]:
                continue

            name = event["user"]["name"]
            comment = event["comment"]

            # When we post we add a byline
            if event["comment"].startswith("Posted from Slack"):
                match = re.match(r"Posted from Slack by (.*?): (.*)", event["comment"])
                if match:
                    name = match.group(1)
                    comment = match.group(2)

            comments.append({"author": name, "comment": comment})

    # We want to show the most recent comments last and the history list is in reverse order
    comments.reverse()

    # Construct the blocks
    block_list = []

    # Add the item title
    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text=f"{item_type.capitalize()}: {item.subject}"
    )

    block_list = slack_formatters.add_block(block_list, blocks.text)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text=f"{item.description}"
    )

    # Info fields
    block_list[-1]["fields"] = []

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
            watcher_info = taigacon.users.get(watcher)
            watcher_strs.append(watcher_info.full_name_display)
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

    # Attach info field edit button
    button = copy(blocks.button)
    button["text"]["text"] = "Edit"
    button["action_id"] = f"edit_info"
    block_list[-1]["accessory"] = button

    # Files
    block_list = slack_formatters.add_block(block_list, blocks.divider)
    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(block_list=block_list, text="Files")

    if len(item.list_attachments()) == 0:
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text="<No files attached>"
        )

    for attachment in item.list_attachments():
        if attachment.is_deprecated:
            continue

        filetype = attachment.attached_file.split(".")[-1]

        # Display images with image blocks directly
        if filetype in ["png", "jpg", "jpeg", "gif"]:
            block_list = slack_formatters.add_block(block_list, blocks.image)
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

        # Display other files as links using the description as the text if possible
        else:
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

    # Create upload field
    block_list = slack_formatters.add_block(block_list, blocks.file_input)
    block_list[-1]["block_id"] = "upload_section"
    block_list[-1]["element"]["action_id"] = "upload_file"
    block_list[-1]["label"]["text"] = "Upload files"

    # Comments
    block_list = slack_formatters.add_block(block_list, blocks.divider)
    block_list = slack_formatters.add_block(block_list, blocks.header)
    block_list = slack_formatters.inject_text(block_list=block_list, text="Comments")
    current_commenter = ""
    for comment in comments:
        if comment["author"] != current_commenter:
            block_list = slack_formatters.add_block(block_list, blocks.text)
            block_list = slack_formatters.inject_text(
                block_list=block_list, text=f"*{comment['author']}*"
            )
            current_commenter = comment["author"]
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=f"{comment['comment']}"
        )
        block_list = slack_formatters.add_block(block_list, blocks.divider)

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

    # Add some junk to the end of the action_id so the input isn't preserved in future modals
    block_list[-1]["element"]["action_id"] = f"add_comment{time.time()}"

    # Add a comment button
    # We use this instead of the submit button because we can't push the modal view to the user after a submission event
    block_list = slack_formatters.add_block(block_list, blocks.actions)
    block_list[-1]["elements"].append(copy(blocks.button))
    block_list[-1]["elements"][0]["text"]["text"] = "Comment"
    block_list[-1]["elements"][0]["action_id"] = "submit_comment"

    return block_list
