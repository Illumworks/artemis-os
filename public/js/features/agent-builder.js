/**
 * Agent-Builder frontend — /operations/agents/builder (O1)
 *
 * Three-column layout:
 *   Left rail  — list of in-flight builder sessions
 *   Center     — chat with the Agent-Builder
 *   Right rail — live preview of the current draft definition
 *
 * Drives the existing /api/builder/* routes.
 */

import { escapeHtml } from "../core/utils.js";
import * as api from "../core/api.js";
import { getState, setState } from "../core/store.js";

// ── Persistence keys (mirror dev_projects.js STORAGE pattern) ─────────────────

const BUILDER_SELECTED_SESSION_KEY = "artemis.builder.selectedSession";

// ── State ──────────────────────────────────────────────────────────────────────

let _sessions = [];
let _selectedSessionId = null;
let _currentSession = null; // { id, conversation, draft, status }
let _pendingProposals = []; // proposals for the current session
let _sending = false;

// ── API helpers ────────────────────────────────────────────────────────────────

async function fetchSessions() {
  const data = await api.builderFetchSessions();
  // Filter abandoned sessions — backend is source of truth; don't surface
  // sessions the user has already discarded (mirrors dev_projects.js archived filter).
  _sessions = (data.sessions || []).filter((s) => s.status !== "abandoned");
}

async function createSession({ target_id } = {}) {
  // CC18: pass target_id when entering from an agent profile so the Builder
  // LLM runs read_recent_runs() against that agent's history. Omit it for the
  // generic "New session" flow so users can still build a fresh agent.
  const payload = { builder_kind: "agent" };
  if (target_id != null) payload.target_id = target_id;
  const data = await api.builderCreateSession(payload);
  _sessions = [data, ..._sessions];
  return data;
}

async function loadSession(sessionId) {
  const data = await api.builderGetSession(sessionId);
  _currentSession = data;
  return data;
}

async function sendMessage(sessionId, content) {
  return api.builderSendMessage(sessionId, content);
}

async function approveProposal(proposalId) {
  return api.builderApproveProposal(proposalId);
}

async function rejectProposal(proposalId) {
  return api.builderRejectProposal(proposalId);
}

async function abandonSession(sessionId) {
  await api.builderAbandonSession(sessionId);
  _sessions = _sessions.filter((s) => s.id !== sessionId);
  if (_selectedSessionId === sessionId) {
    _selectedSessionId = null;
    _currentSession = null;
    _pendingProposals = [];
  }
}

async function fetchPendingProposals(sessionId) {
  const data = await api.builderFetchProposals({ status: "pending" });
  _pendingProposals = (data.proposals || []).filter(
    (p) => p.builder_session_id === sessionId,
  );
}

// ── Render helpers ─────────────────────────────────────────────────────────────

function _lookupAgentLabel(dbId) {
  // CC18: builder_sessions.target_id is the agent int PK; resolve back to the
  // slug/name for display. The normalised agents in state carry both `dbId`
  // (int PK) and `id` (slug, after _normaliseAgent).
  const agents = Array.isArray(getState("agents")) ? getState("agents") : [];
  const hit = agents.find((a) => a.dbId === dbId);
  return hit ? (hit.title || hit.id || String(dbId)) : `agent #${dbId}`;
}

function renderSessionStatus(status) {
  const labels = { active: "Active", committed: "Committed", abandoned: "Abandoned" };
  const tones = { active: "success", committed: "info", abandoned: "muted" };
  return `<span class="builder-session-status builder-session-status-${escapeHtml(status || "active")}">${escapeHtml(labels[status] || status)}</span>`;
}

function renderSessionList() {
  if (!_sessions.length) {
    return `
      <div class="builder-sessions-empty">
        <p>No sessions yet.</p>
        <p>Start a new one to build your first agent.</p>
      </div>
    `;
  }
  return _sessions.map((s) => {
    const active = s.id === _selectedSessionId;
    const preview = s.conversation?.[0]?.content
      ? String(s.conversation[0].content).slice(0, 60) + (String(s.conversation[0].content).length > 60 ? "…" : "")
      : "New session";
    return `
      <button
        type="button"
        class="builder-session-row${active ? " active" : ""}"
        data-builder-action="select-session"
        data-session-id="${s.id}"
      >
        <span class="builder-session-row-preview">${escapeHtml(preview)}</span>
        ${renderSessionStatus(s.status)}
      </button>
    `;
  }).join("");
}

function renderMessage(msg) {
  const isUser = msg.role === "user";
  const content = String(msg.content || "");
  // Minimal markdown: code fences + line breaks
  const escaped = escapeHtml(content);
  const rendered = escaped
    .replace(/```([a-z]*)\n([\s\S]*?)```/g, (_m, lang, code) =>
      `<pre><code class="language-${escapeHtml(lang)}">${code}</code></pre>`)
    .replace(/\n/g, "<br>");
  return `
    <article class="builder-msg builder-msg-${isUser ? "user" : "assistant"}">
      <div class="builder-msg-role">${isUser ? "You" : "Agent-Builder"}</div>
      <div class="builder-msg-body">${rendered}</div>
    </article>
  `;
}

function renderConversation(conversation) {
  if (!conversation?.length) {
    return `
      <div class="builder-chat-empty">
        <div class="builder-chat-empty-icon">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
          </svg>
        </div>
        <p>Describe the agent you want to build.</p>
        <p class="builder-chat-empty-hint">The Agent-Builder will ask clarifying questions and generate a draft definition.</p>
      </div>
    `;
  }
  return conversation.map(renderMessage).join("");
}

function renderDraftField(label, value) {
  if (!value) return "";
  const displayValue = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  return `
    <div class="builder-draft-field">
      <div class="builder-draft-field-label">${escapeHtml(label)}</div>
      <div class="builder-draft-field-value">${escapeHtml(displayValue)}</div>
    </div>
  `;
}

function renderDraftPanel(draft) {
  if (!draft || !Object.keys(draft).length) {
    return `
      <div class="builder-draft-empty">
        <p>Draft definition will appear here as the conversation progresses.</p>
      </div>
    `;
  }
  return `
    <div class="builder-draft-fields">
      ${renderDraftField("Name", draft.name)}
      ${renderDraftField("Goal", draft.goal)}
      ${renderDraftField("Model", draft.model)}
      ${renderDraftField("Tools", draft.tools?.join(", "))}
      ${renderDraftField("Trigger", draft.trigger)}
      ${draft.system_prompt ? `
        <div class="builder-draft-field builder-draft-field-prompt">
          <div class="builder-draft-field-label">System prompt</div>
          <pre class="builder-draft-field-pre">${escapeHtml(String(draft.system_prompt))}</pre>
        </div>
      ` : ""}
    </div>
  `;
}

function renderProposalCitations(citations) {
  if (!citations) return "";
  const summary = citations.summary || "";
  const runIds = citations.run_ids || [];
  const obs = citations.observations || [];

  const runLinks = runIds.map(
    (id) =>
      `<a class="builder-citation-run-link" href="#" data-builder-action="view-run" data-run-id="${id}">Run #${escapeHtml(String(id))}</a>`,
  );

  const obsList = obs.length
    ? obs
        .map((o) => {
          const parts = [];
          if (o.what_stalled) parts.push(`Stalled: ${escapeHtml(String(o.what_stalled))}`);
          if (o.what_was_missing)
            parts.push(`Missing: ${escapeHtml(String(o.what_was_missing))}`);
          if (o.what_worked) parts.push(`Worked: ${escapeHtml(String(o.what_worked))}`);
          return parts.length
            ? `<li class="builder-citation-obs"><strong>Run #${o.run_id}</strong>: ${parts.join(" · ")}</li>`
            : "";
        })
        .filter(Boolean)
        .join("")
    : "";

  return `
    <div class="builder-citations">
      <div class="builder-citations-label">Based on runs:</div>
      <div class="builder-citations-runs">${runLinks.join(" ") || "—"}</div>
      ${summary ? `<p class="builder-citations-summary">${escapeHtml(summary)}</p>` : ""}
      ${obsList ? `<ul class="builder-citations-obs-list">${obsList}</ul>` : ""}
    </div>
  `;
}

function renderPendingProposals(proposals) {
  if (!proposals || !proposals.length) return "";
  return proposals
    .map(
      (p) => `
      <div class="builder-proposal-card">
        <div class="builder-proposal-card-header">
          <span class="builder-proposal-kind">${escapeHtml(p.kind)}</span>
          <span class="builder-proposal-status">Pending approval</span>
        </div>
        ${renderProposalCitations(p.citations)}
        <div class="builder-proposal-actions">
          <button type="button" class="ops-button ops-button-primary builder-proposal-approve"
            data-builder-action="approve-proposal" data-proposal-id="${p.id}">
            Approve
          </button>
          <button type="button" class="ops-button ops-button-ghost builder-proposal-reject"
            data-builder-action="reject-proposal" data-proposal-id="${p.id}">
            Reject
          </button>
        </div>
      </div>
    `,
    )
    .join("");
}

// ── Main render ────────────────────────────────────────────────────────────────

export function renderAgentBuilderPage() {
  const session = _currentSession;
  const conversation = session?.conversation || [];
  const draft = session?.draft || {};

  return `
    <div class="builder-shell">
      <!-- Left rail: session list -->
      <aside class="builder-sessions-rail">
        <div class="builder-sessions-head">
          <h3>Sessions</h3>
          <button type="button" class="builder-new-session-btn" data-builder-action="new-session">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
            New
          </button>
        </div>
        <div class="builder-sessions-list">
          ${renderSessionList()}
        </div>
      </aside>

      <!-- Center: chat -->
      <main class="builder-chat-main">
        <div class="builder-chat-header">
          <div class="builder-chat-title">
            ${session ? `Session #${session.id}` : "Agent-Builder"}
            ${session?.target_id ? `<span class="builder-chat-subtitle">Reviewing: ${escapeHtml(_lookupAgentLabel(session.target_id))}</span>` : ""}
          </div>
          ${session && session.status === "active" ? `
            <button type="button" class="builder-abandon-btn ops-button ops-button-danger-ghost"
              data-builder-action="abandon-session" data-session-id="${session.id}">
              Abandon
            </button>
          ` : ""}
        </div>

        <div class="builder-chat-messages" id="builder-chat-messages">
          ${session ? renderConversation(conversation) : `
            <div class="builder-chat-empty">
              <div class="builder-chat-empty-icon">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                </svg>
              </div>
              <p>Select a session or start a new one.</p>
            </div>
          `}
        </div>

        ${session && session.status === "active" ? `
          <div class="builder-composer">
            <textarea
              id="builder-composer-input"
              class="builder-composer-input"
              placeholder="Describe the agent you want to build..."
              rows="3"
            ></textarea>
            <button
              type="button"
              id="builder-composer-send"
              class="builder-composer-send ops-button ops-button-primary"
              data-builder-action="send-message"
              data-session-id="${session.id}"
              ${_sending ? "disabled" : ""}
            >
              ${_sending ? "Thinking..." : "Send"}
            </button>
          </div>
        ` : ""}
      </main>

      <!-- Right rail: draft preview + pending proposals -->
      <aside class="builder-draft-rail">
        <div class="builder-draft-head">
          <h3>Draft Definition</h3>
        </div>
        <div class="builder-draft-body">
          ${renderDraftPanel(draft)}
          ${renderPendingProposals(_pendingProposals)}
        </div>
      </aside>
    </div>
  `;
}

// ── Action handling ────────────────────────────────────────────────────────────

export function handleBuilderAction(action, button) {
  switch (action) {
    case "new-session":
      void _handleNewSession();
      break;

    case "select-session": {
      const id = Number(button.dataset.sessionId);
      void _handleSelectSession(id);
      break;
    }

    case "send-message": {
      const id = Number(button.dataset.sessionId);
      void _handleSendMessage(id);
      break;
    }

    case "abandon-session": {
      const id = Number(button.dataset.sessionId);
      void _handleAbandonSession(id);
      break;
    }

    case "approve-proposal": {
      const id = Number(button.dataset.proposalId);
      void _handleApproveProposal(id);
      break;
    }

    case "reject-proposal": {
      const id = Number(button.dataset.proposalId);
      void _handleRejectProposal(id);
      break;
    }
  }
}

async function _handleNewSession() {
  try {
    const sess = await createSession();
    _selectedSessionId = sess.id;
    _currentSession = sess;
    // Persist so refresh keeps this session selected (Bug 1).
    try { localStorage.setItem(BUILDER_SELECTED_SESSION_KEY, String(sess.id)); } catch {}
    _rerenderPage();
    _scrollToBottom();
  } catch (err) {
    console.error("builder: failed to create session", err);
    _showError("Failed to create session: " + (err?.message || String(err)));
  }
}

async function _handleSelectSession(sessionId) {
  if (_selectedSessionId === sessionId) return;
  try {
    _selectedSessionId = sessionId;
    // Persist selection so refresh restores the same session (Bug 1 + Bug 3).
    try { localStorage.setItem(BUILDER_SELECTED_SESSION_KEY, String(sessionId)); } catch {}
    // Always hydrate from backend — conversation lives in response.conversation (Bug 3).
    const sess = await loadSession(sessionId);
    _currentSession = sess;
    await fetchPendingProposals(sessionId);
    _rerenderPage();
    _scrollToBottom();
  } catch (err) {
    console.error("builder: failed to load session", err);
    _showError("Failed to load session: " + (err?.message || String(err)));
  }
}

async function _handleSendMessage(sessionId) {
  if (_sending) return;
  const input = document.getElementById("builder-composer-input");
  const content = input?.value?.trim();
  if (!content) return;

  _sending = true;
  if (input) input.value = "";

  const msgArea = document.getElementById("builder-chat-messages");
  if (msgArea) {
    msgArea.insertAdjacentHTML("beforeend", renderMessage({ role: "user", content }));
    msgArea.insertAdjacentHTML("beforeend", `
      <div class="builder-thinking" id="builder-thinking">
        <span class="builder-thinking-dot"></span>
        <span class="builder-thinking-dot"></span>
        <span class="builder-thinking-dot"></span>
      </div>
    `);
    _scrollToBottom();
  }

  const sendBtn = document.getElementById("builder-composer-send");
  if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = "Thinking..."; }

  // ── Streaming SSE via fetch + ReadableStream ───────────────────────────────
  let assistantBubble = null;
  let assistantText = "";

  function _ensureBubble() {
    if (assistantBubble) return;
    document.getElementById("builder-thinking")?.remove();
    if (msgArea) {
      msgArea.insertAdjacentHTML("beforeend",
        `<article class="builder-msg builder-msg-assistant" id="builder-stream-bubble">
           <div class="builder-msg-role">Agent-Builder</div>
           <div class="builder-msg-body" id="builder-stream-body"></div>
         </article>`);
      assistantBubble = document.getElementById("builder-stream-bubble");
    }
  }

  function _appendToken(delta) {
    _ensureBubble();
    assistantText += delta;
    const body = document.getElementById("builder-stream-body");
    if (body) body.textContent = assistantText;
    _scrollToBottom();
  }

  function _addToolBreadcrumb(ev) {
    _ensureBubble();
    if (msgArea) {
      const id = ev.tool_call_id;
      msgArea.insertAdjacentHTML("beforeend",
        `<details class="builder-tool-crumb" id="tool-${escapeHtml(id)}">
           <summary>→ ${escapeHtml(ev.tool_name)}</summary>
           <pre class="builder-tool-inputs">${escapeHtml(JSON.stringify(ev.inputs, null, 2))}</pre>
         </details>`);
      _scrollToBottom();
    }
  }

  function _updateToolResult(ev) {
    const el = document.getElementById(`tool-${ev.tool_call_id}`);
    if (el) {
      const sum = el.querySelector("summary");
      if (sum) sum.textContent = `${ev.ok ? "✓" : "✗"} ${ev.tool_name} (${ev.duration_ms}ms)`;
    }
  }

  function _updateDraftPanel(definition) {
    const rail = document.querySelector(".builder-draft-body");
    if (rail && definition) {
      const node = rail.querySelector(".builder-draft-fields, .builder-draft-empty");
      if (node) node.outerHTML = renderDraftPanel(definition);
    }
  }

  // ~15-LOC SSE line parser
  async function _parseSSEStream(reader) {
    const decoder = new TextDecoder();
    let buf = "";
    let eventType = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("event:")) { eventType = line.slice(6).trim(); }
        else if (line.startsWith("data:")) {
          const payload = JSON.parse(line.slice(5).trim());
          if (eventType === "assistant_token") { _appendToken(payload.delta); }
          else if (eventType === "tool_call") { _addToolBreadcrumb(payload); }
          else if (eventType === "tool_result") { _updateToolResult(payload); }
          else if (eventType === "proposal_staged") { _updateDraftPanel(payload.definition_diff); }
          else if (eventType === "turn_complete") {
            _currentSession = await loadSession(sessionId);
            await fetchPendingProposals(sessionId);
            const idx = _sessions.findIndex((s) => s.id === sessionId);
            if (idx !== -1) _sessions[idx] = { ..._sessions[idx], ..._currentSession };
            _rerenderPage(); _scrollToBottom();
            return;
          }
          else if (eventType === "error") {
            _showError("Builder error: " + (payload.message || "unknown"));
          }
          eventType = "";
        }
      }
    }
  }

  try {
    const token = document.querySelector("meta[name='artemis-token']")?.content || "";
    const resp = await fetch(`/api/builder/sessions/${sessionId}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Artemis-Token": token },
      body: JSON.stringify({ content }),
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
    await _parseSSEStream(resp.body.getReader());
  } catch (err) {
    console.error("builder: stream failed", err);
    document.getElementById("builder-thinking")?.remove();
    _showError("Failed to send: " + (err?.message || String(err)));
  } finally {
    _sending = false;
    const sb = document.getElementById("builder-composer-send");
    if (sb) { sb.disabled = false; sb.textContent = "Send"; }
  }
}

async function _handleAbandonSession(sessionId) {
  if (!window.confirm?.("Abandon this builder session? It will be marked as abandoned and can no longer receive messages.")) return;
  try {
    await abandonSession(sessionId);
    // Clear persisted selection so refresh doesn't try to restore an abandoned session.
    try {
      const stored = Number(localStorage.getItem(BUILDER_SELECTED_SESSION_KEY));
      if (stored === sessionId) localStorage.removeItem(BUILDER_SELECTED_SESSION_KEY);
    } catch {}
    _rerenderPage();
  } catch (err) {
    console.error("builder: abandon failed", err);
    _showError("Failed to abandon session: " + (err?.message || String(err)));
  }
}

async function _handleApproveProposal(proposalId) {
  try {
    await approveProposal(proposalId);
    if (_selectedSessionId) {
      _currentSession = await loadSession(_selectedSessionId);
      await fetchPendingProposals(_selectedSessionId);
    }
    _rerenderPage();
  } catch (err) {
    _showError("Failed to approve proposal: " + (err?.message || String(err)));
  }
}

async function _handleRejectProposal(proposalId) {
  try {
    await rejectProposal(proposalId);
    if (_selectedSessionId) {
      _currentSession = await loadSession(_selectedSessionId);
      await fetchPendingProposals(_selectedSessionId);
    }
    _rerenderPage();
  } catch (err) {
    _showError("Failed to reject proposal: " + (err?.message || String(err)));
  }
}

// ── Internal utils ─────────────────────────────────────────────────────────────

function _rerenderPage() {
  // The operations shell calls renderOperationsView("agents/builder") which calls
  // renderAgentBuilderPage(). We trigger that re-render via a custom event.
  document.dispatchEvent(new CustomEvent("builder:rerender"));
}

function _scrollToBottom() {
  const el = document.getElementById("builder-chat-messages");
  if (el) el.scrollTop = el.scrollHeight;
}

function _showError(msg) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = "toast error";
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}

// ── Bootstrap ──────────────────────────────────────────────────────────────────

export async function initBuilderSurface() {
  try {
    // Always re-fetch from backend — never trust a stale localStorage cache (Bug 2).
    // fetchSessions() already filters abandoned sessions.
    await fetchSessions();

    // CC18: if the user clicked "Edit with Builder" from an agent profile,
    // operations-shell sets builderEditAgentDbId in state. Spawn a fresh
    // target-scoped session so the Builder LLM reads recent runs first.
    const pendingTargetId = getState("builderEditAgentDbId");
    if (pendingTargetId) {
      setState("builderEditAgentDbId", null);
      const sess = await createSession({ target_id: pendingTargetId });
      _selectedSessionId = sess.id;
      _currentSession = await loadSession(sess.id);
      try { localStorage.setItem(BUILDER_SELECTED_SESSION_KEY, String(sess.id)); } catch {}
      return;
    }

    // Restore the previously-selected session if it still exists and is active.
    // Falls back to the most-recent active session so first-time users still
    // get a sensible default (mirrors dev_projects.js restoreActivePointers).
    let storedId = null;
    try { storedId = Number(localStorage.getItem(BUILDER_SELECTED_SESSION_KEY)) || null; } catch {}

    const toRestore = storedId
      ? _sessions.find((s) => s.id === storedId)
      : null;
    const target = toRestore || _sessions.find((s) => s.status === "active") || null;

    if (target) {
      _selectedSessionId = target.id;
      // Hydrate full conversation from backend (Bug 3).
      _currentSession = await loadSession(target.id);
      try { localStorage.setItem(BUILDER_SELECTED_SESSION_KEY, String(target.id)); } catch {}
    }
  } catch (err) {
    console.error("builder: init failed", err);
  }
}
