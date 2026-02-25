"""Switch platform for Aroma-Link."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, CONF_DEVICE_ID

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link switch based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]
    
    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_info = coordinator.get_device_info()
        device_name = device_info["name"]
        entities.append(AromaLinkSwitch(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkFanSwitch(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkProgramEnabled(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkScheduleActiveSwitch(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkNightOwlSwitch(coordinator, entry, device_id, device_name))
        day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        for day_num, day_name in enumerate(day_names):
            entities.append(AromaLinkProgramDaySwitch(coordinator, entry, device_id, device_name, day_num, day_name))
    
    async_add_entities(entities)

class AromaLinkSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Aroma-Link switch."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Power"
        self._unique_id = f"{entry.data['username']}_{device_id}_switch"

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self.coordinator.data.get("state", False)

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        await self.coordinator.turn_on_off(True)

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        await self.coordinator.turn_on_off(False)


class AromaLinkFanSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Aroma-Link fan switch."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the fan switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Fan"
        self._unique_id = f"{entry.data['username']}_{device_id}_fan"

    @property
    def name(self):
        """Return the name of the fan switch."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def is_on(self):
        """Return true if the fan is on."""
        return self.coordinator.data.get("fan_state", False)

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_turn_on(self, **kwargs):
        """Turn the fan on."""
        await self.coordinator.fan_control(True)

    async def async_turn_off(self, **kwargs):
        """Turn the fan off."""
        await self.coordinator.fan_control(False)


class AromaLinkProgramEnabled(CoordinatorEntity, SwitchEntity):
    """Program enabled/disabled switch."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Program Enabled"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_enabled"

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def is_on(self):
        """Return true if the program is enabled."""
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        if day in self.coordinator._schedule_cache:
            schedule = self.coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                return schedule[program_num - 1].get("enabled", 0) == 1
        return False

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_turn_on(self, **kwargs):
        """Enable the current editor program and push to device."""
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        await self.coordinator.set_program_enabled(day, program_num, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Disable the current editor program and push to device."""
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        await self.coordinator.set_program_enabled(day, program_num, False)
        self.async_write_ha_state()


class AromaLinkScheduleActiveSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable P1-P4 for today on the device.

    Only controls the regular schedule programs (P1-P4). P5 (Night Owl)
    is managed independently by AromaLinkNightOwlSwitch. Provides a
    silent alternative to the power toggle — toggling schedule programs
    makes the diffuser start or stop without the audible beep.
    """

    SCHEDULE_PROGRAMS = 4  # P1-P4; P5 is Night Owl

    def __init__(self, coordinator, entry, device_id, device_name):
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Schedule Active"
        self._unique_id = f"{entry.data['username']}_{device_id}_schedule_active"
        self._attr_icon = "mdi:calendar-check"

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def is_on(self):
        """True if any of P1-P4 for today is enabled."""
        day = self.coordinator._get_today_schedule_day()
        programs = self.coordinator._schedule_cache.get(day)
        if not programs:
            return False
        return any(
            p.get("enabled", 0) == 1
            for p in programs[:self.SCHEDULE_PROGRAMS]
        )

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    @property
    def extra_state_attributes(self):
        day = self.coordinator._get_today_schedule_day()
        programs = self.coordinator._schedule_cache.get(day, [])
        enabled_nums = [
            i + 1 for i, p in enumerate(programs[:self.SCHEDULE_PROGRAMS])
            if p.get("enabled", 0) == 1
        ]
        return {
            "schedule_day": day,
            "enabled_programs": enabled_nums,
        }

    async def async_turn_on(self, **kwargs):
        """Enable P1-P4 for today and push to device."""
        await self.coordinator.set_today_programs_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Disable P1-P4 for today and push to device."""
        await self.coordinator.set_today_programs_enabled(False)
        self.async_write_ha_state()


class AromaLinkNightOwlSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to silently enable/disable Program 5 (Night Owl) for today.

    P5 should be configured with an after-hours time window (e.g. 22:00-06:00).
    Toggling this switch enables/disables only P5 on the device, leaving
    P1-P4 untouched. Ideal for presence-based after-hours automations.
    """

    PROGRAM_NUM = 5

    def __init__(self, coordinator, entry, device_id, device_name):
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Night Owl"
        self._unique_id = f"{entry.data['username']}_{device_id}_night_owl"
        self._attr_icon = "mdi:weather-night"

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def is_on(self):
        """True if P5 is enabled for today."""
        day = self.coordinator._get_today_schedule_day()
        programs = self.coordinator._schedule_cache.get(day)
        if not programs or len(programs) < self.PROGRAM_NUM:
            return False
        return programs[self.PROGRAM_NUM - 1].get("enabled", 0) == 1

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    @property
    def extra_state_attributes(self):
        day = self.coordinator._get_today_schedule_day()
        programs = self.coordinator._schedule_cache.get(day, [])
        p5 = programs[self.PROGRAM_NUM - 1] if len(programs) >= self.PROGRAM_NUM else {}
        return {
            "schedule_day": day,
            "program": self.PROGRAM_NUM,
            "start_time": p5.get("start_time", "unknown"),
            "end_time": p5.get("end_time", "unknown"),
        }

    async def async_turn_on(self, **kwargs):
        """Enable P5 for today and push to device."""
        day = self.coordinator._get_today_schedule_day()
        await self.coordinator.set_program_enabled(day, self.PROGRAM_NUM, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Disable P5 for today and push to device."""
        day = self.coordinator._get_today_schedule_day()
        await self.coordinator.set_program_enabled(day, self.PROGRAM_NUM, False)
        self.async_write_ha_state()


class AromaLinkProgramDaySwitch(CoordinatorEntity, SwitchEntity):
    """Day selection switch (one per day)."""

    def __init__(self, coordinator, entry, device_id, device_name, day_num, day_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._day_num = day_num
        self._day_name = day_name
        self._name = f"{device_name} Program {day_name}"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_day_{day_num}"

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def is_on(self):
        """Return true if the day is selected."""
        return self._day_num in self.coordinator._selected_days

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_turn_on(self, **kwargs):
        """Select this day."""
        if self._day_num not in self.coordinator._selected_days:
            self.coordinator._selected_days.append(self._day_num)
            self.coordinator._selected_days.sort()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Deselect this day."""
        if self._day_num in self.coordinator._selected_days:
            self.coordinator._selected_days.remove(self._day_num)
        self.async_write_ha_state()
