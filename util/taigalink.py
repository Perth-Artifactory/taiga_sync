import logging
import sys

import requests


def get_custom_fields_for_story(
    story_id: str, taiga_auth_token: str, config: dict
) -> tuple[dict, int]:
    custom_attributes_url = f"{config['taiga']['url']}/api/v1/userstories/custom-attributes-values/{story_id}"
    response = requests.get(
        custom_attributes_url,
        headers={"Authorization": f"Bearer {taiga_auth_token}"},
    )

    if response.status_code == 200:
        custom_attributes: dict = response.json().get("attributes_values", {})
        version: int = response.json().get("version", 0)
        logging.debug(
            f"Fetched custom attributes for story {story_id}: {custom_attributes}"
        )
    else:
        logging.error(
            f"Failed to fetch custom attributes for story {story_id}: {response.status_code}"
        )

    return custom_attributes, version


def get_tidyhq_id(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    custom_attributes, version = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("1", None)


def get_email(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    custom_attributes, version = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("2", None)


def get_tidyhq_url(story_id: str, taiga_auth_token: str, config: dict) -> str | None:
    custom_attributes, version = get_custom_fields_for_story(
        story_id, taiga_auth_token, config
    )
    return custom_attributes.get("3", None)


def update_task(
    task_id: str, status: int, taiga_auth_token: str, config: dict, version: int
) -> bool:
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
        logging.error(
            f"Failed to update task {task_id} with status {status}: {response.status_code}"
        )
        logging.error(response.json())
        return False


def progress_story(
    story_id: str, taigacon, taiga_auth_token: str, config: dict
) -> bool:
    # Get the current status of the story
    story = taigacon.user_stories.get(story_id)
    current_status = int(story.status)

    if current_status == 5:
        logging.info(f"User story {story_id} is already complete")
        return False

    update_url = f"{config['taiga']['url']}/api/v1/userstories/{story_id}"
    response = requests.patch(
        update_url,
        headers={
            "Authorization": f"Bearer {taiga_auth_token}",
            "Content-Type": "application/json",
        },
        json={"status": current_status + 1, "version": story.version},
    )

    if response.status_code == 200:
        logging.debug(f"User story {story_id} status updated to {current_status + 1}")
        return True
    else:
        logging.error(
            f"Failed to update user story {story_id} status: {response.status_code}"
        )
        logging.error(response.json())
        return False


def set_custom_field(
    config: dict, taiga_auth_token: str, story_id: int, field_id: int, value: str
) -> bool:
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
        logging.debug(
            f"Fetched custom attributes for story {story_id}: {custom_attributes}"
        )
    else:
        logging.error(
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
        logging.info(
            f"Updated story {story_id} with custom attribute {field_id}: {value}"
        )
        return True

    else:
        logging.error(
            f"Failed to update story {story_id} with custom attribute {field_id}: {value}: {response.status_code}"
        )
        logging.error(response.json())

    return False
