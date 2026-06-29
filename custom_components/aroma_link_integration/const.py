"""Constants for the Aroma-Link integration."""

DOMAIN = "aroma_link_integration"

# Configuration
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"
CONF_DIFFUSE_TIME = "diffuse_time"
CONF_WORK_DURATION = "work_duration"
CONF_POLL_INTERVAL = "poll_interval"
CONF_DEBUG_LOGGING = "debug_logging"
CONF_VERIFY_SSL = "verify_ssl"
CONF_ALLOW_SSL_FALLBACK = "allow_ssl_fallback"

# Default values
DEFAULT_DIFFUSE_TIME = 60  # seconds
DEFAULT_WORK_DURATION = 10  # seconds
DEFAULT_PAUSE_DURATION = 900  # seconds (15 minutes)
DEFAULT_POLL_INTERVAL_SECONDS = 60  # Default: 60 seconds (1 minute)
MIN_POLL_INTERVAL_SECONDS = 5  # Minimum: 5 seconds (use with caution!)
MAX_POLL_INTERVAL_SECONDS = 900  # Maximum: 15 minutes
DEFAULT_DEBUG_LOGGING = False
DEFAULT_VERIFY_SSL = True
DEFAULT_ALLOW_SSL_FALLBACK = True

# Services
SERVICE_SET_SCHEDULER = "set_scheduler"
SERVICE_RUN_DIFFUSER = "run_diffuser"
SERVICE_LOAD_WORKSET = "load_workset"
SERVICE_SAVE_WORKSET = "save_workset"

# Attributes
ATTR_DURATION = "duration"
ATTR_DIFFUSE_TIME = "diffuse_time"
ATTR_WORK_DURATION = "work_duration"
ATTR_PAUSE_DURATION = "pause_duration"
ATTR_WEEK_DAYS = "week_days"

# SSL Configuration
# Default SSL verification setting (per-entry override supported).
VERIFY_SSL = True
