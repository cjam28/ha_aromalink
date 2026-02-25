/**
 * Aroma-Link Schedule Card v2.7.0
 * 
 * A complete dashboard card for Aroma-Link diffusers including:
 * - Compact manual controls (Power applies work/pause, Fan, Timed Run)
 * - Schedule matrix with multi-cell editing and LOCAL STAGING
 * - Stage Edits → Sync Staged workflow (bulk operations)
 * - Copy schedule from another diffuser
 * - SERVER-SIDE timed run (survives browser close!)
 * - OIL LEVEL TRACKING with bottle visualization and calibration
 * - RESPONSIVE DESIGN: Fluid typography & layout for mobile/tablet/desktop
 * - Optimized rendering (debounced, surgical updates for countdown)
 * - Safari/iOS scroll fix with proper touch handling
 * - FIX: Auto-refresh matrix after batch sync (v2.6.1)
 * - FIX: Safari touch handler passive:false for preventDefault (v2.6.2)
 * - FIX: Suppress renders during operations to prevent scroll jump (v2.6.2)
 * - FIX: Comprehensive Safari/iOS scroll fix with async renders (v2.6.3)
 *   - All user interactions use setTimeout for async render
 *   - Triple-RAF scroll restoration
 *   - CSS overscroll-behavior: contain
 *   - Surgical status updates without full render
 * - FIX: Block HA's action-handler from causing scroll events (v2.6.4)
 *   - capture:true + stopImmediatePropagation on touch events
 *   - Card-level scroll/wheel event blocking
 * - FIX: Document-level scroll lock during interactions (v2.6.5)
 *   - interactionFlag.active triggers scroll position restoration
 *   - Catches scroll events that bypass element handlers
 * 
 * Styled to match Mushroom/button-card aesthetics.
 * Auto-discovers all Aroma-Link devices - no configuration needed!
 */

class AromaLinkScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._initialized = false;
    // Per-device selection state: Map<deviceName, Set<"day-program">>
    this._selectedCellsByDevice = new Map();
    this._isSaving = false;
    this._statusMessage = null;
    this._statusDevice = null;
    this._editorValuesByDevice = new Map();
    // Timer state: Map<deviceName, { endTime: number, intervalId: number, remainingSeconds: number }>
    this._timersByDevice = new Map();
    // Default timed run duration in HOURS per device
    this._timedRunHoursByDevice = new Map();
    
    // STAGED CHANGES: Map<deviceName, Map<"day-program", scheduleData>>
    // This holds changes that have been "staged" locally but not yet pushed to API
    this._stagedChangesByDevice = new Map();
    
    // Flag to suppress renders during operations (push/pull)
    this._isOperationInProgress = false;
    this._renderPending = false;

    // State fingerprint for smart re-render (only re-render when relevant entities change)
    this._lastStateFingerprint = null;

    // Oil panel open state per device
    this._oilPanelOpenByDevice = new Map();

    // Local oil input values to prevent reset while typing
    this._oilInputValuesByDevice = new Map();
  }

  // Lifecycle: cleanup timers when card is removed from DOM
  disconnectedCallback() {
    // Clear all interval timers to prevent memory leaks
    for (const [deviceName, timerState] of this._timersByDevice.entries()) {
      if (timerState?.intervalId) {
        clearInterval(timerState.intervalId);
      }
    }
    this._timersByDevice.clear();
  }

  _getTimedRunHours(deviceName) {
    if (!this._timedRunHoursByDevice.has(deviceName)) {
      this._timedRunHoursByDevice.set(deviceName, 6); // Default 6 hours
    }
    return this._timedRunHoursByDevice.get(deviceName);
  }

  _setTimedRunHours(deviceName, hours) {
    this._timedRunHoursByDevice.set(deviceName, hours);
  }

  _getOilInputValues(deviceName) {
    if (!this._oilInputValuesByDevice.has(deviceName)) {
      this._oilInputValuesByDevice.set(deviceName, {});
    }
    return this._oilInputValuesByDevice.get(deviceName);
  }

  _setOilInputValue(deviceName, field, value) {
    const values = this._getOilInputValues(deviceName);
    // For date fields, only store valid yyyy-MM-dd values
    if (field === 'fillDate') {
      if (value && typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
        values[field] = value;
      } else {
        values[field] = ''; // Store empty string for invalid dates
      }
    } else {
      values[field] = value;
    }
  }

  _readOilInputValue(deviceName, field, fallbackValue) {
    const values = this._getOilInputValues(deviceName);
    if (values[field] !== undefined && values[field] !== null && values[field] !== '') {
      // For date fields, validate format before returning
      if (field === 'fillDate') {
        return /^\d{4}-\d{2}-\d{2}$/.test(values[field]) ? values[field] : '';
      }
      return values[field];
    }
    // For date fields, validate fallback too
    if (field === 'fillDate') {
      return (fallbackValue && typeof fallbackValue === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(fallbackValue)) ? fallbackValue : '';
    }
    return fallbackValue;
  }

  _getOilPanelOpen(deviceName) {
    if (!this._oilPanelOpenByDevice.has(deviceName)) {
      this._oilPanelOpenByDevice.set(deviceName, false);
    }
    return this._oilPanelOpenByDevice.get(deviceName);
  }

  _setOilPanelOpen(deviceName, isOpen) {
    this._oilPanelOpenByDevice.set(deviceName, isOpen);
  }

  _recalculateManualFields(deviceName, changedField) {
    const values = this._getOilInputValues(deviceName);
    const read = (key) => parseFloat(values[key]) || 0;
    const set = (key, val) => {
      if (val === null || val === undefined || isNaN(val)) return;
      values[key] = val.toFixed(key === 'manualRate' ? 2 : 1);
      const input = this.shadowRoot.querySelector(`.oil-input[data-device="${deviceName}"][data-oil="${key}"]`);
      if (input) input.value = values[key];
    };

    const start = read('manualStart');
    const end = read('manualEnd');
    const runtime = read('manualRuntime');
    const rate = read('manualRate');

    // If rate is missing and we have start/end/runtime, calculate rate
    if (changedField !== 'manualRate' && start > 0 && end >= 0 && runtime > 0 && start > end) {
      const calcRate = (start - end) / runtime;
      if (calcRate > 0) set('manualRate', calcRate);
      return;
    }

    // If rate is provided, calculate missing runtime or end volume
    if (changedField === 'manualRate' && rate > 0) {
      if (start > 0 && runtime > 0 && end <= 0) {
        const calcEnd = Math.max(0, start - (rate * runtime));
        set('manualEnd', calcEnd);
      } else if (start > 0 && end >= 0 && end < start && runtime <= 0) {
        const calcRuntime = (start - end) / rate;
        set('manualRuntime', calcRuntime);
      }
    }
  }

  async _syncManualInputs(deviceName) {
    const values = this._getOilInputValues(deviceName);
    const mappings = {
      manualStart: `number.${deviceName}_oil_manual_start_volume`,
      manualEnd: `number.${deviceName}_oil_manual_end_volume`,
      manualRuntime: `number.${deviceName}_oil_manual_runtime_hours`,
      manualRate: `number.${deviceName}_oil_manual_rate`,
    };

    const calls = [];
    for (const [field, entityId] of Object.entries(mappings)) {
      if (!this._hass.states[entityId]) continue;
      const raw = values[field];
      if (raw === undefined || raw === null || raw === '') continue;
      const num = parseFloat(raw);
      if (isNaN(num)) continue;
      calls.push(
        this._hass.callService('number', 'set_value', {
          entity_id: entityId,
          value: num
        })
      );
    }

    if (calls.length) {
      await Promise.all(calls);
    }
  }

  _getTimerState(deviceName) {
    return this._timersByDevice.get(deviceName);
  }

  _formatCountdown(seconds) {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hrs > 0) {
      return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  _titleCase(name) {
    return name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, char => char.toUpperCase());
  }

  async _startTimedRun(deviceName, deviceId, hours) {
    // Use SERVER-SIDE timer service (survives browser close!)
    const editorValues = this._getEditorValues(deviceName);
    
    try {
      await this._hass.callService('aroma_link_integration', 'start_timed_run', {
        device_id: deviceId,
        duration_hours: hours,
        work_sec: editorValues.workSec,
        pause_sec: editorValues.pauseSec
      });
      
      // Start local countdown display (just for UI, server handles actual turn-off)
      const endTime = Date.now() + (hours * 60 * 60 * 1000);
      const timerState = {
        endTime,
        remainingSeconds: hours * 60 * 60,
        intervalId: null,
        deviceId: deviceId
      };
      
      timerState.intervalId = setInterval(() => {
        const remaining = Math.max(0, Math.ceil((timerState.endTime - Date.now()) / 1000));
        timerState.remainingSeconds = remaining;
        
        if (remaining <= 0) {
          // Timer complete - clear local display (server already turned off device)
          this._clearLocalTimer(deviceName);
          this._showStatus('Timer complete - turned off', false, deviceName);
          this.render();
        } else {
          // Just update the countdown display without full re-render
          this._updateCountdownDisplay(deviceName, remaining);
        }
      }, 1000);
      
      this._timersByDevice.set(deviceName, timerState);
      this._showStatus(`Timer started: ${hours}h (server-side)`, false, deviceName);
      
    } catch (e) {
      console.error('Failed to start timed run:', e);
      this._showStatus('Failed to start timer', true, deviceName);
    }
  }

  _clearLocalTimer(deviceName) {
    const timerState = this._timersByDevice.get(deviceName);
    if (timerState?.intervalId) {
      clearInterval(timerState.intervalId);
    }
    this._timersByDevice.delete(deviceName);
  }

  _updateCountdownDisplay(deviceName, seconds) {
    // Surgically update just the countdown element without re-rendering
    const countdownEl = this.shadowRoot?.querySelector(`.countdown[data-device="${deviceName}"]`);
    if (countdownEl) {
      countdownEl.textContent = `⏱️ ${this._formatCountdown(seconds)}`;
    } else {
      // Countdown element not found, might need full render
      // But check if the container exists to avoid unnecessary renders
      const runPanel = this.shadowRoot?.querySelector(`.run-panel[data-device="${deviceName}"]`);
      if (runPanel) {
        this.render();
      }
    }
  }

  async _cancelTimer(deviceName, deviceId) {
    // Cancel server-side timer
    try {
      await this._hass.callService('aroma_link_integration', 'cancel_timed_run', {
        device_id: deviceId
      });
    } catch (e) {
      console.error('Failed to cancel timed run:', e);
    }
    
    // Clear local display
    this._clearLocalTimer(deviceName);
    this._showStatus('Timer cancelled (device still ON)', false, deviceName);
    this.render();
  }

  async _applySettingsAndRun(deviceName, controls) {
    const editorValues = this._getEditorValues(deviceName);
    
    if (this._hass.states[controls.workNumber]) {
      await this._hass.callService('number', 'set_value', {
        entity_id: controls.workNumber,
        value: editorValues.workSec
      });
    }
    if (this._hass.states[controls.pauseNumber]) {
      await this._hass.callService('number', 'set_value', {
        entity_id: controls.pauseNumber,
        value: editorValues.pauseSec
      });
    }
    
    await new Promise(r => setTimeout(r, 100));
    
    if (this._hass.states[controls.power]?.state !== 'on') {
      await this._hass.callService('switch', 'turn_on', { entity_id: controls.power });
    }
  }

  _getStagedChanges(deviceName) {
    if (!this._stagedChangesByDevice.has(deviceName)) {
      this._stagedChangesByDevice.set(deviceName, new Map());
    }
    return this._stagedChangesByDevice.get(deviceName);
  }

  _hasStagedChanges(deviceName) {
    const staged = this._stagedChangesByDevice.get(deviceName);
    return staged && staged.size > 0;
  }

  _getSelectedCells(deviceName) {
    if (!this._selectedCellsByDevice.has(deviceName)) {
      this._selectedCellsByDevice.set(deviceName, new Set());
    }
    return this._selectedCellsByDevice.get(deviceName);
  }

  _isCellSelected(deviceName, day, program) {
    const cells = this._selectedCellsByDevice.get(deviceName);
    return cells && cells.has(`${day}-${program}`);
  }

  _toggleCell(deviceName, day, program, sensor) {
    const cells = this._getSelectedCells(deviceName);
    const key = `${day}-${program}`;
    if (cells.has(key)) {
      cells.delete(key);
    } else {
      cells.add(key);
      this._loadSelectedIntoEditor(sensor);
    }
    // Use setTimeout to allow browser to finish processing touch/click before render
    setTimeout(() => this.render(), 0);
  }

  _selectProgramRow(deviceName, program) {
    const cells = this._getSelectedCells(deviceName);
    const allSelected = [0,1,2,3,4,5,6].every(day => cells.has(`${day}-${program}`));
    
    if (allSelected) {
      for (let day = 0; day < 7; day++) {
        cells.delete(`${day}-${program}`);
      }
    } else {
      for (let day = 0; day < 7; day++) {
        cells.add(`${day}-${program}`);
      }
    }
    setTimeout(() => this.render(), 0);
  }

  _selectDayColumn(deviceName, day) {
    const cells = this._getSelectedCells(deviceName);
    const allSelected = [1,2,3,4,5].every(prog => cells.has(`${day}-${prog}`));
    
    if (allSelected) {
      for (let prog = 1; prog <= 5; prog++) {
        cells.delete(`${day}-${prog}`);
      }
    } else {
      for (let prog = 1; prog <= 5; prog++) {
        cells.add(`${day}-${prog}`);
      }
    }
    setTimeout(() => this.render(), 0);
  }

  _hasMultipleProgramsSameDay(deviceName) {
    const cells = this._getSelectedCells(deviceName);
    const dayProgramCount = {};
    
    for (const key of cells) {
      const [day] = key.split('-').map(Number);
      dayProgramCount[day] = (dayProgramCount[day] || 0) + 1;
      if (dayProgramCount[day] > 1) return true;
    }
    return false;
  }

  _selectAll(deviceName) {
    const cells = this._getSelectedCells(deviceName);
    for (let day = 0; day < 7; day++) {
      for (let prog = 1; prog <= 5; prog++) {
        cells.add(`${day}-${prog}`);
      }
    }
    setTimeout(() => this.render(), 0);
  }

  _clearSelection(deviceName) {
    this._getSelectedCells(deviceName).clear();
    setTimeout(() => this.render(), 0);
  }

  _getEditorValues(deviceName) {
    if (!this._editorValuesByDevice.has(deviceName)) {
      this._editorValuesByDevice.set(deviceName, {
        enabled: true,
        startTime: '09:00',
        endTime: '21:00',
        workSec: 5,
        pauseSec: 900,
        level: 'A'
      });
    }
    return this._editorValuesByDevice.get(deviceName);
  }

  _loadSelectedIntoEditor(sensor) {
    const cells = this._getSelectedCells(sensor.deviceName);
    if (cells.size === 0) return;
    
    const lastSelected = Array.from(cells).pop();
    const [dayStr, progStr] = lastSelected.split('-');
    const day = parseInt(dayStr);
    const prog = parseInt(progStr);
    
    // First check staged changes
    const staged = this._getStagedChanges(sensor.deviceName);
    const stagedData = staged.get(lastSelected);
    
    if (stagedData) {
      this._editorValuesByDevice.set(sensor.deviceName, {
        enabled: stagedData.enabled,
        startTime: stagedData.startTime,
        endTime: stagedData.endTime,
        workSec: stagedData.workSec,
        pauseSec: stagedData.pauseSec,
        level: stagedData.level
      });
      return;
    }
    
    // Otherwise load from API data
    const matrix = sensor.matrix || {};
    const dayNames = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
    const dayKey = dayNames[day];
    const dayData = matrix[dayKey] || {};
    const progData = dayData[`program_${prog}`] || {};
    
    const levelMap = {1: 'A', 2: 'B', 3: 'C', 'A': 'A', 'B': 'B', 'C': 'C'};
    
    this._editorValuesByDevice.set(sensor.deviceName, {
      enabled: progData.enabled === true || progData.enabled === 1,
      startTime: (progData.start_time || progData.start || '09:00').substring(0, 5),
      endTime: (progData.end_time || progData.end || '21:00').substring(0, 5),
      workSec: progData.work || progData.work_sec || 5,
      pauseSec: progData.pause || progData.pause_sec || 900,
      level: levelMap[progData.level] || 'A'
    });
  }

  _showStatus(message, isError = false, deviceName = null) {
    this._statusMessage = { text: message, isError };
    this._statusDevice = deviceName;
    
    // Update status bar surgically without full render if possible
    const statusEl = this.shadowRoot?.querySelector(`.status-bar[data-device="${deviceName}"]`);
    if (statusEl) {
      statusEl.textContent = message;
      statusEl.className = `status-bar ${isError ? 'error' : 'success'}`;
      statusEl.style.display = 'block';
    } else {
      // Fallback to full render if status element doesn't exist
      setTimeout(() => this.render(), 0);
    }
    
    if (!isError) {
      setTimeout(() => {
        if (this._statusMessage?.text === message) {
          this._statusMessage = null;
          this._statusDevice = null;
          // Clear status surgically
          const el = this.shadowRoot?.querySelector(`.status-bar[data-device="${deviceName}"]`);
          if (el) {
            el.style.display = 'none';
          }
        }
      }, 4000);
    }
  }

  // STAGE EDITS: Apply editor values to selected cells locally (no API call)
  _stageEdits(sensor) {
    const cells = this._getSelectedCells(sensor.deviceName);
    if (cells.size === 0) {
      this._showStatus('No cells selected to stage.', true, sensor.deviceName);
      return;
    }

    const editorValues = this._getEditorValues(sensor.deviceName);
    const staged = this._getStagedChanges(sensor.deviceName);
    const multiProgSameDay = this._hasMultipleProgramsSameDay(sensor.deviceName);

    // Group by day
    const daySelections = {};
    for (const key of cells) {
      const [day, prog] = key.split('-').map(Number);
      if (!daySelections[day]) daySelections[day] = [];
      daySelections[day].push(prog);
    }

    // For overlap check when times are editable
    if (!multiProgSameDay) {
      const dayNames = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
      const overlapDays = [];
      
      for (const [dayStr, programs] of Object.entries(daySelections)) {
        const day = parseInt(dayStr);
        for (const program of programs) {
          const overlaps = this._checkOverlaps(
            sensor, day, program,
            editorValues.startTime,
            editorValues.endTime
          );
          if (overlaps.length > 0 && !overlapDays.includes(dayNames[day])) {
            overlapDays.push(dayNames[day]);
          }
        }
      }

      if (overlapDays.length > 0) {
        this._showStatus(`Schedules in ${overlapDays.join(', ')} overlap. Adjust times.`, true, sensor.deviceName);
        return;
      }
    }

    // Stage changes for each selected cell
    for (const key of cells) {
      const [dayStr, progStr] = key.split('-');
      const day = parseInt(dayStr);
      const prog = parseInt(progStr);
      
      // If multi-program same day, preserve existing times from API or previous stage
      let stagedData;
      if (multiProgSameDay) {
        const existing = staged.get(key) || this._getCellDataFromMatrix(sensor, day, prog);
        stagedData = {
          enabled: editorValues.enabled,
          startTime: existing.startTime,
          endTime: existing.endTime,
          workSec: editorValues.workSec,
          pauseSec: editorValues.pauseSec,
          level: editorValues.level
        };
      } else {
        stagedData = {
          enabled: editorValues.enabled,
          startTime: editorValues.startTime,
          endTime: editorValues.endTime,
          workSec: editorValues.workSec,
          pauseSec: editorValues.pauseSec,
          level: editorValues.level
        };
      }
      
      staged.set(key, stagedData);
    }

    cells.clear();
    this._showStatus(`✓ Staged ${Object.values(daySelections).flat().length} cell(s). Click "Push" when ready.`, false, sensor.deviceName);
    setTimeout(() => this.render(), 0);
  }

  // Clear selected cells (stage as disabled)
  _clearSelectedSchedules(sensor) {
    const cells = this._getSelectedCells(sensor.deviceName);
    if (cells.size === 0) {
      this._showStatus('No cells selected to clear.', true, sensor.deviceName);
      return;
    }

    const staged = this._getStagedChanges(sensor.deviceName);
    let count = 0;

    for (const key of cells) {
      const [dayStr, progStr] = key.split('-');
      const day = parseInt(dayStr);
      const prog = parseInt(progStr);
      
      const existing = staged.get(key) || this._getCellDataFromMatrix(sensor, day, prog);
      staged.set(key, {
        enabled: false,
        startTime: existing.startTime || '00:00',
        endTime: existing.endTime || '23:59',
        workSec: existing.workSec || 10,
        pauseSec: existing.pauseSec || 120,
        level: existing.level || 'A'
      });
      count++;
    }

    cells.clear();
    this._showStatus(`✓ Staged ${count} cell(s) for clearing. Click "Push" when ready.`, false, sensor.deviceName);
    setTimeout(() => this.render(), 0);
  }

  _resetAllSchedules(sensor) {
    const staged = this._getStagedChanges(sensor.deviceName);
    let count = 0;

    for (let day = 0; day < 7; day++) {
      for (let prog = 1; prog <= 5; prog++) {
        const key = `${day}-${prog}`;
        staged.set(key, {
          enabled: false,
          startTime: '00:00',
          endTime: '23:59',
          workSec: 10,
          pauseSec: 120,
          level: 'A'
        });
        count++;
      }
    }

    this._showStatus(`✓ Staged ${count} cells for reset to defaults. Click "Sync Staged" to apply.`, false, sensor.deviceName);
    setTimeout(() => this.render(), 0);
  }

  _getCellDataFromMatrix(sensor, day, prog) {
    const matrix = sensor.matrix || {};
    const dayNames = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
    const dayData = matrix[dayNames[day]] || {};
    const progData = dayData[`program_${prog}`] || {};
    
    const levelMap = {1: 'A', 2: 'B', 3: 'C', 'A': 'A', 'B': 'B', 'C': 'C'};
    
    return {
      enabled: progData.enabled === true || progData.enabled === 1,
      startTime: (progData.start_time || progData.start || '00:00').substring(0, 5),
      endTime: (progData.end_time || progData.end || '23:59').substring(0, 5),
      workSec: progData.work || progData.work_sec || 10,
      pauseSec: progData.pause || progData.pause_sec || 120,
      level: levelMap[progData.level] || 'A'
    };
  }

  // Get cell data considering staged changes
  _getCellData(sensor, day, prog) {
    const key = `${day}-${prog}`;
    const staged = this._getStagedChanges(sensor.deviceName);
    
    if (staged.has(key)) {
      return { ...staged.get(key), isStaged: true };
    }
    
    return { ...this._getCellDataFromMatrix(sensor, day, prog), isStaged: false };
  }

  // PUSH: Send all staged changes to API using FAST batch service
  async _pushStagedChanges(sensor) {
    const staged = this._getStagedChanges(sensor.deviceName);
    if (staged.size === 0) {
      this._showStatus('No staged changes to push.', true, sensor.deviceName);
      return;
    }

    const deviceId = sensor.deviceId;
    if (!deviceId) {
      this._showStatus('Device ID not found. Cannot push.', true, sensor.deviceName);
      return;
    }

    this._isSaving = true;
    this._showStatus('Pushing changes to Aroma-Link...', false, sensor.deviceName);
    this.render();

    try {
      // Group staged changes by day and build full schedule for each day
      const changesByDay = {};
      for (const [key, data] of staged.entries()) {
        const [day, prog] = key.split('-').map(Number);
        if (!changesByDay[day]) changesByDay[day] = {};
        changesByDay[day][prog] = data;
      }

      // Build complete schedules for each day (merge staged with existing)
      const schedules = [];
      const matrix = sensor.matrix || {};
      const dayNames = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

      for (const [dayStr, programs] of Object.entries(changesByDay)) {
        const day = parseInt(dayStr);
        const dayName = dayNames[day];
        const dayMatrix = matrix[dayName] || {};
        
        // Build 5 programs for this day
        const dayPrograms = [];
        for (let prog = 1; prog <= 5; prog++) {
          // Use staged data if available, otherwise use existing matrix data
          const stagedData = programs[prog];
          const existingData = dayMatrix[`program${prog}`] || {};
          
          if (stagedData) {
            dayPrograms.push({
              startTime: stagedData.startTime || '00:00',
              endTime: stagedData.endTime || '23:59',
              enabled: stagedData.enabled ? 1 : 0,
              level: stagedData.level || 'A',
              workSec: stagedData.workSec || 10,
              pauseSec: stagedData.pauseSec || 120
            });
          } else {
            // Use existing data from matrix
            dayPrograms.push({
              startTime: existingData.startTime || '00:00',
              endTime: existingData.endTime || '23:59',
              enabled: existingData.enabled ? 1 : 0,
              level: existingData.level || 'A',
              workSec: existingData.workSec || 10,
              pauseSec: existingData.pauseSec || 120
            });
          }
        }
        
        schedules.push({ day: day, programs: dayPrograms });
      }

      const totalDays = schedules.length;
      console.log(`[AromaLink] Using batch service for ${totalDays} day(s)`);
      this._showStatus(`Saving ${totalDays} day(s)...`, false, sensor.deviceName);

      // FAST: Single service call that batches everything!
      await this._hass.callService('aroma_link_integration', 'save_schedule_batch', {
        device_id: deviceId,
        schedules: schedules
      });
      
      // Clear staged changes
      staged.clear();
      
      this._showStatus(`✓ Saved! Refreshing schedule...`, false, sensor.deviceName);
      console.log(`[AromaLink] Batch save complete, refreshing matrix...`);
      
      // Suppress renders during pull to avoid scroll jump
      this._isOperationInProgress = true;
      
      // Pull fresh schedule data from API to update the matrix display
      await this._pullSchedule(sensor);
      
      // Small delay to let HA propagate state updates
      await new Promise(resolve => setTimeout(resolve, 500));
      
      this._isOperationInProgress = false;
      this._isSaving = false;
      this._showStatus(`✓ Saved ${totalDays} day(s) to Aroma-Link`, false, sensor.deviceName);
      console.log(`[AromaLink] Matrix refreshed`);
      
      // Single render after everything is done
      this.render();
      
    } catch (error) {
      console.error('Error pushing changes:', error);
      console.error('Error details:', {
        message: error.message,
        code: error.code,
        stack: error.stack
      });
      this._isOperationInProgress = false;
      this._isSaving = false;
      this._showStatus(`Error: ${error.message || 'Unknown error'}. Check console.`, true, sensor.deviceName);
      this.render();
    }
  }

  // Discard all staged changes
  _discardStagedChanges(deviceName) {
    this._getStagedChanges(deviceName).clear();
    this._showStatus('Discarded all staged changes', false, deviceName);
    setTimeout(() => this.render(), 0);
  }

  _checkOverlaps(sensor, day, editingProgram, newStart, newEnd) {
    const matrix = sensor.matrix || {};
    const dayNames = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
    const dayData = matrix[dayNames[day]] || {};
    const overlaps = [];
    
    const newStartMin = this._timeToMinutes(newStart);
    const newEndMin = this._timeToMinutes(newEnd);
    
    for (let p = 1; p <= 5; p++) {
      if (p === editingProgram) continue;
      
      const progData = dayData[`program_${p}`] || {};
      if (!progData.enabled) continue;
      
      const existStart = (progData.start_time || progData.start || '00:00').substring(0, 5);
      const existEnd = (progData.end_time || progData.end || '23:59').substring(0, 5);
      const existStartMin = this._timeToMinutes(existStart);
      const existEndMin = this._timeToMinutes(existEnd);
      
      if (newStartMin < existEndMin && newEndMin > existStartMin) {
        overlaps.push({ program: p, start: existStart, end: existEnd });
      }
    }
    
    return overlaps;
  }

  _timeToMinutes(time) {
    const [h, m] = time.split(':').map(Number);
    return h * 60 + m;
  }

  async _pullSchedule(sensor) {
    const syncButton = `button.${sensor.deviceName}_sync_schedules`;
    if (this._hass.states[syncButton]) {
      this._showStatus('Pulling schedule from Aroma-Link...', false, sensor.deviceName);
      await this._hass.callService('button', 'press', { entity_id: syncButton });
      this._showStatus('✓ Schedule pulled', false, sensor.deviceName);
    }
  }

  async _copyScheduleFrom(targetSensor, sourceSensorName) {
    const sensors = this._findScheduleSensors();
    const sourceSensor = sensors.find(s => s.deviceName === sourceSensorName);
    
    if (!sourceSensor) {
      this._showStatus('Source device not found.', true, targetSensor.deviceName);
      return;
    }

    const staged = this._getStagedChanges(targetSensor.deviceName);
    const sourceMatrix = sourceSensor.matrix || {};
    const dayNames = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
    let count = 0;

    for (let day = 0; day < 7; day++) {
      const dayData = sourceMatrix[dayNames[day]] || {};
      for (let prog = 1; prog <= 5; prog++) {
        const key = `${day}-${prog}`;
        const progData = dayData[`program_${prog}`] || {};
        const levelMap = {1: 'A', 2: 'B', 3: 'C', 'A': 'A', 'B': 'B', 'C': 'C'};
        
        staged.set(key, {
          enabled: progData.enabled === true || progData.enabled === 1,
          startTime: (progData.start_time || progData.start || '00:00').substring(0, 5),
          endTime: (progData.end_time || progData.end || '23:59').substring(0, 5),
          workSec: progData.work || progData.work_sec || 10,
          pauseSec: progData.pause || progData.pause_sec || 120,
          level: levelMap[progData.level] || 'A'
        });
        count++;
      }
    }

    this._showStatus(`✓ Staged ${count} schedules from ${sourceSensorName}. Click "Push" to apply.`, false, targetSensor.deviceName);
    this.render();
  }

  setConfig(config) {
    this._config = config;
  }

  _getStateFingerprint(hass) {
    if (!hass) return '';
    const parts = [];
    for (const [entityId, state] of Object.entries(hass.states)) {
      if (entityId.includes('aroma_link') || entityId.includes('aromalink')) {
        parts.push(entityId + '=' + state.state + '|' + state.last_updated);
      }
    }
    return parts.join(';');
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this.render();
      return;
    }

    // Only re-render if aroma-link entities actually changed
    const fingerprint = this._getStateFingerprint(hass);
    if (fingerprint === this._lastStateFingerprint) {
      return;
    }
    this._lastStateFingerprint = fingerprint;

    // Don't re-render if an input field is focused
    const activeEl = this.shadowRoot?.activeElement;
    if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'SELECT' || activeEl.tagName === 'TEXTAREA')) {
      return;
    }
    const docActive = document.activeElement;
    if (docActive === this && this.shadowRoot) {
      const shadowActive = this.shadowRoot.activeElement;
      if (shadowActive && (shadowActive.tagName === 'INPUT' || shadowActive.tagName === 'SELECT' || shadowActive.tagName === 'TEXTAREA')) {
        return;
      }
    }
    this.render();
  }

  _getOilEntities(deviceName) {
    // Find oil-related entities for a device
    const entities = {
      oilLevel: `sensor.${deviceName}_oil_level`,
      oilRemaining: `sensor.${deviceName}_oil_remaining`,
      cumulativeRuntime: `sensor.${deviceName}_cumulative_runtime`,
      bottleCapacity: `number.${deviceName}_oil_bottle_capacity`,
      fillVolume: `number.${deviceName}_oil_fill_volume`,
      measuredRemaining: `number.${deviceName}_oil_remaining_measured`,
      fillDate: `text.${deviceName}_oil_fill_date`,
      calibrationState: `select.${deviceName}_oil_calibration_state`,
      calibrationToggle: `button.${deviceName}_oil_calibration_toggle`,
      calibrationFinalize: `button.${deviceName}_oil_calibration_finalize`,
      refillKeepCalibration: `button.${deviceName}_oil_refill_keep_calibration`,
      manualStart: `number.${deviceName}_oil_manual_start_volume`,
      manualEnd: `number.${deviceName}_oil_manual_end_volume`,
      manualRuntime: `number.${deviceName}_oil_manual_runtime_hours`,
      manualRate: `number.${deviceName}_oil_manual_rate`,
      manualApply: `button.${deviceName}_oil_manual_override`,
    };
    
    // Check which entities exist
    const available = {};
    for (const [key, entityId] of Object.entries(entities)) {
      if (this._hass.states[entityId]) {
        available[key] = entityId;
      }
    }
    return available;
  }

  _renderOilSection(sensor) {
    const entities = this._getOilEntities(sensor.deviceName);
    
    // If no oil entities exist, don't render the section
    if (!entities.oilLevel && !entities.oilRemaining) {
      return '';
    }

    const oilLevelState = this._hass.states[entities.oilLevel];
    const oilRemainingState = this._hass.states[entities.oilRemaining];
    const runtimeState = this._hass.states[entities.cumulativeRuntime];
    const capacityState = this._hass.states[entities.bottleCapacity];
    const fillVolumeState = this._hass.states[entities.fillVolume];
    const measuredState = this._hass.states[entities.measuredRemaining];
    const fillDateState = this._hass.states[entities.fillDate];
    const calibrationStateEntity = this._hass.states[entities.calibrationState];
    const manualStartState = this._hass.states[entities.manualStart];
    const manualEndState = this._hass.states[entities.manualEnd];
    const manualRuntimeState = this._hass.states[entities.manualRuntime];
    const manualRateState = this._hass.states[entities.manualRate];

    const oilLevel = oilLevelState?.state !== 'unknown' && oilLevelState?.state !== 'unavailable' 
      ? parseFloat(oilLevelState?.state) : null;
    const oilRemaining = oilRemainingState?.state !== 'unknown' && oilRemainingState?.state !== 'unavailable'
      ? parseFloat(oilRemainingState?.state) : null;
    const fallbackCalState = oilRemainingState?.attributes?.calibration_state || (oilRemainingState?.attributes?.calibrated ? 'Calibrated' : 'Idle');
    const calibrationState = calibrationStateEntity?.state || fallbackCalState;
    const isCalibrated = oilRemainingState?.attributes?.calibrated || false;
    const bottleCapacity = parseFloat(
      this._readOilInputValue(sensor.deviceName, 'bottleCapacity', capacityState?.state)
    ) || 100;
    const fillVolume = parseFloat(
      this._readOilInputValue(sensor.deviceName, 'fillVolume', fillVolumeState?.state)
    ) || 100;
    const measuredRemaining = parseFloat(
      this._readOilInputValue(sensor.deviceName, 'measuredRemaining', measuredState?.state)
    ) || 0;
    // Handle "unknown" or "unavailable" states for date input (must be empty string or valid date)
    const rawFillDate = this._readOilInputValue(sensor.deviceName, 'fillDate', fillDateState?.state || '');
    // Strict validation: only accept yyyy-MM-dd format, reject everything else including unknown/unavailable/null/undefined
    const fillDate = (rawFillDate && typeof rawFillDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(rawFillDate)) ? rawFillDate : '';
    const manualStart = parseFloat(
      this._readOilInputValue(sensor.deviceName, 'manualStart', manualStartState?.state)
    ) || 0;
    const manualEnd = parseFloat(
      this._readOilInputValue(sensor.deviceName, 'manualEnd', manualEndState?.state)
    ) || 0;
    const manualRuntime = parseFloat(
      this._readOilInputValue(sensor.deviceName, 'manualRuntime', manualRuntimeState?.state)
    ) || 0;
    const manualRate = parseFloat(
      this._readOilInputValue(sensor.deviceName, 'manualRate', manualRateState?.state)
    ) || 0;
    
    // Runtime info
    const formattedRuntime = runtimeState?.attributes?.formatted_runtime || '0h 0m 0s';
    const completedCycles = runtimeState?.attributes?.completed_cycles || 0;
    const trackingActive = runtimeState?.attributes?.tracking_active || false;
    
    // Estimated time remaining
    const daysRemaining = oilRemainingState?.attributes?.estimated_days_remaining_schedule;
    
    // Usage rate
    const usagePerHour = oilRemainingState?.attributes?.usage_rate_ml_per_hour;

    // Bottle fill percentage for visual
    const fillPercent = oilLevel !== null ? Math.min(100, Math.max(0, oilLevel)) : 50;
    
    // Color based on level
    let levelColor = '#4caf50'; // green
    if (fillPercent < 25) levelColor = '#f44336'; // red
    else if (fillPercent < 50) levelColor = '#ff9800'; // orange
    else if (fillPercent < 75) levelColor = '#8bc34a'; // lime

    const showSummaryValue = (val, suffix = '') => {
      if (val === null || val === undefined || isNaN(val)) return 'n/a';
      return `${val}${suffix}`;
    };

    const daysRemainingDisplay = (daysRemaining !== null && daysRemaining !== undefined)
      ? `${daysRemaining} days`
      : 'n/a';
    const usageDisplay = usagePerHour ? `${usagePerHour.toFixed(2)} ml/hr` : 'n/a';
    const remainingDisplay = oilRemaining !== null ? `${Math.round(oilRemaining)} ml` : 'n/a';

    const toggleLabel = (() => {
      if (calibrationState === 'Running') return 'End Calibration';
      if (calibrationState === 'Ready to Finalize') return 'Resume Calibration';
      return 'Start Calibration Measurement';
    })();

    const consumed = fillVolume - measuredRemaining;
    const minConsumed = fillVolume * 0.10;
    const runtimeHours = parseFloat(runtimeState?.attributes?.runtime_hours) || 0;
    const canFinalize = calibrationState === 'Ready to Finalize'
      && fillVolume > 0
      && measuredRemaining >= 0
      && consumed >= minConsumed
      && consumed > 0
      && runtimeHours > 0;

    const canManualApply = manualRate > 0 || (manualStart > 0 && manualEnd >= 0 && manualRuntime > 0 && manualStart > manualEnd);

    const measuredDisabled = calibrationState !== 'Ready to Finalize';
    const calibrationBadge = calibrationState === 'Running'
      ? '<span class="calibration-badge running">Calibration Running</span>'
      : calibrationState === 'Ready to Finalize'
        ? '<span class="calibration-badge ready">Ready to Finalize</span>'
        : calibrationState === 'Calibrated'
          ? '<span class="calibration-badge calibrated">Calibrated</span>'
          : '<span class="calibration-badge idle">Not Calibrated</span>';

    const panelOpen = this._getOilPanelOpen(sensor.deviceName);

    return `
      <div class="oil-section" data-device="${sensor.deviceName}">
        <div class="oil-header">
          <span class="section-title">🛢️ Oil Level</span>
          ${calibrationBadge}
        </div>
        
        <div class="oil-content">
          <!-- LEFT: Bottle + Summary -->
          <div class="oil-left">
            <div class="bottle-container">
              <div class="bottle">
                <div class="bottle-fill" style="height: ${fillPercent}%; background: ${levelColor};"></div>
                <div class="bottle-label">
                  ${oilLevel !== null ? `${Math.round(oilLevel)}%` : '?'}
                </div>
              </div>
            </div>
            
            <div class="oil-summary">
              <div class="summary-row">
                <span class="summary-label">Remaining Oil</span>
                <span class="summary-value">${isCalibrated ? remainingDisplay : 'n/a'}</span>
              </div>
              <div class="summary-row">
                <span class="summary-label">Estimated Days Remaining</span>
                <span class="summary-value">${isCalibrated ? daysRemainingDisplay : 'n/a'}</span>
              </div>
              <div class="summary-row">
                <span class="summary-label">Consumption Rate (ml/hr)</span>
                <span class="summary-value">${isCalibrated ? usageDisplay : 'n/a'}</span>
              </div>
              <div class="summary-row">
                <span class="summary-label">Fill Volume</span>
                <span class="summary-value">${showSummaryValue(fillVolume, ' ml')}</span>
              </div>
              <div class="summary-row">
                <span class="summary-label">Fill Date</span>
                <span class="summary-value">${fillDate || 'n/a'}</span>
              </div>
            </div>
          </div>
          
          <!-- RIGHT: Calibration + Tracking -->
          <div class="oil-right">
            <details class="calibration-panel" data-device="${sensor.deviceName}" ${panelOpen ? 'open' : ''}>
              <summary>Calibration & Tracking</summary>
              <div class="calibration-content">
                
                <!-- SECTION: Current Status -->
                <div class="cal-section">
                  <div class="cal-section-title">📊 Current Status</div>
                  <div class="cal-section-grid">
                    <div class="stat-item">
                      <span class="stat-label">Runtime</span>
                      <span class="stat-value">${formattedRuntime}</span>
                    </div>
                    <div class="stat-item">
                      <span class="stat-label">Cycles</span>
                      <span class="stat-value">${completedCycles}</span>
                    </div>
                    <div class="stat-item">
                      <span class="stat-label">Tracking</span>
                      <span class="stat-value ${trackingActive ? 'active' : 'inactive'}">${trackingActive ? 'Active' : 'Inactive'}</span>
                    </div>
                  </div>
                </div>

                <!-- SECTION: Bottle Settings -->
                <div class="cal-section">
                  <div class="cal-section-title">🍶 Bottle Settings</div>
                  <div class="cal-section-fields">
                    <div class="calibration-row">
                      <label>Capacity</label>
                      <div class="input-group">
                        <input type="number" class="oil-input" id="oil-bottleCapacity-${sensor.deviceName}" data-oil="bottleCapacity" data-device="${sensor.deviceName}" 
                               value="${bottleCapacity}" min="10" max="1000" step="5">
                        <span>ml</span>
                      </div>
                    </div>
                    <div class="calibration-row">
                      <label>Fill Volume</label>
                      <div class="input-group">
                        <input type="number" class="oil-input" id="oil-fillVolume-${sensor.deviceName}" data-oil="fillVolume" data-device="${sensor.deviceName}" 
                               value="${fillVolume}" min="0" max="1000" step="1">
                        <span>ml</span>
                      </div>
                    </div>
                    <div class="calibration-row">
                      <label>Fill Date</label>
                      <div class="input-group">
                        <input type="date" class="oil-input date" id="oil-fillDate-${sensor.deviceName}" data-oil="fillDate" data-device="${sensor.deviceName}" 
                               value="${fillDate}">
                      </div>
                    </div>
                  </div>
                  <div class="calibration-actions secondary">
                    <button class="oil-btn small-btn" data-action="oil-refill" data-device="${sensor.deviceName}">
                      🔄 Refill (Keep Calibration)
                    </button>
                  </div>
                </div>

                <!-- SECTION: Calibration -->
                <div class="cal-section">
                  <div class="cal-section-title">📏 Calibration</div>
                  <div class="cal-section-fields">
                    <div class="calibration-row">
                      <label>Measured Remaining</label>
                      <div class="input-group">
                        <input type="number" class="oil-input" id="oil-measuredRemaining-${sensor.deviceName}" data-oil="measuredRemaining" data-device="${sensor.deviceName}" 
                               value="${measuredRemaining}" min="0" max="1000" step="1" ${measuredDisabled ? 'disabled' : ''}>
                        <span>ml</span>
                      </div>
                    </div>
                  </div>
                  <div class="calibration-actions">
                    <button class="oil-btn fill-btn" data-action="oil-toggle" data-device="${sensor.deviceName}">
                      ${toggleLabel}
                    </button>
                    <button class="oil-btn calibrate-btn ${canFinalize ? '' : 'disabled'}" data-action="oil-finalize" data-device="${sensor.deviceName}">
                      Finalize
                    </button>
                  </div>
                  ${calibrationState === 'Ready to Finalize' && !canFinalize ? `
                    <div class="calibration-warning">
                      ⚠️ Measure remaining oil. At least 10% must be consumed.
                    </div>
                  ` : ''}
                </div>

                <!-- SECTION: Manual Override -->
                <div class="cal-section manual-override">
                  <div class="cal-section-title">✏️ Manual Override</div>
                  <div class="cal-section-fields">
                    <div class="calibration-row">
                      <label>Start Vol</label>
                      <div class="input-group">
                        <input type="number" class="oil-input" id="oil-manualStart-${sensor.deviceName}" data-oil="manualStart" data-device="${sensor.deviceName}" 
                               value="${manualStart}" min="0" max="1000" step="1">
                        <span>ml</span>
                      </div>
                    </div>
                    <div class="calibration-row">
                      <label>End Vol</label>
                      <div class="input-group">
                        <input type="number" class="oil-input" id="oil-manualEnd-${sensor.deviceName}" data-oil="manualEnd" data-device="${sensor.deviceName}" 
                               value="${manualEnd}" min="0" max="1000" step="1">
                        <span>ml</span>
                      </div>
                    </div>
                    <div class="calibration-row">
                      <label>Runtime</label>
                      <div class="input-group">
                        <input type="number" class="oil-input" id="oil-manualRuntime-${sensor.deviceName}" data-oil="manualRuntime" data-device="${sensor.deviceName}" 
                               value="${manualRuntime}" min="0" max="10000" step="0.1">
                        <span>hr</span>
                      </div>
                    </div>
                    <div class="calibration-row">
                      <label>Rate</label>
                      <div class="input-group">
                        <input type="number" class="oil-input" id="oil-manualRate-${sensor.deviceName}" data-oil="manualRate" data-device="${sensor.deviceName}" 
                               value="${manualRate}" min="0" max="1000" step="0.01">
                        <span>ml/hr</span>
                      </div>
                    </div>
                  </div>
                  <div class="calibration-actions secondary">
                    <button class="oil-btn small-btn ${canManualApply ? '' : 'disabled'}" data-action="oil-manual-apply" data-device="${sensor.deviceName}">
                      Apply Override
                    </button>
                  </div>
                </div>

              </div>
            </details>
          </div>
        </div>
      </div>
    `;
  }

  _findScheduleSensors() {
    if (!this._hass) return [];
    
    const sensors = [];
    for (const [entityId, state] of Object.entries(this._hass.states)) {
      if (entityId.startsWith('sensor.') && entityId.endsWith('_schedule_matrix')) {
        const attrs = state.attributes || {};
        // The sensor provides "matrix" attribute
        // Include even if matrix is empty (schedules not yet fetched)
        const deviceName = entityId.replace('sensor.', '').replace('_schedule_matrix', '');
        sensors.push({
          entityId,
          deviceName,
          deviceId: attrs.device_id,
          matrix: attrs.matrix || {}
        });
      }
    }
    return sensors;
  }

  render() {
    if (!this._hass) return;
    
    // Skip renders during operations (like push/pull) to prevent scroll jumps
    if (this._isOperationInProgress) {
      console.log('[AromaLink] Skipping render - operation in progress');
      return;
    }
    
    // Debounce rapid renders
    if (this._renderPending) return;
    this._renderPending = true;
    
    // Use microtask to batch multiple render calls
    queueMicrotask(() => {
      this._renderPending = false;
      this._doRender();
    });
  }
  
  _doRender() {
    if (!this._hass) return;
    
    // Save scroll position before re-render - multiple methods for Safari compatibility
    const savedScrollTop = window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0;
    const savedScrollLeft = window.scrollX || window.pageXOffset || document.documentElement.scrollLeft || 0;
    
    // Also save this card's position relative to viewport
    const cardRect = this.getBoundingClientRect?.() || { top: 0 };
    const cardOffsetFromTop = cardRect.top;
    
    // For Safari: store if we were near top of page
    const wasAtTop = savedScrollTop < 50;
    
    const sensors = this._findScheduleSensors();
    
    if (sensors.length === 0) {
      this.shadowRoot.innerHTML = `
        <ha-card>
          <div style="padding: 20px; text-align: center; color: #999;">
            No Aroma-Link schedule sensors found.<br>
            <small>Looking for sensor.*_schedule_matrix</small>
          </div>
        </ha-card>
      `;
      return;
    }

    const dayLabels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const days = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
    const programs = [1, 2, 3, 4, 5];
    const programLabels = { 1: 'P1', 2: 'P2', 3: 'P3', 4: 'P4', 5: 'Night Owl' };
    const today = new Date().getDay();
    const todayIndex = today;

    const cardsHtml = sensors.map(sensor => {
      const matrix = sensor.matrix || {};
      const cells = this._getSelectedCells(sensor.deviceName);
      const selectionCount = cells.size;
      const editorValues = this._getEditorValues(sensor.deviceName);
      const timerState = this._getTimerState(sensor.deviceName);
      const timedHours = this._getTimedRunHours(sensor.deviceName);
      const hasStagedChanges = this._hasStagedChanges(sensor.deviceName);
      const stagedCount = this._getStagedChanges(sensor.deviceName).size;

      const controls = {
        power: `switch.${sensor.deviceName}_power`,
        fan: `switch.${sensor.deviceName}_fan`,
        workNumber: `number.${sensor.deviceName}_work_time`,
        pauseNumber: `number.${sensor.deviceName}_pause_time`,
        run: `button.${sensor.deviceName}_run`,
        saveSettings: `button.${sensor.deviceName}_save_settings`
      };

      const powerState = this._hass.states[controls.power];
      const fanState = this._hass.states[controls.fan];
      const workState = this._hass.states[controls.workNumber];
      const pauseState = this._hass.states[controls.pauseNumber];
      const isPowerOn = powerState?.state === 'on';
      const isFanOn = fanState?.state === 'on';

      const showStatus = this._statusMessage && this._statusDevice === sensor.deviceName;
      
      // Other sensors for copy dropdown
      const otherSensors = sensors.filter(s => s.deviceName !== sensor.deviceName);

      return `
        <div class="diffuser-card" data-device="${sensor.deviceName}">
          <div class="card-header">
            <span class="title">🌸 ${this._titleCase(sensor.deviceName)} Diffuser</span>
          </div>
          
          <!-- MANUAL CONTROLS ROW -->
          <div class="compact-controls">
            <div class="control-group power-group">
              <button class="icon-btn power-btn ${isPowerOn ? 'active' : ''}" data-action="toggle-power" data-device="${sensor.deviceName}" title="Power">
                <ha-icon class="icon" icon="mdi:power"></ha-icon>
                <span class="label">Power</span>
                <span class="state">${isPowerOn ? 'On' : 'Off'}</span>
              </button>
              <button class="icon-btn fan-btn ${isFanOn ? 'active' : ''}" data-action="toggle-fan" data-device="${sensor.deviceName}" title="Fan">
                <ha-icon class="icon" icon="mdi:fan"></ha-icon>
                <span class="label">Fan</span>
                <span class="state">${isFanOn ? 'On' : 'Off'}</span>
              </button>
            </div>
            
            <div class="control-group settings">
              <label>Work <input type="number" class="compact-input" id="workSec-${sensor.deviceName}" data-field="workSec" data-device="${sensor.deviceName}" value="${editorValues.workSec}" min="1" max="999"><span class="unit">s</span></label>
              <label>Pause <input type="number" class="compact-input" id="pauseSec-${sensor.deviceName}" data-field="pauseSec" data-device="${sensor.deviceName}" value="${editorValues.pauseSec}" min="1" max="9999"><span class="unit">s</span></label>
            </div>
            
            <div class="control-group run-options">
              <div class="run-panel" data-device="${sensor.deviceName}">
                <div class="run-header">Run Timed</div>
                ${timerState ? `
                  <div class="run-buttons">
                    <span class="countdown" data-device="${sensor.deviceName}">⏱️ ${this._formatCountdown(timerState.remainingSeconds)}</span>
                    <button class="cancel-btn" data-action="cancel-timer" data-device="${sensor.deviceName}">Cancel</button>
                  </div>
                ` : `
                  <div class="run-buttons">
                    <button class="run-btn timed" data-action="run-timed" data-device="${sensor.deviceName}">Start</button>
                    <input type="number" class="hours-input" id="timedHours-${sensor.deviceName}" data-field="timedHours" data-device="${sensor.deviceName}" value="${timedHours}" min="0.5" max="24" step="0.5" title="Hours">
                    <span class="hours-label">hr</span>
                  </div>
                `}
              </div>
            </div>
          </div>
          
          <!-- SCHEDULE SECTION -->
          <div class="schedule-section">
            <div class="schedule-header">
              <span class="section-title">Weekly Schedule</span>
              ${otherSensors.length > 0 ? `
                <div class="copy-dropdown">
                  <select class="copy-select" id="copyFrom-${sensor.deviceName}" data-device="${sensor.deviceName}">
                    <option value="">Copy from...</option>
                    ${otherSensors.map(s => `<option value="${s.deviceName}">${s.deviceName.replace(/_/g, ' ')}</option>`).join('')}
                  </select>
                </div>
              ` : ''}
              <button class="chip-btn pull-btn" data-action="pull" data-device="${sensor.deviceName}">Pull Schedule</button>
              <button class="chip-btn reset-btn" data-action="reset-all" data-device="${sensor.deviceName}">Reset All</button>
            </div>
            
            <!-- Legend -->
            <div class="legend">
              <span class="legend-item"><span class="legend-dot enabled"></span>Enabled</span>
              <span class="legend-item"><span class="legend-dot has-settings"></span>Disabled</span>
              <span class="legend-item"><span class="legend-dot staged"></span>Staged</span>
              <span class="legend-item"><span class="legend-dot selected"></span>Selected</span>
            </div>

            <!-- Grid with horizontal scroll wrapper -->
            <div class="schedule-grid-wrapper">
            <div class="schedule-grid" data-device="${sensor.deviceName}">
              <div class="grid-cell header corner"></div>
              ${dayLabels.map((d, idx) => {
                const allProgsSelected = [1,2,3,4,5].every(p => cells.has(`${idx}-${p}`));
                return `
                  <div class="grid-cell header day-header ${idx === todayIndex ? 'today' : ''} ${allProgsSelected ? 'col-selected' : ''}" 
                       data-action="select-day" data-day="${idx}" data-device="${sensor.deviceName}">
                    ${d}
                  </div>
                `;
              }).join('')}
              
              ${programs.map(prog => {
                const allDaysSelected = [0,1,2,3,4,5,6].every(d => cells.has(`${d}-${prog}`));
                return `
                  <div class="grid-cell header program-label ${allDaysSelected ? 'row-selected' : ''}${prog === 5 ? ' night-owl' : ''}"
                       data-action="select-row" data-program="${prog}" data-device="${sensor.deviceName}">
                    ${programLabels[prog]}
                  </div>
                  ${days.map((day, dayIdx) => {
                    const cellData = this._getCellData(sensor, dayIdx, prog);
                    const isEnabled = cellData.enabled;
                    const isStaged = cellData.isStaged;
                    const hasSettings = cellData.startTime !== '00:00' || cellData.endTime !== '23:59' || cellData.workSec > 0;
                    const isSelected = this._isCellSelected(sensor.deviceName, dayIdx, prog);
                    const isToday = dayIdx === todayIndex;
                    
                    let cellClass = isEnabled ? 'enabled' : (hasSettings ? 'has-settings' : 'empty');
                    if (isStaged) cellClass += ' staged';
                    
                    return `
                      <div class="grid-cell schedule-cell ${cellClass} ${isSelected ? 'selected' : ''} ${isToday ? 'today-col' : ''}" 
                           data-action="toggle-cell" data-day="${dayIdx}" data-program="${prog}" data-device="${sensor.deviceName}">
                        ${isEnabled || hasSettings ? `
                          <span class="cell-start">${cellData.startTime}</span>
                          <span class="cell-end">${cellData.endTime}</span>
                          <span class="cell-work">${cellData.workSec}/${cellData.pauseSec}</span>
                          <span class="cell-level">[${cellData.level}]</span>
                        ` : '<span class="off-label">OFF</span>'}
                      </div>
                    `;
                  }).join('')}
                `;
              }).join('')}
            </div>
            </div>
            
            <!-- Selection actions -->
            <div class="selection-bar">
              <button class="chip-btn" data-action="select-all" data-device="${sensor.deviceName}">Select All</button>
              <button class="chip-btn" data-action="clear-selection" data-device="${sensor.deviceName}" ${selectionCount === 0 ? 'disabled' : ''}>
                Clear (${selectionCount})
              </button>
              ${hasStagedChanges ? `
                <span class="staged-badge">📝 ${stagedCount} staged</span>
                <button class="chip-btn discard-btn" data-action="discard-staged" data-device="${sensor.deviceName}">Discard</button>
              ` : ''}
            </div>
            
            ${showStatus ? `
              <div class="status-message ${this._statusMessage.isError ? 'error' : 'success'}">
                ${this._statusMessage.text}
              </div>
            ` : ''}
            
            <!-- Editor -->
            ${(() => {
              const multiProgSameDay = this._hasMultipleProgramsSameDay(sensor.deviceName);
              const timeDisabled = multiProgSameDay ? 'disabled' : '';
              const timeDisabledClass = multiProgSameDay ? 'time-disabled' : '';
              return `
            <div class="editor-section ${selectionCount === 0 ? 'dimmed' : ''}" data-device="${sensor.deviceName}">
              <div class="editor-header">
                ${selectionCount > 0 
                  ? `Editing ${selectionCount} cell${selectionCount > 1 ? 's' : ''} ${multiProgSameDay ? '(times locked)' : ''}` 
                  : 'Click cells to select, click row labels for rows, click day headers for columns'}
              </div>
              
              <!-- Row 1: Settings -->
              <div class="editor-row-inline">
                <label class="toggle-label">
                  <input type="checkbox" id="enabled-${sensor.deviceName}" data-field="enabled" data-device="${sensor.deviceName}" ${editorValues.enabled ? 'checked' : ''}>
                  <span>Enabled</span>
                </label>
                
                <div class="time-inputs ${timeDisabledClass}">
                  <input type="time" class="time-input" id="startTime-${sensor.deviceName}" data-field="startTime" data-device="${sensor.deviceName}" value="${editorValues.startTime}" ${timeDisabled}>
                  <span>-</span>
                  <input type="time" class="time-input" id="endTime-${sensor.deviceName}" data-field="endTime" data-device="${sensor.deviceName}" value="${editorValues.endTime}" ${timeDisabled}>
                </div>
                
                <div class="num-inputs">
                  <label>Work <input type="number" class="num-input" id="editorWorkSec-${sensor.deviceName}" data-field="editorWorkSec" data-device="${sensor.deviceName}" value="${editorValues.workSec}" min="1" max="999"><span class="unit">s</span></label>
                  <label>Pause <input type="number" class="num-input" id="editorPauseSec-${sensor.deviceName}" data-field="editorPauseSec" data-device="${sensor.deviceName}" value="${editorValues.pauseSec}" min="1" max="9999"><span class="unit">s</span></label>
                </div>
              </div>
              
              <!-- Row 2: Actions -->
              <div class="editor-actions-row">
                <span class="level-group">
                  <label class="level-label">Level</label>
                  <select class="level-select" id="level-${sensor.deviceName}" data-field="level" data-device="${sensor.deviceName}">
                    <option value="A" ${editorValues.level === 'A' ? 'selected' : ''}>A</option>
                    <option value="B" ${editorValues.level === 'B' ? 'selected' : ''}>B</option>
                    <option value="C" ${editorValues.level === 'C' ? 'selected' : ''}>C</option>
                  </select>
                </span>
                <button class="clear-btn ${selectionCount === 0 ? 'disabled' : ''}" 
                        data-action="clear-schedule" data-device="${sensor.deviceName}">Clear Selection</button>
                <button class="stage-btn ${selectionCount === 0 ? 'disabled' : ''}" 
                        data-action="stage" data-device="${sensor.deviceName}">Stage Edits</button>
                <button class="push-btn ${hasStagedChanges ? 'ready' : ''} ${!hasStagedChanges || this._isSaving ? 'disabled' : ''}" 
                        data-action="push" data-device="${sensor.deviceName}">
                  ${this._isSaving ? 'Syncing...' : `Sync Staged (${stagedCount})`}
                </button>
              </div>
            </div>
              `;
            })()}
          </div>
          
          <!-- OIL TRACKING SECTION -->
          ${this._renderOilSection(sensor)}
        </div>
      `;
    }).join('');

    this.shadowRoot.innerHTML = `
      <style>
        ${this._getStyles()}
      </style>
      <ha-card>
        ${cardsHtml}
      </ha-card>
    `;

    this._attachEventListeners();
    
    // Restore scroll position after re-render (fixes Safari/iOS scroll jump)
    // Use triple-RAF for Safari which needs extra frames for layout
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          // Only restore if we had a meaningful scroll position and we're not at the same place
          const currentScroll = window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0;
          const scrollDiff = Math.abs(currentScroll - savedScrollTop);
          
          // If scroll jumped by more than 10px, restore it
          if (scrollDiff > 10 && savedScrollTop > 0) {
            try {
              // Try smooth scroll restoration first
              window.scrollTo({
                top: savedScrollTop,
                left: savedScrollLeft,
                behavior: 'instant'
              });
            } catch (e) {
              // Fallback for older browsers
              window.scrollTo(savedScrollLeft, savedScrollTop);
            }
          } else if (wasAtTop && currentScroll > 100) {
            // Safari sometimes scrolls down when it should stay at top
            window.scrollTo(0, 0);
          }
        });
      });
    });
  }

  _attachEventListeners() {
    const sensors = this._findScheduleSensors();
    
    // FLAG: Track if we're in an interaction to suppress scroll side-effects
    if (!this._interactionFlag) {
      this._interactionFlag = { active: false };
    }
    
    // CARD-LEVEL: Block HA's action-handler from seeing scroll events from our card
    const haCard = this.shadowRoot.querySelector('ha-card');
    if (haCard && !haCard._aromaBlockerAttached) {
      haCard._aromaBlockerAttached = true;
      
      // Block scroll events from bubbling to HA's action-handler
      haCard.addEventListener('scroll', (e) => {
        e.stopPropagation();
        e.stopImmediatePropagation();
      }, { capture: true, passive: true });
      
      // Block wheel events 
      haCard.addEventListener('wheel', (e) => {
        e.stopImmediatePropagation();
      }, { capture: true, passive: true });
      
      // DOCUMENT-LEVEL: Temporarily lock scroll position during interactions
      // This catches scroll events that bypass our element handlers
      if (!window._aromaScrollLockRegistered) {
        window._aromaScrollLockRegistered = true;
        let lastGoodScrollY = window.scrollY;
        let lockTimeout = null;
        
        document.addEventListener('scroll', (e) => {
          // If we're in an active interaction, restore scroll position
          if (this._interactionFlag?.active) {
            window.scrollTo(0, lastGoodScrollY);
            e.stopPropagation();
          } else {
            // Update "good" position when not interacting
            lastGoodScrollY = window.scrollY;
          }
        }, { capture: true, passive: false });
      }
    }
    
    // Global touch handler to prevent Safari scroll jump AND block HA's action-handler
    // Use capture:true to intercept BEFORE HA's handlers see the event
    // MUST NOT be passive to allow preventDefault() to work
    const interactionFlag = this._interactionFlag;
    
    this.shadowRoot.querySelectorAll('button, [data-action], .grid-cell, .select-all-btn, .program-header, .day-header').forEach(el => {
      el.addEventListener('touchstart', (e) => {
        // Mark interaction as active - this triggers scroll lock
        interactionFlag.active = true;
        
        // Only prevent default for actual interactive elements, not inputs
        if (!e.target.closest('input, select, textarea')) {
          e.preventDefault();
          e.stopPropagation();
          e.stopImmediatePropagation(); // Stop HA's action-handler from seeing this
        }
        
        // Clear interaction flag after a short delay (allows render to complete)
        setTimeout(() => { interactionFlag.active = false; }, 300);
      }, { passive: false, capture: true }); // capture:true to run FIRST
      
      el.addEventListener('touchend', (e) => {
        if (!e.target.closest('input, select, textarea')) {
          e.preventDefault();
          e.stopPropagation();
          e.stopImmediatePropagation();
        }
        // Keep flag active a bit longer after touch ends to catch delayed scrolls
        setTimeout(() => { interactionFlag.active = false; }, 300);
      }, { passive: false, capture: true });
      
      // Also block touchmove to prevent HA's gesture detection
      el.addEventListener('touchmove', (e) => {
        if (!e.target.closest('input, select, textarea')) {
          e.stopPropagation();
          e.stopImmediatePropagation();
        }
      }, { passive: true, capture: true });
    });
    
    // Power toggle - applies work/pause settings when turning ON
    this.shadowRoot.querySelectorAll('[data-action="toggle-power"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const deviceName = btn.dataset.device;
        const powerEntity = `switch.${deviceName}_power`;
        const isOn = this._hass.states[powerEntity]?.state === 'on';
        
        if (isOn) {
          // Turning OFF - just turn off
          await this._hass.callService('switch', 'turn_off', { entity_id: powerEntity });
        } else {
          // Turning ON - apply work/pause settings first, then turn on
          const controls = {
            power: powerEntity,
            workNumber: `number.${deviceName}_work_time`,
            pauseNumber: `number.${deviceName}_pause_time`
          };
          await this._applySettingsAndRun(deviceName, controls);
          this._showStatus('Running with current settings', false, deviceName);
        }
      });
    });

    // Fan toggle - just toggles fan
    this.shadowRoot.querySelectorAll('[data-action="toggle-fan"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const deviceName = btn.dataset.device;
        const entity = `switch.${deviceName}_fan`;
        const isOn = this._hass.states[entity]?.state === 'on';
        await this._hass.callService('switch', isOn ? 'turn_off' : 'turn_on', { entity_id: entity });
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="run-timed"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const deviceName = btn.dataset.device;
        const sensor = sensors.find(s => s.deviceName === deviceName);
        if (!sensor?.deviceId) {
          this._showStatus('Device ID not found', true, deviceName);
          return;
        }
        const hours = this._getTimedRunHours(deviceName);
        await this._startTimedRun(deviceName, sensor.deviceId, hours);
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="cancel-timer"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const deviceName = btn.dataset.device;
        const sensor = sensors.find(s => s.deviceName === deviceName);
        await this._cancelTimer(deviceName, sensor?.deviceId);
      });
    });

    // Hours input for timed run
    this.shadowRoot.querySelectorAll('[data-field="timedHours"]').forEach(input => {
      input.addEventListener('change', (e) => {
        this._setTimedRunHours(input.dataset.device, parseFloat(e.target.value) || 6);
      });
    });

    // Work/Pause inputs in compact controls
    this.shadowRoot.querySelectorAll('.compact-input[data-field="workSec"]').forEach(input => {
      input.addEventListener('change', (e) => {
        const editorValues = this._getEditorValues(input.dataset.device);
        editorValues.workSec = parseInt(e.target.value) || 5;
      });
    });

    this.shadowRoot.querySelectorAll('.compact-input[data-field="pauseSec"]').forEach(input => {
      input.addEventListener('change', (e) => {
        const editorValues = this._getEditorValues(input.dataset.device);
        editorValues.pauseSec = parseInt(e.target.value) || 900;
      });
    });

    // Cell clicks - Safari-compatible event handling with scroll lock
    this.shadowRoot.querySelectorAll('[data-action="toggle-cell"]').forEach(cell => {
      cell.addEventListener('click', (e) => {
        interactionFlag.active = true; // Enable scroll lock
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (this._isSaving) { interactionFlag.active = false; return; }
        const day = parseInt(cell.dataset.day);
        const program = parseInt(cell.dataset.program);
        const deviceName = cell.dataset.device;
        const sensor = sensors.find(s => s.deviceName === deviceName);
        if (sensor) this._toggleCell(deviceName, day, program, sensor);
        setTimeout(() => { interactionFlag.active = false; }, 300);
        return false;
      }, { passive: false, capture: true });
    });

    // Row/column selection
    this.shadowRoot.querySelectorAll('[data-action="select-row"]').forEach(row => {
      row.addEventListener('click', (e) => {
        interactionFlag.active = true;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (this._isSaving) { interactionFlag.active = false; return; }
        this._selectProgramRow(row.dataset.device, parseInt(row.dataset.program));
        setTimeout(() => { interactionFlag.active = false; }, 300);
        return false;
      }, { passive: false, capture: true });
    });

    this.shadowRoot.querySelectorAll('[data-action="select-day"]').forEach(header => {
      header.addEventListener('click', (e) => {
        interactionFlag.active = true;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (this._isSaving) { interactionFlag.active = false; return; }
        this._selectDayColumn(header.dataset.device, parseInt(header.dataset.day));
        setTimeout(() => { interactionFlag.active = false; }, 300);
        return false;
      }, { passive: false, capture: true });
    });

    // Select all / clear - Safari-compatible
    this.shadowRoot.querySelectorAll('[data-action="select-all"]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        interactionFlag.active = true;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (this._isSaving) { interactionFlag.active = false; return; }
        this._selectAll(btn.dataset.device);
        setTimeout(() => { interactionFlag.active = false; }, 300);
        return false;
      }, { passive: false });
    });

    this.shadowRoot.querySelectorAll('[data-action="clear-selection"]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        interactionFlag.active = true;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (this._isSaving) { interactionFlag.active = false; return; }
        this._clearSelection(btn.dataset.device);
        setTimeout(() => { interactionFlag.active = false; }, 300);
        return false;
      }, { passive: false });
    });

    // Pull
    this.shadowRoot.querySelectorAll('[data-action="pull"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const sensor = sensors.find(s => s.deviceName === btn.dataset.device);
        if (sensor) await this._pullSchedule(sensor);
      });
    });

    // Reset All
    this.shadowRoot.querySelectorAll('[data-action="reset-all"]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this._isSaving) return;
        if (!confirm('Reset ALL schedules to defaults (00:00-23:59, disabled)? This will stage the changes — you still need to Sync to apply.')) return;
        const sensor = sensors.find(s => s.deviceName === btn.dataset.device);
        if (sensor) this._resetAllSchedules(sensor);
      });
    });

    // Copy dropdown
    this.shadowRoot.querySelectorAll('.copy-select').forEach(select => {
      select.addEventListener('change', async (e) => {
        const targetDevice = select.dataset.device;
        const sourceDevice = e.target.value;
        if (sourceDevice) {
          const targetSensor = sensors.find(s => s.deviceName === targetDevice);
          if (targetSensor) await this._copyScheduleFrom(targetSensor, sourceDevice);
          select.value = '';
        }
      });
    });

    // Editor inputs
    this.shadowRoot.querySelectorAll('[data-field="enabled"]').forEach(input => {
      if (input.type === 'checkbox') {
        input.addEventListener('change', (e) => {
          const editorValues = this._getEditorValues(input.dataset.device);
          editorValues.enabled = e.target.checked;
        });
      }
    });

    this.shadowRoot.querySelectorAll('[data-field="startTime"]').forEach(input => {
      input.addEventListener('change', (e) => {
        const editorValues = this._getEditorValues(input.dataset.device);
        editorValues.startTime = e.target.value;
      });
    });

    this.shadowRoot.querySelectorAll('[data-field="endTime"]').forEach(input => {
      input.addEventListener('change', (e) => {
        const editorValues = this._getEditorValues(input.dataset.device);
        editorValues.endTime = e.target.value;
      });
    });

    this.shadowRoot.querySelectorAll('[data-field="editorWorkSec"]').forEach(input => {
      input.addEventListener('change', (e) => {
        const editorValues = this._getEditorValues(input.dataset.device);
        editorValues.workSec = parseInt(e.target.value) || 5;
      });
    });

    this.shadowRoot.querySelectorAll('[data-field="editorPauseSec"]').forEach(input => {
      input.addEventListener('change', (e) => {
        const editorValues = this._getEditorValues(input.dataset.device);
        editorValues.pauseSec = parseInt(e.target.value) || 900;
      });
    });

    this.shadowRoot.querySelectorAll('[data-field="level"]').forEach(select => {
      select.addEventListener('change', (e) => {
        const editorValues = this._getEditorValues(select.dataset.device);
        editorValues.level = e.target.value;
      });
    });

    // Stage / Clear / Push buttons
    this.shadowRoot.querySelectorAll('[data-action="stage"]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this._isSaving) return;
        const sensor = sensors.find(s => s.deviceName === btn.dataset.device);
        if (sensor) this._stageEdits(sensor);
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="clear-schedule"]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this._isSaving) return;
        const sensor = sensors.find(s => s.deviceName === btn.dataset.device);
        if (sensor) this._clearSelectedSchedules(sensor);
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="push"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (this._isSaving) return;
        const sensor = sensors.find(s => s.deviceName === btn.dataset.device);
        if (sensor) await this._pushStagedChanges(sensor);
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="discard-staged"]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this._discardStagedChanges(btn.dataset.device);
      });
    });

    // OIL TRACKING EVENT LISTENERS
    this.shadowRoot.querySelectorAll('.oil-input').forEach(input => {
      input.addEventListener('input', (e) => {
        const deviceName = input.dataset.device;
        const field = input.dataset.oil;
        const rawValue = e.target.value;
        this._setOilInputValue(deviceName, field, rawValue);

        // Manual override auto-calc
        if (field.startsWith('manual')) {
          this._recalculateManualFields(deviceName, field);
        }
      });

      input.addEventListener('change', async (e) => {
        const deviceName = input.dataset.device;
        const field = input.dataset.oil;
        const rawValue = e.target.value;
        
        const entityMap = {
          bottleCapacity: { entity: `number.${deviceName}_oil_bottle_capacity`, domain: 'number' },
          fillVolume: { entity: `number.${deviceName}_oil_fill_volume`, domain: 'number' },
          measuredRemaining: { entity: `number.${deviceName}_oil_remaining_measured`, domain: 'number' },
          fillDate: { entity: `text.${deviceName}_oil_fill_date`, domain: 'text' },
          manualStart: { entity: `number.${deviceName}_oil_manual_start_volume`, domain: 'number' },
          manualEnd: { entity: `number.${deviceName}_oil_manual_end_volume`, domain: 'number' },
          manualRuntime: { entity: `number.${deviceName}_oil_manual_runtime_hours`, domain: 'number' },
          manualRate: { entity: `number.${deviceName}_oil_manual_rate`, domain: 'number' }
        };
        
        const config = entityMap[field];
        if (!config || !this._hass.states[config.entity]) return;

        if (config.domain === 'number') {
          const value = parseFloat(rawValue);
          if (!isNaN(value)) {
            await this._hass.callService('number', 'set_value', {
              entity_id: config.entity,
              value: value
            });
          }
        } else if (config.domain === 'text') {
          await this._hass.callService('text', 'set_value', {
            entity_id: config.entity,
            value: rawValue
          });
        }
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="oil-toggle"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const deviceName = btn.dataset.device;
        const toggleButton = `button.${deviceName}_oil_calibration_toggle`;
        
        if (this._hass.states[toggleButton]) {
          await this._hass.callService('button', 'press', {
            entity_id: toggleButton
          });
          this._showStatus('Calibration state updated', false, deviceName);
        }
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="oil-finalize"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const deviceName = btn.dataset.device;
        const finalizeButton = `button.${deviceName}_oil_calibration_finalize`;
        
        if (btn.classList.contains('disabled')) return;
        if (this._hass.states[finalizeButton]) {
          await this._hass.callService('button', 'press', {
            entity_id: finalizeButton
          });
          this._showStatus('Calibration finalized', false, deviceName);
        }
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="oil-refill"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const deviceName = btn.dataset.device;
        const refillButton = `button.${deviceName}_oil_refill_keep_calibration`;
        
        if (this._hass.states[refillButton]) {
          await this._hass.callService('button', 'press', {
            entity_id: refillButton
          });
          this._showStatus('Refill recorded (calibration kept)', false, deviceName);
        }
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="oil-manual-apply"]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const deviceName = btn.dataset.device;
        const manualButton = `button.${deviceName}_oil_manual_override`;
        
        if (btn.classList.contains('disabled')) return;
        await this._syncManualInputs(deviceName);

        if (this._hass.states[manualButton]) {
          await this._hass.callService('button', 'press', {
            entity_id: manualButton
          });
          this._showStatus('Manual override applied', false, deviceName);
        }
      });
    });

    // Preserve calibration panel open state
    this.shadowRoot.querySelectorAll('.calibration-panel').forEach(panel => {
      panel.addEventListener('toggle', () => {
        const deviceName = panel.dataset.device;
        if (deviceName) {
          this._setOilPanelOpen(deviceName, panel.open);
        }
      });
    });
  }

  _getStyles() {
    return `
      :host {
        --color-bg: var(--card-background-color, #fff);
        --color-surface: var(--secondary-background-color, #f5f5f5);
        --color-text: var(--primary-text-color, #212121);
        --color-text-secondary: var(--secondary-text-color, #757575);
        --color-primary: var(--primary-color, #03a9f4);
        --color-success: #4caf50;
        --color-warning: #ff9800;
        --color-error: #f44336;
        --radius: 12px;
        --radius-sm: 8px;
        --spacing: clamp(8px, 2vw, 12px);
        
        /* Fluid typography */
        --font-xs: clamp(0.6rem, 1.5vw, 0.7rem);
        --font-sm: clamp(0.7rem, 2vw, 0.8rem);
        --font-md: clamp(0.8rem, 2.5vw, 0.95rem);
        --font-lg: clamp(0.9rem, 3vw, 1.1rem);
        
        /* Grid cell sizing */
        --cell-min-width: clamp(58px, 11vw, 90px);
        --cell-height: clamp(54px, 12vw, 68px);
      }
      
      :host {
        display: block;
        /* Prevent scroll chaining - keeps scroll within the card */
        overscroll-behavior: contain;
      }
      
      ha-card {
        background: var(--color-bg);
        border-radius: var(--radius);
        overflow: hidden;
        /* Prevent Safari touch behaviors that cause scroll issues */
        -webkit-overflow-scrolling: auto;
        /* Prevent any scroll restoration weirdness */
        overflow-anchor: none;
      }
      
      /* Prevent touch callout and tap highlight on interactive elements */
      button, [data-action], .grid-cell, .select-all-btn, .program-header, .day-header {
        -webkit-tap-highlight-color: transparent;
        -webkit-touch-callout: none;
        touch-action: manipulation;
        user-select: none;
        -webkit-user-select: none;
        cursor: pointer;
        /* iOS: prevent scroll-to-top on double-tap */
        -webkit-touch-action: manipulation;
      }
      
      /* Prevent body scroll when interacting with card on iOS */
      .diffuser-card:active,
      .diffuser-card:focus-within {
        touch-action: pan-x pan-y;
      }
      
      .diffuser-card {
        padding: var(--spacing);
        border-bottom: 1px solid var(--color-surface);
        container-type: inline-size;
      }
      
      .diffuser-card:last-child {
        border-bottom: none;
      }
      
      .card-header {
        margin-bottom: var(--spacing);
      }
      
      .title {
        font-size: var(--font-lg);
        font-weight: 600;
        color: var(--color-text);
      }
      
      /* COMPACT CONTROLS - Responsive */
      .compact-controls {
        display: grid;
        grid-template-columns: auto 1fr auto;
        align-items: center;
        gap: clamp(8px, 2vw, 12px);
        padding: clamp(8px, 2vw, 14px);
        background: var(--color-surface);
        border-radius: var(--radius-sm);
        margin-bottom: var(--spacing);
      }
      
      /* Stack on narrow screens */
      @container (max-width: 500px) {
        .compact-controls {
          grid-template-columns: 1fr 1fr;
          grid-template-rows: auto auto;
        }
        .control-group.run-options {
          grid-column: 1 / -1;
        }
      }
      
      .control-group {
        display: flex;
        align-items: center;
        gap: clamp(4px, 1vw, 8px);
      }

      .control-group.power-group {
        gap: clamp(6px, 1.5vw, 10px);
      }
      
      .icon-btn {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: clamp(6px, 1.5vw, 10px) clamp(8px, 2vw, 14px);
        border: none;
        border-radius: 8px;
        background: rgba(0,0,0,0.05);
        cursor: pointer;
        transition: all 150ms;
        min-width: clamp(56px, 12vw, 76px);
      }
      
      .icon-btn:hover {
        background: rgba(0,0,0,0.1);
      }
      
      .icon-btn.active {
        background: rgba(76, 175, 80, 0.2);
        color: #2e7d32;
      }
      
      .icon-btn .icon {
        font-size: clamp(1.1em, 3vw, 1.4em);
      }

      .icon-btn ha-icon.icon {
        --mdc-icon-size: clamp(18px, 4vw, 22px);
      }
      
      .icon-btn .label {
        font-size: var(--font-xs);
        font-weight: 600;
        margin-top: 2px;
      }

      .icon-btn .state {
        font-size: var(--font-xs);
        font-weight: 700;
        margin-top: 2px;
        letter-spacing: 0.3px;
      }
      
      .control-group.settings {
        display: flex;
        gap: clamp(6px, 1.5vw, 10px);
        flex-wrap: wrap;
      }
      
      .control-group.settings label {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: var(--font-sm);
        font-weight: 500;
        color: var(--color-text-secondary);
      }
      
      .compact-input {
        width: clamp(48px, 10vw, 64px);
        padding: clamp(4px, 1vw, 6px) clamp(4px, 1vw, 8px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: 4px;
        font-size: var(--font-sm);
        text-align: center;
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
      }

      .unit {
        font-size: var(--font-xs);
        color: var(--color-text-secondary);
      }
      
      .control-group.run-options {
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 6px;
        flex: 1;
        min-width: 0;
      }

      .run-panel {
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: clamp(8px, 2vw, 12px);
        padding: clamp(10px, 2.5vw, 14px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: var(--radius-sm);
        background: rgba(0,0,0,0.02);
      }

      .run-header {
        font-size: var(--font-md);
        font-weight: 600;
        color: var(--primary-text-color);
        text-align: center;
        letter-spacing: 0.5px;
      }

      .run-buttons {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: clamp(6px, 1.5vw, 10px);
      }
      
      .run-btn {
        padding: clamp(6px, 1.5vw, 8px) clamp(12px, 3vw, 18px);
        border: none;
        border-radius: 6px;
        font-weight: 600;
        font-size: var(--font-sm);
        cursor: pointer;
        transition: all 150ms;
        white-space: nowrap;
        background: linear-gradient(135deg, #00acc1, #26c6da);
        color: white;
      }
      
      .run-btn:hover {
        transform: scale(1.03);
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
      }
      
      .hours-input {
        width: clamp(40px, 8vw, 52px);
        padding: clamp(4px, 1vw, 6px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: 4px;
        font-size: var(--font-sm);
        text-align: center;
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
      }
      
      .hours-label {
        font-size: var(--font-xs);
        color: var(--color-text-secondary);
      }
      
      .countdown {
        font-size: var(--font-sm);
        font-weight: 600;
        color: var(--color-primary);
        padding: 4px 8px;
        background: rgba(3, 169, 244, 0.1);
        border-radius: 4px;
      }
      
      .cancel-btn {
        padding: 4px 8px;
        border: none;
        border-radius: 4px;
        background: rgba(244, 67, 54, 0.1);
        color: var(--color-error);
        cursor: pointer;
        font-weight: 600;
        font-size: var(--font-sm);
      }
      
      /* SCHEDULE SECTION */
      .schedule-section {
        background: rgba(0,0,0,0.02);
        border-radius: var(--radius-sm);
        padding: var(--spacing);
      }
      
      .schedule-header {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: clamp(6px, 1.5vw, 8px);
        margin-bottom: 8px;
      }
      
      .section-title {
        font-weight: 600;
        font-size: var(--font-md);
      }
      
      .copy-dropdown {
        margin-left: auto;
      }
      
      .copy-select {
        padding: clamp(3px, 0.8vw, 4px) clamp(6px, 1.5vw, 8px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: 4px;
        font-size: var(--font-xs);
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
        cursor: pointer;
      }
      
      .chip-btn {
        padding: clamp(3px, 0.8vw, 4px) clamp(8px, 2vw, 10px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: 12px;
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
        font-size: var(--font-xs);
        font-weight: 500;
        cursor: pointer;
        transition: all 150ms;
        white-space: nowrap;
      }
      
      .chip-btn:hover:not(:disabled) {
        background: var(--color-surface);
      }
      
      .chip-btn:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }
      
      .chip-btn.pull-btn {
        background: rgba(3, 169, 244, 0.1);
        color: var(--color-primary);
        border-color: rgba(3, 169, 244, 0.2);
      }
      .chip-btn.reset-btn {
        background: rgba(244, 67, 54, 0.08);
        color: var(--error-color, #f44336);
        border-color: rgba(244, 67, 54, 0.2);
      }
      .chip-btn.reset-btn:hover {
        background: rgba(244, 67, 54, 0.15);
      }
      
      .chip-btn.discard-btn {
        background: rgba(244, 67, 54, 0.1);
        color: var(--color-error);
        border-color: rgba(244, 67, 54, 0.2);
      }
      
      /* LEGEND - Compact on mobile */
      .legend {
        display: flex;
        flex-wrap: wrap;
        gap: clamp(6px, 1.5vw, 10px);
        margin-bottom: 8px;
        font-size: var(--font-xs);
        color: var(--color-text-secondary);
      }
      
      .legend-item {
        display: flex;
        align-items: center;
        gap: 3px;
      }
      
      .legend-dot {
        width: clamp(8px, 2vw, 10px);
        height: clamp(8px, 2vw, 10px);
        border-radius: 2px;
        border: 1px solid rgba(0,0,0,0.1);
        flex-shrink: 0;
      }
      
      .legend-dot.enabled {
        background: rgba(76, 175, 80, 0.3);
        border-color: rgba(76, 175, 80, 0.5);
      }
      
      .legend-dot.has-settings {
        background: rgba(158, 158, 158, 0.3);
        border-color: rgba(158, 158, 158, 0.5);
      }
      
      .legend-dot.staged {
        background: rgba(255, 152, 0, 0.3);
        border-color: rgba(255, 152, 0, 0.6);
      }
      
      .legend-dot.selected {
        background: rgba(3, 169, 244, 0.3);
        border-color: var(--color-primary);
      }
      
      /* GRID - Responsive with horizontal scroll on mobile */
      .schedule-grid-wrapper {
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        scroll-snap-type: x mandatory;
        margin: 0 calc(-1 * var(--spacing));
        padding: 0 var(--spacing);
        scrollbar-width: thin;
      }
      
      .schedule-grid-wrapper::-webkit-scrollbar {
        height: 4px;
      }
      
      .schedule-grid-wrapper::-webkit-scrollbar-thumb {
        background: rgba(0,0,0,0.2);
        border-radius: 2px;
      }
      
      .schedule-grid {
        display: grid;
        grid-template-columns: clamp(24px, 5vw, 32px) repeat(7, minmax(var(--cell-min-width), 1fr));
        gap: clamp(1px, 0.5vw, 3px);
        margin-bottom: 8px;
        min-width: max-content;
      }
      
      /* On wider screens, don't need scroll */
      @container (min-width: 600px) {
        .schedule-grid-wrapper {
          overflow-x: visible;
        }
        .schedule-grid {
          min-width: 0;
        }
      }
      
      .grid-cell {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: clamp(3px, 0.6vw, 5px) clamp(2px, 0.4vw, 4px);
        border-radius: 4px;
        min-height: var(--cell-height);
        transition: all 150ms;
        text-align: center;
        scroll-snap-align: start;
        gap: 0;
      }
      
      .grid-cell.header {
        background: var(--color-surface);
        color: var(--primary-text-color);
        font-weight: 600;
        font-size: var(--font-sm);
        min-height: clamp(20px, 4vw, 26px);
        text-transform: none;
        text-shadow: 0 1px 1px rgba(0,0,0,0.1);
        position: sticky;
        top: 0;
        z-index: 2;
      }
      
      .grid-cell.header.today {
        background: var(--color-warning);
        color: white;
      }
      
      .grid-cell.corner {
        background: transparent;
        position: sticky;
        left: 0;
        z-index: 3;
      }
      
      .grid-cell.program-label {
        position: sticky;
        left: 0;
        z-index: 1;
        background: var(--color-surface);
      }
      
      .grid-cell.program-label,
      .grid-cell.day-header {
        cursor: pointer;
        user-select: none;
        -webkit-tap-highlight-color: transparent;
        -webkit-touch-callout: none;
      }
      
      /* Prevent Safari scroll jump on click */
      .schedule-cell,
      .grid-cell.program-label,
      .grid-cell.day-header,
      .chip-btn {
        touch-action: manipulation;
        -webkit-tap-highlight-color: transparent;
      }
      
      .grid-cell.program-label:hover,
      .grid-cell.day-header:hover {
        background: rgba(3, 169, 244, 0.15);
        color: var(--primary-text-color);
      }

      .grid-cell.program-label.night-owl {
        background: var(--card-background-color, #1a1a2e);
        color: #b39ddb;
        font-size: var(--font-xs, 0.65rem);
        font-weight: 600;
      }
      
      .grid-cell.row-selected,
      .grid-cell.col-selected {
        background: rgba(3, 169, 244, 0.2);
        color: var(--primary-text-color);
      }
      
      .schedule-cell {
        cursor: pointer;
        user-select: none;
        border: 2px solid transparent;
      }
      
      .schedule-cell.empty {
        background: var(--color-surface);
        color: var(--color-text-secondary);
      }
      
      .schedule-cell.has-settings {
        background: rgba(158, 158, 158, 0.2);
        border-color: rgba(158, 158, 158, 0.3);
        color: var(--color-text-secondary);
      }
      
      .schedule-cell.enabled {
        background: rgba(76, 175, 80, 0.15);
        border-color: rgba(76, 175, 80, 0.4);
        color: var(--color-text);
      }
      
      .schedule-cell.staged {
        border-color: var(--color-warning) !important;
        box-shadow: inset 0 0 0 1px var(--color-warning);
      }
      
      .schedule-cell.selected {
        background: rgba(3, 169, 244, 0.2) !important;
        border-color: var(--color-primary) !important;
        box-shadow: 0 0 0 2px var(--color-primary);
      }
      
      .schedule-cell:hover {
        transform: scale(1.02);
        z-index: 1;
      }
      
      .schedule-cell:active {
        transform: scale(0.98);
      }
      
      /* Cell content - stacked vertically */
      .schedule-cell .cell-start,
      .schedule-cell .cell-end {
        font-weight: 700;
        font-size: var(--font-sm);
        line-height: 1.15;
        color: var(--primary-text-color);
        text-shadow: 0 1px 1px rgba(0,0,0,0.1);
      }
      
      .schedule-cell .cell-end {
        font-weight: 500;
        opacity: 0.85;
      }
      
      .schedule-cell .cell-work {
        font-size: var(--font-xs);
        font-weight: 600;
        color: var(--primary-text-color);
        opacity: 0.9;
        margin-top: 1px;
      }
      
      .schedule-cell .cell-level {
        font-size: var(--font-xs);
        font-weight: 700;
        color: var(--color-primary);
        opacity: 0.9;
      }
      
      .schedule-cell .off-label {
        font-size: var(--font-xs);
        opacity: 0.5;
        font-weight: 600;
      }
      
      /* On very narrow cells, combine start/end on same row */
      @container (min-width: 600px) {
        .schedule-cell .cell-start::after {
          content: '-';
          margin: 0 1px;
        }
        .schedule-cell .cell-end {
          display: inline;
        }
      }
      
      /* SELECTION BAR */
      .selection-bar {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: clamp(4px, 1vw, 6px);
        margin-bottom: 8px;
      }
      
      .staged-badge {
        padding: 3px 8px;
        background: rgba(255, 152, 0, 0.15);
        color: #e65100;
        border-radius: 10px;
        font-size: var(--font-xs);
        font-weight: 600;
      }
      
      /* STATUS */
      .status-message {
        padding: clamp(6px, 1.5vw, 8px) clamp(8px, 2vw, 12px);
        border-radius: var(--radius-sm);
        margin-bottom: 8px;
        font-size: var(--font-sm);
        font-weight: 500;
      }
      
      .status-message.success {
        background: rgba(76, 175, 80, 0.12);
        color: #2e7d32;
      }
      
      .status-message.error {
        background: rgba(244, 67, 54, 0.12);
        color: #c62828;
      }
      
      /* EDITOR - Responsive */
      .editor-section {
        background: var(--color-surface);
        border-radius: var(--radius-sm);
        padding: clamp(8px, 2vw, 10px);
        transition: opacity 200ms;
      }
      
      .editor-section.dimmed {
        opacity: 0.6;
      }
      
      .editor-header {
        font-size: var(--font-xs);
        color: var(--color-text-secondary);
        margin-bottom: 8px;
      }
      
      .editor-row-inline {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: clamp(6px, 1.5vw, 10px);
        margin-bottom: 10px;
      }
      
      .toggle-label {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: var(--font-sm);
        font-weight: 500;
      }
      
      .toggle-label input {
        width: clamp(14px, 3vw, 18px);
        height: clamp(14px, 3vw, 18px);
      }
      
      .time-inputs {
        display: flex;
        align-items: center;
        gap: 4px;
        transition: opacity 200ms;
      }
      
      .time-inputs.time-disabled {
        opacity: 0.4;
        pointer-events: none;
      }
      
      .time-input {
        padding: clamp(3px, 0.8vw, 4px) clamp(4px, 1vw, 6px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: 4px;
        font-size: var(--font-sm);
        width: clamp(70px, 15vw, 85px);
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
      }
      
      .num-inputs {
        display: flex;
        gap: clamp(4px, 1vw, 6px);
        flex-wrap: wrap;
      }
      
      .num-inputs label {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: var(--font-xs);
        color: var(--color-text-secondary);
      }
      
      .num-input {
        width: clamp(40px, 10vw, 50px);
        padding: clamp(3px, 0.8vw, 4px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: 4px;
        font-size: var(--font-sm);
        text-align: center;
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
      }
      
      .level-select {
        padding: clamp(3px, 0.8vw, 4px) clamp(6px, 1.5vw, 8px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: 4px;
        font-size: var(--font-sm);
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
      }

      .level-label {
        font-size: var(--font-xs);
        color: var(--color-text-secondary);
        margin-left: 4px;
      }
      
      /* EDITOR ACTIONS ROW */
      .editor-actions-row {
        display: flex;
        align-items: center;
        gap: clamp(6px, 1.5vw, 10px);
        flex-wrap: wrap;
      }
      
      .level-group {
        display: flex;
        align-items: center;
        gap: 4px;
        margin-left: auto;
      }
      
      .stage-btn, .clear-btn, .push-btn {
        padding: clamp(5px, 1.2vw, 6px) clamp(8px, 2vw, 10px);
        border: none;
        border-radius: 16px;
        font-size: var(--font-xs);
        font-weight: 600;
        cursor: pointer;
        transition: all 150ms;
        white-space: nowrap;
      }
      
      .stage-btn {
        background: rgba(255, 152, 0, 0.15);
        color: #e65100;
      }
      
      .stage-btn:hover:not(.disabled) {
        background: rgba(255, 152, 0, 0.25);
      }
      
      .clear-btn {
        background: rgba(158, 158, 158, 0.15);
        color: #616161;
      }
      
      .clear-btn:hover:not(.disabled) {
        background: rgba(158, 158, 158, 0.25);
      }
      
      .push-btn {
        background: rgba(158, 158, 158, 0.2);
        color: var(--color-text-secondary);
      }
      
      .push-btn.ready {
        background: linear-gradient(135deg, #00c853, #69f0ae);
        color: white;
        font-weight: 700;
        box-shadow: 0 2px 8px rgba(0, 200, 83, 0.4);
        animation: pulse-green 2s infinite;
      }
      
      @keyframes pulse-green {
        0%, 100% { box-shadow: 0 2px 8px rgba(0, 200, 83, 0.4); }
        50% { box-shadow: 0 4px 16px rgba(0, 200, 83, 0.6); }
      }
      
      .push-btn.ready:hover:not(.disabled) {
        transform: scale(1.03);
        box-shadow: 0 4px 16px rgba(0, 200, 83, 0.5);
      }
      
      .stage-btn.disabled, .clear-btn.disabled, .push-btn.disabled {
        opacity: 0.4;
        cursor: not-allowed;
        transform: none !important;
        box-shadow: none !important;
        animation: none !important;
      }
      
      /* OIL TRACKING SECTION - Responsive */
      .oil-section {
        padding: var(--spacing);
        border-top: 1px solid rgba(0,0,0,0.05);
      }
      
      .oil-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: var(--spacing);
        flex-wrap: wrap;
      }
      
      .calibration-badge {
        font-size: var(--font-xs);
        padding: 2px 8px;
        border-radius: 10px;
        font-weight: 600;
      }

      .calibration-badge.running {
        background: rgba(33, 150, 243, 0.15);
        color: #1565c0;
      }

      .calibration-badge.ready {
        background: rgba(255, 152, 0, 0.15);
        color: #e65100;
      }

      .calibration-badge.calibrated {
        background: rgba(76, 175, 80, 0.15);
        color: #2e7d32;
      }

      .calibration-badge.idle {
        background: rgba(158, 158, 158, 0.15);
        color: #616161;
      }
      
      .oil-content {
        display: flex;
        gap: var(--spacing);
        align-items: flex-start;
      }
      
      /* Stack vertically on mobile */
      @container (max-width: 450px) {
        .oil-content {
          flex-direction: column;
        }
        .oil-left {
          flex-direction: row;
          width: 100%;
          max-width: none;
          justify-content: space-between;
        }
        .oil-summary {
          flex: 1;
        }
      }

      .oil-left {
        display: flex;
        flex-direction: column;
        gap: var(--spacing);
        align-items: center;
        flex: 0 0 clamp(100px, 25%, 160px);
      }

      .oil-right {
        flex: 1;
        min-width: 0;
      }
      
      .bottle-container {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
        flex-shrink: 0;
      }
      
      .bottle {
        width: clamp(40px, 10vw, 50px);
        height: clamp(64px, 16vw, 80px);
        border: 2px solid var(--secondary-text-color, #666);
        border-radius: 0 0 10px 10px;
        position: relative;
        overflow: hidden;
        background: var(--card-background-color, white);
        box-shadow: inset 0 0 8px rgba(0,0,0,0.1);
      }
      
      .bottle::before {
        content: '';
        position: absolute;
        top: -8px;
        left: 50%;
        transform: translateX(-50%);
        width: clamp(18px, 5vw, 24px);
        height: 10px;
        border: 2px solid var(--secondary-text-color, #666);
        border-bottom: none;
        border-radius: 4px 4px 0 0;
        background: var(--card-background-color, white);
      }

      .bottle::after {
        content: '';
        position: absolute;
        top: 6px;
        left: 6px;
        width: 6px;
        height: 70%;
        background: rgba(255,255,255,0.55);
        border-radius: 6px;
        pointer-events: none;
      }
      
      .bottle-fill {
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        transition: height 300ms ease, background 300ms ease;
        border-radius: 0 0 8px 8px;
        background: linear-gradient(180deg, rgba(255,255,255,0.25), rgba(0,0,0,0.05));
      }
      
      .bottle-label {
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        font-size: 0.75em;
        font-weight: 700;
        color: var(--primary-text-color, #333);
        text-shadow: 0 0 3px var(--card-background-color, white);
      }
      
      .bottle-info {
        display: flex;
        flex-direction: column;
        align-items: center;
        font-size: 0.75em;
      }
      
      .bottle-info .remaining {
        font-weight: 600;
        color: var(--primary-text-color, #333);
      }
      
      .bottle-info .days-left {
        color: var(--secondary-text-color, #666);
        font-size: 0.9em;
      }
      
      .oil-summary {
        display: flex;
        flex-direction: column;
        gap: 4px;
        width: 100%;
        font-size: var(--font-xs);
      }

      .summary-row {
        display: flex;
        justify-content: space-between;
        gap: clamp(6px, 1.5vw, 10px);
        font-size: var(--font-sm);
      }

      .summary-label {
        color: var(--secondary-text-color, #666);
      }

      .summary-value {
        font-weight: 600;
        color: var(--primary-text-color, #333);
      }

      .stat-row {
        display: flex;
        justify-content: space-between;
        font-size: var(--font-sm);
        padding: 2px 0;
      }
      
      .stat-label {
        color: var(--secondary-text-color, #666);
      }
      
      .stat-value {
        font-weight: 500;
      }
      
      .stat-value.active {
        color: #4caf50;
      }
      
      .stat-value.inactive {
        color: var(--disabled-text-color, #999);
      }
      
      /* Calibration Panel - Responsive */
      .calibration-panel {
        border: 1px solid var(--divider-color, rgba(0,0,0,0.1));
        border-radius: 8px;
        overflow: hidden;
        width: 100%;
        background: var(--secondary-background-color, rgba(0,0,0,0.02));
      }
      
      .calibration-panel summary {
        padding: clamp(8px, 2vw, 10px) clamp(10px, 2.5vw, 14px);
        background: var(--secondary-background-color, rgba(0,0,0,0.03));
        cursor: pointer;
        font-size: var(--font-sm);
        font-weight: 600;
        color: var(--primary-text-color);
        list-style: none;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      
      .calibration-panel summary::before {
        content: '▶';
        font-size: var(--font-xs);
        transition: transform 200ms;
      }
      
      .calibration-panel[open] summary::before {
        transform: rotate(90deg);
      }
      
      .calibration-panel summary:hover {
        background: var(--secondary-background-color, rgba(0,0,0,0.05));
      }
      
      .calibration-content {
        padding: clamp(10px, 2.5vw, 14px);
        display: flex;
        flex-direction: column;
        gap: clamp(12px, 3vw, 16px);
      }
      
      /* Calibration Sections */
      .cal-section {
        background: var(--card-background-color, white);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.08));
        border-radius: 8px;
        padding: clamp(8px, 2vw, 12px);
      }
      
      .cal-section-title {
        font-size: var(--font-sm);
        font-weight: 600;
        color: var(--primary-text-color);
        margin-bottom: clamp(6px, 1.5vw, 10px);
        padding-bottom: 6px;
        border-bottom: 1px solid var(--divider-color, rgba(0,0,0,0.06));
      }
      
      .cal-section-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: clamp(6px, 1.5vw, 10px);
      }
      
      .stat-item {
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
      }
      
      .cal-section-fields {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(clamp(120px, 30vw, 150px), 1fr));
        gap: clamp(6px, 1.5vw, 10px);
      }
      
      .calibration-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0;
        font-size: var(--font-sm);
      }
      
      .calibration-row label {
        color: var(--secondary-text-color, #555);
      }
      
      .input-group {
        display: flex;
        align-items: center;
        gap: 4px;
      }
      
      .oil-input {
        width: clamp(55px, 14vw, 70px);
        padding: clamp(3px, 0.8vw, 4px) clamp(4px, 1vw, 6px);
        border: 1px solid var(--divider-color, rgba(0,0,0,0.15));
        border-radius: 4px;
        font-size: var(--font-sm);
        text-align: right;
        background: var(--card-background-color, white);
        color: var(--primary-text-color);
      }

      .oil-input.date {
        width: clamp(100px, 25vw, 120px);
        text-align: left;
      }
      
      .input-group span {
        color: var(--secondary-text-color, #666);
        font-size: var(--font-sm);
      }
      
      .calibration-actions {
        display: flex;
        flex-wrap: wrap;
        gap: clamp(6px, 1.5vw, 8px);
        margin-top: var(--spacing);
        grid-column: 1 / -1;
      }

      .calibration-actions.secondary {
        margin-top: 4px;
        grid-column: 1 / -1;
      }
      
      .oil-btn {
        flex: 1;
        min-width: clamp(80px, 20vw, 100px);
        padding: clamp(6px, 1.5vw, 8px) clamp(8px, 2vw, 12px);
        border: none;
        border-radius: 6px;
        font-size: var(--font-sm);
        font-weight: 500;
        cursor: pointer;
        transition: all 150ms;
        white-space: nowrap;
      }

      .oil-btn.small-btn {
        padding: clamp(4px, 1vw, 6px) clamp(8px, 2vw, 10px);
        font-size: var(--font-xs);
        background: var(--secondary-background-color, rgba(0,0,0,0.05));
        color: var(--primary-text-color, #555);
      }

      .oil-btn.small-btn:hover {
        background: rgba(0,0,0,0.1);
      }

      .oil-btn.disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      
      .fill-btn {
        background: rgba(33, 150, 243, 0.15);
        color: #1565c0;
      }
      
      .fill-btn:hover {
        background: rgba(33, 150, 243, 0.25);
      }
      
      .calibrate-btn {
        background: rgba(156, 39, 176, 0.15);
        color: #7b1fa2;
      }
      
      .calibrate-btn:hover {
        background: rgba(156, 39, 176, 0.25);
      }
      
      .calibration-warning {
        margin-top: 10px;
        padding: 8px;
        background: rgba(255, 152, 0, 0.12);
        border-radius: 6px;
        font-size: 0.72em;
        color: #e65100;
        grid-column: 1 / -1;
      }

      .cal-section.manual-override {
        background: rgba(0,0,0,0.02);
        border-style: dashed;
      }
    `;
  }

  getCardSize() {
    return 8;
  }
}

customElements.define('aroma-link-schedule-card', AromaLinkScheduleCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'aroma-link-schedule-card',
  name: 'Aroma-Link Diffuser Card',
  description: 'Complete dashboard card for Aroma-Link diffusers: controls, schedules, and oil level tracking. Auto-discovers all devices!'
});
