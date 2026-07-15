"""GatingEngine — native replacement for the three legacy blueprints.

Computes the desired POWER state from the schedule model and the configured
gates, and idempotently commands the device. It never touches schedule slots
(the reconciler owns those): power is the runtime lever, and because the
device only diffuses when power is on AND inside an armed slot window, power
gating yields the same behavior the old enabled-bit rewrites attempted —
without the whole-day write races.

Decision, evaluated on every trigger (state change, coordinator poll tick,
1-minute timer, model change):

    timed run active           -> ON (timed runs outrank gating)
    schedule_enabled == False  -> hands off (user owns power)
    inside a normal window     -> hvac gate AND occupancy gate
    inside Night Owl period    -> motion seen within the linger window
    otherwise                  -> OFF

Gates are per-device config-entry options; unconfigured gates pass.
Transitions are at worst ~60 s late (interval tick) — acceptable for scent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .models import active_window, night_owl_period
from .store import AromaLinkStore

_LOGGER = logging.getLogger(__name__)

EVALUATE_INTERVAL = timedelta(seconds=60)
# Don't fight the optimistic-state shield: skip corrections while a recent
# power command may still be unacknowledged by the cloud.
COMMAND_SETTLE_SECONDS = 30

HVAC_INACTIVE_ACTIONS = {None, "", "idle", "off"}


@dataclass
class GateConfig:
    climate_entity: str | None = None
    occupancy_entity: str | None = None
    motion_entities: list[str] = field(default_factory=list)
    hvac_on_delay_minutes: int = 1

    @classmethod
    def from_options(cls, options: dict, device_id: str) -> "GateConfig":
        gates = (options.get("gates") or {}).get(str(device_id)) or {}
        return cls(
            climate_entity=gates.get("climate_entity") or None,
            occupancy_entity=gates.get("occupancy_entity") or None,
            motion_entities=list(gates.get("motion_entities") or []),
            hvac_on_delay_minutes=int(gates.get("hvac_on_delay_minutes", 1)),
        )

    @property
    def watched_entities(self) -> list[str]:
        entities = []
        if self.climate_entity:
            entities.append(self.climate_entity)
        if self.occupancy_entity:
            entities.append(self.occupancy_entity)
        entities.extend(self.motion_entities)
        return entities


class GatingEngine:
    """Per-device desired-power controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        store: AromaLinkStore,
        device_id: str,
        config: GateConfig,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._store = store
        self._device_id = str(device_id)
        self._config = config
        self._unsubs = []
        self._hvac_active_since: datetime | None = None
        self._last_commanded: bool | None = None
        self._last_commanded_at: datetime | None = None
        self._snapshot: dict = {}

    # ------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        if self._config.watched_entities:
            self._unsubs.append(
                async_track_state_change_event(
                    self._hass, self._config.watched_entities, self._on_state_event
                )
            )
        self._unsubs.append(
            async_track_time_interval(self._hass, self._on_timer, EVALUATE_INTERVAL)
        )
        self._unsubs.append(
            self._coordinator.async_add_listener(self._on_coordinator_tick)
        )
        self._prime_hvac_state()
        await self.async_evaluate("start")

    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    # ------------------------------------------------------------- triggers

    @callback
    def _on_state_event(self, event) -> None:
        if self._config.climate_entity and event.data.get("entity_id") == self._config.climate_entity:
            self._update_hvac_tracking(event.data.get("new_state"))
        self._hass.async_create_task(self.async_evaluate("state_change"))

    @callback
    def _on_coordinator_tick(self) -> None:
        self._hass.async_create_task(self.async_evaluate("poll"))

    async def _on_timer(self, _now) -> None:
        await self.async_evaluate("tick")

    def _prime_hvac_state(self) -> None:
        if not self._config.climate_entity:
            return
        self._update_hvac_tracking(self._hass.states.get(self._config.climate_entity))

    @callback
    def _update_hvac_tracking(self, state) -> None:
        action = state.attributes.get("hvac_action") if state else None
        if action not in HVAC_INACTIVE_ACTIONS:
            if self._hvac_active_since is None:
                self._hvac_active_since = dt_util.utcnow()
        else:
            self._hvac_active_since = None

    # ------------------------------------------------------------- gates

    def _hvac_gate(self) -> bool:
        if not self._config.climate_entity:
            return True
        if self._hvac_active_since is None:
            return False
        held = dt_util.utcnow() - self._hvac_active_since
        return held >= timedelta(minutes=self._config.hvac_on_delay_minutes)

    def _occupancy_gate(self) -> bool:
        if not self._config.occupancy_entity:
            return True
        state = self._hass.states.get(self._config.occupancy_entity)
        return state is not None and state.state == "on"

    def _motion_gate(self, linger_minutes: int) -> bool:
        if not self._config.motion_entities:
            # Night Owl without motion sensors configured: allow (the per-day
            # pref + master switch are then the only gates).
            return True
        now = dt_util.utcnow()
        linger = timedelta(minutes=linger_minutes)
        for entity_id in self._config.motion_entities:
            state = self._hass.states.get(entity_id)
            if state is None:
                continue
            if state.state == "on":
                return True
            if state.state == "off" and now - state.last_changed <= linger:
                return True
        return False

    # ------------------------------------------------------------- decision

    def desired_power(self, now: datetime | None = None):
        """Return True/False, or None when the engine should hand off."""
        now = now or dt_util.now()
        model = self._store.get_model(self._device_id)

        timed_run = self._store.get_timed_run(self._device_id)
        if timed_run is not None:
            ends_at = dt_util.parse_datetime(timed_run.ends_at)
            if ends_at and ends_at > dt_util.utcnow():
                self._snapshot = {"decision": "timed_run"}
                return True

        if not model.schedule_enabled:
            self._snapshot = {"decision": "hands_off"}
            return None

        window_hit = active_window(model, now)
        if window_hit is not None:
            hvac_ok = self._hvac_gate()
            occupancy_ok = self._occupancy_gate()
            self._snapshot = {
                "decision": "window",
                "window": {
                    "day": int(window_hit[0]),
                    "index": window_hit[1],
                    "start": window_hit[2].start,
                    "end": window_hit[2].end,
                },
                "hvac": hvac_ok,
                "occupancy": occupancy_ok,
            }
            return hvac_ok and occupancy_ok

        if night_owl_period(model, now):
            motion_ok = self._motion_gate(model.night_owl.linger_minutes)
            self._snapshot = {"decision": "night_owl", "motion": motion_ok}
            return motion_ok

        self._snapshot = {"decision": "outside"}
        return False

    def snapshot(self) -> dict:
        """Last decision context (for the scheduled_on attributes / ws status)."""
        return dict(self._snapshot)

    # ------------------------------------------------------------- act

    async def async_evaluate(self, reason: str = "tick") -> None:
        desired = self.desired_power()
        if desired is None:
            return

        current = self._coordinator.data.get("state")
        if current is None or bool(current) == desired:
            return

        now = dt_util.utcnow()

        # Respect the coordinator's optimistic-state shield: a just-sent
        # command may not be reflected yet.
        if (
            self._last_commanded == desired
            and self._last_commanded_at is not None
            and now - self._last_commanded_at
            < timedelta(seconds=COMMAND_SETTLE_SECONDS)
        ):
            return

        _LOGGER.info(
            "Gating engine turning device %s %s (%s: %s)",
            self._device_id,
            "on" if desired else "off",
            reason,
            self._snapshot.get("decision"),
        )
        self._last_commanded = desired
        self._last_commanded_at = now
        await self._coordinator.turn_on_off(desired)
