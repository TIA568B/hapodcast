"""Constants for the Daily Podcast Queue integration."""

DOMAIN = "daily_podcasts"

# Config keys
CONF_PLAYER = "player"
CONF_PODCASTS = "podcasts"
CONF_NAME = "name"
CONF_FEED_URL = "feed_url"
CONF_TIMEZONE = "timezone"
CONF_FETCH_TIMEOUT = "fetch_timeout"
CONF_HISTORY_DIR = "history_dir"
CONF_RECORD_ONLY = "record_only"

# Defaults
DEFAULT_FETCH_TIMEOUT = 20
DEFAULT_HISTORY_DIR = "daily_podcasts_history"
DEFAULT_RECORD_ONLY = False

# Services
SERVICE_BUILD_QUEUE = "build_queue"
SERVICE_PLAY_HISTORY = "play_history"

# Service call fields
ATTR_PLAYER = "player"
ATTR_TZ = "tz"
ATTR_DRY_RUN = "dry_run"
ATTR_RECORD_ONLY = "record_only"
ATTR_DATE = "date"
ATTR_START = "start"
ATTR_END = "end"
ATTR_SINCE = "since"
ATTR_DAYS = "days"

# Music Assistant service used to queue media.
MASS_DOMAIN = "mass"
MASS_PLAY_MEDIA = "play_media"

# Date format for history filenames.
DATE_FMT = "%Y-%m-%d"

# Log prefix
LOG_PREFIX = "[daily_podcasts]"
