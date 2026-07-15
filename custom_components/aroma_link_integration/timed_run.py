"""TimedRunManager — restart-surviving 'run for N minutes' support.

The run's end time is persisted in the Store, so an HA restart mid-run
re-arms the auto-off (or turns the device off immediately when the end time
already passed while HA was down). While a run is active:

- slot 5 carries a 24/7 overlay (via the reconciler) so power-on diffuses
  regardless of schedule windows,
- the gating engine sees the persisted run and holds power ON until expiry.

Expiry turns the device off and clears the overlay; cancel keeps the device
running (matching the old semantics) but clears the run and overlay so the
gating engine resumes ownership on its next evaluation.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from .models import RunOverlay
from .store import AromaLinkStore, TimedRunState

_LOGGER = logging.getLogger(__name__)


class TimedRunManager:
    """Per-config-entry manager of persisted timed runs."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: AromaLinkStore,
        device_coordinators: dict,
        reconcilers: dict,
    ) -> None:
        self._hass = hass
        self._store = store
        self._coordinators = device_coordinators
        self._reconcilers = reconcilers
        self._timers: dict[str, callable] = {}

    # ------------------------------------------------------------- lifecycle

    async def async_restore(self) -> None:
        """Re-arm (or expire) persisted runs after a restart."""
        for device_id in list(self._coordinators):
            run = self._store.get_timed_run(device_id)
            if run is None:
                continue
            ends_at = dt_util.parse_datetime(run.ends_at)
            if ends_at is None or ends_at <= dt_util.utcnow():
                _LOGGER.info(
                    "Timed run for device %s expired while HA was down; turning off",
                    device_id,
                )
                await self._expire(device_id)
            else:
                _LOGGER.info(
                    "Re-arming timed run for device %s (ends %s)", device_id, ends_at
                )
                reconciler = self._reconcilers.get(str(device_id))
                if reconciler and run.work_sec:
                    await reconciler.async_set_overlay(
                        RunOverlay(work_sec=run.work_sec, pause_sec=run.pause_sec or 300)
                    )
                self._arm(device_id, ends_at)

    def stop(self) -> None:
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    # ------------------------------------------------------------- public API

    async def async_start(
        self,
        device_id: str,
        duration_minutes: int,
        work_sec: int | None = None,
        pause_sec: int | None = None,
    ) -> str:
        """Start (or replace) a timed run; returns the ISO end time."""
        device_id = str(device_id)
        coordinator = self._coordinators.get(device_id)
        if coordinator is None:
            raise ValueError(f"Unknown device {device_id}")

        self._disarm(device_id)

        model = self._store.get_model(device_id)
        work = int(work_sec) if work_sec else model.default_work_sec
        pause = int(pause_sec) if pause_sec else model.default_pause_sec

        ends_at = dt_util.utcnow() + timedelta(minutes=int(duration_minutes))
        await self._store.async_set_timed_run(
            device_id,
            TimedRunState(
                ends_at=ends_at.isoformat(),
                work_sec=work,
                pause_sec=pause,
                duration_minutes=int(duration_minutes),
            ),
        )

        # Slot-5 overlay guarantees power-on diffuses even outside windows;
        # it is self-healing (cleared overlay restores the canonical compile).
        # Note: the overlay write reaches the device asynchronously (cloud ack
        # ~15-20s), so a run started outside any window begins diffusing once
        # that write lands — same latency the pre-v3 run flow had.
        reconciler = self._reconcilers.get(device_id)
        if reconciler:
            await reconciler.async_set_overlay(RunOverlay(work_sec=work, pause_sec=pause))

        await coordinator.turn_on_off(True)
        self._arm(device_id, ends_at)
        _LOGGER.info(
            "Timed run started for device %s: %s min (work %ss / pause %ss)",
            device_id,
            duration_minutes,
            work,
            pause,
        )
        return ends_at.isoformat()

    async def async_cancel(self, device_id: str) -> None:
        """Cancel the auto-off; the device keeps running until gated/turned off."""
        device_id = str(device_id)
        self._disarm(device_id)
        reconciler = self._reconcilers.get(device_id)
        if reconciler:
            await reconciler.async_set_overlay(None)
        await self._store.async_set_timed_run(device_id, None)
        _LOGGER.info("Timed run cancelled for device %s (device left as-is)", device_id)

    def status(self, device_id: str) -> dict | None:
        run = self._store.get_timed_run(str(device_id))
        if run is None:
            return None
        ends_at = dt_util.parse_datetime(run.ends_at)
        remaining = 0
        if ends_at:
            remaining = max(0, int((ends_at - dt_util.utcnow()).total_seconds()))
        return {
            "active": remaining > 0,
            "ends_at": run.ends_at,
            "remaining_s": remaining,
            "duration_minutes": run.duration_minutes,
            "work_sec": run.work_sec,
            "pause_sec": run.pause_sec,
        }

    # ------------------------------------------------------------- internals

    def _arm(self, device_id: str, ends_at) -> None:
        expected_ends_at = ends_at.isoformat()

        async def _on_expire(_now):
            self._timers.pop(device_id, None)
            # A run started while this expiry was in flight replaces the
            # store entry; only tear down if OUR run is still the active one.
            current = self._store.get_timed_run(device_id)
            if current is None or current.ends_at != expected_ends_at:
                return
            await self._expire(device_id)

        self._timers[device_id] = async_track_point_in_time(
            self._hass, _on_expire, ends_at
        )

    def _disarm(self, device_id: str) -> None:
        cancel = self._timers.pop(device_id, None)
        if cancel:
            cancel()

    async def _expire(self, device_id: str) -> None:
        reconciler = self._reconcilers.get(str(device_id))
        if reconciler:
            # Disarm the overlay BEFORE the off command so the device cannot
            # re-activate itself in the gap (upstream issue #31 lesson).
            await reconciler.async_set_overlay(None)
        coordinator = self._coordinators.get(str(device_id))
        if coordinator:
            await coordinator.turn_on_off(False)
        await self._store.async_set_timed_run(str(device_id), None)
