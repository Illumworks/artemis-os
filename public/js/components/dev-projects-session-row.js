import { escapeHtml } from "../core/utils.js";

export function renderSessionRow(session, activeSessionId) {
  const title = session.title || "Untitled";
  const active = Number(session.id) === Number(activeSessionId);
  const archived = Boolean(session.archived_at);
  return `
    <button class="dev-session-row${active ? " active" : ""}${archived ? " archived" : ""}" data-session-id="${session.id}">
      <span class="dev-session-status" aria-hidden="true"></span>
      <span class="dev-session-main">
        <span class="dev-session-title">${escapeHtml(title)}</span>
        <span class="dev-session-meta">${escapeHtml(session.provider || "claude-code")} · ${session.message_count || 0} messages</span>
      </span>
    </button>
  `;
}

