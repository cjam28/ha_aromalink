"""ScheduleReconciler — the ONLY code path that writes device schedule slots.

Compiles the persisted DeviceModel into the 5-slot wire format and pushes it,
serialized per device, with verify-after-write tuned to the cloud's slow
(15-20 s) acknowledgement. Runtime gating never calls this module: power is
the runtime lever; slots change only when the model changes (or drift is
detected against what we believe the device holds).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .models import (
    CloudSlot,
    RunOverlay,
    Weekday,
    compile_week,
    day_hash,
    to_cloud_day,
    today,
)
from .store import SYNC_ERROR, SYNC_PENDING, SYNC_SYNCED, AromaLinkStore

if TYPE_CHECKING:
    from .AromaLinkDeviceCoordinator import AromaLinkDeviceCoordinator

_LOGGER = logging.getLogger(__name__)

# The cloud serves stale schedule reads for a while after a write; first
# verification read waits out the ack window, then backs off.
VERIFY_DELAYS_SECONDS = (20, 20, 40)
DEBOUNCE_SECONDS = 2.0


def _fetched_day_to_slots(workset: list[dict]) -> list[CloudSlot]:
    """Convert fetch_workset_for_day output into CloudSlots for hashing."""
    slots = []
    for slot in workset[:5]:
        slots.append(
            CloudSlot(
                start_time=str(slot.get("start_time") or "00:00"),
                end_time=str(slot.get("end_time") or "23:59"),
                enabled=1 if slot.get("enabled") else 0,
                consistence_level=str(slot.get("level") or "1"),
                work_duration=str(slot.get("work_sec") or "0"),
                pause_duration=str(slot.get("pause_sec") or "0"),
            )
        )
    return slots


def _normalize_for_compare(slots: list[CloudSlot]) -> list[tuple]:
    """Reduce slots to comparable tuples, tolerating cosmetic differences.

    The cloud echoes '24:00' for '23:59' end times on some firmware and may
    zero-pad times differently, so compare parsed values rather than strings.
    Disabled slots are compared only on their enabled bit: the device is free
    to keep whatever stale times/durations it wants in a slot that can never
    run.
    """
    def norm_time(value: str) -> tuple[int, int]:
        try:
            hh, mm = value.split(":")
            hh_i, mm_i = int(hh), int(mm)
            if (hh_i, mm_i) in ((24, 0), (23, 59)):
                return (23, 59)
            return (hh_i, mm_i)
        except (ValueError, AttributeError):
            return (-1, -1)

    normalized = []
    for slot in slots:
        if not slot.enabled:
            normalized.append((0,))
            continue
        normalized.append(
            (
                1,
                norm_time(slot.start_time),
                norm_time(slot.end_time),
                int(slot.consistence_level or 1),
                int(slot.work_duration or 0),
                int(slot.pause_duration or 0),
            )
        )
    return normalized


class ScheduleReconciler:
    """Per-device desired-state pusher with verify-after-write."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: "AromaLinkDeviceCoordinator",
        store: AromaLinkStore,
        device_id: str,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._store = store
        self._device_id = str(device_id)
        self._lock = asyncio.Lock()
        self._rerun_requested = False
        self._task: asyncio.Task | None = None
        self._overlay: RunOverlay | None = None
        self._overlay_day: Weekday | None = None
        self._stopped = False

    # ------------------------------------------------------------- public API

    def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()

    @property
    def overlay(self) -> RunOverlay | None:
        return self._overlay

    def async_request_sync(self, reason: str) -> None:
        """Kick a (coalesced) sync loop run."""
        if self._stopped:
            return
        _LOGGER.debug("Sync requested for %s (%s)", self._device_id, reason)
        if self._task and not self._task.done():
            self._rerun_requested = True
            return
        self._task = self._hass.async_create_task(self._sync_loop())

    async def async_set_overlay(self, overlay: RunOverlay | None) -> None:
        """Install/clear the timed-run overlay and push the resulting slots."""
        self._overlay = overlay
        self._overlay_day = today(_now(self._hass)) if overlay else None
        self.async_request_sync("overlay" if overlay else "overlay_cleared")

    async def async_check_drift(self, force_push: bool = False) -> bool:
        """Read all 7 days and resync when the device disagrees with the model.

        Returns True when drift was found (and a repush kicked off).
        """
        expected = self._compiled()
        drifted_days = []
        for day in Weekday:
            fetched = await self._coordinator.fetch_workset_for_day(to_cloud_day(day))
            if fetched is None:
                _LOGGER.warning(
                    "Drift check for %s aborted: day %s unreadable",
                    self._device_id,
                    day.name,
                )
                return False
            if _normalize_for_compare(_fetched_day_to_slots(fetched)) != _normalize_for_compare(
                expected[day]
            ):
                drifted_days.append(day)

        if drifted_days:
            _LOGGER.warning(
                "Schedule drift on device %s (days %s); repushing model",
                self._device_id,
                [d.name for d in drifted_days],
            )
            self.async_request_sync("drift")
            return True
        if force_push:
            self.async_request_sync("manual")
        return False

    # ------------------------------------------------------------- internals

    def _compiled(self) -> dict[Weekday, list[CloudSlot]]:
        model = self._store.get_model(self._device_id)
        return compile_week(model, overlay=self._overlay, overlay_day=self._overlay_day)

    async def _sync_loop(self) -> None:
        try:
            while True:
                self._rerun_requested = False
                async with self._lock:
                    await self._push_and_verify()
                if not self._rerun_requested or self._stopped:
                    return
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception as err:  # noqa: BLE001 - must never crash HA
            _LOGGER.exception("Sync loop failed for device %s", self._device_id)
            await self._store.async_set_sync(
                self._device_id, SYNC_ERROR, last_error=str(err)
            )

    async def _push_and_verify(self) -> None:
        await asyncio.sleep(DEBOUNCE_SECONDS)  # coalesce rapid successive edits
        target_version = self._store.get_model(self._device_id).schedule.version
        compiled = self._compiled()
        await self._store.async_set_sync(self._device_id, SYNC_PENDING)

        if not await self._write_all(compiled):
            await self._store.async_set_sync(
                self._device_id,
                SYNC_ERROR,
                last_error="device write failed",
            )
            return

        # Verify one representative day per identical-payload group, waiting
        # out the cloud's ack window between attempts. One full re-push is
        # allowed before declaring an error.
        for attempt, delay in enumerate(VERIFY_DELAYS_SECONDS):
            await asyncio.sleep(delay)
            if self._rerun_requested:
                return  # a newer model is queued; don't fight it
            mismatched = await self._verify_representatives(compiled)
            if mismatched is None:
                # verification read failed; try again on next delay
                continue
            if not mismatched:
                await self._store.async_set_sync(
                    self._device_id, SYNC_SYNCED, synced_version=target_version
                )
                _LOGGER.info(
                    "Device %s schedule synced (version %s)",
                    self._device_id,
                    target_version,
                )
                return
            if attempt == 1:
                _LOGGER.warning(
                    "Device %s still serving stale schedule for %s; re-pushing once",
                    self._device_id,
                    [d.name for d in mismatched],
                )
                if not await self._write_all(compiled, only_days=mismatched):
                    break

        await self._store.async_set_sync(
            self._device_id,
            SYNC_ERROR,
            last_error="device did not confirm the pushed schedule",
        )

    def _group_days(
        self, compiled: dict[Weekday, list[CloudSlot]]
    ) -> list[tuple[list[Weekday], list[CloudSlot]]]:
        """Group days with byte-identical slot lists into single writes."""
        groups: dict[str, tuple[list[Weekday], list[CloudSlot]]] = {}
        for day in Weekday:
            key = day_hash(compiled[day])
            groups.setdefault(key, ([], compiled[day]))[0].append(day)
        return list(groups.values())

    async def _write_all(
        self,
        compiled: dict[Weekday, list[CloudSlot]],
        only_days: list[Weekday] | None = None,
    ) -> bool:
        ok = True
        for days, slots in self._group_days(compiled):
            if only_days is not None:
                days = [d for d in days if d in only_days]
                if not days:
                    continue
            cloud_days = [to_cloud_day(d) for d in days]
            payloads = [slot.to_payload() for slot in slots]
            if not await self._coordinator.async_write_cloud_days(cloud_days, payloads):
                ok = False
        return ok

    async def _verify_representatives(
        self, compiled: dict[Weekday, list[CloudSlot]]
    ) -> list[Weekday] | None:
        """Read one day per group; return mismatched groups' days, or None on read failure."""
        mismatched: list[Weekday] = []
        for days, slots in self._group_days(compiled):
            representative = days[0]
            fetched = await self._coordinator.fetch_workset_for_day(
                to_cloud_day(representative)
            )
            if fetched is None:
                return None
            if _normalize_for_compare(_fetched_day_to_slots(fetched)) != _normalize_for_compare(slots):
                mismatched.extend(days)
        return mismatched


def _now(hass: HomeAssistant):
    from homeassistant.util import dt as dt_util

    return dt_util.now()
