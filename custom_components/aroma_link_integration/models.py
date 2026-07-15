"""Pure schedule/data model for the Aroma-Link integration.

This module is intentionally free of Home Assistant imports so it can be
unit-tested with plain pytest/unittest. It owns:

- the ONE canonical day-of-week convention (`Weekday`, Monday=0, matching
  ``datetime.weekday()``). The Aroma-Link cloud uses Sunday=0; conversion
  happens ONLY via `to_cloud_day`/`from_cloud_day`, which may be called only
  from the reconciler and the one-time migration importer.
- the schedule data model persisted in the HA Store,
- compilation of that model into the device's 5-slot wire format,
- validation, hashing (drift detection), and time-window queries used by the
  gating engine.

Slot doctrine: the device's slots are the *capability superset* (when
diffusing is permitted); the gating engine's power switching is the
*decision*. Slots change only when the model changes — runtime gating never
rewrites slots.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from enum import IntEnum

# Device firmware exposes 5 schedule slots per day. Slots 1-4 carry the
# user's normal windows; slot 5 is reserved for Night Owl / timed-run overlay.
SLOT_COUNT = 5
MAX_WINDOWS = 4
NIGHT_OWL_SLOT_INDEX = 4  # 0-based position of slot 5

WORK_SEC_MIN, WORK_SEC_MAX = 5, 900
PAUSE_SEC_MIN, PAUSE_SEC_MAX = 5, 900

LEVEL_LETTERS = {1: "A", 2: "B", 3: "C"}
LETTER_LEVELS = {v: k for k, v in LEVEL_LETTERS.items()}


class Weekday(IntEnum):
    """Canonical day convention: Monday=0 .. Sunday=6 (== datetime.weekday())."""

    MON = 0
    TUE = 1
    WED = 2
    THU = 3
    FRI = 4
    SAT = 5
    SUN = 6

    @property
    def prev(self) -> "Weekday":
        return Weekday((int(self) - 1) % 7)

    @property
    def next(self) -> "Weekday":
        return Weekday((int(self) + 1) % 7)


def to_cloud_day(day: Weekday) -> int:
    """Canonical (Mon=0) -> Aroma-Link cloud (Sun=0)."""
    return (int(day) + 1) % 7


def from_cloud_day(cloud_day: int) -> Weekday:
    """Aroma-Link cloud (Sun=0) -> canonical (Mon=0)."""
    return Weekday((int(cloud_day) + 6) % 7)


def today(now: datetime) -> Weekday:
    return Weekday(now.weekday())


def parse_hhmm(value: str) -> time:
    """Parse 'HH:MM' (device also emits '24:00', treated as end-of-day)."""
    hh, mm = value.split(":")
    hh_i, mm_i = int(hh), int(mm)
    if hh_i == 24 and mm_i == 0:
        return time(23, 59)
    return time(hh_i, mm_i)


def _is_hhmm(value: str) -> bool:
    try:
        if not isinstance(value, str) or len(value.split(":")) != 2:
            return False
        hh, mm = value.split(":")
        hh_i, mm_i = int(hh), int(mm)
        if hh_i == 24 and mm_i == 0:
            return True
        return 0 <= hh_i <= 23 and 0 <= mm_i <= 59
    except (ValueError, AttributeError):
        return False


@dataclass(frozen=True)
class ScheduleWindow:
    """One normal diffusing window (device slots 1-4). Must not cross midnight."""

    start: str  # "HH:MM"
    end: str    # "HH:MM", end > start
    work_sec: int
    pause_sec: int
    level: int  # 1|2|3 (A|B|C)
    enabled: bool

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "work_sec": self.work_sec,
            "pause_sec": self.pause_sec,
            "level": self.level,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleWindow":
        return cls(
            start=str(data["start"]),
            end=str(data["end"]),
            work_sec=int(data["work_sec"]),
            pause_sec=int(data["pause_sec"]),
            level=int(data.get("level", 1)),
            enabled=bool(data.get("enabled", True)),
        )

    def contains(self, t: time) -> bool:
        return parse_hhmm(self.start) <= t < parse_hhmm(self.end)


@dataclass
class DaySchedule:
    """Windows + Night Owl preference for one canonical weekday.

    ``night_owl`` refers to the night that STARTS this evening (so Friday's
    flag governs Friday 22:00 through Saturday 06:00 in fixed mode).
    """

    windows: list[ScheduleWindow] = field(default_factory=list)  # 0..4
    night_owl: bool = False

    def enabled_windows(self) -> list[ScheduleWindow]:
        return [w for w in self.windows if w.enabled]

    def to_dict(self) -> dict:
        return {
            "windows": [w.to_dict() for w in self.windows],
            "night_owl": self.night_owl,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DaySchedule":
        return cls(
            windows=[ScheduleWindow.from_dict(w) for w in data.get("windows", [])][:MAX_WINDOWS],
            night_owl=bool(data.get("night_owl", False)),
        )


@dataclass
class NightOwlSettings:
    """Device-level Night Owl configuration (per-day allow flags live on DaySchedule)."""

    mode: str = "outside_windows"  # "outside_windows" | "fixed"
    fixed_start: str = "22:00"
    fixed_end: str = "06:00"
    work_sec: int = 10
    pause_sec: int = 300
    level: int = 1
    linger_minutes: int = 10

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "fixed_start": self.fixed_start,
            "fixed_end": self.fixed_end,
            "work_sec": self.work_sec,
            "pause_sec": self.pause_sec,
            "level": self.level,
            "linger_minutes": self.linger_minutes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NightOwlSettings":
        base = cls()
        return cls(
            mode=str(data.get("mode", base.mode)),
            fixed_start=str(data.get("fixed_start", base.fixed_start)),
            fixed_end=str(data.get("fixed_end", base.fixed_end)),
            work_sec=int(data.get("work_sec", base.work_sec)),
            pause_sec=int(data.get("pause_sec", base.pause_sec)),
            level=int(data.get("level", base.level)),
            linger_minutes=int(data.get("linger_minutes", base.linger_minutes)),
        )


@dataclass
class WeeklySchedule:
    days: dict[Weekday, DaySchedule] = field(
        default_factory=lambda: {d: DaySchedule() for d in Weekday}
    )
    version: int = 0
    updated_at: str = ""  # ISO UTC

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "days": {str(int(d)): self.days[d].to_dict() for d in Weekday},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WeeklySchedule":
        days = {d: DaySchedule() for d in Weekday}
        for key, day_data in (data.get("days") or {}).items():
            days[Weekday(int(key))] = DaySchedule.from_dict(day_data)
        return cls(
            days=days,
            version=int(data.get("version", 0)),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class DeviceModel:
    """Everything the integration persists about one device's desired state."""

    schedule: WeeklySchedule = field(default_factory=WeeklySchedule)
    night_owl: NightOwlSettings = field(default_factory=NightOwlSettings)
    schedule_enabled: bool = True   # gating-engine master switch
    night_owl_enabled: bool = True  # night-owl master switch
    # Defaults used for timed runs and newly created windows.
    default_work_sec: int = 10
    default_pause_sec: int = 300

    def to_dict(self) -> dict:
        return {
            "schedule": self.schedule.to_dict(),
            "night_owl": self.night_owl.to_dict(),
            "schedule_enabled": self.schedule_enabled,
            "night_owl_enabled": self.night_owl_enabled,
            "default_work_sec": self.default_work_sec,
            "default_pause_sec": self.default_pause_sec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceModel":
        return cls(
            schedule=WeeklySchedule.from_dict(data.get("schedule") or {}),
            night_owl=NightOwlSettings.from_dict(data.get("night_owl") or {}),
            schedule_enabled=bool(data.get("schedule_enabled", True)),
            night_owl_enabled=bool(data.get("night_owl_enabled", True)),
            default_work_sec=int(data.get("default_work_sec", 10)),
            default_pause_sec=int(data.get("default_pause_sec", 300)),
        )


@dataclass(frozen=True)
class CloudSlot:
    """Exact wire shape of one entry in the device workSet payload."""

    start_time: str
    end_time: str
    enabled: int  # 0|1
    consistence_level: str  # "1"|"2"|"3"
    work_duration: str  # seconds, stringified
    pause_duration: str  # seconds, stringified

    def to_payload(self) -> dict:
        return {
            "startTime": self.start_time,
            "endTime": self.end_time,
            "enabled": self.enabled,
            "consistenceLevel": self.consistence_level,
            "workDuration": self.work_duration,
            "pauseDuration": self.pause_duration,
        }


# Canonical filler for unused slots — matches the shape historical writers used
# so untouched devices don't show spurious drift.
FILLER_SLOT = CloudSlot(
    start_time="00:00",
    end_time="23:59",
    enabled=0,
    consistence_level="1",
    work_duration="10",
    pause_duration="120",
)


@dataclass(frozen=True)
class RunOverlay:
    """Transient timed-run overlay: slot 5 becomes a 24/7 enabled window today."""

    work_sec: int
    pause_sec: int
    level: int = 1


def _window_to_slot(window: ScheduleWindow) -> CloudSlot:
    return CloudSlot(
        start_time=window.start,
        end_time=window.end,
        enabled=1 if window.enabled else 0,
        consistence_level=str(window.level),
        work_duration=str(window.work_sec),
        pause_duration=str(window.pause_sec),
    )


def _fixed_crosses_midnight(settings: NightOwlSettings) -> bool:
    return parse_hhmm(settings.fixed_end) <= parse_hhmm(settings.fixed_start)


def _night_owl_slot(model: DeviceModel, day: Weekday) -> CloudSlot:
    """Compile slot 5 for a day.

    The slot is a deliberate SUPERSET of when Night Owl may run: the gating
    engine only powers the device on when the *owning* evening's per-day
    preference (and motion) allow it, so over-enabling here is harmless while
    under-enabling would block legitimate runs.
    """
    settings = model.night_owl
    days = model.schedule.days

    if not model.night_owl_enabled:
        allowed = False
    elif settings.mode == "fixed" and not _fixed_crosses_midnight(settings):
        # Same-day window: only this day's own preference matters.
        allowed = days[day].night_owl
    else:
        # Midnight-crossing fixed window or outside_windows mode: day D's slot
        # covers both D's evening and the morning tail of D-1's night.
        allowed = days[day].night_owl or days[day.prev].night_owl

    if settings.mode == "fixed":
        start, end = settings.fixed_start, settings.fixed_end
    else:
        start, end = "00:00", "23:59"

    return CloudSlot(
        start_time=start,
        end_time=end,
        enabled=1 if allowed else 0,
        consistence_level=str(settings.level),
        work_duration=str(settings.work_sec),
        pause_duration=str(settings.pause_sec),
    )


def compile_week(
    model: DeviceModel,
    overlay: RunOverlay | None = None,
    overlay_day: Weekday | None = None,
) -> dict[Weekday, list[CloudSlot]]:
    """Compile the model into 7 days x 5 CloudSlots.

    ``overlay`` (timed run) replaces slot 5 on ``overlay_day`` with a 24/7
    enabled window so power-on always diffuses during the run.
    """
    compiled: dict[Weekday, list[CloudSlot]] = {}
    for day in Weekday:
        slots = [_window_to_slot(w) for w in model.schedule.days[day].windows[:MAX_WINDOWS]]
        while len(slots) < MAX_WINDOWS:
            slots.append(FILLER_SLOT)
        slots.append(_night_owl_slot(model, day))
        compiled[day] = slots

    if overlay is not None and overlay_day is not None:
        overlay_slot = CloudSlot(
            start_time="00:00",
            end_time="23:59",
            enabled=1,
            consistence_level=str(overlay.level),
            work_duration=str(overlay.work_sec),
            pause_duration=str(overlay.pause_sec),
        )
        # Arm the start day AND the following day so a run crossing midnight
        # keeps diffusing (runs are capped at 24h, so two days always cover
        # it). Harmless superset: power gating decides when it actually runs.
        for day in (overlay_day, overlay_day.next):
            slots = list(compiled[day])
            slots[NIGHT_OWL_SLOT_INDEX] = overlay_slot
            compiled[day] = slots

    return compiled


def schedule_hash(compiled: dict[Weekday, list[CloudSlot]]) -> str:
    """Stable hash of a compiled week, for write verification and drift checks."""
    canonical = {
        str(int(day)): [slot.to_payload() for slot in slots]
        for day, slots in sorted(compiled.items(), key=lambda kv: int(kv[0]))
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


def day_hash(slots: list[CloudSlot]) -> str:
    """Stable hash of one day's 5 slots (per-day write verification)."""
    blob = json.dumps([s.to_payload() for s in slots], sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


def validate_schedule(schedule: WeeklySchedule) -> list[str]:
    """Return a list of human-readable problems; empty list means valid."""
    errors: list[str] = []
    for day in Weekday:
        day_sched = schedule.days.get(day)
        if day_sched is None:
            errors.append(f"{day.name}: missing day")
            continue
        if len(day_sched.windows) > MAX_WINDOWS:
            errors.append(f"{day.name}: more than {MAX_WINDOWS} windows")
        for i, w in enumerate(day_sched.windows, start=1):
            label = f"{day.name} window {i}"
            if not _is_hhmm(w.start) or not _is_hhmm(w.end):
                errors.append(f"{label}: invalid time format")
                continue
            if parse_hhmm(w.end) <= parse_hhmm(w.start):
                errors.append(f"{label}: end must be after start (no midnight crossing)")
            if not (WORK_SEC_MIN <= w.work_sec <= WORK_SEC_MAX):
                errors.append(f"{label}: work_sec out of range {WORK_SEC_MIN}-{WORK_SEC_MAX}")
            if not (PAUSE_SEC_MIN <= w.pause_sec <= PAUSE_SEC_MAX):
                errors.append(f"{label}: pause_sec out of range {PAUSE_SEC_MIN}-{PAUSE_SEC_MAX}")
            if w.level not in LEVEL_LETTERS:
                errors.append(f"{label}: level must be 1, 2, or 3")
        # Overlap check among enabled, individually-valid windows.
        enabled = [
            w
            for w in day_sched.windows
            if w.enabled
            and _is_hhmm(w.start)
            and _is_hhmm(w.end)
            and parse_hhmm(w.start) < parse_hhmm(w.end)
        ]
        for i in range(len(enabled)):
            for j in range(i + 1, len(enabled)):
                a, b = enabled[i], enabled[j]
                if parse_hhmm(a.start) < parse_hhmm(b.end) and parse_hhmm(b.start) < parse_hhmm(a.end):
                    errors.append(
                        f"{day.name}: windows {a.start}-{a.end} and {b.start}-{b.end} overlap"
                    )
    return errors


def active_window(
    model: DeviceModel, now: datetime
) -> tuple[Weekday, int, ScheduleWindow] | None:
    """Return (day, window_index, window) if ``now`` is inside an enabled window."""
    day = today(now)
    t = now.time()
    for idx, window in enumerate(model.schedule.days[day].windows):
        if window.enabled and window.contains(t):
            return (day, idx, window)
    return None


def _owning_evening(model: DeviceModel, now: datetime) -> Weekday | None:
    """Which evening's Night Owl preference governs ``now``, or None if not night.

    fixed mode: inside the fixed window only. Evening part -> today; morning
    part of a midnight-crossing window -> yesterday.

    outside_windows mode: after today's last enabled window -> today; before
    today's first enabled window -> yesterday. Gaps BETWEEN two windows on the
    same day are not Night Owl territory. A day with no enabled windows splits
    at noon (before noon -> yesterday's evening, after -> today's).
    """
    settings = model.night_owl
    day = today(now)
    t = now.time()

    if settings.mode == "fixed":
        start, end = parse_hhmm(settings.fixed_start), parse_hhmm(settings.fixed_end)
        if start < end:  # same-day window
            return day if start <= t < end else None
        # crosses midnight
        if t >= start:
            return day
        if t < end:
            return day.prev
        return None

    # outside_windows mode
    enabled = model.schedule.days[day].enabled_windows()
    if not enabled:
        return day.prev if t < time(12, 0) else day
    first_start = min(parse_hhmm(w.start) for w in enabled)
    last_end = max(parse_hhmm(w.end) for w in enabled)
    if t >= last_end:
        return day
    if t < first_start:
        return day.prev
    return None  # inside a window or a mid-day gap


def night_owl_period(model: DeviceModel, now: datetime) -> bool:
    """True when ``now`` falls in a Night Owl period whose owning evening allows it."""
    if not model.night_owl_enabled:
        return False
    if active_window(model, now) is not None:
        return False
    owner = _owning_evening(model, now)
    if owner is None:
        return False
    return model.schedule.days[owner].night_owl
