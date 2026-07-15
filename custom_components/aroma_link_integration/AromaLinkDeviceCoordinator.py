import asyncio
import logging
import time
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import (
    DOMAIN,
    DEFAULT_WORK_DURATION,
    DEFAULT_PAUSE_DURATION,
)

_LOGGER = logging.getLogger(__name__)


class _AuthRetryable(Exception):
    """Raised internally when a 401/403 should trigger a retry."""

    def __init__(self, status):
        self.status = status
        super().__init__(f"HTTP {status}")


class AromaLinkDeviceCoordinator(DataUpdateCoordinator):
    """Coordinator for handling device data and control."""

    # After a switch command, polls that contradict it are only trusted when
    # the server's snapshot (statisticsUpdateTime) postdates the command; the
    # device takes 15-20s to acknowledge commands and the API can serve stale
    # cached state long after that (upstream issue #34).
    STALE_STATS_PROTECT_SECONDS = 1800
    # Fallback shield when the poll carries no snapshot timestamp at all.
    NO_STATS_PROTECT_SECONDS = 180
    # Snapshots must postdate the command by this margin to count as fresh,
    # absorbing small clock skew between the Aroma-Link server and this host.
    STATS_FRESHNESS_MARGIN_SECONDS = 5

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
        self._work_duration = DEFAULT_WORK_DURATION
        self._pause_duration = DEFAULT_PAUSE_DURATION
        # Injected by setup: () -> DeviceModel, used for the oil daily-work
        # estimate. The coordinator no longer owns any schedule state.
        self.schedule_provider = None

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

        self._command_page_visited = False

        # Last power-switch command, used to shield polls that still report
        # pre-command state (upstream issue #34).
        self._last_switch_command_at = 0.0
        self._last_switch_command_wall = 0.0
        self._last_switch_state = None

        self._save_oil_state_cb = save_oil_state_cb

        if oil_state:
            self._apply_oil_state(oil_state)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_id}",
            update_interval=timedelta(seconds=update_interval_seconds),
        )

        # Seed fallback state so entity code never sees data=None when the
        # first refresh fails (the device stays registered and recovers on a
        # later poll).
        if self.data is None:
            self.data = self._default_device_data()

    def _default_device_data(self):
        """Return the fallback state used before the first successful refresh."""
        return {
            "state": False,
            "onOff": None,
            "fan": 0,
            "fan_state": False,
            "workStatus": None,
            "workRemainTime": None,
            "pauseRemainTime": None,
            "workSec": self._work_duration,
            "pauseSec": self._pause_duration,
            "raw_device_data": {},
            "device_id": self.device_id,
            "device_name": self.device_name,
            "pumpCount": 0,
            "runCount": 0,
        }

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
        # Legacy payloads also carried saved_enabled_state / night_owl_per_day;
        # those now live in the schedule model (imported by migration.py) and
        # are intentionally ignored here.

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
        """Estimate expected work seconds per day from the schedule model.

        Uses the injected ``schedule_provider`` (returns the DeviceModel).
        Index 0 = Monday (canonical), which only feeds a per-day average for
        the oil estimate, so ordering is irrelevant to consumers.
        """
        day_seconds = [0.0] * 7

        model = self.schedule_provider() if self.schedule_provider else None
        if model is None:
            return day_seconds

        for day, day_sched in model.schedule.days.items():
            total_day_seconds = 0.0
            for window in day_sched.windows:
                if not window.enabled or window.work_sec <= 0:
                    continue

                start_sec = self._parse_time_to_seconds(window.start)
                end_sec = self._parse_time_to_seconds(window.end)
                span = end_sec - start_sec
                if span <= 0:
                    span += 24 * 3600

                cycle = window.work_sec + max(window.pause_sec, 0)
                if cycle <= 0:
                    continue

                total_day_seconds += span * (window.work_sec / cycle)

            day_seconds[int(day)] = total_day_seconds

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
        }

    async def fetch_workset_for_day(self, week_day=0):
        """Fetch full workset (all 5 programs) for a specific day.
        
        Args:
            week_day: Day of week (0=Monday, 1=Tuesday, ..., 6=Sunday)
            
        Returns:
            List of 5 program dictionaries with keys: enabled, start_time, end_time, 
            work_sec, pause_sec, level, setting_id. Returns None on error.
        """
        await self.auth_coordinator._ensure_login()

        url = f"https://www.aroma-link.com/device/workTime/{self.device_id}?week={week_day}"

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

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

    async def async_write_cloud_days(self, cloud_days, slot_payloads):
        """Dumb transport: write 5 slot payloads to the given cloud days.

        ``cloud_days`` use the CLOUD day convention (Sun=0). ``slot_payloads``
        is the list of 5 wire dicts (CloudSlot.to_payload()). No cache, no
        refresh, no retries — the reconciler owns policy. Returns True on a
        200 response.
        """
        await self.auth_coordinator._ensure_login()

        payload = {
            "deviceId": self.device_id,
            "type": "workTime",
            "week": list(cloud_days),
            "workTimeList": list(slot_payloads),
        }
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        try:
            _LOGGER.debug(
                "workSet write for device %s days=%s", self.device_id, cloud_days
            )
            async with self.auth_coordinator.request(
                "post",
                "https://www.aroma-link.com/device/workSet",
                json=payload,
                headers=headers,
                timeout=15,
            ) as response:
                body = await response.text()
                if response.status == 200:
                    _LOGGER.debug(
                        "workSet response for device %s: %s", self.device_id, body[:200]
                    )
                    return True
                if response.status in (401, 403):
                    _LOGGER.warning(
                        "Authentication error on workSet write (%s).", response.status
                    )
                    self.auth_coordinator.jsessionid = None
                    return False
                _LOGGER.error(
                    "workSet write failed for device %s: HTTP %s",
                    self.device_id,
                    response.status,
                )
                return False
        except Exception as err:
            _LOGGER.error("workSet write error for device %s: %s", self.device_id, err)
            return False

    def get_device_info(self):
        """Get device info for entity setup."""
        return {
            "id": self.device_id,
            "name": self.device_name
        }

    async def _visit_command_page(self):
        """Load the device command page so server-side session is valid for subsequent AJAX calls."""
        cmd_url = f"https://www.aroma-link.com/device/command/{self.device_id}"
        pre = {c.key for c in self.auth_coordinator.session.cookie_jar}
        try:
            async with self.auth_coordinator.request(
                "get",
                cmd_url,
                headers={
                    "Referer": "https://www.aroma-link.com/device/list",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                timeout=10,
            ) as resp:
                body = await resp.text()
                # Capture any new cookies set by the command page.
                post = {c.key for c in self.auth_coordinator.session.cookie_jar}
                new_cookies = post - pre
                if new_cookies:
                    self.auth_coordinator._aroma_cookie_names.update(new_cookies)
                    _LOGGER.debug(
                        "Device %s command page set new cookies: %s",
                        self.device_id, new_cookies,
                    )
                # Detect redirect to login page (aiohttp follows 302 automatically).
                final_url = str(resp.url)
                redirected_to_login = "/login" in final_url or "<form" in body[:2000] and "password" in body[:2000]
                _LOGGER.debug(
                    "Device %s command-page warm-up status: %s, final_url: %s, redirected_to_login: %s",
                    self.device_id,
                    resp.status,
                    final_url,
                    redirected_to_login,
                )
                if resp.status == 200 and not redirected_to_login:
                    self._command_page_visited = True
                elif redirected_to_login:
                    _LOGGER.warning(
                        "Device %s command page redirected to login — session may be invalid.",
                        self.device_id,
                    )
        except Exception as exc:
            _LOGGER.debug(
                "Device %s command-page warm-up failed (non-fatal): %s",
                self.device_id,
                exc,
            )

    def _poll_stats_timestamp(self, data):
        """Return the server snapshot time (epoch seconds) carried by a poll, if any."""
        raw = data.get("raw_device_data")
        if not isinstance(raw, dict):
            return None
        value = raw.get("statisticsUpdateTime")
        if value is None:
            return None
        try:
            return int(value) / 1000
        except (TypeError, ValueError):
            return None

    def _apply_recent_switch_state(self, data):
        """Keep the last switch command while polls still report pre-command state.

        Polls can reflect a device snapshot that predates a just-sent switch
        command (upstream issue #34: HA flipped back to On one poll after
        powering off). A contradicting poll is therefore only trusted when its
        statisticsUpdateTime shows it postdates the command, with time-based
        caps as a backstop.
        """
        if not isinstance(data, dict):
            return data

        if self._last_switch_state is None:
            return data

        optimistic_on_off = 1 if self._last_switch_state else 0

        # The server reflects our command; nothing to shield.
        if data.get("onOff") is not None and data.get("onOff") == optimistic_on_off:
            return data

        command_age = time.monotonic() - self._last_switch_command_at
        stats_timestamp = self._poll_stats_timestamp(data)

        if stats_timestamp is not None:
            snapshot_is_fresh = stats_timestamp >= (
                self._last_switch_command_wall + self.STATS_FRESHNESS_MARGIN_SECONDS
            )
            if snapshot_is_fresh or command_age > self.STALE_STATS_PROTECT_SECONDS:
                return data
            _LOGGER.debug(
                "Ignoring stale poll for device %s: server snapshot %.0fs older than "
                "the last switch command; keeping commanded state onOff=%s.",
                self.device_id,
                self._last_switch_command_wall - stats_timestamp,
                optimistic_on_off,
            )
        elif command_age > self.NO_STATS_PROTECT_SECONDS:
            return data

        optimistic = dict(data)
        optimistic["onOff"] = optimistic_on_off
        optimistic["state"] = bool(self._last_switch_state)
        if self._last_switch_state:
            # The stale poll may still say idle; report the commanded state as
            # active. A reported pause is plausible mid-cycle, keep it.
            if not optimistic.get("workStatus"):
                optimistic["workStatus"] = 1
        else:
            optimistic["workStatus"] = 0

        return optimistic

    async def _delayed_refresh(self, delay_seconds=3):
        """Refresh later so optimistic state is not immediately wiped by stale data."""
        await asyncio.sleep(delay_seconds)
        await self.async_request_refresh()

    async def _async_update_data(self):
        """Fetch current device state from API, with one retry on 401/403."""
        await self.auth_coordinator._ensure_login()

        if not self._command_page_visited:
            await self._visit_command_page()

        try:
            return self._apply_recent_switch_state(await self._fetch_device_info())
        except _AuthRetryable as retry_err:
            # First 403/401: force a fresh login + command page visit, then retry once.
            _LOGGER.warning(
                "Got %s for device %s — re-authenticating and retrying once.",
                retry_err.status,
                self.device_id,
            )
            self.auth_coordinator.jsessionid = None
            self._command_page_visited = False
            await self.auth_coordinator._ensure_login()
            await self._visit_command_page()
            try:
                return self._apply_recent_switch_state(await self._fetch_device_info())
            except _AuthRetryable:
                _LOGGER.error(
                    "Still getting %s for device %s after re-login. Giving up this cycle.",
                    retry_err.status,
                    self.device_id,
                )
                raise UpdateFailed("Authentication error after retry")
        except UpdateFailed:
            raise
        except Exception as e:
            _LOGGER.error("Error fetching device %s info: %s", self.device_id, e)
            raise UpdateFailed(f"Error: {e}")


    async def _fetch_device_info(self):
        """Single attempt to fetch device info. Raises _AuthRetryable on 401/403."""
        url = f"https://www.aroma-link.com/device/deviceInfo/{self.device_id}"

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

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
                    # deviceInfo sometimes returns pure metadata with NO live
                    # run-state fields (no onOff/fan/workStatus). Reading a
                    # missing onOff as "off" made the gating engine re-assert
                    # power every shield expiry, restarting the device's
                    # work/pause cycle. Carry the last known state forward
                    # instead of inventing "off".
                    prev = self.data if isinstance(self.data, dict) else {}
                    state_known = "onOff" in device_data
                    if not state_known:
                        _LOGGER.debug(
                            "deviceInfo for %s has no live state fields; "
                            "carrying previous state forward",
                            self.device_id,
                        )
                    is_on = (
                        device_data.get("onOff") == 1
                        if state_known
                        else bool(prev.get("state", False))
                    )
                    fan_on = (
                        device_data.get("fan") == 1
                        if "fan" in device_data
                        else bool(prev.get("fan_state", False))
                    )
                    work_status = device_data.get(
                        "workStatus", prev.get("workStatus", 0)
                    )
                    pump_count = device_data.get("pumpCount", prev.get("pumpCount", 0))

                    # Get timing values from API (carry forward when absent)
                    work_remain = (
                        device_data.get("workRemainTime", prev.get("workRemainTime", 0))
                        or 0
                    )
                    pause_remain = (
                        device_data.get(
                            "pauseRemainTime", prev.get("pauseRemainTime", 0)
                        )
                        or 0
                    )

                    # Get work/pause duration settings
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
                        "state_known": state_known,
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
                        "API error for device %s: %s", self.device_id, error_msg)
                    raise UpdateFailed(f"API error: {error_msg}")
            elif response.status in [401, 403]:
                self._command_page_visited = False
                resp_body = await response.text()
                _LOGGER.debug(
                    "Auth error (%s) for device %s. Body (first 200): %s",
                    response.status,
                    self.device_id,
                    resp_body[:200],
                )
                raise _AuthRetryable(response.status)
            else:
                _LOGGER.error(
                    "Failed to fetch device %s info, status: %s",
                    self.device_id,
                    response.status,
                )
                raise UpdateFailed(
                    f"Error fetching device info: status {response.status}")

    async def api_request(self, url, method="GET", params=None, data=None, json_body=None):
        """Make an authenticated API request for diagnostics/testing."""
        await self.auth_coordinator._ensure_login()

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

        # Only send Origin on POST/PUT (browsers don't send it on same-origin GET).
        if method.upper() != "GET":
            headers["Origin"] = "https://www.aroma-link.com"

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

        url = "https://www.aroma-link.com/device/switch"

        data = {
            "deviceId": self.device_id,
            "onOff": 1 if state_to_set else 0
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

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
                    # Record the command and show it optimistically; polls that
                    # still report pre-command state are shielded in
                    # _apply_recent_switch_state (upstream issue #34).
                    self._last_switch_command_at = time.monotonic()
                    self._last_switch_command_wall = time.time()
                    self._last_switch_state = state_to_set
                    optimistic_data = dict(self.data or {})
                    optimistic_data["state"] = state_to_set
                    optimistic_data["onOff"] = 1 if state_to_set else 0
                    optimistic_data["workStatus"] = 1 if state_to_set else 0
                    self.async_set_updated_data(optimistic_data)
                    self.hass.async_create_task(self._delayed_refresh())
                    # Second refresh once the device's 15-20s command ack has passed.
                    self.hass.async_create_task(self._delayed_refresh(25))
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

        url = "https://www.aroma-link.com/device/switch"

        data = {
            "deviceId": self.device_id,
            "fan": 1 if state_to_set else 0
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.aroma-link.com/device/command/{self.device_id}",
        }

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
                    # Optimistic only — no early refresh: the stale-poll shield
                    # covers power, not fan, so a quick re-poll would wipe this
                    # with pre-ack state. The next regular poll reconciles.
                    optimistic_data = dict(self.data or {})
                    optimistic_data["fan"] = 1 if state_to_set else 0
                    optimistic_data["fan_state"] = bool(state_to_set)
                    self.async_set_updated_data(optimistic_data)
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

