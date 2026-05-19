import { escapeHtml } from "../core/utils.js";
import { renderSessionRow } from "./dev-projects-session-row.js";

const FOLDER_ICON = `
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>
  </svg>
`;

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

export function renderDevProjectsRail({
  projects,
  sessionsByProject,
  activeProjectId,
  activeSessionId,
  expandedProjectIds,
  visibleCounts,
  showArchived,
  sortMode,
}) {
  if (!projects.length) {
    return `
      <div class="dev-project-empty">
        <button class="dev-empty-cta" type="button" data-dev-action="open-project-modal">Add your first project</button>
      </div>
    `;
  }

  const visibleProjects = projects.filter((project) => showArchived || !project.archived_at);
  const sortedProjects = [...visibleProjects].sort((a, b) => {
    if (sortMode === "name") return String(a.name).localeCompare(String(b.name));
    return new Date(b.last_opened_at || 0) - new Date(a.last_opened_at || 0);
  });

  return sortedProjects.map((project) => {
    const expanded = expandedProjectIds.has(Number(project.id));
    const sessions = sessionsByProject.get(Number(project.id)) || [];
    const visibleSessions = sessions.filter((session) => showArchived || !session.archived_at);
    const limit = visibleCounts.get(Number(project.id)) || 5;
    const limitedSessions = visibleSessions.slice(0, limit);
    const archivedCount = sessions.filter((session) => session.archived_at).length;
    const active = Number(project.id) === Number(activeProjectId);
    const empty = !visibleSessions.length;
    return `
      <section class="dev-project-folder${active ? " active" : ""}${project.archived_at ? " archived" : ""}" data-project-id="${project.id}">
        <div class="dev-project-folder-row" data-project-toggle="${project.id}">
          <span class="dev-project-caret" aria-hidden="true">${expanded ? "▾" : "▸"}</span>
          <span class="dev-project-folder-icon">${FOLDER_ICON}</span>
          <span class="dev-project-folder-name">${escapeHtml(project.name)}</span>
          <button class="dev-project-new-session" type="button" title="New session" data-new-session="${project.id}">+</button>
          <button class="dev-project-more" type="button" title="Project actions" data-project-menu="${project.id}">⋯</button>
        </div>
        ${expanded ? `
          <div class="dev-project-session-list">
            ${empty ? `
              <button type="button" class="dev-start-session" data-new-session="${project.id}">
                ${archivedCount ? "All sessions archived. Show archived?" : "Start a new session"}
              </button>
            ` : limitedSessions.map((session) => renderSessionRow(session, activeSessionId)).join("")}
            ${visibleSessions.length > limit ? `
              <button type="button" class="dev-show-more" data-show-more="${project.id}">Show more</button>
            ` : ""}
          </div>
        ` : ""}
      </section>
    `;
  }).join("");
}
