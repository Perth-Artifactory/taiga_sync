# Questions that can be added to forms

contact = {
    "type": "static_dropdown",
    "text": "Would you like us to contact you regarding the outcome of this report?",
    "options": ["Yes", "No"],
    "action_id": "contact",
}

# Forms

# Injury/near miss
injury = [
    {"text": "Please answer the following questions to the best of your ability."},
    {
        "type": "long",
        "text": "What happened?",
        "placeholder": "In the event of a near miss, what was the potential outcome?",
    },
    {"type": "multi_users_select", "text": "Who was involved?"},
    {"type": "date", "text": "When did it happen?"},
    {
        "type": "short",
        "text": "Where in the space did the incident occur?",
        "placeholder": "e.g. Machine Room, Project Area etc",
    },
    {"type": "multi_users_select", "text": "Did anyone witness the incident?"},
    {
        "type": "long",
        "text": "Were there any injuries?",
        "placeholder": "Include a description of injuries if applicable",
    },
    {
        "type": "long",
        "text": "Was there any damage to property?",
        "placeholder": "e.g. tools, equipment, buildings, personal belongings",
    },
    {
        "type": "long",
        "text": "What factors contributed to the incident?",
        "placeholder": "e.g. environmental conditions, equipment failure, human error",
    },
    {
        "type": "long",
        "text": "Were there any immediate corrective actions taken at the time of the incident?",
        "placeholder": "e.g. first aid, stopping work, isolating equipment",
    },
    {
        "type": "long",
        "text": "What controls could be put in place to prevent this from happening again?",
        "placeholder": "e.g. training, signage, engineering controls",
    },
    contact,
]
