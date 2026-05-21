import { getState, setState } from '../core/store.js';
import { escapeHtml } from '../core/utils.js';
import {
  MARKETING_CAMPAIGNS_VIEW,
  WRITING_STUDIO_VIEW,
} from '../core/navigation.js';
import {
  listApprovalsApi, decideApprovalApi,
  fetchCampaignOpsOverview,
  decideCampaignCandidateApi, promoteCampaignCandidateApi, reopenCampaignCandidateApi,
  createCampaignWritingHandoffApi,
  listCampaignDeliverablesApi,
  assembleCampaignBriefApi, getCampaignBriefApi,
  listCampaignAssetLinksApi, createCampaignAssetLinkApi, deleteCampaignAssetLinkApi,
  listContentAssetsApi, createContentAssetApi,
  listCampaignRulesetsApi, getCampaignRulesetApi,
  listRulesetVersionsApi, activateRulesetVersionApi,
  listReasonCodesApi, getTerritoryConfigApi, upsertTerritoryStateApi,
  listSignalQueueApi, createSignalApi,
  approveSignalApi, rejectSignalApi, snoozeSignalApi, archiveSignalApi,
  qualifySignalApi,
  listScoutRunsApi, listScoutPackagesApi,
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
const MKT_SIGNAL_TREE_STATE = {
  signals: [],
  mode: 'state',
  sort: 'newest',
  query: '',
  filters: { urgencies: [], statuses: [], reasons: [], geographies: [] },
  selectedId: null,
};

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

// ── Mock data ─────────────────────────────────────────────────────────────
// Three real campaigns seeded as the first workspace instances (MVP-1).

const CAMPAIGNS = [
  {
    id: 'michigan-field-guide',
    name: 'Michigan Field Guide',
    family: 'State screener / field guide',
    status: 'In play',
    stage: 'Report',
    priority: 'Live',
    confidence: 88,
    owner: 'Marketing team',
    deadline: 'Ongoing',
    brief: {
      objective: 'Drive awareness and adoption of Amira Reading in Michigan districts navigating the MDE dyslexia screening mandate.',
      signalSource: 'Michigan Department of Education — Dyslexia Screening Guidance (2024)',
      targetDistricts: [
        { name: 'Detroit Public Schools', status: 'in-outreach' },
        { name: 'Flint Community Schools', status: 'qualified' },
        { name: 'Grand Rapids Public Schools', status: 'in-outreach' },
        { name: 'Lansing School District', status: 'qualified' },
        { name: 'Ann Arbor Public Schools', status: 'warm' },
        { name: 'Dearborn Public Schools', status: 'qualified' },
      ],
      keyMessaging: [
        'Amira Reading is SOR-aligned and meets MDE\'s dyslexia screening criteria',
        'Districts implementing Amira report measurable reading growth within one semester',
        'Minimal IT lift — no new hardware; works within existing tech stacks',
        'Field guide walks administrators through implementation step by step',
      ],
    },
    audience: {
      districts: [
        { name: 'Detroit Public Schools', status: 'in-outreach', contacts: 3, fresh: '2025-04-10' },
        { name: 'Flint Community Schools', status: 'qualified', contacts: 2, fresh: '2025-04-22' },
        { name: 'Grand Rapids Public Schools', status: 'in-outreach', contacts: 2, fresh: '2025-04-15' },
        { name: 'Lansing School District', status: 'qualified', contacts: 1, fresh: '2025-04-28' },
        { name: 'Ann Arbor Public Schools', status: 'warm', contacts: 1, fresh: '2025-03-31' },
        { name: 'Dearborn Public Schools', status: 'qualified', contacts: 2, fresh: '2025-04-20' },
      ],
    },
    assets: [
      { name: 'Michigan Field Guide (PDF)', type: 'Field Guide', status: 'shipped', updatedAt: '2025-04-01' },
      { name: 'Email sequence — Michigan screener awareness', type: 'Email Sequence', status: 'shipped', updatedAt: '2025-04-05' },
      { name: 'Landing page copy — Michigan field guide download', type: 'Landing Page', status: 'approved', updatedAt: '2025-04-08' },
      { name: 'LinkedIn post — Michigan SOR mandate launch', type: 'Social', status: 'shipped', updatedAt: '2025-04-09' },
    ],
    sequence: {
      emailCadence: [
        { step: 1, subject: 'How Michigan districts are navigating the new screening mandate', sendOffset: 'Day 0', audience: 'All qualified contacts' },
        { step: 2, subject: 'Field guide: step-by-step implementation for your district', sendOffset: 'Day 3', audience: 'Openers' },
        { step: 3, subject: 'Quick question about your Q3 screener rollout', sendOffset: 'Day 7', audience: 'Non-responders' },
        { step: 4, subject: 'One more resource before we wrap up', sendOffset: 'Day 14', audience: 'All remaining' },
      ],
      socialSchedule: [
        { platform: 'LinkedIn', type: 'Awareness post', scheduledFor: '2025-04-09', status: 'shipped' },
        { platform: 'LinkedIn', type: 'Case study teaser', scheduledFor: '2025-04-23', status: 'shipped' },
      ],
      bdrHandoffThreshold: 'Lead score ≥ 65 OR downloaded field guide AND opened ≥ 2 emails',
    },
    compliance: {
      preferenceCenter: 'active',
      optOutCount: 2,
      legalFlags: [],
      lastAudit: '2025-04-01',
    },
    approvalLog: [
      { gate: 'Signal review', approvedBy: 'Josh + Angela', approvedAt: '2025-02-14', notes: 'Green-lit Michigan as the pilot field-guide campaign.' },
      { gate: 'Content review — field guide draft', approvedBy: 'Josh', approvedAt: '2025-03-18', notes: 'Minor edits to implementation checklist on page 4.' },
      { gate: 'Final approval — email sequence', approvedBy: 'Kristen', approvedAt: '2025-04-05', notes: 'Approved as-is. Enrolled in HubSpot.' },
    ],
    kpis: {
      opens: 312,
      clicks: 88,
      downloads: 47,
      bdrQueued: 6,
      sparkline: [18, 24, 41, 57, 62, 72, 88],
    },
  },
  {
    id: 'florida-obc',
    name: 'Florida OBC Campaign',
    family: 'Outcomes-based contracts',
    status: 'Ready for opportunity review',
    stage: 'Human gate 1',
    priority: 'P0',
    confidence: 84,
    owner: 'Josh + Angela',
    deadline: '2025-06-30',
    brief: {
      objective: 'Position Amira Reading as the reference solution for Florida districts adopting outcomes-based contract models following the Florida DOE accountability framework.',
      signalSource: 'Florida DOE — Accountability Framework update (Q1 2025) + Duval County OBC case study',
      targetDistricts: [
        { name: 'Duval County Public Schools', status: 'qualified' },
        { name: 'Broward County Public Schools', status: 'qualified' },
        { name: 'Orange County Public Schools', status: 'warm' },
        { name: 'Palm Beach County School District', status: 'warm' },
        { name: 'Hillsborough County Public Schools', status: 'cold' },
      ],
      keyMessaging: [
        'Amira\'s OBC model ties payment to measurable reading outcomes — perfect fit for Florida\'s accountability framework',
        'Duval County\'s pilot demonstrates 22% average reading growth in one year',
        'Amira\'s OBC webinar (March 2025) provides plug-and-play proof for procurement conversations',
        'Full compliance with Florida\'s public-school accountability reporting',
      ],
    },
    audience: {
      districts: [
        { name: 'Duval County Public Schools', status: 'qualified', contacts: 4, fresh: '2025-04-18' },
        { name: 'Broward County Public Schools', status: 'qualified', contacts: 3, fresh: '2025-04-12' },
        { name: 'Orange County Public Schools', status: 'warm', contacts: 2, fresh: '2025-03-25' },
        { name: 'Palm Beach County School District', status: 'warm', contacts: 2, fresh: '2025-03-20' },
        { name: 'Hillsborough County Public Schools', status: 'cold', contacts: 1, fresh: '2025-02-14' },
      ],
    },
    assets: [
      { name: 'OBC webinar recording + transcript', type: 'Webinar', status: 'approved', updatedAt: '2025-03-10' },
      { name: 'Duval case study (gated PDF)', type: 'Case Study', status: 'approved', updatedAt: '2025-03-22' },
      { name: 'Florida OBC email sequence (draft)', type: 'Email Sequence', status: 'in-review', updatedAt: '2025-04-20' },
      { name: 'OBC landing page copy (draft)', type: 'Landing Page', status: 'draft', updatedAt: '2025-04-25' },
      { name: 'LinkedIn post — Florida OBC launch', type: 'Social', status: 'draft', updatedAt: '2025-04-27' },
    ],
    sequence: {
      emailCadence: [
        { step: 1, subject: 'How Duval County locked in reading outcomes without budget risk', sendOffset: 'Day 0', audience: 'All qualified + warm contacts' },
        { step: 2, subject: 'The OBC model explained — 8-minute webinar replay', sendOffset: 'Day 4', audience: 'Openers' },
        { step: 3, subject: 'Could [District Name] replicate the Duval results?', sendOffset: 'Day 9', audience: 'Non-responders' },
      ],
      socialSchedule: [
        { platform: 'LinkedIn', type: 'OBC awareness post', scheduledFor: 'TBD', status: 'draft' },
      ],
      bdrHandoffThreshold: 'Lead score ≥ 60 OR opened webinar link AND replied to any email',
    },
    compliance: {
      preferenceCenter: 'pending-setup',
      optOutCount: 0,
      legalFlags: ['Preference Center not yet configured — do not send until Kristen completes setup'],
      lastAudit: null,
    },
    approvalLog: [
      { gate: 'Signal review', approvedBy: 'Pending — Josh + Angela', approvedAt: null, notes: 'Awaiting Human Gate 1 approval to initiate campaign workflow.' },
    ],
    kpis: {
      opens: 0,
      clicks: 0,
      downloads: 0,
      bdrQueued: 0,
      sparkline: [],
    },
  },
  {
    id: 'maryland-field-guide',
    name: 'Maryland Screener Field Guide',
    family: 'State screener / field guide',
    status: 'Needs Ry validation',
    stage: 'Evidence validation',
    priority: 'P1',
    confidence: 72,
    owner: 'Josh + Ry',
    deadline: '2025-07-31',
    brief: {
      objective: 'Support Maryland districts implementing the RISE Act dyslexia screening requirements with a practitioner field guide co-authored with Ry.',
      signalSource: 'Maryland RISE Act (Reading Instruction Supports in Education) — effective SY 2025-26',
      targetDistricts: [
        { name: 'Montgomery County Public Schools', status: 'warm' },
        { name: 'Prince George\'s County Public Schools', status: 'qualified' },
        { name: 'Baltimore City Public Schools', status: 'warm' },
        { name: 'Howard County Public Schools', status: 'cold' },
        { name: 'Anne Arundel County Public Schools', status: 'cold' },
      ],
      keyMessaging: [
        'Amira Reading aligns with Maryland RISE Act\'s evidence-based screening requirements',
        'Field guide walks administrators through RISE Act compliance in 5 steps',
        'Ry\'s policy accuracy review ensures every claim is defensible under Maryland law',
        'Parallel to Michigan model — proven playbook, adapted for Maryland\'s specific statute',
      ],
    },
    audience: {
      districts: [
        { name: 'Montgomery County Public Schools', status: 'warm', contacts: 2, fresh: '2025-03-14' },
        { name: 'Prince George\'s County Public Schools', status: 'qualified', contacts: 2, fresh: '2025-04-02' },
        { name: 'Baltimore City Public Schools', status: 'warm', contacts: 1, fresh: '2025-03-01' },
        { name: 'Howard County Public Schools', status: 'cold', contacts: 1, fresh: '2025-01-20' },
        { name: 'Anne Arundel County Public Schools', status: 'cold', contacts: 1, fresh: '2025-01-15' },
      ],
    },
    assets: [
      { name: 'Maryland field guide draft (pending Ry validation)', type: 'Field Guide', status: 'in-review', updatedAt: '2025-04-14' },
      { name: 'RISE Act legislation summary', type: 'Research', status: 'approved', updatedAt: '2025-04-10' },
      { name: 'Maryland email sequence (blocked — awaiting field guide approval)', type: 'Email Sequence', status: 'draft', updatedAt: null },
    ],
    sequence: {
      emailCadence: [
        { step: 1, subject: 'Maryland RISE Act: what your district needs to do before August', sendOffset: 'Day 0', audience: 'All qualified + warm contacts' },
        { step: 2, subject: 'Field guide: RISE Act implementation in 5 steps', sendOffset: 'Day 5', audience: 'Openers' },
        { step: 3, subject: 'Quick question about your RISE Act readiness', sendOffset: 'Day 10', audience: 'Non-responders' },
      ],
      socialSchedule: [
        { platform: 'LinkedIn', type: 'RISE Act awareness post', scheduledFor: 'TBD', status: 'draft' },
      ],
      bdrHandoffThreshold: 'Lead score ≥ 55 OR downloaded field guide AND clicked any follow-up',
    },
    compliance: {
      preferenceCenter: 'pending-setup',
      optOutCount: 0,
      legalFlags: ['Awaiting Ry\'s policy accuracy validation before any outreach begins'],
      lastAudit: null,
    },
    approvalLog: [
      { gate: 'Expert validation', approvedBy: 'Pending — Ry', approvedAt: null, notes: 'Field guide draft sent to Ry on 2025-04-14. Pending accuracy sign-off.' },
    ],
    kpis: {
      opens: 0,
      clicks: 0,
      downloads: 0,
      bdrQueued: 0,
      sparkline: [],
    },
  },
];

const SIGNALS_MOCK = [
  {
    id: 'sig-1',
    title: 'Indiana HB 1234 — dyslexia screening mandate signed into law',
    state: 'Indiana',
    source: 'Starbridge / Indiana General Assembly',
    type: 'Legislation',
    urgency: 'high',
    deadline: '2025-08-01',
    fitScore: 81,
    campaignType: 'State screener / field guide',
    summary: 'Indiana Governor signed HB 1234 requiring K-3 dyslexia screening statewide by SY 2026. 92 districts affected. Michigan field guide is a direct template.',
    status: 'pending',
  },
  {
    id: 'sig-2',
    title: 'Missouri SB 610 — reading screener appropriation passed committee',
    state: 'Missouri',
    source: 'Starbridge / Missouri Senate',
    type: 'Legislation',
    urgency: 'medium',
    deadline: '2025-09-15',
    fitScore: 76,
    campaignType: 'State screener / field guide',
    summary: 'SB 610 passed Senate Education Committee with $4.2M appropriation for reading screeners. Full chamber vote expected in May.',
    status: 'pending',
  },
  {
    id: 'sig-3',
    title: 'Illinois ISBE issues OBC pilot RFP for literacy vendors',
    state: 'Illinois',
    source: 'Regional News Scout / ISBE',
    type: 'Funding',
    urgency: 'high',
    deadline: '2025-05-30',
    fitScore: 79,
    campaignType: 'Outcomes-based contracts',
    summary: 'Illinois State Board of Education issued an RFP for literacy vendors willing to operate under outcomes-based contracts. Deadline May 30. Florida OBC playbook applies.',
    status: 'pending',
  },
];

const APPROVALS_MOCK = [
  {
    id: 'appr-1',
    campaignId: 'florida-obc',
    campaignName: 'Florida OBC Campaign',
    gate: 'Signal review — Gate 1',
    deliverable: 'Opportunity brief + recommended campaign scope',
    requestedBy: 'System',
    requestedAt: '2025-04-28',
    reviewers: ['Josh', 'Angela'],
    status: 'pending',
    priority: 'P0',
  },
  {
    id: 'appr-2',
    campaignId: 'florida-obc',
    campaignName: 'Florida OBC Campaign',
    gate: 'Content review — Gate 2',
    deliverable: 'Florida OBC email sequence (3-step draft)',
    requestedBy: 'System',
    requestedAt: '2025-04-29',
    reviewers: ['Kristen'],
    status: 'pending',
    priority: 'P0',
  },
  {
    id: 'appr-3',
    campaignId: 'maryland-field-guide',
    campaignName: 'Maryland Screener Field Guide',
    gate: 'Expert validation — Gate 1',
    deliverable: 'Maryland field guide draft (policy accuracy)',
    requestedBy: 'System',
    requestedAt: '2025-04-14',
    reviewers: ['Ry'],
    status: 'pending',
    priority: 'P1',
  },
];

// ── Module-level campaign map ──────────────────────────────────────────────
// Merges static CAMPAIGNS with real API candidates. Updated by async patches.
let _campaignMap = new Map(CAMPAIGNS.map((c) => [c.id, c]));

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

// ── Normalize real API candidates ─────────────────────────────────────────
// Real Campaign Ops candidates lack brief/audience/sequence/compliance; this
// merges them with any matching static entry and flags them as live data.
function _normCandidate(raw) {
  return {
    id: raw.id,
    name: raw.name || '',
    family: raw.family || '',
    status: raw.status || '',
    stage: raw.stage || '',
    priority: raw.priority || '',
    confidence: Number(raw.confidence || 0),
    owner: raw.owner || '',
    deadline: raw.deadline || '',
    nextAction: raw.nextAction || '',
    why: raw.why || '',
    signals: Array.isArray(raw.signals) ? raw.signals : [],
    deliverables: Array.isArray(raw.deliverables) ? raw.deliverables : [],
    gates: Array.isArray(raw.gates) ? raw.gates : [],
    decisionState: raw.decisionState || 'pending_review',
    repositoryBucket: raw.repositoryBucket || null,
    history: raw.history || [],
    linkedDraftCount: Number(raw.linkedDraftCount || 0),
    latestDraftId: raw.latestDraftId ? Number(raw.latestDraftId) : null,
    latestDraftTitle: raw.latestDraftTitle || null,
    kpis: raw.kpis || null,
    _fromApi: true,
  };
}

// Merge real API candidate data into a static CAMPAIGNS entry (or return
// a normalized standalone if there is no static entry).
function _mergeWithStatic(realCandidate) {
  const staticEntry = _campaignMap.get(realCandidate.id);
  if (staticEntry && !staticEntry._fromApi) {
    return {
      ...staticEntry,
      decisionState: realCandidate.decisionState,
      history: realCandidate.history || staticEntry.approvalLog || [],
      linkedDraftCount: Number(realCandidate.linkedDraftCount || 0),
      latestDraftId: realCandidate.latestDraftId ? Number(realCandidate.latestDraftId) : null,
      latestDraftTitle: realCandidate.latestDraftTitle || null,
      _fromApi: true,
    };
  }
  return _normCandidate(realCandidate);
}

// ── Dashboard view ────────────────────────────────────────────────────────

export function renderMarketingDashboard(
  campaigns = CAMPAIGNS,
  pendingApprovalCount = APPROVALS_MOCK.filter((a) => a.status === 'pending').length,
  signalsCount = SIGNALS_MOCK.length,
) {
  const tiles = campaigns.map((c) => {
    const kpis = c.kpis || { opens: 0, clicks: 0, downloads: 0, bdrQueued: 0, sparkline: [] };
    const opens = kpis.opens;
    const clicks = kpis.clicks;
    const downloads = kpis.downloads;
    const bdr = kpis.bdrQueued;
    return `
      <article class="mkt-campaign-tile" data-mkt-tile-id="${esc(c.id)}" role="button" tabindex="0"
               aria-label="Open ${esc(c.name)} workspace">
        <div class="mkt-tile-head">
          <div class="mkt-tile-title-row">
            ${priorityBadge(c.priority)}
            <h3 class="mkt-tile-name">${esc(c.name)}</h3>
          </div>
          <div class="mkt-tile-status-badges">
            <span class="mkt-pill ${statusPillClass(c.status)}">${esc(c.status)}</span>
            ${workspaceStateBadge(c)}
          </div>
        </div>
        <div class="mkt-tile-family">${esc(c.family)}</div>
        <div class="mkt-tile-kpi-row">
          <div class="mkt-tile-kpi">
            <span class="mkt-kpi-value">${opens || '—'}</span>
            <span class="mkt-kpi-label">opens</span>
          </div>
          <div class="mkt-tile-kpi">
            <span class="mkt-kpi-value">${clicks || '—'}</span>
            <span class="mkt-kpi-label">clicks</span>
          </div>
          <div class="mkt-tile-kpi">
            <span class="mkt-kpi-value">${downloads || '—'}</span>
            <span class="mkt-kpi-label">downloads</span>
          </div>
          <div class="mkt-tile-kpi">
            <span class="mkt-kpi-value">${bdr || '—'}</span>
            <span class="mkt-kpi-label">BDR queue</span>
          </div>
        </div>
        <div class="mkt-tile-spark-row">
          ${sparklineHtml(kpis.sparkline)}
          <span class="mkt-tile-stage">${esc(c.stage)}</span>
        </div>
        <div class="mkt-tile-footer">
          <span class="mkt-tile-owner">${esc(c.owner)}</span>
          <button class="mkt-tile-open-btn" data-mkt-open-campaign="${esc(c.id)}" type="button">Open workspace →</button>
        </div>
      </article>
    `;
  }).join('');

  const liveCampaignCount = campaigns.filter((c) => c.priority === 'Live').length;

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
      <div class="mkt-tiles-grid">${tiles}</div>
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
      ${priorityBadge(c.priority)}
      <span class="mkt-pill ${statusPillClass(c.status)} mkt-pill-wrap">${esc(c.status)}</span>
    </div>
    <div class="mkt-panel-meta">
      <span>${esc(c.owner)}</span>
      <span class="mkt-panel-sep">·</span>
      <span>${esc(c.deadline)}</span>
      <span class="mkt-panel-sep">·</span>
      <span class="mkt-panel-confidence">
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        ${c.confidence}%
      </span>
    </div>
  `;
}

// campaignsOrId: array of campaign objects (new callers) OR string/null selectedId (backward compat)
export function renderMarketingCampaigns(campaignsOrId = null, selectedId = null) {
  let campaigns, resolvedSelectedId;
  if (Array.isArray(campaignsOrId)) {
    campaigns = campaignsOrId;
    resolvedSelectedId = selectedId;
  } else {
    campaigns = CAMPAIGNS;
    resolvedSelectedId = campaignsOrId; // original single-arg call: renderMarketingCampaigns(selectedId)
  }

  const selected = resolvedSelectedId ? campaigns.find((c) => c.id === resolvedSelectedId) : null;

  const listItems = campaigns.map((c) => `
    <div class="mkt-campaign-list-item ${resolvedSelectedId === c.id ? 'active' : ''}"
         data-mkt-open-campaign="${esc(c.id)}" role="button" tabindex="0">
      <span class="mkt-list-item-name">${esc(c.name)}</span>
      <span class="mkt-list-item-family">${esc(c.family)}</span>
      <div class="mkt-list-item-foot">
        <span class="mkt-list-dot ${statusDotClass(c.status)}"></span>
        <span class="mkt-list-item-foot-text">${esc(c.stage)} · ${esc(c.deadline)}</span>
      </div>
    </div>
  `).join('');

  const activeCampaignCard = selected ? `
    <div class="mkt-panel-card mkt-active-campaign-card">
      ${_renderActiveCampaignCardInner(selected)}
    </div>
  ` : '';

  const workspaceHtml = selected
    ? renderCampaignWorkspace(selected)
    : `<div class="mkt-workspace-empty">
        <div class="mkt-workspace-empty-icon">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        </div>
        <p>Select a campaign to open its workspace.</p>
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
          <div class="mkt-campaigns-browser-items">
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
    try { return localStorage.getItem(MKT_WORKSPACE_TAB_KEY) || 'brief'; } catch { return 'brief'; }
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

  return `
    <div class="mkt-workspace" data-campaign-id="${esc(campaign.id)}">
      <div class="mkt-workspace-header">
        <div class="mkt-workspace-header-left">
          <div class="shell-eyebrow">${esc(campaign.family)}</div>
          <h2 class="mkt-workspace-title">${esc(campaign.name)}</h2>
          <div class="mkt-workspace-owner">${esc(campaign.owner)} · ${esc(campaign.deadline)}</div>
        </div>
        <div class="mkt-workspace-header-meta">
          ${priorityBadge(campaign.priority)}
          <span class="mkt-pill ${statusPillClass(campaign.status)}">${esc(campaign.status)}</span>
          ${workspaceStateBadge(campaign)}
          <span class="mkt-confidence-badge">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            ${campaign.confidence}% confidence
          </span>
        </div>
      </div>
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
  const objective = brief.objective || c.why || '';
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
    if (entry.gate !== undefined) {
      // Static CAMPAIGNS format: { gate, approvedBy, approvedAt, notes }
      return `
        <div class="mkt-approval-log-row">
          <div class="mkt-approval-log-gate">${esc(entry.gate)}</div>
          <div class="mkt-approval-log-meta">
            <span>${esc(entry.approvedBy)}</span>
            <span class="mkt-approval-log-date">${entry.approvedAt || 'Pending'}</span>
          </div>
          ${entry.notes ? `<div class="mkt-approval-log-notes">${esc(entry.notes)}</div>` : ''}
        </div>
      `;
    }
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

// Convert SIGNALS_MOCK demo entry to the normalized API signal shape.
function _convertMockSignal(s) {
  const tierMap = { high: 'hot', medium: 'standard', low: 'enrichment' };
  return {
    id: s.id,
    signalStatus: 'in_inbox',
    campaignFamily: s.campaignType || '',
    headline: s.title || '',
    whyFlagged: s.summary || '',
    evidence: s.summary || '',
    sourceType: (s.source || '').toLowerCase().includes('starbridge') ? 'starbridge' : 'news_article',
    sourceUrl: null,
    stateCode: s.state || '',
    district: null,
    reasonCodes: [],
    fitScore: s.fitScore ? s.fitScore / 100 : null,
    urgencyTier: tierMap[s.urgency] || 'standard',
    urgencyDeadline: s.deadline || null,
    discoveredAt: null,
    discoveredBy: 'demo',
    rulesetVersionAtQualification: null,
    trainingNotes: null,
    snoozeUntil: null,
    campaignCandidateId: null,
    createdAt: null,
    updatedAt: null,
    _isDemo: true,
  };
}

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

export function renderMarketingSignals(signals = [], isDemo = false) {
  if (!isDemo) {
    return renderSignalInboxTree(signals, {
      mode: MKT_SIGNAL_TREE_STATE.mode,
      sort: MKT_SIGNAL_TREE_STATE.sort,
      query: MKT_SIGNAL_TREE_STATE.query,
      filters: MKT_SIGNAL_TREE_STATE.filters,
      selectedId: MKT_SIGNAL_TREE_STATE.selectedId,
      collapsed: readCollapsedSignalGroups(),
    });
  }
  const items = signals.map((s) => _signalCardHtml(s)).join('');
  const demoBanner = isDemo ? `
    <div class="mkt-signals-demo-banner">
      <span class="mkt-demo-label">Demo data</span>
      No real signals yet — showing reference signals for field testing.
    </div>` : '';
  const addForm = `
    <div class="mkt-signals-add-row">
      <button class="mkt-btn-secondary mkt-signals-add-btn" type="button"
              data-signal-action="add-open">+ Add Signal</button>
    </div>
    <div class="mkt-signal-add-form" hidden>
      <h5 class="mkt-signal-add-title">New Signal</h5>
      <label class="mkt-signal-add-label">Headline <span aria-hidden="true">*</span>
        <input class="mkt-signal-add-input" name="headline" type="text" required
               placeholder="e.g. Indiana HB 1234 — dyslexia screening mandate signed"/>
      </label>
      <label class="mkt-signal-add-label">Campaign family <span aria-hidden="true">*</span>
        <select class="mkt-signal-add-select" name="campaignFamily">
          <option value="obc">Outcomes-based contracts</option>
          <option value="state_screener">State screener / field guide</option>
          <option value="biliteracy">Biliteracy</option>
          <option value="reading_growth">Reading growth</option>
        </select>
      </label>
      <label class="mkt-signal-add-label">State code (optional)
        <input class="mkt-signal-add-input mkt-signal-add-input--short"
               name="stateCode" type="text" maxlength="2" placeholder="IN"/>
      </label>
      <label class="mkt-signal-add-label">Evidence / source quote (optional)
        <textarea class="mkt-signal-add-textarea" name="evidence" rows="2"
                  placeholder="Verbatim snippet from source..."></textarea>
      </label>
      <label class="mkt-signal-add-label">Urgency
        <select class="mkt-signal-add-select" name="urgencyTier">
          <option value="hot">Hot</option>
          <option value="standard" selected>Standard</option>
          <option value="enrichment">Enrichment</option>
        </select>
      </label>
      <label class="mkt-signal-add-label">Deadline (optional)
        <input class="mkt-signal-add-input" name="urgencyDeadline" type="date"/>
      </label>
      <div class="mkt-signal-form-actions">
        <button class="mkt-btn-primary" data-signal-action="add-submit" type="button">Add Signal</button>
        <button class="mkt-btn-ghost" data-signal-action="add-cancel" type="button">Cancel</button>
      </div>
    </div>`;

  return `
    <section class="mkt-section">
      <div class="mkt-signals-hero">
        <h3 class="mkt-signals-title">Signals Inbox</h3>
        <p class="mkt-signals-sub">Review signals and approve to initiate a new campaign workspace.</p>
      </div>
      ${demoBanner}
      ${addForm}
      <div class="mkt-signals-list">${items || '<p class="mkt-signals-empty">No signals in inbox.</p>'}</div>
    </section>`;
}

// ── Approval Queue ────────────────────────────────────────────────────────

// Internal: renders only the mock approval item HTML (no section wrapper).
function _renderMockApprovalItems() {
  return APPROVALS_MOCK.filter((a) => a.status === 'pending').map((a) => `
    <article class="mkt-approval-card" data-approval-id="${esc(a.id)}">
      <div class="mkt-approval-head">
        <div class="mkt-approval-title-row">
          <span class="mkt-badge ${a.priority === 'P0' ? 'mkt-badge-p0' : 'mkt-badge-p1'}">${esc(a.priority)}</span>
          <span class="mkt-approval-campaign">${esc(a.campaignName)}</span>
        </div>
        <span class="mkt-pill mkt-pill-pending">Pending</span>
      </div>
      <div class="mkt-approval-gate">${esc(a.gate)}</div>
      <div class="mkt-approval-deliverable">${esc(a.deliverable)}</div>
      <div class="mkt-approval-meta">
        <span>Reviewers: ${esc(a.reviewers.join(', '))}</span>
        <span>Requested: ${esc(a.requestedAt)}</span>
      </div>
      <div class="mkt-signal-actions">
        <button class="mkt-btn-primary" type="button" disabled data-coming-soon>Approve</button>
        <button class="mkt-btn-ghost" type="button" disabled data-coming-soon>Request edits</button>
      </div>
    </article>
  `).join('');
}

export function renderMarketingApprovals() {
  return `
    <section class="mkt-section">
      <div class="mkt-section-header">
        <h3 class="mkt-section-title">Approval Queue</h3>
      </div>
      <div class="mkt-approvals-list">${_renderMockApprovalItems()}</div>
    </section>
  `;
}

function _parseApprovalPayload(a) {
  if (!a.payload) return {};
  if (typeof a.payload === 'object') return a.payload;
  try { return JSON.parse(a.payload); } catch { return {}; }
}

// Internal: renders a single card for a real unified approval (workflow_gate, pre_run, writing_gate_2, etc.)
function _renderUnifiedApprovalCard(a) {
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

function _wireComingSoonButtons(container) {
  container.querySelectorAll('[data-coming-soon]').forEach((btn) => {
    const original = btn.textContent;
    btn.addEventListener('click', () => {
      btn.textContent = 'Coming in Phase B';
      setTimeout(() => { btn.textContent = original; }, 1800);
    });
  });
}

// ── Shell loader functions (called by home.js) ────────────────────────────

export function loadMarketingDashboard(container) {
  if (!container) return;
  container.innerHTML = renderMarketingDashboard();
  _wireDashboardActions(container);
  _fetchAndPatchDashboard(container);
}

async function _fetchAndPatchDashboard(container) {
  try {
    const [overview, approvals, signalResult] = await Promise.all([
      fetchCampaignOpsOverview(),
      listApprovalsApi({ status: 'pending' }).catch(() => []),
      listSignalQueueApi({ status: 'in_inbox' }).catch(() => null),
    ]);
    const liveCandidates = overview.campaigns || [];
    const mergedCampaigns = liveCandidates.map((c) => _mergeWithStatic(c));
    for (const c of mergedCampaigns) _campaignMap.set(c.id, c);
    const pendingCount = Array.isArray(approvals) ? approvals.length
      : APPROVALS_MOCK.filter((a) => a.status === 'pending').length;
    const signalsCount = signalResult && signalResult.total > 0
      ? signalResult.total : SIGNALS_MOCK.length;
    container.innerHTML = renderMarketingDashboard(
      mergedCampaigns.length > 0 ? mergedCampaigns : CAMPAIGNS,
      pendingCount,
      signalsCount,
    );
    _wireDashboardActions(container);
  } catch {
    // API unavailable — static content already rendered; mark it as demo
    const hero = container.querySelector('.mkt-hero');
    if (hero && !hero.querySelector('.mkt-demo-banner')) {
      const banner = document.createElement('div');
      banner.className = 'mkt-demo-banner';
      banner.innerHTML = '<span class="mkt-demo-label">Demo data</span> Live campaign data unavailable — showing reference campaigns.';
      hero.appendChild(banner);
    }
  }
}

export function loadMarketingCampaigns(container) {
  if (!container) return;
  const storedId = (() => {
    try { return localStorage.getItem(MKT_CAMPAIGN_KEY); } catch { return null; }
  })();
  container.innerHTML = renderMarketingCampaigns(storedId);
  _wireCampaignActions(container);
  _fetchAndPatchCampaigns(container);
}

async function _fetchAndPatchCampaigns(container) {
  try {
    const overview = await fetchCampaignOpsOverview();
    const liveCandidates = overview.campaigns || [];
    const mergedCampaigns = liveCandidates.map((c) => _mergeWithStatic(c));
    for (const c of mergedCampaigns) _campaignMap.set(c.id, c);

    const storedId = (() => {
      try { return localStorage.getItem(MKT_CAMPAIGN_KEY); } catch { return null; }
    })();

    const displayCampaigns = mergedCampaigns.length > 0 ? mergedCampaigns : CAMPAIGNS;
    container.innerHTML = renderMarketingCampaigns(displayCampaigns, storedId);
    _wireCampaignActions(container);
    if (storedId && _campaignMap.has(storedId)) {
      const campaign = _campaignMap.get(storedId);
      _wireWorkspaceTabs(container, campaign);
      _wireWorkspaceActions(container, campaign);
      _wireWritingStudioBridge(container, campaign);
    }
  } catch {
    // API unavailable — static content already rendered; mark the list header as demo
    const header = container.querySelector('.mkt-campaigns-list-header');
    if (header && !header.querySelector('.mkt-demo-label')) {
      const label = document.createElement('span');
      label.className = 'mkt-demo-label';
      label.textContent = 'Demo data';
      header.appendChild(label);
    }
  }
}

export async function loadMarketingSignals(container) {
  if (!container) return;
  // Synchronous skeleton so the section appears immediately
  container.innerHTML = renderMarketingSignals(SIGNALS_MOCK.map(_convertMockSignal), true);
  _wireSignalActions(container);
  MKT_SIGNAL_TREE_STATE.mode = readSignalGroupMode();
  try {
    const result = await listSignalQueueApi({ limit: 200 });
    const realSignals = result.signals || [];
    MKT_SIGNAL_TREE_STATE.signals = realSignals;
    MKT_SIGNAL_TREE_STATE.selectedId = realSignals[0]?.id || null;
    container.innerHTML = renderMarketingSignals(realSignals, false);
  } catch {
    // API unavailable — keep the demo skeleton already rendered
  }
}

function _renderSignalTreeState(container) {
  container.innerHTML = renderMarketingSignals(MKT_SIGNAL_TREE_STATE.signals, false);
}

async function _refreshSignalTree(container) {
  const result = await listSignalQueueApi({ limit: 200 });
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
    if (!e.target.matches('[data-signal-sort]')) return;
    MKT_SIGNAL_TREE_STATE.sort = e.target.value === 'urgency' ? 'urgency' : 'newest';
    _renderSignalTreeState(container);
  });
}

export async function loadMarketingApprovals(container) {
  if (!container) return;
  // Synchronous skeleton — tests that don't await still see .mkt-approvals-list
  container.innerHTML = `<section class="mkt-section"><div class="mkt-approvals-list"></div></section>`;

  let liveApprovals = [];
  try {
    const res = await listApprovalsApi({ status: 'pending' });
    liveApprovals = Array.isArray(res) ? res : [];
  } catch { /* network error — fall through to mock view */ }

  if (liveApprovals.length === 0) {
    container.innerHTML = renderMarketingApprovals();
    _wireComingSoonButtons(container);
    return;
  }

  const liveCards = liveApprovals.map(_renderUnifiedApprovalCard).join('');
  const mockItems = _renderMockApprovalItems();
  container.innerHTML = `
    <section class="mkt-section">
      <div class="mkt-approvals-list">${liveCards}</div>
    </section>
    <section class="mkt-section">
      <div class="mkt-approvals-demo-row">Campaign approvals <span class="mkt-demo-label">demo data</span></div>
      <div class="mkt-approvals-list">${mockItems}</div>
    </section>
  `;

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

  _wireComingSoonButtons(container);
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
      const campaign = _campaignMap.get(id);
      if (!campaign) return;
      const pane = container.querySelector('.mkt-workspace-pane');
      if (pane) {
        pane.innerHTML = renderCampaignWorkspace(campaign);
        _wireWorkspaceTabs(container, campaign);
        _wireWorkspaceActions(container, campaign);
        _wireWritingStudioBridge(container, campaign);
      }
      _updateActiveCampaignCard(container, campaign);
      container.querySelectorAll('.mkt-campaign-list-item').forEach((item) => {
        item.classList.toggle('active', item.dataset.mktOpenCampaign === id);
      });
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') el.click();
    });
  });

  const storedId = (() => {
    try { return localStorage.getItem(MKT_CAMPAIGN_KEY); } catch { return null; }
  })();
  if (storedId) {
    const campaign = _campaignMap.get(storedId);
    if (campaign) {
      _wireWorkspaceTabs(container, campaign);
      _wireWorkspaceActions(container, campaign);
      _wireWritingStudioBridge(container, campaign);
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
        const overview = await fetchCampaignOpsOverview().catch(() => null);
        if (overview) {
          const updated = (overview.campaigns || []).find((c) => c.id === campaign.id);
          if (updated) {
            const merged = _mergeWithStatic(updated);
            _campaignMap.set(campaign.id, merged);
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

// ── Scout Rulesets surface ─────────────────────────────────────────────────

const TIER_PILL_CLASS = {
  hot:      'mkt-tier-hot',
  standard: 'mkt-tier-standard',
  low:      'mkt-tier-low',
  excluded: 'mkt-tier-excluded',
};

function _tierPill(tier) {
  const cls = TIER_PILL_CLASS[tier] || 'mkt-tier-standard';
  return `<span class="mkt-territory-tier-pill ${cls}">${esc(tier)}</span>`;
}

export function renderMarketingRulesets(rulesets = [], reasonCodes = []) {
  if (rulesets.length === 0) {
    return `<section class="mkt-section">
      <h2 class="mkt-section-heading">Scout Rulesets</h2>
      <p class="mkt-section-subtext">No campaign rulesets found. Seed data may not have loaded.</p>
    </section>`;
  }

  const rcByFamily = {};
  for (const rc of reasonCodes) {
    for (const f of (rc.campaignFamilies || [])) {
      if (!rcByFamily[f]) rcByFamily[f] = [];
      rcByFamily[f].push(rc);
    }
  }

  const cards = rulesets.map((rs) => {
    const active = rs.activeVersionDetails;
    const vLabel = active ? `v${active.versionNumber}` : 'No active version';
    const vState = active ? active.state : '—';
    const minScore = active ? `${Math.round((active.minFitScore || 0) * 100)}%` : '—';
    const weightedSignals = active?.weightedSignals || [];
    const hardFilters = active?.hardFilters || [];

    const signalRows = weightedSignals.map((ws) => `
      <tr>
        <td class="mkt-rs-signal-code">${esc(ws.reason_code || ws.ruleId || '')}</td>
        <td>${esc(ws.description || '')}</td>
        <td class="mkt-rs-signal-weight">${ws.weight !== undefined ? Math.round(ws.weight * 100) + '%' : '—'}</td>
      </tr>`).join('');

    const filterItems = hardFilters.map((f) =>
      `<li>${esc(f.description || f.type || JSON.stringify(f))}</li>`
    ).join('');

    return `
      <div class="mkt-rs-card" data-ruleset-family="${esc(rs.campaignFamily)}">
        <div class="mkt-rs-card-header">
          <div>
            <span class="mkt-rs-family-name">${esc(rs.displayName)}</span>
            <span class="mkt-rs-family-slug mkt-demo-label">${esc(rs.campaignFamily)}</span>
          </div>
          <div class="mkt-rs-card-meta">
            <span class="mkt-rs-version-badge">${esc(vLabel)}</span>
            <span class="mkt-workspace-state-pill mkt-pill-${esc(vState)}">${esc(vState)}</span>
          </div>
        </div>
        ${rs.description ? `<p class="mkt-rs-description">${esc(rs.description)}</p>` : ''}
        <div class="mkt-rs-detail-grid">
          <div class="mkt-rs-detail-col">
            <div class="mkt-rs-detail-heading">Weighted signals</div>
            ${weightedSignals.length > 0 ? `
            <table class="mkt-rs-signal-table">
              <thead><tr><th>Reason code</th><th>Description</th><th>Weight</th></tr></thead>
              <tbody>${signalRows}</tbody>
            </table>` : '<p class="mkt-rs-empty">No weighted signals defined.</p>'}
          </div>
          <div class="mkt-rs-detail-col">
            <div class="mkt-rs-detail-heading">Hard filters</div>
            ${hardFilters.length > 0
              ? `<ul class="mkt-rs-filter-list">${filterItems}</ul>`
              : '<p class="mkt-rs-empty">No hard filters defined.</p>'}
            <div class="mkt-rs-detail-heading" style="margin-top:10px">Min fit score</div>
            <span class="mkt-rs-min-score">${minScore}</span>
          </div>
        </div>
        <div class="mkt-rs-territory-section" data-territory-family="${esc(rs.campaignFamily)}">
          <div class="mkt-rs-detail-heading">Territory priorities</div>
          <div class="mkt-rs-territory-loading">Loading…</div>
        </div>
        <div class="mkt-rs-version-history-section">
          <button class="mkt-btn mkt-btn-sm mkt-btn-ghost mkt-rs-history-toggle"
                  data-family="${esc(rs.campaignFamily)}">Version history</button>
          <div class="mkt-rs-version-history" data-family-history="${esc(rs.campaignFamily)}" style="display:none"></div>
        </div>
      </div>`;
  }).join('');

  const rcSection = reasonCodes.length > 0 ? `
    <section class="mkt-section mkt-rs-reason-codes-section">
      <h3 class="mkt-rs-section-heading">Reason Code Registry</h3>
      <p class="mkt-section-subtext">Canonical trigger codes used by signals and rulesets.</p>
      <table class="mkt-rs-signal-table">
        <thead><tr><th>Code</th><th>Label</th><th>Category</th><th>Families</th></tr></thead>
        <tbody>${reasonCodes.map((rc) => `
          <tr class="${rc.retiredAt ? 'mkt-rs-retired' : ''}">
            <td class="mkt-rs-signal-code">${esc(rc.code)}</td>
            <td>${esc(rc.label)}${rc.retiredAt ? ' <em>(retired)</em>' : ''}</td>
            <td>${esc(rc.category || '—')}</td>
            <td>${(rc.campaignFamilies || []).map((f) => `<span class="mkt-rs-family-chip">${esc(f)}</span>`).join(' ')}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </section>` : '';

  return `
    <section class="mkt-section">
      <h2 class="mkt-section-heading">Scout Rulesets</h2>
      <p class="mkt-section-subtext">Campaign trigger criteria and territory priorities. Read-only — ruleset authoring via agent chat is coming in a later milestone.</p>
      <div class="mkt-rs-cards">${cards}</div>
    </section>
    ${rcSection}`;
}

export async function loadMarketingRulesets(container) {
  if (!container) return;
  container.innerHTML = `<section class="mkt-section"><p class="mkt-section-subtext">Loading rulesets…</p></section>`;

  let rulesets = [];
  let reasonCodes = [];
  try {
    [rulesets, reasonCodes] = await Promise.all([
      listCampaignRulesetsApi().then(async (list) => {
        // Fetch full active-version details for each family
        return Promise.all(list.map((rs) => getCampaignRulesetApi(rs.campaignFamily)));
      }),
      listReasonCodesApi(),
    ]);
  } catch {
    container.innerHTML = `<section class="mkt-section">
      <h2 class="mkt-section-heading">Scout Rulesets</h2>
      <p class="mkt-section-subtext mkt-error-text">Could not load rulesets. Is the server running?</p>
    </section>`;
    return;
  }

  container.innerHTML = renderMarketingRulesets(rulesets, reasonCodes);

  // Async-fill territory sections
  for (const rs of rulesets) {
    const family = rs.campaignFamily;
    const territorySection = container.querySelector(`[data-territory-family="${family}"]`);
    if (!territorySection) continue;
    try {
      const config = await getTerritoryConfigApi(family);
      if (config.length === 0) {
        territorySection.querySelector('.mkt-rs-territory-loading').textContent = 'No states configured.';
        continue;
      }
      const tiers = ['hot', 'standard', 'low', 'excluded'];
      const grouped = {};
      for (const t of tiers) grouped[t] = config.filter((s) => s.priorityTier === t);

      const html = tiers.filter((t) => grouped[t].length > 0).map((t) => `
        <div class="mkt-rs-territory-tier-group">
          ${_tierPill(t)}
          ${grouped[t].map((s) => `
            <span class="mkt-rs-state-chip" title="${esc(s.notes || '')}">
              ${esc(s.stateCode)}
            </span>`).join('')}
        </div>`).join('');
      territorySection.querySelector('.mkt-rs-territory-loading').outerHTML = `<div class="mkt-rs-territory-groups">${html}</div>`;
    } catch {
      territorySection.querySelector('.mkt-rs-territory-loading').textContent = 'Territory data unavailable.';
    }
  }

  // Wire version history toggles
  container.querySelectorAll('.mkt-rs-history-toggle').forEach((btn) => {
    const family = btn.dataset.family;
    const historyDiv = container.querySelector(`[data-family-history="${family}"]`);
    if (!historyDiv) return;
    btn.addEventListener('click', async () => {
      if (historyDiv.style.display !== 'none') {
        historyDiv.style.display = 'none';
        btn.textContent = 'Version history';
        return;
      }
      historyDiv.style.display = 'block';
      btn.textContent = 'Hide history';
      if (historyDiv.dataset.loaded) return;
      historyDiv.innerHTML = '<p class="mkt-rs-empty">Loading…</p>';
      try {
        const versions = await listRulesetVersionsApi(family);
        if (versions.length === 0) {
          historyDiv.innerHTML = '<p class="mkt-rs-empty">No versions recorded.</p>';
          return;
        }
        historyDiv.innerHTML = `
          <table class="mkt-rs-signal-table mkt-rs-history-table">
            <thead><tr><th>Version</th><th>State</th><th>Min score</th><th>Notes</th><th>Activated</th></tr></thead>
            <tbody>${versions.map((v) => {
              const activatedDate = v.activatedAt
                ? new Date(v.activatedAt * 1000).toLocaleDateString()
                : '—';
              return `<tr>
                <td>v${v.versionNumber}</td>
                <td><span class="mkt-workspace-state-pill mkt-pill-${esc(v.state)}">${esc(v.state)}</span></td>
                <td>${Math.round((v.minFitScore || 0) * 100)}%</td>
                <td>${esc(v.notes || '—')}</td>
                <td>${esc(activatedDate)}</td>
              </tr>`;
            }).join('')}</tbody>
          </table>`;
        historyDiv.dataset.loaded = '1';
      } catch {
        historyDiv.innerHTML = '<p class="mkt-rs-empty">Could not load version history.</p>';
      }
    });
  });
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
  CAMPAIGNS, SIGNALS_MOCK, APPROVALS_MOCK, sparklineHtml, statusPillClass,
  WORKSPACE_STATE_LABELS, WORKSPACE_STATE_PILL,
  renderTabBrief, renderAssembledBrief,
};
