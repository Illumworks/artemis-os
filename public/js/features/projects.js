// Project selection & system prompts
import { $ } from '../core/dom.js';
import { getState, setState } from '../core/store.js';
import { CHAT_IDS } from '../core/constants.js';
import * as api from '../core/api.js';
import { commandRegistry, registerCommand } from '../ui/commands.js';
import { panes } from '../ui/parallel.js';
import { loadSessions } from './sessions.js';
import { loadStats } from './cost-dashboard.js';
import { showChatEmptyState, addSkillUsedMessage } from '../ui/messages.js';
import { updateAttachmentBadge, clearImageAttachments } from './attachments.js';
import { escapeHtml } from '../core/utils.js';

/** Convert a Unix timestamp (seconds) to a short human-relative string. */
function humanTime(ts) {
  if (!ts) return null;
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const projectPickerState = {
  menu: null,
  trigger: null,
  triggersBound: false,
  listenersBound: false,
};

export async function loadProjects() {
  try {
    const projects = await api.fetchProjects();
    setState("projectsData", projects);
    const saved = localStorage.getItem("artemis-cwd") || "";

    for (const p of projects) {
      const opt = document.createElement("option");
      opt.value = p.path;
      opt.textContent = p.name;
      $.projectSelect.appendChild(opt);
    }

    if (saved && [...$.projectSelect.options].some((o) => o.value === saved)) {
      $.projectSelect.value = saved;
    }
    updateSystemPromptIndicator();
    updateHeaderProjectName();
    updateSessionControls();
    bindProjectPickerTriggers();
    loadProjectCommands();
    refreshHeaderProjectContext();
    // Load sessions after project dropdown is populated so they filter correctly
    loadSessions();

    // If a session was restored from localStorage, load its messages
    const { getState } = await import('../core/store.js');
    const { loadMessages } = await import('./sessions.js');
    const restoredSid = getState("sessionId");
    if (restoredSid) {
      loadMessages(restoredSid);
    }
  } catch (err) {
    console.error("Failed to load projects:", err);
  }
}

const sessionControls = document.getElementById("session-controls");
const headerProjectBranch = document.getElementById("header-project-branch");
const headerProjectSessionCount = document.getElementById("header-project-session-count");
const headerProjectPath = document.getElementById("header-project-path");
// Legacy sidebar project nodes (kept optional for back-compat; may be null on new shell)
const sidebarProjectName = document.getElementById("sidebar-project-name");
const sidebarProjectBranch = document.getElementById("sidebar-project-branch");
const sidebarProjectSessionCount = document.getElementById("sidebar-project-session-count");
const sidebarProjectPath = document.getElementById("sidebar-project-path");
// New rail card (artemis-os design)
const railProjectName = document.getElementById("rail-project-name");
const railProjectSub = document.getElementById("rail-project-sub");
const railProjectCard = document.getElementById("rail-project-card");
const headerProjectTitleBtn = document.getElementById("header-project-title-btn");
const folderPickerSummary = document.querySelector(".folder-picker-summary");
let headerProjectRefreshToken = 0;

function updateSessionControls() {
  // Legacy #session-controls is gone in the new shell. Guard so projects.js
  // keeps loading when the node is absent.
  if (!sessionControls) return;
  if ($.projectSelect.value) {
    sessionControls.classList.remove("hidden");
  } else {
    sessionControls.classList.add("hidden");
  }
}

function getProjectOptions() {
  return [...($.projectSelect?.options || [])].filter((option) => option.value);
}

function closeProjectPicker() {
  if (projectPickerState.menu) {
    const isRail = projectPickerState.menu.classList.contains("project-picker-menu--rail");
    if (isRail) {
      // Restore the placeholder slot inside .rail-dev-friendly
      const slot = document.createElement("div");
      slot.id = "rdf-dropdown";
      slot.className = "rdf-dropdown artemis-project-picker hidden";
      slot.setAttribute("role", "menu");
      projectPickerState.menu.replaceWith(slot);
    } else {
      projectPickerState.menu.remove();
    }
    projectPickerState.menu = null;
  }

  if (projectPickerState.trigger) {
    projectPickerState.trigger.setAttribute("aria-expanded", "false");
    const wrapper =
      projectPickerState.trigger.closest(".header-project-pill, .folder-picker, .rail-dev-friendly");
    wrapper?.classList.remove("open");
  }

  projectPickerState.trigger = null;
}

function handleProjectPickerDismiss(event) {
  if (!projectPickerState.menu || !projectPickerState.trigger) return;
  if (projectPickerState.menu.contains(event.target) || projectPickerState.trigger.contains(event.target)) return;
  closeProjectPicker();
}

function openProjectPicker(trigger) {
  if (!trigger || !$.projectSelect) return;

  if (projectPickerState.trigger === trigger && projectPickerState.menu) {
    closeProjectPicker();
    return;
  }

  closeProjectPicker();

  // Three mount points on the new shell:
  //   • rail-project-card → sibling .rail-dev-friendly container (inline dropdown)
  //   • legacy .header-project-pill or .folder-picker (kept for back-compat)
  //   • header .dp-project-title (new) → floating dropdown anchored under trigger
  const isRailCard = trigger.id === "rail-project-card";
  const isHeaderTitle = trigger.classList.contains("dp-project-title");
  let wrapper;
  if (isRailCard) {
    // Reuse the pre-rendered #rdf-dropdown slot inside .rail-dev-friendly
    wrapper = trigger.closest(".rail-dev-friendly");
  } else {
    wrapper = trigger.closest(".header-project-pill, .folder-picker") || trigger.parentElement;
  }
  if (!wrapper) return;

  const options = getProjectOptions();
  if (options.length === 0) return;

  const menu = document.createElement("div");
  const variant = isRailCard
    ? "project-picker-menu--rail artemis-project-picker--rail"
    : isHeaderTitle
    ? "project-picker-menu--header artemis-project-picker--header"
    : wrapper.classList.contains("folder-picker")
    ? "project-picker-menu--sidebar artemis-project-picker--sidebar"
    : "project-picker-menu--header artemis-project-picker--header";
  const folderIcon = (amber) => `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="${amber ? "var(--amber-ink)" : "var(--ink-4)"}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/></svg>`;

  menu.className = `rdf-dropdown project-picker-menu artemis-project-picker ${variant}`;
  menu.setAttribute("role", "menu");

  if (isRailCard) {
    // Approved design: div items with folder icon, name, "N sessions", amber dot for active
    // Each row also gets a hover × remove button
    options.forEach((option) => {
      const isActive = option.value === $.projectSelect.value;
      const label = option.textContent?.trim() || option.value;

      const item = document.createElement("div");
      item.className = `rdf-dropdown-item project-picker-item artemis-project-picker-item${isActive ? " active" : ""}`;
      item.dataset.projectPath = encodeURIComponent(option.value);
      item.setAttribute("role", "menuitem");
      item.setAttribute("tabindex", "0");
      item.innerHTML = `
        ${folderIcon(isActive)}
        <div style="min-width:0;flex:1">
          <div class="rdf-dropdown-name">${escapeHtml(label)}</div>
          <div class="rdf-dropdown-sub" data-sessions-for="${encodeURIComponent(option.value)}">— sessions</div>
        </div>
        ${isActive ? '<span class="rdf-dropdown-dot"></span>' : ""}
        <button class="rdf-dropdown-remove" title="Remove project" tabindex="-1" aria-label="Remove ${escapeHtml(label)}">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      `;

      // Select project on row click (but not if clicking the remove button)
      const select = () => {
        const projectPath = decodeURIComponent(item.dataset.projectPath || "");
        if (!projectPath || $.projectSelect.value === projectPath) { closeProjectPicker(); return; }
        $.projectSelect.value = projectPath;
        $.projectSelect.dispatchEvent(new Event("change", { bubbles: true }));
        closeProjectPicker();
      };
      item.addEventListener("click", (e) => { if (!e.target.closest(".rdf-dropdown-remove")) select(); });
      item.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); } });

      // Remove project on × click
      const removeBtn = item.querySelector(".rdf-dropdown-remove");
      removeBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const projectPath = decodeURIComponent(item.dataset.projectPath || "");
        if (!projectPath) return;

        removeBtn.disabled = true;
        try {
          await api.deleteProject(projectPath);

          // Remove from hidden select
          const opt = [...$.projectSelect.options].find(o => o.value === projectPath);
          opt?.remove();

          // Remove from projectsData state
          const projects = getState("projectsData");
          setState("projectsData", projects.filter(p => p.path !== projectPath));

          // If this was the active project, switch to the first remaining one
          if ($.projectSelect.value === projectPath || !$.projectSelect.value) {
            const first = $.projectSelect.options[0];
            if (first) {
              $.projectSelect.value = first.value;
              localStorage.setItem("artemis-cwd", first.value);
              $.projectSelect.dispatchEvent(new Event("change", { bubbles: true }));
            }
          }

          // Visually remove the row and re-open picker to reflect new state
          closeProjectPicker();
          // Brief tick so the DOM settles before re-opening
          setTimeout(() => openProjectPicker(trigger), 50);
        } catch (err) {
          removeBtn.disabled = false;
          console.error("Failed to remove project:", err);
        }
      });

      menu.appendChild(item);
    });

    // "＋ Add project" row at the bottom
    const addRow = document.createElement("div");
    addRow.className = "rdf-dropdown-item rdf-dropdown-add artemis-project-picker-item";
    addRow.setAttribute("role", "menuitem");
    addRow.setAttribute("tabindex", "0");
    addRow.innerHTML = `
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--amber-ink)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
      </svg>
      <div style="min-width:0;flex:1">
        <div class="rdf-dropdown-name" style="color:var(--amber-ink)">Add project</div>
      </div>`;
    const openAdd = () => { closeProjectPicker(); openAddProjectModal(); };
    addRow.addEventListener("click", openAdd);
    addRow.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openAdd(); } });
    menu.appendChild(addRow);

    // Async: fill in session counts
    options.forEach((option) => {
      const sub = menu.querySelector(`[data-sessions-for="${encodeURIComponent(option.value)}"]`);
      if (!sub) return;
      fetch(`/api/sessions?project_path=${encodeURIComponent(option.value)}&limit=200`)
        .then(r => r.json())
        .then(sessions => {
          if (!sub.isConnected) return;
          const n = Array.isArray(sessions) ? sessions.length : 0;
          sub.textContent = `${n} session${n === 1 ? "" : "s"}`;
        })
        .catch(() => { sub.textContent = "— sessions"; });
    });

  } else {
    // Header / sidebar variant: button items with path subtitle
    menu.innerHTML = options.map((option) => {
      const isActive = option.value === $.projectSelect.value;
      const label = option.textContent?.trim() || option.value;
      return `
        <button type="button"
          class="rdf-dropdown-item project-picker-item artemis-project-picker-item${isActive ? " active" : ""}"
          data-project-path="${encodeURIComponent(option.value)}">
          <div class="project-picker-item-mark" aria-hidden="true">${folderIcon(isActive)}</div>
          <div class="project-picker-item-copy">
            <div class="rdf-dropdown-name">${escapeHtml(label)}</div>
            <div class="rdf-dropdown-sub">${escapeHtml(option.value)}</div>
          </div>
          ${isActive ? '<span class="rdf-dropdown-dot"></span>' : ""}
        </button>
      `;
    }).join("");

    menu.querySelectorAll(".project-picker-item").forEach((item) => {
      item.addEventListener("click", () => {
        const projectPath = decodeURIComponent(item.dataset.projectPath || "");
        if (!projectPath || $.projectSelect.value === projectPath) { closeProjectPicker(); return; }
        $.projectSelect.value = projectPath;
        $.projectSelect.dispatchEvent(new Event("change", { bubbles: true }));
        closeProjectPicker();
      });
    });
  }

  wrapper.classList.add("open");
  trigger.setAttribute("aria-expanded", "true");

  if (isHeaderTitle) {
    // Float under the trigger using fixed positioning
    const rect = trigger.getBoundingClientRect();
    menu.style.position = "fixed";
    menu.style.top = `${Math.round(rect.bottom + 6)}px`;
    menu.style.left = `${Math.round(rect.left)}px`;
    menu.style.width = `${Math.max(240, Math.round(rect.width))}px`;
    menu.style.zIndex = "60";
    document.body.appendChild(menu);
  } else if (isRailCard) {
    // Inline placement per approved design — sits between card and New session CTA
    // (rail-section-body-inner overflow is set to visible via CSS override)
    const slot = wrapper.querySelector("#rdf-dropdown");
    if (slot) slot.replaceWith(menu);
    else wrapper.insertBefore(menu, wrapper.querySelector(".rdf-primary") || null);
  } else {
    wrapper.appendChild(menu);
  }
  projectPickerState.menu = menu;
  projectPickerState.trigger = trigger;

  if (!projectPickerState.listenersBound) {
    document.addEventListener("mousedown", handleProjectPickerDismiss);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeProjectPicker();
    });
    projectPickerState.listenersBound = true;
  }
}

function bindProjectPickerTriggers() {
  if (projectPickerState.triggersBound) return;
  headerProjectTitleBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    openProjectPicker(headerProjectTitleBtn);
  });

  folderPickerSummary?.addEventListener("click", (event) => {
    event.preventDefault();
    openProjectPicker(folderPickerSummary);
  });

  // New shell: rail project card (artemis-os design)
  railProjectCard?.addEventListener("click", (event) => {
    event.preventDefault();
    openProjectPicker(railProjectCard);
  });
  railProjectCard?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openProjectPicker(railProjectCard);
    }
  });

  projectPickerState.triggersBound = true;
}

export function updateSystemPromptIndicator() {
  const cwd = $.projectSelect.value;
  const project = getState("projectsData").find((p) => p.path === cwd);
  if (project && project.systemPrompt) {
    $.spBadge.classList.remove("hidden");
  } else {
    $.spBadge.classList.add("hidden");
  }
}

export function openSystemPromptModal() {
  const cwd = $.projectSelect.value;
  if (!cwd) return;
  const project = getState("projectsData").find((p) => p.path === cwd);
  $.spTextarea.value = project?.systemPrompt || "";
  $.spModal.classList.remove("hidden");
  $.spTextarea.focus();
}

export async function saveSystemPrompt(prompt) {
  const cwd = $.projectSelect.value;
  if (!cwd) return;
  try {
    await api.saveSystemPromptApi(cwd, prompt);
    const project = getState("projectsData").find((p) => p.path === cwd);
    if (project) project.systemPrompt = prompt;
    updateSystemPromptIndicator();
  } catch (err) {
    console.error("Failed to save system prompt:", err);
  }
}

export function updateHeaderProjectName(sessionCount = null, lastUsedAt = null) {
  const projectSelect = $.projectSelect;
  if (!projectSelect) return;

  const opt = projectSelect.options[projectSelect.selectedIndex];
  const hasProject = Boolean(opt && opt.value);
  const name = hasProject ? opt.textContent : "Select a project";
  const path = hasProject ? projectSelect.value : "No project selected";
  const countLabel =
    typeof sessionCount === "number"
      ? `${sessionCount} session${sessionCount === 1 ? "" : "s"}`
      : "0 sessions";

  if ($.headerProjectName) $.headerProjectName.textContent = name;
  if (sidebarProjectName) sidebarProjectName.textContent = name;
  if (railProjectName) railProjectName.textContent = name;

  // Rail sub: "Last active X · N sessions" per approved design
  if (railProjectSub) {
    if (hasProject) {
      const timeLabel = humanTime(lastUsedAt);
      railProjectSub.textContent = timeLabel
        ? `Last active ${timeLabel} · ${countLabel}`
        : countLabel;
    } else {
      railProjectSub.textContent = "—";
    }
  }

  if (headerProjectPath) headerProjectPath.textContent = path;
  if (sidebarProjectPath) sidebarProjectPath.textContent = path;

  if (headerProjectSessionCount) headerProjectSessionCount.textContent = countLabel;
  if (sidebarProjectSessionCount) sidebarProjectSessionCount.textContent = countLabel;

  // The branch meta element in the new shell is a <span> containing an <svg> and an inner
  // <span>. Write into the last <span> child only, or fall back to textContent.
  const writeBranch = (el, text) => {
    if (!el) return;
    const inner = el.querySelector("span:last-child");
    if (inner) inner.textContent = text;
    else el.textContent = text;
  };
  writeBranch(headerProjectBranch, "Branch --");
  writeBranch(sidebarProjectBranch, "Branch --");
}

export async function refreshHeaderProjectContext({ sessionCount = null } = {}) {
  updateHeaderProjectName(sessionCount);

  const cwd = $.projectSelect.value;
  if (!cwd || !headerProjectBranch) return;

  const token = ++headerProjectRefreshToken;
  try {
    const branchResult = await api.execCommand("git rev-parse --abbrev-ref HEAD", cwd);
    if (token !== headerProjectRefreshToken) return;
    const branch = String(branchResult?.stdout || branchResult?.output || "").trim();
    const label = branch ? `Branch ${branch}` : "Branch --";
    const writeBranch = (el, text) => {
      if (!el) return;
      const inner = el.querySelector("span:last-child");
      if (inner) inner.textContent = text;
      else el.textContent = text;
    };
    writeBranch(headerProjectBranch, label);
    writeBranch(sidebarProjectBranch, label);
  } catch {
    if (token === headerProjectRefreshToken) {
      const writeBranch = (el, text) => {
        if (!el) return;
        const inner = el.querySelector("span:last-child");
        if (inner) inner.textContent = text;
        else el.textContent = text;
      };
      writeBranch(headerProjectBranch, "Branch --");
      writeBranch(sidebarProjectBranch, "Branch --");
    }
  }
}

// Skill lookup map — exported so chat.js can look up model-invoked skills
export const skillLookup = new Map();

export async function loadProjectCommands() {
  // Remove old project commands and skills
  for (const [name, cmd] of Object.entries(commandRegistry)) {
    if (cmd.category === "project" || cmd.category === "skill") delete commandRegistry[name];
  }
  skillLookup.clear();

  const cwd = $.projectSelect.value;
  if (!cwd) return;

  try {
    const commands = await api.fetchProjectCommands(cwd);
    if (!Array.isArray(commands) || commands.length === 0) return;

    for (const c of commands) {
      const slug = c.command;
      if (!slug || commandRegistry[slug]) continue;
      const hasArgs = c.prompt.includes("$ARGUMENTS");
      const label = c.source === "skill" ? `${c.description}` : (c.description || c.command);

      // Build skill lookup map
      if (c.source === "skill") {
        skillLookup.set(slug, { description: label, scope: "project" });
      }

      registerCommand(slug, {
        category: c.source === "skill" ? "skill" : "project",
        description: label,
        needsArgs: hasArgs,
        argumentHint: c.argumentHint || "",
        execute(args, pane) {
          // Show "Skill used" message for skills
          if (c.source === "skill") {
            addSkillUsedMessage(slug, c.description, pane);
          }

          let prompt = c.prompt;
          if (hasArgs) {
            prompt = prompt.replace(/\$ARGUMENTS/g, args || "");
          }
          pane.messageInput.value = prompt;
          pane.messageInput.style.height = "auto";
          pane.messageInput.style.height = Math.min(pane.messageInput.scrollHeight, 200) + "px";
          // Lazy import to avoid circular dep
          import('./chat.js').then(({ sendMessage }) => sendMessage(pane));
        },
      });
    }
  } catch (err) {
    console.error("Failed to load project commands:", err);
  }
}

// ── Add Project (folder browser) ────────────────────────
let currentBrowsePath = "";

function openAddProjectModal() {
  $.addProjectModal.classList.remove("hidden");
  $.addProjectName.value = "";
  navigateToDir(""); // defaults to $HOME on server
}

function closeAddProjectModal() {
  $.addProjectModal.classList.add("hidden");
}

async function navigateToDir(dir) {
  $.folderList.innerHTML = '<div class="folder-list-loading">Loading...</div>';
  try {
    const data = await api.browseFolders(dir || undefined);
    currentBrowsePath = data.current;
    renderBreadcrumb(data.current);
    renderFolderList(data);
    // Auto-fill name from last segment
    const base = data.current.split(/[/\\]/).filter(Boolean).pop() || "";
    $.addProjectName.value = base;
  } catch (err) {
    $.folderList.innerHTML = `<div class="folder-list-empty">Error: ${err.message}</div>`;
  }
}

function renderBreadcrumb(pathStr) {
  $.folderBreadcrumb.innerHTML = "";
  const parts = pathStr.split(/[/\\]/).filter(Boolean);
  // Root
  const rootSeg = document.createElement("span");
  rootSeg.className = "folder-breadcrumb-seg";
  rootSeg.textContent = "/";
  rootSeg.addEventListener("click", () => navigateToDir("/"));
  $.folderBreadcrumb.appendChild(rootSeg);

  let accumulated = "";
  for (const part of parts) {
    accumulated += "/" + part;
    const sep = document.createElement("span");
    sep.className = "folder-breadcrumb-sep";
    sep.textContent = "/";
    $.folderBreadcrumb.appendChild(sep);

    const seg = document.createElement("span");
    seg.className = "folder-breadcrumb-seg";
    seg.textContent = part;
    const target = accumulated;
    seg.addEventListener("click", () => navigateToDir(target));
    $.folderBreadcrumb.appendChild(seg);
  }
}

function renderFolderList(data) {
  $.folderList.innerHTML = "";

  const folderSvg = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/></svg>`;
  const upSvg = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>`;

  // Parent directory entry
  if (data.parent) {
    const parentItem = document.createElement("div");
    parentItem.className = "folder-list-item";
    parentItem.innerHTML = `<span class="folder-icon">${upSvg}</span><span>Parent directory</span>`;
    parentItem.addEventListener("click", () => navigateToDir(data.parent));
    $.folderList.appendChild(parentItem);
  }

  if (data.dirs.length === 0 && !data.parent) {
    $.folderList.innerHTML = '<div class="folder-list-empty">No subdirectories</div>';
    return;
  }

  if (data.dirs.length === 0) {
    const empty = document.createElement("div");
    empty.className = "folder-list-empty";
    empty.textContent = "No subdirectories";
    $.folderList.appendChild(empty);
    return;
  }

  for (const dir of data.dirs) {
    const item = document.createElement("div");
    item.className = "folder-list-item";
    item.innerHTML = `<span class="folder-icon">${folderSvg}</span><span>${dir.name}</span>`;
    item.addEventListener("click", () => navigateToDir(dir.path));
    $.folderList.appendChild(item);
  }
}

async function confirmAddProject() {
  const name = $.addProjectName.value.trim();
  if (!name) {
    $.addProjectName.focus();
    return;
  }
  if (!currentBrowsePath) return;

  // Check for duplicate in dropdown
  const existing = [...$.projectSelect.options].find((o) => o.value === currentBrowsePath);
  if (existing) {
    alert("This project path is already added.");
    return;
  }

  try {
    const result = await api.addProject(name, currentBrowsePath);
    const project = result.project;

    // Add to dropdown and select it
    const opt = document.createElement("option");
    opt.value = project.path;
    opt.textContent = project.name;
    $.projectSelect.appendChild(opt);
    $.projectSelect.value = project.path;

    // Update state
    const projects = getState("projectsData");
    projects.push({ name: project.name, path: project.path });

    localStorage.setItem("artemis-cwd", project.path);
    updateSystemPromptIndicator();
    updateHeaderProjectName();
    updateSessionControls();
    loadProjectCommands();
    refreshHeaderProjectContext();
    loadSessions();
    loadStats();

    closeAddProjectModal();
  } catch (err) {
    alert("Failed to add project: " + err.message);
  }
}

// Open in VS Code
$.openVscodeBtn?.addEventListener("click", async () => {
  const path = $.projectSelect.value;
  if (!path) return;
  try {
    await fetch("/api/exec", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: "code .", cwd: path }),
    });
  } catch { /* ignore */ }
});

// Remove project
$.removeProjectBtn?.addEventListener("click", async () => {
  const path = $.projectSelect.value;
  if (!path) return;
  const name = $.projectSelect.options[$.projectSelect.selectedIndex].textContent;
  if (!confirm(`Remove "${name}" from your projects?\n\nThis only removes it from Artemis — your files won't be deleted.`)) return;
  try {
    await api.deleteProject(path);
    // Remove from dropdown
    const opt = [...$.projectSelect.options].find(o => o.value === path);
    if (opt) opt.remove();
    // Remove from state
    const projects = getState("projectsData");
    const idx = projects.findIndex(p => p.path === path);
    if (idx !== -1) projects.splice(idx, 1);
    // Reset selection
    $.projectSelect.value = "";
    localStorage.removeItem("artemis-cwd");
    updateSystemPromptIndicator();
    updateHeaderProjectName();
    updateSessionControls();
    loadProjectCommands();
    refreshHeaderProjectContext();
    $.messagesDiv.innerHTML = "";
    showChatEmptyState();
    loadSessions();
  } catch (err) {
    alert("Failed to remove project: " + err.message);
  }
});

// Add project button & modal event listeners
$.addProjectBtn?.addEventListener("click", openAddProjectModal);
$.addProjectClose?.addEventListener("click", closeAddProjectModal);
$.addProjectConfirm?.addEventListener("click", confirmAddProject);
$.addProjectModal?.addEventListener("click", (e) => {
  if (e.target === $.addProjectModal) closeAddProjectModal();
});

// System prompt modal event listeners
$.spEditBtn?.addEventListener("click", openSystemPromptModal);
$.spForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  await saveSystemPrompt($.spTextarea.value.trim());
  $.spModal.classList.add("hidden");
});
document.getElementById("sp-cancel-btn")?.addEventListener("click", () => {
  $.spModal.classList.add("hidden");
});
document.getElementById("sp-modal-close")?.addEventListener("click", () => {
  $.spModal.classList.add("hidden");
});
document.getElementById("sp-clear-btn")?.addEventListener("click", async () => {
  $.spTextarea.value = "";
  await saveSystemPrompt("");
  $.spModal.classList.add("hidden");
});
$.spModal?.addEventListener("click", (e) => {
  if (e.target === $.spModal) $.spModal.classList.add("hidden");
});

// Project change handler
$.projectSelect?.addEventListener("change", async () => {
  closeProjectPicker();
  const { guardSwitch } = await import('./background-sessions.js');
  guardSwitch(() => {
    localStorage.setItem("artemis-cwd", $.projectSelect.value);
    setState("sessionId", null);
    if ($.projectSelect.value) {
      setState("view", "chat");
    }
    // Clear attachments and input on project switch
    setState("attachedFiles", []);
    setState("allProjectFiles", []);
    clearImageAttachments();
    updateAttachmentBadge();
    $.messageInput.value = "";
    updateSystemPromptIndicator();
    updateHeaderProjectName();
    updateSessionControls();
    loadProjectCommands();
    refreshHeaderProjectContext();
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
    loadSessions();
    loadStats();
  });
});

// New session button
$.newSessionBtn?.addEventListener("click", async () => {
  const { guardSwitch } = await import('./background-sessions.js');
  guardSwitch(() => {
    setState("view", "chat");
    setState("sessionId", null);
    if (getState("parallelMode")) {
      for (const chatId of CHAT_IDS) {
        const pane = panes.get(chatId);
        if (pane) {
          pane.messagesDiv.innerHTML = "";
          pane.currentAssistantMsg = null;
          showChatEmptyState(pane);
        }
      }
    } else {
      $.messagesDiv.innerHTML = "";
      showChatEmptyState();
    }
    loadSessions();
    if (!getState("parallelMode")) $.messageInput.focus();
  });
});

// Parallel mode toggle
$.toggleParallelBtn?.addEventListener("change", () => {
  if ($.toggleParallelBtn.checked) {
    import('../ui/parallel.js').then(({ enterParallelMode }) => enterParallelMode());
  } else {
    import('../ui/parallel.js').then(({ exitParallelMode }) => exitParallelMode());
  }
});
