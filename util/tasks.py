import logging
import requests
from pprint import pprint
import sys

from util import taigalink, tidyhq, training


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


def member_induction(config, contact_id, tidyhq_cache):
    if contact_id == None:
        return False

    inductions = training.get_inductions_for_contact(
        contact_id=contact_id, config=config, tidyhq_cache=tidyhq_cache
    )

    if "New Member Orientation" in inductions:
        logging.debug(f"Contact {contact_id} has completed the New Member Orientation")
        return True
    return False


def id_photo(config, contact_id, tidyhq_cache):
    if contact_id == None:
        return False

    photo_url = tidyhq.get_custom_field(
        config=config,
        contact_id=contact_id,
        cache=tidyhq_cache,
        field_id=None,
        field_map_name="photo_id",
    )

    if photo_url:
        logging.debug(f"Contact {contact_id} has uploaded an ID photo")
        return True
    return False


def check_payment_method(config, contact_id, tidyhq_cache):
    if contact_id == None:
        return False

    payment_method = None

    contact_id = str(contact_id)

    # Iterate over invoices for given contact id. Invoices are sorted newest first
    for invoice in tidyhq_cache["invoices"][contact_id]:
        if invoice["paid"]:
            payment_method = invoice["payments"][0]["type"]
            break

    logging.debug(f"Contact {contact_id} last paid with: {payment_method}")

    if payment_method == "bank":
        return True
    return False


def bond_invoice_sent(config, contact_id, tidyhq_cache):
    if contact_id == None:
        return False

    contact_id = str(contact_id)
    # Iterate over invoices for given contact id. Invoices are sorted newest first
    for invoice in tidyhq_cache["invoices"][contact_id]:
        # Bond invoices are specific amounts for concession, full respectively
        # This is a best guess without retrieving the full invoice details
        if invoice["amount"] in [135, 225]:
            logging.debug(f"Contact {contact_id} may have been sent a bond invoice")
            return True
        logging.debug(f"Contact {contact_id} has not been sent a bond invoice")
    return False


def check_all_tasks(taigacon, taiga_auth_token, config, tidyhq_cache, project_id):
    made_changes = False
    task_function_map = {
        "Join Slack": joined_slack,
        "Signed up as a visitor": visitor_signup,
        "Signed up as a member": member_signup,
        "Discussed moving to membership": member_signup,
        "New member induction": member_induction,
        "Confirmed photo on tidyhq": id_photo,
        "Confirmed paying via bank": check_payment_method,
        "Send bond invoice": bond_invoice_sent,
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
