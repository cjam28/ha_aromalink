"""The Aroma-Link integration."""
import logging
import os

from .AromaLinkAuthCoordinator import AromaLinkAuthCoordinator
from .AromaLinkDeviceCoordinator import AromaLinkDeviceCoordinator

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.components.http import StaticPathConfig
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_POLL_INTERVAL,
    CONF_DEBUG_LOGGING,
    CONF_VERIFY_SSL,
    CONF_ALLOW_SSL_FALLBACK,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_DEBUG_LOGGING,
    VERIFY_SSL,
    SERVICE_SET_SCHEDULER,
    SERVICE_RUN_DIFFUSER,
    SERVICE_LOAD_WORKSET,
    SERVICE_SAVE_WORKSET,
    ATTR_WORK_DURATION,
    ATTR_PAUSE_DURATION,
    ATTR_WEEK_DAYS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "button", "number", "sensor", "select", "text", "binary_sensor"]

SET_SCHEDULER_SCHEMA = vol.Schema({
    vol.Required(ATTR_WORK_DURATION): vol.All(vol.Coerce(int), vol.Range(min=5, max=900)),
    vol.Optional(ATTR_PAUSE_DURATION): vol.All(vol.Coerce(int), vol.Range(min=5, max=900)),
    vol.Optional(ATTR_WEEK_DAYS): vol.All(
        cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=6))]
    ),
    vol.Optional("device_id"): cv.string,
})

RUN_DIFFUSER_SCHEMA = vol.Schema({
    vol.Optional(ATTR_WORK_DURATION): vol.All(vol.Coerce(int), vol.Range(min=5, max=900)),
    vol.Optional(ATTR_PAUSE_DURATION): vol.All(vol.Coerce(int), vol.Range(min=5, max=900)),
    vol.Optional("device_id"): cv.string,
})

API_DIAGNOSTICS_SCHEMA = vol.Schema({
    vol.Required("path"): cv.string,
    vol.Optional("method", default="GET"): vol.In(["GET", "POST"]),
    vol.Optional("device_id"): cv.string,
    vol.Optional("params"): dict,
    vol.Optional("data"): dict,
    vol.Optional("json"): dict,
    vol.Optional("log_response", default=True): cv.boolean,
    vol.Optional("fire_event", default=True): cv.boolean,
})


async def _cleanup_old_helpers(hass: HomeAssistant, device_name: str):
    """Remove old helper entities created by previous version of the integration."""
    helper_prefix = f"aromalink_{device_name.lower().replace(' ', '_').replace('-', '_')}"
    entity_registry = er.async_get(hass)
    removed_count = 0
    config_entry_ids = set()
    entity_ids_to_remove = set()

    prefixes = (
        f"input_boolean.{helper_prefix}_program_",
        f"input_datetime.{helper_prefix}_program_",
        f"input_number.{helper_prefix}_program_",
        f"input_select.{helper_prefix}_program_",
    )
    selected_day_entity_id = f"input_select.{helper_prefix}_selected_day"

    for reg_entity in entity_registry.entities.values():
        entity_id = reg_entity.entity_id
        if entity_id == selected_day_entity_id or entity_id.startswith(prefixes):
            entity_ids_to_remove.add(entity_id)
            if reg_entity.config_entry_id:
                config_entry_ids.add(reg_entity.config_entry_id)

    # Remove entities from registry and state
    for entity_id in entity_ids_to_remove:
        try:
            entity_registry.async_remove(entity_id)
            _LOGGER.debug(f"Removed helper entity from registry: {entity_id}")
            removed_count += 1
        except Exception as e:
            _LOGGER.warning(f"Failed to remove {entity_id} from registry: {e}")

        if entity_id in hass.states.async_entity_ids():
            try:
                hass.states.async_remove(entity_id)
                _LOGGER.debug(f"Removed helper entity from state: {entity_id}")
            except Exception as e:
                _LOGGER.warning(f"Failed to remove {entity_id} from state: {e}")

    # Remove helper config entries (this actually deletes helpers so they don't come back)
    for entry_id in config_entry_ids:
        config_entry = hass.config_entries.async_get_entry(entry_id)
        if config_entry and config_entry.domain in {
            "input_boolean",
            "input_datetime",
            "input_number",
            "input_select",
        }:
            try:
                await hass.config_entries.async_remove(entry_id)
                _LOGGER.debug(f"Removed helper config entry: {entry_id}")
            except Exception as e:
                _LOGGER.warning(f"Failed to remove helper config entry {entry_id}: {e}")

    if removed_count > 0 or config_entry_ids:
        _LOGGER.info(
            f"Cleaned up old helper entities for {device_name} "
            f"(prefix: {helper_prefix}, entities: {removed_count}, entries: {len(config_entry_ids)})"
        )
    else:
        _LOGGER.debug(f"No old helper entities found for {device_name} (prefix: {helper_prefix})")


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Aroma-Link component."""
    hass.data.setdefault(DOMAIN, {})
    
    # Register the custom card's static path
    await _register_frontend_resources(hass)

    # Install automation blueprints into HA config
    await _install_blueprints(hass)
    
    return True


async def _register_frontend_resources(hass: HomeAssistant):
    """Register custom card resources for the Lovelace frontend."""
    # Path to the www folder in this integration
    www_path = os.path.join(os.path.dirname(__file__), "www")
    card_file = "aroma-link-schedule-card.js"
    card_path = os.path.join(www_path, card_file)
    
    if not os.path.exists(card_path):
        _LOGGER.warning(f"Custom card not found at {card_path}")
        return
    
    # Register static path so the file is accessible
    url_path = f"/aroma_link_integration/{card_file}"
    
    try:
        # Register static path for serving the JS file
        await hass.http.async_register_static_paths([
            StaticPathConfig(url_path, card_path, cache_headers=False)
        ])
        _LOGGER.debug(f"Registered static path: {url_path}")
    except Exception as e:
        _LOGGER.warning(f"Failed to register static path: {e}")
        return
    
    # Add the resource to Lovelace
    try:
        await _add_lovelace_resource(hass, url_path)
    except Exception as e:
        _LOGGER.warning(f"Failed to add Lovelace resource: {e}")


async def _add_lovelace_resource(hass: HomeAssistant, url_path: str):
    """Add the custom card to Lovelace resources if not already present."""
    # Check if lovelace resources component is available
    if "lovelace" not in hass.data:
        _LOGGER.debug("Lovelace not yet loaded, will try via storage")
    
    # Use the resources storage directly
    from homeassistant.components.lovelace.resources import ResourceStorageCollection
    
    resources_collection = hass.data.get("lovelace", {}).get("resources")
    
    if resources_collection is None:
        # Lovelace resources not initialized yet, store for later
        hass.data.setdefault(DOMAIN, {})["pending_resource"] = url_path
        _LOGGER.info(
            f"Custom card resource will be available at: {url_path}\n"
            "Add to Lovelace resources manually if needed:\n"
            f"  URL: {url_path}\n"
            "  Type: JavaScript Module"
        )
        return
    
    # Check if already registered
    existing_urls = [r.get("url") for r in resources_collection.async_items()]
    if url_path in existing_urls:
        _LOGGER.debug(f"Resource already registered: {url_path}")
        return
    
    # Add the resource
    try:
        await resources_collection.async_create_item({
            "url": url_path,
            "type": "module"
        })
        _LOGGER.info(f"Registered Lovelace resource: {url_path}")
    except Exception as e:
        _LOGGER.warning(f"Could not auto-register Lovelace resource: {e}")


async def _install_blueprints(hass: HomeAssistant):
    """Copy bundled blueprint YAML files into HA's blueprints directory."""
    import shutil

    source_dir = os.path.join(
        os.path.dirname(__file__), "blueprints", "automation"
    )
    if not os.path.isdir(source_dir):
        _LOGGER.debug("No bundled blueprints directory found at %s", source_dir)
        return

    target_dir = hass.config.path(
        "blueprints", "automation", "aroma_link_integration"
    )

    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        _LOGGER.warning("Could not create blueprints directory %s: %s", target_dir, exc)
        return

    installed = 0
    for filename in os.listdir(source_dir):
        if not filename.endswith(".yaml"):
            continue
        src = os.path.join(source_dir, filename)
        dst = os.path.join(target_dir, filename)

        if os.path.exists(dst) and os.path.getmtime(src) <= os.path.getmtime(dst):
            continue

        try:
            shutil.copy2(src, dst)
            installed += 1
            _LOGGER.debug("Installed blueprint: %s", filename)
        except OSError as exc:
            _LOGGER.warning("Failed to install blueprint %s: %s", filename, exc)

    if installed:
        _LOGGER.info(
            "Installed %d Aroma-Link blueprint(s) to %s", installed, target_dir
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


def _apply_debug_logging(entry: ConfigEntry) -> None:
    """Apply debug logging based on the config entry options."""
    debug_enabled = entry.options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)
    level = logging.DEBUG if debug_enabled else logging.INFO
    logging.getLogger("custom_components.aroma_link_integration").setLevel(level)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options updates."""
    _apply_debug_logging(entry)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Aroma-Link from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    devices = entry.data.get("devices", [])

    if not devices and CONF_DEVICE_ID in entry.data:
        # Support for old configuration format with single device
        device_id = entry.data[CONF_DEVICE_ID]
        device_name = entry.data.get("device_name", "Unknown")
        devices = [{CONF_DEVICE_ID: device_id, "device_name": device_name}]

    if not devices:
        _LOGGER.error("No devices found in config entry")
        return False

    _LOGGER.info(
        f"Setting up Aroma-Link integration with {len(devices)} devices")

    _apply_debug_logging(entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    verify_ssl = entry.options.get(CONF_VERIFY_SSL)
    if verify_ssl is None:
        verify_ssl = entry.data.get(CONF_VERIFY_SSL)
    if verify_ssl is None:
        verify_ssl = False

    allow_ssl_fallback = entry.options.get(CONF_ALLOW_SSL_FALLBACK)
    if allow_ssl_fallback is None:
        allow_ssl_fallback = entry.data.get(CONF_ALLOW_SSL_FALLBACK)
    if allow_ssl_fallback is None:
        allow_ssl_fallback = True

    # Create a single shared coordinator for authentication
    auth_coordinator = AromaLinkAuthCoordinator(
        hass,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        allow_ssl_fallback=allow_ssl_fallback,
    )

    # Force first login and initialization
    await auth_coordinator.async_config_entry_first_refresh()

    # Store coordinators for each device
    device_coordinators = {}

    # Load persisted oil tracking/calibration state
    oil_state_store = Store(hass, 1, f"{DOMAIN}_oil_state_{entry.entry_id}.json")
    oil_state_data = await oil_state_store.async_load() or {}

    async def _save_oil_state():
        """Persist oil tracking/calibration state for all devices."""
        data = {}
        for dev_id, coord in device_coordinators.items():
            data[dev_id] = coord.export_oil_state()
        await oil_state_store.async_save(data)

    poll_interval_seconds = entry.options.get(
        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_SECONDS
    )
    # Handle migration from old minutes-based config
    if poll_interval_seconds <= 30:
        poll_interval_seconds = poll_interval_seconds * 60

    # Create coordinator for each device
    for device in devices:
        device_id = device[CONF_DEVICE_ID]
        device_name = device.get("device_name", f"Device {device_id}")

        _LOGGER.info(
            f"Initializing device coordinator for {device_name} ({device_id})")

        device_coordinator = AromaLinkDeviceCoordinator(
            hass,
            auth_coordinator=auth_coordinator,
            device_id=device_id,
            device_name=device_name,
            update_interval_seconds=poll_interval_seconds,
            save_oil_state_cb=_save_oil_state,
            oil_state=oil_state_data.get(str(device_id)) or oil_state_data.get(device_id),
        )

        # Do first refresh for each device
        try:
            await device_coordinator.async_config_entry_first_refresh()
            device_coordinators[device_id] = device_coordinator
            
            # Clean up old helper entities from previous version
            try:
                await _cleanup_old_helpers(hass, device_name)
            except Exception as e:
                _LOGGER.warning(f"Failed to cleanup old helpers for {device_name}: {e}")
        except Exception as e:
            _LOGGER.error(f"Error initializing device {device_id}: {e}")

    if not device_coordinators:
        _LOGGER.error("Failed to initialize any devices")
        return False

    # Auto-fetch all schedules on startup (for dashboard matrix view)
    for device_id, coordinator in device_coordinators.items():
        try:
            _LOGGER.debug(f"Auto-fetching all schedules for device {device_id}")
            await coordinator.async_fetch_all_schedules()
        except Exception as e:
            _LOGGER.warning(f"Failed to auto-fetch schedules for device {device_id}: {e}")

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "auth_coordinator": auth_coordinator,
        "device_coordinators": device_coordinators,
    }

    # Register services
    async def set_scheduler_service(call: ServiceCall):
        """Service to set diffuser scheduler."""
        device_id = call.data.get("device_id")
        work_duration = call.data.get(ATTR_WORK_DURATION)
        pause_duration = call.data.get(ATTR_PAUSE_DURATION)
        week_days = call.data.get(ATTR_WEEK_DAYS, [0, 1, 2, 3, 4, 5, 6])

        # If device_id specified, use that coordinator
        if device_id and device_id in device_coordinators:
            await device_coordinators[device_id].set_scheduler(work_duration, pause_duration, week_days)
        elif len(device_coordinators) == 1:
            # If only one device, use that
            first_device_id = list(device_coordinators.keys())[0]
            await device_coordinators[first_device_id].set_scheduler(work_duration, pause_duration, week_days)
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")

    async def run_diffuser_service(call: ServiceCall):
        """Service to run diffuser for a specific time."""
        device_id = call.data.get("device_id")
        work_duration = call.data.get(ATTR_WORK_DURATION)
        pause_duration = call.data.get(ATTR_PAUSE_DURATION)

        # If device_id specified, use that coordinator
        if device_id and device_id in device_coordinators:
            await device_coordinators[device_id].run_diffuser(work_duration, pause_duration=pause_duration)
        elif len(device_coordinators) == 1:
            # If only one device, use that
            first_device_id = list(device_coordinators.keys())[0]
            await device_coordinators[first_device_id].run_diffuser(work_duration, pause_duration=pause_duration)
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULER,
        set_scheduler_service,
        schema=SET_SCHEDULER_SCHEMA
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_DIFFUSER,
        run_diffuser_service,
        schema=RUN_DIFFUSER_SCHEMA
    )

    async def api_diagnostics_service(call: ServiceCall):
        """Call arbitrary API endpoints for diagnostics."""
        device_id = call.data.get("device_id")
        path = call.data.get("path")
        method = call.data.get("method", "GET").upper()
        params = call.data.get("params")
        data = call.data.get("data")
        json_body = call.data.get("json")
        log_response = call.data.get("log_response", True)
        fire_event = call.data.get("fire_event", True)

        if not path.startswith("/"):
            path = f"/{path}"

        if device_id and "{device_id}" in path:
            path = path.format(device_id=device_id)

        url = f"https://www.aroma-link.com{path}"

        coordinator = None
        if device_id and device_id in device_coordinators:
            coordinator = device_coordinators[device_id]
        elif len(device_coordinators) == 1:
            coordinator = list(device_coordinators.values())[0]

        if coordinator is None:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return

        try:
            response_data = await coordinator.api_request(
                url=url,
                method=method,
                params=params,
                data=data,
                json_body=json_body,
            )
        except Exception as exc:
            _LOGGER.error(f"API diagnostics call failed: {exc}")
            return

        if log_response:
            _LOGGER.info(
                "API diagnostics response (%s %s): %s",
                method,
                url,
                response_data,
            )

        if fire_event:
            hass.bus.async_fire(
                f"{DOMAIN}_api_diagnostics",
                {
                    "device_id": device_id,
                    "method": method,
                    "url": url,
                    "response": response_data,
                },
            )

    hass.services.async_register(
        DOMAIN,
        "api_diagnostics",
        api_diagnostics_service,
        schema=API_DIAGNOSTICS_SCHEMA,
    )

    async def load_workset_service(call: ServiceCall):
        """Service to load workset from device into helper entities."""
        device_id = call.data.get("device_id")
        week_day = call.data.get("week_day", 0)
        helper_prefix = call.data.get("helper_prefix")
        
        # Get coordinator
        coordinator = None
        if device_id and device_id in device_coordinators:
            coordinator = device_coordinators[device_id]
        elif len(device_coordinators) == 1:
            coordinator = list(device_coordinators.values())[0]
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return
        
        if not helper_prefix:
            # Use device name as prefix, sanitized
            helper_prefix = f"aromalink_{coordinator.device_name.lower().replace(' ', '_').replace('-', '_')}"
        
        # Fetch workset from device
        workset = await coordinator.fetch_workset_for_day(week_day)
        if not workset:
            _LOGGER.error(f"Failed to load workset for device {coordinator.device_id}")
            return
        
        # Update helper entities
        for i, program in enumerate(workset, 1):
            # Enabled toggle
            enabled_entity = f"input_boolean.{helper_prefix}_program_{i}_enabled"
            if enabled_entity in hass.states.async_entity_ids():
                hass.states.async_set(enabled_entity, "on" if program["enabled"] == 1 else "off")
            
            # Start time
            start_entity = f"input_datetime.{helper_prefix}_program_{i}_start"
            if start_entity in hass.states.async_entity_ids():
                start_time = program["start_time"]
                hass.states.async_set(start_entity, f"2024-01-01 {start_time}:00")
            
            # End time
            end_entity = f"input_datetime.{helper_prefix}_program_{i}_end"
            if end_entity in hass.states.async_entity_ids():
                end_time = program["end_time"]
                hass.states.async_set(end_entity, f"2024-01-01 {end_time}:00")
            
            # Work duration
            work_entity = f"input_number.{helper_prefix}_program_{i}_work"
            if work_entity in hass.states.async_entity_ids():
                hass.states.async_set(work_entity, program["work_sec"])
            
            # Pause duration
            pause_entity = f"input_number.{helper_prefix}_program_{i}_pause"
            if pause_entity in hass.states.async_entity_ids():
                hass.states.async_set(pause_entity, program["pause_sec"])
            
            # Level
            level_entity = f"input_select.{helper_prefix}_program_{i}_level"
            if level_entity in hass.states.async_entity_ids():
                level_map = {1: "A", 2: "B", 3: "C"}
                level = level_map.get(program["level"], "A")
                hass.states.async_set(level_entity, level)
        
        _LOGGER.info(f"Loaded workset for device {coordinator.device_id} day {week_day} into helpers with prefix {helper_prefix}")

    async def save_workset_service(call: ServiceCall):
        """Service to save workset from helper entities to device."""
        device_id = call.data.get("device_id")
        week_days = call.data.get("week_days", [0, 1, 2, 3, 4, 5, 6])
        helper_prefix = call.data.get("helper_prefix")
        
        # Get coordinator
        coordinator = None
        if device_id and device_id in device_coordinators:
            coordinator = device_coordinators[device_id]
        elif len(device_coordinators) == 1:
            coordinator = list(device_coordinators.values())[0]
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return
        
        if not helper_prefix:
            # Use device name as prefix, sanitized
            helper_prefix = f"aromalink_{coordinator.device_name.lower().replace(' ', '_').replace('-', '_')}"
        
        # Build workTimeList from helper entities
        work_time_list = []
        for i in range(1, 6):  # 5 programs
            # Get values from helpers
            enabled_entity = f"input_boolean.{helper_prefix}_program_{i}_enabled"
            start_entity = f"input_datetime.{helper_prefix}_program_{i}_start"
            end_entity = f"input_datetime.{helper_prefix}_program_{i}_end"
            work_entity = f"input_number.{helper_prefix}_program_{i}_work"
            pause_entity = f"input_number.{helper_prefix}_program_{i}_pause"
            level_entity = f"input_select.{helper_prefix}_program_{i}_level"
            
            enabled = 0
            if enabled_entity in hass.states.async_entity_ids():
                state = hass.states.get(enabled_entity)
                enabled = 1 if state and state.state == "on" else 0
            
            start_time = "00:00"
            if start_entity in hass.states.async_entity_ids():
                state = hass.states.get(start_entity)
                if state and state.state:
                    # Extract time from datetime string
                    try:
                        dt_str = state.state
                        if " " in dt_str:
                            start_time = dt_str.split(" ")[1][:5]  # Extract HH:MM
                    except:
                        pass
            
            end_time = "23:59"
            if end_entity in hass.states.async_entity_ids():
                state = hass.states.get(end_entity)
                if state and state.state:
                    try:
                        dt_str = state.state
                        if " " in dt_str:
                            end_time = dt_str.split(" ")[1][:5]  # Extract HH:MM
                    except:
                        pass
            
            work_duration = "10"
            if work_entity in hass.states.async_entity_ids():
                state = hass.states.get(work_entity)
                if state and state.state:
                    work_duration = str(int(float(state.state)))
            
            pause_duration = "120"
            if pause_entity in hass.states.async_entity_ids():
                state = hass.states.get(pause_entity)
                if state and state.state:
                    pause_duration = str(int(float(state.state)))
            
            level = "1"
            if level_entity in hass.states.async_entity_ids():
                state = hass.states.get(level_entity)
                if state and state.state:
                    level_map = {"A": "1", "B": "2", "C": "3"}
                    level = level_map.get(state.state, "1")
            
            work_time_list.append({
                "startTime": start_time,
                "endTime": end_time,
                "enabled": enabled,
                "consistenceLevel": level,
                "workDuration": work_duration,
                "pauseDuration": pause_duration
            })
        
        # Save to device
        result = await coordinator.set_workset(week_days, work_time_list)
        if result:
            _LOGGER.info(f"Saved workset for device {coordinator.device_id} to days {week_days}")
        else:
            _LOGGER.error(f"Failed to save workset for device {coordinator.device_id}")

    LOAD_WORKSET_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
        vol.Optional("week_day", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=6)),
        vol.Optional("helper_prefix"): cv.string,
    })

    SAVE_WORKSET_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
        vol.Required("week_days"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=6))]),
        vol.Optional("helper_prefix"): cv.string,
    })

    hass.services.async_register(
        DOMAIN,
        SERVICE_LOAD_WORKSET,
        load_workset_service,
        schema=LOAD_WORKSET_SCHEMA
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE_WORKSET,
        save_workset_service,
        schema=SAVE_WORKSET_SCHEMA
    )

    # Service: Set editor to specific day/program
    async def set_editor_program_service(call: ServiceCall):
        """Set the schedule editor to a specific day and program."""
        device_id = call.data.get("device_id")
        day = call.data.get("day", 0)
        program = call.data.get("program", 1)

        coordinator = None
        if device_id and device_id in device_coordinators:
            coordinator = device_coordinators[device_id]
        elif len(device_coordinators) == 1:
            coordinator = list(device_coordinators.values())[0]
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return

        # Refresh schedule for the day first
        await coordinator.async_refresh_schedule(day)
        # Set the editor program
        coordinator.set_editor_program(day, program)
        _LOGGER.info(f"Set editor to day {day}, program {program} for device {coordinator.device_id}")

    SET_EDITOR_PROGRAM_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
        vol.Optional("day", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=6)),
        vol.Optional("program", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=5)),
    })

    hass.services.async_register(
        DOMAIN,
        "set_editor_program",
        set_editor_program_service,
        schema=SET_EDITOR_PROGRAM_SCHEMA
    )

    # Service: Refresh all schedules (fetch all 7 days)
    async def refresh_all_schedules_service(call: ServiceCall):
        """Refresh schedules for all 7 days from the API."""
        device_id = call.data.get("device_id")

        coordinator = None
        if device_id and device_id in device_coordinators:
            coordinator = device_coordinators[device_id]
        elif len(device_coordinators) == 1:
            coordinator = list(device_coordinators.values())[0]
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return

        await coordinator.async_fetch_all_schedules()
        _LOGGER.info(f"Refreshed all schedules for device {coordinator.device_id}")

    REFRESH_ALL_SCHEDULES_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
    })

    hass.services.async_register(
        DOMAIN,
        "refresh_all_schedules",
        refresh_all_schedules_service,
        schema=REFRESH_ALL_SCHEDULES_SCHEMA
    )

    # ============================================================
    # BATCH SAVE SERVICE - Direct schedule save bypassing entities
    # ============================================================
    
    async def save_schedule_batch_service(call: ServiceCall):
        """Save schedule directly to device - FAST batch operation.
        
        This bypasses the entity-based flow for maximum speed.
        Accepts full schedule data and batches days with identical schedules.
        """
        device_id = call.data.get("device_id")
        schedules = call.data.get("schedules", [])
        
        coordinator = None
        if device_id and device_id in device_coordinators:
            coordinator = device_coordinators[device_id]
        elif len(device_coordinators) == 1:
            coordinator = list(device_coordinators.values())[0]
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return {"success": False, "error": "device_id required"}
        
        if not schedules:
            return {"success": False, "error": "No schedules provided"}
        
        import json
        
        # Group schedules by identical work_time_list for batching
        schedule_groups = {}  # JSON key -> (days, work_time_list)
        
        for schedule_item in schedules:
            day = schedule_item.get("day")
            programs = schedule_item.get("programs", [])
            
            if day is None or not programs:
                continue
            
            # Build work_time_list for this day
            level_map = {"A": "1", "B": "2", "C": "3", 1: "1", 2: "2", 3: "3"}
            work_time_list = []
            
            for prog in programs:
                work_time_list.append({
                    "startTime": prog.get("startTime", "00:00"),
                    "endTime": prog.get("endTime", "23:59"),
                    "enabled": prog.get("enabled", 0),
                    "consistenceLevel": level_map.get(prog.get("level", "A"), "1"),
                    "workDuration": str(prog.get("workSec", 10)),
                    "pauseDuration": str(prog.get("pauseSec", 120))
                })
            
            # Pad to 5 programs if needed
            while len(work_time_list) < 5:
                work_time_list.append({
                    "startTime": "00:00", "endTime": "23:59",
                    "enabled": 0, "consistenceLevel": "1",
                    "workDuration": "10", "pauseDuration": "120"
                })
            
            # Create hash key for grouping
            schedule_key = json.dumps(work_time_list, sort_keys=True)
            if schedule_key not in schedule_groups:
                schedule_groups[schedule_key] = ([], work_time_list)
            schedule_groups[schedule_key][0].append(day)
        
        # Make batched API calls
        total_days = sum(len(days) for days, _ in schedule_groups.values())
        _LOGGER.info(f"Batch save: {total_days} days grouped into {len(schedule_groups)} API calls")
        
        success_count = 0
        for schedule_key, (days, work_time_list) in schedule_groups.items():
            _LOGGER.info(f"Saving days {days} in single API call")
            result = await coordinator.set_workset(days, work_time_list, skip_refresh=True)
            if result:
                success_count += len(days)
        
        # Single refresh at end
        await coordinator.async_request_refresh()
        await coordinator.async_fetch_all_schedules()
        
        _LOGGER.info(f"Batch save complete: {success_count}/{total_days} days saved")
        return {"success": True, "days_saved": success_count, "api_calls": len(schedule_groups)}
    
    SAVE_SCHEDULE_BATCH_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
        vol.Required("schedules"): vol.All(cv.ensure_list, [vol.Schema({
            vol.Required("day"): vol.All(vol.Coerce(int), vol.Range(min=0, max=6)),
            vol.Required("programs"): vol.All(cv.ensure_list, [vol.Schema({
                vol.Optional("startTime", default="00:00"): cv.string,
                vol.Optional("endTime", default="23:59"): cv.string,
                vol.Optional("enabled", default=0): vol.Coerce(int),
                vol.Optional("level", default="A"): cv.string,
                vol.Optional("workSec", default=10): vol.Coerce(int),
                vol.Optional("pauseSec", default=120): vol.Coerce(int),
            })]),
        })]),
    })
    
    hass.services.async_register(
        DOMAIN,
        "save_schedule_batch",
        save_schedule_batch_service,
        schema=SAVE_SCHEDULE_BATCH_SCHEMA
    )

    # ============================================================
    # TIMED RUN SERVICES (server-side timer, survives browser close)
    # ============================================================
    
    # Storage for active timed runs: {device_id: {"cancel_callback": fn, "end_time": timestamp}}
    if "timed_runs" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["timed_runs"] = {}
    
    timed_runs = hass.data[DOMAIN]["timed_runs"]

    async def _turn_off_device(device_id: str):
        """Turn off device when timer expires."""
        coordinator = device_coordinators.get(device_id)
        if coordinator:
            _LOGGER.info(f"Timed run complete for device {device_id}, turning off")
            try:
                await coordinator.set_power(False)
                # Fire event so card can update
                hass.bus.async_fire(
                    f"{DOMAIN}_timed_run_complete",
                    {"device_id": device_id}
                )
            except Exception as e:
                _LOGGER.error(f"Failed to turn off device {device_id}: {e}")
        
        # Clean up state
        if device_id in timed_runs:
            del timed_runs[device_id]
        
        # Update sensor state
        _update_timed_run_sensor(device_id)

    def _update_timed_run_sensor(device_id: str):
        """Update the sensor with timer state."""
        coordinator = device_coordinators.get(device_id)
        if coordinator:
            coordinator.async_set_updated_data(coordinator.data)

    async def start_timed_run_service(call: ServiceCall):
        """Start a timed run for a device."""
        device_id = call.data.get("device_id")
        duration_hours = call.data.get("duration_hours", 6.0)
        work_sec = call.data.get("work_sec")
        pause_sec = call.data.get("pause_sec")

        # Get coordinator
        coordinator = None
        if device_id and device_id in device_coordinators:
            coordinator = device_coordinators[device_id]
            device_id = device_id  # Already set
        elif len(device_coordinators) == 1:
            device_id = list(device_coordinators.keys())[0]
            coordinator = device_coordinators[device_id]
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return

        # Cancel any existing timer for this device
        if device_id in timed_runs:
            existing = timed_runs[device_id]
            if existing.get("cancel_callback"):
                existing["cancel_callback"]()
            del timed_runs[device_id]

        # Apply work/pause settings if provided
        if work_sec is not None or pause_sec is not None:
            try:
                await coordinator.set_scheduler(
                    work_duration=work_sec or coordinator.data.get("workRemainTime", 5),
                    pause_duration=pause_sec or coordinator.data.get("pauseRemainTime", 900)
                )
            except Exception as e:
                _LOGGER.warning(f"Failed to set work/pause for timed run: {e}")

        # Turn on the device
        try:
            await coordinator.set_power(True)
        except Exception as e:
            _LOGGER.error(f"Failed to turn on device for timed run: {e}")
            return

        # Schedule turn-off
        duration_seconds = duration_hours * 3600
        end_time = hass.loop.time() + duration_seconds
        
        cancel_callback = hass.helpers.event.async_call_later(
            hass,
            duration_seconds,
            lambda _: hass.async_create_task(_turn_off_device(device_id))
        )

        timed_runs[device_id] = {
            "cancel_callback": cancel_callback,
            "end_time": end_time,
            "duration_hours": duration_hours
        }

        _LOGGER.info(f"Started timed run for device {device_id}: {duration_hours} hours")
        
        # Fire event for card
        hass.bus.async_fire(
            f"{DOMAIN}_timed_run_started",
            {
                "device_id": device_id,
                "duration_hours": duration_hours,
                "end_time": end_time
            }
        )
        
        _update_timed_run_sensor(device_id)

    async def cancel_timed_run_service(call: ServiceCall):
        """Cancel a timed run for a device."""
        device_id = call.data.get("device_id")

        # Get device_id if not specified
        if not device_id and len(device_coordinators) == 1:
            device_id = list(device_coordinators.keys())[0]
        
        if not device_id:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return

        if device_id not in timed_runs:
            _LOGGER.warning(f"No timed run active for device {device_id}")
            return

        # Cancel the timer
        existing = timed_runs[device_id]
        if existing.get("cancel_callback"):
            existing["cancel_callback"]()
        
        del timed_runs[device_id]
        
        _LOGGER.info(f"Cancelled timed run for device {device_id}")
        
        # Fire event for card
        hass.bus.async_fire(
            f"{DOMAIN}_timed_run_cancelled",
            {"device_id": device_id}
        )
        
        _update_timed_run_sensor(device_id)

    async def get_timed_run_status_service(call: ServiceCall):
        """Get the status of timed runs."""
        device_id = call.data.get("device_id")
        
        status = {}
        current_time = hass.loop.time()
        
        for dev_id, timer_data in timed_runs.items():
            if device_id and dev_id != device_id:
                continue
            remaining = max(0, timer_data["end_time"] - current_time)
            status[dev_id] = {
                "active": True,
                "remaining_seconds": int(remaining),
                "duration_hours": timer_data["duration_hours"]
            }
        
        # Fire event with status
        hass.bus.async_fire(
            f"{DOMAIN}_timed_run_status",
            {"status": status}
        )
        
        return status

    START_TIMED_RUN_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
        vol.Optional("duration_hours", default=6.0): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=24)),
        vol.Optional("work_sec"): vol.All(vol.Coerce(int), vol.Range(min=1, max=999)),
        vol.Optional("pause_sec"): vol.All(vol.Coerce(int), vol.Range(min=1, max=9999)),
    })

    CANCEL_TIMED_RUN_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
    })

    GET_TIMED_RUN_STATUS_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
    })

    hass.services.async_register(
        DOMAIN,
        "start_timed_run",
        start_timed_run_service,
        schema=START_TIMED_RUN_SCHEMA
    )

    hass.services.async_register(
        DOMAIN,
        "cancel_timed_run",
        cancel_timed_run_service,
        schema=CANCEL_TIMED_RUN_SCHEMA
    )

    hass.services.async_register(
        DOMAIN,
        "get_timed_run_status",
        get_timed_run_status_service,
        schema=GET_TIMED_RUN_STATUS_SCHEMA
    )

    # Store timed_runs reference in domain data for access by sensors
    hass.data[DOMAIN]["timed_runs"] = timed_runs

    # ============================================================
    # OIL TRACKING SERVICES
    # ============================================================

    async def reset_oil_runtime_service(call: ServiceCall):
        """Reset oil tracking (call when refilling oil).
        
        Starts tracking cumulative work time using duty-cycle calculation.
        Also captures pumpCount as secondary reference.
        """
        device_id = call.data.get("device_id")

        coordinator = None
        if device_id and device_id in device_coordinators:
            coordinator = device_coordinators[device_id]
        elif len(device_coordinators) == 1:
            coordinator = list(device_coordinators.values())[0]
        else:
            _LOGGER.error("Multiple devices available, must specify device_id")
            return

        # Get current pump count from last data refresh
        current_pump_count = None
        if coordinator.data:
            current_pump_count = coordinator.data.get("pumpCount", 0)
        
        coordinator.reset_oil_tracking(current_pump_count)
        
        oil_info = coordinator.get_oil_tracking_info()
        
        _LOGGER.info(
            f"Reset oil tracking for device {coordinator.device_id}. "
            f"Baseline pumpCount: {current_pump_count}, "
            f"Current settings: work={oil_info.get('last_work_duration')}s, "
            f"pause={oil_info.get('last_pause_duration')}s"
        )
        
        hass.bus.async_fire(
            f"{DOMAIN}_oil_runtime_reset",
            {
                "device_id": coordinator.device_id,
                "baseline_pump_count": current_pump_count,
                "tracking_started": True,
            }
        )

    RESET_OIL_RUNTIME_SCHEMA = vol.Schema({
        vol.Optional("device_id"): cv.string,
    })

    hass.services.async_register(
        DOMAIN,
        "reset_oil_runtime",
        reset_oil_runtime_service,
        schema=RESET_OIL_RUNTIME_SCHEMA
    )

    # Use the new method
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True
