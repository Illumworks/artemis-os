// HTTP client + WebSocket manager for the Floating Artemis backend.
// All fetch calls run through the global interceptor in api.js which injects
// CSRF tokens and handles 401 redirects.
//
// WS pattern: one connection per session. Reconnects with exponential backoff.
// Subscribe with onFAEvent(handler) — returns unsubscribe fn.
//
// Usage:
//   import * as fa from './floating-artemis-api.js';
//   const unsub = fa.onFAEvent(event => { ... });
//   await fa.ensureSession('fa-abc123');
//   fa.connectFASession('fa-abc123');

const _BASE = '/api/floating-artemis';

// ── Session endpoints ─────────────────────────────────────────────────────────

export async function createSession(sessionId, { ownerUserId = null, title = null } = {}) {
  const res = await fetch(`${_BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, owner_user_id: ownerUserId, title }),
  });
  if (res.status === 409) return getSession(sessionId); // already exists — that's fine
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `createSession failed: ${res.status}`);
  }
  return res.json();
}

export async function getSession(sessionId) {
  const res = await fetch(`${_BASE}/sessions/${encodeURIComponent(sessionId)}`);
  if (!res.ok) throw new Error(`getSession failed: ${res.status}`);
  return res.json();
}

export async function ensureSession(sessionId) {
  try {
    return await getSession(sessionId);
  } catch {
    return createSession(sessionId);
  }
}

export async function archiveSession(sessionId) {
  const res = await fetch(`${_BASE}/sessions/${encodeURIComponent(sessionId)}/archive`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`archiveSession failed: ${res.status}`);
  return res.json();
}

// ── Message endpoints ─────────────────────────────────────────────────────────

export async function sendMessage(sessionId, message, { attachments = [] } = {}) {
  const res = await fetch(`${_BASE}/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, attachments }),
  });
  if (!res.ok) throw new Error(`sendMessage failed: ${res.status}`);
  return res.json();
}

export async function listMessages(sessionId, { limit = 50, cursor = null } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor != null) params.set('cursor', String(cursor));
  const res = await fetch(
    `${_BASE}/sessions/${encodeURIComponent(sessionId)}/messages?${params}`,
  );
  if (!res.ok) throw new Error(`listMessages failed: ${res.status}`);
  return res.json();
}

// ── Page context ──────────────────────────────────────────────────────────────

export async function setPageContext(sessionId, page, refId = null) {
  const res = await fetch(`${_BASE}/sessions/${encodeURIComponent(sessionId)}/page-context`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ page, ref_id: refId }),
  });
  if (!res.ok) throw new Error(`setPageContext failed: ${res.status}`);
  return res.json();
}

// ── Tool confirm ──────────────────────────────────────────────────────────────

export async function toolConfirm(sessionId, toolUseId, decision) {
  const res = await fetch(`${_BASE}/sessions/${encodeURIComponent(sessionId)}/tool-confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tool_use_id: toolUseId, decision }),
  });
  if (!res.ok) throw new Error(`toolConfirm failed: ${res.status}`);
  return res.json();
}

// ── Stop ──────────────────────────────────────────────────────────────────────

export async function stopTurn(sessionId) {
  const res = await fetch(`${_BASE}/sessions/${encodeURIComponent(sessionId)}/stop`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`stopTurn failed: ${res.status}`);
  return res.json();
}

// ── Active runs ───────────────────────────────────────────────────────────────

export async function getActiveRuns({ ownerUserId = null } = {}) {
  const params = new URLSearchParams();
  if (ownerUserId != null) params.set('owner_user_id', String(ownerUserId));
  const qs = params.toString();
  const res = await fetch(`${_BASE}/active-runs${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error(`getActiveRuns failed: ${res.status}`);
  return res.json();
}

// ── WebSocket connection ──────────────────────────────────────────────────────

let _ws = null;
let _wsSessionId = null;
let _listeners = /** @type {Function[]} */ ([]);
let _reconnectTimer = null;
let _reconnectAttempts = 0;
let _intentionalClose = false;

const _WS_BACKOFF_BASE = 2000;
const _WS_BACKOFF_MAX = 30000;

function _backoffDelay() {
  const d = Math.min(_WS_BACKOFF_BASE * 2 ** _reconnectAttempts, _WS_BACKOFF_MAX);
  return d + d * Math.random() * 0.25;
}

function _emit(event) {
  for (const h of _listeners) {
    try { h(event); } catch (e) { console.warn('[FA-WS] listener error', e); }
  }
}

export function onFAEvent(handler) {
  _listeners.push(handler);
  return () => { _listeners = _listeners.filter((h) => h !== handler); };
}

export function connectFASession(sessionId) {
  if (_ws && _wsSessionId === sessionId && _ws.readyState <= WebSocket.OPEN) return;
  _intentionalClose = false;
  if (_ws) {
    _intentionalClose = true;
    _ws.close(1000, 'reconnect');
    _intentionalClose = false;
  }
  _wsSessionId = sessionId;
  _reconnectAttempts = 0;
  _openWs();
}

function _openWs() {
  if (!_wsSessionId) return;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${location.host}/ws/floating-artemis/${encodeURIComponent(_wsSessionId)}`;
  _ws = new WebSocket(url);

  let pingTimer = null;

  _ws.onopen = () => {
    _reconnectAttempts = 0;
    _emit({ type: 'fa:connected', session_id: _wsSessionId });
    pingTimer = setInterval(() => {
      if (_ws?.readyState === WebSocket.OPEN) _ws.send('ping');
    }, 25_000);
  };

  _ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.type !== 'ping') _emit(data);
    } catch {}
  };

  _ws.onclose = (ev) => {
    clearInterval(pingTimer);
    if (ev.code === 1000 || !_wsSessionId) return;
    const delay = _backoffDelay();
    _reconnectAttempts++;
    _emit({ type: 'fa:disconnected', session_id: _wsSessionId });
    _reconnectTimer = setTimeout(_openWs, delay);
  };

  _ws.onerror = () => {
    _emit({ type: 'fa:error', session_id: _wsSessionId });
  };
}

export function disconnectFASession() {
  clearTimeout(_reconnectTimer);
  const sid = _wsSessionId;
  _wsSessionId = null;
  if (_ws) { _ws.close(1000, 'shutdown'); _ws = null; }
  _listeners = [];
  if (sid) _emit({ type: 'fa:disconnected', session_id: sid });
}

export function getFASessionId() {
  return _wsSessionId;
}
