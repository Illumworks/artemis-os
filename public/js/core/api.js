// All fetch() calls consolidated into named functions

// Global fetch interceptor — 401 redirect + CSRF token injection
const hasBrowserWindow = typeof window !== "undefined";

function _getCsrfToken() {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

const CSRF_MUTATING = new Set(["POST", "PUT", "DELETE", "PATCH"]);

if (
  hasBrowserWindow &&
  typeof window.fetch === "function" &&
  !window.__artemisFetchInterceptorInstalled
) {
  const _origFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const csrfToken = _getCsrfToken();
    if (csrfToken) {
      const [url, init = {}] = args;
      const method = (init.method || "GET").toUpperCase();
      if (CSRF_MUTATING.has(method)) {
        args = [url, { ...init, headers: { ...init.headers, "X-CSRF-Token": csrfToken } }];
      }
    }
    const res = await _origFetch(...args);
    const pathname = window.location?.pathname || "";
    if (res.status === 401 && !pathname.startsWith("/login") && window.location) {
      window.location.href = "/login";
    }
    return res;
  };
  window.__artemisFetchInterceptorInstalled = true;
}

export async function fetchProjects() {
  const res = await fetch("/api/projects");
  return res.json();
}

export async function fetchSessions(projectPath) {
  const url = projectPath
    ? `/api/sessions?project_path=${encodeURIComponent(projectPath)}`
    : "/api/sessions";
  const res = await fetch(url);
  return res.json();
}

export async function fetchSession(sessionId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || `Failed to load session ${sessionId}`);
  }
  return res.json();
}

export async function searchSessions(query, projectPath) {
  let url = `/api/sessions/search?q=${encodeURIComponent(query)}`;
  if (projectPath) url += `&project_path=${encodeURIComponent(projectPath)}`;
  const res = await fetch(url);
  return res.json();
}

export async function fetchActiveSessionIds() {
  const res = await fetch("/api/sessions/active");
  const data = await res.json();
  return data.activeSessionIds || [];
}

function _appendPaginationParams(url, { limit, before } = {}) {
  const params = new URLSearchParams();
  if (limit) params.set("limit", limit);
  if (before) params.set("before", before);
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

export async function fetchMessages(sessionId, opts) {
  const url = _appendPaginationParams(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages`, opts
  );
  const res = await fetch(url);
  return res.json();
}

export async function fetchMessagesByChatId(sessionId, chatId, opts) {
  const url = _appendPaginationParams(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(chatId)}`, opts
  );
  const res = await fetch(url);
  return res.json();
}

export async function fetchSingleMessages(sessionId, opts) {
  const url = _appendPaginationParams(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages-single`, opts
  );
  const res = await fetch(url);
  return res.json();
}

export async function fetchStats(projectPath) {
  const url = projectPath
    ? `/api/stats?project_path=${encodeURIComponent(projectPath)}`
    : "/api/stats";
  const res = await fetch(url);
  return res.json();
}

export async function fetchHomeData() {
  const res = await fetch("/api/stats/home");
  return res.json();
}

export async function fetchDashboard(projectPath) {
  const url = projectPath
    ? `/api/stats/dashboard?project_path=${encodeURIComponent(projectPath)}`
    : "/api/stats/dashboard";
  const res = await fetch(url);
  return res.json();
}

function _buildQueryString(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

async function _readJsonOrThrow(res, fallbackMessage) {
  if (res.ok) return res.json();
  const payload = await res.json().catch(() => ({}));
  throw new Error(payload.error || fallbackMessage || `Request failed with ${res.status}`);
}

export async function fetchMemoryList(projectPath, category = null) {
  const res = await fetch(`/api/memory${_buildQueryString({ project: projectPath, category })}`);
  return _readJsonOrThrow(res, "Failed to load memories");
}

export async function fetchMemorySearch(projectPath, query, limit = 20) {
  const res = await fetch(`/api/memory/search${_buildQueryString({ project: projectPath, q: query, limit })}`);
  return _readJsonOrThrow(res, "Failed to search memories");
}

export async function fetchMemoryTop(projectPath, limit = 10) {
  const res = await fetch(`/api/memory/top${_buildQueryString({ project: projectPath, limit })}`);
  return _readJsonOrThrow(res, "Failed to load top memories");
}

export async function fetchMemoryStats(projectPath) {
  const res = await fetch(`/api/memory/stats${_buildQueryString({ project: projectPath })}`);
  return _readJsonOrThrow(res, "Failed to load memory stats");
}

export async function fetchMemoryEmbeddingStatus() {
  const res = await fetch("/api/memory/embeddings/status");
  return _readJsonOrThrow(res, "Failed to load semantic memory status");
}

export async function ensureMemoryEmbeddings() {
  const res = await fetch("/api/memory/embeddings/ensure", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return _readJsonOrThrow(res, "Failed to install semantic memory model");
}

export async function exportMemoryArchiveApi({ includeRetrievalLogs = false } = {}) {
  const res = await fetch(`/api/memory/archive/export${_buildQueryString({ includeRetrievalLogs })}`);
  return _readJsonOrThrow(res, "Failed to export memory archive");
}

export async function createMemorySqliteBackupApi() {
  const res = await fetch("/api/memory/archive/sqlite-backup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return _readJsonOrThrow(res, "Failed to create memory backup");
}

export async function dryRunMemoryArchiveImportApi(archive) {
  const res = await fetch("/api/memory/archive/import/dry-run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ archive }),
  });
  return _readJsonOrThrow(res, "Failed to validate memory archive");
}

export async function applyMemoryArchiveImportApi(archive, { includeRetrievalLogs = false } = {}) {
  const res = await fetch("/api/memory/archive/import/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ archive, includeRetrievalLogs }),
  });
  return _readJsonOrThrow(res, "Failed to import memory archive");
}

export async function fetchMemoryEvidenceApi(observationId) {
  const res = await fetch(`/api/memory/${encodeURIComponent(observationId)}/evidence`);
  return _readJsonOrThrow(res, "Failed to load memory evidence");
}

export async function fetchMemoryDrawerApi(drawerId) {
  const res = await fetch(`/api/memory/drawer/${encodeURIComponent(drawerId)}`);
  return _readJsonOrThrow(res, "Failed to load memory drawer");
}

export async function fetchMemoryEntitiesApi(scopeKind, scopeId, { kind, limit } = {}) {
  const qs = _buildQueryString({ scopeKind, scopeId, kind: kind || undefined, limit: limit || undefined });
  const res = await fetch(`/api/memory/entities${qs ? `?${qs}` : ""}`);
  return _readJsonOrThrow(res, "Failed to load memory entities");
}

export async function fetchEntityNeighborhoodApi(entityId, hops = 1) {
  const res = await fetch(`/api/memory/entities/${encodeURIComponent(entityId)}/neighborhood?hops=${hops}`);
  return _readJsonOrThrow(res, "Failed to load entity neighborhood");
}

export async function fetchCampaignOpsOverview() {
  const res = await fetch("/api/campaign-ops/overview");
  return _readJsonOrThrow(res, "Failed to load Campaign Ops");
}

export async function decideCampaignCandidateApi(id, payload = {}) {
  const res = await fetch(`/api/campaign-ops/candidates/${encodeURIComponent(id)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to update campaign decision");
}

export async function promoteCampaignCandidateApi(id, payload = {}) {
  const res = await fetch(`/api/campaign-ops/candidates/${encodeURIComponent(id)}/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to promote campaign candidate");
}

export async function reopenCampaignCandidateApi(id, payload = {}) {
  const res = await fetch(`/api/campaign-ops/candidates/${encodeURIComponent(id)}/reopen`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to reopen campaign candidate");
}

export async function getCampaignCandidateApi(id) {
  const res = await fetch(`/api/campaign-ops/candidates/${encodeURIComponent(id)}`);
  return _readJsonOrThrow(res, "Failed to load campaign candidate");
}

export async function createCampaignWritingHandoffApi(id, payload = {}) {
  const res = await fetch(`/api/campaign-ops/candidates/${encodeURIComponent(id)}/writing-handoff`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create Writing Studio draft from campaign");
}

export async function fetchCalendarOverviewApi() {
  const res = await fetch("/api/calendar/overview");
  return _readJsonOrThrow(res, "Failed to load Calendar");
}

export async function fetchCalendarEventsApi(rangeStart, rangeEnd) {
  const params = new URLSearchParams({ rangeStart: rangeStart.toISOString(), rangeEnd: rangeEnd.toISOString() });
  const res = await fetch(`/api/calendar/events?${params}`);
  return _readJsonOrThrow(res, "Failed to load calendar events");
}

export async function fetchCalendarEventApi(id) {
  const res = await fetch(`/api/calendar/event/${encodeURIComponent(id)}`);
  return _readJsonOrThrow(res, "Failed to load calendar event");
}

export async function updateCalendarEventApi(id, patch) {
  const res = await fetch(`/api/calendar/event/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return _readJsonOrThrow(res, "Failed to update calendar event");
}

export async function deleteCalendarEventApi(id, sendUpdates = "all") {
  const params = new URLSearchParams({ sendUpdates });
  const res = await fetch(`/api/calendar/event/${encodeURIComponent(id)}?${params}`, { method: "DELETE" });
  return _readJsonOrThrow(res, "Failed to delete calendar event");
}

export async function createCalendarEventApi(payload) {
  const res = await fetch("/api/calendar/event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create calendar event");
}

export async function searchContactsApi(q) {
  const res = await fetch(`/api/google/contacts/search?q=${encodeURIComponent(q)}`);
  return _readJsonOrThrow(res, "Failed to search contacts");
}

export async function respondToCalendarEventApi(id, response) {
  const res = await fetch(`/api/calendar/event/${encodeURIComponent(id)}/respond`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ response }),
  });
  return _readJsonOrThrow(res, "Failed to respond to calendar event");
}

export async function fetchMeetingsOverviewApi() {
  const res = await fetch("/api/meetings/overview");
  return _readJsonOrThrow(res, "Failed to load Meetings");
}

export async function fetchJiraOverviewApi() {
  const res = await fetch("/api/jira/overview");
  return _readJsonOrThrow(res, "Failed to load Jira");
}

export async function disconnectJiraOAuthApi() {
  const res = await fetch("/api/jira/oauth-disconnect", { method: "POST" });
  return _readJsonOrThrow(res, "Failed to disconnect Jira");
}

export async function fetchJiraIssueApi(key) {
  const res = await fetch(`/api/jira/issue/${encodeURIComponent(key)}`);
  return _readJsonOrThrow(res, "Failed to load Jira issue");
}

export async function fetchJiraAssignableUsersApi(project) {
  const q = project ? `?project=${encodeURIComponent(project)}` : "";
  const res = await fetch(`/api/jira/assignable-users${q}`);
  return _readJsonOrThrow(res, "Failed to load assignable users");
}

export async function addJiraCommentApi(key, text, mentions = []) {
  const res = await fetch(`/api/jira/issue/${encodeURIComponent(key)}/comment`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, mentions }),
  });
  return _readJsonOrThrow(res, "Failed to add Jira comment");
}

export async function addJiraWorklogApi(key, hours, note) {
  const res = await fetch(`/api/jira/issue/${encodeURIComponent(key)}/worklog`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hours, note }),
  });
  return _readJsonOrThrow(res, "Failed to add Jira worklog");
}

export async function uploadJiraAttachmentApi(key, file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`/api/jira/issue/${encodeURIComponent(key)}/attachment`, {
    method: "POST",
    body: fd,
  });
  return _readJsonOrThrow(res, "Failed to upload Jira attachment");
}

export async function changeJiraAssigneeApi(key, accountId) {
  const res = await fetch(`/api/jira/issue/${encodeURIComponent(key)}/assignee`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accountId }),
  });
  return _readJsonOrThrow(res, "Failed to change Jira assignee");
}

export async function transitionJiraIssueApi(key, transitionId) {
  const res = await fetch(`/api/jira/issue/${encodeURIComponent(key)}/transition`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transitionId }),
  });
  return _readJsonOrThrow(res, "Failed to transition Jira issue");
}

export async function updateJiraDescriptionApi(key, description) {
  const res = await fetch(`/api/jira/issue/${encodeURIComponent(key)}/description`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ description }),
  });
  return _readJsonOrThrow(res, "Failed to update Jira description");
}

export async function createJiraIssueApi(payload) {
  const res = await fetch("/api/jira/issue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create Jira issue");
}

export async function fetchGranolaOverviewApi() {
  const res = await fetch("/api/granola/overview");
  return _readJsonOrThrow(res, "Failed to load Granola");
}

export async function saveGranolaConfigApi(data) {
  const res = await fetch("/api/granola/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _readJsonOrThrow(res, "Failed to save Granola config");
}

export async function disconnectGranolaOAuthApi() {
  const res = await fetch("/api/granola/oauth-disconnect", { method: "POST" });
  return _readJsonOrThrow(res, "Failed to disconnect Granola");
}

export async function fetchGranolaMeetingsApi(range = "last_30_days") {
  const res = await fetch(`/api/granola/meetings?range=${encodeURIComponent(range)}`);
  return _readJsonOrThrow(res, "Failed to load Granola meetings");
}

export async function searchGranolaMeetingsApi(query) {
  const res = await fetch("/api/granola/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  return _readJsonOrThrow(res, "Failed to search Granola meetings");
}

export async function fetchGranolaTranscriptApi(id) {
  const res = await fetch(`/api/granola/transcript/${encodeURIComponent(id)}`);
  return _readJsonOrThrow(res, "Failed to load transcript");
}

export async function fetchOkrOverviewApi() {
  const res = await fetch("/api/okr/overview");
  return _readJsonOrThrow(res, "Failed to load OKR data");
}

export async function fetchGoogleOverviewApi() {
  const res = await fetch("/api/google/overview");
  return _readJsonOrThrow(res, "Failed to load Google status");
}

export async function importGoogleDocApi(data = {}) {
  const res = await fetch("/api/google/docs/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _readJsonOrThrow(res, "Failed to import Google Doc");
}

export async function fetchSlackSignalsApi() {
  const res = await fetch("/api/slack/signals");
  return _readJsonOrThrow(res, "Failed to load Slack signals");
}

// ── Daily Brief ───────────────────────────────────────────────────────────────

export async function fetchLatestBriefApi() {
  const res = await fetch("/api/daily-brief");
  return _readJsonOrThrow(res, "Failed to load daily brief");
}

export async function generateBriefApi() {
  const res = await fetch("/api/daily-brief/generate", { method: "POST" });
  return _readJsonOrThrow(res, "Failed to generate daily brief");
}

export async function saveGoogleConfigApi(data) {
  const res = await fetch("/api/google/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _readJsonOrThrow(res, "Failed to save Google config");
}

export async function disconnectGoogleOAuthApi() {
  const res = await fetch("/api/google/oauth-disconnect", { method: "POST" });
  return _readJsonOrThrow(res, "Failed to disconnect Google");
}

export async function saveCalendarConfigApi(data) {
  const res = await fetch("/api/calendar/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _readJsonOrThrow(res, "Failed to save calendar config");
}

export async function logOkrActivityApi(text) {
  const res = await fetch("/api/okr/log-activity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return _readJsonOrThrow(res, "Failed to log OKR activity");
}

export async function updateOkrKrApi(id, data) {
  const res = await fetch(`/api/okr/kr/${encodeURIComponent(id)}/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _readJsonOrThrow(res, "Failed to update KR");
}

export async function dismissOkrNextUpApi(id) {
  const res = await fetch(`/api/okr/next-up/${encodeURIComponent(id)}/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return _readJsonOrThrow(res, "Failed to dismiss next-up item");
}

export async function suggestOkrKrProgressApi(id) {
  const res = await fetch(`/api/okr/kr/${encodeURIComponent(id)}/suggest-progress`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return _readJsonOrThrow(res, "Failed to get KR progress suggestion");
}

export async function extractOkrActivitiesApi(text) {
  const res = await fetch("/api/okr/extract-activity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return _readJsonOrThrow(res, "Failed to extract activities");
}

export async function bulkLogOkrActivitiesApi(entries) {
  const res = await fetch("/api/okr/bulk-log-activity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries }),
  });
  return _readJsonOrThrow(res, "Failed to bulk-log activities");
}

export async function updateOkrActivityApi(id, data = {}) {
  const res = await fetch(`/api/okr/activity/${encodeURIComponent(id)}/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return _readJsonOrThrow(res, "Failed to update OKR activity");
}

export async function generateEoyReviewApi(year, additionalContext) {
  const res = await fetch("/api/okr/eoy-review/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ year, additionalContext }),
  });
  return _readJsonOrThrow(res, "Failed to generate EOY review");
}

export async function previewOkrUpdateApi(text) {
  const res = await fetch("/api/okr/update/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return _readJsonOrThrow(res, "Failed to preview OKR update");
}

export async function commitOkrUpdateApi(previewId, overrides = []) {
  const res = await fetch("/api/okr/update/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ previewId, overrides }),
  });
  return _readJsonOrThrow(res, "Failed to commit OKR update");
}

export async function getOkrArchivedApi() {
  const res = await fetch("/api/okr/archived");
  return _readJsonOrThrow(res, "Failed to fetch archived OKRs");
}

export async function generateOkrNextUpApi() {
  const res = await fetch("/api/okr/next-up/generate", { method: "POST" });
  return _readJsonOrThrow(res, "Failed to generate Next Up recommendations");
}

export async function startOkrDeckGenerationApi() {
  const res = await fetch("/api/okr/deck/generate", { method: "POST" });
  return _readJsonOrThrow(res, "Failed to start deck generation");
}

export async function pollOkrDeckStatusApi(jobId) {
  const res = await fetch(`/api/okr/deck/status/${encodeURIComponent(jobId)}`);
  return _readJsonOrThrow(res, "Failed to poll deck status");
}

export async function fetchWritingStudioOverview() {
  const res = await fetch("/api/writing-studio/overview");
  return _readJsonOrThrow(res, "Failed to load Writing Studio");
}

export async function fetchWritingDraftsApi(params = {}) {
  const res = await fetch(`/api/writing-studio/drafts${_buildQueryString(params)}`);
  return _readJsonOrThrow(res, "Failed to load writing drafts");
}

export async function fetchWritingDraft(id) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}`);
  return _readJsonOrThrow(res, "Failed to load writing draft");
}

export async function assembleWritingPromptApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}/prompt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to assemble Writing Studio prompt");
}

export async function composeWritingDraftApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}/compose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to generate Writing Studio draft");
}

export async function createWritingDraftApi(payload = {}) {
  const res = await fetch("/api/writing-studio/drafts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create writing draft");
}

/**
 * Programmatic invocation — create (and optionally generate) a Writing Studio draft.
 * @param {object} payload { campaignId?, title?, assetType?, brief?, profileId?,
 *   metadata?, generateNow?, request?, providerId?, modelId? }
 * @returns {{ draftId, campaignId, title, status, ...generation fields }}
 */
export async function invokeWritingStudioApi(payload = {}) {
  const res = await fetch("/api/writing-studio/invoke", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to invoke Writing Studio");
}

export async function createWritingDraftFromGoogleDocApi(payload = {}) {
  const res = await fetch("/api/writing-studio/drafts/import-google-doc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create writing draft from Google Doc");
}

export async function importWritingDraftFromGoogleDocApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}/google-doc/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to import Google Doc into writing draft");
}

export async function unlinkWritingDraftGoogleDocApi(id) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}/google-doc/unlink`, {
    method: "POST",
  });
  return _readJsonOrThrow(res, "Failed to remove Google Doc link from writing draft");
}

export async function exportWritingDraftToGoogleDocApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}/google-doc/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to export writing draft to Google Doc");
}

export async function createWritingDraftLinkApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}/links`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create writing draft link");
}

export async function createWritingFolderApi(payload = {}) {
  const res = await fetch("/api/writing-studio/folders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create writing folder");
}

export async function updateWritingFolderApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/folders/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to update writing folder");
}

export async function deleteWritingFolderApi(id) {
  const res = await fetch(`/api/writing-studio/folders/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  return _readJsonOrThrow(res, "Failed to delete writing folder");
}

export async function exportWritingStudioSyncApi(payload = {}) {
  const res = await fetch("/api/writing-studio/sync/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to export Writing Studio sync files");
}

export async function importWritingStudioSyncApi(payload = {}) {
  const res = await fetch("/api/writing-studio/sync/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to import Writing Studio sync files");
}

export async function inspectWritingStudioSyncApi(payload = {}) {
  const res = await fetch("/api/writing-studio/sync/inspect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to inspect Writing Studio sync files");
}

export async function reconcileWritingStudioSyncApi(payload = {}) {
  const res = await fetch("/api/writing-studio/sync/reconcile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to reconcile Writing Studio sync conflict");
}

export async function updateWritingDraftApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to update writing draft");
}

export async function submitDraftForReviewApi(draftId) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(draftId)}/submit-for-review`, {
    method: "POST",
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || "submitDraftForReview failed");
  return body;
}

export async function regenerateDraftApi(draftId, { request, revisedContext, reviewerNote, providerId, modelId } = {}) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(draftId)}/regenerate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request, revisedContext, reviewerNote, providerId, modelId }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || "regenerateDraft failed");
  return body;
}

export async function getDraftEditHistoryApi(draftId) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(draftId)}/edit-history`);
  return _readJsonOrThrow(res, "Failed to fetch draft edit history");
}

export async function listCampaignDeliverablesApi(campaignId) {
  const res = await fetch(`/api/campaign-deliverables?campaignId=${encodeURIComponent(campaignId)}`);
  return _readJsonOrThrow(res, "Failed to fetch campaign deliverables");
}

export async function getCampaignDeliverableApi(id) {
  const res = await fetch(`/api/campaign-deliverables/${encodeURIComponent(id)}`);
  return _readJsonOrThrow(res, "Failed to fetch campaign deliverable");
}

export async function assembleCampaignBriefApi(candidateId, { assembledBy } = {}) {
  const res = await fetch(
    `/api/campaign-ops/candidates/${encodeURIComponent(candidateId)}/brief/assemble`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assembledBy: assembledBy ?? null }),
    },
  );
  return _readJsonOrThrow(res, "Failed to assemble campaign brief");
}

export async function getCampaignBriefApi(candidateId) {
  const res = await fetch(
    `/api/campaign-ops/candidates/${encodeURIComponent(candidateId)}/brief`,
  );
  if (res.status === 404) return null;
  return _readJsonOrThrow(res, "Failed to fetch campaign brief");
}

export async function deleteWritingDraftApi(id) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  return _readJsonOrThrow(res, "Failed to delete writing draft");
}

// ── Content Registry ──────────────────────────────────────────────────────

export async function listContentAssetsApi({ status, assetType, campaignFamily, includeArchived } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (assetType) params.set("assetType", assetType);
  if (campaignFamily) params.set("campaignFamily", campaignFamily);
  if (includeArchived) params.set("includeArchived", "true");
  const res = await fetch(`/api/content-assets?${params}`);
  return _readJsonOrThrow(res, "Failed to list content assets");
}

export async function createContentAssetApi(payload = {}) {
  const res = await fetch("/api/content-assets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create content asset");
}

export async function updateContentAssetApi(id, fields = {}) {
  const res = await fetch(`/api/content-assets/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  return _readJsonOrThrow(res, "Failed to update content asset");
}

export async function archiveContentAssetApi(id) {
  const res = await fetch(`/api/content-assets/${encodeURIComponent(id)}/archive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return _readJsonOrThrow(res, "Failed to archive content asset");
}

export async function listCampaignAssetLinksApi(campaignId) {
  const res = await fetch(`/api/content-assets/links?campaignId=${encodeURIComponent(campaignId)}`);
  return _readJsonOrThrow(res, "Failed to list campaign asset links");
}

export async function createCampaignAssetLinkApi({ campaignId, assetId, linkReason, linkedBy } = {}) {
  const res = await fetch("/api/content-assets/links", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ campaignId, assetId, linkReason, linkedBy }),
  });
  return _readJsonOrThrow(res, "Failed to link asset to campaign");
}

export async function deleteCampaignAssetLinkApi(campaignId, assetId) {
  const res = await fetch(
    `/api/content-assets/links/${encodeURIComponent(campaignId)}/${encodeURIComponent(assetId)}`,
    { method: "DELETE" },
  );
  if (!res.ok && res.status !== 204) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "deleteCampaignAssetLinkApi failed");
  }
}

export async function createWritingDraftVersionApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(id)}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to save writing draft version");
}

export async function createWritingTrainingCandidateApi(payload = {}) {
  const res = await fetch("/api/writing-studio/training-candidates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create writing training candidate");
}

export async function decideWritingTrainingCandidateApi(id, status) {
  const res = await fetch(`/api/writing-studio/training-candidates/${encodeURIComponent(id)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  return _readJsonOrThrow(res, "Failed to update writing training candidate");
}

export async function createWritingRuleApi(payload = {}) {
  const res = await fetch("/api/writing-studio/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create writing rule");
}

export async function updateWritingRuleApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/rules/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to update writing rule");
}

export async function updateWritingExampleApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/examples/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to update writing example");
}

export async function updateWritingSourceApi(id, payload = {}) {
  const res = await fetch(`/api/writing-studio/sources/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to update writing source");
}

export async function importWritingSeedApi({ seedDir } = {}) {
  const res = await fetch("/api/writing-studio/seed/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(seedDir ? { seedDir } : {}),
  });
  return _readJsonOrThrow(res, "Failed to import Writing Studio seed corpus");
}

export async function updateMemoryApi(id, content, category) {
  const res = await fetch(`/api/memory/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, category }),
  });
  return _readJsonOrThrow(res, "Failed to update memory");
}

export async function deleteMemoryApi(id) {
  const res = await fetch(`/api/memory/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  return _readJsonOrThrow(res, "Failed to delete memory");
}

export async function createMemoryApi(project, category, content) {
  const res = await fetch("/api/memory/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, category, content }),
  });
  return _readJsonOrThrow(res, "Failed to create memory");
}

export async function optimizeMemoryApi(project) {
  const res = await fetch("/api/memory/optimize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project }),
  });
  return _readJsonOrThrow(res, "Failed to optimize memories");
}

export async function applyOptimizationApi(project, optimized) {
  const res = await fetch("/api/memory/optimize/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project, optimized }),
  });
  return _readJsonOrThrow(res, "Failed to apply optimization");
}

export async function fetchPrompts() {
  const res = await fetch("/api/prompts");
  return res.json();
}

export async function createPrompt(title, description, prompt) {
  const res = await fetch("/api/prompts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, description, prompt }),
  });
  if (!res.ok) throw new Error("Failed to save");
  return res.json();
}

export async function deletePromptApi(idx) {
  const res = await fetch(`/api/prompts/${idx}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete");
  return res.json();
}

export async function fetchWorkflows() {
  const res = await fetch("/api/workflows");
  return res.json();
}

export async function createWorkflow(workflow) {
  const res = await fetch("/api/workflows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(workflow),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to create workflow");
  }
  return res.json();
}

export async function updateWorkflow(id, workflow) {
  const res = await fetch(`/api/workflows/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(workflow),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to update workflow");
  }
  return res.json();
}

export async function deleteWorkflowApi(id) {
  const res = await fetch(`/api/workflows/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to delete workflow");
  }
  return res.json();
}

export async function runWorkflowApi(id) {
  const res = await fetch(`/api/workflows/${encodeURIComponent(id)}/run`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || "Failed to start workflow run");
  }
  return res.json(); // { runId }
}

export async function listWorkflowRunsApi(id) {
  const res = await fetch(`/api/workflows/${encodeURIComponent(id)}/runs`);
  if (!res.ok) return [];
  return res.json();
}

export async function getLatestWorkflowRunApi(id) {
  const res = await fetch(`/api/workflows/${encodeURIComponent(id)}/runs/latest`);
  if (res.status === 404) return null;
  if (!res.ok) return null;
  return res.json();
}

export async function fetchAgents() {
  const res = await fetch("/api/agents");
  return res.json();
}

export async function createAgent(agent) {
  const res = await fetch("/api/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(agent),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to create agent");
  }
  return res.json();
}

export async function updateAgent(id, agent) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(agent),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to update agent");
  }
  return res.json();
}

export async function deleteAgentApi(id) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to delete agent");
  }
  return res.json();
}

// Agent Chains
export async function fetchChains() {
  const res = await fetch("/api/agents/chains");
  return res.json();
}

export async function createChain(chain) {
  const res = await fetch("/api/agents/chains", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(chain),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to create chain");
  }
  return res.json();
}

export async function updateChain(id, chain) {
  const res = await fetch(`/api/agents/chains/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(chain),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to update chain");
  }
  return res.json();
}

export async function fetchAgentContext(runId) {
  const res = await fetch(`/api/agents/context/${encodeURIComponent(runId)}`);
  return res.json();
}

// Agent DAGs
export async function fetchDags() {
  const res = await fetch("/api/agents/dags");
  return res.json();
}

export async function createDag(dag) {
  const res = await fetch("/api/agents/dags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dag),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to create DAG");
  }
  return res.json();
}

export async function updateDag(id, dag) {
  const res = await fetch(`/api/agents/dags/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dag),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to update DAG");
  }
  return res.json();
}

export async function deleteDagApi(id) {
  const res = await fetch(`/api/agents/dags/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to delete DAG");
  }
  return res.json();
}

export async function deleteChainApi(id) {
  const res = await fetch(`/api/agents/chains/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Failed to delete chain");
  }
  return res.json();
}

export async function browseFolders(dir) {
  const url = dir
    ? `/api/projects/browse?dir=${encodeURIComponent(dir)}`
    : "/api/projects/browse";
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error);
  }
  return res.json();
}

export async function addProject(name, path) {
  const res = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, path }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error);
  }
  return res.json();
}

export async function deleteProject(path) {
  const res = await fetch("/api/projects", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error);
  }
  return res.json();
}

export async function fetchProjectCommands(path) {
  const res = await fetch(`/api/projects/commands?path=${encodeURIComponent(path)}`);
  return res.json();
}

export async function fetchFiles(path) {
  const res = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
  return res.json();
}

export async function fetchFileContent(base, filePath) {
  const res = await fetch(`/api/files/content?base=${encodeURIComponent(base)}&path=${encodeURIComponent(filePath)}`);
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error);
  }
  return res.json();
}

export async function writeFileContent(base, filePath, content) {
  const res = await fetch("/api/files/content", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base, path: filePath, content }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error);
  }
  return res.json();
}

export async function fetchFileTree(base, dir = "") {
  let url = `/api/files/tree?base=${encodeURIComponent(base)}`;
  if (dir) url += `&dir=${encodeURIComponent(dir)}`;
  const res = await fetch(url);
  return res.json();
}

export async function searchFiles(base, query) {
  const url = `/api/files/search?base=${encodeURIComponent(base)}&q=${encodeURIComponent(query)}`;
  const res = await fetch(url);
  return res.json();
}

export async function fetchMcpServers(projectPath) {
  let url = "/api/mcp/servers";
  if (projectPath) url += `?project=${encodeURIComponent(projectPath)}`;
  const res = await fetch(url);
  return res.json();
}

export async function saveMcpServer(name, config, projectPath) {
  let url = `/api/mcp/servers/${encodeURIComponent(name)}`;
  if (projectPath) url += `?project=${encodeURIComponent(projectPath)}`;
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) throw new Error("Failed to save MCP server");
  return res.json();
}

export async function deleteMcpServer(name, projectPath) {
  let url = `/api/mcp/servers/${encodeURIComponent(name)}`;
  if (projectPath) url += `?project=${encodeURIComponent(projectPath)}`;
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete MCP server");
  return res.json();
}

export async function fetchAnalytics(projectPath) {
  const url = projectPath
    ? `/api/stats/analytics?project_path=${encodeURIComponent(projectPath)}`
    : "/api/stats/analytics";
  const res = await fetch(url);
  return res.json();
}

export async function fetchAccountInfo() {
  const res = await fetch("/api/account");
  return res.json();
}

export async function fetchProviderStatuses() {
  const res = await fetch("/api/stats/providers");
  return res.json();
}

export async function fetchSystemAlerts() {
  const res = await fetch("/api/stats/alerts");
  return res.json();
}

export async function fetchNotificationHistory({ limit = 20, offset = 0, unreadOnly = false, type = "" } = {}) {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (unreadOnly) params.set("unread_only", "true");
  if (type) params.set("type", type);
  const res = await fetch(`/api/notifications/history?${params.toString()}`);
  return res.json();
}

export async function fetchAgentMetrics() {
  const res = await fetch("/api/stats/agent-metrics");
  return res.json();
}

export async function updateSessionTitle(sessionId, title) {
  await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/title`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function deleteSessionApi(id) {
  await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function toggleSessionPin(sessionId) {
  await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/pin`, { method: "PUT" });
}

export async function generateSummary(sessionId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/summary`, { method: "POST" });
  return res.json();
}

export async function forkSession(sessionId, messageId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messageId }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || "Fork failed");
  }
  return res.json();
}

export async function fetchBranches(sessionId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/branches`);
  return res.json();
}

export async function fetchLineage(sessionId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/lineage`);
  return res.json();
}

export async function saveSystemPromptApi(path, systemPrompt) {
  await fetch("/api/projects/system-prompt", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, systemPrompt }),
  });
}

export async function execCommand(command, cwd) {
  const res = await fetch("/api/exec", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, cwd }),
  });
  const data = await res.json();
  if (!res.ok) {
    return {
      command,
      stdout: "",
      stderr: "",
      exitCode: 1,
      error: data.error || "Shell execution failed",
      code: data.code || "exec_error",
    };
  }
  return data;
}

// Tips
export async function fetchTips() {
  const res = await fetch("/api/tips");
  return res.json();
}

export async function fetchRssFeed(url) {
  const res = await fetch(`/api/tips/rss?url=${encodeURIComponent(url)}`);
  return res.json();
}

// ── Skills (Artemis-native skill library) ──────────────────────────

export async function fetchSkills({ status, category } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (category) params.set("category", category);
  const qs = params.toString();
  const res = await fetch(`/api/skills${qs ? `?${qs}` : ""}`);
  return res.json();
}

export async function fetchSkill(id) {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}`);
  return res.json();
}

export async function createSkillApi(data) {
  const res = await fetch("/api/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateSkillApi(id, data) {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function approveSkillApi(id) {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/approve`, { method: "POST" });
  return res.json();
}

export async function archiveSkillApi(id) {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/archive`, { method: "POST" });
  return res.json();
}

export async function deleteSkillApi(id) {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "Delete failed");
  }
}

export async function assignSkillApi(id, agentId) {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agentId }),
  });
  return res.json();
}

export async function unassignSkillApi(id, agentId) {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/unassign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agentId }),
  });
  return res.json();
}

export async function fetchSkillBySlug(slug) {
  const res = await fetch(`/api/skills/slug/${encodeURIComponent(slug)}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchSkillCategories() {
  const res = await fetch("/api/skills/categories");
  return res.json();
}

export async function fetchSkillTemplates() {
  const res = await fetch("/api/skills/templates");
  if (!res.ok) return [];
  return res.json();
}

export async function importSkillFromZip(file) {
  const ab = await file.arrayBuffer();
  const bytes = new Uint8Array(ab);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  const data = btoa(binary);
  const res = await fetch("/api/skills/import-zip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data, filename: file.name }),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || "ZIP import failed");
  }
  return res.json();
}

export async function importSkillFromUrl(url) {
  const res = await fetch("/api/skills/import-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || "Skill import failed");
  }
  return res.json();
}

export async function fetchBootstrapStatus() {
  const res = await fetch("/api/bootstrap/status");
  if (!res.ok) throw new Error("Bootstrap status fetch failed");
  return res.json();
}

export async function dismissWelcome() {
  const res = await fetch("/api/bootstrap/dismiss-welcome", { method: "POST" });
  if (!res.ok) throw new Error("Dismiss welcome failed");
  return res.json();
}

export async function refreshProviderStatuses() {
  const res = await fetch("/api/stats/providers/refresh", { method: "POST" });
  if (!res.ok) throw new Error("Provider refresh failed");
  return res.json();
}

// ── Agent run observability ───────────────────────────────────────────────────

export async function fetchActiveAgentRuns() {
  const res = await fetch("/api/agents/runs/active");
  if (!res.ok) throw new Error("fetchActiveAgentRuns failed");
  return res.json();
}

export async function fetchRecentAgentRuns(limit = 50) {
  const res = await fetch(`/api/agents/runs/recent?limit=${limit}`);
  if (!res.ok) throw new Error("fetchRecentAgentRuns failed");
  return res.json();
}

export async function searchAgentRunsApi({ q, date, limit = 20 } = {}) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (date) params.set("date", date);
  if (limit !== 20) params.set("limit", String(limit));
  const res = await fetch(`/api/agents/runs/search?${params}`);
  if (!res.ok) throw new Error("searchAgentRunsApi failed");
  return res.json();
}

export async function fetchAgentRunById(runId) {
  const res = await fetch(`/api/agents/runs/${encodeURIComponent(runId)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("fetchAgentRunById failed");
  return res.json();
}

// ── Automations ───────────────────────────────────────────────────────────────

export async function listAutomationsApi() {
  const res = await fetch("/api/automations");
  if (!res.ok) throw new Error("listAutomationsApi failed");
  return res.json();
}

export async function createAutomationApi(data) {
  const res = await fetch("/api/automations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "createAutomationApi failed");
  }
  return res.json();
}

export async function updateAutomationApi(id, data) {
  const res = await fetch(`/api/automations/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "updateAutomationApi failed");
  }
  return res.json();
}

export async function deleteAutomationApi(id) {
  const res = await fetch(`/api/automations/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw new Error("deleteAutomationApi failed");
  return res.json();
}

export async function runAutomationApi(id, opts = {}) {
  const res = await fetch(`/api/automations/${encodeURIComponent(id)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "runAutomationApi failed");
  }
  return res.json();
}

export async function listAutomationRunsApi(id, { limit = 20, cursor } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", String(cursor));
  const res = await fetch(`/api/automations/${encodeURIComponent(id)}/runs?${params}`);
  if (!res.ok) throw new Error("listAutomationRunsApi failed");
  return res.json();
}

// ── Approvals ─────────────────────────────────────────────────────────────────

export async function listApprovalsApi({ status, targetType, limit = 50 } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (targetType) params.set("target_type", targetType);
  params.set("limit", String(limit));
  const res = await fetch(`/api/approvals?${params}`);
  if (!res.ok) throw new Error("listApprovalsApi failed");
  return res.json();
}

export async function decideApprovalApi(id, { decision, note, reviewer } = {}) {
  const res = await fetch(`/api/approvals/${encodeURIComponent(id)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, note, reviewer }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "decideApprovalApi failed");
  }
  return res.json();
}

// ── Agent Packages Lite helpers ───────────────────────────────────────────

export async function getAgentEnrichedApi(id) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error("getAgentEnrichedApi failed");
  return res.json();
}

export async function getAgentInstructionApi(id) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}/instruction`);
  if (!res.ok) throw new Error("getAgentInstructionApi failed");
  return res.json();
}

export async function saveAgentInstructionApi(id, content) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}/instruction`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "saveAgentInstructionApi failed");
  }
  return res.json();
}

export async function deleteAgentInstructionApi(id) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}/instruction`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "deleteAgentInstructionApi failed");
  }
  return res.json();
}

export async function listAgentFilesApi(id) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}/files`);
  if (!res.ok) throw new Error("listAgentFilesApi failed");
  return res.json();
}

export async function listAgentSkillsApi(id) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}/skills`);
  if (!res.ok) throw new Error("listAgentSkillsApi failed");
  return res.json();
}

// ── Signal Criteria / Scout Ruleset Lite ──────────────────────────────────

export async function listReasonCodesApi({ includeRetired = false } = {}) {
  const qs = includeRetired ? "?includeRetired=true" : "";
  const res = await fetch(`/api/signal-criteria/reason-codes${qs}`);
  if (!res.ok) throw new Error("listReasonCodesApi failed");
  return res.json();
}

export async function listCampaignRulesetsApi() {
  const res = await fetch("/api/signal-criteria/rulesets");
  if (!res.ok) throw new Error("listCampaignRulesetsApi failed");
  return res.json();
}

export async function getCampaignRulesetApi(family) {
  const res = await fetch(`/api/signal-criteria/rulesets/${encodeURIComponent(family)}`);
  if (!res.ok) throw new Error("getCampaignRulesetApi failed");
  return res.json();
}

export async function listRulesetVersionsApi(family) {
  const res = await fetch(`/api/signal-criteria/rulesets/${encodeURIComponent(family)}/versions`);
  if (!res.ok) throw new Error("listRulesetVersionsApi failed");
  return res.json();
}

export async function createRulesetVersionApi(family, payload = {}) {
  const res = await fetch(`/api/signal-criteria/rulesets/${encodeURIComponent(family)}/versions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "createRulesetVersionApi failed");
  }
  return res.json();
}

export async function activateRulesetVersionApi(family, version) {
  const res = await fetch(
    `/api/signal-criteria/rulesets/${encodeURIComponent(family)}/versions/${version}/activate`,
    { method: "POST" }
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "activateRulesetVersionApi failed");
  }
  return res.json();
}

export async function getTerritoryConfigApi(family) {
  const res = await fetch(`/api/signal-criteria/territory/${encodeURIComponent(family)}`);
  if (!res.ok) throw new Error("getTerritoryConfigApi failed");
  return res.json();
}

export async function upsertTerritoryStateApi(family, stateCode, payload = {}) {
  const res = await fetch(
    `/api/signal-criteria/territory/${encodeURIComponent(family)}/${encodeURIComponent(stateCode)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "upsertTerritoryStateApi failed");
  }
  return res.json();
}

// ── Signal Queue ──────────────────────────────────────────────────────────

export async function listSignalQueueApi(params = {}) {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.campaignFamily) qs.set("campaignFamily", params.campaignFamily);
  if (params.urgencyTier) qs.set("urgencyTier", params.urgencyTier);
  if (params.stateCode) qs.set("stateCode", params.stateCode);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.cursor) qs.set("cursor", params.cursor);
  const query = qs.toString() ? `?${qs}` : "";
  const res = await fetch(`/api/signal-queue${query}`);
  if (!res.ok) throw new Error("listSignalQueueApi failed");
  return res.json();
}

export async function createSignalApi(payload = {}) {
  const res = await fetch("/api/signal-queue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "createSignalApi failed");
  }
  return res.json();
}

export async function getSignalApi(id) {
  const res = await fetch(`/api/signal-queue/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error("getSignalApi failed");
  return res.json();
}

export async function approveSignalApi(id) {
  const res = await fetch(`/api/signal-queue/${encodeURIComponent(id)}/approve`, {
    method: "POST",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "approveSignalApi failed");
  }
  return res.json();
}

export async function rejectSignalApi(id, payload = {}) {
  const res = await fetch(`/api/signal-queue/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "rejectSignalApi failed");
  }
  return res.json();
}

export async function snoozeSignalApi(id, payload = {}) {
  const res = await fetch(`/api/signal-queue/${encodeURIComponent(id)}/snooze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "snoozeSignalApi failed");
  }
  return res.json();
}

export async function archiveSignalApi(id) {
  const res = await fetch(`/api/signal-queue/${encodeURIComponent(id)}/archive`, {
    method: "POST",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "archiveSignalApi failed");
  }
  return res.json();
}

export async function qualifySignalApi(id) {
  const res = await fetch(`/api/signal-queue/${encodeURIComponent(id)}/qualify`, {
    method: "POST",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "qualifySignalApi failed");
  return body;
}

export async function submitSignalIntakeApi(payload = {}) {
  const res = await fetch("/api/signal-queue/intake", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "submitSignalIntakeApi failed");
  return body;
}

// ── Scout Harness API helpers ──────────────────────────────────────────────

export async function listScoutPackagesApi() {
  const res = await fetch("/api/scouts/packages");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "listScoutPackagesApi failed");
  }
  return res.json();
}

export async function listScoutRunsApi({ scoutType, status, limit, offset } = {}) {
  const params = new URLSearchParams();
  if (scoutType) params.set("scoutType", scoutType);
  if (status) params.set("status", status);
  if (limit !== undefined) params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  const qs = params.toString();
  const res = await fetch(`/api/scouts/runs${qs ? `?${qs}` : ""}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "listScoutRunsApi failed");
  }
  return res.json();
}

export async function getScoutRunApi(id) {
  const res = await fetch(`/api/scouts/runs/${encodeURIComponent(id)}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "getScoutRunApi failed");
  }
  return res.json();
}

export async function runScoutHarnessApi(payload = {}) {
  const res = await fetch("/api/scouts/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "runScoutHarnessApi failed");
  return body;
}
