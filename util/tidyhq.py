import datetime
import json
import logging
import sys
from copy import deepcopy as copy
from typing import Any, Literal

import requests


def query(
    cat: str | int,
    config: dict,
    term: str | Literal[None] = None,
    cache: dict | Literal[None] = None,
):
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


def setup_cache(config) -> dict[str, Any]:
    cache = {}
    logging.debug("Getting contacts from TidyHQ")
    raw_contacts = query(cat="contacts", config=config)
    logging.debug(f"Got {len(raw_contacts)} contacts from TidyHQ")

    logging.debug("Getting groups from TidyHQ")
    cache["groups"] = query(cat="groups", config=config)

    logging.debug(f'Got {len(cache["groups"])} groups from TidyHQ')

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

        # Get rid of custom fields we don't need
        useful_custom_fields = []
        for field in trimmed_contact["custom_fields"]:
            if field["id"] in config["tidyhq"]["ids"].values():
                useful_custom_fields.append(field)
        trimmed_contact["custom_fields"] = useful_custom_fields

        cache["contacts"].append(trimmed_contact)

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


def email_to_tidyhq(config, tidyhq_cache, taigacon, taiga_auth_token, project_id):
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

                response = requests.patch(
                    custom_attributes_url,
                    headers={"Authorization": f"Bearer {taiga_auth_token}"},
                    json={
                        "attributes_values": {"1": contact["id"], "2": "See TidyHQ"},
                        "version": version,
                    },
                )

                if response.status_code == 200:
                    logging.info(
                        f"Updated story {story.id} with TidyHQ ID {contact['id']}"
                    )
                    made_changes = True

                else:
                    logging.error(
                        f"Failed to update story {story.id} with TidyHQ ID {contact['id']}: {response.status_code}"
                    )
                    logging.error(response.json())
                break

    return made_changes
