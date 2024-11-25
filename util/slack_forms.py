from util import blocks, slack_formatters
from copy import deepcopy as copy
import hashlib
from pprint import pprint
import logging
from datetime import datetime
from editable_resources import forms
from util import taigalink

# Set up logging
logger = logging.getLogger("slack_forms")


def text_to_options(options: list[str]):
    """Convert a list of strings to a list of option dictionaries"""
    if len(options) > 10:
        logger.warning(f"Too many options ({len(options)}). Truncating to 10")
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


def render_form_list(form_list: dict, member=False) -> list[dict]:
    """Takes a list of forms and renders them as a list of blocks"""
    block_list = []
    block_list = slack_formatters.add_block(block_list, blocks.text)
    block_list = slack_formatters.inject_text(
        block_list=block_list, text="Please select a form to fill out:"
    )
    unavailable_forms = []
    for form_id in form_list:
        form = form_list[form_id]
        if form["members_only"] and not member:
            unavailable_forms.append(form_id)
            continue
        block_list = slack_formatters.add_block(block_list, blocks.header)
        block_list = slack_formatters.inject_text(
            block_list=block_list,
            text=f'{form["title"]}{":artifactory:" if form["members_only"] else ""}',
        )
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=form["description"]
        )
        # Add a button to fill out the form as an attachment
        accessory = copy(blocks.button)
        accessory["text"]["text"] = form["action_name"]
        accessory["value"] = form_id
        accessory["action_id"] = f"form-open-{form_id}"

        block_list[-1]["accessory"] = accessory

    if unavailable_forms:
        block_list = slack_formatters.add_block(block_list, blocks.divider)
        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list,
            text="We were unable to associate a membership with your Slack account so the following forms are unavailable:",
        )
        unavailable_form_str = ""
        for form_id in unavailable_forms:
            form = form_list[form_id]
            unavailable_form_str += f"• {form['title']}\n"

        block_list = slack_formatters.add_block(block_list, blocks.text)
        block_list = slack_formatters.inject_text(
            block_list=block_list, text=unavailable_form_str
        )
        block_list = slack_formatters.add_block(block_list, blocks.context)
        block_list = slack_formatters.inject_text(
            block_list=block_list,
            text="If you believe this is an error please reach out to #it",
        )
    return block_list


def questions_to_blocks(
    questions: list[dict], taigacon, taiga_project: str | None = None
) -> list[dict]:
    """Convert a list of questions to a list of blocks"""
    block_list = []

    taiga_project_id = None
    if taiga_project:
        taiga_project_id = taigalink.item_mapper(
            item=taiga_project,
            field_type="project",
            project_id=None,
            taiga_auth_token="",
            config={},
            taigacon=taigacon,
        )
        if not taiga_project_id:
            raise ValueError(f"Could not find project with name {taiga_project}")

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

            if question.get("optional"):
                block_list[-1]["optional"] = True

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
            if question.get("optional"):
                block_list[-1]["optional"] = True

        # Check if we're adding a static dropdown menu
        elif question["type"] == "static_dropdown":
            block_list = slack_formatters.add_block(block_list, blocks.static_dropdown)
            block_list[-1]["label"]["text"] = question.get("text", "Choose an option")
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", hash_question(question["text"])
            )

            # Taiga mapping
            if question.get("taiga_map") in ["type", "severity"] and taiga_project_id:
                # This question will be used to map to a Taiga field
                need_query = True
                if question.get("options"):
                    if not taigalink.validate_form_options(
                        project_id=taiga_project_id,
                        option_type=question.get("taiga_map", "type"),
                        options=question.get("options", ["invalid"]),
                        taigacon=taigacon,
                    ):
                        logger.warning(
                            f"Invalid options for {question.get('taiga_map', 'type')} mapping"
                        )
                        logger.warning(f"Options: {options}")
                        logger.warning("Using all available options instead")
                        need_query = True
                    else:
                        need_query = False
                else:
                    need_query = True

                if need_query:
                    if question.get("taiga_map") == "type":
                        raw_options = taigacon.issue_types.list(
                            project=taiga_project_id
                        )
                    elif question.get("taiga_map") == "severity":
                        raw_options = taigacon.severities.list(project=taiga_project_id)
                    question["options"] = [option.name for option in raw_options]

            # Add fallback options
            if "options" not in question and question.get("taiga_map") not in [
                "type",
                "severity",
            ]:
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

            if question.get("optional"):
                block_list[-1]["optional"] = True

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
                block_list[-1]["element"].pop("placeholder")
            if question.get("optional"):
                block_list[-1]["optional"] = True

        # Check if we're adding a date select
        elif question["type"] == "date":
            block_list = slack_formatters.add_block(block_list, blocks.date_select)
            block_list[-1]["label"]["text"] = question.get("text", "Choose a date")
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", hash_question(question["text"])
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
                block_list[-1]["element"]["initial_date"] = question["initial_date"]
            elif "placeholder" in question:
                block_list[-1]["element"]["placeholder"]["text"] = question[
                    "placeholder"
                ]
            else:
                # If we don't have a placeholder we'll use todays date
                block_list[-1]["element"]["initial_date"] = datetime.now().strftime(
                    "%Y-%m-%d"
                )
                # We'll also remove the placeholder present in the base blocks object
                block_list[-1]["element"].pop("placeholder")

            if question.get("optional"):
                block_list[-1]["optional"] = True

        # Check if we're adding a file upload
        elif question["type"] == "file":
            block_list = slack_formatters.add_block(block_list, blocks.file_input)
            block_list[-1]["label"]["text"] = question.get("text", "Upload a file")
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", hash_question(question["text"])
            )

            if question.get("optional"):
                block_list[-1]["optional"] = True

            if question.get("file_type"):
                if type(question["file_type"]) != list:
                    raise ValueError("File type must be a list of strings")
                block_list[-1]["element"]["filetypes"] = question["file_type"]

            if question.get("max_files"):
                if type(question["max_files"]) != int:
                    raise ValueError("Max files must be an integer")
                if 11 < question["max_files"] < 1:
                    raise ValueError("Max files must be between 1 and 10")
                block_list[-1]["element"]["max_files"] = question

        # Check if we're adding checkboxes
        elif question["type"] == "checkboxes":
            if "options" not in question:
                raise ValueError("Checkbox question must have options")
            options = text_to_options(question["options"])
            block_list = slack_formatters.add_block(block_list, blocks.checkboxes)
            block_list[-1]["label"]["text"] = question.get("text", "Choose an option")
            block_list[-1]["element"]["options"] = options
            block_list[-1]["element"]["action_id"] = question.get(
                "action_id", hash_question(question["text"])
            )
            if question.get("optional"):
                block_list[-1]["optional"] = True

        else:
            raise ValueError("Invalid question type")

    return block_list


def hash_question(question_text: str) -> str:
    """Converts a string into a hash for use as a repeatable but unique action_id"""

    # strip non alphanumeric/space characters
    question_text = "".join(
        char for char in question_text if char.isalnum() or char.isspace()
    )

    # strip leading/trailing whitespace
    question_text = question_text.strip()

    return hashlib.md5(question_text.encode()).hexdigest()


def form_submission_to_description(
    submission: dict, slack_app
) -> tuple[str, list[str]]:
    """Convert a form submission to a string description and list of files to download"""
    description = ""
    files = []
    # Get the order of the questions from the submission, the blocks here are ordered but the values later on aren't
    order = []
    questions = {}
    for block in submission["view"]["blocks"]:
        if block["type"] == "input":
            order.append(block["block_id"])
            questions[order[-1]] = block

    # Iterate over the provided values to render "answers"
    for block in order:
        question = questions[block]["label"]["text"]
        answer = ""
        # There's always only one dict. I've split the value calculation here to make it easier to read
        value = submission["view"]["state"]["values"][block]
        value = value[list(value.keys())[0]]

        # The key for submitted values differs depending on the type of question because Slack
        # TODO refactor this with a map of question types to value keys

        # Static dropdowns
        if value["type"] == "static_select":
            if not value["selected_option"]:
                answer = "Question not answered"
            elif value["selected_option"]["value"]:
                answer = value["selected_option"]["value"]

        # File uploads
        # These are handled differently since we need to download the files later
        elif value["type"] == "file_input":
            filenames = []
            for singlefile in value["files"]:
                files.append(singlefile["url_private_download"])
                filenames.append(singlefile["title"])
            answer = f"Files uploaded: {', '.join(filenames)} (see attachments)"

        # User mentions
        # We get the name here for easy reading within Taiga but still include the Slack ID incase we want to replace it with Taiga @s later or something
        elif value["type"] == "multi_users_select":
            if not value["selected_users"]:
                answer = "Question not answered"
            else:
                answer = ""
                for user in value["selected_users"]:
                    user = slack_app.client.users_info(user=user)
                    name_str = user["user"]["profile"].get(
                        "real_name", user["user"]["profile"]["display_name"]
                    )
                    slack_id = user["user"]["id"]
                    answer += f"{name_str} ({slack_id}), "
                answer = answer[:-2]

        # Date picker
        # No sense in converting this to a datetime since it's just going to be a string in Taiga
        # Could include a delta from now if we wanted to be fancy
        # TODO add delta from now
        elif value["type"] == "datepicker":
            if not value["selected_date"]:
                answer = "Question not answered"
            else:
                answer = value["selected_date"]

        # Every other type of question
        # This definitely isn't exhaustive but we'll just add support as required rather than implementing everything now
        else:
            answer = value["value"]

        description += f"**{question}**\n{answer}\n\n"

    # Get the user who submitted the form
    user = slack_app.client.users_info(user=submission["user"]["id"])

    # Format the name to match the version used by issue submissions
    # This way it should already support customised webhook notifications
    name_str = user["user"]["profile"].get(
        "real_name", user["user"]["profile"]["display_name"]
    )
    slack_id = user["user"]["id"]
    by = f"{name_str} ({slack_id})"

    description = f"{description}\n\nAdded to Taiga by: {by}"
    return description, files


def form_submission_to_metadata(
    submission: dict, taigacon
) -> tuple[int, int | None, int | None]:
    """Extracts the Taiga project ID and mapped type/severity if applicable.

    Returns project_id, type, severity
    """

    # Retrieve the original form
    form_id = submission["view"]["private_metadata"]
    form = forms.forms[form_id]

    # Check if the form had a question marked as a taiga type map
    taiga_type_question = False
    taiga_severity_question = False
    for question in form["questions"]:
        if question.get("taiga_type"):
            taiga_type_question = question.get(
                "action_id", hash_question(question["text"])
            )
        if question.get("taiga_severity"):
            taiga_severity_question = question.get(
                "action_id", hash_question(question["text"])
            )

    # Iterate over the submission and look for answers to the relevant questions
    taiga_type_str = None
    taiga_severity_str = None
    for block_id in submission["view"]["state"]["values"]:
        question_hash = list(submission["view"]["state"]["values"][block_id].keys())[0]
        if question_hash == taiga_type_question:
            taiga_type_str = submission["view"]["state"]["values"][block_id][
                question_hash
            ]["value"]
        elif question_hash == taiga_severity_question:
            taiga_severity_str = submission["view"]["state"]["values"][block_id][
                question_hash
            ]["value"]

    project_id = None
    taiga_type_id = None
    taiga_severity_id = None
    try:
        project_id = int(form["taiga_project"])
    except:
        # Query taiga for projects
        projects = taigacon.projects.list()
        for project in projects:
            if project.name.lower() == form["taiga_project"].lower():
                project_id = project.id
                break
        else:
            raise ValueError(
                f"Could not find project with name {form['taiga_project']}"
            )

    if project_id:
        if taiga_type_str:
            taiga_types = taigacon.issue_types.list(project=project_id)
            for taiga_type in taiga_types:
                if taiga_type.name.lower() == taiga_type_str.lower():
                    taiga_type_id = taiga_type.id
                    break
        if taiga_severity_str:
            taiga_severities = taigacon.severities.list()
            for taiga_severity in taiga_severities:
                if taiga_severity.name.lower() == taiga_severity_str.lower():
                    taiga_severity_id = taiga_severity.id
                    break

    return project_id, taiga_type_id, taiga_severity_id
