import logging
import requests
from pprint import pprint
import sys

from util import taigalink, tidyhq


def joined_slack(config, contact_id, tidyhq_cache):
    if contact_id == None:
        return False

    # Find the contact in the cache
    contact = None
    for c in tidyhq_cache["contacts"]:
        if c["id"] == contact_id:
            contact = c
            break
    if not contact:
        logging.error(f"Contact {contact_id} not found in cache")
        return False

    # Check if the contact is already in the Slack group
    for field in contact["custom_fields"]:
        if field["id"] == config["tidyhq"]["ids"]["slack"]:
            if field["value"]:
                logging.debug(f"Contact {contact_id} has an associated slack account")
                return True
    return False


def visitor_signup(config, contact_id, tidyhq_cache):
    if contact_id == None:
        return False

    # Get all memberships for the contact

    memberships = tidyhq.get_memberships_for_contact(
        cache=tidyhq_cache, contact_id=contact_id
    )

    visitor = False
    member = False
    for membership in memberships:
        if membership["membership_level"]["name"] == "Visitor":
            logging.debug(f"Contact {contact_id} is a visitor")
            visitor = True

        elif "Membership" in membership["membership_level"]["name"]:
            logging.debug(f"Contact {contact_id} is a member")
            member = True

    return visitor or member


def member_signup(config, contact_id, tidyhq_cache):
    if contact_id == None:
        return False

    # Get all memberships for the contact

    memberships = tidyhq.get_memberships_for_contact(
        cache=tidyhq_cache, contact_id=contact_id
    )

    logging.debug(f"Contact {contact_id} has {len(memberships)} memberships")

    for membership in memberships:
        if "Membership" in membership["membership_level"]["name"]:
            logging.debug(f"Contact {contact_id} is a member")
            return True
    return False


def check_all_tasks(taigacon, taiga_auth_token, config, tidyhq_cache, project_id):
    made_changes = False
    task_function_map = {
        "Join Slack": joined_slack,
        "Signed up as a visitor": visitor_signup,
        "Signed up as a member": member_signup,
        "Discussed moving to membership": member_signup,
    }

    # Find all user stories that include our bot managed tag
    stories = taigacon.user_stories.list(project=project_id)
    for story in stories:
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                logging.debug(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True

        if not tagged:
            continue

        # Retrieve the TidyHQ ID for the story
        tidyhq_id = taigalink.get_tidyhq_id(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        # Check over each task in the story
        tasks = taigacon.tasks.list(user_story=story.id)
        for task in tasks:
            if task.status == 4:
                logging.debug(f"Task {task.subject} is already completed")
                continue
            if task.subject not in task_function_map:
                logging.debug(f"No function found for task {task.subject}")
                continue

            logging.debug(f"Checking task {task.subject}")
            check = task_function_map[task.subject](
                config=config, tidyhq_cache=tidyhq_cache, contact_id=tidyhq_id
            )

            # If the check is successful, mark the task as complete
            if check:
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
            else:
                logging.debug(f"Task {task.subject} not complete")

    return made_changes
