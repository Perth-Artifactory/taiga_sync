import requests
import logging
from pprint import pprint

logger = logging.getLogger(__name__)


def get_info_from_url(url: str, taiga_auth_token: str, taiga_cache: dict, config: dict):
    """
    Get the project_id, item type, and item id from a Taiga URL.
    """

    # Strip the protocol and domain
    url = url.replace("https://", "").replace("http://", "")
    url = url.replace("tasks.artifactory.org.au", "")
    if url.startswith("/"):
        url = url[1:]

    # This occurs when the URL is just the domain
    if not url:
        return None, None, None

    # Strip query parameters
    url = url.split("?")[0]

    parts = url.split("/")

    # This occurs when the URL is for a project section (kanban/issues etc)
    if len(parts) == 3:
        # Map the project slug to the project id
        for project_id, project in taiga_cache["boards"].items():
            if project["slug"] == parts[1]:
                return project_id, parts[2], None
        return None, None, None
    elif len(parts) < 3:
        return None, None, None
    pprint(parts)

    project_slug = parts[1]
    item_type = parts[2]
    item_ref = parts[3]

    # Resolve the item ref
    url = f"{config['taiga']['url']}/api/v1/resolver"
    params = {"project": project_slug}
    if item_type == "us":
        params["us"] = item_ref
    elif item_type == "task":
        params["task"] = item_ref
    elif item_type == "issue":
        params["issue"] = item_ref

    response = requests.get(
        url, params=params, headers={"Authorization": f"Bearer {taiga_auth_token}"}
    )

    if response.status_code != 200:
        logger.error(f"Failed to resolve item ref: {response.text}")
        return None, None, None

    info = response.json()

    project_id = info["project"]

    # There's only two fields in this response but the item id key changes depending on the item type
    info.pop("project")
    item_id = list(info.values())[0]

    return project_id, item_type, item_id
