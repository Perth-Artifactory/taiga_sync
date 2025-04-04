import logging
import re
from copy import deepcopy as copy
from pprint import pformat
from typing import Literal

import requests
import taiga

from slack import misc as slack_misc
from util import tidyhq

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def get_custom_fields_for_story(
    story_id: str, taiga_auth_token: str, config: dict
) -> tuple[dict, int]:
    """Retrieve all custom fields for a specific story.

    Returns a tuple of the custom fields and the version of the story object. The version object is used when updating the story object.
    """
    custom_attributes_url = f"{config['taiga']['url']}/api/v1/userstories/custom-attributes-values/{story_id}"
    response = requests.get(
        custom_attributes_url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
    )

    if response.status_code == 200:
        custom_attributes: dict = response.json().get("attributes_values", {})
        version: int = response.json().get("version", 0)
        logger.debug(
            f"Fetched custom attributes for story {story_id}: {custom_attributes}"
        )
    else:
        logger.error(
            f"Failed to fetch custom attributes for story {story_id}: {response.status_code}"
        )

    return custom_attributes, version


def get_tidyhq_id(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    """Retrieve the TidyHQ ID for a specific story if set."""
    custom_attributes, _ = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("1", None)


def get_email(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    """Retrieve the email for a specific story if set."""
    custom_attributes, _ = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("2", None)


def get_tidyhq_url(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    """Retrieve the TidyHQ URL for a specific story if set."""
    custom_attributes, _ = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("3", None)


def get_member_type(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    """Retrieve the member type for a specific story if set."""
    custom_attributes, _ = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("4", None)


def update_task(
    task_id: str, status: int, taiga_auth_token: str, config: dict, version: int
) -> bool:
    """Update the status of a task."""
    task_url = f"{config['taiga']['url']}/api/v1/tasks/{task_id}"
    response = requests.patch(
        task_url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        json={
            "status": status,
            "version": version,
        },
    )

    if response.status_code == 200:
        return True

    else:
        logger.error(
            f"Failed to update task {task_id} with status {status}: {response.status_code}"
        )
        logger.error(response.json())
        return False


def progress_story(
    story_id: str,
    taigacon: taiga.TaigaAPI,
    taiga_auth_token: str,
    config: dict,
    story_statuses: dict,
) -> bool:
    """Increment the story status by 1. Does not check for the existence of a next status."""
    # Get the current status of the story
    story = taigacon.user_stories.get(story_id)
    current_status = int(story.status)

    # Get the order of the current status
    current_order = id_to_order(story_statuses, current_status)

    # Check if we're at the end of the statuses
    if current_order == len(story_statuses) - 1:
        logger.debug(f"Story {story_id} is already at the end of the statuses")
        return False

    # Increment the order by one
    new_order = current_order + 1

    # Get the ID of the new status
    new_status = order_to_id(story_statuses, new_order)

    if not new_status:
        logger.error(f"Failed to find a status with order {new_order}")
        return False

    update_url = f"{config['taiga']['url']}/api/v1/userstories/{story_id}"
    response = requests.patch(
        update_url,
        headers={
            "Authorization": f"Bearer {taiga_auth_token}",
            "Content-Type": "application/json",
        },
        json={"status": new_status, "version": story.version},
    )

    if response.status_code == 200:
        logger.debug(f"User story {story_id} status updated to {new_status + 1}")
        return True
    else:
        logger.error(
            f"Failed to update user story {story_id} status: {response.status_code}"
        )
        logger.error(response.json())
        return False


def set_custom_field(
    config: dict, taiga_auth_token: str, story_id: int, field_id: int, value: str
) -> bool:
    """Set a custom field for a specific story."""

    # Fetch custom fields of the story
    custom_attributes_url = f"{config['taiga']['url']}/api/v1/userstories/custom-attributes-values/{story_id}"
    response = requests.get(
        custom_attributes_url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
    )

    if response.status_code == 200:
        custom_attributes = response.json().get("attributes_values", {})
        version = response.json().get("version", 0)
        logger.debug(
            f"Fetched custom attributes for story {story_id}: {custom_attributes}"
        )
    else:
        logger.error(
            f"Failed to fetch custom attributes for story {story_id}: {response.status_code}"
        )
        return False

    # Update the custom field
    custom_attributes[field_id] = value
    custom_attributes_url = f"{config['taiga']['url']}/api/v1/userstories/custom-attributes-values/{story_id}"

    response = requests.patch(
        custom_attributes_url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        json={
            "attributes_values": custom_attributes,
            "version": version,
        },
    )

    if response.status_code == 200:
        logger.info(
            f"Updated story {story_id} with custom attribute {field_id}: {value}"
        )
        return True

    else:
        logger.error(
            f"Failed to update story {story_id} with custom attribute {field_id}: {value}: {response.status_code}"
        )
        logger.error(response.json())

    return False


def base_create_issue(
    taiga_auth_token: str,
    project_id: str | int,
    config: dict,
    subject: str,
    description: str | None = None,
    type_id: str | int | None = None,
    priority_id: str | int | None = None,
    severity_id: str | int | None = None,
    tags: list = [],
) -> dict | Literal[False]:
    """Create an issue on a Taiga project. Does no mapping and supports IDs only

    Fields that accept None can still be passed None (unlike the API directly)"""

    data = {
        "project": project_id,
        "subject": subject,
        "tags": tags + ["slack"],
    }
    if description:
        data["description"] = description
    if type_id:
        data["type"] = type_id
    if priority_id:
        data["priority"] = priority_id
    if severity_id:
        data["severity"] = severity_id

    create_url = f"{config['taiga']['url']}/api/v1/issues"
    response = requests.post(
        create_url,
        headers={
            "Authorization": f"Bearer {taiga_auth_token}",
        },
        json=data,
    )
    if response.status_code == 201:
        logger.info(f"Created issue {response.json()['id']} on project {project_id}")
        return response.json()
    else:
        logger.error(
            f"Failed to create issue on project {project_id}: {response.status_code}"
        )
        logger.error(response.json())
        return False


def create_slack_issue(
    board: str,
    description: str,
    subject: str,
    by_slack: dict,
    project_ids: dict,
    taiga_auth_token: str,
    config: dict,
):
    # Construct the by line. by_slack is a slack user object
    # The by-line should be a deep slack link to the user
    name_str = by_slack["user"]["profile"].get(
        "real_name", by_slack["user"]["profile"]["display_name"]
    )
    slack_id = by_slack["user"]["id"]
    by = f"{name_str} ({slack_id})"

    description = f"{description}\n\nAdded to Taiga by: {by}"
    project_id = project_ids.get(board)
    if not project_id:
        logger.error(f"Project ID not found for board {board}")
        return False

    issue = base_create_issue(
        taiga_auth_token=taiga_auth_token,
        project_id=project_id,
        subject=subject,
        description=description,
        config=config,
    )

    if not issue:
        logger.error(f"Failed to create issue on board {board}")
        return False

    issue_info = issue

    return issue_info


def create_item(
    config: dict,
    taiga_auth_token: str,
    project_id: int,
    item_type: str,
    subject: str,
    assigned_to: int | None = None,
    description: str | None = None,
    due_date: str | None = None,
    status: int | None = None,
    tags: list[str] | None = None,
    watchers: list[int] | None = None,
    type: int | None = None,
    priority: int | None = None,
    severity: int | None = None,
    user_story: int | None = None,
) -> tuple[str, str] | tuple[Literal[False], None]:
    """Create an item on a Taiga project.

    Returns the item ID and version if successful."""

    type_map = {
        "story": "userstories",
        "issue": "issues",
        "task": "tasks",
    }

    if item_type not in type_map:
        raise ValueError(
            f"Item type {item_type} not supported must be one of: {type_map.keys()}"
        )

    data = {
        "project": project_id,
        "subject": subject,
    }
    if description:
        data["description"] = description
    if assigned_to:
        data["assigned_to"] = assigned_to
    if tags:
        data["tags"] = tags
    if type:
        data["type"] = type
    if priority:
        data["priority"] = priority
    if severity:
        data["severity"] = severity
    if status:
        data["status"] = status
    if user_story:
        data["user_story"] = user_story

    create_url = f"{config['taiga']['url']}/api/v1/{type_map[item_type]}"
    response = requests.post(
        create_url,
        headers={
            "Authorization": f"Bearer {taiga_auth_token}",
        },
        json=data,
    )
    if response.status_code == 201:
        logger.info(
            f"Created {item_type} {response.json()['id']} on project {project_id}"
        )
        story_id = response.json()["id"]

        version = response.json()["version"]

        # Add watchers if provided
        if watchers:
            current_watchers = []
            for watcher in watchers:
                added_watcher = watch(
                    type_str=item_type,
                    item_id=story_id,
                    watchers=current_watchers,
                    taiga_id=watcher,
                    taiga_auth_token=taiga_auth_token,
                    config=config,
                    version=version,
                )
                if added_watcher:
                    current_watchers.append(watcher)
                    version += 1

        # Add due date if provided
        if due_date:
            response = requests.patch(
                create_url,
                headers={"Authorization": f"Bearer {taiga_auth_token}"},
                json={"due_date": due_date, "version": version},
            )
            if response.status_code == 200:
                logger.info(f"Added due date to {item_type} {story_id}")
                version = response.json()["version"]

        return story_id, version

    else:
        logger.error(
            f"Failed to create {item_type} on project {project_id}: {response.status_code}"
        )
        logger.error(response.json())
        return False, None


def item_mapper(
    item: str | None,
    field_type: str,
    project_id: str | int | None,
    taiga_auth_token: str,
    config: dict,
    taigacon,
) -> int:
    """Map an item to a Taiga ID."""
    if not item:
        return False
    # Construct the url
    if field_type == "severity":
        url = f"{config['taiga']['url']}/api/v1/severities?project={project_id}"
    elif field_type == "priority":
        url = f"{config['taiga']['url']}/api/v1/priorities?project={project_id}"
    elif field_type == "type":
        url = f"{config['taiga']['url']}/api/v1/issue-types?project={project_id}"
    elif field_type == "status":
        url = f"{config['taiga']['url']}/api/v1/statuses?project={project_id}"
    elif field_type in ["board", "project"]:
        # Map project names to IDs
        projects = taigacon.projects.list()
        project_ids: dict[str, int] = {
            project.name.lower(): project.id for project in projects
        }

        # Duplicate similar board names for QoL
        project_ids["infra"] = project_ids["infrastructure"]
        project_ids["laser"] = project_ids["lasers"]
        project_ids["printer"] = project_ids["3d"]
        project_ids["printers"] = project_ids["3d"]

        project_id = project_ids.get(item.lower(), None)  # type: ignore
        if not project_id:
            logger.error(f"Project ID for {item} not found")
            return False
        return int(project_id)

    # Fetch the items
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
    )

    if response.status_code != 200:
        logger.error(f"Failed to fetch {field_type}: {response.status_code}")
        logger.error(pformat(response.json()))
        logger.error(response.request.url)
        return False

    objects = response.json()

    logger.debug(f"Fetched objects: {objects}")
    logger.debug(f"Looking for item: {item}")

    for object in objects:
        try:
            if object["name"].lower() == item.lower():
                return object["id"]
        except TypeError:
            print(object)

    return False


def map_slack_names_to_taiga_usernames(input_string: str, taiga_users: dict) -> str:
    """Takes a string and maps applicable Slack names to Taiga usernames."""
    for display_name in taiga_users:
        if display_name.strip() != "":
            input_string = input_string.replace(
                display_name, f"@{taiga_users[display_name].username}"
            )
    return input_string


def create_link_to_entry(
    config,
    entry_ref: int,
    project_id: int | None = None,
    project_str: str | None = None,
    entry_type: str = "story",
):
    """Create a link to the Taiga entry for the project."""
    if project_str is None and project_id:
        # Fetch the project name
        # TODO retrieve the project name from the ID.
        # Fortunately the only time this function is used is in a situation where we've derived the project ID from the project name
        logger.error(
            "Project name not provided and this function is not yet capable of retrieving it from the ID"
        )
    # Remap entry_type to the versions used in URLs
    entry_map = {"story": "us", "userstory": "us", "issue": "issue", "task": "task"}

    if entry_type not in entry_map:
        logger.error(f"Entry type {entry_type} not supported")
        return False

    return f"{config['taiga']['url']}/project/{project_str}/{entry_map[entry_type]}/{entry_ref}"


def order_to_id(story_statuses: dict, order: int) -> int:
    """Takes the position of a story status column and returns the ID of the status."""

    # Iterate over statuses and return the ID of the status with the matching order
    for status in story_statuses:
        if story_statuses[status]["order"] == order:
            return status
    logger.error(f"Status with order {order} not found")
    return False


def id_to_order(story_statuses: dict, status_id: int) -> int:
    """Takes the ID of a story status column and returns the position of the column."""

    if status_id not in story_statuses:
        logger.error(f"Status with ID {status_id} not found")
        return False

    return story_statuses[status_id]["order"]


def get_tasks(
    config: dict,
    taiga_auth_token: str,
    filters: dict,
    exclude_done: bool = False,
    taiga_id: int | str | None = None,
    story_id: int | str | None = None,
    taiga_cache: dict = {},
) -> list[dict]:
    """Get tasks assigned to a user or story.

    User will take precedence over story if both are provided.
    Pass exclude_done=True to exclude tasks that have a closed status.
    Pass filters to filter tasks by project, status, etc.
    """

    params = {}

    if taiga_id:
        params["assigned_to"] = taiga_id
    elif story_id:
        params["user_story"] = story_id

    if exclude_done:
        params["status__is_closed"] = False

    # Check for filters
    projects = ["all"]
    related = ["all"]
    if filters:
        if filters.get("type_filter", ["junk"]) == []:
            filters.pop("type_filter")
        # Check if tasks are an allowed type (defaults to yes if filter category not present)
        if "task" not in filters.get("type_filter", ["task"]):
            return []
        if filters.get("project_filter"):
            projects = filters["project_filter"]
            if "all" in projects:
                if not taiga_cache or not taiga_id:
                    raise ValueError(
                        "Project filter 'all' requires a taiga cache and taiga ID"
                    )
                projects = taiga_cache["users"][int(taiga_id)]["projects"]

        if filters.get("status_filter", []) == ["closed"]:
            params["status__is_closed"] = True
        elif filters.get("status_filter", []) == ["open"]:
            params["status__is_closed"] = False
        elif filters.get("status_filter", ["junk"]) == []:
            if "status__is_closed" in params:
                del params["status__is_closed"]

        if filters.get("related_filter"):
            related = filters["related_filter"]
            if related == []:
                related = ["all"]

    url = f"{config['taiga']['url']}/api/v1/tasks"
    tasks = []
    for project_id in projects:
        for relation in related:
            current_params = copy(params)
            if project_id != "all":
                current_params["project"] = int(project_id)
            if relation == "watched":
                current_params["watchers"] = taiga_id
            elif relation == "assigned":
                current_params["assigned_to"] = taiga_id
            elif relation == "all":
                if "assigned_to" in current_params:
                    del current_params["assigned_to"]
            response = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {taiga_auth_token}",
                    "x-disable-pagination": "True",
                },
                params=current_params,
            )
            for task in response.json():
                if task not in tasks:
                    tasks.append(task)

    return tasks


def get_stories(
    taiga_id: int,
    config: dict,
    taiga_auth_token: str,
    filters: dict,
    exclude_done: bool = False,
    taiga_cache: dict = {},
) -> list[dict]:
    """Get stories assigned to a user (default)

    Pass filters to filter stories by project, status, etc.
    """

    params = {}

    if taiga_id:
        params["assigned_to"] = taiga_id

    if exclude_done:
        params["status__is_closed"] = False

    # Check for filters
    projects = ["all"]
    related = ["all"]
    if filters:
        if filters.get("type_filter", ["junk"]) == []:
            filters.pop("type_filter")
        # Check if tasks are an allowed type (defaults to yes if filter category not present)
        if "story" not in filters.get("type_filter", ["story"]):
            return []
        if filters.get("project_filter"):
            projects = filters["project_filter"]
            if "all" in projects:
                if not taiga_cache or not taiga_id:
                    raise ValueError(
                        "Project filter 'all' requires a taiga cache and taiga ID"
                    )
                projects = taiga_cache["users"][taiga_id]["projects"]

        if filters.get("status_filter", []) == ["closed"]:
            params["status__is_closed"] = True
        elif filters.get("status_filter", []) == ["open"]:
            params["status__is_closed"] = False
        elif filters.get("status_filter", ["junk"]) == []:
            if "status__is_closed" in params:
                del params["status__is_closed"]

        if filters.get("related_filter"):
            related = filters["related_filter"]
            if related == []:
                related = ["all"]

    url = f"{config['taiga']['url']}/api/v1/userstories"
    stories = []
    for project_id in projects:
        for relation in related:
            current_params = copy(params)
            if project_id != "all":
                current_params["project"] = int(project_id)
            if relation == "watched":
                current_params["watchers"] = taiga_id
            elif relation == "assigned":
                current_params["assigned_to"] = taiga_id
            elif relation == "all":
                if "assigned_to" in current_params:
                    del current_params["assigned_to"]
            response = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {taiga_auth_token}",
                    "x-disable-pagination": "True",
                },
                params=current_params,
            )
            for story in response.json():
                if story not in stories:
                    stories.append(story)

    return stories


def get_issues(
    taiga_id: int,
    config: dict,
    taiga_auth_token: str,
    filters: dict,
    exclude_done: bool = False,
    taiga_cache: dict = {},
) -> list[dict]:
    """Get issues assigned to a user (default)

    Pass filters to filter issues by project, status, etc.
    """

    params = {}

    if taiga_id:
        params["assigned_to"] = taiga_id

    if exclude_done:
        params["status__is_closed"] = False

    # Check for filters
    projects = ["all"]
    related = ["all"]
    if filters:
        if filters.get("type_filter", ["junk"]) == []:
            filters.pop("type_filter")
        # Check if tasks are an allowed type (defaults to yes if filter category not present)
        if "issue" not in filters.get("type_filter", ["issue"]):
            return []
        if filters.get("project_filter"):
            projects = filters["project_filter"]
            if "all" in projects:
                if not taiga_cache or not taiga_id:
                    raise ValueError(
                        "Project filter 'all' requires a taiga cache and taiga ID"
                    )
                projects = taiga_cache["users"][taiga_id]["projects"]

        if filters.get("status_filter", []) == ["closed"]:
            params["status__is_closed"] = True
        elif filters.get("status_filter", []) == ["open"]:
            params["status__is_closed"] = False
        elif filters.get("status_filter", ["junk"]) == []:
            if "status__is_closed" in params:
                del params["status__is_closed"]

        if filters.get("related_filter"):
            related = filters["related_filter"]
            if related == []:
                related = ["all"]

    url = f"{config['taiga']['url']}/api/v1/issues"
    issues = []
    for project_id in projects:
        for relation in related:
            current_params = copy(params)
            if project_id != "all":
                current_params["project"] = int(project_id)
            if relation == "watched":
                current_params["watchers"] = taiga_id
            elif relation == "assigned":
                current_params["assigned_to"] = taiga_id
            response = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {taiga_auth_token}",
                    "x-disable-pagination": "True",
                },
                params=current_params,
            )
            for issue in response.json():
                if issue not in issues:
                    issues.append(issue)

    return issues


def sort_tasks_by_user_story(tasks: list[dict]) -> dict:
    """Index tasks by user story."""
    user_stories = {}
    for task in tasks:
        if task["user_story"] not in user_stories:
            user_stories[task["user_story"]] = []
        user_stories[task["user_story"]].append(task)
    return user_stories


def sort_by_project(items: list) -> dict:
    """Index items by project."""
    projects = {}
    for item in items:
        if item["project"] not in projects:
            projects[item["project"]] = []
        projects[item["project"]].append(item)
    return projects


def parse_webhook_action_into_str(data: dict, tidyhq_cache: dict, config: dict) -> str:
    """Parse the data of a webhook into a human-readable string."""
    action_map = {
        "create": "created",
        "change": "changed",
        "delete": "deleted",
        "comment": "commented",
    }

    type_map = {"userstory": "card", "task": "task", "issue": "issue", "epic": "epic"}

    action = data.get("action", None)

    if not action:
        logger.error(
            "Action not found in webhook data or isn't one of: create, change or delete"
        )

    subject = data["data"]["subject"]
    # Get the Slack ID of the user if it exists

    description = "\n"

    if action == "change":
        if data["change"]["comment"]:
            # If there's a comment we'll create a fake "comment" action that makes the notification read better
            action = "comment"
            comment = data["change"]["comment"]
            if "Posted from Slack" in comment:
                # Trim bylines we add elsewhere
                if ":" in comment:
                    comment = comment.split(":")[1].strip()
            description += f"Comment: {comment}"
        else:
            for diff in data["change"]["diff"]:
                if diff in ["finish_date"]:
                    continue
                # We never care about the order of the item (and it's a different name for each item type)
                if "order" in diff:
                    continue
                elif diff == "is_closed":
                    if data["change"]["diff"][diff]["to"]:
                        description = "\nClosed"
                        # If the item is closed we don't care about other diffs
                        break

                # When the change is from nothing to something we don't need to display the nothing part.
                from_str = f" from: {data['change']['diff'][diff].get('from', '-')} "
                if data["change"]["diff"][diff].get("from") is None:
                    from_str = ""

                description += (
                    f"{diff}{from_str} to: {data['change']['diff'][diff]['to']}\n"
                )

    elif action == "delete":
        # Nothing we need to do here
        pass

    elif action == "create":
        if data["data"]["assigned_to"]:
            assigned_id = data["data"]["assigned_to"]["id"]
            assigned_name = data["data"]["assigned_to"]["full_name"]
            # Get the Slack ID of the assigned user if it exists
            slack_id = tidyhq.map_taiga_to_slack(
                tidyhq_cache=tidyhq_cache, taiga_id=assigned_id, config=config
            )
            if slack_id:
                assigned_name = f"<@{slack_id}>"

            description += f"Assigned to: {assigned_name}\n"

    # We don't get a lot of information from some task subjects so add in the title oof the user story as well
    card_name = ""
    if data["type"] == "task":
        card_name = f" ({data['data']['user_story']['subject']})"

    return f"""{type_map.get(data["type"], "item").capitalize()} {action_map[action]}: {subject}{card_name}{description}"""


def get_info(
    taiga_auth_token: str,
    config: dict,
    story_id: int | None = None,
    task_id: int | None = None,
    issue_id: int | None = None,
    item_type: str | None = None,
    item_id: int | None = None,
) -> dict | Literal[False]:
    """Get the info of a story, task or issue.

    Return the item as a dictionary or False if it fails.
    """

    type_map = {
        "userstory": "userstories",
        "story": "userstories",
        "us": "userstories",
        "issue": "issues",
        "task": "tasks",
    }

    if story_id:
        url = f"{config['taiga']['url']}/api/v1/userstories/{story_id}"
    elif task_id:
        url = f"{config['taiga']['url']}/api/v1/tasks/{task_id}"
    elif issue_id:
        url = f"{config['taiga']['url']}/api/v1/issues/{issue_id}"
    elif item_type and item_id:
        if item_type not in type_map:
            logger.error(f"Type {item_type} not supported")
            return False
        url = f"{config['taiga']['url']}/api/v1/{type_map[item_type]}/{item_id}"

    if not url:
        logger.error("No ID provided")
        return False

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
    )

    if response.status_code == 200:
        return response.json()

    logger.error(
        f"Failed to get info for {story_id} {task_id} {issue_id}: {response.status_code}"
    )
    logger.error(response.json())
    return False


def add_comment(
    type_str: str,
    item_id: int | str,
    comment: str,
    taiga_auth_token: str,
    config: dict,
    version: int | str,
) -> bool:
    """Add a comment to a story, issue or task"""
    type_map = {
        "userstory": "userstories",
        "story": "userstories",
        "us": "userstories",
        "issue": "issues",
        "task": "tasks",
    }
    if type_str not in type_map:
        logger.error(f"Type {type_str} not supported")
        return False

    url = f"{config['taiga']['url']}/api/v1/{type_map[type_str]}/{item_id}"

    response = requests.patch(
        url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        json={"comment": comment, "version": version},
    )
    if response.status_code == 200:
        return True
    else:
        logger.error(
            f"Failed to add comment to {type_str} {item_id}: {response.status_code}"
        )
        return False


def mark_complete(
    config: dict,
    taiga_auth_token: str,
    taiga_cache: dict,
    item_id: int | str | None = None,
    item_type: str | None = None,
    item: dict | None = None,
    status_id: int | str | None = None,
) -> bool:
    """Mark an item as complete.

    Can either pass the item directly or provide the ID and type. If a status ID is provided it will be used instead of the default (first) closing status.
    """

    if not item:
        if not item_id or not item_type:
            logger.error("No item ID or type provided")
            return False
        # Get the current version of the item
        item = get_info(taiga_auth_token, config, item_id=item_id, item_type=item_type)  # type: ignore

    if not item:
        logger.error(f"Failed to get info for {item_type} {item_id}")
        return False

    type_map = {
        "task": "tasks",
        "issue": "issues",
        "userstory": "userstories",
        "story": "userstories",
    }
    if item_type not in type_map:
        logger.error(f"Type {item_type} not supported")
        return False

    url = f"{config['taiga']['url']}/api/v1/{type_map[item_type]}/{item_id}"

    # Figure out what the closing status is
    if not status_id:
        status_id = taiga_cache["boards"][item["project"]]["closing_status"][item_type]

    response = requests.patch(
        url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        json={"status": status_id, "version": item["version"]},
    )
    if response.status_code == 200:
        return True
    else:
        logger.error(
            f"Failed to mark {item_type} {item_id} as complete: {response.status_code}"
        )
        logger.error(response.json())
        return False


def watch(
    type_str: str,
    item_id: int,
    watchers: list,
    taiga_id: int,
    taiga_auth_token: str,
    config: dict,
    version: int,
) -> bool:
    """Add a watcher to a story or issue."""
    type_map = {"userstory": "userstories", "story": "userstories", "issue": "issues"}
    if type_str not in type_map:
        logger.error(f"Type {type_str} not supported")
        return False

    url = f"{config['taiga']['url']}/api/v1/{type_map[type_str]}/{item_id}"

    response = requests.patch(
        url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        json={"watchers": watchers + [taiga_id], "version": version},
    )
    if response.status_code == 200:
        return True
    else:
        logger.error(
            f"Failed to add watcher to {type_str} {item_id}: {response.status_code}"
        )
        return False


def validate_form_options(
    project_id: int, option_type: str, options: list, taiga_cache: dict
) -> bool:
    """Validate that the options provided are valid for the given project and option type."""
    valid_options = []

    if option_type == "severity":
        key = "severities"
    elif option_type == "type":
        key = "types"
    raw_options = taiga_cache["boards"][project_id][key].values()

    valid_options = [item["name"].lower() for item in raw_options]

    for option in options:
        if option.lower() not in valid_options:
            logger.error(f"Invalid option: {option}")
            logger.error(f"Valid options: {valid_options}")
            return False
    return True


def attach_file(
    taiga_auth_token: str,
    config: dict,
    project_id: str | int,
    item_type: str,
    item_id: str | int,
    url: str | None = None,
    file_obj=None,
    filename: str | None = None,
    description: str | None = None,
) -> bool:
    """Attach a file to a Taiga item. If a URL is provided it will be downloaded and attached. File object can be provided directly.

    Supports: issues, tasks, userstories"""

    # Map types to url segments
    url_segments = {"issue": "issues", "task": "tasks", "story": "userstories"}

    if item_type not in url_segments:
        logger.error(f"Item type {item_type} not supported")
        return False

    upload_url = (
        f"{config['taiga']['url']}/api/v1/{url_segments[item_type]}/attachments"
    )

    # Download the file if required
    if not file_obj:
        if not url:
            logger.error("No URL or file object provided")
            return False
        file_obj = slack_misc.download_file(url, config)

    if not file_obj:
        logger.error("Failed to download file")
        return False

    if isinstance(file_obj, str):
        file_obj = open(file_obj, "rb")

    if filename:
        pass
    elif url:
        filename = url.split("/")[-1]
    else:
        filename = "attached_file"

    # Construct the data and add description if provided
    data = {
        "project": project_id,
        "object_id": item_id,
    }
    if description:
        data["description"] = description

    # Upload the file

    upload = requests.post(
        upload_url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        data=data,
        files={"attached_file": (filename, file_obj, "application/octet-stream")},
    )

    if upload.status_code == 201:
        return True
    else:
        logger.error(f"Failed to attach file: {upload.status_code}")
        logger.error(upload_url)
        logger.error(filename)
        logger.error(upload.text)
        return False


def setup_cache(taiga_auth_token: str, config: dict, taigacon) -> dict:
    """Query Taiga for a variety of information that doesn't change often and cache it for later use."""
    cache = {}
    # Users
    boards = {}
    users = {}
    projects = {"by_name": {}, "by_name_with_extra": {}}
    # Get all projects
    response = requests.get(
        url=f"{config['taiga']['url']}/api/v1/projects",
        headers={
            "Authorization": f"Bearer {taiga_auth_token}",
            "x-disable-pagination": "True",
        },
    )
    raw_projects = response.json()

    for project in raw_projects:
        # Create the board
        boards[project["id"]] = {
            "name": project["name"],
            "members": {},
            "slug": project["slug"],
            "statuses": {"story": {}, "task": {}, "issue": {}},
            "closing_statuses": {"story": [], "task": [], "issue": []},
            "severities": {},
            "types": {},
            "priorities": {},
            "private": project["is_private"],
        }

        # Get the roles for the project
        response = requests.get(
            url=f"{config['taiga']['url']}/api/v1/roles",
            headers={
                "Authorization": f"Bearer {taiga_auth_token}",
                "x-disable-pagination": "True",
            },
            params={"project": project["id"]},
        )

        roles = response.json()

        lowest_role = {}
        highest_role = {}

        for role in roles:
            if role["name"] == "Bot":
                continue
            if not lowest_role:
                lowest_role = role
            elif len(role["permissions"]) < len(lowest_role["permissions"]):
                lowest_role = role
            if not highest_role:
                highest_role = role
            elif len(role["permissions"]) > len(highest_role["permissions"]):
                highest_role = role

        boards[project["id"]]["lowest_role"] = lowest_role
        boards[project["id"]]["highest_role"] = highest_role

        # Add the project to the project cache
        projects["by_name"][project["name"].lower()] = project["id"]

        # Project membership
        for member in project["members"]:
            # Get info about the member
            response = requests.get(
                url=f"{config['taiga']['url']}/api/v1/users/{member}",
                headers={
                    "Authorization": f"Bearer {taiga_auth_token}",
                    "x-disable-pagination": "True",
                },
            )
            member_info = response.json()
            boards[project["id"]]["members"][member] = {
                "name": member_info["full_name_display"]
            }

            # Add the user to the global users list
            if member not in users:
                users[member] = {
                    "name": member_info["full_name_display"],
                    "username": member_info["username"],
                    "photo": member_info["photo"],
                    "projects": [],
                }

            users[member]["projects"].append(project["id"])

    # Statuses

    # Get statuses for all projects
    # This function won't be called outside of startup so we can use python-taiga
    statuses = {
        "story": taigacon.user_story_statuses.list(),
        "task": taigacon.task_statuses.list(),
        "issue": taigacon.issue_statuses.list(),
    }

    for status_type in statuses:
        for status in statuses[status_type]:
            boards[status.project]["statuses"][status_type][status.id] = (
                status.to_dict()
            )
            if status.is_closed:
                boards[status.project]["closing_statuses"][status_type].append(
                    status.to_dict()
                )
                boards[status.project]["closing_statuses"][status_type][-1]["id"] = (
                    status.id
                )

        # Sort the statuses by order
        for project in boards:
            boards[project]["statuses"][status_type] = dict(
                sorted(
                    boards[project]["statuses"][status_type].items(),
                    key=lambda item: item[1]["order"],
                )
            )
        for project in boards:
            boards[project]["closing_statuses"][status_type] = sorted(
                boards[project]["closing_statuses"][status_type],
                key=lambda item: item["order"],
            )

    # Get all severities
    severities = taigacon.severities.list()
    for severity in severities:
        boards[severity.project]["severities"][severity.id] = severity.to_dict()

    # Get all types
    types = taigacon.issue_types.list()
    for type in types:
        boards[type.project]["types"][type.id] = type.to_dict()

    # Get all priorities
    priorities = taigacon.priorities.list()
    for priority in priorities:
        boards[priority.project]["priorities"][priority.id] = priority.to_dict()

    # Sort types, severities, and priorities by order
    for project in boards:
        for key in ["severities", "types", "priorities"]:
            boards[project][key] = dict(
                sorted(
                    boards[project][key].items(),
                    key=lambda item: item[1]["order"],
                )
            )

    cache["boards"] = boards
    cache["users"] = users

    projects["by_name_with_extra"] = projects["by_name"]
    # Duplicate similar board names for QoL
    projects["by_name_with_extra"]["infra"] = projects["by_name_with_extra"][
        "infrastructure"
    ]
    projects["by_name_with_extra"]["laser"] = projects["by_name_with_extra"]["lasers"]
    projects["by_name_with_extra"]["printer"] = projects["by_name_with_extra"]["3d"]
    projects["by_name_with_extra"]["printers"] = projects["by_name_with_extra"]["3d"]

    cache["projects"] = projects

    return cache


def promote_issue(
    config: dict, taiga_auth_token: str, issue_id
) -> int | Literal[False]:
    """Create a user story based on an issue and delete the issue.

    Retains: subject, description, tags, assigned_to, watchers, attachments, due_date
    Does not retain: comments, status, priority, severity, type"""

    # Get the issue
    issue = get_info(taiga_auth_token, config, issue_id=issue_id)

    if not issue:
        logger.error(f"Failed to get issue {issue_id}")
        return False

    issue_data = {
        "subject": issue["subject"],
        "description": issue["description"],
        "due_date": issue["due_date"],
        "tags": issue["tags"],
        "assigned_to": issue["assigned_to"],
        "watchers": issue["watchers"],
    }

    # Get issue comments
    response = requests.get(
        f"{config['taiga']['url']}/api/v1/history/issue/{issue_id}",
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
    )
    comments = response.json()

    # Create the user story
    story_id, version = create_item(
        config,
        taiga_auth_token,
        issue["project"],
        item_type="story",
        **issue_data,
    )

    if not story_id or not version:
        logger.error(f"Failed to create user story for issue {issue_id}")
        return False

    # Attachments

    # The attachments field doesn't seem to be reliably present even when there are attachments
    # So we'll fetch the attachments separately

    response = requests.get(
        f"{config['taiga']['url']}/api/v1/issues/attachments",
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        params={"project": issue["project"], "object_id": issue_id},
    )
    attachments = response.json()

    if attachments:
        for attachment in attachments:
            attach_file(
                taiga_auth_token=taiga_auth_token,
                config=config,
                project_id=issue["project"],
                item_type="story",
                item_id=story_id,
                url=attachment["url"],
                filename=attachment["attached_file"].split("/")[-1],
                description=attachment["description"],
            )

    # Add comments to the story
    if comments:
        comment_strs = []
        for current_comment in comments:
            # Skip deleted comments
            if current_comment["delete_comment_date"]:
                continue

            name: str = current_comment["user"]["name"]
            comment: str = current_comment["comment"]

            if "Posted from Slack by" in current_comment["comment"]:
                match = re.match(
                    r"Posted from Slack by (.*?): (.*)",
                    current_comment["comment"],
                )
                if match:
                    name = match.group(1)
                    comment = match.group(2)
            comment_formatted = comment.replace("\n", "\n> ")
            comment_strs.append(f"> {name}: {comment_formatted}")

        # Taiga comments are new-old but we want old-new
        comment_strs.reverse()

        comment_str = "\n".join(comment_strs)

        # If there's more than one comment add an indication of order
        if len(comments) > 1:
            comment_str += "\n\nComments are sorted from oldest to newest."

        add_comment(
            type_str="story",
            item_id=story_id,
            comment=f"Comments mirrored from issue:\n{comment_str}",
            taiga_auth_token=taiga_auth_token,
            config=config,
            version=version,
        )

    # Delete the issue
    response = requests.delete(
        f"{config['taiga']['url']}/api/v1/issues/{issue_id}",
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
    )
    if response.status_code == 204:
        return int(story_id)
    else:
        logger.error(f"Failed to delete issue {issue_id}: {response.status_code}")
        return False


def check_project_membership(taiga_cache: dict, project_id: int, taiga_id: int) -> bool:
    """Check if the user is a member of the project."""

    return taiga_id in taiga_cache["boards"][int(project_id)]["members"]


def name_mapper(taiga_id: int | str | None, taiga_cache: dict) -> str:
    """Map a Taiga ID to a name."""
    if not taiga_id:
        return "Taiga/?"
    try:
        return taiga_cache["users"][int(taiga_id)]["name"]
    except KeyError:
        logger.error(f"User {taiga_id} not found")
        return f"Taiga/{taiga_id}"
    except ValueError:
        return f"Taiga/{taiga_id}"


def search(
    projects: list, taiga_auth_token: str, config: dict, search_str: str
) -> dict:
    """Search for items in Taiga."""

    results = {}

    for project in projects:
        response = requests.get(
            url=f"{config['taiga']['url']}/api/v1/search",
            headers={
                "Authorization": f"Bearer {taiga_auth_token}",
            },
            params={"project": int(project), "text": search_str},
        )
        current_results = response.json()

        for result_type, result_list in current_results.items():
            # Inject the project ID into each result
            if result_type == "count":
                continue
            for result in result_list:
                result["project"] = int(project)

            if result_type not in results:
                results[result_type] = []
            results[result_type] += result_list

    return results
