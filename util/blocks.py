divider = [{"type": "divider"}]
text = [{"type": "section", "text": {"type": "mrkdwn", "text": ""}}]
context = [
    {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": ""}],
    }
]
quote = [
    {
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_quote",
                "elements": [
                    {
                        "type": "text",
                        "text": "",
                    }
                ],
            }
        ],
    }
]
header = [{"type": "header", "text": {"type": "plain_text", "text": "", "emoji": True}}]

accessory_image = {"type": "image", "image_url": "", "alt_text": ""}

button = {"type": "button", "text": {"type": "plain_text", "text": ""}}

actions = [{"type": "actions", "block_id": "button_actions", "elements": []}]
