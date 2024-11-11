import logging
import requests
from pprint import pprint
import sys

from util import taigalink, tidyhq, training


def joined_slack(config: dict, contact_id: str, tidyhq_cache: dict) -> bool:
    """Check if the contact has a Slack ID field set in TidyHQ."""
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


def visitor_signup(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has ever signed up as a visitor or member."""

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


def member_signup(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has ever signed up as a member."""
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


def member_induction(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has been signed off for the member induction within Training Tracker."""
    if contact_id == None:
        return False

    inductions = training.get_inductions_for_contact(
        contact_id=contact_id, config=config, tidyhq_cache=tidyhq_cache
    )

    if "Induction (Member)" in inductions:
        logging.debug(f"Contact {contact_id} has completed the member induction")
        return True
    return False


def visitor_induction(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has been signed off for the visitor induction within Training Tracker."""
    if contact_id == None:
        return False

    inductions = training.get_inductions_for_contact(
        contact_id=contact_id, config=config, tidyhq_cache=tidyhq_cache
    )

    if "Induction (Visitor)" in inductions:
        logging.debug(f"Contact {contact_id} has completed the visitor induction")
        return True
    return False


def keyholder_induction(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has been signed off for the keyholder induction within Training Tracker."""
    if contact_id == None:
        return False

    inductions = training.get_inductions_for_contact(
        contact_id=contact_id, config=config, tidyhq_cache=tidyhq_cache
    )

    if "Induction (Keyholder)" in inductions:
        logging.debug(f"Contact {contact_id} has completed the keyholder induction")
        return True
    return False


def id_photo(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has uploaded an ID photo."""
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


def check_payment_method(
    config: dict, contact_id: str | None, tidyhq_cache: dict
) -> bool:
    """Check if the contact's most recent payment was via bank transfer."""
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


def bond_invoice_sent(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if we've sent an invoice for 135/225 to the contact. Does not check if it's been paid."""
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


def check_billing_groups(
    config: dict, contact_id: str | None, tidyhq_cache: dict
) -> bool:
    """Check if the contact is in a group that contains the string 'Billing'."""
    if contact_id == None:
        return False

    return tidyhq.check_for_groups(
        contact_id=contact_id, tidyhq_cache=tidyhq_cache, group_string="Billing"
    )


def check_all_tasks(
    taigacon, taiga_auth_token: str, config: dict, tidyhq_cache: dict, project_id: str
):
    """Check for incomplete tasks that have a mapped function to check if they are complete."""
    made_changes = False
    task_function_map = {
        "Join Slack": joined_slack,
        "Signed up as a visitor": visitor_signup,
        "Signed up as a member": member_signup,
        "Discussed moving to membership": member_signup,
        "Completed new member induction": member_induction,
        "Completed new visitor induction": visitor_induction,
        "Completed keyholder induction": keyholder_induction,
        "Confirmed photo on tidyhq": id_photo,
        "Confirmed paying via bank": check_payment_method,
        "Send bond invoice": bond_invoice_sent,
        "Added to billing groups": check_billing_groups,
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
