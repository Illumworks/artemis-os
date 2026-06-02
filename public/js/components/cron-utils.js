/**
 * cron-utils.js — Cron compilation + parse-and-match for preset picker.
 *
 * Modes:
 *   "every_n"  — Every N minutes / hours / days
 *   "daily"    — Every day at HH:MM
 *   "weekly"   — Specific days-of-week at HH:MM
 *   "monthly"  — Day-of-month at HH:MM
 *   "custom"   — Raw cron (fallback / power user)
 */

// ── Validation ────────────────────────────────────────────────────────────────

export function isValidCron(expr) {
  if (!expr || typeof expr !== "string") return false;
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return false;
  return parts.every((p) => /^[\d*\/,\-]+$/.test(p));
}

// ── Compilation — preset → cron string ───────────────────────────────────────

/**
 * compileCron(mode, fields) → cron string
 *
 * fields for each mode:
 *   every_n:  { n: number, unit: "minutes"|"hours"|"days" }
 *   daily:    { hour: number, minute: number }
 *   weekly:   { days: number[], hour: number, minute: number }
 *             days: 0=Sun,1=Mon,...,6=Sat
 *   monthly:  { dom: number, hour: number, minute: number }
 *   custom:   { cron: string }
 */
export function compileCron(mode, fields) {
  switch (mode) {
    case "every_n": {
      const n = Math.max(1, parseInt(fields.n, 10) || 1);
      if (fields.unit === "minutes") return `*/${n} * * * *`;
      if (fields.unit === "hours")   return `0 */${n} * * *`;
      if (fields.unit === "days")    return `0 0 */${n} * *`;
      return `*/${n} * * * *`; // fallback
    }
    case "daily": {
      const h = Math.max(0, Math.min(23, parseInt(fields.hour, 10) || 0));
      const m = Math.max(0, Math.min(59, parseInt(fields.minute, 10) || 0));
      return `${m} ${h} * * *`;
    }
    case "weekly": {
      const h = Math.max(0, Math.min(23, parseInt(fields.hour, 10) || 0));
      const m = Math.max(0, Math.min(59, parseInt(fields.minute, 10) || 0));
      const days = (fields.days || []).map(Number).sort((a, b) => a - b);
      if (!days.length) return `${m} ${h} * * *`; // no days = daily fallback
      const dowStr = _compressDays(days);
      return `${m} ${h} * * ${dowStr}`;
    }
    case "monthly": {
      const dom = Math.max(1, Math.min(31, parseInt(fields.dom, 10) || 1));
      const h   = Math.max(0, Math.min(23, parseInt(fields.hour, 10) || 0));
      const m   = Math.max(0, Math.min(59, parseInt(fields.minute, 10) || 0));
      return `${m} ${h} ${dom} * *`;
    }
    case "custom":
      return fields.cron || "* * * * *";
    default:
      return "* * * * *";
  }
}

/**
 * _compressDays(sortedDays) → dow string
 * Compresses contiguous runs into ranges: [1,2,3,4,5] → "1-5"
 * Non-contiguous or mixed: [1,3,5] → "1,3,5"
 */
function _compressDays(days) {
  if (!days.length) return "*";
  const runs = [];
  let start = days[0];
  let end = days[0];
  for (let i = 1; i < days.length; i++) {
    if (days[i] === end + 1) {
      end = days[i];
    } else {
      runs.push(start === end ? `${start}` : `${start}-${end}`);
      start = days[i];
      end = days[i];
    }
  }
  runs.push(start === end ? `${start}` : `${start}-${end}`);
  return runs.join(",");
}

// ── Parse-and-match — cron string → { mode, fields } ─────────────────────────

/**
 * parseCron(expr) → { mode, fields } | null
 *
 * Priority order:
 *   1. every_n  — *\/N * * * *  or  0 *\/N * * *  or  0 0 *\/N * *
 *   2. daily    — <m> <h> * * *
 *   3. weekly   — <m> <h> * * <dow>
 *   4. monthly  — <m> <h> <dom> * *
 *   5. custom   — fallback (returns { mode:"custom", fields:{ cron:expr } })
 *
 * Returns null only on malformed input; always returns at least custom fallback
 * for a well-formed 5-part expression.
 */
export function parseCron(expr) {
  if (!expr || typeof expr !== "string") return null;
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [min, hr, dom, mon, dow] = parts;

  // Mode 1 — Every N minutes: */N * * * *
  if (min.startsWith("*/") && hr === "*" && dom === "*" && mon === "*" && dow === "*") {
    const n = parseInt(min.slice(2), 10);
    if (!isNaN(n) && n >= 1 && n <= 59)
      return { mode: "every_n", fields: { n, unit: "minutes" } };
  }

  // Mode 1 — Every N hours: 0 */N * * *
  if (min === "0" && hr.startsWith("*/") && dom === "*" && mon === "*" && dow === "*") {
    const n = parseInt(hr.slice(2), 10);
    if (!isNaN(n) && n >= 1 && n <= 23)
      return { mode: "every_n", fields: { n, unit: "hours" } };
  }

  // Mode 1 — Every N days: 0 0 */N * *
  if (min === "0" && hr === "0" && dom.startsWith("*/") && mon === "*" && dow === "*") {
    const n = parseInt(dom.slice(2), 10);
    if (!isNaN(n) && n >= 1 && n <= 31)
      return { mode: "every_n", fields: { n, unit: "days" } };
  }

  // Mode 2 — Daily: <m> <h> * * *
  if (_isInt(min) && _isInt(hr) && dom === "*" && mon === "*" && dow === "*") {
    return {
      mode: "daily",
      fields: { hour: parseInt(hr, 10), minute: parseInt(min, 10) },
    };
  }

  // Mode 3 — Weekly: <m> <h> * * <dow>
  if (_isInt(min) && _isInt(hr) && dom === "*" && mon === "*" && dow !== "*") {
    const days = _expandDow(dow);
    if (days !== null) {
      return {
        mode: "weekly",
        fields: { hour: parseInt(hr, 10), minute: parseInt(min, 10), days },
      };
    }
  }

  // Mode 4 — Monthly: <m> <h> <dom> * *
  if (_isInt(min) && _isInt(hr) && _isInt(dom) && mon === "*" && dow === "*") {
    const domVal = parseInt(dom, 10);
    if (domVal >= 1 && domVal <= 31) {
      return {
        mode: "monthly",
        fields: { dom: domVal, hour: parseInt(hr, 10), minute: parseInt(min, 10) },
      };
    }
  }

  // Mode 5 — Custom fallback
  return { mode: "custom", fields: { cron: expr.trim() } };
}

/** True if the string is a plain non-negative integer (no *, /, ,, -, etc.) */
function _isInt(s) {
  return /^\d+$/.test(s);
}

/**
 * _expandDow(dowStr) → number[] (sorted, 0-6) or null if unparseable
 * Handles: single digits, comma lists, and ranges (1-5).
 * Returns null if the dow field contains step syntax (/) or is otherwise complex.
 */
function _expandDow(dowStr) {
  if (/[\/]/.test(dowStr)) return null; // step syntax → custom
  const days = new Set();
  const parts = dowStr.split(",");
  for (const part of parts) {
    if (/^\d+$/.test(part)) {
      const d = parseInt(part, 10);
      if (d < 0 || d > 7) return null;
      days.add(d % 7); // normalize 7 → 0 (Sun)
    } else if (/^\d+-\d+$/.test(part)) {
      const [lo, hi] = part.split("-").map(Number);
      if (lo > hi || lo < 0 || hi > 7) return null;
      for (let d = lo; d <= hi; d++) days.add(d % 7);
    } else {
      return null; // unrecognised token
    }
  }
  return [...days].sort((a, b) => a - b);
}

// ── Human-readable description ────────────────────────────────────────────────

const DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/**
 * describeCron(expr) → human-readable string, or "" if unrecognised.
 * Delegates to parseAndDescribe for preset patterns; falls back to the
 * original simple descriptions for edge cases.
 */
export function describeCron(expr) {
  const parsed = parseCron(expr);
  if (!parsed) return "";

  const { mode, fields } = parsed;

  switch (mode) {
    case "every_n": {
      const { n, unit } = fields;
      const label = unit === "minutes" ? "minute" : unit === "hours" ? "hour" : "day";
      return `Every ${n} ${label}${n !== 1 ? "s" : ""}`;
    }
    case "daily": {
      const hh = String(fields.hour).padStart(2, "0");
      const mm = String(fields.minute).padStart(2, "0");
      return `Every day at ${hh}:${mm}`;
    }
    case "weekly": {
      const hh = String(fields.hour).padStart(2, "0");
      const mm = String(fields.minute).padStart(2, "0");
      const dayLabels = fields.days.map((d) => DOW_NAMES[d]).join(", ");
      return `Every ${dayLabels} at ${hh}:${mm}`;
    }
    case "monthly": {
      const hh = String(fields.hour).padStart(2, "0");
      const mm = String(fields.minute).padStart(2, "0");
      return `Monthly on day ${fields.dom} at ${hh}:${mm}`;
    }
    case "custom":
      return isValidCron(fields.cron) ? "Custom schedule" : "";
  }
  return "";
}
