"""Constants for the Daily Podcast Queue integration."""

DOMAIN = "daily_podcasts"

# Config / option keys
CONF_PLAYER = "player"
CONF_PODCASTS = "podcasts"
CONF_NAME = "name"
CONF_FEED_URL = "feed_url"
CONF_WEEKEND_CATCHUP = "weekend_catchup"  # per-podcast: Mon includes Sat+Sun
CONF_TIMEZONE = "timezone"
CONF_FETCH_TIMEOUT = "fetch_timeout"
CONF_HISTORY_DIR = "history_dir"
CONF_AT = "at"  # daily "prepare playlist" time, "HH:MM:SS"
CONF_ENABLED = "enabled"  # daily prepare on/off

# Defaults
DEFAULT_FETCH_TIMEOUT = 20
DEFAULT_HISTORY_DIR = "daily_podcasts_history"
DEFAULT_AT = "06:00:00"
DEFAULT_ENABLED = True
DEFAULT_WEEKEND_CATCHUP = True

# Options-flow menu step ids
STEP_INIT = "init"
STEP_SETTINGS = "settings"
STEP_ADD = "add_podcast"
STEP_EDIT = "edit_podcast"
STEP_EDIT_PICK = "edit_pick"
STEP_REMOVE = "remove_podcast"
STEP_MOVE_UP = "move_up"
STEP_MOVE_DOWN = "move_down"

# Services
SERVICE_BUILD_QUEUE = "build_queue"
SERVICE_PLAY_HISTORY = "play_history"

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

# Music Assistant service used to queue media.
MASS_DOMAIN = "mass"
MASS_PLAY_MEDIA = "play_media"

# Date format for history filenames.
DATE_FMT = "%Y-%m-%d"

# Log prefix
LOG_PREFIX = "[daily_podcasts]"
