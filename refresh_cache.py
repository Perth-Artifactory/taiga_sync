import logging
from util import tidyhq
import json

# Set up logging
logging.basicConfig(level=logging.INFO)
# Set urllib3 logging level to INFO to reduce noise when individual modules are set to debug
urllib3_logger = logging.getLogger("urllib3")
urllib3_logger.setLevel(logging.INFO)
# Set slack bolt logging level to INFO to reduce noise when individual modules are set to debug
logger = logging.getLogger("refresh")

# Load config
try:
    with open("config.json") as f:
        config = json.load(f)
except FileNotFoundError:
    setup_logger.error(
        "config.json not found. Create it using example.config.json as a template"
    )
    sys.exit(1)

# Check for required TidyHQ config values
if not all(
    key in config["tidyhq"] for key in ["token"]
):
    setup_logger.error(
        "Missing required config values in tidyhq section. Check config.json"
    )
    sys.exit(1)

# Override cache expiry
config["cache_expiry"] = 60
logger.info("Cache expiry overwritten to 60 seconds")

# Set up TidyHQ cache
tidyhq_cache = tidyhq.fresh_cache(config=config)
logger.info(
    f"TidyHQ cache set up: {len(tidyhq_cache['contacts'])} contacts, {len(tidyhq_cache['groups'])} groups"
)
