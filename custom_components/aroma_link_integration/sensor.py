"""Sensor platform for Aroma-Link."""
import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.const import UnitOfTime
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link sensor based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]
    
    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_info = coordinator.get_device_info()
        
        # Add all the requested sensors
        entities.append(AromaLinkWorkStatusSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkWorkRemainingTimeSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkPauseRemainingTimeSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkOnCountSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkPumpCountSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkSignalStrengthSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkFirmwareVersionSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkLastUpdateSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkScheduleMatrixSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkCumulativeRuntimeSensor(coordinator, entry, device_id, device_info["name"]))
        # Oil level sensors
        entities.append(AromaLinkOilLevelSensor(coordinator, entry, device_id, device_info["name"]))
        entities.append(AromaLinkOilRemainingSensor(coordinator, entry, device_id, device_info["name"]))
    
    async_add_entities(entities)

class AromaLinkSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Aroma-Link sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, device_id, device_name, sensor_type, icon=None, unit=None):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._sensor_type = sensor_type
        self._name = f"{device_name} {sensor_type}"
        self._unique_id = f"{entry.data['username']}_{device_id}_{sensor_type.lower().replace(' ', '_')}"
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit

    @property
    def name(self):
        """Return the name of the sensor."""
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
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )

    def _get_raw_count(self, *keys):
        """Return the first numeric count available from raw device data."""
        raw_data = self.coordinator.data.get("raw_device_data", {})
        if not isinstance(raw_data, dict):
            return None

        for key in keys:
            value = raw_data.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue

        return None


def _get_first_value(raw_data, keys):
    """Return the first non-None value for the provided keys."""
    for key in keys:
        if key in raw_data and raw_data.get(key) is not None:
            return raw_data.get(key)
    return None


def _parse_timestamp(value):
    """Parse a timestamp in seconds or milliseconds to a datetime."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        if value > 1_000_000_000_000:
            value = value / 1000.0
        if value > 1_000_000_000:
            return dt_util.utc_from_timestamp(value)
    return None

class AromaLinkWorkStatusSensor(AromaLinkSensorBase):
    """Sensor showing the current work status."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the work status sensor."""
        super().__init__(
            coordinator, 
            entry, 
            device_id, 
            device_name, 
            "Work Status", 
            icon="mdi:state-machine"
        )

    @property
    def native_value(self):
        """Return the current work status."""
        work_status = self.coordinator.data.get("workStatus")
        if work_status == 0:
            return "Off"
        elif work_status == 1:
            return "Diffusing"
        elif work_status == 2:
            return "Paused"
        else:
            return "Unknown"

class AromaLinkWorkRemainingTimeSensor(AromaLinkSensorBase):
    """Sensor showing the remaining time in the current work cycle."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the work remaining time sensor."""
        super().__init__(
            coordinator, 
            entry, 
            device_id, 
            device_name, 
            "Work Remaining Time", 
            icon="mdi:timer-outline",
            unit=UnitOfTime.SECONDS
        )

    @property
    def native_value(self):
        """Return the remaining time in work cycle."""
        return self.coordinator.data.get("workRemainTime")

class AromaLinkPauseRemainingTimeSensor(AromaLinkSensorBase):
    """Sensor showing the remaining time in the current pause cycle."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the pause remaining time sensor."""
        super().__init__(
            coordinator, 
            entry, 
            device_id, 
            device_name, 
            "Pause Remaining Time", 
            icon="mdi:timer-pause-outline",
            unit=UnitOfTime.SECONDS
        )

    @property
    def native_value(self):
        """Return the remaining time in pause cycle."""
        return self.coordinator.data.get("pauseRemainTime")

class AromaLinkOnCountSensor(AromaLinkSensorBase):
    """Sensor showing how many times the device has been turned on."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the on count sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "On Count",
            icon="mdi:counter",
            unit="activations"
        )
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        """Return the on count value."""
        return self._get_raw_count(
            "onCount",
            "runCount",
            "on_count",
            "run_count",
            "openCount",
            "open_count",
            "startCount",
            "start_count",
        )

class AromaLinkPumpCountSensor(AromaLinkSensorBase):
    """Sensor showing the number of times the pump has operated (diffusions)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the pump count sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "Pump Count",
            icon="mdi:shimmer",
            unit="diffusions"
        )
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        """Return the pump count value."""
        return self._get_raw_count(
            "pumpCount",
            "airPumpCount",
            "pump_count",
            "air_pump_count",
            "pumpTimes",
            "pump_times",
        )


class AromaLinkSignalStrengthSensor(AromaLinkSensorBase):
    """Sensor showing signal strength (if provided by the API)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the signal strength sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "Signal Strength",
            icon="mdi:wifi",
        )

    @property
    def native_value(self):
        """Return the signal strength value."""
        raw_data = self.coordinator.data.get("raw_device_data", {})
        value = _get_first_value(
            raw_data,
            ["rssi", "signalStrength", "signal", "signalLevel", "wifiSignal", "wifiLevel"],
        )
        if isinstance(value, str) and value.strip().isdigit():
            return int(value)
        return value


class AromaLinkFirmwareVersionSensor(AromaLinkSensorBase):
    """Sensor showing firmware version (if provided by the API)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the firmware version sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "Firmware Version",
            icon="mdi:chip",
        )

    @property
    def native_value(self):
        """Return the firmware version value."""
        raw_data = self.coordinator.data.get("raw_device_data", {})
        return _get_first_value(
            raw_data,
            ["firmwareVersion", "firmware", "fwVersion", "deviceVersion", "version"],
        )


class AromaLinkLastUpdateSensor(AromaLinkSensorBase):
    """Sensor showing the last update timestamp (if provided by the API)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the last update sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "Last Update",
            icon="mdi:clock-outline",
        )

    @property
    def native_value(self):
        """Return the last update timestamp."""
        raw_data = self.coordinator.data.get("raw_device_data", {})
        value = _get_first_value(
            raw_data,
            ["updateTime", "lastUpdate", "lastUpdateTime", "update_time", "updateTimestamp"],
        )
        return _parse_timestamp(value)


class AromaLinkScheduleMatrixSensor(AromaLinkSensorBase):
    """Sensor exposing the full schedule matrix as attributes for dashboard cards."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the schedule matrix sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "Schedule Matrix",
            icon="mdi:calendar-clock",
        )

    @property
    def native_value(self):
        """Return number of days with cached schedules."""
        return len(self.coordinator._schedule_cache)

    SCHEDULE_PROGRAMS = 4

    @property
    def extra_state_attributes(self):
        """Return the full schedule matrix as attributes.

        For P1-P4, uses _saved_enabled_state (user intent) so the card
        shows green blocks even when the automation has temporarily
        disabled programs on the device.
        """
        day_names = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        matrix = {}

        for day_num in range(7):
            day_name = day_names[day_num]
            if day_num in self.coordinator._schedule_cache:
                programs = self.coordinator._schedule_cache[day_num]
                user_intent = self.coordinator._saved_enabled_state.get(day_num)
                day_data = {}
                for prog_num, prog in enumerate(programs, 1):
                    if prog_num <= self.SCHEDULE_PROGRAMS:
                        if user_intent and prog_num - 1 < len(user_intent):
                            is_enabled = user_intent[prog_num - 1]
                        else:
                            is_enabled = prog.get("enabled", 0) == 1
                    elif prog_num == 5:
                        is_enabled = self.coordinator.get_night_owl_day(day_num)
                    else:
                        is_enabled = prog.get("enabled", 0) == 1
                    day_data[f"program_{prog_num}"] = {
                        "enabled": is_enabled,
                        "start": prog.get("start_time", "00:00"),
                        "end": prog.get("end_time", "23:59"),
                        "work": prog.get("work_sec", 10),
                        "pause": prog.get("pause_sec", 120),
                        "level": ["A", "B", "C"][prog.get("level", 1) - 1] if prog.get("level") in [1, 2, 3] else "A",
                    }
                matrix[day_name] = day_data
            else:
                matrix[day_name] = None

        return {
            "matrix": matrix,
            "current_day": self.coordinator._current_day,
            "current_program": self.coordinator._current_program,
            "selected_days": self.coordinator._selected_days,
            "device_id": self.coordinator.device_id,
            "night_owl_per_day": self.coordinator._night_owl_per_day.copy(),
        }


class AromaLinkCumulativeRuntimeSensor(AromaLinkSensorBase):
    """Sensor for cumulative work runtime (for oil tracking).
    
    Uses cycle detection from workRemainTime/pauseRemainTime transitions
    to accurately count completed work cycles regardless of poll interval.
    """

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the cumulative runtime sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "Cumulative Runtime",
            icon="mdi:timer-sand",
            unit="s",
        )
        self._attr_state_class = "total_increasing"
        self._attr_device_class = SensorDeviceClass.DURATION

    @property
    def native_value(self):
        """Return the accumulated work seconds."""
        return round(self.coordinator.get_cumulative_work_seconds(), 1)

    @property
    def extra_state_attributes(self):
        """Return comprehensive oil tracking attributes."""
        raw_data = self.coordinator.data or {}
        oil_info = self.coordinator.get_oil_tracking_info()
        cumulative_secs = oil_info.get("accumulated_work_seconds", 0)
        
        # Convert to human-readable format
        hours = int(cumulative_secs // 3600)
        minutes = int((cumulative_secs % 3600) // 60)
        seconds = int(cumulative_secs % 60)
        
        # Tracking duration
        track_secs = oil_info.get("tracking_duration_seconds", 0)
        track_hours = round(track_secs / 3600, 2)
        track_days = round(track_secs / 86400, 2)
        
        # Calculate duty cycle
        work_dur = oil_info.get("current_work_duration", 5)
        pause_dur = oil_info.get("current_pause_duration", 900)
        duty_cycle = (work_dur / (work_dur + pause_dur) * 100) if (work_dur + pause_dur) > 0 else 0
        
        # Format recent events for display
        recent_events = oil_info.get("recent_events", [])
        events_str = "; ".join([f"{e[0]} {e[1]}: {e[2]}" for e in recent_events[-5:]])
        
        return {
            "device_id": self.coordinator.device_id,
            # Main tracking values
            "formatted_runtime": f"{hours}h {minutes}m {seconds}s",
            "runtime_hours": round(cumulative_secs / 3600, 3),
            "runtime_minutes": round(cumulative_secs / 60, 1),
            "completed_cycles": oil_info.get("completed_cycles", 0),
            # Tracking status
            "tracking_active": oil_info.get("tracking_active", False),
            "tracking_duration_hours": track_hours,
            "tracking_duration_days": track_days,
            # Current settings
            "current_work_duration": work_dur,
            "current_pause_duration": pause_dur,
            "duty_cycle_percent": round(duty_cycle, 3),
            # Current state
            "current_work_status": raw_data.get("workStatus", 0),
            "current_work_remain": raw_data.get("workRemainTime", 0),
            "current_pause_remain": raw_data.get("pauseRemainTime", 0),
            # API reference values
            "api_pump_count": raw_data.get("pumpCount", 0),
            "api_pump_count_delta": oil_info.get("pump_count_delta"),
            "baseline_pump_count": oil_info.get("baseline_pump_count"),
            "api_run_count": raw_data.get("runCount", 0),
            # Recent events log
            "recent_events": events_str,
            # Calibration helper
            "calibration_note": "Fill oil, call reset_oil_runtime, run for days, measure remaining, calculate: used_ml / runtime_seconds",
        }


class AromaLinkOilLevelSensor(AromaLinkSensorBase):
    """Sensor showing oil level as percentage (for bottle visualization)."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the oil level sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "Oil Level",
            icon="mdi:bottle-tonic-outline",
            unit="%",
        )

    @property
    def native_value(self):
        """Return the oil level percentage."""
        level = self.coordinator.get_oil_level_percent()
        if level is not None:
            return round(level, 1)
        return None

    @property
    def extra_state_attributes(self):
        """Return oil status details."""
        status = self.coordinator.get_oil_status()
        cal = self.coordinator.get_oil_calibration()
        
        return {
            "device_id": self.coordinator.device_id,
            "calibrated": status.get("calibrated", False),
            "calibration_state": status.get("calibration_state", "Idle"),
            "calibration_method": status.get("calibration_method", "measured"),
            "bottle_capacity_ml": status.get("bottle_capacity_ml"),
            "estimated_remaining_ml": status.get("estimated_remaining_ml"),
            "usage_rate_ml_per_hour": status.get("usage_rate_ml_per_hour"),
            "runtime_since_fill_hours": status.get("runtime_since_fill_hours"),
            "completed_cycles": status.get("completed_cycles"),
            "fill_date": status.get("fill_date"),
            "estimated_days_remaining_schedule": status.get("estimated_days_remaining_schedule"),
            "effective_runtime_hours": status.get("effective_runtime_hours"),
            "runtime_source": status.get("runtime_source"),
            "manual_rate_ml_per_hour": status.get("manual_rate_ml_per_hour"),
            # Visual helper - icon state
            "level_category": self._get_level_category(status.get("level_percent")),
        }
    
    def _get_level_category(self, level_pct):
        """Return a category for visual display."""
        if level_pct is None:
            return "unknown"
        if level_pct > 75:
            return "full"
        if level_pct > 50:
            return "good"
        if level_pct > 25:
            return "low"
        if level_pct > 10:
            return "very_low"
        return "empty"


class AromaLinkOilRemainingSensor(AromaLinkSensorBase):
    """Sensor showing estimated remaining oil in ml."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the oil remaining sensor."""
        super().__init__(
            coordinator,
            entry,
            device_id,
            device_name,
            "Oil Remaining",
            icon="mdi:water",
            unit="ml",
        )

    @property
    def native_value(self):
        """Return the estimated remaining oil in ml."""
        remaining = self.coordinator.get_estimated_oil_remaining()
        if remaining is not None:
            return round(remaining, 1)
        return None

    @property
    def extra_state_attributes(self):
        """Return comprehensive oil status."""
        status = self.coordinator.get_oil_status()
        
        # Schedule-based estimates
        days_remaining = status.get("estimated_days_remaining_schedule")
        hours_remaining = days_remaining * 24 if days_remaining is not None else None
        
        return {
            "device_id": self.coordinator.device_id,
            "calibrated": status.get("calibrated", False),
            "calibration_state": status.get("calibration_state", "Idle"),
            "fill_volume_ml": status.get("fill_volume_ml"),
            "bottle_capacity_ml": status.get("bottle_capacity_ml"),
            "level_percent": status.get("level_percent"),
            "usage_rate_ml_per_sec": status.get("usage_rate_ml_per_sec"),
            "usage_rate_ml_per_hour": status.get("usage_rate_ml_per_hour"),
            "runtime_since_fill_sec": status.get("runtime_since_fill_sec"),
            "runtime_since_fill_hours": status.get("runtime_since_fill_hours"),
            "completed_cycles_since_fill": status.get("completed_cycles"),
            "effective_runtime_sec": status.get("effective_runtime_sec"),
            "effective_runtime_hours": status.get("effective_runtime_hours"),
            "runtime_source": status.get("runtime_source"),
            "estimated_hours_remaining_schedule": round(hours_remaining, 1) if hours_remaining else None,
            "estimated_days_remaining_schedule": round(days_remaining, 1) if days_remaining else None,
            "fill_date": status.get("fill_date"),
            "calibration_method": status.get("calibration_method", "measured"),
            "manual_start_volume": status.get("manual_start_volume"),
            "manual_end_volume": status.get("manual_end_volume"),
            "manual_runtime_hours": status.get("manual_runtime_hours"),
            "manual_rate_ml_per_hour": status.get("manual_rate_ml_per_hour"),
        }