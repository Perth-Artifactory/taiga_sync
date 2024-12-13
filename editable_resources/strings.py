import json

# Load config
with open("config.json") as f:
    config: dict = json.load(f)

unrecognised = (
    """Welcome to Taiga! Unfortunately I don't recognise you. It could be because:"""
)
unrecognised_no_tidyhq = """• You do not have a TidyHQ account linked to your Slack account. If you hold a membership with us, please reach out to #it."""
unrecognised_no_taiga = f"""• You're not signed up for Taiga yet. <{config['taiga']['url']}/register|Sign up here>."""
unrecognised_no_taiga_match = """• Your email address in Taiga doesn't match the one you use for TidyHQ. Reach out to #it if this is the case."""
header = "Artifactory Issue Tracker"
do_instead = (
    """Below are a list of items that are assigned to the whiteboard instead!"""
)
version = "Version: {branch}/{commit}-{platform}"
footer = (
    """This app is in constant development. If you have any feedback or suggestions, please reach out to #it. | """
    + version
)
explainer = """This page can be used to track tasks you're assigned to across all projects in our issue tracker."""
no_tasks = """No tasks to display, try adjusting your filters"""
no_stories = """No stories to display, try adjusting your filters"""
no_issues = """No issues to display, try adjusting your filters"""
trimmed = f"""Unfortunately I can only show you the first {{items}} items. If you need to see more, please visit the <{config['taiga']['url']}|issue tracker> directly."""
compressed = "(Some formatting has also been removed/compressed)"
form_submission_success = "Your form has been submitted successfully: {form_name}"
file_upload_failure = "Unfortunately there was an issue uploading your attached file(s). A volunteer will be in touch shortly."
newline = "\n"
view_only = "Either you don't have permission to edit this item or I do not recognise you. If you believe this is an error, please reach out to #it."
