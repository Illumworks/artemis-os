/**
 * pipeline-run-overlay.js — PIPE5
 * Bottom-right floating panel showing live run status.
 * Props: pipelineId {string}, onCancel {(runId) => void}
 */

import { setState } from "../core/store.js";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled", "partial_complete", "skipped"]);

const STATUS_LABEL = {
  queued: "Queued", running: "Running", awaiting_approval: "Awaiting approval",
  succeeded: "Succeeded", failed: "Failed", cancelled: "Cancelled", partial_complete: "Stopped: cost cap",
  skipped: "Skipped",
};

function _elapsed(startedAt) {
  if (!startedAt) return "";
  const s = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function _nodeProgress(nodeStates) {
  if (!nodeStates || typeof nodeStates !== "object") return { done: 0, total: 0 };
  const entries = Object.values(nodeStates);
  const done = entries.filter((ns) => ns && typeof ns === "object" &&
    ["succeeded", "failed", "partial_complete"].includes(ns.status)).length;
  return { done, total: entries.length };
}

export class PipelineRunOverlay {
  constructor({ pipelineId, onCancel }) {
    this._pipelineId = pipelineId;
    this._onCancel = onCancel;
    this.el = null;
    this._currentRunId = null;
    this._elapsedTimer = null;
    this._startedAt = null;
  }

  mount(parent) {
    this.el = document.createElement("div");
    this.el.className = "pcv-run-overlay pcv-run-overlay--hidden";
    this.el.innerHTML = `
      <div class="pcv-ro-header">
        <span class="pcv-ro-title">Active Run</span>
        <button class="pcv-ro-dismiss" title="Dismiss">×</button>
      </div>
      <div class="pcv-ro-body">
        <div class="pcv-ro-row">
          <button class="pcv-ro-run-id" title="Click to copy run ID"></button>
          <span class="pcv-ro-status pcv-ro-status--queued">Queued</span>
        </div>
        <div class="pcv-ro-progress"></div>
        <div class="pcv-ro-elapsed"></div>
        <div class="pcv-ro-actions">
          <a class="pcv-ro-history-link" href="#/pipeline-run-history">View in run history →</a>
          <a class="pcv-ro-approve" href="#operations/approvals" style="display:none">Approve at Gate →</a>
          <button class="pcv-ro-cancel pcv-ro-btn pbtn pbtn-g">Cancel run</button>
        </div>
      </div>`;
    parent.appendChild(this.el);
    this._wireEvents();
  }

  destroy() {
    if (this._elapsedTimer) clearInterval(this._elapsedTimer);
    this.el?.remove();
  }

  show(runId) {
    this._currentRunId = runId;
    this.el?.classList.remove("pcv-run-overlay--hidden");
  }

  hide() {
    this.el?.classList.add("pcv-run-overlay--hidden");
    if (this._elapsedTimer) { clearInterval(this._elapsedTimer); this._elapsedTimer = null; }
  }

  update(run) {
    if (!this.el || !run) return;
    const runId = run.id || "";
    const status = run.status || "queued";
    const startedAt = run.startedAt || run.started_at || null;
    const nodeStates = run.nodeStates || run.node_states || {};

    this._currentRunId = runId;
    this.el.classList.remove("pcv-run-overlay--hidden");

    const idEl = this.el.querySelector(".pcv-ro-run-id");
    if (idEl) { idEl.textContent = `#${String(runId).slice(0, 7)}`; idEl.title = runId; }

    const badgeEl = this.el.querySelector(".pcv-ro-status");
    if (badgeEl) {
      badgeEl.textContent = STATUS_LABEL[status] || status;
      badgeEl.className = `pcv-ro-status pcv-ro-status--${status.replace("_", "-")}`;
    }

    const { done, total } = _nodeProgress(nodeStates);
    const progEl = this.el.querySelector(".pcv-ro-progress");
    if (progEl) progEl.textContent = total > 0 ? `${done}/${total} nodes complete` : "";

    this._startedAt = startedAt;
    if (!this._elapsedTimer && startedAt) {
      this._tickElapsed();
      this._elapsedTimer = setInterval(() => this._tickElapsed(), 1000);
    }

    const isTerminal = TERMINAL_STATUSES.has(status);
    const cancelBtn = this.el.querySelector(".pcv-ro-cancel");
    if (cancelBtn) {
      cancelBtn.disabled = isTerminal;
      cancelBtn.textContent = isTerminal ? "Run completed" : "Cancel run";
      cancelBtn.title = isTerminal ? `Run already ${STATUS_LABEL[status] || status}` : "Cancel run";
    }

    const approveEl = this.el.querySelector(".pcv-ro-approve");
    if (approveEl) approveEl.style.display = status === "awaiting_approval" ? "" : "none";

    if (isTerminal) {
      if (this._elapsedTimer) { clearInterval(this._elapsedTimer); this._elapsedTimer = null; }
    }
  }

  _tickElapsed() {
    const el = this.el?.querySelector(".pcv-ro-elapsed");
    if (el) el.textContent = this._startedAt ? `Started ${_elapsed(this._startedAt)} ago` : "";
  }

  _wireEvents() {
    if (!this.el) return;
    this.el.querySelector(".pcv-ro-dismiss")?.addEventListener("click", () => this.hide());
    this.el.querySelector(".pcv-ro-history-link")?.addEventListener("click", (e) => {
      e.preventDefault();
      setState("view", "pipeline-run-history");
    });
    this.el.querySelector(".pcv-ro-run-id")?.addEventListener("click", () => {
      if (this._currentRunId) navigator.clipboard?.writeText(this._currentRunId).catch(() => {});
    });
    this.el.querySelector(".pcv-ro-cancel")?.addEventListener("click", async () => {
      if (this._currentRunId && this._onCancel) await this._onCancel(this._currentRunId);
    });
  }
}
