"""One-time import of legacy (v2.x) state into the v3 Store.

Sources merged per device:

1. The device's actual cloud schedule (7 fetched days, slots 1-4 -> windows,
   slot 5 -> Night Owl settings seed).
2. The legacy oil-state Store ``{DOMAIN}_oil_state_{entry_id}.json``:
   - the oil payload is carried over verbatim,
   - ``night_owl_per_day`` (cloud-day keyed, Sun=0) becomes each day's
     ``night_owl`` flag,
   - ``saved_enabled_state`` (cloud-day keyed lists of 4 bools — the old
     "user intent" snapshots) overrides the device's enabled bits, because
     the old blueprints routinely left device bits automation-disabled.

The import is idempotent per device (skipped when the new store already has
the device). If the cloud fetch fails, an empty model is seeded with
``import_pending`` so the next setup retries. The legacy oil store file is
NOT removed here — the old code path still reads it until the v3 cutover
completes (removed at the end of setup once every device imported cleanly
and the coordinators read oil from the new store).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .models import (
    MAX_WINDOWS,
    LETTER_LEVELS,
    DeviceModel,
    NightOwlSettings,
    PAUSE_SEC_MAX,
    PAUSE_SEC_MIN,
    ScheduleWindow,
    WORK_SEC_MAX,
    WORK_SEC_MIN,
    Weekday,
    from_cloud_day,
)
from .store import AromaLinkStore

_LOGGER = logging.getLogger(__name__)

# Fillers historically written by the old code for unused slots.
_FILLER_STARTS = {"00:00"}
_FILLER_ENDS = {"23:59", "24:00"}
_FILLER_PAUSES = {120, 900}


def _coerce_level(value: Any) -> int:
    if isinstance(value, str) and value.upper() in LETTER_LEVELS:
        return LETTER_LEVELS[value.upper()]
    try:
        level = int(value)
    except (TypeError, ValueError):
        return 1
    return level if level in (1, 2, 3) else 1


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, v))


def _is_legacy_filler(slot: dict) -> bool:
    return (
        not slot.get("enabled")
        and slot.get("start_time") in _FILLER_STARTS
        and slot.get("end_time") in _FILLER_ENDS
        and int(slot.get("work_sec") or 0) == 10
        and int(slot.get("pause_sec") or 0) in _FILLER_PAUSES
    )


def _slot_to_window(slot: dict, enabled_override: bool | None) -> ScheduleWindow:
    enabled = bool(slot.get("enabled")) if enabled_override is None else enabled_override
    return ScheduleWindow(
        start=str(slot.get("start_time") or "00:00"),
        end=str(slot.get("end_time") or "23:59").replace("24:00", "23:59"),
        work_sec=_clamp(slot.get("work_sec"), WORK_SEC_MIN, WORK_SEC_MAX, 10),
        pause_sec=_clamp(slot.get("pause_sec"), PAUSE_SEC_MIN, PAUSE_SEC_MAX, 300),
        level=_coerce_level(slot.get("level")),
        enabled=enabled,
    )


def _lookup_cloud_keyed(mapping: dict | None, cloud_day: int):
    """Legacy dicts are keyed by cloud day as int or (post-JSON) str."""
    if not mapping:
        return None
    if cloud_day in mapping:
        return mapping[cloud_day]
    return mapping.get(str(cloud_day))


def _seed_night_owl(slot5_by_day: dict[Weekday, dict]) -> NightOwlSettings:
    """Seed NightOwlSettings from the device's slot-5 times when customized."""
    for slot in slot5_by_day.values():
        start = str(slot.get("start_time") or "00:00")
        end = str(slot.get("end_time") or "23:59")
        if start not in _FILLER_STARTS or end not in _FILLER_ENDS:
            return NightOwlSettings(
                mode="fixed",
                fixed_start=start,
                fixed_end=end.replace("24:00", "23:59"),
                work_sec=_clamp(slot.get("work_sec"), WORK_SEC_MIN, WORK_SEC_MAX, 10),
                pause_sec=_clamp(slot.get("pause_sec"), PAUSE_SEC_MIN, PAUSE_SEC_MAX, 300),
                level=_coerce_level(slot.get("level")),
            )
    return NightOwlSettings()


async def async_import_legacy(
    hass: HomeAssistant,
    entry_id: str,
    store: AromaLinkStore,
    device_coordinators: dict[str, Any],
) -> None:
    """Import legacy state for every device the new store doesn't know yet."""
    pending = [
        (device_id, coordinator)
        for device_id, coordinator in device_coordinators.items()
        if not store.has_device(device_id) or store.is_import_pending(device_id)
    ]
    if not pending:
        return

    legacy_store = Store(hass, 1, f"{DOMAIN}_oil_state_{entry_id}.json")
    legacy_data = await legacy_store.async_load() or {}

    for device_id, coordinator in pending:
        legacy_dev = legacy_data.get(str(device_id)) or legacy_data.get(device_id) or {}
        night_owl_per_day = legacy_dev.get("night_owl_per_day") or {}
        saved_enabled = legacy_dev.get("saved_enabled_state") or {}

        model = DeviceModel()
        fetch_failed = False
        slot5_by_day: dict[Weekday, dict] = {}

        for cloud_day in range(7):
            workset = await coordinator.fetch_workset_for_day(cloud_day)
            if workset is None:
                fetch_failed = True
                break
            day = from_cloud_day(cloud_day)
            intent = _lookup_cloud_keyed(saved_enabled, cloud_day)
            windows = []
            for idx, slot in enumerate(workset[:MAX_WINDOWS]):
                if _is_legacy_filler(slot):
                    continue
                override = None
                if isinstance(intent, (list, tuple)) and idx < len(intent):
                    override = bool(intent[idx])
                windows.append(_slot_to_window(slot, override))
            model.schedule.days[day].windows = windows
            model.schedule.days[day].night_owl = bool(
                _lookup_cloud_keyed(night_owl_per_day, cloud_day)
            )
            if len(workset) > MAX_WINDOWS:
                slot5_by_day[day] = workset[MAX_WINDOWS]

        if fetch_failed:
            _LOGGER.warning(
                "Legacy import for device %s could not fetch the cloud schedule; "
                "seeding empty model and retrying on next setup",
                device_id,
            )
            await store.async_seed_device(
                device_id,
                DeviceModel(),
                oil=legacy_dev or None,
                import_pending=True,
                notify=False,
            )
            continue

        model.night_owl = _seed_night_owl(slot5_by_day)
        model.default_work_sec = _clamp(
            legacy_dev.get("prev_work_duration"), WORK_SEC_MIN, WORK_SEC_MAX, 10
        )
        model.default_pause_sec = _clamp(
            legacy_dev.get("prev_pause_duration"), PAUSE_SEC_MIN, PAUSE_SEC_MAX, 300
        )
        # Preserve the configured linger default; nothing legacy maps to it.
        model.schedule.version = 1
        model.schedule.updated_at = dt_util.utcnow().isoformat()

        await store.async_seed_device(
            device_id,
            model,
            oil=legacy_dev or None,
            import_pending=False,
            notify=False,
        )
        _LOGGER.info(
            "Imported legacy state for device %s: %d windows across the week, "
            "night_owl days=%s, night_owl mode=%s",
            device_id,
            sum(len(model.schedule.days[d].windows) for d in Weekday),
            [int(d) for d in Weekday if model.schedule.days[d].night_owl],
            model.night_owl.mode,
        )

    await store.async_save_now()


async def async_remove_legacy_store(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the old oil-state store file (call only after full cutover)."""
    legacy_store = Store(hass, 1, f"{DOMAIN}_oil_state_{entry_id}.json")
    await legacy_store.async_remove()


# unique_id suffixes of v2.x entities that no longer exist in v3.
REMOVED_UNIQUE_ID_SUFFIXES = (
    "program_enabled",
    "program_day_0",
    "program_day_1",
    "program_day_2",
    "program_day_3",
    "program_day_4",
    "program_day_5",
    "program_day_6",
    "program_selector",
    "program_day_selector",
    "program_level",
    "program_start_time",
    "program_end_time",
    "program_work_duration",
    "program_pause_duration",
    "save_program",
    "save_settings",
    "sync_schedules",
    "run",
    "schedule_matrix",
    "work_remaining_time",
    "pause_remaining_time",
    "oil_bottle_capacity",
    "oil_fill_volume",
    "oil_remaining_input",
    "oil_manual_start_volume",
    "oil_manual_end_volume",
    "oil_manual_runtime_hours",
    "oil_manual_rate",
    "oil_calibration_state",
    "oil_calibration_toggle",
    "oil_calibration_finalize",
    "oil_manual_override",
    "oil_fill_date",
    "diffuse_time",
)


def cleanup_removed_entities(hass: HomeAssistant, entry) -> int:
    """Remove registry entries for entities pruned in v3. Returns count removed."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    removed = 0
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = reg_entry.unique_id or ""
        if any(unique_id.endswith(f"_{suffix}") for suffix in REMOVED_UNIQUE_ID_SUFFIXES):
            registry.async_remove(reg_entry.entity_id)
            removed += 1
    if removed:
        _LOGGER.info("Removed %d obsolete v2.x entities from the registry", removed)
    return removed
