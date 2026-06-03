import { escapeHtml } from "../core/utils.js";

export const SIGNAL_GROUP_KEY = "artemis.signals.group-by";
export const SIGNAL_COLLAPSED_KEY = "artemis.signals.tree.collapsed";

export const SIGNAL_GROUPS = ["state", "reason", "geography", "urgency", "pipeline", "flat"];
export const SIGNAL_STATUSES = [
  "pending_qualification",
  "qualified",
  "approved",
  "suppressed_stale",
  "rejected_hard_filter",
  "rejected_at_gate_1",
  "snoozed",
  "archived",
  "in_inbox",
];
export const SIGNAL_URGENCIES = ["hot", "standard", "enrichment"];

const STATUS_LABELS = {
  pending_qualification: "Pending qualification",
  qualified: "Qualified",
  approved: "Approved",
  suppressed_stale: "Suppressed stale",
  rejected_hard_filter: "Rejected hard filter",
  rejected_at_gate_1: "Rejected",
  snoozed: "Snoozed",
  archived: "Archived",
  in_inbox: "In inbox",
};

const STATUS_ORDER = [
  "qualified",
  "approved",
  "pending_qualification",
  "in_inbox",
  "snoozed",
  "suppressed_stale",
  "rejected_hard_filter",
  "rejected_at_gate_1",
  "archived",
];

const URGENCY_ORDER = { hot: 0, standard: 1, enrichment: 2, low: 2 };

function esc(value) {
  return escapeHtml(value == null ? "" : String(value));
}

function readJsonStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export function readSignalGroupMode() {
  try {
    const stored = localStorage.getItem(SIGNAL_GROUP_KEY);
    return SIGNAL_GROUPS.includes(stored) ? stored : "state";
  } catch {
    return "state";
  }
}

export function writeSignalGroupMode(mode) {
  if (!SIGNAL_GROUPS.includes(mode)) return;
  try { localStorage.setItem(SIGNAL_GROUP_KEY, mode); } catch { /* no-op */ }
}

export function readCollapsedSignalGroups() {
  const value = readJsonStorage(SIGNAL_COLLAPSED_KEY, {});
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function writeCollapsedSignalGroups(collapsed) {
  try { localStorage.setItem(SIGNAL_COLLAPSED_KEY, JSON.stringify(collapsed || {})); } catch { /* no-op */ }
}

function reasonCodeLabel(entry) {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object") return entry.code || entry.reasonCode || entry.label || "";
  return "";
}

function confidenceValue(entry) {
  if (!entry || typeof entry !== "object") return null;
  const value = entry.confidence ?? entry.score ?? null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeUrgency(value) {
  if (value === "low") return "enrichment";
  return SIGNAL_URGENCIES.includes(value) ? value : "standard";
}

function normalizePipelineRun(signal) {
  const run = signal.pipelineRun || signal.pipeline_run || null;
  const id = signal.pipelineRunId || signal.pipeline_run_id || run?.id || null;
  if (!id) return null;
  return {
    id,
    pipelineId: run?.pipelineId || run?.pipeline_id || signal.pipelineId || null,
    pipelineName: run?.pipelineName || run?.pipeline_name || signal.pipelineName || "Marketing Pipeline",
    status: run?.status || "running",
    startedAt: run?.startedAt || run?.started_at || run?.createdAt || run?.created_at || null,
  };
}

export function normalizeSignal(signal = {}) {
  const reasonCodes = Array.isArray(signal.reasonCodes) ? signal.reasonCodes : [];
  const codeLabels = reasonCodes.map(reasonCodeLabel).filter(Boolean);
  const provenance = signal.provenance && typeof signal.provenance === "object" ? signal.provenance : {};
  const district = signal.district || signal.districtName || signal.districtId || provenance.district || "";
  const stateCode = (signal.stateCode || signal.state || provenance.state || "").toString().toUpperCase();
  const discoveredAt = signal.discoveredAt || signal.createdAt || signal.updatedAt || null;
  const pipelineRun = normalizePipelineRun(signal);
  return {
    ...signal,
    id: signal.id,
    signalStatus: signal.signalStatus || signal.status || "pending_qualification",
    headline: signal.headline || codeLabels[0] || "Untitled signal",
    summary: signal.summary || signal.whyFlagged || signal.evidence || "",
    whyFlagged: signal.whyFlagged || provenance.why_flagged || provenance.whyFlagged || signal.summary || "",
    sourceType: signal.sourceType || provenance.sourceType || "manual",
    sourceUrl: signal.sourceUrl || provenance.sourceUrl || null,
    sourceTitle: signal.sourceTitle || provenance.sourceTitle || null,
    sourceAuthor: signal.sourceAuthor || provenance.sourceAuthor || provenance.speakerAttribution || null,
    sourcePublishedAt: signal.sourcePublishedAt || provenance.sourcePublishedAt || provenance.source_published_at || null,
    speakerAttribution: signal.speakerAttribution || provenance.speakerAttribution || signal.sourceAuthor || null,
    stateCode,
    district,
    reasonCodes,
    reasonCodeLabels: codeLabels,
    urgencyTier: normalizeUrgency(signal.urgencyTier),
    discoveredAt,
    discoveredBy: signal.discoveredBy || provenance.discovered_by || provenance.discoveredBy || "manual",
    agentRunId: signal.agentRunId || provenance.agent_run_id || provenance.agentRunId || null,
    relatedSignalsCount: Number(signal.relatedSignalsCount || 0),
    qualificationJson: signal.qualificationJson || null,
    briefId: signal.briefId || signal.campaignBriefId || null,
    pipelineRun,
    approval: signal.approval || signal.gateApproval || null,
    // DIST4: district context from qualification_json.districtContext
    districtContext: signal.districtContext || (signal.qualificationJson && signal.qualificationJson.districtContext) || null,
  };
}

export function makeSignalSearchText(signal) {
  return [
    signal.id,
    signal.headline,
    signal.summary,
    signal.district,
    signal.stateCode,
    signal.sourceType,
    signal.signalStatus,
    signal.pipelineRun?.pipelineName,
    signal.pipelineRun?.id,
    ...signal.reasonCodeLabels,
  ].filter(Boolean).join(" ").toLowerCase();
}

export function filterSignals(signals, options = {}) {
  const query = (options.query || "").trim().toLowerCase();
  const filters = options.filters || {};
  // DIST4: hideUnsupported toggle — default OFF so D4 signals stay visible by default.
  const hideUnsupported = !!options.hideUnsupported;
  return signals.filter((signal) => {
    if (query && !makeSignalSearchText(signal).includes(query)) return false;
    if (filters.urgencies?.length && !filters.urgencies.includes(signal.urgencyTier)) return false;
    if (filters.statuses?.length && !filters.statuses.includes(signal.signalStatus)) return false;
    if (filters.reasons?.length && !signal.reasonCodeLabels.some((c) => filters.reasons.includes(c))) return false;
    if (filters.geographies?.length && !filters.geographies.includes(signal.stateCode)) return false;
    // DIST4: when toggle is on, hide signals whose districtContext.tierFlag === "unsupported_tier"
    if (hideUnsupported && signal.districtContext?.tierFlag === "unsupported_tier") return false;
    return true;
  });
}

export function sortSignals(signals, sort = "newest") {
  const copy = [...signals];
  copy.sort((a, b) => {
    if (sort === "urgency") {
      const urgencyDelta = (URGENCY_ORDER[a.urgencyTier] ?? 9) - (URGENCY_ORDER[b.urgencyTier] ?? 9);
      if (urgencyDelta !== 0) return urgencyDelta;
    }
    return Date.parse(b.discoveredAt || 0) - Date.parse(a.discoveredAt || 0) || Number(b.id || 0) - Number(a.id || 0);
  });
  return copy;
}

function groupLabel(mode, key) {
  if (mode === "state") return STATUS_LABELS[key] || key.replace(/_/g, " ");
  if (mode === "reason") return key || "Uncoded";
  if (mode === "geography") return key || "Unknown state";
  if (mode === "urgency") return key[0]?.toUpperCase() + key.slice(1);
  if (mode === "pipeline") return key;
  return "Signals";
}

function statusSort(a, b) {
  const ai = STATUS_ORDER.indexOf(a.key);
  const bi = STATUS_ORDER.indexOf(b.key);
  return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a.key.localeCompare(b.key);
}

export function buildSignalTree(signals, mode = "state", sort = "newest") {
  const add = (map, key, signal) => {
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(signal);
  };
  const map = new Map();
  if (mode === "flat") {
    return [{ key: "flat", label: "Signals", signals: sortSignals(signals, sort), children: [] }];
  }
  for (const signal of signals) {
    if (mode === "state") add(map, signal.signalStatus || "pending_qualification", signal);
    if (mode === "urgency") add(map, signal.urgencyTier || "standard", signal);
    if (mode === "reason") {
      const codes = signal.reasonCodeLabels.length ? signal.reasonCodeLabels : ["Uncoded"];
      for (const code of codes) add(map, code, signal);
    }
    if (mode === "geography") add(map, signal.stateCode || "Unknown", signal);
    if (mode === "pipeline") {
      const run = signal.pipelineRun;
      const key = run ? `${run.pipelineName} · ${String(run.id).slice(0, 8)}` : "No pipeline run";
      add(map, key, signal);
    }
  }
  let groups = [...map.entries()].map(([key, rows]) => ({
    key,
    label: groupLabel(mode, key),
    signals: mode === "geography" ? [] : sortSignals(rows, sort),
    children: mode === "geography" ? buildDistrictChildren(rows, sort) : [],
  }));
  if (mode === "state") groups.sort(statusSort);
  else if (mode === "urgency") groups.sort((a, b) => (URGENCY_ORDER[a.key] ?? 9) - (URGENCY_ORDER[b.key] ?? 9));
  else groups.sort((a, b) => a.label.localeCompare(b.label));
  return groups;
}

function buildDistrictChildren(signals, sort) {
  const map = new Map();
  for (const signal of signals) {
    const key = signal.district || "Unknown district";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(signal);
  }
  return [...map.entries()]
    .map(([key, rows]) => ({ key, label: key, signals: sortSignals(rows, sort), children: [] }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function summarizeFilterOptions(signals) {
  const reasons = new Set();
  const geographies = new Set();
  const statuses = new Set();
  for (const signal of signals) {
    signal.reasonCodeLabels.forEach((c) => reasons.add(c));
    if (signal.stateCode) geographies.add(signal.stateCode);
    if (signal.signalStatus) statuses.add(signal.signalStatus);
  }
  return {
    reasons: [...reasons].sort(),
    geographies: [...geographies].sort(),
    statuses: [...statuses].sort(statusValueSort),
  };
}

function statusValueSort(a, b) {
  return STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b);
}

function timeAgo(value) {
  if (!value) return "new";
  const diff = Date.now() - Date.parse(value);
  if (!Number.isFinite(diff) || diff < 0) return "new";
  const hours = Math.floor(diff / 36e5);
  if (hours < 24) return `${Math.max(1, hours)}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function signalTitle(signal) {
  const code = signal.reasonCodeLabels[0] || signal.campaignFamily || signal.sourceType;
  const place = signal.district || signal.stateCode || signal.headline;
  return `${code} · ${place}`;
}

function rowHtml(signal, selectedId) {
  const selected = String(signal.id) === String(selectedId);
  const initial = (signal.district || signal.headline || "?").trim()[0]?.toUpperCase() || "?";
  const pipelineBadge = signal.pipelineRun
    ? `<span class="mkt-signal-row-pipeline">${esc(signal.pipelineRun.pipelineName)} · ${esc(String(signal.pipelineRun.id).slice(0, 8))}</span>`
    : "";
  return `
    <button class="mkt-signal-row${selected ? " is-selected" : ""}" type="button"
            data-signal-row="${esc(signal.id)}" aria-pressed="${selected ? "true" : "false"}">
      <span class="mkt-signal-row-geo">${esc(signal.stateCode || "--")}</span>
      <span class="mkt-signal-row-initial">${esc(initial)}</span>
      <span class="mkt-signal-row-main">
        <span class="mkt-signal-row-title">${esc(signalTitle(signal))}</span>
        <span class="mkt-signal-row-sub">${esc(signal.headline)}</span>
        ${pipelineBadge}
      </span>
      <span class="mkt-signal-row-side">
        <span class="mkt-signal-row-urgency mkt-signal-row-urgency--${esc(signal.urgencyTier)}">${esc(signal.urgencyTier)}</span>
        <span class="mkt-signal-row-age">${esc(timeAgo(signal.discoveredAt))}</span>
      </span>
      <span class="mkt-signal-row-dot mkt-signal-row-dot--${esc(signal.signalStatus)}" title="${esc(signal.signalStatus)}"></span>
    </button>`;
}

function groupHtml(group, context, depth = 0) {
  const id = `${context.mode}:${group.key}`;
  const collapsed = !context.forceOpen && !!context.collapsed[id];
  const count = group.signals.length + group.children.reduce((sum, child) => sum + child.signals.length, 0);
  const rows = group.signals.length
    ? group.signals.map((signal) => rowHtml(signal, context.selectedId)).join("")
    : "";
  const children = group.children.map((child) => groupHtml(child, context, depth + 1)).join("");
  const empty = !count ? '<p class="mkt-signal-folder-empty">No signals matching filter.</p>' : "";
  return `
    <section class="mkt-signal-folder mkt-signal-folder--depth-${depth}" data-signal-folder="${esc(id)}">
      <button class="mkt-signal-folder-head" type="button" data-signal-folder-toggle="${esc(id)}">
        <span class="mkt-signal-folder-caret">${collapsed ? "›" : "⌄"}</span>
        <span>${esc(group.label)}</span>
        <span class="mkt-signal-folder-count">${count}</span>
      </button>
      <div class="mkt-signal-folder-body" ${collapsed ? "hidden" : ""}>${empty}${children}${rows}</div>
    </section>`;
}

export function renderReasonChips(signal) {
  if (!signal.reasonCodes.length) return '<span class="mkt-signal-detail-muted">No reason codes assigned.</span>';
  return signal.reasonCodes.map((entry) => {
    const code = reasonCodeLabel(entry) || "Uncoded";
    const confidence = confidenceValue(entry);
    const label = confidence == null ? code : `${code} ${(confidence * 100).toFixed(0)}%`;
    return `<span class="mkt-signal-detail-chip">${esc(label)}</span>`;
  }).join("");
}

function renderQualifierAudit(signal) {
  const qual = signal.qualificationJson;
  if (!qual) return '<p class="mkt-signal-detail-muted">No qualifier audit yet.</p>';
  const fired = [
    ...(Array.isArray(qual.hardFiltersFired) ? qual.hardFiltersFired : []),
    ...(Array.isArray(qual.boostsApplied) ? qual.boostsApplied : []),
    ...(Array.isArray(qual.suppressionsApplied) ? qual.suppressionsApplied : []),
    ...(Array.isArray(qual.ruleApplications) ? qual.ruleApplications : []),
  ];
  if (!fired.length && Array.isArray(qual.scores)) {
    return qual.scores.map((score) => `
      <div class="mkt-signal-audit-row">
        <span>${esc(score.campaignFamily || "ruleset")}</span>
        <span>${esc(score.passedHardFilters === false ? "hard filter blocked" : "scored")}</span>
      </div>`).join("");
  }
  if (!fired.length) return '<p class="mkt-signal-detail-muted">Qualifier ran; no individual rule applications were returned.</p>';
  return fired.map((rule) => {
    const name = typeof rule === "string" ? rule : (rule.ruleId || rule.rule || rule.code || "rule");
    const outcome = typeof rule === "string" ? "fired" : (rule.outcome || rule.action || "fired");
    return `<div class="mkt-signal-audit-row"><span>${esc(name)}</span><span>${esc(outcome)}</span></div>`;
  }).join("");
}

// DIST4 — render district context block for the Gate 1 detail panel.
// Rules:
//   - districtContext null/absent → show nothing (no district data yet).
//   - districtContext.resolved === false → muted "District: unresolved" — never fabricate.
//   - districtContext.resolved === true → show tier, enrollment, supported badge.
//   - Unsupported tier (districtSupported === false) → warning badge, card stays actionable.
function renderDistrictContextBlock(ctx) {
  if (!ctx) return "";
  if (!ctx.resolved) {
    return `<section class="mkt-signal-district mkt-signal-district--unresolved">
      <h5>District</h5>
      <p class="mkt-signal-detail-muted">District: unresolved</p>
    </section>`;
  }
  const enrollment = ctx.districtEnrollment != null
    ? Number(ctx.districtEnrollment).toLocaleString() + " students"
    : "enrollment unknown";
  const tier = ctx.districtTier || "tier unknown";
  const locationParts = [ctx.districtName, ctx.districtState].filter(Boolean);
  const location = locationParts.join(" · ");
  const supportedBadge = ctx.districtSupported === false
    ? `<span class="mkt-signal-district-badge mkt-signal-district-badge--warn" title="This district tier is not currently supported — signal is still actionable">⚠ unsupported tier (filtered)</span>`
    : `<span class="mkt-signal-district-badge mkt-signal-district-badge--ok">supported ✓</span>`;
  const skipListBadge = ctx.onSkipList === true
    ? `<span class="mkt-signal-district-badge mkt-signal-district-badge--warn" title="This district is on the do-not-contact skip list">⚠ do-not-contact (skip list)</span>`
    : "";
  return `<section class="mkt-signal-district${ctx.districtSupported === false ? " mkt-signal-district--unsupported" : ""}">
    <h5>District</h5>
    <p class="mkt-signal-district-line">${esc(location)} · ${esc(tier)} · ${esc(enrollment)} ${supportedBadge} ${skipListBadge}</p>
  </section>`;
}

function renderTraceLink(signal) {
  if (!signal.agentRunId) return "";
  const shortId = String(signal.agentRunId).slice(0, 12);
  return `<a href="#operations" title="Open Operations shell to inspect this scout run">Trace ${esc(shortId)} →</a>`;
}

function renderWhyFlagged(signal) {
  if (!signal.whyFlagged) return '<p class="mkt-signal-detail-muted">No scout reasoning captured.</p>';
  return `<p class="mkt-signal-detail-snippet">${esc(signal.whyFlagged)}</p>`;
}

export function renderSignalDetailPanel(signal) {
  if (!signal) {
    return `<aside class="mkt-signal-detail-panel"><p class="mkt-signal-detail-muted">Select a signal to inspect source evidence and qualifier audit.</p></aside>`;
  }
  const run = signal.pipelineRun;
  const approval = signal.approval;
  return `
    <aside class="mkt-signal-detail-panel" data-signal-id="${esc(signal.id)}">
      <div class="mkt-signal-detail-head">
        <span class="mkt-signal-detail-id">#${esc(String(signal.id).slice(0, 10))}</span>
        <span class="mkt-signal-row-dot mkt-signal-row-dot--${esc(signal.signalStatus)}"></span>
      </div>
      <h4>${esc(signal.headline)}</h4>
      <div class="mkt-signal-detail-meta">
        <span>${esc(signal.signalStatus.replace(/_/g, " "))}</span>
        <span>${esc(signal.urgencyTier)}</span>
        ${signal.stateCode ? `<span>${esc(signal.stateCode)}</span>` : ""}
        <span>${esc(signal.discoveredBy || "manual")}</span>
        <span>${esc(`${signal.relatedSignalsCount || 0} related signals seen`)}</span>
      </div>
      ${run ? `<section class="mkt-signal-pipeline">
        <h5>Pipeline Run</h5>
        <div class="mkt-signal-pipeline-card">
          <span>${esc(run.pipelineName)} · ${esc(String(run.id).slice(0, 8))}</span>
          <span>${esc(run.status.replace(/_/g, " "))}${run.startedAt ? ` · ${esc(timeAgo(run.startedAt))}` : ""}</span>
          <a href="#pipelines/${esc(run.pipelineId || "")}/runs/${esc(run.id)}">View pipeline run →</a>
        </div>
      </section>` : ""}
      ${approval ? `<a class="mkt-signal-approval-badge" href="${esc(approval.href || "#approvals")}">Awaiting Gate 1</a>` : ""}
      ${renderDistrictContextBlock(signal.districtContext)}
      <section>
        <h5>Why flagged</h5>
        ${renderWhyFlagged(signal)}
      </section>
      <section>
        <h5>Scout</h5>
        <div class="mkt-signal-detail-source">
          <span>${esc(signal.discoveredBy || "manual")}</span>
          ${renderTraceLink(signal)}
        </div>
      </section>
      <section>
        <h5>Reason Codes</h5>
        <div class="mkt-signal-detail-chips">${renderReasonChips(signal)}</div>
      </section>
      <details class="mkt-signal-full-details">
        <summary>Expand to full signal</summary>
        <section>
          <h5>Source</h5>
          <p class="mkt-signal-detail-snippet">${esc(signal.summary || "No source snippet captured.")}</p>
          <div class="mkt-signal-detail-source">
            ${signal.sourceUrl ? `<a href="${esc(signal.sourceUrl)}" target="_blank" rel="noreferrer">${esc(signal.sourceTitle || signal.sourceUrl)}</a>` : `<span>${esc(signal.sourceType || "manual")}</span>`}
            ${signal.speakerAttribution ? `<span>${esc(signal.speakerAttribution)}</span>` : ""}
            ${signal.sourcePublishedAt ? `<span>${esc(signal.sourcePublishedAt)}</span>` : ""}
          </div>
        </section>
        <section>
          <h5>Qualifier Audit</h5>
          <div class="mkt-signal-audit">${renderQualifierAudit(signal)}</div>
        </section>
      </details>
      <section>
        <h5>Brief Preview</h5>
        ${signal.briefId || signal.campaignCandidateId
          ? `<a href="#" data-mkt-open-candidate="${esc(signal.campaignCandidateId || "")}">Open campaign workspace</a>`
          : '<p class="mkt-signal-detail-muted">No composed brief yet.</p>'}
      </section>
      <div class="mkt-signal-actions">
        <button class="mkt-btn-primary" data-signal-action="approve" data-signal-id="${esc(signal.id)}" type="button">Approve</button>
        <button class="mkt-btn-secondary" data-signal-action="snooze-open" data-signal-id="${esc(signal.id)}" type="button">Snooze</button>
        <button class="mkt-btn-ghost" data-signal-action="reject-open" data-signal-id="${esc(signal.id)}" type="button">Reject</button>
        <button class="mkt-btn-ghost" data-signal-action="archive" data-signal-id="${esc(signal.id)}" type="button">Archive</button>
      </div>
      <div class="mkt-signal-snooze-form" data-snooze-for="${esc(signal.id)}" hidden>
        <select class="mkt-signal-snooze-days" aria-label="Snooze duration"><option value="7">7 days</option><option value="14" selected>14 days</option><option value="30">30 days</option></select>
        <textarea class="mkt-signal-notes-input" placeholder="Optional note" rows="2"></textarea>
        <div class="mkt-signal-form-actions"><button class="mkt-btn-primary" data-signal-action="snooze-submit" data-signal-id="${esc(signal.id)}" type="button">Snooze</button><button class="mkt-btn-ghost" data-signal-action="snooze-cancel" data-signal-id="${esc(signal.id)}" type="button">Cancel</button></div>
      </div>
      <div class="mkt-signal-reject-form" data-reject-for="${esc(signal.id)}" hidden>
        <textarea class="mkt-signal-notes-input" placeholder="Reason / training note" rows="2"></textarea>
        <div class="mkt-signal-form-actions"><button class="mkt-btn-ghost mkt-btn-danger" data-signal-action="reject-submit" data-signal-id="${esc(signal.id)}" type="button">Reject signal</button><button class="mkt-btn-ghost" data-signal-action="reject-cancel" data-signal-id="${esc(signal.id)}" type="button">Cancel</button></div>
      </div>
    </aside>`;
}

function filterChip(label, value, category, active) {
  return `<button type="button" class="mkt-signal-filter-chip${active ? " is-active" : ""}" data-signal-filter="${esc(category)}" data-filter-value="${esc(value)}">${esc(label)}</button>`;
}

export function renderSignalInboxTree(rawSignals = [], options = {}) {
  const signals = rawSignals.map(normalizeSignal);
  const mode = SIGNAL_GROUPS.includes(options.mode) ? options.mode : readSignalGroupMode();
  const sort = options.sort === "urgency" ? "urgency" : "newest";
  const query = options.query || "";
  const filters = options.filters || {};
  // DIST4: hide-unsupported toggle (default OFF — D4 still visible, per D-4 decision)
  const hideUnsupported = !!options.hideUnsupported;
  const selectedId = options.selectedId || signals[0]?.id || null;
  const selected = signals.find((s) => String(s.id) === String(selectedId)) || signals[0] || null;
  const filtered = sortSignals(filterSignals(signals, { query, filters, hideUnsupported }), sort);
  const tree = buildSignalTree(filtered, mode, sort);
  const filterOptions = summarizeFilterOptions(signals);
  const collapsed = options.collapsed || readCollapsedSignalGroups();
  const forceOpen = !!query;
  const filtersHtml = [
    ...SIGNAL_URGENCIES.map((u) => filterChip(`Urgency: ${u}`, u, "urgencies", filters.urgencies?.includes(u))),
    ...filterOptions.statuses.map((s) => filterChip(`State: ${STATUS_LABELS[s] || s}`, s, "statuses", filters.statuses?.includes(s))),
    ...filterOptions.reasons.slice(0, 8).map((r) => filterChip(`Reason: ${r}`, r, "reasons", filters.reasons?.includes(r))),
    ...filterOptions.geographies.map((g) => filterChip(`Geo: ${g}`, g, "geographies", filters.geographies?.includes(g))),
  ].join("");
  const groupsHtml = mode === "flat"
    ? filtered.map((signal) => rowHtml(signal, selected?.id)).join("")
    : tree.map((group) => groupHtml(group, { mode, selectedId: selected?.id, collapsed, forceOpen })).join("");
  const emptyPage = signals.length === 0;
  const treeEmpty = !emptyPage && filtered.length === 0;

  return `
    <section class="mkt-section mkt-signals-shell" data-signals-inbox>
      <div class="mkt-signals-hero">
        <h3 class="mkt-signals-title">Signals Inbox</h3>
        <p class="mkt-signals-sub">Review signals and approve to initiate a new campaign workspace.</p>
      </div>
      <div class="mkt-signals-toolbar">
        <label class="mkt-signals-search"><span>Search</span><input type="search" value="${esc(query)}" placeholder="Signal, district, reason code..." data-signal-search></label>
        <label class="mkt-signals-sort"><span>Sort</span><select data-signal-sort><option value="newest"${sort === "newest" ? " selected" : ""}>Newest</option><option value="urgency"${sort === "urgency" ? " selected" : ""}>Urgency</option></select></label>
        <label class="mkt-signals-hide-unsupported" title="Hide signals from D4 (unsupported tier) districts. Default OFF — D4 signals are visible for review per design decision D-4.">
          <input type="checkbox" data-signal-hide-unsupported${hideUnsupported ? " checked" : ""}> Hide unsupported tiers
        </label>
      </div>
      <div class="mkt-signals-group-toggle" aria-label="Group signals">
        <span>Group by:</span>
        ${SIGNAL_GROUPS.map((g) => `<button type="button" class="${mode === g ? "is-active" : ""}" data-signal-group="${g}">${esc(g === "reason" ? "Reason Code" : g === "pipeline" ? "Pipeline Run" : g[0].toUpperCase() + g.slice(1))}</button>`).join("")}
      </div>
      <div class="mkt-signals-filters">${filtersHtml}</div>
      <div class="mkt-signals-add-row">
        <button class="mkt-btn-secondary mkt-signals-add-btn" type="button" data-signal-action="add-open">+ Add Signal</button>
      </div>
      <div class="mkt-signal-add-form" hidden>
        <h5 class="mkt-signal-add-title">New Signal</h5>
        <label class="mkt-signal-add-label">Headline <span aria-hidden="true">*</span><input class="mkt-signal-add-input" name="headline" type="text" required placeholder="e.g. Indiana HB 1234 — dyslexia screening mandate signed"/></label>
        <label class="mkt-signal-add-label">Campaign family <span aria-hidden="true">*</span><select class="mkt-signal-add-select" name="campaignFamily"><option value="obc">Outcomes-based contracts</option><option value="state_screener">State screener / field guide</option><option value="biliteracy">Biliteracy</option><option value="reading_growth">Reading growth</option></select></label>
        <label class="mkt-signal-add-label">State code (optional)<input class="mkt-signal-add-input mkt-signal-add-input--short" name="stateCode" type="text" maxlength="2" placeholder="IN"/></label>
        <label class="mkt-signal-add-label">Evidence / source quote (optional)<textarea class="mkt-signal-add-textarea" name="evidence" rows="2" placeholder="Verbatim snippet from source..."></textarea></label>
        <label class="mkt-signal-add-label">Urgency<select class="mkt-signal-add-select" name="urgencyTier"><option value="hot">Hot</option><option value="standard" selected>Standard</option><option value="enrichment">Enrichment</option></select></label>
        <label class="mkt-signal-add-label">Deadline (optional)<input class="mkt-signal-add-input" name="urgencyDeadline" type="date"/></label>
        <div class="mkt-signal-form-actions"><button class="mkt-btn-primary" data-signal-action="add-submit" type="button">Add Signal</button><button class="mkt-btn-ghost" data-signal-action="add-cancel" type="button">Cancel</button></div>
      </div>
      ${emptyPage ? `
        <div class="mkt-signals-empty mkt-signals-empty--page">
          <p>${esc(options.emptyMessage || "No signals yet. Scouts run on the marketing pipeline's schedule.")}</p>
          <a href="#" data-nav="pipelines">Trigger marketing pipeline manually →</a>
          <a href="#integrations" data-nav="integrations">Configure scout connectors →</a>
        </div>` : `
        <div class="mkt-signals-layout">
          <div class="mkt-signal-tree" data-signal-tree>${treeEmpty ? '<p class="mkt-signals-empty">No signals matching filter.</p>' : groupsHtml}</div>
          ${renderSignalDetailPanel(selected)}
        </div>`}
    </section>`;
}
