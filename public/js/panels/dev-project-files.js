// Dev Projects Files — first-class project file rail in the sidebar
import { $ } from "../core/dom.js";
import { on as onBus } from "../core/events.js";
import { on as onState } from "../core/store.js";
import { execCommand, fetchFileTree, searchFiles } from "../core/api.js";
import { escapeHtml } from "../core/utils.js";

const TREE_CACHE = new Map();
const STATUS_CACHE = new Map();
let searchTimer = null;
let activeFilePath = "";
let currentQuery = "";
let currentRenderToken = 0;
const FOCUS_STORAGE_KEY = "artemis-dev-project-files-focus";

const CHEVRON_SVG = `<svg class="file-tree-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>`;
const FOLDER_SVG = `<svg class="file-tree-icon folder" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
const FILE_SVG = `<svg class="file-tree-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>`;

function isExecDisabledResult(result) {
  return result?.code === "exec_disabled" || result?.code === "exec_local_only";
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function escapeSelectorValue(value) {
  return String(value).replace(/["\\]/g, "\\$&");
}

function getProjectPath() {
  return $.projectSelect?.value || localStorage.getItem("artemis-cwd") || "";
}

function getSelectedProjectName(base) {
  const select = $.projectSelect;
  if (!select) return "";
  const option = [...select.options].find((item) => item.value === base);
  return option?.textContent?.trim() || "";
}

function clearBaseCache(base) {
  if (!base) return;
  for (const key of [...TREE_CACHE.keys()]) {
    if (key.startsWith(`${base}::`)) TREE_CACHE.delete(key);
  }
  STATUS_CACHE.delete(base);
}

async function loadTree(base, dir = "") {
  const cacheKey = `${base}::${dir}`;
  if (TREE_CACHE.has(cacheKey)) return TREE_CACHE.get(cacheKey);
  const entries = await fetchFileTree(base, dir);
  TREE_CACHE.set(cacheKey, entries);
  return entries;
}

async function loadStatus(base, force = false) {
  if (!force && STATUS_CACHE.has(base)) return STATUS_CACHE.get(base);

  const [branchResult, statusResult] = await Promise.all([
    execCommand("git rev-parse --abbrev-ref HEAD", base),
    execCommand("git status --porcelain=v1", base),
  ]);

  if (isExecDisabledResult(branchResult) || isExecDisabledResult(statusResult)) {
    const disabled = {
      branch: "Shell disabled",
      dirtyCount: 0,
      statusMap: new Map(),
      shellDisabled: true,
    };
    STATUS_CACHE.set(base, disabled);
    return disabled;
  }

  const branch = String(branchResult?.stdout || branchResult?.output || "").trim();
  const lines = String(statusResult?.stdout || statusResult?.output || "")
    .split("\n")
    .map((line) => line.trimEnd())
    .filter(Boolean);
  const statusMap = new Map();

  for (const line of lines) {
    const rawCode = line.slice(0, 2);
    let filePath = line.slice(3).trim();
    if (filePath.includes(" -> ")) {
      filePath = filePath.split(" -> ").pop().trim();
    }
    if (!filePath) continue;
    statusMap.set(filePath, rawCode);
  }

  const payload = {
    branch: branch || "Unknown",
    dirtyCount: lines.length,
    statusMap,
    shellDisabled: false,
  };
  STATUS_CACHE.set(base, payload);
  return payload;
}

function formatStatusBadge(rawCode) {
  const code = String(rawCode || "").trim();
  if (!code) return "";
  if (code.includes("?")) return "U";
  if (code.includes("R")) return "R";
  if (code.includes("D")) return "D";
  if (code.includes("A")) return "A";
  if (code.includes("M")) return "M";
  return code.slice(0, 2).toUpperCase();
}

function setActiveFile(path) {
  activeFilePath = path || "";
  $.devProjectFilesTree?.querySelectorAll(".file-tree-item.active, .file-tree-item.is-selected").forEach((item) => {
    item.classList.remove("active", "is-selected");
  });
  if (!activeFilePath) return;
  const active = $.devProjectFilesTree?.querySelector(`[data-path="${escapeSelectorValue(activeFilePath)}"]`);
  active?.classList.add("active", "is-selected");
}

function updateHeader(base, status, query = "") {
  if (!$.devProjectFilesSection) return;

  if (!base) {
    if ($.devProjectFilesSummary) {
      $.devProjectFilesSummary.textContent = "Select a project to browse files under Forge.";
    }
    if ($.devProjectFilesDirty) {
      $.devProjectFilesDirty.textContent = "No project";
    }
    if ($.devProjectFilesBranch) {
      $.devProjectFilesBranch.textContent = "Branch --";
    }
    if ($.devProjectFilesOpenBtn) {
      $.devProjectFilesOpenBtn.disabled = true;
    }
    return;
  }

  const projectName = getSelectedProjectName(base) || "Selected project";
  const branch = status?.branch || "Unknown";
  const dirtyCount = Number(status?.dirtyCount || 0);
  const dirtyLabel = dirtyCount === 1 ? "1 dirty file" : `${dirtyCount} dirty files`;

  if ($.devProjectFilesSummary) {
    $.devProjectFilesSummary.textContent = query
      ? `Searching "${query}" in ${projectName} on ${branch}.`
      : `${projectName} on ${branch} with ${dirtyLabel}.`;
  }
  if ($.devProjectFilesDirty) {
    $.devProjectFilesDirty.textContent = dirtyCount ? dirtyLabel : "Clean";
  }
  if ($.devProjectFilesBranch) {
    $.devProjectFilesBranch.textContent = status?.shellDisabled ? "Branch shell disabled" : `Branch ${branch}`;
  }
  if ($.devProjectFilesOpenBtn) {
    $.devProjectFilesOpenBtn.disabled = Boolean(status?.shellDisabled);
  }
}

function renderEmptyState(message, detail = "") {
  if (!$.devProjectFilesTree) return;
  $.devProjectFilesTree.innerHTML = `
    <div class="dev-project-files-empty">
      <strong>${escapeHtml(message)}</strong>
      ${detail ? `<small>${escapeHtml(detail)}</small>` : ""}
    </div>
  `;
}

function makeTreeItem(entry, depth, statusMap, base, renderToken) {
  const item = document.createElement("div");
  item.className = "file-tree-item dev-project-files-item";
  item.style.paddingLeft = `${8 + depth * 16}px`;
  item.dataset.path = entry.path;
  item.dataset.type = entry.type;
  item.draggable = true;

  const rawStatus = statusMap.get(entry.path);
  const badge = rawStatus ? `<span class="dev-project-files-status">${escapeHtml(formatStatusBadge(rawStatus))}</span>` : "";
  const isDir = entry.type === "dir";
  const icon = isDir ? FOLDER_SVG : FILE_SVG;
  const chevron = isDir ? CHEVRON_SVG : `<svg class="file-tree-chevron hidden" viewBox="0 0 24 24"></svg>`;

  item.innerHTML = `${chevron}${icon}<span class="file-tree-name">${escapeHtml(entry.name || entry.path)}</span>${badge}`;

  item.addEventListener("dragstart", (e) => {
    const fullPath = `${base}/${entry.path}`;
    e.dataTransfer?.setData("text/plain", fullPath);
    e.dataTransfer?.setData("application/x-file-path", fullPath);
    e.dataTransfer.effectAllowed = "copy";
    item.classList.add("dragging");
  });

  item.addEventListener("dragend", () => {
    item.classList.remove("dragging");
  });

  if (isDir) {
    const childrenContainer = document.createElement("div");
    childrenContainer.className = "file-tree-children";
    item.after(childrenContainer);

    item.addEventListener("click", async () => {
      const chevronEl = item.querySelector(".file-tree-chevron");
      const isExpanded = childrenContainer.classList.contains("expanded");

      if (isExpanded) {
        childrenContainer.classList.remove("expanded");
        chevronEl?.classList.remove("expanded");
        return;
      }

      chevronEl?.classList.add("expanded");
      childrenContainer.classList.add("expanded");

      if (childrenContainer.dataset.loaded === "1") return;

      childrenContainer.innerHTML = `<div class="file-tree-loading" style="padding-left:${8 + (depth + 1) * 16}px">Loading...</div>`;
      try {
        const children = await loadTree(base, entry.path);
        if (renderToken !== currentRenderToken) return;
        childrenContainer.dataset.loaded = "1";
        childrenContainer.innerHTML = "";
        renderTreeEntries(children, childrenContainer, depth + 1, statusMap, base, renderToken);
      } catch {
        if (renderToken !== currentRenderToken) return;
        childrenContainer.innerHTML = `<div class="file-tree-loading" style="padding-left:${8 + (depth + 1) * 16}px">Failed to load</div>`;
      }
    });

    return [item, childrenContainer];
  }

  item.addEventListener("click", () => {
    setActiveFile(entry.path);
    void openFileInEditor(base, entry.path);
  });

  return [item];
}

function renderTreeEntries(entries, container, depth, statusMap, base, renderToken) {
  if (!entries || entries.length === 0) {
    container.innerHTML = `<div class="file-tree-loading" style="padding-left:${8 + depth * 16}px">Empty</div>`;
    return;
  }

  for (const entry of entries) {
    const nodes = makeTreeItem(entry, depth, statusMap, base, renderToken);
    nodes.forEach((node) => container.appendChild(node));
  }
}

async function openProjectInEditor() {
  const base = getProjectPath();
  if (!base) return;
  try {
    await execCommand("code .", base);
  } catch {
    // No-op. The shell may be disabled or the editor command may not exist.
  }
}

async function openFileInEditor(base, filePath) {
  if (!base || !filePath) return;
  try {
    await execCommand(`code -g ${shellQuote(filePath)}`, base);
  } catch {
    // No-op. Clicking a file should fail softly in disabled shells.
  }
}

function focusSection() {
  if (!$.devProjectFilesSection) return;
  $.devProjectFilesSection.classList.add("is-focused");
  if (typeof $.devProjectFilesSection.scrollIntoView === "function") {
    $.devProjectFilesSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  $.devProjectFilesSearch?.focus();

  if (focusSection.timer) {
    clearTimeout(focusSection.timer);
  }
  focusSection.timer = setTimeout(() => {
    $.devProjectFilesSection?.classList.remove("is-focused");
  }, 2200);
}

function consumePendingFocus() {
  if (localStorage.getItem(FOCUS_STORAGE_KEY) !== "1") return;
  localStorage.removeItem(FOCUS_STORAGE_KEY);
  focusSection();
}

async function refreshFiles({ force = false } = {}) {
  const base = getProjectPath();
  const renderToken = ++currentRenderToken;

  if (!base) {
    updateHeader("", null);
    renderEmptyState("Select a project", "Files live under Forge and follow the selected project.");
    setActiveFile("");
    return;
  }

  if (force) {
    clearBaseCache(base);
  }

  if ($.devProjectFilesTree) {
    $.devProjectFilesTree.innerHTML = `<div class="file-tree-loading">Loading files...</div>`;
  }

  try {
    const status = await loadStatus(base, force);
    if (renderToken !== currentRenderToken) return;

    updateHeader(base, status, currentQuery);

    if (currentQuery) {
      const results = await searchFiles(base, currentQuery);
      if (renderToken !== currentRenderToken) return;

      if (!results?.length) {
        renderEmptyState("No matches", `No files matched "${currentQuery}".`);
        setActiveFile("");
        return;
      }

      if (!$.devProjectFilesTree) return;
      $.devProjectFilesTree.innerHTML = "";
      renderSearchResults(results, $.devProjectFilesTree, base, status.statusMap, renderToken);
      setActiveFile(activeFilePath);
      consumePendingFocus();
      return;
    }

    const rootEntries = await loadTree(base, "");
    if (renderToken !== currentRenderToken) return;

    if (!$.devProjectFilesTree) return;
    $.devProjectFilesTree.innerHTML = "";
    renderTreeEntries(rootEntries, $.devProjectFilesTree, 0, status.statusMap, base, renderToken);
    setActiveFile(activeFilePath);
    consumePendingFocus();
  } catch (error) {
    if (renderToken !== currentRenderToken) return;
    console.error("Failed to load dev project files:", error);
    renderEmptyState("Failed to load files", "Try refreshing the project tree.");
  }
}

function renderSearchResults(results, container, base, statusMap, renderToken) {
  for (const entry of results) {
    const item = document.createElement("div");
    item.className = "file-tree-item file-search-result dev-project-files-item";
    item.dataset.path = entry.path;
    item.dataset.type = entry.type;
    item.draggable = true;

    const status = statusMap.get(entry.path);
    const badge = status ? `<span class="dev-project-files-status">${escapeHtml(formatStatusBadge(status))}</span>` : "";
    const location = entry.path.includes("/")
      ? entry.path.slice(0, entry.path.lastIndexOf("/"))
      : ".";

    item.innerHTML = `${entry.type === "dir" ? FOLDER_SVG : FILE_SVG}<span class="file-search-name">${escapeHtml(entry.name || entry.path)}</span><span class="file-search-path">${escapeHtml(location)}</span>${badge}`;

    item.addEventListener("dragstart", (e) => {
      const fullPath = `${base}/${entry.path}`;
      e.dataTransfer?.setData("text/plain", fullPath);
      e.dataTransfer?.setData("application/x-file-path", fullPath);
      e.dataTransfer.effectAllowed = "copy";
      item.classList.add("dragging");
    });
    item.addEventListener("dragend", () => item.classList.remove("dragging"));

    if (entry.type !== "dir") {
      item.addEventListener("click", () => {
        setActiveFile(entry.path);
        void openFileInEditor(base, entry.path);
      });
    }

    container.appendChild(item);
  }
}

function handleSearchInput(event) {
  clearTimeout(searchTimer);
  const query = event.target.value.trim();
  searchTimer = setTimeout(() => {
    currentQuery = query;
    refreshFiles({ force: false });
  }, 220);
}

function handleProjectChange() {
  currentQuery = "";
  activeFilePath = "";
  clearTimeout(searchTimer);
  if ($.devProjectFilesSearch) {
    $.devProjectFilesSearch.value = "";
  }
  setActiveFile("");
  refreshFiles({ force: false });
}

function initDevProjectFiles() {
  if (!$.devProjectFilesSection || !$.devProjectFilesTree || !$.devProjectFilesSearch) return;

  $.devProjectFilesOpenBtn?.addEventListener("click", () => {
    void openProjectInEditor();
  });

  $.devProjectFilesRefreshBtn?.addEventListener("click", () => {
    refreshFiles({ force: true });
  });

  $.devProjectFilesSearch.addEventListener("input", handleSearchInput);
  $.projectSelect?.addEventListener("change", handleProjectChange);

  onBus("dev-project-files:focus", focusSection);
  onState("projectsData", () => {
    queueMicrotask(() => refreshFiles({ force: false }));
  });

  refreshFiles({ force: false });
}

initDevProjectFiles();
