"""Constants for the Aroma-Link integration."""

DOMAIN = "aroma_link_integration"

# The one bus event: fired on every model / sync / timed-run / oil / gating
# change. Payload: {device_id, change, version}.
EVENT_UPDATED = f"{DOMAIN}_updated"

# Configuration
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"
CONF_POLL_INTERVAL = "poll_interval"
CONF_DEBUG_LOGGING = "debug_logging"
CONF_VERIFY_SSL = "verify_ssl"
CONF_ALLOW_SSL_FALLBACK = "allow_ssl_fallback"

# Per-device gating options (entry.options["gates"][device_id])
CONF_GATES = "gates"
CONF_CLIMATE_ENTITY = "climate_entity"
CONF_OCCUPANCY_ENTITY = "occupancy_entity"
CONF_MOTION_ENTITIES = "motion_entities"
CONF_HVAC_ON_DELAY = "hvac_on_delay_minutes"

# Default values
DEFAULT_WORK_DURATION = 10  # seconds
DEFAULT_PAUSE_DURATION = 900  # seconds (15 minutes)
DEFAULT_POLL_INTERVAL_SECONDS = 60  # Default: 60 seconds (1 minute)
MIN_POLL_INTERVAL_SECONDS = 5  # Minimum: 5 seconds (use with caution!)
MAX_POLL_INTERVAL_SECONDS = 900  # Maximum: 15 minutes
DEFAULT_DEBUG_LOGGING = False
DEFAULT_VERIFY_SSL = True
DEFAULT_ALLOW_SSL_FALLBACK = True
DEFAULT_HVAC_ON_DELAY_MINUTES = 1

# Services
SERVICE_START_TIMED_RUN = "start_timed_run"
SERVICE_CANCEL_TIMED_RUN = "cancel_timed_run"
SERVICE_SYNC_SCHEDULES = "sync_schedules"
SERVICE_OIL_REFILL = "oil_refill"
SERVICE_OIL_CALIBRATE = "oil_calibrate"
SERVICE_API_DIAGNOSTICS = "api_diagnostics"

# SSL Configuration
# Default SSL verification setting (per-entry override supported).
VERIFY_SSL = True
