"""Button platform for Aroma-Link."""
import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link button based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]
    
    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_info = coordinator.get_device_info()
        device_name = device_info["name"]
        entities.append(AromaLinkRunButton(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkSaveSettingsButton(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkSaveProgramButton(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkSyncSchedulesButton(coordinator, entry, device_id, device_name))
        # Oil tracking buttons
        entities.append(AromaLinkOilCalibrationToggleButton(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilCalibrationFinalizeButton(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilRefillKeepCalibrationButton(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkOilManualOverrideButton(coordinator, entry, device_id, device_name))
    
    async_add_entities(entities)

class AromaLinkRunButton(ButtonEntity):
    """Representation of an Aroma-Link run button."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the button."""
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Run"
        self._unique_id = f"{entry.data['username']}_{device_id}_run"

    @property
    def name(self):
        """Return the name of the button."""
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

    async def async_press(self):
        """Run the diffuser for a fixed time."""
        work_duration = self._coordinator.work_duration
        pause_duration = self._coordinator.pause_duration
        
        _LOGGER.info(f"Button pressed. Running diffuser with {work_duration}s work and {pause_duration}s pause settings")
        
        await self._coordinator.run_diffuser(work_duration, pause_duration=pause_duration)

class AromaLinkSaveSettingsButton(ButtonEntity):
    """Representation of an Aroma-Link save settings button."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize the button."""
        self._coordinator = coordinator
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Save Settings"
        self._unique_id = f"{entry.data['username']}_{device_id}_save_settings"
        self._attr_icon = "mdi:content-save"

    @property
    def name(self):
        """Return the name of the button."""
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

    async def async_press(self):
        """Save the current work duration and pause duration settings."""
        work_duration = self._coordinator.work_duration
        pause_duration = self._coordinator.pause_duration
        
        _LOGGER.info(f"Saving settings: work_duration={work_duration}s, pause_duration={pause_duration}s")
        
        result = await self._coordinator.set_scheduler(work_duration, pause_duration)
        if result:
            _LOGGER.info(f"Settings saved successfully for {self._coordinator.device_name}")
        else:
            _LOGGER.error(f"Failed to save settings for {self._coordinator.device_name}")


class AromaLinkSaveProgramButton(CoordinatorEntity, ButtonEntity):
    """Save Program button."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Save Program"
        self._unique_id = f"{entry.data['username']}_{device_id}_save_program"

    @property
    def name(self):
        """Return the name of the button."""
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

    async def async_press(self):
        """Save the program to selected days - OPTIMIZED to batch days with identical schedules."""
        program_num = self.coordinator._current_program
        current_day = self.coordinator._current_day
        selected_days = self.coordinator._selected_days

        if not selected_days:
            _LOGGER.warning("No days selected for saving program")
            return

        # First, capture the edited program data from the cache (before any API calls)
        edited_program = None
        if current_day in self.coordinator._schedule_cache:
            current_schedule = self.coordinator._schedule_cache[current_day]
            if len(current_schedule) >= program_num:
                edited_program = current_schedule[program_num - 1].copy()

        if not edited_program:
            _LOGGER.error("No edited program data found in cache for day %s program %s", current_day, program_num)
            return

        _LOGGER.info(
            "Saving program %s to days %s with: enabled=%s, time=%s-%s, work=%s, pause=%s, level=%s",
            program_num, selected_days,
            edited_program.get("enabled"),
            edited_program.get("start_time"),
            edited_program.get("end_time"),
            edited_program.get("work_sec"),
            edited_program.get("pause_sec"),
            edited_program.get("level")
        )

        # Step 1: Build the final schedule for each day
        import json
        day_schedules = {}  # day -> (schedule_list, work_time_list)
        
        for day in selected_days:
            # Get base schedule for this day
            if day == current_day:
                schedule = self.coordinator._schedule_cache.get(day)
                if schedule:
                    schedule = [prog.copy() for prog in schedule]  # Deep copy
            else:
                schedule = await self.coordinator.fetch_workset_for_day(day)

            if not schedule:
                _LOGGER.error("Failed to get schedule for day %s", day)
                continue

            # Replace the selected program with the edited data
            schedule[program_num - 1] = {
                "enabled": edited_program.get("enabled", 0),
                "start_time": edited_program.get("start_time", "00:00"),
                "end_time": edited_program.get("end_time", "23:59"),
                "work_sec": edited_program.get("work_sec", 10),
                "pause_sec": edited_program.get("pause_sec", 120),
                "level": edited_program.get("level", 1),
            }

            # Convert to API format
            level_map = {1: "1", 2: "2", 3: "3"}
            work_time_list = []
            for prog in schedule:
                work_time_list.append({
                    "startTime": prog.get("start_time", "00:00"),
                    "endTime": prog.get("end_time", "23:59"),
                    "enabled": prog.get("enabled", 0),
                    "consistenceLevel": level_map.get(prog.get("level", 1), "1"),
                    "workDuration": str(prog.get("work_sec", 10)),
                    "pauseDuration": str(prog.get("pause_sec", 120))
                })
            
            day_schedules[day] = (schedule, work_time_list)

        # Step 2: Group days by identical work_time_list (batch them!)
        schedule_groups = {}  # JSON hash -> (days_list, work_time_list, schedules)
        for day, (schedule, work_time_list) in day_schedules.items():
            # Create a hash of the schedule for grouping
            schedule_key = json.dumps(work_time_list, sort_keys=True)
            if schedule_key not in schedule_groups:
                schedule_groups[schedule_key] = ([], work_time_list, {})
            schedule_groups[schedule_key][0].append(day)
            schedule_groups[schedule_key][2][day] = schedule

        # Step 3: Make ONE API call per unique schedule (batching multiple days!)
        _LOGGER.info("Optimized: %d days grouped into %d API calls", len(selected_days), len(schedule_groups))
        
        for schedule_key, (days, work_time_list, schedules) in schedule_groups.items():
            _LOGGER.info("Saving to days %s in single API call", days)
            
            # BATCH: Send multiple days in one call!
            result = await self.coordinator.set_workset(days, work_time_list, skip_refresh=True)
            if result:
                _LOGGER.info("Saved program %s to days %s", program_num, days)
                # Update cache for all days in this batch
                for day in days:
                    self.coordinator._schedule_cache[day] = schedules[day]
            else:
                _LOGGER.error("Failed to save program %s to days %s", program_num, days)

        # Single refresh at the end
        await self.coordinator.async_request_refresh()
        await self.coordinator.async_refresh_schedule(current_day)
        self.coordinator.async_update_listeners()


class AromaLinkSyncSchedulesButton(CoordinatorEntity, ButtonEntity):
    """Sync Schedules with Aroma-Link button."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Sync Schedules"
        self._unique_id = f"{entry.data['username']}_{device_id}_sync_schedules"
        self._attr_icon = "mdi:cloud-sync"

    @property
    def name(self):
        """Return the name of the button."""
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

    async def async_press(self):
        """Fetch all schedules from the Aroma-Link API."""
        _LOGGER.info("Syncing all schedules with Aroma-Link for device %s", self.coordinator.device_id)
        await self.coordinator.async_fetch_all_schedules()
        self.coordinator.async_update_listeners()
        _LOGGER.info("Schedule sync complete for device %s", self.coordinator.device_id)


# ============================================================
# OIL TRACKING BUTTONS
# ============================================================

class AromaLinkOilCalibrationToggleButton(CoordinatorEntity, ButtonEntity):
    """Button to start/end/resume calibration measurement."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Calibration Measurement"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_calibration_toggle"
        self._attr_icon = "mdi:flask-outline"
        self._attr_entity_category = EntityCategory.CONFIG

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

    async def async_press(self):
        """Start/End/Resume calibration measurement based on state."""
        state = self.coordinator.get_calibration_state()
        _LOGGER.info("Calibration toggle pressed for %s (state=%s)", self.coordinator.device_id, state)

        if state in {"Idle", "Calibrated"}:
            self.coordinator.start_calibration_measurement()
        elif state == "Running":
            self.coordinator.end_calibration_measurement()
        elif state == "Ready to Finalize":
            self.coordinator.resume_calibration_measurement()

        self.coordinator.async_update_listeners()


class AromaLinkOilCalibrationFinalizeButton(CoordinatorEntity, ButtonEntity):
    """Button to finalize calibration and compute usage rate."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Calibration Finalize"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_calibration_finalize"
        self._attr_icon = "mdi:check-circle-outline"
        self._attr_entity_category = EntityCategory.CONFIG

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

    async def async_press(self):
        """Finalize calibration and compute usage rate."""
        _LOGGER.info("Calibration finalize pressed for %s", self.coordinator.device_id)

        usage_rate = self.coordinator.finalize_calibration()

        if usage_rate:
            _LOGGER.info(
                "Oil calibration successful for %s: %.6f ml/sec (%.2f ml/hour)",
                self.coordinator.device_id, usage_rate, usage_rate * 3600
            )
        else:
            _LOGGER.warning(
                "Oil calibration failed for %s - check runtime and measured values",
                self.coordinator.device_id,
            )

        self.coordinator.async_update_listeners()


class AromaLinkOilRefillKeepCalibrationButton(CoordinatorEntity, ButtonEntity):
    """Button to refill oil without resetting calibration."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Refill (Keep Calibration)"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_refill_keep_calibration"
        self._attr_icon = "mdi:water-plus-outline"
        self._attr_entity_category = EntityCategory.CONFIG

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

    async def async_press(self):
        """Refill oil and keep calibration rate."""
        _LOGGER.info("Refill (keep calibration) pressed for %s", self.coordinator.device_id)
        self.coordinator.refill_keep_calibration()
        self.coordinator.async_update_listeners()


class AromaLinkOilManualOverrideButton(CoordinatorEntity, ButtonEntity):
    """Button to apply manual calibration override."""

    def __init__(self, coordinator, entry, device_id, device_name):
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._name = f"{device_name} Apply Manual Calibration"
        self._unique_id = f"{entry.data['username']}_{device_id}_oil_manual_override"
        self._attr_icon = "mdi:tune-variant"
        self._attr_entity_category = EntityCategory.CONFIG

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

    async def async_press(self):
        """Apply manual calibration override."""
        _LOGGER.info("Apply manual calibration pressed for %s", self.coordinator.device_id)
        usage_rate = self.coordinator.apply_manual_override()
        if usage_rate:
            _LOGGER.info(
                "Manual override applied for %s: %.6f ml/sec (%.2f ml/hour)",
                self.coordinator.device_id, usage_rate, usage_rate * 3600
            )
        else:
            _LOGGER.warning("Manual override failed for %s", self.coordinator.device_id)
        self.coordinator.async_update_listeners()
