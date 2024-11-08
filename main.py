import json
import sys
from pprint import pprint

from taiga import TaigaAPI

from util import templates

# Load config
with open("config.json") as f:
    config = json.load(f)

taigacon = TaigaAPI(
    host=config["taiga"]["url"],
)

taigacon.auth(
    username=config["taiga"]["username"], password=config["taiga"]["password"]
)

# Sync templates
templates.sync_templates(taigacon=taigacon)
