"""Number platform for Aroma-Link."""
import logging
from homeassistant.components.number import NumberEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import (
    DOMAIN,
    DEFAULT_DIFFUSE_TIME,
    DEFAULT_WORK_DURATION,
    DEFAULT_PAUSE_DURATION,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link number entities based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]
    
    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_info = coordinator.get_device_info()
        device_name = device_info["name"]
        # Fetch current settings
        await coordinator.fetch_work_time_settings()
        entities.append(AromaLinkWorkDurationNumber(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkPauseDurationNumber(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkProgramWorkDuration(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkProgramPauseDuration(coordinator, entry, device_id, device_name))
        # Oil tracking calibration entities
        entities.append(AromaLinkOilBottleCapacity(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilFillVolume(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilRemainingInput(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilManualStartVolume(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilManualEndVolume(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilManualRuntimeHours(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilManualRate(coordinator, entry, device_id, device_name))
    
    async_add_entities(entities)

class AromaLinkDiffuseTimeNumber(NumberEntity):
    """Representation of an Aroma-Link diffuse time setting."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the number entity."""
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Diffuse Time"
        self._unique_id = f"{entry.data['username']}_{device_id}_diffuse_time"
        self._attr_native_min_value = 10  # Minimum 10 seconds
        self._attr_native_max_value = 3600  # Maximum 1 hour
        self._attr_native_step = 10  # 10 second steps
        self._attr_native_unit_of_measurement = "seconds"

    @property
    def name(self):
        """Return the name of the number entity."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def native_value(self):
        """Return the current value."""
        return self._coordinator.diffuse_time

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        """Set the diffuse time."""
        self._coordinator.diffuse_time = int(value)
        self.async_write_ha_state()

class AromaLinkWorkDurationNumber(NumberEntity):
    """Representation of an Aroma-Link work duration setting."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the number entity."""
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Work Duration"
        self._unique_id = f"{entry.data['username']}_{device_id}_work_duration"
        self._attr_native_min_value = 5  # Minimum 5 seconds
        self._attr_native_max_value = 900  # Maximum 900 seconds (15 minutes)
        self._attr_native_step = 1  # 1 second steps
        self._attr_native_unit_of_measurement = "seconds"
        self._attr_icon = "mdi:spray"
        self._attr_mode = "box"  # Make it a number input field instead of a slider

    @property
    def name(self):
        """Return the name of the number entity."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def native_value(self):
        """Return the current value."""
        return self._coordinator.work_duration
        
    async def async_set_native_value(self, value):
        """Set the work duration."""
        self._coordinator.work_duration = int(value)
        self.async_write_ha_state()

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        """Set the work duration."""
        self._coordinator.work_duration = int(value)
        self.async_write_ha_state()

class AromaLinkPauseDurationNumber(NumberEntity):
    """Representation of an Aroma-Link pause duration setting."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the number entity."""
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Pause Duration"
        self._unique_id = f"{entry.data['username']}_{device_id}_pause_duration"
        self._attr_native_min_value = 5  # Minimum 5 seconds
        self._attr_native_max_value = 900  # Maximum 900 seconds (15 minutes)
        self._attr_native_step = 5  # 5 second steps
        self._attr_native_unit_of_measurement = "seconds"
        self._attr_icon = "mdi:timer-pause"
        self._attr_mode = "box"  # Make it a number input field instead of a slider

    @property
    def name(self):
        """Return the name of the number entity."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    @property
    def native_value(self):
        """Return the current value."""
        return self._coordinator.pause_duration
        
    async def async_set_native_value(self, value):
        """Set the pause duration."""
        self._coordinator.pause_duration = int(value)
        self.async_write_ha_state()


class AromaLinkProgramWorkDuration(NumberEntity):
    """Program work duration."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Program Work Time"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_work_duration"
        self._attr_native_min_value = 5
        self._attr_native_max_value = 900
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "sec"
        self._attr_mode = "box"

    @property
    def name(self):
        """Return the name of the number entity."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def native_value(self):
        """Return the current value."""
        program_num = self._coordinator._current_program
        day = self._coordinator._current_day
        if day in self._coordinator._schedule_cache:
            schedule = self._coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                return float(schedule[program_num - 1].get("work_sec", 10))
        return 10.0

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        """Set the work duration."""
        program_num = self._coordinator._current_program
        day = self._coordinator._current_day
        if day not in self._coordinator._schedule_cache:
            await self._coordinator.async_refresh_schedule(day)
        if day in self._coordinator._schedule_cache:
            schedule = self._coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                schedule[program_num - 1]["work_sec"] = int(value)
        self.async_write_ha_state()


class AromaLinkProgramPauseDuration(NumberEntity):
    """Program pause duration."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Program Pause Time"
        self._unique_id = f"{entry.data['username']}_{device_id}_program_pause_duration"
        self._attr_native_min_value = 5
        self._attr_native_max_value = 900
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = "sec"
        self._attr_mode = "box"

    @property
    def name(self):
        """Return the name of the number entity."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for this entity."""
        return self._unique_id

    @property
    def native_value(self):
        """Return the current value."""
        program_num = self._coordinator._current_program
        day = self._coordinator._current_day
        if day in self._coordinator._schedule_cache:
            schedule = self._coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                return float(schedule[program_num - 1].get("pause_sec", 120))
        return 120.0

    @property
    def device_info(self):
        """Return device information about this Aroma-Link device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        """Set the pause duration."""
        program_num = self._coordinator._current_program
        day = self._coordinator._current_day
        if day not in self._coordinator._schedule_cache:
            await self._coordinator.async_refresh_schedule(day)
        if day in self._coordinator._schedule_cache:
            schedule = self._coordinator._schedule_cache[day]
            if len(schedule) >= program_num:
                schedule[program_num - 1]["pause_sec"] = int(value)
        self.async_write_ha_state()


# ============================================================
# OIL TRACKING CALIBRATION ENTITIES
# ============================================================

class AromaLinkOilBottleCapacity(CoordinatorEntity, NumberEntity):
    """Maximum oil bottle capacity in ml."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Oil Bottle Capacity"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_bottle_capacity"
        self._attr_native_min_value = 10
        self._attr_native_max_value = 1000
        self._attr_native_step = 5
        self._attr_native_unit_of_measurement = "ml"
        self._attr_icon = "mdi:bottle-tonic"
        self._attr_mode = "box"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        return self._coordinator.get_oil_calibration().get("bottle_capacity", 100)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        self._coordinator.set_oil_calibration(bottle_capacity=int(value))
        self.async_write_ha_state()


class AromaLinkOilFillVolume(CoordinatorEntity, NumberEntity):
    """Volume of oil at last fill in ml."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Oil Fill Volume"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_fill_volume"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 1000
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "ml"
        self._attr_icon = "mdi:water-plus"
        self._attr_mode = "box"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        return self._coordinator.get_oil_calibration().get("fill_volume", 100)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        self._coordinator.set_oil_calibration(fill_volume=int(value))
        self.async_write_ha_state()


class AromaLinkOilRemainingInput(CoordinatorEntity, NumberEntity):
    """Input for current remaining oil volume (for calibration)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Oil Remaining (Measured)"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_remaining_input"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 1000
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "ml"
        self._attr_icon = "mdi:water-minus"
        self._attr_mode = "box"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        return self._coordinator.get_oil_calibration().get("measured_remaining", 0)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        self._coordinator.set_oil_calibration(measured_remaining=int(value))
        self.async_write_ha_state()


class AromaLinkOilManualStartVolume(CoordinatorEntity, NumberEntity):
    """Manual calibration start volume (ml)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Oil Manual Start Volume"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_manual_start_volume"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 1000
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "ml"
        self._attr_icon = "mdi:water-plus"
        self._attr_mode = "box"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        return self._coordinator.get_oil_calibration().get("manual_start_volume", 0)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        self._coordinator.set_oil_calibration(manual_start_volume=float(value))
        self.async_write_ha_state()


class AromaLinkOilManualEndVolume(CoordinatorEntity, NumberEntity):
    """Manual calibration end volume (ml)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Oil Manual End Volume"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_manual_end_volume"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 1000
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "ml"
        self._attr_icon = "mdi:water-minus"
        self._attr_mode = "box"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        return self._coordinator.get_oil_calibration().get("manual_end_volume", 0)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        self._coordinator.set_oil_calibration(manual_end_volume=float(value))
        self.async_write_ha_state()


class AromaLinkOilManualRuntimeHours(CoordinatorEntity, NumberEntity):
    """Manual calibration runtime hours."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Oil Manual Runtime Hours"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_manual_runtime_hours"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 10000
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = "h"
        self._attr_icon = "mdi:timer"
        self._attr_mode = "box"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        return self._coordinator.get_oil_calibration().get("manual_runtime_hours", 0)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        self._coordinator.set_oil_calibration(manual_runtime_hours=float(value))
        self.async_write_ha_state()


class AromaLinkOilManualRate(CoordinatorEntity, NumberEntity):
    """Manual consumption rate override (ml/hr)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Oil Manual Rate"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_manual_rate"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 1000
        self._attr_native_step = 0.01
        self._attr_native_unit_of_measurement = "ml/hr"
        self._attr_icon = "mdi:speedometer"
        self._attr_mode = "box"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def native_value(self):
        return self._coordinator.get_oil_calibration().get("manual_rate_ml_per_hour", 0)

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self._coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    async def async_set_native_value(self, value):
        self._coordinator.set_oil_calibration(manual_rate_ml_per_hour=float(value))
        self.async_write_ha_state()
