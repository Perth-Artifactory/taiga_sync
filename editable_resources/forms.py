# Questions that can be added to forms

contact = {
    "type": "static_dropdown",
    "text": "Would you like us to contact you regarding the outcome of this report?",
    "options": ["Yes", "No"],
    "action_id": "contact",
}

tagged_out = {
    "type": "static_dropdown",
    "text": "Has the equipment/tool been tagged out?",
    "action_id": "tagged_out",
}

# Question sets

# Broken 3d printer questions
broken_printer_questions = [
    {"text": "Please answer the following questions to the best of your ability."},
    {
        "type": "static_dropdown",
        "text": "Which printer has the issue?",
        "options": [
            "Bambu Labs P1S",
            "Bambu Labs X1",
            "Bambu Labs A1",
            "Bambu Labs A1-Mini",
            "Prusa Mk4",
            "Anycubic Mono X",
            "Phrozen Mega 8K",
            "Wash/Cure Station",
            "Filament Dryer",
            "Other/General Issue",
        ],
        "action_id": "printer",
        "optional": True,
    },
    {
        "type": "long",
        "text": "Describe the issue",
        "optional": True,
    },
    {"type": "file", "text": "Upload photos of the issue", "optional": True},
    tagged_out,
    contact,
]


# Injury/near miss
injury_questions = [
    {"text": "Please answer the following questions to the best of your ability."},
    {
        "type": "long",
        "text": "What happened?",
        "placeholder": "In the event of a near miss, what was the potential outcome?",
        "optional": True,
    },
    {
        "type": "multi_users_select",
        "text": "Who was involved?",
        "optional": True,
    },
    {"type": "date", "text": "When the incident occur?", "optional": True},
    {
        "type": "short",
        "text": "Where in the space did the incident occur?",
        "placeholder": "e.g. Machine Room, Project Area etc",
        "optional": True,
    },
    {
        "type": "multi_users_select",
        "text": "Did anyone witness the incident?",
        "optional": True,
    },
    {
        "type": "long",
        "text": "Were there any injuries?",
        "placeholder": "Include a description of injuries if applicable",
        "optional": True,
    },
    {
        "type": "long",
        "text": "Was there any damage to property?",
        "placeholder": "e.g. tools, equipment, buildings, personal belongings",
        "optional": True,
    },
    {
        "type": "long",
        "text": "What factors contributed to the incident?",
        "placeholder": "e.g. environmental conditions, equipment failure, human error",
        "optional": True,
    },
    {
        "type": "long",
        "text": "Were there any immediate corrective actions taken at the time of the incident?",
        "placeholder": "e.g. first aid, stopping work, isolating equipment",
        "optional": True,
    },
    {
        "type": "long",
        "text": "What controls could be put in place to prevent this from happening again?",
        "placeholder": "e.g. training, signage, engineering controls",
        "optional": True,
    },
    {"type": "static_dropdown", "text": "Srs?", "taiga_map": "severity"},
    contact,
]

# Forms

# Injury/near miss
injury = {
    "title": "Injury/Near Miss Report",
    "description": "Report an injury or near miss within the workshop",
    "questions": injury_questions,
    "members_only": False,
    "action_name": "Report",
    "taiga_project": "taiga",
    "taiga_issue_title": "New Injury/Near Miss Report",
}

# Broken 3d printer
broken_printer = {
    "title": "Broken 3D Printer",
    "description": "Report a broken/misbehaving 3D printer",
    "questions": broken_printer_questions,
    "members_only": False,
    "action_name": "Report",
    "taiga_project": "taiga",
    "taiga_issue_title": "New 3D Printer Report",
}

# Collection of all forms

forms = {"injury": injury, "3d": broken_printer}
