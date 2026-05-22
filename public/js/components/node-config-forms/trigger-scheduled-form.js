/**
 * trigger-scheduled-form.js — PIPE3
 * Typed config form for trigger_scheduled nodes.
 * Fields: cron expression + human preview, timezone, optional active dates,
 *         read-only next-run preview.
 */

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

// ── Cron parser — common pattern recognition ──────────────────────────────────

function describeCron(expr) {
  if (!expr || typeof expr !== "string") return "";
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return "";
  const [min, hr, dom, mon, dow] = parts;

  if (expr === "* * * * *")        return "Every minute";
  if (expr === "0 * * * *")        return "Every hour";
  if (expr === "0 0 * * *")        return "Every day at midnight";
  if (min === "0" && hr === "*" && dom === "*" && mon === "*" && dow === "*")
    return "Every hour at :00";
  if (min.match(/^\d+$/) && hr === "*")
    return `Every hour at :${min.padStart(2, "0")}`;
  if (min.match(/^\d+$/) && hr.match(/^\d+$/) && dom === "*" && mon === "*" && dow === "*")
    return `Daily at ${hr.padStart(2, "0")}:${min.padStart(2, "0")}`;
  if (hr.startsWith("*/")) {
    const n = parseInt(hr.slice(2), 10);
    if (!isNaN(n)) return `Every ${n} hour${n !== 1 ? "s" : ""}`;
  }
  if (min.startsWith("*/")) {
    const n = parseInt(min.slice(2), 10);
    if (!isNaN(n)) return `Every ${n} minute${n !== 1 ? "s" : ""}`;
  }
  if (dow !== "*" && dom === "*") {
    const days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
    const d = parseInt(dow, 10);
    const dayName = isNaN(d) ? dow : (days[d] ?? dow);
    if (min.match(/^\d+$/) && hr.match(/^\d+$/))
      return `Weekly on ${dayName} at ${hr.padStart(2, "0")}:${min.padStart(2, "0")}`;
  }
  return ""; // unrecognised — show nothing extra
}

function isValidCron(expr) {
  if (!expr) return false;
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return false;
  return parts.every((p) => /^[\d*\/,\-]+$/.test(p));
}

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
  const cron = cfg.cron ?? "0 */4 * * *";
  const tz = cfg.timezone ?? "UTC";
  const startDate = cfg.start_date ?? "";
  const endDate = cfg.end_date ?? "";
  const showDates = !!(startDate || endDate);

  container.innerHTML = `
    <div class="ncf ncf-sched">
      <div class="ncf-field">
        <label class="ncf-label ncf-required">Schedule (cron)</label>
        <input class="ncf-input ncf-cron" type="text" value="${_esc(cron)}"
          placeholder="0 */4 * * *" spellcheck="false">
        <div class="ncf-cron-preview ncf-hint"></div>
        <div class="ncf-next-run-preview ncf-hint"></div>
      </div>

      <div class="ncf-field">
        <label class="ncf-label">Timezone</label>
        <select class="ncf-select ncf-tz">
          ${TIMEZONES.map((t) => `<option value="${t}"${t === tz ? " selected" : ""}>${t}</option>`).join("")}
        </select>
      </div>

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

  const cronEl = container.querySelector(".ncf-cron");
  const tzEl = container.querySelector(".ncf-tz");
  const previewEl = container.querySelector(".ncf-cron-preview");
  const nextRunEl = container.querySelector(".ncf-next-run-preview");

  function _updatePreview() {
    const val = cronEl.value.trim();
    const desc = describeCron(val);
    const valid = isValidCron(val);
    if (!valid && val) {
      previewEl.textContent = "Invalid cron expression";
      previewEl.classList.add("ncf-hint--err");
    } else {
      previewEl.textContent = desc || (val ? "Custom schedule" : "");
      previewEl.classList.remove("ncf-hint--err");
    }
    nextRunEl.textContent = val ? computeNextRun(val, tzEl.value) : "";
  }

  cronEl.addEventListener("input", _updatePreview);
  tzEl.addEventListener("change", _updatePreview);
  _updatePreview();

  return {
    getValues() {
      const out = {
        cron: container.querySelector(".ncf-cron")?.value?.trim() ?? cron,
        timezone: container.querySelector(".ncf-tz")?.value ?? "UTC",
      };
      const s = container.querySelector(".ncf-start-date")?.value;
      const e = container.querySelector(".ncf-end-date")?.value;
      if (s) out.start_date = s;
      if (e) out.end_date = e;
      return out;
    },
    validate() {
      const val = container.querySelector(".ncf-cron")?.value?.trim() ?? "";
      if (!val) return "Schedule (cron) is required.";
      if (!isValidCron(val)) return "Invalid cron expression (5 space-separated fields).";
      return null;
    },
  };
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
