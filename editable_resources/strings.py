unrecognised = """Welcome to Taiga! Unfortunately I don't recognise you. It could be because:

• You're not signed up for Taiga yet. <https://tasks.artifactory.org.au/register|Sign up here>.
• Your email address in Taiga doesn't match the one you use for Slack (Reach out to #it if this is the case)"""
header = "Artifactory Issue Tracker"
do_instead = """Here are some things you can do in the meantime:
• Have a look at what our <https://tasks.artifactory.org.au/project/infrastructure/kanban|Infrastructure> team is up to.
• See what's up with the <https://tasks.artifactory.org.au/project/lasers/kanban|laser cutters>."""
version = "Version: {branch}/{commit}-{platform}"
footer = (
    """This app is in constant development. If you have any feedback or suggestions, please reach out to #it. | """
    + version
)
explainer = """This page can be used to track tasks you're assigned to across all projects in our issue tracker."""
no_tasks = """You don't have any tasks assigned to you at the moment."""
no_stories = """You don't have any cards assigned to you at the moment."""
no_issues = """You don't have any issues assigned to you at the moment."""
trimmed = """Unfortunately I can only show you the first {items} items. If you need to see more, please visit the <https://tasks.artifactory.org.au|issue tracker> directly."""
compressed = "(Some formatting has also been removed/compressed)"
form_submission_success = "Your form has been submitted successfully: {form_name}"
file_upload_failure = "Unfortunately there was an issue uploading your attached file(s). A volunteer will be in touch shortly."
newline = "\n"
view_only = "Either you don't have permission to edit this item or I do not recognise you. If you believe this is an error, please reach out to #it."
