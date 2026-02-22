"""Configuration loaded from environment variables / .env file."""

import base64
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


# Garmin credentials
GARMIN_EMAIL = _require("GARMIN_EMAIL")
_raw_pw = os.getenv("GARMIN_PASSWORD_B64", "")
if _raw_pw:
    GARMIN_PASSWORD = base64.b64decode(_raw_pw).decode()
else:
    GARMIN_PASSWORD = _require("GARMIN_PASSWORD")

# Token persistence directory
GARMIN_TOKEN_DIR = os.getenv("GARMIN_TOKEN_DIR", str(Path.home() / ".garminconnect"))

# PostgreSQL
PG_HOST = os.getenv("PG_HOST", "192.168.30.104")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DATABASE = os.getenv("PG_DATABASE", "garmin")
PG_USER = os.getenv("PG_USER", "garmin")
PG_PASSWORD = _require("PG_PASSWORD")

PG_DSN = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"

# Sync settings
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "300"))  # seconds
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "Europe/Warsaw")

# Manual bulk import (set both to enable, then exit after import)
MANUAL_START_DATE = os.getenv("MANUAL_START_DATE", "")  # YYYY-MM-DD
MANUAL_END_DATE = os.getenv("MANUAL_END_DATE", "")  # YYYY-MM-DD

# Comma-separated list of data types to fetch (empty = all)
FETCH_SELECTION = os.getenv("FETCH_SELECTION", "")
