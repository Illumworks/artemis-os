import { escapeHtml } from "../core/utils.js";
import { renderSessionRow } from "./dev-projects-session-row.js";

export function renderProjectOptions(projects, activeProjectId) {
  if (!projects.length) {
    return `<div class="dev-empty-small">No projects yet</div>`;
  }
  return projects.map((project) => `
    <button class="dev-project-row${Number(project.id) === Number(activeProjectId) ? " active" : ""}" data-project-id="${project.id}">
      <span class="dev-project-name">${escapeHtml(project.name)}</span>
      <span class="dev-project-path">${escapeHtml(project.path)}</span>
    </button>
  `).join("");
}

export function renderSessionList(sessions, activeSessionId) {
  if (!sessions.length) {
    return `<div class="dev-empty-small">No sessions yet</div>`;
  }
  return sessions.map((session) => renderSessionRow(session, activeSessionId)).join("");
}

