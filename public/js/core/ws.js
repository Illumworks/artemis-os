// WebSocket connection + message dispatch with exponential backoff
import { $ } from './dom.js';
import { getState, setState } from './store.js';
import { emit } from './events.js';

export function subscribeToSession(sessionId) {
  const ws = getState("ws");
  if (!ws || ws.readyState !== 1 || !sessionId) return;
  ws.send(JSON.stringify({ type: "subscribe", sessionId }));
}

let backoffAttempt = 0;
let hasConnectedBefore = false;

const BACKOFF_BASE_MS = 2000;
const BACKOFF_FACTOR = 2;
const BACKOFF_MAX_MS = 30000;

function getBackoffDelay() {
  const delay = Math.min(BACKOFF_BASE_MS * Math.pow(BACKOFF_FACTOR, backoffAttempt), BACKOFF_MAX_MS);
  // Add 0-25% jitter
  const jitter = delay * Math.random() * 0.25;
  return delay + jitter;
}

const PING_INTERVAL_MS = 25000;

export function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws`);
  setState("ws", ws);

  let pingTimer = null;

  ws.onopen = () => {
    console.log("WebSocket connected");
    if ($.connectionDot) {
      $.connectionDot.className = "term-dot connected";
    }
    if ($.connectionText) {
      $.connectionText.textContent = "connected";
      $.connectionText.className = "term-status ok";
    }

    // Reset backoff on successful connection
    backoffAttempt = 0;

    // Keepalive ping — prevents browsers from dropping the WS while the tab
    // is backgrounded, which would interrupt active Claude streams server-side.
    pingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping" }));
      }
    }, PING_INTERVAL_MS);

    if (hasConnectedBefore) {
      emit("ws:reconnected");
    } else {
      hasConnectedBefore = true;
      emit("ws:connected");
    }

    // Subscribe to current session for multi-client broadcast
    const currentSession = getState("sessionId");
    if (currentSession) {
      ws.send(JSON.stringify({ type: "subscribe", sessionId: currentSession }));
    }
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    emit("ws:message", msg);
  };

  ws.onclose = (event) => {
    clearInterval(pingTimer);
    pingTimer = null;

    // Auth rejected — redirect to login instead of reconnecting
    if (event.code === 1008 || event.code === 4401) {
      window.location.href = "/login";
      return;
    }

    const delay = getBackoffDelay();
    backoffAttempt++;
    console.log(`WebSocket disconnected, reconnecting in ${Math.round(delay)}ms (attempt ${backoffAttempt})...`);
    if ($.connectionDot) {
      $.connectionDot.className = "term-dot reconnecting";
    }
    if ($.connectionText) {
      $.connectionText.textContent = "reconnecting";
      $.connectionText.className = "term-status";
    }
    emit("ws:disconnected");
    setTimeout(connectWebSocket, delay);
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
    if ($.connectionDot) {
      $.connectionDot.className = "term-dot";
    }
    if ($.connectionText) {
      $.connectionText.textContent = "disconnected";
      $.connectionText.className = "term-status";
    }
  };
}
