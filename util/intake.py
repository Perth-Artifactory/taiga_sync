import logging

import taiga

from util import taigalink, tidyhq

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def pull_tidyhq(
    config: dict,
    tidyhq_cache: dict,
    taigacon: taiga.TaigaAPI,
    taiga_auth_token: str,
    project_id: str,
) -> int:
    """Return a list of TidyHQ contact IDs that do not have cards but should.

    Contacts with memberships/visitor registrations that have not expired should have cards.
    """
    made_changes = 0

    contacts = tidyhq.get_useful_contacts(tidyhq_cache=tidyhq_cache)

    story_contacts = []
    # Get a list of stories with TidyHQ contacts attached that are bot managed
    stories = taigacon.user_stories.list(project=project_id, tags="bot-managed")
    for story in stories:
        # Retrieve the TidyHQ ID for the story
        tidyhq_id = taigalink.get_tidyhq_id(
            story_id=story.id, taiga_auth_token=taiga_auth_token, config=config
        )

        story_contacts.append(tidyhq_id)

    # Find the contacts that are in TidyHQ but not in stories
    for contact in contacts:
        if contact not in story_contacts:
            logger.debug(f"Contact {contact} has not been attached to a story")

            # Create a new story for the contact
            story = taigacon.user_stories.create(
                project=project_id,
                subject=tidyhq.format_contact(
                    contact=tidyhq.get_contact(
                        contact_id=contact, tidyhq_cache=tidyhq_cache
                    ),  # type: ignore
                ),
                tags=["bot-managed"],
                status=2,
            )
            made_changes += 1
            logger.debug(f"Created story {story.subject} for contact {contact}")

            # Set the TidyHQ ID for the story
            taigalink.set_custom_field(
                config=config,
                taiga_auth_token=taiga_auth_token,
                story_id=story.id,
                field_id=1,
                value=contact,
            )
            logger.debug(
                f"Set TidyHQ ID {contact} for story {story.subject} to {contact}"
            )

    return made_changes
