# Aroma-Link Integration for Home Assistant

A full-featured Home Assistant custom integration for **Aroma-Link WiFi diffusers**. Control power, fan, schedules, timed runs, oil tracking, and advanced automations — all from your Home Assistant dashboard or the companion app.

## Features

- **Power & Fan Control** — Toggle the diffuser and fan on/off
- **5-Program Scheduling** — Up to 5 time-based programs per day (P1–P5), each with start/end time, work/pause durations, and consistency level (A/B/C)
- **Interactive Lovelace Card** — Auto-registered custom card with a 7-day x 5-program schedule matrix, multi-cell editing, and responsive design
- **Timed Runs** — Start the diffuser for a set number of hours with automatic shutoff (server-side timer)
- **Batch Schedule Sync** — Optimized save that batches identical days into single API calls
- **Oil Calibration & Tracking** — Track oil consumption rate (ml/hr), fill level, and runtime with manual override support
- **Schedule-Aware Binary Sensor** — `binary_sensor.<name>_scheduled_on` indicates whether the current time falls within a scheduled program
- **Silent Program Control** — Enable/disable schedule programs without the audible beep (uses program toggle instead of power toggle)
- **Night Owl Mode (P5)** — Dedicated after-hours program with a standalone switch for automation
- **Automation Blueprints** — Pre-built, auto-installed blueprints for HVAC-linked scheduling and presence-based Night Owl control
- **SSL Fallback** — Option to bypass SSL verification with automatic fallback if certificates expire
- **Multi-Device Support** — Auto-discovers all devices on your Aroma-Link account
- **Responsive Design** — Card scales cleanly across desktop, tablet, and mobile (including iOS companion app)
- **Configurable Polling** — 1–30 minute polling interval with optional debug logging
- **Diagnostics API** — Call any Aroma-Link API endpoint for discovery and troubleshooting

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three-dot menu (top right) and select **Custom repositories**
3. Paste: `https://github.com/cjam28/homeassistant_aroma-link`
4. Select **Integration** as the type, then click **Add**
5. Find "Aroma-Link Integration" in HACS and click **Download**
6. Restart Home Assistant

### Manual

1. Copy the `aroma_link_integration` folder into your `custom_components` directory:

```bash
cp -r aroma_link_integration /config/custom_components/
```

2. Restart Home Assistant

## Configuration

1. Go to **Settings > Devices & Services**
2. Click **+ Add Integration** and search for **Aroma-Link Integration**
3. Enter your Aroma-Link username and password
4. (Optional) Enable **Allow SSL Fallback** — this lets the integration continue operating if Aroma-Link's SSL certificates expire. A disclaimer is shown; HTTPS encryption is still used, but certificate validation is bypassed.
5. The integration automatically discovers and adds all devices in your account

### Options (Post-Setup)

Go to the integration's options page to configure:

- **Polling interval** (1–30 minutes)
- **Debug logging** toggle
- **SSL fallback** toggle

## Custom Schedule Card

The integration includes a custom Lovelace card that is **automatically registered** — no manual frontend installation required.

### Adding the Card

Add this to any dashboard:

```yaml
type: custom:aroma-link-schedule-card
```

Optionally filter to a specific device:

```yaml
type: custom:aroma-link-schedule-card
device: my_device_name
```

### Card Features

- **7 x 5 schedule matrix** — days of the week vs. programs P1–P5
- **Multi-cell selection** — click cells individually, or click a P# row header to select the entire row
- **Bulk editing** — changes apply to all selected cells at once
- **Overlap validation** — warns before saving conflicting time ranges on the same day
- **Batch sync** — optimized save groups identical schedules into single API calls
- **Pull / Sync buttons** — refresh from or push to the Aroma-Link cloud
- **Power, Fan, Timed Run controls** — integrated directly in the card header
- **Oil Calibration & Tracking panel** — expandable section for fill level, consumption rate, and manual overrides
- **Night Owl label** — P5 is labeled "Night Owl" in the matrix for clarity
- **Responsive layout** — fluid typography and container queries scale from mobile to desktop
- **Dark mode support** — adapts to Home Assistant's theme

### Manual Resource Registration (if needed)

If auto-registration fails, add the resource manually:

1. Go to **Settings > Dashboards > Resources** (three-dot menu)
2. Click **Add Resource**
3. URL: `/aroma_link_integration/aroma-link-schedule-card.js`
4. Type: **JavaScript Module**

## Automation Blueprints

Three automation blueprints are **automatically installed** when the integration loads. Find them under **Settings > Automations & Scenes > Blueprints**.

### 1. Scheduled Diffuser with HVAC

**Blueprint:** `Aroma-Link: Scheduled Diffuser with HVAC`

Links your diffuser to your HVAC system. When the thermostat starts heating or cooling (hvac_action leaves `idle`) during scheduled hours and someone is home, the diffuser schedule is silently enabled. When the HVAC goes idle, the schedule is disabled. Works with ecobee, Nest, and other standard climate entities.

**Inputs:**
- HVAC / thermostat entity
- Home occupancy sensor
- Aroma-Link "Scheduled On" binary sensor
- Aroma-Link "Schedule Active" switch
- HVAC on delay (minutes)

### 2. Night Owl (After-Hours Presence)

**Blueprint:** `Aroma-Link: Night Owl (After-Hours Presence)`

Activates the Night Owl program (P5) when room presence sensors detect someone is up *outside* of scheduled hours. Uses silent program toggle — no beep.

**Inputs:**
- Room presence sensors (one or more)
- Aroma-Link "Scheduled On" binary sensor
- Aroma-Link "Night Owl" switch
- Turn-off delay (minutes)

### 3. Full Diffuser Control (Schedule + Night Owl)

**Blueprint:** `Aroma-Link: Full Diffuser Control (Schedule + Night Owl)`

All-in-one automation combining both of the above. During scheduled hours, it follows the HVAC fan. Outside scheduled hours, it responds to room presence.

**Inputs:** All of the above combined.

### Setting Up Night Owl (P5)

1. Open the schedule card on your dashboard
2. Select all 7 day cells for P5 (or click the "Night Owl" row header)
3. Set your desired time window (e.g., `20:00` to `06:00` for evenings/nights)
4. Set work/pause durations and level as desired
5. **Leave Enabled = OFF** — the automation will toggle it on/off based on presence
6. Sync to the device

## Entities

The integration creates the following entities per device (entity IDs use your device name slug, shown here as `<name>`):

### Switches

| Entity | Description |
|---|---|
| `switch.<name>_power` | Diffuser power on/off |
| `switch.<name>_fan` | Fan on/off |
| `switch.<name>_program_enabled` | Enable/disable the currently selected editor program |
| `switch.<name>_schedule_active` | Enable/disable all scheduled programs for today (silent, no beep) |
| `switch.<name>_night_owl` | Enable/disable P5 Night Owl program for today (silent, no beep) |
| `switch.<name>_program_<day>` | Day-of-week toggles for the schedule editor (Monday–Sunday) |

### Binary Sensors

| Entity | Description |
|---|---|
| `binary_sensor.<name>_scheduled_on` | `on` if the current time is within any enabled program's window |

Extra attributes: `active_programs`, `active_program_count`, `current_window_start`, `current_window_end`, `work_sec`, `pause_sec`, `level`

### Sensors

| Entity | Description |
|---|---|
| `sensor.<name>_work_status` | Current status: Off / Diffusing / Paused |
| `sensor.<name>_work_remaining` | Seconds remaining in current work cycle |
| `sensor.<name>_pause_remaining` | Seconds remaining in current pause cycle |
| `sensor.<name>_on_count` | Total activation count |
| `sensor.<name>_pump_count` | Total diffusion count |
| `sensor.<name>_signal_strength` | WiFi signal strength (if available) |
| `sensor.<name>_firmware_version` | Firmware version (if available) |
| `sensor.<name>_last_update` | Last API update timestamp (if available) |
| `sensor.<name>_oil_consumption_rate` | Calculated oil consumption rate (ml/hr) |

### Numbers

| Entity | Description |
|---|---|
| `number.<name>_work_duration` | Default work duration (seconds) |
| `number.<name>_pause_duration` | Default pause duration (seconds) |
| `number.<name>_program_work_time` | Editor: program work duration (5–900s) |
| `number.<name>_program_pause_time` | Editor: program pause duration (5–900s) |
| `number.<name>_manual_start_volume` | Oil calibration: start volume (ml) |
| `number.<name>_manual_end_volume` | Oil calibration: end volume (ml) |
| `number.<name>_manual_runtime_hours` | Oil calibration: total runtime (hours) |
| `number.<name>_manual_rate_ml_per_hour` | Oil calibration: manual consumption rate override (ml/hr) |

### Buttons

| Entity | Description |
|---|---|
| `button.<name>_run` | Start the diffuser with current work/pause settings |
| `button.<name>_save_settings` | Save current work/pause settings to the device |
| `button.<name>_save_program` | Save the schedule editor program to selected days |
| `button.<name>_apply_manual_calibration` | Apply manual oil calibration override |

### Selects

| Entity | Description |
|---|---|
| `select.<name>_program_day` | Schedule editor: select day of week |
| `select.<name>_program` | Schedule editor: select program number (1–5) |
| `select.<name>_program_level` | Schedule editor: consistency level (A/B/C) |

### Text

| Entity | Description |
|---|---|
| `text.<name>_program_start_time` | Schedule editor: program start time (HH:MM) |
| `text.<name>_program_end_time` | Schedule editor: program end time (HH:MM) |
| `text.<name>_oil_fill_date` | Oil calibration: date oil was last filled (YYYY-MM-DD) |

## Services

### `aroma_link_integration.run_diffuser`
Run the diffuser for a specific time with custom work/pause durations.

### `aroma_link_integration.set_scheduler`
Set the weekly scheduler with work/pause durations.

### `aroma_link_integration.start_timed_run`
Start a timed run (0.1–24 hours) with automatic shutoff. The timer runs server-side and survives browser close.

### `aroma_link_integration.cancel_timed_run`
Cancel an active timed run (does not turn off the device).

### `aroma_link_integration.get_timed_run_status`
Get status of active timed runs (fires an event).

### `aroma_link_integration.save_schedule_batch`
Fast batch save — sends schedule data directly to the device, batching days with identical schedules into single API calls.

### `aroma_link_integration.refresh_all_schedules`
Refresh schedules for all 7 days from the API.

### `aroma_link_integration.set_editor_program`
Load a specific day/program into the schedule editor entities.

### `aroma_link_integration.load_workset` / `save_workset`
Legacy helper-based schedule load/save (backward compatible).

### `aroma_link_integration.reset_oil_runtime`
Reset the cumulative oil runtime counter (call when refilling oil).

### `aroma_link_integration.api_diagnostics`
Call any Aroma-Link API endpoint for discovery/diagnostics. Optionally fires an event with the response.

## Oil Calibration & Tracking

The integration tracks oil usage and calculates consumption rates:

1. **Automatic tracking** — When a fill date is set and the diffuser operates, the integration accumulates runtime and estimates consumption.
2. **Manual override** — Set start volume, end volume, and runtime hours to calculate a consumption rate, or directly enter the rate in ml/hr.
3. **Data persistence** — Calibration data is saved to Home Assistant storage and survives restarts.

### Quick Start

1. Fill your diffuser and set the **Oil Fill Date** to today
2. Let the diffuser operate normally
3. The **Consumption Rate** sensor will begin reporting once enough data accumulates
4. Alternatively, use the manual override fields if you already know your rate

## Troubleshooting

- **Can't connect:** Verify your Aroma-Link credentials work in the official app/website
- **SSL errors:** Enable the "Allow SSL Fallback" option in the integration settings
- **Card not appearing:** Clear your browser cache (Cmd+Shift+R) or try an incognito window. If needed, manually register the resource (see above).
- **Schedule not updating after sync:** Click "Pull Schedule" in the card to refresh, or call the `refresh_all_schedules` service
- **iOS/Safari scroll issues:** The card includes scroll-lock mitigations. If issues persist, ensure you're on the latest version.
- **Debug logging:** Enable in the integration options to see detailed API calls in the Home Assistant logs

## Requirements

- A valid Aroma-Link account with at least one registered diffuser
- Home Assistant 2023.3.0 or newer
- Active internet connection (the integration communicates with Aroma-Link's cloud API)

## FAQ

**Q: Can I control multiple diffusers?**
A: Yes. The integration auto-discovers all devices in your Aroma-Link account. Each device gets its own set of entities. Use the `device_id` parameter in service calls to target a specific device.

**Q: How does the "silent" control work?**
A: The Schedule Active and Night Owl switches enable/disable schedule programs via the API rather than toggling device power. This avoids the audible beep that the power toggle causes.

**Q: What is the Night Owl program?**
A: P5 is designated as "Night Owl" — an after-hours program you can configure with your desired settings but leave disabled by default. Automations (or blueprints) can then toggle P5 on/off based on room presence sensors.

**Q: Do I need to set up the blueprints?**
A: Blueprints are optional. They provide guided automation setup for common scenarios (HVAC-linked scheduling, presence-based after-hours). You can also build your own automations using the provided entities.

**Q: What happens if I change settings in the Aroma-Link app?**
A: Device state (power, fan) is polled on the configured interval (default 1 minute). Schedule changes are loaded on-demand when you view the card or call `refresh_all_schedules`.

## Credits

**Fork Chain:**
- Original: [Memberapple/ha_aromalink](https://github.com/Memberapple/ha_aromalink)
- HACS support: [DalyMauldin/ha_aromalink](https://github.com/DalyMauldin/ha_aromalink)
- This fork: [cjam28/homeassistant_aroma-link](https://github.com/cjam28/homeassistant_aroma-link)

## Links

- [Documentation](https://github.com/cjam28/homeassistant_aroma-link#readme)
- [Issue Tracker](https://github.com/cjam28/homeassistant_aroma-link/issues)

## License

This integration is provided as-is with no warranties.
