import { getState, setState } from '../core/store.js';
import { escapeHtml } from '../core/utils.js';
import {
  MARKETING_CAMPAIGNS_VIEW,
  WRITING_STUDIO_VIEW,
} from '../core/navigation.js';
import {
  listApprovalsApi, decideApprovalApi,
  fetchMarketingCampaignsApi,
  decideCampaignCandidateApi, promoteCampaignCandidateApi, reopenCampaignCandidateApi,
  getCampaignInitiationProposalApi, initiateCampaignApi,
  createCampaignWritingHandoffApi,
  listCampaignDeliverablesApi,
  assembleCampaignBriefApi, getCampaignBriefApi,
  listCampaignAssetLinksApi, createCampaignAssetLinkApi, deleteCampaignAssetLinkApi,
  listContentAssetsApi, createContentAssetApi,
  listCampaignRulesetsApi, getCampaignRulesetApi,
  listRulesetVersionsApi, activateRulesetVersionApi,
  listReasonCodesApi, createReasonCodeApi, patchReasonCodeApi, exportReasonCodesMarkdownApi,
  getTerritoryConfigApi, upsertTerritoryStateApi,
  getTierBandsApi, upsertTierBandsApi, recomputeTierBandsApi, getDistrictDataStatusApi,
  refreshDistrictDataApi,
  fetchAccountInfo,
  listSignalQueueApi, createSignalApi,
  approveSignalApi, rejectSignalApi, snoozeSignalApi, archiveSignalApi,
  qualifySignalApi,
  listScoutRunsApi, listScoutPackagesApi,
  listPipelinesApi,
} from '../core/api.js';
import {
  filterSignals,
  normalizeSignal,
  readCollapsedSignalGroups,
  readSignalGroupMode,
  renderSignalInboxTree,
  writeCollapsedSignalGroups,
  writeSignalGroupMode,
} from '../components/signal-tree.js';

// ── Storage keys ──────────────────────────────────────────────────────────
const MKT_CAMPAIGN_KEY = 'artemis-mkt-selected-campaign';
const MKT_WORKSPACE_TAB_KEY = 'artemis-mkt-workspace-tab';
// DIST4: localStorage key for the hide-unsupported-tiers toggle (default OFF)
const MKT_HIDE_UNSUPPORTED_KEY = 'artemis-mkt-hide-unsupported-tiers';
function readHideUnsupported() { try { return localStorage.getItem(MKT_HIDE_UNSUPPORTED_KEY) === 'true'; } catch { return false; } }
function writeHideUnsupported(v) { try { localStorage.setItem(MKT_HIDE_UNSUPPORTED_KEY, v ? 'true' : 'false'); } catch {} }

const MKT_SIGNAL_TREE_STATE = {
  signals: [],
  pipelineRuns: [],
  mode: 'state',
  sort: 'newest',
  query: '',
  filters: { urgencies: [], statuses: [], reasons: [], geographies: [] },
  selectedId: null,
  hideUnsupported: readHideUnsupported(),
};

const SP_SCOUTS = [
  "board_minutes", "federal_funding", "leadership_transition", "legislative",
  "linkedin_observer", "procurement", "regional_news", "starbridge_researcher", "state_doe",
];
// Canonical campaign families (single source of truth: josh_spec §3, slugified).
// Reconciled from the prior 4-slug + 5-label mix (#79/#80).
const SP_FAMILIES = ["obc", "dyslexia", "biliteracy", "hit", "general_growth"];
// Canonical urgency tiers (single source of truth: josh_spec §2 default
// urgencies + §4 suppress/boost ladder). Reconciled from the prior 3-slug
// (hot/standard/low) + 4-item (added enrichment) mix (#81).
const SP_URGENCIES = ["hot", "standard", "enrichment"];
let spState = { codes: [], domain: "", scout: "", showRetired: false, editing: null };

const US_STATES = [
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
  "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
  "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
  "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
  "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
];

// ── Deliverables cache ─────────────────────────────────────────────────────
// Keyed by campaignId. Populated lazily when the Assets tab opens for an API campaign.
const _deliverablesCache = new Map();

// ── Brief cache ────────────────────────────────────────────────────────────
// Keyed by campaignId → assembled brief object (or null if none assembled).
// null means "loaded, no brief". undefined means "not yet fetched".
const _briefCache = new Map();

// ── Asset links cache ──────────────────────────────────────────────────────
// Keyed by campaignId → array of linked asset rows.
// undefined means "not yet fetched". [] means loaded with no links.
const _assetLinksCache = new Map();

// ── Initiation proposal cache ──────────────────────────────────────────────
// Keyed by campaignId → full initiation proposal payload.
const _initiationProposalCache = new Map();

// ── Module-level campaign map ──────────────────────────────────────────────
function _campaignMapKey(id) {
  return String(id ?? '');
}

let _campaignMap = new Map();

// ── Helpers ───────────────────────────────────────────────────────────────

function esc(s) {
  return escapeHtml(String(s ?? ''));
}

function assetTypeIcon(type) {
  const t = String(type).toLowerCase();
  if (t.includes('email'))    return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="3"/><path d="m2 7 10 7 10-7"/></svg>`;
  if (t.includes('field') || t.includes('guide') || t.includes('pdf')) return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`;
  if (t.includes('social') || t.includes('linkedin')) return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>`;
  if (t.includes('landing')) return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`;
  if (t.includes('webinar') || t.includes('video')) return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polygon points="23,7 16,12 23,17"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>`;
  if (t.includes('case'))     return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`;
  if (t.includes('research'))  return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`;
  return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="3" y="3" width="18" height="18" rx="3"/></svg>`;
}

function assetIconColor(type) {
  const t = String(type).toLowerCase();
  if (t.includes('email'))                              return 'rgba(74,144,226,0.9)';
  if (t.includes('field') || t.includes('guide') || t.includes('pdf')) return 'rgba(122,158,142,0.9)';
  if (t.includes('social') || t.includes('linkedin')) return 'rgba(168,130,210,0.9)';
  if (t.includes('landing'))                           return 'rgba(212,137,26,0.9)';
  if (t.includes('webinar') || t.includes('video'))   return 'rgba(200,90,90,0.9)';
  if (t.includes('case'))                              return 'rgba(80,175,155,0.9)';
  if (t.includes('research'))                          return 'rgba(150,150,165,0.9)';
  return 'var(--text-dim)';
}

function statusDotClass(status) {
  const s = String(status).toLowerCase();
  if (s.includes('live') || s.includes('in play') || s.includes('shipped')) return 'mkt-list-dot-live';
  if (s.includes('review') || s.includes('validation') || s.includes('pending') || s.includes('gate') || s.includes('opportunity')) return 'mkt-list-dot-pending';
  if (s.includes('qualified') || s.includes('outreach') || s.includes('active')) return 'mkt-list-dot-active';
  return 'mkt-list-dot-draft';
}

function statusPillClass(status) {
  const map = {
    'In play': 'mkt-pill-live',
    'Live': 'mkt-pill-live',
    'shipped': 'mkt-pill-live',
    'approved': 'mkt-pill-approved',
    'in-outreach': 'mkt-pill-active',
    'qualified': 'mkt-pill-active',
    'Ready for opportunity review': 'mkt-pill-pending',
    'Human gate 1': 'mkt-pill-pending',
    'in-review': 'mkt-pill-pending',
    'Evidence validation': 'mkt-pill-pending',
    'Needs Ry validation': 'mkt-pill-pending',
    'draft': 'mkt-pill-draft',
    'warm': 'mkt-pill-warm',
    'cold': 'mkt-pill-cold',
    'pending': 'mkt-pill-pending',
  };
  return map[status] || 'mkt-pill-neutral';
}

function priorityBadge(priority) {
  if (priority === 'Live') return '<span class="mkt-badge mkt-badge-live">LIVE</span>';
  if (priority === 'P0') return '<span class="mkt-badge mkt-badge-p0">P0</span>';
  if (priority === 'P1') return '<span class="mkt-badge mkt-badge-p1">P1</span>';
  return `<span class="mkt-badge mkt-badge-neutral">${esc(priority)}</span>`;
}

function sparklineHtml(values) {
  if (!values || values.length === 0) {
    return '<span class="mkt-sparkline-empty">No data yet</span>';
  }
  const max = Math.max(...values, 1);
  const bars = values.map((v) => {
    const pct = Math.round((v / max) * 100);
    return `<span class="mkt-spark-bar" style="--h:${pct}%" title="${v}"></span>`;
  }).join('');
  return `<span class="mkt-sparkline" aria-hidden="true">${bars}</span>`;
}

function freshnessBadge(dateStr) {
  if (!dateStr) return '<span class="mkt-freshness mkt-freshness-unknown">—</span>';
  const days = Math.floor((Date.now() - new Date(dateStr).getTime()) / 86400000);
  if (days <= 7) return `<span class="mkt-freshness mkt-freshness-fresh">${days}d ago</span>`;
  if (days <= 30) return `<span class="mkt-freshness mkt-freshness-ok">${days}d ago</span>`;
  return `<span class="mkt-freshness mkt-freshness-stale">${days}d ago</span>`;
}

function _normalizeCampaignCandidate(raw) {
  return {
    id: raw.id,
    name: raw.name || '',
    objective: raw.objective || '',
    state: raw.state || raw.decisionState || 'created',
    family: raw.family || raw.campaignFamily || '',
    initiatedAt: raw.initiatedAt || null,
    initiatedBy: raw.initiatedBy || null,
    signalClusterCount: Number(raw.signalClusterCount ?? raw.clusterCount ?? 0),
    clusterCount: Number(raw.clusterCount ?? raw.signalClusterCount ?? 0),
    primarySignalId: raw.primarySignalId ?? null,
    primarySignalState: raw.primarySignalState || null,
    primarySignalUrgencyTier: raw.primarySignalUrgencyTier || null,
    primarySignalHeadline: raw.primarySignalHeadline || null,
    decisionState: raw.state || raw.decisionState || 'created',
    workspaceState: raw.workspaceState || null,
    rulesetVersionAtQualification: raw.rulesetVersionAtQualification || null,
    initiationProposalJson: raw.initiationProposalJson || null,
    targetScopeJson: raw.targetScopeJson || null,
    deliverableTypesJson: raw.deliverableTypesJson || null,
    sourceSignalId: raw.sourceSignalId || null,
    predecessorId: raw.predecessorId || null,
    linkedDraftCount: Number(raw.linkedDraftCount || 0),
    latestDraftId: raw.latestDraftId ? Number(raw.latestDraftId) : null,
    latestDraftTitle: raw.latestDraftTitle || null,
    history: Array.isArray(raw.history) ? raw.history : [],
    kpis: raw.kpis || null,
    _fromApi: true,
  };
}

function _syncCampaignMap(campaigns = []) {
  _campaignMap = new Map((campaigns || []).map((campaign) => [_campaignMapKey(campaign.id), campaign]));
}

function _campaignLifecycleLabel(campaign) {
  return campaign?.initiatedAt ? 'Initiated' : 'Proposed';
}

function _campaignLifecycleBadge(campaign) {
  const label = _campaignLifecycleLabel(campaign);
  const pill = campaign?.initiatedAt ? 'mkt-badge-live' : 'mkt-badge-neutral';
  return `<span class="mkt-badge ${pill}">${label}</span>`;
}

function _campaignDecisionPillClass(state) {
  const s = String(state || '').toLowerCase();
  if (s.includes('approved') || s.includes('active') || s.includes('initiated')) return 'mkt-pill-live';
  if (s.includes('rejected')) return 'mkt-pill-cold';
  if (s.includes('monitor')) return 'mkt-pill-active';
  if (s.includes('change') || s.includes('pending') || s.includes('review') || s.includes('created')) return 'mkt-pill-pending';
  return 'mkt-pill-neutral';
}

function _campaignDecisionDotClass(state) {
  const s = String(state || '').toLowerCase();
  if (s.includes('approved') || s.includes('active') || s.includes('initiated') || s.includes('live')) return 'mkt-list-dot-live';
  if (s.includes('monitor')) return 'mkt-list-dot-active';
  if (s.includes('rejected')) return 'mkt-list-dot-draft';
  if (s.includes('change') || s.includes('pending') || s.includes('review') || s.includes('created') || s.includes('proposal')) return 'mkt-list-dot-pending';
  return 'mkt-list-dot-draft';
}

function _campaignClusterCount(campaign) {
  return Number(campaign?.signalClusterCount ?? campaign?.clusterCount ?? 0);
}

// ── Dashboard view ────────────────────────────────────────────────────────

export function renderMarketingDashboard(
  campaigns = [],
  pendingApprovalCount = 0,
  signalsCount = 0,
) {
  const tiles = campaigns.map((c) => {
    const clusterCount = _campaignClusterCount(c);
    const primaryState = c.primarySignalState || '—';
    const primaryTier = c.primarySignalUrgencyTier || '—';
    const objective = c.objective || 'No objective recorded yet';
    return `
      <article class="mkt-campaign-tile" data-mkt-tile-id="${esc(c.id)}" role="button" tabindex="0"
               aria-label="Open ${esc(c.name)} workspace">
        <div class="mkt-tile-head">
          <div class="mkt-tile-title-row">
            ${_campaignLifecycleBadge(c)}
            <h3 class="mkt-tile-name">${esc(c.name)}</h3>
          </div>
          <div class="mkt-tile-status-badges">
            <span class="mkt-pill ${_campaignDecisionPillClass(c.state)}">${esc(c.state || 'created')}</span>
            <span class="mkt-pill ${c.initiatedAt ? 'mkt-pill-live' : 'mkt-pill-neutral'}">${c.initiatedAt ? 'Initiated' : 'Proposed'}</span>
          </div>
        </div>
        <div class="mkt-tile-family">${esc(c.family)}</div>
        <p class="mkt-tile-objective">${esc(objective)}</p>
        <div class="mkt-tile-kpi-row">
          <div class="mkt-tile-kpi">
            <span class="mkt-kpi-value">${clusterCount}</span>
            <span class="mkt-kpi-label">signals</span>
          </div>
          <div class="mkt-tile-kpi">
            <span class="mkt-kpi-value">${esc(primaryState)}</span>
            <span class="mkt-kpi-label">state</span>
          </div>
          <div class="mkt-tile-kpi">
            <span class="mkt-kpi-value">${esc(primaryTier)}</span>
            <span class="mkt-kpi-label">tier</span>
          </div>
          <div class="mkt-tile-kpi">
            <span class="mkt-kpi-value">${c.initiatedAt ? 'yes' : 'no'}</span>
            <span class="mkt-kpi-label">initiated</span>
          </div>
        </div>
        <div class="mkt-tile-footer">
          <span class="mkt-tile-owner">${c.initiatedAt ? esc(new Date(c.initiatedAt).toLocaleDateString()) : 'Not initiated yet'}</span>
          <button class="mkt-tile-open-btn" data-mkt-open-campaign="${esc(c.id)}" type="button">Open workspace →</button>
        </div>
      </article>
    `;
  }).join('');

  const liveCampaignCount = campaigns.filter((c) => Boolean(c.initiatedAt)).length;
  const hasCampaigns = campaigns.length > 0;

  return `
    <div class="mkt-hero">
      <h2 class="mkt-hero-title">Marketing Campaign OS</h2>
      <p class="mkt-hero-sub">${campaigns.length} campaign${campaigns.length !== 1 ? 's' : ''} · ${pendingApprovalCount} pending</p>
    </div>
    <section class="mkt-section">
      <div class="mkt-section-header">
        <h3 class="mkt-section-title">Active Campaigns</h3>
        <div style="display:flex;align-items:center;gap:10px;">
          <div class="mkt-summary-chips" style="margin-top:0;">
            <span class="mkt-summary-chip"><span class="mkt-summary-label">Live</span><span class="mkt-summary-value">${liveCampaignCount}</span></span>
            <span class="mkt-summary-chip"><span class="mkt-summary-label">Approvals pending</span><span class="mkt-summary-value">${pendingApprovalCount}</span></span>
            <span class="mkt-summary-chip"><span class="mkt-summary-label">Signals</span><span class="mkt-summary-value">${signalsCount}</span></span>
          </div>
          <button class="mkt-btn-secondary" data-mkt-nav="marketing-campaigns" type="button">View all →</button>
        </div>
      </div>
      ${hasCampaigns
        ? `<div class="mkt-tiles-grid">${tiles}</div>`
        : `<div class="mkt-empty-state">
            <h4>No campaigns yet</h4>
            <p>Approve signals at Gate 1 to start one.</p>
          </div>`}
    </section>
  `;
}

// ── Campaigns list + workspace ────────────────────────────────────────────

function _renderActiveCampaignCardInner(c) {
  return `
    <div class="mkt-panel-eyebrow">Active Campaign</div>
    <div class="mkt-panel-name">${esc(c.name)}</div>
    <div class="mkt-panel-family">${esc(c.family)}</div>
    <div class="mkt-panel-pills">
      ${_campaignLifecycleBadge(c)}
      <span class="mkt-pill ${_campaignDecisionPillClass(c.state)} mkt-pill-wrap">${esc(c.state || 'created')}</span>
    </div>
    <div class="mkt-panel-meta">
      <span>${esc(c.objective || 'No objective recorded yet')}</span>
      <span class="mkt-panel-sep">·</span>
      <span>${_campaignClusterCount(c)} signals</span>
      <span class="mkt-panel-sep">·</span>
      <span>${c.primarySignalState || 'No primary signal state'}</span>
    </div>
  `;
}

function _selectDefaultCampaignId(campaigns) {
  if (!Array.isArray(campaigns) || campaigns.length === 0) return null;
  const initiated = campaigns.find((campaign) => Boolean(campaign.initiatedAt));
  return initiated?.id ?? campaigns[0]?.id ?? null;
}

// campaignsOrId: array of campaign objects (new callers) OR string/null selectedId (backward compat)
export function renderMarketingCampaigns(campaignsOrId = null, selectedId = null) {
  let campaigns, resolvedSelectedId;
  if (Array.isArray(campaignsOrId)) {
    campaigns = campaignsOrId;
    resolvedSelectedId = selectedId;
  } else {
    campaigns = [];
    resolvedSelectedId = campaignsOrId;
  }

  if (!resolvedSelectedId && campaigns.length > 0) {
    resolvedSelectedId = _selectDefaultCampaignId(campaigns);
  }

  const selected = resolvedSelectedId
    ? campaigns.find((c) => String(c.id) === String(resolvedSelectedId))
    : null;

  const listItems = campaigns.map((c) => {
    const clusterCount = _campaignClusterCount(c);
    const objective = c.objective || 'No objective recorded yet';
    const statusLabel = c.initiatedAt ? 'Initiated' : 'Proposed';
    return `
    <div class="mkt-campaign-list-item ${String(resolvedSelectedId) === String(c.id) ? 'active' : ''}"
         data-mkt-open-campaign="${esc(c.id)}" role="button" tabindex="0">
      <span class="mkt-list-item-name">${esc(c.name)}</span>
      <span class="mkt-list-item-family">${esc(c.family)}</span>
      <p class="mkt-list-item-objective">${esc(objective)}</p>
      <div class="mkt-list-item-foot">
        <span class="mkt-list-dot ${_campaignDecisionDotClass(c.state)}"></span>
        <span class="mkt-list-item-foot-text">${esc(c.state || 'created')} · ${statusLabel} · ${clusterCount} signal${clusterCount === 1 ? '' : 's'}</span>
      </div>
    </div>
  `; }).join('');

  const activeCampaignCard = selected ? `
    <div class="mkt-panel-card mkt-active-campaign-card">
      ${_renderActiveCampaignCardInner(selected)}
    </div>
  ` : `
    <div class="mkt-panel-card mkt-campaigns-empty-card">
      <div class="mkt-panel-eyebrow">Campaigns</div>
      <h4>No campaigns yet</h4>
      <p>Approve signals at Gate 1 to start one.</p>
    </div>`;

  const workspaceHtml = selected
    ? renderCampaignWorkspace(selected)
    : `<div class="mkt-workspace-empty">
        <div class="mkt-workspace-empty-icon">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        </div>
        <p>${campaigns.length > 0 ? 'Select a campaign to open its workspace.' : 'Approve signals at Gate 1 to start a campaign.'}</p>
      </div>`;

  return `
    <div class="mkt-campaigns-layout">
      <aside class="mkt-campaigns-list" aria-label="Campaign list">
        ${activeCampaignCard}
        <div class="mkt-panel-card mkt-campaigns-browser-card">
          <div class="mkt-campaigns-list-header">
            <span class="shell-eyebrow">All Campaigns</span>
            <span class="mkt-badge mkt-badge-neutral">${campaigns.length}</span>
          </div>
          <div class="mkt-campaigns-browser-items ${campaigns.length === 0 ? 'mkt-campaigns-browser-items--empty' : ''}">
            ${listItems}
          </div>
        </div>
      </aside>
      <div class="mkt-workspace-pane">
        ${workspaceHtml}
      </div>
    </div>
  `;
}

function renderCampaignWorkspace(campaign) {
  const storedTab = (() => {
    try { return localStorage.getItem(MKT_WORKSPACE_TAB_KEY) || 'audience'; } catch { return 'audience'; }
  })();

  const tabs = [
    { id: 'brief', label: 'Brief' },
    { id: 'audience', label: 'Audience' },
    { id: 'assets', label: 'Assets' },
    { id: 'sequence', label: 'Sequence' },
    { id: 'compliance', label: 'Compliance' },
    { id: 'performance', label: 'Performance' },
    { id: 'approval-log', label: 'Approval Log' },
  ];

  const tabNav = tabs.map((t) => `
    <button class="mkt-tab-btn ${storedTab === t.id ? 'active' : ''}"
            data-mkt-tab="${esc(t.id)}" type="button">${esc(t.label)}</button>
  `).join('');

  const tabContent = renderWorkspaceTab(campaign, storedTab);

  const actionsHtml = campaign._fromApi ? _renderWorkspaceActions(campaign) : '';
  const writingStudioHtml = campaign._fromApi ? _renderWritingStudioSection(campaign) : '';
  const initiationActionHtml = campaign._fromApi && !campaign.initiatedAt
    ? `<button class="mkt-btn-secondary mkt-initiation-open-btn" type="button" data-mkt-initiation-open="${esc(campaign.id)}">Review initiation proposal</button>`
    : '';

  return `
    <div class="mkt-workspace" data-campaign-id="${esc(campaign.id)}">
      <div class="mkt-workspace-header">
        <div class="mkt-workspace-header-left">
          <div class="shell-eyebrow">${esc(campaign.family)}</div>
          <h2 class="mkt-workspace-title">${esc(campaign.name || 'Pending initiation')}</h2>
          <div class="mkt-workspace-owner">${esc(campaign.objective || 'No objective recorded yet')}</div>
        </div>
        <div class="mkt-workspace-header-meta">
          ${_campaignLifecycleBadge(campaign)}
          <span class="mkt-pill ${_campaignDecisionPillClass(campaign.state)}">${esc(campaign.state || 'created')}</span>
          ${workspaceStateBadge(campaign)}
          <span class="mkt-confidence-badge">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            ${_campaignClusterCount(campaign)} signal${_campaignClusterCount(campaign) === 1 ? '' : 's'}
          </span>
        </div>
      </div>
      ${initiationActionHtml}
      ${actionsHtml}
      ${writingStudioHtml}
      <nav class="mkt-tab-nav" aria-label="Campaign workspace tabs">
        ${tabNav}
      </nav>
      <div class="mkt-tab-content" data-mkt-tab-content>
        ${tabContent}
      </div>
    </div>
  `;
}

function _renderWorkspaceActions(c) {
  const ds = c.decisionState;
  const buttons = [];

  if (!c.repositoryBucket) {
    if (ds === 'pending_review' || ds === 'changes_requested' || ds === 'monitoring') {
      buttons.push(`<button class="mkt-btn-primary mkt-action-btn" data-campaign-action="approve" type="button">Approve</button>`);
    }
    if (ds === 'pending_review' || ds === 'changes_requested') {
      buttons.push(`<button class="mkt-btn-secondary mkt-action-btn" data-campaign-action="monitor" type="button">Monitor</button>`);
      buttons.push(`<button class="mkt-btn-secondary mkt-action-btn" data-campaign-action="request_changes" type="button">Request changes</button>`);
    }
    if (ds === 'pending_review' || ds === 'changes_requested' || ds === 'monitoring') {
      buttons.push(`<button class="mkt-btn-ghost mkt-action-btn" data-campaign-action="reject" type="button">Reject</button>`);
    }
    if (ds === 'approved') {
      buttons.push(`<button class="mkt-btn-secondary mkt-action-btn" data-campaign-action="monitor" type="button">Move to monitoring</button>`);
    }
  } else {
    if (ds === 'pending_review') {
      buttons.push(`<button class="mkt-btn-primary mkt-action-btn" data-campaign-promote type="button">Promote to active</button>`);
    }
    if (ds === 'rejected' || ds === 'approved') {
      buttons.push(`<button class="mkt-btn-secondary mkt-action-btn" data-campaign-reopen type="button">Reopen</button>`);
    }
  }

  if (buttons.length === 0) return '';

  return `
    <div class="mkt-workspace-actions">
      ${buttons.join('\n      ')}
    </div>
  `;
}

function _renderWritingStudioSection(c) {
  const count = c.linkedDraftCount || 0;
  const latestId = c.latestDraftId;
  const latestTitle = c.latestDraftTitle;

  return `
    <div class="mkt-writing-studio-section">
      <span class="mkt-writing-studio-label">Writing Studio</span>
      ${count > 0 ? `
        <span class="mkt-writing-draft-count">${count} draft${count !== 1 ? 's' : ''}</span>
        ${latestId ? `<button class="mkt-btn-secondary mkt-ws-open-draft-btn" data-ws-draft-id="${esc(String(latestId))}" type="button">Open latest draft →</button>` : ''}
      ` : `<span class="mkt-writing-no-drafts">No drafts yet</span>`}
      <button class="mkt-btn-secondary mkt-ws-create-draft-btn" type="button">Create draft in Writing Studio</button>
    </div>
  `;
}

function _resolveAccountUser(accountInfo) {
  if (!accountInfo || typeof accountInfo !== 'object') return null;
  const id = accountInfo.id ?? accountInfo.userId ?? accountInfo.accountId ?? accountInfo.ownerUserId ?? null;
  if (id == null || id === '') return null;
  const label =
    accountInfo.displayName ||
    accountInfo.name ||
    accountInfo.fullName ||
    accountInfo.email ||
    String(id);
  return { id: String(id), label };
}

function _scopeToFormModel(targetScope, defaultTargetScope) {
  const scope = targetScope && typeof targetScope === 'object' ? targetScope : defaultTargetScope || { mode: 'all_districts' };
  const mode = scope.mode || 'all_districts';
  return {
    mode,
    states: Array.isArray(scope.states) ? scope.states.map((state) => String(state).toUpperCase()) : [],
    tiers: Array.isArray(scope.tiers) ? scope.tiers.map((tier) => String(tier).toUpperCase()) : [],
  };
}

function _renderTargetScopeSection(scopeModel, districtContext) {
  const stateOptions = US_STATES.map((state) => `
    <option value="${esc(state)}"${scopeModel.mode === 'states' && scopeModel.states.includes(state) ? ' selected' : ''}>${esc(state)}</option>
  `).join('');
  const tierOptions = ['D1', 'D2', 'D3', 'D4'].map((tier) => `
    <label class="mkt-initiation-tier-option ${tier === 'D4' ? 'mkt-initiation-tier-option--disabled' : ''}">
      <input type="checkbox" data-initiation-tier="${esc(tier)}" ${scopeModel.mode === 'district_tier' && scopeModel.tiers.includes(tier) ? 'checked' : ''} ${tier === 'D4' ? 'disabled' : ''}>
      <span>${esc(tier)}${tier === 'D4' ? ' (unsupported)' : ''}</span>
    </label>
  `).join('');

  const allChecked = scopeModel.mode === 'all_districts';
  const statesChecked = scopeModel.mode === 'states';
  const tierChecked = scopeModel.mode === 'district_tier';
  const note = districtContext?.note ? `<p class="mkt-initiation-note">${esc(districtContext.note)}</p>` : '';

  return `
    <section class="mkt-initiation-section">
      <h4>Target scope</h4>
      ${note}
      <label class="mkt-initiation-radio">
        <input type="radio" name="initiation-target-mode" value="all_districts" ${allChecked ? 'checked' : ''}>
        <span>All districts</span>
      </label>
      <label class="mkt-initiation-radio">
        <input type="radio" name="initiation-target-mode" value="states" ${statesChecked ? 'checked' : ''}>
        <span>Specific states</span>
      </label>
      <div class="mkt-initiation-subpanel" data-initiation-states ${statesChecked ? '' : 'hidden'}>
        <select multiple size="8" data-initiation-states-select>
          ${stateOptions}
        </select>
      </div>
      <label class="mkt-initiation-radio">
        <input type="radio" name="initiation-target-mode" value="district_tier" ${tierChecked ? 'checked' : ''}>
        <span>By tier</span>
      </label>
      <div class="mkt-initiation-subpanel" data-initiation-tiers ${tierChecked ? '' : 'hidden'}>
        <p class="mkt-initiation-note">D4 is shown for context but remains unsupported.</p>
        <div class="mkt-initiation-tier-grid">${tierOptions}</div>
      </div>
    </section>
  `;
}

function _renderSignalClusterRows(cluster) {
  return cluster.map((signal) => {
    const removable = signal.isPrimary ? 'data-initiation-signal-primary="true"' : '';
    const title = signal.isPrimary ? 'Primary signal' : 'Corroborating signal';
    return `
      <article class="mkt-initiation-signal ${signal.isPrimary ? 'mkt-initiation-signal--primary' : ''}" data-initiation-signal-id="${esc(String(signal.signalId))}" ${removable}>
        <div class="mkt-initiation-signal-topline">
          <strong>${esc(signal.headline || 'Untitled signal')}</strong>
          <span class="mkt-pill ${signal.isPrimary ? 'mkt-pill-live' : 'mkt-pill-active'}">${esc(title)}</span>
        </div>
        ${signal.summary ? `<p class="mkt-initiation-signal-summary">${esc(signal.summary)}</p>` : ''}
        <div class="mkt-initiation-signal-meta">
          <span>${esc(signal.campaignFamily || 'unknown family')}</span>
          <span>${signal.state ? esc(signal.state) : '—'}</span>
          <span>${signal.resolvedDistrictId ? `District ${esc(String(signal.resolvedDistrictId))}` : 'District unresolved'}</span>
        </div>
        <button class="mkt-btn-text mkt-initiation-signal-remove" type="button" data-initiation-signal-remove="${esc(String(signal.signalId))}" ${signal.isPrimary ? 'disabled' : ''}>
          ${signal.isPrimary ? 'Primary signal' : 'Remove'}
        </button>
      </article>
    `;
  }).join('');
}

function _renderDeliverableRegistryRows(registry, recommendedSlugs) {
  return registry.map((row) => {
    const active = !!row.active;
    const recommended = recommendedSlugs.includes(row.slug);
    return `
      <label class="mkt-initiation-deliverable ${active ? '' : 'mkt-initiation-deliverable--disabled'}">
        <input
          type="checkbox"
          name="initiation-deliverable"
          value="${esc(row.slug)}"
          ${recommended ? 'checked' : ''}
          ${active ? '' : 'disabled'}
        >
        <span>
          ${esc(row.label)}
          ${active ? '' : ' (coming soon)'}
        </span>
      </label>
    `;
  }).join('');
}

function _renderLineagePanel(lineage) {
  if (!Array.isArray(lineage) || lineage.length === 0) return '';
  return `
    <section class="mkt-initiation-section">
      <h4>Prior campaigns</h4>
      <p class="mkt-initiation-note">Collateral only for v1. Outcomes/results are not tracked yet.</p>
      <div class="mkt-initiation-lineage-list">
        ${lineage.map((item) => `
          <article class="mkt-initiation-lineage-item" data-lineage-candidate-id="${esc(String(item.candidateId))}">
            <div class="mkt-initiation-lineage-head">
              <strong>${esc(item.name || `Campaign ${item.candidateId}`)}</strong>
              <span>${esc(item.objective || 'No objective recorded')}</span>
            </div>
            ${item.latestBrief ? `<p class="mkt-initiation-lineage-brief">Latest brief available</p>` : ''}
            <div class="mkt-initiation-lineage-collateral">
              <button class="mkt-btn-text" type="button" data-lineage-action="view" data-lineage-candidate-id="${esc(String(item.candidateId))}">View</button>
              <button class="mkt-btn-text" type="button" data-lineage-action="clone" data-lineage-candidate-id="${esc(String(item.candidateId))}">Clone</button>
              <button class="mkt-btn-text" type="button" data-lineage-action="adapt" data-lineage-candidate-id="${esc(String(item.candidateId))}">Adapt</button>
            </div>
            <div class="mkt-initiation-lineage-collateral-list">
              ${(item.drafts || []).map((draft) => `<span class="mkt-initiation-chip">Draft ${esc(String(draft.draft_id || draft.id || ''))}</span>`).join('')}
              ${(item.linkedAssets || []).map((asset) => `<span class="mkt-initiation-chip">${esc(asset.asset_type || 'asset')}</span>`).join('')}
            </div>
          </article>
        `).join('')}
      </div>
    </section>
  `;
}

function _renderInitiationModal(campaign, bundle, accountInfo) {
  const proposal = bundle?.proposal || campaign.initiationProposalJson || {};
  const scopeModel = _scopeToFormModel(proposal.target_scope || proposal.targetScope, bundle?.defaultTargetScope);
  const accountUser = _resolveAccountUser(accountInfo);
  const ownerSelect = accountUser
    ? `<option value="${esc(accountUser.id)}" selected>${esc(accountUser.label)} (current user)</option>`
    : '<option value="" selected>Unassigned</option>';

  return `
    <div class="mkt-modal-backdrop mkt-initiation-backdrop" data-initiation-modal>
      <div class="mkt-modal mkt-sp-modal mkt-initiation-modal">
        <div class="mkt-initiation-header">
          <div>
            <div class="mkt-modal-eyebrow">Campaign initiation</div>
            <h3>${esc(proposal.name || campaign.name || 'Pending initiation')}</h3>
            <p>${esc(bundle?.districtContext?.label || 'All districts')}</p>
          </div>
          <button class="mkt-btn-text" type="button" data-initiation-close>Close</button>
        </div>

        <label class="mkt-initiation-field">
          <span>Name</span>
          <input type="text" data-initiation-field="name" value="${esc(proposal.name || campaign.name || '')}">
        </label>

        <label class="mkt-initiation-field">
          <span>Objective</span>
          <textarea rows="4" data-initiation-field="objective">${esc(proposal.objective || '')}</textarea>
        </label>

        <label class="mkt-initiation-field">
          <span>Owner</span>
          <select data-initiation-field="owner_user_id">
            ${ownerSelect}
          </select>
        </label>

        <section class="mkt-initiation-section">
          <h4>Signal cluster</h4>
          <p class="mkt-initiation-note">Primary signal is flagged. Removing a corroborating signal updates the preview only in v1.</p>
          <div class="mkt-initiation-signals">
            ${_renderSignalClusterRows(bundle?.signalCluster || [])}
          </div>
        </section>

        <section class="mkt-initiation-section">
          <h4>Deliverables</h4>
          <div class="mkt-initiation-deliverables">
            ${_renderDeliverableRegistryRows(bundle?.deliverableRegistry || [], proposal.recommended_deliverable_types || proposal.recommendedDeliverableTypes || [])}
          </div>
        </section>

        ${_renderTargetScopeSection(scopeModel, bundle?.districtContext)}
        ${_renderLineagePanel(bundle?.lineage || [])}

        <div class="mkt-initiation-actions">
          <button class="mkt-btn-secondary" type="button" data-initiation-close>Cancel</button>
          <button class="mkt-btn-primary" type="button" data-initiation-confirm>Confirm and initiate</button>
        </div>
      </div>
    </div>
  `;
}

function renderWorkspaceTab(campaign, tab) {
  switch (tab) {
    case 'brief':      return renderTabBrief(campaign);
    case 'audience':   return renderTabAudience(campaign);
    case 'assets':     return renderTabAssets(campaign, _deliverablesCache.get(campaign.id), _assetLinksCache.get(campaign.id));
    case 'sequence':   return renderTabSequence(campaign);
    case 'compliance': return renderTabCompliance(campaign);
    case 'performance':return renderTabPerformance(campaign);
    case 'approval-log': return renderTabApprovalLog(campaign);
    default:           return renderTabBrief(campaign);
  }
}

async function _maybeOpenInitiationProposal(container, campaign) {
  if (!campaign || !campaign._fromApi || campaign.initiatedAt) return;
  if (container.querySelector('[data-initiation-modal]')) return;

  const backdrop = document.createElement('div');
  backdrop.className = 'mkt-modal-backdrop mkt-initiation-backdrop';
  backdrop.dataset.initiationModal = 'loading';
  backdrop.innerHTML = `
    <div class="mkt-modal mkt-sp-modal mkt-initiation-modal">
      <p class="mkt-section-subtext">Loading initiation proposal…</p>
    </div>
  `;
  container.appendChild(backdrop);

  let bundle = null;
  try {
    bundle = _initiationProposalCache.get(campaign.id) || await getCampaignInitiationProposalApi(campaign.id);
    _initiationProposalCache.set(campaign.id, bundle);
  } catch (err) {
    backdrop.innerHTML = `
      <div class="mkt-modal mkt-sp-modal mkt-initiation-modal">
        <div class="mkt-initiation-header">
          <div>
            <div class="mkt-modal-eyebrow">Campaign initiation</div>
            <h3>Proposal unavailable</h3>
          </div>
          <button class="mkt-btn-text" type="button" data-initiation-close>Close</button>
        </div>
        <p class="mkt-error-text">${esc(err?.message || 'Missing initiation proposal.')}</p>
      </div>
    `;
    _wireInitiationModal(container, campaign, null, null);
    return;
  }

  let accountInfo = null;
  try {
    accountInfo = await fetchAccountInfo();
  } catch {
    accountInfo = null;
  }

  const activeCampaignId = (() => {
    try { return localStorage.getItem(MKT_CAMPAIGN_KEY); } catch { return null; }
  })();
  if (String(activeCampaignId || '') !== String(campaign.id)) {
    return;
  }

  backdrop.innerHTML = _renderInitiationModal(campaign, bundle, accountInfo);
  _wireInitiationModal(container, campaign, bundle, accountInfo);
}

function _wireInitiationModal(container, campaign, bundle, accountInfo) {
  const modal = container.querySelector('[data-initiation-modal]');
  if (!modal) return;
  modal.setAttribute('tabindex', '-1');
  modal.focus({ preventScroll: true });

  const closeModal = () => {
    _initiationProposalCache.set(campaign.id, bundle || _initiationProposalCache.get(campaign.id) || null);
    modal.remove();
  };

  const toggleScopePanels = () => {
    const mode = modal.querySelector('input[name="initiation-target-mode"]:checked')?.value || 'all_districts';
    const statesPanel = modal.querySelector('[data-initiation-states]');
    const tiersPanel = modal.querySelector('[data-initiation-tiers]');
    if (statesPanel) statesPanel.hidden = mode !== 'states';
    if (tiersPanel) tiersPanel.hidden = mode !== 'district_tier';
  };

  const selectedScope = () => {
    const mode = modal.querySelector('input[name="initiation-target-mode"]:checked')?.value || 'all_districts';
    if (mode === 'states') {
      const select = modal.querySelector('[data-initiation-states-select]');
      const states = [...(select?.selectedOptions || [])].map((option) => option.value).filter(Boolean);
      return { mode, states };
    }
    if (mode === 'district_tier') {
      const tiers = [...modal.querySelectorAll('[data-initiation-tier]:checked')].map((el) => el.getAttribute('data-initiation-tier')).filter(Boolean);
      return { mode, tiers };
    }
    return { mode: 'all_districts' };
  };

  const collectPayload = () => {
    const name = modal.querySelector('[data-initiation-field="name"]')?.value?.trim() || '';
    const objective = modal.querySelector('[data-initiation-field="objective"]')?.value?.trim() || '';
    const ownerRaw = modal.querySelector('[data-initiation-field="owner_user_id"]')?.value || '';
    const owner_user_id = ownerRaw === '' ? null : Number(ownerRaw);
    const deliverable_type_slugs = [...modal.querySelectorAll('[name="initiation-deliverable"]:checked')]
      .map((el) => el.value)
      .filter(Boolean);
    return {
      name,
      objective,
      owner_user_id,
      deliverable_type_slugs,
      target_scope: selectedScope(),
      actor: accountInfo ? (_resolveAccountUser(accountInfo)?.label || null) : null,
    };
  };

  modal.querySelectorAll('[data-initiation-close]').forEach((btn) => {
    btn.addEventListener('click', closeModal);
  });
  modal.addEventListener('click', (event) => {
    if (event.target === modal) closeModal();
  });
  modal.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeModal();
  });

  modal.querySelectorAll('input[name="initiation-target-mode"]').forEach((input) => {
    input.addEventListener('change', toggleScopePanels);
  });
  toggleScopePanels();

  modal.querySelectorAll('[data-initiation-signal-remove]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const card = btn.closest('[data-initiation-signal-id]');
      if (card) {
        const hidden = card.getAttribute('data-removed') === 'true';
        card.setAttribute('data-removed', hidden ? 'false' : 'true');
        card.classList.toggle('mkt-initiation-signal--removed', !hidden);
      }
    });
  });

  modal.querySelectorAll('[data-lineage-action]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.lineageAction;
      const candidateId = btn.dataset.lineageCandidateId;
      if (action === 'view' && candidateId) {
        closeModal();
        try { localStorage.setItem(MKT_CAMPAIGN_KEY, candidateId); } catch {}
        const opened = _campaignMap.get(_campaignMapKey(candidateId));
        if (opened) {
          const pane = container.querySelector('.mkt-workspace-pane');
          if (pane) {
            pane.innerHTML = renderCampaignWorkspace(opened);
            _wireWorkspaceTabs(container, opened);
            _wireWorkspaceActions(container, opened);
            _wireWritingStudioBridge(container, opened);
          }
        }
      }
      if (action === 'clone' || action === 'adapt') {
        btn.textContent = action === 'clone' ? 'Clone coming soon' : 'Adapt coming soon';
        setTimeout(() => {
          btn.textContent = action === 'clone' ? 'Clone' : 'Adapt';
        }, 1600);
      }
    });
  });

  modal.querySelector('[data-initiation-confirm]')?.addEventListener('click', async () => {
    const confirmBtn = modal.querySelector('[data-initiation-confirm]');
    if (!confirmBtn) return;
    const payload = collectPayload();
    if (!payload.name) {
      modal.querySelector('[data-initiation-field="name"]')?.focus();
      return;
    }
    if (!payload.objective) {
      modal.querySelector('[data-initiation-field="objective"]')?.focus();
      return;
    }
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Initiating…';
    try {
      const result = await initiateCampaignApi(campaign.id, payload);
      closeModal();
      const toast = document.createElement('div');
      toast.className = 'mkt-signal-toast';
      toast.textContent = `Campaign "${result.name || payload.name}" initiated; ${payload.deliverable_type_slugs.length} deliverable(s) queued`;
      container.querySelector('.mkt-workspace')?.prepend(toast);
      setTimeout(() => toast.remove(), 6500);
      const liveCandidates = (await fetchMarketingCampaignsApi()).map((c) => _normalizeCampaignCandidate(c));
      _syncCampaignMap(liveCandidates);
      const updated = _campaignMap.get(_campaignMapKey(campaign.id));
      if (updated) {
        const pane = container.querySelector('.mkt-workspace-pane');
        if (pane) {
          pane.innerHTML = renderCampaignWorkspace(updated);
          _wireWorkspaceTabs(container, updated);
          _wireWorkspaceActions(container, updated);
          _wireWritingStudioBridge(container, updated);
        }
        _updateActiveCampaignCard(container, updated);
      }
    } catch (err) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'Confirm and initiate';
      const errEl = modal.querySelector('.mkt-initiation-error') || (() => {
        const node = document.createElement('p');
        node.className = 'mkt-initiation-error mkt-error-text';
        modal.querySelector('.mkt-initiation-actions')?.before(node);
        return node;
      })();
      errEl.textContent = err?.message || 'Failed to initiate campaign.';
    }
  });
}

function renderTabBrief(c) {
  // For real API candidates: show assembled brief if present in cache,
  // or an "Assemble Brief" prompt for approved/in-planning candidates.
  if (c._fromApi) {
    const cachedBrief = _briefCache.get(c.id); // undefined = not loaded, null = loaded+none
    if (cachedBrief) {
      return renderAssembledBrief(cachedBrief, c);
    }
    // Brief not yet assembled (or cache miss before async load)
    const canAssemble = c.decisionState === 'approved' ||
      c.decisionState === 'in_planning' ||
      c.decisionState === 'promoted';
    const assembleSection = canAssemble ? `
      <div class="mkt-brief-assemble-row">
        <button class="mkt-btn mkt-btn-sm" data-brief-action="assemble">Assemble brief</button>
        <span class="mkt-brief-assemble-hint">Assembles from available campaign data. Missing fields (contacts, district) will be clearly marked.</span>
      </div>
    ` : '';
    // Fall through to existing field rendering below, with assemble row prepended
    const rulesetRow = c.rulesetVersionAtQualification ? `
      <div class="mkt-brief-ruleset-row">
        <span class="mkt-brief-ruleset-label">Ruleset at qualification:</span>
        <span class="mkt-brief-ruleset-value">${esc(c.rulesetVersionAtQualification)}</span>
      </div>` : '';
    return _renderLegacyBriefFields(c, assembleSection + rulesetRow);
  }
  // Demo candidates — original rendering unchanged
  return _renderLegacyBriefFields(c, '');
}

function _renderLegacyBriefFields(c, prefixHtml) {
  const brief = c.brief || {};
  const objective = brief.objective || c.objective || c.why || '';
  const signalSource = brief.signalSource || c.signalSource || '';
  const keyMessaging = brief.keyMessaging || c.keyMessaging || [];
  const targetDistricts = brief.targetDistricts || c.targetDistricts || [];
  const signals = Array.isArray(c.signals) && !brief.signalSource ? c.signals : [];
  const deliverables = Array.isArray(c.deliverables) ? c.deliverables : [];
  const gates = Array.isArray(c.gates) ? c.gates : [];
  const nextAction = c.nextAction || '';

  const districtRows = targetDistricts.map((d) => `
    <tr>
      <td>${esc(d.name)}</td>
      <td><span class="mkt-pill ${statusPillClass(d.status)}">${esc(d.status)}</span></td>
    </tr>
  `).join('');

  const msgItems = keyMessaging.map((m) => `<li>${esc(m)}</li>`).join('');

  return `
    <div class="mkt-tab-brief">
      ${prefixHtml}
      <div class="mkt-brief-grid">
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Objective</div>
          <div class="mkt-brief-value">${esc(objective) || '<em>Not yet defined</em>'}</div>
        </div>
        ${signalSource ? `
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Signal source</div>
          <div class="mkt-brief-value">${esc(signalSource)}</div>
        </div>
        ` : ''}
        ${nextAction ? `
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Next action</div>
          <div class="mkt-brief-value">${esc(nextAction)}</div>
        </div>
        ` : ''}
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Deadline</div>
          <div class="mkt-brief-value">${esc(c.deadline) || '—'}</div>
        </div>
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Owner</div>
          <div class="mkt-brief-value">${esc(c.owner) || '—'}</div>
        </div>
      </div>
      ${signals.length > 0 ? `
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Signals</div>
        <ul class="mkt-brief-list">${signals.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>
      </div>
      ` : ''}
      ${deliverables.length > 0 ? `
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Deliverables</div>
        <ul class="mkt-brief-list">${deliverables.map((d) => `<li>${esc(d)}</li>`).join('')}</ul>
      </div>
      ` : ''}
      ${gates.length > 0 ? `
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Gates</div>
        <ul class="mkt-brief-list">${gates.map((g) => `<li>${esc(g)}</li>`).join('')}</ul>
      </div>
      ` : ''}
      ${msgItems ? `
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Key messaging</div>
        <ul class="mkt-brief-list">${msgItems}</ul>
      </div>
      ` : ''}
      ${targetDistricts.length > 0 ? `
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Target districts</div>
        <table class="mkt-table">
          <thead><tr><th>District</th><th>Status</th></tr></thead>
          <tbody>${districtRows}</tbody>
        </table>
      </div>
      ` : ''}
    </div>
  `;
}

function renderAssembledBrief(briefRecord, c) {
  const b = briefRecord.brief || {};
  const assembledDate = briefRecord.assembledAt
    ? new Date(briefRecord.assembledAt * 1000).toLocaleDateString()
    : '—';
  const version = briefRecord.version || 1;

  const signalEvidence = b.signal?.verbatimEvidence || '';
  const reasonCodes = Array.isArray(b.signal?.reasonCodesWithEvidence)
    ? b.signal.reasonCodesWithEvidence.filter(Boolean) : [];
  const campaignType = b.campaignType?.primary || '';
  const urgencyTier = b.signal?.urgency?.tier || '';
  const deliverables = Array.isArray(b.deliverables) ? b.deliverables.filter(Boolean) : [];
  const gates = Array.isArray(b.gates) ? b.gates.filter(Boolean) : [];

  const unavailableNotices = [
    b.districtDataUnavailable && 'District data — no external data source connected',
    b.contactsUnavailable && 'Target contacts — Contact DB not configured',
    b.audienceTierUnavailable && 'Audience tier distribution not available',
  ].filter(Boolean);

  return `
    <div class="mkt-tab-brief">
      <div class="mkt-brief-assembled-header">
        <span class="mkt-brief-assembled-meta">Brief · v${version} · assembled ${esc(assembledDate)}</span>
        <button class="mkt-btn mkt-btn-sm mkt-btn-ghost" data-brief-action="reassemble">Assemble new version</button>
      </div>
      ${c.rulesetVersionAtQualification ? `
      <div class="mkt-brief-ruleset-row">
        <span class="mkt-brief-ruleset-label">Ruleset at qualification:</span>
        <span class="mkt-brief-ruleset-value">${esc(c.rulesetVersionAtQualification)}</span>
      </div>` : ''}
      <div class="mkt-brief-grid">
        ${campaignType ? `
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Campaign type</div>
          <div class="mkt-brief-value">${esc(campaignType)}</div>
        </div>
        ` : ''}
        ${signalEvidence ? `
        <div class="mkt-brief-field mkt-brief-field-wide">
          <div class="mkt-brief-label">Signal / Why</div>
          <div class="mkt-brief-value">${esc(signalEvidence)}</div>
        </div>
        ` : ''}
        ${urgencyTier ? `
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Urgency tier</div>
          <div class="mkt-brief-value">${esc(urgencyTier)}</div>
        </div>
        ` : ''}
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Owner</div>
          <div class="mkt-brief-value">${esc(c.owner) || '—'}</div>
        </div>
      </div>
      ${reasonCodes.length > 0 ? `
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Signals / reason codes</div>
        <ul class="mkt-brief-list">${reasonCodes.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>
      </div>
      ` : ''}
      ${deliverables.length > 0 ? `
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Planned deliverables</div>
        <ul class="mkt-brief-list">${deliverables.map((d) => `<li>${esc(d)}</li>`).join('')}</ul>
      </div>
      ` : ''}
      ${gates.length > 0 ? `
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Gates</div>
        <ul class="mkt-brief-list">${gates.map((g) => `<li>${esc(g)}</li>`).join('')}</ul>
      </div>
      ` : ''}
      ${unavailableNotices.length > 0 ? `
      <div class="mkt-brief-section">
        ${unavailableNotices.map((n) => `
        <div class="mkt-brief-unavailable">Not available: ${esc(n)}</div>
        `).join('')}
      </div>
      ` : ''}
    </div>
  `;
}

function renderTabAudience(c) {
  const districts = c.audience?.districts || [];
  if (districts.length === 0) {
    return `
      <div class="mkt-tab-audience">
        <div class="mkt-placeholder-panel">
          <h4>Audience data not yet available</h4>
          <p>Contact enrichment via Poppl + Salesforce cache — Phase B</p>
        </div>
      </div>
    `;
  }

  const rows = districts.map((d) => `
    <tr>
      <td>${esc(d.name)}</td>
      <td><span class="mkt-pill ${statusPillClass(d.status)}">${esc(d.status)}</span></td>
      <td class="mkt-td-num">${d.contacts}</td>
      <td>${freshnessBadge(d.fresh)}</td>
    </tr>
  `).join('');

  return `
    <div class="mkt-tab-audience">
      <div class="mkt-tab-section-header">
        <h4>Districts & Contact Freshness</h4>
        <span class="mkt-note">Contact enrichment via Poppl + Salesforce cache — Phase B</span>
      </div>
      <table class="mkt-table">
        <thead>
          <tr>
            <th>District</th>
            <th>Qualification status</th>
            <th>Contacts</th>
            <th>Last refreshed</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

const DELIVERABLE_STATE_LABELS = {
  generating:         'Generating…',
  ready_for_review:   'Awaiting review',
  rejected_at_gate_2: 'Changes requested',
  approved:           'Approved',
  generation_failed:  'Failed',
};

const DELIVERABLE_STATE_PILL = {
  generating:         'mkt-pill-neutral',
  ready_for_review:   'mkt-pill-pending',
  rejected_at_gate_2: 'mkt-pill-warning',
  approved:           'mkt-pill-success',
  generation_failed:  'mkt-pill-danger',
};

function _deliverableStateLabel(state) {
  return DELIVERABLE_STATE_LABELS[state] || esc(state);
}

function _deliverableStatePill(state) {
  return DELIVERABLE_STATE_PILL[state] || 'mkt-pill-neutral';
}

// ── Campaign workspace state display ──────────────────────────────────────

const WORKSPACE_STATE_LABELS = {
  created:              'Ready for content',
  content_generating:   'Content generating',
  content_review:       'Awaiting review',
  blocked:              'Blocked',
  all_content_approved: 'Content approved',
};

const WORKSPACE_STATE_PILL = {
  created:              'mkt-pill-neutral',
  content_generating:   'mkt-pill-neutral',
  content_review:       'mkt-pill-pending',
  blocked:              'mkt-pill-danger',
  all_content_approved: 'mkt-pill-success',
};

const WORKSPACE_BLOCKER_LABELS = {
  generation_failed:                       'generation failed',
  changes_requested:                       'changes requested',
  generation_failed_and_changes_requested: 'generation failed + changes requested',
};

export function workspaceStateBadge(c) {
  if (!c._fromApi || c.decisionState !== 'approved') return '';
  const state = c.workspaceState || 'created';
  const label = WORKSPACE_STATE_LABELS[state] || state.replace(/_/g, ' ');
  const pill  = WORKSPACE_STATE_PILL[state] || 'mkt-pill-neutral';
  const blocker = state === 'blocked' && c.workspaceBlockerReason
    ? ` — ${WORKSPACE_BLOCKER_LABELS[c.workspaceBlockerReason] || c.workspaceBlockerReason}`
    : '';
  return `<span class="mkt-pill ${pill} mkt-workspace-state-pill" title="Content pipeline state">${esc(label + blocker)}</span>`;
}

function _renderDeliverablesSummary(c, deliverables) {
  const ws = c.workspaceState || 'created';
  if (ws === 'blocked') {
    const reason = c.workspaceBlockerReason
      ? (WORKSPACE_BLOCKER_LABELS[c.workspaceBlockerReason] || c.workspaceBlockerReason)
      : 'unknown reason';
    return `
      <div class="mkt-deliverables-summary mkt-deliverables-summary-blocked">
        <span class="mkt-pill mkt-pill-danger">Blocked</span>
        <span class="mkt-deliverables-summary-text">${esc(reason)}</span>
      </div>
    `;
  }

  const counts = c.deliverableCounts || {};
  const parts = [];
  if (counts.generating > 0) parts.push(`${counts.generating} generating`);
  if (counts.ready_for_review > 0) parts.push(`${counts.ready_for_review} awaiting review`);
  if (counts.approved > 0) parts.push(`${counts.approved} approved`);
  if (counts.rejected_at_gate_2 > 0) parts.push(`${counts.rejected_at_gate_2} changes requested`);
  if (counts.generation_failed > 0) parts.push(`${counts.generation_failed} failed`);

  if (parts.length === 0) return '';

  return `
    <div class="mkt-deliverables-summary">
      <span class="mkt-deliverables-summary-text">${esc(parts.join(' · '))}</span>
    </div>
  `;
}

// deliverables: null = loading, undefined = not yet fetched (treat as null), [] = empty, [...] = data
function _assetStatusPill(status) {
  const map = {
    ready: 'mkt-pill-approved',
    draft: 'mkt-pill-pending',
    needs_validation: 'mkt-pill-warning',
    needs_design: 'mkt-pill-warning',
    blocked: 'mkt-pill-blocked',
    archived: 'mkt-pill-neutral',
  };
  return map[status] || 'mkt-pill-neutral';
}

function _renderLinkedCollateral(c, linkedAssets) {
  const isLoading = linkedAssets === undefined;
  const links = isLoading ? [] : (Array.isArray(linkedAssets) ? linkedAssets : []);

  const rows = links.map((a) => `
    <div class="mkt-collateral-row">
      <div class="mkt-collateral-row-body">
        <span class="mkt-collateral-title">${esc(a.title)}</span>
        <span class="mkt-collateral-type">${esc((a.assetType || '').replace(/_/g, ' '))}</span>
        ${a.summary ? `<span class="mkt-collateral-summary">${esc(a.summary)}</span>` : ''}
        ${a.sourceUrl ? `<a class="mkt-collateral-link" href="${esc(a.sourceUrl)}" target="_blank" rel="noopener">↗ Source</a>` : ''}
      </div>
      <span class="mkt-pill ${_assetStatusPill(a.status)}">${esc((a.status || '').replace(/_/g, ' '))}</span>
      <button class="mkt-collateral-unlink-btn" data-unlink-asset="${esc(String(a.id))}" type="button" title="Unlink asset">✕</button>
    </div>
  `).join('');

  return `
    <div class="mkt-collateral-section">
      <div class="mkt-tab-section-header mkt-collateral-header">
        <h4>Linked Collateral</h4>
        <div class="mkt-collateral-actions">
          <button class="mkt-btn-text mkt-collateral-action-btn" data-asset-action="link" type="button">+ Link asset</button>
          <button class="mkt-btn-text mkt-collateral-action-btn" data-asset-action="add" type="button">Add new asset</button>
        </div>
      </div>
      ${isLoading ? `<div class="mkt-placeholder-panel"><p>Loading linked assets…</p></div>` :
        links.length > 0 ? `<div class="mkt-collateral-list">${rows}</div>` :
        `<div class="mkt-collateral-empty"><p>No linked collateral yet. Link an existing asset or add a new one.</p></div>`}
    </div>
  `;
}

export function renderTabAssets(c, deliverables, linkedAssets) {
  const assets = c.assets || [];

  // Deliverable records path (API campaigns only)
  if (c._fromApi) {
    if (deliverables === null || deliverables === undefined) {
      // Loading state — the async fetch will replace this
      return `
        <div class="mkt-tab-assets" data-deliverables-loading="true">
          <div class="mkt-tab-section-header"><h4>Content Assets</h4></div>
          <div class="mkt-placeholder-panel"><p>Loading deliverables…</p></div>
        </div>
      `;
    }

    if (deliverables.length > 0) {
      const rows = deliverables.map((d) => {
        const title = d.draftTitle || (d.formatMode ? `${d.formatMode} draft` : 'Draft');
        const format = d.formatMode ? esc(d.formatMode.replace(/_/g, ' ')) : null;
        return `
          <div class="mkt-deliverable-row">
            <div class="mkt-deliverable-row-body">
              <span class="mkt-deliverable-title">${esc(title)}</span>
              ${format ? `<span class="mkt-deliverable-format">${format}</span>` : ''}
            </div>
            <span class="mkt-pill ${_deliverableStatePill(d.state)}">${_deliverableStateLabel(d.state)}</span>
            ${d.writingStudioDraftId
              ? `<button class="mkt-asset-open-btn mkt-ws-open-draft-btn"
                         data-ws-draft-id="${esc(String(d.writingStudioDraftId))}"
                         type="button">Open →</button>`
              : '<span class="mkt-deliverable-no-draft">—</span>'}
          </div>
        `;
      }).join('');

      const summaryBar = _renderDeliverablesSummary(c, deliverables);

      return `
        <div class="mkt-tab-assets">
          <div class="mkt-tab-section-header">
            <h4>Content Assets</h4>
            <span class="mkt-asset-count">${deliverables.length} deliverable${deliverables.length !== 1 ? 's' : ''}</span>
          </div>
          ${summaryBar}
          <div class="mkt-deliverables-list">${rows}</div>
          ${_renderLinkedCollateral(c, linkedAssets)}
        </div>
      `;
    }

    // No deliverables yet — fall back to linked draft count display
    const count = c.linkedDraftCount || 0;
    const latestId = c.latestDraftId;
    const latestTitle = c.latestDraftTitle;
    return `
      <div class="mkt-tab-assets">
        <div class="mkt-tab-section-header">
          <h4>Content Assets</h4>
          ${count > 0 ? `<span class="mkt-asset-count">${count} Writing Studio draft${count !== 1 ? 's' : ''}</span>` : ''}
        </div>
        ${count === 0 ? `
          <div class="mkt-placeholder-panel">
            <h4>No drafts yet</h4>
            <p>Use "Create draft in Writing Studio" above to start a content asset for this campaign.</p>
          </div>
        ` : `
          <div class="mkt-writing-drafts-list">
            ${latestId ? `
              <div class="mkt-writing-draft-item">
                <span class="mkt-writing-draft-title">${esc(latestTitle || 'Untitled draft')}</span>
                <button class="mkt-asset-open-btn mkt-ws-open-draft-btn" data-ws-draft-id="${esc(String(latestId))}" type="button">Open →</button>
              </div>
            ` : ''}
            ${count > 1 ? `<p class="mkt-writing-more-drafts">+ ${count - 1} more</p>` : ''}
          </div>
        `}
        ${_renderLinkedCollateral(c, linkedAssets)}
      </div>
    `;
  }

  // Static mock campaigns
  if (assets.length === 0) {
    return `<div class="mkt-tab-assets"><p class="mkt-empty-msg">No assets yet.</p></div>`;
  }

  const rows = assets.map((a) => {
    const color = assetIconColor(a.type);
    return `
      <div class="mkt-asset-row">
        <span class="mkt-asset-icon-wrap" style="color:${color}">${assetTypeIcon(a.type)}</span>
        <div class="mkt-asset-row-body">
          <span class="mkt-asset-row-name">${esc(a.name)}</span>
          <span class="mkt-asset-row-type">${esc(a.type)}</span>
        </div>
        <span class="mkt-pill ${statusPillClass(a.status)}">${esc(a.status)}</span>
        <span class="mkt-asset-row-date">${a.updatedAt ? esc(a.updatedAt) : '—'}</span>
        <button class="mkt-asset-open-btn" type="button">Open →</button>
      </div>
    `;
  }).join('');

  return `
    <div class="mkt-tab-assets">
      <div class="mkt-tab-section-header">
        <h4>Content Assets</h4>
        <span class="mkt-asset-count">${assets.length} asset${assets.length !== 1 ? 's' : ''}</span>
      </div>
      <div class="mkt-assets-list">${rows}</div>
    </div>
  `;
}

function renderTabSequence(c) {
  const sequence = c.sequence || null;
  if (!sequence) {
    return `
      <div class="mkt-tab-sequence">
        <div class="mkt-placeholder-panel">
          <h4>Sequence not yet configured</h4>
          <p>Email cadence and social schedule will be set up during campaign planning.</p>
        </div>
      </div>
    `;
  }

  const emailRows = sequence.emailCadence.map((s) => `
    <tr>
      <td class="mkt-td-num">${s.step}</td>
      <td>${esc(s.subject)}</td>
      <td>${esc(s.sendOffset)}</td>
      <td>${esc(s.audience)}</td>
    </tr>
  `).join('');

  const socialRows = sequence.socialSchedule.map((s) => `
    <tr>
      <td>${esc(s.platform)}</td>
      <td>${esc(s.type)}</td>
      <td>${esc(s.scheduledFor)}</td>
      <td><span class="mkt-pill ${statusPillClass(s.status)}">${esc(s.status)}</span></td>
    </tr>
  `).join('');

  return `
    <div class="mkt-tab-sequence">
      <div class="mkt-tab-section-header"><h4>Email Cadence</h4></div>
      <table class="mkt-table">
        <thead><tr><th>#</th><th>Subject line</th><th>Send timing</th><th>Audience</th></tr></thead>
        <tbody>${emailRows}</tbody>
      </table>

      <div class="mkt-tab-section-header mkt-section-gap"><h4>Social Schedule</h4></div>
      <table class="mkt-table">
        <thead><tr><th>Platform</th><th>Type</th><th>Scheduled</th><th>Status</th></tr></thead>
        <tbody>${socialRows}</tbody>
      </table>

      <div class="mkt-tab-section-header mkt-section-gap"><h4>BDR Handoff Threshold</h4></div>
      <div class="mkt-brief-value mkt-bdr-threshold">${esc(sequence.bdrHandoffThreshold)}</div>
    </div>
  `;
}

export function renderTabCompliance(c) {
  const compliance = c.compliance || null;
  if (!compliance) {
    return `
      <div class="mkt-tab-compliance">
        <div class="mkt-placeholder-panel">
          <h4>Compliance not yet configured</h4>
          <p>Preference center and legal flags are configured during campaign preparation.</p>
        </div>
        ${c._fromApi ? `<p class="mkt-compliance-stub">Compliance gate not wired yet</p>` : ''}
      </div>
    `;
  }

  const flagItems = compliance.legalFlags.length > 0
    ? compliance.legalFlags.map((f) => `<li class="mkt-compliance-flag">${esc(f)}</li>`).join('')
    : '<li class="mkt-compliance-ok">No flags</li>';

  const pcStatusClass = compliance.preferenceCenter === 'active' ? 'mkt-pill-live'
    : compliance.preferenceCenter === 'pending-setup' ? 'mkt-pill-pending'
    : 'mkt-pill-neutral';

  return `
    <div class="mkt-tab-compliance">
      <div class="mkt-compliance-grid">
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Preference Center</div>
          <div class="mkt-brief-value">
            <span class="mkt-pill ${pcStatusClass}">${esc(compliance.preferenceCenter)}</span>
          </div>
        </div>
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Opt-outs</div>
          <div class="mkt-brief-value">${compliance.optOutCount}</div>
        </div>
        <div class="mkt-brief-field">
          <div class="mkt-brief-label">Last compliance audit</div>
          <div class="mkt-brief-value">${compliance.lastAudit || '—'}</div>
        </div>
      </div>
      <div class="mkt-brief-section">
        <div class="mkt-brief-label">Legal flags</div>
        <ul class="mkt-compliance-flags">${flagItems}</ul>
      </div>
    </div>
  `;
}

function renderTabPerformance(c) {
  const kpis = c.kpis || null;
  return `
    <div class="mkt-tab-performance">
      <div class="mkt-placeholder-panel">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
        </svg>
        <h4>Performance data — Phase B</h4>
        <p>HubSpot read-only integration will populate email opens, clicks, form submits, and BDR queue depth here. No data available until Phase B is wired.</p>
        ${kpis && kpis.opens > 0 ? `
          <div class="mkt-perf-preview">
            <div class="mkt-tile-kpi-row">
              <div class="mkt-tile-kpi"><span class="mkt-kpi-value">${kpis.opens}</span><span class="mkt-kpi-label">opens (mock)</span></div>
              <div class="mkt-tile-kpi"><span class="mkt-kpi-value">${kpis.clicks}</span><span class="mkt-kpi-label">clicks (mock)</span></div>
              <div class="mkt-tile-kpi"><span class="mkt-kpi-value">${kpis.downloads}</span><span class="mkt-kpi-label">downloads (mock)</span></div>
              <div class="mkt-tile-kpi"><span class="mkt-kpi-value">${kpis.bdrQueued}</span><span class="mkt-kpi-label">BDR queued (mock)</span></div>
            </div>
          </div>
        ` : ''}
      </div>
    </div>
  `;
}

function renderTabApprovalLog(c) {
  const log = c.history || c.approvalLog || [];

  if (log.length === 0) {
    return `<div class="mkt-tab-approval-log"><p class="mkt-empty-msg">No approvals recorded yet.</p></div>`;
  }

  const rows = log.map((entry) => {
    // Real API format: { action, actor, notes, to_decision_state, created_at }
    const date = entry.created_at
      ? new Date(entry.created_at * 1000).toLocaleDateString()
      : '';
    return `
      <div class="mkt-approval-log-row">
        <div class="mkt-approval-log-gate">${esc(entry.action || '')}</div>
        <div class="mkt-approval-log-meta">
          <span>${esc(entry.actor || 'System')}</span>
          ${date ? `<span class="mkt-approval-log-date">${date}</span>` : ''}
          ${entry.to_decision_state ? `<span class="mkt-approval-log-state">${esc(entry.to_decision_state)}</span>` : ''}
        </div>
        ${entry.notes ? `<div class="mkt-approval-log-notes">${esc(entry.notes)}</div>` : ''}
      </div>
    `;
  }).join('');

  return `<div class="mkt-tab-approval-log">${rows}</div>`;
}

// ── Signals Inbox ─────────────────────────────────────────────────────────

const CAMPAIGN_FAMILY_LABELS = {
  obc: 'Outcomes-Based Contracts',
  state_screener: 'State Screener',
  biliteracy: 'Biliteracy',
  reading_growth: 'Reading Growth',
};

function _qualificationBlockHtml(signal) {
  const isDemo = !!signal._isDemo;
  const qual = signal.qualificationJson;

  if (isDemo) return '';

  if (!qual) {
    // Signal has not been qualified yet — show button for in_inbox signals
    const qualBtn = signal.signalStatus === 'in_inbox'
      ? `<button class="mkt-btn-ghost mkt-qual-btn" data-signal-action="qualify"
                 data-signal-id="${esc(signal.id)}" type="button">Qualify</button>`
      : '';
    return `
    <div class="mkt-signal-qual mkt-signal-qual--unqualified">
      <span class="mkt-qual-label">Not yet qualified</span>
      ${qualBtn}
    </div>`;
  }

  const primary = qual.recommendedFamilies && qual.recommendedFamilies[0];
  const topScore = primary
    ? qual.scores?.find((s) => s.campaignFamily === primary.campaignFamily)
    : null;

  // Show top 2 qualifying families (primary + first secondary)
  const visibleScores = (qual.recommendedFamilies || []).slice(0, 2).map((rf) => {
    const score = qual.scores?.find((s) => s.campaignFamily === rf.campaignFamily);
    const pct = Math.round((rf.adjustedScore ?? 0) * 100);
    const passes = score?.passesMinFitScore;
    const tierChip = score?.territoryTier && score.territoryTier !== 'unlisted'
      ? `<span class="mkt-qual-tier mkt-qual-tier--${esc(score.territoryTier)}">${esc(score.territoryTier)}</span>`
      : '';
    const barClass = passes ? 'mkt-qual-bar--pass' : 'mkt-qual-bar--fail';
    const label = CAMPAIGN_FAMILY_LABELS[rf.campaignFamily] || esc(rf.campaignFamily);
    return `
      <div class="mkt-qual-score-row">
        <span class="mkt-qual-family">${label}</span>
        <div class="mkt-qual-bar-wrap">
          <div class="mkt-qual-bar ${barClass}" style="width:${pct}%"></div>
        </div>
        <span class="mkt-qual-pct">${pct}</span>
        ${tierChip}
      </div>`;
  }).join('');

  // Hard filter warning for the declared campaign family
  const declaredScore = qual.scores?.find((s) => s.campaignFamily === signal.campaignFamily);
  const hardFilterWarn = declaredScore && !declaredScore.passedHardFilters
    ? `<span class="mkt-qual-warn" title="State outside known territory for this campaign type">⚠ Outside known territory</span>`
    : '';

  // Mismatch warning: top recommended family differs from declared family
  const mismatchWarn = primary && primary.campaignFamily !== signal.campaignFamily
    ? `<span class="mkt-qual-warn">Suggested: ${esc(CAMPAIGN_FAMILY_LABELS[primary.campaignFamily] || primary.campaignFamily)}</span>`
    : '';

  // Qualify button (re-qualify)
  const reQualBtn = signal.signalStatus === 'in_inbox'
    ? `<button class="mkt-btn-ghost mkt-qual-btn" data-signal-action="qualify"
               data-signal-id="${esc(signal.id)}" type="button">Re-qualify</button>`
    : '';

  const noQualifying = (!qual.recommendedFamilies || qual.recommendedFamilies.length === 0)
    ? `<span class="mkt-qual-none">No qualifying campaign type found</span>`
    : '';

  return `
    <div class="mkt-signal-qual">
      <div class="mkt-qual-header">
        <span class="mkt-qual-label">Qualification</span>
        ${hardFilterWarn}
        ${mismatchWarn}
        ${reQualBtn}
      </div>
      ${noQualifying}
      ${visibleScores}
    </div>`;
}

function _signalCardHtml(signal) {
  const isDemo = !!signal._isDemo;
  const tierClass = signal.urgencyTier === 'hot' ? 'mkt-signal-urgency-hot'
    : signal.urgencyTier === 'enrichment' ? 'mkt-signal-urgency-low'
    : 'mkt-signal-urgency-standard';
  const tierLabel = signal.urgencyTier === 'hot' ? 'Hot'
    : signal.urgencyTier === 'enrichment' ? 'Low priority' : 'Standard';
  const fitDisplay = signal.fitScore !== null
    ? Math.round(Number(signal.fitScore) * 100) : '—';
  const statusBadge = signal.signalStatus !== 'in_inbox' ? `
    <span class="mkt-signal-status-badge mkt-signal-status-${esc(signal.signalStatus)}">
      ${esc(signal.signalStatus)}
    </span>` : '';
  const approvedLink = signal.signalStatus === 'approved' && signal.campaignCandidateId ? `
    <a class="mkt-signal-workspace-link" href="#"
       data-mkt-open-candidate="${esc(signal.campaignCandidateId)}">View campaign workspace →</a>` : '';
  const trainingNote = signal.trainingNotes ? `
    <p class="mkt-signal-training-note">${esc(signal.trainingNotes)}</p>` : '';
  const rulesetBadge = signal.rulesetVersionAtQualification ? `
    <span class="mkt-signal-ruleset-badge">Ruleset ${esc(signal.rulesetVersionAtQualification)}</span>` : '';

  const actionsHtml = isDemo
    ? `<div class="mkt-signal-actions">
        <button class="mkt-btn-primary" type="button" disabled data-coming-soon>Approve →</button>
        <button class="mkt-btn-secondary" type="button" disabled data-coming-soon>Snooze</button>
        <button class="mkt-btn-ghost" type="button" disabled data-coming-soon>Reject</button>
      </div>`
    : signal.signalStatus === 'in_inbox' ? `
      <div class="mkt-signal-actions">
        <button class="mkt-btn-primary" data-signal-action="approve"
                data-signal-id="${esc(signal.id)}" type="button">Approve →</button>
        <button class="mkt-btn-secondary" data-signal-action="snooze-open"
                data-signal-id="${esc(signal.id)}" type="button">Snooze</button>
        <button class="mkt-btn-ghost" data-signal-action="reject-open"
                data-signal-id="${esc(signal.id)}" type="button">Reject</button>
      </div>
      <div class="mkt-signal-snooze-form" data-snooze-for="${esc(signal.id)}" hidden>
        <select class="mkt-signal-snooze-days" aria-label="Snooze duration">
          <option value="7">7 days</option>
          <option value="14" selected>14 days</option>
          <option value="30">30 days</option>
        </select>
        <textarea class="mkt-signal-notes-input" placeholder="Optional note (for training)" rows="2"></textarea>
        <div class="mkt-signal-form-actions">
          <button class="mkt-btn-primary" data-signal-action="snooze-submit"
                  data-signal-id="${esc(signal.id)}" type="button">Snooze</button>
          <button class="mkt-btn-ghost" data-signal-action="snooze-cancel"
                  data-signal-id="${esc(signal.id)}" type="button">Cancel</button>
        </div>
      </div>
      <div class="mkt-signal-reject-form" data-reject-for="${esc(signal.id)}" hidden>
        <textarea class="mkt-signal-notes-input" placeholder="Reason / training note (optional)" rows="2"></textarea>
        <div class="mkt-signal-form-actions">
          <button class="mkt-btn-ghost mkt-btn-danger" data-signal-action="reject-submit"
                  data-signal-id="${esc(signal.id)}" type="button">Reject signal</button>
          <button class="mkt-btn-ghost" data-signal-action="reject-cancel"
                  data-signal-id="${esc(signal.id)}" type="button">Cancel</button>
        </div>
      </div>` : '';

  return `
    <article class="mkt-signal-card mkt-signal-card--${esc(signal.signalStatus)}"
             data-signal-id="${esc(signal.id)}">
      <div class="mkt-signal-head">
        <div class="mkt-signal-title-row">
          ${signal.stateCode ? `<span class="mkt-signal-state">${esc(signal.stateCode)}</span>` : ''}
          <span class="mkt-signal-urgency ${tierClass}">${tierLabel}</span>
          ${statusBadge}
          ${rulesetBadge}
        </div>
        <div class="mkt-signal-fit">
          <span class="mkt-kpi-value">${fitDisplay}</span>
          <span class="mkt-kpi-label">fit</span>
        </div>
      </div>
      <h4 class="mkt-signal-title">${esc(signal.headline)}</h4>
      ${signal.whyFlagged ? `<p class="mkt-signal-summary">${esc(signal.whyFlagged)}</p>` : ''}
      <div class="mkt-signal-footer">
        <span class="mkt-signal-source">${esc(signal.sourceType || 'manual')}</span>
        ${signal.urgencyDeadline ? `<span class="mkt-signal-deadline">Deadline: ${esc(signal.urgencyDeadline)}</span>` : ''}
        ${signal.campaignFamily ? `<span class="mkt-signal-campaign-type">${esc(signal.campaignFamily)}</span>` : ''}
      </div>
      ${(signal.sourceTitle || signal.sourcePublishedAt || signal.sourceAuthor) ? `
      <div class="mkt-signal-provenance">
        ${signal.sourceTitle ? `<span class="mkt-signal-provenance-title">${esc(signal.sourceTitle)}</span>` : ''}
        ${signal.sourcePublishedAt ? `<span class="mkt-signal-provenance-date">${esc(signal.sourcePublishedAt)}</span>` : ''}
        ${signal.sourceAuthor ? `<span class="mkt-signal-provenance-author">${esc(signal.sourceAuthor)}</span>` : ''}
      </div>` : ''}
      ${_qualificationBlockHtml(signal)}
      ${approvedLink}
      ${trainingNote}
      ${actionsHtml}
    </article>`;
}

export function renderMarketingSignals(signals = []) {
  const completedRuns = MKT_SIGNAL_TREE_STATE.pipelineRuns
    .filter((run) => ['succeeded', 'skipped', 'partial_complete'].includes(run.status));
  return renderSignalInboxTree(signals, {
    mode: MKT_SIGNAL_TREE_STATE.mode,
    sort: MKT_SIGNAL_TREE_STATE.sort,
    query: MKT_SIGNAL_TREE_STATE.query,
    filters: MKT_SIGNAL_TREE_STATE.filters,
    selectedId: MKT_SIGNAL_TREE_STATE.selectedId,
    collapsed: readCollapsedSignalGroups(),
    hideUnsupported: MKT_SIGNAL_TREE_STATE.hideUnsupported,
    emptyMessage: completedRuns.length
      ? `Last ${Math.min(3, completedRuns.length)} pipeline runs produced 0 signals. Configure scout connectors to start ingesting data.`
      : null,
  });
}

// ── Approval Queue ────────────────────────────────────────────────────────

export function renderMarketingApprovals(approvals = []) {
  if (approvals.length === 0) {
    return `
      <section class="mkt-section">
        <div class="mkt-section-header">
          <h3 class="mkt-section-title">Approval Queue</h3>
        </div>
        <div class="mkt-empty-state">
          <h4>No approvals waiting</h4>
          <p>New approvals will appear here when the workflow creates them.</p>
        </div>
      </section>
    `;
  }

  return `
    <section class="mkt-section">
      <div class="mkt-section-header">
        <h3 class="mkt-section-title">Approval Queue</h3>
      </div>
      <div class="mkt-approvals-list">${approvals.map(_renderUnifiedApprovalCard).join('')}</div>
    </section>
  `;
}

function _parseApprovalPayload(a) {
  if (!a.payload) return {};
  if (typeof a.payload === 'object') return a.payload;
  try { return JSON.parse(a.payload); } catch { return {}; }
}

// Internal: renders a PIPE4 approval card with pipeline/node/signal context.
function _renderPipe4ApprovalCard(a) {
  const p4 = a.pipe4Context || {};
  const ctx = p4.context || {};
  const requestedAt = a.createdAt
    ? new Date(a.createdAt).toLocaleString()
    : '—';
  const pipelineLabel = [p4.pipeline_name, p4.node_label].filter(Boolean).join(' — ') || 'Pipeline gate';
  const runHref = p4.pipeline_run_id
    ? `#pipelines/runs/${esc(p4.pipeline_run_id)}`
    : null;

  // Signals section
  let signalSection = '';
  if (ctx.signal_count > 0) {
    const districts = (ctx.districts || []).map(esc).join(', ') || '—';
    const codes = (ctx.reason_codes || []).map(esc).join(', ') || '—';
    signalSection = `
      <div class="mkt-pipe4-signals">
        <span class="mkt-pipe4-label">Signals</span>
        <span class="mkt-pipe4-value">${ctx.signal_count} qualified</span>
        <span class="mkt-pipe4-sep">·</span>
        <span class="mkt-pipe4-label">Districts</span>
        <span class="mkt-pipe4-value">${districts}</span>
        <span class="mkt-pipe4-sep">·</span>
        <span class="mkt-pipe4-label">Codes</span>
        <span class="mkt-pipe4-value">${codes}</span>
      </div>`;
  } else {
    signalSection = `<div class="mkt-pipe4-empty">No signals qualified this run</div>`;
  }

  const evidenceSection = ctx.evidence_quote
    ? `<blockquote class="mkt-pipe4-evidence">${esc(ctx.evidence_quote)}</blockquote>`
    : '';
  const briefSection = ctx.brief_preview
    ? `<div class="mkt-pipe4-brief"><span class="mkt-pipe4-label">Brief</span> ${esc(ctx.brief_preview)}</div>`
    : '';

  return `
    <article class="mkt-approval-card mkt-pipe4-card" data-unified-approval-id="${esc(String(a.id))}">
      <div class="mkt-approval-head">
        <div class="mkt-approval-title-row">
          <span class="mkt-badge mkt-badge-pipe4">Pipeline gate</span>
          <span class="mkt-approval-campaign">${esc(pipelineLabel)}</span>
        </div>
        <span class="mkt-pill mkt-pill-pending">Pending</span>
      </div>
      ${signalSection}
      ${evidenceSection}
      ${briefSection}
      <div class="mkt-approval-meta">
        <span>Requested: ${esc(requestedAt)}</span>
        ${p4.node_id ? `<span>Node: ${esc(p4.node_id)}</span>` : ''}
      </div>
      <div class="mkt-signal-actions">
        <button class="mkt-btn-primary" type="button" data-approve-id="${esc(String(a.id))}">Approve</button>
        <button class="mkt-btn-ghost" type="button" data-reject-id="${esc(String(a.id))}">Reject</button>
        ${runHref ? `<a class="mkt-btn-link" href="${runHref}">View pipeline run →</a>` : ''}
      </div>
    </article>
  `;
}

// Internal: renders a single card for a real unified approval (workflow_gate, pre_run, writing_gate_2, etc.)
function _renderUnifiedApprovalCard(a) {
  // PIPE4 gate: has pipe4Context.pipeline_run_id
  if (a.pipe4Context && a.pipe4Context.pipeline_run_id) {
    return _renderPipe4ApprovalCard(a);
  }

  const requestedAt = a.created_at
    ? new Date(a.created_at * 1000).toLocaleString()
    : '—';

  if (a.approval_kind === 'writing_gate_2') {
    const payload = _parseApprovalPayload(a);
    const draftId = payload.draftId ?? null;
    const excerpt = payload.contentExcerpt ?? null;
    return `
      <article class="mkt-approval-card" data-unified-approval-id="${esc(a.id)}">
        <div class="mkt-approval-head">
          <div class="mkt-approval-title-row">
            <span class="mkt-badge mkt-badge-content">Content review</span>
            <span class="mkt-approval-campaign">${esc(a.title || 'Review draft')}</span>
          </div>
          <span class="mkt-pill mkt-pill-pending">Pending</span>
        </div>
        ${a.description ? `<div class="mkt-approval-deliverable">${esc(a.description)}</div>` : ''}
        ${excerpt ? `<div class="mkt-approval-excerpt">${esc(excerpt)}${payload.contentExcerpt && payload.contentExcerpt.length >= 300 ? '…' : ''}</div>` : ''}
        <div class="mkt-approval-meta">
          <span>Requested: ${esc(requestedAt)}</span>
          ${payload.versionNumber ? `<span>Version ${esc(String(payload.versionNumber))}</span>` : ''}
        </div>
        <div class="mkt-signal-actions">
          ${draftId ? `<button class="mkt-btn-secondary" type="button" data-ws-draft-id="${esc(String(draftId))}">Open draft →</button>` : ''}
          <button class="mkt-btn-primary" type="button" data-approve-id="${esc(a.id)}">Approve</button>
          <button class="mkt-btn-ghost" type="button" data-reject-id="${esc(a.id)}">Request changes</button>
        </div>
      </article>
    `;
  }

  const kindLabel = a.approval_kind === 'workflow_gate' ? 'Workflow gate'
    : a.approval_kind === 'pre_run' ? 'Pre-run gate'
    : 'Approval';
  return `
    <article class="mkt-approval-card" data-unified-approval-id="${esc(a.id)}">
      <div class="mkt-approval-head">
        <div class="mkt-approval-title-row">
          <span class="mkt-badge mkt-badge-neutral">${esc(kindLabel)}</span>
          <span class="mkt-approval-campaign">${esc(a.title || 'Approval required')}</span>
        </div>
        <span class="mkt-pill mkt-pill-pending">Pending</span>
      </div>
      <div class="mkt-approval-gate">${esc(a.requested_action || '')}</div>
      ${a.description ? `<div class="mkt-approval-deliverable">${esc(a.description)}</div>` : ''}
      <div class="mkt-approval-meta">
        <span>Requested: ${esc(requestedAt)}</span>
      </div>
      <div class="mkt-signal-actions">
        <button class="mkt-btn-primary" type="button" data-approve-id="${esc(a.id)}">Approve</button>
        <button class="mkt-btn-ghost" type="button" data-reject-id="${esc(a.id)}">Reject</button>
      </div>
    </article>
  `;
}

// ── Shell loader functions (called by home.js) ────────────────────────────

export async function loadMarketingDashboard(container) {
  if (!container) return;
  container.innerHTML = `
    <section class="mkt-section">
      <div class="mkt-section-header">
        <h3 class="mkt-section-title">Marketing Dashboard</h3>
      </div>
      <div class="mkt-placeholder-panel">
        <p>Loading live campaign metrics…</p>
      </div>
    </section>
  `;
  _wireDashboardActions(container);
  try {
    const [campaigns, approvals, signalResult] = await Promise.all([
      fetchMarketingCampaignsApi(),
      listApprovalsApi({ status: 'pending' }).catch(() => []),
      listSignalQueueApi({ status: 'in_inbox' }).catch(() => null),
    ]);
    const liveCandidates = campaigns.map((c) => _normalizeCampaignCandidate(c));
    _syncCampaignMap(liveCandidates);
    const pendingCount = Array.isArray(approvals) ? approvals.length : 0;
    const signalsCount = signalResult && typeof signalResult.total === 'number'
      ? signalResult.total : 0;
    container.innerHTML = renderMarketingDashboard(
      liveCandidates,
      pendingCount,
      signalsCount,
    );
    _wireDashboardActions(container);
  } catch (err) {
    container.innerHTML = `
      <section class="mkt-section">
        <div class="mkt-section-header">
          <h3 class="mkt-section-title">Marketing Dashboard</h3>
        </div>
        <div class="mkt-empty-state">
          <h4>Dashboard unavailable</h4>
          <p>${esc(err?.message || 'Live dashboard data could not be loaded.')}</p>
        </div>
      </section>
    `;
  }
}

export async function loadMarketingCampaigns(container) {
  if (!container) return;
  container.innerHTML = `
    <section class="mkt-section">
      <div class="mkt-section-header">
        <h3 class="mkt-section-title">Campaigns</h3>
      </div>
      <div class="mkt-placeholder-panel">
        <p>Loading live campaign candidates…</p>
      </div>
    </section>
  `;
  _wireCampaignActions(container);
  try {
    const liveCandidates = (await fetchMarketingCampaignsApi()).map((c) => _normalizeCampaignCandidate(c));
    _syncCampaignMap(liveCandidates);

    const storedId = (() => {
      try { return localStorage.getItem(MKT_CAMPAIGN_KEY); } catch { return null; }
    })();

    const resolvedSelectedId = storedId && liveCandidates.some((c) => String(c.id) === String(storedId))
      ? storedId
      : _selectDefaultCampaignId(liveCandidates);
    if (resolvedSelectedId) {
      try { localStorage.setItem(MKT_CAMPAIGN_KEY, String(resolvedSelectedId)); } catch {}
    }
    container.innerHTML = renderMarketingCampaigns(liveCandidates, resolvedSelectedId);
    _wireCampaignActions(container);
    if (resolvedSelectedId && _campaignMap.has(_campaignMapKey(resolvedSelectedId))) {
      const campaign = _campaignMap.get(_campaignMapKey(resolvedSelectedId));
      _wireWorkspaceTabs(container, campaign);
      _wireWorkspaceActions(container, campaign);
      _wireWritingStudioBridge(container, campaign);
    }
  } catch (err) {
    container.innerHTML = `
      <section class="mkt-section">
        <div class="mkt-section-header">
          <h3 class="mkt-section-title">Campaigns</h3>
        </div>
        <div class="mkt-empty-state">
          <h4>Campaigns unavailable</h4>
          <p>${esc(err?.message || 'Live campaign data could not be loaded.')}</p>
        </div>
      </section>
    `;
  }
}

export async function loadMarketingSignals(container) {
  if (!container) return;
  container.innerHTML = `
    <section class="mkt-section">
      <div class="mkt-signals-hero">
        <h3 class="mkt-signals-title">Signals Inbox</h3>
        <p class="mkt-signals-sub">Loading live signals…</p>
      </div>
      <div class="mkt-placeholder-panel">
        <p>Fetching the current signal tree…</p>
      </div>
    </section>
  `;
  _wireSignalActions(container);
  MKT_SIGNAL_TREE_STATE.mode = readSignalGroupMode();
  try {
    const [result, pipelines] = await Promise.all([
      listSignalQueueApi({ limit: 200 }),
      listPipelinesApi({ limit: 12 }).catch(() => []),
    ]);
    const realSignals = result.signals || [];
    MKT_SIGNAL_TREE_STATE.pipelineRuns = _latestPipelineRuns(pipelines);
    MKT_SIGNAL_TREE_STATE.signals = realSignals;
    MKT_SIGNAL_TREE_STATE.selectedId = realSignals[0]?.id || null;
    container.innerHTML = renderMarketingSignals(realSignals);
  } catch (err) {
    container.innerHTML = `
      <section class="mkt-section">
        <div class="mkt-signals-hero">
          <h3 class="mkt-signals-title">Signals Inbox</h3>
          <p class="mkt-signals-sub">Unable to load live signals.</p>
        </div>
        <div class="mkt-empty-state">
          <h4>Signals unavailable</h4>
          <p>${esc(err?.message || 'Live signals could not be loaded.')}</p>
        </div>
      </section>
    `;
  }
}

function _renderSignalTreeState(container) {
  container.innerHTML = renderMarketingSignals(MKT_SIGNAL_TREE_STATE.signals, false);
}

async function _refreshSignalTree(container) {
  const [result, pipelines] = await Promise.all([
    listSignalQueueApi({ limit: 200 }),
    listPipelinesApi({ limit: 12 }).catch(() => []),
  ]);
  MKT_SIGNAL_TREE_STATE.pipelineRuns = _latestPipelineRuns(pipelines);
  MKT_SIGNAL_TREE_STATE.signals = result.signals || [];
  const visible = filterSignals(MKT_SIGNAL_TREE_STATE.signals.map(normalizeSignal), {
    query: MKT_SIGNAL_TREE_STATE.query,
    filters: MKT_SIGNAL_TREE_STATE.filters,
  });
  if (!visible.some((signal) => String(signal.id) === String(MKT_SIGNAL_TREE_STATE.selectedId))) {
    MKT_SIGNAL_TREE_STATE.selectedId = visible[0]?.id || MKT_SIGNAL_TREE_STATE.signals[0]?.id || null;
  }
  _renderSignalTreeState(container);
}

function _latestPipelineRuns(pipelines = []) {
  return (pipelines || [])
    .map((pipeline) => pipeline.latestRun ? {
      ...pipeline.latestRun,
      pipelineName: pipeline.name,
      pipelineId: pipeline.id,
    } : null)
    .filter(Boolean)
    .slice(0, 3);
}

function _toggleSignalFilter(category, value) {
  const current = MKT_SIGNAL_TREE_STATE.filters[category] || [];
  MKT_SIGNAL_TREE_STATE.filters[category] = current.includes(value)
    ? current.filter((item) => item !== value)
    : [...current, value];
}

function _wireSignalActions(container) {
  if (container.dataset.signalsWired === 'true') return;
  container.dataset.signalsWired = 'true';
  container.addEventListener('click', async (e) => {
    const groupBtn = e.target.closest('[data-signal-group]');
    if (groupBtn) {
      MKT_SIGNAL_TREE_STATE.mode = groupBtn.dataset.signalGroup || 'state';
      writeSignalGroupMode(MKT_SIGNAL_TREE_STATE.mode);
      _renderSignalTreeState(container);
      return;
    }

    const filterBtn = e.target.closest('[data-signal-filter]');
    if (filterBtn) {
      _toggleSignalFilter(filterBtn.dataset.signalFilter, filterBtn.dataset.filterValue);
      MKT_SIGNAL_TREE_STATE.selectedId = null;
      _renderSignalTreeState(container);
      return;
    }

    const folderBtn = e.target.closest('[data-signal-folder-toggle]');
    if (folderBtn) {
      const key = folderBtn.dataset.signalFolderToggle;
      const collapsed = readCollapsedSignalGroups();
      collapsed[key] = !collapsed[key];
      if (!collapsed[key]) delete collapsed[key];
      writeCollapsedSignalGroups(collapsed);
      _renderSignalTreeState(container);
      return;
    }

    const row = e.target.closest('[data-signal-row]');
    if (row) {
      MKT_SIGNAL_TREE_STATE.selectedId = row.dataset.signalRow;
      _renderSignalTreeState(container);
      return;
    }

    const btn = e.target.closest('[data-signal-action]');
    if (!btn) return;
    const action = btn.dataset.signalAction;
    const signalId = btn.dataset.signalId;

    if (action === 'add-open') {
      const form = container.querySelector('.mkt-signal-add-form');
      if (form) { form.hidden = false; btn.hidden = true; }
      return;
    }
    if (action === 'add-cancel') {
      const form = container.querySelector('.mkt-signal-add-form');
      if (form) form.hidden = true;
      const addBtn = container.querySelector('[data-signal-action="add-open"]');
      if (addBtn) addBtn.hidden = false;
      return;
    }
    if (action === 'add-submit') {
      const form = container.querySelector('.mkt-signal-add-form');
      if (!form) return;
      const headline = form.querySelector('[name="headline"]')?.value?.trim();
      if (!headline) { form.querySelector('[name="headline"]')?.focus(); return; }
      const payload = {
        headline,
        campaignFamily: form.querySelector('[name="campaignFamily"]')?.value,
        stateCode: form.querySelector('[name="stateCode"]')?.value?.trim().toUpperCase() || null,
        evidence: form.querySelector('[name="evidence"]')?.value?.trim() || null,
        urgencyTier: form.querySelector('[name="urgencyTier"]')?.value || 'standard',
        urgencyDeadline: form.querySelector('[name="urgencyDeadline"]')?.value || null,
      };
      btn.disabled = true;
      btn.textContent = 'Adding…';
      try {
        await createSignalApi(payload);
        await _refreshSignalTree(container);
      } catch (err) {
        btn.disabled = false;
        btn.textContent = 'Add Signal';
        const errEl = form.querySelector('.mkt-signal-add-error')
          || (() => { const d = document.createElement('p'); d.className = 'mkt-signal-add-error'; form.appendChild(d); return d; })();
        errEl.textContent = err.message || 'Failed to add signal.';
      }
      return;
    }

    // Card-level actions — find the card
    if (!signalId) return;
    const card = container.querySelector(`[data-signal-id="${CSS.escape(signalId)}"]`);

    if (action === 'snooze-open') {
      card?.querySelector(`[data-snooze-for="${CSS.escape(signalId)}"]`)?.removeAttribute('hidden');
      return;
    }
    if (action === 'snooze-cancel') {
      card?.querySelector(`[data-snooze-for="${CSS.escape(signalId)}"]`)?.setAttribute('hidden', '');
      return;
    }
    if (action === 'reject-open') {
      card?.querySelector(`[data-reject-for="${CSS.escape(signalId)}"]`)?.removeAttribute('hidden');
      return;
    }
    if (action === 'reject-cancel') {
      card?.querySelector(`[data-reject-for="${CSS.escape(signalId)}"]`)?.setAttribute('hidden', '');
      return;
    }

    if (action === 'approve') {
      btn.disabled = true; btn.textContent = 'Approving…';
      try {
        const res = await approveSignalApi(signalId);
        await _refreshSignalTree(container);
        if (res.candidateId) {
          const toast = document.createElement('div');
          toast.className = 'mkt-signal-toast';
          toast.innerHTML = `Campaign workspace created. <a href="#" data-mkt-open-candidate="${esc(res.candidateId)}">Open workspace →</a>`;
          container.querySelector('.mkt-signals-list')?.prepend(toast);
          setTimeout(() => toast.remove(), 6000);
        }
      } catch (err) {
        btn.disabled = false; btn.textContent = 'Approve →';
        const errEl = document.createElement('p');
        errEl.className = 'mkt-signal-inline-error';
        errEl.textContent = err.message || 'Approve failed.';
        btn.parentElement?.appendChild(errEl);
        setTimeout(() => errEl.remove(), 4000);
      }
      return;
    }

    if (action === 'snooze-submit') {
      const snoozeForm = card?.querySelector(`[data-snooze-for="${CSS.escape(signalId)}"]`);
      const days = Number(snoozeForm?.querySelector('.mkt-signal-snooze-days')?.value || 14);
      const notes = snoozeForm?.querySelector('.mkt-signal-notes-input')?.value?.trim() || null;
      btn.disabled = true; btn.textContent = 'Snoozing…';
      try {
        await snoozeSignalApi(signalId, { days, trainingNotes: notes });
        await _refreshSignalTree(container);
      } catch (err) {
        btn.disabled = false; btn.textContent = 'Snooze';
        const errEl = document.createElement('p');
        errEl.className = 'mkt-signal-inline-error';
        errEl.textContent = err.message || 'Snooze failed.';
        snoozeForm?.appendChild(errEl);
        setTimeout(() => errEl.remove(), 4000);
      }
      return;
    }

    if (action === 'reject-submit') {
      const rejectForm = card?.querySelector(`[data-reject-for="${CSS.escape(signalId)}"]`);
      const notes = rejectForm?.querySelector('.mkt-signal-notes-input')?.value?.trim() || null;
      btn.disabled = true; btn.textContent = 'Rejecting…';
      try {
        await rejectSignalApi(signalId, { trainingNotes: notes });
        await _refreshSignalTree(container);
      } catch (err) {
        btn.disabled = false; btn.textContent = 'Reject signal';
        const errEl = document.createElement('p');
        errEl.className = 'mkt-signal-inline-error';
        errEl.textContent = err.message || 'Reject failed.';
        rejectForm?.appendChild(errEl);
        setTimeout(() => errEl.remove(), 4000);
      }
      return;
    }

    if (action === 'qualify') {
      btn.disabled = true; btn.textContent = 'Qualifying…';
      try {
        const res = await qualifySignalApi(signalId);
        // Patch just this card's qualification block in-place
        const qualBlock = card?.querySelector('.mkt-signal-qual');
        if (qualBlock && res.signal) {
          const tmpDiv = document.createElement('div');
          tmpDiv.innerHTML = _qualificationBlockHtml(res.signal);
          const newBlock = tmpDiv.firstElementChild;
          if (newBlock) qualBlock.replaceWith(newBlock);
        }
      } catch (err) {
        btn.disabled = false; btn.textContent = btn.dataset.origText || 'Qualify';
        const errEl = document.createElement('p');
        errEl.className = 'mkt-signal-inline-error';
        errEl.textContent = err.message || 'Qualification failed.';
        btn.parentElement?.appendChild(errEl);
        setTimeout(() => errEl.remove(), 4000);
      }
      return;
    }

    if (action === 'archive') {
      btn.disabled = true; btn.textContent = 'Archiving…';
      try {
        await archiveSignalApi(signalId);
        await _refreshSignalTree(container);
      } catch (err) {
        btn.disabled = false; btn.textContent = 'Archive';
        const errEl = document.createElement('p');
        errEl.className = 'mkt-signal-inline-error';
        errEl.textContent = err.message || 'Archive failed.';
        btn.parentElement?.appendChild(errEl);
        setTimeout(() => errEl.remove(), 4000);
      }
      return;
    }
  });

  container.addEventListener('input', (e) => {
    if (!e.target.matches('[data-signal-search]')) return;
    MKT_SIGNAL_TREE_STATE.query = e.target.value || '';
    MKT_SIGNAL_TREE_STATE.selectedId = null;
    _renderSignalTreeState(container);
  });

  container.addEventListener('keydown', (e) => {
    if (!e.target.matches('[data-signal-search]') || e.key !== 'Escape') return;
    MKT_SIGNAL_TREE_STATE.query = '';
    e.target.value = '';
    _renderSignalTreeState(container);
  });

  container.addEventListener('change', (e) => {
    if (e.target.matches('[data-signal-sort]')) {
      MKT_SIGNAL_TREE_STATE.sort = e.target.value === 'urgency' ? 'urgency' : 'newest';
      _renderSignalTreeState(container);
      return;
    }
    // DIST4: hide-unsupported toggle — persists in localStorage, default OFF
    if (e.target.matches('[data-signal-hide-unsupported]')) {
      MKT_SIGNAL_TREE_STATE.hideUnsupported = e.target.checked;
      writeHideUnsupported(e.target.checked);
      MKT_SIGNAL_TREE_STATE.selectedId = null;
      _renderSignalTreeState(container);
    }
  });
}

export async function loadMarketingApprovals(container) {
  if (!container) return;
  container.innerHTML = `
    <section class="mkt-section">
      <div class="mkt-section-header">
        <h3 class="mkt-section-title">Approval Queue</h3>
      </div>
      <div class="mkt-placeholder-panel">
        <p>Loading pending approvals…</p>
      </div>
    </section>
  `;

  try {
    const res = await listApprovalsApi({ status: 'pending' });
    const liveApprovals = Array.isArray(res) ? res : [];
    container.innerHTML = renderMarketingApprovals(liveApprovals);
    if (liveApprovals.length === 0) return;
    container.querySelectorAll('[data-approve-id]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.approveId;
        btn.disabled = true;
        btn.textContent = 'Approving…';
        try {
          await decideApprovalApi(id, { decision: 'approve' });
          await loadMarketingApprovals(container);
        } catch {
          btn.disabled = false;
          btn.textContent = 'Approve';
        }
      });
    });

    container.querySelectorAll('[data-reject-id]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.rejectId;
        const originalLabel = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Submitting…';
        try {
          await decideApprovalApi(id, { decision: 'reject' });
          await loadMarketingApprovals(container);
        } catch {
          btn.disabled = false;
          btn.textContent = originalLabel;
        }
      });
    });

    container.querySelectorAll('[data-ws-draft-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const draftId = Number(btn.dataset.wsDraftId);
        if (draftId) _navigateToWritingStudio(draftId);
      });
    });

  } catch (err) {
    container.innerHTML = `
      <section class="mkt-section">
        <div class="mkt-section-header">
          <h3 class="mkt-section-title">Approval Queue</h3>
        </div>
        <div class="mkt-empty-state">
          <h4>Approvals unavailable</h4>
          <p>${esc(err?.message || 'Live approvals could not be loaded.')}</p>
        </div>
      </section>
    `;
  }
}

// ── Action wiring ─────────────────────────────────────────────────────────

function _wireDashboardActions(container) {
  container.querySelectorAll('[data-mkt-open-campaign], [data-mkt-tile-id]').forEach((el) => {
    el.addEventListener('click', (e) => {
      const id = el.dataset.mktOpenCampaign || el.dataset.mktTileId;
      if (!id) return;
      e.stopPropagation();
      try { localStorage.setItem(MKT_CAMPAIGN_KEY, id); } catch {}
      setState('view', MARKETING_CAMPAIGNS_VIEW);
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') el.click();
    });
  });

  container.querySelectorAll('[data-mkt-nav]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const view = btn.dataset.mktNav;
      if (view) setState('view', view);
    });
  });
}

function _updateActiveCampaignCard(container, campaign) {
  let card = container.querySelector('.mkt-active-campaign-card');
  if (!card) {
    card = document.createElement('div');
    card.className = 'mkt-panel-card mkt-active-campaign-card';
    const browserCard = container.querySelector('.mkt-campaigns-browser-card');
    if (browserCard) browserCard.before(card);
  }
  card.innerHTML = _renderActiveCampaignCardInner(campaign);
}

function _wireCampaignActions(container) {
  container.querySelectorAll('[data-mkt-open-campaign]').forEach((el) => {
    el.addEventListener('click', () => {
      const id = el.dataset.mktOpenCampaign;
      try { localStorage.setItem(MKT_CAMPAIGN_KEY, id); } catch {}
      const campaign = _campaignMap.get(_campaignMapKey(id));
      if (!campaign) return;
      container.querySelector('[data-initiation-modal]')?.remove();
      const pane = container.querySelector('.mkt-workspace-pane');
      if (pane) {
        pane.innerHTML = renderCampaignWorkspace(campaign);
        _wireWorkspaceTabs(container, campaign);
        _wireWorkspaceActions(container, campaign);
        _wireWritingStudioBridge(container, campaign);
      }
      _updateActiveCampaignCard(container, campaign);
      container.querySelectorAll('.mkt-campaign-list-item').forEach((item) => {
        item.classList.toggle('active', String(item.dataset.mktOpenCampaign) === String(id));
      });
      _maybeOpenInitiationProposal(container, campaign);
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') el.click();
    });
  });

  container.querySelectorAll('[data-mkt-initiation-open]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.mktInitiationOpen;
      const campaign = _campaignMap.get(_campaignMapKey(id));
      if (campaign) _maybeOpenInitiationProposal(container, campaign);
    });
  });

  const storedId = (() => {
    try { return localStorage.getItem(MKT_CAMPAIGN_KEY); } catch { return null; }
  })();
  if (storedId) {
    const campaign = _campaignMap.get(_campaignMapKey(storedId));
    if (campaign) {
      _wireWorkspaceTabs(container, campaign);
      _wireWorkspaceActions(container, campaign);
      _wireWritingStudioBridge(container, campaign);
      _maybeOpenInitiationProposal(container, campaign);
    }
  }
}

async function _loadAndRenderAssetsTab(container, campaign) {
  if (!campaign._fromApi || !campaign.id) return;
  const content = container.querySelector('[data-mkt-tab-content]');
  if (!content) return;
  // Only update if Assets tab is currently active
  const activeBtn = container.querySelector('[data-mkt-tab].active');
  if (!activeBtn || activeBtn.dataset.mktTab !== 'assets') return;

  // Fetch deliverables and asset links in parallel
  const [deliverablesResult, linksResult] = await Promise.allSettled([
    listCampaignDeliverablesApi(campaign.id),
    listCampaignAssetLinksApi(campaign.id),
  ]);

  if (deliverablesResult.status === 'fulfilled') {
    _deliverablesCache.set(campaign.id, Array.isArray(deliverablesResult.value) ? deliverablesResult.value : []);
  } else if (!_deliverablesCache.has(campaign.id)) {
    _deliverablesCache.set(campaign.id, []);
  }

  if (linksResult.status === 'fulfilled') {
    _assetLinksCache.set(campaign.id, Array.isArray(linksResult.value) ? linksResult.value : []);
  } else if (!_assetLinksCache.has(campaign.id)) {
    _assetLinksCache.set(campaign.id, []);
  }

  // Only re-render if Assets tab is still active (user may have switched)
  const stillActiveBtn = container.querySelector('[data-mkt-tab].active');
  if (stillActiveBtn && stillActiveBtn.dataset.mktTab === 'assets') {
    content.innerHTML = renderWorkspaceTab(campaign, 'assets');
    _wireWritingStudioBridge(container, campaign);
    _wireAssetTabActions(container, campaign);
  }
}

async function _loadAndRenderBriefTab(container, campaign) {
  if (!campaign._fromApi || !campaign.id) return;
  const content = container.querySelector('[data-mkt-tab-content]');
  if (!content) return;
  const activeBtn = container.querySelector('[data-mkt-tab].active');
  if (!activeBtn || activeBtn.dataset.mktTab !== 'brief') return;

  try {
    const briefRecord = await getCampaignBriefApi(campaign.id);
    _briefCache.set(campaign.id, briefRecord ?? null);
  } catch {
    if (!_briefCache.has(campaign.id)) _briefCache.set(campaign.id, null);
  }

  const stillActiveBtn = container.querySelector('[data-mkt-tab].active');
  if (stillActiveBtn && stillActiveBtn.dataset.mktTab === 'brief') {
    content.innerHTML = renderWorkspaceTab(campaign, 'brief');
    _wireBriefTabActions(container, campaign);
    _wireWritingStudioBridge(container, campaign);
  }
}

function _wireBriefTabActions(container, campaign) {
  container.querySelectorAll('[data-brief-action]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.briefAction;
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Assembling…';
      try {
        await assembleCampaignBriefApi(campaign.id);
        _briefCache.delete(campaign.id); // force reload
        const content = container.querySelector('[data-mkt-tab-content]');
        if (content) {
          await _loadAndRenderBriefTab(container, campaign);
        }
      } catch (err) {
        btn.disabled = false;
        btn.textContent = origText;
        console.error('[brief] assemble failed:', err?.message ?? err);
      }
    });
  });
}

function _wireAssetTabActions(container, campaign) {
  // Unlink buttons
  container.querySelectorAll('[data-unlink-asset]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const assetId = btn.dataset.unlinkAsset;
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        await deleteCampaignAssetLinkApi(campaign.id, assetId);
        _assetLinksCache.delete(campaign.id);
        await _loadAndRenderAssetsTab(container, campaign);
      } catch (err) {
        btn.disabled = false;
        btn.textContent = origText;
        console.error('[assets] unlink failed:', err?.message ?? err);
      }
    });
  });

  // Link existing asset picker — shows a small inline search UI
  const linkBtn = container.querySelector('[data-asset-action="link"]');
  if (linkBtn) {
    linkBtn.addEventListener('click', async () => {
      linkBtn.disabled = true;
      linkBtn.textContent = 'Loading…';
      let allAssets = [];
      try {
        allAssets = await listContentAssetsApi({ includeArchived: false });
      } catch {
        linkBtn.disabled = false;
        linkBtn.textContent = '+ Link asset';
        return;
      }
      const linked = _assetLinksCache.get(campaign.id) || [];
      const linkedIds = new Set(linked.map((a) => String(a.id)));
      const available = allAssets.filter((a) => !linkedIds.has(String(a.id)));
      _showAssetPicker(container, campaign, available, linkBtn);
    });
  }

  // Add new asset inline form
  const addBtn = container.querySelector('[data-asset-action="add"]');
  if (addBtn) {
    addBtn.addEventListener('click', () => {
      _showAddAssetForm(container, campaign, addBtn);
    });
  }
}

function _showAssetPicker(container, campaign, assets, triggerBtn) {
  const existing = container.querySelector('.mkt-asset-picker');
  if (existing) { existing.remove(); triggerBtn.disabled = false; triggerBtn.textContent = '+ Link asset'; return; }

  const picker = document.createElement('div');
  picker.className = 'mkt-asset-picker';
  if (assets.length === 0) {
    picker.innerHTML = `<p class="mkt-asset-picker-empty">No unlinkable assets in the registry yet. Use "Add new asset" to create one.</p>`;
  } else {
    picker.innerHTML = `
      <input class="mkt-asset-picker-search" type="text" placeholder="Search assets…" autocomplete="off" />
      <ul class="mkt-asset-picker-list">
        ${assets.map((a) => `
          <li class="mkt-asset-picker-item" data-pick-asset="${esc(String(a.id))}">
            <span class="mkt-asset-picker-title">${esc(a.title)}</span>
            <span class="mkt-asset-picker-type">${esc(a.assetType || '')}</span>
            <span class="mkt-pill ${_assetStatusPill(a.status)}">${esc(a.status)}</span>
          </li>
        `).join('')}
      </ul>
    `;
  }
  const colSection = container.querySelector('.mkt-collateral-section');
  if (colSection) colSection.appendChild(picker);
  else container.querySelector('[data-mkt-tab-content]')?.appendChild(picker);

  const searchInput = picker.querySelector('.mkt-asset-picker-search');
  if (searchInput) {
    searchInput.focus();
    searchInput.addEventListener('input', () => {
      const q = searchInput.value.toLowerCase();
      picker.querySelectorAll('.mkt-asset-picker-item').forEach((li) => {
        const title = (li.querySelector('.mkt-asset-picker-title')?.textContent || '').toLowerCase();
        li.style.display = title.includes(q) ? '' : 'none';
      });
    });
  }

  picker.querySelectorAll('[data-pick-asset]').forEach((li) => {
    li.addEventListener('click', async () => {
      const assetId = li.dataset.pickAsset;
      li.style.opacity = '0.5';
      try {
        await createCampaignAssetLinkApi({ campaignId: campaign.id, assetId: Number(assetId) });
        _assetLinksCache.delete(campaign.id);
        picker.remove();
        triggerBtn.disabled = false;
        triggerBtn.textContent = '+ Link asset';
        await _loadAndRenderAssetsTab(container, campaign);
      } catch (err) {
        li.style.opacity = '1';
        console.error('[assets] link failed:', err?.message ?? err);
      }
    });
  });

  // Close picker on outside click
  const close = (e) => { if (!picker.contains(e.target) && e.target !== triggerBtn) { picker.remove(); triggerBtn.disabled = false; triggerBtn.textContent = '+ Link asset'; document.removeEventListener('click', close); } };
  setTimeout(() => document.addEventListener('click', close), 0);
}

function _showAddAssetForm(container, campaign, triggerBtn) {
  const existing = container.querySelector('.mkt-add-asset-form');
  if (existing) { existing.remove(); return; }

  const ASSET_TYPES = ['field_guide','webinar','research_summary','case_study','landing_page',
    'email_sequence','social_copy','op_ed','template','pdf','google_doc','url','other'];

  const form = document.createElement('div');
  form.className = 'mkt-add-asset-form';
  form.innerHTML = `
    <div class="mkt-add-asset-form-row">
      <input class="mkt-add-asset-input" name="title" type="text" placeholder="Asset title *" autocomplete="off" />
      <select class="mkt-add-asset-select" name="assetType">
        <option value="">Type *</option>
        ${ASSET_TYPES.map((t) => `<option value="${esc(t)}">${esc(t.replace(/_/g, ' '))}</option>`).join('')}
      </select>
      <select class="mkt-add-asset-select" name="status">
        ${['draft','ready','needs_validation','needs_design','blocked'].map((s) =>
          `<option value="${esc(s)}">${esc(s.replace(/_/g, ' '))}</option>`).join('')}
      </select>
    </div>
    <div class="mkt-add-asset-form-row">
      <input class="mkt-add-asset-input mkt-add-asset-input-wide" name="sourceUrl" type="url" placeholder="Source URL" autocomplete="off" />
      <input class="mkt-add-asset-input mkt-add-asset-input-wide" name="summary" type="text" placeholder="Summary (shown to Writing Studio)" autocomplete="off" />
    </div>
    <div class="mkt-add-asset-form-actions">
      <button class="mkt-btn-primary mkt-add-asset-save" type="button">Save &amp; link</button>
      <button class="mkt-btn-text mkt-add-asset-cancel" type="button">Cancel</button>
    </div>
  `;

  const colSection = container.querySelector('.mkt-collateral-section');
  if (colSection) colSection.appendChild(form);
  else container.querySelector('[data-mkt-tab-content]')?.appendChild(form);

  form.querySelector('.mkt-add-asset-cancel').addEventListener('click', () => form.remove());

  form.querySelector('.mkt-add-asset-save').addEventListener('click', async () => {
    const title = form.querySelector('[name="title"]').value.trim();
    const assetType = form.querySelector('[name="assetType"]').value;
    const status = form.querySelector('[name="status"]').value || 'draft';
    const sourceUrl = form.querySelector('[name="sourceUrl"]').value.trim() || null;
    const summary = form.querySelector('[name="summary"]').value.trim() || null;
    if (!title || !assetType) { alert('Title and type are required.'); return; }
    const saveBtn = form.querySelector('.mkt-add-asset-save');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    try {
      const asset = await createContentAssetApi({ title, assetType, status, sourceUrl, summary });
      await createCampaignAssetLinkApi({ campaignId: campaign.id, assetId: asset.id });
      _assetLinksCache.delete(campaign.id);
      form.remove();
      await _loadAndRenderAssetsTab(container, campaign);
    } catch (err) {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save & link';
      console.error('[assets] add asset failed:', err?.message ?? err);
    }
  });

  form.querySelector('[name="title"]').focus();
}

function _wireWorkspaceTabs(container, campaign) {
  container.querySelectorAll('[data-mkt-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.mktTab;
      try { localStorage.setItem(MKT_WORKSPACE_TAB_KEY, tab); } catch {}
      container.querySelectorAll('[data-mkt-tab]').forEach((b) => b.classList.toggle('active', b.dataset.mktTab === tab));
      const content = container.querySelector('[data-mkt-tab-content]');
      if (content) {
        content.innerHTML = renderWorkspaceTab(campaign, tab);
        _wireWritingStudioBridge(container, campaign);
      }
      if (tab === 'assets' && campaign._fromApi) {
        _loadAndRenderAssetsTab(container, campaign);
      }
      if (tab === 'brief' && campaign._fromApi) {
        _wireBriefTabActions(container, campaign);
        if (!_briefCache.has(campaign.id)) {
          _loadAndRenderBriefTab(container, campaign);
        }
      }
    });
  });

  // If the initial active tab is assets or brief, trigger the async fetch immediately
  const initialActive = container.querySelector('[data-mkt-tab].active');
  if (initialActive && campaign._fromApi) {
    if (initialActive.dataset.mktTab === 'assets') {
      _loadAndRenderAssetsTab(container, campaign);
    } else if (initialActive.dataset.mktTab === 'brief') {
      _wireBriefTabActions(container, campaign);
      if (!_briefCache.has(campaign.id)) {
        _loadAndRenderBriefTab(container, campaign);
      }
    }
  }
}

function _wireWorkspaceActions(container, campaign) {
  container.querySelectorAll('[data-campaign-action]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.campaignAction;
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        await decideCampaignCandidateApi(campaign.id, { action });
        const campaigns = await fetchMarketingCampaignsApi().catch(() => null);
        if (campaigns) {
          const updated = campaigns.find((c) => c.id === campaign.id);
          if (updated) {
            const merged = _normalizeCampaignCandidate(updated);
            _syncCampaignMap(campaigns.map((c) => _normalizeCampaignCandidate(c)));
            const pane = container.querySelector('.mkt-workspace-pane');
            if (pane) {
              pane.innerHTML = renderCampaignWorkspace(merged);
              _wireWorkspaceTabs(container, merged);
              _wireWorkspaceActions(container, merged);
              _wireWritingStudioBridge(container, merged);
            }
            _updateActiveCampaignCard(container, merged);
          }
        }
      } catch {
        btn.disabled = false;
        btn.textContent = origText;
      }
    });
  });

  container.querySelectorAll('[data-campaign-promote]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        await promoteCampaignCandidateApi(campaign.id, {});
        btn.textContent = 'Promoted ✓';
      } catch {
        btn.disabled = false;
        btn.textContent = origText;
      }
    });
  });

  container.querySelectorAll('[data-campaign-reopen]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = '…';
      try {
        await reopenCampaignCandidateApi(campaign.id, {});
        btn.textContent = 'Reopened ✓';
      } catch {
        btn.disabled = false;
        btn.textContent = origText;
      }
    });
  });
}

function _wireWritingStudioBridge(container, campaign) {
  container.querySelectorAll('.mkt-ws-create-draft-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Creating…';
      try {
        const draft = await createCampaignWritingHandoffApi(campaign.id);
        _navigateToWritingStudio(draft.id);
      } catch {
        btn.disabled = false;
        btn.textContent = origText;
      }
    });
  });

  container.querySelectorAll('[data-ws-draft-id]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const draftId = Number(btn.dataset.wsDraftId);
      if (draftId) _navigateToWritingStudio(draftId);
    });
  });
}

function _navigateToWritingStudio(draftId) {
  try { localStorage.setItem('artemis-writing-studio-handoff', JSON.stringify({ draftId })); } catch {}
  setState('view', WRITING_STUDIO_VIEW);
}

// ── District Data provenance card (DIST5) ─────────────────────────────────

const ddState = {
  status: /** @type {Object|null} */ (null),
  loading: false,
  error: /** @type {string|null} */ (null),
  refreshing: false,
  refreshError: /** @type {string|null} */ (null),
  refreshStartedAt: /** @type {string|null} */ (null),
};

function _renderDistrictDataCard() {
  if (ddState.loading) {
    return `
    <section class="mkt-section mkt-dd-shell" data-dd-section>
      <div class="mkt-section-header">
        <div>
          <h2 class="mkt-section-heading">District Data</h2>
          <p class="mkt-section-subtext">NCES Common Core of Data provenance and freshness.</p>
        </div>
      </div>
      <p class="mkt-section-subtext">Loading…</p>
    </section>`;
  }

  if (ddState.error) {
    return `
    <section class="mkt-section mkt-dd-shell" data-dd-section>
      <div class="mkt-section-header">
        <div>
          <h2 class="mkt-section-heading">District Data</h2>
          <p class="mkt-section-subtext">NCES Common Core of Data provenance and freshness.</p>
        </div>
      </div>
      <p class="mkt-section-subtext mkt-error-text">${esc(ddState.error)}</p>
    </section>`;
  }

  const s = ddState.status;

  if (!s || !s.loaded) {
    return `
    <section class="mkt-section mkt-dd-shell" data-dd-section>
      <div class="mkt-section-header">
        <div>
          <h2 class="mkt-section-heading">District Data</h2>
          <p class="mkt-section-subtext">NCES Common Core of Data provenance and freshness.</p>
        </div>
      </div>
      <p class="mkt-section-subtext mkt-error-text">No district data loaded — run <code>scripts/refresh_nces_districts.py</code> then load via the loader.</p>
    </section>`;
  }

  // Freshness badge
  const freshness = s.freshness || "current";
  const months = s.months_since_loaded ?? s.monthsSinceLoaded ?? 0;
  const freshnessClasses = { current: "mkt-dd-badge--current", aging: "mkt-dd-badge--aging", stale: "mkt-dd-badge--stale" };
  const freshnessLabels = {
    current: `Current · loaded ${months} mo ago`,
    aging:   `Aging · loaded ${months} mo ago`,
    stale:   `Stale · ${months} mo old — newer school-year data likely available. Refresh: <code>scripts/refresh_nces_districts.py</code>`,
  };
  const badgeClass = freshnessClasses[freshness] || freshnessClasses.current;
  const badgeLabel = freshnessLabels[freshness] || freshnessLabels.current;

  // Tier mini-breakdown
  const tc = s.tier_counts || s.tierCounts || {};
  const schoolYear = s.school_year || s.schoolYear || "—";
  const total = s.total_districts ?? s.totalDistricts ?? 0;
  const supported = s.supported_count ?? s.supportedCount ?? 0;
  const unsupported = s.unsupported_count ?? s.unsupportedCount ?? 0;
  const tierRows = ["D1", "D2", "D3", "D4"].map((t) => `
    <tr>
      <td class="mkt-ds-tier-label">${esc(t)}</td>
      <td>${tc[t] ?? 0}</td>
    </tr>`).join("");

  return `
    <section class="mkt-section mkt-dd-shell" data-dd-section>
      <div class="mkt-section-header">
        <div>
          <h2 class="mkt-section-heading">District Data</h2>
          <p class="mkt-section-subtext">NCES Common Core of Data (${esc(schoolYear)}) · via Urban Institute</p>
        </div>
      </div>
      <p class="mkt-section-subtext">
        ${total.toLocaleString()} districts · ${supported.toLocaleString()} supported (D1–D3) · ${unsupported.toLocaleString()} unsupported (D4)
      </p>
      <table class="mkt-ds-table">
        <thead><tr><th>Tier</th><th>Count</th></tr></thead>
        <tbody>${tierRows}</tbody>
      </table>
      <div class="mkt-dd-freshness">
        <span class="mkt-dd-badge ${esc(badgeClass)}">${badgeLabel}</span>
      </div>
      <div class="mkt-dd-refresh">
        <button class="mkt-btn mkt-btn--secondary" data-dd-refresh-btn ${ddState.refreshing ? 'disabled' : ''}>
          ${ddState.refreshing ? 'Refreshing…' : 'Check for newer data'}
        </button>
        ${ddState.refreshing
          ? '<span class="mkt-section-subtext mkt-dd-refresh-status">Pulling CCD directory + recomputing tiers… this can take a few minutes.</span>'
          : ''}
        ${ddState.refreshError
          ? `<span class="mkt-section-subtext mkt-error-text">${esc(ddState.refreshError)}</span>`
          : ''}
      </div>
    </section>`;
}

async function _loadDistrictDataCard(container) {
  ddState.loading = true;
  ddState.error = null;
  try {
    ddState.status = await getDistrictDataStatusApi();
  } catch (err) {
    ddState.error = err.message || "Could not load district data status.";
    ddState.status = null;
  } finally {
    ddState.loading = false;
  }
  _repatchDistrictDataCard(container);
}

function _repatchDistrictDataCard(container) {
  const section = container.querySelector("[data-dd-section]");
  if (!section) return;
  const replacement = document.createElement("div");
  replacement.innerHTML = _renderDistrictDataCard();
  const newSection = replacement.querySelector("[data-dd-section]");
  if (newSection) {
    section.replaceWith(newSection);
    _attachDistrictDataHandlers(container);
  }
}

function _attachDistrictDataHandlers(container) {
  const btn = container.querySelector("[data-dd-refresh-btn]");
  if (!btn) return;
  btn.addEventListener("click", () => _handleDistrictDataRefresh(container));
}

// Poll interval while a refresh is in flight. CCD pull + load + recompute
// can take a few minutes — 20s is frequent enough to feel responsive
// without hammering the endpoint.
const _DD_REFRESH_POLL_MS = 20000;

async function _handleDistrictDataRefresh(container) {
  ddState.refreshing = true;
  ddState.refreshError = null;
  _repatchDistrictDataCard(container);

  try {
    const result = await refreshDistrictDataApi();
    ddState.refreshStartedAt = result.started_at || result.startedAt || null;
  } catch (err) {
    if (err.status === 409) {
      // Already in flight — fall through to polling so the panel still updates.
      ddState.refreshError = null;
    } else {
      ddState.refreshing = false;
      ddState.refreshError = err.message || "Could not start refresh.";
      _repatchDistrictDataCard(container);
      return;
    }
  }

  // Poll the status endpoint until loaded_at advances past the moment we
  // kicked off the refresh — that's our completion signal.
  const startMs = Date.now();
  const startedAt = ddState.refreshStartedAt ? Date.parse(ddState.refreshStartedAt) : startMs;
  const watchdogMs = 10 * 60 * 1000; // 10 minutes — beyond which give up polling and let the user reload manually.
  while (ddState.refreshing && Date.now() - startMs < watchdogMs) {
    await new Promise((r) => setTimeout(r, _DD_REFRESH_POLL_MS));
    try {
      const status = await getDistrictDataStatusApi();
      const loadedAtMs = status?.loaded_at ? Date.parse(status.loaded_at) : 0;
      if (status?.loaded && loadedAtMs >= startedAt - 1000) {
        ddState.status = status;
        ddState.refreshing = false;
        _repatchDistrictDataCard(container);
        return;
      }
    } catch {
      // Transient — keep polling until watchdog fires.
    }
  }

  // Watchdog: stop the spinner; the user can refresh the page to re-check.
  ddState.refreshing = false;
  if (!ddState.refreshError) {
    ddState.refreshError = "Refresh is still running — check back in a few minutes.";
  }
  _repatchDistrictDataCard(container);
}

// ── District Sizing section ────────────────────────────────────────────────

const dsState = {
  bands: /** @type {Array<{tier:string,minEnrollment:number|null,maxEnrollment:number|null,displayOrder:number}>} */ ([]),
  saving: false,
  recomputeResult: /** @type {number|null} */ (null),
  error: /** @type {string|null} */ (null),
};

function _renderDistrictSizing() {
  const TIER_LABELS = { D1: "D1 — Large (≥ threshold)", D2: "D2 — Mid", D3: "D3 — Small", D4: "D4 — Micro (unsupported)" };
  const bandRows = dsState.bands.map((b) => `
    <tr>
      <td class="mkt-ds-tier-label">${esc(TIER_LABELS[b.tier] || b.tier)}</td>
      <td><input class="mkt-ds-input" type="number" min="0" data-ds-tier="${esc(b.tier)}" data-ds-field="minEnrollment" value="${b.minEnrollment ?? ""}" placeholder="null (no floor)"></td>
      <td><input class="mkt-ds-input" type="number" min="0" data-ds-tier="${esc(b.tier)}" data-ds-field="maxEnrollment" value="${b.maxEnrollment ?? ""}" placeholder="null (no ceiling)"></td>
    </tr>`).join("");
  const recomputeMsg = dsState.recomputeResult !== null
    ? `<span class="mkt-ds-feedback mkt-ds-feedback--ok">Recomputed — ${dsState.recomputeResult} district(s) updated.</span>` : "";
  const errorMsg = dsState.error
    ? `<span class="mkt-ds-feedback mkt-ds-feedback--err">${esc(dsState.error)}</span>` : "";
  return `
    <section class="mkt-section mkt-ds-shell" data-ds-section>
      <div class="mkt-section-header">
        <div>
          <h2 class="mkt-section-heading">District Sizing</h2>
          <p class="mkt-section-subtext">Global enrollment tier bands (D1–D4). Edit thresholds and save; then recompute to reclassify all districts.</p>
        </div>
      </div>
      ${dsState.bands.length === 0
        ? '<p class="mkt-section-subtext mkt-error-text">No tier bands found — run migrations to seed defaults.</p>'
        : `<table class="mkt-ds-table">
            <thead><tr><th>Tier</th><th>Min enrollment</th><th>Max enrollment</th></tr></thead>
            <tbody>${bandRows}</tbody>
          </table>
          <div class="mkt-ds-actions">
            <button class="mkt-btn-primary" data-ds-action="save"${dsState.saving ? " disabled" : ""}>${dsState.saving ? "Saving…" : "Save bands"}</button>
            <button class="mkt-btn-secondary" data-ds-action="recompute">Recompute all districts</button>
            ${recomputeMsg}${errorMsg}
          </div>`}
    </section>`;
}

function _attachDistrictSizingHandlers(container) {
  const section = container.querySelector("[data-ds-section]");
  if (!section) return;
  section.querySelectorAll("[data-ds-action]").forEach((btn) => {
    btn.addEventListener("click", () => _handleDsAction(container, btn.dataset.dsAction));
  });
}

async function _handleDsAction(container, action) {
  dsState.error = null;
  dsState.recomputeResult = null;
  if (action === "save") {
    dsState.saving = true;
    _repatchDistrictSizing(container);
    const payload = dsState.bands.map((b) => {
      const section = container.querySelector("[data-ds-section]");
      const minEl = section?.querySelector(`[data-ds-tier="${b.tier}"][data-ds-field="minEnrollment"]`);
      const maxEl = section?.querySelector(`[data-ds-tier="${b.tier}"][data-ds-field="maxEnrollment"]`);
      return {
        tier: b.tier,
        minEnrollment: minEl?.value !== "" ? parseInt(minEl.value, 10) : null,
        maxEnrollment: maxEl?.value !== "" ? parseInt(maxEl.value, 10) : null,
        displayOrder: b.displayOrder,
      };
    });
    try {
      const result = await upsertTierBandsApi(payload);
      dsState.bands = result.bands;
    } catch (err) {
      dsState.error = err.message || "Could not save tier bands.";
    } finally {
      dsState.saving = false;
    }
    _repatchDistrictSizing(container);
  } else if (action === "recompute") {
    try {
      const result = await recomputeTierBandsApi();
      dsState.recomputeResult = result.updated;
    } catch (err) {
      dsState.error = err.message || "Recompute failed.";
    }
    _repatchDistrictSizing(container);
  }
}

function _repatchDistrictSizing(container) {
  const section = container.querySelector("[data-ds-section]");
  if (!section) return;
  const replacement = document.createElement("div");
  replacement.innerHTML = _renderDistrictSizing();
  const newSection = replacement.querySelector("[data-ds-section]");
  if (newSection) {
    section.replaceWith(newSection);
    _attachDistrictSizingHandlers(container);
  }
}

// ── Signal Playbook surface ────────────────────────────────────────────────

export function renderMarketingRulesets(_rulesets = [], reasonCodes = []) {
  const codes = reasonCodes.length ? reasonCodes : spState.codes;
  const domains = [...new Set(codes.map((rc) => rc.domain).filter(Boolean))].sort();
  const visible = codes.filter((rc) => {
    if (!spState.showRetired && rc.isActive === false) return false;
    if (spState.domain && rc.domain !== spState.domain) return false;
    if (spState.scout && !(rc.primaryScouts || []).includes(spState.scout)) return false;
    return true;
  });
  const grouped = visible.reduce((acc, rc) => {
    (acc[rc.domain || "other"] ||= []).push(rc);
    return acc;
  }, {});
  const groups = Object.entries(grouped).map(([domain, rows]) => `
    <section class="mkt-sp-domain">
      <h3 class="mkt-rs-section-heading">Domain: ${esc(domain)}</h3>
      <div class="mkt-rs-cards">${rows.map(_renderReasonCodeCard).join("")}</div>
    </section>`).join("");
  return `
    <section class="mkt-section mkt-sp-shell">
      <div class="mkt-section-header">
        <div>
          <h2 class="mkt-section-heading">Signal Playbook</h2>
          <p class="mkt-section-subtext">Live registry of campaign-signal criteria. Edits here are read by scouts and the qualifier on the next run.</p>
        </div>
        <div class="mkt-sp-actions">
          <button class="mkt-btn-secondary" data-sp-action="export">Export as markdown</button>
          <button class="mkt-btn-primary" data-sp-action="add">+ Add code</button>
        </div>
      </div>
      <div class="mkt-sp-toolbar">
        <label>Domain <select data-sp-filter="domain"><option value="">All</option>${domains.map((d) => `<option value="${esc(d)}"${spState.domain === d ? " selected" : ""}>${esc(d)}</option>`).join("")}</select></label>
        <label>Scout <select data-sp-filter="scout"><option value="">All</option>${SP_SCOUTS.map((s) => `<option value="${esc(s)}"${spState.scout === s ? " selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
        <label class="mkt-sp-check"><input type="checkbox" data-sp-filter="retired"${spState.showRetired ? " checked" : ""}> Show retired</label>
      </div>
      ${groups || '<p class="mkt-section-subtext">No reason codes match the current filters.</p>'}
    </section>
    ${spState.editing ? _renderReasonCodeEditor(spState.editing) : ""}`;
}

export async function loadMarketingRulesets(container) {
  if (!container) return;
  container.innerHTML = `<section class="mkt-section"><p class="mkt-section-subtext">Loading Signal Playbook…</p></section>`;
  try {
    const [codesResult, bandsResult, ddResult] = await Promise.allSettled([
      listReasonCodesApi({ includeRetired: true }),
      getTierBandsApi(),
      getDistrictDataStatusApi(),
    ]);
    if (codesResult.status === "rejected") {
      container.innerHTML = `<section class="mkt-section"><h2 class="mkt-section-heading">Signal Playbook</h2><p class="mkt-section-subtext mkt-error-text">Could not load reason codes.</p></section>`;
      return;
    }
    spState.codes = codesResult.value;
    dsState.bands = bandsResult.status === "fulfilled" ? (bandsResult.value.bands || []) : [];
    dsState.recomputeResult = null;
    dsState.error = null;
    // District Data provenance state
    ddState.status = ddResult.status === "fulfilled" ? ddResult.value : null;
    ddState.error = ddResult.status === "rejected" ? (ddResult.reason?.message || "Could not load district data status.") : null;
    ddState.loading = false;
  } catch {
    container.innerHTML = `<section class="mkt-section"><h2 class="mkt-section-heading">Signal Playbook</h2><p class="mkt-section-subtext mkt-error-text">Could not load Signal Playbook.</p></section>`;
    return;
  }
  _renderSignalPlaybook(container);
}

function _renderSignalPlaybook(container) {
  container.innerHTML = _renderDistrictDataCard() + _renderDistrictSizing() + renderMarketingRulesets([], spState.codes);
  _attachDistrictSizingHandlers(container);
  _attachDistrictDataHandlers(container);
  container.querySelectorAll("[data-sp-action]").forEach((btn) => btn.addEventListener("click", () => _handleSpAction(container, btn)));
  container.querySelectorAll("[data-sp-filter]").forEach((el) => el.addEventListener("change", () => {
    if (el.dataset.spFilter === "domain") spState.domain = el.value;
    if (el.dataset.spFilter === "scout") spState.scout = el.value;
    if (el.dataset.spFilter === "retired") spState.showRetired = el.checked;
    _renderSignalPlaybook(container);
  }));
}

function _renderReasonCodeCard(rc) {
  const scouts = (rc.primaryScouts || []).map((s) => `<span class="mkt-rs-family-chip">${esc(s)}</span>`).join(" ") || "—";
  const families = (rc.campaignFamilies || []).map((f) => `<span class="mkt-rs-family-chip">${esc(f)}</span>`).join(" ") || "—";
  return `<article class="mkt-rs-card ${rc.isActive === false ? "mkt-rs-retired" : ""}">
    <div class="mkt-rs-card-header"><span class="mkt-rs-family-name">${esc(rc.code)}</span><button class="mkt-btn mkt-btn-sm mkt-btn-ghost" data-sp-action="edit" data-code="${esc(rc.code)}">Edit</button></div>
    <p class="mkt-rs-description">${esc(rc.description || "")}</p>
    <p class="mkt-rs-empty">Scout watches: ${esc(rc.whatScoutLooksFor || "")}</p>
    <div class="mkt-sp-card-meta"><span>Urgency: ${esc(rc.defaultUrgency || "—")}</span><span>Primary scouts: ${scouts}</span><span>Campaign families: ${families}</span></div>
  </article>`;
}

function _renderReasonCodeEditor(rc = {}) {
  const isNew = !rc.code;
  const chipGroup = (name, values, selected = []) => values.map((v) => `
    <label class="mkt-sp-chip"><input type="checkbox" name="${name}" value="${esc(v)}"${selected.includes(v) ? " checked" : ""}> ${esc(v)}</label>`).join("");
  return `<div class="mkt-modal-backdrop"><div class="mkt-modal mkt-sp-modal">
    <h3>${isNew ? "Add reason code" : `Edit ${esc(rc.code)}`}</h3>
    <label>Code<input data-sp-field="code" value="${esc(rc.code || "")}" ${isNew ? "" : "readonly"}></label>
    <label>Domain<input data-sp-field="domain" value="${esc(rc.domain || "")}" ${isNew ? "" : "readonly"}></label>
    <label>Plain-English trigger<textarea data-sp-field="description" maxlength="2000">${esc(rc.description || "")}</textarea></label>
    <label>What the scout looks for<textarea data-sp-field="whatScoutLooksFor" maxlength="2000">${esc(rc.whatScoutLooksFor || "")}</textarea></label>
    <label>Default urgency<select data-sp-field="defaultUrgency">${SP_URGENCIES.map((u) => `<option value="${u}"${(rc.defaultUrgency || "standard") === u ? " selected" : ""}>${u}</option>`).join("")}</select></label>
    <div class="mkt-sp-chipset"><strong>Primary scouts</strong>${chipGroup("primaryScouts", SP_SCOUTS, rc.primaryScouts || [])}</div>
    <div class="mkt-sp-chipset"><strong>Campaign families</strong>${chipGroup("campaignFamilies", SP_FAMILIES, rc.campaignFamilies || [])}</div>
    <label class="mkt-sp-check"><input type="checkbox" data-sp-field="isActive"${rc.isActive !== false ? " checked" : ""}> Active</label>
    <div class="mkt-sp-modal-actions"><button class="mkt-btn-secondary" data-sp-action="cancel">Cancel</button><button class="mkt-btn-primary" data-sp-action="save">Save</button></div>
  </div></div>`;
}

async function _handleSpAction(container, btn) {
  const action = btn.dataset.spAction;
  if (action === "add") spState.editing = { isActive: true, primaryScouts: [], campaignFamilies: [] };
  if (action === "edit") spState.editing = spState.codes.find((rc) => rc.code === btn.dataset.code);
  if (action === "cancel") spState.editing = null;
  if (action === "export") return _downloadSignalPlaybookMarkdown();
  if (action === "save") return _saveSignalPlaybookCode(container);
  _renderSignalPlaybook(container);
}

async function _saveSignalPlaybookCode(container) {
  const modal = container.querySelector(".mkt-sp-modal");
  if (!modal) return;
  const payload = {
    code: modal.querySelector('[data-sp-field="code"]').value.trim(),
    domain: modal.querySelector('[data-sp-field="domain"]').value.trim(),
    description: modal.querySelector('[data-sp-field="description"]').value.trim(),
    whatScoutLooksFor: modal.querySelector('[data-sp-field="whatScoutLooksFor"]').value.trim(),
    defaultUrgency: modal.querySelector('[data-sp-field="defaultUrgency"]').value,
    primaryScouts: [...modal.querySelectorAll('input[name="primaryScouts"]:checked')].map((i) => i.value),
    campaignFamilies: [...modal.querySelectorAll('input[name="campaignFamilies"]:checked')].map((i) => i.value),
    isActive: modal.querySelector('[data-sp-field="isActive"]').checked,
  };
  if (!payload.code || !/^[A-Z0-9]+(?:_[A-Z0-9]+)*$/.test(payload.code) || !payload.domain) {
    alert("Code must be SCREAMING_SNAKE and domain is required.");
    return;
  }
  try {
    const saved = spState.editing?.code
      ? await patchReasonCodeApi(spState.editing.code, Object.fromEntries(Object.entries(payload).filter(([k]) => !["code", "domain"].includes(k))))
      : await createReasonCodeApi(payload);
    spState.codes = spState.codes.filter((rc) => rc.code !== saved.code).concat(saved).sort((a, b) => a.domain.localeCompare(b.domain) || a.code.localeCompare(b.code));
    spState.editing = null;
    _renderSignalPlaybook(container);
  } catch (err) {
    alert(err.message || "Could not save reason code.");
  }
}

async function _downloadSignalPlaybookMarkdown() {
  const markdown = await exportReasonCodesMarkdownApi();
  const blob = new Blob([markdown], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `signal-playbook-${new Date().toISOString().slice(0, 10)}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ── Scout Runs debug surface (read-only) ──────────────────────────────────

const SCOUT_TYPE_LABELS = {
  starbridge_researcher: 'Starbridge Researcher',
  regional_news_scout:  'Regional News Scout',
  linkedin_observer:    'LinkedIn Observer',
};

const SCOUT_RUN_STATUS_PILL = {
  pending:        'mkt-pill-created',
  dry_run_passed: 'mkt-pill-approved',
  committed:      'mkt-pill-active',
  failed:         'mkt-pill-blocked',
};

export function renderMarketingScoutRuns(runs = [], packages = []) {
  const pkgByType = {};
  for (const p of packages) pkgByType[p.scoutType] = p;

  const packageSection = packages.length > 0 ? `
    <section class="mkt-section mkt-scout-packages-section">
      <h3 class="mkt-rs-section-heading">Scout Packages</h3>
      <p class="mkt-section-subtext">Declarative skeleton configs. No live integrations — discovery is manual harness only.</p>
      <div class="mkt-scout-pkg-list">
        ${packages.map((p) => `
          <div class="mkt-scout-pkg-card">
            <div class="mkt-scout-pkg-header">
              <span class="mkt-scout-pkg-name">${esc(p.title)}</span>
              <span class="mkt-demo-label">${esc(p.scoutType)}</span>
            </div>
            <p class="mkt-scout-pkg-desc">${esc(p.description)}</p>
            <div class="mkt-scout-pkg-meta">
              <span class="mkt-scout-pkg-label">Allowed sources:</span>
              ${(p.allowedSourceTypes || []).map((s) => `<span class="mkt-rs-family-chip">${esc(s)}</span>`).join(' ')}
            </div>
            <div class="mkt-scout-pkg-guardrails">
              ${(p.guardrails || []).map((g) => `<span class="mkt-scout-guardrail">${esc(g)}</span>`).join(' ')}
            </div>
          </div>`).join('')}
      </div>
    </section>` : '';

  const runSection = runs.length === 0 ? `
    <section class="mkt-section">
      <h2 class="mkt-section-heading">Scout Runs</h2>
      <p class="mkt-section-subtext">No scout runs yet. Use <code>POST /api/scouts/runs</code> with sample findings to test the intake seam.</p>
    </section>` : `
    <section class="mkt-section">
      <h2 class="mkt-section-heading">Scout Runs</h2>
      <p class="mkt-section-subtext">Manual harness run history. Read-only.</p>
      <div class="mkt-scout-run-list">
        ${runs.map((run) => {
          const statusPill = SCOUT_RUN_STATUS_PILL[run.status] || 'mkt-pill-created';
          const typeLabel = SCOUT_TYPE_LABELS[run.scoutType] || run.scoutType;
          const createdAt = run.createdAt ? new Date(run.createdAt).toLocaleString() : '—';
          const createdCount = (run.createdSignalIds || []).length;
          const errorCount = (run.errors || []).length;
          const drs = run.dryRunSummary;

          let detailHtml = '';
          if (drs) {
            detailHtml = `
              <div class="mkt-scout-run-detail">
                <span class="mkt-scout-run-stat">Valid: ${drs.valid ?? 0}</span>
                <span class="mkt-scout-run-stat">Invalid: ${drs.invalid ?? 0}</span>
                <span class="mkt-scout-run-stat">Duplicates: ${drs.duplicates ?? 0}</span>
              </div>`;
          } else if (run.status === 'committed') {
            detailHtml = `
              <div class="mkt-scout-run-detail">
                <span class="mkt-scout-run-stat">Created: ${createdCount}</span>
                <span class="mkt-scout-run-stat">Errors: ${errorCount}</span>
              </div>`;
          }

          return `
            <div class="mkt-scout-run-card">
              <div class="mkt-scout-run-header">
                <div class="mkt-scout-run-meta">
                  <span class="mkt-scout-pkg-name">${esc(typeLabel)}</span>
                  <span class="mkt-signal-status-badge ${statusPill}">${esc(run.status.replace(/_/g, ' '))}</span>
                </div>
                <span class="mkt-scout-run-ts">${esc(createdAt)}</span>
              </div>
              <div class="mkt-scout-run-body">
                <span class="mkt-scout-run-stat mkt-scout-run-id">${esc(run.id)}</span>
                <span class="mkt-scout-run-stat">Input: ${run.inputCount}</span>
                ${detailHtml}
              </div>
            </div>`;
        }).join('')}
      </div>
    </section>`;

  return `${packageSection}${runSection}`;
}

export async function loadMarketingScoutRuns(container) {
  if (!container) return;
  container.innerHTML = `<section class="mkt-section"><p class="mkt-section-subtext">Loading scout runs…</p></section>`;

  let runs = [];
  let packages = [];
  try {
    [{ runs }, { packages }] = await Promise.all([
      listScoutRunsApi({ limit: 50 }),
      listScoutPackagesApi(),
    ]);
  } catch {
    container.innerHTML = `<section class="mkt-section">
      <h2 class="mkt-section-heading">Scout Runs</h2>
      <p class="mkt-section-subtext mkt-error-text">Could not load scout runs. Is the server running?</p>
    </section>`;
    return;
  }

  container.innerHTML = renderMarketingScoutRuns(runs, packages);
}

// ── Exports for re-use / testing ──────────────────────────────────────────
export {
  sparklineHtml, statusPillClass,
  WORKSPACE_STATE_LABELS, WORKSPACE_STATE_PILL,
  renderTabBrief, renderAssembledBrief,
};
