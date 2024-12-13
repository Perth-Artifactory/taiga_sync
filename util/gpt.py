import openai
import logging
from pprint import pprint

for module in ["httpcore", "openai"]:
    logging.getLogger(module).setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


def generate_tasks(
    subject: str,
    description: str,
    existing_tasks: list,
    attachments: list,
    client: openai.OpenAI,
) -> list:

    task_strs = "\n"
    for task in existing_tasks:
        task_strs += f"{task['subject']} ({task['status']})\n"

    if task_strs == "\n":
        task_strs = "None"

    attachment_urls = []
    for attachment in attachments:
        if attachment["name"].lower().endswith((".png", ".jpg", ".jpeg")):
            attachment_urls.append(attachment["url"])

    prompt = f"""
    Based on the following issue:

    Subject: {subject}
    Description: {description}
    Existing Tasks: {task_strs}

    Please generate a list of actionable tasks to address this issue.
    """

    content = [
        {
            "type": "text",
            "text": prompt,
        },
    ]

    if attachment_urls:
        prompt += "A few pictures were also provided:"
        image_messages = []
        for url in attachment_urls:
            image_messages.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": url,
                    },
                }
            )
        content += image_messages

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are the project manager for a small makerspace. You have a new issue to address. Respond only with a list of tasks to address the issue below. Do not include any other information. Task lists should be clear, concise, and actionable. Task list should be formatted with the task on each line with no title or numbering. You may respond with up to 10 tasks.",
            },
            {"role": "user", "content": content},  # type: ignore
        ],
    )

    # Extract and print the generated tasks
    message = response.choices[0].message.content

    if not message:
        return []
    if "I'm sorry, I can't assist with that" in message:
        logging.critical(
            "GPT was asked to generate tasks for a story that was likely illegal in some way."
        )
        return []
    generated_tasks = []
    for line in message.split("\n"):
        line = line.strip()
        if line.endswith("."):
            line = line[:-1]
        # Remove anything but a alphanumeric character from the beginning of the line
        while not line[0].isalnum():
            line = line[1:]
        if line:
            generated_tasks.append(line)

    return generated_tasks
