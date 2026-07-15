# Aroma-Link Integration for Home Assistant

A Home Assistant custom integration for **Aroma-Link WiFi diffusers** — schedules, motion-gated Night Owl, HVAC/occupancy gating, timed runs, and oil tracking, with a fast Lovelace card.

**v3 architecture ("power-gated"):** Home Assistant owns the schedule model. Your weekly windows are pushed to the device's onboard scheduler once per edit (a single reconciler with verify-after-write), and at runtime the integration only toggles **power** — the device diffuses when power is on *and* the time is inside an armed window. No automations or blueprints are needed: HVAC, occupancy, and Night Owl motion gating are built in.

## Features

- **Instant schedule editing** — edits save to HA storage immediately; the device sync runs in the background with a visible Synced / Pending / Error status
- **Weekly schedule** — up to 4 windows per day (start/end, work/pause seconds, level A/B/C)
- **Night Owl** 🦉 — overnight diffusing outside scheduled hours, only while **motion** is detected in the linked area; per-night allow flags, configurable window (outside-hours or fixed, e.g. 22:00–06:00) and motion linger
- **Built-in gating** — per device, optional: only diffuse while the **HVAC** is circulating air and/or someone is **home** (configure in the integration's Options)
- **Timed runs** — run for N minutes with automatic shutoff that **survives HA restarts**
- **Truthful state** — `binary_sensor.<name>_scheduled_on` is on only when the device is powered **and** inside an armed window; its attributes show why it is (or isn't) diffusing
- **Oil tracking & calibration** — consumption rate (ml/h), level %, days remaining; measured or manual calibration via services or the card
- **Lovelace card** — Lit-based, live-updating (event-driven, no polling), week grid on tablets / day view on phones, inline editor with apply-to-days chips, undo, timed-run countdown that survives reloads, oil panel; zero configuration required
- **SSL fallback** — verification on by default with automatic bypass + notification if Aroma-Link's certificate breaks again
- **Multi-device** — auto-discovers every diffuser on your account

## Installation

### HACS (Recommended)

1. Open HACS → three-dot menu → **Custom repositories**
2. Paste: `https://github.com/cjam28/homeassistant_aroma-link`, type **Integration**, **Add**
3. Download "Aroma-Link" and restart Home Assistant
4. Add the integration: Settings → Devices & Services → Add Integration → Aroma-Link (account credentials; devices are auto-discovered)

### Manual

Copy `custom_components/aroma_link_integration/` into your `config/custom_components/` and restart.

## The card

The card auto-registers as a Lovelace resource. Add it to any dashboard:

```yaml
type: custom:aroma-link-schedule-card
# everything below is optional:
devices:            # omit to show all diffusers
  - "12345"         # Aroma-Link device id
show_controls: true # power / fan / timed run
show_schedule: true
show_oil: true
compact: false      # denser layout for wall tablets
title: ""           # override the header
```

A GUI editor is available when adding the card from the dashboard UI.

## Gating (replaces the old blueprints)

Settings → Devices & Services → Aroma-Link → **Configure** → *Diffusing gates*:

- **HVAC gate** — pick a `climate` entity; scheduled windows only diffuse while its `hvac_action` shows air moving (sustained for the configured delay)
- **Occupancy gate** — pick a `binary_sensor`; windows only diffuse while it is `on`
- **Night Owl motion sensors** — pick the motion sensors of the linked area

Leave any gate empty to disable it. `switch.<name>_schedule_active` ("Schedule Enabled") is the master: turn it off to take manual control of power. Note: while it is on, manual power flips are corrected within ~a minute — use a timed run for ad-hoc diffusing.

## Entities (17 per device)

| Entity | Purpose |
|---|---|
| `switch.<name>_power` | Device power (the runtime lever the gating engine drives) |
| `switch.<name>_fan` | Exhaust fan |
| `switch.<name>_schedule_active` | Gating master ("Schedule Enabled") |
| `switch.<name>_night_owl` | Night Owl master |
| `number.<name>_work_duration` / `_pause_duration` | Defaults for timed runs / new windows |
| `binary_sensor.<name>_scheduled_on` | Truthful "allowed to diffuse now" + gate attributes |
| `sensor.<name>_work_status`, `_on_count`, `_pump_count`, `_signal_strength`, `_firmware_version`, `_last_update` | Diagnostics (counts keep long-term statistics) |
| `sensor.<name>_cumulative_runtime`, `_oil_level`, `_oil_remaining` | Oil tracking |
| `button.<name>_refill` | Record an oil refill (keeps calibration) |

## Services

| Service | Purpose |
|---|---|
| `aroma_link_integration.start_timed_run` | Run for `duration_minutes` (restart-surviving auto-off; optional `work_sec`/`pause_sec`) |
| `aroma_link_integration.cancel_timed_run` | Cancel the auto-off (device left as-is) |
| `aroma_link_integration.sync_schedules` | Check device slots against the model; re-push on drift |
| `aroma_link_integration.oil_refill` | Record a refill (`fill_volume`, `fill_date`, `keep_calibration`) |
| `aroma_link_integration.oil_calibrate` | Calibration workflow (`action`: start/end/finalize/manual/set) |
| `aroma_link_integration.api_diagnostics` | Raw API probe for debugging |

Schedule editing is done in the card (websocket API `aroma_link/get_schedule`, `save_schedule`, …) — there are no schedule services to script against; automations should react to `binary_sensor.<name>_scheduled_on` or the `aroma_link_integration_updated` event instead.

## Upgrading from v2.x

The upgrade is automatic and one-time:

- Your device's current schedule is imported into the new HA-side model (the enabled flags use your last *user intent*, since the old blueprints routinely toggled the device bits). **Review each device's schedule once in the card after upgrading.**
- Night Owl per-day preferences and oil calibration carry over.
- ~30 editor/helper entities per device are removed (program selectors, per-day switches, oil calibration inputs, the schedule-matrix sensor); the surviving entity ids (`switch.<name>_power`, sensors, …) are unchanged.
- The three blueprints are deleted (installed copies removed too). **Re-create their behavior in the integration Options → Diffusing gates, then delete any automations that used them.** Their services (`set_scheduler`, `run_diffuser`, `set_night_owl_active`, `save_schedule_batch`, …) no longer exist.
- Dashboards keep working: the card element name and resource URL are unchanged.

## Notes

- Aroma-Link's cloud acknowledges schedule writes slowly (~15–20 s); the card's sync chip shows the push status, and a drift check re-pushes hourly if the device disagrees with the model.
- Timed runs arm a temporary 24/7 slot so they diffuse regardless of schedule windows; diffusing starts once that write reaches the device.

## Credits

Fork of [dalyem/ha_aromalink](https://github.com/dalyem/ha_aromalink). MIT licensed.
