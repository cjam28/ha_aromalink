/**
 * Aroma-Link Schedule Card v3.0.0 — ground-up Lit rewrite.
 *
 * Same custom element name and resource URL as v2.x, so existing dashboards
 * keep working with zero edits. Talks to the integration's websocket API
 * (instant HA-side saves; the backend reconciler owns the slow device push)
 * and subscribes to the single `aroma_link_integration_updated` bus event —
 * the card is always live, with no polling and no full-DOM rebuilds.
 *
 * Modules: al-api (backend adapter) · al-store (server-state cache, undo) ·
 * al-model (pure schedule math) · al-schedule-grid · al-editor-sheet ·
 * al-controls-row · al-oil-panel. Lit is vendored (no CDN dependency).
 */
import { LitElement, html, css, nothing } from "./vendor/lit-all.min.js";
import * as api from "./al-api.js";
import { acquireStore, releaseStore } from "./al-store.js";
import { applyWindowEdit, cloneSchedule, gateReason, removeWindows } from "./al-model.js";
import "./al-schedule-grid.js";
import "./al-editor-sheet.js";
import "./al-controls-row.js";
import "./al-oil-panel.js";

const CONFIG_KEYS = new Set([
  "type",
  "devices",
  "show_controls",
  "show_schedule",
  "show_oil",
  "compact",
  "title",
  "grid_options",
  "view_layout",
  "layout_options",
]);

const NARROW_WIDTH = 560;
const UNDO_TIMEOUT_MS = 8000;
const COPY_ARM_TIMEOUT_MS = 3000;

class AromaLinkScheduleCard extends LitElement {
  static properties = {
    _config: { state: true },
    _devices: { attribute: false, state: true }, // [{device_id, name, entities}]
    _narrow: { state: true },
    _editTargets: { state: true }, // device_id -> {day,index}|"night_owl"|null
    _saving: { state: true }, // device_id -> bool (save in flight)
    _copyArm: { state: true }, // {target, source} | null (two-tap confirm)
    _toast: { state: true }, // {text, undoDeviceId} | null
  };

  constructor() {
    super();
    this._config = {};
    this._devices = null;
    this._stores = new Map(); // device_id -> DeviceStore
    this._storeListeners = new Map();
    this._narrow = false;
    this._editTargets = {};
    this._saving = {};
    this._copyArm = null;
    this._copyArmTimer = null;
    this._toast = null;
    this._toastTimer = null;
    this._resizeObserver = null;
    this._hass = null;
    this._loadingDevices = false;
  }

  // ------------------------------------------------------------- HA plumbing

  setConfig(config) {
    for (const key of Object.keys(config || {})) {
      if (!CONFIG_KEYS.has(key)) {
        // v2.x accepted arbitrary options (e.g. show_editor), so stale keys
        // survive on dashboards; rejecting them would break the zero-edit
        // upgrade promise. Ignore with a warning instead.
        console.warn(`aroma-link-schedule-card: ignoring unknown option "${key}"`);
      }
    }
    if (config.devices && !Array.isArray(config.devices)) {
      throw new Error("aroma-link-schedule-card: devices must be a list");
    }
    // HA reuses the element and calls setConfig again on edit — if the
    // devices filter changed, the device list must be re-derived.
    const devicesChanged =
      JSON.stringify(this._config.devices ?? null) !== JSON.stringify(config.devices ?? null);
    this._config = { ...config };
    if (devicesChanged) {
      this._detachStores();
      this._devices = null;
      if (this._hass) this._loadDevices();
    }
  }

  set hass(hass) {
    this._hass = hass;
    for (const store of this._stores.values()) store.setHass(hass);
    if (!this._devices && !this._loadingDevices) this._loadDevices();
    // Entity states feed the controls row; Lit re-renders subcomponents on
    // property identity change.
    this.requestUpdate();
  }

  get hass() {
    return this._hass;
  }

  static getStubConfig() {
    return {};
  }

  static async getConfigElement() {
    await import("./al-card-editor.js");
    return document.createElement("aroma-link-schedule-card-editor");
  }

  getCardSize() {
    return 3 + 4 * (this._devices?.length || 1);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  // ------------------------------------------------------------- lifecycle

  connectedCallback() {
    super.connectedCallback();
    // Seed narrow-ness before the ResizeObserver's first (async) callback so
    // phones don't flash the wide grid; fall back to the viewport width when
    // the card has not been laid out yet (its width can never exceed it).
    const width = this.clientWidth || window.innerWidth;
    this._narrow = width > 0 && width < NARROW_WIDTH;
    this._resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width || this.clientWidth;
      this._narrow = width > 0 && width < NARROW_WIDTH;
    });
    this._resizeObserver.observe(this);
    // Re-acquire stores if we were detached (dashboard edit / tab switch).
    if (this._devices) this._attachStores();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._resizeObserver?.disconnect();
    this._resizeObserver = null;
    this._detachStores();
    clearTimeout(this._toastTimer);
    clearTimeout(this._copyArmTimer);
  }

  async _loadDevices() {
    if (!this._hass) return;
    this._loadingDevices = true;
    try {
      let devices = await api.listDevices(this._hass);
      const filter = this._config.devices;
      if (filter && filter.length) {
        devices = devices.filter((d) => filter.includes(d.device_id));
      }
      this._devices = devices;
      this._attachStores();
    } catch (err) {
      this._devices = [];
    } finally {
      this._loadingDevices = false;
    }
  }

  _attachStores() {
    if (!this._devices || !this._hass) return;
    for (const device of this._devices) {
      if (this._stores.has(device.device_id)) continue;
      const store = acquireStore(this._hass, device.device_id);
      const listener = () => this.requestUpdate();
      store.addEventListener("change", listener);
      this._stores.set(device.device_id, store);
      this._storeListeners.set(device.device_id, listener);
    }
  }

  _detachStores() {
    for (const [deviceId, store] of this._stores) {
      const listener = this._storeListeners.get(deviceId);
      if (listener) store.removeEventListener("change", listener);
      releaseStore(deviceId);
    }
    this._stores.clear();
    this._storeListeners.clear();
  }

  // ------------------------------------------------------------- actions

  _showToast(text, undoDeviceId = null) {
    clearTimeout(this._toastTimer);
    this._toast = { text, undoDeviceId };
    this._toastTimer = setTimeout(() => {
      this._toast = null;
    }, UNDO_TIMEOUT_MS);
  }

  async _deleteWindow(deviceId, detail) {
    const store = this._stores.get(deviceId);
    if (!store || !store.schedule || this._saving[deviceId]) return;
    const next = removeWindows(store.schedule, detail.targets);
    this._saving = { ...this._saving, [deviceId]: true };
    try {
      await store.save(next);
      this._editTargets = { ...this._editTargets, [deviceId]: null };
      this._showToast("Window deleted · pushing to device…", deviceId);
    } catch (err) {
      if (err?.code === "version_conflict") {
        await store.handleConflict();
        this._showToast("Schedule changed elsewhere — reloaded");
      } else {
        this._showToast(`Delete failed: ${err?.message || err}`);
      }
    } finally {
      this._saving = { ...this._saving, [deviceId]: false };
    }
  }

  async _saveWindow(deviceId, detail) {
    const store = this._stores.get(deviceId);
    if (!store || !store.schedule || this._saving[deviceId]) return;
    const next = applyWindowEdit(store.schedule, detail.targets, detail.window);
    this._saving = { ...this._saving, [deviceId]: true };
    try {
      await store.save(next);
      this._editTargets = { ...this._editTargets, [deviceId]: null };
      this._showToast("Saved · pushing to device…", deviceId);
    } catch (err) {
      if (err?.code === "version_conflict") {
        await store.handleConflict();
        this._showToast("Schedule changed elsewhere — reloaded");
      } else {
        this._showToast(`Save failed: ${err?.message || err}`);
      }
    } finally {
      this._saving = { ...this._saving, [deviceId]: false };
    }
  }

  async _saveNightOwl(deviceId, detail) {
    const store = this._stores.get(deviceId);
    if (!store || !store.schedule || this._saving[deviceId]) return;
    const next = cloneSchedule(store.schedule);
    for (const [day, enabled] of Object.entries(detail.days)) {
      if (!next.days[day]) next.days[day] = { windows: [], night_owl: false };
      next.days[day].night_owl = enabled;
    }
    this._saving = { ...this._saving, [deviceId]: true };
    try {
      await store.save(next, detail.settings);
      this._editTargets = { ...this._editTargets, [deviceId]: null };
      this._showToast("Night Owl saved", deviceId);
    } catch (err) {
      this._showToast(`Save failed: ${err?.message || err}`);
    } finally {
      this._saving = { ...this._saving, [deviceId]: false };
    }
  }

  async _toggleNightOwlDay(deviceId, detail) {
    try {
      await api.setNightOwlDays(this._hass, deviceId, [detail.day], detail.enabled);
    } catch (err) {
      this._showToast(`Failed: ${err?.message || err}`);
    }
  }

  async _undo() {
    const deviceId = this._toast?.undoDeviceId;
    this._toast = null;
    if (!deviceId) return;
    const store = this._stores.get(deviceId);
    if (!store) return;
    try {
      if (await store.undo()) this._showToast("Undone");
    } catch (err) {
      if (err?.code === "version_conflict") {
        await store.handleConflict();
        this._showToast("Schedule changed elsewhere — reloaded");
      } else {
        this._showToast(`Undo failed: ${err?.message || err}`);
      }
    }
  }

  async _syncNow(deviceId) {
    try {
      await api.syncNow(this._hass, deviceId);
      this._showToast("Checking device sync…");
    } catch (err) {
      this._showToast(`Sync failed: ${err?.message || err}`);
    }
  }

  _copyChipTapped(targetDeviceId, sourceDeviceId) {
    // Two-tap arm: the first tap arms the chip ("Confirm copy?"), the second
    // tap within the window copies; the arm auto-reverts after 3 seconds.
    const armed =
      this._copyArm &&
      this._copyArm.target === targetDeviceId &&
      this._copyArm.source === sourceDeviceId;
    clearTimeout(this._copyArmTimer);
    if (armed) {
      this._copyArm = null;
      this._copyFrom(targetDeviceId, sourceDeviceId);
      return;
    }
    this._copyArm = { target: targetDeviceId, source: sourceDeviceId };
    this._copyArmTimer = setTimeout(() => {
      this._copyArm = null;
    }, COPY_ARM_TIMEOUT_MS);
  }

  async _copyFrom(targetDeviceId, sourceDeviceId) {
    const target = this._stores.get(targetDeviceId);
    const source = this._stores.get(sourceDeviceId);
    if (!target || !source || !source.schedule) return;
    try {
      await target.save(cloneSchedule(source.schedule), { ...source.nightOwl });
      this._showToast("Schedule copied", targetDeviceId);
    } catch (err) {
      this._showToast(`Copy failed: ${err?.message || err}`);
    }
  }

  // ------------------------------------------------------------- rendering

  _syncChip(store, deviceId) {
    if (!store.ready) {
      // Not loaded yet — a neutral chip, not a fake backend "pending".
      return html`<button class="chip sync" title="Loading">…</button>`;
    }
    const state = store.sync?.state || "pending";
    const label = { synced: "Synced", pending: "Pending", error: "Push failed" }[state];
    const icon = {
      synced: "mdi:check-circle-outline",
      pending: "mdi:sync",
      error: "mdi:alert-outline",
    }[state];
    return html`
      <button
        class="chip sync ${state}"
        title=${store.sync?.last_error || "Device schedule sync"}
        @click=${() => this._syncNow(deviceId)}
      >
        ${label} <ha-icon icon=${icon}></ha-icon>
      </button>
    `;
  }

  _statusLine(store) {
    const status = store.status;
    if (!status) return html`<span class="dim">Loading status…</span>`;
    const diffusing = status.power && status.work_status === 2;
    const dot = diffusing ? "●" : "○";
    const reason = gateReason(status) || (status.power ? "Powered on" : "Off");
    return html`<span class="dot ${diffusing ? "on" : ""}">${dot}</span> ${reason}`;
  }

  _renderDevice(device) {
    const store = this._stores.get(device.device_id);
    if (!store) return nothing;
    const showControls = this._config.show_controls !== false;
    const showSchedule = this._config.show_schedule !== false;
    const showOil = this._config.show_oil !== false && store.status?.oil;
    const editTarget = this._editTargets[device.device_id] || null;
    const gatingWindow = store.status?.gating?.window || null;

    return html`
      <div class="device">
        <div class="devhead">
          <span class="devname">${device.name}</span>
          <span class="headactions">
            ${(this._devices || [])
              .filter((other) => other.device_id !== device.device_id)
              .map((other) => {
                const armed =
                  this._copyArm &&
                  this._copyArm.target === device.device_id &&
                  this._copyArm.source === other.device_id;
                return html`
                  <button
                    class="chip ${armed ? "armed" : ""}"
                    title="Copy ${other.name}'s schedule to ${device.name}"
                    @click=${() => this._copyChipTapped(device.device_id, other.device_id)}
                  >
                    ${armed ? "Confirm copy?" : html`⧉ ${other.name}`}
                  </button>
                `;
              })}
            ${this._syncChip(store, device.device_id)}
          </span>
        </div>
        <div class="statusline">${this._statusLine(store)}</div>
        ${store.error ? html`<div class="error">⚠ ${store.error}</div>` : nothing}
        ${showControls
          ? html`
              <al-controls-row
                .hass=${this._hass}
                .deviceId=${device.device_id}
                .entities=${device.entities || {}}
                .status=${store.status}
              ></al-controls-row>
            `
          : nothing}
        ${showSchedule
          ? html`
              <al-schedule-grid
                .schedule=${store.schedule}
                .nightOwlEnabled=${store.flags?.night_owl_enabled !== false}
                .activeWindow=${gatingWindow}
                .narrow=${this._narrow}
                .compact=${!!this._config.compact}
                .editTarget=${editTarget}
                @cell-selected=${(e) => {
                  this._editTargets = {
                    ...this._editTargets,
                    [device.device_id]: e.detail,
                  };
                }}
                @night-owl-edit=${() => {
                  this._editTargets = {
                    ...this._editTargets,
                    [device.device_id]: "night_owl",
                  };
                }}
                @night-owl-toggle=${(e) => this._toggleNightOwlDay(device.device_id, e.detail)}
              ></al-schedule-grid>
              ${editTarget
                ? html`
                    <al-editor-sheet
                      .schedule=${store.schedule}
                      .nightOwl=${store.nightOwl}
                      .target=${editTarget}
                      .saving=${!!this._saving[device.device_id]}
                      @editor-save=${(e) => this._saveWindow(device.device_id, e.detail)}
                      @editor-delete=${(e) => this._deleteWindow(device.device_id, e.detail)}
                      @night-owl-save=${(e) => this._saveNightOwl(device.device_id, e.detail)}
                      @editor-cancel=${() => {
                        this._editTargets = {
                          ...this._editTargets,
                          [device.device_id]: null,
                        };
                      }}
                    ></al-editor-sheet>
                  `
                : nothing}
            `
          : nothing}
        ${showOil
          ? html`
              <al-oil-panel
                .hass=${this._hass}
                .deviceId=${device.device_id}
                .oil=${store.status?.oil}
              ></al-oil-panel>
            `
          : nothing}
      </div>
    `;
  }

  render() {
    if (this._devices === null) {
      return html`<ha-card><div class="pad dim">Looking for Aroma-Link diffusers…</div></ha-card>`;
    }
    if (!this._devices.length) {
      return html`<ha-card>
        <div class="pad">
          No Aroma-Link devices found. Is the integration set up
          ${this._config.devices ? " (check the card's devices list)?" : "?"}
        </div>
      </ha-card>`;
    }
    return html`
      <ha-card .header=${this._config.title || undefined}>
        <div class="pad ${this._config.compact ? "compact" : ""}">
          ${this._devices.map((device) => this._renderDevice(device))}
        </div>
        ${this._toast
          ? html`
              <div class="toast" role="status" aria-live="polite">
                <span>${this._toast.text}</span>
                ${this._toast.undoDeviceId
                  ? html`<button class="undo" @click=${this._undo}>Undo</button>`
                  : nothing}
              </div>
            `
          : nothing}
      </ha-card>
    `;
  }

  static styles = css`
    :host {
      -webkit-tap-highlight-color: transparent;
    }
    ha-card {
      position: relative;
      overflow: hidden;
    }
    .pad {
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .pad.compact {
      padding: 8px;
      gap: 8px;
    }
    .device {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .device + .device {
      border-top: 1px solid var(--divider-color);
      padding-top: 12px;
    }
    .devhead {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .devname {
      font-weight: 600;
      font-size: 1.05em;
    }
    .headactions {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .statusline {
      font-size: 0.88em;
      color: var(--secondary-text-color);
    }
    .dot {
      color: var(--secondary-text-color);
    }
    .dot.on {
      color: var(--success-color, #4caf50);
    }
    .chip {
      font: inherit;
      font-size: 0.75em;
      border-radius: 12px;
      padding: 3px 10px;
      border: 1px solid var(--divider-color);
      background: var(--card-background-color);
      color: var(--secondary-text-color);
      cursor: pointer;
      white-space: nowrap;
    }
    .chip ha-icon {
      --mdc-icon-size: 16px;
      vertical-align: -3px;
    }
    .chip.sync.synced {
      color: var(--success-color, #4caf50);
      border-color: var(--success-color, #4caf50);
    }
    .chip.sync.pending {
      color: var(--warning-color, #ff9800);
      border-color: var(--warning-color, #ff9800);
    }
    .chip.sync.error {
      color: var(--error-color, #f44336);
      border-color: var(--error-color, #f44336);
    }
    .error {
      color: var(--error-color);
      font-size: 0.85em;
    }
    .dim {
      color: var(--secondary-text-color);
    }
    .toast {
      position: absolute;
      left: 12px;
      right: 12px;
      bottom: 10px;
      background: var(--primary-color);
      color: var(--text-primary-color, #fff);
      border-radius: 8px;
      padding: 8px 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
    }
    .toast .undo {
      font: inherit;
      font-weight: 600;
      background: none;
      border: none;
      color: inherit;
      text-decoration: underline;
      cursor: pointer;
      padding: 6px 12px;
      margin: -6px -12px;
    }
    button:active {
      filter: brightness(0.92);
    }
    button:focus-visible {
      outline: 2px solid var(--primary-color);
      outline-offset: 1px;
    }
    @media (pointer: coarse) {
      .chip {
        min-height: 40px;
        padding: 8px 12px;
      }
    }
  `;
}

if (!customElements.get("aroma-link-schedule-card")) {
  customElements.define("aroma-link-schedule-card", AromaLinkScheduleCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "aroma-link-schedule-card")) {
  window.customCards.push({
    type: "aroma-link-schedule-card",
    name: "Aroma-Link Schedule Card",
    description:
      "Weekly schedule, Night Owl, timed runs, and oil tracking for Aroma-Link diffusers.",
    preview: true,
    documentationURL: "https://github.com/cjam28/homeassistant_aroma-link#readme",
  });
}
