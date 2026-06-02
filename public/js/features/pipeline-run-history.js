/**
 * pipeline-run-history.js — PIPE5
 * Run history surface: table of recent pipeline_runs across all pipelines.
 * Entry point: initPipelineRunHistoryPage(); mount root: #pipeline-run-history-root
 */
import { escapeHtml } from "../core/utils.js";
import * as api from "../core/api.js";

const MOUNT = "#pipeline-run-history-root";
const getRoot = () => document.querySelector(MOUNT);
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "partial_complete"]);

const STATUS_LABEL = {
  queued: "Queued", running: "Running", awaiting_approval: "Awaiting approval",
  succeeded: "Succeeded", failed: "Failed", cancelled: "Cancelled", partial_complete: "Stopped: cost cap",
};
const STATUS_MOD = {
  queued: "queued", running: "running", awaiting_approval: "waiting",
  succeeded: "success", failed: "failed", cancelled: "cancelled", partial_complete: "warning",
};

let _runs = [], _pipelines = {}, _loaded = false, _error = null;
let _statusFilter = "all", _sortBy = "started";

function _fmtDuration(run) {
  const start = run.startedAt || run.started_at;
  const end = run.completedAt || run.completed_at;
  if (!start) return "—";
  const s = Math.floor((end ? new Date(end) - new Date(start) : Date.now() - new Date(start)) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function _fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function _nodeProgress(run) {
  const ns = run.nodeStates || run.node_states || {};
  const entries = Object.values(ns);
  if (!entries.length) return "—";
  const done = entries.filter((n) => n && TERMINAL.has(n.status)).length;
  return `${done}/${entries.length}`;
}

function _pipelineName(run) {
  const id = run.pipelineId || run.pipeline_id;
  return _pipelines[id]?.name || id || "—";
}

function _filtered() {
  let list = _statusFilter === "all" ? _runs.slice() : _runs.filter((r) => r.status === _statusFilter);
  if (_sortBy === "status") list.sort((a, b) => (a.status || "").localeCompare(b.status || ""));
  else if (_sortBy === "pipeline") list.sort((a, b) => _pipelineName(a).localeCompare(_pipelineName(b)));
  else list.sort((a, b) => new Date(b.startedAt || b.started_at || b.createdAt || 0) - new Date(a.startedAt || a.started_at || a.createdAt || 0));
  return list;
}

function _rowActions(run) {
  const { id } = run;
  const pid = run.pipelineId || run.pipeline_id;
  const parts = [];
  if (!TERMINAL.has(run.status)) parts.push(`<button class="pbtn pbtn-g prh-cancel" data-run-id="${escapeHtml(id)}">Cancel</button>`);
  if (run.status === "awaiting_approval") parts.push(`<a class="pbtn pbtn-g" href="#operations/approvals">Resume</a>`);
  if (TERMINAL.has(run.status) && pid) parts.push(`<button class="pbtn pbtn-g prh-retry" data-pipeline-id="${escapeHtml(pid)}">Retry</button>`);
  if (pid) parts.push(`<button class="pbtn pbtn-p prh-open-canvas" data-run-id="${escapeHtml(id)}" data-pipeline-id="${escapeHtml(pid)}">Canvas</button>`);
  return parts.join("");
}

function _row(run) {
  const mod = STATUS_MOD[run.status] || "unknown";
  return `<tr class="prh-row" data-run-id="${escapeHtml(run.id)}" data-pipeline-id="${escapeHtml(run.pipelineId || run.pipeline_id || "")}">
    <td class="prh-td"><span class="prh-name">${escapeHtml(_pipelineName(run))}</span></td>
    <td class="prh-td">${_fmtTime(run.startedAt || run.started_at)}</td>
    <td class="prh-td">${escapeHtml(_fmtDuration(run))}</td>
    <td class="prh-td"><span class="pb pb-${mod}">${escapeHtml(STATUS_LABEL[run.status] || run.status)}</span></td>
    <td class="prh-td">${escapeHtml(run.trigger || "manual")}</td>
    <td class="prh-td">${escapeHtml(_nodeProgress(run))}</td>
    <td class="prh-td prh-actions">${_rowActions(run)}</td>
  </tr>`;
}

export function render() {
  const root = getRoot();
  if (!root) return;
  if (!_loaded) { root.innerHTML = `<div class="ppg"><div class="pload">Loading run history…</div></div>`; return; }
  const filtered = _filtered();
  const STATUSES = ["all", "running", "queued", "awaiting_approval", "succeeded", "failed", "cancelled"];
  root.innerHTML = `<div class="ppg">
    <div class="ppgh">
      <div class="pptr"><h2>Pipeline Run History</h2><button class="pbtn pbtn-g" id="prh-refresh">↻ Refresh</button></div>
      <div class="ptb">
        <div class="psrt"><span class="prh-filter-label">Status:</span>
          ${STATUSES.map((s) => `<button class="psb ${_statusFilter === s ? "psb-a" : ""}" data-status="${s}">${s === "all" ? "All" : (STATUS_LABEL[s] || s)}</button>`).join("")}
        </div>
        <div class="psrt"><span class="prh-filter-label">Sort:</span>
          <button class="psb ${_sortBy === "started" ? "psb-a" : ""}" data-sort="started">Started</button>
          <button class="psb ${_sortBy === "status" ? "psb-a" : ""}" data-sort="status">Status</button>
          <button class="psb ${_sortBy === "pipeline" ? "psb-a" : ""}" data-sort="pipeline">Pipeline</button>
        </div>
      </div>
    </div>
    ${_error ? `<div class="pempty"><strong>Load failed</strong><span>${escapeHtml(_error)}</span></div>` : ""}
    <div class="prh-table-wrap"><table class="prh-table">
      <thead><tr>
        <th class="prh-th">Pipeline</th><th class="prh-th">Started</th><th class="prh-th">Duration</th>
        <th class="prh-th">Status</th><th class="prh-th">Trigger</th><th class="prh-th">Nodes</th>
        <th class="prh-th">Actions</th>
      </tr></thead>
      <tbody id="prh-tbody">${filtered.length ? filtered.map(_row).join("") : `<tr><td colspan="7" class="prh-empty">No runs found.</td></tr>`}</tbody>
    </table></div>
  </div>`;
  _wire(root);
}

function _showToast(msg, isError = false) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `bg-toast${isError ? " toast-error" : ""}`;
  toast.innerHTML = `<span class="bg-toast-dot"></span><div class="bg-toast-body"><div class="bg-toast-label"></div></div><button class="bg-toast-close">&times;</button>`;
  toast.querySelector(".bg-toast-label").textContent = msg;
  const dismiss = () => { toast.classList.add("toast-exit"); toast.addEventListener("animationend", () => toast.remove(), { once: true }); };
  toast.querySelector(".bg-toast-close")?.addEventListener("click", dismiss);
  container.appendChild(toast);
  setTimeout(() => { if (toast.parentNode) dismiss(); }, 3500);
}

function _openCanvasReplay(pipelineId, runId) {
  if (!pipelineId) return;
  const run = _runs.find((r) => r.id === runId);
  window.dispatchEvent(new CustomEvent("artemis:open-pipeline-canvas", { detail: { pipelineId, replayRun: run || null } }));
}

function _wire(root) {
  root.querySelector("#prh-refresh")?.addEventListener("click", () => load());
  root.querySelectorAll("[data-status]").forEach((b) => b.addEventListener("click", () => { _statusFilter = b.dataset.status; render(); }));
  root.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => { _sortBy = b.dataset.sort; render(); }));
  root.querySelectorAll(".prh-cancel").forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    try { await api.cancelPipelineRunApi(b.dataset.runId); await load(); } catch (err) { _showToast(`Cancel failed: ${err.message}`, true); }
  }));
  root.querySelectorAll(".prh-retry").forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    try { await api.retryPipelineRunApi(b.dataset.pipelineId); await load(); _showToast("New run started."); } catch (err) { _showToast(`Retry failed: ${err.message}`, true); }
  }));
  root.querySelectorAll(".prh-open-canvas").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation(); _openCanvasReplay(b.dataset.pipelineId, b.dataset.runId);
  }));
  root.querySelectorAll(".prh-row").forEach((row) => row.addEventListener("click", (e) => {
    if (e.target.closest("button,a")) return;
    if (row.dataset.pipelineId && row.dataset.runId) _openCanvasReplay(row.dataset.pipelineId, row.dataset.runId);
  }));
}

export async function load() {
  _error = null;
  try {
    const [runs, pipelines] = await Promise.all([api.listAllPipelineRunsApi({ limit: 100 }), api.listPipelinesApi({ limit: 100 })]);
    _runs = runs || [];
    _pipelines = {};
    for (const p of (pipelines || [])) _pipelines[p.id] = p;
    _loaded = true;
  } catch (e) {
    _error = e.message || String(e);
    _loaded = true;
  }
  render();
}

export function initPipelineRunHistoryPage() {
  _runs = []; _pipelines = {}; _loaded = false; _error = null;
  _statusFilter = "all"; _sortBy = "started";
  render();
  load();
}
