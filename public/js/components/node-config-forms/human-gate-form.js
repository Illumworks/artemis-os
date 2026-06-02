/**
 * human-gate-form.js — PIPE3
 * Typed config form for human_gate nodes.
 * Fields: approval_kind, approvers multi-select, timeout_hours, on_timeout.
 */

const APPROVAL_KINDS = [
  { value: "signal_brief",     label: "Signal Brief" },
  { value: "campaign_initiation", label: "Campaign Initiation" },
  { value: "content_draft",    label: "Content Draft" },
  { value: "policy_decision",  label: "Policy Decision" },
  { value: "manual",           label: "Manual / General" },
];

const ON_TIMEOUT_OPTIONS = [
  { value: "escalate",     label: "Escalate",     help: "If approver doesn't respond within timeout, ping a secondary approver." },
  { value: "auto_approve", label: "Auto-approve", help: "If timeout passes without response, automatically approve." },
  { value: "auto_reject",  label: "Auto-reject",  help: "If timeout passes without response, automatically reject." },
];

const DEFAULT_APPROVERS = [
  { email: "josh@amiralearning.com", name: "Josh" },
  { email: "angela@amiralearning.com", name: "Angela" },
  { email: "jon@amiralearning.com", name: "Jon" },
];

// ── Render ───────────────────────────────────────────────────────────────────

export function renderHumanGateForm(config, container) {
  const cfg = config ?? {};
  const approvalKind = cfg.approval_kind ?? "manual";
  const approvers = Array.isArray(cfg.approvers) ? cfg.approvers : [];
  const escalationTo = Array.isArray(cfg.escalation_to) ? cfg.escalation_to : [];
  const timeoutHours = cfg.timeout_hours ?? 72;
  const onTimeout = cfg.on_timeout ?? "escalate";

  // Custom kind: not in the preset list
  const isCustomKind = approvalKind && !APPROVAL_KINDS.find((k) => k.value === approvalKind);

  container.innerHTML = `
    <div class="ncf ncf-gate">
      <div class="ncf-field">
        <label class="ncf-label">Approval kind</label>
        <select class="ncf-select ncf-approval-kind">
          ${APPROVAL_KINDS.map(
            (k) => `<option value="${k.value}"${k.value === approvalKind ? " selected" : ""}>${k.label}</option>`
          ).join("")}
          <option value="__custom__"${isCustomKind ? " selected" : ""}>Custom…</option>
        </select>
        <input class="ncf-input ncf-approval-kind-custom" type="text"
          value="${isCustomKind ? _esc(approvalKind) : ""}"
          placeholder="Enter custom kind"
          style="${isCustomKind ? "" : "display:none"}">
      </div>

      <div class="ncf-field">
        <label class="ncf-label">Approvers</label>
        <div class="ncf-multiselect" data-ncf="approvers">
          <div class="ncf-tags"></div>
          <div class="ncf-ms-input-row">
            <input class="ncf-ms-search" type="text" placeholder="Add approver…"
              autocomplete="off" autocapitalize="none">
            <div class="ncf-ms-results" hidden></div>
          </div>
        </div>
        <div class="ncf-hint">Press Enter or pick from list to add. Click tag to remove.</div>
      </div>

      <div class="ncf-row">
        <div class="ncf-field ncf-field--half">
          <label class="ncf-label">Timeout (hours)</label>
          <input class="ncf-input ncf-timeout" type="number" min="1" step="1"
            value="${_escVal(timeoutHours)}" placeholder="72">
        </div>
        <div class="ncf-field ncf-field--half">
          <label class="ncf-label">On timeout</label>
          <select class="ncf-select ncf-on-timeout">
            ${ON_TIMEOUT_OPTIONS.map(
              (o) => `<option value="${o.value}"${o.value === onTimeout ? " selected" : ""}>${o.label}</option>`
            ).join("")}
          </select>
          <div class="ncf-timeout-help">
            ${ON_TIMEOUT_OPTIONS.map((o) => `<div><strong>${o.value}</strong>: ${_esc(o.help)}</div>`).join("")}
          </div>
        </div>
      </div>

      <div class="ncf-field ncf-escalation-field" ${onTimeout === "escalate" ? "" : "hidden"}>
        <label class="ncf-label">Escalate to</label>
        <div class="ncf-multiselect" data-ncf="escalation_to">
          <div class="ncf-tags"></div>
          <div class="ncf-ms-input-row">
            <input class="ncf-ms-search" type="text" placeholder="Add escalation approver email…"
              autocomplete="off" autocapitalize="none">
            <div class="ncf-ms-results" hidden></div>
          </div>
        </div>
        <div class="ncf-hint">Required when timeout escalates. Press Enter or pick from list to add.</div>
      </div>
    </div>
  `;

  // Wire approval_kind custom toggle
  const kindSelect = container.querySelector(".ncf-approval-kind");
  const kindCustom = container.querySelector(".ncf-approval-kind-custom");
  kindSelect.addEventListener("change", () => {
    if (kindSelect.value === "__custom__") {
      kindCustom.style.display = "";
      kindCustom.focus();
    } else {
      kindCustom.style.display = "none";
    }
  });

  const onTimeoutEl = container.querySelector(".ncf-on-timeout");
  const escalationField = container.querySelector(".ncf-escalation-field");
  onTimeoutEl.addEventListener("change", () => {
    escalationField.hidden = onTimeoutEl.value !== "escalate";
  });

  // Wire multi-select approvers
  const msWrap = container.querySelector('[data-ncf="approvers"]');
  const escalationWrap = container.querySelector('[data-ncf="escalation_to"]');

  const approverPicker = _wireEmailPicker(msWrap, approvers);
  const escalationPicker = _wireEmailPicker(escalationWrap, escalationTo);

  return {
    getValues() {
      let kind = container.querySelector(".ncf-approval-kind")?.value ?? "manual";
      if (kind === "__custom__") {
        kind = container.querySelector(".ncf-approval-kind-custom")?.value?.trim() || "manual";
      }
      const timeout = parseInt(container.querySelector(".ncf-timeout")?.value ?? "72", 10);
      return {
        approval_kind: kind,
        approvers: approverPicker.values(),
        timeout_hours: isNaN(timeout) ? 72 : timeout,
        on_timeout: onTimeoutEl?.value ?? "escalate",
        escalation_to: escalationPicker.values(),
      };
    },
    validate() {
      const kind = container.querySelector(".ncf-approval-kind")?.value;
      if (kind === "__custom__") {
        const custom = container.querySelector(".ncf-approval-kind-custom")?.value?.trim();
        if (!custom) return "Custom approval kind cannot be empty.";
      }
      if (onTimeoutEl?.value === "escalate" && !escalationPicker.values().length) {
        return "Specify at least one escalation approver.";
      }
      return null;
    },
  };
}

function _wireEmailPicker(msWrap, initial) {
  const tagsEl = msWrap.querySelector(".ncf-tags");
  const searchEl = msWrap.querySelector(".ncf-ms-search");
  const resultsEl = msWrap.querySelector(".ncf-ms-results");

  let _selected = [...initial];

  function _renderTags() {
    tagsEl.innerHTML = _selected
      .map(
        (email) =>
          `<span class="ncf-tag" data-email="${_esc(email)}">${_esc(email)}<button type="button" class="ncf-tag-rm" aria-label="Remove">×</button></span>`
      )
      .join("");
  }

  function _renderSuggestions(q) {
    const needle = q.toLowerCase();
    const avail = DEFAULT_APPROVERS.filter(
      (p) => !_selected.includes(p.email) && `${p.name} ${p.email}`.toLowerCase().includes(needle)
    );
    if (!avail.length && !q) {
      resultsEl.hidden = true;
      return;
    }
    const items = avail.map(
      (p) => `<button type="button" class="ncf-picker-item" data-email="${_esc(p.email)}">
        <span class="ncf-picker-name">${_esc(p.name)}</span>
        <span class="ncf-picker-sub">${_esc(p.email)}</span>
      </button>`
    );
    if (q && isValidEmail(q) && !DEFAULT_APPROVERS.some((p) => p.email === q) && !_selected.includes(q)) {
      items.unshift(
        `<button type="button" class="ncf-picker-item ncf-picker-item--free" data-email="${_esc(q)}">Add "${_esc(q)}"</button>`
      );
    }
    resultsEl.innerHTML = items.join("");
    resultsEl.hidden = !items.length;
  }

  function _addApprover(email) {
    email = email.trim();
    if (isValidEmail(email) && !_selected.includes(email)) {
      _selected.push(email);
      _renderTags();
    }
    searchEl.value = "";
    resultsEl.hidden = true;
  }

  _renderTags();

  tagsEl.addEventListener("click", (e) => {
    const rm = e.target.closest(".ncf-tag-rm");
    if (!rm) return;
    const email = rm.closest(".ncf-tag")?.dataset.email;
    if (email) _selected = _selected.filter((x) => x !== email);
    _renderTags();
  });

  searchEl.addEventListener("focus", () => _renderSuggestions(searchEl.value));
  searchEl.addEventListener("input", () => _renderSuggestions(searchEl.value));
  searchEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const q = searchEl.value.trim();
      if (q) _addApprover(q);
    }
  });
  searchEl.addEventListener("blur", () => {
    setTimeout(() => { resultsEl.hidden = true; }, 150);
  });

  resultsEl.addEventListener("click", (e) => {
    const item = e.target.closest(".ncf-picker-item");
    if (item) _addApprover(item.dataset.email);
  });

  return {
    values() { return [..._selected]; },
  };
}

function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(email ?? "").trim());
}

function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
function _escVal(v) { return String(v ?? ""); }
