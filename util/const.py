base_filter = {
    "closed_filter": {
        "closed_filter": {
            "selected_options": [
                {
                    "text": {"emoji": True, "text": "Open", "type": "plain_text"},
                    "value": "open",
                }
            ],
            "type": "checkboxes",
        }
    },
    "project_filter": {
        "project_filter": {
            "selected_options": [
                {
                    "text": {
                        "emoji": True,
                        "text": "All " "projects",
                        "type": "plain_text",
                    },
                    "value": "all",
                }
            ],
            "type": "multi_static_select",
        }
    },
    "related_filter": {
        "related_filter": {
            "selected_options": [
                {
                    "text": {"emoji": True, "text": "Watched", "type": "plain_text"},
                    "value": "watched",
                },
                {
                    "text": {"emoji": True, "text": "Assigned", "type": "plain_text"},
                    "value": "assigned",
                },
            ],
            "type": "checkboxes",
        }
    },
    "type_filter": {
        "type_filter": {
            "selected_options": [
                {
                    "text": {
                        "emoji": True,
                        "text": "User " "Stories",
                        "type": "plain_text",
                    },
                    "value": "story",
                },
                {
                    "text": {"emoji": True, "text": "Issues", "type": "plain_text"},
                    "value": "issue",
                },
                {
                    "text": {"emoji": True, "text": "Tasks", "type": "plain_text"},
                    "value": "task",
                },
            ],
            "type": "checkboxes",
        }
    },
}
