/**
 * al-api.js — the ONLY module that knows backend names and payload shapes.
 *
 * Days are canonical 0=Monday..6=Sunday everywhere in the card (matching the
 * backend). Convert from JS Date.getDay() (0=Sunday) with `todayCanonical`.
 */

export const EVENT_UPDATED = "aroma_link_integration_updated";

export function todayCanonical(date = new Date()) {
  return (date.getDay() + 6) % 7;
}

export async function getSchedule(hass, deviceId) {
  return hass.callWS({ type: "aroma_link/get_schedule", device_id: String(deviceId) });
}

export async function saveSchedule(hass, deviceId, schedule, { nightOwl, baseVersion } = {}) {
  const msg = {
    type: "aroma_link/save_schedule",
    device_id: String(deviceId),
    schedule,
  };
  if (nightOwl !== undefined) msg.night_owl = nightOwl;
  if (baseVersion !== undefined) msg.base_version = baseVersion;
  return hass.callWS(msg);
}

export async function setNightOwlDays(hass, deviceId, days, enabled) {
  return hass.callWS({
    type: "aroma_link/set_night_owl_days",
    device_id: String(deviceId),
    days,
    enabled,
  });
}

export async function setFlags(hass, deviceId, flags) {
  return hass.callWS({ type: "aroma_link/set_flags", device_id: String(deviceId), ...flags });
}

export async function getStatus(hass, deviceId) {
  return hass.callWS({ type: "aroma_link/get_status", device_id: String(deviceId) });
}

export async function startTimedRun(hass, deviceId, durationMinutes, workSec, pauseSec) {
  const msg = {
    type: "aroma_link/timed_run_start",
    device_id: String(deviceId),
    duration_minutes: durationMinutes,
  };
  if (workSec) msg.work_sec = workSec;
  if (pauseSec) msg.pause_sec = pauseSec;
  return hass.callWS(msg);
}

export async function cancelTimedRun(hass, deviceId) {
  return hass.callWS({ type: "aroma_link/timed_run_cancel", device_id: String(deviceId) });
}

export async function syncNow(hass, deviceId) {
  return hass.callWS({ type: "aroma_link/sync_now", device_id: String(deviceId) });
}

export async function listDevices(hass) {
  const result = await hass.callWS({ type: "aroma_link/list_devices" });
  return result.devices || [];
}

export function subscribeUpdates(hass, callback) {
  // Returns a promise resolving to an unsubscribe function.
  return hass.connection.subscribeEvents((event) => callback(event.data || {}), EVENT_UPDATED);
}

export async function callEntityService(hass, domain, service, entityId, data = {}) {
  return hass.callService(domain, service, { entity_id: entityId, ...data });
}

export async function oilService(hass, service, deviceId, data = {}) {
  return hass.callService("aroma_link_integration", service, {
    device_id: String(deviceId),
    ...data,
  });
}
