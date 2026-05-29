import { on as onState, getState, setState } from "../core/store.js";
import { escapeHtml, slugify } from "../core/utils.js";
import {
  MEMORY_VIEW,
  OPERATIONS_VIEW,
  PIPELINE_RUN_HISTORY_VIEW,
  WRITING_STUDIO_VIEW,
  normalizeAppView,
} from "../core/navigation.js";
import * as api from "../core/api.js";
import { renderSkillsGuideHTML } from "./skills-guide.js";
import {
  renderAgentBuilderPage,
  handleBuilderAction,
  initBuilderSurface,
} from "./agent-builder.js";
import { initPipelinesPage } from "./pipelines.js";
import { initPipelineRunHistoryPage } from "./pipeline-run-history.js";
import { PROVIDER_LABELS, PROVIDER_PICKERS, getSourceModels } from "../ui/model-selector.js";
import {
  createCustomAgentTreeView,
  createAgentTreeView,
  getAgentHealth,
  getAgentTrigger,
  normalizeDisplayFolder,
} from "../components/agent-tree.js";

const SHELL_CONTENT_SELECTOR = "#app-shell-content";
const AGENT_LOADING_THRESHOLD = 0;
const WORKFLOW_LOADING_THRESHOLD = 0;
const OPS_AGENT_SELECTION_KEY = "artemis-ops-selected-agent";
const OPS_WORKFLOW_SELECTION_KEY = "artemis-ops-selected-workflow";
const OPS_SKILL_SELECTION_KEY = "artemis-ops-selected-skill";
const OPS_SKILL_CATEGORY_KEY = "artemis-ops-skill-category";
const OPS_SKILL_TAB_KEY = "artemis-ops-skill-tab";
const OPS_SKILL_SEARCH_KEY = "artemis-ops-skill-search";
const OPS_AUTOMATION_SELECTION_KEY = "artemis-ops-selected-automation";
const OPS_AGENT_DRAFT_KEY = "artemis-ops-agent-draft";
const OPS_AGENT_TREE_COLLAPSED_KEY = "artemis.agents.tree.collapsed";
const OPS_AGENT_CUSTOM_TREE_COLLAPSED_KEY = "artemis.agents.custom-tree.collapsed";
const OPS_AGENT_VIEW_MODE_KEY = "artemis.agents.view-mode";
const OPS_AGENT_EMPTY_FOLDERS_KEY = "artemis.agents.empty-folders";
const OPS_WORKFLOW_DRAFT_KEY = "artemis-ops-workflow-draft";
const OPS_CAMPAIGN_NOTE_KEY = "artemis-ops-campaign-notes";
const WRITING_STUDIO_HANDOFF_KEY = "artemis-writing-studio-handoff";

// Dynamic skill state — populated from /api/skills on view enter
let _approvedSkills = [];
let _proposedSkills = [];
let _skillCategories = []; // [{ category, count }]
let _skillsLoaded = false;
let _skillsError = null;
let _campaignOpsOverview = null;
let _campaignOpsLoaded = false;
let _campaignOpsError = null;
// Automations state — populated from /api/automations on view enter
let _automations = [];
let _automationsLoaded = false;
let _automationsError = null;
let _pendingApprovals = [];
// Create-automation form draft
let _automationDraft = null;

// ── Skill import helpers ──────────────────────────────────────
// Client-side front-matter parser (mirrors server/skills-store.js parseFrontMatter)
function parseSkillFrontMatter(text) {
  const lines = text.split("\n");
  const meta = {};
  let bodyStart = 0;
  if (lines[0]?.trim() === "---") {
    let i = 1;
    for (; i < lines.length; i++) {
      if (lines[i].trim() === "---") { bodyStart = i + 1; break; }
      const colonIdx = lines[i].indexOf(":");
      if (colonIdx === -1) continue;
      const key = lines[i].slice(0, colonIdx).trim();
      let val = lines[i].slice(colonIdx + 1).trim();
      if (val.startsWith("[")) { try { val = JSON.parse(val); } catch { /* keep as string */ } }
      meta[key] = val;
    }
    if (bodyStart === 0) bodyStart = i + 1;
  }
  return { meta, body: lines.slice(bodyStart).join("\n").trim() };
}

function showToast(label, title = "", { isError = false, ms = 4000 } = {}) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `bg-toast${isError ? " toast-error" : ""}`;
  toast.innerHTML = `
    <span class="bg-toast-dot"></span>
    <div class="bg-toast-body">
      <div class="bg-toast-label"></div>
      <div class="bg-toast-title"></div>
    </div>
    <button class="bg-toast-close" type="button" title="Dismiss">&times;</button>`;
  toast.querySelector(".bg-toast-label").textContent = label;
  toast.querySelector(".bg-toast-title").textContent = title;
  const dismiss = () => {
    toast.classList.add("toast-exit");
    toast.addEventListener("animationend", () => toast.remove(), { once: true });
  };
  toast.querySelector(".bg-toast-close")?.addEventListener("click", dismiss);
  container.appendChild(toast);
  setTimeout(() => { if (toast.parentNode) dismiss(); }, ms);
}

function showOpsImportToast(message, isError = false) {
  showToast(isError ? "Action failed" : "Done", message, { isError });
}

function showOpsConfirm({ title, message, confirmLabel = "Delete", onConfirm }) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay ops-confirm-overlay";
  overlay.innerHTML = `
    <div class="modal ops-confirm-modal" role="dialog" aria-modal="true">
      <div class="modal-header">
        <h3 class="modal-title">${title}</h3>
      </div>
      <p class="ops-confirm-body">${message}</p>
      <div class="ops-confirm-actions">
        <button type="button" class="ops-button ops-button-danger ops-confirm-ok">${confirmLabel}</button>
        <button type="button" class="ops-button ops-button-secondary ops-confirm-cancel">Cancel</button>
      </div>
    </div>
  `;
  const close = () => overlay.remove();
  overlay.querySelector(".ops-confirm-cancel").addEventListener("click", close);
  overlay.querySelector(".ops-confirm-ok").addEventListener("click", () => { close(); onConfirm(); });
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.body.appendChild(overlay);
  overlay.querySelector(".ops-confirm-ok").focus();
}

const MARKETING_CAMPAIGNS = [
  {
    id: "florida-obc",
    name: "Florida OBC campaign",
    family: "Outcomes-based contracts",
    status: "Ready for opportunity review",
    stage: "Human gate 1",
    priority: "P0",
    confidence: 84,
    owner: "Josh + Angela",
    nextAction: "Approve the opportunity brief, then build the OBC playbook from the webinar transcript.",
    why: "OBC webinar and Mark op-ed already exist; Duvall story is useful but not required for first launch.",
    agents: ["Campaign Scout", "District Fit Analyst", "Brief Builder", "Compliance Gatekeeper"],
    deliverables: ["OBC playbook", "Email sequence", "LinkedIn message", "Landing page copy"],
    gates: ["Opportunity approval", "Draft content review", "Compliance / opt-out check", "Launch handoff"],
    signals: ["Districts discussing vendor accountability", "Outcomes-based purchasing language", "Florida proof story"],
    metrics: { audience: "Florida districts", assetReadiness: "High", launchRisk: "Medium" },
  },
  {
    id: "indiana-obc",
    name: "Indiana OBC campaign",
    family: "Outcomes-based contracts",
    status: "Template-adaptable",
    stage: "Brief build",
    priority: "P0",
    confidence: 78,
    owner: "Josh",
    nextAction: "Adapt the Florida OBC playbook and validate Indiana-specific triggers before drafting.",
    why: "Fast follow from Florida once the OBC framing is approved.",
    agents: ["Signal Triangulator", "Brief Builder", "Email Copywriter", "Launch Auditor"],
    deliverables: ["Indiana OBC playbook", "Email sequence", "Salesforce campaign handoff"],
    gates: ["Opportunity approval", "Draft content review", "Compliance / opt-out check"],
    signals: ["Accountability triggers", "Reading outcomes pressure", "Comparable state story"],
    metrics: { audience: "Indiana districts", assetReadiness: "Medium", launchRisk: "Medium" },
  },
  {
    id: "maryland-field-guide",
    name: "Maryland screener field guide",
    family: "State screener / field guide",
    status: "Needs Ry validation",
    stage: "Evidence validation",
    priority: "P1",
    confidence: 72,
    owner: "Josh + Ry",
    nextAction: "Validate legislation/funding accuracy, then move the draft into the field-guide template.",
    why: "Draft exists, but policy accuracy is the blocker before content review.",
    agents: ["State DOE Watcher", "Legislation Monitor", "Field Guide Builder", "Compliance Gatekeeper"],
    deliverables: ["Maryland field guide", "Landing page", "Post-download nurture"],
    gates: ["Expert validation", "Draft content review", "Launch handoff"],
    signals: ["State guidance", "Approved screener language", "Funding options"],
    metrics: { audience: "Maryland districts", assetReadiness: "Medium", launchRisk: "Low" },
  },
  {
    id: "missouri-field-guide",
    name: "Missouri screener field guide",
    family: "State screener / field guide",
    status: "Template-ready",
    stage: "Draft content",
    priority: "P1",
    confidence: 76,
    owner: "Josh + Design",
    nextAction: "Port the draft into the Michigan field-guide structure and route for content review.",
    why: "The Michigan pattern gives this a low-lift launch path.",
    agents: ["State DOE Watcher", "Field Guide Builder", "Email Copywriter"],
    deliverables: ["Missouri field guide", "Email sequence", "Landing page"],
    gates: ["Draft content review", "Compliance / opt-out check"],
    signals: ["State screener requirements", "District awareness gap"],
    metrics: { audience: "Missouri districts", assetReadiness: "High", launchRisk: "Low" },
  },
  {
    id: "illinois-dyslexia",
    name: "Illinois dyslexia field guide",
    family: "State screener / field guide",
    status: "Template-ready",
    stage: "Draft content",
    priority: "P1",
    confidence: 74,
    owner: "Josh + Design",
    nextAction: "Confirm dyslexia-screening language and create a template-compatible field guide.",
    why: "Existing draft appears compatible with the field-guide campaign family.",
    agents: ["Legislation Monitor", "Field Guide Builder", "Claims Checker"],
    deliverables: ["Illinois field guide", "Landing page", "Nurture email sequence"],
    gates: ["Draft content review", "Claims review", "Launch handoff"],
    signals: ["Dyslexia screening", "State compliance deadlines", "District curriculum priorities"],
    metrics: { audience: "Illinois districts", assetReadiness: "High", launchRisk: "Low" },
  },
  {
    id: "texas-research",
    name: "Texas research-summary campaign",
    family: "Research / proof",
    status: "Asset-ready",
    stage: "Launch prep",
    priority: "P1",
    confidence: 70,
    owner: "Angela + Kristen",
    nextAction: "Use the existing Texas research summary as gated collateral and prepare HubSpot handoff.",
    why: "Existing research asset can move quickly without waiting on the larger growth campaign.",
    agents: ["Audience Segmenter", "Email Copywriter", "Launch Auditor", "Performance Analyst"],
    deliverables: ["Gated research summary", "Email sequence", "Campaign report"],
    gates: ["Draft content review", "Compliance / opt-out check", "Launch handoff"],
    signals: ["Texas research proof", "Reading growth needs", "District engagement"],
    metrics: { audience: "Texas districts", assetReadiness: "High", launchRisk: "Medium" },
  },
  {
    id: "michigan-field-guide",
    name: "Michigan field guide",
    family: "State screener / field guide",
    status: "In play",
    stage: "Report",
    priority: "Live",
    confidence: 88,
    owner: "Marketing team",
    nextAction: "Centralize performance reporting: HubSpot email stats, landing-page conversions, and BDR handoff state.",
    why: "This is the active pattern to learn from before scaling the family.",
    agents: ["Performance Analyst", "Dashboard Reporter", "Learning Recorder"],
    deliverables: ["Campaign report", "Learning summary", "Next-action queue"],
    gates: ["Performance review", "Learning approval"],
    signals: ["Inbound activity", "Download behavior", "Warm lead handoffs"],
    metrics: { audience: "Michigan districts", assetReadiness: "Live", launchRisk: "Low" },
  },
];

const MARKETING_REJECTED_OPPORTUNITIES = [
  {
    id: "national-reading-growth",
    name: "National Reading Growth campaign",
    reason: "Top-of-funnel brand play; not the immediate demand-generation priority.",
    reviewer: "Angela",
    action: "Monitor for All Star Reading Growth umbrella timing",
  },
  {
    id: "texas-biliteracy",
    name: "Texas biliteracy campaign",
    reason: "Nice-to-have until the broader dual-language / Lectora campaign is ready.",
    reviewer: "Team discussion",
    action: "Revisit after bilingual webinar proof is packaged",
  },
];

function buildCampaignOpsPreviewOverview() {
  return {
    campaigns: MARKETING_CAMPAIGNS.map((campaign) => ({
      ...campaign,
      decisionState: "pending_review",
      repositoryBucket: null,
      repositoryReason: null,
      repositoryAction: null,
      reviewer: null,
      history: [],
    })),
    repository: MARKETING_REJECTED_OPPORTUNITIES.map((item) => ({
      id: item.id,
      name: item.name,
      family: "Repository",
      status: "Rejected / monitor",
      stage: "Repository",
      priority: "Watch",
      confidence: 0,
      owner: item.reviewer,
      nextAction: item.action,
      why: item.reason,
      agents: [],
      deliverables: [],
      gates: [],
      signals: [],
      metrics: {},
      decisionState: item.id === "texas-biliteracy" ? "monitoring" : "rejected",
      repositoryBucket: item.id === "texas-biliteracy" ? "monitor" : "rejected",
      repositoryReason: item.reason,
      repositoryAction: item.action,
      reviewer: item.reviewer,
      history: [],
    })),
    recentDecisions: [],
    counts: getMarketingCampaignStats(MARKETING_CAMPAIGNS),
  };
}

const AGENT_SURFACE_PROFILES = {
  "review-pr": {
    preferredProvider: "Claude Sonnet",
    fallbackChain: "Haiku after 1 timeout",
    linkedSkills: ["Summarize thread", "Cite evidence", "Redact PII"],
    memorySummary: "Project · Run · Learned",
    usedIn: ["Review PR workflow", "Agent roster", "Command center handoff"],
    policyNote: "Read-first, write-second. Best when review depth matters more than speed.",
    heroTone: "A high-signal reviewer that keeps diffs honest and grounded in evidence.",
  },
  bugHunter: {
    preferredProvider: "Claude Sonnet",
    fallbackChain: "Haiku after 1 timeout",
    linkedSkills: ["Pull Jira tickets", "Cite evidence", "Summarize thread"],
    memorySummary: "Project · Run",
    usedIn: ["Bug sweep workflow", "Operations roster"],
    policyNote: "Wide scan mode with a fast fallback so signal still lands when the queue is noisy.",
    heroTone: "A broad sweep worker built to catch defects, regressions, and risky assumptions.",
  },
  "test-writer": {
    preferredProvider: "Claude Sonnet",
    fallbackChain: "Haiku after 1 timeout",
    linkedSkills: ["Pull Jira tickets", "Summarize thread", "Cite evidence"],
    memorySummary: "Project · Run · Learned",
    usedIn: ["Test plan workflow", "Maintenance sweeps"],
    policyNote: "Favors coverage over brevity and leans on evidence when behavior is ambiguous.",
    heroTone: "A worker for test design, edge cases, and under-covered paths.",
  },
  refactor: {
    preferredProvider: "Claude Sonnet",
    fallbackChain: "Haiku after 1 timeout",
    linkedSkills: ["Summarize thread", "Cross-reference OKRs", "Redact PII"],
    memorySummary: "Project · Run",
    usedIn: ["Refactor workflow", "Code health reviews"],
    policyNote: "Optimizes for clarity and change safety before structural reshaping.",
    heroTone: "A refactoring-focused worker that trims complexity without losing intent.",
  },
};

const DEFAULT_AGENT_PROFILE = {
  preferredProvider: "Claude Sonnet",
  fallbackChain: "Haiku after 1 timeout",
  linkedSkills: ["Summarize thread", "Cite evidence"],
  memorySummary: "Project · Run",
  usedIn: ["Operations roster"],
  policyNote: "Runs with cautious defaults and a simple fallback chain.",
  heroTone: "A reusable worker with explicit policy, memory, and runtime context.",
};

const AGENT_FALLBACK_METRICS = {
  "review-pr": {
    runs: 47,
    successRate: 96,
    avgDuration: "9m",
    avgCost: "$0.84",
    lastRun: "2h ago",
    health: "Healthy",
    totalCost: "$39.48",
    totalInputTokens: "378k",
    totalOutputTokens: "114k",
  },
  "bug-hunter": {
    runs: 31,
    successRate: 92,
    avgDuration: "12m",
    avgCost: "$0.71",
    lastRun: "4h ago",
    health: "Healthy",
    totalCost: "$22.01",
    totalInputTokens: "211k",
    totalOutputTokens: "86k",
  },
  "test-writer": {
    runs: 68,
    successRate: 94,
    avgDuration: "11m",
    avgCost: "$0.76",
    lastRun: "1d ago",
    health: "Healthy",
    totalCost: "$51.68",
    totalInputTokens: "430k",
    totalOutputTokens: "132k",
  },
  refactor: {
    runs: 44,
    successRate: 95,
    avgDuration: "10m",
    avgCost: "$0.79",
    lastRun: "3d ago",
    health: "Healthy",
    totalCost: "$34.76",
    totalInputTokens: "298k",
    totalOutputTokens: "98k",
  },
};

const WORKFLOW_META_PRESETS = {
  "review-pr": {
    status: "active",
    schedule: "Manual / review request",
    lastRun: "2h ago",
    nextRun: "On demand",
    runs: 12,
    successRate: 92,
    cost: "$4.18",
    owner: "Operations",
    linkedAgent: "PR Reviewer",
    runtime: "Flow inspector",
    notes: "The recipe is the primary object; runtime metadata rides alongside it.",
  },
  "onboard-me-to-this-repo": {
    status: "draft",
    schedule: "Manual / draft only",
    lastRun: "—",
    nextRun: "Draft",
    runs: 0,
    successRate: 0,
    cost: "—",
    owner: "Operations",
    linkedAgent: "Refactoring Agent",
    runtime: "Builder draft",
    notes: "A staging workflow before it graduates into a scheduled runtime.",
  },
  "generate-migration-plan": {
    status: "draft",
    schedule: "Manual / draft only",
    lastRun: "—",
    nextRun: "Draft",
    runs: 0,
    successRate: 0,
    cost: "—",
    owner: "Operations",
    linkedAgent: "Refactoring Agent",
    runtime: "Builder draft",
    notes: "Still a recipe, not a schedule.",
  },
  "code-health-check": {
    status: "active",
    schedule: "Mon 09:00",
    lastRun: "Yesterday",
    nextRun: "Mon 09:00",
    runs: 8,
    successRate: 88,
    cost: "$2.75",
    owner: "Operations",
    linkedAgent: "Bug Hunter",
    runtime: "Scheduled",
    notes: "Run metadata is attached, but the workflow remains the primary object.",
  },
};

const DEFAULT_WORKFLOW_META = {
  status: "active",
  schedule: "Manual",
  lastRun: "—",
  nextRun: "On demand",
  runs: 0,
  successRate: 0,
  cost: "—",
  owner: "Operations",
  linkedAgent: "PR Reviewer",
  runtime: "Inspector",
  notes: "Workflow schedule and runtime data attach to the recipe, not the identity.",
};

let renderQueued = false;
let selectedAgentId = readStorage(OPS_AGENT_SELECTION_KEY, "");
let selectedWorkflowId = readStorage(OPS_WORKFLOW_SELECTION_KEY, "");
let selectedSkillTab = readStorage(OPS_SKILL_TAB_KEY, "library");
let selectedSkillCategory = readStorage(OPS_SKILL_CATEGORY_KEY, "all");
let selectedSkillId = readStorage(OPS_SKILL_SELECTION_KEY, "");
let selectedAutomationId = readStorage(OPS_AUTOMATION_SELECTION_KEY, "");
let selectedAgentRunId = "";
let selectedAgentRunDetail = null;
let selectedAgentRunLoading = false;
let selectedAgentRunError = "";
let agentTreeSearch = "";
let agentTreeSort = "name";
let agentTreeFilters = { statuses: [], triggers: [] };
let agentTreeCollapsed = readStorage(OPS_AGENT_TREE_COLLAPSED_KEY, {});
let agentCustomTreeCollapsed = readStorage(OPS_AGENT_CUSTOM_TREE_COLLAPSED_KEY, {});
let agentViewMode = readStorage(OPS_AGENT_VIEW_MODE_KEY, "slug") === "custom" ? "custom" : "slug";
let agentEmptyFolders = readStorage(OPS_AGENT_EMPTY_FOLDERS_KEY, []);
let workflowDraft = null;
// Latest run data for the selected workflow, loaded on selection + refreshed on Run now.
let _latestWorkflowRun = null;
let _latestWorkflowRunLoading = false;
let _latestWorkflowRunForId = null;   // workflow id we last fetched for
let agentDraft = null;
// Enriched agent data (linked skills, instruction file, policy fields) loaded per-selection.
let _enrichedAgent = null;
let _enrichedAgentLoadedForId = null;   // tracks which agent ID we last attempted (avoids re-trigger on null)
let _agentInstructionContent = null;
let _agentInstructionLoading = false;
let _agentSupportingFiles = [];
let _reasonCodeOptions = [];
let _reasonCodesLoaded = false;
let _reasonCodesLoading = false;
let campaignDecisionNotes = readStorage(OPS_CAMPAIGN_NOTE_KEY, {});

// Legacy in-memory skill promotion/dismissal state removed — now API-driven

function getShellContent() {
  return document.querySelector(SHELL_CONTENT_SELECTOR);
}

function escapeAttr(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function providerOptions(selected, allowEmpty = false) {
  const empty = allowEmpty ? '<option value="">Not set — required</option>' : "";
  return empty + Object.keys(PROVIDER_PICKERS).map((id) =>
    `<option value="${escapeAttr(id)}"${selected === id ? " selected" : ""}>${escapeHtml(PROVIDER_LABELS[id] || id)}</option>`
  ).join("");
}

function modelOptions(provider, selected) {
  const models = getSourceModels(provider || "claude-code");
  return models.map((m) =>
    `<option value="${escapeAttr(m.value)}"${selected === m.value ? " selected" : ""}>${escapeHtml(m.label)}</option>`
  ).join("");
}

function firstModelForProvider(provider) {
  return getSourceModels(provider || "claude-code")[0]?.value || "";
}

function renderReasonCodeOptions(selectedCodes = []) {
  const selected = new Set((selectedCodes || []).map(String));
  return _reasonCodeOptions.map((rc) => {
    const code = rc.code || rc;
    const label = rc.description || code;
    const domain = rc.domain || "CODE";
    return `
      <label class="ops-reason-code-option">
        <input
          type="checkbox"
          value="${escapeAttr(code)}"
          data-ops-field="reasonCodesEmitted"
          ${selected.has(code) ? "checked" : ""}
        >
        <span class="ops-reason-code-copy">
          <strong>${escapeHtml(code)}</strong>
          <small>${escapeHtml(domain)} · ${escapeHtml(label)}</small>
        </span>
      </label>
    `;
  }).join("");
}

async function refreshReasonCodesFromApi() {
  if (_reasonCodesLoading) return;
  _reasonCodesLoading = true;
  try {
    const reasonCodes = await api.listReasonCodesApi();
    _reasonCodeOptions = Array.isArray(reasonCodes) ? reasonCodes : [];
    _reasonCodesLoaded = true;
  } finally {
    _reasonCodesLoading = false;
  }
}

function readStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null || raw === "") return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function writeStorage(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function isOperationsSurfaceView(view) {
  const normalized = normalizeAppView(view);
  return normalized === OPERATIONS_VIEW
    || normalized === "agents"
    || normalized === "agents/builder"
    || normalized === "skills"
    || normalized === "workflows"
    || normalized === "automations"
    || normalized === "pipelines"
    || normalized === PIPELINE_RUN_HISTORY_VIEW;
}

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  queueMicrotask(() => {
    renderQueued = false;
    const view = normalizeAppView(getState("view"));
    if (!isOperationsSurfaceView(view)) return;
    renderOperationsView(view);
  });
}

function getAgents() {
  return Array.isArray(getState("agents")) ? getState("agents") : [];
}

function getAgentChains() {
  return Array.isArray(getState("agentChains")) ? getState("agentChains") : [];
}

function getAgentDags() {
  return Array.isArray(getState("agentDags")) ? getState("agentDags") : [];
}

function getAgentMetrics() {
  const metrics = getState("agentMetrics");
  return metrics && typeof metrics === "object" ? metrics : null;
}

function getWorkflows() {
  return Array.isArray(getState("workflows")) ? getState("workflows") : [];
}

function getApprovedSkills() {
  return _approvedSkills.map(normalizeSkillForUI);
}

function getPendingSkills() {
  return _proposedSkills.map(normalizeSkillForUI);
}

function normalizeSkillForUI(skill) {
  const providers = (() => {
    try { return JSON.parse(skill.provider_compat || '["all"]'); } catch { return ["all"]; }
  })();
  return {
    id: String(skill.id),
    name: skill.name,
    cat: skill.category || "",
    desc: skill.description || "",
    agents: 0,
    uses: skill.uses || 0,
    success: Math.round(skill.success_rate || 0),
    owner: skill.origin === "agent" ? (skill.proposed_by || "Agent") : skill.origin === "policy" ? "Policy" : "You",
    updated: skill.updated_at ? formatRelativeTime(skill.updated_at) : "—",
    origin: skill.origin || "user",
    status: skill.status || "approved",
    scope: skill.scope || "global",
    providerCompat: providers,
    proposedBy: skill.proposed_by || "",
    proposedReason: skill.proposed_reason || "",
    evidence: skill.evidence || "",
    whenToUse: skill.when_to_use || "",
    linkedAgents: [],
    memoryBoundary: "Managed in Skills library",
    // proposal-specific UI fields
    proposedByMark: (skill.proposed_by || "?").slice(0, 2).toUpperCase(),
    proposedByColor: "#5E8372",
    observedIn: "",
    when: skill.updated_at ? formatRelativeTime(skill.updated_at) : "—",
    rationale: skill.proposed_reason || "",
    tests: skill.evidence || "",
    risk: "low",
    // raw
    _raw: skill,
  };
}

function formatRelativeTime(unixSecs) {
  const diff = Math.max(Math.floor(Date.now() / 1000) - Number(unixSecs), 0);
  if (diff < 60) return "just now";
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  if (w < 5) return `${w}w ago`;
  const mo = Math.floor(d / 30);
  return `${mo}mo ago`;
}

async function refreshAutomationsFromApi() {
  _automationsError = null;
  try {
    _automations = await api.listAutomationsApi();
    _automationsLoaded = true;
    _pendingApprovals = await api.listApprovalsApi({ status: "pending", targetType: "automation_run", limit: 50 });
  } catch (err) {
    _automationsError = err.message || String(err);
    _automationsLoaded = true;
  }
}

function getAutomationStatus(automation) {
  return automation.status || "active";
}

function formatRunStatus(status) {
  const map = {
    pending: "Pending",
    running: "Running…",
    awaiting_approval: "Awaiting approval",
    completed: "Completed",
    failed: "Failed",
    rejected: "Rejected",
    cancelled: "Cancelled",
  };
  return map[status] || status || "—";
}

function runStatusVariant(status) {
  if (status === "completed") return "ok";
  if (status === "failed" || status === "rejected") return "warn";
  if (status === "running") return "accent";
  if (status === "awaiting_approval") return "neutral";
  return "neutral";
}

function persistCampaignDecisionNotes() {
  writeStorage(OPS_CAMPAIGN_NOTE_KEY, campaignDecisionNotes);
}

function normalizeAgentId(agent) {
  return String(agent?.id || agent?.title || "").toLowerCase();
}

function normalizeWorkflowId(workflow) {
  return String(workflow?.id || workflow?.title || "").toLowerCase();
}

function lookupAgentProfile(agent) {
  if (!agent) return DEFAULT_AGENT_PROFILE;
  const id = normalizeAgentId(agent);
  if (AGENT_SURFACE_PROFILES[id]) return AGENT_SURFACE_PROFILES[id];
  return DEFAULT_AGENT_PROFILE;
}

function lookupWorkflowMeta(workflow) {
  if (!workflow) return DEFAULT_WORKFLOW_META;
  const id = normalizeWorkflowId(workflow);
  if (WORKFLOW_META_PRESETS[id]) return WORKFLOW_META_PRESETS[id];

  const stepCount = Array.isArray(workflow.steps) ? workflow.steps.length : 0;
  const title = String(workflow.title || "").toLowerCase();
  if (title.includes("check")) {
    return {
      ...DEFAULT_WORKFLOW_META,
      status: "active",
      schedule: "Weekly / morning check",
      nextRun: "Next check",
      runs: Math.max(stepCount, 1) * 3,
      successRate: 90,
      cost: "$2.40",
      linkedAgent: "Bug Hunter",
      runtime: "Scheduled",
      notes: "This workflow is clearly a repeated check rather than a one-off recipe.",
    };
  }

  return {
    ...DEFAULT_WORKFLOW_META,
    runs: stepCount,
    successRate: stepCount ? 88 : 0,
    cost: stepCount ? "$1.00" : "—",
    linkedAgent: "PR Reviewer",
  };
}

function formatMetricValue(value, fallback = "—") {
  if (value == null || value === "") return fallback;
  return String(value);
}

function formatDuration(ms) {
  const value = Number(ms || 0);
  if (!value) return "—";
  if (value < 1000) return `${value}ms`;
  const seconds = Math.round(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  return `${hours}h`;
}

function formatCadenceSeconds(seconds) {
  const value = Number(seconds || 0);
  if (!value) return "Not specified";
  if (value % 86400 === 0) return `Every ${value / 86400} day${value === 86400 ? "" : "s"}`;
  if (value % 3600 === 0) return `Every ${value / 3600} hour${value === 3600 ? "" : "s"}`;
  if (value % 60 === 0) return `Every ${value / 60} minute${value === 60 ? "" : "s"}`;
  return `Every ${value} seconds`;
}

function formatLifecycleStatus(status) {
  return status ? String(status).replace(/_/g, "-") : "Not specified";
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${Math.round(Number(value))}%`;
}

function getAgentMetricRow(agent) {
  const metrics = getAgentMetrics();
  const rows = Array.isArray(metrics?.agents) ? metrics.agents : [];
  const agentId = normalizeAgentId(agent);
  const title = String(agent?.title || "").toLowerCase();
  return rows.find((row) => normalizeAgentId({ id: row.agent_id, title: row.agent_title }) === agentId || String(row.agent_title || "").toLowerCase() === title) || null;
}

function buildAgentProfile(agent) {
  // Merge enriched real data when available for this agent.
  const enriched = (_enrichedAgent?.agentId || _enrichedAgent?.id) === agent?.id ? _enrichedAgent : null;
  const draft = agentDraft?.id === agent?.id ? agentDraft : null;
  const config = draft || enriched || agent;
  const profile = lookupAgentProfile(agent);
  const metricRow = getAgentMetricRow(agent);
  const fallback = AGENT_FALLBACK_METRICS[normalizeAgentId(agent)] || {};
  const successRate = metricRow
    ? Math.round((Number(metricRow.successes || 0) / Math.max(Number(metricRow.runs || 0), 1)) * 100)
    : fallback.successRate || 0;

  const recentRuns = Array.isArray(getAgentMetrics()?.recent)
    ? getAgentMetrics().recent.filter((run) => normalizeAgentId({ id: run.agent_id, title: run.agent_title }) === normalizeAgentId(agent)).slice(0, 3)
    : [];

  // Real linked skills from DB — fall back to hardcoded profile chips for display only.
  const linkedSkills = enriched?.linkedSkills ?? profile.linkedSkills;

  // O2/O3: persona, supportingFiles list, and recentRuns from enriched API response
  const persona = (enriched ?? agent).persona || null;
  const supportingFiles = enriched?.supportingFiles ?? [];
  const recentRunsFromDb = Array.isArray(enriched?.recentRuns) ? enriched.recentRuns : [];

  return {
    ...agent,
    // Policy fields from real agent config (enriched or raw agent record).
    provider: config.provider || "",
    model: config.model || "",
    fallbackProvider: config.fallbackProvider || null,
    fallbackModel: config.fallbackModel || null,
    memoryPolicy: config.memoryPolicy || null,
    permissionMode: config.permissionMode || null,
    outputContract: config.outputContract || null,
    reasonCodesEmitted: config.reasonCodesEmitted || [],
    cadenceSeconds: config.cadenceSeconds ?? null,
    lifecycleStatus: config.lifecycleStatus ?? null,
    urgencyTiers: config.urgencyTiers ?? null,
    failureModes: Array.isArray(config.failureModes) ? config.failureModes : null,
    dbTablesTouched: Array.isArray(config.dbTablesTouched) ? config.dbTablesTouched : null,
    implementationNotes: config.implementationNotes || null,
    inputsRequired: Array.isArray(config.inputsRequired) ? config.inputsRequired : null,
    instructionFileExists: enriched?.instructionFileExists ?? false,
    supportingFileCount: enriched?.supportingFileCount ?? 0,
    persona,
    supportingFiles,
    recentRunsFromDb,
    linkedSkills,
    // Cosmetic display-only fields from hardcoded profile (not runtime-active).
    memorySummary: profile.memorySummary,
    usedIn: profile.usedIn,
    policyNote: profile.policyNote,
    heroTone: profile.heroTone,
    metrics: {
      runs: metricRow ? metricRow.runs : fallback.runs || 0,
      successRate: metricRow ? successRate : fallback.successRate || 0,
      avgDuration: metricRow ? formatDuration(metricRow.avg_duration) : fallback.avgDuration || "—",
      avgCost: metricRow ? formatMetricValue(metricRow.avg_cost ? `$${Number(metricRow.avg_cost).toFixed(2)}` : null, fallback.avgCost || "—") : fallback.avgCost || "—",
      totalCost: metricRow ? formatMetricValue(metricRow.total_cost ? `$${Number(metricRow.total_cost).toFixed(2)}` : null, fallback.totalCost || "—") : fallback.totalCost || "—",
      totalInputTokens: metricRow ? formatMetricValue(metricRow.total_input_tokens ? `${Number(metricRow.total_input_tokens).toLocaleString()}` : null, fallback.totalInputTokens || "—") : fallback.totalInputTokens || "—",
      totalOutputTokens: metricRow ? formatMetricValue(metricRow.total_output_tokens ? `${Number(metricRow.total_output_tokens).toLocaleString()}` : null, fallback.totalOutputTokens || "—") : fallback.totalOutputTokens || "—",
      lastRun: recentRuns[0]?.status ? formatLastRun(recentRuns[0]) : fallback.lastRun || "—",
      health: metricRow
        ? (successRate >= 95 ? "Healthy" : successRate >= 90 ? "Watch" : "Needs attention")
        : fallback.health || "Healthy",
    },
    recentRuns,
  };
}

function formatLastRun(run) {
  const startedAt = Number(run?.started_at || 0);
  if (!startedAt) return "—";
  const diff = Math.max(Math.floor((Date.now() / 1000) - startedAt), 0);
  if (diff < 60) return "just now";
  const minutes = Math.floor(diff / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatRunCost(cost) {
  if (cost == null || Number.isNaN(Number(cost))) return "—";
  const value = Number(cost);
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

function getRunStatusTone(status) {
  switch (status) {
    case "completed":
      return "success";
    case "running":
      return "running";
    case "error":
      return "error";
    case "aborted":
      return "warn";
    default:
      return "neutral";
  }
}

function resetSelectedAgentRun() {
  selectedAgentRunId = "";
  selectedAgentRunDetail = null;
  selectedAgentRunLoading = false;
  selectedAgentRunError = "";
}

function renderAgentRunDetail(run) {
  if (selectedAgentRunLoading && selectedAgentRunId) {
    return `<div class="ops-run-detail-card"><div class="ops-empty-inline">Loading run details…</div></div>`;
  }
  if (selectedAgentRunError) {
    return `<div class="ops-run-detail-card"><div class="ops-empty-inline">${escapeHtml(selectedAgentRunError)}</div></div>`;
  }
  if (!run) return "";

  const rows = [
    ["Status", run.status || "unknown"],
    ["Run type", run.run_type || "single"],
    ["Started", run.started_at ? new Date(run.started_at * 1000).toLocaleString() : "—"],
    ["Duration", formatDuration(run.duration_ms)],
    ["Turns", run.turns != null ? String(run.turns) : "—"],
    ["Cost", formatRunCost(run.cost_usd)],
    ["Tokens", `${formatMetricValue(run.input_tokens ? Number(run.input_tokens).toLocaleString() : null, "0")} in / ${formatMetricValue(run.output_tokens ? Number(run.output_tokens).toLocaleString() : null, "0")} out`],
    ["Run ID", run.run_id || "—"],
  ];

  return `
    <div class="ops-run-detail-card" data-run-detail-id="${escapeAttr(run.run_id || "")}">
      <div class="ops-run-detail-head">
        <div>
          <div class="ops-run-detail-title">${escapeHtml(run.agent_title || run.agent_id || "Agent run")}</div>
          <div class="ops-run-detail-subtitle">${escapeHtml(run.agent_id || run.run_type || "run")}</div>
        </div>
        <span class="ops-run-status-pill ops-run-status-pill-${escapeAttr(getRunStatusTone(run.status))}">${escapeHtml(run.status || "unknown")}</span>
      </div>
      <div class="ops-run-detail-grid">
        ${rows.map(([label, value]) => `
          <div class="ops-run-detail-row">
            <span class="ops-run-detail-label">${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
          </div>
        `).join("")}
      </div>
      ${run.error ? `<div class="ops-run-detail-error">${escapeHtml(run.error)}</div>` : ""}
    </div>
  `;
}

function buildWorkflowProfile(workflow) {
  const meta = lookupWorkflowMeta(workflow);
  const selected = workflowDraft && workflowDraft.id === workflow.id ? workflowDraft : buildWorkflowDraft(workflow, meta);
  return {
    ...workflow,
    status: selected.meta?.status || meta.status,
    schedule: selected.meta?.schedule || meta.schedule,
    lastRun: meta.lastRun,
    nextRun: meta.nextRun,
    runs: meta.runs,
    successRate: meta.successRate,
    cost: meta.cost,
    owner: meta.owner,
    linkedAgent: meta.linkedAgent,
    runtime: meta.runtime,
    notes: meta.notes,
    steps: selected.steps,
  };
}

function buildWorkflowDraft(workflow, meta = lookupWorkflowMeta(workflow)) {
  const steps = Array.isArray(workflow?.steps) && workflow.steps.length
    ? workflow.steps.map((step, index) => ({
      // backward-compat: existing {label, prompt} steps become type="prompt"
      type: step.type || "prompt",
      label: step.label || `Step ${index + 1}`,
      prompt: step.prompt || "",
      // agent step fields
      agentId: step.agentId || "",
      instructions: step.instructions || "",
      // approval step fields
      title: step.title || "",
      description: step.description || "",
      behavior: step.behavior || "pause",
      // output step fields
      outputRef: step.outputRef || "run_summary",
      destination: step.destination || step.outputRef || "run_summary",
      draftAssetType: step.draftAssetType || "",
      draftCampaignId: step.draftCampaignId || "",
      generateNow: step.generateNow === true,
    }))
    : [
      { type: "prompt", label: "Step 1", prompt: "", agentId: "", instructions: "", title: "", description: "", behavior: "pause", outputRef: "run_summary" },
      { type: "prompt", label: "Step 2", prompt: "", agentId: "", instructions: "", title: "", description: "", behavior: "pause", outputRef: "run_summary" },
    ];

  return {
    id: workflow?.id || "",
    title: workflow?.title || "",
    description: workflow?.description || "",
    steps,
    meta: {
      status: meta.status || "active",
      schedule: meta.schedule || "Manual",
    },
  };
}

function ensureAgentSelection(agents) {
  const first = agents[0];
  if (!first) {
    selectedAgentId = "";
    return null;
  }
  if (!selectedAgentId || !agents.some((agent) => agent.id === selectedAgentId)) {
    selectedAgentId = first.id;
    writeStorage(OPS_AGENT_SELECTION_KEY, selectedAgentId);
  }
  return agents.find((agent) => agent.id === selectedAgentId) || first;
}

function decorateAgentForTree(agent) {
  const profile = buildAgentProfile(agent);
  const persona = profile.persona;
  const displayName = persona?.name || agent.title || agent.name || agent.agentId || "Unnamed";
  return {
    ...agent,
    displayName,
    description: persona?.purpose || agent.description || agent.goal || "",
    health: profile.metrics.health,
    lastRun: profile.metrics.lastRun || null,
    lastRunAt: getAgentMetricRow(agent)?.last_run_at || agent.lastRunAt || agent.last_run_at || null,
    modelLabel: profile.model || profile.provider || "",
    avatarUrl: persona?.profile_image_path || null,
  };
}

function isTreeCollapsed(kind, id) {
  if (agentTreeSearch.trim()) return false;
  const store = agentViewMode === "custom" ? agentCustomTreeCollapsed : agentTreeCollapsed;
  return Boolean(store[`${kind}:${id}`]);
}

function getAgentFolders(agents = getAgents()) {
  return [...new Set([
    ...agents.map((agent) => normalizeDisplayFolder(agent.metadata?.display_folder)).filter(Boolean),
    ...agentEmptyFolders.map((folder) => normalizeDisplayFolder(folder)).filter(Boolean),
  ])]
    .sort((a, b) => a.localeCompare(b));
}

function persistEmptyFolders() {
  writeStorage(OPS_AGENT_EMPTY_FOLDERS_KEY, agentEmptyFolders);
}

function createEmptyFolder(basePath = "") {
  const base = normalizeDisplayFolder(basePath);
  const name = normalizeDisplayFolder(window.prompt?.("New folder name") || "");
  if (!name) return;
  const path = normalizeDisplayFolder([base, name].filter(Boolean).join("/"));
  agentEmptyFolders = [...new Set([...agentEmptyFolders, path])];
  persistEmptyFolders();
  agentViewMode = "custom";
  writeStorage(OPS_AGENT_VIEW_MODE_KEY, agentViewMode);
  renderOperationsView("agents");
}

function renderAgentViewToggle() {
  return `
    <div class="ops-agent-view-toggle" aria-label="Agent tree view">
      <span>View:</span>
      ${["slug", "custom"].map((mode) => `
        <button type="button" class="${agentViewMode === mode ? "active" : ""}" data-ops-action="set-agent-view-mode" data-view-mode="${mode}">
          ${mode === "slug" ? "Slug" : "Custom"}
        </button>
      `).join("")}
    </div>
  `;
}

function renderAgentTreeControls() {
  const chips = [
    ["status", "healthy", "Status: Healthy"],
    ["status", "warning", "Status: Needs attention"],
    ["status", "never", "Status: Never run"],
    ["trigger", "manual", "Trigger: Manual"],
    ["trigger", "scheduled", "Trigger: Scheduled"],
  ];
  return `
    <div class="ops-agent-tree-controls">
      <input class="ops-agent-tree-search" type="search" value="${escapeAttr(agentTreeSearch)}" placeholder="Search agents" aria-label="Search agents" data-ops-agent-tree-search>
      <select class="ops-agent-tree-sort" aria-label="Sort agents" data-ops-agent-tree-sort>
        <option value="name"${agentTreeSort === "name" ? " selected" : ""}>By name (A-Z)</option>
        <option value="last_run"${agentTreeSort === "last_run" ? " selected" : ""}>By last run</option>
        <option value="health"${agentTreeSort === "health" ? " selected" : ""}>By run health</option>
      </select>
      ${renderAgentViewToggle()}
      ${agentViewMode === "custom" ? `<button type="button" class="ops-agent-new-folder" data-ops-action="create-agent-folder">+ New folder</button>` : ""}
      <div class="ops-agent-tree-filters" aria-label="Filter agents">
        ${chips.map(([group, value, label]) => {
          const active = group === "status"
            ? agentTreeFilters.statuses.includes(value)
            : agentTreeFilters.triggers.includes(value);
          return `<button type="button" class="ops-agent-filter-chip${active ? " active" : ""}" data-ops-action="toggle-agent-filter" data-filter-group="${group}" data-filter-value="${value}">${escapeHtml(label)}</button>`;
        }).join("")}
      </div>
    </div>
  `;
}

function renderAgentTreeRow(agent, selectedAgent, { custom = false } = {}) {
  const active = agent.id === selectedAgent?.id;
  const health = getAgentHealth(agent);
  const trigger = getAgentTrigger(agent);
  const initial = (agent.displayName || "A").charAt(0).toUpperCase();
  return `
    <button type="button" class="ops-agent-tree-row${active ? " active" : ""}" data-ops-action="select-agent" data-agent-id="${escapeAttr(agent.id)}"${custom ? ' draggable="true" data-drag-kind="agent"' : ""}>
      <span class="ops-agent-tree-avatar">
        ${agent.avatarUrl ? `<img src="${escapeAttr(agent.avatarUrl)}" alt="${escapeAttr(agent.displayName)} avatar">` : escapeHtml(initial)}
      </span>
      <span class="ops-agent-tree-main">
        <span class="ops-agent-tree-name">${escapeHtml(agent.displayName)}</span>
        <span class="ops-agent-tree-id">${escapeHtml(agent.id || agent.agentId || "")}</span>
      </span>
      <span class="ops-agent-tree-side">
        <span class="ops-agent-tree-model">${escapeHtml(agent.modelLabel || "default")}</span>
        <span class="ops-agent-tree-run"><i class="ops-agent-dot ops-agent-dot-${escapeAttr(health)}"></i>${escapeHtml(agent.lastRun || "never")} &middot; ${escapeHtml(trigger)}</span>
      </span>
      <span class="ops-agent-folder-action" data-ops-action="add-agent-to-folder" data-agent-id="${escapeAttr(agent.id)}" title="Add to folder">+</span>
    </button>
  `;
}

function renderCustomAgentNodes(nodes, selectedAgent) {
  return nodes.map((node) => {
    const collapsed = isTreeCollapsed("folder", node.id);
    return `
      <section class="ops-agent-tree-subdomain">
        <button type="button" class="ops-agent-tree-folder ops-agent-tree-folder-subdomain ops-agent-custom-folder"
          draggable="${node.id === "Unsorted" ? "false" : "true"}"
          data-ops-action="toggle-agent-tree"
          data-tree-kind="folder"
          data-tree-id="${escapeAttr(node.id)}"
          data-folder-path="${escapeAttr(node.id === "Unsorted" ? "" : node.id)}"
          aria-expanded="${collapsed ? "false" : "true"}">
          <span>${collapsed ? ">" : "v"} ${escapeHtml(node.label)}</span>
          <span>${node.agents.length}/${node.total}</span>
        </button>
        <div class="ops-agent-tree-rows${collapsed ? " hidden" : ""}">
          ${node.agents.length ? node.agents.map((item) => renderAgentTreeRow(item, selectedAgent, { custom: true })).join("") : ""}
          ${node.children.length ? renderCustomAgentNodes(node.children, selectedAgent) : ""}
          ${!node.agents.length && !node.children.length ? `<div class="ops-agent-tree-empty ops-agent-empty-dropzone">Drop agent here</div>` : ""}
        </div>
      </section>
    `;
  }).join("");
}

function renderAgentTree(agents, selectedAgent) {
  if (agentViewMode === "custom") {
    const view = createCustomAgentTreeView(agents, {
      query: agentTreeSearch.trim(),
      sort: agentTreeSort,
      filters: agentTreeFilters,
      emptyFolders: agentEmptyFolders,
    });
    return `
      ${renderAgentTreeControls()}
      <div class="ops-agent-tree ops-agent-tree-custom" data-localstorage-key="${escapeAttr(OPS_AGENT_CUSTOM_TREE_COLLAPSED_KEY)}">
        ${renderCustomAgentNodes(view, selectedAgent)}
      </div>
    `;
  }
  const view = createAgentTreeView(agents, {
    query: agentTreeSearch.trim(),
    sort: agentTreeSort,
    filters: agentTreeFilters,
  });
  return `
    ${renderAgentTreeControls()}
    <div class="ops-agent-tree" data-localstorage-key="${escapeAttr(OPS_AGENT_TREE_COLLAPSED_KEY)}">
      ${view.map((domain) => {
        const domainCollapsed = isTreeCollapsed("domain", domain.id);
        return `
          <section class="ops-agent-tree-domain">
            <button type="button" class="ops-agent-tree-folder ops-agent-tree-folder-domain" data-ops-action="toggle-agent-tree" data-tree-kind="domain" data-tree-id="${escapeAttr(domain.id)}" aria-expanded="${domainCollapsed ? "false" : "true"}">
              <span>${domainCollapsed ? ">" : "v"} ${escapeHtml(domain.label)}</span>
              <span>${domain.total}</span>
            </button>
            <div class="ops-agent-tree-children${domainCollapsed ? " hidden" : ""}">
              ${domain.subdomains.map((subdomain) => {
                const subdomainKey = `${domain.id}/${subdomain.id}`;
                const subdomainCollapsed = isTreeCollapsed("subdomain", subdomainKey);
                return `
                  <section class="ops-agent-tree-subdomain">
                    <button type="button" class="ops-agent-tree-folder ops-agent-tree-folder-subdomain" data-ops-action="toggle-agent-tree" data-tree-kind="subdomain" data-tree-id="${escapeAttr(subdomainKey)}" aria-expanded="${subdomainCollapsed ? "false" : "true"}">
                      <span>${subdomainCollapsed ? ">" : "v"} ${escapeHtml(subdomain.label)}</span>
                      <span>${subdomain.agents.length}/${subdomain.total}</span>
                    </button>
                    <div class="ops-agent-tree-rows${subdomainCollapsed ? " hidden" : ""}">
                      ${subdomain.agents.length
                        ? subdomain.agents.map((item) => renderAgentTreeRow(item, selectedAgent)).join("")
                        : `<div class="ops-agent-tree-empty">No agents matching filter.</div>`}
                    </div>
                  </section>
                `;
              }).join("")}
            </div>
          </section>
        `;
      }).join("")}
    </div>
  `;
}

async function patchAgentDisplayFolder(agentId, folderPath) {
  const agent = getAgents().find((item) => item.id === agentId);
  if (!agent) throw new Error(`Agent ${agentId} not found`);
  const nextFolder = normalizeDisplayFolder(folderPath);
  const metadata = { ...(agent.metadata || {}) };
  if (nextFolder) metadata.display_folder = nextFolder;
  else delete metadata.display_folder;
  await api.updateAgent(agentId, { metadata });
}

async function moveAgentToFolder(agentId, folderPath, { flipView = true } = {}) {
  const nextFolder = normalizeDisplayFolder(folderPath);
  await patchAgentDisplayFolder(agentId, folderPath);
  if (flipView) {
    agentViewMode = "custom";
    writeStorage(OPS_AGENT_VIEW_MODE_KEY, agentViewMode);
  }
  await refreshAgentsFromApi();
  renderOperationsView("agents");
  showToast("Moved", nextFolder ? `Moved to ${nextFolder}.` : "Moved to Unsorted.");
}

async function moveFolderToFolder(sourcePath, targetPath) {
  const source = normalizeDisplayFolder(sourcePath);
  const target = normalizeDisplayFolder(targetPath);
  if (!source || source === target || target.startsWith(`${source}/`)) return;
  const moved = getAgents().filter((agent) => {
    const folder = normalizeDisplayFolder(agent.metadata?.display_folder);
    return folder === source || folder.startsWith(`${source}/`);
  });
  await Promise.all(moved.map((agent) => {
    const folder = normalizeDisplayFolder(agent.metadata?.display_folder);
    const suffix = folder === source ? "" : folder.slice(source.length + 1);
    return patchAgentDisplayFolder(agent.id, [target, source.split("/").at(-1), suffix].filter(Boolean).join("/"));
  }));
  agentViewMode = "custom";
  writeStorage(OPS_AGENT_VIEW_MODE_KEY, agentViewMode);
  await refreshAgentsFromApi();
  renderOperationsView("agents");
}

function promptFolderForAgent(agentId) {
  document.querySelector(".ops-agent-folder-menu")?.remove();
  const button = document.querySelector(`[data-ops-action="add-agent-to-folder"][data-agent-id="${CSS.escape(agentId)}"]`);
  const rect = button?.getBoundingClientRect();
  const menu = document.createElement("div");
  menu.className = "ops-agent-folder-menu";
  menu.style.left = `${Math.min(rect?.left || 16, window.innerWidth - 240)}px`;
  menu.style.top = `${(rect?.bottom || 16) + 6}px`;
  const folders = getAgentFolders();
  menu.innerHTML = `
    <div class="ops-agent-folder-menu-title">Add to folder</div>
    ${folders.length ? folders.map((folder) => `<button type="button" data-folder="${escapeAttr(folder)}">${escapeHtml(folder)}</button>`).join("") : `<div class="ops-agent-folder-menu-empty">No folders yet</div>`}
    <button type="button" data-folder="">Unsorted</button>
    <hr>
    <button type="button" data-new-folder>+ New folder...</button>
  `;
  menu.addEventListener("click", (event) => {
    const item = event.target.closest("button");
    if (!item) return;
    event.stopPropagation();
    menu.remove();
    if (item.hasAttribute("data-new-folder")) {
      const name = normalizeDisplayFolder(window.prompt?.("New folder name") || "");
      if (name) void moveAgentToFolder(agentId, name).catch((err) => showToast("Move failed", err.message || "Could not move agent.", { isError: true }));
      return;
    }
    void moveAgentToFolder(agentId, item.dataset.folder || "").catch((err) => showToast("Move failed", err.message || "Could not move agent.", { isError: true }));
  });
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener("click", () => menu.remove(), { once: true }), 0);
}

function showFolderContextMenu(folderPath, x = 16, y = 16) {
  const folder = normalizeDisplayFolder(folderPath);
  document.querySelector(".ops-agent-folder-menu")?.remove();
  const menu = document.createElement("div");
  menu.className = "ops-agent-folder-menu ops-agent-context-menu";
  menu.style.left = `${Math.min(x, window.innerWidth - 240)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - 160)}px`;
  menu.innerHTML = `
    <button type="button" data-folder-action="create">Create subfolder...</button>
    ${folder ? `<button type="button" data-folder-action="rename">Rename folder...</button><button type="button" data-folder-action="delete">Delete folder...</button>` : ""}
  `;
  menu.addEventListener("click", (event) => {
    const action = event.target.closest("button")?.dataset.folderAction || "";
    if (!action) return;
    menu.remove();
    if (action === "create") { createEmptyFolder(folder); return; }
    if (action === "rename") {
    const next = normalizeDisplayFolder(window.prompt?.("Rename folder to", folder));
    if (!next) return;
    const agents = getAgents().filter((agent) => {
      const current = normalizeDisplayFolder(agent.metadata?.display_folder);
      return current === folder || current.startsWith(`${folder}/`);
    });
    void Promise.all(agents.map((agent) => {
      const current = normalizeDisplayFolder(agent.metadata?.display_folder);
      const suffix = current === folder ? "" : current.slice(folder.length + 1);
      return patchAgentDisplayFolder(agent.id, [next, suffix].filter(Boolean).join("/"));
    })).then(async () => {
      agentEmptyFolders = agentEmptyFolders.map((item) => {
        const current = normalizeDisplayFolder(item);
        if (current === folder) return next;
        if (current.startsWith(`${folder}/`)) return [next, current.slice(folder.length + 1)].filter(Boolean).join("/");
        return current;
      });
      await refreshAgentsFromApi();
      persistEmptyFolders();
      renderOperationsView("agents");
      showToast("Saved", `Renamed to ${next}.`);
    }).catch((err) => showToast("Save failed", err.message || "Rename failed.", { isError: true }));
    return;
  }
    if (action === "delete") {
    if (!window.confirm?.(`Delete "${folder}" and move agents to Unsorted?`)) return;
    const agents = getAgents().filter((agent) => {
      const current = normalizeDisplayFolder(agent.metadata?.display_folder);
      return current === folder || current.startsWith(`${folder}/`);
    });
    void Promise.all(agents.map((agent) => patchAgentDisplayFolder(agent.id, "")))
      .then(async () => {
        await refreshAgentsFromApi();
        agentEmptyFolders = agentEmptyFolders.filter((item) => {
          const current = normalizeDisplayFolder(item);
          return current !== folder && !current.startsWith(`${folder}/`);
        });
        persistEmptyFolders();
        renderOperationsView("agents");
        showToast("Saved", `${folder} deleted.`);
      })
      .catch((err) => showToast("Save failed", err.message || "Delete failed.", { isError: true }));
    }
  });
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener("click", () => menu.remove(), { once: true }), 0);
}

function ensureWorkflowSelection(workflows) {
  const first = workflows[0];
  if (!first) {
    selectedWorkflowId = "";
    return null;
  }
  // New-workflow mode: draft is unsaved — don't auto-select a saved workflow
  if (workflowDraft && workflowDraft.id === "" && selectedWorkflowId === "") return null;
  if (!selectedWorkflowId || !workflows.some((workflow) => workflow.id === selectedWorkflowId)) {
    selectedWorkflowId = first.id;
    writeStorage(OPS_WORKFLOW_SELECTION_KEY, selectedWorkflowId);
  }
  return workflows.find((workflow) => workflow.id === selectedWorkflowId) || first;
}

function ensureSkillSelection(skills) {
  const visible = skills.length ? skills : getPendingSkills();
  const first = visible[0];
  if (!first) {
    selectedSkillId = "";
    return null;
  }
  if (!selectedSkillId || !visible.some((skill) => skill.id === selectedSkillId)) {
    selectedSkillId = first.id;
    writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
  }
  return visible.find((skill) => skill.id === selectedSkillId) || first;
}

function ensureAutomationSelection(automations) {
  const visible = automations.length ? automations : [];
  const first = visible[0];
  if (!first) {
    selectedAutomationId = "";
    return null;
  }
  if (!selectedAutomationId || !visible.some((automation) => automation.id === selectedAutomationId)) {
    selectedAutomationId = first.id;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
  }
  return visible.find((automation) => automation.id === selectedAutomationId) || first;
}

function getAgentDraft(agent) {
  if (agentDraft && agentDraft.id === agent?.id) return agentDraft;
  // Prefer enriched data when available for the same agent.
  const enriched = (_enrichedAgent?.agentId || _enrichedAgent?.id) === agent?.id ? _enrichedAgent : agent;
  agentDraft = {
    id: enriched?.id || "",
    title: enriched?.title || "",
    description: enriched?.description || "",
    goal: enriched?.goal || "",
    icon: enriched?.icon || "tool",
    constraints: {
      maxTurns: Number(enriched?.constraints?.maxTurns || 50),
      timeoutMs: Number(enriched?.constraints?.timeoutMs || 300000),
    },
    provider: enriched?.provider || "",
    model: enriched?.model || "",
    fallbackProvider: enriched?.fallbackProvider || "",
    fallbackModel: enriched?.fallbackModel || "",
    memoryPolicy: enriched?.memoryPolicy ? { ...enriched.memoryPolicy } : { scope: "project" },
    permissionMode: enriched?.permissionMode || "bypass",
    outputContract: enriched?.outputContract ? { ...enriched.outputContract } : { type: "run_summary" },
    reasonCodesEmitted: enriched?.reasonCodesEmitted || [],
  };
  writeStorage(OPS_AGENT_DRAFT_KEY, agentDraft);
  return agentDraft;
}

function getWorkflowDraft(workflow) {
  // New-workflow mode: preserve the unsaved draft regardless of workflow arg
  if (workflowDraft && workflowDraft.id === "") return workflowDraft;
  if (workflowDraft && workflowDraft.id === workflow?.id) return workflowDraft;
  workflowDraft = buildWorkflowDraft(workflow);
  writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
  return workflowDraft;
}

function updateAgentDraftField(field, value) {
  if (!agentDraft) return;
  if (field === "title" || field === "description" || field === "goal" || field === "icon" ||
      field === "provider" || field === "model" || field === "fallbackProvider" || field === "fallbackModel") {
    agentDraft[field] = value;
    if (field === "provider") {
      const models = getSourceModels(value || "claude-code").map((model) => model.value);
      if (!models.includes(agentDraft.model)) agentDraft.model = firstModelForProvider(value);
    }
    if (field === "fallbackProvider") {
      const models = getSourceModels(value || "claude-code").map((model) => model.value);
      if (!value) agentDraft.fallbackModel = "";
      else if (!models.includes(agentDraft.fallbackModel)) agentDraft.fallbackModel = firstModelForProvider(value);
    }
  } else if (field === "maxTurns") {
    agentDraft.constraints.maxTurns = Number(value || 0);
  } else if (field === "timeoutMs") {
    agentDraft.constraints.timeoutMs = Number(value || 0);
  } else if (field === "memoryScope") {
    agentDraft.memoryPolicy = { ...agentDraft.memoryPolicy, scope: value };
  } else if (field === "permissionMode") {
    agentDraft.permissionMode = value;
  } else if (field === "outputType") {
    agentDraft.outputContract = { ...agentDraft.outputContract, type: value };
  }
  writeStorage(OPS_AGENT_DRAFT_KEY, agentDraft);
}

function updateWorkflowDraftField(field, value) {
  if (!workflowDraft) return;
  if (field === "title" || field === "description") {
    workflowDraft[field] = value;
  } else if (field === "status" || field === "schedule") {
    workflowDraft.meta[field] = value;
  }
  writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
}

function updateWorkflowStepField(index, field, value) {
  if (!workflowDraft?.steps?.[index]) return;
  workflowDraft.steps[index][field] = value;
  writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
}

function appendWorkflowStep() {
  if (!workflowDraft) return;
  workflowDraft.steps.push({
    type: "prompt",
    label: `Step ${workflowDraft.steps.length + 1}`,
    prompt: "",
    agentId: "",
    instructions: "",
    title: "",
    description: "",
    behavior: "pause",
    outputRef: "run_summary",
    destination: "run_summary",
    draftAssetType: "",
    draftCampaignId: "",
    generateNow: false,
  });
  writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
}

function removeWorkflowStep(index) {
  if (!workflowDraft) return;
  workflowDraft.steps.splice(index, 1);
  if (!workflowDraft.steps.length) {
    workflowDraft.steps.push({ label: "Step 1", prompt: "" });
  }
  workflowDraft.steps = workflowDraft.steps.map((step, idx) => ({
    label: step.label || `Step ${idx + 1}`,
    prompt: step.prompt || "",
  }));
  writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
}

function getAgentOverviewStats(agents, metrics) {
  const activeAgents = agents.length;
  const totalRuns = Array.isArray(metrics?.agents)
    ? metrics.agents.reduce((sum, row) => sum + Number(row.runs || 0), 0)
    : 0;
  const avgSuccess = Array.isArray(metrics?.agents) && metrics.agents.length
    ? Math.round(metrics.agents.reduce((sum, row) => sum + Math.round((Number(row.successes || 0) / Math.max(Number(row.runs || 0), 1)) * 100), 0) / metrics.agents.length)
    : 0;
  return {
    activeAgents,
    totalRuns,
    avgSuccess,
    chains: getAgentChains().length,
    dags: getAgentDags().length,
  };
}

function getWorkflowOverviewStats(workflows) {
  const selected = workflows.map((workflow) => buildWorkflowProfile(workflow));
  return {
    total: workflows.length,
    active: selected.filter((workflow) => workflow.status === "active").length,
    paused: selected.filter((workflow) => workflow.status === "paused").length,
    draft: selected.filter((workflow) => workflow.status === "draft").length,
    steps: selected.reduce((sum, workflow) => sum + (workflow.steps?.length || 0), 0),
  };
}

function buildSkillCategoryButtons(approvedSkills) {
  // "All" button is always first
  const allBtn = `
    <button type="button"
      class="ops-category${selectedSkillCategory === "all" ? " active" : ""}"
      data-ops-action="select-skill-category"
      data-skill-category="all">
      <span>All</span>
      <small>${escapeHtml(String(approvedSkills.length))}</small>
    </button>`;

  const catBtns = _skillCategories.map(({ category, count }) => `
    <button type="button"
      class="ops-category${selectedSkillCategory === category ? " active" : ""}"
      data-ops-action="select-skill-category"
      data-skill-category="${escapeAttr(category)}">
      <span>${escapeHtml(category)}</span>
      <small>${escapeHtml(String(count))}</small>
    </button>`).join("");

  return allBtn + catBtns;
}

function getSkillOverviewStats(approved, pending) {
  const totalUses = approved.reduce((sum, skill) => sum + Number(skill.uses || 0), 0);
  const avgSuccess = approved.length
    ? Math.round(approved.reduce((sum, skill) => sum + Number(skill.success || 0), 0) / approved.length)
    : 0;
  return {
    approved: approved.length,
    pending: pending.length,
    totalUses,
    avgSuccess,
  };
}

function getAutomationOverviewStats(automations) {
  return {
    active: automations.filter((a) => a.status === "active").length,
    paused: automations.filter((a) => a.status === "paused").length,
    triggered: automations.filter((a) => a.trigger_type === "schedule").length,
  };
}

function getMarketingCampaignStats(campaigns = MARKETING_CAMPAIGNS) {
  return {
    ready: campaigns.filter((campaign) => /ready|template|asset/i.test(campaign.status)).length,
    review: campaigns.filter((campaign) => /gate|validation|review/i.test(campaign.stage)).length,
    live: campaigns.filter((campaign) => /in play|live/i.test(campaign.status)).length,
    blocked: campaigns.filter((campaign) => /needs|blocked/i.test(campaign.status)).length,
  };
}

function getCampaignOpsOverviewState() {
  if (_campaignOpsOverview && typeof _campaignOpsOverview === "object") return _campaignOpsOverview;
  return buildCampaignOpsPreviewOverview();
}

function ensureMarketingCampaignSelection(campaigns) {
  const first = campaigns[0];
  if (!first) {
    selectedAutomationId = "";
    return null;
  }
  if (!selectedAutomationId || !campaigns.some((campaign) => campaign.id === selectedAutomationId)) {
    selectedAutomationId = first.id;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
  }
  return campaigns.find((campaign) => campaign.id === selectedAutomationId) || first;
}

function renderCampaignStatusPill(campaign) {
  const status = String(campaign?.status || "");
  let variant = "neutral";
  if (/ready|template|asset|in play/i.test(status)) variant = "success";
  if (/needs|blocked/i.test(status)) variant = "warn";
  return renderOpsPill(status, variant);
}

function renderCampaignDecisionPill(campaign) {
  const label = String(campaign?.decisionState || "pending_review").replace(/_/g, " ");
  let tone = "neutral";
  if (/approved|active/.test(campaign?.decisionState || "")) tone = "success";
  if (/rejected|monitoring/.test(campaign?.decisionState || "")) tone = "warn";
  if (/changes/.test(campaign?.decisionState || "")) tone = "accent";
  return renderOpsPill(label, tone);
}

function renderSummaryChip(label, value) {
  return `
    <span class="ops-summary-chip">
      <span class="ops-summary-chip-label">${escapeHtml(label)}</span>
      <span class="ops-summary-chip-value">${escapeHtml(String(value))}</span>
    </span>
  `;
}

function renderOperationsHero(title, eyebrow, copy, chips = [], actions = []) {
  return `
    <section class="shell-hero operations-hero">
      <div class="shell-eyebrow">${escapeHtml(eyebrow)}</div>
      <div class="operations-hero-top">
        <div>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(copy)}</p>
        </div>
        ${actions.length ? `
          <div class="operations-hero-actions">
            ${actions.map((action) => action).join("")}
          </div>
        ` : ""}
      </div>
      ${chips.length ? `<div class="operations-summary">${chips.map((chip) => chip).join("")}</div>` : ""}
    </section>
  `;
}

function renderOpsButton(label, action, extra = {}) {
  const attrs = Object.entries(extra).map(([key, value]) => ` data-${escapeAttr(key)}="${escapeAttr(value)}"`).join("");
  return `<button type="button" class="ops-button" data-ops-action="${escapeAttr(action)}"${attrs}>${escapeHtml(label)}</button>`;
}

function renderOpsSecondaryButton(label, action, extra = {}) {
  const attrs = Object.entries(extra).map(([key, value]) => ` data-${escapeAttr(key)}="${escapeAttr(value)}"`).join("");
  return `<button type="button" class="ops-button ops-button-secondary" data-ops-action="${escapeAttr(action)}"${attrs}>${escapeHtml(label)}</button>`;
}

function renderOpsPill(label, tone = "") {
  return `<span class="ops-pill${tone ? ` ops-pill-${escapeAttr(tone)}` : ""}">${escapeHtml(label)}</span>`;
}

function renderOverviewPage() {
  const agents = getAgents();
  const workflows = getWorkflows();
  const approvedSkills = getApprovedSkills();
  const pendingSkills = getPendingSkills();
  const automations = _automations;
  const metrics = getAgentMetrics();
  const agentStats = getAgentOverviewStats(agents, metrics);
  const workflowStats = getWorkflowOverviewStats(workflows);
  const skillStats = getSkillOverviewStats(approvedSkills, pendingSkills);
  const automationStats = getAutomationOverviewStats(automations);

  return `
    ${renderOperationsHero(
      "Operations",
      "Operations hub",
      "Who does work, what skills they can use, how work is defined, and when it runs all stay in the main canvas now.",
      [
        renderSummaryChip("Agents", `${agentStats.activeAgents} rostered`),
        renderSummaryChip("Skills", `${skillStats.approved} approved`),
        renderSummaryChip("Workflows", `${workflowStats.total} saved`),
        renderSummaryChip("Automations", `${automationStats.active} active`),
      ],
    )}
    <section class="ops-launch-grid">
      <article class="ops-launch-card">
        <div class="ops-launch-head">
          <div>
            <div class="ops-launch-eyebrow">Agents</div>
            <h3>Who does work<span id="ops-inbox-badge-placeholder"></span></h3>
          </div>
          <span class="ops-pill">${escapeHtml(`${agentStats.activeAgents} rostered`)}</span>
        </div>
        <p>Roster and detail view for identity, policy, linked skills, memory, health, and where each agent is used.</p>
        <div class="ops-launch-meta">
          <span>${escapeHtml(String(agentStats.totalRuns))} runs</span>
          <span>${escapeHtml(formatPercent(agentStats.avgSuccess))} avg success</span>
        </div>
        <div class="operations-hero-actions">
          ${renderOpsButton("Open Agents", "open-shell-view", { "shell-view": "agents" })}
          ${renderOpsSecondaryButton("Open Operations", "open-shell-view", { "shell-view": "operations" })}
        </div>
      </article>
      <article class="ops-launch-card">
        <div class="ops-launch-head">
          <div>
            <div class="ops-launch-eyebrow">Skills</div>
            <h3>Capability library</h3>
          </div>
          <span class="ops-pill">${escapeHtml(`${skillStats.pending} proposed`)}</span>
        </div>
        <p>Approved library and proposal queue, with Memory > Skills / Rules remaining the promotion boundary.</p>
        <div class="ops-launch-meta">
          <span>${escapeHtml(String(skillStats.totalUses))} uses</span>
          <span>${escapeHtml(formatPercent(skillStats.avgSuccess))} avg success</span>
        </div>
        <div class="operations-hero-actions">
          ${renderOpsButton("Open Skills", "open-shell-view", { "shell-view": "skills" })}
          ${renderOpsSecondaryButton("Open Memory", "open-shell-view", { "shell-view": MEMORY_VIEW })}
        </div>
      </article>
      <article class="ops-launch-card">
        <div class="ops-launch-head">
          <div>
            <div class="ops-launch-eyebrow">Workflows</div>
            <h3>Builder + inspector</h3>
          </div>
          <span class="ops-pill">${escapeHtml(`${workflowStats.active} active`)}</span>
        </div>
        <p>The recipe is the primary object. Schedule and runtime data travel with it, not instead of it.</p>
        <div class="ops-launch-meta">
          <span>${escapeHtml(String(workflowStats.steps))} steps total</span>
          <span>${escapeHtml(String(workflowStats.paused))} paused</span>
        </div>
        <div class="operations-hero-actions">
          ${renderOpsButton("Open Workflows", "open-shell-view", { "shell-view": "workflows" })}
          ${renderOpsSecondaryButton("Open Operations", "open-shell-view", { "shell-view": "operations" })}
        </div>
      </article>
      <article class="ops-launch-card">
        <div class="ops-launch-head">
          <div>
            <div class="ops-launch-eyebrow">Automations</div>
            <h3>Runtime registry</h3>
          </div>
          <span class="ops-pill">${escapeHtml(`${automationStats.paused} paused`)}</span>
        </div>
        <p>Fast list/toggle control for active or paused runtime schedules. No second workflow builder here.</p>
        <div class="ops-launch-meta">
          <span>${escapeHtml(String(automationStats.triggered))} triggered</span>
          <span>${escapeHtml(String(automationStats.active))} active</span>
        </div>
        <div class="operations-hero-actions">
          ${renderOpsButton("Open Automations", "open-shell-view", { "shell-view": "automations" })}
          ${renderOpsSecondaryButton("Open Workflows", "open-shell-view", { "shell-view": "workflows" })}
        </div>
      </article>
    </section>
  `;
}

function renderAgentsPage() {
  const agents = getAgents();
  const metrics = getAgentMetrics();
  const loading = Boolean(getState("agentsLoading"));
  const loaded = Boolean(getState("agentsLoaded"));
  const error = getState("agentsError");

  if ((!loaded && !loading && !agents.length) || loading) {
    return `
      ${renderOperationsHero(
        "Agents",
        "Roster and detail",
        "Loading the worker roster, policy summaries, and run-health metrics...",
        [
          renderSummaryChip("Roster", "Loading"),
          renderSummaryChip("Run health", "Loading"),
          renderSummaryChip("Skills", "Loading"),
          renderSummaryChip("Memory", "Loading"),
        ],
      )}
      <section class="ops-grid ops-agents-grid">
        <article class="ops-panel ops-list-panel ops-loading-panel">
          <div class="ops-loading-block ops-loading-block-wide"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
        </article>
        <article class="ops-panel ops-detail-panel ops-loading-panel">
          <div class="ops-loading-block ops-loading-block-wide"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block ops-loading-block-wide"></div>
        </article>
      </section>
    `;
  }

  if (error) {
    return `
      ${renderOperationsHero(
        "Agents",
        "Roster and detail",
        "The agent registry could not load cleanly, so the page is showing a safe failure state instead of guessing.",
        [renderSummaryChip("Load", "Failed")],
      )}
      <section class="ops-grid ops-agents-grid">
        <article class="ops-panel ops-list-panel">
          <div class="ops-empty-state">
            <strong>Could not load agents</strong>
            <span>${escapeHtml(String(error))}</span>
          </div>
        </article>
        <article class="ops-panel ops-detail-panel">
          <div class="ops-empty-state">
            <strong>Retry the load</strong>
            <span>Refresh the surface after confirming the agents config still exists.</span>
          </div>
        </article>
      </section>
    `;
  }

  // Load skills list if not yet fetched — needed to populate the attach-skill dropdown.
  if (!_skillsLoaded) {
    refreshSkillsFromApi().then(() => renderOperationsView("agents")).catch(() => {});
  }
  if (!_reasonCodesLoaded && !_reasonCodesLoading) {
    refreshReasonCodesFromApi().then(() => renderOperationsView("agents")).catch(() => {});
  }

  const selectedAgent = ensureAgentSelection(agents);
  // If the enriched data isn't loaded yet for this agent, kick off the fetch and re-render when ready.
  if (selectedAgent && _enrichedAgentLoadedForId !== selectedAgent.id) {
    loadEnrichedAgent(selectedAgent.id).then(() => renderOperationsView("agents")).catch(() => {});
  }
  // Ensure agentDraft is initialized for the selected agent (covers post-attach/detach re-renders).
  if (selectedAgent) getAgentDraft(selectedAgent);
  const selectedProfile = buildAgentProfile(selectedAgent);
  const treeAgents = agents.map(decorateAgentForTree);
  const agentChips = [
    renderSummaryChip("Roster", `${agents.length} agents`),
    renderSummaryChip("Run health", selectedProfile.metrics.health),
    renderSummaryChip("Skills linked", String(selectedProfile.linkedSkills.length)),
    renderSummaryChip("Memory", selectedProfile.memorySummary),
  ];

  return `
    ${renderOperationsHero(
      "Agents",
      "Who does work",
      "A roster for scanning, plus a dedicated main-canvas profile for policy, memory, skills, and runtime health.",
      agentChips,
      [renderOpsButton("Build with Agent-Builder", "open-shell-view", { "shell-view": "agents/builder" }), renderOpsSecondaryButton("New agent", "new-agent"), ...(selectedAgent ? [renderOpsSecondaryButton("Edit with Builder", "edit-agent-with-builder", { "agent-id": selectedAgent.id, "agent-db-id": selectedAgent.dbId ?? (_enrichedAgent?.id ?? "") })] : []), renderOpsSecondaryButton("Back to Operations", "open-shell-view", { "shell-view": "operations" })],
    )}
    <section class="ops-grid ops-agents-grid">
      <article class="ops-panel ops-list-panel">
        <div class="ops-panel-head">
          <div>
            <div class="ops-panel-eyebrow">Roster</div>
            <h3>Agents</h3>
            <p>Click a row to open the dedicated profile and inspect the full operating definition.</p>
          </div>
          <span class="ops-pill">${escapeHtml(String(agents.length))} rostered</span>
        </div>
        ${renderAgentTree(treeAgents, selectedAgent)}
      </article>
      <article class="ops-panel ops-detail-panel">
        ${renderAgentDetail(selectedProfile)}
      </article>
    </section>
  `;
}

function renderAgentDetail(agent) {
  const metricRow = getAgentMetricRow(agent);
  // Prefer real DB runs from the enriched endpoint (recentRunsFromDb), fall back to metrics runs.
  const recentRuns = (agent.recentRunsFromDb && agent.recentRunsFromDb.length > 0)
    ? agent.recentRunsFromDb
    : (agent.recentRuns || []);
  const selectedRun = selectedAgentRunDetail && normalizeAgentId({ id: selectedAgentRunDetail.agent_id, title: selectedAgentRunDetail.agent_title }) === normalizeAgentId(agent)
    ? selectedAgentRunDetail
    : recentRuns.find((run) => run.run_id && run.run_id === selectedAgentRunId) || null;
  return `
    <div class="ops-panel-head ops-detail-head">
      <div>
        <div class="ops-panel-eyebrow">Agent profile</div>
        <h3>${escapeHtml(agent.title || "Untitled agent")}</h3>
        <p>${escapeHtml(agent.heroTone || agent.description || agent.goal || "Reusable worker")}</p>
      </div>
      <div class="ops-detail-head-actions">
        <span class="ops-pill ops-pill-${escapeAttr(agent.metrics.health === "Healthy" ? "success" : "warn")}">${escapeHtml(agent.metrics.health)}</span>
        <span class="ops-pill">${escapeHtml(agent.metrics.lastRun || "—")}</span>
      </div>
    </div>

    <div class="ops-detail-stack">
      <section class="ops-mini-card ops-persona-card">
        <div class="ops-mini-card-label">Persona / soul
          ${agent.persona ? "" : `<span class="ops-pill" style="margin-left:6px;font-size:0.7rem">Not set</span>`}
        </div>
        <div class="ops-form-grid">
          <label class="ops-field">
            <span>Name</span>
            <input type="text" value="${escapeAttr(agent.persona?.name || "")}" data-ops-field="persona-name" placeholder="e.g. Iris">
          </label>
          <label class="ops-field">
            <span>Ghostwrite</span>
            <select data-ops-field="persona-ghostwrite">
              <option value=""${!agent.persona?.ghostwrite ? " selected" : ""}>No — agent speaks as itself</option>
              <option value="true"${agent.persona?.ghostwrite ? " selected" : ""}>Yes — output framed as Jon</option>
            </select>
          </label>
        </div>
        <label class="ops-field">
          <span>Purpose (one line)</span>
          <input type="text" value="${escapeAttr(agent.persona?.purpose || "")}" data-ops-field="persona-purpose" placeholder="e.g. Watches my Jira board and brings morning insight">
        </label>
        <label class="ops-field">
          <span>Voice notes</span>
          <input type="text" value="${escapeAttr(agent.persona?.voice_notes || "")}" data-ops-field="persona-voice-notes" placeholder="e.g. lowercase, concise, no greetings">
        </label>
        <div class="ops-detail-actions" style="margin-top:10px">
          ${renderOpsButton("Save persona", "save-persona")}
          ${renderOpsSecondaryButton("Cancel", "reset-persona")}
        </div>
        ${agent.persona?.ghostwrite ? `<p class="ops-muted-copy" style="margin-top:8px">Ghostwrite is active — this agent's output is framed as if Jon wrote it. Voice samples from the personality profile are prepended to the system prompt at run-time.</p>` : ""}
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Identity / purpose</div>
        <div class="ops-mini-card-title">${escapeHtml(agent.persona?.name || agent.title || "Untitled agent")}</div>
        <p>${escapeHtml(agent.persona?.purpose || agent.description || "No description yet.")}</p>
      </section>

      ${renderOperatingBlueprint(agent)}

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Recent runs</div>
        <div class="ops-run-list">
          ${recentRuns.length ? recentRuns.map((run) => `
            <button
              type="button"
              class="ops-run-row${run.run_id && run.run_id === selectedAgentRunId ? " active" : ""}"
              data-ops-action="open-agent-run"
              data-run-id="${escapeAttr(run.run_id || "")}"
            >
              <span>${escapeHtml(formatLastRun(run))}</span>
              <span class="ops-run-status-pill ops-run-status-pill-${escapeAttr(getRunStatusTone(run.status))}">${escapeHtml(run.status || "running")}</span>
              <span>${escapeHtml(formatMetricValue(run.run_type, "single"))}</span>
              <span class="ops-run-open-label">${run.run_id && run.run_id === selectedAgentRunId ? "Hide details" : "Open run"}</span>
            </button>
          `).join("") : `
            <div class="ops-empty-inline">No recent runs found for this agent yet.</div>
          `}
        </div>
        ${renderAgentRunDetail(selectedRun)}
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Operating definition</div>
        <div class="ops-form-grid">
          <label class="ops-field">
            <span>Title</span>
            <input type="text" value="${escapeAttr(agent.title || "")}" data-ops-field="agent-title">
          </label>
          <label class="ops-field">
            <span>Icon</span>
            <input type="text" value="${escapeAttr(agent.icon || "")}" data-ops-field="agent-icon">
          </label>
        </div>
        <label class="ops-field">
          <span>Description</span>
          <textarea rows="2" data-ops-field="agent-description">${escapeHtml(agent.description || "")}</textarea>
        </label>
        <label class="ops-field">
          <span>Goal / instructions</span>
          <textarea rows="4" data-ops-field="agent-goal">${escapeHtml(agent.goal || "")}</textarea>
        </label>
        <div class="ops-form-grid">
          <label class="ops-field">
            <span>Max turns</span>
            <input type="number" min="1" step="1" value="${escapeAttr(String(agent.constraints?.maxTurns || 50))}" data-ops-field="agent-max-turns">
          </label>
          <label class="ops-field">
            <span>Timeout (ms)</span>
            <input type="number" min="1000" step="1000" value="${escapeAttr(String(agent.constraints?.timeoutMs || 300000))}" data-ops-field="agent-timeout-ms">
          </label>
        </div>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Instruction file
          ${agent.instructionFileExists
            ? `<span class="ops-pill ops-pill-success" style="margin-left:6px;font-size:0.7rem">Active at runtime</span>`
            : `<span class="ops-pill" style="margin-left:6px;font-size:0.7rem">Not set — Goal used</span>`}
        </div>
        ${agent.instructionFileExists
          ? `<p class="ops-muted-copy">This file is active at runtime. When present, it replaces the Goal field as the agent's primary instruction source. Clear it to fall back to Goal.</p>`
          : `<p class="ops-muted-copy">No instruction file. The Goal field below is the active instruction source. Write an instruction file to make it the runtime-primary source.</p>`}
        <label class="ops-field">
          <span>Instruction file content</span>
          <textarea rows="6" data-ops-field="agent-instruction" placeholder="Write detailed agent instructions here. When saved, this file becomes the runtime instruction source instead of Goal.">${escapeHtml(_agentInstructionContent || "")}</textarea>
        </label>
        <button type="button" class="ops-secondary-btn" style="margin-top:6px" data-ops-action="generate-instruction-from-goal">Generate from Goal</button>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Reason codes emitted <span class="ops-badge-success">runtime-active</span></div>
        <div class="ops-field">
          <span>Allowed codes</span>
          <div class="ops-reason-code-multiselect" data-ops-reason-code-multiselect>
            ${renderReasonCodeOptions(agent.reasonCodesEmitted || []) || '<p class="ops-muted-copy">No active reason codes found.</p>'}
          </div>
        </div>
        <p class="ops-muted-copy">When empty, runtime allows any active registry code.</p>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Provider policy</div>
        <div class="ops-form-grid">
          <label class="ops-field">
            <span>Preferred provider</span>
            <select data-ops-field="provider">
              ${providerOptions(agent.provider || "claude-code")}
            </select>
          </label>
          <label class="ops-field">
            <span>Model</span>
            <select data-ops-field="model">${modelOptions(agent.provider, agent.model || "")}</select>
          </label>
        </div>
        <div class="ops-form-grid" style="margin-top:8px">
          <div class="ops-field">
            <span class="ops-field-label">Fallback provider ${agent.fallbackProvider ? "" : '<span class="ops-pill ops-pill-warn">Not set — required</span>'}</span>
            <select data-ops-field="fallbackProvider">
              ${providerOptions(agent.fallbackProvider || "", true)}
            </select>
          </div>
          <div class="ops-field">
            <span class="ops-field-label">Fallback model</span>
            <select data-ops-field="fallbackModel">${modelOptions(agent.fallbackProvider, agent.fallbackModel || "")}</select>
          </div>
        </div>
        ${agent.policyNote ? `<p class="ops-muted-copy" style="margin-top:8px">${escapeHtml(agent.policyNote)}</p>` : ""}
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Attached skills</div>
        ${Array.isArray(agent.linkedSkills) && agent.linkedSkills.length > 0
          ? `<div class="ops-chip-row" style="flex-wrap:wrap;gap:6px;margin-bottom:8px">
              ${agent.linkedSkills.map((skill) => {
                const skillId = typeof skill === "object" ? skill.id : null;
                const skillName = typeof skill === "object" ? (skill.name || skill.id) : skill;
                return `<span class="ops-pill ops-pill-accent">${escapeHtml(skillName)}${
                  skillId ? ` <button type="button" class="ops-pill-detach" data-ops-action="detach-skill" data-skill-id="${escapeAttr(String(skillId))}" title="Detach skill" aria-label="Detach ${escapeAttr(skillName)}">×</button>` : ""
                }</span>`;
              }).join("")}
            </div>`
          : `<p class="ops-muted-copy" style="margin-bottom:8px">No skills attached. Attach approved skills to include them in this agent's runtime context.</p>`}
        <div class="ops-attach-skill-row">
          <select data-ops-field="attach-skill-select" class="ops-attach-skill-select">
            <option value="">— attach a skill —</option>
            ${getApprovedSkills()
              .filter((s) => !Array.isArray(agent.linkedSkills) || !agent.linkedSkills.some((ls) => (typeof ls === "object" ? ls.id : ls) === s.id))
              .map((s) => `<option value="${escapeAttr(String(s.id))}">${escapeHtml(s.name)}</option>`)
              .join("")}
          </select>
          <button type="button" class="ops-secondary-btn" data-ops-action="attach-skill">Attach</button>
        </div>
      </section>

      <section class="ops-mini-card" id="agent-connectors-section" data-agent-id="${escapeAttr(String(agent.dbId || ''))}">
        <div class="ops-mini-card-label">Linked connectors</div>
        <div class="ops-connectors-list" id="agent-connectors-list">
          <div class="ops-loading-inline">Loading connectors…</div>
        </div>
        <div class="ops-attach-connector-row" style="margin-top:8px;display:flex;gap:8px;align-items:center">
          <select data-ops-field="attach-connector-select" class="ops-attach-connector-select">
            <option value="">— link a connector —</option>
          </select>
          <input type="text" data-ops-field="attach-connector-namespace" placeholder="tool namespace (e.g. starbridge)" style="flex:1;min-width:120px">
          <button type="button" class="ops-secondary-btn" data-ops-action="attach-connector">Link</button>
        </div>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Memory policy <span class="ops-badge-coming">stored — scope filtering coming</span></div>
        <label class="ops-field">
          <span>Memory scope</span>
          <select data-ops-field="memoryScope">
            ${["project", "workspace", "global"].map((s) =>
              `<option value="${escapeAttr(s)}"${(agent.memoryPolicy?.scope || "project") === s ? " selected" : ""}>${escapeHtml(s.charAt(0).toUpperCase() + s.slice(1))}</option>`
            ).join("")}
          </select>
        </label>
        <p class="ops-muted-copy">Declares which memory observations this agent should draw from. Stored and displayed; scope-filtered retrieval is not yet active.</p>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Permissions policy</div>
        <label class="ops-field">
          <span>Permission mode <span class="ops-badge-success">runtime-active</span></span>
          <select data-ops-field="permissionMode">
            <option value="bypass"${(agent.permissionMode || "bypass") === "bypass" ? " selected" : ""}>Bypass — skip all tool approval prompts</option>
            <option value="default"${agent.permissionMode === "default" ? " selected" : ""}>Default — prompt for each tool use</option>
            <option value="plan"${agent.permissionMode === "plan" ? " selected" : ""}>Plan — read-only planning mode</option>
          </select>
        </label>
        <p class="ops-muted-copy">This value is active at runtime. Callers can override it at launch time, but this serves as the agent's declared default.</p>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Output contract <span class="ops-badge-coming">stored — write-back coming</span></div>
        <label class="ops-field">
          <span>Output type</span>
          <select data-ops-field="outputType">
            <option value="run_summary"${(agent.outputContract?.type || "run_summary") === "run_summary" ? " selected" : ""}>Run summary</option>
            <option value="writing_draft"${agent.outputContract?.type === "writing_draft" ? " selected" : ""}>Writing draft</option>
            <option value="campaign_candidate"${agent.outputContract?.type === "campaign_candidate" ? " selected" : ""}>Campaign candidate</option>
            <option value="file"${agent.outputContract?.type === "file" ? " selected" : ""}>File</option>
          </select>
        </label>
        <p class="ops-muted-copy">Declares the intended output type. Stored and displayed; write-back to the target destination is not yet active.</p>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Supporting files</div>
        ${Array.isArray(agent.supportingFiles) && agent.supportingFiles.length > 0
          ? `<div class="ops-supporting-files-list">
              ${agent.supportingFiles.map((f) => `
                <div class="ops-supporting-file-row">
                  <span class="ops-supporting-file-name">${escapeHtml(f.filename || f.name || "")}</span>
                  <span class="ops-supporting-file-meta">${escapeHtml(formatFileSize(f.sizeBytes ?? f.size))} · ${escapeHtml(formatRelativeTime(f.modifiedAt))}</span>
                </div>
              `).join("")}
            </div>`
          : `<p class="ops-muted-copy">No supporting files. Upload files via the avatar/files APIs or place files in the agent's workspace directory.</p>`}
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Performance / run health</div>
        <div class="ops-form-grid">
          <div class="ops-stat-box">
            <span class="ops-stat-label">Runs</span>
            <strong>${escapeHtml(String(agent.metrics.runs))}</strong>
          </div>
          <div class="ops-stat-box">
            <span class="ops-stat-label">Success</span>
            <strong>${escapeHtml(formatPercent(agent.metrics.successRate))}</strong>
          </div>
          <div class="ops-stat-box">
            <span class="ops-stat-label">Avg duration</span>
            <strong>${escapeHtml(agent.metrics.avgDuration)}</strong>
          </div>
          <div class="ops-stat-box">
            <span class="ops-stat-label">Avg cost</span>
            <strong>${escapeHtml(agent.metrics.avgCost)}</strong>
          </div>
        </div>
        <div class="ops-compact-grid">
          <div class="ops-stat-box">
            <span class="ops-stat-label">Last run</span>
            <strong>${escapeHtml(agent.metrics.lastRun || "—")}</strong>
          </div>
          <div class="ops-stat-box">
            <span class="ops-stat-label">Used in</span>
            <strong>${escapeHtml((agent.usedIn || []).join(" · ") || "Operations roster")}</strong>
          </div>
        </div>
        ${metricRow ? `<p class="ops-muted-copy">Real run metrics from the monitoring table back this profile.</p>` : `<p class="ops-muted-copy">No live run metrics were found, so the surface is falling back to a stable design-time profile.</p>`}
      </section>

      <div class="ops-detail-actions">
        ${renderOpsButton("Save changes", "save-agent")}
        ${renderOpsSecondaryButton("Reset", "reset-agent")}
        ${selectedAgentId ? renderOpsSecondaryButton("Delete", "delete-agent") : ""}
      </div>
    </div>
  `;
}

function renderOperatingBlueprint(agent) {
  const inputs = agent.inputsRequired || [];
  const tiers = agent.urgencyTiers || {};
  const failures = agent.failureModes || [];
  const tables = agent.dbTablesTouched || [];
  return `
    <section class="ops-mini-card ops-blueprint-card">
      <details open>
        <summary class="ops-blueprint-summary">
          <span>Operating Blueprint</span>
          <span class="ops-pill">${escapeHtml(formatLifecycleStatus(agent.lifecycleStatus))}</span>
        </summary>
        <div class="ops-blueprint-grid">
          <div class="ops-blueprint-kv">
            <span>Cadence</span>
            <strong>${escapeHtml(formatCadenceSeconds(agent.cadenceSeconds))}</strong>
          </div>
          <div class="ops-blueprint-kv">
            <span>Lifecycle</span>
            <strong>${escapeHtml(formatLifecycleStatus(agent.lifecycleStatus))}</strong>
          </div>
        </div>
        <div class="ops-blueprint-block">
          <div class="ops-mini-card-label">Inputs required</div>
          ${inputs.length ? `<div class="ops-blueprint-list">${inputs.map((input) => `
            <div class="ops-blueprint-row">
              <code>${escapeHtml(input.key || "Input")}</code>
              <span>${escapeHtml(input.description || input.kind || "No description")}</span>
            </div>
          `).join("")}</div>` : `<p class="ops-muted-copy">Not specified</p>`}
        </div>
        <div class="ops-blueprint-block">
          <div class="ops-mini-card-label">Urgency tiers</div>
          ${Object.keys(tiers).length ? `<div class="ops-blueprint-list">${Object.entries(tiers).map(([tier, detail]) => `
            <div class="ops-blueprint-row">
              <code>${escapeHtml(tier)}</code>
              <span>${escapeHtml(String(detail))}</span>
            </div>
          `).join("")}</div>` : `<p class="ops-muted-copy">Not specified</p>`}
        </div>
        <div class="ops-blueprint-block">
          <div class="ops-mini-card-label">Failure modes</div>
          ${failures.length ? `<ul class="ops-blueprint-bullets">${failures.map((mode) => `
            <li><strong>${escapeHtml(mode.name || "Failure")}</strong>${mode.description ? ` — ${escapeHtml(mode.description)}` : ""}</li>
          `).join("")}</ul>` : `<p class="ops-muted-copy">Not specified</p>`}
        </div>
        <div class="ops-blueprint-block">
          <div class="ops-mini-card-label">DB tables touched</div>
          ${tables.length ? `<div class="ops-chip-row ops-blueprint-chips">${tables.map((table) => `<span class="ops-agent-meta-chip">${escapeHtml(table)}</span>`).join("")}</div>` : `<p class="ops-muted-copy">Not specified</p>`}
        </div>
        <div class="ops-blueprint-block">
          <div class="ops-mini-card-label">Implementation notes</div>
          ${agent.implementationNotes ? `<pre class="ops-blueprint-notes">${escapeHtml(agent.implementationNotes)}</pre>` : `<p class="ops-muted-copy">Not specified</p>`}
        </div>
      </details>
    </section>
  `;
}

// ── Agent Connectors UI ───────────────────────────────────────────────────────

async function loadAgentConnectors(agentDbId) {
  const section = document.getElementById('agent-connectors-section');
  if (!section || !agentDbId) return;
  const list = section.querySelector('#agent-connectors-list');
  if (!list) return;

  // Populate the connector select
  const selectEl = section.querySelector('[data-ops-field="attach-connector-select"]');
  try {
    const res = await fetch('/api/connectors?status=active');
    if (res.ok) {
      const connectors = await res.json();
      if (selectEl) {
        selectEl.innerHTML = '<option value="">— link a connector —</option>' +
          connectors.map((c) => `<option value="${c.id}" data-kind="${c.kind}">[${c.kind}] ${c.name}</option>`).join('');
      }
    }
  } catch { /* non-fatal */ }

  // Load linked connectors
  try {
    const res = await fetch(`/api/agents/${agentDbId}/connectors`);
    if (!res.ok) { list.innerHTML = '<div class="ops-muted-copy">Could not load linked connectors.</div>'; return; }
    const links = await res.json();
    if (!links.length) {
      list.innerHTML = '<div class="ops-muted-copy">No connectors linked. Link a connector below to give this agent runtime credentials.</div>';
      return;
    }
    list.innerHTML = links.map((link) => `
      <div class="ops-connector-link-row">
        <span class="ops-pill ops-pill-accent">${link.tool_namespace}</span>
        <span class="ops-muted-copy" style="flex:1">${link.connector_id.slice(0, 8)}…</span>
        <button type="button" class="ops-pill-detach" data-ops-action="detach-connector"
          data-connector-id="${link.connector_id}" data-agent-id="${agentDbId}" title="Unlink connector">×</button>
      </div>
    `).join('');
  } catch {
    list.innerHTML = '<div class="ops-muted-copy">Could not load linked connectors.</div>';
  }
}

async function handleAttachConnector(section) {
  const agentDbId = section.dataset.agentId;
  if (!agentDbId) return;
  const selectEl = section.querySelector('[data-ops-field="attach-connector-select"]');
  const nsInput = section.querySelector('[data-ops-field="attach-connector-namespace"]');
  const connectorId = selectEl?.value;
  const toolNamespace = nsInput?.value?.trim();
  if (!connectorId || !toolNamespace) {
    showToast('Missing field', 'Select a connector and enter a tool namespace.', { isError: true });
    return;
  }
  try {
    const res = await fetch(`/api/agents/${agentDbId}/connectors`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ connector_id: connectorId, tool_namespace: toolNamespace }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast('Link failed', err.error || res.statusText, { isError: true });
      return;
    }
    if (nsInput) nsInput.value = '';
    await loadAgentConnectors(agentDbId);
    showToast('Linked', `Connector linked as "${toolNamespace}".`);
  } catch (e) {
    showToast('Link failed', String(e), { isError: true });
  }
}

async function handleDetachConnector(btn) {
  const connectorId = btn.dataset.connectorId;
  const agentDbId = btn.dataset.agentId;
  if (!connectorId || !agentDbId) return;
  try {
    await fetch(`/api/agents/${agentDbId}/connectors/${connectorId}`, { method: 'DELETE' });
    await loadAgentConnectors(agentDbId);
    showToast('Unlinked', 'Connector unlinked from agent.');
  } catch (e) {
    showToast('Unlink failed', String(e), { isError: true });
  }
}

function formatFileSize(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderSkillsPage() {
  if (!_skillsLoaded) {
    return `
      ${renderOperationsHero("Skills", "Capability library", "Loading skill library…", [renderSummaryChip("Library", "Loading")])}
      <section class="ops-grid ops-skills-grid">
        <article class="ops-panel ops-list-panel ops-loading-panel">
          <div class="ops-loading-block ops-loading-block-wide"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
        </article>
        <article class="ops-panel ops-detail-panel ops-loading-panel"></article>
      </section>`;
  }
  if (_skillsError) {
    return renderOperationsError("Skills", _skillsError);
  }
  const approvedSkills = getApprovedSkills();
  const pendingSkills = getPendingSkills();
  const tab = selectedSkillTab === "proposed" ? "proposed" : "library";
  const search = selectedSkillSearch();
  const filteredApproved = filterSkills(approvedSkills, search, selectedSkillCategory);
  const visibleSkills = tab === "proposed" ? pendingSkills : filteredApproved;
  const selectedSkill = ensureSkillSelection(visibleSkills);
  const summary = getSkillOverviewStats(approvedSkills, pendingSkills);

  return `
    ${renderOperationsHero(
      "Skills",
      "Capability library",
      "Approved skills stay in the library. Proposed skills live in the review queue until they are promoted through Memory > Skills / Rules.",
      [
        renderSummaryChip("Approved", `${summary.approved}`),
        renderSummaryChip("Proposed", `${summary.pending}`),
        renderSummaryChip("Uses", `${summary.totalUses}`),
        renderSummaryChip("Avg success", formatPercent(summary.avgSuccess)),
      ],
      [renderOpsSecondaryButton("Open Memory / Rules", "open-shell-view", { "shell-view": MEMORY_VIEW })],
    )}
    ${renderSkillsGuideHTML()}
    <section class="ops-skills-tabs">
      <button class="ops-tab${tab === "library" ? " active" : ""}" type="button" data-ops-action="switch-skill-tab" data-skill-tab="library">Library <span>${escapeHtml(String(approvedSkills.length))}</span></button>
      <button class="ops-tab${tab === "proposed" ? " active" : ""}" type="button" data-ops-action="switch-skill-tab" data-skill-tab="proposed">Proposed by agents <span>${escapeHtml(String(pendingSkills.length))}</span></button>
      <div style="flex: 1"></div>
      ${renderOpsSecondaryButton("New skill", "new-skill")}
      ${renderOpsSecondaryButton("Back to Operations", "open-shell-view", { "shell-view": "operations" })}
    </section>
    <section class="ops-grid ops-skills-grid">
      <article class="ops-panel ops-list-panel">
        <div class="ops-panel-head">
          <div>
            <div class="ops-panel-eyebrow">${tab === "proposed" ? "Review queue" : "Library"}</div>
            <h3>${tab === "proposed" ? "Proposed skills" : "Approved skills"}</h3>
            <p>${tab === "proposed"
              ? "Review rationale, evidence, and scope before promoting the pattern."
              : "Table-first scanning with category filters and a stable detail surface."}</p>
          </div>
          <span class="ops-pill">${escapeHtml(tab === "proposed" ? `${pendingSkills.length} pending` : `${filteredApproved.length} visible`)}</span>
        </div>
        ${tab === "library" ? `
          <div class="ops-filter-bar">
            <label class="ops-search">
              <span>Search</span>
              <input type="search" value="${escapeAttr(search)}" placeholder="Search approved skills" data-ops-field="skill-search">
            </label>
            <div class="ops-category-row">
              ${buildSkillCategoryButtons(approvedSkills)}
            </div>
          </div>
        ` : `
          <div class="ops-filter-bar">
            <p class="ops-muted-copy">Proposals stay separate until you approve them. The Memory page remains the governance boundary.</p>
          </div>
        `}
        <div class="ops-import-bar">
          <input
            type="text"
            class="ops-import-url-input"
            placeholder="Paste SKILL.md URL to import…"
            data-ops-field="skill-import-url"
          >
          <button type="button" class="ops-button ops-button-secondary" data-ops-action="import-skill-url">Import URL</button>
          <span class="ops-import-hint">or drop a .md file</span>
        </div>
        <div class="ops-table">
          <div class="ops-table-head">
            <span>Skill</span>
            <span>Used by</span>
            <span>Uses</span>
            <span>Success</span>
            <span>Origin</span>
          </div>
          <div class="ops-table-body">
            ${visibleSkills.map((skill) => {
              const active = skill.id === selectedSkill?.id;
              const originLabel = skill.origin === "policy" ? "Policy" : skill.origin === "agent" ? "Agent" : skill.origin === "memory" ? "Memory" : "You";
              return `
                <button
                  type="button"
                  class="ops-row ops-skill-row${active ? " active" : ""}"
                  data-ops-action="select-skill"
                  data-skill-id="${escapeAttr(skill.id)}"
                >
                  <span class="ops-row-main">
                    <span class="ops-row-title">${escapeHtml(skill.name)}</span>
                    <span class="ops-row-sub">${escapeHtml(skill.desc)}</span>
                  </span>
                  <span class="ops-row-value">${escapeHtml(String(skill.agents || 0))}</span>
                  <span class="ops-row-value">${escapeHtml(String(skill.uses || 0))}</span>
                  <span class="ops-row-value">${escapeHtml(formatPercent(skill.success || 0))}</span>
                  <span class="ops-row-value">${escapeHtml(originLabel)}</span>
                </button>
              `;
            }).join("")}
          </div>
        </div>
      </article>
      <article class="ops-panel ops-detail-panel">
        ${renderSkillDetail(selectedSkill, tab, summary)}
      </article>
    </section>
  `;
}

function selectedSkillSearch() {
  return String(readStorage(OPS_SKILL_SEARCH_KEY, "") || "").trim();
}

function filterSkills(skills, search, category) {
  return skills.filter((skill) => {
    const categoryMatch = category === "all" || skill.cat === category;
    if (!search) return categoryMatch;
    const haystack = [skill.name, skill.desc, skill.owner, ...(skill.linkedAgents || [])].join(" ").toLowerCase();
    return categoryMatch && haystack.includes(search.toLowerCase());
  });
}

function renderSkillDetail(skill, tab, summary) {
  if (!skill) {
    return `
      <div class="ops-empty-state">
        <strong>No skill selected</strong>
        <span>Pick a library row or a proposal to inspect the detail surface.</span>
      </div>
    `;
  }

  const isProposal = tab === "proposed";
  const linkedAgents = skill.linkedAgents || [];

  const detailFooter = isProposal ? `
        <section class="ops-mini-card">
          <div class="ops-mini-card-label">Evidence</div>
          <p>${escapeHtml(skill.tests || "No evidence yet.")}</p>
        </section>
        <div class="ops-detail-actions">
          ${renderOpsButton("Approve skill", "approve-skill", { "skill-id": skill.id })}
          ${renderOpsSecondaryButton("Reject", "reject-skill", { "skill-id": skill.id })}
          <button type="button" class="ops-button ops-button-danger" data-ops-action="delete-skill" data-skill-id="${escapeAttr(String(skill.id))}">Delete</button>
        </div>
      ` : `
        <section class="ops-mini-card">
          <div class="ops-mini-card-label">Promotion path</div>
          <p>${escapeHtml(skill.memoryBoundary || "Approved in Memory > Skills / Rules")}</p>
        </section>
        <div class="ops-detail-actions">
          ${renderOpsButton("Edit skill", "focus-skill-editor", { "skill-id": skill.id })}
          ${renderOpsSecondaryButton("Archive", "archive-skill", { "skill-id": skill.id })}
          <button type="button" class="ops-button ops-button-danger" data-ops-action="delete-skill" data-skill-id="${escapeAttr(String(skill.id))}">Delete</button>
        </div>
      `;

  return `
    <div class="ops-panel-head ops-detail-head">
      <div>
        <div class="ops-panel-eyebrow">${isProposal ? "Proposal review" : "Skill detail"}</div>
        <h3>${escapeHtml(skill.name)}</h3>
        <p>${escapeHtml(skill.desc)}</p>
      </div>
      <div class="ops-detail-head-actions">
        <span class="ops-pill ${isProposal ? "ops-pill-warn" : "ops-pill-success"}">${escapeHtml(isProposal ? "Proposed" : "Approved")}</span>
        <span class="ops-pill">${escapeHtml(isProposal ? skill.risk : `${skill.agents} agents`)}</span>
      </div>
    </div>

    <div class="ops-detail-stack">
      <section class="ops-mini-card">
        <div class="ops-mini-card-label">${isProposal ? "Why the agent proposed it" : "What it does"}</div>
        <p>${escapeHtml(isProposal ? skill.rationale : skill.desc)}</p>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">${isProposal ? "Scope requested" : "Approved scope"}</div>
        <div class="ops-chip-row">
          ${(isProposal ? skill.scope : linkedAgents).map((item) => renderOpsPill(item, isProposal ? "warn" : "accent")).join("")}
        </div>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Where it is used</div>
        <div class="ops-chip-row">
          ${(isProposal ? [skill.proposedBy] : linkedAgents).map((item) => renderOpsPill(item, "neutral")).join("")}
        </div>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Memory boundary</div>
        <p>${escapeHtml(skill.memoryBoundary || "Memory > Skills / Rules")}</p>
        <p class="ops-muted-copy">This page is the approved library. Promotions still live in the Memory governance surface.</p>
        <div class="operations-hero-actions">
          ${renderOpsButton("Open Memory / Rules", "open-shell-view", { "shell-view": MEMORY_VIEW })}
        </div>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Operational stats</div>
        <div class="ops-form-grid">
          <div class="ops-stat-box">
            <span class="ops-stat-label">Uses</span>
            <strong>${escapeHtml(String(skill.uses || 0))}</strong>
          </div>
          <div class="ops-stat-box">
            <span class="ops-stat-label">Success</span>
            <strong>${escapeHtml(formatPercent(skill.success || 0))}</strong>
          </div>
          <div class="ops-stat-box">
            <span class="ops-stat-label">Owner</span>
            <strong>${escapeHtml(skill.owner || "—")}</strong>
          </div>
          <div class="ops-stat-box">
            <span class="ops-stat-label">Updated</span>
            <strong>${escapeHtml(skill.updated || "—")}</strong>
          </div>
        </div>
      </section>
      ${detailFooter}
    </div>
  `;
}

function renderWorkflowsPage() {
  const workflows = getWorkflows();
  const loading = Boolean(getState("workflowsLoading"));
  const loaded = Boolean(getState("workflowsLoaded"));
  const error = getState("workflowsError");

  if ((!loaded && !loading && !workflows.length) || loading) {
    return `
      ${renderOperationsHero(
        "Workflows",
        "Builder and inspector",
        "Loading the workflow inventory and the current recipe preview...",
        [
          renderSummaryChip("Workflows", "Loading"),
          renderSummaryChip("Steps", "Loading"),
          renderSummaryChip("Schedule", "Loading"),
          renderSummaryChip("Runtime", "Loading"),
        ],
      )}
      <section class="ops-grid ops-workflows-grid">
        <article class="ops-panel ops-list-panel ops-loading-panel">
          <div class="ops-loading-block ops-loading-block-wide"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
        </article>
        <article class="ops-panel ops-canvas-panel ops-loading-panel">
          <div class="ops-loading-block ops-loading-block-wide"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block ops-loading-block-wide"></div>
        </article>
        <article class="ops-panel ops-detail-panel ops-loading-panel">
          <div class="ops-loading-block ops-loading-block-wide"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
        </article>
      </section>
    `;
  }

  if (error) {
    return `
      ${renderOperationsHero(
        "Workflows",
        "Builder and inspector",
        "The workflow inventory could not load cleanly, so the page is showing a safe failure state.",
        [renderSummaryChip("Load", "Failed")],
      )}
      <section class="ops-grid ops-workflows-grid">
        <article class="ops-panel ops-list-panel">
          <div class="ops-empty-state">
            <strong>Could not load workflows</strong>
            <span>${escapeHtml(String(error))}</span>
          </div>
        </article>
        <article class="ops-panel ops-canvas-panel">
          <div class="ops-empty-state">
            <strong>Retry the load</strong>
            <span>The workflow registry failed before the builder could render.</span>
          </div>
        </article>
        <article class="ops-panel ops-detail-panel">
          <div class="ops-empty-state">
            <strong>Retry after confirming the config</strong>
            <span>The builder and inspector will appear again once the config is available.</span>
          </div>
        </article>
      </section>
    `;
  }

  const selectedWorkflow = ensureWorkflowSelection(workflows);
  // In new-workflow mode selectedWorkflow is null; use the unsaved draft as the profile
  const selectedProfile = selectedWorkflow
    ? buildWorkflowProfile(selectedWorkflow)
    : (workflowDraft && workflowDraft.id === "" ? workflowDraft : null);
  const workflowStats = getWorkflowOverviewStats(workflows);

  // Kick off latest run fetch when a workflow is selected (guard is inside loadLatestWorkflowRun)
  if (selectedWorkflow?.id) void loadLatestWorkflowRun(selectedWorkflow.id);

  return `
    ${renderOperationsHero(
      "Workflows",
      "Builder and inspector",
      "The recipe is the primary object. Schedule and run metadata attach to it, but they do not define it.",
      [
        renderSummaryChip("Saved workflows", `${workflowStats.total}`),
        renderSummaryChip("Active", `${workflowStats.active}`),
        renderSummaryChip("Paused", `${workflowStats.paused}`),
        renderSummaryChip("Total steps", `${workflowStats.steps}`),
      ],
      [renderOpsSecondaryButton("New workflow", "new-workflow")],
    )}
    <section class="ops-grid ops-workflows-grid">
      <article class="ops-panel ops-list-panel">
        <div class="ops-panel-head">
          <div>
            <div class="ops-panel-eyebrow">Workflow inventory</div>
            <h3>Saved workflows</h3>
            <p>Scan the list, then open the builder and inspector on the right.</p>
          </div>
          <span class="ops-pill">${escapeHtml(String(workflows.length))} saved</span>
        </div>
        <div class="ops-list">
          ${workflows.map((workflow) => {
            const profile = buildWorkflowProfile(workflow);
            const active = workflow.id === selectedWorkflow?.id;
            return `
              <button
                type="button"
                class="ops-row ops-workflow-row${active ? " active" : ""}"
                data-ops-action="select-workflow"
                data-workflow-id="${escapeAttr(workflow.id)}"
              >
                <span class="ops-row-main">
                  <span class="ops-row-title">${escapeHtml(workflow.title)}</span>
                  <span class="ops-row-sub">${escapeHtml(workflow.description || `${profile.steps.length} steps`)}</span>
                </span>
                <span class="ops-row-value">${escapeHtml(profile.schedule)}</span>
                <span class="ops-row-value">${escapeHtml(profile.lastRun)}</span>
                <span class="ops-row-value">${escapeHtml(String(profile.steps.length))}</span>
                <span class="ops-row-value"><span class="ops-pill ${profile.status === "active" ? "ops-pill-success" : profile.status === "paused" ? "ops-pill-warn" : ""}">${escapeHtml(profile.status)}</span></span>
              </button>
            `;
          }).join("")}
        </div>
      </article>
      <article class="ops-panel ops-canvas-panel">
        ${renderWorkflowCanvas(selectedProfile)}
      </article>
      <article class="ops-panel ops-detail-panel">
        ${renderWorkflowInspector(selectedProfile)}
      </article>
    </section>
  `;
}

const STEP_TYPE_LABELS = { prompt: "Prompt", agent: "Agent", approval: "Approval", output: "Output" };

function canvasStepSummary(step) {
  const type = step.type || "prompt";
  if (type === "agent") return step.agentId ? `Agent: ${step.agentId}` : "(no agent selected)";
  if (type === "approval") return step.behavior === "notify" ? "Notify (non-blocking)" : "Pause until approved";
  if (type === "output") return "Stores run summary";
  return (step.prompt || "").slice(0, 80) || "No prompt yet.";
}

function renderWorkflowCanvas(workflow) {
  if (!workflow) return `<div class="ops-empty-state"><strong>No workflow selected</strong></div>`;
  const latestRun = _latestWorkflowRun;
  const stepResultMap = {};
  try {
    if (latestRun?.step_results) {
      JSON.parse(latestRun.step_results).forEach((s) => { stepResultMap[s.stepIndex] = s; });
    }
  } catch { /* noop */ }

  const runStatusClass = { completed: "ops-pill-success", failed: "ops-pill-warn", awaiting_approval: "ops-pill-accent" }[latestRun?.status] || "";

  return `
    <div class="ops-panel-head ops-detail-head">
      <div>
        <div class="ops-panel-eyebrow">Workflow canvas</div>
        <h3>${escapeHtml(workflow.title || "Untitled workflow")}</h3>
        <p>${escapeHtml(workflow.description || "Step flow for the current workflow.")}</p>
      </div>
      <div class="ops-detail-head-actions">
        ${latestRun ? `<span class="ops-pill ${runStatusClass}">${escapeHtml(latestRun.status)}</span>` : ""}
        <span class="ops-pill">Manual</span>
      </div>
    </div>
    <div class="ops-canvas-shell">
      <div class="ops-canvas-flow">
        ${workflow.steps.map((step, index) => {
          const type = step.type || "prompt";
          const runStep = stepResultMap[index];
          const runDot = runStep ? ({ completed: "✓", failed: "✗", awaiting_approval: "⏳", running: "…" }[runStep.status] || "") : "";
          return `
            <div class="ops-canvas-step">
              <div class="ops-canvas-step-index">${escapeHtml(String(index + 1))}${runDot ? ` <span style="opacity:.8">${runDot}</span>` : ""}</div>
              <div class="ops-canvas-step-body">
                <div style="display:flex;gap:6px;align-items:center;margin-bottom:3px;">
                  <span class="ops-pill" style="font-size:10px;padding:1px 6px">${escapeHtml(STEP_TYPE_LABELS[type] || type)}</span>
                  <span class="ops-canvas-step-title">${escapeHtml(step.label || `Step ${index + 1}`)}</span>
                </div>
                <div class="ops-canvas-step-copy">${escapeHtml(canvasStepSummary(step))}</div>
              </div>
            </div>
            ${index < workflow.steps.length - 1 ? '<div class="ops-canvas-arrow">→</div>' : ""}
          `;
        }).join("")}
      </div>
    </div>
  `;
}

function renderWorkflowStepFields(step, index) {
  const si = escapeAttr(String(index));
  const type = step.type || "prompt";

  const typeSelect = `
    <label class="ops-field">
      <span>Type</span>
      <select data-ops-field="workflow-step-type" data-step-index="${si}">
        <option value="prompt"${type === "prompt" ? " selected" : ""}>Prompt</option>
        <option value="agent"${type === "agent" ? " selected" : ""}>Agent</option>
        <option value="approval"${type === "approval" ? " selected" : ""}>Approval gate</option>
        <option value="output"${type === "output" ? " selected" : ""}>Output</option>
      </select>
    </label>
  `;

  const labelField = `
    <label class="ops-field">
      <span>Label</span>
      <input type="text" value="${escapeAttr(step.label)}" data-ops-field="workflow-step-label" data-step-index="${si}">
    </label>
  `;

  if (type === "agent") {
    const agents = getAgents();
    const agentOptions = agents.map((a) =>
      `<option value="${escapeAttr(a.id)}"${step.agentId === a.id ? " selected" : ""}>${escapeHtml(a.title || a.id)}</option>`
    ).join("");
    return `
      ${typeSelect}
      ${labelField}
      <label class="ops-field">
        <span>Agent</span>
        <select data-ops-field="workflow-step-agentId" data-step-index="${si}">
          <option value="">— select agent —</option>
          ${agentOptions}
        </select>
      </label>
      <label class="ops-field">
        <span>Step instructions <span class="ops-muted-copy">(optional — overrides agent goal for this step)</span></span>
        <textarea rows="2" data-ops-field="workflow-step-instructions" data-step-index="${si}">${escapeHtml(step.instructions || "")}</textarea>
      </label>
      <p class="ops-muted-copy">Agent inherits its own provider / model / permission policy.</p>
    `;
  }

  if (type === "approval") {
    return `
      ${typeSelect}
      ${labelField}
      <label class="ops-field">
        <span>Approval title</span>
        <input type="text" value="${escapeAttr(step.title || "")}" data-ops-field="workflow-step-title" data-step-index="${si}" placeholder="What needs approval?">
      </label>
      <label class="ops-field">
        <span>Description</span>
        <textarea rows="2" data-ops-field="workflow-step-description" data-step-index="${si}" placeholder="Context for the reviewer…">${escapeHtml(step.description || "")}</textarea>
      </label>
      <label class="ops-field">
        <span>Behavior</span>
        <select data-ops-field="workflow-step-behavior" data-step-index="${si}">
          <option value="pause"${(step.behavior || "pause") === "pause" ? " selected" : ""}>Pause — wait for decision before continuing</option>
          <option value="notify"${step.behavior === "notify" ? " selected" : ""}>Notify — create record, execution continues</option>
        </select>
      </label>
    `;
  }

  if (type === "output") {
    const dest = step.destination || step.outputRef || "run_summary";
    return `
      ${typeSelect}
      ${labelField}
      <label class="ops-field">
        <span>Destination</span>
        <select data-ops-field="workflow-step-destination" data-step-index="${si}">
          <option value="run_summary"${dest === "run_summary" ? " selected" : ""}>Run summary (stored on this run)</option>
          <option value="writing_draft"${dest === "writing_draft" ? " selected" : ""}>Writing Studio draft</option>
          <option value="jira_ticket" disabled>Jira ticket — coming</option>
        </select>
      </label>
      ${dest === "writing_draft" ? `
        <label class="ops-field">
          <span>Asset type</span>
          <input type="text" value="${escapeAttr(step.draftAssetType || "")}" data-ops-field="workflow-step-draftAssetType" data-step-index="${si}" placeholder="email, social, one-pager…">
        </label>
        <label class="ops-field">
          <span>Campaign ID</span>
          <input type="text" value="${escapeAttr(step.draftCampaignId || "")}" data-ops-field="workflow-step-draftCampaignId" data-step-index="${si}" placeholder="optional">
        </label>
        <label class="ops-field" style="flex-direction:row;align-items:center;gap:8px;">
          <input type="checkbox" data-ops-field="workflow-step-generateNow" data-step-index="${si}"${step.generateNow ? " checked" : ""}>
          <span>Generate draft content from prior step output</span>
        </label>
        <p class="ops-muted-copy">Checked: invokes the writing engine using prior step output as the brief. Unchecked: creates a blank draft stub with the brief attached.</p>
      ` : `
        <p class="ops-muted-copy">Stores previous step output as the run's output summary.</p>
      `}
    `;
  }

  // Default: prompt type (backward-compat with existing {label, prompt} steps)
  return `
    ${typeSelect}
    ${labelField}
    <label class="ops-field">
      <span>Prompt</span>
      <textarea rows="3" data-ops-field="workflow-step-prompt" data-step-index="${si}">${escapeHtml(step.prompt || "")}</textarea>
    </label>
  `;
}

function renderWorkflowRunHistory() {
  if (_latestWorkflowRunLoading) {
    return `
      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Latest run</div>
        <div class="ops-muted-copy">Loading…</div>
      </section>
    `;
  }

  const run = _latestWorkflowRun;
  if (!run) {
    return `
      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Latest run</div>
        <div class="ops-muted-copy">No runs yet — click Run now to start one.</div>
      </section>
    `;
  }

  const statusClass = {
    completed: "ops-pill-success",
    failed: "ops-pill-warn",
    awaiting_approval: "ops-pill-accent",
    rejected: "ops-pill-warn",
    cancelled: "ops-pill-warn",
  }[run.status] || "";

  const steps = [];
  try {
    const parsed = run.step_results ? JSON.parse(run.step_results) : [];
    steps.push(...parsed);
  } catch { /* malformed — skip */ }

  const stepStatusIcon = { completed: "✓", failed: "✗", awaiting_approval: "⏳", running: "…", notified: "✓", pending: "–" };

  return `
    <section class="ops-mini-card">
      <div class="ops-mini-card-label" style="display:flex;align-items:center;justify-content:space-between;">
        <span>Latest run</span>
        <button type="button" class="ops-link-button" data-ops-action="refresh-workflow-run" style="font-size:11px;">Refresh</button>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
        <span class="ops-pill ${statusClass}">${escapeHtml(run.status)}</span>
        ${run.cost_usd ? `<span class="ops-muted-copy">$${Number(run.cost_usd).toFixed(4)}</span>` : ""}
      </div>
      ${steps.length ? `
        <div class="ops-step-list" style="gap:4px;">
          ${steps.map((s) => `
            <div style="display:flex;gap:6px;align-items:flex-start;font-size:12px;">
              <span style="min-width:14px;color:var(--c-accent)">${stepStatusIcon[s.status] || "–"}</span>
              <span style="opacity:.7">${escapeHtml(s.type || "prompt")}</span>
              <span>${escapeHtml(s.label || `Step ${s.stepIndex + 1}`)}</span>
              <span class="ops-muted-copy" style="margin-left:auto">${escapeHtml(s.status)}</span>
            </div>
          `).join("")}
        </div>
      ` : `<div class="ops-muted-copy">Step results pending…</div>`}
      ${run.writing_draft_id ? `
        <div style="margin-top:8px;">
          <button type="button" class="ops-link-button" data-ops-action="open-workflow-writing-draft" data-draft-id="${escapeAttr(String(run.writing_draft_id))}">Open draft in Writing Studio →</button>
        </div>
      ` : ""}
      <p class="ops-muted-copy" style="margin-top:6px;">Latest run only — durable run history coming.</p>
    </section>
  `;
}

function renderWorkflowInspector(workflow) {
  // workflow may be null (no saved workflows) or the unsaved draft object; both render the form
  if (!workflow && !(workflowDraft && workflowDraft.id === "")) {
    return `
      <div class="ops-empty-state">
        <strong>No workflow selected</strong>
        <span>Pick a workflow to inspect the recipe and schedule metadata.</span>
      </div>
    `;
  }

  if (workflowDraft && workflowDraft.id === "") {
    // New-workflow mode — draft already set by new-workflow action; nothing to rebuild
  } else if (!workflowDraft || workflowDraft.id !== workflow?.id) {
    workflowDraft = buildWorkflowDraft(workflow);
    writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
  }

  const draft = workflowDraft;
  const isSaved = Boolean(draft.id);

  return `
    <div class="ops-panel-head ops-detail-head">
      <div>
        <div class="ops-panel-eyebrow">Workflow inspector</div>
        <h3>${escapeHtml(draft.title || workflow?.title || "Untitled workflow")}</h3>
      </div>
      <div class="ops-detail-head-actions">
        <span class="ops-pill ${draft.meta?.status === "active" ? "ops-pill-success" : draft.meta?.status === "paused" ? "ops-pill-warn" : ""}">${escapeHtml(draft.meta?.status || "draft")}</span>
      </div>
    </div>
    <div class="ops-detail-stack">
      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Recipe details</div>
        <label class="ops-field">
          <span>Title</span>
          <input type="text" value="${escapeAttr(draft.title)}" data-ops-field="workflow-title">
        </label>
        <label class="ops-field">
          <span>Description</span>
          <textarea rows="2" data-ops-field="workflow-description">${escapeHtml(draft.description)}</textarea>
        </label>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Steps</div>
        <div class="ops-step-list">
          ${draft.steps.map((step, index) => `
            <div class="ops-step-row">
              <div class="ops-step-row-head">
                <span class="ops-step-index">${escapeHtml(String(index + 1))}</span>
                <button type="button" class="ops-link-button" data-ops-action="remove-workflow-step" data-step-index="${escapeAttr(String(index))}">Remove</button>
              </div>
              ${renderWorkflowStepFields(step, index)}
            </div>
          `).join("")}
          <button type="button" class="ops-button ops-button-secondary" data-ops-action="add-workflow-step">Add step</button>
        </div>
        <p class="ops-muted-copy" style="margin-top:6px;">Previous step output is passed to each subsequent step automatically.</p>
      </section>

      ${renderWorkflowRunHistory()}

      <div class="ops-detail-actions">
        ${renderOpsButton("Save workflow", "save-workflow")}
        ${isSaved ? renderOpsButton("Run now", "run-workflow", { "workflow-id": escapeAttr(draft.id) }) : ""}
        ${renderOpsSecondaryButton("Duplicate", "duplicate-workflow")}
        ${renderOpsSecondaryButton("Delete", "delete-workflow")}
      </div>
    </div>
  `;
}

function renderAutomationsPage() {
  if (!_automationsLoaded) {
    return `
      ${renderOperationsHero(
        "Automations",
        "Runtime registry",
        "Loading automations…",
        [renderSummaryChip("Status", "Loading")],
      )}
      <section class="ops-grid ops-automations-grid">
        <article class="ops-panel ops-list-panel ops-loading-panel">
          <div class="ops-loading-block ops-loading-block-wide"></div>
          <div class="ops-loading-block"></div>
          <div class="ops-loading-block"></div>
        </article>
        <article class="ops-panel ops-detail-panel ops-loading-panel">
          <div class="ops-loading-block ops-loading-block-wide"></div>
          <div class="ops-loading-block"></div>
        </article>
      </section>
    `;
  }

  const automations = _automations;
  const selectedAutomation = ensureAutomationSelection(automations);
  const stats = getAutomationOverviewStats(automations);
  const pendingCount = _pendingApprovals.length;

  return `
    ${renderOperationsHero(
      "Automations",
      "Runtime registry",
      "Persistent automations that target existing workflows or agents. Run manually or on a schedule.",
      [
        renderSummaryChip("Active", String(stats.active)),
        renderSummaryChip("Paused", String(stats.paused)),
        renderSummaryChip("Approvals", pendingCount ? `${pendingCount} pending` : "none"),
      ],
    )}
    ${pendingCount ? `
      <section class="ops-approval-queue-banner">
        <div class="ops-approval-queue-inner">
          <strong>${pendingCount} pending approval${pendingCount !== 1 ? "s" : ""}</strong>
          ${_pendingApprovals.slice(0, 3).map((ap) => `
            <div class="ops-approval-item">
              <span class="ops-approval-title">${escapeHtml(ap.title)}</span>
              <span class="ops-approval-actions">
                <button type="button" class="ops-button ops-button-small" data-ops-action="decide-approval" data-approval-id="${escapeAttr(ap.id)}" data-decision="approve">Approve</button>
                <button type="button" class="ops-button ops-button-secondary ops-button-small" data-ops-action="decide-approval" data-approval-id="${escapeAttr(ap.id)}" data-decision="reject">Reject</button>
              </span>
            </div>
          `).join("")}
        </div>
      </section>
    ` : ""}
    <section class="ops-grid ops-automations-grid">
      <article class="ops-panel ops-list-panel">
        <div class="ops-panel-head">
          <div>
            <div class="ops-panel-eyebrow">Scheduled &amp; triggered rules</div>
            <h3>Automations</h3>
            <p>Each automation targets a workflow or agent. Run manually or on a schedule.</p>
          </div>
          <button type="button" class="ops-button" data-ops-action="new-automation">+ New</button>
        </div>
        ${_automationsError ? `<div class="ops-empty-state"><strong>Could not load automations</strong><span>${escapeHtml(_automationsError)}</span></div>` : ""}
        <div class="ops-list">
          ${automations.length === 0 && !_automationsError ? `
            <div class="ops-empty-state ops-empty-state-inline">
              <span>No automations yet. Create one to get started.</span>
            </div>
          ` : automations.map((automation) => {
            const status = getAutomationStatus(automation);
            const isSelected = automation.id === selectedAutomation?.id;
            const latest = automation.latest_run;
            const hasApproval = latest?.status === "awaiting_approval";
            return `
              <div
                class="ops-row ops-automation-row${isSelected ? " active" : ""}"
                role="button"
                tabindex="0"
                data-ops-action="select-automation"
                data-automation-id="${escapeAttr(automation.id)}"
              >
                <span class="ops-row-main">
                  <span class="ops-row-title">${escapeHtml(automation.name)}${hasApproval ? ' <span class="ops-approval-badge">⏳ approval needed</span>' : ""}</span>
                  <span class="ops-row-sub">${escapeHtml(automation.description || "")} · <em>${escapeHtml(automation.target_type)}: ${escapeHtml(automation.target_id)}</em></span>
                </span>
                <span class="ops-row-value">${latest ? renderOpsPill(formatRunStatus(latest.status), runStatusVariant(latest.status)) : renderOpsPill(automation.trigger_type, "neutral")}</span>
                <label class="ops-auto-toggle" title="${status === "active" ? "Active — click to pause" : "Paused — click to resume"}">
                  <input
                    type="checkbox"
                    data-ops-action="toggle-automation"
                    data-automation-id="${escapeAttr(automation.id)}"
                    ${status === "active" ? "checked" : ""}
                  >
                  <span class="ops-auto-toggle-track"></span>
                </label>
              </div>
            `;
          }).join("")}
        </div>
      </article>
      <article class="ops-panel ops-detail-panel">
        ${renderAutomationDetail(selectedAutomation)}
      </article>
    </section>
  `;
}

function renderAutomationCreateForm() {
  const draft = _automationDraft || {};
  const workflows = getWorkflows();
  const agents = getAgents();
  return `
    <div class="ops-panel-head ops-detail-head">
      <div>
        <div class="ops-panel-eyebrow">New automation</div>
        <h3>Create automation</h3>
      </div>
      <button type="button" class="ops-button ops-button-secondary" data-ops-action="cancel-new-automation">Cancel</button>
    </div>
    <div class="ops-detail-stack">
      <section class="ops-mini-card">
        <label class="ops-field">
          <span>Name</span>
          <input type="text" placeholder="e.g. Morning Brief" value="${escapeAttr(draft.name || "")}" data-ops-field="new-automation-name">
        </label>
        <label class="ops-field">
          <span>Description <small>(optional)</small></span>
          <input type="text" placeholder="What does this automation do?" value="${escapeAttr(draft.description || "")}" data-ops-field="new-automation-description">
        </label>
      </section>
      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Target</div>
        <label class="ops-field">
          <span>Type</span>
          <select data-ops-field="new-automation-target-type">
            <option value="workflow"${(draft.targetType || "workflow") === "workflow" ? " selected" : ""}>Workflow</option>
            <option value="agent"${draft.targetType === "agent" ? " selected" : ""}>Agent</option>
          </select>
        </label>
        <label class="ops-field">
          <span>Target</span>
          <select data-ops-field="new-automation-target-id">
            ${(draft.targetType === "agent" ? agents : workflows).map((t) =>
              `<option value="${escapeAttr(t.id)}"${draft.targetId === t.id ? " selected" : ""}>${escapeHtml(t.title || t.name || t.id)}</option>`
            ).join("")}
          </select>
        </label>
      </section>
      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Trigger &amp; approval</div>
        <label class="ops-field">
          <span>Trigger</span>
          <select data-ops-field="new-automation-trigger-type">
            <option value="manual"${(draft.triggerType || "manual") === "manual" ? " selected" : ""}>Manual only</option>
            <option value="schedule"${draft.triggerType === "schedule" ? " selected" : ""}>Scheduled (coming soon)</option>
          </select>
        </label>
        <label class="ops-field">
          <span>Approval</span>
          <select data-ops-field="new-automation-approval-policy">
            <option value="none"${(draft.approvalPolicy || "none") === "none" ? " selected" : ""}>None — run immediately</option>
            <option value="require_before_run"${draft.approvalPolicy === "require_before_run" ? " selected" : ""}>Require approval before running</option>
          </select>
        </label>
      </section>
      <div class="ops-detail-actions">
        <button type="button" class="ops-button" data-ops-action="save-new-automation">Create automation</button>
        <button type="button" class="ops-button ops-button-secondary" data-ops-action="cancel-new-automation">Cancel</button>
      </div>
    </div>
  `;
}

function renderAutomationDetail(automation) {
  if (_automationDraft !== null && !automation) {
    return renderAutomationCreateForm();
  }
  if (_automationDraft !== null && _automationDraft._creating) {
    return renderAutomationCreateForm();
  }

  if (!automation) {
    return `
      <div class="ops-empty-state">
        <strong>No automation selected</strong>
        <span>Select an automation from the list, or create one with + New.</span>
      </div>
    `;
  }

  const status = getAutomationStatus(automation);
  const latest = automation.latest_run;
  const hasApproval = latest?.status === "awaiting_approval";

  return `
    <div class="ops-panel-head ops-detail-head">
      <div>
        <div class="ops-panel-eyebrow">Automation inspector</div>
        <h3>${escapeHtml(automation.name)}</h3>
        <p>${escapeHtml(automation.description || "")}</p>
      </div>
      <div class="ops-detail-head-actions">
        ${renderOpsPill(automation.trigger_type, "neutral")}
        ${renderOpsPill(status, status === "active" ? "ok" : "warn")}
      </div>
    </div>
    <div class="ops-detail-stack">
      ${hasApproval ? `
        <section class="ops-mini-card ops-approval-card">
          <div class="ops-mini-card-label">Approval required</div>
          <p>This run is waiting for approval before it can execute.</p>
          ${_pendingApprovals.filter((ap) => ap.target_id === latest.id).map((ap) => `
            <div class="ops-approval-item">
              <span class="ops-approval-title">${escapeHtml(ap.title)}</span>
              <div class="ops-approval-actions">
                <button type="button" class="ops-button" data-ops-action="decide-approval" data-approval-id="${escapeAttr(ap.id)}" data-decision="approve">Approve</button>
                <button type="button" class="ops-button ops-button-secondary" data-ops-action="decide-approval" data-approval-id="${escapeAttr(ap.id)}" data-decision="reject">Reject</button>
              </div>
            </div>
          `).join("")}
        </section>
      ` : ""}

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Target</div>
        <div class="ops-chip-row" style="margin-bottom:8px">
          ${renderOpsPill(automation.target_type, "neutral")}
          ${renderOpsPill(automation.target_id, "accent")}
        </div>
        <div class="operations-hero-actions ops-left-actions">
          ${automation.target_type === "workflow"
            ? renderOpsSecondaryButton("Open in Workflows", "open-shell-view", { "shell-view": "workflows" })
            : renderOpsSecondaryButton("Open in Agents", "open-shell-view", { "shell-view": "agents" })}
        </div>
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Latest run</div>
        ${latest ? `
          <div class="ops-form-grid">
            <div class="ops-stat-box">
              <span class="ops-stat-label">Status</span>
              <strong>${escapeHtml(formatRunStatus(latest.status))}</strong>
            </div>
            <div class="ops-stat-box">
              <span class="ops-stat-label">Cost</span>
              <strong>${latest.cost != null ? `$${Number(latest.cost).toFixed(4)}` : "—"}</strong>
            </div>
          </div>
          ${latest.output_summary ? `<p class="ops-muted-copy ops-run-summary">${escapeHtml(latest.output_summary.slice(0, 300))}${latest.output_summary.length > 300 ? "…" : ""}</p>` : ""}
          ${latest.error ? `<p class="ops-error-copy">${escapeHtml(latest.error)}</p>` : ""}
        ` : `<p class="ops-muted-copy">Never run.</p>`}
      </section>

      <section class="ops-mini-card">
        <div class="ops-mini-card-label">Policy</div>
        <div class="ops-chip-row">
          ${renderOpsPill(`Approval: ${automation.approval_policy}`, "neutral")}
          ${automation.provider ? renderOpsPill(automation.provider, "neutral") : ""}
        </div>
        <div class="ops-detail-toggle-row" style="margin-top:10px">
          <label class="ops-auto-toggle">
            <input
              type="checkbox"
              data-ops-action="toggle-automation"
              data-automation-id="${escapeAttr(automation.id)}"
              ${status === "active" ? "checked" : ""}
            >
            <span class="ops-auto-toggle-track"></span>
          </label>
          <span class="ops-detail-toggle-label">${status === "active" ? "Active" : "Paused"}</span>
        </div>
      </section>

      <div class="ops-detail-actions">
        <button type="button" class="ops-button" data-ops-action="run-automation" data-automation-id="${escapeAttr(automation.id)}">Run now</button>
        <button type="button" class="ops-button ops-button-secondary" data-ops-action="delete-automation" data-automation-id="${escapeAttr(automation.id)}">Archive</button>
      </div>
    </div>
  `;
}

function renderLoadingCardGrid(title) {
  return `
    <section class="shell-hero operations-hero">
      <div class="shell-eyebrow">${escapeHtml(title)}</div>
      <h2>${escapeHtml(title)}</h2>
      <p>Loading the dedicated surface...</p>
      <div class="operations-summary">
        ${renderSummaryChip("Loading", title)}
      </div>
    </section>
    <section class="ops-grid ops-loading-grid" aria-busy="true">
      <article class="ops-panel ops-loading-panel">
        <div class="ops-loading-block ops-loading-block-wide"></div>
        <div class="ops-loading-block"></div>
        <div class="ops-loading-block"></div>
      </article>
      <article class="ops-panel ops-loading-panel">
        <div class="ops-loading-block ops-loading-block-wide"></div>
        <div class="ops-loading-block"></div>
        <div class="ops-loading-block"></div>
      </article>
    </section>
  `;
}

function renderOperationsError(title, message) {
  return `
    <section class="shell-hero operations-hero">
      <div class="shell-eyebrow">${escapeHtml(title)}</div>
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(message)}</p>
    </section>
    <section class="ops-grid ops-loading-grid">
      <article class="ops-panel">
        <div class="ops-empty-state">
          <strong>${escapeHtml(title)} could not load</strong>
          <span>${escapeHtml(message)}</span>
        </div>
      </article>
    </section>
  `;
}

function renderPipelinesPage() {
  // Pipelines are rendered by the standalone pipelines.js module.
  // We return a mount point and delegate rendering to initPipelinesPage.
  return `<div id="pipelines-page-root"></div>`;
}

function renderPipelineRunHistoryPage() {
  return `<div id="pipeline-run-history-root"></div>`;
}

function buildOperationsMarkup(view) {
  const normalized = normalizeAppView(view);
  switch (normalized) {
    case OPERATIONS_VIEW:
      return renderOverviewPage();
    case "agents":
      return renderAgentsPage();
    case "agents/builder":
      return renderAgentBuilderPage();
    case "skills":
      return renderSkillsPage();
    case "workflows":
      return renderWorkflowsPage();
    case "automations":
      return renderAutomationsPage();
    case "pipelines":
      return renderPipelinesPage();
    case PIPELINE_RUN_HISTORY_VIEW:
      return renderPipelineRunHistoryPage();
    default:
      return "";
  }
}

export function buildOperationsViewMarkup(view = getState("view")) {
  const normalized = normalizeAppView(view);
  if (!isOperationsSurfaceView(normalized)) return "";
  return buildOperationsMarkup(normalized);
}

export function renderOperationsView(view = getState("view")) {
  const markup = buildOperationsViewMarkup(view);
  const shell = getShellContent();
  if (shell) {
    shell.innerHTML = markup;
  }
  // For the Pipelines sub-view, delegate to the pipelines module after DOM is set.
  if (normalizeAppView(view) === "pipelines") {
    initPipelinesPage();
  } else if (normalizeAppView(view) === PIPELINE_RUN_HISTORY_VIEW) {
    initPipelineRunHistoryPage();
  }
  // Load agent connectors asynchronously after the agent detail DOM is ready.
  const connSection = document.getElementById("agent-connectors-section");
  if (connSection && connSection.dataset.agentId) {
    void loadAgentConnectors(connSection.dataset.agentId);
  }
  return markup;
}

async function refreshCampaignOpsFromApi() {
  _campaignOpsError = null;
  try {
    _campaignOpsOverview = await api.fetchCampaignOpsOverview();
    _campaignOpsLoaded = true;
  } catch (err) {
    _campaignOpsError = err?.message || "Failed to load Campaign Ops";
    _campaignOpsLoaded = true;
  }
}

async function refreshAgentsFromApi() {
  const [agents, chains, dags, metrics] = await Promise.all([
    api.fetchAgents(),
    api.fetchChains(),
    api.fetchDags(),
    api.fetchAgentMetrics().catch(() => null),
  ]);
  await refreshReasonCodesFromApi().catch(() => {});
  setState("agents", agents);
  setState("agentChains", chains);
  setState("agentDags", dags);
  setState("agentMetrics", metrics);
  setState("agentsLoaded", true);
}

async function loadEnrichedAgent(agentId) {
  if (!agentId) { _enrichedAgent = null; _agentInstructionContent = null; _agentSupportingFiles = []; _enrichedAgentLoadedForId = null; return; }
  _enrichedAgentLoadedForId = agentId;  // set before async work to prevent re-trigger on null result
  _agentInstructionLoading = true;
  try {
    const [enriched, instrData, filesData] = await Promise.all([
      api.getAgentEnrichedApi(agentId).catch(() => null),
      api.getAgentInstructionApi(agentId).catch(() => ({ content: null })),
      api.listAgentFilesApi(agentId).catch(() => ({ files: [] })),
    ]);
    _enrichedAgent = enriched;
    _agentInstructionContent = instrData?.content ?? null;
    _agentSupportingFiles = Array.isArray(filesData?.files) ? filesData.files : [];
  } finally {
    _agentInstructionLoading = false;
  }
}

async function refreshWorkflowsFromApi() {
  const workflows = await api.fetchWorkflows();
  setState("workflows", workflows);
  setState("workflowsLoaded", true);
}

async function loadLatestWorkflowRun(workflowId) {
  if (!workflowId || _latestWorkflowRunForId === workflowId) return;
  _latestWorkflowRunForId = workflowId;
  _latestWorkflowRunLoading = true;
  _latestWorkflowRun = null;
  try {
    _latestWorkflowRun = await api.getLatestWorkflowRunApi(workflowId);
  } catch {
    _latestWorkflowRun = null;
  } finally {
    _latestWorkflowRunLoading = false;
    scheduleRender();
  }
}

async function refreshLatestWorkflowRun(workflowId) {
  if (!workflowId) return;
  _latestWorkflowRunForId = null; // clear cache key so next call fetches
  await loadLatestWorkflowRun(workflowId);
}

async function refreshSkillsFromApi() {
  _skillsError = null;
  try {
    const [all, proposed, cats] = await Promise.all([
      api.fetchSkills({ status: "approved" }),
      api.fetchSkills({ status: "proposed" }),
      api.fetchSkillCategories(),
    ]);
    _approvedSkills = Array.isArray(all) ? all : [];
    _proposedSkills = Array.isArray(proposed) ? proposed : [];
    _skillCategories = Array.isArray(cats) ? cats : [];
    _skillsLoaded = true;
  } catch (err) {
    _skillsError = err?.message || "Failed to load skills";
    _skillsLoaded = true;
  }
}

async function saveAgentDraft() {
  if (!agentDraft) return;
  const payload = {
    id: agentDraft.id || slugify(agentDraft.title || "agent"),
    title: agentDraft.title.trim(),
    description: agentDraft.description.trim(),
    goal: agentDraft.goal.trim(),
    icon: agentDraft.icon.trim() || "tool",
    constraints: {
      maxTurns: Number(agentDraft.constraints.maxTurns || 50),
      timeoutMs: Number(agentDraft.constraints.timeoutMs || 300000),
    },
    provider: agentDraft.provider || undefined,
    model: agentDraft.model || undefined,
    fallbackProvider: agentDraft.fallbackProvider || null,
    fallbackModel: agentDraft.fallbackModel || null,
    memoryPolicy: agentDraft.memoryPolicy || null,
    permissionMode: agentDraft.permissionMode || null,
    outputContract: agentDraft.outputContract || null,
    reasonCodesEmitted: agentDraft.reasonCodesEmitted || [],
  };
  if (!payload.title || !payload.goal) {
    window.alert?.("Title and goal are required.");
    return;
  }
  if (!payload.provider || !payload.fallbackProvider || !payload.fallbackModel) {
    window.alert?.("Both preferred and fallback provider are required — these protect your agent from upstream outages.");
    return;
  }
  if (agentDraft.id) {
    await api.updateAgent(agentDraft.id, payload);
    selectedAgentId = agentDraft.id;
  } else {
    const created = await api.createAgent(payload);
    selectedAgentId = created.id;
    agentDraft.id = created.id;
  }
  // Save instruction content if edited.
  const instrEl = document.querySelector("[data-ops-field='agent-instruction']");
  if (instrEl && selectedAgentId) {
    const content = instrEl.value.trim();
    if (content) {
      await api.saveAgentInstructionApi(selectedAgentId, content).catch(() => {});
    } else {
      await api.deleteAgentInstructionApi(selectedAgentId).catch(() => {});
    }
  }
  writeStorage(OPS_AGENT_SELECTION_KEY, selectedAgentId);
  _enrichedAgent = null;
  _agentInstructionContent = null;
  await refreshAgentsFromApi();
  await loadEnrichedAgent(selectedAgentId);
  renderOperationsView("agents");
  showToast("Saved", `${payload.title} updated.`);
}

async function savePersonaDraft() {
  if (!selectedAgentId) return;
  const nameEl = document.querySelector("[data-ops-field='persona-name']");
  const purposeEl = document.querySelector("[data-ops-field='persona-purpose']");
  const voiceEl = document.querySelector("[data-ops-field='persona-voice-notes']");
  const ghostwriteEl = document.querySelector("[data-ops-field='persona-ghostwrite']");
  const patch = {};
  if (nameEl && nameEl.value.trim()) patch.name = nameEl.value.trim();
  if (purposeEl && purposeEl.value.trim()) patch.purpose = purposeEl.value.trim();
  if (voiceEl && voiceEl.value.trim()) patch.voiceNotes = voiceEl.value.trim();
  if (ghostwriteEl) patch.ghostwrite = ghostwriteEl.value === "true";
  if (Object.keys(patch).length === 0) return;
  try {
    await api.patchAgentPersonaApi(selectedAgentId, patch);
    _enrichedAgent = null;
    await loadEnrichedAgent(selectedAgentId);
    renderOperationsView("agents");
    const agent = getAgents().find((item) => item.id === selectedAgentId);
    showToast("Saved", `${agent?.title || selectedAgentId} updated.`);
  } catch (err) {
    showToast("Save failed", err?.message || String(err), { isError: true });
  }
}

async function saveAgentReasonCodes(fieldEl) {
  if (!selectedAgentId) return;
  const container = fieldEl.closest("[data-ops-reason-code-multiselect]");
  if (!container) return;
  const reasonCodesEmitted = Array.from(container.querySelectorAll("input[type='checkbox']:checked"))
    .map((option) => option.value);
  if (agentDraft?.id === selectedAgentId) agentDraft.reasonCodesEmitted = reasonCodesEmitted;
  try {
    await api.updateAgent(selectedAgentId, { reasonCodesEmitted });
    _enrichedAgent = null;
    await refreshAgentsFromApi();
    await loadEnrichedAgent(selectedAgentId);
    renderOperationsView("agents");
    showToast("Saved", "Reason-code emit list updated.");
  } catch (err) {
    showToast("Save failed", err?.message || String(err), { isError: true });
  }
}

async function saveWorkflowDraft() {
  if (!workflowDraft) return;
  const payload = {
    title: workflowDraft.title.trim(),
    description: workflowDraft.description.trim(),
    steps: workflowDraft.steps
      .map((step, index) => {
        const type = step.type || "prompt";
        const base = {
          type,
          label: (step.label || "").trim() || `Step ${index + 1}`,
        };
        if (type === "prompt") {
          base.prompt = (step.prompt || "").trim();
        } else if (type === "agent") {
          base.agentId = (step.agentId || "").trim();
          if (step.instructions?.trim()) base.instructions = step.instructions.trim();
        } else if (type === "approval") {
          base.title = (step.title || "").trim();
          base.description = (step.description || "").trim();
          base.behavior = step.behavior === "notify" ? "notify" : "pause";
        } else if (type === "output") {
          base.outputRef = "run_summary";
        }
        return base;
      })
      .filter((step) => step.label),
  };
  if (!payload.title) {
    window.alert?.("Title is required.");
    return;
  }
  if (!payload.steps.length) {
    payload.steps = [{ type: "prompt", label: "Step 1", prompt: "" }];
  }
  if (workflowDraft.id) {
    await api.updateWorkflow(workflowDraft.id, payload);
    selectedWorkflowId = workflowDraft.id;
  } else {
    const created = await api.createWorkflow(payload);
    selectedWorkflowId = created.id;
    workflowDraft.id = created.id;
  }
  writeStorage(OPS_WORKFLOW_SELECTION_KEY, selectedWorkflowId);
  workflowDraft.meta = workflowDraft.meta || {};
  workflowDraft.meta.status = workflowDraft.meta.status || "active";
  workflowDraft.meta.schedule = workflowDraft.meta.schedule || "Manual";
  writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
  await refreshWorkflowsFromApi();
  renderOperationsView("workflows");
}

function promoteSkill(skillId) {
  if (!skillId) return;
  void (async () => {
    await api.approveSkillApi(skillId);
    selectedSkillTab = "library";
    selectedSkillId = String(skillId);
    writeStorage(OPS_SKILL_TAB_KEY, selectedSkillTab);
    writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
    await refreshSkillsFromApi();
    renderOperationsView("skills");
  })();
}

function rejectSkill(skillId) {
  if (!skillId) return;
  void (async () => {
    await api.archiveSkillApi(skillId);
    selectedSkillId = "";
    writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
    await refreshSkillsFromApi();
    renderOperationsView("skills");
  })();
}

async function handleSkillFileDrop(file) {
  if (!file) return;
  if (file.name.endsWith(".zip")) {
    try {
      const created = await api.importSkillFromZip(file);
      selectedSkillTab = "proposed";
      selectedSkillId = String(created.id || "");
      writeStorage(OPS_SKILL_TAB_KEY, selectedSkillTab);
      writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
      await refreshSkillsFromApi();
      renderOperationsView("skills");
      const helperNote = created.helpersCount ? ` (+${created.helpersCount} helper files)` : "";
      showOpsImportToast(`"${created.name}" added to Proposed${helperNote}`);
    } catch (err) {
      showOpsImportToast(err.message || "ZIP import failed", true);
    }
    return;
  }
  if (!file.name.endsWith(".md")) {
    showOpsImportToast("Drop a SKILL.md or .md file to import a skill", true);
    return;
  }
  let text;
  try {
    text = await file.text();
  } catch {
    showOpsImportToast("Could not read file", true);
    return;
  }
  const { meta, body } = parseSkillFrontMatter(text);
  const name = (meta.name || file.name.replace(/\.md$/i, "")).trim();
  if (!name) {
    showOpsImportToast("Could not derive skill name from file", true);
    return;
  }
  try {
    const created = await api.createSkillApi({
      name,
      description: meta.description || "",
      category: meta.category || "",
      scope: meta.scope || "global",
      provider_compat: Array.isArray(meta.provider_compat) ? meta.provider_compat : ["all"],
      when_to_use: meta.when_to_use || "",
      body,
      status: "proposed",
      origin: "user",
    });
    selectedSkillTab = "proposed";
    selectedSkillId = String(created.id || "");
    writeStorage(OPS_SKILL_TAB_KEY, selectedSkillTab);
    writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
    await refreshSkillsFromApi();
    renderOperationsView("skills");
    showOpsImportToast(`"${created.name || name}" added to Proposed`);
  } catch (err) {
    showOpsImportToast(err?.message || "Import failed", true);
  }
}

function handleOperationsDragstart(event) {
  const row = event.target.closest("[data-drag-kind='agent']");
  if (row && agentViewMode === "custom") {
    event.dataTransfer?.setData("application/x-artemis-agent", row.dataset.agentId || "");
    return;
  }
  const folder = event.target.closest(".ops-agent-custom-folder[data-folder-path]");
  if (folder && agentViewMode === "custom") {
    event.dataTransfer?.setData("application/x-artemis-folder", folder.dataset.folderPath || "");
  }
}

function handleOperationsContextMenu(event) {
  const folder = event.target.closest(".ops-agent-custom-folder[data-folder-path]");
  if (!folder || agentViewMode !== "custom") return;
  event.preventDefault();
  showFolderContextMenu(folder.dataset.folderPath || "", event.clientX, event.clientY);
}

function handleOperationsDragover(event) {
  const agentFolder = event.target.closest(".ops-agent-custom-folder");
  if (agentFolder && agentViewMode === "custom") {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    agentFolder.classList.add("ops-agent-folder-drop");
    return;
  }
  if (!getShellContent()?.querySelector(".ops-skills-grid")) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  getShellContent()?.querySelector(".ops-list-panel")?.classList.add("ops-drop-active");
}

function handleOperationsDragleave(event) {
  event.target.closest(".ops-agent-custom-folder")?.classList.remove("ops-agent-folder-drop");
  getShellContent()?.querySelector(".ops-list-panel")?.classList.remove("ops-drop-active");
}

function handleOperationsDrop(event) {
  const folder = event.target.closest(".ops-agent-custom-folder");
  if (folder && agentViewMode === "custom") {
    event.preventDefault();
    folder.classList.remove("ops-agent-folder-drop");
    const target = folder.dataset.folderPath || "";
    const agentId = event.dataTransfer?.getData("application/x-artemis-agent") || "";
    const sourceFolder = event.dataTransfer?.getData("application/x-artemis-folder") || "";
    if (agentId) void moveAgentToFolder(agentId, target).catch((err) => showOpsImportToast(err.message || "Move failed", true));
    else if (sourceFolder) void moveFolderToFolder(sourceFolder, target).catch((err) => showOpsImportToast(err.message || "Move failed", true));
    return;
  }
  if (!getShellContent()?.querySelector(".ops-skills-grid")) return;
  event.preventDefault();
  getShellContent()?.querySelector(".ops-list-panel")?.classList.remove("ops-drop-active");
  const file = event.dataTransfer?.files?.[0];
  if (file) void handleSkillFileDrop(file);
}

async function toggleAutomation(automationId) {
  const automation = _automations.find((a) => a.id === automationId);
  if (!automation) return;
  const newStatus = automation.status === "active" ? "paused" : "active";
  try {
    await api.updateAutomationApi(automationId, { status: newStatus });
    await refreshAutomationsFromApi();
    selectedAutomationId = automationId;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
    renderOperationsView("automations");
  } catch (err) {
    showOpsImportToast(err.message || "Could not update automation", true);
  }
}

async function saveNewAutomation() {
  const draft = _automationDraft;
  if (!draft) return;
  const name = (draft.name || "").trim();
  if (!name) { showOpsImportToast("Name is required.", true); return; }
  const targetType = draft.targetType || "workflow";
  const targetId = (draft.targetId || "").trim();
  if (!targetId) { showOpsImportToast("Target is required.", true); return; }
  try {
    const automation = await api.createAutomationApi({
      name,
      description: draft.description || "",
      targetType,
      targetId,
      triggerType: draft.triggerType || "manual",
      approvalPolicy: draft.approvalPolicy || "none",
    });
    _automationDraft = null;
    await refreshAutomationsFromApi();
    selectedAutomationId = automation.id;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
    showOpsImportToast(`Automation "${name}" created.`);
    renderOperationsView("automations");
  } catch (err) {
    showOpsImportToast(err.message || "Could not create automation.", true);
  }
}

async function triggerAutomationRun(automationId) {
  if (!automationId) return;
  try {
    const result = await api.runAutomationApi(automationId);
    const msg = result.status === "awaiting_approval"
      ? "Run created — waiting for approval."
      : "Run started.";
    showOpsImportToast(msg);
    await refreshAutomationsFromApi();
    selectedAutomationId = automationId;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
    renderOperationsView("automations");
  } catch (err) {
    showOpsImportToast(err.message || "Could not start run.", true);
  }
}

async function archiveAutomation(automationId) {
  if (!automationId) return;
  try {
    await api.deleteAutomationApi(automationId);
    await refreshAutomationsFromApi();
    selectedAutomationId = "";
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, "");
    renderOperationsView("automations");
  } catch (err) {
    showOpsImportToast(err.message || "Could not archive automation.", true);
  }
}

async function decideAutomationApproval(approvalId, decision) {
  if (!approvalId) return;
  try {
    await api.decideApprovalApi(approvalId, { decision, reviewer: "user" });
    const msg = decision === "approve" ? "Run approved — starting now." : "Run rejected.";
    showOpsImportToast(msg);
    await refreshAutomationsFromApi();
    renderOperationsView("automations");
  } catch (err) {
    showOpsImportToast(err.message || "Could not record decision.", true);
  }
}

async function applyCampaignDecision(campaignId, action) {
  if (!campaignId) return;
  try {
    await api.decideCampaignCandidateApi(campaignId, {
      action,
      actor: "Campaign Ops",
      notes: campaignDecisionNotes[campaignId] || undefined,
    });
    delete campaignDecisionNotes[campaignId];
    persistCampaignDecisionNotes();
    selectedAutomationId = campaignId;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
    await refreshCampaignOpsFromApi();
    renderOperationsView("automations");
  } catch (err) {
    showOpsImportToast(err.message || "Campaign decision failed", true);
  }
}

async function promoteCampaignCandidate(campaignId) {
  if (!campaignId) return;
  try {
    await api.promoteCampaignCandidateApi(campaignId, {
      actor: "Campaign Ops",
      notes: campaignDecisionNotes[campaignId] || undefined,
    });
    delete campaignDecisionNotes[campaignId];
    persistCampaignDecisionNotes();
    selectedAutomationId = campaignId;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
    await refreshCampaignOpsFromApi();
    renderOperationsView("automations");
  } catch (err) {
    showOpsImportToast(err.message || "Promote failed", true);
  }
}

async function reopenCampaignCandidate(campaignId) {
  if (!campaignId) return;
  try {
    await api.reopenCampaignCandidateApi(campaignId, {
      actor: "Campaign Ops",
      notes: campaignDecisionNotes[campaignId] || undefined,
    });
    delete campaignDecisionNotes[campaignId];
    persistCampaignDecisionNotes();
    selectedAutomationId = campaignId;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
    await refreshCampaignOpsFromApi();
    renderOperationsView("automations");
  } catch (err) {
    showOpsImportToast(err.message || "Reopen failed", true);
  }
}

function openWritingStudioForDraft({ draftId = null, campaignId = null } = {}) {
  const handoff = {};
  if (draftId) handoff.draftId = Number(draftId);
  if (campaignId) handoff.campaignId = campaignId;
  try {
    localStorage.setItem(WRITING_STUDIO_HANDOFF_KEY, JSON.stringify(handoff));
  } catch {}
  setState("view", WRITING_STUDIO_VIEW);
}

async function createCampaignWritingDraft(campaignId) {
  if (!campaignId) return;
  const overview = getCampaignOpsOverviewState();
  const campaign = [...(overview.campaigns || []), ...(overview.repository || [])]
    .find((item) => item.id === campaignId);
  if (!campaign) {
    showOpsImportToast("Campaign not found.", true);
    return;
  }
  try {
    const primaryDeliverable = String(campaign.deliverables?.[0] || "Campaign brief");
    const deliverableId = slugify(primaryDeliverable || "campaign-brief");
    const draft = await api.createWritingDraftApi({
      title: `${campaign.name} — ${primaryDeliverable}`,
      assetType: primaryDeliverable,
      content: `Campaign: ${campaign.name}\nDeliverable: ${primaryDeliverable}\n\nGoal:\n${campaign.nextAction || ""}`,
      campaignId: campaign.id,
      deliverableId,
      owner: campaign.owner || null,
      reviewer: campaign.reviewer || null,
      metadata: {
        source: "campaign_ops",
        campaignFamily: campaign.family || null,
      },
      source: "campaign_ops",
    });
    openWritingStudioForDraft({ draftId: draft.id, campaignId: campaign.id });
  } catch (err) {
    showOpsImportToast(err.message || "Draft creation failed", true);
  }
}

function handleOperationsClick(event) {
  // Delegate builder actions
  const builderBtn = event.target.closest("[data-builder-action]");
  if (builderBtn) {
    const builderAction = builderBtn.dataset.builderAction || "";
    handleBuilderAction(builderAction, builderBtn);
    return;
  }

  const button = event.target.closest("[data-ops-action]");
  if (!button) return;

  const action = button.dataset.opsAction || "";
  if (action === "select-agent") {
    selectedAgentId = button.dataset.agentId || "";
    writeStorage(OPS_AGENT_SELECTION_KEY, selectedAgentId);
    resetSelectedAgentRun();
    agentDraft = null;
    _enrichedAgent = null;
    _enrichedAgentLoadedForId = null;
    _agentInstructionContent = null;
    _agentSupportingFiles = [];
    renderOperationsView("agents");
    // Load enriched data asynchronously; re-render when ready.
    loadEnrichedAgent(selectedAgentId).then(() => renderOperationsView("agents")).catch(() => {});
    return;
  }
  if (action === "set-agent-view-mode") {
    agentViewMode = button.dataset.viewMode === "custom" ? "custom" : "slug";
    writeStorage(OPS_AGENT_VIEW_MODE_KEY, agentViewMode);
    renderOperationsView("agents");
    return;
  }
  if (action === "create-agent-folder") {
    createEmptyFolder();
    return;
  }
  if (action === "add-agent-to-folder") {
    event.preventDefault();
    event.stopPropagation();
    promptFolderForAgent(button.dataset.agentId || "");
    return;
  }
  if (action === "toggle-agent-tree") {
    const key = `${button.dataset.treeKind}:${button.dataset.treeId}`;
    const current = agentViewMode === "custom" ? agentCustomTreeCollapsed : agentTreeCollapsed;
    const next = { ...current, [key]: !current[key] };
    if (!next[key]) delete next[key];
    if (agentViewMode === "custom") {
      agentCustomTreeCollapsed = next;
      writeStorage(OPS_AGENT_CUSTOM_TREE_COLLAPSED_KEY, agentCustomTreeCollapsed);
    } else {
      agentTreeCollapsed = next;
      writeStorage(OPS_AGENT_TREE_COLLAPSED_KEY, agentTreeCollapsed);
    }
    renderOperationsView("agents");
    return;
  }
  if (action === "toggle-agent-filter") {
    const group = button.dataset.filterGroup;
    const value = button.dataset.filterValue;
    const key = group === "status" ? "statuses" : "triggers";
    const current = new Set(agentTreeFilters[key] || []);
    if (current.has(value)) current.delete(value);
    else current.add(value);
    agentTreeFilters = { ...agentTreeFilters, [key]: [...current] };
    renderOperationsView("agents");
    return;
  }
  if (action === "new-agent") {
    selectedAgentId = "";
    resetSelectedAgentRun();
    agentDraft = {
      id: "",
      title: "",
      description: "",
      goal: "",
      icon: "tool",
      constraints: { maxTurns: 50, timeoutMs: 300000 },
    };
    writeStorage(OPS_AGENT_SELECTION_KEY, selectedAgentId);
    writeStorage(OPS_AGENT_DRAFT_KEY, agentDraft);
    renderOperationsView("agents");
    return;
  }
  if (action === "save-agent") {
    void saveAgentDraft().catch((err) => showToast("Save failed", err.message || "Could not save agent.", { isError: true }));
    return;
  }
  if (action === "save-persona") {
    void savePersonaDraft().catch((err) => showToast("Save failed", err.message || "Could not save persona.", { isError: true }));
    return;
  }
  if (action === "reset-persona") {
    // Re-render to discard unsaved persona edits
    renderOperationsView("agents");
    return;
  }
  if (action === "edit-agent-with-builder") {
    const agentId = button.dataset.agentId || selectedAgentId;
    // CC18: builder_sessions.target_id is the Agent INT PK, not the slug.
    // We pass it through state so initBuilderSurface() can create a new
    // target-scoped session that triggers read_recent_runs() automatically.
    const rawDbId = button.dataset.agentDbId || "";
    const agentDbId = rawDbId && !Number.isNaN(Number(rawDbId)) ? Number(rawDbId) : null;
    if (agentId) {
      writeStorage(OPS_AGENT_SELECTION_KEY, agentId);
      setState("builderEditAgentId", agentId);
    }
    if (agentDbId) setState("builderEditAgentDbId", agentDbId);
    setState("view", "agents/builder");
    renderOperationsView("agents/builder");
    return;
  }
  if (action === "generate-instruction-from-goal") {
    const goalEl = document.querySelector("[data-ops-field='agent-goal']");
    const instrEl = document.querySelector("[data-ops-field='agent-instruction']");
    if (goalEl && instrEl && goalEl.value.trim()) {
      instrEl.value = goalEl.value.trim();
    }
    return;
  }
  if (action === "attach-skill") {
    const select = document.querySelector("[data-ops-field='attach-skill-select']");
    const skillId = select?.value;
    if (!skillId || !selectedAgentId) return;
    void (async () => {
      try {
        await api.assignSkillApi(Number(skillId), selectedAgentId);
        _enrichedAgent = null;
        await loadEnrichedAgent(selectedAgentId);
        agentDraft = null;
        renderOperationsView("agents");
      } catch (err) {
        showOpsImportToast(err.message || "Attach failed", true);
      }
    })();
    return;
  }
  if (action === "detach-skill") {
    const skillId = button.dataset.skillId;
    if (!skillId || !selectedAgentId) return;
    void (async () => {
      try {
        await api.unassignSkillApi(Number(skillId), selectedAgentId);
        _enrichedAgent = null;
        await loadEnrichedAgent(selectedAgentId);
        agentDraft = null;
        renderOperationsView("agents");
      } catch (err) {
        showOpsImportToast(err.message || "Detach failed", true);
      }
    })();
    return;
  }
  if (action === "attach-connector") {
    const section = document.getElementById("agent-connectors-section");
    if (section) void handleAttachConnector(section);
    return;
  }
  if (action === "detach-connector") {
    void handleDetachConnector(button);
    return;
  }
  if (action === "reset-agent") {
    const agents = getAgents();
    const selected = agents.find((agent) => agent.id === selectedAgentId) || agents[0];
    if (selected) {
      resetSelectedAgentRun();
      agentDraft = null;
      renderOperationsView("agents");
    }
    return;
  }
  if (action === "delete-agent") {
    if (!selectedAgentId) return;
    if (!window.confirm?.("Delete this agent?")) return;
    void (async () => {
      await api.deleteAgentApi(selectedAgentId);
      selectedAgentId = "";
      writeStorage(OPS_AGENT_SELECTION_KEY, selectedAgentId);
      resetSelectedAgentRun();
      agentDraft = null;
      await refreshAgentsFromApi();
      renderOperationsView("agents");
    })();
    return;
  }
  if (action === "open-agent-run") {
    const runId = button.dataset.runId || "";
    if (!runId) return;
    if (selectedAgentRunId === runId && !selectedAgentRunLoading) {
      resetSelectedAgentRun();
      renderOperationsView("agents");
      return;
    }
    selectedAgentRunId = runId;
    selectedAgentRunDetail = null;
    selectedAgentRunError = "";
    selectedAgentRunLoading = true;
    renderOperationsView("agents");
    void (async () => {
      try {
        const run = await api.fetchAgentRunById(runId);
        if (selectedAgentRunId !== runId) return;
        selectedAgentRunDetail = run || null;
        selectedAgentRunError = run ? "" : "That run could not be found.";
      } catch (err) {
        if (selectedAgentRunId !== runId) return;
        selectedAgentRunDetail = null;
        selectedAgentRunError = err?.message || "Could not load this run.";
      } finally {
        if (selectedAgentRunId === runId) {
          selectedAgentRunLoading = false;
          renderOperationsView("agents");
        }
      }
    })();
    return;
  }
  if (action === "switch-skill-tab") {
    selectedSkillTab = button.dataset.skillTab === "proposed" ? "proposed" : "library";
    writeStorage(OPS_SKILL_TAB_KEY, selectedSkillTab);
    if (selectedSkillTab === "library") {
      selectedSkillId = String(_approvedSkills[0]?.id || "");
    } else {
      selectedSkillId = String(_proposedSkills[0]?.id || "");
    }
    writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
    renderOperationsView("skills");
    return;
  }
  if (action === "select-skill-category") {
    selectedSkillCategory = button.dataset.skillCategory || "all";
    writeStorage(OPS_SKILL_CATEGORY_KEY, selectedSkillCategory);
    renderOperationsView("skills");
    return;
  }
  if (action === "select-skill") {
    selectedSkillId = button.dataset.skillId || "";
    writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
    renderOperationsView("skills");
    return;
  }
  if (action === "approve-skill") {
    promoteSkill(button.dataset.skillId || selectedSkillId);
    return;
  }
  if (action === "reject-skill") {
    rejectSkill(button.dataset.skillId || selectedSkillId);
    return;
  }
  if (action === "new-skill") {
    import("./skill-edit-modal.js").then(({ openSkillEditModal }) => {
      openSkillEditModal(null, async (saved) => {
        selectedSkillId = String(saved.id);
        writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
        await refreshSkillsFromApi();
        renderOperationsView("skills");
      });
    });
    return;
  }
  if (action === "focus-skill-editor") {
    const skillId = button.dataset.skillId || selectedSkillId;
    const skill = [..._approvedSkills, ..._proposedSkills].find((s) => String(s.id) === String(skillId));
    import("./skill-edit-modal.js").then(({ openSkillEditModal }) => {
      openSkillEditModal(skill || null, async (saved) => {
        selectedSkillId = String(saved.id);
        writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
        await refreshSkillsFromApi();
        renderOperationsView("skills");
      });
    });
    return;
  }
  if (action === "archive-skill") {
    const skillId = button.dataset.skillId || selectedSkillId;
    if (!skillId) return;
    void (async () => {
      await api.archiveSkillApi(skillId);
      selectedSkillId = "";
      writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
      await refreshSkillsFromApi();
      renderOperationsView("skills");
    })();
    return;
  }
  if (action === "delete-skill") {
    const skillId = button.dataset.skillId || selectedSkillId;
    if (!skillId) return;
    const skill = [..._approvedSkills, ..._proposedSkills].find((s) => String(s.id) === String(skillId));
    const skillName = skill?.name || `skill #${skillId}`;
    showOpsConfirm({
      title: "Delete skill",
      message: `Permanently delete <strong>${escapeHtml(skillName)}</strong>? This removes the skill file and all supporting files from disk and cannot be undone.`,
      confirmLabel: "Delete permanently",
      onConfirm: () => {
        void (async () => {
          try {
            await api.deleteSkillApi(skillId);
            selectedSkillId = "";
            writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
            await refreshSkillsFromApi();
            renderOperationsView("skills");
          } catch (err) {
            showOpsImportToast(err.message || "Delete failed", true);
          }
        })();
      },
    });
    return;
  }
  if (action === "import-skill-url") {
    const input = getShellContent()?.querySelector("[data-ops-field='skill-import-url']");
    const url = input?.value?.trim();
    if (!url) return;
    void (async () => {
      try {
        const created = await api.importSkillFromUrl(url);
        selectedSkillTab = "proposed";
        selectedSkillId = String(created.id || "");
        writeStorage(OPS_SKILL_TAB_KEY, selectedSkillTab);
        writeStorage(OPS_SKILL_SELECTION_KEY, selectedSkillId);
        await refreshSkillsFromApi();
        renderOperationsView("skills");
        showOpsImportToast(`"${created.name || url}" added to Proposed`);
      } catch (err) {
        showOpsImportToast(err.message || "Import failed", true);
      }
    })();
    return;
  }
  if (action === "select-workflow") {
    const newId = button.dataset.workflowId || "";
    if (newId !== selectedWorkflowId) {
      _latestWorkflowRunForId = null;
      _latestWorkflowRun = null;
    }
    selectedWorkflowId = newId;
    writeStorage(OPS_WORKFLOW_SELECTION_KEY, selectedWorkflowId);
    workflowDraft = null;
    renderOperationsView("workflows");
    if (selectedWorkflowId) void loadLatestWorkflowRun(selectedWorkflowId);
    return;
  }
  if (action === "new-workflow") {
    selectedWorkflowId = "";
    _latestWorkflowRun = null;
    _latestWorkflowRunForId = null;
    workflowDraft = {
      id: "",
      title: "",
      description: "",
      steps: [
        { type: "prompt", label: "Step 1", prompt: "", agentId: "", instructions: "", title: "", description: "", behavior: "pause", outputRef: "run_summary" },
        { type: "prompt", label: "Step 2", prompt: "", agentId: "", instructions: "", title: "", description: "", behavior: "pause", outputRef: "run_summary" },
      ],
      meta: {
        status: "draft",
        schedule: "Manual",
      },
    };
    writeStorage(OPS_WORKFLOW_SELECTION_KEY, selectedWorkflowId);
    writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
    renderOperationsView("workflows");
    return;
  }
  if (action === "run-workflow") {
    const wfId = button.dataset.workflowId || workflowDraft?.id || selectedWorkflowId;
    if (!wfId) return;
    void (async () => {
      try {
        const { runId } = await api.runWorkflowApi(wfId);
        _latestWorkflowRun = { id: runId, workflow_id: wfId, status: "pending", step_results: null, cost_usd: 0, started_at: Math.floor(Date.now() / 1000) };
        _latestWorkflowRunForId = wfId;
        renderOperationsView("workflows");
        // Auto-refresh once after 5 s to catch fast completions
        setTimeout(() => void refreshLatestWorkflowRun(wfId), 5000);
      } catch (err) {
        showOpsImportToast(err.message || "Failed to start run", true);
      }
    })();
    return;
  }
  if (action === "refresh-workflow-run") {
    const wfId = workflowDraft?.id || selectedWorkflowId;
    if (wfId) void refreshLatestWorkflowRun(wfId);
    return;
  }
  if (action === "save-workflow") {
    void saveWorkflowDraft();
    return;
  }
  if (action === "duplicate-workflow") {
    if (!workflowDraft) return;
    selectedWorkflowId = "";
    workflowDraft = {
      ...workflowDraft,
      id: "",
      title: workflowDraft.title ? `Copy of ${workflowDraft.title}` : "",
    };
    writeStorage(OPS_WORKFLOW_SELECTION_KEY, selectedWorkflowId);
    writeStorage(OPS_WORKFLOW_DRAFT_KEY, workflowDraft);
    renderOperationsView("workflows");
    return;
  }
  if (action === "delete-workflow") {
    if (!workflowDraft?.id) {
      workflowDraft = null;
      renderOperationsView("workflows");
      return;
    }
    if (!window.confirm?.("Delete this workflow?")) return;
    void (async () => {
      await api.deleteWorkflowApi(workflowDraft.id);
      selectedWorkflowId = "";
      workflowDraft = null;
      writeStorage(OPS_WORKFLOW_SELECTION_KEY, selectedWorkflowId);
      await refreshWorkflowsFromApi();
      renderOperationsView("workflows");
    })();
    return;
  }
  if (action === "add-workflow-step") {
    appendWorkflowStep();
    renderOperationsView("workflows");
    return;
  }
  if (action === "remove-workflow-step") {
    removeWorkflowStep(Number(button.dataset.stepIndex || 0));
    renderOperationsView("workflows");
    return;
  }
  if (action === "select-automation") {
    selectedAutomationId = button.dataset.automationId || "";
    _automationDraft = null;
    writeStorage(OPS_AUTOMATION_SELECTION_KEY, selectedAutomationId);
    renderOperationsView("automations");
    return;
  }
  if (action === "toggle-automation") {
    void toggleAutomation(button.dataset.automationId || selectedAutomationId);
    return;
  }
  if (action === "new-automation") {
    _automationDraft = { _creating: true, targetType: "workflow" };
    renderOperationsView("automations");
    return;
  }
  if (action === "cancel-new-automation") {
    _automationDraft = null;
    renderOperationsView("automations");
    return;
  }
  if (action === "save-new-automation") {
    void saveNewAutomation();
    return;
  }
  if (action === "run-automation") {
    void triggerAutomationRun(button.dataset.automationId || selectedAutomationId);
    return;
  }
  if (action === "delete-automation") {
    const automationId = button.dataset.automationId || selectedAutomationId;
    showOpsConfirm({
      title: "Archive automation",
      message: "Archive this automation? It will no longer appear in the active list. Run history is preserved.",
      confirmLabel: "Archive",
      onConfirm: () => void archiveAutomation(automationId),
    });
    return;
  }
  if (action === "decide-approval") {
    void decideAutomationApproval(button.dataset.approvalId, button.dataset.decision || "approve");
    return;
  }
  if (action === "campaign-decision") {
    void applyCampaignDecision(button.dataset.campaignId || selectedAutomationId, button.dataset.campaignAction || "");
    return;
  }
  if (action === "promote-campaign-candidate") {
    void promoteCampaignCandidate(button.dataset.campaignId || selectedAutomationId);
    return;
  }
  if (action === "reopen-campaign-candidate") {
    void reopenCampaignCandidate(button.dataset.campaignId || selectedAutomationId);
    return;
  }
  if (action === "open-campaign-writing-draft") {
    openWritingStudioForDraft({
      draftId: button.dataset.draftId || null,
      campaignId: button.dataset.campaignId || selectedAutomationId,
    });
    return;
  }
  if (action === "open-workflow-writing-draft") {
    openWritingStudioForDraft({ draftId: button.dataset.draftId || null });
    return;
  }
  if (action === "create-campaign-writing-draft") {
    void createCampaignWritingDraft(button.dataset.campaignId || selectedAutomationId);
    return;
  }
  if (action === "preview-campaign-action") {
    showOpsImportToast("Manual campaign creation is still deferred while CRM and approval seams stay intentionally narrow.");
    return;
  }
  if (action === "open-shell-view") {
    const targetView = normalizeAppView(button.dataset.shellView);
    if (targetView === MEMORY_VIEW || targetView === WRITING_STUDIO_VIEW || isOperationsSurfaceView(targetView)) {
      setState("view", targetView);
    }
    return;
  }
}

function handleOperationsInput(event) {
  const treeSearch = event.target.closest("[data-ops-agent-tree-search]");
  if (treeSearch) {
    agentTreeSearch = treeSearch.value;
    renderOperationsView("agents");
    requestAnimationFrame(() => {
      const next = document.querySelector("[data-ops-agent-tree-search]");
      if (next) {
        next.focus();
        next.setSelectionRange(agentTreeSearch.length, agentTreeSearch.length);
      }
    });
    return;
  }

  const input = event.target.closest("[data-ops-field]");
  if (!input) return;

  const field = input.dataset.opsField || "";
  const value = input.value;

  if (field.startsWith("agent-")) {
    updateAgentDraftField(field.replace("agent-", ""), value);
    return;
  }
  // Agent package policy fields — not prefixed with "agent-" for readability.
  if (["provider", "model", "fallbackProvider", "fallbackModel", "memoryScope", "permissionMode", "outputType"].includes(field)) {
    updateAgentDraftField(field, value);
    return;
  }
  // agent-instruction textarea: stored live in DOM; read at save time. No draft update needed.
  if (field === "agent-instruction") return;
  if (field.startsWith("workflow-step-")) {
    const index = Number(input.dataset.stepIndex || 0);
    updateWorkflowStepField(index, field.replace("workflow-step-", ""), value);
    return;
  }
  if (field.startsWith("workflow-")) {
    updateWorkflowDraftField(field.replace("workflow-", ""), value);
    return;
  }
  if (field === "campaign-note") {
    const campaignId = input.dataset.campaignId || selectedAutomationId;
    if (campaignId) {
      campaignDecisionNotes[campaignId] = value;
      persistCampaignDecisionNotes();
    }
    return;
  }
  if (field === "new-automation-name") {
    if (_automationDraft) _automationDraft.name = value;
    return;
  }
  if (field === "new-automation-description") {
    if (_automationDraft) _automationDraft.description = value;
    return;
  }
  if (field === "new-automation-target-type") {
    if (_automationDraft) {
      _automationDraft.targetType = value;
      _automationDraft.targetId = "";
      renderOperationsView("automations");
    }
    return;
  }
  if (field === "new-automation-target-id") {
    if (_automationDraft) _automationDraft.targetId = value;
    return;
  }
  if (field === "new-automation-trigger-type") {
    if (_automationDraft) _automationDraft.triggerType = value;
    return;
  }
  if (field === "new-automation-approval-policy") {
    if (_automationDraft) _automationDraft.approvalPolicy = value;
    return;
  }
  if (field === "skill-search") {
    writeStorage(OPS_SKILL_SEARCH_KEY, value);
    renderOperationsView("skills");
  }
}

function handleOperationsChange(event) {
  const treeSort = event.target.closest("[data-ops-agent-tree-sort]");
  if (treeSort) {
    agentTreeSort = treeSort.value || "name";
    renderOperationsView("agents");
    return;
  }

  const field = event.target.closest("[data-ops-field]");
  if (!field) return;
  const opsField = field.dataset.opsField || "";
  if (opsField === "skill-search") {
    writeStorage(OPS_SKILL_SEARCH_KEY, field.value);
    renderOperationsView("skills");
    return;
  }
  // <select> elements fire "change" not "input" — mirror the policy-field logic from handleOperationsInput.
  if (["provider", "model", "fallbackProvider", "fallbackModel", "memoryScope", "permissionMode", "outputType"].includes(opsField)) {
    updateAgentDraftField(opsField, field.value);
    if (opsField === "provider" || opsField === "fallbackProvider") renderOperationsView("agents");
    return;
  }
  if (opsField === "reasonCodesEmitted") {
    void saveAgentReasonCodes(field);
    return;
  }
  // Workflow step selects and checkboxes: type/destination changes need a re-render.
  if (opsField.startsWith("workflow-step-")) {
    const index = parseInt(field.dataset.stepIndex ?? "-1", 10);
    if (index < 0) return;
    const stepField = opsField.replace("workflow-step-", "");
    // Checkboxes report checked, not value
    const value = field.type === "checkbox" ? field.checked : field.value;
    updateWorkflowStepField(index, stepField, value);
    if (stepField === "type" || stepField === "destination") renderOperationsView("workflows");
    return;
  }
  // Workflow-level selects
  if (opsField.startsWith("workflow-")) {
    const wfField = opsField.replace("workflow-", "");
    if (wfField === "status" || wfField === "schedule") {
      updateWorkflowDraftField(wfField, field.value);
    }
    return;
  }
}

function handleOperationsKeydown(event) {
  const treeSearch = event.target.closest("[data-ops-agent-tree-search]");
  if (treeSearch && event.key === "Escape") {
    agentTreeSearch = "";
    treeSearch.value = "";
    renderOperationsView("agents");
  }
}

onState("view", scheduleRender);
onState("agents", scheduleRender);
onState("agentChains", scheduleRender);
onState("agentDags", scheduleRender);
onState("agentMetrics", scheduleRender);
onState("agentsLoaded", scheduleRender);
onState("agentsLoading", scheduleRender);
onState("agentsError", scheduleRender);
onState("workflows", scheduleRender);
onState("workflowsLoaded", scheduleRender);
onState("workflowsLoading", scheduleRender);
onState("workflowsError", scheduleRender);

// Init Agent-Builder surface when the view switches to "agents/builder"
let _builderInitialized = false;
onState("view", (view) => {
  if (normalizeAppView(view) !== "agents/builder") return;
  // CC18: when navigating with a pending "Edit with Builder" target, always
  // re-init so initBuilderSurface() can spawn a new target-scoped session.
  if (!_builderInitialized || getState("builderEditAgentDbId")) {
    _builderInitialized = true;
    void initBuilderSurface().then(() => renderOperationsView("agents/builder")).catch(() => {});
  }
});

// Re-render when the builder triggers an internal state change
document.addEventListener("builder:rerender", () => {
  if (normalizeAppView(getState("view")) === "agents/builder") {
    renderOperationsView("agents/builder");
  }
});

// ── Proposals Inbox badge (J6a) ───────────────────────────────────────────────
// Fetch the inbox count and inject a badge into the operations overview page's
// Agents card.  Cache 30 s; clear on builder session close / proposal action.

let _opsInboxCache = null;
let _opsInboxCacheTs = 0;
const _OPS_INBOX_TTL_MS = 30_000;

async function _refreshOpsInboxBadge() {
  const placeholder = document.getElementById("ops-inbox-badge-placeholder");
  if (!placeholder) return;

  const now = Date.now();
  if (!_opsInboxCache || now - _opsInboxCacheTs >= _OPS_INBOX_TTL_MS) {
    try {
      _opsInboxCache = await api.builderFetchInbox();
      _opsInboxCacheTs = now;
    } catch {
      return; // don't show a broken badge
    }
  }

  const total =
    (_opsInboxCache.agents_with_pending_proposals?.length ?? 0) +
    (_opsInboxCache.agents_with_new_summaries?.length ?? 0);

  if (total > 0) {
    placeholder.innerHTML = `<span class="inbox-overview-badge">${total}</span>`;
  } else {
    placeholder.innerHTML = "";
  }
}

// Invalidate ops inbox cache on proposal approval/rejection so the badge updates.
document.addEventListener("builder:proposal-actioned", () => {
  _opsInboxCache = null;
});

// After each operations overview render, inject the inbox badge asynchronously.
onState("view", (view) => {
  if (normalizeAppView(view) === OPERATIONS_VIEW) {
    // Give the DOM a tick to settle after scheduleRender, then inject the badge.
    queueMicrotask(() => _refreshOpsInboxBadge().catch(() => {}));
  }
});

const shellContent = getShellContent();
shellContent?.addEventListener("click", handleOperationsClick);
shellContent?.addEventListener("input", handleOperationsInput);
shellContent?.addEventListener("change", handleOperationsChange);
shellContent?.addEventListener("keydown", handleOperationsKeydown);
shellContent?.addEventListener("dragstart", handleOperationsDragstart);
shellContent?.addEventListener("contextmenu", handleOperationsContextMenu);
shellContent?.addEventListener("dragover", handleOperationsDragover);
shellContent?.addEventListener("dragleave", handleOperationsDragleave);
shellContent?.addEventListener("drop", handleOperationsDrop);

export async function loadSkillsShell() {
  if (!_skillsLoaded) {
    renderOperationsView("skills"); // show loading state first
    await refreshSkillsFromApi();
  }
  renderOperationsView("skills");
}

export async function loadAutomationsShell() {
  if (!_automationsLoaded) {
    renderOperationsView("automations");
    await refreshAutomationsFromApi();
  }
  renderOperationsView("automations");
}

export function loadPipelinesShell() {
  renderOperationsView("pipelines");
}

export function loadPipelineRunHistoryShell() {
  renderOperationsView(PIPELINE_RUN_HISTORY_VIEW);
}

export async function loadCampaignOpsShell() {
  renderOperationsView("automations");
  await refreshCampaignOpsFromApi();
  renderOperationsView("automations");
}

export {
  renderOperationsError,
  renderLoadingCardGrid,
};
