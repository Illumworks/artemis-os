// Background sessions — guardSwitch, toast notifications, header indicator, localStorage persistence
import { $ } from '../core/dom.js';
import { getState } from '../core/store.js';
import { on } from '../core/events.js';
import { panes } from '../ui/parallel.js';
import { removeThinking } from '../ui/messages.js';
import { sendNotification } from '../ui/notifications.js';

const BG_STORAGE_KEY = "artemis-bg-sessions";

function createBellNotification(type, title, body, sourceSessionId) {
  if (typeof fetch !== 'function') return;
  fetch('/api/notifications/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, title, body, sourceSessionId }),
  }).catch(() => {});
}

function readStorage(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Ignore unavailable or blocked storage during startup.
  }
}

// ── localStorage helpers ──

function persistBgSessions() {
  const map = getBackgroundSessions();
  const obj = {};
  for (const [k, v] of map.entries()) {
    obj[k] = v;
  }
  try {
    writeStorage(BG_STORAGE_KEY, JSON.stringify(obj));
  } catch { /* quota exceeded — ignore */ }
}

function restoreBgSessions() {
  try {
    const raw = readStorage(BG_STORAGE_KEY);
    if (!raw) return;
    const obj = JSON.parse(raw);
    const map = getBackgroundSessions();
    for (const [k, v] of Object.entries(obj)) {
      map.set(k, v);
    }
    updateHeaderIndicator();
  } catch { /* corrupted data — ignore */ }
}

// ── Helpers ──

function getBackgroundSessions() {
  return getState("backgroundSessions");
}

function isAnyPaneStreaming() {
  for (const pane of panes.values()) {
    if (pane.isStreaming) return true;
  }
  return false;
}

function updateHeaderIndicator() {
  const map = getBackgroundSessions();
  const count = map.size;
  if (!$.bgSessionIndicator || !$.bgSessionBadge) return;
  if (count > 0) {
    $.bgSessionIndicator.classList.remove("hidden");
    $.bgSessionBadge.textContent = count;
  } else {
    $.bgSessionIndicator.classList.add("hidden");
  }
}

// ── Public API ──

export function addBackgroundSession(sessionId, title, projectName, projectPath) {
  const map = getBackgroundSessions();
  map.set(sessionId, { title, projectName, projectPath, startedAt: Date.now() });
  updateHeaderIndicator();
  persistBgSessions();
}

export function removeBackgroundSession(sessionId) {
  const map = getBackgroundSessions();
  map.delete(sessionId);
  updateHeaderIndicator();
  persistBgSessions();
}

export function isBackgroundSession(sessionId) {
  return getBackgroundSessions().has(sessionId);
}

/**
 * Reconcile background sessions against the server's active query list.
 * Sessions NOT in activeSessionIds → show completion toast, remove from map.
 * Sessions still active → keep in map.
 */
export function reconcileBackgroundSessions(activeSessionIds) {
  const activeSet = new Set(activeSessionIds);
  const map = getBackgroundSessions();
  for (const [sessionId, info] of [...map.entries()]) {
    if (!activeSet.has(sessionId)) {
      showCompletionToast(sessionId, info.title || "Background session", info.projectPath || "");
      map.delete(sessionId);
    }
  }
  updateHeaderIndicator();
  persistBgSessions();
}

export function showErrorToast(sessionId, title, error) {
  sendNotification('Session Error', `${title}: ${error}`, `error-${sessionId}`);
  createBellNotification('error', `Session "${title}" failed`, error?.slice(0, 200), sessionId);

  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = "bg-toast bg-toast-error";
  toast.innerHTML = `
    <span class="bg-toast-dot error"></span>
    <div class="bg-toast-body">
      <div class="bg-toast-label" style="color:var(--error)">Session error</div>
      <div class="bg-toast-title">${escapeForHtml(title)}</div>
      <div class="bg-toast-error-msg">${escapeForHtml(error.slice(0, 120))}</div>
    </div>
    <button class="bg-toast-close" title="Dismiss">&times;</button>
  `;

  toast.querySelector(".bg-toast-close").addEventListener("click", () => {
    dismissToast(toast);
  });

  container.appendChild(toast);
}

export function showCompletionToast(sessionId, title, projectPath) {
  sendNotification('Session Completed', title, `done-${sessionId}`);
  createBellNotification('session', `Session "${title}" completed`, null, sessionId);

  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = "bg-toast";
  toast.innerHTML = `
    <span class="bg-toast-dot"></span>
    <div class="bg-toast-body">
      <div class="bg-toast-label">Session completed</div>
      <div class="bg-toast-title">${escapeForHtml(title)}</div>
    </div>
    <button class="bg-toast-switch" title="Switch to session">&#8594;</button>
    <button class="bg-toast-close" title="Dismiss">&times;</button>
  `;

  toast.querySelector(".bg-toast-switch").addEventListener("click", () => {
    dismissToast(toast);
    switchToSession(sessionId, projectPath);
  });

  toast.querySelector(".bg-toast-close").addEventListener("click", () => {
    dismissToast(toast);
  });

  container.appendChild(toast);
}

export function showInputNeededToast(sessionId, title, projectPath) {
  sendNotification('Input Needed', `${title} is waiting for your response`, `input-${sessionId}`);
  createBellNotification('approval', `"${title}" needs your input`, 'Waiting for your response', sessionId);

  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = "bg-toast bg-toast-input";
  toast.innerHTML = `
    <span class="bg-toast-dot input-needed"></span>
    <div class="bg-toast-body">
      <div class="bg-toast-label">Waiting for your input</div>
      <div class="bg-toast-title">${escapeForHtml(title)}</div>
    </div>
    <button class="bg-toast-switch" title="Switch to session">&#8594;</button>
    <button class="bg-toast-close" title="Dismiss">&times;</button>
  `;

  toast.querySelector(".bg-toast-switch").addEventListener("click", () => {
    dismissToast(toast);
    switchToSession(sessionId, projectPath);
  });

  toast.querySelector(".bg-toast-close").addEventListener("click", () => {
    dismissToast(toast);
  });

  container.appendChild(toast);
}

function escapeForHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function dismissToast(toast) {
  toast.classList.add("toast-exit");
  toast.addEventListener("animationend", () => toast.remove());
}

async function switchToSession(sessionId, projectPath) {
  const { switchToSession: selectSession } = await import('./sessions.js');
  await selectSession(sessionId, { guard: true });
}

// ── Guard Switch ──

// guardSwitch is default-on background: switching away from a streaming session
// automatically continues it in the background without prompting. The confirm
// modal infrastructure is kept for per-session opt-out surfaces if added later.

export async function guardSwitch(onProceed) {
  if (!isAnyPaneStreaming()) {
    onProceed();
    return;
  }

  // Auto-background the current session — no modal, no opt-in required.
  const sessionId = getState("sessionId");
  if (sessionId) {
    const prevPath = readStorage("artemis-cwd") || "";
    const prevOpt = [...($.projectSelect?.options || [])].find(o => o.value === prevPath);
    const titleEl = $.sessionList?.querySelector(".rdf-session.active .session-title, li.active .session-title");
    const title = titleEl?.textContent || prevOpt?.textContent || "Session";
    addBackgroundSession(sessionId, title, prevOpt?.textContent || "", prevPath);
  }

  // Reset streaming UI so the incoming session renders cleanly. The server-side
  // stream continues writing to DB; the client simply stops rendering it here.
  try {
    const { finishStreamingHandler } = await import('./chat.js');
    const { getPane } = await import('../ui/parallel.js');
    if (getState("parallelMode")) {
      for (const pane of panes.values()) {
        if (pane.isStreaming) finishStreamingHandler(pane);
      }
    } else {
      const pane = getPane(null);
      if (pane?.isStreaming) finishStreamingHandler(pane);
    }
  } catch {
    if ($.sendBtn) $.sendBtn.classList.remove("hidden");
    if ($.stopBtn) $.stopBtn.classList.add("hidden");
    if ($.streamingTokens) $.streamingTokens.classList.add("hidden");
    if ($.streamingTokensSep) $.streamingTokensSep.classList.add("hidden");
    for (const pane of panes.values()) {
      if (pane.isStreaming) {
        pane.isStreaming = false;
        pane.currentAssistantMsg = null;
        removeThinking(pane);
      }
    }
  }

  onProceed();
}


// ── WS disconnect handler ──
// Non-destructive: bg sessions are persisted in localStorage and will be
// reconciled on reconnect instead of being cleared.

on("ws:disconnected", () => {
  // No-op: bg sessions survive disconnects via localStorage persistence.
  // reconcileBackgroundSessions() is called on ws:reconnected.
});

// ── Hover popup on background indicator ──

function initBgHoverPopup() {
  const indicator = $.bgSessionIndicator;
  if (!indicator) return;

  let popup = null;

  function formatElapsed(startedAt) {
    if (!startedAt) return '';
    const sec = Math.floor((Date.now() - startedAt) / 1000);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ${sec % 60}s`;
    return `${Math.floor(min / 60)}h ${min % 60}m`;
  }

  function show() {
    const map = getBackgroundSessions();
    if (map.size === 0) return;

    if (popup) popup.remove();
    popup = document.createElement('div');
    popup.className = 'bg-popup';

    let html = '<div class="bg-popup-header">Background Sessions</div>';
    for (const [sid, info] of map.entries()) {
      const shortId = sid.slice(0, 8);
      const elapsed = formatElapsed(info.startedAt);
      html += `
        <div class="bg-popup-row" data-sid="${sid}">
          <span class="bg-popup-dot"></span>
          <div class="bg-popup-info">
            <div class="bg-popup-title">${escapeForHtml(info.title || 'Untitled')}</div>
            <div class="bg-popup-meta">
              <span class="bg-popup-id">${shortId}</span>
              ${info.projectName ? `<span class="bg-popup-sep">&middot;</span><span>${escapeForHtml(info.projectName)}</span>` : ''}
              ${elapsed ? `<span class="bg-popup-sep">&middot;</span><span>${elapsed}</span>` : ''}
            </div>
          </div>
          <button class="bg-popup-switch" title="Switch to session">&rarr;</button>
        </div>
      `;
    }
    popup.innerHTML = html;

    // Switch buttons
    popup.querySelectorAll('.bg-popup-switch').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const sid = btn.closest('.bg-popup-row').dataset.sid;
        const info = map.get(sid);
        hide();
        if (info) switchToSession(sid, info.projectPath || '');
      });
    });

    // Rows clickable too
    popup.querySelectorAll('.bg-popup-row').forEach(row => {
      row.addEventListener('click', () => {
        const sid = row.dataset.sid;
        const info = map.get(sid);
        hide();
        if (info) switchToSession(sid, info.projectPath || '');
      });
    });

    // Hide when mouse leaves the popup
    popup.addEventListener('mouseleave', () => {
      setTimeout(() => {
        if (popup && !popup.matches(':hover') && !indicator.matches(':hover')) {
          hide();
        }
      }, 100);
    });

    document.body.appendChild(popup);

    // Position below indicator
    const rect = indicator.getBoundingClientRect();
    popup.style.top = (rect.bottom + 4) + 'px';
    popup.style.left = Math.max(8, rect.left + rect.width / 2 - popup.offsetWidth / 2) + 'px';
  }

  function hide() {
    if (popup) { popup.remove(); popup = null; }
  }

  indicator.addEventListener('mouseenter', show);
  indicator.addEventListener('mouseleave', (e) => {
    // Don't hide if moving into the popup
    setTimeout(() => {
      if (popup && !popup.matches(':hover') && !indicator.matches(':hover')) {
        hide();
      }
    }, 100);
  });

  // Also hide when clicking outside
  document.addEventListener('click', (e) => {
    if (popup && !popup.contains(e.target) && !indicator.contains(e.target)) {
      hide();
    }
  });
}

// ── Init ──
try {
  restoreBgSessions();
  initBgHoverPopup();
} catch (err) {
  console.warn('[background-sessions] init skipped:', err.message);
}
