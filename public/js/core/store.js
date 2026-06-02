// Centralized reactive state store
import { DEFAULT_APP_VIEW, normalizeAppView, parseAppHash } from "./navigation.js";

const HISTORY_COMPANION_KEYS = ["builderAgentId", "builderEditAgentId", "sessionId"];

function hasBrowserHistory() {
  return typeof window !== "undefined" && typeof window.history?.pushState === "function";
}

function parseHashView() {
  if (typeof window === "undefined") return null;
  return parseAppHash(window.location?.hash || "").view;
}

function buildHistoryState(view) {
  const data = { view: normalizeAppView(view) };
  for (const key of HISTORY_COMPANION_KEYS) {
    if (state[key] !== undefined && state[key] !== null && state[key] !== "") {
      data[key] = state[key];
    }
  }
  return data;
}

function viewHash(view) {
  return "#/" + encodeURIComponent(normalizeAppView(view));
}

function pushViewHistory(view) {
  if (!hasBrowserHistory()) return;
  const nextState = buildHistoryState(view);
  const nextHash = viewHash(nextState.view);
  if (window.location.hash === nextHash && window.history.state?.view === nextState.view) return;
  window.history.pushState(nextState, "", nextHash);
}

function replaceInitialHistoryState() {
  if (!hasBrowserHistory()) return;
  window.history.replaceState(buildHistoryState(state.view), "", window.location.href);
}

function restoreHistoryState(event) {
  const historyState = event.state || {};
  const nextView = historyState.view || parseHashView() || DEFAULT_APP_VIEW;
  for (const key of HISTORY_COMPANION_KEYS) {
    if (historyState[key] !== undefined) {
      setState(key, historyState[key], { fromHistory: true });
    }
  }
  setState("view", nextView, { fromHistory: true });
}

const state = {
  view: parseHashView() || "command-center",
  ws: null,
  sessionId: null,
  parallelMode: false,
  streamingCharCount: 0,
  prompts: [],
  workflows: [],
  agents: [],
  projectsData: [],
  attachedFiles: [],
  imageAttachments: [],
  allProjectFiles: [],
  mermaidCounter: 0,
  savedChatArea: null,
  backgroundSessions: new Map(),
  notificationsEnabled: false,
  sessionTokens: { input: 0, output: 0, cacheRead: 0, cacheCreation: 0 },
};

const listeners = {};

export function getState(key) {
  return state[key];
}

export function setState(key, val, options = {}) {
  state[key] = val;
  emit(key, val);
  if (key === "view" && !options.fromHistory) {
    pushViewHistory(val);
  }
}

/** Subscribe to state changes for a key. Returns an unsubscribe function. */
export function on(key, fn) {
  (listeners[key] ||= []).push(fn);
  return () => {
    const arr = listeners[key];
    if (arr) listeners[key] = arr.filter(f => f !== fn);
  };
}

/** Remove a specific listener for a key. */
export function off(key, fn) {
  const arr = listeners[key];
  if (arr) listeners[key] = arr.filter(f => f !== fn);
}

function emit(key, val) {
  (listeners[key] || []).forEach((fn) => fn(val));
}

if (hasBrowserHistory()) {
  replaceInitialHistoryState();
  window.addEventListener("popstate", restoreHistoryState);
}
