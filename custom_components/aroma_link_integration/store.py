"""Persisted state for the Aroma-Link integration (schema v2).

One HA Store per config entry holds, per device:

- ``model``      — the desired-state :class:`~.models.DeviceModel` (schedule,
                   Night Owl settings, master flags). The card edits THIS,
                   instantly; the reconciler pushes it to the device.
- ``sync``       — reconciler push status (synced/pending/error + metadata).
- ``oil``        — the oil tracking/calibration payload, verbatim in the shape
                   ``AromaLinkDeviceCoordinator.export_oil_state`` produces.
- ``timed_run``  — persisted timed-run end time so runs survive HA restarts.

All mutations bump nothing implicitly: schedule saves bump the schedule
version; every mutation notifies the (single) change listener, which is how
the ``aroma_link_integration_updated`` bus event gets fired without this
module importing the event layer.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .models import DeviceModel, NightOwlSettings, WeeklySchedule

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 2
SAVE_DELAY_SECONDS = 1.0

SYNC_SYNCED = "synced"
SYNC_PENDING = "pending"
SYNC_ERROR = "error"


@dataclass
class SyncStatus:
    state: str = SYNC_PENDING
    synced_version: int | None = None
    synced_at: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "synced_version": self.synced_version,
            "synced_at": self.synced_at,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "SyncStatus":
        data = data or {}
        return cls(
            state=data.get("state", SYNC_PENDING),
            synced_version=data.get("synced_version"),
            synced_at=data.get("synced_at"),
            last_error=data.get("last_error"),
        )


@dataclass
class TimedRunState:
    ends_at: str  # ISO UTC
    work_sec: int | None = None
    pause_sec: int | None = None
    duration_minutes: int | None = None

    def to_dict(self) -> dict:
        return {
            "ends_at": self.ends_at,
            "work_sec": self.work_sec,
            "pause_sec": self.pause_sec,
            "duration_minutes": self.duration_minutes,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "TimedRunState | None":
        if not data or not data.get("ends_at"):
            return None
        return cls(
            ends_at=data["ends_at"],
            work_sec=data.get("work_sec"),
            pause_sec=data.get("pause_sec"),
            duration_minutes=data.get("duration_minutes"),
        )


class AromaLinkStore:
    """Owner of the persisted per-device desired state."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry_id}")
        self._data: dict[str, Any] = {"devices": {}}
        # Parsed-model cache: get_model is on hot paths (engine evaluation,
        # entity renders); reconstructing the dataclass tree per call is waste.
        self._model_cache: dict[str, DeviceModel] = {}
        # change listener: (device_id, change, version) -> None
        self._on_change: Callable[[str, str, int | None], None] | None = None

    # ------------------------------------------------------------- lifecycle

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self._data = stored
        self._data.setdefault("devices", {})

    def _schedule_save(self) -> None:
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY_SECONDS)

    async def async_save_now(self) -> None:
        await self._store.async_save(self._data)

    def set_change_listener(
        self, listener: Callable[[str, str, int | None], None]
    ) -> None:
        self._on_change = listener

    def _notify(self, device_id: str, change: str, version: int | None = None) -> None:
        if self._on_change is not None:
            try:
                self._on_change(device_id, change, version)
            except Exception:  # pragma: no cover - listener bugs must not break saves
                _LOGGER.exception("Store change listener failed")

    # ------------------------------------------------------------- device access

    def has_device(self, device_id: str) -> bool:
        return str(device_id) in self._data["devices"]

    def device_ids(self) -> list[str]:
        return list(self._data["devices"])

    def _device(self, device_id: str) -> dict:
        return self._data["devices"].setdefault(str(device_id), {})

    # ------------------------------------------------------------- model

    def get_model(self, device_id: str) -> DeviceModel:
        key = str(device_id)
        cached = self._model_cache.get(key)
        if cached is not None:
            return cached
        raw = self._device(device_id).get("model")
        model = DeviceModel() if raw is None else DeviceModel.from_dict(raw)
        self._model_cache[key] = model
        return model

    def _set_model(self, device_id: str, model: DeviceModel) -> None:
        self._device(device_id)["model"] = model.to_dict()
        self._model_cache[str(device_id)] = model

    async def async_seed_device(
        self,
        device_id: str,
        model: DeviceModel,
        *,
        oil: dict | None = None,
        import_pending: bool = False,
        notify: bool = True,
    ) -> None:
        """Install an initial model for a device (migration / first sight)."""
        dev = self._device(device_id)
        dev["model"] = model.to_dict()
        self._model_cache[str(device_id)] = model
        dev["sync"] = SyncStatus(state=SYNC_ERROR if import_pending else SYNC_PENDING).to_dict()
        dev["import_pending"] = import_pending
        if oil is not None:
            dev["oil"] = oil
        self._schedule_save()
        if notify:
            self._notify(str(device_id), "schedule", model.schedule.version)

    def is_import_pending(self, device_id: str) -> bool:
        return bool(self._device(device_id).get("import_pending"))

    async def async_clear_import_pending(self, device_id: str) -> None:
        self._device(device_id)["import_pending"] = False
        self._schedule_save()

    async def async_save_schedule(
        self,
        device_id: str,
        schedule: WeeklySchedule,
        night_owl: NightOwlSettings | None = None,
    ) -> int:
        """Persist a new schedule (and optionally Night Owl settings); returns new version."""
        model = self.get_model(device_id)
        new_version = model.schedule.version + 1
        schedule.version = new_version
        schedule.updated_at = dt_util.utcnow().isoformat()
        model.schedule = schedule
        if night_owl is not None:
            model.night_owl = night_owl
        self._set_model(device_id, model)
        await self._async_mark_pending(device_id)
        self._schedule_save()
        self._notify(str(device_id), "schedule", new_version)
        return new_version

    async def async_set_night_owl_days(
        self, device_id: str, days: dict[int, bool]
    ) -> int:
        """Set per-day Night Owl flags (canonical Mon=0 keys); returns new version."""
        model = self.get_model(device_id)
        schedule = model.schedule
        from .models import Weekday  # local import keeps module load order simple

        for day_int, enabled in days.items():
            schedule.days[Weekday(int(day_int))].night_owl = bool(enabled)
        return await self.async_save_schedule(device_id, schedule)

    async def async_set_flags(
        self,
        device_id: str,
        schedule_enabled: bool | None = None,
        night_owl_enabled: bool | None = None,
    ) -> int:
        """Update master flags. Bumps the schedule version only when the compiled
        output can change (night_owl_enabled affects slot 5)."""
        model = self.get_model(device_id)
        changed_compile = False
        if schedule_enabled is not None:
            model.schedule_enabled = bool(schedule_enabled)
        if night_owl_enabled is not None and model.night_owl_enabled != bool(night_owl_enabled):
            model.night_owl_enabled = bool(night_owl_enabled)
            changed_compile = True
        if changed_compile:
            model.schedule.version += 1
            model.schedule.updated_at = dt_util.utcnow().isoformat()
            self._set_model(device_id, model)
            await self._async_mark_pending(device_id)
        else:
            self._set_model(device_id, model)
        self._schedule_save()
        self._notify(str(device_id), "flags", model.schedule.version)
        return model.schedule.version

    async def async_set_defaults(
        self,
        device_id: str,
        work_sec: int | None = None,
        pause_sec: int | None = None,
    ) -> None:
        """Update the default work/pause used for timed runs and new windows."""
        model = self.get_model(device_id)
        if work_sec is not None:
            model.default_work_sec = int(work_sec)
        if pause_sec is not None:
            model.default_pause_sec = int(pause_sec)
        self._set_model(device_id, model)
        self._schedule_save()
        self._notify(str(device_id), "flags", model.schedule.version)

    # ------------------------------------------------------------- sync status

    def get_sync(self, device_id: str) -> SyncStatus:
        return SyncStatus.from_dict(self._device(device_id).get("sync"))

    async def _async_mark_pending(self, device_id: str) -> None:
        sync = self.get_sync(device_id)
        sync.state = SYNC_PENDING
        self._device(device_id)["sync"] = sync.to_dict()

    async def async_set_sync(
        self,
        device_id: str,
        state: str,
        synced_version: int | None = None,
        last_error: str | None = None,
    ) -> None:
        sync = self.get_sync(device_id)
        sync.state = state
        sync.last_error = last_error
        if state == SYNC_SYNCED:
            sync.synced_version = synced_version
            sync.synced_at = dt_util.utcnow().isoformat()
        self._device(device_id)["sync"] = sync.to_dict()
        self._schedule_save()
        self._notify(str(device_id), "sync", sync.synced_version)

    # ------------------------------------------------------------- oil

    def get_oil(self, device_id: str) -> dict | None:
        return self._device(device_id).get("oil")

    async def async_save_oil(self, device_id: str, oil: dict, notify: bool = True) -> None:
        self._device(device_id)["oil"] = oil
        self._schedule_save()
        if notify:
            self._notify(str(device_id), "oil", None)

    # ------------------------------------------------------------- timed runs

    def get_timed_run(self, device_id: str) -> TimedRunState | None:
        return TimedRunState.from_dict(self._device(device_id).get("timed_run"))

    async def async_set_timed_run(
        self, device_id: str, state: TimedRunState | None
    ) -> None:
        self._device(device_id)["timed_run"] = state.to_dict() if state else None
        self._schedule_save()
        self._notify(str(device_id), "timed_run", None)
