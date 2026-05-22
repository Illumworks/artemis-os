/**
 * trigger-scheduled-form.js — PIPE3 + cron-picker-presets
 *
 * Replaces the raw cron input with a 5-mode preset picker:
 *   Mode 1 "every_n"  — Every N minutes / hours / days
 *   Mode 2 "daily"    — Every day at HH:MM
 *   Mode 3 "weekly"   — Specific days-of-week at HH:MM
 *   Mode 4 "monthly"  — Day-of-month at HH:MM
 *   Mode 5 "custom"   — Raw cron (power-user / fallback)
 *
 * Cron string remains the canonical persisted format.
 * Parse-and-match on load opens the form in the correct mode.
 */

import { compileCron, parseCron, describeCron, isValidCron } from "../cron-utils.js";

const TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Tokyo",
  "Asia/Shanghai",
  "Australia/Sydney",
];

const MODES = [
  { id: "every_n", label: "Every N" },
  { id: "daily",   label: "Daily" },
  { id: "weekly",  label: "Weekly" },
  { id: "monthly", label: "Monthly" },
  { id: "custom",  label: "Custom" },
];
const MODE_IDS = new Set(MODES.map((m) => m.id));

const DOW_LABELS = [
  { d: 0, abbr: "Sun" },
  { d: 1, abbr: "Mon" },
  { d: 2, abbr: "Tue" },
  { d: 3, abbr: "Wed" },
  { d: 4, abbr: "Thu" },
  { d: 5, abbr: "Fri" },
  { d: 6, abbr: "Sat" },
];

function fieldMatches(field, value, min, max) {
  if (field === "*") return true;
  return field.split(",").some((part) => {
    if (part.startsWith("*/")) {
      const step = parseInt(part.slice(2), 10);
      return !isNaN(step) && step > 0 && (value - min) % step === 0;
    }
    if (part.includes("-")) {
      const [start, end] = part.split("-").map((x) => parseInt(x, 10));
      return !isNaN(start) && !isNaN(end) && value >= start && value <= end;
    }
    const exact = parseInt(part, 10);
    if (max === 7 && exact === 7 && value === 0) return true;
    return !isNaN(exact) && exact >= min && exact <= max && value === exact;
  });
}

function zonedParts(date, timezone) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone || "UTC",
    minute: "2-digit",
    hour: "2-digit",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hourCycle: "h23",
  });
  const parts = Object.fromEntries(fmt.formatToParts(date).map((p) => [p.type, p.value]));
  return {
    minute: Number(parts.minute),
    hour: Number(parts.hour),
    day: Number(parts.day),
    month: Number(parts.month),
  };
}

function zonedDayOfWeek(date, timezone) {
  const label = date.toLocaleString("en-US", { timeZone: timezone || "UTC", weekday: "short" });
  return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(label);
}

function computeNextRun(expr, timezone) {
  if (!isValidCron(expr)) return "Next run: see scheduler";
  const [min, hr, dom, mon, dow] = expr.trim().split(/\s+/);
  const start = new Date();
  start.setSeconds(0, 0);
  start.setMinutes(start.getMinutes() + 1);

  for (let i = 0; i < 60 * 24 * 8; i += 1) {
    const candidate = new Date(start.getTime() + i * 60_000);
    const parts = zonedParts(candidate, timezone);
    parts.dow = zonedDayOfWeek(candidate, timezone);
    if (
      fieldMatches(min, parts.minute, 0, 59) &&
      fieldMatches(hr, parts.hour, 0, 23) &&
      fieldMatches(dom, parts.day, 1, 31) &&
      fieldMatches(mon, parts.month, 1, 12) &&
      fieldMatches(dow, parts.dow, 0, 7)
    ) {
      const stamp = candidate.toLocaleString("en-US", {
        timeZone: timezone || "UTC",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZoneName: "short",
      });
      return `Next run: ${stamp}`;
    }
  }
  return "Next run: see scheduler";
}

// ── Render ───────────────────────────────────────────────────────────────────

export function renderTriggerScheduledForm(config, container) {
  const cfg = config ?? {};
  const savedCron = cfg.cron ?? "0 */4 * * *";
  const tz = cfg.timezone ?? "UTC";
  const startDate = cfg.start_date ?? "";
  const endDate = cfg.end_date ?? "";
  const showDates = !!(startDate || endDate);

  // Parse saved cron → determine opening mode + fields
  const parsed = parseCron(savedCron) ?? { mode: "custom", fields: { cron: savedCron } };
  const preferredMode = MODE_IDS.has(cfg.preferred_mode) ? cfg.preferred_mode : null;
  let currentMode = preferredMode || parsed.mode;
  let currentFields = _fieldsForMode(currentMode, parsed, savedCron);

  // ── Initial render ────────────────────────────────────────────────────────

  container.innerHTML = `
    <div class="ncf ncf-sched">
      <div class="ncf-cron-title"></div>

      <!-- Mode selector -->
      <div class="ncf-field">
        <label class="ncf-label ncf-required">Schedule</label>
        <div class="ncf-cron-mode-bar" role="group" aria-label="Schedule mode">
          ${MODES.map((m) => `
            <button type="button"
              class="ncf-cron-mode-btn${m.id === currentMode ? " ncf-cron-mode-btn--active" : ""}"
              data-mode="${m.id}">${m.label}</button>
          `).join("")}
        </div>
      </div>

      <!-- Per-mode inputs (replaced on mode switch) -->
      <div class="ncf-cron-inputs"></div>

      <!-- Human-readable preview -->
      <div class="ncf-cron-preview ncf-hint"></div>
      <div class="ncf-next-run-preview ncf-hint"></div>

      <!-- Timezone -->
      <div class="ncf-field">
        <label class="ncf-label">Timezone</label>
        <select class="ncf-select ncf-tz">
          ${TIMEZONES.map((t) => `<option value="${t}"${t === tz ? " selected" : ""}>${t}</option>`).join("")}
        </select>
      </div>

      <!-- Active date range -->
      <details class="ncf-details" ${showDates ? "open" : ""}>
        <summary class="ncf-summary">Active date range (optional)</summary>
        <div class="ncf-details-body">
          <div class="ncf-row">
            <div class="ncf-field ncf-field--half">
              <label class="ncf-label">Start date</label>
              <input class="ncf-input ncf-start-date" type="date" value="${_esc(startDate)}">
            </div>
            <div class="ncf-field ncf-field--half">
              <label class="ncf-label">End date</label>
              <input class="ncf-input ncf-end-date" type="date" value="${_esc(endDate)}">
            </div>
          </div>
        </div>
      </details>

    </div>
  `;

  const inputsEl  = container.querySelector(".ncf-cron-inputs");
  const tzEl = container.querySelector(".ncf-tz");
  const titleEl = container.querySelector(".ncf-cron-title");
  const previewEl = container.querySelector(".ncf-cron-preview");
  const nextRunEl = container.querySelector(".ncf-next-run-preview");

  // ── Per-mode renderers ────────────────────────────────────────────────────

  function _renderEveryN(fields) {
    const n    = fields.n ?? 4;
    const unit = fields.unit ?? "hours";
    inputsEl.innerHTML = `
      <div class="ncf-field">
        <label class="ncf-label">Repeat every</label>
        <div class="ncf-cron-every-row">
          <input class="ncf-input ncf-cron-n" type="number" min="1" max="59"
            value="${_esc(n)}" style="width:80px">
          <select class="ncf-select ncf-cron-unit" style="flex:1">
            <option value="minutes"${unit === "minutes" ? " selected" : ""}>minutes</option>
            <option value="hours"${unit === "hours" ? " selected" : ""}>hours</option>
            <option value="days"${unit === "days" ? " selected" : ""}>days</option>
          </select>
        </div>
        <div class="ncf-hint">Min 1, max 59 min / 23 hr / 31 days</div>
      </div>
    `;
    inputsEl.querySelector(".ncf-cron-n").addEventListener("input", _sync);
    inputsEl.querySelector(".ncf-cron-unit").addEventListener("change", _sync);
  }

  function _renderDaily(fields) {
    const { hour = 9, minute = 0 } = fields;
    inputsEl.innerHTML = `
      <div class="ncf-field">
        <label class="ncf-label">Time of day</label>
        <input class="ncf-input ncf-cron-time" type="time"
          value="${_timePad(hour, minute)}">
      </div>
    `;
    inputsEl.querySelector(".ncf-cron-time").addEventListener("change", _sync);
  }

  function _renderWeekly(fields) {
    const { hour = 9, minute = 0, days = [1, 2, 3, 4, 5] } = fields;
    const daySet = new Set(days);
    inputsEl.innerHTML = `
      <div class="ncf-field">
        <label class="ncf-label">Days of week</label>
        <div class="ncf-cron-dow-row">
          ${DOW_LABELS.map(({ d, abbr }) => `
            <label class="ncf-cron-dow-item${daySet.has(d) ? " ncf-cron-dow-item--on" : ""}">
              <input type="checkbox" class="ncf-cron-dow-cb" value="${d}"
                ${daySet.has(d) ? "checked" : ""}>${abbr}
            </label>
          `).join("")}
        </div>
      </div>
      <div class="ncf-field">
        <label class="ncf-label">Time of day</label>
        <input class="ncf-input ncf-cron-time" type="time"
          value="${_timePad(hour, minute)}">
      </div>
    `;
    inputsEl.querySelectorAll(".ncf-cron-dow-cb").forEach((cb) => {
      cb.addEventListener("change", (e) => {
        e.target.closest(".ncf-cron-dow-item").classList.toggle(
          "ncf-cron-dow-item--on", e.target.checked
        );
        _sync();
      });
    });
    inputsEl.querySelector(".ncf-cron-time").addEventListener("change", _sync);
  }

  function _renderMonthly(fields) {
    const { dom = 1, hour = 9, minute = 0 } = fields;
    inputsEl.innerHTML = `
      <div class="ncf-field">
        <label class="ncf-label">Day of month</label>
        <input class="ncf-input ncf-cron-dom" type="number" min="1" max="31"
          value="${_esc(dom)}" style="width:80px">
        <div class="ncf-hint">Days 29-31 may not occur in all months.</div>
      </div>
      <div class="ncf-field">
        <label class="ncf-label">Time of day</label>
        <input class="ncf-input ncf-cron-time" type="time"
          value="${_timePad(hour, minute)}">
      </div>
    `;
    inputsEl.querySelector(".ncf-cron-dom").addEventListener("input", _sync);
    inputsEl.querySelector(".ncf-cron-time").addEventListener("change", _sync);
  }

  function _renderCustom(fields) {
    const raw = fields.cron ?? savedCron;
    inputsEl.innerHTML = `
      <div class="ncf-field">
        <label class="ncf-label ncf-required">Cron expression</label>
        <input class="ncf-input ncf-cron" type="text" value="${_esc(raw)}"
          placeholder="0 */4 * * *" spellcheck="false">
        <div class="ncf-hint">5 fields: minute hour day-of-month month day-of-week</div>
      </div>
    `;
    inputsEl.querySelector(".ncf-cron").addEventListener("input", _sync);
  }

  // ── Mode switch ───────────────────────────────────────────────────────────

  function _renderMode(mode, fields) {
    currentMode = mode;
    currentFields = fields ?? {};
    switch (mode) {
      case "every_n":  _renderEveryN(currentFields);  break;
      case "daily":    _renderDaily(currentFields);    break;
      case "weekly":   _renderWeekly(currentFields);   break;
      case "monthly":  _renderMonthly(currentFields);  break;
      case "custom":   _renderCustom(currentFields);   break;
    }
    // Update active button
    container.querySelectorAll(".ncf-cron-mode-btn").forEach((btn) => {
      btn.classList.toggle("ncf-cron-mode-btn--active", btn.dataset.mode === mode);
    });
    _sync();
  }

  // ── Read current inputs → fields object ──────────────────────────────────

  function _readFields() {
    switch (currentMode) {
      case "every_n": {
        const n    = parseInt(inputsEl.querySelector(".ncf-cron-n")?.value ?? "1", 10);
        const unit = inputsEl.querySelector(".ncf-cron-unit")?.value ?? "hours";
        return { n, unit };
      }
      case "daily": {
        const [hour, minute] = _parseTime(inputsEl.querySelector(".ncf-cron-time")?.value);
        return { hour, minute };
      }
      case "weekly": {
        const [hour, minute] = _parseTime(inputsEl.querySelector(".ncf-cron-time")?.value);
        const days = [...inputsEl.querySelectorAll(".ncf-cron-dow-cb:checked")]
          .map((el) => parseInt(el.value, 10));
        return { hour, minute, days };
      }
      case "monthly": {
        const dom = parseInt(inputsEl.querySelector(".ncf-cron-dom")?.value ?? "1", 10);
        const [hour, minute] = _parseTime(inputsEl.querySelector(".ncf-cron-time")?.value);
        return { dom, hour, minute };
      }
      case "custom": {
        const cron = inputsEl.querySelector(".ncf-cron")?.value?.trim() ?? "";
        return { cron };
      }
    }
    return {};
  }

  // ── Sync: update preview ──────────────────────────────────────────────────

  function _sync() {
    currentFields = _readFields();
    const cron = compileCron(currentMode, currentFields);
    const desc = describeCron(cron);
    const valid = isValidCron(cron);

    if (currentMode === "custom" && !valid && currentFields.cron) {
      titleEl.textContent = "Custom schedule";
      previewEl.textContent = "Invalid cron expression";
      previewEl.classList.add("ncf-hint--err");
    } else {
      titleEl.textContent = desc || (valid ? "Custom schedule" : "Schedule");
      previewEl.textContent = desc || (valid ? "Custom schedule" : "");
      previewEl.classList.remove("ncf-hint--err");
    }
    nextRunEl.textContent = cron ? computeNextRun(cron, tzEl.value) : "";
  }

  // ── Wire up mode buttons ──────────────────────────────────────────────────

  container.querySelectorAll(".ncf-cron-mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const newMode = btn.dataset.mode;
      if (newMode === currentMode) return;
      // When switching to custom, seed with current compiled cron
      if (newMode === "custom") {
        const cron = compileCron(currentMode, _readFields());
        _renderMode("custom", { cron });
      } else {
        _renderMode(newMode, {});
      }
    });
  });

  tzEl.addEventListener("change", _sync);

  // ── Initial mode render ───────────────────────────────────────────────────

  _renderMode(currentMode, currentFields);

  // ── Public API ────────────────────────────────────────────────────────────

  return {
    getValues() {
      const fields = _readFields();
      const cron = compileCron(currentMode, fields);
      const out = {
        cron,
        timezone: container.querySelector(".ncf-tz")?.value ?? "UTC",
        preferred_mode: currentMode,
      };
      const s = container.querySelector(".ncf-start-date")?.value;
      const e = container.querySelector(".ncf-end-date")?.value;
      if (s) out.start_date = s;
      if (e) out.end_date = e;
      return out;
    },

    validate() {
      const fields = _readFields();
      const cron = compileCron(currentMode, fields);
      if (!cron) return "Schedule is required.";
      if (!isValidCron(cron)) return "Invalid cron expression (5 space-separated fields).";
      if (currentMode === "weekly" && !(fields.days?.length))
        return "Select at least one day of the week.";
      return null;
    },
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _timePad(hour, minute) {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function _parseTime(val) {
  if (!val) return [9, 0];
  const [h, m] = val.split(":").map(Number);
  return [isNaN(h) ? 9 : h, isNaN(m) ? 0 : m];
}

function _fieldsForMode(mode, parsed, savedCron) {
  if (mode === "custom") return { cron: savedCron };
  if (parsed?.mode === mode) return parsed.fields;
  return {};
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
