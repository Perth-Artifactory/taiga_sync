import logging

from util import taigalink, tidyhq


def pull_tidyhq(config, tidyhq_cache, taigacon, taiga_auth_token, project_id):
    made_changes = False
    # Get a list of TidyHQ contacts that should have cards

    contacts = tidyhq.get_useful_contacts(tidyhq_cache=tidyhq_cache)

    story_contacts = []
    # Get a list of stories with TidyHQ contacts attached that are bot managed
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

        story_contacts.append(tidyhq_id)

    # Find the contacts that are in TidyHQ but not in stories
    for contact in contacts:
        if contact not in story_contacts:
            logging.debug(f"Contact {contact} has not been attached to a story")

            # Create a new story for the contact
            story = taigacon.user_stories.create(
                project=project_id,
                subject=tidyhq.format_contact(
                    contact=tidyhq.get_contact(
                        contact_id=contact, tidyhq_cache=tidyhq_cache
                    ),  # type: ignore
                    config=config,
                ),
                tags=["bot-managed"],
                status=1,
            )
            made_changes = True
            logging.debug(f"Created story {story.subject} for contact {contact}")

            # Set the TidyHQ ID for the story
            taigalink.set_custom_field(
                config=config,
                taiga_auth_token=taiga_auth_token,
                story_id=story.id,
                field_id=1,
                value=contact,
            )
            logging.debug(
                f"Set TidyHQ ID {contact} for story {story.subject} to {contact}"
            )

    return made_changes
