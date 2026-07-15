/**
 * <aroma-link-schedule-card-editor> — GUI config editor (lazy-loaded).
 */
import { LitElement, html, css } from "./vendor/lit-all.min.js";
import { listDevices } from "./al-api.js";

class AromaLinkScheduleCardEditor extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
    _devices: { state: true },
  };

  constructor() {
    super();
    this._config = {};
    this._devices = null;
  }

  setConfig(config) {
    this._config = { ...config };
  }

  updated() {
    if (this._devices === null && this.hass) {
      this._devices = [];
      listDevices(this.hass)
        .then((devices) => {
          this._devices = devices;
        })
        .catch(() => {
          this._devices = [];
        });
    }
  }

  _emit(config) {
    this._config = config;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }

  _toggleDevice(deviceId) {
    const all = (this._devices || []).map((d) => d.device_id);
    let selected = this._config.devices ? [...this._config.devices] : all;
    if (selected.includes(deviceId)) selected = selected.filter((d) => d !== deviceId);
    else selected.push(deviceId);
    const config = { ...this._config };
    if (selected.length === all.length) delete config.devices;
    else config.devices = selected;
    this._emit(config);
  }

  _toggleFlag(key) {
    const config = { ...this._config };
    const current = config[key] !== false;
    if (key === "compact") {
      if (config.compact) delete config.compact;
      else config.compact = true;
    } else if (current) {
      config[key] = false;
    } else {
      delete config[key];
    }
    this._emit(config);
  }

  _flag(key, label) {
    const value = key === "compact" ? !!this._config.compact : this._config[key] !== false;
    return html`
      <label class="row">
        <input type="checkbox" .checked=${value} @change=${() => this._toggleFlag(key)} />
        <span>${label}</span>
      </label>
    `;
  }

  render() {
    const selected = this._config.devices;
    return html`
      <div class="editor">
        <div class="group">
          <div class="head">Devices (all when none selected)</div>
          ${(this._devices || []).map(
            (device) => html`
              <label class="row">
                <input
                  type="checkbox"
                  .checked=${!selected || selected.includes(device.device_id)}
                  @change=${() => this._toggleDevice(device.device_id)}
                />
                <span>${device.name}</span>
              </label>
            `
          )}
        </div>
        <div class="group">
          <div class="head">Sections</div>
          ${this._flag("show_controls", "Controls (power / fan / timed run)")}
          ${this._flag("show_schedule", "Weekly schedule")}
          ${this._flag("show_oil", "Oil panel")}
          ${this._flag("compact", "Compact (wall tablet)")}
        </div>
        <div class="group">
          <div class="head">Title override</div>
          <input
            class="text"
            type="text"
            .value=${this._config.title || ""}
            @change=${(e) => {
              const config = { ...this._config };
              if (e.target.value) config.title = e.target.value;
              else delete config.title;
              this._emit(config);
            }}
          />
        </div>
      </div>
    `;
  }

  static styles = css`
    .editor {
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 6px 0;
    }
    .head {
      font-weight: 600;
      font-size: 0.85em;
      color: var(--secondary-text-color);
      margin-bottom: 4px;
    }
    .row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 3px 0;
      cursor: pointer;
    }
    .text {
      font: inherit;
      color: var(--primary-text-color);
      background: var(--card-background-color);
      border: 1px solid var(--divider-color);
      border-radius: 6px;
      padding: 6px 8px;
      width: 100%;
      box-sizing: border-box;
    }
  `;
}

if (!customElements.get("aroma-link-schedule-card-editor")) {
  customElements.define("aroma-link-schedule-card-editor", AromaLinkScheduleCardEditor);
}
