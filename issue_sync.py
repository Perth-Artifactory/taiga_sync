import requests
import logging
import json
import sys
from slack_bolt import App
from datetime import datetime
from pprint import pprint, pformat
import time

from taiga import TaigaAPI

from util import slack, taigalink

# Set up logging
logging.basicConfig(level=logging.INFO)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
# Set slack bolt logging level to INFO to reduce noise when individual modules are set to debug
slack_logger = logging.getLogger("slack")
slack_logger.setLevel(logging.INFO)
setup_logger = logging.getLogger("setup")
logger = logging.getLogger("issue_sync")

# Check for testing mode
testing = False
if "--testing" in sys.argv:
    testing = True
    logger.info("Running in test mode")

# Load config
try:
    with open("config.json") as f:
        config = json.load(f)
except FileNotFoundError:
    setup_logger.error(
        "config.json not found. Create it using example.config.json as a template"
    )
    sys.exit(1)

# Get auth token for Taiga
# This is used instead of python-taiga's inbuilt user/pass login method since we also need to interact with the api directly
auth_url = f"{config['taiga']['url']}/api/v1/auth"
auth_data = {
    "password": config["taiga"]["password"],
    "type": "normal",
    "username": config["taiga"]["username"],
}
response = requests.post(
    auth_url, headers={"Content-Type": "application/json"}, data=json.dumps(auth_data)
)

if response.status_code == 200:
    taiga_auth_token = response.json().get("auth_token")
else:
    setup_logger.error(f"Failed to get auth token: {response.status_code}")
    sys.exit(1)

taigacon = TaigaAPI(host=config["taiga"]["url"], token=taiga_auth_token)

# Get the project ID
projects = taigacon.projects.list()
attendee_project = None
for project in projects:
    if project.name == "Infrastructure":
        infrastructure_project = project
        break

# Get a list of all users in the project
raw_users = taigacon.users.list(project=infrastructure_project.id)
taiga_users = {}
for user in raw_users:
    taiga_users[user.full_name] = user
setup_logger.debug(f"Found {len(taiga_users)} users in the project")

# Initiate Slack client
app = App(token=config["slack"]["bot_token"], logger=slack_logger)

# Get our user ID
ID = app.client.auth_test()["user_id"]
setup_logger.info(f"Connected to Slack with user ID {ID}")

# Get conversation history
result = app.client.conversations_history(channel=config["slack"]["issue_channel"])

conversation_history = result["messages"]

count = 0
for message in conversation_history:
    # Check if the message is a message from the workflow
    if message["subtype"] != "bot_message":
        logger.debug(f"Skipping message {message['ts']} as it is not a bot message")
        continue

    # Check if we've reacted
    if "reactions" in message.keys():
        reacted = False
        for reaction in message["reactions"]:
            if reaction["name"] == "heavy_check_mark" and ID in reaction["users"]:
                reacted = True
                break
        if reacted and not testing:
            logger.debug(f"Already reacted to message {message['ts']}")
            continue
    count += 1

    # Get the variables from the message
    variables = slack.extract_message_vars(message=message)
    logger.debug(f"Variables extracted from message {message['ts']}:")
    logger.debug(pformat(variables))

    # Pull out Slack IDs before we start modifying the description
    reporter_id = variables.get("Reporter", None)
    discussed_with_id = variables.get(
        "Have you reported the incident to a volunteer in person?", ""
    ).split(",")
    discussed_with_id = [id.strip() for id in discussed_with_id if id.strip() != ""]

    # Construct the task description
    description = f"User report: {variables.get('Describe the issue', 'No description provided')}\n\n"
    description += f"Discussed with: {slack.name_mapper(variables.get('Have you reported the incident to a volunteer in person?', 'No one'), slack_app=app)}\n\n"
    description += f"Tagged out: {variables.get('Have you tagged out the affected item?', 'No')}\n\n"
    description += f"Reporter wants to be contacted: {variables.get('Do you want to be contacted regarding this report?', 'No')}\n\n"
    description += f"Reporter: {slack.name_mapper(slack_id=variables.get('Reporter', 'Unknown'), slack_app=app)}\n\n"

    # Look for watchers in discussed with field
    watchers = []
    involved = []
    for user in taiga_users:
        if user in description:
            watchers.append(taiga_users[user].id)
            involved.append(user)
            logger.debug(
                f"Adding {user} to watchers as the issue was discussed with them"
            )
            logger.debug(
                f"Due to a bug in the Taiga API this doesn't actually work ...yet"
            )

    # Tag mentioned users in the description
    description = taigalink.map_slack_names_to_taiga_usernames(
        input_string=description, taiga_users=taiga_users
    )

    # Create the issue on the board
    logger.info("Creating issue on Taiga")
    success = taigalink.create_issue(
        taiga_auth_token=taiga_auth_token,
        project_id=None,
        config=config,
        description=description,
        severity_str=variables.get("How severe is the issue?", "Not found in message"),
        board_str=variables.get(
            "What type of fault would you like to report?", "Not found in message"
        ),
        watchers=watchers,
        taigacon=taigacon,
    )
    if success:
        logger.info("Issue created successfully")
    else:
        logger.error("Failed to create issue on Taiga")
        logger.error("Retrieved variables:")
        logger.error(variables)
        continue

    # TODO Figure out if the entry IDs are always four off
    # They're not :(
    issue_url = taigalink.create_link_to_entry(
        config=config,
        taiga_auth_token=taiga_auth_token,
        entry_id=success + 4,
        project_id=None,
        project_str=infrastructure_project.slug,
        entry_type="issue",
    )

    # Send a DM to the reporter
    if reporter_id:

        message_str = f"Thank you for reporting an issue to the Infrastructure team. We have created a ticket on Taiga to track this issue. You can view it <{issue_url}|here>."
        slack.send_dm(slack_id=reporter_id, message=message_str, slack_app=app)

    # Remove the reporter from the list of people to notify
    if reporter_id in discussed_with_id:
        discussed_with_id.remove(reporter_id)

    # Send a DM to everyone else who was mentioned
    for user in discussed_with_id:
        message_str = f"""An issue has been reported to the Infrastructure team by <@{reporter_id}> and you were mentioned as someone who was involved in the discussion.
        
        You can view the issue <{issue_url}|here>. If you feel there's more information or context you can add to the report please do so."""
        slack.send_dm(slack_id=user, message=message_str, slack_app=app)

    # Add a reaction to the message
    if not testing:
        logger.info(f"Reacting to message {message['ts']}")
        app.client.reactions_add(
            channel=config["slack"]["issue_channel"],
            name="heavy_check_mark",
            timestamp=message["ts"],
        )
    else:
        logger.info("Test mode: skipping reaction")

logger.info(f"Finished processing messages {count}/{len(conversation_history)}")
