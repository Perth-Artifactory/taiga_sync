from util import blocks, slack_formatters
from copy import deepcopy as copy
import hashlib
from pprint import pprint
import logging
from datetime import datetime

# Set up logging
logger = logging.getLogger("slack_forms")


def text_to_options(options: list[str]):
    """
    Convert a list of strings to a list of option dictionaries
    """
    if len(options) > 10:
        logger.warning(
            f"Too many options ({len(options)}) for radio buttons. Truncating to 10"
        )
        options = options[:10]

    formatted_options = []
    for option in options:
        if len(option) > 150:
            logger.warning(
                f"Option '{option}' is too long for value to be set. Truncating to 150 characters"
            )
            option = option[:150]
        formatted_options.append(copy(blocks.option))
        formatted_options[-1]["text"]["text"] = option
        formatted_options[-1]["value"] = option

    return formatted_options


def questions_to_blocks(questions: list[dict]):
    """
    Convert a list of questions to a list of blocks
    """
    block_list = []

    for question in questions:

        # Some fields will break if they're included but are empty, so we'll remove them now
        for key in ["placeholder", "text", "action_id"]:
            if key in question:
                if not question[key]:
                    question.pop(key)
                    logger.warning(f"Empty {key} field removed from question")

        # Check if we're just adding an explainer
        if "text" in question and len(question) == 1:
            block_list += blocks.text
            block_list = slack_formatters.inject_text(
                block_list=block_list,
                text=question.get("text", "This is some default text!"),
            )

        # Check if we're adding a short or long question field
        elif question["type"] in ["short", "long"]:
            if "text" not in question:
                raise ValueError("Short question must have a text field")
            if type(question["text"]) != str:
                raise ValueError("Short question text must be a string")
            block_list = slack_formatters.add_block(block_list, blocks.text_question)
            block_list[-1]["label"]["text"] = question.get("text")
            # Get a md5 hash of the question text to use as the action_id

            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", hash_question(question["text"])
            )

            # This is the only difference between short and long questions
            if question["type"] == "long":
                block_list[-1]["element"]["multiline"] = True

            # Add optional placeholder
            if "placeholder" in question:
                block_list[-1]["element"]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                block_list[-1]["element"].pop("placeholder")

        # Check if we're adding radio buttons
        elif question["type"] == "radio":
            if "options" not in question:
                logger.info(
                    "No options provided for radio question, defaulting to Y/N/NA"
                )
                question["options"] = ["Yes", "No", "Not applicable"]
            options = text_to_options(question["options"])
            block_list = slack_formatters.add_block(block_list, blocks.radio_buttons)
            block_list[-1]["label"]["text"] = question.get("text", "Choose an option")
            block_list[-1]["element"]["options"] = options
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", hash_question(question["text"])
            )

        # Check if we're adding a static dropdown menu
        elif question["type"] == "static_dropdown":
            block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
            block_list[-1]["label"]["text"] = question.get("text", "Choose an option")
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", hash_question(question["text"])
            )

            # Add options
            if "options" not in question:
                logger.info(
                    "No options provided for dropdown question, defaulting to Y/N/NA"
                )
                question["options"] = ["Yes", "No", "Not applicable"]
            options = text_to_options(question["options"])
            block_list[-1]["element"]["options"] = options

            # Add optional placeholder
            if "placeholder" in question:
                block_list[-1]["element"]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                block_list[-1]["element"].pop("placeholder")

        # Check if we're adding a multi slack user select
        elif question["type"] == "multi_users_select":
            block_list = slack_formatters.add_block(
                block_list, blocks.multi_users_select
            )
            block_list[-1]["label"]["text"] = question.get("text", "Choose a user")
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", hash_question(question["text"])
            )

            # Placeholders are optional
            if "placeholder" in question:
                block_list[-1]["element"]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                pprint(block_list[-1])
                block_list[-1]["element"].pop("placeholder")

        # Check if we're adding a date select
        elif question["type"] == "date":
            # The action block doesn't include a label, we can add one manually if required
            if "text" in question:
                block_list = slack_formatters.add_block(block_list, blocks.text)
                block_list = slack_formatters.inject_text(
                    block_list=block_list, text=question.get("text", "Choose a date")
                )

            block_list = slack_formatters.add_block(block_list, blocks.date_select)

            # Setting an action id is somewhat difficult here since there's valid situations where no text has been provided

            if "action_id" in question:
                block_list[-1]["elements"][0]["action_id"] = question["action_id"]
            elif "text" in question:
                block_list[-1]["elements"][0]["action_id"] = hash_question(
                    question["text"]
                )
            elif "placeholder" in question:
                block_list[-1]["elements"][0]["action_id"] = hash_question(
                    question["placeholder"]
                )
            else:
                block_list[-1]["elements"][0]["action_id"] = hash_question(
                    "Choose a date"
                )
                logger.warning(
                    "No action_id provided for date question, using default action_id, if you do this for two dates there will be issues!"
                )

            # Slack prioritises the initial date over the placeholder so we'll do the same

            # If we have an initial date set, use it
            if "initial_date" in question:
                # Validate the date by turning it into a datetime object
                try:
                    datetime.strptime(question["initial_date"], "%Y-%m-%d")
                except ValueError:
                    raise ValueError(
                        "Invalid date format for initial_date. Dates should be in the format YYYY-MM-DD"
                    )
                block_list[-1]["elements"][0]["initial_date"] = question["initial_date"]
            elif "placeholder" in question:
                block_list[-1]["elements"][0]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                # If we don't have a placeholder we'll use todays date
                block_list[-1]["elements"][0]["initial_date"] = datetime.now().strftime(
                    "%Y-%m-%d"
                )
                # We'll also remove the placeholder present in the base blocks object
                block_list[-1]["elements"][0].pop("placeholder")

        else:
            raise ValueError("Invalid question type")

    return block_list


def hash_question(question_text: str) -> str:

    # strip non alphanumeric/space characters
    question_text = "".join(
        char for char in question_text if char.isalnum() or char.isspace()
    )

    # strip leading/trailing whitespace
    question_text = question_text.strip()

    return hashlib.md5(question_text.encode()).hexdigest()
