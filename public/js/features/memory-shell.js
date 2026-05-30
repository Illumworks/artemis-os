import { $ } from "../core/dom.js";
import { on as onBus } from "../core/events.js";
import { getState, setState } from "../core/store.js";
import {
  MEMORY_VIEW,
  normalizeAppView,
} from "../core/navigation.js";
import {
  fetchAgents,
  fetchMemoryList,
  fetchMemoryStats,
  fetchSessions,
  updateMemoryApi,
  deleteMemoryApi,
  createMemoryApi,
  optimizeMemoryApi,
  applyOptimizationApi,
  createSkillApi,
  exportMemoryArchiveApi,
  createMemorySqliteBackupApi,
  dryRunMemoryArchiveImportApi,
  applyMemoryArchiveImportApi,
  fetchMemoryEvidenceApi,
  fetchMemoryDrawerApi,
  fetchMemoryEntitiesApi,
  fetchEntityNeighborhoodApi,
  fetchMemoryShellStats,
  fetchMemoryShellScopes,
  fetchMemoryShellObservations,
  fetchMemoryShellDrawers,
  fetchMemoryShellObservationDetail,
} from "../core/api.js";
import { escapeHtml } from "../core/utils.js";

const MEMORY_SECTION_DEFS = [
  {
    id: "review",
    label: "Needs Review",
    eyebrow: "Approval inbox",
    description: "New, risky, or under-specified memory that needs a human call before it becomes durable.",
    emptyTitle: "No review items right now",
    emptyCopy: "Warnings and fresh discoveries land here first so you can approve, reroute, or trim them.",
  },
  {
    id: "knows",
    label: "Artemis Knows",
    eyebrow: "Approved knowledge",
    description: "Durable facts and conventions already in use by the assistant.",
    emptyTitle: "No approved knowledge yet",
    emptyCopy: "Approved knowledge will appear here once it graduates from the review inbox.",
  },
  {
    id: "projects",
    label: "Projects",
    eyebrow: "Project memory",
    description: "Current project-scoped memory for decisions, facts, and constraints.",
    emptyTitle: "No project memory yet",
    emptyCopy: "Project memory stays scoped to the selected workspace and grows as decisions settle in.",
  },
  {
    id: "working",
    label: "Working",
    eyebrow: "Working memory",
    description: "Short-lived context still in motion for the active project or session.",
    emptyTitle: "No working memory right now",
    emptyCopy: "Working memory clears quickly by design and only stays visible while it still matters.",
  },
  {
    id: "agents",
    label: "Agents",
    eyebrow: "Agent memory",
    description: "What each agent is allowed to remember and reuse across runs.",
    emptyTitle: "No agent memory yet",
    emptyCopy: "Agent-scoped rows will show up here once agents start contributing durable context.",
  },
  {
    id: "skills",
    label: "Skills / Rules",
    eyebrow: "Rules and skills",
    description: "Promoted patterns and guardrails that should survive as reusable behavior.",
    emptyTitle: "No skills or rules yet",
    emptyCopy: "When a pattern becomes reusable, it can graduate here as a skill or rule.",
  },
];

const MEMORY_FILTERS = [
  { id: "all", label: "All" },
  { id: "artemis", label: "Artemis" },
  { id: "agents", label: "Agents" },
  { id: "projects", label: "Projects" },
  { id: "skills", label: "Skills" },
];

const CATEGORY_LABELS = {
  warning: "Warning",
  discovery: "Discovery",
  decision: "Decision",
  convention: "Convention",
};

const CATEGORIES = ["convention", "decision", "discovery", "warning"];

let memoryLoadToken = 0;
let memoryModel = null;
let memoryState = {
  section: "review",
  filter: "all",
  selectedBySection: new Map(),
  searchQuery: "",
  editingRowId: null,
  addFormOpen: false,
  archiveStatus: null,
  pendingArchive: null,
  evidenceByObs: new Map(), // observationId -> { loading, rows, error }
  wingFilter: null,         // null | { scopeKind, scopeId, label }
  roomFilter: null,         // null | { type: 'category'|'entity', value, label }
};

// M6 shell state
let m6State = {
  tab: "observations",        // "observations" | "drawers"
  scopeFilter: null,          // null | { scope_kind, scope_id }
  selectedObsId: null,        // int | null
  stats: null,
  scopes: [],
  listData: null,             // { observations|drawers: [], total, offset }
  detailData: null,           // { observation, evidence }
  detailLoading: false,
};

function resetMemoryState() {
  return {
    section: "review",
    filter: "all",
    selectedBySection: new Map(),
    searchQuery: "",
    editingRowId: null,
    addFormOpen: false,
    archiveStatus: null,
    pendingArchive: null,
    evidenceByObs: new Map(),
    wingFilter: null,
    roomFilter: null,
  };
}

onBus("projectChanged", () => {
  if (normalizeAppView(getState("view")) === MEMORY_VIEW) {
    void loadMemoryShell();
  }
});

export function renderMemoryShellLoading() {
  return `
    <section class="shell-hero memory-shell-hero">
      <div class="shell-eyebrow">Memory</div>
      <h2>Memory</h2>
      <p>Loading the memory control center, approval inbox, and project-scoped knowledge base...</p>
      <div class="memory-shell-summary memory-shell-summary-loading">
        <span class="memory-shell-summary-chip">Loading review queue</span>
        <span class="memory-shell-summary-chip">Loading approved memory</span>
        <span class="memory-shell-summary-chip">Loading project scope</span>
      </div>
    </section>
    <section class="memory-shell-layout" aria-busy="true">
      <article class="memory-shell-panel memory-shell-nav memory-shell-panel-loading">
        <div class="memory-shell-nav-head">
          <div class="memory-shell-nav-eyebrow">Governance</div>
          <div class="memory-shell-nav-title">Loading sections</div>
          <div class="memory-shell-nav-sub">The split view will land once the selected project memory finishes loading.</div>
        </div>
        <div class="memory-shell-loading-block"></div>
        <div class="memory-shell-loading-block"></div>
        <div class="memory-shell-loading-block"></div>
      </article>
      <article class="memory-shell-panel memory-shell-queue memory-shell-panel-loading">
        <div class="memory-shell-queue-head">
          <div>
            <div class="memory-shell-section-eyebrow">Needs Review</div>
            <h3>Approval inbox</h3>
            <p>Fresh memory proposals will appear here first.</p>
          </div>
        </div>
        <div class="memory-shell-loading-block memory-shell-loading-block-wide"></div>
        <div class="memory-shell-loading-block memory-shell-loading-block-row"></div>
        <div class="memory-shell-loading-block memory-shell-loading-block-row"></div>
        <div class="memory-shell-loading-block memory-shell-loading-block-row"></div>
      </article>
      <article class="memory-shell-panel memory-shell-detail memory-shell-panel-loading">
        <div class="memory-shell-detail-empty">
          <strong>Loading detail panel</strong>
          <span>The selected memory row will appear here once the queue is ready.</span>
        </div>
      </article>
    </section>
  `;
}

export function renderMemoryShellPrompt() {
  return `
    <section class="shell-hero memory-shell-hero">
      <div class="shell-eyebrow">Memory</div>
      <h2>Memory</h2>
      <p>Select a project to unlock the Memory control center. This surface is project-scoped in the current build so review, approval, and promotion stay grounded in the active workspace.</p>
      <div class="memory-shell-summary">
        <span class="memory-shell-summary-chip">Project required</span>
        <span class="memory-shell-summary-chip">Needs Review first</span>
        <span class="memory-shell-summary-chip">Approval inbox</span>
      </div>
    </section>
    <section class="memory-shell-layout">
      <article class="memory-shell-panel memory-shell-nav">
        <div class="memory-shell-nav-head">
          <div class="memory-shell-nav-eyebrow">Governance</div>
          <div class="memory-shell-nav-title">Choose a project</div>
          <div class="memory-shell-nav-sub">Memory only becomes live once a project is selected.</div>
        </div>
      </article>
      <article class="memory-shell-panel memory-shell-queue">
        <div class="memory-shell-queue-head">
          <div>
            <div class="memory-shell-section-eyebrow">Needs Review</div>
            <h3>Approval inbox</h3>
            <p>Waiting for a project selection before loading live memory rows.</p>
          </div>
        </div>
      </article>
      <article class="memory-shell-panel memory-shell-detail">
        <div class="memory-shell-detail-empty">
          <strong>No project selected</strong>
          <span>Pick a project to see the live memory queue and detail pane.</span>
        </div>
      </article>
    </section>
  `;
}

export function renderMemoryShellError(message = "The memory surface could not load.") {
  const detail = message ? String(message) : "Unknown memory load failure.";
  return `
    <section class="shell-hero memory-shell-hero">
      <div class="shell-eyebrow">Memory</div>
      <h2>Memory</h2>
      <p>The memory control center is mounted, but the current project rows could not be read. The shell stays isolated so the rest of the app can keep working.</p>
      <div class="memory-shell-summary">
        <span class="memory-shell-summary-chip memory-shell-summary-chip-warning">Load failed</span>
        <span class="memory-shell-summary-chip">Refresh to retry</span>
      </div>
    </section>
    <section class="memory-shell-layout">
      <article class="memory-shell-panel memory-shell-nav">
        <div class="memory-shell-nav-head">
          <div class="memory-shell-nav-eyebrow">Governance</div>
          <div class="memory-shell-nav-title">Memory unavailable</div>
          <div class="memory-shell-nav-sub">The split view stays mounted so the approved shell structure remains stable.</div>
        </div>
      </article>
      <article class="memory-shell-panel memory-shell-queue">
        <div class="memory-shell-detail-empty">
          <strong>Could not read memory rows</strong>
          <span>${escapeHtml(detail)}</span>
        </div>
      </article>
      <article class="memory-shell-panel memory-shell-detail">
        <div class="memory-shell-detail-empty">
          <strong>Retry the load</strong>
          <span>Choose the refresh action after confirming the selected project still exists.</span>
        </div>
      </article>
    </section>
  `;
}

export async function loadMemoryShell(options = {}) {
  const { resetState = true } = options;
  const shell = getShellContent();
  if (!shell) return;

  const loadToken = ++memoryLoadToken;

  if (resetState) {
    m6State = {
      tab: "observations",
      scopeFilter: null,
      selectedObsId: null,
      stats: null,
      scopes: [],
      listData: null,
      detailData: null,
      detailLoading: false,
    };
  }

  shell.innerHTML = renderMemoryShellLoading();

  try {
    const [stats, scopes, listData] = await Promise.all([
      fetchMemoryShellStats(),
      fetchMemoryShellScopes(),
      fetchMemoryShellObservations({ limit: 50, offset: 0 }),
    ]);

    if (loadToken !== memoryLoadToken || normalizeAppView(getState("view")) !== MEMORY_VIEW) {
      return;
    }

    m6State.stats = stats;
    m6State.scopes = Array.isArray(scopes) ? scopes : [];
    m6State.listData = listData;

    renderM6Shell(shell);
  } catch (error) {
    if (loadToken !== memoryLoadToken || normalizeAppView(getState("view")) !== MEMORY_VIEW) {
      return;
    }
    shell.innerHTML = renderMemoryShellError(error?.message || "The memory surface could not load.");
    console.error("Failed to load memory shell:", error);
  }
}

// ── M6 render functions ──────────────────────────────────────────────────────

function renderM6Shell(shell) {
  const stats = m6State.stats || {};
  const totalDrawers = stats.total_drawers ?? 0;
  const totalObs = stats.total_observations ?? 0;
  const totalEvidence = stats.total_evidence_links ?? 0;
  const scopeCount = stats.scope_count ?? 0;

  const scopes = m6State.scopes || [];
  const listData = m6State.listData || { observations: [], drawers: [], total: 0, offset: 0 };
  const items = m6State.tab === "observations"
    ? (listData.observations || [])
    : (listData.drawers || []);
  const total = listData.total ?? 0;

  const scopeFilterOptions = scopes.map((s) => {
    const val = `${escapeHtml(s.scope_kind)}:${escapeHtml(s.scope_id)}`;
    const sel = m6State.scopeFilter &&
      m6State.scopeFilter.scope_kind === s.scope_kind &&
      m6State.scopeFilter.scope_id === s.scope_id ? " selected" : "";
    return `<option value="${val}"${sel}>${escapeHtml(s.scope_kind)} · ${escapeHtml(s.scope_id)} (${s.drawer_count}d / ${s.observation_count}o)</option>`;
  }).join("");

  const activeScope = m6State.scopeFilter
    ? `${m6State.scopeFilter.scope_kind} · ${m6State.scopeFilter.scope_id}`
    : "All";

  shell.innerHTML = `
    <section class="shell-hero memory-shell-hero">
      <div class="shell-eyebrow">Memory</div>
      <h2>Memory</h2>
      <div class="memory-shell-summary">
        <span class="memory-shell-summary-chip">${totalDrawers} drawers</span>
        <span class="memory-shell-summary-chip">${totalObs} observations</span>
        <span class="memory-shell-summary-chip">${totalEvidence} evidence links</span>
        <span class="memory-shell-summary-chip">${scopeCount} scopes</span>
      </div>
    </section>
    <div class="m6-shell-toolbar">
      <div class="m6-shell-tabs">
        <button type="button" class="m6-tab-btn${m6State.tab === "observations" ? " active" : ""}" data-m6-action="tab" data-m6-tab="observations">Observations</button>
        <button type="button" class="m6-tab-btn${m6State.tab === "drawers" ? " active" : ""}" data-m6-action="tab" data-m6-tab="drawers">Drawers</button>
      </div>
      <div class="m6-shell-scope-filter">
        <label class="m6-scope-label">Scope</label>
        <select class="m6-scope-select" data-m6-action="scope-filter">
          <option value=""${m6State.scopeFilter ? "" : " selected"}>All scopes</option>
          ${scopeFilterOptions}
        </select>
      </div>
    </div>
    <section class="m6-shell-layout">
      <article class="memory-shell-panel m6-list-panel">
        ${renderM6ListPanel(items, total)}
      </article>
      <article class="memory-shell-panel m6-detail-panel">
        ${renderM6DetailPanel()}
      </article>
    </section>
  `;

  shell.querySelectorAll("[data-m6-action]").forEach((el) => {
    el.addEventListener("click", handleM6Action);
    if (el.tagName === "SELECT") {
      el.removeEventListener("click", handleM6Action);
      el.addEventListener("change", handleM6Action);
    }
  });
}

function renderM6ListPanel(items, total) {
  if (!items || items.length === 0) {
    return `
      <div class="memory-shell-detail-empty">
        <strong>Memory is still populating</strong>
        <span>New observations will appear here as agents run and signals qualify.</span>
      </div>
    `;
  }
  const rows = items.map((item) => {
    const isObs = m6State.tab === "observations";
    const preview = escapeHtml((item.content_preview || "").slice(0, 120));
    const scope = `${escapeHtml(item.scope_kind)} · ${escapeHtml(item.scope_id)}`;
    const ts = item.created_at ? new Date(item.created_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
    const active = isObs && m6State.selectedObsId === item.id ? " m6-list-row-active" : "";
    const sup = isObs && item.superseded_by ? `<span class="m6-superseded-badge">superseded</span>` : "";
    return `
      <button type="button" class="m6-list-row${active}" data-m6-action="select" data-m6-id="${item.id}">
        <div class="m6-list-row-scope">${scope}${sup}</div>
        <div class="m6-list-row-preview">${preview}</div>
        <div class="m6-list-row-meta">${ts}</div>
      </button>
    `;
  }).join("");
  const countLine = total > items.length
    ? `<div class="m6-list-count">Showing ${items.length} of ${total}</div>`
    : `<div class="m6-list-count">${total} total</div>`;
  return `<div class="m6-list-rows">${rows}</div>${countLine}`;
}

function renderM6DetailPanel() {
  if (m6State.detailLoading) {
    return `<div class="memory-shell-detail-empty"><span>Loading…</span></div>`;
  }
  if (!m6State.detailData) {
    return `
      <div class="memory-shell-detail-empty">
        <strong>Select an observation</strong>
        <span>Click any row to see the full content and evidence chain.</span>
      </div>
    `;
  }
  const { observation: obs, evidence } = m6State.detailData;
  const ts = obs.created_at ? new Date(obs.created_at).toLocaleString() : "";
  const evRows = (evidence || []).map((ev) => {
    const preview = escapeHtml(ev.source_preview || "");
    return `
      <div class="m6-evidence-row">
        <div class="m6-evidence-kind">${escapeHtml(ev.source_kind)} #${ev.source_id}</div>
        ${preview ? `<div class="m6-evidence-preview">${preview}</div>` : ""}
      </div>
    `;
  }).join("");
  const supBadge = obs.superseded_by
    ? `<div class="m6-detail-superseded">Superseded by observation #${obs.superseded_by}</div>`
    : "";
  return `
    <div class="memory-shell-detail-head">
      <div class="memory-shell-detail-eyebrow">Observation #${obs.id}</div>
      <p class="m6-detail-scope">${escapeHtml(obs.scope_kind)} · ${escapeHtml(obs.scope_id)}</p>
      <p class="m6-detail-ts">${ts}</p>
    </div>
    <div class="memory-shell-detail-quote">
      <div class="memory-shell-detail-quote-mark">"</div>
      <div>${escapeHtml(obs.content || "")}</div>
    </div>
    ${supBadge}
    <div class="m6-evidence-section">
      <div class="m6-evidence-label">Backed by (${(evidence || []).length})</div>
      ${evRows || "<div class='m6-evidence-empty'>No evidence links yet.</div>"}
    </div>
  `;
}

async function handleM6Action(event) {
  const el = event.currentTarget;
  const action = el.dataset.m6Action;

  if (action === "tab") {
    const tab = el.dataset.m6Tab;
    if (tab === m6State.tab) return;
    m6State.tab = tab;
    m6State.selectedObsId = null;
    m6State.detailData = null;
    const scf = m6State.scopeFilter;
    try {
      const listData = tab === "observations"
        ? await fetchMemoryShellObservations({ scopeKind: scf?.scope_kind, scopeId: scf?.scope_id })
        : await fetchMemoryShellDrawers({ scopeKind: scf?.scope_kind, scopeId: scf?.scope_id });
      m6State.listData = listData;
    } catch (err) {
      console.error("M6 tab switch failed:", err);
    }
    const shell = getShellContent();
    if (shell) renderM6Shell(shell);
    return;
  }

  if (action === "scope-filter") {
    const val = el.value;
    if (!val) {
      m6State.scopeFilter = null;
    } else {
      const [sk, ...rest] = val.split(":");
      m6State.scopeFilter = { scope_kind: sk, scope_id: rest.join(":") };
    }
    m6State.selectedObsId = null;
    m6State.detailData = null;
    const scf = m6State.scopeFilter;
    try {
      const listData = m6State.tab === "observations"
        ? await fetchMemoryShellObservations({ scopeKind: scf?.scope_kind, scopeId: scf?.scope_id })
        : await fetchMemoryShellDrawers({ scopeKind: scf?.scope_kind, scopeId: scf?.scope_id });
      m6State.listData = listData;
    } catch (err) {
      console.error("M6 scope filter failed:", err);
    }
    const shell = getShellContent();
    if (shell) renderM6Shell(shell);
    return;
  }

  if (action === "select" && m6State.tab === "observations") {
    const obsId = parseInt(el.dataset.m6Id, 10);
    if (!obsId) return;
    m6State.selectedObsId = obsId;
    m6State.detailLoading = true;
    const shell = getShellContent();
    if (shell) {
      const detail = shell.querySelector(".m6-detail-panel");
      if (detail) detail.innerHTML = renderM6DetailPanel();
    }
    try {
      m6State.detailData = await fetchMemoryShellObservationDetail(obsId);
    } catch (err) {
      console.error("M6 observation detail failed:", err);
      m6State.detailData = null;
    }
    m6State.detailLoading = false;
    if (shell) renderM6Shell(shell);
    return;
  }
}

export function handleMemoryShellAction(button) {
  if (!button) return false;
  const action = button.dataset.memoryAction || "";
  if (!action) return false;

  if (action === "switch-section") {
    const section = button.dataset.memorySection || "review";
    if (!MEMORY_SECTION_DEFS.some((item) => item.id === section)) return true;
    memoryState.section = section;
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "switch-filter") {
    memoryState.filter = button.dataset.memoryFilter || "all";
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "switch-wing") {
    const scopeKind = button.dataset.wingScopeKind || "all";
    const scopeId = button.dataset.wingScopeId || "all";
    const label = button.dataset.wingLabel || "All";
    memoryState.wingFilter = scopeKind === "all" ? null : { scopeKind, scopeId, label };
    memoryState.roomFilter = null;
    memoryState.selectedBySection = new Map();
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "switch-room") {
    const type = button.dataset.roomType || "category";
    const value = button.dataset.roomValue || "";
    const label = button.dataset.roomLabel || value;
    memoryState.roomFilter = value ? { type, value, label } : null;
    memoryState.selectedBySection = new Map();
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "open-neighborhood-drawer") {
    const rowId = button.dataset.memoryRowId || "";
    if (!rowId || !memoryModel) return true;
    const row = memoryModel.rowById.get(rowId);
    if (!row) return true;
    void showNeighborhoodDrawer(row);
    return true;
  }

  if (action === "select-row") {
    const rowId = button.dataset.memoryRowId || "";
    if (!rowId) return true;
    memoryState.selectedBySection.set(memoryState.section, rowId);
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "refresh-memory") {
    void loadMemoryShell({ resetState: false });
    return true;
  }

  if (action === "export-memory-archive") {
    void (async () => {
      try {
        setMemoryArchiveStatus("Exporting portable memory archive…", "busy");
        const archive = await exportMemoryArchiveApi({ includeRetrievalLogs: false });
        downloadJson(archive, `artemis-memory-${Date.now()}.json`);
        setMemoryArchiveStatus("Portable memory archive downloaded.", "success");
      } catch (error) {
        console.error("Failed to export memory archive:", error);
        setMemoryArchiveStatus(error?.message || "Memory archive export failed.", "error");
      }
    })();
    return true;
  }

  if (action === "backup-memory-sqlite") {
    void (async () => {
      try {
        setMemoryArchiveStatus("Creating full SQLite backup…", "busy");
        const result = await createMemorySqliteBackupApi();
        setMemoryArchiveStatus(`SQLite backup created: ${result.path || "backup complete"}`, "success");
      } catch (error) {
        console.error("Failed to create memory backup:", error);
        setMemoryArchiveStatus(error?.message || "SQLite backup failed.", "error");
      }
    })();
    return true;
  }

  if (action === "choose-memory-archive") {
    chooseArchiveFile();
    return true;
  }

  if (action === "apply-memory-archive") {
    if (!memoryState.pendingArchive) {
      setMemoryArchiveStatus("Choose and validate an archive before importing.", "error");
      return true;
    }

    const confirmed = window.confirm("Import this memory archive?\n\nExisting drawers and observations are deduped by content hash and will not be overwritten.");
    if (!confirmed) return true;

    void (async () => {
      try {
        setMemoryArchiveStatus("Importing memory archive…", "busy");
        const result = await applyMemoryArchiveImportApi(memoryState.pendingArchive, { includeRetrievalLogs: false });
        const applied = result?.applied || {};
        memoryState.pendingArchive = null;
        setMemoryArchiveStatus(
          `Imported ${applied.drawersInserted || 0} drawers and ${applied.observationsInserted || 0} observations; skipped ${applied.drawersSkipped || 0} duplicate drawers and ${applied.observationsSkipped || 0} duplicate observations.`,
          "success",
        );
        await loadMemoryShell({ resetState: false });
      } catch (error) {
        console.error("Failed to import memory archive:", error);
        setMemoryArchiveStatus(error?.message || "Memory archive import failed.", "error");
      }
    })();
    return true;
  }

  if (action === "retag-memory") {
    const rowId = button.dataset.memoryRowId || "";
    const targetCategory = button.dataset.memoryTargetCategory || "";
    if (!rowId || !targetCategory || !memoryModel) return true;

    const row = memoryModel.rowById.get(rowId);
    if (!row) return true;

    void (async () => {
      try {
        await updateMemoryApi(row.id, row.content, targetCategory);
        await loadMemoryShell({ resetState: false });
      } catch (error) {
        console.error("Failed to update memory row:", error);
      }
    })();

    return true;
  }

  if (action === "delete-memory") {
    const rowId = button.dataset.memoryRowId || "";
    if (!rowId || !memoryModel) return true;
    const row = memoryModel.rowById.get(rowId);
    if (!row) return true;

    const confirmed = window.confirm(`Delete this memory?\n\n"${row.headline}"\n\nThis cannot be undone.`);
    if (!confirmed) return true;

    void (async () => {
      try {
        await deleteMemoryApi(row.id);
        await loadMemoryShell({ resetState: false });
      } catch (error) {
        console.error("Failed to delete memory row:", error);
      }
    })();

    return true;
  }

  if (action === "promote-to-skill") {
    const rowId = button.dataset.memoryRowId || "";
    if (!rowId || !memoryModel) return true;
    const row = memoryModel.rowById.get(rowId);
    if (!row) return true;

    void (async () => {
      try {
        const name = (row.headline || row.content || "Memory skill").slice(0, 80).trim();
        const created = await createSkillApi({
          name,
          description: row.content,
          category: "convention",
          scope: "global",
          provider_compat: ["all"],
          when_to_use: "",
          body: row.content,
          status: "proposed",
          origin: "memory",
        });
        try {
          localStorage.setItem("artemis-ops-skill-tab", JSON.stringify("proposed"));
          localStorage.setItem("artemis-ops-selected-skill", JSON.stringify(String(created.id || "")));
        } catch { /* ignore storage errors */ }
        setState("view", "skills");
      } catch (err) {
        console.error("Failed to promote memory to skill:", err);
      }
    })();
    return true;
  }

  if (action === "toggle-add-form") {
    memoryState.addFormOpen = !memoryState.addFormOpen;
    memoryState.editingRowId = null;
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "cancel-add-form") {
    memoryState.addFormOpen = false;
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "save-add-form") {
    if (!memoryModel) return true;
    const content = document.querySelector("[data-memory-add-content]")?.value?.trim() || "";
    const category = document.querySelector("[data-memory-add-category]")?.value || "discovery";
    if (!content) {
      document.querySelector("[data-memory-add-content]")?.focus();
      return true;
    }

    void (async () => {
      try {
        await createMemoryApi(memoryModel.projectPath, category, content);
        await loadMemoryShell({ resetState: false });
      } catch (error) {
        console.error("Failed to create memory:", error);
      }
    })();

    return true;
  }

  if (action === "start-edit") {
    const rowId = button.dataset.memoryRowId || "";
    if (!rowId) return true;
    memoryState.editingRowId = rowId;
    memoryState.addFormOpen = false;
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "cancel-edit") {
    memoryState.editingRowId = null;
    renderCurrentMemoryShell();
    return true;
  }

  if (action === "save-edit") {
    const rowId = button.dataset.memoryRowId || "";
    if (!rowId || !memoryModel) return true;
    const row = memoryModel.rowById.get(rowId);
    if (!row) return true;

    const content = document.querySelector("[data-memory-edit-content]")?.value?.trim() || "";
    const category = document.querySelector("[data-memory-edit-category]")?.value || row.category;
    if (!content) {
      document.querySelector("[data-memory-edit-content]")?.focus();
      return true;
    }

    void (async () => {
      try {
        await updateMemoryApi(row.id, content, category);
        await loadMemoryShell({ resetState: false });
      } catch (error) {
        console.error("Failed to save memory edit:", error);
      }
    })();

    return true;
  }

  if (action === "optimize-memory") {
    if (!memoryModel) return true;
    const project = memoryModel.projectPath;

    void (async () => {
      const optimizeBtn = button;
      const originalText = optimizeBtn.textContent;
      optimizeBtn.disabled = true;
      optimizeBtn.textContent = "Analyzing…";

      try {
        const result = await optimizeMemoryApi(project);
        const preview = result?.preview;
        if (!preview) throw new Error("No preview returned");
        showOptimizePreviewModal(preview, project);
      } catch (error) {
        console.error("Optimize failed:", error);
      } finally {
        optimizeBtn.disabled = false;
        optimizeBtn.textContent = originalText;
      }
    })();

    return true;
  }

  if (action === "show-evidence-detail") {
    showEvidenceDetailModal({
      id: button.dataset.evidenceId || "",
      source_kind: button.dataset.evidenceSourceKind || "",
      source_id: button.dataset.evidenceSourceId || "",
      source_quote: button.dataset.evidenceSourceQuote || "",
      weight: button.dataset.evidenceWeight || "1.00",
    });
    return true;
  }

  return false;
}

function getShellContent() {
  return document.getElementById("app-shell-content");
}

async function loadEvidenceForObservation(observationId) {
  if (memoryState.evidenceByObs.has(observationId)) return;
  memoryState.evidenceByObs.set(observationId, { loading: true, rows: [], error: null });
  renderCurrentMemoryShell();
  try {
    const rows = await fetchMemoryEvidenceApi(observationId);
    memoryState.evidenceByObs.set(observationId, { loading: false, rows: Array.isArray(rows) ? rows : [], error: null });
  } catch (err) {
    memoryState.evidenceByObs.set(observationId, { loading: false, rows: [], error: err?.message || "Failed to load evidence" });
  }
  renderCurrentMemoryShell();
}

function getActiveProjectPath() {
  return $.projectSelect?.value || localStorage.getItem("artemis-cwd") || "";
}

function getProjectLabel() {
  const select = $.projectSelect;
  if (!select || !select.value) return "";
  const option = [...(select.options || [])].find((item) => item.value === select.value);
  return option?.textContent?.trim() || select.value;
}

function setMemoryArchiveStatus(message, tone = "idle") {
  memoryState.archiveStatus = { message, tone };
  renderCurrentMemoryShell();
}

function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function chooseArchiveFile() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "application/json,.json";
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (!file) return;
    void (async () => {
      try {
        setMemoryArchiveStatus(`Reading ${file.name}…`, "busy");
        const archive = JSON.parse(await file.text());
        const preview = await dryRunMemoryArchiveImportApi(archive);
        memoryState.pendingArchive = archive;
        const counts = preview?.counts || {};
        const conflicts = Array.isArray(preview?.conflicts) ? preview.conflicts.length : 0;
        setMemoryArchiveStatus(
          `Archive validated: ${counts.scopes || 0} scopes, ${counts.drawers || 0} drawers, ${counts.observations || 0} observations, ${counts.evidence || 0} evidence links. ${conflicts} duplicate row${conflicts === 1 ? "" : "s"} will be skipped.`,
          "success",
        );
      } catch (error) {
        memoryState.pendingArchive = null;
        console.error("Failed to validate memory archive:", error);
        setMemoryArchiveStatus(error?.message || "Memory archive validation failed.", "error");
      }
    })();
  }, { once: true });
  input.click();
}

function buildMemoryModel({
  memories = [],
  stats = {},
  projectPath = "",
  projectLabel = "",
  sessions = [],
  agents = [],
}) {
  const sessionsById = new Map((sessions || []).map((session) => [session.id, session]));
  const agentsById = new Map((agents || []).map((agent) => [agent.id, agent]));
  const normalizedRows = (Array.isArray(memories) ? memories : []).map((row) => normalizeMemoryRow(row, {
    projectPath,
    projectLabel,
    sessionsById,
    agentsById,
  }));

  const sectionRows = {
    review: normalizedRows.filter((row) => row.category === "warning" || row.category === "discovery"),
    knows: normalizedRows.filter((row) => row.category === "decision"),
    projects: normalizedRows.filter((row) => row.category !== "warning" && row.category !== "discovery"),
    working: normalizedRows.filter((row) => row.category === "discovery"),
    agents: normalizedRows.filter((row) => Boolean(row.sourceAgentId)),
    skills: normalizedRows.filter((row) => row.category === "convention"),
  };

  const countsByCategory = new Map((stats?.categories || []).map((item) => [item.category, Number(item.count || 0)]));
  const reviewCount = sectionRows.review.length;
  const decisionCount = countsByCategory.get("decision") || sectionRows.knows.length;
  const conventionCount = countsByCategory.get("convention") || sectionRows.skills.length;
  const discoveryCount = countsByCategory.get("discovery") || sectionRows.working.length;
  const warningCount = countsByCategory.get("warning") || 0;
  const totalCount = Number(stats?.total || normalizedRows.length || 0);

  return {
    projectPath,
    projectLabel: projectLabel || projectPath,
    totalCount,
    reviewCount,
    decisionCount,
    conventionCount,
    discoveryCount,
    warningCount,
    accessedToday: Number(stats?.accessed_today || 0),
    avgRelevance: Number(stats?.avg_relevance || 0),
    sectionRows,
    normalizedRows,
    rowById: new Map(normalizedRows.map((row) => [row.id, row])),
    countsByCategory,
  };
}

function normalizeMemoryRow(row, { projectPath, projectLabel, sessionsById, agentsById }) {
  const rawContent = String(row?.content || "").trim();
  const sourceSession = row?.source_session_id ? sessionsById.get(row.source_session_id) : null;
  const sourceAgent = row?.source_agent_id ? agentsById.get(row.source_agent_id) : null;
  const sourceLabel = sourceAgent?.title
    || sourceAgent?.name
    || sourceSession?.title
    || sourceSession?.summary
    || projectLabel
    || projectPath
    || "Workspace";
  const sourceKind = sourceAgent
    ? "agent"
    : sourceSession
      ? "session"
      : "project";
  const bucket = deriveMemoryBucket(row, { sourceAgent, sourceSession });
  const ageLabel = formatRelativeTime(row?.created_at || row?.accessed_at || 0);
  const relevance = Number(row?.relevance_score || 0);
  const category = row?.category || "discovery";
  const headline = buildMemoryHeadline(rawContent);
  const preview = buildMemoryPreview(rawContent);

  return {
    id: String(row?.id || ""),
    content: rawContent,
    headline,
    preview,
    category,
    categoryLabel: CATEGORY_LABELS[category] || category,
    bucket,
    bucketLabel: getBucketLabel(bucket),
    projectPath: row?.project_path || projectPath || "",
    projectLabel: projectLabel || row?.project_path || "",
    sourceSessionId: row?.source_session_id || "",
    sourceAgentId: row?.source_agent_id || "",
    sourceLabel,
    sourceKind,
    sourceMark: buildSourceMark(sourceKind, sourceAgent, sourceSession),
    risk: deriveMemoryRisk(row),
    riskLabel: deriveMemoryRiskLabel(row),
    durability: deriveMemoryDurability(row),
    durabilityLabel: deriveMemoryDurabilityLabel(row),
    scopeLabel: buildScopeLabel(row, sourceKind, sourceLabel, projectLabel, projectPath),
    ageLabel,
    relevance,
    relevanceLabel: relevance ? relevance.toFixed(1) : "0.0",
    reviewReason: buildReviewReason(row, sourceKind),
    sectionHint: buildSectionHint(row),
    canPromote: category === "warning" || category === "discovery" || category === "convention",
    isReviewCandidate: category === "warning" || category === "discovery",
    actionsKey: bucket,
  };
}

function deriveMemoryBucket(row, { sourceAgent, sourceSession }) {
  if (row?.category === "convention") return "skills";
  if (sourceAgent) return "agents";
  if (row?.category === "decision") return "projects";
  if (sourceSession) return "artemis";
  return "artemis";
}

function getBucketLabel(bucket) {
  switch (bucket) {
    case "agents":
      return "Agents";
    case "projects":
      return "Projects";
    case "skills":
      return "Skills";
    default:
      return "Artemis";
  }
}

function buildSourceMark(sourceKind, sourceAgent, sourceSession) {
  if (sourceKind === "agent") {
    return String(sourceAgent?.mark || sourceAgent?.name || "A").slice(0, 3).toUpperCase();
  }
  if (sourceKind === "session") {
    return "S";
  }
  return "P";
}

function buildMemoryHeadline(content) {
  if (!content) return "Untitled memory";
  const normalized = content.replace(/\s+/g, " ").trim();
  if (normalized.length <= 84) return normalized;
  const firstSentence = normalized.split(/(?<=[.!?])\s+/)[0];
  if (firstSentence && firstSentence.length <= 84) return firstSentence;
  return `${normalized.slice(0, 81).trimEnd()}…`;
}

function buildMemoryPreview(content) {
  if (!content) return "No content";
  const normalized = content.replace(/\s+/g, " ").trim();
  if (normalized.length <= 124) return normalized;
  return `${normalized.slice(0, 121).trimEnd()}…`;
}

function deriveMemoryRisk(row) {
  if (row?.category === "warning") return "high";
  if (row?.source_agent_id) return row?.category === "decision" ? "low" : "med";
  if (row?.category === "discovery") return "med";
  return "low";
}

function deriveMemoryRiskLabel(row) {
  switch (deriveMemoryRisk(row)) {
    case "high":
      return "High risk";
    case "med":
      return "Review scope";
    default:
      return "Low risk";
  }
}

function deriveMemoryDurability(row) {
  if (row?.category === "warning" || row?.category === "discovery") return "temporary";
  return "durable";
}

function deriveMemoryDurabilityLabel(row) {
  return deriveMemoryDurability(row) === "temporary" ? "Temporary" : "Durable";
}

function buildScopeLabel(row, sourceKind, sourceLabel, projectLabel, projectPath) {
  if (sourceKind === "agent") return `Agent · ${sourceLabel}`;
  if (sourceKind === "session") return `Session · ${sourceLabel}`;
  if (row?.project_path) return `Project · ${projectLabel || projectPath || row.project_path}`;
  return "Workspace";
}

function buildReviewReason(row, sourceKind) {
  if (row?.category === "warning") {
    return "This memory is flagged as a warning and should be reviewed before it becomes durable.";
  }
  if (row?.category === "discovery") {
    return sourceKind === "agent"
      ? "An agent surfaced this as working memory. Review it before promoting it into a durable lane."
      : "This is still working memory and should be promoted or rerouted only after review.";
  }
  if (row?.category === "decision") {
    return "This is already approved memory and can be moved back into review if the scope changes.";
  }
  return "This memory participates in the shared governance layer.";
}

function buildSectionHint(row) {
  if (row?.category === "decision") return "Approved durable memory";
  if (row?.category === "convention") return "Promoted pattern or guardrail";
  if (row?.category === "warning") return "Needs attention";
  return "Working context";
}

function getSectionDef(sectionId) {
  return MEMORY_SECTION_DEFS.find((item) => item.id === sectionId) || MEMORY_SECTION_DEFS[0];
}

function getVisibleRows() {
  if (!memoryModel) return [];
  const sectionRows = memoryModel.sectionRows[memoryState.section] || [];
  let rows = sectionRows;

  const q = memoryState.searchQuery.toLowerCase().trim();
  if (q) {
    rows = rows.filter((row) => row.content.toLowerCase().includes(q));
  }

  const filter = memoryState.filter || "all";
  if (filter !== "all") {
    rows = rows.filter((row) => row.bucket === filter);
  }

  if (memoryState.wingFilter) {
    const { scopeKind, scopeId } = memoryState.wingFilter;
    rows = rows.filter((row) => rowMatchesWing(row, scopeKind, scopeId));
  }

  if (memoryState.roomFilter) {
    const { type, value } = memoryState.roomFilter;
    if (type === "category") {
      rows = rows.filter((row) => row.category === value);
    }
  }

  return rows;
}

function rowMatchesWing(row, scopeKind, scopeId) {
  if (scopeKind === "project") return !row.sourceAgentId && row.projectPath === scopeId;
  if (scopeKind === "agent") return row.sourceAgentId === scopeId;
  return true;
}

function getSelectedRow(rows) {
  const section = memoryState.section;
  const storedId = memoryState.selectedBySection.get(section);
  if (storedId) {
    const storedRow = rows.find((row) => row.id === storedId);
    if (storedRow) return storedRow;
  }
  return rows[0] || null;
}

function renderCurrentMemoryShell() {
  const shell = getShellContent();
  if (!shell || !memoryModel) return;

  const sectionDef = getSectionDef(memoryState.section);
  const rows = getVisibleRows();
  const selectedRow = getSelectedRow(rows);
  if (selectedRow) {
    memoryState.selectedBySection.set(memoryState.section, selectedRow.id);
  }

  shell.innerHTML = renderMemoryShell(memoryModel, sectionDef, rows, selectedRow, memoryState.filter);
}

function renderMemoryShell(model, sectionDef, rows, selectedRow, filter) {
  const totalApproved = model.decisionCount + model.conventionCount;
  const summaryChips = [
    `${model.totalCount} total`,
    `${model.reviewCount} need review`,
    `${totalApproved} approved`,
    `${model.accessedToday} accessed today`,
  ];

  return `
    <section class="shell-hero memory-shell-hero">
      <div class="shell-eyebrow">Memory</div>
      <h2>Memory</h2>
      <p>Memory is the control center, knowledge base, and approval inbox for the selected project. Needs Review is the default landing surface.</p>
      <div class="memory-shell-summary">
        ${summaryChips.map((chip) => `<span class="memory-shell-summary-chip">${escapeHtml(chip)}</span>`).join("")}
      </div>
    </section>
    <section class="memory-shell-layout">
      ${renderMemoryNav(model)}
      ${renderMemoryQueue(model, sectionDef, rows, selectedRow, filter)}
      ${renderMemoryDetail(model, sectionDef, selectedRow)}
    </section>
  `;
}

function buildWings(normalizedRows) {
  const wings = [{ scopeKind: "all", scopeId: "all", label: "All", count: normalizedRows.length }];
  const seen = new Map();
  for (const row of normalizedRows) {
    // Project wing: all non-agent rows, keyed by projectPath
    const projectKey = `project:${row.projectPath}`;
    if (!seen.has(projectKey)) {
      seen.set(projectKey, { scopeKind: "project", scopeId: row.projectPath, label: "Project", count: 0 });
    }
    if (!row.sourceAgentId) seen.get(projectKey).count++;

    // Agent wings: one per unique agent
    if (row.sourceAgentId) {
      const agentKey = `agent:${row.sourceAgentId}`;
      if (!seen.has(agentKey)) {
        seen.set(agentKey, { scopeKind: "agent", scopeId: row.sourceAgentId, label: row.sourceLabel || row.sourceAgentId, count: 0 });
      }
      seen.get(agentKey).count++;
    }
  }
  wings.push(...seen.values());
  return wings;
}

function buildRooms(wingRows) {
  const counts = new Map();
  for (const row of wingRows) {
    counts.set(row.category, (counts.get(row.category) || 0) + 1);
  }
  const ORDER = ["warning", "discovery", "decision", "convention"];
  const LABELS = { warning: "Warning", discovery: "Discovery", decision: "Decision", convention: "Convention" };
  return ORDER.filter((c) => counts.has(c)).map((c) => ({
    type: "category",
    value: c,
    label: LABELS[c] || c,
    count: counts.get(c),
  }));
}

function renderMemoryNav(model) {
  const counts = {
    review: model.sectionRows.review.length,
    knows: model.sectionRows.knows.length,
    projects: model.sectionRows.projects.length,
    working: model.sectionRows.working.length,
    agents: model.sectionRows.agents.length,
    skills: model.sectionRows.skills.length,
  };

  const wings = buildWings(model.normalizedRows);
  const activeWingKey = memoryState.wingFilter
    ? `${memoryState.wingFilter.scopeKind}:${memoryState.wingFilter.scopeId}`
    : "all:all";

  // Compute rooms for the active wing
  const wingRows = memoryState.wingFilter
    ? model.normalizedRows.filter((r) => rowMatchesWing(r, memoryState.wingFilter.scopeKind, memoryState.wingFilter.scopeId))
    : model.normalizedRows;
  const rooms = buildRooms(wingRows);
  const activeRoomValue = memoryState.roomFilter?.value || "";

  return `
    <article class="memory-shell-panel memory-shell-nav">
      <div class="memory-shell-nav-head">
        <div class="memory-shell-nav-eyebrow">Governance</div>
        <div class="memory-shell-nav-title">Memory sections</div>
        <div class="memory-shell-nav-sub">${escapeHtml(model.projectLabel || model.projectPath || "Selected project")} is the active scope.</div>
      </div>
      <div class="memory-shell-nav-list">
        ${MEMORY_SECTION_DEFS.map((section) => {
          const active = section.id === memoryState.section;
          const count = counts[section.id] || 0;
          return `
            <button
              type="button"
              class="memory-shell-nav-item${active ? " active" : ""}"
              data-memory-action="switch-section"
              data-memory-section="${escapeAttribute(section.id)}"
            >
              <span class="memory-shell-nav-item-main">
                <span class="memory-shell-nav-item-label">${escapeHtml(section.label)}</span>
                <span class="memory-shell-nav-item-desc">${escapeHtml(section.description)}</span>
              </span>
              <span class="memory-shell-nav-item-badge">${escapeHtml(String(count))}</span>
            </button>
          `;
        }).join("")}
      </div>
      <div class="memory-wings-tree">
        <div class="memory-wings-tree-label">Browse by scope</div>
        <div class="memory-wings-list">
          ${wings.map((wing) => {
            const wingKey = `${wing.scopeKind}:${wing.scopeId}`;
            const wingActive = wingKey === activeWingKey;
            const expanded = wingActive && wing.scopeKind !== "all";
            return `
              <div class="memory-wing-item${wingActive ? " active" : ""}">
                <button
                  type="button"
                  class="memory-wing-btn${wingActive ? " active" : ""}"
                  data-memory-action="switch-wing"
                  data-wing-scope-kind="${escapeAttribute(wing.scopeKind)}"
                  data-wing-scope-id="${escapeAttribute(wing.scopeId)}"
                  data-wing-label="${escapeAttribute(wing.label)}"
                >
                  <span class="memory-wing-label">${escapeHtml(wing.label)}</span>
                  <span class="memory-wing-count">${escapeHtml(String(wing.count))}</span>
                </button>
                ${expanded && rooms.length ? `
                  <div class="memory-rooms-list">
                    ${rooms.map((room) => {
                      const roomActive = room.value === activeRoomValue;
                      return `
                        <button
                          type="button"
                          class="memory-room-btn${roomActive ? " active" : ""}"
                          data-memory-action="switch-room"
                          data-room-type="${escapeAttribute(room.type)}"
                          data-room-value="${escapeAttribute(room.value)}"
                          data-room-label="${escapeAttribute(room.label)}"
                        >
                          <span class="memory-room-label">${escapeHtml(room.label)}</span>
                          <span class="memory-room-count">${escapeHtml(String(room.count))}</span>
                        </button>
                      `;
                    }).join("")}
                    ${activeRoomValue ? `
                      <button
                        type="button"
                        class="memory-room-btn memory-room-btn-clear"
                        data-memory-action="switch-room"
                        data-room-type="category"
                        data-room-value=""
                        data-room-label=""
                      >Clear filter</button>
                    ` : ""}
                  </div>
                ` : ""}
              </div>
            `;
          }).join("")}
        </div>
      </div>
      <div class="memory-shell-nav-foot">
        <div class="memory-shell-nav-foot-row">
          <span>Project</span>
          <strong>${escapeHtml(model.projectLabel || model.projectPath || "Select a project")}</strong>
        </div>
        <div class="memory-shell-nav-foot-row">
          <span>Review queue</span>
          <strong>${escapeHtml(String(model.reviewCount))}</strong>
        </div>
        <div class="memory-shell-nav-foot-row">
          <span>Avg relevance</span>
          <strong>${escapeHtml(model.avgRelevance ? model.avgRelevance.toFixed(1) : "0.0")}</strong>
        </div>
      </div>
    </article>
  `;
}

function renderMemoryQueue(model, sectionDef, rows, selectedRow, filter) {
  const filterCounts = countFilters(rows);
  const searchActive = Boolean(memoryState.searchQuery);
  return `
    <article class="memory-shell-panel memory-shell-queue">
      <div class="memory-shell-queue-head">
        <div>
          <div class="memory-shell-section-eyebrow">${escapeHtml(sectionDef.eyebrow)}</div>
          <h3>${escapeHtml(sectionDef.label)}</h3>
          <p>${escapeHtml(sectionDef.description)}</p>
        </div>
        <div class="memory-shell-queue-actions">
          <button
            type="button"
            class="memory-shell-refresh-btn"
            data-memory-action="refresh-memory"
          >Refresh</button>
          <button
            type="button"
            class="memory-shell-add-btn"
            data-memory-action="toggle-add-form"
          >${memoryState.addFormOpen ? "Cancel" : "+ Add"}</button>
          <button
            type="button"
            class="memory-shell-optimize-btn"
            data-memory-action="optimize-memory"
          >Optimize</button>
        </div>
      </div>
      ${renderMemoryArchiveAdmin()}
      <div class="memory-shell-search-bar">
        <input
          type="search"
          class="memory-shell-search-input"
          data-memory-search-input="1"
          placeholder="Search ${escapeAttribute(sectionDef.label)}…"
          value="${escapeAttribute(memoryState.searchQuery)}"
          autocomplete="off"
        />
        ${searchActive ? `<span class="memory-shell-search-hint">${escapeHtml(String(rows.length))} result${rows.length === 1 ? "" : "s"}</span>` : ""}
      </div>
      <div class="memory-shell-filter-bar" role="tablist" aria-label="Memory buckets">
        ${MEMORY_FILTERS.map((item) => {
          const active = item.id === filter;
          const count = filterCounts[item.id] || 0;
          return `
            <button
              type="button"
              class="memory-shell-filter-btn${active ? " active" : ""}"
              data-memory-action="switch-filter"
              data-memory-filter="${escapeAttribute(item.id)}"
            >
              <span>${escapeHtml(item.label)}</span>
              <span class="memory-shell-filter-count">${escapeHtml(String(count))}</span>
            </button>
          `;
        }).join("")}
      </div>
      <div class="memory-shell-list">
        ${rows.length
          ? rows.map((row) => renderMemoryRow(row, row.id === selectedRow?.id)).join("")
          : renderMemoryEmpty(sectionDef, filter)}
      </div>
    </article>
  `;
}

function renderMemoryArchiveAdmin() {
  const status = memoryState.archiveStatus;
  return `
    <div class="memory-shell-archive-card">
      <div class="memory-shell-archive-copy">
        <span>Archive + Restore</span>
        <small>Lossless drawers and evidence are preserved. Imports dry-run before apply.</small>
      </div>
      <div class="memory-shell-archive-actions">
        <button type="button" class="memory-shell-archive-btn" data-memory-action="export-memory-archive">Export JSON</button>
        <button type="button" class="memory-shell-archive-btn" data-memory-action="backup-memory-sqlite">SQLite backup</button>
        <button type="button" class="memory-shell-archive-btn" data-memory-action="choose-memory-archive">Validate import</button>
        <button
          type="button"
          class="memory-shell-archive-btn memory-shell-archive-btn-primary"
          data-memory-action="apply-memory-archive"
          ${memoryState.pendingArchive ? "" : "disabled"}
        >Apply import</button>
      </div>
      ${status ? `<div class="memory-shell-archive-status memory-shell-archive-status-${escapeAttribute(status.tone || "idle")}">${escapeHtml(status.message)}</div>` : ""}
    </div>
  `;
}

function renderMemoryRow(row, active) {
  return `
    <button
      type="button"
      class="memory-shell-row${active ? " active" : ""}"
      data-memory-action="select-row"
      data-memory-row-id="${escapeAttribute(row.id)}"
    >
      <div class="memory-shell-row-main">
        <div class="memory-shell-row-topline">
          <span class="memory-shell-chip memory-shell-chip-${escapeAttribute(row.bucket)}">${escapeHtml(row.bucketLabel)}</span>
          <span class="memory-shell-row-age">${escapeHtml(row.ageLabel)}</span>
        </div>
        <div class="memory-shell-row-title">${escapeHtml(row.headline)}</div>
        <div class="memory-shell-row-preview">${escapeHtml(row.preview)}</div>
        <div class="memory-shell-row-meta">
          <span>${escapeHtml(row.categoryLabel)}</span>
          <span>•</span>
          <span>${escapeHtml(row.scopeLabel)}</span>
          <span>•</span>
          <span>${escapeHtml(row.sourceLabel)}</span>
        </div>
      </div>
      <div class="memory-shell-row-side">
        <span class="memory-shell-row-risk memory-shell-row-risk-${escapeAttribute(row.risk)}">${escapeHtml(row.riskLabel)}</span>
        <span class="memory-shell-row-source">${escapeHtml(row.sourceMark)}</span>
      </div>
    </button>
  `;
}

function renderMemoryEmpty(sectionDef, filter) {
  const filterLabel = MEMORY_FILTERS.find((item) => item.id === filter)?.label || "All";
  return `
    <div class="memory-shell-empty">
      <strong>${escapeHtml(sectionDef.emptyTitle)}</strong>
      <span>${escapeHtml(sectionDef.emptyCopy)}</span>
      ${filter === "all" ? "" : `<small>Current filter: ${escapeHtml(filterLabel)}</small>`}
    </div>
  `;
}

function renderMemoryDetail(model, sectionDef, selectedRow) {
  if (memoryState.addFormOpen) {
    return renderAddForm();
  }

  if (!selectedRow) {
    return `
      <article class="memory-shell-panel memory-shell-detail">
        <div class="memory-shell-detail-empty">
          <strong>${escapeHtml(sectionDef.emptyTitle)}</strong>
          <span>${escapeHtml(sectionDef.emptyCopy)}</span>
        </div>
      </article>
    `;
  }

  if (memoryState.editingRowId === selectedRow.id) {
    return renderEditForm(selectedRow);
  }

  const actions = buildMemoryActions(sectionDef.id, selectedRow);

  return `
    <article class="memory-shell-panel memory-shell-detail">
      <div class="memory-shell-detail-head">
        <div>
          <div class="memory-shell-detail-eyebrow">${escapeHtml(sectionDef.eyebrow)}</div>
          <h3>${escapeHtml(selectedRow.headline)}</h3>
          <p>${escapeHtml(selectedRow.reviewReason)}</p>
        </div>
        <span class="memory-shell-detail-badge memory-shell-detail-badge-${escapeAttribute(selectedRow.bucket)}">
          ${escapeHtml(selectedRow.bucketLabel)}
        </span>
      </div>

      <div class="memory-shell-detail-quote">
        <div class="memory-shell-detail-quote-mark">"</div>
        <div>${escapeHtml(selectedRow.content || selectedRow.preview)}</div>
      </div>

      <div class="memory-shell-detail-meta">
        <div class="memory-shell-detail-meta-row">
          <span>Category</span>
          <strong>${escapeHtml(selectedRow.categoryLabel)}</strong>
        </div>
        <div class="memory-shell-detail-meta-row">
          <span>Scope</span>
          <strong>${escapeHtml(selectedRow.scopeLabel)}</strong>
        </div>
        <div class="memory-shell-detail-meta-row">
          <span>Source</span>
          <strong>${escapeHtml(selectedRow.sourceLabel)}</strong>
        </div>
        <div class="memory-shell-detail-meta-row">
          <span>Durability</span>
          <strong>${escapeHtml(selectedRow.durabilityLabel)}</strong>
        </div>
        <div class="memory-shell-detail-meta-row">
          <span>Age</span>
          <strong>${escapeHtml(selectedRow.ageLabel)}</strong>
        </div>
        <div class="memory-shell-detail-meta-row">
          <span>Relevance</span>
          <strong>${escapeHtml(selectedRow.relevanceLabel)}</strong>
        </div>
      </div>

      <div class="memory-shell-detail-body">
        <div class="memory-shell-detail-copy">
          <div class="memory-shell-detail-copy-label">Why it surfaced</div>
          <p>${escapeHtml(selectedRow.reviewReason)}</p>
        </div>
        <div class="memory-shell-detail-copy">
          <div class="memory-shell-detail-copy-label">Shared primitive</div>
          <p>${escapeHtml(selectedRow.sectionHint)}. Later memory views will reuse the same row model, filters, and promotion actions.</p>
        </div>
      </div>

      ${renderEvidenceSection(selectedRow)}

      <div class="memory-shell-actions">
        ${actions.map((action) => `
          <button
            type="button"
            class="memory-shell-action-btn memory-shell-action-btn-${escapeAttribute(action.tone)}"
            data-memory-action="${action.promote ? "promote-to-skill" : "retag-memory"}"
            data-memory-row-id="${escapeAttribute(selectedRow.id)}"
            ${!action.promote ? `data-memory-target-category="${escapeAttribute(action.category)}"` : ""}
          >
            ${escapeHtml(action.label)}
          </button>
        `).join("")}
        <button
          type="button"
          class="memory-shell-action-btn memory-shell-action-btn-secondary"
          data-memory-action="start-edit"
          data-memory-row-id="${escapeAttribute(selectedRow.id)}"
        >Edit</button>
        <button
          type="button"
          class="memory-shell-action-btn memory-shell-action-btn-secondary"
          data-memory-action="open-neighborhood-drawer"
          data-memory-row-id="${escapeAttribute(selectedRow.id)}"
        >Entities</button>
        <button
          type="button"
          class="memory-shell-action-btn memory-shell-action-btn-danger"
          data-memory-action="delete-memory"
          data-memory-row-id="${escapeAttribute(selectedRow.id)}"
        >Delete</button>
      </div>
    </article>
  `;
}

function renderAddForm() {
  return `
    <article class="memory-shell-panel memory-shell-detail memory-shell-detail-form">
      <div class="memory-shell-detail-head">
        <div>
          <div class="memory-shell-detail-eyebrow">New memory</div>
          <h3>Add a memory</h3>
          <p>Manually record a fact, decision, convention, or warning for this project.</p>
        </div>
      </div>
      <div class="memory-shell-form">
        <label class="memory-shell-form-label">
          Category
          <select class="memory-shell-form-select" data-memory-add-category>
            ${CATEGORIES.map((c) => `<option value="${escapeAttribute(c)}">${escapeHtml(c)}</option>`).join("")}
          </select>
        </label>
        <label class="memory-shell-form-label">
          Content
          <textarea
            class="memory-shell-form-textarea"
            data-memory-add-content
            placeholder="What should Artemis remember about this project?"
            rows="5"
          ></textarea>
        </label>
        <div class="memory-shell-form-actions">
          <button
            type="button"
            class="memory-shell-action-btn memory-shell-action-btn-secondary"
            data-memory-action="cancel-add-form"
          >Cancel</button>
          <button
            type="button"
            class="memory-shell-action-btn memory-shell-action-btn-primary"
            data-memory-action="save-add-form"
          >Save memory</button>
        </div>
      </div>
    </article>
  `;
}

function renderEditForm(row) {
  return `
    <article class="memory-shell-panel memory-shell-detail memory-shell-detail-form">
      <div class="memory-shell-detail-head">
        <div>
          <div class="memory-shell-detail-eyebrow">Editing</div>
          <h3>${escapeHtml(row.headline)}</h3>
          <p>Update the content or category of this memory, then save.</p>
        </div>
        <span class="memory-shell-detail-badge memory-shell-detail-badge-${escapeAttribute(row.bucket)}">
          ${escapeHtml(row.bucketLabel)}
        </span>
      </div>
      <div class="memory-shell-form">
        <label class="memory-shell-form-label">
          Category
          <select class="memory-shell-form-select" data-memory-edit-category>
            ${CATEGORIES.map((c) => `<option value="${escapeAttribute(c)}"${c === row.category ? " selected" : ""}>${escapeHtml(c)}</option>`).join("")}
          </select>
        </label>
        <label class="memory-shell-form-label">
          Content
          <textarea
            class="memory-shell-form-textarea"
            data-memory-edit-content
            rows="5"
          >${escapeHtml(row.content)}</textarea>
        </label>
        <div class="memory-shell-form-actions">
          <button
            type="button"
            class="memory-shell-action-btn memory-shell-action-btn-secondary"
            data-memory-action="cancel-edit"
          >Cancel</button>
          <button
            type="button"
            class="memory-shell-action-btn memory-shell-action-btn-primary"
            data-memory-action="save-edit"
            data-memory-row-id="${escapeAttribute(row.id)}"
          >Save changes</button>
        </div>
      </div>
    </article>
  `;
}

function buildMemoryActions(sectionId, row) {
  switch (sectionId) {
    case "review":
      return [
        { label: "Approve to Artemis Knows", category: "decision", tone: "primary" },
        { label: "Keep in Working", category: "discovery", tone: "secondary" },
        { label: "Promote to Skills / Rules", category: "convention", tone: "secondary", promote: true },
      ];
    case "knows":
      return [
        { label: "Send back to Review", category: "warning", tone: "secondary" },
        { label: "Move to Working", category: "discovery", tone: "secondary" },
        { label: "Promote to Skills / Rules", category: "convention", tone: "primary", promote: true },
      ];
    case "projects":
      return [
        { label: "Approve to Artemis Knows", category: "decision", tone: "primary" },
        { label: "Move to Working", category: "discovery", tone: "secondary" },
        { label: "Promote to Skills / Rules", category: "convention", tone: "secondary", promote: true },
      ];
    case "working":
      return [
        { label: "Approve to Artemis Knows", category: "decision", tone: "primary" },
        { label: "Send to Review", category: "warning", tone: "secondary" },
        { label: "Promote to Skills / Rules", category: "convention", tone: "secondary", promote: true },
      ];
    case "agents":
      return [
        { label: "Promote to Skills / Rules", category: "convention", tone: "primary", promote: true },
        { label: "Send to Review", category: "warning", tone: "secondary" },
        { label: "Move to Working", category: "discovery", tone: "secondary" },
      ];
    case "skills":
      return [
        { label: "Approve to Artemis Knows", category: "decision", tone: "primary" },
        { label: "Send to Review", category: "warning", tone: "secondary" },
      ];
    default:
      return [
        { label: "Approve to Artemis Knows", category: "decision", tone: "primary" },
        { label: "Keep in Working", category: "discovery", tone: "secondary" },
      ];
  }
}

function countFilters(rows) {
  const counts = { all: rows.length, artemis: 0, agents: 0, projects: 0, skills: 0 };
  for (const row of rows) {
    counts[row.bucket] = (counts[row.bucket] || 0) + 1;
  }
  return counts;
}

function formatRelativeTime(timestampSeconds) {
  if (!timestampSeconds) return "just now";
  const diff = Math.max(0, Math.floor(Date.now() / 1000) - Number(timestampSeconds));
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(Number(timestampSeconds) * 1000).toLocaleDateString();
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("\n", "&#10;");
}

// ── Evidence drill-down ───────────────────────────────────────

function renderEvidenceSection(row) {
  const ev = memoryState.evidenceByObs.get(row.id);

  if (!ev) {
    void loadEvidenceForObservation(row.id);
    return `
      <div class="memory-evidence-section">
        <div class="memory-evidence-section-head">Evidence</div>
        <div class="memory-evidence-loading">Loading evidence…</div>
      </div>
    `;
  }

  if (ev.loading) {
    return `
      <div class="memory-evidence-section">
        <div class="memory-evidence-section-head">Evidence</div>
        <div class="memory-evidence-loading">Loading evidence…</div>
      </div>
    `;
  }

  if (ev.error) {
    return `
      <div class="memory-evidence-section">
        <div class="memory-evidence-section-head">Evidence</div>
        <div class="memory-evidence-error">${escapeHtml(ev.error)}</div>
      </div>
    `;
  }

  if (!ev.rows.length) {
    return `
      <div class="memory-evidence-section">
        <div class="memory-evidence-section-head">Evidence</div>
        <div class="memory-evidence-empty">No evidence linked to this observation.</div>
      </div>
    `;
  }

  return `
    <div class="memory-evidence-section">
      <div class="memory-evidence-section-head">
        Evidence
        <span class="memory-evidence-count">${escapeHtml(String(ev.rows.length))}</span>
      </div>
      <div class="memory-evidence-list">
        ${ev.rows.map((item) => renderEvidenceRow(item)).join("")}
      </div>
    </div>
  `;
}

function renderEvidenceRow(ev) {
  const quote = String(ev.source_quote || "").trim();
  const preview = quote.length > 120 ? `${quote.slice(0, 117)}…` : quote;
  const KIND_LABELS = { drawer: "Drawer", observation: "Observation", legacy_memory: "Legacy" };
  const kindLabel = KIND_LABELS[ev.source_kind] || String(ev.source_kind || "source");
  const weight = typeof ev.weight === "number" ? ev.weight.toFixed(2) : String(ev.weight || "1.00");

  return `
    <button
      type="button"
      class="memory-evidence-row"
      data-memory-action="show-evidence-detail"
      data-evidence-id="${escapeAttribute(String(ev.id || ""))}"
      data-evidence-source-kind="${escapeAttribute(ev.source_kind || "")}"
      data-evidence-source-id="${escapeAttribute(String(ev.source_id || ""))}"
      data-evidence-source-quote="${escapeAttribute(ev.source_quote || "")}"
      data-evidence-weight="${escapeAttribute(weight)}"
    >
      <span class="memory-evidence-kind memory-evidence-kind-${escapeAttribute(ev.source_kind || "source")}">${escapeHtml(kindLabel)}</span>
      <span class="memory-evidence-quote-preview">${quote ? escapeHtml(preview) : "<em>No quote captured</em>"}</span>
      <span class="memory-evidence-weight-badge">${escapeHtml(weight)}</span>
    </button>
  `;
}

function showEvidenceDetailModal(ev) {
  document.querySelector(".memory-evidence-detail-overlay")?.remove();

  const overlay = document.createElement("div");
  overlay.className = "memory-evidence-detail-overlay";

  const KIND_LABELS = { drawer: "Verbatim drawer", observation: "Consolidated observation", legacy_memory: "Legacy memory" };
  const kindLabel = KIND_LABELS[ev.source_kind] || String(ev.source_kind || "Source");
  const canLoadDrawer = ev.source_kind === "drawer" && ev.source_id;

  overlay.innerHTML = `
    <div class="memory-evidence-detail-modal">
      <div class="memory-evidence-detail-header">
        <h3>Evidence Source</h3>
        <button type="button" class="memory-evidence-detail-close" data-evidence-close>Close</button>
      </div>
      <div class="memory-evidence-detail-meta">
        <span class="memory-evidence-kind memory-evidence-kind-${escapeAttribute(ev.source_kind || "source")}">${escapeHtml(kindLabel)}</span>
        <span class="memory-evidence-detail-weight">Weight: ${escapeHtml(String(ev.weight))}</span>
      </div>
      <div class="memory-evidence-detail-body">
        <div class="memory-evidence-detail-label">Captured quote</div>
        <div class="memory-evidence-detail-quote">
          ${ev.source_quote
            ? escapeHtml(ev.source_quote)
            : '<em class="memory-evidence-detail-empty">No source quote captured for this evidence link.</em>'}
        </div>
      </div>
      ${canLoadDrawer ? `
        <div class="memory-evidence-detail-drawer-section">
          <button
            type="button"
            class="memory-shell-action-btn memory-shell-action-btn-secondary memory-evidence-load-drawer-btn"
            data-evidence-drawer-id="${escapeAttribute(String(ev.source_id))}"
          >Load full drawer</button>
          <div class="memory-evidence-drawer-content" hidden></div>
        </div>
      ` : ""}
    </div>
  `;

  overlay.querySelector("[data-evidence-close]").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

  const loadBtn = overlay.querySelector("[data-evidence-drawer-id]");
  if (loadBtn) {
    loadBtn.addEventListener("click", async () => {
      const drawerId = loadBtn.dataset.evidenceDrawerId;
      loadBtn.disabled = true;
      loadBtn.textContent = "Loading…";
      const container = overlay.querySelector(".memory-evidence-drawer-content");
      try {
        const drawer = await fetchMemoryDrawerApi(drawerId);
        container.hidden = false;
        container.innerHTML = `
          <div class="memory-evidence-detail-label">Full drawer (${escapeHtml(drawer.corpus_kind || "drawer")})</div>
          <div class="memory-evidence-detail-quote">${escapeHtml(drawer.content || "")}</div>
          <div class="memory-evidence-drawer-meta">
            <span>Captured: ${escapeHtml(drawer.captured_at ? new Date(Number(drawer.captured_at) * 1000).toLocaleString() : "unknown")}</span>
          </div>
        `;
        loadBtn.hidden = true;
      } catch (err) {
        container.hidden = false;
        container.textContent = err?.message || "Failed to load drawer.";
        loadBtn.disabled = false;
        loadBtn.textContent = "Load full drawer";
      }
    });
  }

  document.body.appendChild(overlay);
}

// ── Entity neighborhood drawer ──────────────────────────

const ENTITY_KIND_LABELS = {
  person: "Person", project: "Project", brand: "Brand",
  campaign: "Campaign", post: "Post", channel: "Channel", other: "Other",
};

const PREDICATE_LABELS = {
  works_on: "works on", owns: "owns", publishes_to: "publishes to",
  belongs_to: "belongs to", posted_on: "posted on", runs_campaign: "runs campaign",
  authored_by: "authored by", mentioned_with: "mentioned with", related_to: "related to",
};

async function showNeighborhoodDrawer(row) {
  document.querySelector(".memory-neighborhood-overlay")?.remove();

  const overlay = document.createElement("div");
  overlay.className = "memory-neighborhood-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-label", "Entity neighborhood");

  // Derive scope from row
  const scopeKind = row.sourceKind === "agent" ? "agent" : "project";
  const scopeId = row.sourceKind === "agent" ? row.sourceAgentId : row.projectPath;

  overlay.innerHTML = `
    <div class="memory-neighborhood-modal">
      <div class="memory-neighborhood-header">
        <h3>Entities</h3>
        <button type="button" class="memory-neighborhood-close" data-neighborhood-close>Close</button>
      </div>
      <div class="memory-neighborhood-sub">${escapeHtml(row.headline)}</div>
      <div class="memory-neighborhood-body">
        <div class="memory-neighborhood-loading">Loading entities…</div>
      </div>
    </div>
  `;

  overlay.querySelector("[data-neighborhood-close]").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);

  const body = overlay.querySelector(".memory-neighborhood-body");

  try {
    const result = await fetchMemoryEntitiesApi(scopeKind, scopeId, { limit: 20 });
    const entities = Array.isArray(result?.entities) ? result.entities : [];

    if (!entities.length) {
      body.innerHTML = `<div class="memory-neighborhood-empty">No entities extracted for this scope yet. Entities are populated automatically after observations are consolidated.</div>`;
      return;
    }

    body.innerHTML = `
      <div class="memory-neighborhood-list">
        ${entities.map((entity) => renderNeighborhoodEntity(entity)).join("")}
      </div>
    `;

    // Wire expand buttons
    body.querySelectorAll("[data-neighborhood-expand]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const entityId = btn.dataset.neighborhoodExpand;
        const neighborBox = body.querySelector(`[data-neighborhood-neighbors="${entityId}"]`);
        if (!neighborBox) return;
        if (neighborBox.dataset.loaded) {
          neighborBox.hidden = !neighborBox.hidden;
          btn.textContent = neighborBox.hidden ? "Neighbors →" : "Hide neighbors";
          return;
        }
        btn.disabled = true;
        btn.textContent = "Loading…";
        try {
          const nb = await fetchEntityNeighborhoodApi(Number(entityId));
          const neighbors = [...(nb?.neighbors || [])];
          neighborBox.dataset.loaded = "1";
          neighborBox.hidden = false;
          btn.textContent = "Hide neighbors";
          btn.disabled = false;
          neighborBox.innerHTML = neighbors.length
            ? neighbors.map((n) => `
                <div class="memory-neighbor-row">
                  <span class="memory-neighbor-predicate">${escapeHtml(PREDICATE_LABELS[n.predicate] || n.predicate)}</span>
                  <span class="memory-neighbor-name">${escapeHtml(n.canonical_name || n.name || "")}</span>
                  <span class="memory-neighbor-kind">${escapeHtml(ENTITY_KIND_LABELS[n.entity_kind] || n.entity_kind || "")}</span>
                </div>
              `).join("")
            : `<div class="memory-neighborhood-empty">No 1-hop neighbors.</div>`;
        } catch (err) {
          btn.disabled = false;
          btn.textContent = "Neighbors →";
          neighborBox.hidden = false;
          neighborBox.innerHTML = `<div class="memory-neighborhood-error">${escapeHtml(err?.message || "Failed to load neighbors.")}</div>`;
        }
      });
    });

    // Wire "filter to" buttons
    body.querySelectorAll("[data-neighborhood-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const entityId = btn.dataset.neighborhoodFilter;
        const entityName = btn.dataset.neighborhoodFilterName || "";
        memoryState.roomFilter = { type: "entity", value: entityId, label: entityName };
        memoryState.selectedBySection = new Map();
        renderCurrentMemoryShell();
        overlay.remove();
      });
    });
  } catch (err) {
    body.innerHTML = `<div class="memory-neighborhood-error">${escapeHtml(err?.message || "Failed to load entities.")}</div>`;
  }
}

function renderNeighborhoodEntity(entity) {
  const kindLabel = ENTITY_KIND_LABELS[entity.entity_kind] || entity.entity_kind || "other";
  return `
    <div class="memory-neighborhood-entity" data-entity-id="${escapeAttribute(String(entity.id))}">
      <div class="memory-neighborhood-entity-head">
        <span class="memory-neighborhood-kind-badge memory-neighborhood-kind-${escapeAttribute(entity.entity_kind || "other")}">${escapeHtml(kindLabel)}</span>
        <span class="memory-neighborhood-entity-name">${escapeHtml(entity.canonical_name || "")}</span>
        <span class="memory-neighborhood-mention-count">${escapeHtml(String(entity.mention_count || 0))} mention${entity.mention_count === 1 ? "" : "s"}</span>
      </div>
      <div class="memory-neighborhood-entity-actions">
        <button
          type="button"
          class="memory-neighborhood-expand-btn"
          data-neighborhood-expand="${escapeAttribute(String(entity.id))}"
        >Neighbors →</button>
        <button
          type="button"
          class="memory-neighborhood-filter-btn"
          data-neighborhood-filter="${escapeAttribute(String(entity.id))}"
          data-neighborhood-filter-name="${escapeAttribute(entity.canonical_name || "")}"
        >Filter to</button>
      </div>
      <div class="memory-neighbor-list" data-neighborhood-neighbors="${escapeAttribute(String(entity.id))}" hidden></div>
    </div>
  `;
}

// ── Optimize modal ───────────────────────────────────────

function showOptimizePreviewModal(preview, project) {
  document.querySelector(".memory-shell-optimize-overlay")?.remove();

  const overlay = document.createElement("div");
  overlay.className = "memory-shell-optimize-overlay";

  const beforeCount = preview.before ?? (preview.original?.length ?? 0);
  const afterCount = preview.after ?? (preview.optimized?.length ?? 0);

  overlay.innerHTML = `
    <div class="memory-shell-optimize-modal">
      <div class="memory-shell-optimize-header">
        <h3>Optimize Memories</h3>
        <span class="memory-shell-optimize-summary">${escapeHtml(String(preview.summary || ""))}</span>
      </div>
      <div class="memory-shell-optimize-stats">
        <span class="memory-shell-optimize-stat"><strong>${escapeHtml(String(beforeCount))}</strong> before</span>
        <span class="memory-shell-optimize-arrow">&rarr;</span>
        <span class="memory-shell-optimize-stat memory-shell-optimize-stat-success"><strong>${escapeHtml(String(afterCount))}</strong> after</span>
        ${preview.noiseRemoved ? `<span class="memory-shell-optimize-stat memory-shell-optimize-stat-warn">${escapeHtml(String(preview.noiseRemoved))} noise removed</span>` : ""}
      </div>
      <div class="memory-shell-optimize-columns">
        <div class="memory-shell-optimize-col">
          <h4>Before (${escapeHtml(String(preview.original?.length ?? beforeCount))})</h4>
          <div class="memory-shell-optimize-list">
            ${(preview.original || []).map((m) => `
              <div class="memory-shell-optimize-item memory-shell-optimize-item-removed">
                <span class="memory-shell-chip memory-shell-chip-${escapeAttribute(m.category || "discovery")}">${escapeHtml(m.category || "discovery")}</span>
                ${escapeHtml(m.content || "")}
              </div>
            `).join("")}
          </div>
        </div>
        <div class="memory-shell-optimize-col">
          <h4>After (${escapeHtml(String(preview.optimized?.length ?? 0))})</h4>
          <div class="memory-shell-optimize-list">
            ${(preview.optimized || []).length
              ? (preview.optimized || []).map((m) => `
                  <div class="memory-shell-optimize-item memory-shell-optimize-item-added">
                    <span class="memory-shell-chip memory-shell-chip-${escapeAttribute(m.category || "discovery")}">${escapeHtml(m.category || "discovery")}</span>
                    ${escapeHtml(m.content || "")}
                  </div>
                `).join("")
              : `<div class="memory-shell-optimize-empty">All memories were noise. Nothing to keep.</div>`
            }
          </div>
        </div>
      </div>
      <div class="memory-shell-optimize-footer">
        <button type="button" class="memory-shell-action-btn memory-shell-action-btn-secondary" data-optimize-cancel>Cancel</button>
        <button type="button" class="memory-shell-action-btn memory-shell-action-btn-primary" data-optimize-apply>Apply (${escapeHtml(String(afterCount))} memories)</button>
      </div>
    </div>
  `;

  overlay.querySelector("[data-optimize-cancel]").addEventListener("click", () => overlay.remove());
  overlay.querySelector("[data-optimize-apply]").addEventListener("click", async () => {
    const applyBtn = overlay.querySelector("[data-optimize-apply]");
    applyBtn.disabled = true;
    applyBtn.textContent = "Applying…";
    try {
      await applyOptimizationApi(project, preview.optimized || []);
      overlay.remove();
      await loadMemoryShell({ resetState: false });
    } catch (error) {
      console.error("Apply optimization failed:", error);
      applyBtn.disabled = false;
      applyBtn.textContent = `Apply (${afterCount} memories)`;
    }
  });

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });

  document.body.appendChild(overlay);
}

// ── Search input handler (called from home.js input delegation) ──

let _searchDebounceTimer = null;

export function handleMemoryShellInput(input) {
  if (!input?.dataset?.memorySearchInput) return false;
  clearTimeout(_searchDebounceTimer);
  const q = input.value || "";
  _searchDebounceTimer = setTimeout(() => {
    memoryState.searchQuery = q.trim();
    memoryState.editingRowId = null;
    memoryState.addFormOpen = false;
    renderCurrentMemoryShell();
    const restored = document.querySelector("[data-memory-search-input]");
    if (restored) {
      restored.value = q;
      restored.focus();
    }
  }, 300);
  return true;
}
