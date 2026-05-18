import { bindComposer } from "../components/dev-projects-composer.js";
import { renderMessages } from "../components/dev-projects-chat.js";
import { railMarkup, renderAnnotations } from "../components/dev-projects-annotation-rail.js";
import { renderProjectOptions, renderSessionList } from "../components/dev-projects-sidebar.js";
import { appendPermission } from "../components/dev-projects-chat.js";
import { escapeHtml } from "../core/utils.js";
import { enterParallelMode, exitParallelMode } from "../ui/parallel.js";

const API = "/api/dev-projects";

const state = {
  projects: [],
  sessions: [],
  activeProjectId: null,
  activeSessionId: null,
  activeSession: null,
  annotations: [],
  ws: null,
  suppressPickerSave: false,
};

const els = {};

async function request(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed: ${res.status}`);
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
  els.newSession = $("new-session-btn");
  els.headerActions = document.querySelector(".dp-header-actions");
  els.dpShell = document.querySelector(".dp-shell");

  if (!els.dpShell || $("dev-project-panel")) return Boolean(els.dpShell);

  const panel = document.createElement("div");
  panel.id = "dev-project-panel";
  panel.className = "dev-project-panel";
  panel.innerHTML = `
    <div class="dev-project-panel-header">
      <button class="dev-btn primary" id="dev-add-project">New project</button>
    </div>
    <div id="dev-project-list" class="dev-project-list"></div>
  `;
  document.querySelector(".rail-dev-friendly")?.prepend(panel);

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
  for (const fallback of [
    { id: "openai", name: "OpenAI API", models: [{ id: "", label: "Default" }] },
  ]) {
    if (!providers.some((provider) => provider.id === fallback.id)) providers.push(fallback);
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
  if (state.suppressPickerSave) return;
  if (!state.activeSessionId) return;
  const provider = $("dev-provider-select")?.value || "claude-code";
  const model = $("dev-model-select")?.value || null;
  const session = await request(`/sessions/${state.activeSessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ provider, model }),
  });
  state.activeSession = session;
  await saveProjectModelDefault(provider, model);
}

async function saveProjectModelDefault(provider, model) {
  const project = getActiveProject();
  if (!project) return;
  const metadata = {
    ...(project.metadata || {}),
    default_provider: provider,
    default_model: model,
  };
  const updated = await request(`/projects/${project.id}`, {
    method: "PATCH",
    body: JSON.stringify({ metadata }),
  });
  project.metadata = updated.metadata || metadata;
}

async function loadProjects() {
  const data = await request("/projects");
  state.projects = data.projects || [];
  if (!state.activeProjectId && state.projects.length) state.activeProjectId = state.projects[0].id;
  renderProjects();
  if (state.activeProjectId) await loadSessions(state.activeProjectId);
}

function renderProjects() {
  const list = $("dev-project-list");
  if (list) list.innerHTML = renderProjectOptions(state.projects, state.activeProjectId);
  const active = getActiveProject();
  const name = active?.name || "Select a project";
  const path = active?.path || "No project selected";
  if (els.projectName) els.projectName.textContent = name;
  if (els.projectPath) els.projectPath.textContent = path;
  if (els.railProjectName) els.railProjectName.textContent = name;
  if (els.railProjectSub) els.railProjectSub.textContent = active ? path : "—";
}

function getActiveProject() {
  return state.projects.find((p) => Number(p.id) === Number(state.activeProjectId));
}

async function loadSessions(projectId) {
  const data = await request(`/projects/${projectId}/sessions`);
  state.sessions = data.sessions || [];
  if (!state.activeSessionId && state.sessions.length) state.activeSessionId = state.sessions[0].id;
  renderSessions();
  if (state.activeSessionId) await loadSession(state.activeSessionId);
}

function renderSessions() {
  if (els.sessionList) els.sessionList.innerHTML = renderSessionList(state.sessions, state.activeSessionId);
  if (els.projectCount) {
    const count = state.sessions.length;
    els.projectCount.textContent = `${count} session${count === 1 ? "" : "s"}`;
  }
}

async function loadSession(sessionId) {
  const data = await request(`/sessions/${sessionId}`);
  state.activeSessionId = data.session.id;
  state.activeSession = data.session;
  state.annotations = data.annotations || [];
  renderSessions();
  renderChat(data.messages || []);
  renderRail();
  syncPickerFromSession();
  connectWs(data.session.id);
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
  }
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
    if (state.activeProjectId) await createSession();
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

async function createSession() {
  if (!state.activeProjectId) return;
  const defaults = getActiveProject()?.metadata || {};
  const provider = $("dev-provider-select")?.value || defaults.default_provider || "claude-code";
  const model = $("dev-model-select")?.value || defaults.default_model || null;
  const session = await request(`/projects/${state.activeProjectId}/sessions`, {
    method: "POST",
    body: JSON.stringify({ provider, model }),
  });
  state.sessions.unshift(session);
  state.activeSessionId = session.id;
  await loadSession(session.id);
}

async function createProject() {
  const path = prompt("Project folder path");
  if (!path) return;
  const name = prompt("Project name", path.split("/").filter(Boolean).pop() || "Project");
  if (!name) return;
  const project = await request("/projects", {
    method: "POST",
    body: JSON.stringify({ name, path }),
  });
  state.projects.unshift(project);
  state.activeProjectId = project.id;
  state.activeSessionId = null;
  renderProjects();
  await loadSessions(project.id);
}

async function forkAt(messageId) {
  if (!state.activeSessionId) return;
  const session = await request(`/sessions/${state.activeSessionId}/fork`, {
    method: "POST",
    body: JSON.stringify({ at_message_id: Number(messageId) }),
  });
  state.sessions.unshift(session);
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

function bindEvents() {
  bindComposer(els.input, els.send, sendCurrent);
  $("dev-add-project")?.addEventListener("click", createProject);
  els.newSession?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    createSession();
  }, true);

  document.addEventListener("click", async (event) => {
    const target = event.target;
    const projectRow = target.closest?.(".dev-project-row");
    if (projectRow) {
      state.activeProjectId = Number(projectRow.dataset.projectId);
      state.activeSessionId = null;
      renderProjects();
      await loadSessions(state.activeProjectId);
      return;
    }
    const sessionRow = target.closest?.(".dev-session-row");
    if (sessionRow) {
      await loadSession(Number(sessionRow.dataset.sessionId));
      return;
    }
    const fork = target.closest?.("[data-fork-at]");
    if (fork) await forkAt(fork.dataset.forkAt);
    const approve = target.closest?.("[data-approve]");
    if (approve) await decide(approve.dataset.approve, true);
    const deny = target.closest?.("[data-deny]");
    if (deny) await decide(deny.dataset.deny, false);
    const trust = target.closest?.("[data-trust]");
    if (trust) await decide(trust.dataset.trust, true, true);
  });

  $("dev-rail-toggle")?.addEventListener("click", () => {
    els.dpShell.classList.toggle("rail-open");
  });
  $("dev-rail-close")?.addEventListener("click", () => els.dpShell.classList.remove("rail-open"));
  $("dev-rail-url")?.addEventListener("change", (event) => {
    $("dev-preview-frame").src = event.target.value;
  });
  $("dev-note-send")?.addEventListener("click", sendAnnotation);
  $("dev-target-pick")?.addEventListener("click", () => {
    $("dev-target-overlay")?.classList.remove("hidden");
  });
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

  els.input.value = `Re: ${url} — ${target}: `;
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
