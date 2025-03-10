import logging
from datetime import datetime

import taiga

from util import misc, taigalink, tidyhq, training

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def joined_slack(config: dict, contact_id: str, tidyhq_cache: dict) -> bool:
    """Check if the contact has a Slack ID field set in TidyHQ."""
    if contact_id is None:
        return False

    # Find the contact in the cache
    contact = None
    for c in tidyhq_cache["contacts"]:
        if c["id"] == contact_id:
            contact = c
            break
    if not contact:
        logger.error(f"Contact {contact_id} not found in cache")
        return False

    # Check if the contact is already in the Slack group
    for field in contact["custom_fields"]:
        if field["id"] == config["tidyhq"]["ids"]["slack"]:
            if field["value"]:
                logger.debug(f"Contact {contact_id} has an associated slack account")
                return True
    return False


def visitor_signup(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has ever signed up as a visitor or member."""

    if contact_id is None:
        return False

    # Get all memberships for the contact

    memberships = tidyhq.get_memberships_for_contact(
        cache=tidyhq_cache, contact_id=contact_id
    )

    visitor = False
    member = False
    for membership in memberships:
        if membership["membership_level"]["name"] == "Visitor":
            logger.debug(f"Contact {contact_id} is a visitor")
            visitor = True

        elif "Membership" in membership["membership_level"]["name"]:
            logger.debug(f"Contact {contact_id} is a member")
            member = True

    return visitor or member


def member_signup(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has ever signed up as a member."""
    if contact_id is None:
        return False

    # Get all memberships for the contact

    memberships = tidyhq.get_memberships_for_contact(
        cache=tidyhq_cache, contact_id=contact_id
    )

    logger.debug(f"Contact {contact_id} has {len(memberships)} memberships")

    for membership in memberships:
        if "Membership" in membership["membership_level"]["name"]:
            logger.debug(f"Contact {contact_id} is a member")
            return True
    return False


def member_induction(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has been signed off for the member induction within Training Tracker."""
    if contact_id is None:
        return False

    inductions = training.get_inductions_for_contact(
        contact_id=contact_id, config=config, tidyhq_cache=tidyhq_cache
    )

    if "Induction (Member)" in inductions:
        logger.debug(f"Contact {contact_id} has completed the member induction")
        return True
    return False


def visitor_induction(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has been signed off for the visitor induction within Training Tracker."""
    if contact_id is None:
        return False

    inductions = training.get_inductions_for_contact(
        contact_id=contact_id, config=config, tidyhq_cache=tidyhq_cache
    )

    if "Induction (Visitor)" in inductions:
        logger.debug(f"Contact {contact_id} has completed the visitor induction")
        return True

    elif "Induction (Member)" in inductions:
        logger.debug(
            f"Contact {contact_id} has completed the member induction (bypassing visitor induction)"
        )
        return True
    return False


def keyholder_induction(
    config: dict, contact_id: str | None, tidyhq_cache: dict
) -> bool:
    """Check if the contact has been signed off for the keyholder induction within Training Tracker."""
    if contact_id is None:
        return False

    inductions = training.get_inductions_for_contact(
        contact_id=contact_id, config=config, tidyhq_cache=tidyhq_cache
    )

    if "Induction (Keyholder)" in inductions:
        logger.debug(f"Contact {contact_id} has completed the keyholder induction")
        return True
    return False


def id_photo(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has uploaded an ID photo."""
    if contact_id is None:
        return False

    photo_url = tidyhq.get_custom_field(
        config=config,
        contact_id=contact_id,
        cache=tidyhq_cache,
        field_id=None,
        field_map_name="photo_id",
    )

    if photo_url:
        logger.debug(f"Contact {contact_id} has uploaded an ID photo")
        return True
    return False


def check_payment_method(
    config: dict, contact_id: str | None, tidyhq_cache: dict
) -> bool:
    """Check if the contact's most recent payment was via bank transfer."""
    if contact_id is None:
        return False

    payment_method = None

    contact_id = str(contact_id)

    # Confirm that the contact has at least one invoice
    if contact_id not in tidyhq_cache["invoices"]:
        logger.debug(f"Contact {contact_id} has no invoices")
        return False

    # Iterate over invoices for given contact id. Invoices are sorted newest first
    for invoice in tidyhq_cache["invoices"][contact_id]:
        if invoice["paid"]:
            payment_method = invoice["payments"][0]["type"]
            break

    logger.debug(f"Contact {contact_id} last paid with: {payment_method}")

    if payment_method == "bank":
        return True
    return False


def bond_invoice_sent(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if we've sent an invoice for 135/225 to the contact. Does not check if it's been paid."""
    if contact_id is None:
        return False

    contact_id = str(contact_id)
    # Iterate over invoices for given contact id. Invoices are sorted newest first
    for invoice in tidyhq_cache["invoices"][contact_id]:
        # Bond invoices are specific amounts for concession, full respectively
        # This is a best guess without retrieving the full invoice details
        if invoice["amount"] in [135, 225]:
            logger.debug(f"Contact {contact_id} may have been sent a bond invoice")
            return True
        logger.debug(f"Contact {contact_id} has not been sent a bond invoice")
    return False


def bond_invoice_paid(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if we've sent an invoice for 135/225 to the contact. Does not check if it's been paid."""
    if contact_id is None:
        return False

    contact_id = str(contact_id)
    # Iterate over invoices for given contact id. Invoices are sorted newest first
    for invoice in tidyhq_cache["invoices"][contact_id]:
        # Bond invoices are specific amounts for concession, full respectively
        # This is a best guess without retrieving the full invoice details
        if invoice["amount"] in [135, 225]:
            logger.debug(f"Contact {contact_id} may have been sent a bond invoice")
            if invoice["paid"]:
                logger.debug(f"Contact {contact_id} has paid their bond invoice")
                return True
            else:
                logger.debug(f"Contact {contact_id} has not paid their bond invoice")
                return False
        logger.debug(f"Contact {contact_id} has not been sent a bond invoice")
    return False


def check_billing_groups(
    config: dict, contact_id: str | None, tidyhq_cache: dict
) -> bool:
    """Check if the contact is in a group that contains the string 'Billing'."""
    if contact_id is None:
        return False

    return tidyhq.check_for_groups(
        contact_id=contact_id, tidyhq_cache=tidyhq_cache, group_string="Billing"
    )


def at_least_one_tool(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has been signed off on at least one tool"""
    if contact_id is None:
        return False

    inductions = training.get_inductions_for_contact(
        contact_id=contact_id, config=config, tidyhq_cache=tidyhq_cache
    )

    for induction in inductions:
        # Orientation inductions all have the word "Induction" in them, tool inductions don't
        if "Induction" not in induction:
            logger.debug(
                f"Contact {contact_id} has completed at least one tool induction"
            )
            return True
    return False


def concession_sighted(
    config: dict, contact_id: str | None, tidyhq_cache: dict
) -> bool:
    """Check if the contact has had their concession proof sighted and recorded in TidyHQ.

    _Does not_ return True if the user does not need to provide proof of concession."""
    if contact_id is None:
        return False

    if tidyhq.get_custom_field(
        config=config,
        contact_id=contact_id,
        cache=tidyhq_cache,
        field_map_name="concession",
    ):
        return True

    return False


def concession_not_needed(contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Returns True if the contact does not need to provide proof of concession.

    Will also return False if the contact does not have an actual membership"""

    if contact_id is None:
        return False

    member_type = tidyhq.get_membership_type(
        contact_id=contact_id, tidyhq_cache=tidyhq_cache
    )

    # Technically visitors etc also don't need to provide proof of concession but this task isn't added until they're a member
    return member_type in ["Full", "Sponsored"]


def member_2week(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check whether the member has held their current membership for at least two weeks."""

    if contact_id is None:
        return False

    memberships = tidyhq.get_memberships_for_contact(
        cache=tidyhq_cache, contact_id=contact_id
    )

    most_recent = tidyhq.return_most_recent_membership(memberships)

    # Check if the start date of the membership is at least two weeks ago
    # Format is 2019-11-01T08:00:00+08:00
    start_date = most_recent["start_date"].split("T")[0]
    start_date = datetime.strptime(start_date, "%Y-%m-%d")
    days = (datetime.now() - start_date).days
    if days >= 14:
        logger.debug(
            f"Contact {contact_id} has held their membership for at least two weeks"
        )
        return True
    return False


def member_6month(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check whether the member has held their current membership for at least six months (180 days)."""

    if contact_id is None:
        return False

    memberships = tidyhq.get_memberships_for_contact(
        cache=tidyhq_cache, contact_id=contact_id
    )

    most_recent = tidyhq.return_most_recent_membership(memberships)

    # Check if the start date of the membership is at least two weeks ago
    # Format is 2019-11-01T08:00:00+08:00
    start_date = most_recent["start_date"].split("T")[0]
    start_date = datetime.strptime(start_date, "%Y-%m-%d")
    days = (datetime.now() - start_date).days
    if days >= 180:
        logger.debug(
            f"Contact {contact_id} has held their membership for at least 6 months"
        )
        return True
    return False


def member_18month(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check whether the member has held their current membership for at least 18 months (540 days)."""

    if contact_id is None:
        return False

    memberships = tidyhq.get_memberships_for_contact(
        cache=tidyhq_cache, contact_id=contact_id
    )

    most_recent = tidyhq.return_most_recent_membership(memberships)

    # Check if the start date of the membership is at least two weeks ago
    # Format is 2019-11-01T08:00:00+08:00
    start_date = most_recent["start_date"].split("T")[0]
    start_date = datetime.strptime(start_date, "%Y-%m-%d")
    days = (datetime.now() - start_date).days
    if days >= 540:
        logger.debug(
            f"Contact {contact_id} has held their membership for at least 18 months"
        )
        return True
    return False


def valid_emergency(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has valid emergency contact details."""

    if contact_id is None:
        return False

    contact = tidyhq.get_contact(contact_id=contact_id, tidyhq_cache=tidyhq_cache)

    if not contact:
        logger.error(f"Contact {contact_id} not found in cache")
        return False

    contact_number = contact.get("phone_number")
    emergency_name = contact.get("emergency_contact_person")
    emergency_number = contact.get("emergency_contact_number")

    # Confirm that all three fields are filled out
    if not (contact_number and emergency_name and emergency_number):
        logger.debug(
            f"Contact {contact_id} has at least one missing field of: {contact_number}, {emergency_name}, {emergency_number}"
        )
        return False

    # Confirm that the emergency contact number is a valid phone number
    if not misc.valid_phone_number(emergency_number):
        logger.debug(f"Contact {contact_id} has an invalid emergency contact number")
        return False

    # Check if the emergency contact number is the same as the contact's number
    if contact_number == emergency_number:
        logger.debug(
            f"Contact {contact_id} has the same emergency contact number as their own"
        )
        return False

    if contact_number[-9:] == emergency_number[-9:]:
        logger.debug(
            f"Contact {contact_id} has the same emergency contact number as their own"
        )
        return False

    return True


def has_key(config: dict, contact_id: str | None, tidyhq_cache: dict) -> bool:
    """Check if the contact has a key enabled"""
    if contact_id is None:
        return False

    key_status = tidyhq.get_custom_field(
        config=config,
        contact_id=contact_id,
        cache=tidyhq_cache,
        field_map_name="key_status",
    )

    if not key_status:
        return False

    for value in key_status["value"]:
        if value["title"] == "Enabled":
            return True
    return False


def check_all_tasks(
    taigacon: taiga.TaigaAPI,
    taiga_auth_token: str,
    config: dict,
    tidyhq_cache: dict,
    project_id: str,
    task_statuses: dict,
) -> int:
    """Check for incomplete tasks that have a mapped function to check if they are complete."""
    made_changes = 0
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
        "Received at least one tool induction": at_least_one_tool,
        "Proof of concession sighted": concession_sighted,
        "Held membership for at least two weeks": member_2week,
        "Confirmed bond invoice paid": bond_invoice_paid,
        "Has valid emergency contact details": valid_emergency,
        "Keyholder motion put to committee": has_key,
        "Keyholder motion successful": has_key,
        "Send keyholder documentation": has_key,
        "Send bond invoice": has_key,
        "Confirmed bond invoice paid": has_key,
        "No indications of Code of Conduct violations": has_key,
        "Competent to decide who can come in outside of events": has_key,
        "Works well unsupervised": has_key,
        "Undertakes tasks safely": has_key,
        "Cleans own work area": has_key,
        "Communicates issues to Management Committee if they arise": has_key,
        "Offered backing for key": has_key,
        "Planned first project": member_6month,
        "No history of invoice deliquency": member_18month,
    }

    # Find all user stories that include our bot managed tag
    stories = taigacon.user_stories.list(project=project_id, tags="bot-managed")
    for story in stories:
        # Retrieve the TidyHQ ID for the story
        tidyhq_id = taigalink.get_tidyhq_id(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        # Check over each task in the story
        tasks = taigacon.tasks.list(user_story=story.id)
        for task in tasks:
            if task.is_closed or task_statuses[task.status] in [
                "Not applicable",
            ]:
                logger.debug(f"Task {task.subject} is not complete, optional, or N/A")
                continue
            if task.subject not in task_function_map:
                logger.debug(f"No function found for task {task.subject}")
                continue

            logger.debug(f"Checking task {task.subject}")
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
                    logger.info(f"Task {task.subject} marked as complete")
                    made_changes += 1
                else:
                    logger.error(f"Failed to mark task {task.subject} as complete")
            else:
                logger.debug(f"Task {task.subject} not complete")

            if task.subject == "Proof of concession sighted":
                if concession_not_needed(
                    contact_id=tidyhq_id, tidyhq_cache=tidyhq_cache
                ):
                    logger.debug(
                        f"Contact {tidyhq_id} does not need to provide proof of concession"
                    )
                    updating = taigalink.update_task(
                        task_id=task.id,
                        status=23,
                        taiga_auth_token=taiga_auth_token,
                        config=config,
                        version=task.version,
                    )
                    if updating:
                        logger.info(f"Task {task.subject} marked as not applicable")
                        made_changes += 1
                    else:
                        logger.error(
                            f"Failed to mark task {task.subject} as not applicable"
                        )

    return made_changes
