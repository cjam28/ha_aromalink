/**
 * al-model.js — pure schedule helpers (no DOM, no hass). Unit-testable.
 */

export const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const DAY_NAMES_LONG = [
  "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
];
export const LEVEL_LETTERS = { 1: "A", 2: "B", 3: "C" };
export const MAX_WINDOWS = 4;

export const DEFAULT_WINDOW = {
  start: "08:00",
  end: "20:00",
  work_sec: 10,
  pause_sec: 300,
  level: 1,
  enabled: true,
};

export function timeToMinutes(hhmm) {
  const [h, m] = String(hhmm).split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  if (h === 24 && m === 0) return 23 * 60 + 59;
  return h * 60 + m;
}

export function isValidTime(hhmm) {
  const mins = timeToMinutes(hhmm);
  return mins !== null && mins >= 0 && mins < 24 * 60;
}

/** Deep-clone a schedule payload (plain JSON data). */
export function cloneSchedule(schedule) {
  return JSON.parse(JSON.stringify(schedule));
}

export function getDay(schedule, day) {
  return (schedule.days && schedule.days[String(day)]) || { windows: [], night_owl: false };
}

export function getWindow(schedule, day, index) {
  return getDay(schedule, day).windows[index] || null;
}

/**
 * Return conflicts a candidate window would create on a day.
 * Only enabled windows are checked; comparisons use strict interval overlap.
 */
export function findOverlaps(schedule, day, windowIndex, candidate) {
  if (!candidate.enabled) return [];
  const start = timeToMinutes(candidate.start);
  const end = timeToMinutes(candidate.end);
  if (start === null || end === null || end <= start) return [];
  const conflicts = [];
  getDay(schedule, day).windows.forEach((existing, idx) => {
    if (idx === windowIndex || !existing.enabled) return;
    const eStart = timeToMinutes(existing.start);
    const eEnd = timeToMinutes(existing.end);
    if (eStart === null || eEnd === null) return;
    if (start < eEnd && eStart < end) conflicts.push({ day, index: idx, window: existing });
  });
  return conflicts;
}

/** Validate one window's fields; returns error strings. */
export function validateWindow(w) {
  const errors = [];
  if (!isValidTime(w.start) || !isValidTime(w.end)) errors.push("Times must be HH:MM");
  else if (timeToMinutes(w.end) <= timeToMinutes(w.start)) {
    errors.push("End must be after start");
  }
  if (!(w.work_sec >= 5 && w.work_sec <= 900)) errors.push("Work 5–900s");
  if (!(w.pause_sec >= 5 && w.pause_sec <= 900)) errors.push("Pause 5–900s");
  return errors;
}

/**
 * Apply an edited window to a set of (day, index) targets, returning a new
 * schedule. Missing window slots are padded with disabled defaults.
 */
export function applyWindowEdit(schedule, targets, edited) {
  const next = cloneSchedule(schedule);
  for (const { day, index } of targets) {
    const dayKey = String(day);
    if (!next.days[dayKey]) next.days[dayKey] = { windows: [], night_owl: false };
    const windows = next.days[dayKey].windows;
    while (windows.length <= index) {
      windows.push({ ...DEFAULT_WINDOW, enabled: false });
    }
    windows[index] = { ...edited };
  }
  return next;
}

/** Human summary for a grid cell. Disabled state is shown structurally
 * (dashed cell border), not in the label. */
export function windowLabel(w) {
  if (!w) return "—";
  return `${w.start}–${w.end}`;
}

export function formatCountdown(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

/** Map the backend gating snapshot to a human "why" line. */
export function gateReason(status) {
  if (!status) return "";
  const g = status.gating || {};
  if (!status.available) return "Device unreachable";
  switch (g.decision) {
    case "timed_run":
      return "Timed run active";
    case "hands_off":
      return "Automation off — manual control";
    case "window": {
      if (g.hvac === false) return "In window · waiting for HVAC";
      if (g.occupancy === false) return "In window · nobody home";
      return status.power ? "Diffusing · scheduled window" : "Scheduled window";
    }
    case "night_owl":
      return g.motion
        ? "Night Owl · motion detected"
        : "Night Owl armed · waiting for motion";
    case "outside":
      return "Outside schedule windows";
    default:
      return "";
  }
}

/** Oil manual-calibration math (ported from the v2 card). */
export function recalcManualOil(fields, changedField) {
  const next = { ...fields };
  const { start, end, hours, rate } = next;
  if (changedField === "rate" && rate > 0 && start > 0 && hours > 0) {
    next.end = Math.max(0, start - rate * hours);
  } else if (start > 0 && end >= 0 && hours > 0 && start > end) {
    next.rate = (start - end) / hours;
  }
  return next;
}
