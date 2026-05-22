/**
 * agent-invocation-form.js — PIPE3
 * Typed config form for agent_invocation nodes.
 * Fields: agent picker, mode, cost_cap_usd, optional provider/model override.
 */

import { PROVIDER_LABELS, PROVIDER_PICKERS, getSourceModels } from "../../ui/model-selector.js";

const MODES = ["scheduled", "manual", "backfill"];
const PROVIDERS = Object.keys(PROVIDER_PICKERS).filter((p) => p !== "hermes");
const PROVIDER_ALIASES = { anthropic: "claude-code", openai: "codex" };
const COST_CAP_HELP =
  "Stops execution when total LLM cost for this run exceeds this cap. Applies to all provider modes (API, CLI, local). Run is marked partial_complete and the cap-hit reason is logged.";

// ── Searchable agent picker ──────────────────────────────────────────────────

let _agentCache = null; // { ts, agents[] }

async function _fetchAgents() {
  if (_agentCache && Date.now() - _agentCache.ts < 30_000) return _agentCache.agents;
  try {
    const res = await fetch("/api/agents");
    if (!res.ok) return [];
    const data = await res.json();
    const agents = Array.isArray(data) ? data : (data.agents ?? data.items ?? []);
    _agentCache = { ts: Date.now(), agents };
    return agents;
  } catch {
    return [];
  }
}

// ── Render ───────────────────────────────────────────────────────────────────

/**
 * renderAgentInvocationForm(config, container)
 * Renders the form into container. Returns { getValues } so the drawer can
 * read the current field values before saving.
 */
export function renderAgentInvocationForm(config, container) {
  const cfg = config ?? {};
  const agentId = cfg.agent_id ?? "";
  const mode = cfg.mode ?? "scheduled";
  const costCap = cfg.cost_cap_usd != null ? cfg.cost_cap_usd : 1.0;
  const providerOverride = PROVIDER_ALIASES[cfg.provider_override] ?? cfg.provider_override ?? "";
  const modelOverride = cfg.model_override ?? "";
  const showOverride = !!(providerOverride || modelOverride);

  container.innerHTML = `
    <div class="ncf ncf-agent">
      <div class="ncf-field">
        <label class="ncf-label ncf-required">Agent</label>
        <div class="ncf-picker" data-ncf-picker="agent">
          <input class="ncf-search" type="text" placeholder="Search agents…"
            autocomplete="off" value="${_esc(agentId)}">
          <div class="ncf-picker-results" hidden></div>
          <input type="hidden" class="ncf-agent-id" value="${_esc(agentId)}">
        </div>
        <div class="ncf-hint">Required — links this node to an agent definition.</div>
      </div>

      <div class="ncf-field">
        <label class="ncf-label">Mode</label>
        <select class="ncf-select ncf-mode">
          ${MODES.map((m) => `<option value="${m}"${m === mode ? " selected" : ""}>${m}</option>`).join("")}
        </select>
      </div>

      <div class="ncf-field">
        <label class="ncf-label ncf-label-with-help">
          <span>Cost cap (USD)</span>
          <span class="ncf-help" tabindex="0" aria-label="${_esc(COST_CAP_HELP)}" data-tooltip="${_esc(COST_CAP_HELP)}">?</span>
        </label>
        <input class="ncf-input ncf-cost-cap" type="number" min="0" step="0.01"
          value="${_escVal(costCap)}" placeholder="1.00">
        <div class="ncf-hint">Optional — leave blank for no cap.</div>
      </div>

      <details class="ncf-details" ${showOverride ? "open" : ""}>
        <summary class="ncf-summary">Provider override (optional)</summary>
        <div class="ncf-details-body">
          <div class="ncf-field">
            <label class="ncf-label">Provider</label>
            <select class="ncf-select ncf-provider-override">
              <option value="">— use agent default —</option>
              ${PROVIDERS.map((p) => `<option value="${p}"${p === providerOverride ? " selected" : ""}>${_esc(PROVIDER_LABELS[p] || p)}</option>`).join("")}
            </select>
          </div>
          <div class="ncf-field">
            <label class="ncf-label">Model override</label>
            <select class="ncf-select ncf-model-override"></select>
          </div>
        </div>
      </details>
    </div>
  `;

  // Wire picker
  const pickerWrap = container.querySelector("[data-ncf-picker='agent']");
  const searchEl = pickerWrap.querySelector(".ncf-search");
  const resultsEl = pickerWrap.querySelector(".ncf-picker-results");
  const hiddenEl = pickerWrap.querySelector(".ncf-agent-id");
  const providerEl = container.querySelector(".ncf-provider-override");
  const modelEl = container.querySelector(".ncf-model-override");

  let _allAgents = [];
  _fetchAgents().then((agents) => {
    _allAgents = agents;
    // Pre-populate search label if we have a matching agent
    const found = agents.find((a) => (a.agent_id ?? a.id) === agentId);
    if (found) searchEl.value = found.name ?? found.agent_id ?? agentId;
    _renderResults("");
  });

  function _renderResults(q) {
    const needle = _safeStr(q).toLowerCase();
    const matches = q
      ? _allAgents.filter(
          (a) =>
            _safeStr(a.name).toLowerCase().includes(needle) ||
            _safeStr(a.agent_id ?? a.id).toLowerCase().includes(needle)
        )
      : _allAgents;
    if (!matches.length) {
      resultsEl.innerHTML = `<div class="ncf-picker-empty">No agents found</div>`;
    } else {
      resultsEl.innerHTML = matches
        .slice(0, 20)
        .map(
          (a) =>
            `<button type="button" class="ncf-picker-item" data-id="${_esc(a.agent_id ?? a.id ?? "")}"
              data-name="${_esc(a.name ?? a.agent_id ?? "")}">
              <span class="ncf-picker-name">${_esc(a.name ?? a.agent_id ?? "—")}</span>
              <span class="ncf-picker-sub">${_esc(a.agent_id ?? a.id ?? "")}</span>
            </button>`
        )
        .join("");
    }
  }

  function _renderModelOptions() {
    if (!modelEl) return;
    const provider = providerEl?.value ?? "";
    if (!provider) {
      modelEl.innerHTML = `<option value="">— use agent default —</option>`;
      modelEl.disabled = true;
      return;
    }
    const models = getSourceModels(provider) || [];
    const selected = models.some((m) => m.value === modelOverride) ? modelOverride : "";
    modelEl.disabled = false;
    modelEl.innerHTML = [
      `<option value="">— use provider default —</option>`,
      ...models
        .filter((m) => m.value)
        .map((m) => `<option value="${_esc(m.value)}"${m.value === selected ? " selected" : ""}>${_esc(m.label || m.value)}</option>`),
    ].join("");
  }

  searchEl.addEventListener("focus", () => {
    _renderResults(searchEl.value);
    resultsEl.hidden = false;
  });
  searchEl.addEventListener("input", () => {
    hiddenEl.value = searchEl.value; // allow freeform ID entry
    _renderResults(searchEl.value);
    resultsEl.hidden = false;
  });
  searchEl.addEventListener("blur", () => {
    setTimeout(() => { resultsEl.hidden = true; }, 150);
  });

  resultsEl.addEventListener("click", (e) => {
    const item = e.target.closest(".ncf-picker-item");
    if (!item) return;
    hiddenEl.value = item.dataset.id;
    searchEl.value = item.dataset.name || item.dataset.id;
    resultsEl.hidden = true;
  });

  providerEl?.addEventListener("change", () => {
    if (modelEl) modelEl.dataset.previous = modelEl.value;
    _renderModelOptions();
  });
  _renderModelOptions();

  // Return value extractor
  return {
    getValues() {
      const provVal = container.querySelector(".ncf-provider-override")?.value ?? "";
      const modelVal = (container.querySelector(".ncf-model-override")?.value ?? "").trim();
      const capVal = parseFloat(container.querySelector(".ncf-cost-cap")?.value ?? "");

      const out = {
        agent_id: container.querySelector(".ncf-agent-id")?.value ?? "",
        mode: container.querySelector(".ncf-mode")?.value ?? "scheduled",
      };
      if (!isNaN(capVal)) out.cost_cap_usd = capVal;
      if (provVal) out.provider_override = provVal;
      if (modelVal) out.model_override = modelVal;
      return out;
    },
    validate() {
      const id = container.querySelector(".ncf-agent-id")?.value ?? "";
      if (!id.trim()) return "Agent is required.";
      return null;
    },
  };
}

function _safeStr(v) {
  return typeof v === "string" ? v : String(v ?? "");
}
function _esc(str) {
  const d = document.createElement("div");
  d.textContent = String(str ?? "");
  return d.innerHTML;
}
function _escVal(v) { return String(v ?? ""); }
