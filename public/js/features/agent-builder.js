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

// ── State ──────────────────────────────────────────────────────────────────────

let _sessions = [];
let _selectedSessionId = null;
let _currentSession = null; // { id, conversation, draft, status }
let _sending = false;

// ── API helpers ────────────────────────────────────────────────────────────────

async function fetchSessions() {
  const data = await api.builderFetchSessions();
  _sessions = data.sessions || [];
}

async function createSession() {
  const data = await api.builderCreateSession({ builder_kind: "agent" });
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
  }
}

// ── Render helpers ─────────────────────────────────────────────────────────────

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

      <!-- Right rail: draft preview -->
      <aside class="builder-draft-rail">
        <div class="builder-draft-head">
          <h3>Draft Definition</h3>
        </div>
        <div class="builder-draft-body">
          ${renderDraftPanel(draft)}
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
    const sess = await loadSession(sessionId);
    _currentSession = sess;
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

  // Optimistic append
  const msgArea = document.getElementById("builder-chat-messages");
  if (msgArea) {
    msgArea.insertAdjacentHTML("beforeend", renderMessage({ role: "user", content }));
    // Thinking indicator
    msgArea.insertAdjacentHTML("beforeend", `
      <div class="builder-thinking" id="builder-thinking">
        <span class="builder-thinking-dot"></span>
        <span class="builder-thinking-dot"></span>
        <span class="builder-thinking-dot"></span>
      </div>
    `);
    _scrollToBottom();
  }

  // Disable send button
  const sendBtn = document.getElementById("builder-composer-send");
  if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = "Thinking..."; }

  try {
    const result = await sendMessage(sessionId, content);
    // Refresh full session to get updated conversation + draft
    _currentSession = await loadSession(sessionId);
    // Update session list entry
    const idx = _sessions.findIndex((s) => s.id === sessionId);
    if (idx !== -1) _sessions[idx] = { ..._sessions[idx], ..._currentSession };
    _rerenderPage();
    _scrollToBottom();
  } catch (err) {
    console.error("builder: send message failed", err);
    // Remove thinking indicator and show error
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
    await fetchSessions();
    // Auto-select most recent active session
    const active = _sessions.find((s) => s.status === "active");
    if (active) {
      _selectedSessionId = active.id;
      _currentSession = await loadSession(active.id);
    }
  } catch (err) {
    console.error("builder: init failed", err);
  }
}
