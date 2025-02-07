import json
import logging

import taiga

from util import taigalink, tidyhq

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def sync_templates(taigacon: taiga.TaigaAPI, project_id: str) -> int:
    """Copy tasks from template stories to user stories."""
    made_changes: int = 0

    # Load a list of past actions
    try:
        with open("template_actions.json") as f:
            actions = json.load(f)
    except FileNotFoundError:
        actions = {}

    # Find template stories
    templates = {}

    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project_id)
    for story in stories:
        # Check if the story is a template story
        if story.subject == "Template":
            # Get the tasks for the template story
            raw_tasks = taigacon.tasks.list(user_story=story.id)
            tasks = []
            for task in raw_tasks:
                tasks.append({"status": task.status, "subject": task.subject})

            templates[story.status] = tasks

    # Find all user stories that include our bot managed tag
    # We don't filter the bot-managed tag in the query because template stories don't have that tag
    for story in stories:
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                logger.debug(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True

        if not tagged:
            continue

        # Check if we have already created tasks for this story in the current state

        if str(story.id) in actions:
            if str(story.status) in actions[str(story.id)]:
                logger.debug(
                    f"Tasks for story {story.subject} already created in state {story.status}"
                )
                continue

        # Check if we have a template for this type of story
        if story.status not in templates:
            logger.debug(f"No template for story {story.subject}")
            continue

        logger.debug(f"Found template for story {story.subject}")
        template = templates[story.status]

        # Get a list of existing tasks for the story
        raw_tasks = taigacon.tasks.list(user_story=story.id)
        existing_tasks = []
        for task in raw_tasks:
            existing_tasks.append(task.subject)

        for task in template:
            if task["subject"] in existing_tasks:
                logger.debug(f"Task {task['subject']} already exists")
                continue

            logger.info(f"Creating task {task['subject']} with status {task['status']}")
            taigacon.tasks.create(
                project=project_id,
                user_story=story.id,
                status=task["status"],
                subject=task["subject"],
            )
            made_changes += 1

        if str(story.id) not in actions:
            actions[str(story.id)] = []
        actions[str(story.id)].append(str(story.status))

        # Update our saved actions
        with open("template_actions.json", "w") as f:
            json.dump(actions, f)

    return made_changes


def progress_stories(
    taigacon: taiga.TaigaAPI,
    project_id: str,
    taiga_auth_token: str,
    config: dict,
    story_statuses: dict,
    task_statuses: dict,
) -> int:
    """Progress stories to the next status that have all tasks complete."""
    made_changes: int = 0
    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project_id, tags="bot-managed")

    for story in stories:
        # Check if all tasks are complete

        tasks = taigacon.tasks.list(user_story=story.id)

        complete = True

        for task in tasks:
            if not task.is_closed and task_statuses[task.status] not in [
                "Optional",
                "Not applicable",
            ]:
                complete = False
                logger.debug(f"Task {task.subject} is not complete, optional, or N/A")
                break

        if complete:
            logger.info(
                f"Story {story.subject} has all tasks complete and will be progressed"
            )
            changed = taigalink.progress_story(
                story_id=story.id,
                taigacon=taigacon,
                taiga_auth_token=taiga_auth_token,
                config=config,
                story_statuses=story_statuses,
            )

            if changed:
                made_changes += 1

    return made_changes


def progress_on_tidyhq(
    taigacon: taiga.TaigaAPI,
    project_id: str,
    taiga_auth_token: str,
    config: dict,
    story_statuses: dict,
) -> int:
    """Progress stories from column 2 to column 3 when a TidyHQ ID is set."""
    made_changes: int = 0
    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project_id, tags="bot-managed")

    for story in stories:
        # Check if the story is in the prospective column
        if story_statuses[story.status]["name"] not in ["Prospective", "Intake"]:
            logger.debug(f"Story {story.subject} is not in the prospective column")
            continue

        # Check if the story has a TidyHQ ID set
        tidyhq_id = taigalink.get_tidyhq_id(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        if tidyhq_id:
            logger.debug(
                f"Story {story.subject} has a TidyHQ ID set but is prospective"
            )

            # Move the story to the next column
            taigalink.progress_story(
                story_id=story.id,
                taigacon=taigacon,
                taiga_auth_token=taiga_auth_token,
                config=config,
                story_statuses=story_statuses,
            )

            made_changes += 1

    return made_changes


def progress_on_membership(
    taigacon: taiga.TaigaAPI,
    project_id: str,
    taiga_auth_token: str,
    config: dict,
    story_statuses: dict,
    tidyhq_cache: dict,
) -> int:
    """Progress stories from column 3 to column 4 the contact has a membership"""
    made_changes: int = 0
    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project_id, tags="bot-managed")

    for story in stories:
        # Check if the story is in the attendee column
        if story_statuses[story.status]["name"] != "Attendee":
            logger.debug(f"Story {story.subject} is not in the attendee column")
            continue

        # Check if the story has a TidyHQ ID set
        tidyhq_id = taigalink.get_tidyhq_id(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        if not tidyhq_id:
            logger.debug(f"Story {story.subject} does not have a TidyHQ ID set")
            continue

        # Check if the user has a membership
        membership = tidyhq.get_membership_type(
            contact_id=tidyhq_id, tidyhq_cache=tidyhq_cache
        )

        if not membership:
            logger.debug(f"Contact {tidyhq_id} does not have a membership")
            continue

        if membership not in ["Full", "Concession", "Sponsor"]:
            logger.debug(
                f"Contact {tidyhq_id} has a membership type of {membership} which is not valid for this step"
            )
            continue

        # Move the story to the next column
        taigalink.progress_story(
            story_id=story.id,
            taigacon=taigacon,
            taiga_auth_token=taiga_auth_token,
            config=config,
            story_statuses=story_statuses,
        )

        made_changes += 1

    return made_changes


def add_useful_fields(
    project_id: str,
    taigacon: taiga.TaigaAPI,
    taiga_auth_token: str,
    config: dict,
    tidyhq_cache: dict,
):
    """Add useful fields to stories.

    Current useful fields:
    * Clickable TidyHQ contact link
    * Membership type
    """
    # Iterate over all user stories
    stories = taigacon.user_stories.list(project=project_id, tags="bot-managed")
    for story in stories:
        # Set TidyHQ contact URL

        # Check if the story has a TidyHQ link set
        tidyhq_url = taigalink.get_tidyhq_url(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        if tidyhq_url:
            logger.debug(f"Story {story.subject} already has a TidyHQ URL set")
        else:
            # Check if the story has a TidyHQ ID set
            tidyhq_id = taigalink.get_tidyhq_id(
                story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
            )

            if tidyhq_id:
                logger.debug(f"Story {story.subject} has a TidyHQ ID set but no URL")

                # Set the TidyHQ URL
                taigalink.set_custom_field(
                    config=config,
                    taiga_auth_token=taiga_auth_token,
                    story_id=story.id,
                    field_id=3,
                    value=f"https://{tidyhq_cache['org']['domain_prefix']}.tidyhq.com/contacts/{tidyhq_id}",
                )

        # Set TidyHQ membership type

        # Check if the story has a membership type set
        membership_type = taigalink.get_member_type(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        if not membership_type:
            # Check if the story has a TidyHQ ID set
            tidyhq_id = taigalink.get_tidyhq_id(
                story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
            )

            if tidyhq_id:
                # Get the membership type
                membership = tidyhq.get_membership_type(
                    contact_id=tidyhq_id, tidyhq_cache=tidyhq_cache
                )

                if membership:
                    logger.debug(
                        f"Setting membership type for story {story.subject} to {membership}"
                    )
                    taigalink.set_custom_field(
                        config=config,
                        taiga_auth_token=taiga_auth_token,
                        story_id=story.id,
                        field_id=4,
                        value=membership,
                    )
                else:
                    logger.debug(f"Contact {tidyhq_id} does not have a membership")
            else:
                logger.debug(f"Story {story.subject} does not have a TidyHQ ID set")
        else:
            logger.debug(f"Story {story.subject} already has a membership type set")
