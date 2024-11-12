from util import tidyhq
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def get_inductions_for_contact(
    config: dict, contact_id: str, tidyhq_cache: dict
) -> list:
    """Get a list of all inductions completed by a contact."""

    # Get a list of all groups that the contact is a member of
    contact = tidyhq.get_contact(contact_id=contact_id, tidyhq_cache=tidyhq_cache)

    if not contact:
        logger.error(f"Contact {contact_id} not found in cache")
        return []

    raw_groups = contact["groups"]

    logger.debug(f"Got {len(raw_groups)} groups for contact {contact_id}")

    # Strip down to just the induction groups
    induction_groups = []
    for group in raw_groups:
        if config["tidyhq"]["training_prefix"] in group["label"]:
            induction_groups.append(
                group["label"].replace(config["tidyhq"]["training_prefix"], "")
            )

    logger.debug(
        f"Stripped down to {len(induction_groups)} induction groups for contact {contact_id}"
    )

    return induction_groups
