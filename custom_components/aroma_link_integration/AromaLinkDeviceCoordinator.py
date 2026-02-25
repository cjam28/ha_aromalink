import asyncio
import logging
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import (
    DOMAIN,
    DEFAULT_DIFFUSE_TIME,
    DEFAULT_WORK_DURATION,
    DEFAULT_PAUSE_DURATION,
)

_LOGGER = logging.getLogger(__name__)


class AromaLinkDeviceCoordinator(DataUpdateCoordinator):
    """Coordinator for handling device data and control."""

    def __init__(
        self,
        hass,
        auth_coordinator,
        device_id,
        device_name,
        update_interval_seconds=60,
        save_oil_state_cb=None,
        oil_state=None,
    ):
        """Initialize the device coordinator."""
        self.hass = hass
        self.auth_coordinator = auth_coordinator
        self.device_id = device_id
        self.device_name = device_name
        self._diffuse_time = DEFAULT_DIFFUSE_TIME
        self._work_duration = DEFAULT_WORK_DURATION
        self._pause_duration = DEFAULT_PAUSE_DURATION
        self._schedule_cache = {}  # Cache schedules per day (0-6)
        # Editor state for schedule entities
        self._current_program = 1  # Currently selected program (1-5)
        self._current_day = 0  # Currently selected day for viewing (0-6)
        self._selected_days = [0]  # Days selected for saving (list of 0-6)
        
        # Oil tracking - cycle detection approach
        import time
        self._oil_tracking_active = False
        self._oil_tracking_start_time = None
        self._baseline_pump_count = None
        self._accumulated_work_seconds = 0.0
        self._completed_cycles = 0
        
        # Previous poll state for cycle detection
        self._prev_device_on = False
        self._prev_work_status = 0  # 0=off, 1=pausing, 2=working
        self._prev_work_remain = 0
        self._prev_pause_remain = 0
        self._prev_work_duration = 5  # Current work setting
        self._prev_pause_duration = 900  # Current pause setting
        
        # Event log for debugging
        self._oil_events = []  # List of (timestamp, event, details)
        
        # Oil calibration data (persists until recalibration)
        self._oil_calibration = {
            "bottle_capacity": 100,  # Max bottle size in ml
            "fill_volume": 100,  # Volume at last fill in ml
            "measured_remaining": 0,  # User-measured remaining (for calibration)
            "usage_rate": None,  # ml per work-second (calculated)
            "calibrated": False,  # Backward compatible flag
            "calibration_runtime": 0,  # Runtime at calibration point
            "fill_date": None,  # YYYY-MM-DD
            "calibration_state": "Idle",  # Idle, Running, Ready to Finalize, Calibrated
            "calibration_method": "measured",  # measured | manual
            "manual_start_volume": 0,
            "manual_end_volume": 0,
            "manual_runtime_hours": 0,
            "manual_rate_ml_per_hour": 0,
        }

        # Snapshot of which programs were enabled before schedule_active turned off.
        # Keyed by day number (0-6), value is list of 5 booleans.
        self._saved_enabled_state = {}

        self._save_oil_state_cb = save_oil_state_cb

        if oil_state:
            self._apply_oil_state(oil_state)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_id}",
            update_interval=timedelta(seconds=update_interval_seconds),
        )

    @property
    def diffuse_time(self):
        """Return the diffuse time."""
        return self._diffuse_time

    @diffuse_time.setter
    def diffuse_time(self, value):
        """Set the diffuse time."""
        self._diffuse_time = value

    @property
    def work_duration(self):
        """Return the work duration."""
        return self._work_duration

    @work_duration.setter
    def work_duration(self, value):
        """Set the work duration."""
        self._work_duration = value

    @property
    def pause_duration(self):
        """Return the pause duration."""
        return self._pause_duration

    @pause_duration.setter
    def pause_duration(self, value):
        """Set the pause duration."""
        self._pause_duration = value

    # ============================================================
    # OIL TRACKING METHODS (cycle detection from workRemain/pauseRemain)
    # ============================================================
    
    def reset_oil_tracking(self, current_pump_count=None):
        """Reset oil tracking (call when refilling oil).
        
        Uses cycle detection from workRemainTime/pauseRemainTime changes
        to accurately count completed work cycles.
        """
        import time
        self._oil_tracking_active = True
        self._oil_tracking_start_time = time.time()
        self._accumulated_work_seconds = 0.0
        self._completed_cycles = 0
        self._oil_events = []
        
        # Reset previous state
        self._prev_device_on = False
        self._prev_work_status = 0
        self._prev_work_remain = 0
        self._prev_pause_remain = 0
        
        # Capture pumpCount as reference
        if current_pump_count is not None:
            self._baseline_pump_count = current_pump_count
        elif self.data and "pumpCount" in self.data:
            self._baseline_pump_count = self.data.get("pumpCount", 0)
        else:
            self._baseline_pump_count = 0
        
        self._log_oil_event("RESET", f"Started tracking. Baseline pumpCount: {self._baseline_pump_count}")
        
        _LOGGER.info(
            "Reset oil tracking for device %s. Baseline pumpCount: %s",
            self.device_id, self._baseline_pump_count
        )
        self._request_oil_state_save()
    
    def _log_oil_event(self, event_type: str, details: str):
        """Log an oil tracking event for debugging."""
        import time
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._oil_events.append((timestamp, event_type, details))
        # Keep only last 50 events
        if len(self._oil_events) > 50:
            self._oil_events = self._oil_events[-50:]
        _LOGGER.debug(f"Oil event [{self.device_id}] {event_type}: {details}")

    def _request_oil_state_save(self):
        """Request persistence of oil tracking/calibration state."""
        if not self._save_oil_state_cb:
            return
        self.hass.async_create_task(self._save_oil_state_cb())

    def _apply_oil_state(self, oil_state):
        """Apply persisted oil state to coordinator."""
        if not isinstance(oil_state, dict):
            return

        self._oil_tracking_active = oil_state.get("oil_tracking_active", self._oil_tracking_active)
        self._oil_tracking_start_time = oil_state.get("oil_tracking_start_time", self._oil_tracking_start_time)
        self._baseline_pump_count = oil_state.get("baseline_pump_count", self._baseline_pump_count)
        self._accumulated_work_seconds = oil_state.get("accumulated_work_seconds", self._accumulated_work_seconds)
        self._completed_cycles = oil_state.get("completed_cycles", self._completed_cycles)
        self._prev_work_duration = oil_state.get("prev_work_duration", self._prev_work_duration)
        self._prev_pause_duration = oil_state.get("prev_pause_duration", self._prev_pause_duration)

        calibration = oil_state.get("calibration", {})
        if isinstance(calibration, dict):
            for key in self._oil_calibration:
                if key in calibration:
                    self._oil_calibration[key] = calibration[key]

        saved_enabled = oil_state.get("saved_enabled_state", {})
        if isinstance(saved_enabled, dict):
            self._saved_enabled_state = {
                int(k): v for k, v in saved_enabled.items()
                if isinstance(v, list)
            }
    
    def update_oil_tracking(
        self,
        device_on: bool,
        work_status: int,
        work_remain: int,
        pause_remain: int,
        work_duration: int,
        pause_duration: int,
    ):
        """Update oil tracking using cycle detection.
        
        Detects completed work cycles by monitoring:
        - workStatus transitions (working → pausing)
        - workRemainTime resets (indicates new cycle)
        - pauseRemainTime jumps (indicates work just completed)
        
        Args:
            device_on: Whether device is on
            work_status: 0=off, 1=pausing, 2=working
            work_remain: Seconds remaining in current work cycle
            pause_remain: Seconds remaining in current pause cycle
            work_duration: Current work setting (seconds per spray)
            pause_duration: Current pause setting
        """
        if not self._oil_tracking_active:
            # Update state but don't track
            self._prev_device_on = device_on
            self._prev_work_status = work_status
            self._prev_work_remain = work_remain
            self._prev_pause_remain = pause_remain
            self._prev_work_duration = work_duration
            self._prev_pause_duration = pause_duration
            return
        
        cycle_completed = False
        work_seconds_to_add = 0
        
        # Detection Case 1: Device turned OFF
        if self._prev_device_on and not device_on:
            self._log_oil_event("OFF", "Device turned off")
        
        # Detection Case 2: Device turned ON
        elif not self._prev_device_on and device_on:
            self._log_oil_event("ON", f"Device turned on. Status={work_status}")
        
        # Detection Case 3: Device was ON and still ON - check for cycle completion
        elif self._prev_device_on and device_on:
            
            # Case 3a: Was working (2), now pausing (1) → work cycle completed!
            if self._prev_work_status == 2 and work_status == 1:
                cycle_completed = True
                work_seconds_to_add = self._prev_work_duration
                self._log_oil_event(
                    "CYCLE", 
                    f"Work→Pause transition. +{work_seconds_to_add}s"
                )
            
            # Case 3b: workRemain jumped UP (e.g., 2 → 10) → new cycle started
            elif work_status == 2 and work_remain > self._prev_work_remain + 2:
                # Only count if we were previously in a work cycle
                if self._prev_work_status == 2:
                    cycle_completed = True
                    work_seconds_to_add = self._prev_work_duration
                    self._log_oil_event(
                        "CYCLE",
                        f"workRemain reset {self._prev_work_remain}→{work_remain}. +{work_seconds_to_add}s"
                    )
            
            # Case 3c: pauseRemain jumped UP significantly → new pause started after work
            elif work_status == 1 and self._prev_work_status == 1:
                if pause_remain > self._prev_pause_remain + 100:
                    # Pause timer reset = new cycle started
                    cycle_completed = True
                    work_seconds_to_add = self._prev_work_duration
                    self._log_oil_event(
                        "CYCLE",
                        f"pauseRemain reset {self._prev_pause_remain}→{pause_remain}. +{work_seconds_to_add}s"
                    )
            
            # Case 3d: Settings changed
            if work_duration != self._prev_work_duration or pause_duration != self._prev_pause_duration:
                self._log_oil_event(
                    "SETTINGS",
                    f"Changed: work {self._prev_work_duration}→{work_duration}s, "
                    f"pause {self._prev_pause_duration}→{pause_duration}s"
                )
        
        # Apply detected cycle
        if cycle_completed:
            self._accumulated_work_seconds += work_seconds_to_add
            self._completed_cycles += 1
            _LOGGER.info(
                "Oil tracking [%s]: Cycle #%d completed. +%ds work. Total: %.1fs",
                self.device_id, self._completed_cycles, 
                work_seconds_to_add, self._accumulated_work_seconds
            )
            self._request_oil_state_save()
        
        # Update previous state for next poll
        self._prev_device_on = device_on
        self._prev_work_status = work_status
        self._prev_work_remain = work_remain
        self._prev_pause_remain = pause_remain
        self._prev_work_duration = work_duration
        self._prev_pause_duration = pause_duration
    
    def get_cumulative_work_seconds(self):
        """Get accumulated work seconds since last fill."""
        return self._accumulated_work_seconds
    
    def get_completed_cycles(self):
        """Get number of completed work/spray cycles."""
        return self._completed_cycles
    
    def get_pump_count_delta(self):
        """Get pumpCount change since fill (API reference)."""
        if self._baseline_pump_count is None:
            return None
        current = self.data.get("pumpCount", 0) if self.data else 0
        return current - self._baseline_pump_count
    
    def get_oil_tracking_info(self):
        """Get comprehensive oil tracking data."""
        import time
        tracking_duration = 0
        if self._oil_tracking_start_time:
            tracking_duration = time.time() - self._oil_tracking_start_time
        
        return {
            "tracking_active": self._oil_tracking_active,
            "tracking_duration_seconds": tracking_duration,
            "accumulated_work_seconds": self._accumulated_work_seconds,
            "completed_cycles": self._completed_cycles,
            "baseline_pump_count": self._baseline_pump_count,
            "pump_count_delta": self.get_pump_count_delta(),
            "current_work_duration": self._prev_work_duration,
            "current_pause_duration": self._prev_pause_duration,
            "recent_events": self._oil_events[-10:] if self._oil_events else [],
            "calibration_state": self._oil_calibration.get("calibration_state", "Idle"),
            "fill_date": self._oil_calibration.get("fill_date"),
        }
    
    def get_oil_events_log(self):
        """Get the full event log for debugging."""
        return self._oil_events.copy()
    
    def set_accumulated_work_seconds(self, seconds):
        """Set accumulated work seconds (for restoring state)."""
        self._accumulated_work_seconds = seconds
    
    def set_completed_cycles(self, cycles):
        """Set completed cycles (for restoring state)."""
        self._completed_cycles = cycles
    
    def set_oil_tracking_start_time(self, timestamp):
        """Set tracking start time (for restoring state)."""
        self._oil_tracking_start_time = timestamp
        self._oil_tracking_active = timestamp is not None
    
    # ============================================================
    # OIL CALIBRATION METHODS
    # ============================================================
    
    def get_oil_calibration(self):
        """Get current oil calibration data."""
        return self._oil_calibration.copy()
    
    def set_oil_calibration(self, **kwargs):
        """Update oil calibration values."""
        for key, value in kwargs.items():
            if key in self._oil_calibration:
                self._oil_calibration[key] = value
        _LOGGER.debug("Oil calibration updated: %s", self._oil_calibration)
        self._request_oil_state_save()
    
    def perform_oil_calibration(self):
        """Backward-compatible calibration call (now uses finalize)."""
        return self.finalize_calibration()

    def get_calibration_state(self):
        """Return the current calibration state."""
        return self._oil_calibration.get("calibration_state", "Idle")

    def set_calibration_state(self, state):
        """Set calibration state and update tracking behavior."""
        valid_states = {"Idle", "Running", "Ready to Finalize", "Calibrated"}
        if state not in valid_states:
            _LOGGER.warning("Invalid calibration state: %s", state)
            return

        self._oil_calibration["calibration_state"] = state

        # Control tracking behavior based on state
        if state in {"Running", "Calibrated"}:
            self._oil_tracking_active = True
        elif state == "Ready to Finalize":
            self._oil_tracking_active = False
        else:  # Idle
            self._oil_tracking_active = False

        self._request_oil_state_save()

    def start_calibration_measurement(self):
        """Start a new calibration measurement run."""
        from datetime import datetime

        self.reset_oil_tracking()
        self._oil_calibration["calibration_state"] = "Running"
        self._oil_calibration["calibrated"] = False
        self._oil_calibration["measured_remaining"] = 0

        # Update fill date to today
        self._oil_calibration["fill_date"] = datetime.now().strftime("%Y-%m-%d")

        self._log_oil_event("CAL_START", "Calibration measurement started")
        self._request_oil_state_save()

    def end_calibration_measurement(self):
        """End calibration measurement and freeze tracking for finalization."""
        self._oil_calibration["calibration_state"] = "Ready to Finalize"
        self._oil_calibration["calibration_runtime"] = self._accumulated_work_seconds
        self._oil_tracking_active = False
        self._log_oil_event("CAL_END", "Calibration measurement ended")
        self._request_oil_state_save()

    def resume_calibration_measurement(self):
        """Resume a paused calibration measurement."""
        self._oil_calibration["calibration_state"] = "Running"
        self._oil_tracking_active = True
        self._log_oil_event("CAL_RESUME", "Calibration measurement resumed")
        self._request_oil_state_save()

    def refill_keep_calibration(self):
        """Refill oil without resetting calibration rate.

        Resets runtime/cycle counters, updates fill volume/date, preserves usage rate.
        """
        from datetime import datetime

        capacity = self._oil_calibration["bottle_capacity"]
        self._oil_calibration["fill_volume"] = capacity
        self._oil_calibration["measured_remaining"] = 0
        self._oil_calibration["fill_date"] = datetime.now().strftime("%Y-%m-%d")
        self._oil_calibration["calibration_runtime"] = 0

        # Preserve usage rate if it exists
        if self._oil_calibration.get("usage_rate"):
            self._oil_calibration["calibration_state"] = "Calibrated"
            self._oil_calibration["calibrated"] = True
        else:
            self._oil_calibration["calibration_state"] = "Idle"
            self._oil_calibration["calibrated"] = False

        # Reset runtime tracking
        self._oil_tracking_active = True
        self._oil_tracking_start_time = datetime.now().timestamp()
        self._accumulated_work_seconds = 0.0
        self._completed_cycles = 0
        self._oil_events = []

        # Capture baseline
        if self.data:
            self._baseline_pump_count = self.data.get("pumpCount", 0)

        self._log_oil_event("REFILL", f"Refilled to {capacity}ml (kept calibration).")
        self._request_oil_state_save()

    def _validate_calibration_inputs(self):
        """Validate calibration inputs and minimum consumption.

        Returns (is_valid, message).
        """
        fill_vol = self._oil_calibration["fill_volume"]
        remaining = self._oil_calibration["measured_remaining"]
        runtime = self._accumulated_work_seconds

        if runtime <= 0:
            return False, "No runtime recorded"
        if fill_vol <= 0:
            return False, "Fill volume must be > 0"
        if remaining < 0:
            return False, "Measured remaining cannot be negative"

        oil_used = fill_vol - remaining
        if oil_used <= 0:
            return False, "No oil consumed yet"

        # Require at least 10% consumption
        if oil_used < (fill_vol * 0.10):
            return False, "Less than 10% of fill volume consumed"

        return True, "OK"

    def finalize_calibration(self):
        """Finalize calibration and compute usage rate.

        Returns usage_rate if successful, otherwise None.
        """
        if self.get_calibration_state() != "Ready to Finalize":
            _LOGGER.warning("Calibration finalize requested but state is %s", self.get_calibration_state())
            return None

        is_valid, message = self._validate_calibration_inputs()
        if not is_valid:
            _LOGGER.warning("Cannot calibrate: %s", message)
            return None

        fill_vol = self._oil_calibration["fill_volume"]
        remaining = self._oil_calibration["measured_remaining"]
        runtime = self._accumulated_work_seconds

        oil_used = fill_vol - remaining
        usage_rate = oil_used / runtime  # ml per second of work

        self._oil_calibration["usage_rate"] = usage_rate
        self._oil_calibration["calibrated"] = True
        self._oil_calibration["calibration_runtime"] = runtime
        self._oil_calibration["calibration_state"] = "Calibrated"
        self._oil_calibration["calibration_method"] = "measured"
        self._oil_tracking_active = True

        self._log_oil_event("CAL_FINAL", f"Calibration finalized: {usage_rate:.6f} ml/sec")
        self._request_oil_state_save()

        _LOGGER.info(
            "Oil calibration complete for %s: %.6f ml/sec (%.2f ml/hour of spray)",
            self.device_id, usage_rate, usage_rate * 3600
        )

        return usage_rate

    def apply_manual_override(self):
        """Apply manual override for calibration.

        Priority:
        1) manual_rate_ml_per_hour if provided
        2) compute from manual_start/end + manual_runtime_hours
        """
        manual_rate = self._oil_calibration.get("manual_rate_ml_per_hour", 0) or 0
        start_vol = self._oil_calibration.get("manual_start_volume", 0) or 0
        end_vol = self._oil_calibration.get("manual_end_volume", 0) or 0
        runtime_hours = self._oil_calibration.get("manual_runtime_hours", 0) or 0

        usage_rate = None
        method = None

        if manual_rate > 0:
            usage_rate = manual_rate / 3600.0
            method = "manual_rate"
        elif runtime_hours > 0 and start_vol > 0 and end_vol >= 0:
            oil_used = start_vol - end_vol
            if oil_used <= 0:
                _LOGGER.warning("Manual override failed: end volume must be less than start volume.")
                return None
            usage_rate = oil_used / (runtime_hours * 3600.0)
            method = "manual_calc"

        if not usage_rate:
            _LOGGER.warning("Manual override failed: provide rate or start/end/runtime.")
            return None

        self._oil_calibration["usage_rate"] = usage_rate
        self._oil_calibration["calibration_state"] = "Calibrated"
        self._oil_calibration["calibration_method"] = "manual" if method else "measured"
        self._oil_calibration["calibrated"] = True
        self._oil_calibration["calibration_runtime"] = self._accumulated_work_seconds
        self._log_oil_event("CAL_MANUAL", f"Manual override applied ({method}).")
        self._request_oil_state_save()

        _LOGGER.info(
            "Manual calibration override for %s: %.6f ml/sec (%.2f ml/hour)",
            self.device_id, usage_rate, usage_rate * 3600
        )

        return usage_rate
    
    def get_estimated_oil_remaining(self):
        """Calculate estimated remaining oil based on calibration and runtime.
        
        Returns remaining ml, or None if not calibrated.
        """
        if not self._oil_calibration["calibrated"] or self._oil_calibration["usage_rate"] is None:
            return None
        
        fill_vol = self._oil_calibration["fill_volume"]
        usage_rate = self._oil_calibration["usage_rate"]
        runtime, _ = self._get_effective_runtime_seconds()
        
        oil_used = runtime * usage_rate
        remaining = fill_vol - oil_used
        
        return max(0, remaining)  # Don't go negative
    
    def get_oil_level_percent(self):
        """Get oil level as percentage of bottle capacity.
        
        Returns percentage (0-100), or None if not calibrated.
        """
        remaining = self.get_estimated_oil_remaining()
        if remaining is None:
            return None
        
        capacity = self._oil_calibration["bottle_capacity"]
        if capacity <= 0:
            return None
        
        return min(100, (remaining / capacity) * 100)

    def _parse_time_to_seconds(self, time_str):
        """Convert HH:MM to seconds since midnight."""
        if not time_str:
            return 0
        parts = time_str.split(":")
        if len(parts) != 2:
            return 0
        try:
            hours = int(parts[0])
            minutes = int(parts[1])
            return (hours * 3600) + (minutes * 60)
        except ValueError:
            return 0

    def get_schedule_daily_work_seconds(self):
        """Estimate expected work seconds per day from the schedule matrix."""
        day_seconds = [0.0] * 7

        if not self._schedule_cache:
            return day_seconds

        for day, programs in self._schedule_cache.items():
            if day < 0 or day > 6:
                continue

            total_day_seconds = 0.0
            for program in programs:
                if program.get("enabled", 0) != 1:
                    continue

                start_time = program.get("start_time", "00:00")
                end_time = program.get("end_time", "23:59")
                work_sec = int(program.get("work_sec", 0) or 0)
                pause_sec = int(program.get("pause_sec", 0) or 0)

                if work_sec <= 0:
                    continue

                start_sec = self._parse_time_to_seconds(start_time)
                end_sec = self._parse_time_to_seconds(end_time)
                window = end_sec - start_sec
                if window <= 0:
                    # Crosses midnight, treat as next-day
                    window += 24 * 3600

                cycle = work_sec + max(pause_sec, 0)
                if cycle <= 0:
                    continue

                duty_cycle = work_sec / cycle
                total_day_seconds += window * duty_cycle

            day_seconds[day] = total_day_seconds

        return day_seconds

    def _get_effective_runtime_seconds(self):
        """Return runtime seconds and source (tracked or schedule_estimate)."""
        if self._accumulated_work_seconds > 0:
            return self._accumulated_work_seconds, "tracked"

        fill_date = self._oil_calibration.get("fill_date")
        if not fill_date:
            return self._accumulated_work_seconds, "tracked"

        try:
            from datetime import datetime, date
            fill_dt = datetime.strptime(fill_date, "%Y-%m-%d").date()
            days_since = (date.today() - fill_dt).days
        except ValueError:
            return self._accumulated_work_seconds, "tracked"

        if days_since <= 0:
            return self._accumulated_work_seconds, "tracked"

        day_seconds = self.get_schedule_daily_work_seconds()
        avg_daily_work = sum(day_seconds) / 7 if day_seconds else 0
        if avg_daily_work <= 0:
            return self._accumulated_work_seconds, "tracked"

        return avg_daily_work * days_since, "schedule_estimate"

    def get_estimated_days_remaining_schedule(self):
        """Estimate remaining days based on schedule (future projection)."""
        remaining = self.get_estimated_oil_remaining()
        usage_rate = self._oil_calibration.get("usage_rate")

        if remaining is None or not usage_rate:
            return None

        day_seconds = self.get_schedule_daily_work_seconds()
        avg_daily_work = sum(day_seconds) / 7 if day_seconds else 0

        if avg_daily_work <= 0:
            return None

        daily_usage_ml = avg_daily_work * usage_rate
        if daily_usage_ml <= 0:
            return None

        return remaining / daily_usage_ml
    
    def reset_oil_fill(self):
        """Mark oil as just filled (resets runtime tracking)."""
        self.start_calibration_measurement()
        _LOGGER.info("Oil fill reset for %s.", self.device_id)
    
    def get_oil_status(self):
        """Get comprehensive oil status for display."""
        remaining = self.get_estimated_oil_remaining()
        level_pct = self.get_oil_level_percent()
        schedule_days = self.get_estimated_days_remaining_schedule()
        cal = self._oil_calibration
        calibrated = cal["usage_rate"] is not None
        effective_runtime, runtime_source = self._get_effective_runtime_seconds()
        
        return {
            "bottle_capacity_ml": cal["bottle_capacity"],
            "fill_volume_ml": cal["fill_volume"],
            "calibrated": calibrated,
            "calibration_state": cal.get("calibration_state", "Idle"),
            "calibration_method": cal.get("calibration_method", "measured"),
            "fill_date": cal.get("fill_date"),
            "usage_rate_ml_per_sec": cal["usage_rate"],
            "usage_rate_ml_per_hour": cal["usage_rate"] * 3600 if cal["usage_rate"] else None,
            "estimated_remaining_ml": round(remaining, 1) if remaining is not None else None,
            "level_percent": round(level_pct, 1) if level_pct is not None else None,
            "estimated_days_remaining_schedule": round(schedule_days, 1) if schedule_days is not None else None,
            "runtime_since_fill_sec": self._accumulated_work_seconds,
            "runtime_since_fill_hours": round(self._accumulated_work_seconds / 3600, 2),
            "effective_runtime_sec": round(effective_runtime, 1),
            "effective_runtime_hours": round(effective_runtime / 3600, 2),
            "runtime_source": runtime_source,
            "completed_cycles": self._completed_cycles,
            "manual_start_volume": cal.get("manual_start_volume"),
            "manual_end_volume": cal.get("manual_end_volume"),
            "manual_runtime_hours": cal.get("manual_runtime_hours"),
            "manual_rate_ml_per_hour": cal.get("manual_rate_ml_per_hour"),
        }

    def export_oil_state(self):
        """Export oil tracking/calibration state for persistence."""
        return {
            "oil_tracking_active": self._oil_tracking_active,
            "oil_tracking_start_time": self._oil_tracking_start_time,
            "baseline_pump_count": self._baseline_pump_count,
            "accumulated_work_seconds": self._accumulated_work_seconds,
            "completed_cycles": self._completed_cycles,
            "prev_work_duration": self._prev_work_duration,
            "prev_pause_duration": self._prev_pause_duration,
            "calibration": self._oil_calibration.copy(),
            "saved_enabled_state": self._saved_enabled_state.copy(),
        }

    async def fetch_work_time_settings(self, week_day=0):
        """Fetch current work time settings from API."""
        await self.auth_coordinator._ensure_login()
        jsessionid = self.auth_coordinator.jsessionid

        url = f"https://www.aroma-link.com/device/workTime/{self.device_id}?week={week_day}"

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        if jsessionid and not jsessionid.startswith("temp_"):
            headers["Cookie"] = f"languagecode={self.auth_coordinator.language_code}; JSESSIONID={jsessionid}"

        try:
            _LOGGER.debug(
                "Fetching work time settings for device %s day %s (url=%s)",
                self.device_id,
                week_day,
                url,
            )
            async with self.auth_coordinator.request(
                "get",
                url,
                headers=headers,
                timeout=15,
            ) as response:
                if response.status == 200:
                    response_json = await response.json()
                    _LOGGER.debug(
                        "Work time response for device %s day %s: %s",
                        self.device_id,
                        week_day,
                        response_json,
                    )

                    if response_json.get("code") == 200 and "data" in response_json and response_json["data"]:
                        # Find the enabled setting (enabled: 1)
                        for setting in response_json["data"]:
                            if setting.get("enabled") == 1:
                                self._work_duration = setting.get(
                                    "workSec", self._work_duration)
                                self._pause_duration = setting.get(
                                    "pauseSec", self._pause_duration)
                                _LOGGER.debug(
                                    f"Found settings: work={self._work_duration}s, pause={self._pause_duration}s")
                                return {
                                    "work_duration": self._work_duration,
                                    "pause_duration": self._pause_duration,
                                    "week_day": week_day
                                }

                    _LOGGER.warning(
                        f"No enabled work time settings found for device {self.device_id}")
                    return None
                elif response.status in [401, 403]:
                    _LOGGER.warning(
                        f"Authentication error on fetch_work_time_settings ({response.status}).")
                    self.auth_coordinator.jsessionid = None
                    return None
                else:
                    _LOGGER.error(
                        f"Failed to fetch work time settings for device {self.device_id}: {response.status}")
                    return None
        except Exception as e:
            _LOGGER.error(
                f"Error fetching work time settings for device {self.device_id}: {e}")
            return None

    async def async_fetch_schedule(self, week_day):
        """Fetch schedule for a day, using cache if available.
        
        Args:
            week_day: Day of week (0=Monday, 1=Tuesday, ..., 6=Sunday)
            
        Returns:
            List of 5 program dictionaries, or None on error.
        """
        # Check cache first
        if week_day in self._schedule_cache:
            _LOGGER.debug(f"Using cached schedule for day {week_day}")
            return self._schedule_cache[week_day]
        
        # Fetch from API
        return await self.async_refresh_schedule(week_day)
    
    async def async_refresh_schedule(self, week_day):
        """Refresh schedule for a day from API (on-demand).

        Also captures the user's intended P1-P4 enabled states so
        automation restore logic always has the correct baseline.

        Args:
            week_day: Day of week (0=Monday, 1=Tuesday, ..., 6=Sunday)

        Returns:
            List of 5 program dictionaries, or None on error.
        """
        workset = await self.fetch_workset_for_day(week_day)
        if workset:
            self._schedule_cache[week_day] = workset
            self.update_user_intent(week_day)
            _LOGGER.debug(f"Cached schedule for day {week_day}")
        return workset

    async def async_fetch_all_schedules(self):
        """Fetch schedules for all 7 days in parallel.
        
        Returns:
            Dict mapping day (0-6) to list of 5 program dictionaries.
        """
        _LOGGER.debug(f"Fetching all schedules for device {self.device_id}")
        
        # Fetch all 7 days in parallel
        tasks = [self.fetch_workset_for_day(day) for day in range(7)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and update cache
        for day, result in enumerate(results):
            if isinstance(result, Exception):
                _LOGGER.error(f"Error fetching day {day}: {result}")
            elif result:
                self._schedule_cache[day] = result
        
        _LOGGER.info(f"Fetched all schedules for device {self.device_id}: {len(self._schedule_cache)} days cached")
        return self._schedule_cache.copy()

    def get_schedule_matrix(self):
        """Return the cached schedule matrix (7 days × 5 programs).
        
        Returns:
            Dict mapping day (0-6) to list of 5 program dictionaries.
            Days without cached data return None.
        """
        return {day: self._schedule_cache.get(day) for day in range(7)}

    def get_current_program_data(self):
        """Get the current program data from cache for the selected day/program.
        
        Returns:
            Dict with program settings, or None if not cached.
        """
        day = self._current_day
        program = self._current_program
        if day in self._schedule_cache and self._schedule_cache[day]:
            programs = self._schedule_cache[day]
            if 0 < program <= len(programs):
                return programs[program - 1]
        return None

    def set_editor_program(self, day, program):
        """Set the editor to a specific day and program.
        
        Args:
            day: Day of week (0=Monday, 1=Tuesday, ..., 6=Sunday)
            program: Program number (1-5)
        """
        self._current_day = day
        self._current_program = program
        # Also update selected_days to include this day by default
        if day not in self._selected_days:
            self._selected_days = [day]
        _LOGGER.debug(f"Set editor to day {day}, program {program}")
        # Notify listeners
        self.async_update_listeners()

    async def fetch_workset_for_day(self, week_day=0):
        """Fetch full workset (all 5 programs) for a specific day.
        
        Args:
            week_day: Day of week (0=Monday, 1=Tuesday, ..., 6=Sunday)
            
        Returns:
            List of 5 program dictionaries with keys: enabled, start_time, end_time, 
            work_sec, pause_sec, level, setting_id. Returns None on error.
        """
        await self.auth_coordinator._ensure_login()
        jsessionid = self.auth_coordinator.jsessionid

        url = f"https://www.aroma-link.com/device/workTime/{self.device_id}?week={week_day}"

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        if jsessionid and not jsessionid.startswith("temp_"):
            headers["Cookie"] = f"languagecode={self.auth_coordinator.language_code}; JSESSIONID={jsessionid}"

        try:
            _LOGGER.debug(
                "Fetching workset for device %s day %s (url=%s)",
                self.device_id,
                week_day,
                url,
            )
            async with self.auth_coordinator.request(
                "get",
                url,
                headers=headers,
                timeout=15,
            ) as response:
                if response.status == 200:
                    response_json = await response.json()
                    _LOGGER.debug(
                        "Workset response for device %s day %s: %s",
                        self.device_id,
                        week_day,
                        response_json,
                    )

                    if response_json.get("code") == 200 and "data" in response_json and response_json["data"]:
                        workset = []
                        # API returns up to 5 programs, ensure we have exactly 5
                        data = response_json["data"]
                        for i, setting in enumerate(data[:5]):  # Limit to 5
                            workset.append({
                                "enabled": setting.get("enabled", 0),
                                "start_time": setting.get("startHour", "00:00"),
                                "end_time": setting.get("endHour", "23:59"),
                                "work_sec": setting.get("workSec", 10),
                                "pause_sec": setting.get("pauseSec", 120),
                                "level": setting.get("consistenceLevel", 1),
                                "setting_id": setting.get("settingId"),
                            })
                        
                        # Pad to 5 if fewer returned
                        while len(workset) < 5:
                            workset.append({
                                "enabled": 0,
                                "start_time": "00:00",
                                "end_time": "23:59",
                                "work_sec": 10,
                                "pause_sec": 120,
                                "level": 1,
                                "setting_id": None,
                            })
                        
                        _LOGGER.debug(
                            "Fetched workset for device %s day %s: %s",
                            self.device_id,
                            week_day,
                            workset,
                        )
                        return workset
                    else:
                        _LOGGER.warning(
                            f"No workset data found for device {self.device_id} day {week_day}")
                        # Return empty workset (5 disabled programs)
                        return [
                            {"enabled": 0, "start_time": "00:00", "end_time": "23:59", 
                             "work_sec": 10, "pause_sec": 120, "level": 1, "setting_id": None}
                            for _ in range(5)
                        ]
                elif response.status in [401, 403]:
                    _LOGGER.warning(
                        f"Authentication error on fetch_workset_for_day ({response.status}).")
                    self.auth_coordinator.jsessionid = None
                    return None
                else:
                    _LOGGER.error(
                        f"Failed to fetch workset for device {self.device_id}: {response.status}")
                    return None
        except Exception as e:
            _LOGGER.error(
                f"Error fetching workset for device {self.device_id}: {e}")
            return None

    async def set_workset(self, week_days, work_time_list, skip_refresh=False):
        """Set workset schedule for specified days.
        
        Args:
            week_days: List of day numbers (0=Monday, 1=Tuesday, ..., 6=Sunday)
            work_time_list: List of 5 program dictionaries, each with:
                - enabled: 0 or 1
                - startTime: "HH:MM" format
                - endTime: "HH:MM" format
                - workDuration: string (seconds)
                - pauseDuration: string (seconds)
                - consistenceLevel: "1", "2", or "3" (A, B, or C)
            skip_refresh: If True, skip the automatic refresh after save (for batch operations)
                
        Returns:
            True if successful, False otherwise
        """
        await self.auth_coordinator._ensure_login()
        jsessionid = self.auth_coordinator.jsessionid

        url = "https://www.aroma-link.com/device/workSet"

        # Ensure we have exactly 5 programs
        if len(work_time_list) < 5:
            # Pad with disabled programs
            work_time_list = list(work_time_list)
            while len(work_time_list) < 5:
                work_time_list.append({
                    "startTime": "00:00",
                    "endTime": "23:59",
                    "enabled": 0,
                    "consistenceLevel": "1",
                    "workDuration": "10",
                    "pauseDuration": "120"
                })
        elif len(work_time_list) > 5:
            work_time_list = work_time_list[:5]

        payload = {
            "deviceId": str(self.device_id),
            "type": "workTime",
            "week": week_days,
            "workTimeList": work_time_list
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        if jsessionid and not jsessionid.startswith("temp_"):
            headers["Cookie"] = f"languagecode={self.auth_coordinator.language_code}; JSESSIONID={jsessionid}"

        try:
            _LOGGER.debug(
                "Setting workset for device %s on days %s (payload=%s)",
                self.device_id,
                week_days,
                payload,
            )
            async with self.auth_coordinator.request(
                "post",
                url,
                json=payload,
                headers=headers,
                timeout=10,
            ) as response:
                if response.status == 200:
                    response_json = await response.json()
                    _LOGGER.debug(
                        "Workset save response for device %s: %s",
                        self.device_id,
                        response_json,
                    )
                    if response_json.get("code") == 200:
                        _LOGGER.info(
                            f"Successfully set workset for device {self.device_id} to days {week_days}")
                        # Clear cache for updated days
                        for day in week_days:
                            if day in self._schedule_cache:
                                del self._schedule_cache[day]
                                _LOGGER.debug(f"Cleared schedule cache for day {day}")
                        if not skip_refresh:
                            await self.async_request_refresh()
                        return True
                    else:
                        _LOGGER.error(
                            f"API error setting workset: {response_json.get('msg', 'Unknown error')}")
                        return False
                elif response.status in [401, 403]:
                    _LOGGER.warning(
                        f"Authentication error on set_workset ({response.status}).")
                    self.auth_coordinator.jsessionid = None
                    return False
                else:
                    _LOGGER.error(
                        f"Failed to set workset for device {self.device_id}: {response.status}")
                    return False
        except Exception as e:
            _LOGGER.error(f"Error setting workset for device {self.device_id}: {e}")
            return False

    def _cache_to_api_format(self, programs):
        """Convert schedule cache format to set_workset API format."""
        api_list = []
        for prog in programs:
            level_raw = prog.get("level", 1)
            api_list.append({
                "startTime": prog.get("start_time", "00:00"),
                "endTime": prog.get("end_time", "23:59"),
                "enabled": prog.get("enabled", 0),
                "consistenceLevel": str(level_raw),
                "workDuration": str(prog.get("work_sec", 10)),
                "pauseDuration": str(prog.get("pause_sec", 120)),
            })
        return api_list

    def _get_today_schedule_day(self):
        """Return the schedule cache key for today (0=Sun .. 6=Sat)."""
        from homeassistant.util import dt as dt_util
        now = dt_util.now()
        return (now.weekday() + 1) % 7

    async def set_today_programs_enabled(self, enabled):
        """Enable or disable P1-P4 on the device for today.

        Always fetches fresh data from the API before modifying to ensure
        we never overwrite other programs with stale cached data.

        Args:
            enabled: True to restore user's P1-P4 settings on device,
                     False to disable P1-P4 on device (silent stop).

        Returns:
            True if the API call succeeded.
        """
        SCHEDULE_PROGRAMS = 4
        day = self._get_today_schedule_day()

        # Always fetch fresh from API to avoid pushing stale data for other programs
        fresh_programs = await self.fetch_workset_for_day(day)
        if not fresh_programs:
            _LOGGER.warning("Could not fetch schedule for today (day %s), cannot toggle programs", day)
            return False

        if day not in self._saved_enabled_state:
            self._saved_enabled_state[day] = [
                p.get("enabled", 0) == 1 for p in fresh_programs[:SCHEDULE_PROGRAMS]
            ]
            self._request_oil_state_save()

        api_programs = self._cache_to_api_format(fresh_programs)

        if not enabled:
            for prog in api_programs[:SCHEDULE_PROGRAMS]:
                prog["enabled"] = 0
        else:
            snapshot = self._saved_enabled_state[day]
            for i, was_on in enumerate(snapshot):
                api_programs[i]["enabled"] = 1 if was_on else 0

        return await self.set_workset([day], api_programs, skip_refresh=True)

    async def set_program_enabled(self, day, program_num, enabled):
        """Enable or disable a single program on the device.

        Always fetches fresh data from the API before modifying to ensure
        we never overwrite other programs with stale cached data.

        Args:
            day: Schedule day number (0=Sun .. 6=Sat).
            program_num: Program number (1-5).
            enabled: True to enable, False to disable.

        Returns:
            True if the API call succeeded.
        """
        # Always fetch fresh from API to avoid pushing stale data for other programs
        fresh_programs = await self.fetch_workset_for_day(day)
        if not fresh_programs:
            _LOGGER.warning("Could not fetch schedule for day %s", day)
            return False

        if program_num < 1 or program_num > len(fresh_programs):
            _LOGGER.warning("Invalid program number %s", program_num)
            return False

        api_programs = self._cache_to_api_format(fresh_programs)
        api_programs[program_num - 1]["enabled"] = 1 if enabled else 0

        return await self.set_workset([day], api_programs, skip_refresh=True)

    def update_user_intent(self, day=None):
        """Capture the user's intended P1-P4 enabled states from the cache.

        Call this after the user explicitly saves their schedule so the
        automation restore logic uses the correct baseline.
        """
        SCHEDULE_PROGRAMS = 4
        if day is not None:
            programs = self._schedule_cache.get(day, [])
            if programs:
                self._saved_enabled_state[day] = [
                    p.get("enabled", 0) == 1 for p in programs[:SCHEDULE_PROGRAMS]
                ]
        else:
            for d, programs in self._schedule_cache.items():
                if programs:
                    self._saved_enabled_state[d] = [
                        p.get("enabled", 0) == 1 for p in programs[:SCHEDULE_PROGRAMS]
                    ]
        self._request_oil_state_save()

    def get_device_info(self):
        """Get device info for entity setup."""
        return {
            "id": self.device_id,
            "name": self.device_name
        }

    async def _async_update_data(self):
        """Fetch current device state from API."""
        # Ensure auth is valid
        await self.auth_coordinator._ensure_login()
        jsessionid = self.auth_coordinator.jsessionid

        url = f"https://www.aroma-link.com/device/deviceInfo/now/{self.device_id}?timeout=1000"

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        # Only add Cookie header if we have a valid JSESSIONID
        if jsessionid and not jsessionid.startswith("temp_"):
            headers["Cookie"] = f"languagecode={self.auth_coordinator.language_code}; JSESSIONID={jsessionid}"

        try:
            _LOGGER.debug(
                "Fetching device info for device %s (url=%s)",
                self.device_id,
                url,
            )
            async with self.auth_coordinator.request(
                "get",
                url,
                headers=headers,
                timeout=15,
            ) as response:
                if response.status == 200:
                    response_json = await response.json()
                    _LOGGER.debug(
                        "Device info response for device %s: %s",
                        self.device_id,
                        response_json,
                    )

                    if response_json.get("code") == 200 and "data" in response_json:
                        device_data = response_json["data"]
                        is_on = device_data.get("onOff") == 1
                        fan_on = device_data.get("fan") == 1
                        work_status = device_data.get("workStatus", 0)
                        pump_count = device_data.get("pumpCount", 0)
                        
                        # Get timing values from API
                        work_remain = device_data.get("workRemainTime", 0) or 0
                        pause_remain = device_data.get("pauseRemainTime", 0) or 0
                        
                        # Get work/pause duration settings
                        # API may return workSec/pauseSec, or we infer from schedule
                        work_duration = device_data.get("workSec", self._prev_work_duration)
                        pause_duration = device_data.get("pauseSec", self._prev_pause_duration)
                        
                        # If workStatus=2 and workRemain > stored duration, update it
                        if work_status == 2 and work_remain > work_duration:
                            work_duration = work_remain
                        
                        # Update oil tracking with cycle detection
                        self.update_oil_tracking(
                            device_on=is_on,
                            work_status=work_status,
                            work_remain=work_remain,
                            pause_remain=pause_remain,
                            work_duration=work_duration,
                            pause_duration=pause_duration,
                        )
                        
                        # Get comprehensive oil tracking info
                        oil_info = self.get_oil_tracking_info()
                        
                        return {
                            "state": is_on,
                            "onOff": device_data.get("onOff"),
                            "fan": device_data.get("fan", 0),
                            "fan_state": fan_on,
                            "workStatus": work_status,
                            "workRemainTime": work_remain,
                            "pauseRemainTime": pause_remain,
                            "workSec": work_duration,
                            "pauseSec": pause_duration,
                            "raw_device_data": device_data,
                            "device_id": self.device_id,
                            "device_name": self.device_name,
                            "pumpCount": pump_count,
                            "runCount": device_data.get("runCount", 0),
                            # Oil tracking data
                            **oil_info,
                        }
                    else:
                        error_msg = response_json.get("msg", "Unknown error")
                        _LOGGER.error(
                            f"API error for device {self.device_id}: {error_msg}")
                        raise UpdateFailed(f"API error: {error_msg}")
                elif response.status in [401, 403]:
                    _LOGGER.warning(
                        f"Authentication error ({response.status}) for device {self.device_id}. Forcing re-login.")
                    self.auth_coordinator.jsessionid = None
                    raise UpdateFailed(f"Authentication error")
                else:
                    _LOGGER.error(
                        f"Failed to fetch device {self.device_id} info, status: {response.status}")
                    raise UpdateFailed(
                        f"Error fetching device info: status {response.status}")
        except Exception as e:
            _LOGGER.error(f"Error fetching device {self.device_id} info: {e}")
            raise UpdateFailed(f"Error: {e}")

    async def api_request(self, url, method="GET", params=None, data=None, json_body=None):
        """Make an authenticated API request for diagnostics/testing."""
        await self.auth_coordinator._ensure_login()
        jsessionid = self.auth_coordinator.jsessionid

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        if jsessionid and not jsessionid.startswith("temp_"):
            headers["Cookie"] = (
                f"languagecode={self.auth_coordinator.language_code}; JSESSIONID={jsessionid}"
            )

        if json_body is not None:
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        _LOGGER.debug("API diagnostics request: %s %s", method, url)

        async with self.auth_coordinator.request(
            method,
            url,
            params=params,
            data=data,
            json=json_body,
            timeout=15,
            headers=headers,
        ) as response:
            content_type = response.headers.get("Content-Type", "")
            response_text = await response.text()

            try:
                response_json = await response.json()
            except Exception:
                response_json = None

            return {
                "status": response.status,
                "content_type": content_type,
                "json": response_json,
                "text": response_text if response_json is None else None,
            }

    async def turn_on_off(self, state_to_set):
        """Turn the diffuser on or off."""
        await self.auth_coordinator._ensure_login()
        jsessionid = self.auth_coordinator.jsessionid

        url = "https://www.aroma-link.com/device/switch"

        data = {
            "deviceId": self.device_id,
            "onOff": 1 if state_to_set else 0
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        if jsessionid and not jsessionid.startswith("temp_"):
            headers["Cookie"] = f"languagecode={self.auth_coordinator.language_code}; JSESSIONID={jsessionid}"

        try:
            _LOGGER.debug(
                "Switch request for device %s (data=%s)",
                self.device_id,
                data,
            )
            async with self.auth_coordinator.request(
                "post",
                url,
                data=data,
                headers=headers,
                timeout=10,
            ) as response:
                if response.status == 200:
                    response_text = await response.text()
                    _LOGGER.debug(
                        "Switch response for device %s: %s",
                        self.device_id,
                        response_text,
                    )
                    _LOGGER.info(
                        f"Successfully commanded device {self.device_id} to {'on' if state_to_set else 'off'}")
                    await self.async_request_refresh()
                    return True
                elif response.status in [401, 403]:
                    _LOGGER.warning(
                        f"Authentication error on turn_on_off ({response.status}).")
                    self.auth_coordinator.jsessionid = None
                    return False
                else:
                    _LOGGER.error(
                        f"Failed to control device {self.device_id}: {response.status}")
                    return False
        except Exception as e:
            _LOGGER.error(f"Control error for device {self.device_id}: {e}")
            return False

    async def fan_control(self, state_to_set):
        """Turn the fan on or off."""
        await self.auth_coordinator._ensure_login()
        jsessionid = self.auth_coordinator.jsessionid

        url = "https://www.aroma-link.com/device/switch"

        data = {
            "deviceId": self.device_id,
            "fan": 1 if state_to_set else 0
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        if jsessionid and not jsessionid.startswith("temp_"):
            headers["Cookie"] = f"languagecode={self.auth_coordinator.language_code}; JSESSIONID={jsessionid}"

        try:
            _LOGGER.debug(
                "Fan request for device %s (data=%s)",
                self.device_id,
                data,
            )
            async with self.auth_coordinator.request(
                "post",
                url,
                data=data,
                headers=headers,
                timeout=10,
            ) as response:
                if response.status == 200:
                    response_text = await response.text()
                    _LOGGER.debug(
                        "Fan response for device %s: %s",
                        self.device_id,
                        response_text,
                    )
                    _LOGGER.info(
                        f"Successfully commanded fan for device {self.device_id} to {'on' if state_to_set else 'off'}")
                    await self.async_request_refresh()
                    return True
                elif response.status in [401, 403]:
                    _LOGGER.warning(
                        f"Authentication error on fan_control ({response.status}).")
                    self.auth_coordinator.jsessionid = None
                    return False
                else:
                    _LOGGER.error(
                        f"Failed to control fan for device {self.device_id}: {response.status}")
                    return False
        except Exception as e:
            _LOGGER.error(f"Fan control error for device {self.device_id}: {e}")
            return False

    async def set_scheduler(self, work_duration=None, pause_duration=None, week_days=None):
        """Set the scheduler for the diffuser."""
        await self.auth_coordinator._ensure_login()
        jsessionid = self.auth_coordinator.jsessionid

        url = "https://www.aroma-link.com/device/workSet"

        if week_days is None:
            week_days = [0, 1, 2, 3, 4, 5, 6]  # Default to all days

        # Use provided values or fall back to stored values
        work_duration = work_duration if work_duration is not None else self._work_duration
        pause_duration = pause_duration if pause_duration is not None else self._pause_duration

        payload = {
            "deviceId": self.device_id,
            "type": "workTime",
            "week": week_days,
            "workTimeList": [
                {
                    "startTime": "00:00",
                    "endTime": "23:59",
                    "enabled": 1,
                    "consistenceLevel": "1",
                    "workDuration": str(work_duration),
                    "pauseDuration": str(pause_duration)
                },
                {
                    "startTime": "00:00",
                    "endTime": "24:00",
                    "enabled": 0,
                    "consistenceLevel": "1",
                    "workDuration": "10",
                    "pauseDuration": "900"
                },
                {
                    "startTime": "00:00",
                    "endTime": "24:00",
                    "enabled": 0,
                    "consistenceLevel": "1",
                    "workDuration": "10",
                    "pauseDuration": "900"
                },
                {
                    "startTime": "00:00",
                    "endTime": "24:00",
                    "enabled": 0,
                    "consistenceLevel": "1",
                    "workDuration": "10",
                    "pauseDuration": "900"
                },
                {
                    "startTime": "00:00",
                    "endTime": "24:00",
                    "enabled": 0,
                    "consistenceLevel": "1",
                    "workDuration": "10",
                    "pauseDuration": "900"
                }
            ]
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        if jsessionid and not jsessionid.startswith("temp_"):
            headers["Cookie"] = f"languagecode={self.auth_coordinator.language_code}; JSESSIONID={jsessionid}"

        try:
            _LOGGER.debug(
                "Scheduler request for device %s (payload=%s)",
                self.device_id,
                payload,
            )
            async with self.auth_coordinator.request(
                "post",
                url,
                json=payload,
                headers=headers,
                timeout=10,
            ) as response:
                if response.status == 200:
                    response_text = await response.text()
                    _LOGGER.debug(
                        "Scheduler response for device %s: %s",
                        self.device_id,
                        response_text,
                    )
                    _LOGGER.info(
                        f"Successfully set scheduler for device {self.device_id}")
                    await self.async_request_refresh()
                    return True
                elif response.status in [401, 403]:
                    _LOGGER.warning(
                        f"Authentication error on set_scheduler ({response.status}).")
                    self.auth_coordinator.jsessionid = None
                    return False
                else:
                    _LOGGER.error(
                        f"Failed to set scheduler for device {self.device_id}: {response.status}")
                    return False
        except Exception as e:
            _LOGGER.error(f"Scheduler error for device {self.device_id}: {e}")
            return False

    async def run_diffuser(self, work_duration=None, pause_duration=None):
        """Run the diffuser for a specific time."""
        # Use default values if specific ones aren't provided
        current_work_duration = work_duration if work_duration is not None else self._work_duration
        current_pause_duration = pause_duration if pause_duration is not None else self._pause_duration
        buffertime = current_work_duration + 5  # Add buffer time

        _LOGGER.info(
            f"Setting up device {self.device_id} to run for {current_work_duration} seconds with {current_work_duration} second diffusion cycles and {current_pause_duration} second pauses")

        # Set scheduler
        if not await self.set_scheduler(current_work_duration, current_pause_duration):
            _LOGGER.error(
                f"Failed to set scheduler for device {self.device_id}")
            return False

        await asyncio.sleep(1)  # Allow time for scheduler settings to apply

        if not await self.turn_on_off(True):
            _LOGGER.error(f"Failed to turn on device {self.device_id}")
            return False

        _LOGGER.info(
            f"Device {self.device_id} turned on. Will turn off automatically after {buffertime} seconds.")

        # Schedule turn off after the specified time
        async def turn_off_later():
            await asyncio.sleep(buffertime)
            _LOGGER.info(
                f"Timer complete for device {self.device_id}. Attempting to turn off.")
            if not await self.turn_on_off(False):
                _LOGGER.error(
                    f"Failed to automatically turn off device {self.device_id}")
            else:
                _LOGGER.info(
                    f"Device {self.device_id} turned off successfully after timer")

        self.hass.async_create_task(turn_off_later())

        return True
