import json
import logging
import sys

from util import taigalink


def sync_templates(taigacon, project_id: str) -> bool:
    """Copy tasks from template stories to user stories."""
    made_changes: bool = False

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
    for story in stories:
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                logging.debug(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True

        if not tagged:
            continue

        # Check if we have already created tasks for this story in the current state

        if str(story.id) in actions:
            if str(story.status) in actions[str(story.id)]:
                logging.debug(
                    f"Tasks for story {story.subject} already created in state {story.status}"
                )
                continue

        # Check if we have a template for this type of story
        if story.status not in templates:
            logging.debug(f"No template for story {story.subject}")
            continue

        logging.debug(f"Found template for story {story.subject}")
        template = templates[story.status]
        for task in template:
            logging.info(
                f"Creating task {task['subject']} with status {task['status']}"
            )
            taigacon.tasks.create(
                project=project_id,
                user_story=story.id,
                status=task["status"],
                subject=task["subject"],
            )
            made_changes = True

        if str(story.id) not in actions:
            actions[str(story.id)] = []
        actions[str(story.id)].append(str(story.status))

        # Update our saved actions
        with open("template_actions.json", "w") as f:
            json.dump(actions, f)

    return made_changes


def progress_stories(
    taigacon, project_id: str, taiga_auth_token: str, config: dict
) -> bool:
    """Progress stories to the next status that have all tasks complete."""
    made_changes: bool = False
    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project_id)

    for story in stories:
        # Check if the story is managed by us
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                logging.debug(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True
                break

        if not tagged:
            continue

        # Check if all tasks are complete

        tasks = taigacon.tasks.list(user_story=story.id)

        complete = True

        for task in tasks:
            if task.status != 4:
                complete = False
                logging.debug(f"Task {task.subject} is not complete")
                break

        if complete:
            logging.info(
                f"Story {story.subject} has all tasks complete and will be progressed"
            )
            taigalink.progress_story(
                story_id=story.id,
                taigacon=taigacon,
                taiga_auth_token=taiga_auth_token,
                config=config,
            )

            made_changes = True

    return made_changes


def progress_on_signup(
    taigacon, project_id: str, taiga_auth_token: str, config: dict
) -> bool:
    """Progress stories from status 1 to status 2 when a TidyHQ ID is set."""
    made_changes: bool = False
    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project_id)

    for story in stories:
        # Check if the story is managed by us
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                logging.debug(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True
                break

        if not tagged:
            continue

        # Check if the story is in the prospective column
        if story.status != 1:
            continue

        # Check if the story has a TidyHQ ID set
        tidyhq_id = taigalink.get_tidyhq_id(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        if tidyhq_id:
            logging.debug(
                f"Story {story.subject} has a TidyHQ ID set but is prospective"
            )

            # Move the story to the next column
            taigalink.progress_story(
                story_id=story.id,
                taigacon=taigacon,
                taiga_auth_token=taiga_auth_token,
                config=config,
            )

            made_changes = True

    return made_changes


def add_useful_fields(
    project_id: str, taigacon, taiga_auth_token: str, config: dict, tidyhq_cache: dict
):
    """Add useful fields to stories.

    Current useful field is a clickable TidyHQ contact link.
    """
    # Iterate over all user stories
    stories = taigacon.user_stories.list(project=project_id)
    for story in stories:
        # Check if the story is managed by us
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                logging.debug(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True
                break

        if not tagged:
            continue

        # Set TidyHQ contact URL

        # Check if the story has a TidyHQ link set
        tidyhq_url = taigalink.get_tidyhq_url(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        if tidyhq_url:
            logging.debug(f"Story {story.subject} already has a TidyHQ URL set")
            continue

        # Check if the story has a TidyHQ ID set
        tidyhq_id = taigalink.get_tidyhq_id(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        if not tidyhq_id:
            logging.debug(f"Story {story.subject} does not have a TidyHQ ID set")
            continue

        # Set the TidyHQ URL
        taigalink.set_custom_field(
            config=config,
            taiga_auth_token=taiga_auth_token,
            story_id=story.id,
            field_id=3,
            value=f"https://{tidyhq_cache['org']['domain_prefix']}.tidyhq.com/contacts/{tidyhq_id}",
        )
