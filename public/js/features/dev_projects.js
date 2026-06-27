import { railMarkup, renderAnnotations } from "../components/dev-projects-annotation-rail.js";
import { appendPermission, renderMessages } from "../components/dev-projects-chat.js";
import { bindComposer } from "../components/dev-projects-composer.js";
import { renderProjectModal } from "../components/dev-projects-project-modal.js";
import { renderDevProjectsRail } from "../components/dev-projects-sidebar.js";
import { getState, setState, on as onStore } from "../core/store.js";
import { escapeHtml } from "../core/utils.js";

const API = "/api/dev-projects";
const STORAGE = {
  activeSession: "artemis.devProjects.activeSession",
  expanded: "artemis.devProjects.expandedProjects",
  showArchived: "artemis.devProjects.showArchived",
  sortMode: "artemis.devProjects.sortMode",
  railOpen: "artemis.devProjects.railOpen",
  railWidth: "artemis.devProjects.railWidth",
};

const RAIL_MIN = 280;
// Cap rail at 60% of viewport width (with a 600px floor for narrow displays)
// so users on wider monitors can drag the preview rail far wider than the
// previous fixed 600px ceiling. Recomputed on each clamp call so resizing the
// window stays consistent. Persistence in localStorage is unchanged.
const railMax = () => Math.max(600, Math.round((typeof window !== "undefined" ? window.innerWidth : 1600) * 0.6));
const RAIL_DEFAULT = 360;

const state = {
  projects: [],
  sessionsByProject: new Map(),
  expandedProjectIds: new Set(JSON.parse(localStorage.getItem(STORAGE.expanded) || "[]")),
  visibleCounts: new Map(),
  activeProjectId: null,
  activeSessionId: Number(localStorage.getItem(STORAGE.activeSession) || 0) || null,
  activeSession: null,
  annotations: [],
  ws: null,
  showArchived: localStorage.getItem(STORAGE.showArchived) === "true",
  sortMode: localStorage.getItem(STORAGE.sortMode) || "recent",
  railOpen: localStorage.getItem(STORAGE.railOpen) === "true",
  railWidth: Math.min(railMax(), Math.max(RAIL_MIN, Number(localStorage.getItem(STORAGE.railWidth)) || RAIL_DEFAULT)),
  providers: [],
  modal: {
    open: false,
    currentPath: "",
    parentPath: null,
    entries: [],
    name: "",
    error: "",
    loading: false,
    focusedIndex: 0,
    dirtyName: false,
    manualFallback: false,
    manualPath: "",
    focusAfterRender: "",
  },
};

const els = {};

async function request(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || data;
    throw new Error(detail.error || data.error || `Request failed: ${res.status}`);
  }
  return data;
}

function $(id) {
  return document.getElementById(id);
}

function ensureShell() {
  els.messages = $("messages");
  els.input = $("message-input");
  els.send = $("send-btn");
  els.sessionList = $("session-list");
  els.projectName = $("header-project-name");
  els.projectPath = $("header-project-path");
  els.projectCount = $("header-project-session-count");
  els.railProjectName = $("rail-project-name");
  els.railProjectSub = $("rail-project-sub");
  els.headerActions = document.querySelector(".dp-header-actions");
  els.dpShell = document.querySelector(".dp-shell");
  els.rail = document.querySelector(".rail-dev-friendly");
  els.projectTitleBtn = $("header-project-title-btn");
  els.modelBtn = $("composer-model-btn");
  els.modelBtnLabel = $("composer-model-btn-label");
  els.modelMenu = $("composer-model-menu");

  if (!els.dpShell || $("dev-project-panel")) return Boolean(els.dpShell);

  document.body.insertAdjacentHTML("beforeend", `<div id="dev-project-modal-root"></div><div id="dev-context-root"></div>`);
  $("rail-project-card")?.classList.add("dev-legacy-hidden");
  $("new-session-btn")?.classList.add("dev-legacy-hidden");

  const panel = document.createElement("div");
  panel.id = "dev-project-panel";
  panel.className = "dev-project-panel";
  panel.innerHTML = `
    <div class="dev-project-panel-header">
      <div class="dev-panel-title">Projects</div>
      <div class="dev-panel-actions">
        <button class="dev-icon-button" type="button" title="Collapse all" data-dev-action="collapse-all">⇱</button>
        <button class="dev-icon-button" type="button" title="Sort and archived" data-dev-action="sort-menu">≡</button>
        <button class="dev-icon-button" type="button" title="New project" data-dev-action="open-project-modal">▣+</button>
      </div>
    </div>
    <div id="dev-project-list" class="dev-project-list"></div>
  `;
  els.rail?.prepend(panel);

  // Right-rail toggle: lives alongside Session Config / bell so users can
  // open the on-demand preview rail. Hidden in parallel mode (single-session
  // only, per spec). Hidden until a session is loaded.
  const railToggle = document.createElement("button");
  railToggle.type = "button";
  railToggle.className = "dp-icon-btn dev-rail-toggle-btn hidden";
  railToggle.id = "dev-rail-toggle";
  railToggle.title = "Preview rail";
  railToggle.setAttribute("aria-label", "Preview rail");
  railToggle.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="15" y1="4" x2="15" y2="20"/></svg>`;
  els.headerActions?.appendChild(railToggle);

  els.dpShell.insertAdjacentHTML("beforeend", railMarkup());
  document.querySelector(".chat-area")?.classList.add("dev-projects-ready");
  return true;
}

async function loadModels() {
  const data = await fetch("/api/floating-artemis/models").then((res) => res.json());
  const providers = [...(data.providers || [])];
  if (!providers.some((provider) => provider.id === "openai")) {
    providers.push({ id: "openai", name: "OpenAI API", models: [{ id: "", label: "Default" }] });
  }
  state.providers = providers.filter((p) => ["claude-code", "codex", "anthropic", "openai", "gemini", "lm-studio"].includes(p.id));
  renderModelMenu();
  // Reflect persisted Session Config selection (or provider default) in the
  // composer button label immediately on page load, rather than waiting for
  // a session to load and overwriting the hardcoded HTML default.
  syncModelButtonLabel();
}

function getCurrentProviderId() {
  return state.activeSession?.provider
    || (() => { try { return localStorage.getItem("artemis-provider-source"); } catch { return null; } })()
    || "claude-code";
}

function renderModelMenu() {
  if (!els.modelMenu) return;
  if (!state.providers.length) { els.modelMenu.innerHTML = ""; return; }
  const activeProvider = getCurrentProviderId();
  const activeModel = state.activeSession?.model || "";
  // Render every configured provider as its own group so the picker always
  // shows the full catalog (3-5+ options across providers), not just whatever
  // single-model CLI is currently selected.
  const groups = state.providers.map((provider) => {
    const models = provider.models?.length ? provider.models : [{ id: "", label: "Default" }];
    const items = models.map((m) => {
      const modelId = m.id === "default" ? "" : (m.id || "");
      const label = escapeHtml(m.label || m.id || "Default");
      const isActive = provider.id === activeProvider && modelId === activeModel;
      const cls = isActive ? "dp-model-menu-item is-active" : "dp-model-menu-item";
      return `<button type="button" class="${cls}" role="menuitem" data-provider="${escapeHtml(provider.id)}" data-model="${escapeHtml(modelId)}">${label}</button>`;
    }).join("");
    const groupLabel = escapeHtml(provider.name || provider.id);
    return `<div class="dp-model-menu-group"><div class="dp-model-menu-group-label">${groupLabel}</div>${items}</div>`;
  }).join("");
  els.modelMenu.innerHTML = groups;
}

function syncModelButtonLabel() {
  if (!els.modelBtnLabel) return;
  const provider = getCurrentProviderId();
  // Fall back to the Session Config select / localStorage when no session is
  // active yet, so the button reflects what the user picked in the cog tray
  // even before a session exists.
  let modelId = state.activeSession?.model || "";
  if (!modelId) {
    try { modelId = $("model-select")?.value || localStorage.getItem("artemis-model") || ""; } catch { /* noop */ }
  }
  const providerDef = state.providers.find((p) => p.id === provider);
  const modelDef = providerDef?.models?.find((m) => (m.id === "default" ? "" : (m.id || "")) === modelId);
  els.modelBtnLabel.textContent = modelDef?.label || providerDef?.models?.find((m) => m.default)?.label || providerDef?.models?.[0]?.label || providerDef?.name || "Model";
}

function openModelMenu() {
  if (!els.modelMenu || !els.modelBtn) return;
  if (!state.providers.length) return;
  els.modelMenu.classList.remove("hidden");
  els.modelBtn.setAttribute("aria-expanded", "true");
}

function closeModelMenu() {
  els.modelMenu?.classList.add("hidden");
  els.modelBtn?.setAttribute("aria-expanded", "false");
}

async function selectModel(provider, model) {
  closeModelMenu();
  if (!state.activeSessionId) {
    // No active session yet — stash defaults via the project, so the next
    // session created via the composer picks them up.
    state.activeSession = { ...(state.activeSession || {}), provider, model };
    syncModelButtonLabel();
    await saveProjectModelDefault(provider, model);
    return;
  }
  state.activeSession = await request(`/sessions/${state.activeSessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ provider, model }),
  });
  syncModelButtonLabel();
  await saveProjectModelDefault(provider, model);
}

async function saveProjectModelDefault(provider, model) {
  const project = getActiveProject();
  if (!project) return;
  const metadata = { ...(project.metadata || {}), default_provider: provider, default_model: model };
  const updated = await request(`/projects/${project.id}`, {
    method: "PATCH",
    body: JSON.stringify({ metadata }),
  });
  project.metadata = updated.metadata || metadata;
}

async function loadProjects() {
  const data = await request("/projects");
  state.projects = data.projects || [];
  await Promise.all(state.projects.map(async (project) => {
    const sessionData = await request(`/projects/${project.id}/sessions`);
    state.sessionsByProject.set(Number(project.id), sessionData.sessions || []);
  }));
  restoreActivePointers();
  renderProjects();
  if (state.activeSessionId) await loadSession(state.activeSessionId);
  else clearChat();
}

function restoreActivePointers() {
  const allSessions = [...state.sessionsByProject.values()].flat();
  const restored = allSessions.find((session) => Number(session.id) === Number(state.activeSessionId));
  if (restored && !restored.archived_at) {
    state.activeProjectId = restored.project_id;
    state.expandedProjectIds.add(Number(restored.project_id));
    return;
  }
  const firstProject = state.projects.find((project) => !project.archived_at) || state.projects[0];
  state.activeProjectId = firstProject?.id || null;
  if (state.activeProjectId) state.expandedProjectIds.add(Number(state.activeProjectId));
  state.activeSessionId = null;
}

function renderProjects() {
  const list = $("dev-project-list");
  if (list) {
    list.innerHTML = renderDevProjectsRail({
      projects: state.projects,
      sessionsByProject: state.sessionsByProject,
      activeProjectId: state.activeProjectId,
      activeSessionId: state.activeSessionId,
      expandedProjectIds: state.expandedProjectIds,
      visibleCounts: state.visibleCounts,
      showArchived: state.showArchived,
      sortMode: state.sortMode,
    });
  }
  syncProjectHeader();
  localStorage.setItem(STORAGE.expanded, JSON.stringify([...state.expandedProjectIds]));
  localStorage.setItem(STORAGE.showArchived, String(state.showArchived));
  localStorage.setItem(STORAGE.sortMode, state.sortMode);
}

function syncProjectHeader() {
  const active = getActiveProject();
  const sessions = active ? state.sessionsByProject.get(Number(active.id)) || [] : [];
  const name = active?.name || "Select a project";
  const path = active?.path || "No project selected";
  if (els.projectName) els.projectName.textContent = name;
  if (els.projectPath) els.projectPath.textContent = path;
  if (els.projectCount) els.projectCount.textContent = `${sessions.length} session${sessions.length === 1 ? "" : "s"}`;
  if (els.railProjectName) els.railProjectName.textContent = name;
  if (els.railProjectSub) els.railProjectSub.textContent = active ? path : "-";
}

function getActiveProject() {
  return state.projects.find((p) => Number(p.id) === Number(state.activeProjectId));
}

async function refreshProjectSessions(projectId) {
  const data = await request(`/projects/${projectId}/sessions`);
  state.sessionsByProject.set(Number(projectId), data.sessions || []);
  renderProjects();
}

async function loadSession(sessionId) {
  const data = await request(`/sessions/${sessionId}`);
  state.activeSessionId = data.session.id;
  state.activeProjectId = data.session.project_id;
  state.activeSession = data.session;
  state.annotations = data.annotations || [];
  state.expandedProjectIds.add(Number(data.session.project_id));
  localStorage.setItem(STORAGE.activeSession, String(data.session.id));
  setState("view", "chat");
  renderProjects();
  renderChat(data.messages || []);
  renderRail();
  applyRailState();
  syncModelButtonLabel();

  // Reconnect catch-up: if a build is in flight, replay persisted log entries
  // into the streaming bubble before attaching the live WS so the user sees
  // everything streamed so far instead of a blank screen.
  let activeRun = null;
  try {
    const runData = await fetch(`${API}/sessions/${sessionId}/active-run`).then((r) => r.json());
    activeRun = runData?.active_run || null;
  } catch {
    // Network/parse failure — fall through and connect WS as normal.
  }

  if (activeRun && activeRun.status === "running") {
    // Show a lightweight "build running" badge above the messages area.
    if (els.messages) {
      els.messages.insertAdjacentHTML("afterbegin", `
        <div class="dev-run-badge" id="dev-run-badge">
          <span class="dev-run-badge-dot"></span>build running
        </div>
      `);
    }

    // Replay log entries in seq order.
    const log = Array.isArray(activeRun.log) ? activeRun.log.slice().sort((a, b) => a.seq - b.seq) : [];
    let lastPermission = null;
    let seenTokenOrError = false;

    for (const entry of log) {
      if (entry.kind === "token") {
        const text = entry.payload?.text || "";
        if (text) {
          ensureStreamingBubble().querySelector(".dev-message-text").textContent += text;
          seenTokenOrError = true;
        }
        lastPermission = null; // a token after a permission means it was resolved
      } else if (entry.kind === "permission") {
        lastPermission = entry;
      } else if (entry.kind === "error") {
        const text = entry.payload?.text || "";
        if (text && els.messages) {
          els.messages.insertAdjacentHTML("beforeend", `
            <div class="dev-message dev-message-error">
              <div class="dev-message-role">error</div>
              <div class="dev-message-body"><div class="dev-message-text">${escapeHtml(text)}</div></div>
            </div>
          `);
          seenTokenOrError = true;
        }
        lastPermission = null;
      } else if (entry.kind === "message") {
        // Ares-path message replay: payload is { role, content: [...] }
        const content = entry.payload?.content;
        if (Array.isArray(content)) {
          renderAresTextBlocks(content);
          seenTokenOrError = true;
        }
        lastPermission = null;
      } else if (entry.kind === "tool_use") {
        // Tool call chip replay: payload is { tool, input }
        if (els.messages) {
          els.messages.insertAdjacentHTML("beforeend", renderToolStepChip(entry.payload?.tool, entry.payload?.input));
          seenTokenOrError = true;
        }
      } else if (entry.kind === "tool_result") {
        // Tool result replay: payload is { tool, result, is_error }
        if (els.messages) {
          els.messages.insertAdjacentHTML("beforeend", renderToolStepResult(
            entry.payload?.tool,
            entry.payload?.result,
            entry.payload?.is_error,
          ));
          seenTokenOrError = true;
        }
      }
    }

    // Re-surface the permission gate if the last unresolved entry was a
    // permission (no subsequent token/error consumed it).
    if (lastPermission) {
      const p = lastPermission.payload || {};
      appendPermission(els.messages, {
        permission_id: p.permission_id,
        tool_name: p.tool_name,
        args: p.args || {},
      });
    }

    if (seenTokenOrError || lastPermission) scrollToBottom();
  }

  // Connect WS after replay so live tokens append to the same streaming bubble
  // (ensureStreamingBubble reuses the existing .streaming element).
  connectWs(data.session.id);

  // Auto-load the Review Changes panel for write-mode sessions.
  if (data.session.forge_mode === "write") {
    resetReviewState();
    refreshReviewPanel();
  } else {
    resetReviewState();
    // Ensure any stale panel is cleared for read sessions.
    renderReviewPanel();
  }
}

function clearChat() {
  // v3 port: no empty-state block. The composer placeholder is the empty-state
  // UX. The scroll area just stays empty.
  if (els.messages) els.messages.innerHTML = "";
}

function renderChat(messages) {
  if (!els.messages) return;
  if (!messages || messages.length === 0) {
    els.messages.innerHTML = "";
    return;
  }
  els.messages.innerHTML = renderMessages(messages);
  scrollToBottom();
  window.hljs?.highlightAll?.();
  window.mermaid?.run?.({ querySelector: ".language-mermaid" }).catch?.(() => {});
}

function scrollToBottom() {
  if (!els.messages) return;
  els.messages.scrollTop = els.messages.scrollHeight;
}

function renderRail() {
  const list = $("dev-annotation-list");
  if (list) {
    list.innerHTML = renderAnnotations(state.annotations);
    list.dataset.empty = state.annotations.length === 0 ? "true" : "false";
  }
  const tools = $("dev-rail-tools");
  if (tools) tools.hidden = state.annotations.length === 0;
  // The rail toggle is only available in single-session mode. Parallel mode
  // replaces .dp-main wholesale, so the toggle stays hidden until exit.
  if (!getState("parallelMode")) {
    $("dev-rail-toggle")?.classList.remove("hidden");
  } else {
    $("dev-rail-toggle")?.classList.add("hidden");
  }
  renderWriteModeBadge();
  renderReviewPanel();
}

// ── Write-mode badge + toggle ─────────────────────────────────────────────────

function renderWriteModeBadge() {
  // Remove any existing badge/toggle so we re-render cleanly.
  $("dev-write-mode-badge")?.remove();
  $("dev-write-mode-toggle")?.remove();

  if (!state.activeSession) return;

  const isWrite = state.activeSession.forge_mode === "write";

  if (isWrite) {
    const badge = document.createElement("span");
    badge.id = "dev-write-mode-badge";
    badge.className = "dev-review-badge";
    badge.textContent = "WRITE MODE";
    els.headerActions?.insertBefore(badge, els.headerActions.firstChild);
  }

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.id = "dev-write-mode-toggle";
  toggle.className = "dp-icon-btn dev-write-mode-toggle-btn";
  toggle.title = isWrite ? "Switch to read mode" : "Switch to write mode";
  toggle.setAttribute("aria-label", toggle.title);
  toggle.innerHTML = isWrite
    ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`
    : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
  toggle.addEventListener("click", toggleForgeMode);
  els.headerActions?.insertBefore(toggle, els.headerActions.firstChild);
}

async function toggleForgeMode() {
  if (!state.activeSessionId) return;
  const current = state.activeSession?.forge_mode;
  const next = current === "write" ? "read" : "write";
  try {
    const updated = await request(`/sessions/${state.activeSessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ forge_mode: next }),
    });
    state.activeSession = { ...state.activeSession, ...updated };
    renderWriteModeBadge();
    renderReviewPanel();
    if (next === "write") refreshReviewPanel();
  } catch (err) {
    // Non-fatal: show inline, don't crash.
    showReviewError(`Could not switch forge mode: ${err.message}`);
  }
}

// ── Review Changes panel ──────────────────────────────────────────────────────

// Internal panel state (reset on session change).
const reviewState = {
  loading: false,
  error: null,
  status: null, // { exists, branch, ahead, dirty_files, commits }
  diff: null,   // { diff, truncated, branch, base }
  diffLoading: false,
  diffError: null,
};

function resetReviewState() {
  reviewState.loading = false;
  reviewState.error = null;
  reviewState.status = null;
  reviewState.diff = null;
  reviewState.diffLoading = false;
  reviewState.diffError = null;
}

function renderReviewPanel() {
  // The panel lives inside the right rail. We inject/replace a dedicated slot.
  const rail = document.querySelector(".dp-rail-body");
  if (!rail) return;

  let panel = $("dev-review-panel");
  if (!panel) {
    panel = document.createElement("section");
    panel.id = "dev-review-panel";
    panel.className = "dev-review-panel";
    rail.appendChild(panel);
  }

  const isWrite = state.activeSession?.forge_mode === "write";
  if (!isWrite) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;

  if (reviewState.loading) {
    panel.innerHTML = `
      <div class="dev-review-section-head">
        <span class="dev-review-section-title">Review Changes</span>
      </div>
      <div class="dev-review-status-row dev-review-loading">Loading...</div>
    `;
    return;
  }

  if (reviewState.error) {
    panel.innerHTML = `
      <div class="dev-review-section-head">
        <span class="dev-review-section-title">Review Changes</span>
        <button type="button" class="dev-icon-btn" id="dev-review-refresh" title="Refresh">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
        </button>
      </div>
      <div class="dev-review-error-note">${escapeHtml(reviewState.error)}</div>
    `;
    $("dev-review-refresh")?.addEventListener("click", refreshReviewPanel);
    return;
  }

  const st = reviewState.status;
  if (!st || !st.exists) {
    panel.innerHTML = `
      <div class="dev-review-section-head">
        <span class="dev-review-section-title">Review Changes</span>
        <button type="button" class="dev-icon-btn" id="dev-review-refresh" title="Refresh">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
        </button>
      </div>
      <div class="dev-review-empty">No changes yet &mdash; ask Ares to build something in this session.</div>
    `;
    $("dev-review-refresh")?.addEventListener("click", refreshReviewPanel);
    return;
  }

  // Has changes -- render status + diff + action buttons.
  const commits = Array.isArray(st.commits) ? st.commits : [];
  const commitRows = commits.map((c) =>
    `<div class="dev-review-commit-row"><span class="dev-review-commit-sha">${escapeHtml((c.sha || "").slice(0, 7))}</span><span class="dev-review-commit-subject">${escapeHtml(c.subject || "")}</span></div>`
  ).join("") || `<div class="dev-review-commit-row dev-review-empty-small">No commits</div>`;

  const diffBlock = buildDiffBlock();

  panel.innerHTML = `
    <div class="dev-review-section-head">
      <span class="dev-review-section-title">Review Changes</span>
      <button type="button" class="dev-icon-btn" id="dev-review-refresh" title="Refresh">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
      </button>
    </div>
    <div class="dev-review-status">
      <span class="dev-review-branch">${escapeHtml(st.branch || "")}</span>
      <span class="dev-review-meta">${st.ahead || 0} commit${st.ahead !== 1 ? "s" : ""} ahead &bull; ${st.dirty_files || 0} dirty file${st.dirty_files !== 1 ? "s" : ""}</span>
    </div>
    <div class="dev-review-commits">${commitRows}</div>
    ${diffBlock}
    <div id="dev-review-action-note" class="dev-review-action-note hidden"></div>
    <div class="dev-review-actions">
      <button type="button" class="dev-btn dev-review-discard-btn" id="dev-review-discard">Discard</button>
      <button type="button" class="dev-btn dev-review-merge-btn" id="dev-review-merge">Merge to main</button>
    </div>
  `;

  $("dev-review-refresh")?.addEventListener("click", refreshReviewPanel);
  $("dev-review-merge")?.addEventListener("click", () => handleMerge(st));
  $("dev-review-discard")?.addEventListener("click", () => handleDiscard(st));
  // Load diff lazily if not yet loaded.
  if (!reviewState.diff && !reviewState.diffLoading && !reviewState.diffError) {
    loadWorktreeDiff();
  }
}

function buildDiffBlock() {
  if (reviewState.diffLoading) {
    return `<details class="dev-review-diff-details">
      <summary class="dev-review-diff-summary">Diff <span class="dev-review-diff-note">loading...</span></summary>
    </details>`;
  }
  if (reviewState.diffError) {
    return `<details class="dev-review-diff-details">
      <summary class="dev-review-diff-summary">Diff</summary>
      <div class="dev-review-error-note">${escapeHtml(reviewState.diffError)}</div>
    </details>`;
  }
  if (!reviewState.diff) {
    return `<details class="dev-review-diff-details">
      <summary class="dev-review-diff-summary">Diff</summary>
    </details>`;
  }
  const truncNote = reviewState.diff.truncated
    ? `<span class="dev-review-diff-note">diff truncated</span>`
    : "";
  const diffHtml = escapeHtml(reviewState.diff.diff || "");
  return `<details class="dev-review-diff-details">
    <summary class="dev-review-diff-summary">Diff ${truncNote}</summary>
    <pre class="dev-diff">${diffHtml}</pre>
  </details>`;
}

function showReviewError(msg) {
  const note = $("dev-review-action-note");
  if (!note) return;
  note.textContent = msg;
  note.className = "dev-review-action-note dev-review-error-note";
  note.classList.remove("hidden");
}

function showReviewSuccess(msg) {
  const note = $("dev-review-action-note");
  if (!note) return;
  note.textContent = msg;
  note.className = "dev-review-action-note dev-review-success-note";
  note.classList.remove("hidden");
}

async function refreshReviewPanel() {
  if (!state.activeSessionId || state.activeSession?.forge_mode !== "write") return;
  resetReviewState();
  reviewState.loading = true;
  renderReviewPanel();
  await loadWorktreeStatus();
}

async function loadWorktreeStatus() {
  if (!state.activeSessionId) return;
  try {
    const data = await fetch(`${API}/sessions/${state.activeSessionId}/worktree/status`)
      .then((r) => r.json());
    reviewState.status = data;
    reviewState.loading = false;
    reviewState.error = null;
  } catch (err) {
    reviewState.loading = false;
    reviewState.error = `Could not load worktree status: ${err.message}`;
  }
  renderReviewPanel();
}

async function loadWorktreeDiff() {
  if (!state.activeSessionId) return;
  reviewState.diffLoading = true;
  reviewState.diffError = null;
  renderReviewPanel();
  try {
    const data = await fetch(`${API}/sessions/${state.activeSessionId}/worktree/diff`)
      .then((r) => r.json());
    reviewState.diff = data;
    reviewState.diffLoading = false;
  } catch (err) {
    reviewState.diffLoading = false;
    reviewState.diffError = `Could not load diff: ${err.message}`;
  }
  renderReviewPanel();
}

async function handleMerge(st) {
  const branch = st.branch || "worktree branch";
  const base = (reviewState.diff?.base) || "main";
  if (!window.confirm(`Merge branch "${branch}" into ${base}? This updates your real project.`)) return;

  const mergeBtn = $("dev-review-merge");
  if (mergeBtn) mergeBtn.disabled = true;

  try {
    const res = await fetch(`${API}/sessions/${state.activeSessionId}/worktree/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      // 409 or other error -- show detail without throwing.
      const detail = data.detail || data.error || `Merge failed (HTTP ${res.status})`;
      showReviewError(`Cannot merge: ${detail}`);
      if (mergeBtn) mergeBtn.disabled = false;
      return;
    }
    showReviewSuccess(`Merged into ${data.into || base}.`);
    // Refresh status -- worktree is gone after a successful merge.
    await loadWorktreeStatus();
  } catch (err) {
    showReviewError(`Merge error: ${err.message}`);
    if (mergeBtn) mergeBtn.disabled = false;
  }
}

async function handleDiscard(st) {
  const branch = st.branch || "worktree branch";
  if (!window.confirm(`Discard branch "${branch}" and all its changes? This cannot be undone.`)) return;

  const discardBtn = $("dev-review-discard");
  if (discardBtn) discardBtn.disabled = true;

  try {
    const res = await fetch(`${API}/sessions/${state.activeSessionId}/worktree`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const detail = data.detail || data.error || `Discard failed (HTTP ${res.status})`;
      showReviewError(`Cannot discard: ${detail}`);
      if (discardBtn) discardBtn.disabled = false;
      return;
    }
    // Refresh status -- worktree is gone after discard.
    resetReviewState();
    await loadWorktreeStatus();
  } catch (err) {
    showReviewError(`Discard error: ${err.message}`);
    if (discardBtn) discardBtn.disabled = false;
  }
}

function applyRailState() {
  const toggle = $("dev-rail-toggle");
  if (state.railOpen) {
    els.dpShell?.classList.add("rail-open");
    toggle?.classList.add("active");
  } else {
    els.dpShell?.classList.remove("rail-open");
    toggle?.classList.remove("active");
  }
  applyRailWidth();
}

function applyRailWidth() {
  if (!els.dpShell) return;
  const w = Math.min(railMax(), Math.max(RAIL_MIN, state.railWidth || RAIL_DEFAULT));
  els.dpShell.style.setProperty("--dp-rail-width", `${w}px`);
}

function bindRailResize() {
  const handle = $("dev-rail-resize");
  if (!handle || handle.dataset.bound === "1") return;
  handle.dataset.bound = "1";
  let startX = 0;
  let startW = state.railWidth;
  const onMove = (event) => {
    const dx = startX - event.clientX; // drag left = wider rail
    const next = Math.min(railMax(), Math.max(RAIL_MIN, startW + dx));
    state.railWidth = next;
    applyRailWidth();
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    document.body.classList.remove("dp-rail-resizing");
    handle.classList.remove("is-dragging");
    try { localStorage.setItem(STORAGE.railWidth, String(state.railWidth)); } catch {}
  };
  handle.addEventListener("mousedown", (event) => {
    event.preventDefault();
    startX = event.clientX;
    startW = state.railWidth;
    handle.classList.add("is-dragging");
    document.body.classList.add("dp-rail-resizing");
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

function setRailOpen(open) {
  state.railOpen = open;
  localStorage.setItem(STORAGE.railOpen, String(open));
  applyRailState();
}

function connectWs(sessionId) {
  if (state.ws) state.ws.close();
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  state.ws = new WebSocket(`${protocol}//${location.host}/ws/dev-projects/${sessionId}`);
  state.ws.onmessage = (event) => handleWs(JSON.parse(event.data));
}

// Render a compact tool-step chip for a tool call.
function renderToolStepChip(tool, input) {
  let label = escapeHtml(tool || "tool");
  // Summarize the most useful part of input for common Ares tools.
  if (input) {
    const path = input.path || input.file_path || input.filepath || "";
    if (path) {
      label += ` <span class="dev-tool-step-arg">${escapeHtml(String(path))}</span>`;
    } else if (tool === "git_diff" && input.args) {
      const arg = Array.isArray(input.args) ? input.args.join(" ") : String(input.args);
      if (arg) label += ` <span class="dev-tool-step-arg">${escapeHtml(arg.slice(0, 60))}</span>`;
    }
  }
  return `<div class="dev-tool-step dev-tool-step-call">${label}</div>`;
}

// Render a collapsible tool result block, appended after its call chip.
function renderToolStepResult(tool, result, isError) {
  const safeResult = escapeHtml(typeof result === "string" ? result : JSON.stringify(result));
  const errorCls = isError ? " dev-tool-step-error" : "";
  return `<details class="dev-tool-step dev-tool-step-result${errorCls}">
    <summary class="dev-tool-step-summary">${isError ? "error" : "result"}</summary>
    <pre class="dev-tool-step-pre">${safeResult}</pre>
  </details>`;
}

// Render text blocks from an Ares-path content array as an assistant bubble.
function renderAresTextBlocks(contentArray) {
  const texts = contentArray
    .filter((block) => block.type === "text" && block.text)
    .map((block) => escapeHtml(block.text))
    .join("\n");
  if (!texts) return;
  // Finalize any open streaming bubble first (Ares messages arrive as
  // complete per-iteration chunks, not token streams, so we start fresh).
  finalizeStreamingBubble();
  els.messages?.insertAdjacentHTML("beforeend", `
    <article class="dev-message dev-message-assistant">
      <div class="dev-message-role">assistant</div>
      <div class="dev-message-body"><div class="dev-message-text">${texts}</div></div>
    </article>
  `);
}

// Seal the current streaming bubble (remove the .streaming class so a
// subsequent ensureStreamingBubble() call creates a fresh one).
function finalizeStreamingBubble() {
  els.messages?.querySelector(".dev-message.streaming")?.classList.remove("streaming");
}

function handleWs(event) {
  if (event.type === "dev_projects.message" || event.type === "dev_projects.message_complete") {
    if (event.message) {
      // Legacy path: wrapped as { message: { id, session_id, role, content, created_at } }
      els.messages?.insertAdjacentHTML("beforeend", renderMessages([event.message]));
      scrollToBottom();
      window.hljs?.highlightAll?.();
    } else if (Array.isArray(event.content)) {
      // Ares path: { type, role, content: [...] } — no message wrapper, no id.
      renderAresTextBlocks(event.content);
      scrollToBottom();
    }
    // After a turn completes, refresh the Review panel so new commits from
    // Ares appear immediately (no backend worktree_updated event needed).
    if (event.type === "dev_projects.message_complete" && state.activeSession?.forge_mode === "write") {
      resetReviewState();
      refreshReviewPanel();
    }
  } else if (event.type === "dev_projects.tool_step") {
    if (!els.messages) return;
    if (event.is_result) {
      // Tool result: collapsible block after the preceding chip.
      els.messages.insertAdjacentHTML("beforeend", renderToolStepResult(event.tool, event.result, event.is_error));
    } else {
      // Tool call chip.
      els.messages.insertAdjacentHTML("beforeend", renderToolStepChip(event.tool, event.input));
    }
    scrollToBottom();
  } else if (event.type === "dev_projects.token") {
    ensureStreamingBubble().querySelector(".dev-message-text").textContent += event.token || "";
    scrollToBottom();
  } else if (event.type === "dev_projects.permission_required") {
    appendPermission(els.messages, event);
  } else if (event.type === "dev_projects.annotation") {
    state.annotations.unshift(event.annotation);
    // Auto-open the rail when an annotation is created (per spec: only on
    // explicit user actions or annotation creation, not every render).
    if (!getState("parallelMode")) {
      setRailOpen(true);
    }
    renderRail();
  } else if (event.type === "dev_projects.session_updated" && event.session) {
    upsertSession(event.session);
  }
}

function upsertSession(session) {
  const sessions = state.sessionsByProject.get(Number(session.project_id)) || [];
  const idx = sessions.findIndex((item) => Number(item.id) === Number(session.id));
  if (idx >= 0) sessions[idx] = session;
  else sessions.unshift(session);
  state.sessionsByProject.set(Number(session.project_id), sessions);
  renderProjects();
}

function ensureStreamingBubble() {
  let bubble = els.messages?.querySelector(".dev-message.streaming");
  if (!bubble) {
    els.messages?.insertAdjacentHTML("beforeend", `
      <article class="dev-message dev-message-assistant streaming">
        <div class="dev-message-role">assistant</div>
        <div class="dev-message-body"><div class="dev-message-text"></div></div>
      </article>
    `);
    bubble = els.messages.querySelector(".dev-message.streaming");
  }
  return bubble;
}

async function sendCurrent() {
  if (!state.activeSessionId) {
    if (state.activeProjectId) await createSession(state.activeProjectId);
    if (!state.activeSessionId) return;
  }
  const text = els.input?.value.trim();
  if (!text) return;
  els.input.value = "";
  els.input.dispatchEvent(new Event("input"));
  await request(`/sessions/${state.activeSessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ text, images: [] }),
  });
}

async function createSession(projectId = state.activeProjectId) {
  if (!projectId) return;
  const project = state.projects.find((item) => Number(item.id) === Number(projectId));
  const defaults = project?.metadata || {};
  const provider = state.activeSession?.provider || defaults.default_provider || "claude-code";
  const model = state.activeSession?.model || defaults.default_model || null;
  const session = await request(`/projects/${projectId}/sessions`, {
    method: "POST",
    body: JSON.stringify({ provider, model, title: "New session" }),
  });
  state.activeProjectId = Number(projectId);
  state.expandedProjectIds.add(Number(projectId));
  await refreshProjectSessions(projectId);
  await loadSession(session.id);
}

async function openProjectModal() {
  state.modal = {
    open: true,
    currentPath: "",
    parentPath: null,
    entries: [],
    name: "",
    error: "",
    loading: true,
    focusedIndex: 0,
    dirtyName: false,
    manualFallback: false,
    manualPath: "",
    focusAfterRender: "dev-project-name-input",
  };
  renderProjectModalRoot();
  await browseProjectFolder("~");
}

function closeProjectModal(force = false) {
  if (!force && state.modal.dirtyName && state.modal.name?.trim() && !window.confirm("Close without creating this project?")) return;
  state.modal.open = false;
  $("dev-project-modal-root").innerHTML = "";
}

function renderProjectModalRoot() {
  const root = $("dev-project-modal-root");
  if (!root || !state.modal.open) return;
  const active = document.activeElement?.id;
  const activeFolderIndex = document.activeElement?.dataset?.folderIndex;
  root.innerHTML = renderProjectModal(state.modal);
  if (state.modal.focusAfterRender) {
    if (state.modal.focusAfterRender === "folder") {
      root.querySelector(`[data-folder-index="${state.modal.focusedIndex}"]`)?.focus();
    } else {
      $(state.modal.focusAfterRender)?.focus();
    }
    state.modal.focusAfterRender = "";
  } else if (active) {
    $(active)?.focus();
  } else if (activeFolderIndex !== undefined) {
    root.querySelector(`[data-folder-index="${activeFolderIndex}"]`)?.focus();
  }
}

function folderNameFromPath(path) {
  return String(path || "").split(/[\\/]/).filter(Boolean).pop() || "";
}

function syncModalNameFromPath(path) {
  if (!state.modal.dirtyName) state.modal.name = folderNameFromPath(path);
}

async function browseProjectFolder(path = "~") {
  state.modal.loading = true;
  state.modal.error = "";
  renderProjectModalRoot();
  try {
    const data = await request(`/browse?path=${encodeURIComponent(path)}`);
    state.modal.currentPath = data.resolved_path || "";
    state.modal.parentPath = data.parent_path || null;
    state.modal.entries = data.entries || [];
    state.modal.focusedIndex = Math.min(state.modal.focusedIndex || 0, Math.max(0, state.modal.entries.length - 1));
    state.modal.manualFallback = false;
    state.modal.manualPath = "";
    syncModalNameFromPath(state.modal.currentPath);
  } catch (err) {
    state.modal.error = err.message || "Folder could not be opened.";
    if (!state.modal.currentPath) {
      state.modal.manualFallback = true;
      state.modal.manualPath = path === "~" ? "" : path;
      state.modal.focusAfterRender = "dev-project-manual-path-input";
    }
  } finally {
    state.modal.loading = false;
    renderProjectModalRoot();
  }
}

function focusFolder(index) {
  const max = Math.max(0, state.modal.entries.length - 1);
  state.modal.focusedIndex = Math.max(0, Math.min(index, max));
  state.modal.focusAfterRender = "folder";
  renderProjectModalRoot();
}

async function createProject() {
  const path = state.modal.manualFallback
    ? $("dev-project-manual-path-input")?.value.trim()
    : state.modal.currentPath;
  const name = $("dev-project-name-input")?.value.trim() || folderNameFromPath(path);
  if (!path) {
    state.modal.error = "Choose a project folder first.";
    renderProjectModalRoot();
    return;
  }
  state.modal.name = name;
  state.modal.manualPath = state.modal.manualFallback ? path : "";
  try {
    const project = await request("/projects", {
      method: "POST",
      body: JSON.stringify({ name, path }),
    });
    state.projects.unshift(project);
    state.sessionsByProject.set(Number(project.id), []);
    state.activeProjectId = project.id;
    state.activeSessionId = null;
    state.expandedProjectIds.add(Number(project.id));
    closeProjectModal(true);
    renderProjects();
    document.querySelector(`[data-new-session="${project.id}"]`)?.focus();
  } catch (err) {
    state.modal.error = err.message;
    renderProjectModalRoot();
  }
}

async function forkAt(messageId) {
  if (!state.activeSessionId) return;
  const session = await request(`/sessions/${state.activeSessionId}/fork`, {
    method: "POST",
    body: JSON.stringify({ at_message_id: Number(messageId) }),
  });
  await refreshProjectSessions(session.project_id);
  await loadSession(session.id);
}

async function decide(permissionId, approved, trust = false) {
  await request(`/sessions/${state.activeSessionId}/permissions/${permissionId}/${approved ? "approve" : "deny"}`, {
    method: "POST",
    body: JSON.stringify({ trust_for_session: trust }),
  });
}

async function sendAnnotation() {
  if (!state.activeSessionId) return;
  const url = $("dev-rail-url")?.value.trim() || "";
  const note = $("dev-note-input")?.value.trim() || "";
  if (!note) return;
  await request(`/sessions/${state.activeSessionId}/annotations`, {
    method: "POST",
    body: JSON.stringify({ url, note }),
  });
  els.input.value = `Re: ${url || "preview"}: ${note}`;
  els.input.focus();
  $("dev-note-input").value = "";
}

function openSortMenu(anchor) {
  openContextMenu(anchor, [
    { label: state.sortMode === "recent" ? "Sort by name" : "Sort by recent", action: () => {
      state.sortMode = state.sortMode === "recent" ? "name" : "recent";
      renderProjects();
    } },
    { label: state.showArchived ? "Hide archived" : "Show archived", action: () => {
      state.showArchived = !state.showArchived;
      renderProjects();
    } },
  ]);
}

function openProjectMenu(anchor, projectId) {
  const project = state.projects.find((item) => Number(item.id) === Number(projectId));
  if (!project) return;
  openContextMenu(anchor, [
    { label: "Rename", action: async () => renameProject(project) },
    { label: "New session", action: () => createSession(project.id) },
    { label: "Open in Finder", action: () => request(`/projects/${project.id}/open`, { method: "POST", body: "{}" }) },
    { label: project.archived_at ? "Restore project" : "Archive project", action: async () => {
      const updated = await request(`/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({ archived: !project.archived_at }),
      });
      Object.assign(project, updated);
      renderProjects();
    } },
    { label: "Delete permanently", danger: true, action: async () => {
      if (!window.confirm(`Permanently delete "${project.name}" from Artemis? This will remove its sessions from Artemis, but not files on disk.`)) return;
      await request(`/projects/${project.id}/permanent`, { method: "DELETE" });
      state.projects = state.projects.filter((item) => Number(item.id) !== Number(project.id));
      state.sessionsByProject.delete(Number(project.id));
      restoreActivePointers();
      renderProjects();
    } },
  ]);
}

function openSessionMenu(anchor, sessionId) {
  const session = [...state.sessionsByProject.values()].flat().find((item) => Number(item.id) === Number(sessionId));
  if (!session) return;
  openContextMenu(anchor, [
    { label: "Rename", action: async () => renameSession(session) },
    { label: session.pinned ? "Unpin" : "Pin", action: async () => {
      await request(`/sessions/${session.id}`, { method: "PATCH", body: JSON.stringify({ pinned: !session.pinned }) });
      await refreshProjectSessions(session.project_id);
    } },
    { label: session.archived_at ? "Restore session" : "Archive session", action: async () => {
      await request(`/sessions/${session.id}`, { method: "PATCH", body: JSON.stringify({ archived: !session.archived_at }) });
      await refreshProjectSessions(session.project_id);
    } },
    { label: "Delete permanently", danger: true, action: async () => {
      if (!window.confirm(`Permanently delete "${session.title || "Untitled"}" from Artemis?`)) return;
      await request(`/sessions/${session.id}/permanent`, { method: "DELETE" });
      if (Number(state.activeSessionId) === Number(session.id)) {
        state.activeSessionId = null;
        localStorage.removeItem(STORAGE.activeSession);
        clearChat();
      }
      await refreshProjectSessions(session.project_id);
    } },
  ]);
}

async function renameProject(project) {
  const name = await askText("Rename project", project.name);
  if (!name) return;
  const updated = await request(`/projects/${project.id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
  Object.assign(project, updated);
  renderProjects();
}

async function renameSession(session) {
  const title = await askText("Rename session", session.title || "Untitled");
  if (!title) return;
  await request(`/sessions/${session.id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  await refreshProjectSessions(session.project_id);
}

function openContextMenu(anchor, items) {
  const root = $("dev-context-root");
  const rect = anchor.getBoundingClientRect();
  root.innerHTML = `
    <div class="dev-menu-backdrop" data-close-menu></div>
    <div class="dev-context-menu" style="left:${Math.min(rect.left, window.innerWidth - 220)}px;top:${rect.bottom + 4}px">
      ${items.map((item, index) => `<button type="button" class="${item.danger ? "danger" : ""}" data-menu-index="${index}">${escapeHtml(item.label)}</button>`).join("")}
    </div>
  `;
  root.querySelectorAll("[data-menu-index]").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = items[Number(button.dataset.menuIndex)];
      root.innerHTML = "";
      await item.action();
    });
  });
}

function askText(title, value) {
  return new Promise((resolve) => {
    const root = $("dev-context-root");
    root.innerHTML = `
      <div class="dev-modal-backdrop">
        <form class="dev-small-dialog">
          <h2>${escapeHtml(title)}</h2>
          <input class="dev-text-input" id="dev-text-dialog-input" value="${escapeHtml(value)}">
          <div class="dev-modal-foot">
            <button type="button" class="dev-btn" data-text-cancel>Cancel</button>
            <button type="submit" class="dev-btn primary">Save</button>
          </div>
        </form>
      </div>
    `;
    const input = $("dev-text-dialog-input");
    input?.focus();
    input?.select();
    root.querySelector("[data-text-cancel]")?.addEventListener("click", () => {
      root.innerHTML = "";
      resolve(null);
    });
    root.querySelector("form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const next = input?.value.trim();
      root.innerHTML = "";
      resolve(next || null);
    });
  });
}

function openProjectSwitcher(anchor) {
  const projects = state.projects.filter((p) => !p.archived_at);
  if (!projects.length) {
    openContextMenu(anchor, [{ label: "New project…", action: openProjectModal }]);
    return;
  }
  const root = $("dev-context-root");
  const rect = anchor.getBoundingClientRect();
  const width = Math.min(360, window.innerWidth - 24);
  const left = Math.min(rect.left, window.innerWidth - width - 12);
  const pickProject = async (project) => {
    state.activeProjectId = Number(project.id);
    state.expandedProjectIds.add(Number(project.id));
    const sessions = (state.sessionsByProject.get(Number(project.id)) || []).filter((s) => !s.archived_at);
    const first = sessions[0];
    if (first) await loadSession(first.id);
    else {
      state.activeSessionId = null;
      localStorage.removeItem(STORAGE.activeSession);
      renderProjects();
      clearChat();
    }
  };
  const folderIcon = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/></svg>`;
  const rows = projects.map((project, index) => {
    const active = Number(project.id) === Number(state.activeProjectId);
    return `
      <button type="button" class="dp-proj-row${active ? " is-active" : ""}" data-proj-index="${index}">
        <span class="dp-proj-row-icon" aria-hidden="true">${folderIcon}</span>
        <span class="dp-proj-row-text">
          <span class="dp-proj-row-name">${escapeHtml(project.name || "Untitled")}</span>
          <span class="dp-proj-row-path">${escapeHtml(project.path || "")}</span>
        </span>
        ${active ? '<span class="dp-proj-row-dot" aria-label="active"></span>' : ""}
      </button>`;
  }).join("");
  root.innerHTML = `
    <div class="dev-menu-backdrop" data-close-menu></div>
    <div class="dev-context-menu dp-proj-switcher" style="left:${left}px;top:${rect.bottom + 4}px;width:${width}px">
      <div class="dp-proj-rows">${rows}</div>
      <button type="button" class="dp-proj-new" data-proj-new>+ New project…</button>
    </div>
  `;
  root.querySelector("[data-close-menu]")?.addEventListener("click", () => { root.innerHTML = ""; });
  root.querySelectorAll("[data-proj-index]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const project = projects[Number(btn.dataset.projIndex)];
      root.innerHTML = "";
      await pickProject(project);
    });
  });
  root.querySelector("[data-proj-new]")?.addEventListener("click", () => {
    root.innerHTML = "";
    openProjectModal();
  });
}

function bindEvents() {
  bindComposer(els.input, els.send, sendCurrent);

  // Project breadcrumb opens the v3 switcher. Use capture + stopImmediatePropagation
  // so the legacy projects.js click handler on the same element can't double-fire.
  els.projectTitleBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    openProjectSwitcher(els.projectTitleBtn);
  }, true);

  document.addEventListener("click", async (event) => {
    const target = event.target;
    if (target.closest?.("[data-close-menu]")) $("dev-context-root").innerHTML = "";

    const action = target.closest?.("[data-dev-action]")?.dataset.devAction;
    if (action === "open-project-modal") return openProjectModal();
    if (action === "collapse-all") {
      state.expandedProjectIds.clear();
      return renderProjects();
    }
    if (action === "sort-menu") return openSortMenu(target.closest("[data-dev-action]"));

    const toggle = target.closest?.("[data-project-toggle]");
    if (toggle && !target.closest("button")) {
      const projectId = Number(toggle.dataset.projectToggle);
      state.activeProjectId = projectId;
      if (state.expandedProjectIds.has(projectId)) state.expandedProjectIds.delete(projectId);
      else state.expandedProjectIds.add(projectId);
      return renderProjects();
    }

    const newSession = target.closest?.("[data-new-session]");
    if (newSession) return createSession(Number(newSession.dataset.newSession));

    const showMore = target.closest?.("[data-show-more]");
    if (showMore) {
      const projectId = Number(showMore.dataset.showMore);
      state.visibleCounts.set(projectId, (state.visibleCounts.get(projectId) || 5) + 50);
      return renderProjects();
    }

    const projectMenu = target.closest?.("[data-project-menu]");
    if (projectMenu) return openProjectMenu(projectMenu, Number(projectMenu.dataset.projectMenu));

    const sessionMenu = target.closest?.("[data-session-menu]");
    if (sessionMenu) {
      event.preventDefault();
      event.stopPropagation();
      return openSessionMenu(sessionMenu, Number(sessionMenu.dataset.sessionMenu));
    }

    const sessionRow = target.closest?.(".dev-session-row");
    if (sessionRow) return loadSession(Number(sessionRow.dataset.sessionId));

    const fork = target.closest?.("[data-fork-at]");
    if (fork) await forkAt(fork.dataset.forkAt);
    const approve = target.closest?.("[data-approve]");
    if (approve) await decide(approve.dataset.approve, true);
    const deny = target.closest?.("[data-deny]");
    if (deny) await decide(deny.dataset.deny, false);
    const trust = target.closest?.("[data-trust]");
    if (trust) await decide(trust.dataset.trust, true, true);
  });

  document.addEventListener("contextmenu", (event) => {
    const sessionRow = event.target.closest?.(".dev-session-row");
    const project = event.target.closest?.(".dev-project-folder-row");
    if (sessionRow) {
      event.preventDefault();
      openSessionMenu(sessionRow, Number(sessionRow.dataset.sessionId));
    } else if (project) {
      event.preventDefault();
      openProjectMenu(project, Number(project.dataset.projectToggle));
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target?.id === "dev-project-name-input") {
      state.modal.name = event.target.value;
      state.modal.dirtyName = true;
    } else if (event.target?.id === "dev-project-manual-path-input") {
      state.modal.manualPath = event.target.value;
      syncModalNameFromPath(event.target.value);
      renderProjectModalRoot();
    }
  });

  document.addEventListener("click", async (event) => {
    const closeTarget = event.target.closest?.("[data-close-project-modal]");
    if (closeTarget) {
      const clickedBackdrop = closeTarget.classList.contains("dev-modal-backdrop") && event.target === closeTarget;
      const clickedCloseButton = closeTarget.tagName === "BUTTON";
      if (clickedBackdrop || clickedCloseButton) closeProjectModal();
    }
    if (event.target?.id === "dev-create-project-confirm") await createProject();
    const folder = event.target.closest?.("[data-folder-path]");
    if (folder) await browseProjectFolder(folder.dataset.folderPath);
    const up = event.target.closest?.("[data-folder-up]");
    if (up && state.modal.parentPath) await browseProjectFolder(state.modal.parentPath);
  });

  document.addEventListener("keydown", async (event) => {
    if (!state.modal.open) return;
    const root = $("dev-project-modal-root");
    const modal = root?.querySelector(".dev-modal");
    if (!modal) return;

    if (event.key === "Escape") {
      event.preventDefault();
      closeProjectModal();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      await createProject();
      return;
    }
    if (!state.modal.manualFallback && event.key === "ArrowDown") {
      event.preventDefault();
      focusFolder(state.modal.focusedIndex + 1);
      return;
    }
    if (!state.modal.manualFallback && event.key === "ArrowUp") {
      event.preventDefault();
      focusFolder(state.modal.focusedIndex - 1);
      return;
    }
    if (!state.modal.manualFallback && event.key === "Enter" && document.activeElement?.dataset?.folderPath) {
      event.preventDefault();
      await browseProjectFolder(document.activeElement.dataset.folderPath);
      return;
    }
    if (event.key === "Tab") {
      const focusables = [...modal.querySelectorAll("button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])")];
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  $("dev-rail-toggle")?.addEventListener("click", () => {
    if (getState("parallelMode")) return; // gate: single-session only
    setRailOpen(!state.railOpen);
  });
  $("dev-rail-close")?.addEventListener("click", () => setRailOpen(false));

  // Model picker — open/close menu and apply selection
  els.modelBtn?.addEventListener("click", (event) => {
    event.stopPropagation();
    if (els.modelMenu?.classList.contains("hidden")) openModelMenu();
    else closeModelMenu();
  });
  els.modelMenu?.addEventListener("click", async (event) => {
    const item = event.target.closest?.("[data-provider]");
    if (!item) return;
    await selectModel(item.dataset.provider, item.dataset.model || null);
  });
  document.addEventListener("click", (event) => {
    if (!els.modelMenu || els.modelMenu.classList.contains("hidden")) return;
    if (els.modelMenu.contains(event.target) || els.modelBtn?.contains(event.target)) return;
    closeModelMenu();
  });

  // Session Config (cog) changes provider via #source-select; keep composer
  // picker in sync — refresh model list + label to match the new provider.
  $("source-select")?.addEventListener("change", (event) => {
    const newProvider = event.target.value;
    if (state.activeSession) state.activeSession = { ...state.activeSession, provider: newProvider, model: "" };
    renderModelMenu();
    syncModelButtonLabel();
  });
  $("model-select")?.addEventListener("change", (event) => {
    if (state.activeSession) state.activeSession = { ...state.activeSession, model: event.target.value };
    syncModelButtonLabel();
  });

  $("dev-rail-url")?.addEventListener("change", (event) => { $("dev-preview-frame").src = event.target.value; });
  $("dev-note-send")?.addEventListener("click", sendAnnotation);
  $("dev-target-pick")?.addEventListener("click", () => $("dev-target-overlay")?.classList.remove("hidden"));
  $("dev-target-overlay")?.addEventListener("click", pickPreviewTarget);
  $("dev-annotation-list")?.addEventListener("click", (event) => {
    const item = event.target.closest(".dev-annotation-item");
    if (!item) return;
    $("dev-rail-url").value = item.dataset.url || "";
    $("dev-preview-frame").src = item.dataset.url || "about:blank";
    $("dev-note-input").value = item.dataset.note || "";
  });
}

function pickPreviewTarget(event) {
  const overlay = $("dev-target-overlay");
  const frame = $("dev-preview-frame");
  const url = $("dev-rail-url")?.value.trim() || frame?.src || "preview";
  overlay?.classList.add("hidden");
  if (!frame) return;

  const rect = frame.getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  let target = `visual target at ${Math.round(x * 100)}%, ${Math.round(y * 100)}%`;

  try {
    const doc = frame.contentDocument;
    const element = doc?.elementFromPoint(x * rect.width, y * rect.height);
    if (element) {
      const label = element.getAttribute("aria-label") || element.textContent?.trim().slice(0, 60);
      const id = element.id ? `#${element.id}` : "";
      const classes = element.className && typeof element.className === "string"
        ? `.${element.className.trim().split(/\s+/).slice(0, 2).join(".")}`
        : "";
      target = `${element.tagName.toLowerCase()}${id}${classes}${label ? ` (${label})` : ""}`;
    }
  } catch {
    // Cross-origin previews cannot expose DOM details; the visual coordinate is still useful.
  }

  els.input.value = `Re: ${url} - ${target}: `;
  els.input.focus();
  els.input.dispatchEvent(new Event("input"));
}

export async function bootDevProjects() {
  if (!ensureShell()) return;
  applyRailWidth();
  bindRailResize();
  await loadModels();
  bindEvents();
  // Re-sync rail toggle visibility on parallel-mode transitions. In parallel
  // mode the rail can't be opened (single-session-only per spec).
  onStore("parallelMode", (isParallel) => {
    const toggle = $("dev-rail-toggle");
    if (isParallel) {
      toggle?.classList.add("hidden");
      // Close the rail since dp-main was replaced and the toggle is hidden.
      els.dpShell?.classList.remove("rail-open");
    } else if (state.activeSessionId) {
      toggle?.classList.remove("hidden");
      applyRailState();
    }
  });
  await loadProjects();
}

bootDevProjects().catch((err) => {
  console.error("Dev Projects failed to boot", err);
});
