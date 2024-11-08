import json
import sys


def sync_templates(taigacon):
    # Load a list of past actions
    try:
        with open("template_actions.json") as f:
            actions = json.load(f)
    except FileNotFoundError:
        actions = {}

    projects = taigacon.projects.list()

    # Attendee board is the first project
    project = projects[0]

    # Check the name of the project
    if project.name != "Attendee":
        sys.exit(1)

    # Find template stories
    templates = {}

    # Iterate over the project's user stories
    stories = taigacon.user_stories.list(project=project.id)
    for story in stories:
        # Check if the story is a template story
        if story.subject == "Template":
            # Get the tasks for the template story
            raw_tasks = taigacon.tasks.list(user_story=story.id)
            tasks = []
            for task in raw_tasks:
                tasks.append({"status": task.status, "subject": task.subject})

            templates[story.status] = tasks

    # Find all user stories that include our bot managed tag
    for story in stories:
        tagged = False
        for tag in story.tags:
            if tag[0] == "bot-managed":
                print(f"Story {story.subject} includes the tag 'bot-managed'")
                tagged = True

        if not tagged:
            continue

        # Check if we have already created tasks for this story in the current state

        if str(story.id) in actions:
            if str(story.status) in actions[str(story.id)]:
                print(
                    f"Tasks for story {story.subject} already created in state {story.status}"
                )
                continue

        # Check if we have a template for this type of story
        if story.status not in templates:
            print(f"No template for story {story.subject}")
            continue

        print(f"Found template for story {story.subject}")
        template = templates[story.status]
        for task in template:
            print(f"Creating task {task['subject']} with status {task['status']}")
            taigacon.tasks.create(
                project=project.id,
                user_story=story.id,
                status=task["status"],
                subject=task["subject"],
            )

        if str(story.id) not in actions:
            actions[str(story.id)] = []
        actions[str(story.id)].append(str(story.status))

        # Update our saved actions
        with open("template_actions.json", "w") as f:
            json.dump(actions, f)
