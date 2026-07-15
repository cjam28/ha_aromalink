/**
 * al-store.js — per-device server-state cache with live updates.
 *
 * One DeviceStore per device_id, shared across card instances in the tab via
 * a refcounted registry. Subscribes ONCE to the integration's bus event and
 * re-fetches on relevant changes; stale event echoes (version <= current) are
 * ignored. Saves are optimistic: the pre-save schedule is kept for Undo.
 */
import * as api from "./al-api.js";

const registry = new Map(); // device_id -> { store, refCount }

export function acquireStore(hass, deviceId) {
  const key = String(deviceId);
  let entry = registry.get(key);
  if (!entry) {
    entry = { store: new DeviceStore(key), refCount: 0 };
    registry.set(key, entry);
  }
  entry.refCount += 1;
  entry.store.connect(hass);
  return entry.store;
}

export function releaseStore(deviceId) {
  const key = String(deviceId);
  const entry = registry.get(key);
  if (!entry) return;
  entry.refCount -= 1;
  if (entry.refCount <= 0) {
    entry.store.disconnect();
    registry.delete(key);
  }
}

export class DeviceStore extends EventTarget {
  constructor(deviceId) {
    super();
    this.deviceId = deviceId;
    this.hass = null;
    this.version = -1;
    this.schedule = null; // {version, updated_at, days}
    this.nightOwl = null; // settings object
    this.flags = null;
    this.sync = null;
    this.status = null; // get_status payload
    this.undoSnapshot = null; // {schedule, nightOwl}
    this.error = null;
    this._unsubPromise = null;
    this._loading = false;
  }

  connect(hass) {
    this.hass = hass;
    if (!this._unsubPromise) {
      this._unsubPromise = api.subscribeUpdates(hass, (data) => this._onEvent(data));
      this.refresh();
      this.refreshStatus();
    }
  }

  disconnect() {
    if (this._unsubPromise) {
      this._unsubPromise.then((unsub) => unsub()).catch(() => {});
      this._unsubPromise = null;
    }
  }

  setHass(hass) {
    this.hass = hass;
  }

  _emit() {
    this.dispatchEvent(new CustomEvent("change"));
  }

  _onEvent(data) {
    if (String(data.device_id) !== this.deviceId) return;
    const change = data.change;
    if (change === "schedule" || change === "night_owl" || change === "flags") {
      if (typeof data.version === "number" && data.version <= this.version) return;
      this.refresh();
    } else if (change === "sync") {
      this.refresh(); // sync state lives in the schedule payload
      this.refreshStatus();
    } else if (change === "timed_run" || change === "oil" || change === "gating") {
      this.refreshStatus();
    }
  }

  async refresh() {
    if (!this.hass || this._loading) return;
    this._loading = true;
    try {
      const result = await api.getSchedule(this.hass, this.deviceId);
      this.schedule = result.schedule;
      this.nightOwl = result.night_owl;
      this.flags = result.flags;
      this.sync = result.sync;
      this.version = result.schedule?.version ?? -1;
      this.error = null;
    } catch (err) {
      this.error = err?.message || String(err);
    } finally {
      this._loading = false;
      this._emit();
    }
  }

  async refreshStatus() {
    if (!this.hass) return;
    try {
      this.status = await api.getStatus(this.hass, this.deviceId);
    } catch (err) {
      this.status = null;
    }
    this._emit();
  }

  /**
   * Save a schedule (and optional night-owl settings). Optimistic: local
   * state adopts the result atomically; the previous model is kept for undo.
   */
  async save(schedule, nightOwl = undefined) {
    const previous = {
      schedule: this.schedule,
      nightOwl: this.nightOwl,
    };
    const result = await api.saveSchedule(this.hass, this.deviceId, schedule, {
      nightOwl,
      baseVersion: this.version >= 0 ? this.version : undefined,
    });
    this.undoSnapshot = previous;
    this.version = result.version;
    if (this.schedule) {
      this.schedule = { ...schedule, version: result.version };
      if (nightOwl !== undefined) this.nightOwl = { ...this.nightOwl, ...nightOwl };
    }
    this.sync = result.sync || this.sync;
    this._emit();
    // Pull the canonical echo (validation may normalize values).
    this.refresh();
    return result;
  }

  async undo() {
    const snapshot = this.undoSnapshot;
    if (!snapshot || !snapshot.schedule) return false;
    this.undoSnapshot = null;
    await this.save(snapshot.schedule, snapshot.nightOwl);
    return true;
  }

  async handleConflict() {
    // version_conflict: reload and let the UI tell the user.
    await this.refresh();
  }
}
