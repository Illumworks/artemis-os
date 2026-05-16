// Floating assistant bot — approved Artemis OS design port
// Panel: header (aIcon mark + Artemis name + context) + mode tabs + message body
//        + quick chips + context chip + textarea input.
// WS / session / history logic is unchanged from the original implementation.
import { BOT_CHAT_ID } from '../core/constants.js';
import { emit, on } from '../core/events.js';
import { getState, on as storeOn, setState } from '../core/store.js';
import { renderMarkdown, highlightCodeBlocks, addCopyButtons } from '../ui/formatting.js';
import * as api from '../core/api.js';
import { getSelectedModel, getSelectedProvider, getSelectedReasoningEffort, getSelectedSpeedTier, PROVIDER_LABELS } from '../ui/model-selector.js';
import { $ } from '../core/dom.js';
import { getSetting } from '../components/settings-modal.js';

const SESSIONS_KEY = 'artemis-bot-sessions';
const COMPOSER_CONTEXT_NOTE_STORAGE_KEY = 'artemis-composer-context-note';

// ── Observability state ─────────────────────────────────
let activeRunCount = 0;
let activeRunPollTimer = null;

export function getActiveRunCount() { return activeRunCount; }

function updateFabBadge(count) {
  activeRunCount = count;
  const fab = document.getElementById('assistant-fab');
  if (!fab) return;
  let badge = fab.querySelector('.assistant-fab-badge');
  if (count > 0) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'assistant-fab-badge';
      fab.appendChild(badge);
    }
    badge.textContent = String(count);
  } else if (badge) {
    badge.remove();
  }
}

async function refreshActiveRunCount() {
  try {
    const runs = await api.fetchActiveAgentRuns();
    updateFabBadge(runs.length);
  } catch {
    // non-fatal: badge may be stale but UI stays functional
  }
}

function startActiveRunPolling() {
  if (activeRunPollTimer) return;
  refreshActiveRunCount();
  activeRunPollTimer = setInterval(refreshActiveRunCount, 15_000);
}

// ── Observability intent detection ─────────────────────
// Returns null if no match, otherwise { intent, args }
const ACTIVE_RE = /\b(what('?s| is) (running|happening|active|going on)|what are (the )?agents? doing|list (active|running) (agents?|runs?)|what('?s| is) the status)\b/i;
const BLOCKED_RE = /\b(what('?s| is) blocked|what needs approval|what('?s| is) waiting for approval|show (me )?(blocked|approvals?)|list blocked items)\b/i;
// "show me Scout's last run", "get Scout's latest run", "Scout's last run"
const AGENT_LAST_RE = /\b(?:show\s+me|show|get)\s+(?:me\s+)?([\w][\w .-]{0,39}?)(?:'s?)?\s+(?:last|latest|most\s+recent)\s+run\b/i;
const TODAY_RE = /\bwhat did ([\w][\w\s-]{1,40}?) do (today|this (?:morning|afternoon|evening))\b/i;

export function detectObservabilityIntent(text) {
  const t = text.trim();
  if (ACTIVE_RE.test(t)) return { intent: 'list_active' };
  if (BLOCKED_RE.test(t)) return { intent: 'list_blocked' };

  const agentLast = AGENT_LAST_RE.exec(t);
  if (agentLast) return { intent: 'last_run', agentQ: agentLast[1].trim() };

  const today = TODAY_RE.exec(t);
  if (today) return { intent: 'runs_today', agentQ: today[1].trim() };

  return null;
}

// ── Inline run card rendering ───────────────────────────

function formatDuration(ms) {
  if (!ms) return '—';
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

function formatTokenCount(count) {
  if (!count) return '0';
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return String(count);
}

function formatTs(unixSec) {
  if (!unixSec) return '';
  const d = new Date(unixSec * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function statusChip(status) {
  const cls = { running: 'obs-chip-running', completed: 'obs-chip-done', error: 'obs-chip-error' }[status] || '';
  return `<span class="obs-chip ${cls}">${status}</span>`;
}

function runCardHtml(run) {
  const agentHref = `#agents/${encodeURIComponent(run.agent_id)}`;
  return `
    <div class="obs-run-card" data-agent-id="${_escHtml(run.agent_id)}">
      <div class="obs-run-card-title">
        <a class="obs-agent-link" href="${agentHref}" data-nav-agents="${_escHtml(run.agent_id)}">
          ${_escHtml(run.agent_title)}
        </a>
        ${statusChip(run.status)}
      </div>
      <div class="obs-run-card-meta">
        ${run.run_type !== 'single' ? `<span>${_escHtml(run.run_type)}</span> · ` : ''}
        <span>Started ${formatTs(run.started_at)}</span>
        ${run.turns ? ` · <span>${run.turns} turns</span>` : ''}
        ${run.duration_ms ? ` · <span>${formatDuration(run.duration_ms)}</span>` : ''}
        ${run.cost_usd ? ` · <span>$${run.cost_usd.toFixed(4)}</span>` : ''}
      </div>
      ${run.error ? `<div class="obs-run-card-error">${_escHtml(run.error)}</div>` : ''}
    </div>`;
}

function blockedCardHtml(approval) {
  return `
    <div class="obs-run-card">
      <div class="obs-run-card-title">
        <span>${_escHtml(approval.toolName || 'Approval')}</span>
        <span class="obs-chip obs-chip-error">blocked</span>
      </div>
      <div class="obs-run-card-meta">
        ${approval.chatId ? `<span>Chat ${_escHtml(approval.chatId.slice(0, 20))}</span>` : '<span>Waiting for a decision</span>'}
      </div>
    </div>`;
}

const _escHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function appendObsHtml(title, bodyHtml) {
  if (!messagesDiv) return;
  removeBotEmpty();
  const div = document.createElement('div');
  div.className = 'assistant-msg assistant obs-card-msg';
  div.innerHTML = `
    <div class="assistant-msg-head">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
      <span>Artemis</span>
    </div>
    <div class="obs-card-title">${_escHtml(title)}</div>
    <div class="obs-card-body">${bodyHtml}</div>`;
  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
  return div;
}

function appendObsCard(title, runs) {
  const body = runs.length
    ? runs.map(runCardHtml).join('')
    : '<div class="obs-empty">No runs found.</div>';
  const div = appendObsHtml(title, body);
  if (!div) return;
  // Wire nav links
  div.querySelectorAll('[data-nav-agents]').forEach((link) => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      setState('view', 'agents');
    });
  });
}

function runDetailCardHtml(run) {
  const detailRows = [
    ['Status', run.status || 'unknown'],
    ['Run type', run.run_type || 'single'],
    ['Started', run.started_at ? new Date(run.started_at * 1000).toLocaleString() : '—'],
    ['Duration', formatDuration(run.duration_ms)],
    ['Turns', run.turns != null ? String(run.turns) : '—'],
    ['Cost', run.cost_usd != null ? `$${Number(run.cost_usd).toFixed(4)}` : '—'],
    ['Tokens', `${formatTokenCount(run.input_tokens || 0)} in / ${formatTokenCount(run.output_tokens || 0)} out`],
    ['Run ID', run.run_id || '—'],
  ];
  const errorHtml = run.error
    ? `<div class="obs-run-detail-error">${_escHtml(run.error)}</div>`
    : '';
  return `
    <div class="obs-run-detail-card" data-run-detail-id="${_escHtml(run.run_id || '')}">
      <div class="obs-run-detail-head">
        <div>
          <div class="obs-run-detail-agent">${_escHtml(run.agent_title || run.agent_id || 'Agent')}</div>
          <div class="obs-run-detail-subhead">${_escHtml(run.agent_id || 'run')}</div>
        </div>
        ${statusChip(run.status)}
      </div>
      <div class="obs-run-detail-grid">
        ${detailRows.map(([label, value]) => `
          <div class="obs-run-detail-row">
            <span class="obs-run-detail-label">${_escHtml(label)}</span>
            <span class="obs-run-detail-value">${_escHtml(value)}</span>
          </div>
        `).join('')}
      </div>
      ${errorHtml}
    </div>`;
}

async function openRunDetail(runId) {
  if (!runId) return;
  try {
    const run = await api.fetchAgentRunById(runId);
    if (!run) {
      appendObsHtml('Run details unavailable', '<div class="obs-empty">That run could not be found.</div>');
      return;
    }
    appendObsHtml(`Run details: ${run.agent_title || run.agent_id || 'Agent'}`, runDetailCardHtml(run));
  } catch (err) {
    appendObsHtml('Run details unavailable', `<div class="obs-empty">${_escHtml(err.message || 'Could not load this run.')}</div>`);
  }
}

// ── Handle observability query (before sending to Claude) ───
async function handleObsQuery(intent) {
  try {
    if (intent.intent === 'list_active') {
      const runs = await api.fetchActiveAgentRuns();
      const title = runs.length
        ? `${runs.length} agent${runs.length === 1 ? '' : 's'} currently running`
        : 'No agents running right now';
      appendObsCard(title, runs);
    } else if (intent.intent === 'list_blocked') {
      const blocked = [...pendingApprovalMap.values()];
      const title = blocked.length
        ? `${blocked.length} item${blocked.length === 1 ? '' : 's'} waiting for approval`
        : 'No approvals are blocked right now';
      appendObsHtml(
        title,
        blocked.length ? blocked.map(blockedCardHtml).join('') : '<div class="obs-empty">Nothing is waiting on approval.</div>',
      );
    } else if (intent.intent === 'last_run') {
      const runs = await api.searchAgentRunsApi({ q: intent.agentQ, limit: 1 });
      appendObsCard(
        `Last run: ${intent.agentQ}`,
        runs.slice(0, 1),
      );
    } else if (intent.intent === 'runs_today') {
      const today = new Date().toISOString().slice(0, 10);
      const runs = await api.searchAgentRunsApi({ q: intent.agentQ, date: today, limit: 10 });
      appendObsCard(
        `${intent.agentQ} — today's runs`,
        runs,
      );
    }
  } catch (err) {
    appendMessage('error', `Could not fetch run data: ${err.message}`);
  }
}

let panel, messagesDiv, inputEl, sendBtn, stopBtn, settingsOverlay, promptTextarea, contextChipEl;
let botAttachBtn, botImageFileInput, botImagePreview;
let botImages = []; // [{ name, data, mimeType }]
let freeBotSessionId = null;
let isStreaming = false;
let currentAssistantEl = null;
let cachedSystemPrompt = null;
let cachedSlackContext = null;  // { channels: [{id,name}], users: [{id,name,displayName}] }
let cachedJiraContext = null;   // { siteUrl, connected }

async function loadLiveContext() {
  try {
    const [slackRes, jiraRes] = await Promise.allSettled([
      fetch('/api/slack/cache'),
      fetch('/api/jira/overview'),
    ]);
    if (slackRes.status === 'fulfilled' && slackRes.value.ok) {
      cachedSlackContext = await slackRes.value.json();
    }
    if (jiraRes.status === 'fulfilled' && jiraRes.value.ok) {
      const j = await jiraRes.value.json();
      if (j.connected) cachedJiraContext = { siteUrl: j.siteUrl, connected: true };
    }
  } catch {}
}

// ── Sidebar state ───────────────────────────────────────
let sidebarOpen = false;
let sidebarRecentWindow = '24h'; // '24h' or '7d'
// Pending approvals tracked via WS permission_request events (any chatId)
export const pendingApprovalMap = new Map(); // id → { id, toolName, input, chatId }
export function isSidebarOpen() { return sidebarOpen; }

// ── Session management ──────────────────────────────────

function getBotSessions() {
  try {
    return JSON.parse(localStorage.getItem(SESSIONS_KEY) || '{}');
  } catch { return {}; }
}

function setBotSession(projectPath, sessionId) {
  const sessions = getBotSessions();
  sessions[projectPath] = sessionId;
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
}

function getCurrentProject() {
  return $.projectSelect?.value || '';
}

// ── Context chip ────────────────────────────────────────

const VIEW_LABELS = {
  'command-center':     'Focus',
  'workspace':          'Workspace',
  'calendar':           'Calendar',
  'meetings':           'Meetings',
  'jira':               'Jira Board',
  'jira-board':         'Jira Board',
  'okr':                'OKR Studio',
  'operations':         'Operations',
  'memory':             'Memory',
  'writing-studio':     'Writing Studio',
  'automations':        'Campaign Ops',
  'agents':             'Agents',
  'workflows':          'Workflows',
  'skills':             'Skills',
  'marketing-os':       'Marketing',
  'marketing-dashboard':'Marketing Dashboard',
  'marketing-campaigns':'Campaigns',
  'marketing-signals':  'Signals Inbox',
  'marketing-approvals':'Approval Queue',
};

// ── Context-aware chips ───────────────────────────────────────────────────────
// { label, prompt, autoSend? }
// autoSend: true  → fires immediately when clicked (no pre-fill step)
// autoSend: false → drops into input box for user to complete/edit

const WHAT_NEXT_PROMPT = "What should I focus on right now? Check my Jira tickets, any pending Slack messages that need a response, OKR progress, and anything overdue. Give me a short prioritized list.";
const WHAT_NEXT_MARKETING = "What should I focus on right now from a marketing perspective? Check campaign status, pending approvals, and any signals that need review. Give me a short prioritized list.";

const CONTEXT_CHIPS = {
  'command-center': [
    { label: "What's next?",        prompt: WHAT_NEXT_PROMPT,  autoSend: true },
    { label: 'Send Slack message',  prompt: 'Send a Slack message to ' },
    { label: 'Search Jira',         prompt: 'Search Jira for ' },
  ],
  'workspace': [
    { label: "What's next?",        prompt: WHAT_NEXT_PROMPT,  autoSend: true },
    { label: 'Search Jira',         prompt: 'Search Jira for ' },
    { label: 'Send Slack message',  prompt: 'Send a Slack message to ' },
  ],
  'jira': [
    { label: "What's blocked?",     prompt: "Look at my Jira board and tell me what's currently blocked or at risk. Use GET /api/jira/overview.", autoSend: true },
    { label: 'Draft standup',       prompt: "Write a standup update based on my Jira tickets. Use GET /api/jira/overview to see what I worked on, what I'm doing today, and any blockers.", autoSend: true },
    { label: 'Triage unassigned',   prompt: "Show me unassigned Jira tickets that need to be assigned. Use GET /api/jira/overview.", autoSend: true },
  ],
  'jira-board': [
    { label: "What's blocked?",     prompt: "Look at my Jira board and tell me what's currently blocked or at risk. Use GET /api/jira/overview.", autoSend: true },
    { label: 'Draft standup',       prompt: "Write a standup update based on my Jira tickets. Use GET /api/jira/overview to see what I worked on, what I'm doing today, and any blockers.", autoSend: true },
    { label: 'Triage unassigned',   prompt: "Show me unassigned Jira tickets that need to be assigned. Use GET /api/jira/overview.", autoSend: true },
  ],
  'okr': [
    { label: "What's at risk?",         prompt: "Review my OKRs and tell me which ones are at risk of not being completed on time. Use GET /api/okr/objectives.", autoSend: true },
    { label: 'Write progress update',   prompt: "Draft a brief OKR progress update based on current scores. Use GET /api/okr/objectives.", autoSend: true },
    { label: "What's next?",            prompt: WHAT_NEXT_PROMPT, autoSend: true },
  ],
  'agents': [
    { label: 'What ran today?',     prompt: "What agents ran today and what did they do? Summarize recent agent activity.", autoSend: true },
    { label: 'Run workflow',        prompt: 'Run the ' },
    { label: "What's next?",        prompt: WHAT_NEXT_PROMPT, autoSend: true },
  ],
  'workflows': [
    { label: 'What ran today?',     prompt: "What workflows ran today and what were the results?", autoSend: true },
    { label: "What's next?",        prompt: WHAT_NEXT_PROMPT, autoSend: true },
  ],
  'automations': [
    { label: "What's behind?",      prompt: "Which campaigns are behind schedule or at risk? Give me a concise status report.", autoSend: true },
    { label: "What's next?",        prompt: WHAT_NEXT_MARKETING, autoSend: true },
  ],
  'marketing-os': [
    { label: "What needs attention?",   prompt: "What marketing work needs attention right now? Check campaign status, pending approvals, and unreviewed signals.", autoSend: true },
    { label: "What's next?",            prompt: WHAT_NEXT_MARKETING, autoSend: true },
  ],
  'marketing-dashboard': [
    { label: "What needs attention?",   prompt: "What campaigns need attention right now? What's behind schedule or at risk?", autoSend: true },
    { label: 'Summarize performance',   prompt: "Summarize current campaign performance. What are the key metrics and how are we tracking against goals?", autoSend: true },
    { label: "What's next?",            prompt: WHAT_NEXT_MARKETING, autoSend: true },
  ],
  'marketing-campaigns': [
    { label: "What's behind?",          prompt: "Which campaigns are behind schedule or at risk? Give me a concise status report.", autoSend: true },
    { label: 'Create campaign brief',   prompt: 'Draft a new campaign brief for ' },
    { label: "What's next?",            prompt: WHAT_NEXT_MARKETING, autoSend: true },
  ],
  'marketing-signals': [
    { label: 'Summarize signals',       prompt: "Summarize the latest marketing signals. Which ones are most significant and warrant a new campaign?", autoSend: true },
    { label: 'What needs a response?',  prompt: "Which signals haven't been reviewed yet and need attention?", autoSend: true },
    { label: "What's next?",            prompt: WHAT_NEXT_MARKETING, autoSend: true },
  ],
  'marketing-approvals': [
    { label: "What's pending?",         prompt: "What's currently in the approval queue? Give me a summary of what's waiting for review.", autoSend: true },
    { label: "What's blocked?",         prompt: "Are there approvals blocking downstream work? What needs to be reviewed urgently?", autoSend: true },
    { label: "What's next?",            prompt: WHAT_NEXT_MARKETING, autoSend: true },
  ],
  // Writing Studio: intentionally empty — use the dedicated Writing Studio agent
  'writing-studio': [],
  // Default fallback
  '_default': [
    { label: "What's next?",        prompt: WHAT_NEXT_PROMPT,  autoSend: true },
    { label: 'Send Slack message',  prompt: 'Send a Slack message to ' },
    { label: 'Search Jira',         prompt: 'Search Jira for ' },
  ],
};

function getViewLabel() {
  const view = getState('view') || '';
  return VIEW_LABELS[view] || view || 'Focus';
}

function getProjectLabel() {
  const sel = $.projectSelect;
  if (!sel?.value) return '';
  if (sel.options && sel.selectedIndex >= 0) {
    const opt = sel.options[sel.selectedIndex];
    if (opt?.text) return opt.text;
  }
  return sel.value.split('/').filter(Boolean).pop() || '';
}

function updateContextChip() {
  if (!contextChipEl) return;
  const project = getProjectLabel();
  const view = getViewLabel();
  const sessionId = getState('sessionId');
  const parts = [project || null, view !== 'Focus' || !project ? view : null].filter(Boolean);
  if (sessionId) parts.push('Chat');
  contextChipEl.textContent = parts.join(' · ') || 'Focus';
  renderContextChips();
}

function renderContextChips() {
  const quickEl = panel?.querySelector('.assistant-quick');
  if (!quickEl) return;
  const view = getState('view') || '';
  const chips = CONTEXT_CHIPS[view] ?? CONTEXT_CHIPS['_default'];
  if (!chips.length) {
    quickEl.innerHTML = '';
    return;
  }
  quickEl.innerHTML = chips.map((c) =>
    `<div class="assistant-quick-chip${c.autoSend ? ' auto-send' : ''}" data-prompt="${_escHtml(c.prompt)}">${_escHtml(c.label)}</div>`
  ).join('');
  quickEl.querySelectorAll('.assistant-quick-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const prompt = chip.dataset.prompt || chip.textContent.trim();
      if (chip.classList.contains('auto-send')) {
        inputEl.value = prompt;
        inputEl.dispatchEvent(new Event('input'));
        sendBotMessage();
      } else {
        inputEl.value = prompt;
        inputEl.dispatchEvent(new Event('input'));
        inputEl.focus();
        // Move cursor to end so user can continue typing
        inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
      }
    });
  });
}

export function getCurrentPageContext() {
  const project = getProjectLabel();
  const view = getViewLabel();
  const sessionId = getState('sessionId');
  const lines = [`Current view: ${view}`];
  if (project) lines.push(`Active project: ${project}`);
  if (sessionId) lines.push(`A chat session is open alongside this panel.`);

  // ── Connected integrations (lightweight — fetch details on demand) ───────
  const sc = cachedSlackContext;
  const jc = cachedJiraContext;

  if (sc && (sc.channels?.length || sc.users?.length)) {
    lines.push(
      `Slack connected: ${sc.channels?.length || 0} channels cached, ${sc.users?.length || 0} users cached.` +
      ` When you need to send a message, call GET /api/slack/cache to get channel/user IDs, then POST /api/slack/send { channel: "<ID>", text }.` +
      ` Always use IDs (C…/U…) — never channel names — to avoid rate limits.`
    );
  } else {
    lines.push(`Slack: POST /api/slack/send { channel, text }. Call GET /api/slack/cache for channel/user IDs first.`);
  }

  if (jc?.connected) {
    lines.push(
      `Jira connected (${jc.siteUrl}): GET /api/jira/overview → { issues, sprints, siteUrl }.` +
      ` GET /api/jira/search?q=keyword → search issues by text (use this to find tickets by name).` +
      ` GET /api/jira/issue/:key → full issue detail. POST /api/jira/issue/:key/comment { body }.` +
      ` Issue URLs: ${jc.siteUrl}/browse/:key`
    );
  } else {
    lines.push(`Jira: GET /api/jira/search?q=keyword to find tickets. GET /api/jira/overview for board. GET /api/jira/issue/:key for detail.`);
  }

  lines.push(`Other APIs: GET /api/okr/objectives, GET /api/calendar/overview, GET /api/google/overview.`);

  lines.push([
    `BEHAVIOR: You are a conversational assistant. NEVER dump raw JSON or command output.`,
    `NEVER fabricate ticket numbers, URLs, names, or any data — if you can't find something, say so and ask the user for clarification.`,
    `Slack @mentions: just write @FirstName or @display.name in the message text (e.g. "Hi @Angel, ..."). The server resolves names to Slack user IDs automatically — no need to look up IDs or use <@ID> syntax yourself.`,
    `After acting, narrate the result naturally: "Sent to #marketing-design-projects ✓" — not a JSON blob.`,
    `If something fails, say why in one sentence and suggest the fix. Keep responses short and chat-like.`,
    `Do not announce what you are about to do — just do it and report back.`,
  ].join(' '));

  return lines.join('\n');
}

// ── Sidebar helpers ─────────────────────────────────────

function openSidebar() {
  sidebarOpen = true;
  panel.classList.add('sidebar-open');
  refreshSidebar();
}

function closeSidebar() {
  sidebarOpen = false;
  sidebarRecentWindow = '24h';
  panel.classList.remove('sidebar-open');
}

function toggleSidebar() {
  if (sidebarOpen) closeSidebar(); else openSidebar();
}

function sidebarItemHtml(run) {
  const agentHref = `#agents/${encodeURIComponent(run.agent_id || '')}`;
  const runIdLabel = run.run_id ? `Run ${_escHtml(String(run.run_id).slice(0, 8))}` : 'Run';
  return `
    <div class="sidebar-item" data-agent-id="${_escHtml(run.agent_id)}" data-run-id="${_escHtml(run.run_id || '')}">
      <div class="sidebar-item-title">
        <a class="sidebar-item-link" href="${agentHref}" data-nav-agents="${_escHtml(run.agent_id)}">
          ${_escHtml(run.agent_title || run.agent_id || 'Agent')}
        </a>
        ${statusChip(run.status)}
      </div>
      <div class="sidebar-item-meta">
        ${run.started_at ? `<span>${formatTs(run.started_at)}</span>` : ''}
        ${run.turns ? ` · <span>${run.turns}t</span>` : ''}
        ${run.cost_usd ? ` · <span>$${run.cost_usd.toFixed(3)}</span>` : ''}
      </div>
      <div class="sidebar-item-actions">
        <div class="sidebar-inline-actions">
          <button class="sidebar-inline-action" type="button" data-open-run="${_escHtml(run.run_id || '')}">
            Open run
          </button>
          <button class="sidebar-inline-action" type="button" data-nav-agents="${_escHtml(run.agent_id || '')}">
            Open agent
          </button>
        </div>
        <span class="sidebar-item-run-label">${runIdLabel}</span>
      </div>
    </div>`;
}

function summarizeApprovalInput(input) {
  if (!input || typeof input !== 'object') return '';
  if (typeof input.cmd === 'string' && input.cmd.trim()) return input.cmd.trim();
  if (typeof input.command === 'string' && input.command.trim()) return input.command.trim();
  if (typeof input.path === 'string' && input.path.trim()) return input.path.trim();
  if (typeof input.target === 'string' && input.target.trim()) return input.target.trim();
  return '';
}

function blockedItemHtml(approval) {
  const inputSummary = summarizeApprovalInput(approval.input);
  const openChatButton = approval.sessionId
    ? `<button class="sidebar-inline-action" type="button" data-nav-session="${_escHtml(approval.sessionId)}">Open chat</button>`
    : `<span class="sidebar-item-run-label">Awaiting active session</span>`;
  return `
    <div class="sidebar-item sidebar-item-blocked" data-approval-id="${_escHtml(approval.id)}">
      <div class="sidebar-item-title">
        <span class="sidebar-item-blocked-icon">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </span>
        <span class="sidebar-item-label">${_escHtml(approval.toolName || 'Approval')}</span>
        <span class="sidebar-mini-chip sidebar-mini-chip-blocked">Waiting</span>
      </div>
      <div class="sidebar-item-meta">
        <span>${approval.chatId ? `Chat ${_escHtml(approval.chatId.slice(0, 20))}` : 'Waiting for a decision'}</span>
        ${inputSummary ? `<div class="sidebar-item-detail">${_escHtml(inputSummary)}</div>` : ''}
      </div>
      <div class="sidebar-item-actions">
        ${openChatButton}
        <span class="sidebar-item-run-label">Approval</span>
      </div>
    </div>`;
}

function handleSidebarAgentNav(e) {
  e.preventDefault();
  setState('view', 'agents');
}

function handleSidebarSessionNav(e) {
  e.preventDefault();
  const sessionId = e.currentTarget.dataset.navSession;
  if (!sessionId) return;
  localStorage.setItem(COMPOSER_CONTEXT_NOTE_STORAGE_KEY, 'Approval reopened from Activity sidebar.');
  emit('session:switch', sessionId);
  setState('view', 'chat');
}

function handleSidebarRunNav(e) {
  e.preventDefault();
  const runId = e.currentTarget.dataset.openRun;
  openRunDetail(runId);
}

function sectionHtml(title, count, bodyHtml, extra = '') {
  return `
    <div class="sidebar-section">
      <div class="sidebar-section-hd">
        <span>${title}</span>
        <span class="sidebar-section-count">${count}</span>
      </div>
      <div class="sidebar-section-body">${bodyHtml}</div>
      ${extra}
    </div>`;
}

async function refreshSidebar() {
  const sidebarBody = panel?.querySelector('.sidebar-body');
  if (!sidebarBody || !sidebarOpen) return;

  const [liveRuns, allRecent] = await Promise.all([
    api.fetchActiveAgentRuns().catch(() => []),
    api.fetchRecentAgentRuns(sidebarRecentWindow === '7d' ? 100 : 30).catch(() => []),
  ]);

  const nowSec = Date.now() / 1000;
  const cutoffSec = sidebarRecentWindow === '24h' ? (nowSec - 86400) : (nowSec - 604800);
  const recentRuns = allRecent.filter(
    (r) => r.status !== 'running' && (!r.started_at || r.started_at > cutoffSec),
  );

  const liveHtml = liveRuns.length
    ? liveRuns.map(sidebarItemHtml).join('')
    : '<div class="sidebar-empty">No agents running</div>';

  const blocked = [...pendingApprovalMap.values()];
  const blockedHtml = blocked.length
    ? blocked.map(blockedItemHtml).join('')
    : '<div class="sidebar-empty">None</div>';

  const recentHtml = recentRuns.length
    ? recentRuns.slice(0, sidebarRecentWindow === '24h' ? 10 : 40).map(sidebarItemHtml).join('')
    : '<div class="sidebar-empty">No runs in this window</div>';

  const showMoreLabel = sidebarRecentWindow === '24h' ? 'Show 7 days' : 'Show today only';

  const recentTitle = `Recent <span class="sidebar-window-tag">${_escHtml(sidebarRecentWindow)}</span>`;
  sidebarBody.innerHTML = `
    ${sectionHtml('Live', liveRuns.length, liveHtml)}
    ${sectionHtml('Blocked', blocked.length, blockedHtml)}
    ${sectionHtml(recentTitle, recentRuns.length, recentHtml, `<button class="sidebar-show-more">${showMoreLabel}</button>`)}
  `;

  // Wire nav links
  sidebarBody.querySelectorAll('[data-nav-agents]').forEach((link) => {
    link.addEventListener('click', handleSidebarAgentNav);
  });
  sidebarBody.querySelectorAll('[data-open-run]').forEach((button) => {
    button.addEventListener('click', handleSidebarRunNav);
  });
  sidebarBody.querySelectorAll('[data-nav-session]').forEach((button) => {
    button.addEventListener('click', handleSidebarSessionNav);
  });

  sidebarBody.querySelector('.sidebar-show-more')?.addEventListener('click', () => {
    sidebarRecentWindow = sidebarRecentWindow === '24h' ? '7d' : '24h';
    refreshSidebar();
  });
}

// ── DOM creation ────────────────────────────────────────

function createBotDOM() {
  // Wire the shell's #assistant-fab directly
  const fab = document.getElementById('assistant-fab');
  if (fab) fab.addEventListener('click', togglePanel);

  // Panel — approved design structure (two-pane: chat + sidebar)
  panel = document.createElement('div');
  panel.className = 'assistant-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Artemis assistant');
  panel.innerHTML = `
    <div class="assistant-chat-pane">
      <div class="assistant-header">
        <div class="assistant-mark">
          <img src="/icons/aIcon.png" alt="Artemis" class="assistant-mark-img">
        </div>
        <div class="assistant-info">
          <div class="assistant-name">Artemis</div>
          <div class="assistant-context">Operations · Dev Projects</div>
        </div>
        <div class="assistant-header-actions">
          <button class="assistant-header-btn bot-sidebar-toggle-btn" title="Activity sidebar">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>
          </button>
          <button class="assistant-header-btn bot-new-btn" title="New chat">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>
          </button>
          <button class="assistant-header-btn bot-settings-btn" title="Settings">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          </button>
          <button class="assistant-close bot-close-btn" title="Close">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
      </div>

      <div class="assistant-body"></div>

      <div class="assistant-quick"></div>

      <div class="assistant-input-wrap">
        <div class="assistant-context-row">
          <span class="assistant-context-chip">
            <span class="assistant-context-dot"></span>
            Context: <span class="assistant-context-label">Home</span>
          </span>
          <div class="assistant-provider-wrap">
            <button class="assistant-provider-btn" title="Switch provider">
              <span class="assistant-provider-label"></span>
              <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
            <div class="assistant-provider-drop hidden"></div>
          </div>
        </div>
        <div class="assistant-image-preview bot-image-preview hidden"></div>
        <div class="bot-composer-pill">
          <input type="file" class="bot-image-file-input" accept="image/png,image/jpeg,image/gif,image/webp" style="display:none" multiple />
          <textarea class="bot-input" rows="1" placeholder="Ask, delegate, or reshape the plan…"></textarea>
          <div class="bot-pill-action">
            <button type="button" class="bot-pill-send bot-send-btn" title="Send (Enter)" aria-label="Send">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>
              </svg>
            </button>
            <button type="button" class="bot-pill-stop bot-stop-btn" title="Stop" aria-label="Stop">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <div class="assistant-sidebar" aria-label="Activity sidebar">
      <div class="sidebar-hd">
        <span class="sidebar-hd-title">Activity</span>
      </div>
      <div class="sidebar-body"></div>
    </div>

    <div class="bot-settings-overlay">
      <div class="bot-settings-header">
        <span>System Prompt</span>
        <button class="assistant-header-btn bot-settings-close">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div class="bot-settings-body">
        <label>Your instructions:</label>
        <textarea class="bot-prompt-textarea" placeholder="Enter system prompt..."></textarea>
        <details class="bot-settings-context-details">
          <summary>Built-in context (read-only)</summary>
          <pre class="bot-settings-context-pre"></pre>
        </details>
      </div>
      <div class="bot-settings-actions">
        <button class="bot-settings-cancel">Cancel</button>
        <button class="bot-settings-save primary">Save</button>
      </div>
    </div>
  `;

  document.body.appendChild(panel);

  // Cache DOM references (all inside .assistant-chat-pane — unchanged selectors still work)
  messagesDiv      = panel.querySelector('.assistant-body');
  inputEl          = panel.querySelector('.bot-input');
  sendBtn          = panel.querySelector('.bot-send-btn');
  stopBtn          = panel.querySelector('.bot-stop-btn');
  settingsOverlay  = panel.querySelector('.bot-settings-overlay');
  promptTextarea   = panel.querySelector('.bot-prompt-textarea');
  contextChipEl    = panel.querySelector('.assistant-context-label');
  botImageFileInput = panel.querySelector('.bot-image-file-input');
  botImagePreview  = panel.querySelector('.bot-image-preview');
  updateContextChip();

  // Provider pill
  const providerLabelEl = panel.querySelector('.assistant-provider-label');
  const providerDrop    = panel.querySelector('.assistant-provider-drop');
  const providerBtn     = panel.querySelector('.assistant-provider-btn');

  const BOT_PROVIDERS = ['claude-code', 'codex', 'gemini', 'openrouter', 'local'];
  const SOURCE_KEY = 'artemis-provider-source';

  function renderProviderDrop() {
    const current = getSelectedProvider();
    providerDrop.innerHTML = BOT_PROVIDERS.map((id) => `
      <button class="assistant-provider-opt${id === current ? ' active' : ''}" data-provider="${id}">
        ${PROVIDER_LABELS[id] || id}
      </button>
    `).join('');
  }

  function updateProviderPill() {
    if (!providerLabelEl) return;
    providerLabelEl.textContent = PROVIDER_LABELS[getSelectedProvider()] || getSelectedProvider();
  }

  function closeProviderDrop() {
    providerDrop.classList.add('hidden');
  }

  providerBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = !providerDrop.classList.contains('hidden');
    if (isOpen) { closeProviderDrop(); return; }
    renderProviderDrop();
    providerDrop.classList.remove('hidden');
  });

  providerDrop.addEventListener('click', (e) => {
    const opt = e.target.closest('[data-provider]');
    if (!opt) return;
    const id = opt.dataset.provider;
    // Write through to the main model-selector
    if ($.sourceSelect) {
      $.sourceSelect.value = id;
      $.sourceSelect.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      localStorage.setItem(SOURCE_KEY, id);
    }
    updateProviderPill();
    closeProviderDrop();
  });

  document.addEventListener('click', (e) => {
    if (!providerDrop.classList.contains('hidden') &&
        !providerBtn.contains(e.target) && !providerDrop.contains(e.target)) {
      closeProviderDrop();
    }
  });

  // Keep pill in sync when provider changes from anywhere else in the app
  if ($.sourceSelect) {
    $.sourceSelect.addEventListener('change', updateProviderPill);
  }

  updateProviderPill();

  // Header action buttons
  panel.querySelector('.bot-close-btn').addEventListener('click', closePanel);
  panel.querySelector('.bot-new-btn').addEventListener('click', newBotSession);
  panel.querySelector('.bot-settings-btn').addEventListener('click', openSettings);
  panel.querySelector('.bot-settings-close').addEventListener('click', closeSettings);
  panel.querySelector('.bot-settings-cancel').addEventListener('click', closeSettings);
  panel.querySelector('.bot-settings-save').addEventListener('click', saveSettings);
  panel.querySelector('.bot-sidebar-toggle-btn').addEventListener('click', toggleSidebar);

  // Quick chips are rendered dynamically by renderContextChips()

  // Send / stop / Enter key
  sendBtn.addEventListener('click', sendBotMessage);
  stopBtn.addEventListener('click', stopBotGeneration);

  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendBotMessage();
    }
  });

  inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + 'px';
  });

  const botComposerPill = panel.querySelector('.bot-composer-pill');
  inputEl.addEventListener('focus', () => botComposerPill?.classList.add('is-focused'));
  inputEl.addEventListener('blur', () => botComposerPill?.classList.remove('is-focused'));

  // Image file input (triggered programmatically if needed)
  botImageFileInput?.addEventListener('change', () => {
    for (const file of botImageFileInput.files) addBotImage(file);
    botImageFileInput.value = '';
  });

  // Paste: images from clipboard
  inputEl.addEventListener('paste', (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        e.preventDefault();
        addBotImage(item.getAsFile());
      }
    }
  });

  // Drag-and-drop images onto the textarea
  inputEl.addEventListener('dragover', (e) => {
    if ([...e.dataTransfer.types].includes('Files')) {
      e.preventDefault();
      panel.querySelector('.bot-composer-pill')?.classList.add('drag-over');
    }
  });
  inputEl.addEventListener('dragleave', () => {
    panel.querySelector('.bot-composer-pill')?.classList.remove('drag-over');
  });
  inputEl.addEventListener('drop', (e) => {
    panel.querySelector('.bot-composer-pill')?.classList.remove('drag-over');
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    for (const file of e.dataTransfer.files) {
      if (file.type.startsWith('image/')) addBotImage(file);
    }
  });
}

// ── Bot image attachments ────────────────────────────────

const BOT_SUPPORTED_IMAGE_TYPES = ['image/png','image/jpeg','image/gif','image/webp'];

function addBotImage(file) {
  if (!BOT_SUPPORTED_IMAGE_TYPES.includes(file.type)) return;
  if (file.size > 5 * 1024 * 1024) return;
  const reader = new FileReader();
  reader.onload = () => {
    botImages.push({ name: file.name, data: reader.result.split(',')[1], mimeType: file.type });
    renderBotImagePreview();
  };
  reader.readAsDataURL(file);
}

function removeBotImage(i) {
  botImages.splice(i, 1);
  renderBotImagePreview();
}

function renderBotImagePreview() {
  if (!botImagePreview) return;
  botImagePreview.innerHTML = '';
  if (!botImages.length) { botImagePreview.classList.add('hidden'); return; }
  botImagePreview.classList.remove('hidden');
  botImages.forEach((img, i) => {
    const item = document.createElement('div');
    item.className = 'bot-img-preview-item';
    const imgEl = document.createElement('img');
    imgEl.src = `data:${img.mimeType};base64,${img.data}`;
    imgEl.alt = img.name;
    const rm = document.createElement('button');
    rm.className = 'bot-img-preview-remove';
    rm.textContent = '×';
    rm.addEventListener('click', (e) => { e.stopPropagation(); removeBotImage(i); });
    item.appendChild(imgEl);
    item.appendChild(rm);
    botImagePreview.appendChild(item);
  });
}

function clearBotImages() {
  botImages = [];
  renderBotImagePreview();
}

// ── Panel toggle ────────────────────────────────────────

function onOutsideClick(e) {
  const fab = document.getElementById('assistant-fab');
  if (panel.contains(e.target) || fab?.contains(e.target)) return;
  closePanel();
}

function togglePanel() {
  if (panel.classList.contains('open')) {
    closePanel();
  } else {
    openPanel();
  }
}

function openPanel() {
  closeSidebar();
  panel.classList.add('open');
  freeBotSessionId = getBotSessions()['__free__'] || null;
  loadBotHistory();
  inputEl.focus();
  loadLiveContext(); // refresh Slack cache + Jira context in background
}

function closePanel() {
  panel.classList.remove('open');
  closeSettings();
}

// ── System prompt ───────────────────────────────────────

async function fetchSystemPrompt() {
  try {
    const data = await fetch('/api/bot/prompt').then(r => r.json());
    cachedSystemPrompt = data.systemPrompt || '';
    return cachedSystemPrompt;
  } catch {
    return cachedSystemPrompt || '';
  }
}

async function openSettings() {
  const prompt = await fetchSystemPrompt();
  promptTextarea.value = prompt;
  const ctxPre = settingsOverlay.querySelector('.bot-settings-context-pre');
  if (ctxPre) ctxPre.textContent = getCurrentPageContext();
  settingsOverlay.classList.add('open');
  promptTextarea.focus();
}

function closeSettings() {
  settingsOverlay.classList.remove('open');
}

async function saveSettings() {
  const newPrompt = promptTextarea.value.trim();
  try {
    await fetch('/api/bot/prompt', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ systemPrompt: newPrompt }),
    });
    cachedSystemPrompt = newPrompt;
  } catch (err) {
    console.error('Failed to save bot prompt:', err);
  }
  closeSettings();
}

// ── Send message ────────────────────────────────────────

function getActiveBotSessionId() {
  return freeBotSessionId;
}

async function sendBotMessage() {
  const text = inputEl.value.trim();
  if (!text || isStreaming) return;

  // Observability intent: handle locally without sending to Claude
  const obsIntent = detectObservabilityIntent(text);
  if (obsIntent) {
    appendMessage('user', text);
    inputEl.value = '';
    inputEl.style.height = 'auto';
    await handleObsQuery(obsIntent);
    return;
  }

  const ws = getState('ws');
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  const cwd = getCurrentProject() || '/tmp';

  if (cachedSystemPrompt === null) {
    await fetchSystemPrompt();
  }

  const pendingImages = [...botImages];
  appendMessage('user', text);
  inputEl.value = '';
  inputEl.style.height = 'auto';
  clearBotImages();

  isStreaming = true;
  sendBtn.classList.add('hidden-btn');
  stopBtn.classList.add('visible');
  currentAssistantEl = null;

  const model = getSelectedModel();
  const provider = getSelectedProvider();
  const reasoningEffort = getSelectedReasoningEffort();
  const speedTier = getSelectedSpeedTier();
  const activeSid = getActiveBotSessionId();

  const pageCtx = getCurrentPageContext();
  const systemPrompt = [cachedSystemPrompt, pageCtx].filter(Boolean).join('\n\n---\n');

  const payload = {
    type: 'chat',
    message: text,
    chatId: BOT_CHAT_ID,
    systemPrompt,
    cwd,
    sessionId: activeSid,
    projectName: 'Assistant Bot',
    permissionMode: 'bypass',
  };

  if (provider) payload.provider = provider;
  if (model) payload.model = model;
  if (reasoningEffort) payload.reasoningEffort = reasoningEffort;
  if (speedTier && speedTier !== "standard") payload.speedTier = speedTier;
  if (pendingImages.length) payload.images = pendingImages.map(({ name, data, mimeType }) => ({ name, data, mimeType }));

  ws.send(JSON.stringify(payload));
  showBotThinking('Thinking…');
}

function stopBotGeneration() {
  const ws = getState('ws');
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'abort', chatId: BOT_CHAT_ID }));
  }
}

function buildDispatchSummary(payload = {}) {
  const lines = [];
  const text = String(payload.text || '').trim();
  const rationale = String(payload.rationale || '').trim();
  const target = String(payload.target || '').trim();
  const params = payload.params && typeof payload.params === 'object'
    ? payload.params
    : null;

  if (text) lines.push(`<strong>${_escHtml(text)}</strong>`);
  if (rationale) lines.push(`<div class="obs-run-card-meta">Why now: ${_escHtml(rationale)}</div>`);
  if (target) lines.push(`<div class="obs-run-card-meta">Dispatch hint: ${_escHtml(target)}</div>`);
  if (params && Object.keys(params).length) {
    lines.push(`<div class="obs-run-card-meta">Context: <code>${_escHtml(JSON.stringify(params))}</code></div>`);
  }
  return lines.join('');
}

function buildDispatchTask(payload = {}) {
  const text = String(payload.text || '').trim();
  const rationale = String(payload.rationale || '').trim();
  const target = String(payload.target || '').trim();
  const params = payload.params && typeof payload.params === 'object'
    ? payload.params
    : null;

  const parts = [
    'Act on this OKR Next Up recommendation for Jon inside Artemis.',
    text ? `Primary task: ${text}` : 'Primary task: make progress on the selected OKR recommendation.',
  ];
  if (rationale) parts.push(`Why it surfaced now: ${rationale}`);
  if (target) parts.push(`Preferred dispatch hint: ${target}`);
  if (params && Object.keys(params).length) parts.push(`Structured context: ${JSON.stringify(params)}`);
  parts.push('Use the smallest reasonable agent workflow, produce a concrete first draft or research artifact, and stop when the next useful output is ready for review.');
  return parts.join('\n');
}

function handleAssistantDispatch(payload = {}) {
  openPanel();
  openSidebar();
  freeBotSessionId = getBotSessions()['__free__'] || freeBotSessionId;

  const ws = getState('ws');
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    appendObsHtml('Dispatch unavailable', '<div class="obs-empty">Artemis is offline right now, so this task could not be queued.</div>');
    return;
  }

  const cwd = getCurrentProject() || '/tmp';
  const selectedProjectName = $.projectSelect?.options?.[$.projectSelect?.selectedIndex]?.textContent || 'Assistant Bot';
  const provider = getSelectedProvider();
  const model = getSelectedModel();
  const reasoningEffort = getSelectedReasoningEffort();
  const speedTier = getSelectedSpeedTier();
  const task = buildDispatchTask(payload);

  appendObsHtml('Dispatch queued via Artemis', buildDispatchSummary(payload));

  const dispatchPayload = {
    type: 'orchestrate',
    task,
    cwd,
    sessionId: freeBotSessionId || getState('sessionId') || undefined,
    projectName: selectedProjectName,
    permissionMode: 'confirmDangerous',
  };
  if (provider) dispatchPayload.provider = provider;
  if (model) dispatchPayload.model = model;
  if (reasoningEffort) dispatchPayload.reasoningEffort = reasoningEffort;
  if (speedTier && speedTier !== 'standard') dispatchPayload.speedTier = speedTier;

  ws.send(JSON.stringify(dispatchPayload));
}

// ── Message rendering ───────────────────────────────────

function mergeAdjacentLists(html) {
  return html
    .replace(/<\/ol>(?:\s*<br>\s*)*<ol class="md-list md-ol">/g, '')
    .replace(/<\/ul>(?:\s*<br>\s*)*<ul class="md-list md-ul">/g, '');
}

function renderBotMarkdown(text) {
  return mergeAdjacentLists(renderMarkdown(text));
}

function showBotEmpty() {
  if (!messagesDiv) return;
  removeBotEmpty();
  const el = document.createElement('div');
  el.className = 'assistant-empty';
  el.innerHTML = `
    <div class="assistant-empty-mark">
      <img src="/icons/aIcon.png" alt="" width="32" height="32">
    </div>
    <div class="assistant-empty-text">Ask anything, delegate a task,<br>or pick a quick action below.</div>
  `;
  messagesDiv.appendChild(el);
}

function removeBotEmpty() {
  if (!messagesDiv) return;
  const el = messagesDiv.querySelector('.assistant-empty');
  if (el) el.remove();
}

function appendMessage(role, content) {
  removeBotEmpty();
  const div = document.createElement('div');
  div.className = `assistant-msg ${role}`;

  if (role === 'assistant') {
    div.innerHTML = `
      <div class="assistant-msg-head">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
        <span>Artemis</span>
      </div>
      <div class="assistant-msg-body"></div>
    `;
    const body = div.querySelector('.assistant-msg-body');
    body.innerHTML = renderBotMarkdown(content);
    highlightCodeBlocks(body);
    addCopyButtons(body);
  } else if (role === 'user') {
    div.textContent = content;
  } else if (role === 'error') {
    div.textContent = content;
  }

  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
  return div;
}

function appendToolIndicator(name, input) {
  const div = document.createElement('div');
  div.className = 'assistant-msg tool-indicator';
  const detail = input?.file_path || input?.command?.slice(0, 60) || input?.pattern || '';
  div.textContent = `${name}${detail ? ': ' + detail : ''}`;
  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function appendToolResult(content, isError) {
  const div = document.createElement('div');
  div.className = `assistant-msg tool-result-msg${isError ? ' error' : ''}`;
  div.textContent = content?.slice(0, 200) || (isError ? 'Error' : 'Done');
  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function showBotThinking(text) {
  removeBotThinking();
  const el = document.createElement('div');
  el.className = 'assistant-thinking';
  el.textContent = text;
  messagesDiv.appendChild(el);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function removeBotThinking() {
  const t = messagesDiv?.querySelector('.assistant-thinking');
  if (t) t.remove();
}

function finishStreaming() {
  isStreaming = false;
  currentAssistantEl = null;
  removeBotThinking();
  sendBtn.classList.remove('hidden-btn');
  stopBtn.classList.remove('visible');
  inputEl.focus();
}

// ── WS message handler ──────────────────────────────────

function handleBotWsMessage(msg) {
  // Workspace-scoped agent_activity events update the FAB badge for all clients
  if (msg.type === 'agent_activity') {
    // Refresh count from server to stay accurate (don't try to track delta locally)
    refreshActiveRunCount();
    // Refresh sidebar live section if open
    if (sidebarOpen) refreshSidebar();
    return;
  }

  // Track permission_request across ALL sessions for the blocked sidebar section
  if (msg.type === 'permission_request') {
    pendingApprovalMap.set(msg.id, {
      id: msg.id,
      toolName: msg.toolName,
      input: msg.input,
      chatId: msg.chatId,
      sessionId: msg.sessionId,
    });
    if (sidebarOpen) refreshSidebar();
    // Don't return — fall through for BOT_CHAT_ID handling below
  }

  if (msg.type === 'permission_response_external' && msg.id) {
    pendingApprovalMap.delete(msg.id);
    if (sidebarOpen) refreshSidebar();
  }

  // Clear pending approvals when a session finishes
  if ((msg.type === 'done' || msg.type === 'aborted' || msg.type === 'error') && msg.chatId) {
    for (const [id, approval] of pendingApprovalMap) {
      if (approval.chatId === msg.chatId) pendingApprovalMap.delete(id);
    }
    if (sidebarOpen) refreshSidebar();
  }

  if (msg.chatId !== BOT_CHAT_ID) return;

  removeBotThinking();

  switch (msg.type) {
    case 'session':
      freeBotSessionId = msg.sessionId;
      setBotSession('__free__', freeBotSessionId);
      showBotThinking('Thinking…');
      break;

    case 'text':
      if (!currentAssistantEl) {
        currentAssistantEl = appendMessage('assistant', msg.text);
      } else {
        const prev = currentAssistantEl.dataset.rawText || '';
        const full = prev + msg.text;
        currentAssistantEl.dataset.rawText = full;
        const body = currentAssistantEl.querySelector('.assistant-msg-body') || currentAssistantEl;
        body.innerHTML = renderBotMarkdown(full);
        highlightCodeBlocks(body);
        addCopyButtons(body);
      }
      if (!currentAssistantEl.dataset.rawText) {
        currentAssistantEl.dataset.rawText = msg.text;
      }
      messagesDiv.scrollTop = messagesDiv.scrollHeight;
      break;

    case 'tool':
      appendToolIndicator(msg.name, msg.input);
      showBotThinking(`Running ${msg.name}…`);
      break;

    case 'tool_result':
      appendToolResult(msg.content, msg.isError);
      showBotThinking('Thinking…');
      break;

    case 'result':
      removeBotThinking();
      break;

    case 'done':
      finishStreaming();
      break;

    case 'aborted':
      finishStreaming();
      break;

    case 'error':
      finishStreaming();
      appendMessage('error', msg.error || 'Unknown error');
      break;

    case 'permission_request':
      break;
  }
}

// ── Load history ────────────────────────────────────────

async function loadBotHistory() {
  const sid = getActiveBotSessionId();
  if (!sid) {
    messagesDiv.innerHTML = '';
    showBotEmpty();
    return;
  }

  try {
    const messages = await api.fetchMessagesByChatId(sid, BOT_CHAT_ID);
    messagesDiv.innerHTML = '';

    for (const msg of messages) {
      let data;
      try { data = JSON.parse(msg.content); } catch { continue; }

      if (msg.role === 'user') {
        appendMessage('user', data.text || '');
      } else if (msg.role === 'assistant') {
        appendMessage('assistant', data.text || '');
      } else if (msg.role === 'tool') {
        appendToolIndicator(data.name, data.input);
      } else if (msg.role === 'tool_result') {
        appendToolResult(data.content, data.isError);
      } else if (msg.role === 'error') {
        appendMessage('error', data.error || 'Error');
      }
    }

    if (messagesDiv.children.length === 0) showBotEmpty();
  } catch (err) {
    console.error('Failed to load bot history:', err);
  }
}

// ── New session ─────────────────────────────────────────

function newBotSession() {
  if (isStreaming) {
    stopBotGeneration();
    finishStreaming();
  }

  const sessions = getBotSessions();
  freeBotSessionId = null;
  delete sessions['__free__'];
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
  messagesDiv.innerHTML = '';
  currentAssistantEl = null;
  showBotEmpty();
}

// ── Visibility ──────────────────────────────────────────

function setBotVisible(visible) {
  const fab = document.getElementById('assistant-fab');
  if (fab) fab.style.display = visible ? '' : 'none';
  if (!visible && panel) closePanel();
}

// ── Init ────────────────────────────────────────────────

function init() {
  createBotDOM();
  on('ws:message', handleBotWsMessage);
  on('assistant:dispatch', handleAssistantDispatch);
  fetchSystemPrompt();
  startActiveRunPolling();

  storeOn('sessionId', updateContextChip);
  storeOn('view', updateContextChip); // updateContextChip calls renderContextChips internally
  if ($.projectSelect?.addEventListener) {
    $.projectSelect.addEventListener('change', updateContextChip);
  }

  // Render chips for initial view
  renderContextChips();

  // Default to visible in the new Artemis OS shell (approved design has FAB always shown)
  setBotVisible(getSetting('assistantBot', true));
  window.addEventListener('setting:assistantBot', (e) => setBotVisible(e.detail));

  // Pre-load Slack + Jira context so it's ready on first message
  loadLiveContext();
}

init();
