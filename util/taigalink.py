import logging
import sys
from pprint import pprint

import requests

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
    custom_attributes, version = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("1", None)


def get_email(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    """Retrieve the email for a specific story if set."""
    custom_attributes, version = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("2", None)


def get_tidyhq_url(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    """Retrieve the TidyHQ URL for a specific story if set."""
    custom_attributes, version = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("3", None)


def get_member_type(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    """Retrieve the member type for a specific story if set."""
    custom_attributes, version = get_custom_fields_for_story(
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
    story_id: str, taigacon, taiga_auth_token: str, config: dict, story_statuses: dict
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
    update_url = f"{config['taiga']['url']}/api/v1/userstories/{story_id}"

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
    project_id: str,
    config: dict,
    subject: str,
    description: str | None = None,
    type_id: str | int | None = None,
    priority_id: str | int | None = None,
    severity_id: str | int | None = None,
    tags: list = [],
):
    """Create an issue on a Taiga project. Does no mapping and supports IDs only"""

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


def create_issue(
    taiga_auth_token: str,
    taigacon,
    project_id: str | None,
    config: dict,
    description: str = "No description provided",
    severity_id: int | None = None,
    severity_str: str | None = None,
    board_str: str = "Something else (Including administrative issues)",
    priority_id: int | None = None,
    subject: str = "Untriaged issue reported on Slack",
    tags: list = [],
    watchers: list = [],
) -> bool:
    """Create an issue on a Taiga project."""

    # Maps
    # Slack name -> Taiga name
    severity_map = {
        "This is a minor inconvenience": "Minor - Minor inconvenience",
        "This affects my ability to use the space": "Normal - Affects an attendees ability to use the space",
        "This significantly affects my ability to use the space": "Important - Significantly affects an attendees ability to use the space",
        "This affects a large number of members": "Important - This is a key tool/equipment/resource for members",
        "There is a risk of injury or damage to infrastructure": "Critical - This has the potential to cause injury",
    }
    board_map = {
        "Broken/Damaged tool, Broken Infrastructure": "infrastructure",
        "IT fault": "it",
        "3D printer/scanner problem": "3d",
        "Laser cutter problem": "laser",
        "Something else (Including administrative issues)": "committee",
    }

    # Convert all maps to lowercase
    severity_map = {key.lower(): value for key, value in severity_map.items()}
    board_map = {key.lower(): value for key, value in board_map.items()}

    logger.debug(f"Attempting to match board: {board_str}")
    board_str = board_map.get(board_str.lower(), "committee")
    project_id = str(
        item_mapper(
            item=board_str,
            field_type="board",
            project_id=None,
            taiga_auth_token=taiga_auth_token,
            config=config,
            taigacon=taigacon,
        )
    )
    logger.debug(f"Matched project: {project_id}")

    # Get the severity, priority and type IDs
    if severity_id is None and severity_str:
        logger.debug(f"Attempting to match severity: {severity_str}")
        severity_str = severity_map.get(severity_str.lower(), None)
        severity_id = item_mapper(
            item=severity_str,
            field_type="severity",
            project_id=project_id,
            taiga_auth_token=taiga_auth_token,
            config=config,
            taigacon=taigacon,
        )
        logger.debug(f"Matched severity: {severity_id}")

    # Get the priority ID for untriaged issues
    if not priority_id:
        priority_id = item_mapper(
            item="Untriaged",
            field_type="priority",
            project_id=project_id,
            taiga_auth_token=taiga_auth_token,
            config=config,
            taigacon=taigacon,
        )

    if not severity_id and priority_id and project_id:
        logger.error("Severity, priority and type IDs not found")
        return False

    create_url = f"{config['taiga']['url']}/api/v1/issues"
    logger.warning(watchers)
    response = requests.post(
        create_url,
        headers={
            "Authorization": f"Bearer {taiga_auth_token}",
        },
        json={
            "project": project_id,
            "subject": subject,
            "description": description,
            "tags": tags + ["slack"],
            "severity": severity_id,
            "priority": priority_id,
            "watchers": watchers,
        },
    )

    if response.status_code == 201:
        logger.info(f"Created issue {response.json()['id']} on project {project_id}")
        # Print the raw request that was made to taiga
        return response.json()["id"]
    else:
        logger.error(
            f"Failed to create issue on project {project_id}: {response.status_code}"
        )
        logger.error(response.json())
        logger.error(response.request.body)
        return False


def item_mapper(
    item: str | None,
    field_type: str,
    project_id: str | None,
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
    elif field_type == "board":
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
    objects = response.json()

    logger.debug(f"Fetched objects: {objects}")
    logger.debug(f"Looking for item: {item}")

    for object in objects:
        if object["name"].lower() == item.lower():
            return object["id"]

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
    taiga_auth_token,
    entry_id: int,
    project_id: int | None = None,
    project_str: str | None = None,
    entry_type: str = "story",
):
    """Create a link to the TidyHQ entry for the project."""
    if project_str is None and project_id:
        # Fetch the project name
        # TODO retrieve the project name from the ID.
        # Fortunately the only time this function is used is in a situation where we've derived the project ID from the project name
        logger.error(
            "Project name not provided and this function is not yet capable of retrieving it from the ID"
        )
    # Remap entry_type to the versions used in URLs
    entry_map = {"story": "us", "issue": "issue", "task": "task"}

    if entry_type not in entry_map:
        logger.error(f"Entry type {entry_type} not supported")
        return False

    return f"{config['taiga']['url']}/project/{project_str}/{entry_map[entry_type]}/{entry_id}"


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


def get_tasks(taiga_id: int, config: dict, taiga_auth_token: str):
    """Get all tasks assigned to a user."""

    url = f"{config['taiga']['url']}/api/v1/tasks"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        params={"assigned_to": taiga_id},
    )
    tasks = response.json()
    return tasks


def get_stories(taiga_id: int, config: dict, taiga_auth_token: str):
    """Get all stories assigned to a user."""

    url = f"{config['taiga']['url']}/api/v1/userstories"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
        params={"assigned_to": taiga_id},
    )
    stories = response.json()
    return stories


def sort_tasks_by_user_story(tasks):
    """Sort tasks by user story."""
    user_stories = {}
    for task in tasks:
        if task["user_story"] not in user_stories:
            user_stories[task["user_story"]] = []
        user_stories[task["user_story"]].append(task)
    return user_stories


def sort_stories_by_project(stories):
    """Sort stories by project."""
    projects = {}
    for story in stories:
        if story["project"] not in projects:
            projects[story["project"]] = []
        projects[story["project"]].append(story)
    return projects
