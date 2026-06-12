// Per-draft WebSocket factory for real-time collaborative editing.
//
// Phase 0: identity-aware connection, heartbeat keep-alive, reconnect with
// exponential backoff.  No editor synchronisation yet — that is Phase 1.
//
// Usage:
//   import { openCollabSocket } from '../core/collab-socket.js';
//   const handle = openCollabSocket({
//     draftId: 42,
//     onEvent: (evt) => console.debug('[collab]', evt),
//   });
//   // ... later:
//   handle.close();

const _WS_BACKOFF_BASE = 2000;
const _WS_BACKOFF_MAX = 30000;
const _PING_INTERVAL_MS = 25000;

/**
 * Open a collab WebSocket for the given draft and return a handle.
 *
 * @param {object} opts
 * @param {number|string} opts.draftId  — draft primary key.
 * @param {string|null}   [opts.asEmail] — dev-only identity override.
 * @param {string|null}   [opts.asName]  — dev-only name override.
 * @param {Function}      [opts.onEvent] — called with typed event objects.
 * @returns {{ send: (msg: string) => void, close: () => void }}
 */
export function openCollabSocket({ draftId, asEmail = null, asName = null, onEvent } = {}) {
  let ws = null;
  let reconnectTimer = null;
  let pingTimer = null;
  let attempts = 0;
  let intentionalClose = false;
  let everConnected = false;

  function _backoffDelay() {
    const d = Math.min(_WS_BACKOFF_BASE * 2 ** attempts, _WS_BACKOFF_MAX);
    return d + d * Math.random() * 0.25;
  }

  function _emit(evt) {
    if (typeof onEvent === 'function') {
      try { onEvent(evt); } catch (e) { console.warn('[collab-socket] onEvent error', e); }
    }
  }

  function _buildUrl() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let url = `${protocol}//${location.host}/api/writing-studio/drafts/${encodeURIComponent(String(draftId))}/collab`;
    if (asEmail) {
      const qs = new URLSearchParams({ as_email: asEmail });
      if (asName) qs.set('as_name', asName);
      url += '?' + qs.toString();
    }
    return url;
  }

  function _open() {
    ws = new WebSocket(_buildUrl());

    ws.onopen = () => {
      attempts = 0;
      _emit({ type: everConnected ? 'collab:reconnected' : 'collab:connected' });
      everConnected = true;
      pingTimer = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) ws.send('ping');
      }, _PING_INTERVAL_MS);
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        _emit(data);
      } catch {
        // Non-JSON frame (e.g. a plain pong string) — ignore.
      }
    };

    ws.onclose = (ev) => {
      clearInterval(pingTimer);
      pingTimer = null;
      if (intentionalClose || ev.code === 1000) return;
      if (ev.code === 4401) {
        console.warn('[collab-socket] auth rejected (4401) — not reconnecting');
        _emit({ type: 'collab:disconnected' });
        return;
      }
      _emit({ type: 'collab:disconnected' });
      const delay = _backoffDelay();
      attempts++;
      reconnectTimer = setTimeout(_open, delay);
    };

    ws.onerror = () => {
      // onclose fires immediately after onerror; handle reconnect there.
    };
  }

  _open();

  return {
    /** Send an arbitrary string frame (e.g. a ping or future delta payload). */
    send(msg) {
      if (ws?.readyState === WebSocket.OPEN) ws.send(msg);
    },

    /** Cleanly close the connection (code 1000) and suppress reconnect. */
    close() {
      intentionalClose = true;
      clearTimeout(reconnectTimer);
      clearInterval(pingTimer);
      reconnectTimer = null;
      pingTimer = null;
      if (ws) {
        ws.close(1000, 'shutdown');
        ws = null;
      }
    },
  };
}
