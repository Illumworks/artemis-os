import { escapeHtml } from "../core/utils.js";

export function relativeTime(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "";
  const seconds = Math.max(1, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 14) return `${days}d`;
  const weeks = Math.floor(days / 7);
  if (weeks < 9) return `${weeks}w`;
  const months = Math.floor(days / 30);
  if (months < 18) return `${months}mo`;
  return `${Math.floor(days / 365)}y`;
}

export function renderSessionRow(session, activeSessionId) {
  const title = session.title || "Untitled";
  const active = Number(session.id) === Number(activeSessionId);
  const archived = Boolean(session.archived_at);
  const pinned = Boolean(session.pinned);
  const time = relativeTime(session.last_active_at || session.started_at);
  return `
    <button class="dev-session-row${active ? " active" : ""}${archived ? " archived" : ""}" data-session-id="${session.id}">
      <span class="dev-session-pin" aria-hidden="true">${pinned ? "⌖" : ""}</span>
      <span class="dev-session-main">
        <span class="dev-session-title">${escapeHtml(title)}</span>
      </span>
      <span class="dev-session-time">${escapeHtml(time)}</span>
      <span class="dev-session-actions" aria-hidden="true">
        <span class="dev-row-action" data-session-menu="${session.id}">⋯</span>
      </span>
    </button>
  `;
}
