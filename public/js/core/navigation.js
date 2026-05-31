export const DEFAULT_APP_VIEW = "command-center";
export const LEGACY_DASHBOARD_VIEW = "analytics-dashboard";
export const DASHBOARD_VIEW = "dashboard";
export const WORKSPACE_VIEW = "workspace";
export const CALENDAR_VIEW = "calendar";
export const MEETINGS_VIEW = "meetings";
export const JIRA_VIEW = "jira";
export const OKR_VIEW = "okr";
export const OPERATIONS_VIEW = "operations";
export const PIPELINE_RUN_HISTORY_VIEW = "pipeline-run-history";
export const MEMORY_VIEW = "memory";
export const DEV_PROJECTS_VIEW = "dev-projects";
export const WRITING_STUDIO_VIEW = "writing-studio";
export const MARKETING_OS_VIEW = "marketing-os";
export const MARKETING_DASHBOARD_VIEW = "marketing-dashboard";
export const MARKETING_CAMPAIGNS_VIEW = "marketing-campaigns";
export const MARKETING_SIGNALS_VIEW = "marketing-signals";
export const MARKETING_APPROVALS_VIEW = "marketing-approvals";
export const MARKETING_RULESETS_VIEW = "marketing-rulesets";
export const MARKETING_SIGNAL_PLAYBOOK_VIEW = "signal-playbook";
export const MARKETING_SCOUT_RUNS_VIEW = "marketing-scout-runs";
export const INTEGRATIONS_VIEW = "integrations";

export const PRIMARY_NAV_DESTINATIONS = [
  {
    id: DASHBOARD_VIEW,
    label: "Dashboard",
    shortLabel: "Dashboard",
    description: "Morning Brief, Task Command, Needs Attention, and the top-level Artemis readout.",
  },
  {
    id: WORKSPACE_VIEW,
    label: "Workspace",
    shortLabel: "Workspace",
    description: "Calendar, Meetings, Jira Board, and OKR Studio.",
  },
  {
    id: OPERATIONS_VIEW,
    label: "Operations",
    shortLabel: "Operations",
    description: "Agents, Skills, Workflows, Campaign Ops, and Memory.",
  },
  {
    id: MARKETING_OS_VIEW,
    label: "Marketing Campaign OS",
    shortLabel: "Marketing",
    description: "Campaign portfolio, workspace, signals inbox, and approval queue.",
  },
  {
    id: DEV_PROJECTS_VIEW,
    label: "Dev Projects",
    shortLabel: "Projects",
    description: "Project-scoped chat, sessions, files, and project context.",
  },
];

export const SECONDARY_NAV_DESTINATIONS = [
  {
    id: "calendar",
    label: "Calendar",
    shortLabel: "Calendar",
    description: "Time reality, prep windows, and future work-block awareness.",
    section: "Personal Workspace",
    view: CALENDAR_VIEW,
  },
  {
    id: "meetings",
    label: "Meetings",
    shortLabel: "Meetings",
    description: "Prep, notes, follow-up, and decision extraction.",
    section: "Personal Workspace",
    view: MEETINGS_VIEW,
  },
  {
    id: "jira-board",
    label: "Jira Board",
    shortLabel: "Jira",
    description: "Operational risk, deadlines, and execution queue.",
    section: "Personal Workspace",
    view: JIRA_VIEW,
  },
  {
    id: "okr-studio",
    label: "OKR Studio",
    shortLabel: "OKRs",
    description: "Goal health, evidence capture, and update risk.",
    section: "Personal Workspace",
    view: OKR_VIEW,
  },
  {
    id: "agents",
    label: "Agents",
    shortLabel: "Agents",
    description: "Durable worker profiles and the agent operating surface.",
    section: "Operations",
    view: "agents",
    focus: "agents",
  },
  {
    id: "skills",
    label: "Skills",
    shortLabel: "Skills",
    description: "Approved capability library and proposal review surface.",
    section: "Operations",
    view: "skills",
    focus: "skills",
  },
  {
    id: "workflows",
    label: "Workflows",
    shortLabel: "Flows",
    description: "Workflow builder and inspector for saved recipes.",
    section: "Operations",
    view: "workflows",
    focus: "workflows",
  },
  {
    id: "pipelines",
    label: "Pipelines",
    shortLabel: "Pipelines",
    description: "Unified orchestration primitives — nodes, edges, and trigger config. Visual canvas in PIPE2.",
    section: "Operations",
    view: "pipelines",
    focus: "pipelines",
  },
  {
    id: PIPELINE_RUN_HISTORY_VIEW,
    label: "Run History",
    shortLabel: "Runs",
    description: "Pipeline execution history, replay links, and terminal run actions.",
    section: "Operations",
    view: PIPELINE_RUN_HISTORY_VIEW,
    focus: "pipelines",
  },
  {
    id: "automations",
    label: "Automations",
    shortLabel: "Automations",
    description: "Scheduled and triggered rules that control when workflows and agents run.",
    section: "Operations",
    view: "automations",
    focus: "automations",
  },
  {
    id: "memory",
    label: "Memory",
    shortLabel: "Memory",
    description: "Approval inbox, knowledge base, and memory governance.",
    section: "Operations",
    view: MEMORY_VIEW,
  },
  {
    id: MARKETING_DASHBOARD_VIEW,
    label: "Dashboard",
    shortLabel: "Dashboard",
    description: "Campaign portfolio with headline KPIs and sparklines.",
    section: "Marketing",
    view: MARKETING_DASHBOARD_VIEW,
  },
  {
    id: WRITING_STUDIO_VIEW,
    label: "Writing Studio",
    shortLabel: "Writing",
    description: "Co-write, review drafts, and train the marketing writing agent.",
    section: "Personal Workspace",
    view: WRITING_STUDIO_VIEW,
    focus: "writing-studio",
  },
  {
    id: MARKETING_CAMPAIGNS_VIEW,
    label: "Campaigns",
    shortLabel: "Campaigns",
    description: "Campaign list and per-campaign workspace.",
    section: "Marketing",
    view: MARKETING_CAMPAIGNS_VIEW,
  },
  {
    id: MARKETING_SIGNALS_VIEW,
    label: "Signals Inbox",
    shortLabel: "Signals",
    description: "Detected legislative and funding signals for review.",
    section: "Marketing",
    view: MARKETING_SIGNALS_VIEW,
  },
  {
    id: MARKETING_APPROVALS_VIEW,
    label: "Approval Queue",
    shortLabel: "Approvals",
    description: "Human approval gates for campaign deliverables.",
    section: "Marketing",
    view: MARKETING_APPROVALS_VIEW,
  },
  {
    id: MARKETING_SIGNAL_PLAYBOOK_VIEW,
    label: "Signal Playbook",
    shortLabel: "Playbook",
    description: "Editable campaign-signal criteria and reason-code registry.",
    section: "Marketing",
    view: MARKETING_SIGNAL_PLAYBOOK_VIEW,
  },
  {
    id: MARKETING_SCOUT_RUNS_VIEW,
    label: "Scout Runs",
    shortLabel: "Scout Runs",
    description: "Read-only debug surface for scout harness run history and intake results.",
    section: "Marketing",
    view: MARKETING_SCOUT_RUNS_VIEW,
  },
  {
    id: "files",
    label: "Files",
    shortLabel: "Files",
    description: "Project tree, dirty state, and editor handoff for the selected Dev Project.",
    section: "Dev Projects",
    view: DEV_PROJECTS_VIEW,
    action: "open-project-files",
  },
  {
    id: INTEGRATIONS_VIEW,
    label: "Integrations",
    shortLabel: "Integrations",
    description: "Connect Slack and other external services.",
    section: "Settings",
    view: INTEGRATIONS_VIEW,
  },
];

const KNOWN_VIEWS = new Set([
  DEFAULT_APP_VIEW,
  DASHBOARD_VIEW,
  WORKSPACE_VIEW,
  CALENDAR_VIEW,
  MEETINGS_VIEW,
  JIRA_VIEW,
  OKR_VIEW,
  OPERATIONS_VIEW,
  MEMORY_VIEW,
  DEV_PROJECTS_VIEW,
  WRITING_STUDIO_VIEW,
  LEGACY_DASHBOARD_VIEW,
  INTEGRATIONS_VIEW,
  "agents/builder",
  ...PRIMARY_NAV_DESTINATIONS.map((item) => item.id),
  ...SECONDARY_NAV_DESTINATIONS.map((item) => item.id),
]);

export function normalizeAppView(view) {
  if (view === "home") return DEFAULT_APP_VIEW;
  if (view === "dashboard") return DEFAULT_APP_VIEW;
  if (view === "workspace") return WORKSPACE_VIEW;
  if (view === "calendar") return CALENDAR_VIEW;
  if (view === "meetings") return MEETINGS_VIEW;
  if (view === "jira" || view === "jira-board") return JIRA_VIEW;
  if (view === "okr" || view === "okr-studio") return OKR_VIEW;
  if (view === "operations") return OPERATIONS_VIEW;
  if (view === "pipeline-run-history" || view === "operations/pipeline-run-history") return PIPELINE_RUN_HISTORY_VIEW;
  if (view === "memory") return MEMORY_VIEW;
  if (view === "writing-studio") return WRITING_STUDIO_VIEW;
  if (view === "marketing-os") return MARKETING_OS_VIEW;
  if (view === "marketing-dashboard") return MARKETING_DASHBOARD_VIEW;
  if (view === "marketing-campaigns") return MARKETING_CAMPAIGNS_VIEW;
  if (view === "marketing-signals") return MARKETING_SIGNALS_VIEW;
  if (view === "marketing-approvals") return MARKETING_APPROVALS_VIEW;
  if (view === "marketing-rulesets") return MARKETING_RULESETS_VIEW;
  if (view === "signal-playbook") return MARKETING_SIGNAL_PLAYBOOK_VIEW;
  if (view === "marketing-scout-runs") return MARKETING_SCOUT_RUNS_VIEW;
  if (view === "pipelines") return "pipelines";
  if (view === "dev-projects") return "chat";
  if (view === "integrations") return INTEGRATIONS_VIEW;
  if (view === "command-center") return DEFAULT_APP_VIEW;
  if (view === "modules") return WORKSPACE_VIEW;
  if (view === "chat") return "chat";
  return KNOWN_VIEWS.has(view) ? view : DEFAULT_APP_VIEW;
}

export function isPrimaryNavView(view) {
  return PRIMARY_NAV_DESTINATIONS.some((item) => item.id === normalizeAppView(view));
}

export function isShellView(view) {
  const normalizedView = normalizeAppView(view);
  return normalizedView === DEFAULT_APP_VIEW
    || normalizedView === WORKSPACE_VIEW
    || normalizedView === CALENDAR_VIEW
    || normalizedView === MEETINGS_VIEW
    || normalizedView === JIRA_VIEW
    || normalizedView === OKR_VIEW
    || normalizedView === OPERATIONS_VIEW
    || normalizedView === MEMORY_VIEW
    || normalizedView === WRITING_STUDIO_VIEW
    || normalizedView === "workflows"
    || normalizedView === "agents"
    || normalizedView === "agents/builder"
    || normalizedView === "skills"
    || normalizedView === "automations"
    || normalizedView === "pipelines"
    || normalizedView === PIPELINE_RUN_HISTORY_VIEW
    || normalizedView === MARKETING_OS_VIEW
    || normalizedView === MARKETING_DASHBOARD_VIEW
    || normalizedView === MARKETING_CAMPAIGNS_VIEW
    || normalizedView === MARKETING_SIGNALS_VIEW
    || normalizedView === MARKETING_APPROVALS_VIEW
    || normalizedView === MARKETING_RULESETS_VIEW
    || normalizedView === MARKETING_SIGNAL_PLAYBOOK_VIEW
    || normalizedView === LEGACY_DASHBOARD_VIEW
    || normalizedView === INTEGRATIONS_VIEW;
}
