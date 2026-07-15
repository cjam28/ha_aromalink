/**
 * <al-schedule-grid> — weekly schedule display + selection.
 *
 * Desktop: 7 columns (Mon..Sun) x 4 window rows + a Night Owl row.
 * Narrow: day-chip strip + stacked window rows for the selected day.
 *
 * Emits:
 *   cell-selected     {day, index}
 *   night-owl-edit    {}                (open the Night Owl editor)
 *   night-owl-toggle  {day, enabled}    (per-day allow flag)
 */
import { LitElement, html, css, nothing } from "./vendor/lit-all.min.js";
import { DAY_NAMES, LEVEL_LETTERS, MAX_WINDOWS, windowLabel, getDay } from "./al-model.js";
import { todayCanonical } from "./al-api.js";

class AlScheduleGrid extends LitElement {
  static properties = {
    schedule: { attribute: false },
    nightOwlEnabled: { type: Boolean },
    activeWindow: { attribute: false }, // {day, index} | null
    narrow: { type: Boolean },
    compact: { type: Boolean },
    selectedDay: { type: Number },
    editTarget: { attribute: false }, // {day, index} | "night_owl" | null
  };

  constructor() {
    super();
    this.schedule = null;
    this.nightOwlEnabled = true;
    this.activeWindow = null;
    this.narrow = false;
    this.compact = false;
    this.selectedDay = todayCanonical();
    this.editTarget = null;
  }

  _selectCell(day, index) {
    this.dispatchEvent(
      new CustomEvent("cell-selected", { detail: { day, index }, bubbles: true, composed: true })
    );
  }

  _toggleNightOwl(day, current) {
    this.dispatchEvent(
      new CustomEvent("night-owl-toggle", {
        detail: { day, enabled: !current },
        bubbles: true,
        composed: true,
      })
    );
  }

  _editNightOwl() {
    this.dispatchEvent(
      new CustomEvent("night-owl-edit", { detail: {}, bubbles: true, composed: true })
    );
  }

  _cell(day, index) {
    const w = getDay(this.schedule, day).windows[index] || null;
    const isActive =
      this.activeWindow && this.activeWindow.day === day && this.activeWindow.index === index;
    const isEditing =
      this.editTarget &&
      this.editTarget !== "night_owl" &&
      this.editTarget.day === day &&
      this.editTarget.index === index;
    const classes = [
      "cell",
      w ? (w.enabled ? "on" : "off") : "empty",
      isActive ? "active" : "",
      isEditing ? "editing" : "",
    ].join(" ");
    return html`
      <button
        class=${classes}
        @click=${() => this._selectCell(day, index)}
        title=${w ? `${w.start}–${w.end} · ${w.work_sec}s/${w.pause_sec}s · ${LEVEL_LETTERS[w.level] || w.level}` : "Add window"}
      >
        <span class="time">${windowLabel(w)}</span>
        ${w && w.enabled
          ? html`<span class="meta">${w.work_sec}/${w.pause_sec}s·${LEVEL_LETTERS[w.level] || w.level}</span>`
          : nothing}
      </button>
    `;
  }

  _nightOwlCell(day) {
    const allowed = getDay(this.schedule, day).night_owl;
    return html`
      <button
        class="cell owl ${allowed && this.nightOwlEnabled ? "on" : "off"}"
        @click=${() => this._toggleNightOwl(day, allowed)}
        title="Night Owl ${allowed ? "allowed" : "off"} for the night starting ${DAY_NAMES[day]} evening"
      >
        ${allowed ? "✓" : "–"}
      </button>
    `;
  }

  _legend() {
    if (this.compact) return nothing;
    return html`
      <div class="legend">
        <span><span class="sw on"></span>Enabled</span>
        <span><span class="sw off"></span>Off</span>
        <span><span class="sw owl"></span>Night Owl</span>
        <span><span class="sw now"></span>Running now</span>
      </div>
    `;
  }

  _renderWide() {
    const today = todayCanonical();
    return html`
      <div class="grid" style="--cols: 7">
        <div class="corner"></div>
        ${DAY_NAMES.map(
          (name, day) => html`<div class="dayhead ${day === today ? "today" : ""}">${name}</div>`
        )}
        ${[...Array(MAX_WINDOWS).keys()].map(
          (index) => html`
            <div class="rowhead">W${index + 1}</div>
            ${DAY_NAMES.map((_n, day) => this._cell(day, index))}
          `
        )}
        <button class="rowhead owl-head" @click=${this._editNightOwl} title="Edit Night Owl settings">
          <ha-icon icon="mdi:owl"></ha-icon>
        </button>
        ${DAY_NAMES.map((_n, day) => this._nightOwlCell(day))}
      </div>
      ${this._legend()}
    `;
  }

  _renderNarrow() {
    const today = todayCanonical();
    const day = this.selectedDay;
    const dayData = getDay(this.schedule, day);
    return html`
      <div class="daychips">
        ${DAY_NAMES.map(
          (name, d) => html`
            <button
              class="chip ${d === day ? "sel" : ""} ${d === today ? "today" : ""}"
              @click=${() => {
                this.selectedDay = d;
              }}
            >
              ${name[0]}
            </button>
          `
        )}
      </div>
      <div class="daylist">
        ${[...Array(MAX_WINDOWS).keys()].map((index) => {
          const w = dayData.windows[index] || null;
          const isActive =
            this.activeWindow && this.activeWindow.day === day && this.activeWindow.index === index;
          return html`
            <button
              class="dayrow ${w ? (w.enabled ? "on" : "off") : "empty"} ${isActive ? "active" : ""}"
              @click=${() => this._selectCell(day, index)}
            >
              <span class="wname">W${index + 1}</span>
              <span class="time">${windowLabel(w)}</span>
              <span class="meta">
                ${w && w.enabled
                  ? `${w.work_sec}/${w.pause_sec}s · ${LEVEL_LETTERS[w.level] || w.level}`
                  : w
                    ? "off"
                    : "+"}
              </span>
            </button>
          `;
        })}
        <div
          class="dayrow owl ${dayData.night_owl && this.nightOwlEnabled ? "on" : "off"}"
          role="button"
          tabindex="0"
          @click=${() => this._toggleNightOwl(day, dayData.night_owl)}
          @keydown=${(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              this._toggleNightOwl(day, dayData.night_owl);
            }
          }}
        >
          <span class="wname"><ha-icon icon="mdi:owl"></ha-icon></span>
          <span class="time">Night Owl</span>
          <span class="meta">${dayData.night_owl ? "allowed tonight" : "off tonight"}</span>
          <button
            class="owl-cog"
            title="Edit Night Owl settings"
            @click=${(e) => {
              e.stopPropagation();
              this._editNightOwl();
            }}
          >
            <ha-icon icon="mdi:cog-outline"></ha-icon>
          </button>
        </div>
      </div>
      ${this._legend()}
    `;
  }

  render() {
    if (!this.schedule) return html`<div class="loading">Loading schedule…</div>`;
    return this.narrow ? this._renderNarrow() : this._renderWide();
  }

  static styles = css`
    :host {
      display: block;
      -webkit-tap-highlight-color: transparent;
      --al-owl: var(--aroma-link-night-owl-color, #673ab7);
      --al-owl-tint: color-mix(in srgb, var(--al-owl) 18%, transparent);
      --al-owl-border: color-mix(in srgb, var(--al-owl) 60%, transparent);
    }
    .loading {
      color: var(--secondary-text-color);
      padding: 8px;
    }
    .grid {
      display: grid;
      grid-template-columns: auto repeat(var(--cols), minmax(0, 1fr));
      gap: 3px;
    }
    .corner {
    }
    .dayhead {
      text-align: center;
      font-size: 0.78em;
      color: var(--secondary-text-color);
      padding: 2px 0;
    }
    .dayhead.today {
      color: var(--primary-color);
      font-weight: 600;
    }
    .rowhead {
      font-size: 0.75em;
      color: var(--secondary-text-color);
      display: flex;
      align-items: center;
      padding-right: 4px;
      background: none;
      border: none;
    }
    .owl-head {
      cursor: pointer;
      font-size: 1em;
      --mdc-icon-size: 18px;
    }
    button.cell {
      font: inherit;
      border: 1px solid var(--divider-color);
      border-radius: 6px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      padding: 4px 2px;
      cursor: pointer;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 1px;
      min-height: 38px;
      min-width: 0;
      overflow: hidden;
      justify-content: center;
    }
    button.cell .time {
      font-size: 0.72em;
      white-space: nowrap;
    }
    button.cell .meta {
      font-size: 0.62em;
      color: var(--secondary-text-color);
      white-space: nowrap;
    }
    button.cell.on {
      border-color: rgba(var(--rgb-primary-color, 33, 150, 243), 0.7);
      background: rgba(var(--rgb-primary-color, 33, 150, 243), 0.12);
    }
    button.cell.off {
      border-style: dashed;
      opacity: 0.65;
    }
    button.cell.empty .time {
      color: var(--disabled-text-color, var(--secondary-text-color));
    }
    button.cell.active {
      outline: 2px solid var(--primary-color);
      outline-offset: 1px;
    }
    button.cell.editing {
      outline: 2px dashed var(--primary-color);
      outline-offset: 1px;
    }
    button.cell.owl.on {
      background: var(--al-owl-tint);
      border-color: var(--al-owl-border);
    }
    .daychips {
      display: flex;
      gap: 4px;
      margin-bottom: 6px;
    }
    .chip {
      flex: 1;
      font: inherit;
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      padding: 4px 0;
      cursor: pointer;
    }
    .chip.sel {
      background: var(--primary-color);
      color: var(--text-primary-color, #fff);
      border-color: var(--primary-color);
    }
    .chip.today:not(.sel) {
      border-color: var(--primary-color);
    }
    .daylist {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .dayrow {
      font: inherit;
      display: flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--divider-color);
      border-radius: 8px;
      background: var(--card-background-color);
      color: var(--primary-text-color);
      padding: 8px;
      cursor: pointer;
      text-align: left;
    }
    .dayrow.on {
      border-color: rgba(var(--rgb-primary-color, 33, 150, 243), 0.7);
      background: rgba(var(--rgb-primary-color, 33, 150, 243), 0.1);
    }
    .dayrow.off {
      border-style: dashed;
      opacity: 0.65;
    }
    .dayrow.active {
      outline: 2px solid var(--primary-color);
    }
    .dayrow .wname {
      font-size: 0.8em;
      color: var(--secondary-text-color);
      width: 28px;
      --mdc-icon-size: 18px;
    }
    .dayrow .meta {
      margin-left: auto;
      font-size: 0.8em;
      color: var(--secondary-text-color);
    }
    .dayrow.owl.on {
      background: var(--al-owl-tint);
      border-color: var(--al-owl-border);
    }
    .owl-cog {
      font: inherit;
      display: flex;
      align-items: center;
      border: none;
      background: none;
      color: var(--secondary-text-color);
      padding: 4px;
      margin-left: 8px;
      cursor: pointer;
      --mdc-icon-size: 18px;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 12px;
      margin-top: 6px;
      font-size: 0.72em;
      color: var(--secondary-text-color);
    }
    .legend .sw {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 3px;
      margin-right: 4px;
      border: 1px solid var(--divider-color);
      vertical-align: -1px;
    }
    .legend .sw.on {
      background: rgba(var(--rgb-primary-color, 33, 150, 243), 0.12);
      border-color: rgba(var(--rgb-primary-color, 33, 150, 243), 0.7);
    }
    .legend .sw.off {
      border-style: dashed;
      opacity: 0.65;
    }
    .legend .sw.owl {
      background: var(--al-owl-tint);
      border-color: var(--al-owl-border);
    }
    .legend .sw.now {
      outline: 2px solid var(--primary-color);
      outline-offset: 1px;
    }
    button:active,
    .dayrow:active {
      filter: brightness(0.92);
    }
    button:focus-visible,
    .dayrow:focus-visible {
      outline: 2px solid var(--primary-color);
      outline-offset: 1px;
    }
    @media (pointer: coarse) {
      button.cell {
        min-height: 44px;
      }
      .chip {
        min-height: 40px;
        padding: 8px 12px;
      }
      .dayrow {
        min-height: 44px;
      }
    }
  `;
}

if (!customElements.get("al-schedule-grid")) {
  customElements.define("al-schedule-grid", AlScheduleGrid);
}
