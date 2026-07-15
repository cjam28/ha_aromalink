"""WebSocket API for the Aroma-Link Lovelace card.

All commands take the Aroma-Link cloud ``device_id`` and speak the canonical
day convention (0=Monday..6=Sunday) exclusively. The card converts from
``Date.getDay()`` at its own edge.

Live updates: the card subscribes to the single ``EVENT_UPDATED`` bus event
(payload ``{device_id, change, version}``) and re-issues ``get_schedule`` /
``get_status`` on receipt.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import (
    NightOwlSettings,
    WeeklySchedule,
    parse_hhmm,
    validate_schedule,
)

_LOGGER = logging.getLogger(__name__)

ERR_UNKNOWN_DEVICE = "unknown_device"
ERR_INVALID_SCHEDULE = "invalid_schedule"
ERR_VERSION_CONFLICT = "version_conflict"


def _entry_data_for_device(hass: HomeAssistant, device_id: str) -> dict | None:
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        coordinators = entry_data.get("device_coordinators") or {}
        if str(device_id) in {str(d) for d in coordinators}:
            return entry_data
    return None


def _schedule_payload(entry_data: dict, device_id: str) -> dict:
    store = entry_data["store"]
    model = store.get_model(device_id)
    sync = store.get_sync(device_id)
    return {
        "schedule": model.schedule.to_dict(),
        "night_owl": model.night_owl.to_dict(),
        "flags": {
            "schedule_enabled": model.schedule_enabled,
            "night_owl_enabled": model.night_owl_enabled,
            "default_work_sec": model.default_work_sec,
            "default_pause_sec": model.default_pause_sec,
        },
        "sync": sync.to_dict(),
    }


def _validate_night_owl(data: dict) -> list[str]:
    errors = []
    mode = data.get("mode", "outside_windows")
    if mode not in ("outside_windows", "fixed"):
        errors.append("night_owl.mode must be 'outside_windows' or 'fixed'")
    for key in ("fixed_start", "fixed_end"):
        value = data.get(key)
        if value is not None:
            try:
                parse_hhmm(str(value))
            except (ValueError, AttributeError):
                errors.append(f"night_owl.{key} must be HH:MM")
    for key, low, high in (
        ("work_sec", 5, 900),
        ("pause_sec", 5, 900),
        ("linger_minutes", 1, 240),
    ):
        value = data.get(key)
        if value is not None and not (low <= int(value) <= high):
            errors.append(f"night_owl.{key} out of range {low}-{high}")
    level = data.get("level")
    if level is not None and int(level) not in (1, 2, 3):
        errors.append("night_owl.level must be 1, 2, or 3")
    return errors


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aroma_link/get_schedule",
        vol.Required("device_id"): str,
    }
)
@websocket_api.async_response
async def ws_get_schedule(hass, connection, msg):
    """Return the full desired-state model + sync status for a device."""
    entry_data = _entry_data_for_device(hass, msg["device_id"])
    if entry_data is None:
        connection.send_error(msg["id"], ERR_UNKNOWN_DEVICE, "Unknown device_id")
        return
    connection.send_result(msg["id"], _schedule_payload(entry_data, msg["device_id"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aroma_link/save_schedule",
        vol.Required("device_id"): str,
        vol.Required("schedule"): dict,
        vol.Optional("night_owl"): dict,
        vol.Optional("base_version"): int,
    }
)
@websocket_api.async_response
async def ws_save_schedule(hass, connection, msg):
    """Validate and instantly persist a schedule; kicks the device push."""
    device_id = msg["device_id"]
    entry_data = _entry_data_for_device(hass, device_id)
    if entry_data is None:
        connection.send_error(msg["id"], ERR_UNKNOWN_DEVICE, "Unknown device_id")
        return

    store = entry_data["store"]
    current = store.get_model(device_id)

    base_version = msg.get("base_version")
    if base_version is not None and base_version != current.schedule.version:
        connection.send_error(
            msg["id"],
            ERR_VERSION_CONFLICT,
            f"Schedule changed elsewhere (have {current.schedule.version}, "
            f"you based on {base_version})",
        )
        return

    try:
        schedule = WeeklySchedule.from_dict(msg["schedule"])
    except (KeyError, TypeError, ValueError) as err:
        connection.send_error(msg["id"], ERR_INVALID_SCHEDULE, f"Malformed schedule: {err}")
        return

    errors = validate_schedule(schedule)
    night_owl = None
    if "night_owl" in msg:
        errors.extend(_validate_night_owl(msg["night_owl"]))
        if not errors:
            merged = {**current.night_owl.to_dict(), **msg["night_owl"]}
            night_owl = NightOwlSettings.from_dict(merged)
    if errors:
        connection.send_error(msg["id"], ERR_INVALID_SCHEDULE, "; ".join(errors))
        return

    version = await store.async_save_schedule(device_id, schedule, night_owl)
    _kick_reconciler(entry_data, device_id, "save_schedule")
    connection.send_result(
        msg["id"],
        {"version": version, "sync": store.get_sync(device_id).to_dict()},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aroma_link/set_night_owl_days",
        vol.Required("device_id"): str,
        vol.Required("days"): [vol.All(int, vol.Range(min=0, max=6))],
        vol.Required("enabled"): bool,
    }
)
@websocket_api.async_response
async def ws_set_night_owl_days(hass, connection, msg):
    """Set the per-day Night Owl allow flags (days are 0=Mon..6=Sun)."""
    entry_data = _entry_data_for_device(hass, msg["device_id"])
    if entry_data is None:
        connection.send_error(msg["id"], ERR_UNKNOWN_DEVICE, "Unknown device_id")
        return
    store = entry_data["store"]
    version = await store.async_set_night_owl_days(
        msg["device_id"], {day: msg["enabled"] for day in msg["days"]}
    )
    _kick_reconciler(entry_data, msg["device_id"], "night_owl_days")
    connection.send_result(msg["id"], {"version": version})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aroma_link/set_flags",
        vol.Required("device_id"): str,
        vol.Optional("schedule_enabled"): bool,
        vol.Optional("night_owl_enabled"): bool,
    }
)
@websocket_api.async_response
async def ws_set_flags(hass, connection, msg):
    """Set the master schedule/night-owl flags."""
    entry_data = _entry_data_for_device(hass, msg["device_id"])
    if entry_data is None:
        connection.send_error(msg["id"], ERR_UNKNOWN_DEVICE, "Unknown device_id")
        return
    store = entry_data["store"]
    version = await store.async_set_flags(
        msg["device_id"],
        schedule_enabled=msg.get("schedule_enabled"),
        night_owl_enabled=msg.get("night_owl_enabled"),
    )
    if msg.get("night_owl_enabled") is not None:
        _kick_reconciler(entry_data, msg["device_id"], "flags")
    # Gating reacts through the store change listener (engine, P3).
    connection.send_result(msg["id"], {"version": version})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aroma_link/sync_now",
        vol.Required("device_id"): str,
    }
)
@websocket_api.async_response
async def ws_sync_now(hass, connection, msg):
    """Force a drift check and (re)push of the schedule."""
    entry_data = _entry_data_for_device(hass, msg["device_id"])
    if entry_data is None:
        connection.send_error(msg["id"], ERR_UNKNOWN_DEVICE, "Unknown device_id")
        return
    reconciler = (entry_data.get("reconcilers") or {}).get(str(msg["device_id"]))
    if reconciler:
        hass.async_create_task(reconciler.async_check_drift(force_push=True))
    connection.send_result(
        msg["id"], {"sync": entry_data["store"].get_sync(msg["device_id"]).to_dict()}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aroma_link/get_status",
        vol.Required("device_id"): str,
    }
)
@websocket_api.async_response
async def ws_get_status(hass, connection, msg):
    """Live device/gating/timed-run/oil status for the card header."""
    device_id = str(msg["device_id"])
    entry_data = _entry_data_for_device(hass, device_id)
    if entry_data is None:
        connection.send_error(msg["id"], ERR_UNKNOWN_DEVICE, "Unknown device_id")
        return

    coordinator = entry_data["device_coordinators"][device_id]
    store = entry_data["store"]
    engine = (entry_data.get("engines") or {}).get(device_id)
    timed_runs = entry_data.get("timed_runs")

    data = coordinator.data or {}
    desired = engine.desired_power() if engine else None
    raw_oil = coordinator.get_oil_status() or {}
    oil = {
        # Stable card contract, mapped from the coordinator's internal names.
        "level_pct": raw_oil.get("level_percent"),
        "remaining_ml": raw_oil.get("estimated_remaining_ml"),
        "days_remaining": raw_oil.get("estimated_days_remaining_schedule"),
        "usage_rate_ml_per_hour": raw_oil.get("usage_rate_ml_per_hour"),
        "calibrated": raw_oil.get("calibrated"),
        "calibration_state": raw_oil.get("calibration_state"),
        "fill_date": raw_oil.get("fill_date"),
        "bottle_capacity_ml": raw_oil.get("bottle_capacity_ml"),
        "fill_volume_ml": raw_oil.get("fill_volume_ml"),
        "runtime_since_fill_hours": raw_oil.get("runtime_since_fill_hours"),
    }
    connection.send_result(
        msg["id"],
        {
            "power": bool(data.get("state", False)),
            "fan": bool(data.get("fan_state", False)),
            "work_status": data.get("workStatus"),
            "available": coordinator.last_update_success,
            "desired_power": desired,
            "gating": engine.snapshot() if engine else {},
            "timed_run": timed_runs.status(device_id) if timed_runs else None,
            "oil": oil,
            "sync": store.get_sync(device_id).to_dict(),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aroma_link/timed_run_start",
        vol.Required("device_id"): str,
        vol.Required("duration_minutes"): vol.All(int, vol.Range(min=1, max=24 * 60)),
        vol.Optional("work_sec"): vol.All(int, vol.Range(min=5, max=900)),
        vol.Optional("pause_sec"): vol.All(int, vol.Range(min=5, max=900)),
    }
)
@websocket_api.async_response
async def ws_timed_run_start(hass, connection, msg):
    """Start a restart-surviving timed run."""
    entry_data = _entry_data_for_device(hass, msg["device_id"])
    if entry_data is None:
        connection.send_error(msg["id"], ERR_UNKNOWN_DEVICE, "Unknown device_id")
        return
    ends_at = await entry_data["timed_runs"].async_start(
        msg["device_id"],
        msg["duration_minutes"],
        work_sec=msg.get("work_sec"),
        pause_sec=msg.get("pause_sec"),
    )
    connection.send_result(msg["id"], {"ends_at": ends_at})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aroma_link/timed_run_cancel",
        vol.Required("device_id"): str,
    }
)
@websocket_api.async_response
async def ws_timed_run_cancel(hass, connection, msg):
    """Cancel a timed run's auto-off (device is left as-is)."""
    entry_data = _entry_data_for_device(hass, msg["device_id"])
    if entry_data is None:
        connection.send_error(msg["id"], ERR_UNKNOWN_DEVICE, "Unknown device_id")
        return
    await entry_data["timed_runs"].async_cancel(msg["device_id"])
    connection.send_result(msg["id"], {})


_ENTITY_SUFFIXES = {
    "power": ("switch", "switch"),
    "fan": ("switch", "fan"),
    "schedule_enabled": ("switch", "schedule_active"),
    "night_owl": ("switch", "night_owl"),
    "work_number": ("number", "work_duration"),
    "pause_number": ("number", "pause_duration"),
    "scheduled_on": ("binary_sensor", "scheduled_on"),
    "oil_level": ("sensor", "oil_level"),
    "oil_remaining": ("sensor", "oil_remaining"),
    "refill_button": ("button", "oil_refill_keep_calibration"),
}


@websocket_api.websocket_command({vol.Required("type"): "aroma_link/list_devices"})
@websocket_api.async_response
async def ws_list_devices(hass, connection, msg):
    """Enumerate Aroma-Link devices with their resolved entity_ids.

    Entity ids are looked up by unique_id in the registry, so the card
    survives entity renames.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    devices: list[dict[str, Any]] = []
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        username = entry_data.get("username", "")
        for device_id, coordinator in (entry_data.get("device_coordinators") or {}).items():
            entities = {}
            for key, (platform, suffix) in _ENTITY_SUFFIXES.items():
                entity_id = registry.async_get_entity_id(
                    platform, DOMAIN, f"{username}_{device_id}_{suffix}"
                )
                if entity_id:
                    entities[key] = entity_id
            devices.append(
                {
                    "device_id": str(device_id),
                    "name": coordinator.device_name,
                    "entities": entities,
                }
            )
    connection.send_result(msg["id"], {"devices": devices})


def _kick_reconciler(entry_data: dict, device_id: str, reason: str) -> None:
    reconciler = (entry_data.get("reconcilers") or {}).get(str(device_id))
    if reconciler:
        reconciler.async_request_sync(reason)


COMMANDS = (
    ws_get_schedule,
    ws_save_schedule,
    ws_set_night_owl_days,
    ws_set_flags,
    ws_get_status,
    ws_timed_run_start,
    ws_timed_run_cancel,
    ws_sync_now,
    ws_list_devices,
)


def async_register(hass: HomeAssistant) -> None:
    """Register all websocket commands (idempotent per HA run)."""
    if hass.data.get(f"{DOMAIN}_ws_registered"):
        return
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
    hass.data[f"{DOMAIN}_ws_registered"] = True
