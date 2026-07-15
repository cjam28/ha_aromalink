"""The Aroma-Link integration (v3: power-gated architecture).

Layout:
- models.py      pure schedule domain (single day convention, compile, validate)
- store.py       persisted desired state (schedule model, sync, oil, timed runs)
- reconciler.py  the ONLY schedule-slot writer (verify-after-write, drift heal)
- ws_api.py      websocket API for the Lovelace card
- coordinators   auth session + device poll/commands (power path is shielded)

This module is glue: setup/unload/migration, card asset serving, and wiring
the store's change notifications to the single bus event.
"""
import functools
import hashlib
import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.http import StaticPathConfig
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from datetime import timedelta

from .AromaLinkAuthCoordinator import AromaLinkAuthCoordinator
from .AromaLinkDeviceCoordinator import AromaLinkDeviceCoordinator
from .migration import async_import_legacy, async_remove_legacy_store
from .reconciler import ScheduleReconciler
from .store import AromaLinkStore
from . import ws_api
from .const import (
    DOMAIN,
    EVENT_UPDATED,
    CONF_DEVICE_ID,
    CONF_POLL_INTERVAL,
    CONF_DEBUG_LOGGING,
    CONF_VERIFY_SSL,
    CONF_ALLOW_SSL_FALLBACK,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_DEBUG_LOGGING,
    SERVICE_API_DIAGNOSTICS,
)

_LOGGER = logging.getLogger(__name__)

# This integration is config-entry only; declare it so hassfest can verify.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = ["switch", "binary_sensor", "sensor", "number", "button"]

DRIFT_CHECK_INTERVAL = timedelta(hours=1)

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
    """Remove helper entities created by the pre-2.x helper-based UI."""
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

    for entity_id in entity_ids_to_remove:
        try:
            entity_registry.async_remove(entity_id)
            removed_count += 1
        except Exception as e:
            _LOGGER.warning(f"Failed to remove {entity_id} from registry: {e}")

        if entity_id in hass.states.async_entity_ids():
            try:
                hass.states.async_remove(entity_id)
            except Exception as e:
                _LOGGER.warning(f"Failed to remove {entity_id} from state: {e}")

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
            except Exception as e:
                _LOGGER.warning(f"Failed to remove helper config entry {entry_id}: {e}")

    if removed_count or config_entry_ids:
        _LOGGER.info(
            f"Cleaned up old helper entities for {device_name} "
            f"(entities: {removed_count}, entries: {len(config_entry_ids)})"
        )


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Aroma-Link component."""
    hass.data.setdefault(DOMAIN, {})

    # Serve the card assets and register the Lovelace resource once HA is up.
    await _register_static_path(hass)

    async def _deferred_setup(event):
        await _register_lovelace_resource(hass)

    hass.bus.async_listen_once("homeassistant_started", _deferred_setup)

    await _install_blueprints(hass)

    ws_api.async_register(hass)

    return True


async def _register_static_path(hass: HomeAssistant):
    """Serve the whole www/ directory (card entry module + submodules + vendor)."""
    www_path = os.path.join(os.path.dirname(__file__), "www")
    if not os.path.isdir(www_path):
        _LOGGER.warning("Card assets directory missing at %s", www_path)
        return
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(f"/{DOMAIN}", www_path, cache_headers=False)
        ])
        _LOGGER.debug("Registered static path /%s -> %s", DOMAIN, www_path)
    except Exception as e:
        _LOGGER.warning(f"Failed to register static path: {e}")


def _hash_card_assets(www_path: str) -> str | None:
    """Hash every .js under www/ so any module change busts browser caches."""
    digest = hashlib.md5()
    found = False
    for root, _dirs, files in sorted(os.walk(www_path)):
        for filename in sorted(files):
            if not filename.endswith(".js"):
                continue
            found = True
            path = os.path.join(root, filename)
            with open(path, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()[:8] if found else None


async def _register_lovelace_resource(hass: HomeAssistant):
    """Register the card as a Lovelace resource (after HA is fully started)."""
    www_path = os.path.join(os.path.dirname(__file__), "www")
    card_file = "aroma-link-schedule-card.js"
    if not os.path.exists(os.path.join(www_path, card_file)):
        return

    file_hash = await hass.async_add_executor_job(_hash_card_assets, www_path)
    if file_hash is None:
        return

    versioned_url = f"/{DOMAIN}/{card_file}?v={file_hash}"
    try:
        await _add_lovelace_resource(hass, versioned_url)
    except Exception as e:
        _LOGGER.warning(f"Failed to add Lovelace resource: {e}")


async def _add_lovelace_resource(hass: HomeAssistant, url_path: str):
    """Add or update the custom card in Lovelace resources."""
    resources_collection = None

    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is not None:
        if hasattr(lovelace_data, "resources"):
            resources_collection = lovelace_data.resources
        elif isinstance(lovelace_data, dict):
            resources_collection = lovelace_data.get("resources")

    if resources_collection is None:
        resources_collection = hass.data.get("lovelace_resources")

    if resources_collection is None:
        _LOGGER.warning(
            f"Lovelace resources not available. Please add manually:\n"
            f"  URL: {url_path}\n"
            f"  Type: JavaScript Module"
        )
        return

    base_url = url_path.split("?")[0]

    existing_item = None
    try:
        for item in resources_collection.async_items():
            item_url = item.get("url", "")
            if item_url.split("?")[0] == base_url:
                existing_item = item
                break
    except Exception as e:
        _LOGGER.warning(f"Error reading existing resources: {e}")

    try:
        if existing_item:
            if existing_item.get("url") == url_path:
                return
            await resources_collection.async_update_item(
                existing_item["id"],
                {"url": url_path}
            )
            _LOGGER.info(f"Updated Lovelace resource: {url_path}")
        else:
            await resources_collection.async_create_item({
                "url": url_path,
                "res_type": "module"
            })
            _LOGGER.info(f"Registered Lovelace resource: {url_path}")
    except Exception as e:
        _LOGGER.warning(
            f"Could not auto-register Lovelace resource ({e}). Add manually:\n"
            f"  URL: {url_path}\n"
            f"  Type: JavaScript Module"
        )


async def _install_blueprints(hass: HomeAssistant):
    """Copy bundled blueprint YAML files into HA's blueprints directory."""
    import shutil

    source_dir = os.path.join(
        os.path.dirname(__file__), "blueprints", "automation"
    )
    target_dir = hass.config.path(
        "blueprints", "automation", "aroma_link_integration"
    )

    def _sync_copy():
        if not os.path.isdir(source_dir):
            return 0
        os.makedirs(target_dir, exist_ok=True)
        count = 0
        for filename in os.listdir(source_dir):
            if not filename.endswith(".yaml"):
                continue
            src = os.path.join(source_dir, filename)
            dst = os.path.join(target_dir, filename)
            if os.path.exists(dst) and os.path.getmtime(src) <= os.path.getmtime(dst):
                continue
            shutil.copy2(src, dst)
            count += 1
        return count

    try:
        installed = await hass.async_add_executor_job(_sync_copy)
        if installed:
            _LOGGER.info(
                "Installed %d Aroma-Link blueprint(s) to %s", installed, target_dir
            )
    except OSError as exc:
        _LOGGER.warning("Failed to install blueprints: %s", exc)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Migrate old config entries to the current version."""
    if entry.version > 2:
        return False
    if entry.version < 2:
        # v1 -> v2: introduce the per-device gates container in options.
        new_options = {**entry.options}
        new_options.setdefault("gates", {})
        hass.config_entries.async_update_entry(entry, options=new_options, version=2)
        _LOGGER.info("Migrated %s config entry to version 2", DOMAIN)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, {})
        for reconciler in (entry_data.get("reconcilers") or {}).values():
            reconciler.stop()
        store = entry_data.get("store")
        if store:
            await store.async_save_now()
        auth_coordinator = entry_data.get("auth_coordinator")
        if auth_coordinator:
            await auth_coordinator.async_close()

    return unload_ok


def _apply_debug_logging(entry: ConfigEntry) -> None:
    debug_enabled = entry.options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)
    level = logging.DEBUG if debug_enabled else logging.INFO
    logging.getLogger("custom_components.aroma_link_integration").setLevel(level)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _apply_debug_logging(entry)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Aroma-Link from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    devices = entry.data.get("devices", [])

    if not devices and CONF_DEVICE_ID in entry.data:
        device_id = entry.data[CONF_DEVICE_ID]
        device_name = entry.data.get("device_name", "Unknown")
        devices = [{CONF_DEVICE_ID: device_id, "device_name": device_name}]

    if not devices:
        _LOGGER.error("No devices found in config entry")
        return False

    _LOGGER.info(f"Setting up Aroma-Link integration with {len(devices)} devices")

    _apply_debug_logging(entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    verify_ssl = entry.options.get(CONF_VERIFY_SSL)
    if verify_ssl is None:
        verify_ssl = entry.data.get(CONF_VERIFY_SSL)
    if verify_ssl is None:
        # Verify by default; the runtime SSL fallback (allow_ssl_fallback)
        # still downgrades with a notification if the cert breaks again.
        verify_ssl = True

    allow_ssl_fallback = entry.options.get(CONF_ALLOW_SSL_FALLBACK)
    if allow_ssl_fallback is None:
        allow_ssl_fallback = entry.data.get(CONF_ALLOW_SSL_FALLBACK)
    if allow_ssl_fallback is None:
        allow_ssl_fallback = True

    auth_coordinator = AromaLinkAuthCoordinator(
        hass,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        allow_ssl_fallback=allow_ssl_fallback,
    )
    await auth_coordinator.async_config_entry_first_refresh()

    al_store = AromaLinkStore(hass, entry.entry_id)
    await al_store.async_load()

    poll_interval_seconds = entry.options.get(
        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_SECONDS
    )
    if poll_interval_seconds <= 30:
        # Migration from old minutes-based config
        poll_interval_seconds = poll_interval_seconds * 60

    device_coordinators: dict[str, AromaLinkDeviceCoordinator] = {}
    reconcilers: dict[str, ScheduleReconciler] = {}

    async def _save_oil_state():
        for dev_id, coord in device_coordinators.items():
            await al_store.async_save_oil(dev_id, coord.export_oil_state())

    for device in devices:
        device_id = str(device[CONF_DEVICE_ID])
        device_name = device.get("device_name", f"Device {device_id}")

        device_coordinator = AromaLinkDeviceCoordinator(
            hass,
            auth_coordinator=auth_coordinator,
            device_id=device_id,
            device_name=device_name,
            update_interval_seconds=poll_interval_seconds,
            save_oil_state_cb=_save_oil_state,
            oil_state=None,  # applied below, after the store/import settles
        )
        device_coordinators[device_id] = device_coordinator

    # One-time (idempotent) legacy import needs the coordinators for fetching.
    try:
        await async_import_legacy(hass, entry.entry_id, al_store, device_coordinators)
    except Exception:
        _LOGGER.exception("Legacy state import failed; will retry next setup")

    for device_id, coordinator in device_coordinators.items():
        oil_state = al_store.get_oil(device_id)
        if oil_state:
            coordinator._apply_oil_state(oil_state)
        coordinator.schedule_provider = functools.partial(al_store.get_model, device_id)

        # First refresh: a failure no longer drops the device — it stays
        # registered so entities appear (unavailable) and recover on a later
        # poll (upstream bdbaea2).
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            _LOGGER.warning(
                f"Initial refresh failed for device {device_id}; "
                "keeping it registered so it can recover on a later poll"
            )

        try:
            await _cleanup_old_helpers(hass, coordinator.device_name)
        except Exception as e:
            _LOGGER.warning(f"Failed to cleanup old helpers: {e}")

        reconcilers[device_id] = ScheduleReconciler(
            hass, coordinator, al_store, device_id
        )

    # Once every device is imported cleanly and oil lives in the new store,
    # the legacy oil-state file has no reader left.
    if all(not al_store.is_import_pending(d) for d in device_coordinators):
        try:
            await async_remove_legacy_store(hass, entry.entry_id)
        except Exception:
            _LOGGER.debug("Legacy store removal skipped", exc_info=True)

    # Store change notifications -> the ONE bus event.
    def _on_store_change(device_id: str, change: str, version):
        hass.bus.async_fire(
            EVENT_UPDATED,
            {"device_id": device_id, "change": change, "version": version},
        )

    al_store.set_change_listener(_on_store_change)

    entry_data = {
        "auth_coordinator": auth_coordinator,
        "device_coordinators": device_coordinators,
        "store": al_store,
        "reconcilers": reconcilers,
    }
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry_data

    # Kick the initial reconcile for devices whose model isn't confirmed synced
    # (first run after import: this is the cutover write that normalizes slots).
    for device_id, reconciler in reconcilers.items():
        sync = al_store.get_sync(device_id)
        model_version = al_store.get_model(device_id).schedule.version
        if sync.state != "synced" or sync.synced_version != model_version:
            reconciler.async_request_sync("startup")

    # Hourly drift check (schedule reads are deliberately NOT on every poll).
    async def _drift_tick(_now):
        for reconciler in reconcilers.values():
            try:
                await reconciler.async_check_drift()
            except Exception:
                _LOGGER.exception("Drift check failed")

    entry.async_on_unload(
        async_track_time_interval(hass, _drift_tick, DRIFT_CHECK_INTERVAL)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ------------------------------------------------------------- services

    async def api_diagnostics_service(call: ServiceCall):
        """Raw API probe for debugging (fires {DOMAIN}_api_diagnostics)."""
        device_id = call.data.get("device_id")
        coordinator = None
        if device_id and str(device_id) in device_coordinators:
            coordinator = device_coordinators[str(device_id)]
        elif len(device_coordinators) == 1:
            coordinator = next(iter(device_coordinators.values()))
        if coordinator is None:
            _LOGGER.error("api_diagnostics: specify device_id (multiple devices)")
            return

        path = call.data["path"]
        url = path if path.startswith("http") else f"https://www.aroma-link.com{path}"
        result = await coordinator.api_request(
            url,
            method=call.data.get("method", "GET"),
            params=call.data.get("params"),
            data=call.data.get("data"),
            json_body=call.data.get("json"),
        )
        if call.data.get("log_response", True):
            _LOGGER.info("api_diagnostics %s -> %s", path, result)
        if call.data.get("fire_event", True):
            hass.bus.async_fire(
                f"{DOMAIN}_api_diagnostics",
                {"device_id": coordinator.device_id, "path": path, "result": result},
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_API_DIAGNOSTICS,
        api_diagnostics_service,
        API_DIAGNOSTICS_SCHEMA,
    )

    return True
