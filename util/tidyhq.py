import datetime
import json
import logging
import sys
import time
from copy import deepcopy as copy
from pprint import pprint
from typing import Any

import requests

from util import taigalink

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def query(
    cat: str | int,
    config: dict,
    term: str | None = None,
    cache: dict | None = None,
) -> dict | list:
    """Send a query to the TidyHQ API"""

    if type(term) == int:
        term = str(term)

    # If we have a cache, try using that first before querying TidyHQ
    if cache:
        if cat in cache:
            # Groups are indexed by ID before being cached
            if cat == "groups":
                if term:
                    if term in cache["groups"]:
                        return cache["groups"][term]
                    else:
                        try:
                            if int(term) in cache["groups"]:
                                return cache["groups"][int(term)]
                        except:
                            pass
                    # If we can't find the group, handle via query instead
                    logger.debug(f"Could not find group with ID {term} in cache")
                else:
                    return cache["groups"]
            elif cat == "contacts":
                if term:
                    for contact in cache["contacts"]:
                        if int(contact["id"]) == int(term):
                            return contact
                    # If we can't find the contact, handle via query
                    logger.debug(f"Could not find contact with ID {term} in cache")
                else:
                    return cache["contacts"]
        else:
            logger.debug(f"Could not find category {cat} in cache")

    append = ""
    if term:
        append = f"/{term}"

    logger.debug(f"Querying TidyHQ for {cat}{append}")
    try:
        r = requests.get(
            f"https://api.tidyhq.com/v1/{cat}{append}",
            params={"access_token": config["tidyhq"]["token"]},
        )
        data = r.json()
    except requests.exceptions.RequestException as e:
        logger.error("Could not reach TidyHQ")
        sys.exit(1)

    if cat == "groups" and not term:
        # Index groups by ID
        groups_indexed = {}
        for group in data:
            groups_indexed[group["id"]] = group
        return groups_indexed

    return data


def get_emails(config: dict, limit: int = 1000) -> list:
    """Retrieve emails from TidyHQ, broken."""
    emails = []
    offset = 0

    # Calculate date from 3 months ago
    three_months_ago = datetime.datetime.now() - datetime.timedelta(days=90)
    created_since = three_months_ago.strftime("%Y-%m-%dT%H:%M:%S%zZ")

    while len(emails) < limit:
        r = requests.get(
            "https://api.tidyhq.com/v1/emails",
            params={
                "access_token": config["tidyhq"]["token"],
                "way": "outbound",
                "limit": 5,
                "created_since": created_since,
                "offset": offset,
            },
        )
        if r.status_code == 200:
            raw_emails = r.json()
            emails += raw_emails
            offset += 5
            logger.debug(f"Sleeping for 3 seconds")
            time.sleep(3)
        else:
            logger.error(f"Failed to get emails from TidyHQ: {r.status_code}")
            logger.error(r.text)
            logger.error(f"Returning {len(emails)}/{limit} emails")
            break
    return emails


def setup_cache(config: dict) -> dict[str, Any]:
    """Retrieve preset data from TidyHQ and store it in a cache file"""
    logger.info("Cache is being retrieved from TidyHQ")
    cache = {}
    logger.debug("Getting contacts from TidyHQ")
    raw_contacts = query(cat="contacts", config=config)
    logger.debug(f"Got {len(raw_contacts)} contacts from TidyHQ")

    logger.debug("Getting groups from TidyHQ")
    cache["groups"] = query(cat="groups", config=config)

    logger.debug(f'Got {len(cache["groups"])} groups from TidyHQ')

    logger.debug("Getting memberships from TidyHQ")
    cache["memberships"] = query(cat="memberships", config=config)
    logger.debug(f'Got {len(cache["memberships"])} memberships from TidyHQ')

    logger.debug("Getting invoices from TidyHQ")
    raw_invoices = query(cat="invoices", config=config)
    logger.debug(f"Got {len(raw_invoices)} invoices from TidyHQ")

    logger.debug("Getting emails from TidyHQ")
    raw_emails = get_emails(config, limit=1)
    logger.debug(f"Got {len(raw_emails)} emails from TidyHQ")

    logger.debug("Getting org details from TidyHQ")
    cache["org"] = query(cat="organization", config=config)
    logger.debug(f"Org domain is set to {cache['org']['domain_prefix']}")  # type: ignore

    # Trim contact data to just what we need
    cache["contacts"] = []
    useful_fields = [
        "contact_id",
        "custom_fields",
        "first_name",
        "groups",
        "id",
        "last_name",
        "nick_name",
        "status",
        "email_address",
        "phone_number",
        "emergency_contact_number",
        "emergency_contact_person",
    ]

    for contact in raw_contacts:
        trimmed_contact = copy(contact)

        # Get rid of fields we don't need
        for field in contact:
            if field not in useful_fields:
                del trimmed_contact[field]

        cache["contacts"].append(trimmed_contact)

    # Sort invoices by contact ID
    cache["invoices"] = {}
    newest = {}
    for invoice in raw_invoices:
        if invoice["contact_id"] not in cache["invoices"]:
            cache["invoices"][invoice["contact_id"]] = []
            # Convert created_at to unix timestamp
            # Starts in format 2022-12-30T16:36:35+0000
            created_at = datetime.datetime.strptime(
                invoice["created_at"], "%Y-%m-%dT%H:%M:%S%z"
            ).timestamp()

            newest[invoice["contact_id"]] = created_at
        cache["invoices"][invoice["contact_id"]].append(invoice)
        if created_at > newest[invoice["contact_id"]]:
            newest[invoice["contact_id"]] = created_at

    # Remove contacts from the invoice cache if they have no invoices in 18 months
    removed = 0
    cleaned_invoices = {}
    for contact_id in cache["invoices"]:
        if newest[contact_id] > datetime.datetime.now().timestamp() - 86400 * 30 * 18:
            cleaned_invoices[contact_id] = cache["invoices"][contact_id]
        else:
            removed += 1
    logger.debug(
        f"Removed {removed} invoice lists where contact hasn't had an invoice in 18 months"
    )
    logger.debug(f"Left with {len(cleaned_invoices)} contacts with invoices")
    cache["invoices"] = cleaned_invoices

    # Sort invoices in each contact by date
    for contact_id in cache["invoices"]:
        cache["invoices"][contact_id].sort(key=lambda x: x["created_at"], reverse=True)

    # strip emails down to just the recipient and subject
    cache["emails"] = {}
    for email in raw_emails:
        recipients = email["recipient_ids"]

        for recipient in recipients:
            if recipient not in cache["emails"]:
                cache["emails"][recipient] = []
            cache["emails"][recipient].append({"subject": email["subject"]})

    logger.debug(f"Got {len(cache['emails'])} email recipients from TidyHQ")

    logger.debug("Writing cache to file")
    cache["time"] = datetime.datetime.now().timestamp()
    with open("cache.json", "w") as f:
        json.dump(cache, f)

    return cache


def setup_cache_from_tidyproxy(config: dict) -> dict[str, Any]:
    """Retrieve data from tidyproxy"""
    if "tidyproxy" not in config:
        logger.error("No tidyproxy config found")
        sys.exit(1)

    if "url" not in config["tidyproxy"]:
        logger.error("No tidyproxy URL found")
        sys.exit(1)

    url = config["tidyproxy"]["url"]

    if url.endswith("/"):
        url = url[:-1]

    # Check if we have a username specified, if not we'll assume that authentication is handled externally
    if "username" in config["tidyproxy"]:
        auth = (config["tidyproxy"]["username"], config["tidyproxy"]["password"])
        # Get the full cache
        try:
            r = requests.get(url=f"{url}/cache.json", auth=auth)
            cache: dict = r.json()
        except requests.exceptions.RequestException as e:
            logger.error("Could not reach tidyproxy")
            sys.exit(1)
    else:
        # Get the full cache
        try:
            r = requests.get(url=f"{url}/cache.json")
            cache: dict = r.json()
        except requests.exceptions.RequestException as e:
            logger.error("Could not reach tidyproxy")
            sys.exit(1)
    if r.status_code != 200:
        logger.error(f"Failed to get cache from tidyproxy: {r.status_code}")
        sys.exit(1)

    cache = r.json()

    # Write the cache to file
    with open("cache.json", "w") as f:
        json.dump(cache, f)

    return cache


def fresh_cache(cache=None, config=None, force=False) -> dict[str, Any]:
    """Return a fresh TidyHQ cache.

    Freshness is determined by the cache_expiry value in the config file.
    Cache source is (in order of priority):
    - Provided cache
    - Cache file
    - TidyHQ API
    """
    if not config:
        with open("config.json") as f:
            logger.debug("Loading config from file")
            config = json.load(f)

    # Check if the current version of the file has tidyproxy support
    if "tidyproxy" in config:
        logger.info("Using tidyproxy for TidyHQ retrieval")
        retrieval_function = setup_cache_from_tidyproxy
    else:
        logger.info("Using TidyHQ API for TidyHQ retrieval")
        retrieval_function = setup_cache

    if cache:
        # Check if the cache we've been provided with is fresh
        if (
            cache["time"] < datetime.datetime.now().timestamp() - config["cache_expiry"]
            or force
        ):
            logger.debug("Provided cache is stale")
        else:
            # If the provided cache is fresh, just return it
            return cache

    # If we haven't been provided with a cache, or the provided cache is stale, try loading from file
    try:
        with open("cache.json") as f:
            cache = json.load(f)
    except FileNotFoundError:
        logger.debug("No cache file found")
        cache = retrieval_function(config=config)
        return cache
    except json.decoder.JSONDecodeError:
        logger.error("Cache file is invalid")
        cache = retrieval_function(config=config)
        return cache

    # If the cache file is also stale, refresh it
    if (
        cache["time"] < datetime.datetime.now().timestamp() - config["cache_expiry"]
        or force
    ):
        logger.debug("Cache file is stale")
        cache = retrieval_function(config=config)
        return cache
    else:
        logger.debug("Cache file is fresh")
        return cache


def email_to_tidyhq(
    config: dict, tidyhq_cache: dict, taigacon, taiga_auth_token: str, project_id: str
) -> int:
    """Map email addresses to TidyHQ contacts in Taiga user stories and update the stories with the TidyHQ contact ID.

    Searches all TidyHQ contacts, not just those with active memberships.
    """
    # Map email addresses to TidyHQ members
    made_changes = 0

    # Get the list of user stories

    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project_id, tags="bot-managed")
    for story in stories:

        # Fetch custom fields of the story
        custom_attributes_url = f"{config['taiga']['url']}/api/v1/userstories/custom-attributes-values/{story.id}"
        response = requests.get(
            custom_attributes_url,
            headers={"Authorization": f"Bearer {taiga_auth_token}"},
        )

        if response.status_code == 200:
            custom_attributes = response.json().get("attributes_values", {})
            version = response.json().get("version", 0)
            logger.debug(
                f"Fetched custom attributes for story {story.id}: {custom_attributes}"
            )
        else:
            logger.error(
                f"Failed to fetch custom attributes for story {story.id}: {response.status_code}"
            )

        # Skip if no custom attributes
        if custom_attributes == {}:
            logger.debug(f"Story {story.id} has no custom attributes")
            continue

        # Skip if TidyHQ ID already set
        if custom_attributes.get("1", None):
            logger.debug(f"Story {story.id} already has a TidyHQ ID")
            continue

        # Skip if no email address
        if not custom_attributes.get("2", None):
            logger.debug(f"Story {story.id} has no email address")
            continue

        # Get the email address
        email = custom_attributes["2"]
        logger.debug(f"Searching for TidyHQ contact with email: {email}")

        for contact in tidyhq_cache["contacts"]:
            if contact["email_address"] == email:
                logger.info(f"Found TidyHQ contact for {email}")

                # Update the custom field via the Taiga API
                custom_attributes["1"] = contact["id"]
                custom_attributes_url = f"{config['taiga']['url']}/api/v1/userstories/custom-attributes-values/{story.id}"

                updating = taigalink.set_custom_field(
                    config=config,
                    taiga_auth_token=taiga_auth_token,
                    story_id=story.id,
                    field_id=1,
                    value=contact["id"],
                )

                if updating:
                    logger.info(
                        f"Updated story {story.id} with TidyHQ ID {contact['id']}"
                    )
                    made_changes += 1

                else:
                    logger.error(
                        f"Failed to update story {story.id} with TidyHQ ID {contact['id']}"
                    )
                break

    return made_changes


def get_memberships_for_contact(contact_id: str, cache: dict) -> list:
    """Filter memberships to only those for a specific contact."""
    memberships = []
    for membership in cache["memberships"]:
        if str(membership["contact_id"]) == str(contact_id):
            memberships.append(membership)
    return memberships


def get_custom_field(
    config: dict,
    cache: dict,
    contact_id: str | None = None,
    contact: dict | None = None,
    field_id: str | None = None,
    field_map_name: str | None = None,
) -> dict | None:
    """Get the value of a custom field for a contact within TidyHQ.

    The field can be specified by either its ID or its name in the config file.
    """
    if field_map_name:
        logger.debug(f"Looking for field {field_map_name} for contact {contact_id}")
        field_id = config["tidyhq"]["ids"].get(field_map_name, None)
        logger.debug(f"Field ID for {field_map_name} is {field_id}")

    if not field_id:
        logger.error("No field ID provided or found in config")
        return None

    if not contact and contact_id:
        for c in cache["contacts"]:
            if str(c["id"]) == str(contact_id):
                contact = c
                break
    elif not contact and not contact_id:
        logger.error("No contact ID or contact provided")
        return None

    if not contact:
        logger.error(f"Contact {contact_id} not found in cache or we failed to find it")
        return None

    for field in contact["custom_fields"]:
        if field["id"] == field_id:
            logger.info(f"Found field {field_id} with value {field['value']}")
            return field
        else:
            logger.debug(f"Field {field_id} does not match {field['id']}")
    logger.debug(f"Could not find field {field_id} for contact {contact_id}")
    return None


def set_custom_field(
    contact_id: str,
    value: str,
    config: dict,
    field_id: str | None = None,
    field_map_name: str | None = None,
):
    if field_map_name and not field_id:
        field_id = config["tidyhq"]["ids"].get(field_map_name, None)

    if not field_id:
        logger.error("No field ID provided or found in config")
        return False

    logger.debug(f"Setting field {field_id} to {value} for contact {contact_id}")

    r = requests.put(
        f"https://api.tidyhq.com/v1/contacts/{contact_id}",
        params={"access_token": config["tidyhq"]["token"]},
        json={"custom_fields": {field_id: value}},
    )
    if r.status_code != 200:
        logger.error(
            f"Failed to set field {field_id} to {value} for contact {contact_id}"
        )
        return False
    else:
        logger.debug(f"Set field {field_id} to {value} for contact {contact_id}")
        return True


def check_for_groups(
    contact_id: str, tidyhq_cache: dict, groups: list = [], group_string: str = ""
) -> bool:
    """Check if a contact is a member of at least one group or groups."""
    # Get a list of all groups that the contact is a member of

    contact = get_contact(contact_id=contact_id, tidyhq_cache=tidyhq_cache)
    if not contact:
        logger.error(f"Contact {contact_id} not found in cache")
        return False

    raw_groups = contact["groups"]

    logger.debug(f"Got {len(raw_groups)} groups for contact {contact_id}")

    for group in raw_groups:
        if len(groups) > 0:
            if group["id"] in groups:
                return True
        if group_string:
            if group_string in group["label"]:
                return True

    return False


def get_useful_contacts(tidyhq_cache: dict) -> list:
    """Get a list of contacts with active or partial memberships or visitor registrations."""
    useful_contacts = []
    for membership in tidyhq_cache["memberships"]:
        if membership["state"] != "expired":
            useful_contacts.append(membership["contact_id"])

    logger.debug(
        f"Got {len(useful_contacts)} contacts with active or partial memberships"
    )
    return useful_contacts


def get_contact(contact_id: str, tidyhq_cache: dict) -> dict | None:
    """Get a contact by ID from the TidyHQ cache."""
    for contact in tidyhq_cache["contacts"]:
        if contact["id"] == contact_id:
            return contact
    return None


def format_contact(contact: dict) -> str:
    """Format a contact's name for display. Includes first name, last name, and nickname if available.

    Formatted as "First Last (Nickname)".
    """
    if not contact:
        return "Unknown"

    n = ""
    s = ""
    if contact["nick_name"]:
        n = f' ({contact["nick_name"]})'

    # This field is present in the API response regardless of whether the contact has a first or last name. Since the field has a value dict.get won't work as expected.
    if not contact["first_name"]:
        contact["first_name"] = "Unknown"

    if not contact["last_name"]:
        contact["last_name"] = "Unknown"

    return f'{contact.get("first_name","Unknown").capitalize()} {contact.get("last_name","Unknown").capitalize()}{n}{s}'


def return_most_recent_membership(memberships):
    """Return the most recent membership from a list of memberships."""
    memberships.sort(key=lambda x: x["end_date"], reverse=True)
    return memberships[0]


def get_membership_type(contact_id, tidyhq_cache):
    """Returns the type of membership held by a contact.

    One of : "None", "Expired", "Concession", "Full", "Visitor", "Sponsor"
    """
    memberships = get_memberships_for_contact(contact_id, tidyhq_cache)
    if not memberships:
        logger.debug(f"Contact {contact_id} has no memberships")
        return "None"

    most_recent = return_most_recent_membership(memberships)

    # Check if the most recent membership is expired
    if most_recent["state"] == "expired":
        return "Expired"

    elif "Concession" in most_recent["membership_level"]["name"]:
        return "Concession"

    elif "Full" in most_recent["membership_level"]["name"]:
        return "Full"

    elif "Associate" in most_recent["membership_level"]["name"]:
        return "Visitor"

    elif "Sponsor" in most_recent["membership_level"]["name"]:
        return "Sponsor"

    return None


def map_taiga_to_tidyhq(
    tidyhq_cache: dict, taiga_id: str | int, config: dict
) -> str | None:
    """Accepts a Taiga user ID and returns the TidyHQ contact ID if one is found.

    This function is comparatively slow"""

    # Taiga IDs are stored as strings in TidyHQ
    taiga_id = str(taiga_id)

    logger.debug(f"Looking for TidyHQ contact with Taiga ID {taiga_id}")
    for contact in tidyhq_cache["contacts"]:
        taiga_field = get_custom_field(
            config=config,
            contact=contact,
            cache=tidyhq_cache,
            field_map_name="taiga",
        )
        if taiga_field:
            if str(taiga_field["value"]) == taiga_id:
                logger.info(f"Found TidyHQ contact with Taiga ID {taiga_id}")
                return str(contact["id"])
    logger.debug(f"Could not find TidyHQ contact with Taiga ID {taiga_id}")
    return None


def map_tidyhq_to_taiga(
    tidyhq_cache: dict, config: dict, tidyhq_id: str | int
) -> int | None:
    """Accepts a TidyHQ contact ID and returns the Taiga user ID if one is found."""

    logger.debug(f"Looking for Taiga ID for TidyHQ contact {tidyhq_id}")

    tidyhq_id = str(tidyhq_id)

    taiga_id = get_custom_field(
        config=config, contact_id=tidyhq_id, cache=tidyhq_cache, field_map_name="taiga"
    )

    if taiga_id:
        logger.info(
            f"Found Taiga ID {taiga_id['value']} for TidyHQ contact {tidyhq_id}"
        )
        return int(taiga_id["value"])
    else:
        logger.debug(f"Could not find Taiga ID for TidyHQ contact {tidyhq_id}")
        return None


def map_taiga_to_slack(
    tidyhq_cache: dict, taiga_id: str | int, config: dict
) -> str | None:
    """Map Taiga user IDs to Slack user IDs."""
    # Get the TidyHQ contact ID from the Taiga user ID

    logger.debug(f"Looking for Slack ID for Taiga user {taiga_id}")

    tidyhq_id = map_taiga_to_tidyhq(tidyhq_cache, taiga_id, config)

    if not tidyhq_id:
        return None

    # Look for a Slack ID
    slack_id = get_custom_field(
        config=config, contact_id=tidyhq_id, cache=tidyhq_cache, field_map_name="slack"
    )

    if slack_id:
        logger.info(f"Found Slack ID {slack_id['value']} for Taiga user {taiga_id}")
        return slack_id["value"]
    else:
        logger.debug(f"Could not find Slack ID for Taiga user {taiga_id}")
        return None


def map_slack_to_taiga(tidyhq_cache: dict, slack_id: str, config: dict) -> int | None:
    """Map Slack user IDs to Taiga user IDs."""

    # Get the TidyHQ contact ID from the Slack user ID

    logger.debug(f"Looking for Taiga ID for Slack user {slack_id}")

    tidyhq_id = map_slack_to_tidyhq(tidyhq_cache, slack_id, config)

    if not tidyhq_id:
        return None

    # Look for a Taiga ID
    taiga_id = get_custom_field(
        config=config, contact_id=tidyhq_id, cache=tidyhq_cache, field_map_name="taiga"
    )

    if taiga_id:
        logger.info(f"Found Taiga ID {taiga_id['value']} for Slack user {slack_id}")
        return int(taiga_id["value"])
    else:
        logger.debug(f"Could not find Taiga ID for Slack user {slack_id}")
        return None


def map_slack_to_tidyhq(tidyhq_cache: dict, slack_id: str, config: dict) -> str | None:
    """Map Slack user IDs to TidyHQ contact IDs."""

    logger.debug(f"Looking for TidyHQ contact with Slack ID {slack_id}")

    # Look for a TidyHQ ID with the matching Slack ID
    for contact in tidyhq_cache["contacts"]:
        slack_field = get_custom_field(
            config=config,
            contact=contact,
            cache=tidyhq_cache,
            field_map_name="slack",
        )
        if slack_field:
            if slack_field["value"] == slack_id:
                logger.info(f"Found TidyHQ contact with Slack ID {slack_id}")
                return str(contact["id"])

    logger.debug(f"Could not find TidyHQ contact with Slack ID {slack_id}")
    return None
