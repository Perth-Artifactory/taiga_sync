import importlib
import logging
from pprint import pprint

import slack_bolt as bolt

from editable_resources import forms
from util import misc
import taiga

# Set up logging
logger = logging.getLogger("slack.forms")


def form_submission_to_description(
    submission: dict, slack_app: bolt.App
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
            # Only add the filenames to the answer if there were actually files uploaded
            if filenames:
                answer = f"Files uploaded: {', '.join(filenames)} (see attachments)"
            else:
                answer = "Files not uploaded"

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

        # Radio buttons
        # These are just a single value so we can just grab the value
        elif value["type"] == "radio_buttons":
            if not value["selected_option"]:
                answer = "Question not answered"
            elif value["selected_option"]["value"]:
                answer = value["selected_option"]["value"]
            else:
                answer = "Question not answered"

        # Checkboxes
        # These come in as a list of values so we'll just join them
        elif value["type"] == "checkboxes":
            if not value["selected_options"]:
                answer = "Question not answered"
            else:
                answer = ", ".join(
                    [option["value"] for option in value["selected_options"]]
                )

        # Every other type of question
        # This definitely isn't exhaustive but we'll just add support as required rather than implementing everything now
        else:
            pprint(value)
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
    submission: dict, taigacon: taiga.TaigaAPI, taiga_cache: dict, form_name: str
) -> tuple[int, int | None, int | None]:
    """Extracts the Taiga project ID and mapped type/severity if applicable.

    Returns project_id, type, severity
    """

    # Reload forms from file
    importlib.reload(forms)

    form = forms.forms[form_name]

    # Check if the form had a question marked as a taiga type map
    taiga_type_question = False
    taiga_severity_question = False
    for question in form["questions"]:
        if question.get("taiga_type"):
            taiga_type_question = question.get(
                "action_id", misc.hash_question(question["text"])
            )
        if question.get("taiga_severity"):
            taiga_severity_question = question.get(
                "action_id", misc.hash_question(question["text"])
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
        project_id = taiga_cache["projects"]["by_name_with_extra"].get(
            form["taiga_project"]
        )
        if not project_id:
            raise ValueError(
                f"Could not find project with name {form['taiga_project']}"
            )

    if project_id:
        if taiga_type_str:
            taiga_types: dict = taiga_cache["boards"][project_id]["types"]
            for current_type_id, taiga_type in taiga_types.items():
                if taiga_type["name"].lower() == taiga_type_str.lower():
                    taiga_type_id = current_type_id
                    break
        if taiga_severity_str:
            taiga_severities = taiga_cache["boards"][project_id]["severities"]
            for current_severity_id, taiga_severity in taiga_severities:
                if taiga_severity["name"].lower() == taiga_severity_str.lower():
                    taiga_severity_id = current_severity_id
                    break

    return project_id, taiga_type_id, taiga_severity_id
