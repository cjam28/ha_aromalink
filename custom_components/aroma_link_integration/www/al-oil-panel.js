/**
 * <al-oil-panel> — oil level + calibration workflow.
 *
 * Reads the oil summary from get_status; drives the flow through the
 * oil_refill / oil_calibrate services. Input drafts are local state.
 */
import { LitElement, html, css, nothing } from "./vendor/lit-all.min.js";
import { live } from "./vendor/lit-all.min.js";
import * as api from "./al-api.js";
import { recalcManualOil } from "./al-model.js";

class AlOilPanel extends LitElement {
  static properties = {
    hass: { attribute: false },
    deviceId: { type: String },
    oil: { attribute: false }, // status.oil payload
    _open: { state: true },
    _fill: { state: true }, // refill draft {volume, date}
    _measured: { state: true },
    _manual: { state: true }, // {start, end, hours, rate}
    _busy: { state: true },
  };

  constructor() {
    super();
    this.hass = null;
    this.deviceId = "";
    this.oil = null;
    this._open = false;
    this._fill = { volume: "", date: "" };
    this._measured = "";
    this._manual = { start: "", end: "", hours: "", rate: "" };
    this._busy = false;
  }

  async _call(service, data) {
    this._busy = true;
    try {
      await api.oilService(this.hass, service, this.deviceId, data);
    } finally {
      this._busy = false;
    }
  }

  _refill() {
    const data = { keep_calibration: true };
    if (this._fill.volume) data.fill_volume = Number(this._fill.volume);
    if (this._fill.date) data.fill_date = this._fill.date;
    return this._call("oil_refill", data);
  }

  _calibrate(action, extra = {}) {
    return this._call("oil_calibrate", { action, ...extra });
  }

  _manualField(label, key, step = "0.1") {
    return html`
      <label class="field">
        <span>${label}</span>
        <input
          type="number"
          min="0"
          step=${step}
          .value=${live(String(this._manual[key] ?? ""))}
          @change=${(e) => {
            const next = { ...this._manual, [key]: e.target.value };
            // Only recalc once every participating field holds a real value —
            // an empty End must not be read as "bottle is empty".
            const haveEnd = next.end !== "" && !Number.isNaN(Number(next.end));
            const nums = {
              start: Number(next.start) || 0,
              end: haveEnd ? Number(next.end) : 0,
              hours: Number(next.hours) || 0,
              rate: Number(next.rate) || 0,
            };
            if (key === "rate" ? nums.rate > 0 && nums.start > 0 && nums.hours > 0
                               : haveEnd && nums.start > 0 && nums.hours > 0) {
              const recalced = recalcManualOil(nums, key);
              this._manual = {
                start: next.start,
                end: key === "rate" ? String(recalced.end.toFixed(1)) : next.end,
                hours: next.hours,
                rate:
                  key !== "rate" && recalced.rate > 0
                    ? String(recalced.rate.toFixed(3))
                    : next.rate,
              };
            } else {
              this._manual = next;
            }
          }}
        />
      </label>
    `;
  }

  render() {
    const oil = this.oil || {};
    const pct = oil.level_pct;
    const state = oil.calibration_state || "Idle";
    const days = oil.schedule_days_remaining ?? oil.days_remaining;

    return html`
      <div class="summary" @click=${() => (this._open = !this._open)}>
        <span class="ico">🛢</span>
        <div class="bar"><div class="fill" style="width: ${Math.max(0, Math.min(100, pct ?? 0))}%"></div></div>
        <span class="pct">${pct != null ? `${Math.round(pct)}%` : "—"}</span>
        ${days != null ? html`<span class="days">~${Math.round(days)}d left</span>` : nothing}
        <span class="chev">${this._open ? "▾" : "▸"}</span>
      </div>
      ${this._open
        ? html`
            <div class="detail">
              <div class="staterow">
                Calibration: <b>${state}</b>
                ${oil.usage_rate_ml_per_hour
                  ? html`· ${Number(oil.usage_rate_ml_per_hour).toFixed(2)} ml/h`
                  : nothing}
                ${oil.remaining_ml != null
                  ? html`· ${Math.round(oil.remaining_ml)} ml left`
                  : nothing}
              </div>

              <div class="section">
                <div class="sect-head">Refill</div>
                <div class="rows">
                  <label class="field">
                    <span>Volume (ml)</span>
                    <input type="number" min="1" .value=${live(String(this._fill.volume))}
                      @change=${(e) => (this._fill = { ...this._fill, volume: e.target.value })} />
                  </label>
                  <label class="field">
                    <span>Date</span>
                    <input type="date" .value=${live(this._fill.date)}
                      @change=${(e) => (this._fill = { ...this._fill, date: e.target.value })} />
                  </label>
                  <button class="btn" ?disabled=${this._busy} @click=${this._refill}>
                    Refilled ✓
                  </button>
                </div>
              </div>

              <div class="section">
                <div class="sect-head">Measured calibration</div>
                <div class="rows">
                  ${state === "Running"
                    ? html`<button class="btn" ?disabled=${this._busy}
                        @click=${() => this._calibrate("end")}>Stop measuring</button>`
                    : html`<button class="btn" ?disabled=${this._busy}
                        @click=${() => this._calibrate("start")}>Start measuring</button>`}
                  <label class="field">
                    <span>Measured remaining (ml)</span>
                    <input type="number" min="0" .value=${live(String(this._measured))}
                      @change=${(e) => (this._measured = e.target.value)} />
                  </label>
                  <button class="btn primary"
                    ?disabled=${this._busy || state !== "Ready to Finalize" || this._measured === ""}
                    @click=${() =>
                      this._calibrate("finalize", { measured_remaining: Number(this._measured) })}>
                    Finalize
                  </button>
                </div>
                <div class="hint">
                  Start after a refill, let it run for days, stop, measure what's left, finalize.
                </div>
              </div>

              <div class="section">
                <div class="sect-head">Manual override</div>
                <div class="rows">
                  ${this._manualField("Start (ml)", "start", "1")}
                  ${this._manualField("End (ml)", "end", "1")}
                  ${this._manualField("Runtime (h)", "hours", "0.1")}
                  ${this._manualField("Rate (ml/h)", "rate", "0.01")}
                  <button class="btn"
                    ?disabled=${this._busy}
                    @click=${() =>
                      this._calibrate("manual", {
                        ...(this._manual.rate ? { manual_rate_ml_per_hour: Number(this._manual.rate) } : {}),
                        ...(this._manual.start ? { manual_start_volume: Number(this._manual.start) } : {}),
                        ...(this._manual.end !== "" ? { manual_end_volume: Number(this._manual.end) } : {}),
                        ...(this._manual.hours ? { manual_runtime_hours: Number(this._manual.hours) } : {}),
                      })}>
                    Apply
                  </button>
                </div>
              </div>
            </div>
          `
        : nothing}
    `;
  }

  static styles = css`
    .summary {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      padding: 4px 0;
    }
    .bar {
      flex: 1;
      height: 8px;
      border-radius: 4px;
      background: var(--divider-color);
      overflow: hidden;
    }
    .fill {
      height: 100%;
      border-radius: 4px;
      background: var(--success-color, #4caf50);
    }
    .pct {
      font-variant-numeric: tabular-nums;
      font-weight: 600;
    }
    .days,
    .chev {
      color: var(--secondary-text-color);
      font-size: 0.85em;
    }
    .detail {
      border-top: 1px dashed var(--divider-color);
      margin-top: 6px;
      padding-top: 6px;
    }
    .staterow {
      font-size: 0.85em;
      color: var(--secondary-text-color);
      margin-bottom: 6px;
    }
    .section {
      margin: 8px 0;
    }
    .sect-head {
      font-size: 0.8em;
      font-weight: 600;
      color: var(--secondary-text-color);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
    }
    .rows {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      align-items: flex-end;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 2px;
      font-size: 0.78em;
      color: var(--secondary-text-color);
    }
    input {
      font: inherit;
      color: var(--primary-text-color);
      background: var(--card-background-color);
      border: 1px solid var(--divider-color);
      border-radius: 6px;
      padding: 4px 6px;
      width: 110px;
    }
    .btn {
      font: inherit;
      border: 1px solid var(--divider-color);
      border-radius: 8px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      padding: 5px 12px;
      cursor: pointer;
    }
    .btn.primary {
      background: var(--primary-color);
      border-color: var(--primary-color);
      color: var(--text-primary-color, #fff);
    }
    .btn[disabled] {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .hint {
      font-size: 0.75em;
      color: var(--secondary-text-color);
      margin-top: 4px;
    }
  `;
}

if (!customElements.get("al-oil-panel")) {
  customElements.define("al-oil-panel", AlOilPanel);
}
