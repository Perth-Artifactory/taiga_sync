import logging
import sys

from util import taigalink


def close_by_status(
    taigacon, project_id: str, config: dict, taiga_auth_token: str
) -> bool:
    made_changes: bool = False
    task_map: dict[int, list] = {
        1: [],
        2: ["Respond to query", "Encourage to visit", "Visit"],
        3: ["Signed up as a member"],
        4: [],
        5: ["Demonstrated keyholder responsibilities", "Offered key"],
        6: [
            "Keyholder motion put to committee",
            "Keyholder motion successful",
            "Confirmed photo on tidyhq",
            "Confirmed paying via bank",
            "Send keyholder documentation",
            "Send bond invoice",
            "Keyholder induction completed",
        ],
    }

    stories = taigacon.user_stories.list(project=project_id)
    for story in stories:
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                logging.debug(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True
                status = int(story.status)

        if not tagged:
            continue

        # Check over each task in the story
        tasks = taigacon.tasks.list(user_story=story.id)
        for task in tasks:
            # If the task is already complete, skip it
            if task.status == 4:
                logging.debug(f"Task {task.subject} is already completed")
                continue

            # Look for task in map up until the status of the story
            for current_status in range(1, status + 1):
                if task.subject in task_map[current_status]:
                    logging.debug(f"Completing task {task.subject}")
                    updating = taigalink.update_task(
                        task_id=task.id,
                        status=4,
                        taiga_auth_token=taiga_auth_token,
                        config=config,
                        version=task.version,
                    )
                    if updating:
                        logging.info(f"Task {task.subject} marked as complete")
                        made_changes = True
                    else:
                        logging.error(f"Failed to mark task {task.subject} as complete")
    return made_changes


def remove_by_status():
    task_map = {
        1: [],
        2: [],
        3: [
            "Respond to query",
            "Encourage to visit",
            "Visit",
            "Signed up as a visitor",
            "Discussed moving to membership",
            "Completed new visitor induction",
        ],
        4: ["Determine project viability", "Signed up as a member"],
        5: ["Join Slack", "Participated in an event", "Attending events as a member"],
        6: ["Demonstrated keyholder responsibilities", "Offered key"],
    }
