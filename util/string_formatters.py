import logging
from datetime import datetime
from pprint import pprint


# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("slack_formatters")


def due_item(item: dict, item_type: str, for_user: str):
    """Converts a story or issue into a string for Slack highlighting the due date and status."""
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
