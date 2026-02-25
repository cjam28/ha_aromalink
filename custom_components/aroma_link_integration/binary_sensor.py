"""Binary sensor platform for Aroma-Link."""
import logging
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

TIME_CHECK_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link binary sensors based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]

    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_info = coordinator.get_device_info()
        entities.append(
            AromaLinkScheduledOnSensor(coordinator, entry, device_id, device_info["name"])
        )

    async_add_entities(entities)


class AromaLinkScheduledOnSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that is ON when the current time falls within an enabled schedule window."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator, entry, device_id, device_name):
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Scheduled On"
        self._unique_id = f"{entry.data['username']}_{device_id}_scheduled_on"
        self._attr_icon = "mdi:calendar-check"
        self._unsub_timer = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._unsub_timer = async_track_time_interval(
            self.hass, self._time_tick, TIME_CHECK_INTERVAL
        )

    async def async_will_remove_from_hass(self):
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        await super().async_will_remove_from_hass()

    async def _time_tick(self, _now=None):
        """Re-evaluate state on each time tick."""
        self.async_write_ha_state()

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    @property
    def is_on(self):
        """Return True if current time is within any enabled program window for today."""
        active = self._get_active_programs()
        return len(active) > 0

    @property
    def extra_state_attributes(self):
        active = self._get_active_programs()
        attrs = {
            "active_programs": [p["program_number"] for p in active],
            "active_program_count": len(active),
        }
        if active:
            attrs["current_window_start"] = active[0]["start_time"]
            attrs["current_window_end"] = active[0]["end_time"]
            attrs["current_work_sec"] = active[0]["work_sec"]
            attrs["current_pause_sec"] = active[0]["pause_sec"]
            attrs["current_level"] = active[0]["level"]
        return attrs

    SCHEDULE_PROGRAMS = 4  # Only P1-P4; P5 (Night Owl) is automation-controlled

    def _get_active_programs(self):
        """Return list of P1-P4 programs whose time window covers the current time.

        P5 (Night Owl) is excluded because it uses a 00:00-23:59 window
        and is toggled by automations, not the schedule.
        """
        now = dt_util.now()

        # Convert Python weekday (Mon=0 .. Sun=6) to schedule convention (Sun=0 .. Sat=6)
        schedule_day = (now.weekday() + 1) % 7

        programs = self.coordinator._schedule_cache.get(schedule_day)
        if not programs:
            return []

        current_minutes = now.hour * 60 + now.minute
        active = []

        for idx, prog in enumerate(programs[:self.SCHEDULE_PROGRAMS], 1):
            if prog.get("enabled", 0) != 1:
                continue

            start_str = prog.get("start_time", "00:00")
            end_str = prog.get("end_time", "23:59")

            start_min = self._parse_hhmm(start_str)
            end_min = self._parse_hhmm(end_str)

            if start_min <= end_min:
                in_window = start_min <= current_minutes <= end_min
            else:
                in_window = current_minutes >= start_min or current_minutes <= end_min

            if in_window:
                level_raw = prog.get("level", 1)
                level_label = ["A", "B", "C"][level_raw - 1] if level_raw in (1, 2, 3) else "A"
                active.append({
                    "program_number": idx,
                    "start_time": start_str,
                    "end_time": end_str,
                    "work_sec": prog.get("work_sec", 10),
                    "pause_sec": prog.get("pause_sec", 120),
                    "level": level_label,
                })

        return active

    @staticmethod
    def _parse_hhmm(time_str):
        """Parse 'HH:MM' to total minutes since midnight."""
        try:
            parts = time_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError, AttributeError):
            return 0
