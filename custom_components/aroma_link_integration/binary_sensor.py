"""Binary sensor platform for Aroma-Link.

``scheduled_on`` is now TRUTHFUL: on means the device power is on AND the
current time is inside an armed capability window (a normal schedule window,
the Night Owl period, or a timed-run overlay) — i.e. the device is actually
allowed to be diffusing right now.
"""
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN, EVENT_UPDATED
from .entity import AromaLinkEntity
from .models import active_window, night_owl_period

UPDATE_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link binary sensors based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]
    store = data["store"]

    entities = [
        AromaLinkScheduledOnSensor(
            coordinator, entry, device_id, coordinator.device_name, store
        )
        for device_id, coordinator in device_coordinators.items()
    ]
    async_add_entities(entities)


class AromaLinkScheduledOnSensor(AromaLinkEntity, BinarySensorEntity):
    """True while the device is powered AND inside an armed window."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, device_id, device_name, store):
        super().__init__(
            coordinator, entry, device_id, device_name, "scheduled_on", "Scheduled On"
        )
        self._store = store

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def _on_updated(event):
            if event.data.get("device_id") == str(self._device_id):
                self.async_write_ha_state()

        self.async_on_remove(self.hass.bus.async_listen(EVENT_UPDATED, _on_updated))
        # Window boundaries don't emit events; tick to catch them promptly.
        self.async_on_remove(
            async_track_time_interval(
                self.hass, lambda _now: self.async_write_ha_state(), UPDATE_INTERVAL
            )
        )

    def _capability(self):
        """Return (source, window_hit) for the current capability, or (None, None)."""
        model = self._store.get_model(self._device_id)
        now = dt_util.now()

        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        reconciler = (entry_data.get("reconcilers") or {}).get(str(self._device_id))
        if reconciler is not None and reconciler.overlay is not None:
            return "timed_run", None

        window_hit = active_window(model, now)
        if window_hit is not None:
            return "window", window_hit

        if night_owl_period(model, now):
            return "night_owl", None

        return None, None

    @property
    def is_on(self):
        power_on = bool(self.coordinator.data.get("state", False))
        if not power_on:
            return False
        source, _ = self._capability()
        return source is not None

    @property
    def extra_state_attributes(self):
        source, window_hit = self._capability()
        attrs = {
            "power": bool(self.coordinator.data.get("state", False)),
            "source": source,
        }
        if window_hit is not None:
            day, index, window = window_hit
            attrs["active_window"] = {
                "day": int(day),
                "index": index,
                "start": window.start,
                "end": window.end,
                "work_sec": window.work_sec,
                "pause_sec": window.pause_sec,
            }
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        engine = (entry_data.get("engines") or {}).get(str(self._device_id))
        if engine is not None:
            attrs["gating"] = engine.snapshot()
        return attrs
