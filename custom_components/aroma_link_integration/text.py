"""Text platform for Aroma-Link."""
import re

from homeassistant.components.text import TextEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link text entities based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]

    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_info = coordinator.get_device_info()
        device_name = device_info["name"]
        entities.append(AromaLinkProgramStartTime(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkProgramEndTime(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilFillDate(coordinator, entry, device_id, device_name))

    async_add_entities(entities)


class AromaLinkProgramStartTime(CoordinatorEntity, TextEntity):
    """Program start time (HH:MM format)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._name = f"{device_name} Program Start Time"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_start_time"
        self._attr_native_min = 0
        self._attr_native_max = 5  # "23:59" is 5 chars
        self._attr_pattern = r"^([0-1]?[0-9]|2[0-4]):[0-5][0-9]$"  # HH:MM format (allows 24:00)

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique ID."""
        return self._unique_id

    @property
    def native_value(self):
        """Return the current time as HH:MM string."""
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        if day in self.coordinator._schedule_cache:
            schedule = self.coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                return schedule[program_num - 1].get("start_time", "00:00")
        return "00:00"

    @property
    def device_info(self):
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_value(self, value):
        """Set the start time."""
        if not re.match(self._attr_pattern, value):
            return
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        if day not in self.coordinator._schedule_cache:
            await self.coordinator.async_refresh_schedule(day)
        if day in self.coordinator._schedule_cache:
            schedule = self.coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                schedule[program_num - 1]["start_time"] = value
        self.async_write_ha_state()


class AromaLinkProgramEndTime(CoordinatorEntity, TextEntity):
    """Program end time (HH:MM format)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._name = f"{device_name} Program End Time"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_end_time"
        self._attr_native_min = 0
        self._attr_native_max = 5  # "23:59" is 5 chars
        self._attr_pattern = r"^([0-1]?[0-9]|2[0-4]):[0-5][0-9]$"  # HH:MM format (allows 24:00)

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique ID."""
        return self._unique_id

    @property
    def native_value(self):
        """Return the current time as HH:MM string."""
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        if day in self.coordinator._schedule_cache:
            schedule = self.coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                return schedule[program_num - 1].get("end_time", "23:59")
        return "23:59"

    @property
    def device_info(self):
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_value(self, value):
        """Set the end time."""
        if not re.match(self._attr_pattern, value):
            return
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        if day not in self.coordinator._schedule_cache:
            await self.coordinator.async_refresh_schedule(day)
        if day in self.coordinator._schedule_cache:
            schedule = self.coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                schedule[program_num - 1]["end_time"] = value
        self.async_write_ha_state()


class AromaLinkOilFillDate(CoordinatorEntity, TextEntity):
    """Oil fill date (YYYY-MM-DD)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._name = f"{device_name} Oil Fill Date"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_fill_date"
        self._attr_native_min = 0
        self._attr_native_max = 10  # "YYYY-MM-DD"
        # Pattern for YYYY-MM-DD format (optional - None is allowed)
        self._attr_pattern = r"^\d{4}-\d{2}-\d{2}$"
        self._attr_icon = "mdi:calendar"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique ID."""
        return self._unique_id

    @property
    def native_value(self):
        """Return the fill date or None if not set."""
        fill_date = self.coordinator.get_oil_calibration().get("fill_date")
        # Return None instead of empty string to avoid pattern validation issues
        return fill_date if fill_date else None

    @property
    def device_info(self):
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_value(self, value):
        """Set the fill date."""
        if not re.match(self._attr_pattern, value):
            return
        self.coordinator.set_oil_calibration(fill_date=value)
        self.async_write_ha_state()
