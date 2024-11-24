from copy import deepcopy as copy
from pprint import pprint
from datetime import datetime
import logging

from util import blocks, tidyhq, strings, taigalink

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
    task_strs = []
    for task in task_list:
        url = f"https://tasks.artifactory.org.au/project/{task['project_extra_info']['slug']}/task/{task['ref']}"
        task_strs.append(
            f"• <{url}|{task['subject']}> ({task['status_extra_info']['name']})"
        )
    out_str = "\n".join(task_strs)

    return user_story_str, out_str


def format_stories(story_list):
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
            sorted_stories = taigalink.sort_stories_by_project(user_stories)

            for project in sorted_stories:
                header, body = format_stories(sorted_stories[project])
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=f"*{header}*")
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=body)

        block_list += blocks.divider

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
                header, body = format_tasks(sorted_tasks[project])

                # Skip over tasks assigned in template cards
                if "template" in header.lower():
                    continue

                displayed_tasks += 1
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=f"*{header}*")
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=body)
            else:
                trimmed = False

            if trimmed:
                block_list += blocks.divider
                block_list += blocks.text
                block_list = inject_text(block_list=block_list, text=strings.trimmed)
        block_list += blocks.context
        block_list = inject_text(block_list=block_list, text=strings.footer)

    return block_list
