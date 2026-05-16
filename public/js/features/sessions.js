// Session management
import { $ } from '../core/dom.js';
import { getState, setState, on as onState } from '../core/store.js';
import { CHAT_IDS } from '../core/constants.js';
import { escapeHtml } from '../core/utils.js';
import { on } from '../core/events.js';
import * as api from '../core/api.js';
import { panes, enterParallelMode, exitParallelMode } from '../ui/parallel.js';
import { renderMessagesIntoPane, prependOlderMessages, showChatEmptyState, showLoadingIndicator, hideLoadingIndicator } from '../ui/messages.js';
import { loadContextGauge } from '../ui/context-gauge.js';
import { subscribeToSession } from '../core/ws.js';
import { PROVIDER_LABELS } from '../ui/model-selector.js';

const MESSAGE_PAGE_SIZE = 30;
const SCROLL_LOAD_THRESHOLD = 150; // px from top to trigger load more

const SESSION_STORAGE_KEY = "artemis-session-id";
const COMPOSER_CONTEXT_NOTE_STORAGE_KEY = "artemis-composer-context-note";
const OPERATIONAL_SESSION_TITLE_PREFIXES = ["Agent:", "Workflow:", "Orchestrator:"];

function isOperationalSession(session) {
  if (!session || typeof session !== "object") return false;
  if (session.agent_id || session.run_id || session.run_type) return true;

  const title = typeof session.title === "string" ? session.title.trim() : "";
  return OPERATIONAL_SESSION_TITLE_PREFIXES.some((prefix) => title.startsWith(prefix));
}

function filterDevRailSessions(sessions) {
  if (!Array.isArray(sessions)) return [];
  return sessions.filter((session) => !isOperationalSession(session));
}

function formatSessionTime(timestampSeconds) {
  if (!timestampSeconds) return { relative: "", exact: "" };

  const date = new Date(timestampSeconds * 1000);
  const exact = date.toLocaleString();
  const diffMs = Date.now() - date.getTime();
  const diffMinutes = Math.max(0, Math.round(diffMs / 60000));

  if (diffMinutes < 1) return { relative: "just now", exact };
  if (diffMinutes < 60) return { relative: `${diffMinutes}m ago`, exact };

  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return { relative: `${diffHours}h ago`, exact };

  const diffDays = Math.round(diffHours / 24);
  if (diffDays < 7) return { relative: `${diffDays}d ago`, exact };

  return {
    relative: date.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    exact,
  };
}

// Persist sessionId to localStorage whenever it changes
onState("sessionId", (val) => {
  if (val) {
    localStorage.setItem(SESSION_STORAGE_KEY, val);
    subscribeToSession(val);
  } else {
    localStorage.removeItem(SESSION_STORAGE_KEY);
  }
});

// Restore sessionId from localStorage on module load
(function restoreSessionId() {
  const saved = localStorage.getItem(SESSION_STORAGE_KEY);
  if (saved && !getState("sessionId")) {
    setState("sessionId", saved);
  }
})();

export async function loadSessions(searchTerm) {
  try {
    const cwd = $.projectSelect?.value;
    if (!cwd) {
      renderSessions([]);
      const { updateHeaderProjectName } = await import("./projects.js");
      updateHeaderProjectName(0);
      return;
    }
    let sessions;
    if (searchTerm) {
      sessions = await api.searchSessions(searchTerm, cwd);
    } else {
      sessions = await api.fetchSessions(cwd);
    }
    const visibleSessions = filterDevRailSessions(sessions);
    renderSessions(visibleSessions);
    const { updateHeaderProjectName } = await import("./projects.js");
    const count = visibleSessions.length;
    // Pass most-recent session's last_used_at for "Last active X" rail subtitle
    const lastUsedAt = visibleSessions.length > 0
      ? (visibleSessions[0].last_used_at || visibleSessions[0].created_at || null)
      : null;
    updateHeaderProjectName(count, lastUsedAt);
  } catch (err) {
    console.error("Failed to load sessions:", err);
  }
}

async function syncProjectSelection(projectPath) {
  if (!$.projectSelect || !projectPath || $.projectSelect.value === projectPath) return;
  $.projectSelect.value = projectPath;
  localStorage.setItem("artemis-cwd", projectPath);
  const { updateSystemPromptIndicator, updateHeaderProjectName, loadProjectCommands } = await import('./projects.js');
  updateSystemPromptIndicator();
  updateHeaderProjectName();
  loadProjectCommands();
}

function clearSessionMessages() {
  if (getState("parallelMode")) {
    for (const chatId of CHAT_IDS) {
      const pane = panes.get(chatId);
      if (pane) pane.messagesDiv.innerHTML = "";
    }
    return;
  }
  if ($.messagesDiv) $.messagesDiv.innerHTML = "";
}

async function applySessionSelection(session) {
  if (!session?.id) return;

  setState("view", "chat");
  await syncProjectSelection(session.project_path);
  setState("sessionId", session.id);

  const parallelMode = getState("parallelMode");
  const needsParallel = session.mode === "parallel" || session.mode === "both";
  if (needsParallel && !parallelMode) {
    enterParallelMode();
  } else if (!needsParallel && parallelMode) {
    exitParallelMode();
  }

  clearSessionMessages();
  await loadMessages(session.id);
  $.messageInput?.focus();
  applyPendingComposerContextNote();
  loadSessions($.sessionSearchInput?.value?.trim() || undefined);
}

function applyPendingComposerContextNote() {
  const note = localStorage.getItem(COMPOSER_CONTEXT_NOTE_STORAGE_KEY);
  if (!note) return;

  const composerContextNote = document.getElementById("composer-context-note");
  if (composerContextNote) {
    composerContextNote.textContent = note;
  }
  localStorage.removeItem(COMPOSER_CONTEXT_NOTE_STORAGE_KEY);
}

async function resolveSessionForSwitch(sessionOrId) {
  if (typeof sessionOrId !== "string") return sessionOrId;

  try {
    return await api.fetchSession(sessionOrId);
  } catch (primaryError) {
    const projectPath = $.projectSelect?.value || undefined;
    const projectSessions = projectPath ? await api.fetchSessions(projectPath) : [];
    const fallbackSession = projectSessions.find((session) => session?.id === sessionOrId);
    if (fallbackSession) return fallbackSession;

    if (projectPath) {
      const allSessions = await api.fetchSessions();
      const crossProjectSession = allSessions.find((session) => session?.id === sessionOrId);
      if (crossProjectSession) return crossProjectSession;
    }

    throw primaryError;
  }
}

export async function switchToSession(sessionOrId, { guard = true } = {}) {
  const session = await resolveSessionForSwitch(sessionOrId);
  if (!session?.id) return;

  const proceed = () => applySessionSelection(session);
  if (!guard) {
    await proceed();
    return;
  }

  const { guardSwitch } = await import('./background-sessions.js');
  await guardSwitch(proceed);
}

function renderSessions(sessions, append = false) {
  const sessionId = getState("sessionId");
  if (!append && $.sessionList) $.sessionList.innerHTML = "";

  if (!append) {
    // Empty state: no project selected
    const cwd = $.projectSelect?.value;
    if (!cwd) {
      $.sessionList.innerHTML = `
        <div class="session-empty">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
          </svg>
          <span>Select a project to view sessions</span>
        </div>`;
      return;
    }

    // Empty state: no sessions found
    if (sessions.length === 0) {
      const isSearch = $.sessionSearchInput.value.trim();
      $.sessionList.innerHTML = `
        <div class="session-empty">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            ${isSearch
              ? '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'
              : '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'}
          </svg>
          <span>${isSearch ? 'No matching sessions' : 'No sessions yet'}</span>
          ${!isSearch ? '<span class="session-empty-hint">Start a new conversation to create one</span>' : ''}
        </div>`;
      return;
    }
  }

  for (const s of sessions) {
    // Emit .rdf-session row (artemis-os design) instead of legacy <li>
    const li = document.createElement("div");
    li.className = `rdf-session${s.id === sessionId ? " active" : ""}`;
    li.setAttribute("role", "button");
    li.setAttribute("tabindex", "0");
    const time = formatSessionTime(s.last_used_at);
    const dotClass = s.mode === "parallel" || s.mode === "both" ? "parallel" : "single";
    const modeBadge = s.mode === "parallel"
      ? '<span class="session-mode parallel">parallel</span>'
      : s.mode === "both"
      ? '<span class="session-mode both">single + parallel</span>'
      : '<span class="session-mode single">single</span>';
    const displayTitle = s.title || s.project_name || "Session";
    const isPinned = s.pinned === 1;
    const summaryTooltip = s.summary ? escapeHtml(s.summary) : "";
    const providerLabel = PROVIDER_LABELS[s.provider_id] || s.provider_id || "";
    const providerBadge = providerLabel
      ? `<span class="session-provider provider-${escapeHtml(s.provider_id || "unknown")}">${escapeHtml(providerLabel)}</span>`
      : "";
    const forkIndicator = s.parent_session_id
      ? `<span class="session-fork-badge" title="Forked session"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg></span>`
      : "";
    const metaLine = `${escapeHtml(time.relative || "New")}${providerLabel ? " · " + escapeHtml(providerLabel) : ""}`;
    li.innerHTML = `
      <span class="rdf-session-dot ${dotClass}" aria-hidden="true"></span>
      <div class="rdf-session-body">
        <div class="rdf-session-title" title="${escapeHtml(displayTitle)}">${forkIndicator}<span class="session-title">${escapeHtml(displayTitle)}</span></div>
        <div class="rdf-session-meta">${modeBadge}${providerBadge}<span class="session-preview-time" title="${escapeHtml(time.exact)}">${metaLine}</span></div>
      </div>
      <span class="session-card-actions">
        <button class="session-pin${isPinned ? " pinned" : ""}" title="${isPinned ? "Unpin" : "Pin"} session" type="button">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="${isPinned ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 17v5"/><path d="M9 2h6l-1 7h4l-5 8h-2l-5-8h4z"/>
          </svg>
        </button>
        <button class="session-delete" title="Delete session" type="button">&times;</button>
      </span>
    `;
    if (summaryTooltip) {
      li.setAttribute("data-summary", summaryTooltip);
    }
    li.querySelector(".session-pin").addEventListener("click", async (e) => {
      e.stopPropagation();
      await api.toggleSessionPin(s.id);
      loadSessions($.sessionSearchInput.value.trim() || undefined);
    });
    li.querySelector(".session-delete").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    });
    const titleSpan = li.querySelector(".session-title");
    titleSpan.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      startInlineEdit(titleSpan, s.id, displayTitle);
    });
    li.addEventListener("click", async (e) => {
      if (e.target.closest(".session-delete") || e.target.closest(".session-pin") || e.target.closest(".session-title-edit")) return;
      await switchToSession(s);
    });
    // Right-click context menu with dev info
    li.addEventListener("contextmenu", (e) => showSessionContextMenu(e, s));

    // Show blinking dot for background sessions
    const bgMap = getState("backgroundSessions");
    if (bgMap && bgMap.has(s.id)) {
      const dot = document.createElement("span");
      dot.className = "session-bg-indicator";
      li.querySelector(".session-title").after(dot);
    }

    $.sessionList.appendChild(li);
  }

  const activeItem = $.sessionList?.querySelector(".rdf-session.active, li.active");
  activeItem?.scrollIntoView?.({ block: "nearest" });
}

function startInlineEdit(titleSpan, sessionIdToEdit, currentTitle) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "session-title-edit";
  input.value = currentTitle;
  titleSpan.replaceWith(input);
  input.focus();
  input.select();

  async function commitEdit() {
    const newTitle = input.value.trim();
    if (newTitle && newTitle !== currentTitle) {
      await api.updateSessionTitle(sessionIdToEdit, newTitle);
    }
    loadSessions();
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commitEdit();
    } else if (e.key === "Escape") {
      loadSessions();
    }
  });
  input.addEventListener("blur", commitEdit);
}

export async function deleteSession(id) {
  try {
    await api.deleteSessionApi(id);
    if (id === getState("sessionId")) {
      setState("sessionId", null);
      if (getState("parallelMode")) {
        for (const chatId of CHAT_IDS) {
          const pane = panes.get(chatId);
          if (pane) {
            pane.messagesDiv.innerHTML = "";
            showChatEmptyState(pane);
          }
        }
      } else {
        $.messagesDiv.innerHTML = "";
        showChatEmptyState();
      }
    }
    await loadSessions();
  } catch (err) {
    console.error("Failed to delete session:", err);
  }
}

export async function loadMessages(sid, options = {}) {
  if (getState("parallelMode")) {
    // Load all panes concurrently instead of sequentially
    await Promise.all(CHAT_IDS.map(chatId => loadPaneMessages(sid, chatId, options)));
    return;
  }

  const pane = panes.get(null);
  try {
    const shouldLoadFull = options.full !== false;
    const messages = shouldLoadFull
      ? await api.fetchSingleMessages(sid)
      : await api.fetchSingleMessages(sid, { limit: MESSAGE_PAGE_SIZE });
    if (options.guard && !options.guard()) return;
    if (getState("sessionId") !== sid) return;
    if (pane.isStreaming) return;
    renderMessagesIntoPane(messages, pane);
    _initPanePagination(pane, messages, "single", { full: shouldLoadFull });
    loadContextGauge(sid);
  } catch (err) {
    console.error("Failed to load messages:", err);
  }
}

export async function loadPaneMessages(sid, chatId, options = {}) {
  const pane = panes.get(chatId);
  if (!pane) return;
  try {
    const shouldLoadFull = options.full !== false;
    let messages;
    // For Chat 1 (chat-0): also load single-mode messages as fallback
    if (chatId === CHAT_IDS[0]) {
      const [chatMsgs, singleMsgs] = await Promise.all([
        shouldLoadFull
          ? api.fetchMessagesByChatId(sid, chatId)
          : api.fetchMessagesByChatId(sid, chatId, { limit: MESSAGE_PAGE_SIZE }),
        shouldLoadFull
          ? api.fetchSingleMessages(sid)
          : api.fetchSingleMessages(sid, { limit: MESSAGE_PAGE_SIZE }),
      ]);
      if (singleMsgs.length > 0) {
        messages = [...singleMsgs, ...chatMsgs].sort((a, b) => a.id - b.id);
      } else {
        messages = chatMsgs;
      }
    } else {
      messages = shouldLoadFull
        ? await api.fetchMessagesByChatId(sid, chatId)
        : await api.fetchMessagesByChatId(sid, chatId, { limit: MESSAGE_PAGE_SIZE });
    }

    if (options.guard && !options.guard()) return;
    if (getState("sessionId") !== sid) return;
    if (pane.isStreaming) return;
    renderMessagesIntoPane(messages, pane);
    _initPanePagination(pane, messages, chatId === CHAT_IDS[0] ? "chat0" : "chat", { full: shouldLoadFull });
  } catch (err) {
    console.error(`Failed to load messages for ${chatId}:`, err);
  }
}

// ── Lazy-load pagination ────────────────────────────────

function _initPanePagination(pane, messages, mode, { full = false } = {}) {
  pane._hasMore = !full && messages.length >= MESSAGE_PAGE_SIZE;
  pane._oldestMessageId = messages.length > 0 ? messages[0].id : null;
  pane._loadingMore = false;
  pane._paginationMode = mode; // "single" | "chat" | "chat0"

  // Remove any existing scroll listener
  if (pane._scrollHandler) {
    pane.messagesDiv.removeEventListener("scroll", pane._scrollHandler);
  }

  if (pane._hasMore) {
    pane._scrollHandler = () => _onPaneScroll(pane);
    pane.messagesDiv.addEventListener("scroll", pane._scrollHandler, { passive: true });
  }
}

function _onPaneScroll(pane) {
  if (
    pane.messagesDiv.scrollTop < SCROLL_LOAD_THRESHOLD &&
    pane._hasMore &&
    !pane._loadingMore
  ) {
    _loadMoreMessages(pane);
  }
}

async function _loadMoreMessages(pane) {
  pane._loadingMore = true;
  showLoadingIndicator(pane);

  const sid = getState("sessionId");
  const before = pane._oldestMessageId;
  const opts = { limit: MESSAGE_PAGE_SIZE, before };

  try {
    let olderMessages;

    switch (pane._paginationMode) {
      case "single":
        olderMessages = await api.fetchSingleMessages(sid, opts);
        break;
      case "chat0": {
        // Chat 1 merges chatId + single messages
        const [chatMsgs, singleMsgs] = await Promise.all([
          api.fetchMessagesByChatId(sid, pane.chatId, opts),
          api.fetchSingleMessages(sid, opts),
        ]);
        olderMessages = singleMsgs.length > 0
          ? [...singleMsgs, ...chatMsgs].sort((a, b) => a.id - b.id)
          : chatMsgs;
        break;
      }
      default:
        olderMessages = await api.fetchMessagesByChatId(sid, pane.chatId, opts);
    }

    if (olderMessages.length === 0) {
      pane._hasMore = false;
    } else {
      pane._oldestMessageId = olderMessages[0].id;
      pane._hasMore = olderMessages.length >= MESSAGE_PAGE_SIZE;
      prependOlderMessages(olderMessages, pane);
    }
  } catch (err) {
    console.error("Failed to load more messages:", err);
  } finally {
    hideLoadingIndicator(pane);
    pane._loadingMore = false;

    // Detach scroll listener if no more messages
    if (!pane._hasMore && pane._scrollHandler) {
      pane.messagesDiv.removeEventListener("scroll", pane._scrollHandler);
      pane._scrollHandler = null;
    }
  }
}

// ── Session Context Menu ────────────────────────────────
let sessionCtxMenu = null;

function hideSessionContextMenu() {
  if (sessionCtxMenu) {
    sessionCtxMenu.remove();
    sessionCtxMenu = null;
  }
}

function showSessionContextMenu(e, session) {
  e.preventDefault();
  hideSessionContextMenu();

  const items = [
    { label: "Copy Session ID", value: session.id },
    { label: "Copy Claude Session ID", value: session.claude_session_id || "(none)" },
    { label: "Copy Project Path", value: session.project_path || "(none)" },
    { label: "Copy Title", value: session.title || session.project_name || "(none)" },
  ];

  sessionCtxMenu = document.createElement("div");
  sessionCtxMenu.className = "session-ctx-menu";

  for (const item of items) {
    const btn = document.createElement("button");
    btn.innerHTML = `<span class="ctx-label">${escapeHtml(item.label)}</span><span class="ctx-value">${escapeHtml(String(item.value).slice(0, 40))}</span>`;
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(item.value);
      btn.querySelector(".ctx-label").textContent = "Copied!";
      setTimeout(hideSessionContextMenu, 400);
    });
    sessionCtxMenu.appendChild(btn);
  }

  // Generate Summary action
  const summaryBtn = document.createElement("button");
  summaryBtn.innerHTML = `<span class="ctx-label">Generate Summary</span><span class="ctx-value">${session.summary ? "Regenerate" : "No summary yet"}</span>`;
  summaryBtn.addEventListener("click", async () => {
    summaryBtn.querySelector(".ctx-label").textContent = "Generating...";
    const result = await api.generateSummary(session.id);
    hideSessionContextMenu();
    if (result.summary) loadSessions($.sessionSearchInput.value.trim() || undefined);
  });
  sessionCtxMenu.appendChild(summaryBtn);

  // View Parent (only for forked sessions)
    if (session.parent_session_id) {
      const parentBtn = document.createElement("button");
      parentBtn.innerHTML = `<span class="ctx-label">View Parent Session</span><span class="ctx-value">Switch to parent</span>`;
      parentBtn.addEventListener("click", async () => {
        hideSessionContextMenu();
        await switchToSession(session.parent_session_id);
      });
      sessionCtxMenu.appendChild(parentBtn);
    }

  // View Forks
  const forksBtn = document.createElement("button");
  forksBtn.innerHTML = `<span class="ctx-label">View Forks</span><span class="ctx-value">Loading...</span>`;
  forksBtn.addEventListener("click", async () => {
    const branches = await api.fetchBranches(session.id);
    if (branches.length === 0) {
      forksBtn.querySelector(".ctx-value").textContent = "No forks";
      return;
    }
    hideSessionContextMenu();
    // Show back header + filtered fork list
    $.sessionList.innerHTML = "";
    const backHeader = document.createElement("div");
    backHeader.className = "session-forks-back";
    backHeader.innerHTML = `<button class="session-forks-back-btn">&larr; All Sessions</button><span class="session-forks-label">Forks of "${escapeHtml((session.title || session.project_name || "Session").slice(0, 30))}"</span>`;
    backHeader.querySelector(".session-forks-back-btn").addEventListener("click", () => {
      loadSessions($.sessionSearchInput.value.trim() || undefined);
    });
    $.sessionList.appendChild(backHeader);
    renderSessions(branches, true);
  });
  sessionCtxMenu.appendChild(forksBtn);
  // Eagerly load fork count
  api.fetchBranches(session.id).then(branches => {
    const val = forksBtn.querySelector(".ctx-value");
    if (val) val.textContent = branches.length > 0 ? `${branches.length} fork${branches.length > 1 ? "s" : ""}` : "No forks";
  });

  sessionCtxMenu.style.left = e.clientX + "px";
  sessionCtxMenu.style.top = e.clientY + "px";
  document.body.appendChild(sessionCtxMenu);

  // Keep within viewport
  const rect = sessionCtxMenu.getBoundingClientRect();
  if (rect.right > window.innerWidth) sessionCtxMenu.style.left = (e.clientX - rect.width) + "px";
  if (rect.bottom > window.innerHeight) sessionCtxMenu.style.top = (e.clientY - rect.height) + "px";
}

document.addEventListener("click", (e) => {
  if (sessionCtxMenu && !sessionCtxMenu.contains(e.target)) hideSessionContextMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideSessionContextMenu();
});

on("session:switch", (sessionId) => {
  if (!sessionId) return;
  switchToSession(sessionId).catch((err) => {
    console.error("Failed to switch sessions:", err);
  });
});

// Session search with debounce
let searchDebounceTimer = null;
$.sessionSearchInput?.addEventListener("input", () => {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    loadSessions($.sessionSearchInput.value.trim());
  }, 200);
});
