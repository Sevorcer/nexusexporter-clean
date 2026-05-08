import logging
import os
import secrets

from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is required. "
            f"Copy .env.example to .env and fill it in, or export the variable. "
            f"See README.md section 'Quick start' for details."
        )
    return value


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(48)
if not os.environ.get("SECRET_KEY"):
    logging.warning(
        "SECRET_KEY not set; generated an ephemeral one. Sessions will not survive restarts. "
        "Set SECRET_KEY in .env for any non-trivial deployment."
    )

DISCORD_CLIENT_ID = _require_env("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = _require_env("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = _require_env("DISCORD_REDIRECT_URI")
DISCORD_BOT_CLIENT_ID = os.environ.get("DISCORD_BOT_CLIENT_ID", "").strip() or None

MAX_MADDEN_LEAGUE_ID_LENGTH = 64
MAX_BOT_CLIENT_ID_LENGTH = 32
COMPANION_JSON_FORM_KEYS = ("payload", "data", "body", "json")
COMPANION_DEBUG_PREVIEW_LIMIT = 1000
COMPANION_DEBUG_LOG_ENABLED = os.environ.get("COMPANION_DEBUG_LOG", "").strip().lower() in {"1", "true"}
DEV_TRAIT_MAP = {0: "Normal", 1: "Star", 2: "Superstar", 3: "X-Factor"}

logging.basicConfig(level=logging.INFO)
companion_logger = logging.getLogger("companion_ingest")
