import datetime
import json
import logging
import sys
import time
from copy import deepcopy as copy
from pprint import pprint
from typing import Any, Literal

import requests

from util import taigalink


def query(
    cat: str | int,
    config: dict,
    term: str | None = None,
    cache: dict | None = None,
) -> dict | list:
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
                    logging.debug(f"Could not find group with ID {term} in cache")
                else:
                    return cache["groups"]
            elif cat == "contacts":
                if term:
                    for contact in cache["contacts"]:
                        if int(contact["id"]) == int(term):
                            return contact
                    # If we can't find the contact, handle via query
                    logging.debug(f"Could not find contact with ID {term} in cache")
                else:
                    return cache["contacts"]
        else:
            logging.debug(f"Could not find category {cat} in cache")

    append = ""
    if term:
        append = f"/{term}"

    logging.debug(f"Querying TidyHQ for {cat}{append}")
    try:
        r = requests.get(
            f"https://api.tidyhq.com/v1/{cat}{append}",
            params={"access_token": config["tidyhq"]["token"]},
        )
        data = r.json()
    except requests.exceptions.RequestException as e:
        logging.error("Could not reach TidyHQ")
        sys.exit(1)

    if cat == "groups" and not term:
        # Index groups by ID
        groups_indexed = {}
        for group in data:
            groups_indexed[group["id"]] = group
        return groups_indexed

    return data


def get_emails(config: dict, limit: int = 1000) -> list:
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
            logging.debug(f"Sleeping for 3 seconds")
            time.sleep(3)
        else:
            logging.error(f"Failed to get emails from TidyHQ: {r.status_code}")
            logging.error(r.text)
            logging.error(f"Returning {len(emails)}/{limit} emails")
            break
    return emails


def setup_cache(config: dict) -> dict[str, Any]:
    cache = {}
    logging.debug("Getting contacts from TidyHQ")
    raw_contacts = query(cat="contacts", config=config)
    logging.debug(f"Got {len(raw_contacts)} contacts from TidyHQ")

    logging.debug("Getting groups from TidyHQ")
    cache["groups"] = query(cat="groups", config=config)

    logging.debug(f'Got {len(cache["groups"])} groups from TidyHQ')

    logging.debug("Getting memberships from TidyHQ")
    cache["memberships"] = query(cat="memberships", config=config)
    logging.debug(f'Got {len(cache["memberships"])} memberships from TidyHQ')

    logging.debug("Getting invoices from TidyHQ")
    raw_invoices = query(cat="invoices", config=config)
    logging.debug(f"Got {len(raw_invoices)} invoices from TidyHQ")

    logging.debug("Getting emails from TidyHQ")
    raw_emails = get_emails(config, limit=1)
    logging.debug(f"Got {len(raw_emails)} emails from TidyHQ")

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
    logging.debug(
        f"Removed {removed} invoice lists where contact hasn't had an invoice in 18 months"
    )
    logging.debug(f"Left with {len(cleaned_invoices)} contacts with invoices")
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

    logging.debug(f"Got {len(cache['emails'])} email recipients from TidyHQ")

    logging.debug("Writing cache to file")
    cache["time"] = datetime.datetime.now().timestamp()
    with open("cache.json", "w") as f:
        json.dump(cache, f)

    return cache


def fresh_cache(cache=None, config=None, force=False) -> dict[str, Any]:
    if not config:
        with open("config.json") as f:
            logging.debug("Loading config from file")
            config = json.load(f)

    if cache:
        # Check if the cache we've been provided with is fresh
        if (
            cache["time"] < datetime.datetime.now().timestamp() - config["cache_expiry"]
            or force
        ):
            logging.debug("Provided cache is stale")
        else:
            # If the provided cache is fresh, just return it
            return cache

    # If we haven't been provided with a cache, or the provided cache is stale, try loading from file
    try:
        with open("cache.json") as f:
            cache = json.load(f)
    except FileNotFoundError:
        logging.debug("No cache file found")
        cache = setup_cache(config=config)
        return cache
    except json.decoder.JSONDecodeError:
        logging.error("Cache file is invalid")
        cache = setup_cache(config=config)
        return cache

    # If the cache file is also stale, refresh it
    if (
        cache["time"] < datetime.datetime.now().timestamp() - config["cache_expiry"]
        or force
    ):
        logging.debug("Cache file is stale")
        cache = setup_cache(config=config)
        return cache
    else:
        logging.debug("Cache file is fresh")
        return cache


def email_to_tidyhq(
    config: dict, tidyhq_cache: dict, taigacon, taiga_auth_token: str, project_id: str
) -> bool:
    # Map email addresses to TidyHQ members
    made_changes = False

    # Get the list of user stories

    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project_id)
    for story in stories:
        # Check if the story is managed by us
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                logging.debug(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True

        if not tagged:
            continue

        # Fetch custom fields of the story
        custom_attributes_url = f"{config['taiga']['url']}/api/v1/userstories/custom-attributes-values/{story.id}"
        response = requests.get(
            custom_attributes_url,
            headers={"Authorization": f"Bearer {taiga_auth_token}"},
        )

        if response.status_code == 200:
            custom_attributes = response.json().get("attributes_values", {})
            version = response.json().get("version", 0)
            logging.debug(
                f"Fetched custom attributes for story {story.id}: {custom_attributes}"
            )
        else:
            logging.error(
                f"Failed to fetch custom attributes for story {story.id}: {response.status_code}"
            )

        # Skip if no custom attributes
        if custom_attributes == {}:
            logging.debug(f"Story {story.id} has no custom attributes")
            continue

        # Skip if TidyHQ ID already set
        if custom_attributes.get("1", None):
            logging.debug(f"Story {story.id} already has a TidyHQ ID")
            continue

        # Skip if no email address
        if not custom_attributes.get("2", None):
            logging.debug(f"Story {story.id} has no email address")
            continue

        # Get the email address
        email = custom_attributes["2"]
        logging.debug(f"Searching for TidyHQ contact with email: {email}")

        for contact in tidyhq_cache["contacts"]:
            if contact["email_address"] == email:
                logging.info(f"Found TidyHQ contact for {email}")

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
                    logging.info(
                        f"Updated story {story.id} with TidyHQ ID {contact['id']}"
                    )
                    made_changes = True

                else:
                    logging.error(
                        f"Failed to update story {story.id} with TidyHQ ID {contact['id']}"
                    )
                break

    return made_changes


def get_memberships_for_contact(contact_id: str, cache: dict) -> list:
    memberships = []
    for membership in cache["memberships"]:
        if membership["contact_id"] == contact_id:
            memberships.append(membership)
    return memberships


def get_custom_field(
    config: dict,
    contact_id: str,
    cache: dict,
    field_id: str | None = None,
    field_map_name: str | None = None,
) -> str | None:
    if field_map_name:
        field_id = config["tidyhq"]["ids"].get(field_map_name, None)

    if not field_id:
        logging.error("No field ID provided or found in config")
        return None

    for contact in cache["contacts"]:
        if contact["id"] == contact_id:
            for field in contact["custom_fields"]:
                if field_id:
                    if field["id"] == field_id:
                        return field
    return None


def check_for_groups(
    contact_id: str, tidyhq_cache: dict, groups: list = [], group_string: str = ""
) -> bool:
    # Get a list of all groups that the contact is a member of

    for contact in tidyhq_cache["contacts"]:
        if contact["id"] == contact_id:
            raw_groups = contact["groups"]
            break
    else:
        logging.error(f"Contact {contact_id} not found in cache")
        return False

    logging.debug(f"Got {len(raw_groups)} groups for contact {contact_id}")

    for group in raw_groups:
        if len(groups) > 0:
            if group["id"] in groups:
                return True
        if group_string:
            if group_string in group["label"]:
                return True

    return False


def get_useful_contacts(tidyhq_cache: dict) -> list:
    useful_contacts = []
    for membership in tidyhq_cache["memberships"]:
        if membership["state"] != "expired":
            useful_contacts.append(membership["contact_id"])

    logging.debug(
        f"Got {len(useful_contacts)} contacts with active or partial memberships"
    )
    return useful_contacts


def get_contact(contact_id: str, tidyhq_cache: dict) -> dict | None:
    for contact in tidyhq_cache["contacts"]:
        if contact["id"] == contact_id:
            return contact
    return None


def format_contact(contact: dict) -> str:
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
