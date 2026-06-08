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

// M6 — Memory shell read endpoints
export async function fetchMemoryShellStats() {
  const res = await fetch("/api/memory/stats");
  return _readJsonOrThrow(res, "Failed to load memory stats");
}

export async function fetchMemoryShellScopes() {
  const res = await fetch("/api/memory/scopes");
  return _readJsonOrThrow(res, "Failed to load memory scopes");
}

export async function fetchMemoryShellDrawers({ scopeKind, scopeId, limit = 50, offset = 0 } = {}) {
  const qs = _buildQueryString({ scope_kind: scopeKind, scope_id: scopeId, limit, offset });
  const res = await fetch(`/api/memory/drawers${qs}`);
  return _readJsonOrThrow(res, "Failed to load memory drawers");
}

export async function fetchMemoryShellObservations({ scopeKind, scopeId, limit = 50, offset = 0 } = {}) {
  const qs = _buildQueryString({ scope_kind: scopeKind, scope_id: scopeId, limit, offset });
  const res = await fetch(`/api/memory/observations${qs}`);
  return _readJsonOrThrow(res, "Failed to load memory observations");
}

export async function fetchMemoryShellObservationDetail(observationId) {
  const res = await fetch(`/api/memory/observations/${encodeURIComponent(observationId)}`);
  return _readJsonOrThrow(res, "Failed to load observation detail");
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

export async function fetchMarketingCampaignsApi() {
  const res = await fetch("/api/marketing/campaigns");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "fetchMarketingCampaignsApi failed");
  }
  const data = await res.json();
  if (Array.isArray(data)) return data;
  return data.campaigns || data.items || [];
}

export async function fetchCampaignOpsOverview() {
  const res = await fetch("/api/campaign-ops/candidates");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "fetchCampaignOpsOverview failed");
  }
  const data = await res.json();
  const candidates = Array.isArray(data) ? data : (data.candidates || data.items || []);
  return { campaigns: candidates };
}

export async function decideCampaignCandidateApi(id, payload = {}) {
  // E1b: Python uses /advance (not /decision). Payload shape unchanged.
  const res = await fetch(`/api/campaign-ops/candidates/${encodeURIComponent(id)}/advance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to update campaign decision");
}

// E1b: Python uses /advance with stage payload instead of dedicated /promote and /reopen.
export async function promoteCampaignCandidateApi(id, payload = {}) {
  return decideCampaignCandidateApi(id, { action: "promote", ...payload });
}

export async function reopenCampaignCandidateApi(id, payload = {}) {
  return decideCampaignCandidateApi(id, { action: "reopen", ...payload });
}

export async function getCampaignCandidateApi(id) {
  const res = await fetch(`/api/campaign-ops/candidates/${encodeURIComponent(id)}`);
  return _readJsonOrThrow(res, "Failed to load campaign candidate");
}

// TODO E1b: not yet ported — /writing-handoff does not exist in Python (C2/C3/C4).
// The writing-handoff concept is partially handled via campaign-deliverables POST.
// Flag for Lead review: callers should use createCampaignDeliverableApi instead.
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
  const res = await fetch(`/api/people/search?q=${encodeURIComponent(q)}`);
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

export async function fetchPersonalTodosApi(includeDone = false) {
  const url = includeDone ? "/api/todos?include_done=true" : "/api/todos";
  const res = await fetch(url);
  return _readJsonOrThrow(res, "Failed to load personal todos");
}

export async function markPersonalTodoDoneApi(id) {
  const res = await fetch(`/api/todos/${encodeURIComponent(id)}/done`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return _readJsonOrThrow(res, "Failed to mark todo done");
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

export async function fetchSlackMentionsApi() {
  const res = await fetch("/api/slack/signals/mentions");
  return _readJsonOrThrow(res, "Failed to load Slack mentions");
}

export async function resolveSlackMentionApi(eventId) {
  const res = await fetch(`/api/slack/signals/mentions/${encodeURIComponent(eventId)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return _readJsonOrThrow(res, "Failed to resolve Slack mention");
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
  const res = await fetch("/api/okr/activity", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return _readJsonOrThrow(res, "Failed to log OKR activity");
}

export async function updateOkrKrApi(id, data) {
  const res = await fetch(`/api/okr/key-results/${encodeURIComponent(id)}`, {
    method: "PATCH",
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
  const res = await fetch(`/api/okr/key-results/${encodeURIComponent(id)}/suggest-progress`, {
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
  const res = await fetch(`/api/okr/activity/${encodeURIComponent(id)}`, {
    method: "PATCH",
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
  const res = await fetch("/api/okr/objectives?include_archived=true");
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

// TODO E1b: not yet ported — /writing-studio/overview does not exist in Python (C4).
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

/**
 * Rewrite a selected text span using the tag-scoped rules for the draft.
 * Composer Stage 2 — powers the floating selection toolbar.
 *
 * @param {number|string} id  — draft id
 * @param {{ selectedText: string, instruction: string, fullText?: string }} payload
 * @returns {{ rewrittenText: string, trace: object }}
 */
export async function rewriteSpanApi(id, payload = {}) {
  const res = await fetch(
    `/api/writing-studio/drafts/${encodeURIComponent(id)}/rewrite-span`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
  return _readJsonOrThrow(res, "Failed to rewrite selected span");
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

export async function listWritingTrainingCandidatesApi(status) {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await fetch(`/api/writing-studio/training-candidates${qs}`);
  return _readJsonOrThrow(res, "Failed to load writing training candidates");
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
  return _readJsonOrThrow(res, "Failed to record training candidate decision");
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
  // E1b: Python endpoint is /submit-review (not /submit-for-review).
  const res = await fetch(`/api/writing-studio/drafts/${encodeURIComponent(draftId)}/submit-review`, {
    method: "POST",
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || "submitDraftForReview failed");
  return body;
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

// TODO E1b: not yet ported — GET /brief does not exist in Python (only POST /brief/assemble).
export async function getCampaignBriefApi(candidateId) {
  const res = await fetch(
    `/api/campaign-ops/candidates/${encodeURIComponent(candidateId)}/brief`,
  );
  if (res.status === 404) return null;
  return _readJsonOrThrow(res, "Failed to fetch campaign brief");
}

export async function getCampaignInitiationProposalApi(candidateId) {
  const res = await fetch(
    `/api/marketing/campaigns/${encodeURIComponent(candidateId)}/initiation-proposal`,
  );
  return _readJsonOrThrow(res, "Failed to fetch campaign initiation proposal");
}

// Marketing Intelligence Phase 1 — Decision 2 prioritization ranking.
// Read-only; returns velocity_ranking, time_sensitive, and the merged `combined`
// list. Optional state filter narrows both lists to one 2-letter state.
export async function fetchMarketingPrioritizationApi({
  windowDays,
  horizonDays,
  limit,
  state,
} = {}) {
  const params = new URLSearchParams();
  if (Number.isFinite(windowDays)) params.set("window_days", String(windowDays));
  if (Number.isFinite(horizonDays)) params.set("horizon_days", String(horizonDays));
  if (Number.isFinite(limit)) params.set("limit", String(limit));
  if (state) params.set("state", String(state).toUpperCase().slice(0, 2));
  const qs = params.toString();
  const url = qs
    ? `/api/marketing/intel/prioritization?${qs}`
    : "/api/marketing/intel/prioritization";
  const res = await fetch(url);
  return _readJsonOrThrow(res, "Failed to fetch marketing prioritization");
}

export async function initiateCampaignApi(candidateId, payload = {}) {
  const res = await fetch(
    `/api/marketing/campaigns/${encodeURIComponent(candidateId)}/initiate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return _readJsonOrThrow(res, "Failed to initiate campaign");
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

// ── F3: Python response-shape adapters ───────────────────────────────────────
/** Derive a stable slug from a display name (lowercase, hyphens, no specials) */
function _slugify(name = "") {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "item";
}
// Python F2a CRUD endpoints return Pydantic-serialized models with camelCase
// aliases (via model_dump(by_alias=True)) but list endpoints wrap the array in
// a keyed envelope: { agents: [...] }, { workflows: [...] }, etc.
// The feature modules (agents.js, workflows.js, dag-editor.js) expect flat
// arrays of objects whose shape matches the *Node* schema: id, title, agents
// (chains), edges (DAGs), etc.  These small normaliser functions sit here so
// the feature modules need zero changes.

/** Map a Python AgentRead → Node agent shape */
function _normaliseAgent(a) {
  // Preserve the Postgres int PK as `dbId` — needed by callers that have to
  // address the agent by its row id (e.g. builder_sessions.target_id is INT).
  // The slug overrides `id` for legacy Node-shape compatibility.
  const dbId = typeof a.id === "number" ? a.id : (a.dbId ?? null);
  return {
    ...a,
    // Python: agentId / name → Node: id / title
    id: a.agentId ?? a.id,
    dbId,
    title: a.name ?? a.title ?? a.agentId ?? "",
    reasonCodesEmitted: a.reasonCodesEmitted ?? a.reason_codes_emitted ?? [],
  };
}

// Historical note: _normaliseWorkflow was removed with the PIPE6 frontend
// prune; workflow payloads are now dead front-end surface area.

/** Map a Python AgentChainRead → Node chain shape
 *  Python stores per-step agent refs inside steps[].agentId; Node stores a
 *  flat agents[] array of IDs.  We coerce here so the chain modal can
 *  populate its agent-picker rows without backend changes.
 */
function _normaliseChain(c) {
  const agents = (c.steps || [])
    .map((s) => s.agentId ?? s.agent_id ?? s)
    .filter((v) => v && typeof v === "string");
  return {
    ...c,
    id: c.chainId ?? c.id,
    title: c.name ?? c.title ?? c.chainId ?? "",
    agents,
  };
}

/** Map a Python AgentDagRead → Node dag shape
 *  Python stores adjacency in nodes[].deps (or nodes[].edges); Node stores
 *  separate nodes[] + edges[].  We materialise edges from deps if present.
 */
function _normaliseDag(d) {
  const rawNodes = d.nodes || [];
  // Build edges from per-node deps array if edges not already present
  const edges = d.edges
    ? d.edges
    : rawNodes.flatMap((n) =>
        (n.deps || []).map((dep) => ({ from: dep, to: n.id ?? n.agentId }))
      );
  const nodes = rawNodes.map((n) => ({
    id: n.id ?? n.agentId,
    agentId: n.agentId ?? n.id,
    x: n.x ?? 0,
    y: n.y ?? 0,
    ...n,
  }));
  return {
    ...d,
    id: d.dagId ?? d.id,
    title: d.name ?? d.title ?? d.dagId ?? "",
    nodes,
    edges,
  };
}

export async function fetchAgents() {
  const res = await fetch("/api/agents");
  const body = await res.json();
  // Python returns { agents: [...] }; Node expected a flat array
  const raw = Array.isArray(body) ? body : (body.agents ?? []);
  return raw.map(_normaliseAgent);
}

export async function createAgent(agent) {
  // Map Node shape → Python AgentCreate
  const payload = {
    agentId: agent.id ?? agent.agentId ?? _slugify(agent.title),
    name: agent.title ?? agent.name,
    description: agent.description || "",
    goal: agent.goal || "",
    system_prompt: agent.systemPrompt ?? agent.system_prompt ?? "",
    tools: agent.tools || [],
    model: agent.model || "claude-sonnet-4-5",
    provider: agent.provider || "anthropic",
    fallbackProvider: agent.fallbackProvider || agent.provider || "anthropic",
    fallbackModel: agent.fallbackModel || agent.model || "claude-sonnet-4-5",
    max_iterations: agent.constraints?.maxTurns ?? agent.maxIterations ?? 50,
    reasonCodesEmitted: agent.reasonCodesEmitted || [],
  };
  const res = await fetch("/api/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to create agent");
  }
  return _normaliseAgent(await res.json());
}

export async function updateAgent(id, agent) {
  // Python uses PATCH; Node used PUT.
  const payload = {};
  if (agent.title != null || agent.name != null) payload.name = agent.title ?? agent.name;
  if (agent.description != null) payload.description = agent.description;
  if (agent.goal != null) payload.goal = agent.goal;
  if (agent.systemPrompt != null || agent.system_prompt != null) payload.system_prompt = agent.systemPrompt ?? agent.system_prompt;
  if (agent.tools != null) payload.tools = agent.tools;
  if (agent.model != null) payload.model = agent.model;
  if (agent.provider != null) payload.provider = agent.provider;
  if (agent.fallbackProvider != null) payload.fallbackProvider = agent.fallbackProvider;
  if (agent.fallbackModel != null) payload.fallbackModel = agent.fallbackModel;
  if (agent.constraints?.maxTurns != null) payload.max_iterations = agent.constraints.maxTurns;
  if (agent.metadata != null) payload.metadata = agent.metadata;
  if (agent.reasonCodesEmitted != null) payload.reasonCodesEmitted = agent.reasonCodesEmitted;
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to update agent");
  }
  return _normaliseAgent(await res.json());
}

export async function deleteAgentApi(id) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to delete agent");
  }
  // Python returns 204 No Content — no JSON body
}

export async function runAgentApi(id) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}/run`, {
    method: "POST",
  });
  if (res.status === 404) {
    // F2b execution endpoints not yet wired — graceful no-op
    return { __notYetWired: true, message: "Run not yet wired (Phase F2b in progress)" };
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to start agent run");
  }
  return res.json();
}

// Agent Chains — Python prefix is /api/agent-chains (not /api/agents/chains)
export async function fetchChains() {
  const res = await fetch("/api/agent-chains");
  const body = await res.json();
  const raw = Array.isArray(body) ? body : (body.chains ?? []);
  return raw.map(_normaliseChain);
}

export async function createChain(chain) {
  const payload = {
    chainId: chain.id ?? chain.chainId ?? _slugify(chain.title),
    name: chain.title ?? chain.name,
    description: chain.description || "",
    // Convert flat agents[] of IDs → steps[{ agentId }]
    steps: (chain.agents || []).map((agentId) => ({ agentId, contextPassing: chain.contextPassing ?? "summary" })),
  };
  const res = await fetch("/api/agent-chains", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to create chain");
  }
  return _normaliseChain(await res.json());
}

export async function updateChain(id, chain) {
  // Python uses PATCH; Node used PUT.
  const payload = {};
  if (chain.title != null || chain.name != null) payload.name = chain.title ?? chain.name;
  if (chain.description != null) payload.description = chain.description;
  if (chain.agents != null) {
    payload.steps = chain.agents.map((agentId) => ({ agentId, contextPassing: chain.contextPassing ?? "summary" }));
  }
  const res = await fetch(`/api/agent-chains/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to update chain");
  }
  return _normaliseChain(await res.json());
}

export async function fetchAgentContext(runId) {
  const res = await fetch(`/api/agents/context/${encodeURIComponent(runId)}`);
  return res.json();
}

// Agent DAGs — Python prefix is /api/agent-dags (not /api/agents/dags)
export async function fetchDags() {
  const res = await fetch("/api/agent-dags");
  const body = await res.json();
  const raw = Array.isArray(body) ? body : (body.dags ?? []);
  return raw.map(_normaliseDag);
}

export async function createDag(dag) {
  // Convert Node { nodes[], edges[] } → Python { nodes[] } (edges folded into deps)
  const nodes = (dag.nodes || []).map((n) => {
    const deps = (dag.edges || []).filter((e) => e.to === n.id).map((e) => e.from);
    return { id: n.id, agentId: n.agentId ?? n.id, x: n.x ?? 0, y: n.y ?? 0, deps };
  });
  const payload = {
    dagId: dag.id ?? dag.dagId ?? _slugify(dag.title),
    name: dag.title ?? dag.name,
    description: dag.description || "",
    nodes,
  };
  const res = await fetch("/api/agent-dags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to create DAG");
  }
  return _normaliseDag(await res.json());
}

export async function updateDag(id, dag) {
  // Python uses PATCH; Node used PUT.
  const payload = {};
  if (dag.title != null || dag.name != null) payload.name = dag.title ?? dag.name;
  if (dag.description != null) payload.description = dag.description;
  if (dag.nodes != null) {
    payload.nodes = (dag.nodes || []).map((n) => {
      const deps = (dag.edges || []).filter((e) => e.to === n.id).map((e) => e.from);
      return { id: n.id, agentId: n.agentId ?? n.id, x: n.x ?? 0, y: n.y ?? 0, deps };
    });
  }
  const res = await fetch(`/api/agent-dags/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to update DAG");
  }
  return _normaliseDag(await res.json());
}

export async function deleteDagApi(id) {
  const res = await fetch(`/api/agent-dags/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to delete DAG");
  }
  // Python returns 204 No Content — no JSON body
}

export async function deleteChainApi(id) {
  const res = await fetch(`/api/agent-chains/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to delete chain");
  }
  // Python returns 204 No Content — no JSON body
}

export async function runChainApi(id) {
  const res = await fetch(`/api/agent-chains/${encodeURIComponent(id)}/run`, {
    method: "POST",
  });
  if (res.status === 404) {
    return { __notYetWired: true, message: "Run not yet wired (Phase F2b in progress)" };
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to start chain run");
  }
  return res.json();
}

export async function runDagApi(id) {
  const res = await fetch(`/api/agent-dags/${encodeURIComponent(id)}/run`, {
    method: "POST",
  });
  if (res.status === 404) {
    return { __notYetWired: true, message: "Run not yet wired (Phase F2b in progress)" };
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.[0]?.msg ?? err.error ?? "Failed to start DAG run");
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

export async function fetchSkills({ status, category, kind } = {}) {
  const params = new URLSearchParams();
  // Python uses `kind` not `status`/`category`; forward both for compatibility
  if (kind) params.set("kind", kind);
  if (status) params.set("kind", status); // fallback mapping: status→kind
  if (category) params.set("kind", category); // category param not supported by Python; best-effort
  const qs = params.toString();
  const res = await fetch(`/api/skills${qs ? `?${qs}` : ""}`);
  const body = await res.json();
  // Python returns { skills: [...] }; Node expected a flat array
  const raw = Array.isArray(body) ? body : (body.skills ?? []);
  // Normalise: Python skills have slug/name; callers also use .id
  return raw.map((s) => ({ ...s, id: s.id ?? s.slug }));
}

export async function fetchSkill(id) {
  // Python identifies skills by slug; id IS the slug for skills
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}`);
  const body = await res.json();
  return body ? { ...body, id: body.id ?? body.slug } : body;
}

export async function createSkillApi(data) {
  // Map Node skill payload → Python SkillCreate
  const payload = {
    slug: data.slug ?? _slugify(data.name),
    name: data.name,
    description: data.description || "",
    instructions: data.body ?? data.instructions ?? "",
    tools: data.tools || [],
    kind: data.kind ?? data.scope ?? "global",
    source_path: data.source_path ?? null,
  };
  const res = await fetch("/api/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json();
  if (body?.error || body?.detail) {
    return body; // let caller handle error shape
  }
  return body ? { ...body, id: body.id ?? body.slug } : body;
}

export async function updateSkillApi(id, data) {
  // Python uses PATCH; Node used PUT.
  const payload = {};
  if (data.name != null) payload.name = data.name;
  if (data.description != null) payload.description = data.description;
  if (data.body != null || data.instructions != null) payload.instructions = data.body ?? data.instructions;
  if (data.tools != null) payload.tools = data.tools;
  if (data.kind != null || data.scope != null) payload.kind = data.kind ?? data.scope;
  if (data.source_path != null) payload.source_path = data.source_path;
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json();
  return body ? { ...body, id: body.id ?? body.slug } : body;
}

export async function approveSkillApi(id) {
  // Python F2a does not have /approve — graceful no-op returning optimistic ok
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/approve`, { method: "POST" });
  if (res.status === 404) return { ok: true, __notYetWired: true };
  return res.json().catch(() => ({}));
}

export async function archiveSkillApi(id) {
  // Python F2a does not have /archive — graceful no-op
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/archive`, { method: "POST" });
  if (res.status === 404) return { ok: true, __notYetWired: true };
  return res.json().catch(() => ({}));
}

export async function deleteSkillApi(id) {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail?.[0]?.msg ?? body.error ?? "Delete failed");
  }
  // Python returns 204 — no JSON body
}

export async function assignSkillApi(id, agentId) {
  // Python F2a does not have /assign — graceful no-op
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agentId }),
  });
  if (res.status === 404) return { ok: true, __notYetWired: true };
  return res.json().catch(() => ({}));
}

export async function unassignSkillApi(id, agentId) {
  // Python F2a does not have /unassign — graceful no-op
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/unassign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agentId }),
  });
  if (res.status === 404) return { ok: true, __notYetWired: true };
  return res.json().catch(() => ({}));
}

export async function fetchSkillBySlug(slug) {
  // Python exposes skills directly at /api/skills/{slug} (slug IS the ID)
  const res = await fetch(`/api/skills/${encodeURIComponent(slug)}`);
  if (!res.ok) return null;
  const body = await res.json();
  return body ? { ...body, id: body.id ?? body.slug } : null;
}

export async function fetchSkillCategories() {
  const res = await fetch("/api/skills/categories");
  if (res.status === 404) return []; // not yet wired in Python F2a
  return res.json().catch(() => []);
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

// ── Pipelines (PIPE1) ─────────────────────────────────────────────────────────

export async function listPipelinesApi({ status, hasTrigger, limit = 50 } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (hasTrigger !== undefined) params.set("hasTrigger", String(hasTrigger));
  if (limit !== 50) params.set("limit", String(limit));
  const qs = params.toString();
  const res = await fetch(`/api/pipelines${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error("listPipelinesApi failed");
  return res.json();
}

export async function createPipelineApi(data) {
  const res = await fetch("/api/pipelines/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "createPipelineApi failed");
  }
  return res.json();
}

export async function getPipelineApi(id) {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error("getPipelineApi failed");
  return res.json();
}

export async function exportPipelineApi(id) {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}/export`);
  if (!res.ok) throw new Error("exportPipelineApi failed");
  return res.json();
}

export async function importPipelineApi(bundle) {
  const res = await fetch("/api/pipelines/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(bundle),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || body.detail?.[0]?.msg || "importPipelineApi failed");
  }
  return res.json();
}

export async function updatePipelineApi(id, data) {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "updatePipelineApi failed");
  }
  return res.json();
}

export async function deletePipelineApi(id) {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw new Error("deletePipelineApi failed");
}

export async function permanentDeletePipelineApi(id) {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}/permanent`, { method: "DELETE" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "permanentDeletePipelineApi failed");
  }
}

export async function enablePipelineApi(id) {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}/enable`, { method: "POST" });
  if (!res.ok) throw new Error("enablePipelineApi failed");
  return res.json();
}

export async function disablePipelineApi(id) {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}/disable`, { method: "POST" });
  if (!res.ok) throw new Error("disablePipelineApi failed");
  return res.json();
}

export async function runPipelineApi(id, opts = {}) {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "runPipelineApi failed");
  }
  return res.json();
}

export async function listPipelineRunsApi(id, { limit = 20, cursor, sort } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", String(cursor));
  if (sort) params.set("sort", String(sort));
  const res = await fetch(`/api/pipelines/${encodeURIComponent(id)}/runs?${params}`);
  if (!res.ok) throw new Error("listPipelineRunsApi failed");
  return res.json();
}

export async function cancelPipelineRunApi(runId) {
  const res = await fetch(`/api/pipeline-runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("cancelPipelineRunApi failed");
  return res.json();
}

export async function listAllPipelineRunsApi({ status, limit = 50, cursor } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set("status", status);
  if (cursor) params.set("cursor", String(cursor));
  const res = await fetch(`/api/pipeline-runs?${params}`);
  if (!res.ok) throw new Error("listAllPipelineRunsApi failed");
  return res.json();
}

export async function retryPipelineRunApi(pipelineId) {
  return runPipelineApi(pipelineId, {});
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

export async function decideApprovalApi(id, { decision, note, reviewer, selected_cluster_keys } = {}) {
  const payload = { decision, reason: note, reviewer };
  if (selected_cluster_keys != null) payload.selected_cluster_keys = selected_cluster_keys;
  const res = await fetch(`/api/approvals/${encodeURIComponent(id)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "decideApprovalApi failed");
  }
  return res.json();
}

// ── Outbox / Send queue ───────────────────────────────────────────────────

export async function fetchMarketingQueuedSends({ status = 'queued', limit = 50 } = {}) {
  const params = new URLSearchParams({ status, limit: String(limit) });
  const res = await fetch(`/api/marketing/sends?${params}`);
  return _readJsonOrThrow(res, 'Failed to fetch outbox');
}

export async function sendMarketingOutboxItem(sendId, { actor } = {}) {
  const res = await fetch(`/api/marketing/sends/${encodeURIComponent(sendId)}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ actor: actor || 'operator' }),
  });
  return _readJsonOrThrow(res, 'Failed to send outbox item');
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

// O2/O3 — persona PATCH
export async function patchAgentPersonaApi(id, patch) {
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}/persona`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "patchAgentPersonaApi failed");
  }
  return res.json();
}

// O2/O3 — avatar upload
export async function uploadAgentAvatarApi(id, file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/agents/${encodeURIComponent(id)}/avatar`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "uploadAgentAvatarApi failed");
  }
  return res.json();
}

// ── Signal Criteria / Scout Ruleset Lite ──────────────────────────────────

export async function listReasonCodesApi({ includeRetired = false } = {}) {
  const qs = includeRetired ? "?include_inactive=true" : "";
  const res = await fetch(`/api/signal-criteria/reason-codes${qs}`);
  if (!res.ok) throw new Error("listReasonCodesApi failed");
  return res.json();
}

export async function createReasonCodeApi(payload = {}) {
  const res = await fetch("/api/signal-criteria/reason-codes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail?.error || body.error || "createReasonCodeApi failed");
  }
  return res.json();
}

export async function patchReasonCodeApi(code, patch = {}) {
  const res = await fetch(`/api/signal-criteria/reason-codes/${encodeURIComponent(code)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail?.error || body.error || "patchReasonCodeApi failed");
  }
  return res.json();
}

export async function exportReasonCodesMarkdownApi() {
  const res = await fetch("/api/signal-criteria/reason-codes/markdown-export");
  if (!res.ok) throw new Error("exportReasonCodesMarkdownApi failed");
  return res.text();
}

export async function listCampaignRulesetsApi() {
  const res = await fetch("/api/signal-criteria/rulesets");
  if (!res.ok) throw new Error("listCampaignRulesetsApi failed");
  return res.json();
}

export async function getCampaignRulesetApi(family) {
  // E1b: Python returns { family, versions[], activeVersionDetails } (flat shape).
  // Node returned a nested two-level shape. Callers must read activeVersionDetails
  // for the active ruleset, not a top-level "active" key.
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

// E1b: Python territory PUT is per-family (not per-state). Send the whole family config in one PUT.
// Callers should pass the full family payload (hotStates, standardStates, unlistedMultiplier).
// The stateCode parameter is kept for API compat but is ignored — merge it into payload before calling.
export async function upsertTerritoryStateApi(family, _stateCode, payload = {}) {
  const res = await fetch(
    `/api/signal-criteria/territory/${encodeURIComponent(family)}`,
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

// ── District Tier Bands ───────────────────────────────────────────────────

export async function getTierBandsApi() {
  const res = await fetch("/api/signal-criteria/tier-bands");
  if (!res.ok) throw new Error("getTierBandsApi failed");
  return res.json();
}

export async function upsertTierBandsApi(bands) {
  const res = await fetch("/api/signal-criteria/tier-bands", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bands }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "upsertTierBandsApi failed");
  }
  return res.json();
}

export async function recomputeTierBandsApi() {
  const res = await fetch("/api/signal-criteria/tier-bands/recompute", { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "recomputeTierBandsApi failed");
  }
  return res.json();
}

// ── District Data Status ───────────────────────────────────────────────────

/** Fetch district data provenance + freshness from the DIST5 meta singleton. */
export async function getDistrictDataStatusApi() {
  const res = await fetch("/api/signal-criteria/district-data-status");
  if (!res.ok) throw new Error("getDistrictDataStatusApi failed");
  return res.json();
}

/** Start a background NCES refresh. Returns immediately (202) — the panel
 *  re-fetches /district-data-status on completion or next load. 409 if a
 *  refresh is already in flight. */
export async function refreshDistrictDataApi() {
  const res = await fetch("/api/signal-criteria/district-data-refresh", { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.error || body.detail?.error || "refreshDistrictDataApi failed");
    err.status = res.status;
    throw err;
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
  // E1b: Python returns 200 for dry-run mode and 201 for committed intake.
  // Both are success states — only throw on other non-ok statuses.
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

// ── Builder API (O1) ──────────────────────────────────────────────────────────

export async function builderFetchSessions({ status, limit } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (limit !== undefined) params.set("limit", String(limit));
  const qs = params.toString();
  const res = await fetch(`/api/builder/sessions${qs ? `?${qs}` : ""}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "builderFetchSessions failed");
  }
  return res.json();
}

export async function builderCreateSession({ builder_kind = "agent", target_id, user_id } = {}) {
  const res = await fetch("/api/builder/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ builder_kind, target_id, user_id }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "builderCreateSession failed");
  return body;
}

export async function builderGetSession(sessionId) {
  const res = await fetch(`/api/builder/sessions/${encodeURIComponent(sessionId)}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `builderGetSession(${sessionId}) failed`);
  }
  return res.json();
}

export async function builderSendMessage(sessionId, content) {
  const res = await fetch(`/api/builder/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "builderSendMessage failed");
  return body;
}

export async function builderAbandonSession(sessionId) {
  const res = await fetch(`/api/builder/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  if (res.status === 204) return;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "builderAbandonSession failed");
  return body;
}

export async function builderTestRun(sessionId, { prompt, allow_writes = false } = {}) {
  const res = await fetch(`/api/builder/sessions/${encodeURIComponent(sessionId)}/test-run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, allow_writes }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "builderTestRun failed");
  return body;
}

export async function builderFetchProposals({ status = "pending", kind, limit } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (kind) params.set("kind", kind);
  if (limit !== undefined) params.set("limit", String(limit));
  const qs = params.toString();
  const res = await fetch(`/api/builder/proposals${qs ? `?${qs}` : ""}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "builderFetchProposals failed");
  }
  return res.json();
}

export async function builderGetAgentContext(agentId) {
  const res = await fetch(`/api/agents/${encodeURIComponent(agentId)}/builder-context`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `builderGetAgentContext(${agentId}) failed`);
  }
  return res.json();
}

export async function builderApproveProposal(proposalId) {
  const res = await fetch(`/api/builder/proposals/${encodeURIComponent(proposalId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "builderApproveProposal failed");
  return body;
}

export async function builderRejectProposal(proposalId, reason = null) {
  // CC22: optional reason body — backend accepts empty body for one-click reject.
  const init = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  };
  if (reason && typeof reason === "string" && reason.trim()) {
    init.body = JSON.stringify({ reason: reason.trim().slice(0, 2000) });
  }
  const res = await fetch(`/api/builder/proposals/${encodeURIComponent(proposalId)}/reject`, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "builderRejectProposal failed");
  return body;
}

// ── Proposals Inbox (J6a) ─────────────────────────────────────────────────────

export async function builderFetchInbox() {
  const res = await fetch("/api/builder/inbox");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "builderFetchInbox failed");
  return body;
}

export async function builderMarkAgentReviewed(agentId) {
  const res = await fetch(`/api/agents/${encodeURIComponent(agentId)}/mark-reviewed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "builderMarkAgentReviewed failed");
  return body;
}

// ── Claims Register (Stage 4 — claim-flags) ────────────────────────────────

/**
 * Scan a draft for unregistered strong claims.
 * POST /api/writing-studio/drafts/{id}/claim-scan
 * body: { text?: string }  — if text is omitted, server uses draft's live_content.
 * Returns: { flags: [{start, end, text, reason, nearestApproved}], scannedChars, approvedClaimsCount }
 */
export async function scanDraftClaimsApi(draftId, payload = {}) {
  const res = await fetch(
    `/api/writing-studio/drafts/${encodeURIComponent(draftId)}/claim-scan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return _readJsonOrThrow(res, "Failed to scan draft for claims");
}

/**
 * List claims (all or filtered by status).
 * GET /api/writing-studio/claims[?status=approved]
 */
export async function fetchClaimsApi(params = {}) {
  const qs = Object.keys(params).length
    ? "?" + new URLSearchParams(params).toString()
    : "";
  const res = await fetch(`/api/writing-studio/claims${qs}`);
  return _readJsonOrThrow(res, "Failed to fetch claims");
}

/**
 * Create a new claim (POST /api/writing-studio/claims).
 * Body: { claimCode, category, tier?, approvedPhrasing, notes?, source? }
 */
export async function createClaimApi(payload = {}) {
  const res = await fetch("/api/writing-studio/claims", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return _readJsonOrThrow(res, "Failed to create claim");
}

/**
 * Approve an existing claim (POST /api/writing-studio/claims/{id}/approve).
 */
export async function approveClaimApi(claimId) {
  const res = await fetch(
    `/api/writing-studio/claims/${encodeURIComponent(claimId)}/approve`,
    { method: "POST" },
  );
  return _readJsonOrThrow(res, "Failed to approve claim");
}
