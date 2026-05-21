/**
 * conditional-form.js — PIPE3
 * Typed config form for conditional nodes.
 * v1: single-line predicate (op + LHS + RHS) + true/false branch labels.
 * Power-user fallback: "Raw JSONLogic" toggle for complex predicates.
 */

const OPERATORS = [
  { value: "equals",       label: "equals" },
  { value: "not_equals",   label: "does not equal" },
  { value: "greater_than", label: "is greater than" },
  { value: "less_than",    label: "is less than" },
  { value: "contains",     label: "contains" },
  { value: "in_list",      label: "is in list (comma-sep)" },
];

const LHS_FIELDS = [
  { value: "signal.score",        label: "Signal score" },
  { value: "signal.source",       label: "Signal source" },
  { value: "signal.channel",      label: "Signal channel" },
  { value: "context.run_count",   label: "Run count" },
  { value: "context.last_status", label: "Last run status" },
  { value: "context.hour_of_day", label: "Hour of day" },
  { value: "input.value",         label: "Input value" },
];

// ── Render ───────────────────────────────────────────────────────────────────

export function renderConditionalForm(config, container) {
  const cfg = config ?? {};
  const pred = cfg.predicate ?? {};
  const op = pred.op ?? "equals";
  const left = pred.left ?? "signal.score";
  const right = pred.right ?? "";
  const trueLabel = cfg.true_label ?? "";
  const falseLabel = cfg.false_label ?? "";
  const rawJsonLogic = cfg.expression ?? "";

  // Determine initial mode: raw JSONLogic if expression present but no structured predicate
  const initRaw = !!(rawJsonLogic && !pred.op);

  container.innerHTML = `
    <div class="ncf ncf-cond">
      <div class="ncf-field">
        <div class="ncf-toggle-row">
          <span class="ncf-label">Predicate</span>
          <label class="ncf-inline-toggle" title="Toggle raw JSONLogic mode">
            <input type="checkbox" class="ncf-jsonlogic-toggle"${initRaw ? " checked" : ""}>
            <span class="ncf-inline-toggle-label">Raw JSONLogic</span>
          </label>
        </div>

        <div class="ncf-pred-builder" style="${initRaw ? "display:none" : ""}">
          <div class="ncf-pred-row">
            <select class="ncf-select ncf-pred-left">
              ${LHS_FIELDS.map(
                (f) => `<option value="${f.value}"${f.value === left ? " selected" : ""}>${f.label}</option>`
              ).join("")}
            </select>
            <select class="ncf-select ncf-pred-op">
              ${OPERATORS.map(
                (o) => `<option value="${o.value}"${o.value === op ? " selected" : ""}>${o.label}</option>`
              ).join("")}
            </select>
            <input class="ncf-input ncf-pred-right" type="text"
              value="${_esc(right)}" placeholder="value">
          </div>
        </div>

        <div class="ncf-jsonlogic-editor" style="${initRaw ? "" : "display:none"}">
          <textarea class="ncf-jsonlogic-raw" rows="5" spellcheck="false"
            placeholder='{">":[{"var":"signal.score"},0.7]}'>${_esc(rawJsonLogic)}</textarea>
          <div class="ncf-hint">JSONLogic JSON — complex multi-condition predicates.</div>
        </div>
      </div>

      <div class="ncf-row">
        <div class="ncf-field ncf-field--half">
          <label class="ncf-label">True branch label</label>
          <input class="ncf-input ncf-true-label" type="text"
            value="${_esc(trueLabel)}" placeholder="e.g. Hot signal — fast lane">
        </div>
        <div class="ncf-field ncf-field--half">
          <label class="ncf-label">False branch label</label>
          <input class="ncf-input ncf-false-label" type="text"
            value="${_esc(falseLabel)}" placeholder="e.g. Low signal — skip">
        </div>
      </div>
    </div>
  `;

  const toggleEl = container.querySelector(".ncf-jsonlogic-toggle");
  const builderEl = container.querySelector(".ncf-pred-builder");
  const jsonEditorEl = container.querySelector(".ncf-jsonlogic-editor");

  toggleEl.addEventListener("change", () => {
    if (toggleEl.checked) {
      builderEl.style.display = "none";
      jsonEditorEl.style.display = "";
    } else {
      builderEl.style.display = "";
      jsonEditorEl.style.display = "none";
    }
  });

  return {
    getValues() {
      const isRaw = container.querySelector(".ncf-jsonlogic-toggle")?.checked;
      const out = {
        true_label: (container.querySelector(".ncf-true-label")?.value ?? "").trim(),
        false_label: (container.querySelector(".ncf-false-label")?.value ?? "").trim(),
      };
      if (isRaw) {
        const raw = (container.querySelector(".ncf-jsonlogic-raw")?.value ?? "").trim();
        if (raw) out.expression = raw;
      } else {
        out.predicate = {
          op: container.querySelector(".ncf-pred-op")?.value ?? "equals",
          left: container.querySelector(".ncf-pred-left")?.value ?? "signal.score",
          right: (container.querySelector(".ncf-pred-right")?.value ?? "").trim(),
        };
      }
      return out;
    },
    validate() {
      const isRaw = container.querySelector(".ncf-jsonlogic-toggle")?.checked;
      if (isRaw) {
        const raw = (container.querySelector(".ncf-jsonlogic-raw")?.value ?? "").trim();
        if (raw) {
          try { JSON.parse(raw); } catch { return "JSONLogic expression is not valid JSON."; }
        }
      }
      return null;
    },
  };
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
