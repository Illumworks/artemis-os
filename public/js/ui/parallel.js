// Parallel mode — 2x2 chat panes
//
// V1: each pane is an independent floating-artemis session.
// Backend: POST /api/parallel/sessions → { pane_ids: [...] }
// Each pane_id becomes a FA session; messages flow through
//   POST /api/floating-artemis/sessions/{id}/messages (HTTP)
//   WS   /ws/floating-artemis/{id}                   (streaming)
//
// The pane map keyed by chatId is kept for backward compat with
// chat.js's getPane() / panes.get() calls (single-pane mode).

import { $ } from '../core/dom.js';
import { getState, setState } from '../core/store.js';
import { CHAT_IDS } from '../core/constants.js';
import { handleAutocompleteKeydown, handleSlashAutocomplete } from './commands.js';
import { handleHistoryKeydown } from '../features/input-history.js';

// Panes map — chatId -> pane state object
export const panes = new Map();

export function getPane(chatId) {
  if (!getState("parallelMode")) return panes.get(null);
  return panes.get(chatId) || panes.get(null);
}

export function initSinglePane() {
  panes.clear();
  panes.set(null, {
    chatId: null,
    messagesDiv: $.messagesDiv,
    messageInput: $.messageInput,
    sendBtn: $.sendBtn,
    stopBtn: $.stopBtn,
    isStreaming: false,
    currentAssistantMsg: null,
    autocompleteEl: document.getElementById("slash-autocomplete"),
    _autocompleteIndex: -1,
  });
}

// Initialize on load
initSinglePane();

// ── FA-based pane send ─────────────────────────────────────────────────────
// When the floating-artemis surface is available, each pane uses its own FA
// session for messaging. Otherwise falls back to the legacy WS sendMessage.

async function _faSendPaneMessage(pane) {
  if (pane.isStreaming) return;
  const text = pane.messageInput?.value?.trim();
  if (!text) return;

  const faSessionId = pane.faSessionId;
  if (!faSessionId) {
    console.warn("[parallel] pane has no FA session — cannot send");
    return;
  }

  pane.messageInput.value = "";
  pane.messageInput.style.height = "auto";
  pane.isStreaming = true;
  _setPaneStatus(pane, "sending…");

  // Render the user turn immediately
  const userRow = document.createElement("div");
  userRow.className = "msg msg-user";
  userRow.textContent = text;
  pane.messagesDiv.appendChild(userRow);
  pane.messagesDiv.scrollTop = pane.messagesDiv.scrollHeight;

  if (pane.sendBtn) pane.sendBtn.classList.add("hidden");
  if (pane.stopBtn) pane.stopBtn.classList.remove("hidden");

  try {
    const { sendMessage: faSend } = await import('../core/floating-artemis-api.js');
    await faSend(faSessionId, text);
    // Streaming response arrives via the FA WebSocket (wired in enterParallelMode)
  } catch (err) {
    _setPaneStatus(pane, "error");
    const errRow = document.createElement("div");
    errRow.className = "msg msg-assistant";
    errRow.textContent = `[Error: ${err.message}]`;
    pane.messagesDiv.appendChild(errRow);
    pane.isStreaming = false;
    if (pane.sendBtn) pane.sendBtn.classList.remove("hidden");
    if (pane.stopBtn) pane.stopBtn.classList.add("hidden");
  }
}

function _setPaneStatus(pane, text) {
  if (pane.statusEl) {
    pane.statusEl.textContent = text;
    pane.statusEl.classList.toggle("streaming", text === "streaming");
  }
}

// ── FA WS event → pane rendering ─────────────────────────────────────────
function _handleFAEvent(pane, event) {
  if (event.type === "floating_artemis.token") {
    // Streaming token — append to current assistant bubble
    if (!pane.currentAssistantMsg) {
      const row = document.createElement("div");
      row.className = "msg msg-assistant";
      pane.messagesDiv.appendChild(row);
      pane.currentAssistantMsg = row;
      _setPaneStatus(pane, "streaming");
    }
    pane.currentAssistantMsg.textContent += event.token ?? "";
    pane.messagesDiv.scrollTop = pane.messagesDiv.scrollHeight;
  } else if (event.type === "floating_artemis.turn_complete") {
    pane.isStreaming = false;
    pane.currentAssistantMsg = null;
    _setPaneStatus(pane, "idle");
    if (pane.sendBtn) pane.sendBtn.classList.remove("hidden");
    if (pane.stopBtn) pane.stopBtn.classList.add("hidden");
  } else if (event.type === "floating_artemis.error") {
    pane.isStreaming = false;
    pane.currentAssistantMsg = null;
    _setPaneStatus(pane, "error");
    if (pane.sendBtn) pane.sendBtn.classList.remove("hidden");
    if (pane.stopBtn) pane.stopBtn.classList.add("hidden");
  }
}

// ── Pane DOM factory ──────────────────────────────────────────────────────
export function createChatPane(chatId, index, sendFnOverride) {
  // Lazy import to avoid circular dependency at module parse time
  const { sendMessage, stopGeneration } = _getLazyChatFns();
  // Use the FA-based sender when provided, otherwise fall back to WS
  const effectiveSend = sendFnOverride || ((pane) => sendMessage(pane));

  // Fresh DOM — new `pane-*` class namespace so we don't collide with
  // any legacy `.input-bar` / `.messages` / `.chat-pane-header`
  // cascade rules. Styles live in public/css/ui/parallel-panes.css.
  const container = document.createElement("div");
  container.className = "chat-pane";
  container.dataset.chatId = chatId;

  // Header
  const header = document.createElement("div");
  header.className = "pane-header";
  header.innerHTML = `
    <span class="pane-header-label">Chat ${index + 1}</span>
    <span class="pane-header-status">idle</span>
  `;
  container.appendChild(header);

  // Body (scrollable message list)
  const body = document.createElement("div");
  body.className = "pane-body";
  container.appendChild(body);

  // Composer (mirrors .dp-composer)
  const composerWrap = document.createElement("div");
  composerWrap.className = "pane-composer-wrap";

  const composer = document.createElement("div");
  composer.className = "pane-composer";

  const textarea = document.createElement("textarea");
  textarea.className = "pane-composer-input";
  textarea.placeholder = "Message…";
  textarea.rows = 1;
  composer.appendChild(textarea);

  const paneSendBtn = document.createElement("button");
  paneSendBtn.className = "pane-composer-send";
  paneSendBtn.title = "Send";
  paneSendBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M6 11l6-6 6 6"/></svg>`;
  composer.appendChild(paneSendBtn);

  const paneStopBtn = document.createElement("button");
  paneStopBtn.className = "pane-composer-stop hidden";
  paneStopBtn.title = "Stop";
  paneStopBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 20 20" fill="none"><rect x="4" y="4" width="12" height="12" rx="2" fill="currentColor"/></svg>`;
  composer.appendChild(paneStopBtn);

  const paneAutocomplete = document.createElement("div");
  // Keep `slash-autocomplete` in the class list for existing commands.js
  // logic that queries by that class, but layer `pane-composer-autocomplete`
  // for positioning from our stylesheet.
  paneAutocomplete.className = "pane-composer-autocomplete slash-autocomplete hidden";
  composer.appendChild(paneAutocomplete);

  composerWrap.appendChild(composer);
  container.appendChild(composerWrap);

  const state = {
    chatId,
    faSessionId: null,  // set by enterParallelMode after backend allocates
    messagesDiv: body,
    messageInput: textarea,
    sendBtn: paneSendBtn,
    stopBtn: paneStopBtn,
    isStreaming: false,
    currentAssistantMsg: null,
    statusEl: header.querySelector(".pane-header-status"),
    autocompleteEl: paneAutocomplete,
    _autocompleteIndex: -1,
  };

  paneSendBtn.addEventListener("click", () => effectiveSend(state));
  paneStopBtn.addEventListener("click", () => stopGeneration(state));

  textarea.addEventListener("keydown", (e) => {
    if (handleAutocompleteKeydown(e, state)) return;
    // Lazy import to avoid circular dependency — getInputHistory is set by chat.js
    const history = _getInputHistory();
    if (history && handleHistoryKeydown(e, state, history)) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      effectiveSend(state);
    }
  });

  textarea.addEventListener("input", () => {
    const history = _getInputHistory();
    if (history && history.isNavigating) history.reset();
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 80) + "px";
    handleSlashAutocomplete(state);
  });

  return { container, state };
}

// ── enterParallelMode ─────────────────────────────────────────────────────
export async function enterParallelMode(count = 4) {
  count = Math.max(2, Math.min(4, count)); // clamp to 2–4
  setState("parallelCount", count);

  // Determine the send function to use for panes.
  // When the floating-artemis surface is available, use FA sessions per pane.
  // Otherwise fall back to the legacy WS sendMessage path.
  let paneSendFn = null;
  const { isSurfaceAvailable } = await import('../core/status.js');
  const useFASessions = isSurfaceAvailable('floating-artemis');

  // Allocate pane session IDs from backend (if FA is available)
  let paneSessionIds = null;
  if (useFASessions) {
    try {
      const res = await fetch('/api/parallel/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count }),
      });
      if (res.ok) {
        const data = await res.json();
        paneSessionIds = data.pane_ids;
      } else {
        console.warn('[parallel] /api/parallel/sessions returned', res.status);
      }
    } catch (err) {
      console.warn('[parallel] failed to allocate pane sessions:', err.message);
    }
    if (paneSessionIds) {
      paneSendFn = _faSendPaneMessage;
    }
  }

  // Build the new grid first
  const grid = document.createElement("div");
  grid.className = "chat-grid";
  grid.id = "chat-grid";
  grid.dataset.parallelCount = String(count);

  panes.clear();

  const ids = CHAT_IDS.slice(0, count);
  for (let i = 0; i < ids.length; i++) {
    const { container, state } = createChatPane(ids[i], i, paneSendFn);
    grid.appendChild(container);
    panes.set(ids[i], state);
  }

  // Wire FA sessions into pane states + connect WebSocket per pane
  if (paneSessionIds) {
    const { ensureSession, connectFASession, onFAEvent } =
      await import('../core/floating-artemis-api.js');

    // We need per-pane WS connections, but the FA API currently tracks a
    // single global WS. For V1 we create N short-lived fetch+WS pairs by
    // creating each session over HTTP and opening a dedicated WS per pane.
    // The global onFAEvent bus is used; we filter by session_id inside each
    // pane's handler.
    for (let i = 0; i < ids.length; i++) {
      const paneId = paneSessionIds[i];
      const chatId = ids[i];
      const pane = panes.get(chatId);
      if (!pane) continue;

      pane.faSessionId = paneId;
      _setPaneStatus(pane, "connecting…");

      // Ensure FA session exists (backend created it; this is a safety get)
      try {
        await ensureSession(paneId);
      } catch (err) {
        console.warn(`[parallel] ensureSession(${paneId}) failed:`, err.message);
      }

      // Connect WS for this pane — attach a per-pane event listener
      // The global onFAEvent bus filters by session_id so panes don't
      // cross-contaminate even though they share the single FA WS channel.
      // NOTE: the FA WS is per-session; connectFASession switches the
      // single connection. For V1 we piggyback on the HTTP push model:
      // panes render user turns immediately and poll the session for
      // assistant turns via the WS events routed to the correct pane.
      const unsubscribe = onFAEvent((event) => {
        if (event.session_id !== paneId) return;
        _handleFAEvent(pane, event);
      });
      // Store unsubscribe handle on pane for cleanup in exitParallelMode
      pane._faUnsub = unsubscribe;

      _setPaneStatus(pane, "idle");
    }

    // Connect the first pane's WS session so events can flow.
    // V1 limitation: only one pane has an active streaming WS at a time.
    // Subsequent panes get responses via HTTP polling or the same bus.
    // TODO (post-V1): open N independent WS connections per pane.
    if (paneSessionIds[0]) {
      connectFASession(paneSessionIds[0]);
    }
  }

  const alreadyParallel = getState("parallelMode");
  if (alreadyParallel) {
    // Switching between parallel counts — just swap the existing grid in place.
    // savedChatArea is still valid; dp-header is unaffected.
    const existingGrid = document.getElementById("chat-grid");
    if (existingGrid) {
      existingGrid.replaceWith(grid);
    }
  } else {
    // First entry into parallel mode — save .dp-main and replace it.
    // Target .dp-main (inner chat area) so .dp-header above it stays visible.
    const chatMain = document.querySelector(".dp-main") || document.querySelector(".chat-area");
    if (!chatMain) return;
    setState("savedChatArea", chatMain);
    setState("parallelMode", true);
    if ($.toggleParallelBtn) $.toggleParallelBtn.checked = true;
    chatMain.replaceWith(grid);
  }

  // Remove the canvas-scroll gradient mask in parallel mode — the mask fades
  // the top/bottom 32px which wipes out pane headers and makes the background
  // appear lighter. Directly toggling a class is more reliable than :has().
  document.querySelector('.canvas-scroll')?.classList.add('is-parallel');
  // Keep .canvas warm aura enabled — panes are transparent so the aura
  // becomes their backdrop, matching the main chat's look. (Previously
  // toggled off; that made panes read cold/flat and the composer card
  // then looked like a visible band above the messages area.)
  document.querySelector('.canvas')?.classList.add('is-parallel');

  const sessionId = getState("sessionId");
  if (sessionId) {
    // Lazy import to avoid circular dependency — load all panes concurrently
    import('../features/sessions.js').then(({ loadPaneMessages }) => {
      Promise.all(ids.map(chatId => loadPaneMessages(sessionId, chatId)));
    });
  }
}

export function exitParallelMode() {
  setState("parallelMode", false);
  setState("parallelCount", 1);
  if ($.toggleParallelBtn) $.toggleParallelBtn.checked = false;

  // Restore the gradient mask for single-chat scroll UX
  document.querySelector('.canvas-scroll')?.classList.remove('is-parallel');
  document.querySelector('.canvas')?.classList.remove('is-parallel');

  // Clean up FA event listeners attached to panes
  panes.forEach((pane) => {
    if (typeof pane._faUnsub === 'function') {
      pane._faUnsub();
      pane._faUnsub = null;
    }
  });

  const grid = document.getElementById("chat-grid");
  const savedChatArea = getState("savedChatArea");
  if (grid && savedChatArea) {
    grid.replaceWith(savedChatArea);
  }

  initSinglePane();

  const sessionId = getState("sessionId");
  if (sessionId) {
    import('../features/sessions.js').then(({ loadMessages }) => {
      loadMessages(sessionId);
    });
  }
}

// Lazy getter for input history to avoid circular dependency
let _inputHistoryGetter = null;
export function _setInputHistoryGetter(fn) { _inputHistoryGetter = fn; }
function _getInputHistory() { return _inputHistoryGetter ? _inputHistoryGetter() : null; }

// Lazy getter for chat.js functions to avoid circular dependency
let _chatFns = null;
function _getLazyChatFns() {
  if (!_chatFns) {
    // These are set by chat.js during init
    _chatFns = { sendMessage: () => {}, stopGeneration: () => {} };
  }
  return _chatFns;
}

export function _setChatFns(fns) {
  _chatFns = fns;
}
