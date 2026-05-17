// Floating Artemis feature orchestrator.
//
// Responsibilities:
//   - One persistent session per operator (localStorage-backed)
//   - Session creation/retrieval on boot
//   - WebSocket setup via floating-artemis-api
//   - Page-context sync on every navigation (debounced)
//   - FAB badge polling (active runs count)
//   - First-run calibration (Position B: grounded opening, not a template)
//   - "Start fresh" — archives current session, creates a new one
//   - Panel toggle wired to the #assistant-fab button
//
// The panel custom element (<floating-artemis-panel>) receives events via
// CustomEvents dispatched on the element itself. This module fires them;
// floating-panel.js handles them.

import { isSurfaceAvailable } from '../core/status.js';
import { on as storeOn, getState } from '../core/store.js';
import {
  ensureSession as apiEnsureSession,
  archiveSession,
  setPageContext,
  sendMessage,
  listMessages,
  getActiveRuns,
  connectFASession,
  onFAEvent,
  getLatestMemoryReads,
} from '../core/floating-artemis-api.js';

// ── Session persistence ───────────────────────────────────────────────────────

const _SESSION_KEY = 'artemis-fa-session-id';
const _FIRST_RUN_KEY = 'artemis-fa-first-run-done';

function _storedSessionId() { return localStorage.getItem(_SESSION_KEY); }
function _setSessionId(id) { localStorage.setItem(_SESSION_KEY, id); }
function _clearSession() {
  localStorage.removeItem(_SESSION_KEY);
  localStorage.removeItem(_FIRST_RUN_KEY);
}
function _genSessionId() {
  return `fa-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

// ── Module state ──────────────────────────────────────────────────────────────

let _sessionId = null;
let _panelEl = null;
let _isFirstRun = false;
let _badgePollTimer = null;
let _pageDebounce = null;
let _lastView = null;

export function getCurrentSessionId() { return _sessionId; }

// ── Panel helpers ─────────────────────────────────────────────────────────────

function _panelDispatch(type, detail = {}) {
  _panelEl?.dispatchEvent(new CustomEvent(type, { detail, bubbles: false }));
}

function _isPanelOpen() {
  return _panelEl?.hasAttribute('open') ?? false;
}

function _openPanel() {
  if (_panelEl && !_isPanelOpen()) {
    _panelEl.setAttribute('open', '');
    _panelDispatch('fa:opened');
  }
}

function _closePanel() {
  if (_panelEl && _isPanelOpen()) {
    _panelEl.removeAttribute('open');
    _panelDispatch('fa:closed');
  }
}

function _togglePanel() {
  _isPanelOpen() ? _closePanel() : _openPanel();
  if (_isFirstRun && _sessionId && _isPanelOpen()) {
    _isFirstRun = false;
    _runFirstRunCalibration();
  }
}

// ── FAB badge ─────────────────────────────────────────────────────────────────

function _updateBadge(count) {
  const fab = document.getElementById('assistant-fab');
  if (!fab) return;
  let badge = fab.querySelector('.assistant-fab-badge');
  if (count > 0) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'assistant-fab-badge';
      fab.appendChild(badge);
    }
    badge.textContent = String(count);
  } else if (badge) {
    badge.remove();
  }
}

async function _refreshBadge() {
  try {
    const data = await getActiveRuns();
    _updateBadge((data.runs || []).length);
  } catch {}
}

function _startBadgePoll() {
  if (_badgePollTimer) return;
  _refreshBadge();
  _badgePollTimer = setInterval(_refreshBadge, 15_000);
}

// ── Page context ──────────────────────────────────────────────────────────────

function _syncPageContext(view) {
  if (view === _lastView || !_sessionId) return;
  _lastView = view;
  clearTimeout(_pageDebounce);
  _pageDebounce = setTimeout(async () => {
    try {
      await setPageContext(_sessionId, view);
      _panelDispatch('fa:page-changed', { page: view });
    } catch {}
  }, 400);
}

// ── First-run calibration (Position B) ───────────────────────────────────────
// She reads context, shows a loading step, then opens with a grounded message.
// No templated greeting — she says something specific to what she found.

async function _runFirstRunCalibration() {
  _panelDispatch('fa:calibrating', { step: 'Catching up on context…' });
  try {
    await sendMessage(
      _sessionId,
      [
        'You are opening a fresh session.',
        'Before responding, look at the current page context and any relevant memory you have.',
        'Open with one or two sentences that are specific to what you actually know right now.',
        'No templated greeting. No "Hello, I am Artemis." — just start from what you see.',
      ].join(' '),
    );
    // Response comes via WS; panel renders it.
    localStorage.setItem(_FIRST_RUN_KEY, '1');
  } catch (err) {
    console.warn('[FA] First-run calibration failed:', err);
  }
}

// ── Session lifecycle ─────────────────────────────────────────────────────────

async function _setupSession() {
  const stored = _storedSessionId();
  const sid = stored || _genSessionId();
  _isFirstRun = !stored || localStorage.getItem(_FIRST_RUN_KEY) !== '1';

  try {
    await apiEnsureSession(sid);
    _sessionId = sid;
    _setSessionId(sid);
  } catch (err) {
    console.error('[FA] Session setup failed:', err);
    return;
  }

  connectFASession(_sessionId);

  // Relay WS events to panel
  onFAEvent((event) => {
    _panelDispatch('fa:event', event);
    if (
      event.type === 'floating_artemis.turn_complete' ||
      event.type === 'floating_artemis.failed'
    ) {
      _refreshBadge();
    }
    // ── Memory inspector: relay memory_read events ────────────────────────────
    if (event.event === 'floating_artemis.memory_read' && event.session_id === _sessionId) {
      _panelDispatch('fa:memory-read', {
        observations: event.observations || [],
        turn_id: event.turn_id || null,
      });
    }
  });

  // Load history into panel
  try {
    const data = await listMessages(_sessionId, { limit: 50 });
    _panelDispatch('fa:history', { messages: data.messages || [] });
  } catch {}

  // Sync current view
  _syncPageContext(getState('view') || 'command-center');
}

// ── Start fresh ───────────────────────────────────────────────────────────────

export async function startFresh() {
  if (!_sessionId) return;
  try {
    await archiveSession(_sessionId);
  } catch {}

  _clearSession();
  _sessionId = null;
  _lastView = null;

  await _setupSession();
  _panelDispatch('fa:fresh-start');

  if (_sessionId) {
    _isFirstRun = false;
    _runFirstRunCalibration();
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

export async function init() {
  // Gate on surface availability
  if (!isSurfaceAvailable('floating-artemis')) {
    const fab = document.getElementById('assistant-fab');
    if (fab) fab.style.display = 'none';
    return;
  }

  _panelEl = document.getElementById('floating-artemis-panel');

  // Wire FAB
  const fab = document.getElementById('assistant-fab');
  if (fab) fab.addEventListener('click', _togglePanel);

  // Session + WS
  await _setupSession();

  // Navigate: sync page context
  storeOn('view', _syncPageContext);

  // Panel requests start-fresh
  _panelEl?.addEventListener('fa:request-fresh', startFresh);

  // Badge polling
  _startBadgePoll();
}

init();
