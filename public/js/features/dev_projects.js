import { railMarkup, renderAnnotations } from "../components/dev-projects-annotation-rail.js";
import { appendPermission, renderMessages } from "../components/dev-projects-chat.js";
import { bindComposer } from "../components/dev-projects-composer.js";
import { renderProjectModal } from "../components/dev-projects-project-modal.js";
import { renderDevProjectsRail } from "../components/dev-projects-sidebar.js";
import { setState } from "../core/store.js";
import { escapeHtml } from "../core/utils.js";
import { enterParallelMode, exitParallelMode } from "../ui/parallel.js";

const API = "/api/dev-projects";
const STORAGE = {
  activeSession: "artemis.devProjects.activeSession",
  expanded: "artemis.devProjects.expandedProjects",
  showArchived: "artemis.devProjects.showArchived",
  sortMode: "artemis.devProjects.sortMode",
};

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
  suppressPickerSave: false,
  showArchived: localStorage.getItem(STORAGE.showArchived) === "true",
  sortMode: localStorage.getItem(STORAGE.sortMode) || "recent",
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
  els.empty = $("dp-chat-empty");
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

  const modelPicker = document.createElement("div");
  modelPicker.className = "dev-model-picker";
  modelPicker.innerHTML = `
    <select id="dev-provider-select" title="Provider"></select>
    <select id="dev-model-select" title="Model"></select>
    <button class="dp-icon-btn" id="dev-rail-toggle" title="Annotations" aria-label="Annotations">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a4 4 0 0 1-4 4H7l-4 4V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>
    </button>
  `;
  els.headerActions?.prepend(modelPicker);

  els.dpShell.insertAdjacentHTML("beforeend", railMarkup());
  document.querySelector(".chat-area")?.classList.add("dev-projects-ready");
  return true;
}

async function loadModels() {
  const data = await fetch("/api/floating-artemis/models").then((res) => res.json());
  const providerSelect = $("dev-provider-select");
  const modelSelect = $("dev-model-select");
  if (!providerSelect || !modelSelect) return;
  const providers = [...(data.providers || [])];
  if (!providers.some((provider) => provider.id === "openai")) {
    providers.push({ id: "openai", name: "OpenAI API", models: [{ id: "", label: "Default" }] });
  }
  providerSelect.innerHTML = providers
    .filter((p) => ["claude-code", "codex", "anthropic", "openai", "gemini", "lm-studio"].includes(p.id))
    .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name || p.id)}</option>`)
    .join("");
  const syncModels = () => {
    const provider = providers.find((p) => p.id === providerSelect.value);
    modelSelect.innerHTML = (provider?.models || [{ id: "", label: "Default" }])
      .map((m) => `<option value="${escapeHtml(m.id === "default" ? "" : m.id)}">${escapeHtml(m.label || m.id || "Default")}</option>`)
      .join("");
  };
  providerSelect.addEventListener("change", async () => {
    syncModels();
    await saveSessionModel();
  });
  modelSelect.addEventListener("change", saveSessionModel);
  syncModels();
}

async function saveSessionModel() {
  if (state.suppressPickerSave || !state.activeSessionId) return;
  const provider = $("dev-provider-select")?.value || "claude-code";
  const model = $("dev-model-select")?.value || null;
  state.activeSession = await request(`/sessions/${state.activeSessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ provider, model }),
  });
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
  else renderEmptyChat();
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
  syncPickerFromSession();
  connectWs(data.session.id);
}

function renderEmptyChat() {
  els.messages?.classList.add("hidden");
  els.empty?.classList.remove("hidden");
}

function renderChat(messages) {
  els.empty?.classList.add("hidden");
  els.messages?.classList.remove("hidden");
  if (els.messages) {
    els.messages.innerHTML = renderMessages(messages);
    els.messages.scrollTop = els.messages.scrollHeight;
    window.hljs?.highlightAll?.();
    window.mermaid?.run?.({ querySelector: ".language-mermaid" }).catch?.(() => {});
  }
}

function renderRail() {
  const list = $("dev-annotation-list");
  if (list) list.innerHTML = renderAnnotations(state.annotations);
}

function syncPickerFromSession() {
  const providerSelect = $("dev-provider-select");
  const modelSelect = $("dev-model-select");
  if (!state.activeSession || !providerSelect || !modelSelect) return;
  state.suppressPickerSave = true;
  providerSelect.value = state.activeSession.provider || "claude-code";
  providerSelect.dispatchEvent(new Event("change"));
  if (state.activeSession.model) modelSelect.value = state.activeSession.model;
  state.suppressPickerSave = false;
}

function connectWs(sessionId) {
  if (state.ws) state.ws.close();
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  state.ws = new WebSocket(`${protocol}//${location.host}/ws/dev-projects/${sessionId}`);
  state.ws.onmessage = (event) => handleWs(JSON.parse(event.data));
}

function handleWs(event) {
  if (event.type === "dev_projects.message" || event.type === "dev_projects.message_complete") {
    if (event.message) {
      els.messages?.insertAdjacentHTML("beforeend", renderMessages([event.message]));
      els.messages.scrollTop = els.messages.scrollHeight;
      window.hljs?.highlightAll?.();
    }
  } else if (event.type === "dev_projects.token") {
    ensureStreamingBubble().querySelector(".dev-message-text").textContent += event.token || "";
    els.messages.scrollTop = els.messages.scrollHeight;
  } else if (event.type === "dev_projects.permission_required") {
    appendPermission(els.messages, event);
  } else if (event.type === "dev_projects.annotation") {
    state.annotations.unshift(event.annotation);
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
  const provider = $("dev-provider-select")?.value || defaults.default_provider || "claude-code";
  const model = $("dev-model-select")?.value || defaults.default_model || null;
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
        renderEmptyChat();
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

function bindEvents() {
  bindComposer(els.input, els.send, sendCurrent);

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

  $("dev-rail-toggle")?.addEventListener("click", () => els.dpShell.classList.toggle("rail-open"));
  $("dev-rail-close")?.addEventListener("click", () => els.dpShell.classList.remove("rail-open"));
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

  document.querySelectorAll(".dp-parallel-seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => enterDevParallel(Number(btn.dataset.parallel || 1)));
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

async function enterDevParallel(count) {
  document.querySelectorAll(".dp-parallel-seg-btn").forEach((btn) => {
    const active = Number(btn.dataset.parallel) === count;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-checked", String(active));
  });
  if (count === 1) {
    exitParallelMode();
    await loadProjects();
    return;
  }
  await enterParallelMode(count);
}

export async function bootDevProjects() {
  if (!ensureShell()) return;
  await loadModels();
  bindEvents();
  await loadProjects();
}

bootDevProjects().catch((err) => {
  console.error("Dev Projects failed to boot", err);
});
