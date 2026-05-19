import { escapeHtml } from "../core/utils.js";

function renderEntries(entries = [], focusedIndex = 0) {
  if (!entries.length) return `<div class="dev-folder-empty">No subfolders</div>`;
  return entries.map((entry, index) => `
    <button
      type="button"
      class="dev-folder-item ${index === focusedIndex ? "focused" : ""}"
      data-folder-path="${escapeHtml(entry.path)}"
      data-folder-index="${index}"
      aria-current="${index === focusedIndex ? "true" : "false"}"
    >
      <span class="dev-folder-name">📁 ${escapeHtml(entry.name)}</span>
      ${entry.is_git_repo ? `<span class="dev-folder-git">(git)</span>` : ""}
    </button>
  `).join("");
}

export function renderProjectModal({
  currentPath = "",
  parentPath = null,
  entries = [],
  name = "",
  error = "",
  loading = false,
  focusedIndex = 0,
  manualFallback = false,
  manualPath = "",
} = {}) {
  const canUseFolder = Boolean(currentPath) || Boolean(manualPath);
  const body = manualFallback ? `
    <label class="dev-field">
      <span>Project folder</span>
      <input
        id="dev-project-manual-path-input"
        class="dev-text-input"
        autocomplete="off"
        spellcheck="false"
        value="${escapeHtml(manualPath)}"
        placeholder="/Users/artemis/Desktop/Artemis/artemis-os"
      >
    </label>
  ` : `
    <div class="dev-folder-browser" aria-busy="${loading ? "true" : "false"}">
      <div class="dev-folder-toolbar">
        <div class="dev-folder-breadcrumb" title="${escapeHtml(currentPath)}">${escapeHtml(currentPath || "Loading home folder...")}</div>
        <button type="button" class="dev-btn dev-folder-up" data-folder-up ${parentPath ? "" : "disabled"}>Up</button>
      </div>
      ${error ? `<div class="dev-modal-error">${escapeHtml(error)}</div>` : ""}
      ${loading ? `<div class="dev-folder-loading">Loading folders...</div>` : ""}
      <div class="dev-folder-list" role="listbox" aria-label="Project folders">
        ${renderEntries(entries, focusedIndex)}
      </div>
    </div>
  `;

  return `
    <div class="dev-modal-backdrop" data-close-project-modal>
      <div class="dev-modal" role="dialog" aria-modal="true" aria-labelledby="dev-project-modal-title">
        <div class="dev-modal-head">
          <h2 id="dev-project-modal-title">Add a project</h2>
          <button type="button" class="dev-icon-btn" data-close-project-modal aria-label="Close">×</button>
        </div>
        <div class="dev-modal-body">
          ${body}
          ${manualFallback && error ? `<div class="dev-modal-error">${escapeHtml(error)}</div>` : ""}
          <label class="dev-field">
            <span>Project name</span>
            <input id="dev-project-name-input" class="dev-text-input" autocomplete="off" value="${escapeHtml(name)}">
          </label>
        </div>
        <div class="dev-modal-foot">
          <button type="button" class="dev-btn" data-close-project-modal>Cancel</button>
          <button type="button" class="dev-btn primary" id="dev-create-project-confirm" ${canUseFolder ? "" : "disabled"}>Use this folder</button>
        </div>
      </div>
    </div>
  `;
}
