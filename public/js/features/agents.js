// Agents — autonomous AI agents with CRUD + Agent Chains
import { isSurfaceAvailable } from '../core/status.js';
import { $ } from '../core/dom.js';
import { getState, setState } from '../core/store.js';
import { escapeHtml, scrollToBottom } from '../core/utils.js';
import * as api from '../core/api.js';
import { commandRegistry, registerCommand } from '../ui/commands.js';
import { getPane } from '../ui/parallel.js';
import { showThinking, removeThinking, addStatus } from '../ui/messages.js';
import { getPermissionMode } from '../ui/permissions.js';
import { getSelectedModel, getSelectedProvider, getSelectedReasoningEffort, getSelectedSpeedTier, PROVIDER_PICKERS } from '../ui/model-selector.js';
import { openDagModal, closeDagModal } from './dag-editor.js';
import { openAgentMonitor } from './agent-monitor.js';

function bindListener(target, eventName, handler) {
  if (typeof target?.addEventListener === "function") {
    target.addEventListener(eventName, handler);
  }
}

// ══════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════

/** Reset streaming state — swap stop→send button, re-enable input */
function finishAgentStreaming(pane) {
  if (!pane) return;
  pane.isStreaming = false;
  if ($.streamingTokens) $.streamingTokens.classList.add("hidden");
  if ($.streamingTokensSep) $.streamingTokensSep.classList.add("hidden");
  const parallelMode = getState("parallelMode");
  if (parallelMode) {
    if (pane.sendBtn) pane.sendBtn.classList.remove("hidden");
    if (pane.stopBtn) pane.stopBtn.classList.add("hidden");
    if (pane.messageInput) pane.messageInput.focus();
  } else {
    $.sendBtn.classList.remove("hidden");
    $.stopBtn.classList.add("hidden");
    $.sendBtn.disabled = false;
    $.messageInput.focus();
  }
}

// ══════════════════════════════════════════════════════════
// Agents
// ══════════════════════════════════════════════════════════

export async function loadAgents() {
  if (!isSurfaceAvailable("agents")) {
    console.info("Agents surface not available in this build — module disabled");
    return;
  }
  setState("agentsLoading", true);
  setState("agentsError", null);
  let agents = null;
  let chains = null;
  let dags = null;
  try {
    const [loadedAgents, loadedChains, loadedDags, metrics] = await Promise.all([
      api.fetchAgents(),
      api.fetchChains(),
      api.fetchDags(),
      api.fetchAgentMetrics().catch(() => null),
    ]);
    agents = loadedAgents;
    chains = loadedChains;
    dags = loadedDags;
    setState("agents", agents);
    setState("agentChains", chains);
    setState("agentDags", dags);
    setState("agentMetrics", metrics);
    setState("agentsLoaded", true);
  } catch (err) {
    console.error("Failed to load agents:", err);
    setState("agentsError", err?.message || String(err));
    setState("agentsLoaded", false);
  } finally {
    setState("agentsLoading", false);
    registerAgentCommands(agents, chains, dags);
  }
}

function getSurfaceSummary(type) {
  const summaries = {
    orchestrate: "Describe one larger task and let Artemis break it into helper runs for you.",
    monitor: "Inspect recent agent activity, costs, and comparisons across automation runs.",
    chain: "Run saved agents in a fixed left-to-right order when the task should pass through the same stages every time.",
    dag: "Run saved agents as a dependency graph when some steps can branch or wait on earlier results.",
    agent: "Save a reusable worker with one goal so you can launch the same specialized helper again later.",
  };
  return summaries[type] || "";
}

function getSurfaceSupportNote(type) {
  const notes = {
    orchestrate: "Current launch note: this surface launches with the provider/model selected in the main composer today, and broader provider coverage, clearer run-state detail, and durable history remain in progress.",
    monitor: "Current launch note: this tracks recent activity after runs start, but it is not yet a durable run history, audit trail, maintenance surface, or full launch-debug view.",
    chain: "Current launch note: chains launch with the provider/model selected in the main composer, while per-step provider assignment, deeper run-state detail, and durable history remain in progress.",
    dag: "Current launch note: DAGs launch with the provider/model selected in the main composer, while node-level provider assignment, clearer logs, and durable history remain in progress.",
    agent: "Current launch note: agents launch with the provider/model selected in the main composer, while saved per-agent provider assignment, richer launch-state detail, and durable history remain in progress.",
  };
  return notes[type] || "";
}

function getLaunchPreviewNote(type) {
  const notes = {
    agent: "Launching with the provider/model selected in the main composer. The activity list here is only a lightweight recent trace, not durable history or a maintenance surface.",
    chain: "Launching with the provider/model selected in the main composer. This run view shows lightweight recent activity only, not durable history or a maintenance surface.",
    orchestrate: "Launching with the provider/model selected in the main composer. This run view shows lightweight recent activity only, not durable history or a maintenance surface.",
    dag: "Launching with the provider/model selected in the main composer. This run view shows lightweight recent activity only, not durable history or a maintenance surface.",
  };
  return notes[type] || "";
}

function renderSurfaceMeta(type) {
  return `
    <div class="toolbox-surface-meta">
      <div class="toolbox-surface-summary">${escapeHtml(getSurfaceSummary(type))}</div>
      <div class="toolbox-surface-note">${escapeHtml(getSurfaceSupportNote(type))}</div>
    </div>
  `;
}

function renderSectionHeader(title, countLabel, note) {
  const header = document.createElement("div");
  header.className = "agent-section-header";
  header.innerHTML = `
    <div class="agent-section-header-top">
      <span class="agent-section-header-title">${escapeHtml(title)}</span>
      <span class="agent-section-header-count">${escapeHtml(countLabel)}</span>
    </div>
    ${note ? `<div class="agent-section-header-note">${escapeHtml(note)}</div>` : ""}
  `;
  return header;
}

function formatRunDuration(ms) {
  if (!ms) return "0s";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function formatRunCost(cost) {
  if (!cost) return "$0.00";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

function formatRunStartedAt(timestampSeconds) {
  if (!timestampSeconds) return "Started just now";
  const diffSeconds = Math.max(0, Math.round(Date.now() / 1000) - timestampSeconds);
  if (diffSeconds < 60) return `Started ${diffSeconds}s ago`;
  const diffMinutes = Math.round(diffSeconds / 60);
  if (diffMinutes < 60) return `Started ${diffMinutes}m ago`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `Started ${diffHours}h ago`;
  const diffDays = Math.round(diffHours / 24);
  return `Started ${diffDays}d ago`;
}

function getRunStatusTone(status) {
  switch (status) {
    case "completed":
      return "success";
    case "running":
      return "running";
    case "error":
      return "error";
    case "aborted":
      return "warn";
    default:
      return "idle";
  }
}

function renderRecentRunsSection() {
  const metrics = getState("agentMetrics") || {};
  const recentRuns = Array.isArray(metrics.recent) ? metrics.recent.slice(0, 6) : [];

  const runsHeader = renderSectionHeader(
    "Recent Runs",
    recentRuns.length ? `${recentRuns.length} visible` : "0 visible",
    "Operational history lives here so Dev Projects can stay focused on human coding conversations."
  );
  $.agentPanel.appendChild(runsHeader);

  if (!recentRuns.length) {
    const emptyCard = document.createElement("div");
    emptyCard.className = "toolbox-card agent-run-card agent-run-card-empty";
    emptyCard.innerHTML = `
      <div class="toolbox-card-chip-row">
        <span class="toolbox-card-chip">Runs</span>
        <span class="toolbox-card-chip toolbox-card-chip-muted">Waiting</span>
      </div>
      <div class="toolbox-card-title">
        <span class="agent-icon">${getMonitorIcon()}</span>
        No recent runs yet
      </div>
      <div class="toolbox-card-desc">Launch an agent, chain, DAG, or orchestration run and the latest activity will appear here instead of the Dev Projects rail.</div>
    `;
    emptyCard.addEventListener("click", () => {
      $.agentSidebar?.classList.add("hidden");
      $.agentBtn.classList.remove("active");
      openAgentMonitor();
    });
    $.agentPanel.appendChild(emptyCard);
    return;
  }

  const runsWrap = document.createElement("div");
  runsWrap.className = "agent-runs-grid";

  for (const run of recentRuns) {
    const card = document.createElement("div");
    const tone = getRunStatusTone(run.status);
    const agentTitle = run.agent_title || run.agent_id || "Agent run";
    const runType = run.run_type || "agent";
    const runLabel = run.run_id ? `Run ${String(run.run_id).slice(0, 8)}` : "Run";

    card.className = "toolbox-card agent-run-card";
    card.innerHTML = `
      <div class="toolbox-card-chip-row">
        <span class="toolbox-card-chip">Run</span>
        <span class="toolbox-card-chip toolbox-card-chip-muted">${escapeHtml(runType)}</span>
      </div>
      <div class="agent-run-topline">
        <div class="toolbox-card-title">
          <span class="agent-icon">${getMonitorIcon()}</span>
          ${escapeHtml(agentTitle)}
        </div>
        <span class="agent-run-status ${tone}">${escapeHtml(run.status || "unknown")}</span>
      </div>
      <div class="agent-run-meta">
        <span>${escapeHtml(runLabel)}</span>
        <span>${escapeHtml(formatRunStartedAt(run.started_at))}</span>
      </div>
      <div class="agent-run-stats">
        <span><strong>${escapeHtml(formatRunDuration(run.duration_ms))}</strong> duration</span>
        <span><strong>${escapeHtml(formatRunCost(run.cost_usd))}</strong> cost</span>
      </div>
    `;
    card.addEventListener("click", () => {
      $.agentSidebar?.classList.add("hidden");
      $.agentBtn.classList.remove("active");
      openAgentMonitor();
    });
    runsWrap.appendChild(card);
  }

  $.agentPanel.appendChild(runsWrap);
}

// ══════════════════════════════════════════════════════════
// Proposals Inbox Panel (J6a)
// ══════════════════════════════════════════════════════════

/** 30-second cache so repeated sidebar opens don't hammer the endpoint. */
let _inboxCache = null;
let _inboxCacheTs = 0;
const _INBOX_TTL_MS = 30_000;

export function invalidateInboxCache() {
  _inboxCache = null;
  _inboxCacheTs = 0;
}

async function fetchInboxCached() {
  const now = Date.now();
  if (_inboxCache && now - _inboxCacheTs < _INBOX_TTL_MS) return _inboxCache;
  try {
    _inboxCache = await api.builderFetchInbox();
    _inboxCacheTs = now;
  } catch (err) {
    console.warn("Inbox fetch failed:", err);
    _inboxCache = { agents_with_pending_proposals: [], agents_with_new_summaries: [], skills_with_pending_proposals: [] };
    _inboxCacheTs = now;
  }
  return _inboxCache;
}

/** Navigate to Builder for a specific agent via the CC18 pattern. */
function _openBuilderForAgent(agentId) {
  // Resolve the agentDbId from state (same logic as operations-shell CC18).
  const agents = getState("agents") || [];
  const match = agents.find(a => a.id === agentId);
  const agentDbId = match?.dbId ?? null;

  // Close the sidebar panel first.
  $.agentSidebar?.classList.add("hidden");
  $.agentBtn?.classList.remove("active");

  // CC18 pattern: set builderEditAgent* state so initBuilderSurface()
  // creates a target-scoped session when the builder view opens.
  setState("builderEditAgentId", agentId);
  if (agentDbId) setState("builderEditAgentDbId", agentDbId);

  // Navigate to the builder view — operations-shell's onState("view") listener
  // handles init and re-render automatically.
  setState("view", "agents/builder");
}

async function renderInboxPanel() {
  if (!$.agentPanel) return;

  const inbox = await fetchInboxCached();
  const proposals = inbox.agents_with_pending_proposals || [];
  const summaries = inbox.agents_with_new_summaries || [];
  const totalCount = proposals.length + summaries.length;

  const panel = document.createElement("div");
  panel.className = "inbox-panel";

  // ── Header ──────────────────────────────────────────
  const header = document.createElement("div");
  header.className = "inbox-header";
  header.innerHTML = `
    <span class="inbox-title">Agent Review Inbox</span>
    ${totalCount > 0 ? `<span class="inbox-badge">${totalCount}</span>` : ""}
  `;
  panel.appendChild(header);

  if (totalCount === 0) {
    const empty = document.createElement("div");
    empty.className = "inbox-empty";
    empty.textContent = "No pending proposals or new summaries.";
    panel.appendChild(empty);
    $.agentPanel.insertBefore(panel, $.agentPanel.firstChild);
    return;
  }

  // ── Section 1: Pending Proposals ────────────────────
  if (proposals.length > 0) {
    const label = document.createElement("div");
    label.className = "inbox-section-label";
    label.textContent = "Pending Proposals";
    panel.appendChild(label);

    for (const item of proposals) {
      const row = document.createElement("div");
      row.className = "inbox-row";

      const firstProposalId = item.proposal_ids?.[0] ?? null;

      row.innerHTML = `
        <span class="inbox-agent-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
        <span class="inbox-pill inbox-pill-proposals">${item.pending_count} proposal${item.pending_count !== 1 ? "s" : ""}</span>
        <div class="inbox-actions">
          ${firstProposalId ? `
            <button class="inbox-btn inbox-btn-approve" data-proposal-id="${firstProposalId}" data-action="inbox-approve" title="Approve first pending proposal">Approve</button>
            <button class="inbox-btn inbox-btn-reject" data-proposal-id="${firstProposalId}" data-action="inbox-reject" title="Reject first pending proposal">Reject</button>
          ` : ""}
          <button class="inbox-btn" data-action="inbox-review-builder" data-agent-id="${escapeHtml(item.agent_id)}">Review</button>
        </div>
      `;
      panel.appendChild(row);
    }
  }

  // ── Section 2: New Summaries ─────────────────────────
  if (summaries.length > 0) {
    const label = document.createElement("div");
    label.className = "inbox-section-label";
    label.textContent = "New Summaries";
    panel.appendChild(label);

    for (const item of summaries) {
      const row = document.createElement("div");
      row.className = "inbox-row";
      row.innerHTML = `
        <span class="inbox-agent-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
        <span class="inbox-pill inbox-pill-summaries">${item.new_summary_count} new</span>
        <div class="inbox-actions">
          <button class="inbox-btn" data-action="inbox-distill-skills" data-agent-id="${escapeHtml(item.agent_id)}">Distill skills</button>
          <button class="inbox-btn" data-action="inbox-review-builder" data-agent-id="${escapeHtml(item.agent_id)}">Review</button>
        </div>
      `;
      panel.appendChild(row);
    }
  }

  // ── Event delegation on the panel ───────────────────
  panel.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;

    if (action === "inbox-approve") {
      const proposalId = Number(btn.dataset.proposalId);
      btn.disabled = true;
      btn.textContent = "…";
      try {
        await api.builderApproveProposal(proposalId);
        invalidateInboxCache();
        await renderInboxPanel();
      } catch (err) {
        console.error("Approve failed:", err);
        btn.disabled = false;
        btn.textContent = "Approve";
      }
    } else if (action === "inbox-reject") {
      const proposalId = Number(btn.dataset.proposalId);
      // CC22: prompt for an optional reason.  Cancel = abort entirely (no
      // accidental reject).  OK with empty text = reject without reason
      // (one-click flow: just press Enter).  OK with text = reject with reason.
      const reason = window.prompt(
        "Optional: why are you rejecting this? (Helps the Builder learn.)\n" +
        "Leave blank and press Enter to reject without a reason.\n" +
        "Press Cancel to abort.",
        ""
      );
      if (reason === null) return; // user cancelled
      btn.disabled = true;
      btn.textContent = "…";
      try {
        await api.builderRejectProposal(proposalId, reason || null);
        invalidateInboxCache();
        await renderInboxPanel();
      } catch (err) {
        console.error("Reject failed:", err);
        btn.disabled = false;
        btn.textContent = "Reject";
      }
    } else if (action === "inbox-review-builder") {
      const agentId = btn.dataset.agentId;
      // Mark reviewed then open builder.
      api.builderMarkAgentReviewed(agentId).catch(() => {});
      invalidateInboxCache();
      _openBuilderForAgent(agentId);
    } else if (action === "inbox-distill-skills") {
      const agentId = btn.dataset.agentId;
      btn.disabled = true;
      const originalText = btn.textContent;
      btn.textContent = "…";
      try {
        const result = await api.distillSkills(agentId);
        btn.textContent = `Proposed ${result.n_proposed}`;
        invalidateInboxCache();
        await renderInboxPanel();
      } catch (err) {
        console.error("Distill skills failed:", err);
        alert(`Distill skills failed: ${err.message}`);
        btn.disabled = false;
        btn.textContent = originalText;
      }
    }
  });

  // Insert at top of sidebar panel, replacing any existing inbox panel.
  const existing = $.agentPanel.querySelector(".inbox-panel");
  if (existing) {
    $.agentPanel.replaceChild(panel, existing);
  } else {
    $.agentPanel.insertBefore(panel, $.agentPanel.firstChild);
  }
}

function renderAgentPanel() {
  const agents = getState("agents");
  const chains = getState("agentChains") || [];
  if (!$.agentPanel) return;
  $.agentPanel.innerHTML = "";

  // ── Compact preview banner ──
  const previewBanner = document.createElement("div");
  previewBanner.className = "toolbox-preview-banner";
  previewBanner.innerHTML = `
    <div class="toolbox-preview-title">Builder surfaces</div>
    <div class="toolbox-preview-body">Agents, chains, and DAGs.</div>
  `;
  $.agentPanel.appendChild(previewBanner);

  // Render Inbox panel (async — fires and fills in when data arrives)
  renderInboxPanel().catch((err) => console.warn("renderInboxPanel failed:", err));

  // ── Orchestrate card ──
  const orchCard = document.createElement("div");
  orchCard.className = "toolbox-card agent-card orch-card";
  orchCard.innerHTML = `
    <div class="toolbox-card-chip-row">
      <span class="toolbox-card-chip">Launch</span>
      <span class="toolbox-card-chip toolbox-card-chip-muted">Delegation</span>
    </div>
    <div class="toolbox-card-title">
      <span class="agent-icon">${getOrchIcon()}</span>
      Orchestrate
    </div>
    <div class="toolbox-card-desc">Describe a task — the orchestrator decomposes it and delegates to the right agents automatically.</div>
    ${renderSurfaceMeta("orchestrate")}
  `;
  orchCard.addEventListener("click", () => {
    $.agentSidebar?.classList.add("hidden");
    $.agentBtn.classList.remove("active");
    openOrchModal();
  });
  $.agentPanel.appendChild(orchCard);

  // ── Monitor button ──
  const monitorCard = document.createElement("div");
  monitorCard.className = "toolbox-card agent-card monitor-card";
  monitorCard.innerHTML = `
    <div class="toolbox-card-chip-row">
      <span class="toolbox-card-chip">Monitor</span>
      <span class="toolbox-card-chip toolbox-card-chip-muted">Runs</span>
    </div>
    <div class="toolbox-card-title">
      <span class="agent-icon">${getMonitorIcon()}</span>
      Agent Monitor
    </div>
    <div class="toolbox-card-desc">Real-time metrics, cost aggregation, and comparative analysis across all agents.</div>
    ${renderSurfaceMeta("monitor")}
  `;
  monitorCard.addEventListener("click", () => {
    $.agentSidebar?.classList.add("hidden");
    $.agentBtn.classList.remove("active");
    openAgentMonitor();
  });
  $.agentPanel.appendChild(monitorCard);

  renderRecentRunsSection();

  // ── Chains section ──
  if (chains.length > 0 || true) {
    const chainHeader = renderSectionHeader(
      "Chains",
      `${chains.length} saved`,
      "Fixed stage-by-stage runs."
    );
    $.agentPanel.appendChild(chainHeader);

    for (const chain of chains) {
      const agentNames = chain.agents.map(id => {
        const a = agents.find(ag => ag.id === id);
        return a ? a.title : id;
      });
      const card = document.createElement("div");
      card.className = "toolbox-card agent-card chain-card";
      card.innerHTML = `
        <div class="agent-card-actions">
          <button class="agent-card-edit" data-chain-id="${escapeHtml(chain.id)}" title="Edit chain">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
          <button class="agent-card-delete" data-chain-id="${escapeHtml(chain.id)}" title="Delete chain">&times;</button>
        </div>
        <div class="toolbox-card-chip-row">
          <span class="toolbox-card-chip">Saved</span>
          <span class="toolbox-card-chip toolbox-card-chip-muted">${agentNames.length} stages</span>
        </div>
        <div class="toolbox-card-title">
          <span class="agent-icon">${getChainIcon()}</span>
          ${escapeHtml(chain.title)}
        </div>
        <div class="toolbox-card-desc">${escapeHtml(chain.description || agentNames.join(" → "))}</div>
        <div class="chain-steps-preview">${agentNames.map(n => `<span class="chain-step-tag">${escapeHtml(n)}</span>`).join('<span class="chain-arrow">→</span>')}</div>
        ${renderSurfaceMeta("chain")}
      `;
      card.addEventListener("click", (e) => {
        if (e.target.closest(".agent-card-edit") || e.target.closest(".agent-card-delete")) return;
        $.agentSidebar?.classList.add("hidden");
        $.agentBtn.classList.remove("active");
        startChain(chain, getPane(null));
      });
      card.querySelector(".agent-card-edit").addEventListener("click", (e) => {
        e.stopPropagation();
        openChainModal(chain);
      });
      card.querySelector(".agent-card-delete").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteChain(chain.id, chain.title);
      });
      $.agentPanel.appendChild(card);
    }

    const addChainCard = document.createElement("div");
    addChainCard.className = "toolbox-card-add";
    addChainCard.innerHTML = `+ Add Chain`;
    addChainCard.addEventListener("click", () => openChainModal());
    $.agentPanel.appendChild(addChainCard);
  }

  // ── DAGs section ──
  const dags = getState("agentDags") || [];
  {
    const dagHeader = renderSectionHeader(
      "DAGs",
      `${dags.length} saved`,
      "Branching flows with dependencies."
    );
    $.agentPanel.appendChild(dagHeader);

    for (const dag of dags) {
      const nodeNames = dag.nodes.map(n => {
        const a = agents.find(ag => ag.id === n.agentId);
        return a ? a.title : n.agentId;
      });
      const card = document.createElement("div");
      card.className = "toolbox-card agent-card dag-card";
      card.innerHTML = `
        <div class="agent-card-actions">
          <button class="agent-card-edit" data-dag-id="${escapeHtml(dag.id)}" title="Edit DAG">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
          <button class="agent-card-delete" data-dag-id="${escapeHtml(dag.id)}" title="Delete DAG">&times;</button>
        </div>
        <div class="toolbox-card-chip-row">
          <span class="toolbox-card-chip">Saved</span>
          <span class="toolbox-card-chip toolbox-card-chip-muted">${dag.nodes.length} nodes</span>
        </div>
        <div class="toolbox-card-title">
          <span class="agent-icon">${getDagIcon()}</span>
          ${escapeHtml(dag.title)}
        </div>
        <div class="toolbox-card-desc">${escapeHtml(dag.description || `${dag.nodes.length} nodes, ${dag.edges.length} edges`)}</div>
        <div class="dag-nodes-preview">${nodeNames.map(n => `<span class="chain-step-tag">${escapeHtml(n)}</span>`).join('')}</div>
        ${renderSurfaceMeta("dag")}
      `;
      card.addEventListener("click", (e) => {
        if (e.target.closest(".agent-card-edit") || e.target.closest(".agent-card-delete")) return;
        $.agentSidebar?.classList.add("hidden");
        $.agentBtn.classList.remove("active");
        startDag(dag, getPane(null));
      });
      card.querySelector(".agent-card-edit").addEventListener("click", (e) => {
        e.stopPropagation();
        openDagModal(dag);
      });
      card.querySelector(".agent-card-delete").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteDag(dag.id, dag.title);
      });
      $.agentPanel.appendChild(card);
    }

    const addDagCard = document.createElement("div");
    addDagCard.className = "toolbox-card-add";
    addDagCard.innerHTML = `+ Add DAG`;
    addDagCard.addEventListener("click", () => openDagModal());
    $.agentPanel.appendChild(addDagCard);
  }

  // ── Agents section ──
  const agentHeader = renderSectionHeader(
    "Agents",
    `${agents.length} saved`,
    "Reusable workers launched with the main composer context."
  );
  $.agentPanel.appendChild(agentHeader);

  for (const agent of agents) {
    const card = document.createElement("div");
    card.className = "toolbox-card agent-card";
    card.innerHTML = `
      <div class="agent-card-actions">
        <button class="agent-card-edit" data-id="${escapeHtml(agent.id)}" title="Edit agent">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="agent-card-delete" data-id="${escapeHtml(agent.id)}" title="Delete agent">&times;</button>
      </div>
      <div class="toolbox-card-chip-row">
        <span class="toolbox-card-chip">Saved agent</span>
        <span class="toolbox-card-chip toolbox-card-chip-muted">${agent.custom ? "Custom" : "Built-in"}</span>
      </div>
      <div class="toolbox-card-title">
        <span class="agent-icon">${getAgentIcon(agent.icon)}</span>
        ${escapeHtml(agent.title)}
        ${agent.custom ? '<span class="agent-custom-badge">custom</span>' : ''}
      </div>
      <div class="toolbox-card-desc">${escapeHtml(agent.description)}</div>
      ${renderSurfaceMeta("agent")}
    `;
    card.addEventListener("click", (e) => {
      if (e.target.closest(".agent-card-edit") || e.target.closest(".agent-card-delete")) return;
      $.agentSidebar?.classList.add("hidden");
      $.agentBtn.classList.remove("active");
      startAgent(agent, getPane(null));
    });
    card.querySelector(".agent-card-edit").addEventListener("click", (e) => {
      e.stopPropagation();
      openAgentModal(agent);
    });
    card.querySelector(".agent-card-delete").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteAgent(agent.id, agent.title);
    });
    $.agentPanel.appendChild(card);
  }

  const addCard = document.createElement("div");
  addCard.className = "toolbox-card-add";
  addCard.innerHTML = `+ Add Agent`;
  addCard.addEventListener("click", () => openAgentModal());
  $.agentPanel.appendChild(addCard);

}

// ══════════════════════════════════════════════════════════
// Agent CRUD Modal
// ══════════════════════════════════════════════════════════

function populateAgentModelOptions(provider, selectedModel = "") {
  if (!$.agentFormModel) return;
  const models = PROVIDER_PICKERS[provider]?.models || [];
  $.agentFormModel.innerHTML = models.length
    ? models.map(m => `<option value="${m.value}"${m.value === selectedModel ? " selected" : ""}>${m.label}</option>`).join("")
    : `<option value="">— same as session —</option>`;
}

function openAgentModal(agent) {
  $.agentForm.reset();
  if (agent) {
    $.agentModalTitle.textContent = "Edit Agent";
    $.agentFormTitle.value = agent.title;
    $.agentFormDesc.value = agent.description;
    $.agentFormIcon.value = agent.icon || "tool";
    $.agentFormGoal.value = agent.goal;
    $.agentFormMaxTurns.value = agent.constraints?.maxTurns || 50;
    $.agentFormTimeout.value = Math.round((agent.constraints?.timeoutMs || 300000) / 1000);
    $.agentFormEditId.value = agent.id;
    if ($.agentFormProvider) $.agentFormProvider.value = agent.provider || "";
    populateAgentModelOptions(agent.provider || "", agent.model || "");
  } else {
    $.agentModalTitle.textContent = "New Agent";
    $.agentFormEditId.value = "";
    $.agentFormMaxTurns.value = 50;
    $.agentFormTimeout.value = 300;
    if ($.agentFormProvider) $.agentFormProvider.value = "";
    populateAgentModelOptions("", "");
  }
  $.agentFormProvider?.addEventListener("change", _onAgentProviderChange);
  $.agentModal.classList.remove("hidden");
  $.agentFormTitle.focus();
}

function _onAgentProviderChange() {
  populateAgentModelOptions($.agentFormProvider.value, "");
}

function closeAgentModal() {
  $.agentModal.classList.add("hidden");
  $.agentFormProvider?.removeEventListener("change", _onAgentProviderChange);
}

$.agentForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const editId = $.agentFormEditId.value;
  const savedProvider = $.agentFormProvider?.value || "";
  const savedModel = $.agentFormModel?.value || "";
  const data = {
    title: $.agentFormTitle.value.trim(),
    description: $.agentFormDesc.value.trim(),
    icon: $.agentFormIcon.value,
    goal: $.agentFormGoal.value.trim(),
    constraints: {
      maxTurns: parseInt($.agentFormMaxTurns.value, 10) || 50,
      timeoutMs: (parseInt($.agentFormTimeout.value, 10) || 300) * 1000,
    },
  };
  if (savedProvider) data.provider = savedProvider;
  if (savedModel) data.model = savedModel;
  if (!data.title || !data.goal) return;
  try {
    if (editId) {
      await api.updateAgent(editId, data);
    } else {
      await api.createAgent(data);
    }
    closeAgentModal();
    await loadAgents();
  } catch (err) {
    console.error("Failed to save agent:", err);
    alert(err.message);
  }
});

$.agentModalClose?.addEventListener("click", closeAgentModal);
$.agentModalCancel?.addEventListener("click", closeAgentModal);
$.agentModal?.addEventListener("click", (e) => {
  if (e.target === $.agentModal) closeAgentModal();
});

async function deleteAgent(id, title) {
  if (!confirm(`Delete agent "${title}"?`)) return;
  try {
    await api.deleteAgentApi(id);
    await loadAgents();
  } catch (err) {
    console.error("Failed to delete agent:", err);
  }
}

// ══════════════════════════════════════════════════════════
// Chain CRUD Modal
// ══════════════════════════════════════════════════════════

function openChainModal(chain) {
  $.chainForm.reset();
  $.chainAgentList.innerHTML = "";

  if (chain) {
    $.chainModalTitle.textContent = "Edit Chain";
    $.chainFormTitle.value = chain.title;
    $.chainFormDesc.value = chain.description || "";
    $.chainFormContext.value = chain.contextPassing || "summary";
    $.chainFormEditId.value = chain.id;
    for (const agentId of chain.agents) {
      addChainAgentRow(agentId);
    }
  } else {
    $.chainModalTitle.textContent = "New Chain";
    $.chainFormEditId.value = "";
    $.chainFormContext.value = "summary";
    // Start with two empty rows
    addChainAgentRow();
    addChainAgentRow();
  }
  $.chainModal.classList.remove("hidden");
  $.chainFormTitle.focus();
}

function closeChainModal() {
  $.chainModal.classList.add("hidden");
}

function addChainAgentRow(selectedId) {
  const agents = getState("agents") || [];
  const row = document.createElement("div");
  row.className = "chain-agent-row";

  const stepNum = $.chainAgentList.children.length + 1;
  row.innerHTML = `
    <span class="chain-agent-step">${stepNum}</span>
    <select class="chain-agent-select">
      <option value="">Select agent...</option>
      ${agents.map(a => `<option value="${escapeHtml(a.id)}" ${a.id === selectedId ? 'selected' : ''}>${escapeHtml(a.title)}</option>`).join("")}
    </select>
    <button type="button" class="chain-agent-up" title="Move up">↑</button>
    <button type="button" class="chain-agent-down" title="Move down">↓</button>
    <button type="button" class="chain-agent-remove" title="Remove">&times;</button>
  `;

  row.querySelector(".chain-agent-up").addEventListener("click", () => {
    const prev = row.previousElementSibling;
    if (prev) {
      $.chainAgentList.insertBefore(row, prev);
      renumberChainSteps();
    }
  });

  row.querySelector(".chain-agent-down").addEventListener("click", () => {
    const next = row.nextElementSibling;
    if (next) {
      $.chainAgentList.insertBefore(next, row);
      renumberChainSteps();
    }
  });

  row.querySelector(".chain-agent-remove").addEventListener("click", () => {
    row.remove();
    renumberChainSteps();
  });

  $.chainAgentList.appendChild(row);
}

function renumberChainSteps() {
  const rows = $.chainAgentList.querySelectorAll(".chain-agent-row");
  rows.forEach((row, i) => {
    row.querySelector(".chain-agent-step").textContent = i + 1;
  });
}

$.chainAddAgentBtn?.addEventListener("click", () => addChainAgentRow());

$.chainForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const editId = $.chainFormEditId.value;
  const selects = $.chainAgentList.querySelectorAll(".chain-agent-select");
  const agentIds = [...selects].map(s => s.value).filter(Boolean);

  if (agentIds.length < 2) {
    alert("A chain needs at least 2 agents.");
    return;
  }

  const data = {
    title: $.chainFormTitle.value.trim(),
    description: $.chainFormDesc.value.trim(),
    agents: agentIds,
    contextPassing: $.chainFormContext.value,
  };
  if (!data.title) return;

  try {
    if (editId) {
      await api.updateChain(editId, data);
    } else {
      await api.createChain(data);
    }
    closeChainModal();
    await loadAgents();
  } catch (err) {
    console.error("Failed to save chain:", err);
    alert(err.message);
  }
});

$.chainModalClose?.addEventListener("click", closeChainModal);
$.chainModalCancel?.addEventListener("click", closeChainModal);
$.chainModal?.addEventListener("click", (e) => {
  if (e.target === $.chainModal) closeChainModal();
});

async function deleteChain(id, title) {
  if (!confirm(`Delete chain "${title}"?`)) return;
  try {
    await api.deleteChainApi(id);
    await loadAgents();
  } catch (err) {
    console.error("Failed to delete chain:", err);
  }
}

// ══════════════════════════════════════════════════════════
// Commands
// ══════════════════════════════════════════════════════════

export function registerAgentCommands(agents = getState("agents"), chains = getState("agentChains"), dags = getState("agentDags")) {
  for (const [name, cmd] of Object.entries(commandRegistry)) {
    if (cmd.category === "agent") delete commandRegistry[name];
  }
  for (const agent of agents || []) {
    registerCommand(`agent-${agent.id}`, {
      category: "agent",
      description: agent.description,
      execute(args, pane) {
        startAgent(agent, pane, args.trim() || undefined);
      },
    });
  }
  // Orchestrate command
  registerCommand("orchestrate", {
    category: "agent",
    description: "Orchestrate: decompose a task and delegate to specialist agents",
    execute(args, pane) {
      if (args.trim()) {
        startOrchestration(args.trim(), pane);
      }
    },
  });

  for (const chain of chains || []) {
    registerCommand(`chain-${chain.id}`, {
      category: "agent",
      description: `Chain: ${chain.description || chain.title}`,
      execute(args, pane) {
        startChain(chain, pane);
      },
    });
  }

  for (const dag of dags || []) {
    registerCommand(`dag-${dag.id}`, {
      category: "agent",
      description: `DAG: ${dag.description || dag.title}`,
      execute(args, pane) {
        startDag(dag, pane);
      },
    });
  }
}

// ══════════════════════════════════════════════════════════
// Start Agent
// ══════════════════════════════════════════════════════════

export function startAgent(agentDef, pane, userContext) {
  pane = pane || getPane(null);
  const cwd = $.projectSelect.value;
  if (!cwd) {
    addStatus("Select a project first", true, pane);
    return;
  }

  // Render agent header card
  const div = document.createElement("div");
  div.className = "msg";
  const header = document.createElement("div");
  header.className = "agent-header";
  header.id = `agent-header-${agentDef.id}`;
  header.innerHTML = `
    <div class="agent-header-top">
      <span class="agent-header-icon">${getAgentIcon(agentDef.icon)}</span>
      <span class="agent-header-title">${escapeHtml(agentDef.title)}</span>
      <span class="agent-status-badge running" id="agent-badge-${agentDef.id}">Launching</span>
    </div>
    <div class="agent-header-goal">${escapeHtml(agentDef.goal)}</div>
    <div class="workflow-preview-note">${escapeHtml(getLaunchPreviewNote("agent"))}</div>
    <div class="agent-header-stats" id="agent-stats-${agentDef.id}">
      <span class="agent-stat" id="agent-elapsed-${agentDef.id}">0s</span>
      <span class="agent-stat-sep"></span>
      <span class="agent-stat" id="agent-turns-${agentDef.id}">0/${agentDef.constraints?.maxTurns || 50} turns</span>
    </div>
    <div class="agent-activity-log" id="agent-log-${agentDef.id}"></div>
  `;
  div.appendChild(header);
  pane.messagesDiv.appendChild(div);
  scrollToBottom(pane);

  // Start elapsed timer
  const startTime = Date.now();
  const timerId = setInterval(() => {
    const el = document.getElementById(`agent-elapsed-${agentDef.id}`);
    if (!el) { clearInterval(timerId); return; }
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    const mins = Math.floor(elapsed / 60);
    const secs = elapsed % 60;
    el.textContent = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  }, 1000);

  pane._agentTimerId = timerId;
  pane._agentId = agentDef.id;

  pane.isStreaming = true;
  if (!getState("parallelMode")) {
    $.sendBtn.classList.add("hidden");
    $.stopBtn.classList.remove("hidden");
  }

  const selectedOption = $.projectSelect.options[$.projectSelect.selectedIndex];
  const projectName = selectedOption?.textContent || "Session";
  const ws = getState("ws");

  const model = agentDef.model || getSelectedModel();
  const provider = agentDef.provider || getSelectedProvider();
  const reasoningEffort = getSelectedReasoningEffort();
  const speedTier = getSelectedSpeedTier();
  const payload = {
    type: "agent",
    agentDef,
    cwd,
    sessionId: getState("sessionId"),
    projectName,
    permissionMode: getPermissionMode(),
  };
  if (userContext) payload.userContext = userContext;
  if (provider) payload.provider = provider;
  if (model) payload.model = model;
  if (reasoningEffort) payload.reasoningEffort = reasoningEffort;
  if (speedTier && speedTier !== "standard") payload.speedTier = speedTier;
  ws.send(JSON.stringify(payload));

  showThinking(`Agent: ${agentDef.title} starting...`, pane);
}

// ══════════════════════════════════════════════════════════
// Start Chain
// ══════════════════════════════════════════════════════════

export function startChain(chain, pane) {
  pane = pane || getPane(null);
  const cwd = $.projectSelect.value;
  if (!cwd) {
    addStatus("Select a project first", true, pane);
    return;
  }

  const allAgents = getState("agents") || [];
  const agentDefs = chain.agents.map(id => allAgents.find(a => a.id === id)).filter(Boolean);
  if (agentDefs.length === 0) {
    addStatus("No valid agents in this chain", true, pane);
    return;
  }

  // Render chain header card
  const div = document.createElement("div");
  div.className = "msg";
  const header = document.createElement("div");
  header.className = "chain-header";
  header.id = `chain-header-${chain.id}`;
  header.innerHTML = `
    <div class="chain-header-top">
      <span class="chain-header-icon">${getChainIcon()}</span>
      <span class="chain-header-title">${escapeHtml(chain.title)}</span>
      <span class="agent-status-badge running" id="chain-badge-${chain.id}">Launching</span>
    </div>
    <div class="workflow-preview-note">${escapeHtml(getLaunchPreviewNote("chain"))}</div>
    <div class="chain-pipeline" id="chain-pipeline-${chain.id}">
      ${agentDefs.map((a, i) => `
        <div class="chain-pipeline-step" id="chain-step-${chain.id}-${i}">
          <span class="chain-pipeline-num">${i + 1}</span>
          <span class="chain-pipeline-name">${escapeHtml(a.title)}</span>
          <span class="chain-pipeline-status" id="chain-step-status-${chain.id}-${i}">pending</span>
        </div>
        ${i < agentDefs.length - 1 ? '<div class="chain-pipeline-connector"></div>' : ''}
      `).join("")}
    </div>
  `;
  div.appendChild(header);
  pane.messagesDiv.appendChild(div);
  scrollToBottom(pane);

  // Start elapsed timer
  const startTime = Date.now();
  const timerId = setInterval(() => {
    const badge = document.getElementById(`chain-badge-${chain.id}`);
    if (!badge || !badge.classList.contains("running")) { clearInterval(timerId); return; }
    const elapsed = Math.round((Date.now() - startTime) / 1000);
    const mins = Math.floor(elapsed / 60);
    const secs = elapsed % 60;
    badge.textContent = mins > 0 ? `Running ${mins}m ${secs}s` : `Running ${secs}s`;
  }, 1000);

  pane._chainTimerId = timerId;

  pane.isStreaming = true;
  if (!getState("parallelMode")) {
    $.sendBtn.classList.add("hidden");
    $.stopBtn.classList.remove("hidden");
  }

  const selectedOption = $.projectSelect.options[$.projectSelect.selectedIndex];
  const projectName = selectedOption?.textContent || "Session";
  const ws = getState("ws");
  const model = getSelectedModel();
  const provider = getSelectedProvider();
  const reasoningEffort = getSelectedReasoningEffort();
  const speedTier = getSelectedSpeedTier();

  const payload = {
    type: "agent_chain",
    chain,
    agents: agentDefs,
    cwd,
    sessionId: getState("sessionId"),
    projectName,
    permissionMode: getPermissionMode(),
    provider,
    model,
  };
  if (reasoningEffort) payload.reasoningEffort = reasoningEffort;
  if (speedTier && speedTier !== "standard") payload.speedTier = speedTier;
  ws.send(JSON.stringify(payload));

  showThinking(`Chain: ${chain.title} starting...`, pane);
}

// ══════════════════════════════════════════════════════════
// Start Orchestration
// ══════════════════════════════════════════════════════════

export function startOrchestration(task, pane) {
  pane = pane || getPane(null);
  const cwd = $.projectSelect.value;
  if (!cwd) {
    addStatus("Select a project first", true, pane);
    return;
  }

  // Render orchestrator header
  const orchId = `orch-${Date.now()}`;
  const div = document.createElement("div");
  div.className = "msg";
  const header = document.createElement("div");
  header.className = "orchestrator-header";
  header.id = orchId;
  header.innerHTML = `
    <div class="orch-header-top">
      <span class="orch-header-icon">${getOrchIcon()}</span>
      <span class="orch-header-title">Orchestrator</span>
      <span class="agent-status-badge running" id="${orchId}-badge">Launching</span>
    </div>
    <div class="orch-task">${escapeHtml(task.length > 200 ? task.slice(0, 200) + '...' : task)}</div>
    <div class="workflow-preview-note">${escapeHtml(getLaunchPreviewNote("orchestrate"))}</div>
    <div class="orch-dispatches" id="${orchId}-dispatches"></div>
  `;
  div.appendChild(header);
  pane.messagesDiv.appendChild(div);
  scrollToBottom(pane);

  // Store orchId on pane for message routing
  pane._orchId = orchId;

  pane.isStreaming = true;
  if (!getState("parallelMode")) {
    $.sendBtn.classList.add("hidden");
    $.stopBtn.classList.remove("hidden");
  }

  const selectedOption = $.projectSelect.options[$.projectSelect.selectedIndex];
  const projectName = selectedOption?.textContent || "Session";
  const ws = getState("ws");
  const model = getSelectedModel();
  const provider = getSelectedProvider();
  const reasoningEffort = getSelectedReasoningEffort();
  const speedTier = getSelectedSpeedTier();

  const payload = {
    type: "orchestrate",
    task,
    cwd,
    sessionId: getState("sessionId"),
    projectName,
    permissionMode: getPermissionMode(),
    provider,
    model,
  };
  if (reasoningEffort) payload.reasoningEffort = reasoningEffort;
  if (speedTier && speedTier !== "standard") payload.speedTier = speedTier;
  ws.send(JSON.stringify(payload));

  showThinking("Orchestrator: analyzing task...", pane);
}

// ══════════════════════════════════════════════════════════
// DAG
// ══════════════════════════════════════════════════════════

async function deleteDag(id, title) {
  if (!confirm(`Delete DAG "${title}"?`)) return;
  try {
    await api.deleteDagApi(id);
    await loadAgents();
  } catch (err) {
    console.error("Failed to delete DAG:", err);
  }
}

export function startDag(dag, pane) {
  pane = pane || getPane(null);
  const cwd = $.projectSelect.value;
  if (!cwd) {
    addStatus("Select a project first", true, pane);
    return;
  }

  const allAgents = getState("agents") || [];
  const agentDefs = dag.nodes.map(n => {
    const a = allAgents.find(ag => ag.id === n.agentId);
    return a ? { ...a, nodeId: n.id } : null;
  }).filter(Boolean);

  if (agentDefs.length < 2) {
    addStatus("DAG needs at least 2 valid agent nodes", true, pane);
    return;
  }

  const dagRunId = `dag-${Date.now()}`;

  // Render DAG execution header
  const div = document.createElement("div");
  div.className = "msg";
  const header = document.createElement("div");
  header.className = "dag-header";
  header.id = dagRunId;
  header.innerHTML = `
    <div class="dag-header-top">
      <span class="dag-header-icon">${getDagIcon()}</span>
      <span class="dag-header-title">${escapeHtml(dag.title)}</span>
      <span class="agent-status-badge running" id="${dagRunId}-badge">Launching</span>
    </div>
    <div class="dag-desc">${escapeHtml(dag.description || `${dag.nodes.length} nodes, ${dag.edges.length} edges`)}</div>
    <div class="workflow-preview-note">${escapeHtml(getLaunchPreviewNote("dag"))}</div>
    <div class="dag-graph" id="${dagRunId}-graph">
      ${dag.nodes.map(n => {
        const a = allAgents.find(ag => ag.id === n.agentId);
        const name = a ? a.title : n.agentId;
        return `<div class="dag-graph-node" id="${dagRunId}-node-${n.id}">
          <span class="dag-graph-node-name">${escapeHtml(name)}</span>
          <span class="dag-graph-node-status" id="${dagRunId}-node-status-${n.id}">pending</span>
        </div>`;
      }).join('')}
    </div>
  `;
  div.appendChild(header);
  pane.messagesDiv.appendChild(div);
  scrollToBottom(pane);

  // Store dagRunId on pane for message routing
  pane._dagRunId = dagRunId;

  pane.isStreaming = true;
  if (!getState("parallelMode")) {
    $.sendBtn.classList.add("hidden");
    $.stopBtn.classList.remove("hidden");
  }

  const selectedOption = $.projectSelect.options[$.projectSelect.selectedIndex];
  const projectName = selectedOption?.textContent || "Session";
  const ws = getState("ws");
  const model = getSelectedModel();
  const provider = getSelectedProvider();
  const reasoningEffort = getSelectedReasoningEffort();
  const speedTier = getSelectedSpeedTier();

  const payload = {
    type: "agent_dag",
    dag,
    agents: allAgents,
    cwd,
    sessionId: getState("sessionId"),
    projectName,
    permissionMode: getPermissionMode(),
    provider,
    model,
  };
  if (reasoningEffort) payload.reasoningEffort = reasoningEffort;
  if (speedTier && speedTier !== "standard") payload.speedTier = speedTier;
  ws.send(JSON.stringify(payload));

  showThinking(`DAG: ${dag.title} starting...`, pane);
}

// ══════════════════════════════════════════════════════════
// WebSocket Message Handlers
// ══════════════════════════════════════════════════════════

export function handleAgentMessage(msg, pane) {
  switch (msg.type) {
    case "agent_started":
      {
        const badge = document.getElementById(`agent-badge-${msg.agentId}`);
        if (badge) badge.textContent = "Running";
      }
      showThinking(`Agent: ${msg.title} working...`, pane);
      break;

    case "agent_progress": {
      const turnsEl = document.getElementById(`agent-turns-${msg.agentId}`);
      if (turnsEl) turnsEl.textContent = `${msg.turn}/${msg.maxTurns} turns`;

      const log = document.getElementById(`agent-log-${msg.agentId}`);
      if (log) {
        const entry = document.createElement("div");
        entry.className = "agent-log-entry";
        const detail = msg.detail ? ` ${escapeHtml(msg.detail)}` : "";
        entry.innerHTML = `<span class="agent-log-action">${escapeHtml(msg.action)}</span>${detail}`;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
      }

      showThinking(`Agent: ${msg.action}...`, pane);
      break;
    }

    case "agent_completed": {
      const badge = document.getElementById(`agent-badge-${msg.agentId}`);
      if (badge) {
        badge.textContent = "Completed";
        badge.className = "agent-status-badge completed";
      }

      if (pane._agentTimerId) {
        clearInterval(pane._agentTimerId);
        pane._agentTimerId = null;
      }

      const turnsEl = document.getElementById(`agent-turns-${msg.agentId}`);
      if (turnsEl) turnsEl.textContent = `${msg.totalTurns} turns`;

      const elapsedEl = document.getElementById(`agent-elapsed-${msg.agentId}`);
      if (elapsedEl) {
        const secs = Math.round((msg.durationMs || 0) / 1000);
        const mins = Math.floor(secs / 60);
        const s = secs % 60;
        elapsedEl.textContent = mins > 0 ? `${mins}m ${s}s` : `${secs}s`;
      }

      removeThinking(pane);
      addStatus(`Agent completed (${msg.totalTurns} turns, $${(msg.costUsd || 0).toFixed(4)})`, false, pane);
      finishAgentStreaming(pane);
      break;
    }

    case "agent_error": {
      const badge = document.getElementById(`agent-badge-${msg.agentId}`);
      if (badge) {
        badge.textContent = "Error";
        badge.className = "agent-status-badge error";
      }
      if (pane._agentTimerId) {
        clearInterval(pane._agentTimerId);
        pane._agentTimerId = null;
      }
      removeThinking(pane);
      finishAgentStreaming(pane);
      break;
    }

    case "agent_aborted": {
      const badge = document.getElementById(`agent-badge-${msg.agentId}`);
      if (badge) {
        badge.textContent = "Aborted";
        badge.className = "agent-status-badge error";
      }
      if (pane._agentTimerId) {
        clearInterval(pane._agentTimerId);
        pane._agentTimerId = null;
      }
      removeThinking(pane);
      finishAgentStreaming(pane);
      break;
    }

    // ── Chain messages ──

    case "agent_chain_started":
      {
        const chainBadge = document.getElementById(`chain-badge-${msg.chainId}`);
        if (chainBadge) chainBadge.textContent = "Running";
      }
      showThinking(`Chain: ${msg.title} — ${msg.totalSteps} agents...`, pane);
      break;

    case "agent_chain_step": {
      const statusEl = document.getElementById(`chain-step-status-${msg.chainId}-${msg.stepIndex}`);
      const stepEl = document.getElementById(`chain-step-${msg.chainId}-${msg.stepIndex}`);
      if (statusEl) {
        statusEl.textContent = msg.status;
        statusEl.className = `chain-pipeline-status ${msg.status}`;
      }
      if (stepEl) {
        stepEl.className = `chain-pipeline-step ${msg.status}`;
      }
      if (msg.status === "running") {
        showThinking(`Chain step ${msg.stepIndex + 1}: ${msg.agentTitle} working...`, pane);
      }
      break;
    }

    case "agent_chain_completed": {
      const chainBadge = document.getElementById(`chain-badge-${msg.chainId}`);
      if (chainBadge) {
        chainBadge.textContent = "Completed";
        chainBadge.className = "agent-status-badge completed";
      }
      if (pane._chainTimerId) {
        clearInterval(pane._chainTimerId);
        pane._chainTimerId = null;
      }

      // Show shared context summary
      if (msg.runId) {
        api.fetchAgentContext(msg.runId).then(contexts => {
          if (contexts.length > 0) {
            const ctxDiv = document.createElement("div");
            ctxDiv.className = "chain-context-summary";
            ctxDiv.innerHTML = `
              <div class="chain-context-header">Shared Context (${contexts.length} entries)</div>
              ${contexts.map(c => `
                <div class="chain-context-entry">
                  <span class="chain-context-agent">${escapeHtml(c.agent_id)}</span>
                  <span class="chain-context-preview">${escapeHtml((c.value || "").slice(0, 150))}${c.value?.length > 150 ? '...' : ''}</span>
                </div>
              `).join("")}
            `;
            const pipeline = document.getElementById(`chain-pipeline-${msg.chainId}`);
            if (pipeline) pipeline.parentElement.appendChild(ctxDiv);
          }
        }).catch(() => {});
      }

      removeThinking(pane);
      addStatus(`Chain completed`, false, pane);
      finishAgentStreaming(pane);
      break;
    }

    // ── Orchestrator messages ──

    case "orchestrator_started":
      {
        const orchId = pane._orchId;
        const badge = orchId ? document.getElementById(`${orchId}-badge`) : null;
        if (badge) badge.textContent = "Planning";
      }
      showThinking("Orchestrator: planning...", pane);
      break;

    case "orchestrator_phase": {
      const orchId = pane._orchId;
      const badge = orchId ? document.getElementById(`${orchId}-badge`) : null;
      if (badge) {
        badge.textContent = msg.phase === "planning" ? "Planning" : "Synthesizing";
      }
      showThinking(`Orchestrator: ${msg.phase}...`, pane);
      break;
    }

    case "orchestrator_dispatching": {
      const orchId = pane._orchId;
      const badge = orchId ? document.getElementById(`${orchId}-badge`) : null;
      if (badge) badge.textContent = `Dispatching ${msg.totalAgents} agents`;

      const container = orchId ? document.getElementById(`${orchId}-dispatches`) : null;
      if (container && msg.dispatches) {
        container.innerHTML = msg.dispatches.map((d, i) => `
          <div class="orch-dispatch-row" id="orch-dispatch-${i}">
            <span class="orch-dispatch-num">${i + 1}</span>
            <span class="orch-dispatch-agent">${escapeHtml(d.agentId)}</span>
            <span class="orch-dispatch-ctx">${escapeHtml(d.context)}</span>
            <span class="orch-dispatch-status" id="orch-dispatch-status-${i}">queued</span>
          </div>
        `).join("");
      }
      showThinking(`Orchestrator: dispatching ${msg.totalAgents} agents...`, pane);
      break;
    }

    case "orchestrator_dispatch": {
      const statusEl = document.getElementById(`orch-dispatch-status-${msg.stepIndex}`);
      const rowEl = document.getElementById(`orch-dispatch-${msg.stepIndex}`);
      if (statusEl) {
        statusEl.textContent = msg.status;
        statusEl.className = `orch-dispatch-status ${msg.status}`;
      }
      if (rowEl) {
        rowEl.className = `orch-dispatch-row ${msg.status}`;
      }
      if (msg.status === "running") {
        showThinking(`Orchestrator: ${msg.agentTitle} working...`, pane);
      }
      break;
    }

    case "orchestrator_dispatch_skip":
      addStatus(`Skipped agent "${msg.agentId}": ${msg.reason}`, true, pane);
      break;

    case "orchestrator_error": {
      const orchId = pane._orchId;
      const badge = orchId ? document.getElementById(`${orchId}-badge`) : null;
      if (badge) {
        badge.textContent = "Error";
        badge.className = "agent-status-badge error";
      }
      removeThinking(pane);
      finishAgentStreaming(pane);
      break;
    }

    case "orchestrator_completed": {
      const orchId = pane._orchId;
      const badge = orchId ? document.getElementById(`${orchId}-badge`) : null;
      if (badge) {
        badge.textContent = "Completed";
        badge.className = "agent-status-badge completed";
      }
      removeThinking(pane);
      addStatus(`Orchestrator completed (${msg.dispatched} agents dispatched)`, false, pane);
      finishAgentStreaming(pane);
      break;
    }

    // ── DAG messages ──

    case "dag_started":
      {
        const dagRunId = pane._dagRunId;
        const badge = dagRunId ? document.getElementById(`${dagRunId}-badge`) : null;
        if (badge) badge.textContent = "Running";
      }
      showThinking(`DAG: ${msg.title} — ${msg.totalNodes} nodes...`, pane);
      break;

    case "dag_level":
      showThinking(`DAG: running level ${msg.level + 1} (${msg.nodeIds.length} parallel nodes)...`, pane);
      break;

    case "dag_node": {
      const dagRunId = pane._dagRunId;
      if (dagRunId) {
        const statusEl = document.getElementById(`${dagRunId}-node-status-${msg.nodeId}`);
        const nodeEl = document.getElementById(`${dagRunId}-node-${msg.nodeId}`);
        if (statusEl) {
          statusEl.textContent = msg.status;
          statusEl.className = `dag-graph-node-status ${msg.status}`;
        }
        if (nodeEl) {
          nodeEl.className = `dag-graph-node ${msg.status}`;
        }
      }
      if (msg.status === "running") {
        showThinking(`DAG: ${msg.agentTitle || msg.nodeId} working...`, pane);
      }
      break;
    }

    case "dag_completed": {
      const dagRunId = pane._dagRunId;
      if (dagRunId) {
        const badge = document.getElementById(`${dagRunId}-badge`);
        if (badge) {
          badge.textContent = "Completed";
          badge.className = "agent-status-badge completed";
        }
      }
      removeThinking(pane);
      addStatus(`DAG completed (${msg.succeeded}/${msg.totalNodes} succeeded)`, false, pane);
      finishAgentStreaming(pane);
      break;
    }

    case "dag_error": {
      const dagRunId = pane._dagRunId;
      if (dagRunId) {
        const badge = document.getElementById(`${dagRunId}-badge`);
        if (badge) {
          badge.textContent = "Error";
          badge.className = "agent-status-badge error";
        }
      }
      removeThinking(pane);
      addStatus(`DAG error: ${msg.error}`, true, pane);
      finishAgentStreaming(pane);
      break;
    }
  }
}

// ══════════════════════════════════════════════════════════
// Icons
// ══════════════════════════════════════════════════════════

function getAgentIcon(icon) {
  const icons = {
    search: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>`,
    bug: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m8 2 1.88 1.88M14.12 3.88 16 2M9 7.13v-1a3.003 3.003 0 1 1 6 0v1"/><path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6"/><path d="M12 20v-9"/><path d="M6.53 9C4.6 8.8 3 7.1 3 5"/><path d="M6 13H2"/><path d="M3 21c0-2.1 1.7-3.9 3.8-4"/><path d="M20.97 5c0 2.1-1.6 3.8-3.5 4"/><path d="M22 13h-4"/><path d="M17.2 17c2.1.1 3.8 1.9 3.8 4"/></svg>`,
    check: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
    tool: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`,
  };
  return icons[icon] || icons.tool;
}

function getChainIcon() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`;
}

function getOrchIcon() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="M12 8v4M8.5 16.5 10.5 13M15.5 16.5 13.5 13"/></svg>`;
}

function getMonitorIcon() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>`;
}

function getDagIcon() {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="3" width="6" height="6" rx="1"/><rect x="9" y="15" width="6" height="6" rx="1"/><path d="M9 6h6M6 9v3l3 3M18 9v3l-3 3"/></svg>`;
}

// ══════════════════════════════════════════════════════════
// Panel toggle
// ══════════════════════════════════════════════════════════

function toggleAgentSidebar(forceOpen) {
  const sidebar = $.agentSidebar;
  if (!sidebar) return;
  const isOpen = !sidebar.classList.contains("hidden");
  if (forceOpen === true || !isOpen) {
    sidebar.classList.remove("hidden");
    $.agentBtn.classList.add("active");
  } else {
    sidebar.classList.add("hidden");
    $.agentBtn.classList.remove("active");
  }
}

bindListener($.agentBtn, "click", () => {
  $.toolboxPanel.classList.add("hidden");
  $.toolboxBtn.classList.remove("active");
  toggleAgentSidebar();
});

bindListener($.agentSidebarClose, "click", () => {
  toggleAgentSidebar(false);
  $.agentSidebar.classList.add("hidden");
  $.agentBtn.classList.remove("active");
});

// ══════════════════════════════════════════════════════════
// Orchestrate Modal
// ══════════════════════════════════════════════════════════

function openOrchModal() {
  if (!$.orchModal) return;
  $.orchTaskInput.value = "";
  $.orchModal.classList.remove("hidden");
  setTimeout(() => $.orchTaskInput.focus(), 100);
}

function closeOrchModal() {
  if (!$.orchModal) return;
  $.orchModal.classList.add("hidden");
}

bindListener($.orchModalClose, "click", closeOrchModal);
bindListener($.orchModalCancel, "click", closeOrchModal);
bindListener($.orchModal, "click", (e) => {
  if (e.target === $.orchModal) closeOrchModal();
});

bindListener($.orchModalRun, "click", () => {
  const task = $.orchTaskInput.value.trim();
  if (!task) {
    $.orchTaskInput.focus();
    return;
  }
  closeOrchModal();
  startOrchestration(task, getPane(null));
});

// Ctrl/Cmd+Enter to submit
bindListener($.orchTaskInput, "keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    $.orchModalRun?.click();
  }
  if (e.key === "Escape") closeOrchModal();
});
