"""Constants for the Daily Podcast Queue integration."""

import json
from pathlib import Path

DOMAIN = "daily_podcasts"

# Integration version, read from manifest.json (single source of truth).
try:
    _MANIFEST = json.loads(
        (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
    )
    INTEGRATION_VERSION = _MANIFEST.get("version", "0.0.0")
except Exception:  # noqa: BLE001
    INTEGRATION_VERSION = "0.0.0"

# Sidebar panel + management service names.
SERVICE_LIST_PODCASTS = "list_podcasts"
SERVICE_SET_PODCASTS = "set_podcasts"

# Config / option keys
CONF_PLAYER = "player"
CONF_PODCASTS = "podcasts"
CONF_NAME = "name"
CONF_FEED_URL = "feed_url"
# Per-podcast: catch up everything since the last successful prepare (vs just
# today). Replaces the old `weekend_catchup` flag (migrated on read).
CONF_CATCHUP = "catchup"
CONF_WEEKEND_CATCHUP = "weekend_catchup"  # legacy key, migrated to CONF_CATCHUP
CONF_TIMEZONE = "timezone"
CONF_FETCH_TIMEOUT = "fetch_timeout"
CONF_HISTORY_DIR = "history_dir"
CONF_MAX_LOOKBACK_DAYS = "max_lookback_days"
CONF_AT = "at"  # daily "prepare playlist" time, "HH:MM:SS"
CONF_ENABLED = "enabled"  # daily prepare on/off

# Defaults
DEFAULT_FETCH_TIMEOUT = 20
DEFAULT_HISTORY_DIR = "daily_podcasts_history"
DEFAULT_AT = "06:00:00"
DEFAULT_ENABLED = True
DEFAULT_CATCHUP = True
DEFAULT_MAX_LOOKBACK_DAYS = 18

# Per-podcast high-water mark storage (helpers.storage.Store).
HWM_STORAGE_KEY = "daily_podcasts_hwm"
HWM_STORAGE_VERSION = 1

# Options-flow step id (single settings form; podcasts are managed from the
# "Daily Podcasts" sidebar panel).
STEP_INIT = "init"

# Services
SERVICE_BUILD_QUEUE = "build_queue"
SERVICE_PLAY_HISTORY = "play_history"
# Overview panel services.
SERVICE_GET_QUEUE = "get_queue"
SERVICE_SKIP_TO = "skip_to"

# Service call fields
ATTR_PLAYER = "player"
ATTR_TZ = "tz"
ATTR_DRY_RUN = "dry_run"
ATTR_PLAY = "play"
ATTR_DATE = "date"
ATTR_START = "start"
ATTR_END = "end"
ATTR_SINCE = "since"
ATTR_DAYS = "days"
ATTR_POSITION = "position"  # 0-based queue index for skip_to

# Music Assistant service used to queue media.
MASS_DOMAIN = "mass"
MASS_PLAY_MEDIA = "play_media"

# Date format for history filenames.
DATE_FMT = "%Y-%m-%d"

# Log prefix
LOG_PREFIX = "[daily_podcasts]"
