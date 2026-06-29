"""Select platform for Aroma-Link."""
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link select entities based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]

    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_info = coordinator.get_device_info()
        device_name = device_info["name"]
        # Load current day schedule on startup for immediate visibility
        await coordinator.async_refresh_schedule(coordinator._current_day)
        entities.append(AromaLinkProgramDaySelector(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkProgramSelector(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkProgramLevel(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilCalibrationState(coordinator, entry, device_id, device_name))

    async_add_entities(entities)


class AromaLinkProgramSelector(CoordinatorEntity, SelectEntity):
    """Program selector entity (1-5)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the program selector."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._name = f"{device_name} Program"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_selector"
        self._current_program = 1  # Default to Program 1

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique ID."""
        return self._unique_id

    @property
    def options(self):
        """Return the options."""
        return ["1", "2", "3", "4", "5"]

    @property
    def current_option(self):
        """Return the current option."""
        return str(self._current_program)

    @property
    def device_info(self):
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_select_option(self, option: str):
        """Select a program."""
        self._current_program = int(option)
        self.coordinator._current_program = int(option)
        # Always refresh current day to reflect app changes
        await self.coordinator.async_refresh_schedule(self.coordinator._current_day)
        # Notify all listeners so other entities update
        self.coordinator.async_update_listeners()


class AromaLinkProgramLevel(CoordinatorEntity, SelectEntity):
    """Program consistency level."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._name = f"{device_name} Program Level"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_level"

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique ID."""
        return self._unique_id

    @property
    def options(self):
        """Return the options."""
        return ["A", "B", "C"]

    @property
    def current_option(self):
        """Return the current option."""
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        if day in self.coordinator._schedule_cache:
            schedule = self.coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                level = schedule[program_num - 1].get("level", 1)
                level_map = {1: "A", 2: "B", 3: "C"}
                return level_map.get(level, "A")
        return "A"

    @property
    def device_info(self):
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_select_option(self, option: str):
        """Select a level."""
        program_num = self.coordinator._current_program
        day = self.coordinator._current_day
        if day not in self.coordinator._schedule_cache:
            await self.coordinator.async_refresh_schedule(day)
        if day in self.coordinator._schedule_cache:
            schedule = self.coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                level_map = {"A": 1, "B": 2, "C": 3}
                schedule[program_num - 1]["level"] = level_map.get(option, 1)
        self.async_write_ha_state()


class AromaLinkProgramDaySelector(CoordinatorEntity, SelectEntity):
    """Day selector for viewing program schedules."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the day selector."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._name = f"{device_name} Program Day"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_day_selector"
        self._day_names = [
            "Sunday",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ]

    @property
    def name(self):
        """Return the name."""
        return self._name

    @property
    def unique_id(self):
        """Return unique ID."""
        return self._unique_id

    @property
    def options(self):
        """Return day options."""
        return self._day_names

    @property
    def current_option(self):
        """Return the selected day."""
        day = self.coordinator._current_day
        if 0 <= day < len(self._day_names):
            return self._day_names[day]
        return self._day_names[0]

    @property
    def device_info(self):
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_select_option(self, option: str):
        """Select a day and refresh schedule."""
        if option not in self._day_names:
            return
        self.coordinator._current_day = self._day_names.index(option)
        await self.coordinator.async_refresh_schedule(self.coordinator._current_day)
        self.coordinator.async_update_listeners()


class AromaLinkOilCalibrationState(CoordinatorEntity, SelectEntity):
    """Calibration state selector."""

    _options = ["Idle", "Running", "Ready to Finalize", "Calibrated"]

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._device_name = device_name
        self._name = f"{device_name} Oil Calibration State"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_calibration_state"
        self._attr_icon = "mdi:flask-outline"
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
    def options(self):
        """Return available states."""
        return self._options

    @property
    def current_option(self):
        """Return current calibration state."""
        state = self.coordinator.get_calibration_state()
        return state if state in self._options else "Idle"

    @property
    def device_info(self):
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_select_option(self, option: str):
        """Set calibration state."""
        if option not in self._options:
            return
        self.coordinator.set_calibration_state(option)
        self.coordinator.async_update_listeners()
