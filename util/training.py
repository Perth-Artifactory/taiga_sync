from util import tidyhq
import logging


def get_inductions_for_contact(
    config: dict, contact_id: str, tidyhq_cache: dict
) -> list:
    # Get a list of all groups that the contact is a member of

    for contact in tidyhq_cache["contacts"]:
        if contact["id"] == contact_id:
            raw_groups = contact["groups"]
            break
    else:
        logging.error(f"Contact {contact_id} not found in cache")
        return []

    logging.debug(f"Got {len(raw_groups)} groups for contact {contact_id}")

    # Strip down to just the induction groups
    induction_groups = []
    for group in raw_groups:
        if config["tidyhq"]["training_prefix"] in group["label"]:
            induction_groups.append(
                group["label"].replace(config["tidyhq"]["training_prefix"], "")
            )

    logging.debug(
        f"Stripped down to {len(induction_groups)} induction groups for contact {contact_id}"
    )

    return induction_groups
