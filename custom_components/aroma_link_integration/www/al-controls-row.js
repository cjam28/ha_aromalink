/**
 * <al-controls-row> — power / fan / defaults / timed run.
 *
 * The countdown derives from the backend-persisted end time, so it survives
 * browser reloads and HA restarts. The 1s ticker renders only this component.
 */
import { LitElement, html, css, nothing } from "./vendor/lit-all.min.js";
import { live } from "./vendor/lit-all.min.js";
import * as api from "./al-api.js";
import { formatCountdown } from "./al-model.js";

class AlControlsRow extends LitElement {
  static properties = {
    hass: { attribute: false },
    deviceId: { type: String },
    entities: { attribute: false }, // {power, fan, work_number, pause_number}
    status: { attribute: false }, // get_status payload
    _hours: { state: true },
    _now: { state: true },
  };

  constructor() {
    super();
    this.hass = null;
    this.deviceId = "";
    this.entities = {};
    this.status = null;
    this._hours = 6;
    this._now = Date.now();
    this._ticker = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._ticker = setInterval(() => {
      if (this._runActive) this._now = Date.now();
    }, 1000);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._ticker);
    this._ticker = null;
  }

  get _runActive() {
    const run = this.status?.timed_run;
    if (!run || !run.ends_at) return false;
    return new Date(run.ends_at).getTime() > this._now;
  }

  _entityState(key) {
    const entityId = this.entities?.[key];
    return entityId ? this.hass?.states?.[entityId] : undefined;
  }

  async _togglePower() {
    const entityId = this.entities?.power;
    if (!entityId) return;
    const on = this._entityState("power")?.state === "on";
    await api.callEntityService(this.hass, "switch", on ? "turn_off" : "turn_on", entityId);
  }

  async _toggleFan() {
    const entityId = this.entities?.fan;
    if (!entityId) return;
    const on = this._entityState("fan")?.state === "on";
    await api.callEntityService(this.hass, "switch", on ? "turn_off" : "turn_on", entityId);
  }

  async _startRun() {
    const minutes = Math.round(Number(this._hours) * 60);
    if (!minutes || minutes <= 0) return;
    await api.startTimedRun(this.hass, this.deviceId, minutes);
  }

  async _cancelRun() {
    await api.cancelTimedRun(this.hass, this.deviceId);
  }

  render() {
    const power = this._entityState("power");
    const fan = this._entityState("fan");
    const powerOn = power?.state === "on";
    const fanOn = fan?.state === "on";
    const run = this.status?.timed_run;
    const remaining = run?.ends_at
      ? (new Date(run.ends_at).getTime() - this._now) / 1000
      : 0;

    return html`
      <div class="row">
        <button class="ctl ${powerOn ? "on" : ""}" @click=${this._togglePower} title="Power">
          <span class="ico">⏻</span>
          <span class="lbl">${powerOn ? "On" : "Off"}</span>
        </button>
        <button class="ctl ${fanOn ? "on" : ""}" @click=${this._toggleFan} title="Fan">
          <span class="ico">🌀</span>
          <span class="lbl">Fan</span>
        </button>
        ${this._runActive
          ? html`
              <div class="run active">
                <span class="countdown">⏱ ${formatCountdown(remaining)}</span>
                <button class="btn" @click=${this._cancelRun}>Cancel</button>
              </div>
            `
          : html`
              <div class="run">
                <span class="run-label">⏱ Timed</span>
                <input
                  type="number"
                  min="0.5"
                  max="24"
                  step="0.5"
                  .value=${live(String(this._hours))}
                  @change=${(e) => {
                    this._hours = Number(e.target.value);
                  }}
                />
                <span class="unit">h</span>
                <button class="btn primary" @click=${this._startRun}>▶</button>
              </div>
            `}
      </div>
    `;
  }

  static styles = css`
    .row {
      display: flex;
      gap: 8px;
      align-items: stretch;
      flex-wrap: wrap;
    }
    .ctl {
      font: inherit;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      padding: 8px 14px;
      cursor: pointer;
      min-width: 58px;
    }
    .ctl.on {
      background: rgba(var(--rgb-primary-color, 33, 150, 243), 0.15);
      border-color: var(--primary-color);
    }
    .ctl .ico {
      font-size: 1.15em;
    }
    .ctl .lbl {
      font-size: 0.72em;
      color: var(--secondary-text-color);
    }
    .run {
      display: flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      padding: 4px 10px;
      margin-left: auto;
    }
    .run.active {
      border-color: var(--primary-color);
    }
    .run-label,
    .unit {
      font-size: 0.8em;
      color: var(--secondary-text-color);
    }
    .countdown {
      font-variant-numeric: tabular-nums;
      font-weight: 600;
    }
    input[type="number"] {
      font: inherit;
      width: 58px;
      color: var(--primary-text-color);
      background: var(--card-background-color);
      border: 1px solid var(--divider-color);
      border-radius: 6px;
      padding: 3px 5px;
    }
    .btn {
      font: inherit;
      border: 1px solid var(--divider-color);
      border-radius: 8px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      padding: 4px 10px;
      cursor: pointer;
    }
    .btn.primary {
      background: var(--primary-color);
      border-color: var(--primary-color);
      color: var(--text-primary-color, #fff);
    }
  `;
}

if (!customElements.get("al-controls-row")) {
  customElements.define("al-controls-row", AlControlsRow);
}
