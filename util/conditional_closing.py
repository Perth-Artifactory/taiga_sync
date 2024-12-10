import logging
import sys

from util import taigalink
import taiga

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def close_by_order(
    taigacon: taiga.TaigaAPI,
    project_id: str,
    config: dict,
    taiga_auth_token: str,
    story_statuses: dict,
) -> int:
    """Close tasks once a story reaches a certain order."""
    made_changes: int = 0
    # Reminder: Orders are 0-indexed
    task_map: dict[int, list] = {
        3: ["Respond to enquiry", "Encourage to visit"],
        4: ["Signed up as a member", "Visit"],
        7: [
            "Held membership for at least two weeks",
            "No indications of Code of Conduct violations",
            "Competent to decide who can come in outside of events",
            "Works well unsupervised",
            "Undertakes tasks safely",
            "Cleans own work area",
            "Communicates issues to Management Committee if they arise",
            "No history of invoice deliquency",
            "Offered backing for key",
            "Keyholder motion put to committee",
            "Keyholder motion successful",
            "Send keyholder documentation",
            "Send bond invoice",
            "Confirm bond invoice paid",
        ],
    }

    stories = taigacon.user_stories.list(project=project_id, tags="bot-managed")
    for story in stories:

        # Check over each task in the story
        tasks = taigacon.tasks.list(user_story=story.id)
        for task in tasks:
            # If the task is already complete, skip it
            if task.status in [4, 23]:
                logger.debug(f"Task {task.subject} is already completed")
                continue

            # Look for task in map up until the position of the story
            order: int = taigalink.id_to_order(
                story_statuses=story_statuses, status_id=int(story.status)
            )
            for current_order in range(0, order + 2):
                logger.debug(
                    f"Checking task {task.subject} against tasks for order: {current_order}"
                )
                if task.subject in task_map.get(current_order, []):
                    logger.debug(f"Completing task {task.subject}")
                    updating = taigalink.update_task(
                        task_id=task.id,
                        status=4,
                        taiga_auth_token=taiga_auth_token,
                        config=config,
                        version=task.version,
                    )
                    if updating:
                        logger.info(f"Task {task.subject} marked as complete")
                        made_changes += 1
                    else:
                        logger.error(f"Failed to mark task {task.subject} as complete")
    return made_changes
