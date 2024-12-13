import openai
import logging

for module in ["httpcore", "openai"]:
    logging.getLogger(module).setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


def generate_tasks(subject: str, description: str, client: openai.OpenAI) -> list:
    prompt = f"""
    Based on the following issue:

    Subject: {subject}
    Description: {description}

    Please generate a list of actionable tasks to address this issue.
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are the project manager for a small makerspace. You have a new issue to address. Respond only with a list of tasks to address the issue below. Do not include any other information. Task lists should be clear, concise, and actionable. Task list should be formatted with the task on each line with no title or numbering. You may respond with up to 10 tasks.",
            },
            {"role": "user", "content": prompt},
        ],
    )

    # Extract and print the generated tasks
    message = response.choices[0].message.content

    if not message:
        return []
    generated_tasks = []
    for line in message.split("\n"):
        line = line.strip()
        if line.endswith("."):
            line = line[:-1]
        if line:
            generated_tasks.append(line)

    return generated_tasks
