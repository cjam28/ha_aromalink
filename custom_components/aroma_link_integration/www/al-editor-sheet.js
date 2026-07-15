/**
 * <al-editor-sheet> — inline window editor with apply-to chips.
 *
 * Two modes:
 *  - window editor (target = {day, index}): edits one window, optionally
 *    applying to multiple days/window slots in a single save.
 *  - Night Owl editor (target = "night_owl"): edits the night window
 *    settings + per-day allow flags.
 *
 * Emits:
 *   editor-save       {targets: [{day,index}], window}         (window mode)
 *   night-owl-save    {settings, days: {0..6: bool}}           (night owl mode)
 *   editor-cancel     {}
 *
 * Draft state is local — hass churn can never clobber typing.
 */
import { LitElement, html, css, nothing } from "./vendor/lit-all.min.js";
import { live } from "./vendor/lit-all.min.js";
import {
  DAY_NAMES,
  DEFAULT_WINDOW,
  MAX_WINDOWS,
  findOverlaps,
  getWindow,
  isValidTime,
  timeToMinutes,
  validateWindow,
  getDay,
} from "./al-model.js";

class AlEditorSheet extends LitElement {
  static properties = {
    schedule: { attribute: false },
    nightOwl: { attribute: false }, // settings object
    target: { attribute: false }, // {day, index} | "night_owl"
    saving: { type: Boolean }, // save in flight (owned by the card)
    _draft: { state: true },
    _days: { state: true }, // selected day chips (Set)
    _slots: { state: true }, // selected window slots (Set)
    _owlDays: { state: true },
  };

  constructor() {
    super();
    this.schedule = null;
    this.nightOwl = null;
    this.target = null;
    this.saving = false;
    this._draft = null;
    this._days = new Set();
    this._slots = new Set();
    this._owlDays = new Set();
  }

  willUpdate(changed) {
    if (changed.has("target") && this.target) {
      if (this.target === "night_owl") {
        this._draft = { ...(this.nightOwl || {}) };
        this._owlDays = new Set(
          [...Array(7).keys()].filter((d) => getDay(this.schedule, d).night_owl)
        );
      } else {
        const existing = getWindow(this.schedule, this.target.day, this.target.index);
        this._draft = existing ? { ...existing } : { ...DEFAULT_WINDOW };
        this._days = new Set([this.target.day]);
        this._slots = new Set([this.target.index]);
      }
    }
  }

  _set(field, value) {
    this._draft = { ...this._draft, [field]: value };
  }

  _toggle(set, value) {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  }

  get _targets() {
    const targets = [];
    for (const day of this._days) {
      for (const index of this._slots) targets.push({ day, index });
    }
    return targets;
  }

  get _problems() {
    if (!this._draft) return [];
    if (this.target === "night_owl") {
      const d = this._draft;
      const errors = [];
      if ((d.mode || "outside_windows") === "fixed") {
        // Mirror the input fallbacks so we validate what the user sees.
        const start = d.fixed_start || "22:00";
        const end = d.fixed_end || "06:00";
        if (!isValidTime(start) || !isValidTime(end)) errors.push("Times must be HH:MM");
        else if (timeToMinutes(start) === timeToMinutes(end)) {
          errors.push("From and To must differ");
        }
      }
      if (!(d.work_sec >= 5 && d.work_sec <= 900)) errors.push("Work 5–900s");
      if (!(d.pause_sec >= 5 && d.pause_sec <= 900)) errors.push("Pause 5–900s");
      if (!(d.linger_minutes >= 1 && d.linger_minutes <= 240)) errors.push("Linger 1–240min");
      return errors;
    }
    const errors = validateWindow(this._draft);
    if (errors.length) return errors;
    if (this._draft.enabled && this._slots.size > 1) {
      // The same window written to several slots on one day always
      // self-overlaps once enabled.
      return ["Enabled window can only target one window slot per day"];
    }
    const conflicts = [];
    for (const { day, index } of this._targets) {
      for (const c of findOverlaps(this.schedule, day, index, this._draft)) {
        // Conflict is void if the conflicting slot is itself being overwritten.
        if (!(this._days.has(day) && this._slots.has(c.index))) {
          conflicts.push(`${DAY_NAMES[day]}: overlaps W${c.index + 1} (${c.window.start}–${c.window.end})`);
        }
      }
    }
    return [...new Set(conflicts)];
  }

  _save() {
    if (this.saving || this._problems.length) return;
    if (this.target === "night_owl") {
      const days = {};
      for (let d = 0; d < 7; d++) days[d] = this._owlDays.has(d);
      this.dispatchEvent(
        new CustomEvent("night-owl-save", {
          detail: { settings: this._draft, days },
          bubbles: true,
          composed: true,
        })
      );
      return;
    }
    this.dispatchEvent(
      new CustomEvent("editor-save", {
        detail: { targets: this._targets, window: { ...this._draft } },
        bubbles: true,
        composed: true,
      })
    );
  }

  _cancel() {
    this.dispatchEvent(
      new CustomEvent("editor-cancel", { detail: {}, bubbles: true, composed: true })
    );
  }

  _numberField(label, field, min, max, step = 1) {
    return html`
      <label class="field">
        <span>${label}</span>
        <input
          type="number"
          min=${min}
          max=${max}
          step=${step}
          .value=${live(String(this._draft[field] ?? ""))}
          @change=${(e) => this._set(field, Number(e.target.value))}
        />
      </label>
    `;
  }

  _levelField() {
    return html`
      <label class="field">
        <span>Level</span>
        <select
          .value=${live(String(this._draft.level || 1))}
          @change=${(e) => this._set("level", Number(e.target.value))}
        >
          <option value="1">A (light)</option>
          <option value="2">B (medium)</option>
          <option value="3">C (strong)</option>
        </select>
      </label>
    `;
  }

  _renderWindowEditor() {
    const problems = this._problems;
    return html`
      <div class="head">Edit · Window ${this.target.index + 1} — ${DAY_NAMES[this.target.day]}</div>
      <div class="rows">
        <label class="field check">
          <input
            type="checkbox"
            .checked=${live(!!this._draft.enabled)}
            @change=${(e) => this._set("enabled", e.target.checked)}
          />
          <span>Enabled</span>
        </label>
        <label class="field">
          <span>Start</span>
          <input
            type="time"
            .value=${live(this._draft.start || "")}
            @change=${(e) => this._set("start", e.target.value)}
          />
        </label>
        <label class="field">
          <span>End</span>
          <input
            type="time"
            .value=${live(this._draft.end || "")}
            @change=${(e) => this._set("end", e.target.value)}
          />
        </label>
        ${this._numberField("Work (s)", "work_sec", 5, 900)}
        ${this._numberField("Pause (s)", "pause_sec", 5, 900, 5)}
        ${this._levelField()}
      </div>
      <div class="chips">
        <span class="chips-label">Days:</span>
        ${DAY_NAMES.map(
          (name, d) => html`
            <button
              class="chip ${this._days.has(d) ? "sel" : ""}"
              @click=${() => {
                this._days = this._toggle(this._days, d);
              }}
            >
              ${name[0]}
            </button>
          `
        )}
        <button class="chip all" @click=${() => {
          this._days = this._days.size === 7 ? new Set([this.target.day]) : new Set([...Array(7).keys()]);
        }}>All</button>
      </div>
      <div class="chips">
        <span class="chips-label">Windows:</span>
        ${[...Array(MAX_WINDOWS).keys()].map(
          (i) => html`
            <button
              class="chip ${this._slots.has(i) ? "sel" : ""}"
              @click=${() => {
                this._slots = this._toggle(this._slots, i);
              }}
            >
              ${i + 1}
            </button>
          `
        )}
      </div>
      ${problems.length
        ? html`<div class="problems">⚠ ${problems.join(" · ")}</div>`
        : nothing}
      <div class="actions">
        <button class="btn" @click=${this._cancel}>Cancel</button>
        <button
          class="btn primary"
          ?disabled=${this.saving || problems.length > 0 || this._targets.length === 0}
          @click=${this._save}
        >
          Save
        </button>
      </div>
    `;
  }

  _renderNightOwlEditor() {
    const problems = this._problems;
    return html`
      <div class="head">Night Owl 🦉 — motion-gated overnight diffusing</div>
      <div class="rows">
        <label class="field">
          <span>Mode</span>
          <select
            .value=${live(this._draft.mode || "outside_windows")}
            @change=${(e) => this._set("mode", e.target.value)}
          >
            <option value="outside_windows">Outside scheduled hours</option>
            <option value="fixed">Fixed night window</option>
          </select>
        </label>
        ${this._draft.mode === "fixed"
          ? html`
              <label class="field">
                <span>From</span>
                <input
                  type="time"
                  .value=${live(this._draft.fixed_start || "22:00")}
                  @change=${(e) => this._set("fixed_start", e.target.value)}
                />
              </label>
              <label class="field">
                <span>To</span>
                <input
                  type="time"
                  .value=${live(this._draft.fixed_end || "06:00")}
                  @change=${(e) => this._set("fixed_end", e.target.value)}
                />
              </label>
            `
          : nothing}
        ${this._numberField("Work (s)", "work_sec", 5, 900)}
        ${this._numberField("Pause (s)", "pause_sec", 5, 900, 5)}
        ${this._levelField()}
        ${this._numberField("Motion linger (min)", "linger_minutes", 1, 240)}
      </div>
      <div class="chips">
        <span class="chips-label">Nights:</span>
        ${DAY_NAMES.map(
          (name, d) => html`
            <button
              class="chip ${this._owlDays.has(d) ? "sel" : ""}"
              title="Night starting ${name} evening"
              @click=${() => {
                this._owlDays = this._toggle(this._owlDays, d);
              }}
            >
              ${name[0]}
            </button>
          `
        )}
      </div>
      <div class="hint">
        Runs only while motion is detected in the linked area (configure motion
        sensors in the integration options). A night belongs to the evening it
        starts on.
      </div>
      ${problems.length
        ? html`<div class="problems">⚠ ${problems.join(" · ")}</div>`
        : nothing}
      <div class="actions">
        <button class="btn" @click=${this._cancel}>Cancel</button>
        <button
          class="btn primary"
          ?disabled=${this.saving || problems.length > 0}
          @click=${this._save}
        >
          Save
        </button>
      </div>
    `;
  }

  render() {
    if (!this.target || !this._draft) return nothing;
    return html`
      <div class="sheet">
        ${this.target === "night_owl" ? this._renderNightOwlEditor() : this._renderWindowEditor()}
      </div>
    `;
  }

  static styles = css`
    :host {
      -webkit-tap-highlight-color: transparent;
    }
    .sheet {
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      padding: 10px;
      margin-top: 8px;
      background: var(--secondary-background-color, rgba(127, 127, 127, 0.06));
    }
    .head {
      font-weight: 600;
      margin-bottom: 8px;
    }
    .rows {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
      align-items: flex-end;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 2px;
      font-size: 0.8em;
      color: var(--secondary-text-color);
    }
    .field.check {
      flex-direction: row;
      align-items: center;
      gap: 6px;
      font-size: 0.9em;
      color: var(--primary-text-color);
    }
    input,
    select {
      font: inherit;
      color: var(--primary-text-color);
      background: var(--card-background-color);
      border: 1px solid var(--divider-color);
      border-radius: 6px;
      padding: 5px 6px;
      min-width: 72px;
    }
    input[type="number"] {
      width: 84px;
    }
    .chips {
      display: flex;
      align-items: center;
      gap: 5px;
      margin-top: 10px;
      flex-wrap: wrap;
    }
    .chips-label {
      font-size: 0.8em;
      color: var(--secondary-text-color);
      margin-right: 2px;
    }
    .chip {
      font: inherit;
      font-size: 0.85em;
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      padding: 3px 10px;
      cursor: pointer;
    }
    .chip.sel {
      background: var(--primary-color);
      border-color: var(--primary-color);
      color: var(--text-primary-color, #fff);
    }
    .problems {
      margin-top: 10px;
      color: var(--error-color);
      font-size: 0.85em;
    }
    .hint {
      margin-top: 10px;
      color: var(--secondary-text-color);
      font-size: 0.8em;
    }
    .actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 12px;
    }
    .btn {
      font: inherit;
      border: 1px solid var(--divider-color);
      border-radius: 8px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      padding: 6px 16px;
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
    button:active {
      filter: brightness(0.92);
    }
    button:focus-visible,
    input:focus-visible,
    select:focus-visible {
      outline: 2px solid var(--primary-color);
      outline-offset: 1px;
    }
    @media (pointer: coarse) {
      .chip {
        min-height: 40px;
        padding: 8px 12px;
      }
      .btn {
        min-height: 44px;
      }
    }
  `;
}

if (!customElements.get("al-editor-sheet")) {
  customElements.define("al-editor-sheet", AlEditorSheet);
}
