"""Domain services for Aroma-Link (v3).

Automation/script surface only — schedule editing goes through the card's
websocket API. Services here are thin wrappers over the timed-run manager,
the reconciler, and the coordinator's oil-tracking methods.
"""
from __future__ import annotations

import logging

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    DOMAIN,
    SERVICE_API_DIAGNOSTICS,
    SERVICE_CANCEL_TIMED_RUN,
    SERVICE_OIL_CALIBRATE,
    SERVICE_OIL_REFILL,
    SERVICE_START_TIMED_RUN,
    SERVICE_SYNC_SCHEDULES,
)

_LOGGER = logging.getLogger(__name__)

START_TIMED_RUN_SCHEMA = vol.Schema({
    vol.Optional("device_id"): cv.string,
    vol.Required("duration_minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=24 * 60)),
    vol.Optional("work_sec"): vol.All(vol.Coerce(int), vol.Range(min=5, max=900)),
    vol.Optional("pause_sec"): vol.All(vol.Coerce(int), vol.Range(min=5, max=900)),
})

DEVICE_ONLY_SCHEMA = vol.Schema({
    vol.Optional("device_id"): cv.string,
})

OIL_REFILL_SCHEMA = vol.Schema({
    vol.Optional("device_id"): cv.string,
    vol.Optional("fill_volume"): vol.All(vol.Coerce(int), vol.Range(min=1, max=5000)),
    vol.Optional("fill_date"): cv.string,
    vol.Optional("keep_calibration", default=True): cv.boolean,
})

OIL_CALIBRATE_SCHEMA = vol.Schema({
    vol.Optional("device_id"): cv.string,
    vol.Required("action"): vol.In(["start", "end", "finalize", "manual", "set"]),
    vol.Optional("measured_remaining"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("bottle_capacity"): vol.All(vol.Coerce(int), vol.Range(min=1, max=5000)),
    vol.Optional("fill_volume"): vol.All(vol.Coerce(int), vol.Range(min=1, max=5000)),
    vol.Optional("manual_rate_ml_per_hour"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("manual_start_volume"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("manual_end_volume"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("manual_runtime_hours"): vol.All(vol.Coerce(float), vol.Range(min=0)),
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


def _resolve(hass: HomeAssistant, call: ServiceCall):
    """Resolve (entry_data, device_id, coordinator) from a service call."""
    device_id = call.data.get("device_id")
    candidates = []
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        coordinators = entry_data.get("device_coordinators") or {}
        if device_id is not None:
            if str(device_id) in coordinators:
                return entry_data, str(device_id), coordinators[str(device_id)]
        else:
            for dev_id, coordinator in coordinators.items():
                candidates.append((entry_data, dev_id, coordinator))
    if device_id is not None:
        raise vol.Invalid(f"Unknown device_id {device_id}")
    if len(candidates) == 1:
        return candidates[0]
    raise vol.Invalid("Multiple devices available; specify device_id")


def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services (idempotent per HA run)."""
    if hass.services.has_service(DOMAIN, SERVICE_START_TIMED_RUN):
        return

    async def start_timed_run(call: ServiceCall):
        entry_data, device_id, _ = _resolve(hass, call)
        manager = entry_data["timed_runs"]
        await manager.async_start(
            device_id,
            call.data["duration_minutes"],
            work_sec=call.data.get("work_sec"),
            pause_sec=call.data.get("pause_sec"),
        )

    async def cancel_timed_run(call: ServiceCall):
        entry_data, device_id, _ = _resolve(hass, call)
        await entry_data["timed_runs"].async_cancel(device_id)

    async def sync_schedules(call: ServiceCall):
        entry_data, device_id, _ = _resolve(hass, call)
        reconciler = (entry_data.get("reconcilers") or {}).get(device_id)
        if reconciler:
            await reconciler.async_check_drift(force_push=True)

    async def oil_refill(call: ServiceCall):
        entry_data, device_id, coordinator = _resolve(hass, call)
        updates = {}
        if call.data.get("fill_volume") is not None:
            updates["fill_volume"] = call.data["fill_volume"]
        if call.data.get("fill_date") is not None:
            updates["fill_date"] = call.data["fill_date"]
        if updates:
            coordinator.set_oil_calibration(**updates)
        if call.data.get("keep_calibration", True):
            coordinator.refill_keep_calibration()
        else:
            coordinator.reset_oil_tracking()
            coordinator.set_calibration_state("Idle")
        coordinator.async_update_listeners()
        await entry_data["store"].async_save_oil(
            device_id, coordinator.export_oil_state()
        )

    async def oil_calibrate(call: ServiceCall):
        entry_data, device_id, coordinator = _resolve(hass, call)
        action = call.data["action"]

        settable = (
            "bottle_capacity",
            "fill_volume",
            "measured_remaining",
            "manual_rate_ml_per_hour",
            "manual_start_volume",
            "manual_end_volume",
            "manual_runtime_hours",
        )
        updates = {
            key: call.data[key] for key in settable if call.data.get(key) is not None
        }
        if updates:
            coordinator.set_oil_calibration(**updates)

        if action == "start":
            coordinator.start_calibration_measurement()
        elif action == "end":
            coordinator.end_calibration_measurement()
        elif action == "finalize":
            rate = coordinator.finalize_calibration()
            if rate is None:
                _LOGGER.warning(
                    "oil_calibrate finalize for %s did not produce a usage rate "
                    "(check measured_remaining / runtime)",
                    device_id,
                )
        elif action == "manual":
            coordinator.apply_manual_override()
        # action == "set": updates alone

        coordinator.async_update_listeners()
        await entry_data["store"].async_save_oil(
            device_id, coordinator.export_oil_state()
        )

    async def api_diagnostics(call: ServiceCall):
        _entry_data, _device_id, coordinator = _resolve(hass, call)
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
        DOMAIN, SERVICE_START_TIMED_RUN, start_timed_run, START_TIMED_RUN_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_TIMED_RUN, cancel_timed_run, DEVICE_ONLY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_SCHEDULES, sync_schedules, DEVICE_ONLY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_OIL_REFILL, oil_refill, OIL_REFILL_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_OIL_CALIBRATE, oil_calibrate, OIL_CALIBRATE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_API_DIAGNOSTICS, api_diagnostics, API_DIAGNOSTICS_SCHEMA
    )
