"""Unit tests for the pure schedule model (no Home Assistant required)."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "aroma_link_integration"))

import models  # noqa: E402
from models import (  # noqa: E402
    FILLER_SLOT,
    DaySchedule,
    DeviceModel,
    NightOwlSettings,
    RunOverlay,
    ScheduleWindow,
    Weekday,
    WeeklySchedule,
    active_window,
    compile_week,
    day_hash,
    from_cloud_day,
    night_owl_period,
    parse_hhmm,
    schedule_hash,
    to_cloud_day,
    today,
    validate_schedule,
)


def w(start, end, work=10, pause=300, level=1, enabled=True):
    return ScheduleWindow(start=start, end=end, work_sec=work, pause_sec=pause, level=level, enabled=enabled)


def model_with(days=None, night_owl=None, **flags):
    m = DeviceModel()
    if days:
        for day, sched in days.items():
            m.schedule.days[day] = sched
    if night_owl:
        m.night_owl = night_owl
    for k, v in flags.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------- day conversion

def test_day_roundtrip_all_seven():
    for d in Weekday:
        assert from_cloud_day(to_cloud_day(d)) is d
    for cloud in range(7):
        assert to_cloud_day(from_cloud_day(cloud)) == cloud


def test_cloud_convention_anchors():
    # Cloud is Sunday=0 (verified against production behavior of
    # _get_today_schedule_day: (weekday()+1) % 7).
    assert to_cloud_day(Weekday.SUN) == 0
    assert to_cloud_day(Weekday.MON) == 1
    assert to_cloud_day(Weekday.SAT) == 6
    assert from_cloud_day(0) is Weekday.SUN


def test_today_matches_datetime_weekday():
    # 2026-07-13 is a Monday
    assert today(datetime(2026, 7, 13, 9, 0)) is Weekday.MON
    assert today(datetime(2026, 7, 19, 9, 0)) is Weekday.SUN


def test_prev_next_wrap():
    assert Weekday.MON.prev is Weekday.SUN
    assert Weekday.SUN.next is Weekday.MON


# ---------------------------------------------------------------- parse

def test_parse_hhmm_24_00_is_end_of_day():
    assert parse_hhmm("24:00") == parse_hhmm("23:59")


# ---------------------------------------------------------------- compile

def test_compile_pads_with_fillers():
    m = model_with(days={Weekday.MON: DaySchedule(windows=[w("08:00", "11:00")])})
    compiled = compile_week(m)
    mon = compiled[Weekday.MON]
    assert len(mon) == 5
    assert mon[0].start_time == "08:00" and mon[0].enabled == 1
    assert mon[1] == FILLER_SLOT and mon[2] == FILLER_SLOT and mon[3] == FILLER_SLOT


def test_compile_window_enabled_flag_carried():
    m = model_with(days={Weekday.TUE: DaySchedule(windows=[w("08:00", "11:00", enabled=False)])})
    assert compile_week(m)[Weekday.TUE][0].enabled == 0


def test_compile_night_owl_fixed_midnight_cross_superset():
    # Friday night owl on, fixed 22:00-06:00 -> slot 5 enabled on Friday
    # (evening) AND Saturday (morning tail), not Sunday.
    m = model_with(
        days={Weekday.FRI: DaySchedule(night_owl=True)},
        night_owl=NightOwlSettings(mode="fixed", fixed_start="22:00", fixed_end="06:00"),
    )
    compiled = compile_week(m)
    assert compiled[Weekday.FRI][4].enabled == 1
    assert compiled[Weekday.SAT][4].enabled == 1  # morning tail superset
    assert compiled[Weekday.SUN][4].enabled == 0
    assert compiled[Weekday.FRI][4].start_time == "22:00"
    assert compiled[Weekday.FRI][4].end_time == "06:00"


def test_compile_night_owl_fixed_same_day_no_superset():
    # A same-day fixed window (e.g. 20:00-23:00) only follows its own day.
    m = model_with(
        days={Weekday.FRI: DaySchedule(night_owl=True)},
        night_owl=NightOwlSettings(mode="fixed", fixed_start="20:00", fixed_end="23:00"),
    )
    compiled = compile_week(m)
    assert compiled[Weekday.FRI][4].enabled == 1
    assert compiled[Weekday.SAT][4].enabled == 0


def test_compile_night_owl_outside_windows_full_day():
    m = model_with(days={Weekday.WED: DaySchedule(night_owl=True)})
    compiled = compile_week(m)
    slot5 = compiled[Weekday.WED][4]
    assert (slot5.start_time, slot5.end_time, slot5.enabled) == ("00:00", "23:59", 1)
    assert compiled[Weekday.THU][4].enabled == 1  # morning tail superset
    assert compiled[Weekday.FRI][4].enabled == 0


def test_compile_night_owl_master_off_disables_all():
    m = model_with(days={Weekday.WED: DaySchedule(night_owl=True)}, night_owl_enabled=False)
    compiled = compile_week(m)
    assert all(compiled[d][4].enabled == 0 for d in Weekday)


def test_compile_overlay_replaces_slot5_today_only():
    m = model_with(days={Weekday.MON: DaySchedule(night_owl=False)})
    compiled = compile_week(m, overlay=RunOverlay(work_sec=7, pause_sec=60), overlay_day=Weekday.MON)
    mon5 = compiled[Weekday.MON][4]
    assert (mon5.enabled, mon5.work_duration, mon5.pause_duration) == (1, "7", "60")
    assert (mon5.start_time, mon5.end_time) == ("00:00", "23:59")
    assert compiled[Weekday.TUE][4].enabled == 0


def test_slot_payload_wire_shape():
    payload = FILLER_SLOT.to_payload()
    assert set(payload) == {"startTime", "endTime", "enabled", "consistenceLevel", "workDuration", "pauseDuration"}


# ---------------------------------------------------------------- hash

def test_schedule_hash_stable_and_sensitive():
    m1 = model_with(days={Weekday.MON: DaySchedule(windows=[w("08:00", "11:00")])})
    m2 = model_with(days={Weekday.MON: DaySchedule(windows=[w("08:00", "11:00")])})
    m3 = model_with(days={Weekday.MON: DaySchedule(windows=[w("08:00", "11:30")])})
    assert schedule_hash(compile_week(m1)) == schedule_hash(compile_week(m2))
    assert schedule_hash(compile_week(m1)) != schedule_hash(compile_week(m3))
    assert day_hash(compile_week(m1)[Weekday.MON]) == day_hash(compile_week(m2)[Weekday.MON])


# ---------------------------------------------------------------- validation

def test_validate_ok():
    m = model_with(days={Weekday.MON: DaySchedule(windows=[w("08:00", "11:00"), w("12:00", "14:00")])})
    assert validate_schedule(m.schedule) == []


def test_validate_rejects_bad_times_and_ranges():
    sched = WeeklySchedule()
    sched.days[Weekday.MON] = DaySchedule(windows=[
        w("25:00", "11:00"),          # bad format
        w("10:00", "09:00"),          # end before start
        w("10:00", "11:00", work=2),  # work too small
        w("12:00", "13:00", level=9), # bad level
    ])
    errors = validate_schedule(sched)
    assert any("invalid time" in e for e in errors)
    assert any("end must be after start" in e for e in errors)
    assert any("work_sec out of range" in e for e in errors)
    assert any("level" in e for e in errors)


def test_validate_rejects_overlap_enabled_only():
    sched = WeeklySchedule()
    sched.days[Weekday.MON] = DaySchedule(windows=[w("08:00", "12:00"), w("11:00", "14:00")])
    assert any("overlap" in e for e in validate_schedule(sched))
    sched.days[Weekday.MON].windows[1] = w("11:00", "14:00", enabled=False)
    assert validate_schedule(sched) == []


# ---------------------------------------------------------------- active_window

def test_active_window_boundaries():
    m = model_with(days={Weekday.MON: DaySchedule(windows=[w("08:00", "11:00")])})
    assert active_window(m, datetime(2026, 7, 13, 8, 0)) is not None    # inclusive start
    assert active_window(m, datetime(2026, 7, 13, 10, 59)) is not None
    assert active_window(m, datetime(2026, 7, 13, 11, 0)) is None       # exclusive end
    assert active_window(m, datetime(2026, 7, 14, 9, 0)) is None        # other day


def test_active_window_skips_disabled():
    m = model_with(days={Weekday.MON: DaySchedule(windows=[w("08:00", "11:00", enabled=False)])})
    assert active_window(m, datetime(2026, 7, 13, 9, 0)) is None


# ---------------------------------------------------------------- night_owl_period

def _fixed_night_model(fri_pref=True, sat_pref=False):
    return model_with(
        days={Weekday.FRI: DaySchedule(night_owl=fri_pref), Weekday.SAT: DaySchedule(night_owl=sat_pref)},
        night_owl=NightOwlSettings(mode="fixed", fixed_start="22:00", fixed_end="06:00"),
    )


def test_night_owl_fixed_evening_belongs_to_today():
    m = _fixed_night_model(fri_pref=True)
    # 2026-07-17 is a Friday
    assert night_owl_period(m, datetime(2026, 7, 17, 23, 0)) is True
    assert night_owl_period(m, datetime(2026, 7, 17, 21, 0)) is False  # before window


def test_night_owl_fixed_morning_belongs_to_previous_evening():
    m = _fixed_night_model(fri_pref=True, sat_pref=False)
    # Saturday 00:30 belongs to FRIDAY's evening pref
    assert night_owl_period(m, datetime(2026, 7, 18, 0, 30)) is True
    # Sunday 00:30 belongs to SATURDAY's pref (False)
    assert night_owl_period(m, datetime(2026, 7, 19, 0, 30)) is False


def test_night_owl_master_off():
    m = _fixed_night_model(fri_pref=True)
    m.night_owl_enabled = False
    assert night_owl_period(m, datetime(2026, 7, 17, 23, 0)) is False


def test_night_owl_outside_windows_mode():
    m = model_with(days={
        Weekday.MON: DaySchedule(windows=[w("08:00", "11:00"), w("13:00", "18:00")], night_owl=True),
        Weekday.TUE: DaySchedule(windows=[w("08:00", "11:00")], night_owl=False),
        Weekday.SUN: DaySchedule(night_owl=True),
    })
    # Monday 19:00 = after last window, MON pref True -> night owl
    assert night_owl_period(m, datetime(2026, 7, 13, 19, 0)) is True
    # Monday 12:00 = mid-day gap between windows -> NOT night owl
    assert night_owl_period(m, datetime(2026, 7, 13, 12, 0)) is False
    # Monday 09:00 = inside a window -> not night owl
    assert night_owl_period(m, datetime(2026, 7, 13, 9, 0)) is False
    # Tuesday 06:00 = before first window; owning evening is MONDAY (True)
    assert night_owl_period(m, datetime(2026, 7, 14, 6, 0)) is True
    # Tuesday 20:00 = after last window; owning evening is TUESDAY (False)
    assert night_owl_period(m, datetime(2026, 7, 14, 20, 0)) is False
    # Monday 06:00 = before first window; owning evening is SUNDAY (True)
    assert night_owl_period(m, datetime(2026, 7, 13, 6, 0)) is True


def test_night_owl_outside_windows_empty_day_noon_split():
    m = model_with(days={
        Weekday.SAT: DaySchedule(night_owl=True),
        Weekday.SUN: DaySchedule(night_owl=False),
    })
    # Sunday has no windows: 09:00 belongs to SATURDAY's evening (True)
    assert night_owl_period(m, datetime(2026, 7, 19, 9, 0)) is True
    # Sunday 15:00 belongs to SUNDAY's evening (False)
    assert night_owl_period(m, datetime(2026, 7, 19, 15, 0)) is False


# ---------------------------------------------------------------- serialization

def test_device_model_roundtrip():
    m = model_with(
        days={
            Weekday.MON: DaySchedule(windows=[w("08:00", "11:00", level=2)], night_owl=True),
            Weekday.FRI: DaySchedule(windows=[w("06:30", "07:45", work=15, pause=600, enabled=False)]),
        },
        night_owl=NightOwlSettings(mode="fixed", fixed_start="21:30", fixed_end="05:00", linger_minutes=20),
        schedule_enabled=False,
    )
    m.schedule.version = 7
    m.schedule.updated_at = "2026-07-15T00:00:00+00:00"
    restored = DeviceModel.from_dict(m.to_dict())
    assert restored.to_dict() == m.to_dict()
    assert restored.schedule.version == 7
    assert restored.schedule.days[Weekday.MON].windows[0].level == 2
    assert restored.schedule_enabled is False
    assert restored.night_owl.linger_minutes == 20
    # JSON round-trip safety (Store serializes to JSON)
    import json
    assert DeviceModel.from_dict(json.loads(json.dumps(m.to_dict()))).to_dict() == m.to_dict()
