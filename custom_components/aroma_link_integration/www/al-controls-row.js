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

  _gatePills() {
    const g = this.status?.gating;
    if (!g || !g.decision) return nothing;
    if (g.decision === "window") {
      const pills = [];
      if (g.holding) {
        pills.push(html`
          <span class="pill hold">
            <ha-icon icon="mdi:timer-sand"></ha-icon>
            Off-delay — holding power
          </span>
        `);
      }
      if (g.hvac_configured) {
        const action = g.hvac_action || (g.hvac ? "circulating" : "idle");
        pills.push(html`
          <span class="pill ${g.hvac ? "ok" : "hold"}">
            <ha-icon icon=${g.hvac ? "mdi:fan" : "mdi:fan-off"}></ha-icon>
            HVAC ${action} — ${g.hvac ? "diffusing" : "paused"}
          </span>
        `);
      }
      if (g.occupancy_configured) {
        pills.push(html`
          <span class="pill ${g.occupancy ? "ok" : "hold"}">
            <ha-icon icon=${g.occupancy ? "mdi:account-check" : "mdi:account-off"}></ha-icon>
            ${g.occupancy ? "Occupied" : "Empty"}
          </span>
        `);
      }
      return pills.length ? html`<div class="gates">${pills}</div>` : nothing;
    }
    if (g.decision === "night_owl") {
      return html`
        <div class="gates">
          <span class="pill ${g.motion ? "ok" : "hold"}">
            <ha-icon icon="mdi:owl"></ha-icon>
            Night Owl — ${g.motion ? "motion seen, diffusing" : "waiting for motion"}
          </span>
        </div>
      `;
    }
    if (g.decision === "outside") {
      return html`
        <div class="gates">
          <span class="pill muted">
            <ha-icon icon="mdi:clock-outline"></ha-icon>
            Outside schedule
          </span>
        </div>
      `;
    }
    if (g.decision === "hands_off") {
      return html`
        <div class="gates">
          <span class="pill muted">
            <ha-icon icon="mdi:hand-back-right-outline"></ha-icon>
            Schedule off — manual control
          </span>
        </div>
      `;
    }
    return nothing; // timed_run: the countdown already shows it
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
          <span class="ico"><ha-icon icon="mdi:power"></ha-icon></span>
          <span class="lbl">${powerOn ? "On" : "Off"}</span>
        </button>
        <button class="ctl ${fanOn ? "on" : ""}" @click=${this._toggleFan} title="Fan">
          <span class="ico"><ha-icon icon="mdi:fan"></ha-icon></span>
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
      ${this._gatePills()}
    `;
  }

  static styles = css`
    :host {
      -webkit-tap-highlight-color: transparent;
    }
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
      display: flex;
      --mdc-icon-size: 20px;
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
    .gates {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 6px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 0.75em;
      padding: 3px 10px;
      border-radius: 999px;
      border: 1px solid var(--divider-color);
      color: var(--secondary-text-color);
      --mdc-icon-size: 14px;
    }
    .pill ha-icon {
      display: flex;
    }
    .pill.ok {
      color: var(--primary-text-color);
      background: rgba(var(--rgb-primary-color, 33, 150, 243), 0.12);
      border-color: rgba(var(--rgb-primary-color, 33, 150, 243), 0.5);
    }
    .pill.hold {
      color: var(--primary-text-color);
      background: rgba(255, 152, 0, 0.14);
      border-color: rgba(255, 152, 0, 0.6);
    }
    button:active {
      filter: brightness(0.92);
    }
    button:focus-visible,
    input:focus-visible {
      outline: 2px solid var(--primary-color);
      outline-offset: 1px;
    }
    @media (pointer: coarse) {
      .ctl {
        min-height: 44px;
      }
      .btn {
        min-height: 40px;
        padding: 8px 12px;
      }
    }
  `;
}

if (!customElements.get("al-controls-row")) {
  customElements.define("al-controls-row", AlControlsRow);
}
