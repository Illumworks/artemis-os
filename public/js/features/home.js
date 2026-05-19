import { $ } from '../core/dom.js';
import { on as onState, setState, getState } from '../core/store.js';
import { emit } from '../core/events.js';
import { swr } from '../core/swr-cache.js';
import {
  DEFAULT_APP_VIEW,
  DASHBOARD_VIEW,
  LEGACY_DASHBOARD_VIEW,
  PRIMARY_NAV_DESTINATIONS,
  SECONDARY_NAV_DESTINATIONS,
  OPERATIONS_VIEW,
  MEMORY_VIEW,
  DEV_PROJECTS_VIEW,
  WRITING_STUDIO_VIEW,
  WORKSPACE_VIEW,
  CALENDAR_VIEW,
  MEETINGS_VIEW,
  JIRA_VIEW,
  OKR_VIEW,
  MARKETING_DASHBOARD_VIEW,
  MARKETING_CAMPAIGNS_VIEW,
  MARKETING_SIGNALS_VIEW,
  MARKETING_APPROVALS_VIEW,
  MARKETING_RULESETS_VIEW,
  MARKETING_SCOUT_RUNS_VIEW,
  isShellView,
  normalizeAppView,
} from '../core/navigation.js';
import { loadLegacyDashboard } from './dashboard-home.js';
import { openIntegrationsModal } from '../components/integrations-modal.js';
import {
  fetchAnalytics,
  fetchCalendarOverviewApi,
  fetchMeetingsOverviewApi,
  fetchJiraOverviewApi,
  fetchOkrOverviewApi,
  logOkrActivityApi,
  updateOkrKrApi,
  dismissOkrNextUpApi,
  suggestOkrKrProgressApi,
  extractOkrActivitiesApi,
  bulkLogOkrActivitiesApi,
  updateOkrActivityApi,
  generateEoyReviewApi,
  previewOkrUpdateApi,
  commitOkrUpdateApi,
  generateOkrNextUpApi,
  getOkrArchivedApi,
  startOkrDeckGenerationApi,
  pollOkrDeckStatusApi,
  fetchNotificationHistory,
  fetchProviderStatuses,
  fetchSessions,
  createMemoryApi,
  fetchGranolaMeetingsApi,
  searchGranolaMeetingsApi,
  fetchGranolaTranscriptApi,
  fetchJiraIssueApi,
  transitionJiraIssueApi,
  changeJiraAssigneeApi,
  createJiraIssueApi,
  fetchCalendarEventsApi,
  updateCalendarEventApi,
  fetchSlackSignalsApi,
  fetchSlackMentionsApi,
  resolveSlackMentionApi,
  fetchLatestBriefApi,
  generateBriefApi,
} from '../core/api.js';
import {
  handleMeetingsRowClick as _meetingsRowClick,
  renderMeetingsPastList as _renderMeetingsPastList,
  renderMeetingsGranolaTodayCanvas as _renderGranolaTodayCanvas,
  renderMeetingsPastCanvas as _renderMeetingsPastCanvas,
} from './meetings.js';
import { loadAgents } from './agents.js';
import { loadWorkflows } from './workflows.js';
import { renderOperationsView, loadSkillsShell, loadAutomationsShell, loadCampaignOpsShell } from './operations-shell.js';
import {
  loadMarketingDashboard,
  loadMarketingCampaigns,
  loadMarketingSignals,
  loadMarketingApprovals,
  loadMarketingRulesets,
  loadMarketingScoutRuns,
} from './marketing-os.js';
import {
  handleMemoryShellAction,
  handleMemoryShellInput,
  loadMemoryShell,
  renderMemoryShellLoading,
} from './memory-shell.js';
import {
  handleWritingStudioAction,
  handleWritingStudioInput,
  loadWritingStudio,
  renderWritingStudioLoading,
} from './writing-studio.js';

const chatAreaMain = document.querySelector('.chat-area-main') || document.querySelector('main.canvas.chat-area');
const appShellPage = document.getElementById('app-shell-page');
const appShellMount = document.getElementById('app-shell-page-mount');
const appShellContent = document.getElementById('app-shell-content');
const dashboardPage = document.getElementById('dashboard-page');
const primaryNav = document.getElementById('primary-nav');
const secondaryNav = document.getElementById('secondary-nav');
let commandCenterLoadToken = 0;
let modulesLoadToken = 0;
let calendarLoadToken = 0;
let meetingsLoadToken = 0;
let jiraLoadToken = 0;
let _jiraWireController = null;
let okrLoadToken = 0;
let lastDashboardJiraOverview = null;
let lastDashboardOkrOverview = null;
const SHELL_INTENT_STORAGE_KEY = 'artemis-shell-intent';
const TIME_REALITY_STORAGE_KEY = 'artemis-shell-time-reality';
const MODULE_FOCUS_STORAGE_KEY = 'artemis-shell-module-focus';
const OPERATIONS_FOCUS_STORAGE_KEY = 'artemis-shell-operations-focus';
const TASK_COMMAND_STATE_STORAGE_KEY = 'artemis-task-command-state';
const TASK_COMMAND_STATE_META_KEY = '__meta';
const TASK_COMMAND_FOCUS_CLASS = 'task-command-column-focused';
const TASK_COMMAND_SECTION_OPTIONS = ['Now', 'Today', 'Delegate', 'Later / Watch'];
const COMPOSER_CONTEXT_NOTE_STORAGE_KEY = 'artemis-composer-context-note';
const DEV_PROJECT_FILES_FOCUS_STORAGE_KEY = 'artemis-dev-project-files-focus';
const SHELL_NAV_SECTIONS_STORAGE_KEY = 'artemis-shell-nav-sections';
const CAL_VIEW_STORAGE_KEY = 'artemis-cal-view';
const CAL_FOCUS_DATE_STORAGE_KEY = 'artemis-cal-focus-date';
const ACTIVE_VIEW_STORAGE_KEY = 'artemis-active-view';
const DASHBOARD_CAPTURE_LOCAL_NOTES_STORAGE_KEY = 'artemis-dashboard-capture-local-notes';
const SHELL_NAV_SECTION_ORDER = ['Personal Workspace', 'Operations', 'Dev Projects'];
const HOME_SHELL_BOOTSTRAPPED_KEY = '__artemisHomeShellBootstrapped__';
const IS_VITEST = typeof process !== 'undefined' && Boolean(process.env?.VITEST);
const DASHBOARD_CAPTURE_DEFAULTS = {
  open: false,
  draft: '',
  source: 'build',
  outcome: 'progress',
  summary: '',
  error: '',
  savedMessage: '',
  proposals: [],
};
const dashboardCaptureState = { ...DASHBOARD_CAPTURE_DEFAULTS };

const WIDE_PAGE_VIEWS = new Set([
  DEFAULT_APP_VIEW, CALENDAR_VIEW, MEETINGS_VIEW, JIRA_VIEW, OKR_VIEW, WRITING_STUDIO_VIEW,
  OPERATIONS_VIEW, MEMORY_VIEW,
  MARKETING_DASHBOARD_VIEW, MARKETING_CAMPAIGNS_VIEW, MARKETING_SIGNALS_VIEW, MARKETING_APPROVALS_VIEW, MARKETING_RULESETS_VIEW, MARKETING_SCOUT_RUNS_VIEW,
  'agents', 'skills', 'workflows', 'automations',
]);
function isWidePageView(view) {
  return WIDE_PAGE_VIEWS.has(view);
}

renderNav();

onState('view', (view) => {
  const normalizedView = normalizeAppView(view);
  if (isShellView(normalizedView)) {
    try { localStorage.setItem(ACTIVE_VIEW_STORAGE_KEY, normalizedView); } catch {}
  }
  appShellPage?.classList.toggle('hidden', !isShellView(normalizedView));
  appShellMount?.classList.toggle('hidden', !isShellView(normalizedView));
  appShellPage?.classList.toggle('shell-page-wide', isWidePageView(normalizedView));
  dashboardPage?.classList.toggle('hidden', normalizedView !== LEGACY_DASHBOARD_VIEW);
  chatAreaMain?.classList.toggle('hidden', isShellView(normalizedView) || normalizedView === LEGACY_DASHBOARD_VIEW);
  setActiveNav(normalizedView);

  if (normalizedView === LEGACY_DASHBOARD_VIEW) {
    loadLegacyDashboard();
    return;
  }

  if (isShellView(normalizedView)) {
    renderShell(normalizedView);
    if (normalizedView === DEFAULT_APP_VIEW) {
      loadCommandCenter();
    } else if (normalizedView === WORKSPACE_VIEW) {
      loadModulesShell();
    } else if (normalizedView === CALENDAR_VIEW) {
      loadCalendarShell();
    } else if (normalizedView === MEETINGS_VIEW) {
      loadMeetingsShell();
    } else if (normalizedView === JIRA_VIEW) {
      loadJiraShell();
    } else if (normalizedView === OKR_VIEW) {
      loadOkrShell();
    } else if (normalizedView === OPERATIONS_VIEW) {
      loadOperationsShell();
    } else if (normalizedView === MEMORY_VIEW) {
      loadMemoryShell();
    } else if (normalizedView === WRITING_STUDIO_VIEW) {
      loadWritingStudio();
    } else if (normalizedView === 'workflows') {
      loadWorkflowsShell();
    } else if (normalizedView === 'agents') {
      loadAgentsShell();
    } else if (normalizedView === 'skills') {
      loadSkillsShell();
    } else if (normalizedView === 'automations') {
      loadAutomationsShell();
    } else if (normalizedView === MARKETING_DASHBOARD_VIEW) {
      loadMarketingDashboard(appShellContent);
    } else if (normalizedView === MARKETING_CAMPAIGNS_VIEW) {
      loadMarketingCampaigns(appShellContent);
    } else if (normalizedView === MARKETING_SIGNALS_VIEW) {
      loadMarketingSignals(appShellContent);
    } else if (normalizedView === MARKETING_APPROVALS_VIEW) {
      loadMarketingApprovals(appShellContent);
    } else if (normalizedView === MARKETING_RULESETS_VIEW) {
      loadMarketingRulesets(appShellContent);
    } else if (normalizedView === MARKETING_SCOUT_RUNS_VIEW) {
      loadMarketingScoutRuns(appShellContent);
    } else if (normalizedView === 'integrations') {
      // Integrations is now a modal — open it and stay on the current view
      openIntegrationsModal();
    }
  }
});

onState('sessionId', (id) => {
  if (id) setState('view', 'chat');
});

primaryNav?.addEventListener('click', handleNavClick);
secondaryNav?.addEventListener('click', handleNavClick);
appShellContent?.addEventListener('click', handleShellActionClick);
appShellContent?.addEventListener('change', handleTimeRealityChange);
appShellContent?.addEventListener('change', handleTaskCommandEditChange);
appShellContent?.addEventListener('input', (e) => handleMemoryShellInput(e.target));
appShellContent?.addEventListener('input', (e) => handleWritingStudioInput(e.target));
appShellContent?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.matches('[data-meetings-search-input]')) {
    e.preventDefault();
    handleMeetingsSearchSubmit();
  }
  if (e.target.matches('[data-writing-input="inline-folder-name"]')) {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.target.closest('[data-writing-library-card]')
        ?.querySelector('[data-writing-action="writing-create-inline-folder"]')
        ?.click();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.target.closest('[data-writing-library-card]')
        ?.querySelector('[data-writing-action="writing-cancel-inline-folder"]')
        ?.click();
    }
  }
});

// Calendar event drawer — created once, persists across calendar re-renders
let _calendarEventDrawer = null;
function _getOrCreateCalendarDrawer() {
  if (!_calendarEventDrawer) {
    _calendarEventDrawer = document.createElement('artemis-calendar-event-drawer');
    document.body.appendChild(_calendarEventDrawer);
    _calendarEventDrawer.addEventListener('change', () => {
      if (normalizeAppView(getState('view')) === CALENDAR_VIEW) {
        swr.invalidate(SWR_CAL_OVERVIEW_KEY);
        // invalidate all events keys (we don't know exact range, so clear by prefix)
        _swrInvalidateCalendarEvents();
        loadCalendarShell();
      }
    });
  }
  return _calendarEventDrawer;
}

// New-event modal — created once, persists across calendar re-renders
let _calendarNewEventModal = null;
function _getOrCreateCalendarNewEventModal() {
  if (!_calendarNewEventModal) {
    _calendarNewEventModal = document.createElement('artemis-calendar-new-event-modal');
    document.body.appendChild(_calendarNewEventModal);
    _calendarNewEventModal.addEventListener('created', (e) => {
      if (normalizeAppView(getState('view')) === CALENDAR_VIEW) {
        swr.invalidate(SWR_CAL_OVERVIEW_KEY);
        _swrInvalidateCalendarEvents();
        loadCalendarShell().then(() => {
          const eventId = e.detail?.event?.id;
          if (eventId) _getOrCreateCalendarDrawer().open(eventId);
        }).catch(() => {});
      }
    });
  }
  return _calendarNewEventModal;
}

const savedProject = localStorage.getItem('artemis-cwd');
if (!IS_VITEST && !globalThis[HOME_SHELL_BOOTSTRAPPED_KEY]) {
  globalThis[HOME_SHELL_BOOTSTRAPPED_KEY] = true;
  if (getState('sessionId') || savedProject) {
    setState('view', 'chat');
  } else {
    let persistedView = null;
    try { persistedView = localStorage.getItem(ACTIVE_VIEW_STORAGE_KEY); } catch {}
    const normalizedPersisted = persistedView ? normalizeAppView(persistedView) : null;
    const restored = normalizedPersisted && isShellView(normalizedPersisted)
      ? normalizedPersisted
      : null;
    const currentView = normalizeAppView(getState('view'));
    setState('view', restored || currentView || DEFAULT_APP_VIEW);
  }
}

function renderNav() {
  if (primaryNav) {
    const primaryItems = PRIMARY_NAV_DESTINATIONS.filter((item) => item.id === DASHBOARD_VIEW);
    primaryNav.innerHTML = primaryItems.map((item) => `
      <button
        type="button"
        class="shell-nav-btn shell-nav-btn-primary${normalizeAppView(item.id) === normalizeAppView(getState('view')) ? ' active' : ''}"
        data-app-view="${item.id}"
        title="${item.description}"
      >
        <span class="shell-nav-btn-label">${item.label}</span>
      </button>
    `).join('');
  }

  if (secondaryNav) {
    const workspaceItems = SECONDARY_NAV_DESTINATIONS.filter((item) => item.section === 'Personal Workspace');
    const operationsItems = SECONDARY_NAV_DESTINATIONS.filter((item) => item.section === 'Operations');
    const devProjectItems = SECONDARY_NAV_DESTINATIONS.filter((item) => item.section === 'Dev Projects');
    const sectionState = getShellNavSectionState();

    const renderSection = (title, items) => `
      <section class="shell-nav-section shell-nav-section-collapse ${sectionState[title] === false ? 'closed' : 'open'}" data-shell-nav-section="${title}">
        <button
          type="button"
          class="shell-nav-section-toggle"
          data-shell-section-toggle="${title}"
          aria-expanded="${sectionState[title] === false ? 'false' : 'true'}"
          aria-controls="shell-nav-section-body-${slugify(title)}"
        >
          <span>${title}</span>
          <svg class="shell-nav-section-chev" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M6 9l6 6 6-6"/>
          </svg>
        </button>
        <div class="shell-nav-section-body" id="shell-nav-section-body-${slugify(title)}" aria-hidden="${sectionState[title] === false ? 'true' : 'false'}">
          <div class="shell-nav-section-body-inner">
          ${items.map((item) => `
            <button
              type="button"
              class="shell-nav-btn shell-nav-btn-secondary${normalizeAppView(item.view || item.id) === normalizeAppView(getState('view')) ? ' active' : ''}"
              data-app-view="${item.view || item.id}"
              ${item.focus ? `data-shell-focus="${item.focus}"` : ''}
              ${item.action ? `data-shell-action="${item.action}"` : ''}
              title="${item.description}"
            >
              <span class="shell-nav-btn-label">${item.label}</span>
            </button>
          `).join('')}
          </div>
        </div>
      </section>
    `;

    secondaryNav.innerHTML = [
      workspaceItems.length ? renderSection('Personal Workspace', workspaceItems) : '',
      operationsItems.length ? renderSection('Operations', operationsItems) : '',
      devProjectItems.length ? renderSection('Dev Projects', devProjectItems) : '',
    ].join('');
  }
}

function handleNavClick(event) {
  const button = event.target.closest('[data-app-view], [data-shell-action], [data-shell-section-toggle]');
  if (!button) return;
  if (button.dataset.shellSectionToggle) {
    const section = button.dataset.shellSectionToggle;
    const shouldOpen = toggleShellNavSection(section);
    button.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    const body = document.getElementById(`shell-nav-section-body-${slugify(section)}`);
    body?.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
    return;
  }
  const action = button.dataset.shellAction || '';
  if (action === 'open-project-files') {
    localStorage.setItem(DEV_PROJECT_FILES_FOCUS_STORAGE_KEY, '1');
    setState('view', 'chat');
    emit('dev-project-files:focus');
    return;
  }

  const targetView = normalizeAppView(button.dataset.appView);
  if (button.dataset.shellFocus) {
    const focusTarget = button.dataset.shellFocus;
    if (targetView === WORKSPACE_VIEW) {
      localStorage.setItem(MODULE_FOCUS_STORAGE_KEY, focusTarget);
      setShellNavSectionState('Personal Workspace', true);
    } else if (targetView === OPERATIONS_VIEW) {
      localStorage.setItem(OPERATIONS_FOCUS_STORAGE_KEY, focusTarget);
      setShellNavSectionState('Operations', true);
    }
  } else if (targetView === WORKSPACE_VIEW) {
    localStorage.removeItem(MODULE_FOCUS_STORAGE_KEY);
    setShellNavSectionState('Personal Workspace', true);
  } else if (targetView === MEMORY_VIEW) {
    localStorage.removeItem(MODULE_FOCUS_STORAGE_KEY);
    localStorage.removeItem(OPERATIONS_FOCUS_STORAGE_KEY);
  }

  if (targetView === DEV_PROJECTS_VIEW || targetView === 'chat') {
    setShellNavSectionState('Dev Projects', true);
    setState('view', 'chat');
    return;
  }

  if (targetView === OPERATIONS_VIEW && !button.dataset.shellFocus) {
    localStorage.removeItem(OPERATIONS_FOCUS_STORAGE_KEY);
    setShellNavSectionState('Operations', true);
  }

  setState('sessionId', null);
  setState('view', targetView);
}

function setActiveNav(view) {
  document.querySelectorAll('[data-app-view]').forEach((button) => {
    button.classList.toggle('active', normalizeAppView(button.dataset.appView) === view);
  });
}

function slugify(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function getShellNavSectionState() {
  try {
    const stored = JSON.parse(localStorage.getItem(SHELL_NAV_SECTIONS_STORAGE_KEY) || '{}');
    return SHELL_NAV_SECTION_ORDER.reduce((acc, section) => {
      acc[section] = stored[section] !== false;
      return acc;
    }, {});
  } catch {
    return SHELL_NAV_SECTION_ORDER.reduce((acc, section) => {
      acc[section] = true;
      return acc;
    }, {});
  }
}

function setShellNavSectionState(section, open) {
  const state = getShellNavSectionState();
  state[section] = Boolean(open);
  localStorage.setItem(SHELL_NAV_SECTIONS_STORAGE_KEY, JSON.stringify(state));
  const sectionEl = document.querySelector(`[data-shell-nav-section="${section}"]`);
  if (!sectionEl) return;
  sectionEl.classList.toggle('open', Boolean(open));
  sectionEl.classList.toggle('closed', !open);
  const body = sectionEl.querySelector('.shell-nav-section-body');
  body?.setAttribute('aria-hidden', open ? 'false' : 'true');
  sectionEl.querySelector('.shell-nav-section-toggle')?.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function toggleShellNavSection(section) {
  const state = getShellNavSectionState();
  const next = !state[section];
  setShellNavSectionState(section, next);
  return next;
}

function renderShell(view) {
  if (!appShellContent) return;
  if (view === DEFAULT_APP_VIEW) {
    appShellContent.innerHTML = renderCommandCenterLoading();
    return;
  }
  if (view === WORKSPACE_VIEW) {
    appShellContent.innerHTML = renderModulesLoading();
    return;
  }
  if (view === CALENDAR_VIEW) {
    appShellContent.innerHTML = renderCalendarShellLoading();
    return;
  }
  if (view === MEETINGS_VIEW) {
    appShellContent.innerHTML = renderMeetingsShellLoading();
    return;
  }
  if (view === JIRA_VIEW) {
    appShellContent.innerHTML = renderJiraShellLoading();
    return;
  }
  if (view === OKR_VIEW) {
    appShellContent.innerHTML = renderOkrShellLoading();
    return;
  }
  if (view === MEMORY_VIEW) {
    appShellContent.innerHTML = renderMemoryShellLoading();
    return;
  }
  if (view === WRITING_STUDIO_VIEW) {
    appShellContent.innerHTML = renderWritingStudioLoading();
    return;
  }
  if (view === OPERATIONS_VIEW || view === 'agents' || view === 'skills' || view === 'workflows' || view === 'automations') {
    renderOperationsView(view);
    return;
  }
  if (
    view === MARKETING_DASHBOARD_VIEW ||
    view === MARKETING_CAMPAIGNS_VIEW ||
    view === MARKETING_SIGNALS_VIEW ||
    view === MARKETING_APPROVALS_VIEW
  ) {
    appShellContent.innerHTML = `
      <section class="mkt-hero" aria-busy="true">
        <div class="shell-eyebrow">Marketing Campaign OS</div>
        <h2>Loading…</h2>
      </section>`;
  }
}

function renderShellCard(title, body) {
  return `
    <article class="shell-card">
      <h3>${title}</h3>
      <p>${body}</p>
    </article>
  `;
}

function renderPlaceholderDestinationShell({
  eyebrow,
  title,
  heroCopy,
  stateTitle,
  stateCopy,
  readinessLabel,
  readinessTitle,
  readinessPoints = [],
  rails = [],
  chatIntent,
}) {
  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">${escapeHtml(eyebrow)}</div>
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(heroCopy)}</p>
    </section>
    <section class="command-center-grid shell-destination-grid">
      <article class="shell-card command-card">
        <div class="command-card-header">
          <div>
            <div class="shell-eyebrow">Current shell role</div>
            <h3>${escapeHtml(stateTitle)}</h3>
          </div>
          <span class="command-card-badge">${escapeHtml(readinessLabel)}</span>
        </div>
        <p>${escapeHtml(stateCopy)}</p>
        <div class="command-subsection">
          <div class="command-subsection-title">${escapeHtml(readinessTitle)}</div>
          <ul class="command-bullet-list">
            ${readinessPoints.map((point) => `<li>${escapeHtml(point)}</li>`).join('')}
          </ul>
        </div>
        <div class="shell-actions">
          <button type="button" class="shell-action-btn" data-shell-action="open-builder">Open Builder</button>
          <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(chatIntent)}">Ask Artemis</button>
        </div>
      </article>
      ${rails.map((rail) => renderPlaceholderDestinationRail(rail)).join('')}
    </section>
  `;
}

function renderPlaceholderDestinationRail({ eyebrow, title, badge, items = [], footnote }) {
  return `
    <article class="shell-card command-card">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">${escapeHtml(eyebrow)}</div>
          <h3>${escapeHtml(title)}</h3>
        </div>
        <span class="command-card-badge">${escapeHtml(badge)}</span>
      </div>
      <div class="module-grid">
        ${items.map((item) => `
          <article class="module-tile"${item.operationsFocus ? ` data-operations-focus-target="${escapeAttribute(item.operationsFocus)}"` : ''}>
            <div class="module-tile-topline">
              <h4>${escapeHtml(item.title)}</h4>
            </div>
            <p>${escapeHtml(item.body)}</p>
          </article>
        `).join('')}
      </div>
      <p class="command-card-footnote">${escapeHtml(footnote)}</p>
    </article>
  `;
}

function renderWorkflowsShell(viewModel) {
  return renderPlaceholderDestinationShell({
    eyebrow: 'Operations',
    title: 'Workflows',
    heroCopy: viewModel.heroCopy,
    stateTitle: 'Workflow Inventory',
    stateCopy: viewModel.stateCopy,
    readinessLabel: viewModel.readinessLabel,
    readinessTitle: 'Why this destination exists now',
    readinessPoints: viewModel.readinessPoints,
    rails: [
      ...(viewModel.bridgeRail ? [viewModel.bridgeRail] : []),
      {
        eyebrow: 'Current saved inventory',
        title: 'Saved Workflows',
        badge: viewModel.inventoryBadge,
        items: viewModel.workflowItems,
        footnote: viewModel.inventoryFootnote,
      },
      {
        eyebrow: 'Still true today',
        title: 'Current Limits',
        badge: 'Current',
        items: [
          { title: 'Builder Launch', body: 'The current builder still opens from the sidebar tooling and keeps its existing launch path.' },
          { title: 'Provider / Model Policy', body: 'Current launches still inherit provider/model choice from the main composer instead of storing durable per-workflow policy.' },
          { title: 'History / Logs', body: 'Visible traces are still lightweight snapshots, not the long-term workflow operations surface Artemis OS will need.' },
        ],
        footnote: 'This slice makes the shell destination truthful about the current saved inventory without pretending the workflow rebuild is done.',
      },
    ],
    chatIntent: 'Help me think through the next workflow I should create from the current Artemis shell context.',
  });
}

function renderAgentsShell(viewModel) {
  return renderPlaceholderDestinationShell({
    eyebrow: 'Operations',
    title: 'Agents',
    heroCopy: viewModel.heroCopy,
    stateTitle: 'Agent Inventory',
    stateCopy: viewModel.stateCopy,
    readinessLabel: viewModel.readinessLabel,
    readinessTitle: 'Why this destination exists now',
    readinessPoints: viewModel.readinessPoints,
    rails: [
      {
        eyebrow: 'Current saved inventory',
        title: 'Saved Agents',
        badge: viewModel.agentBadge,
        items: viewModel.agentItems,
        footnote: viewModel.agentFootnote,
      },
      {
        eyebrow: 'Current launch inventory',
        title: 'Saved Launchers',
        badge: viewModel.launcherBadge,
        items: viewModel.launcherItems,
        footnote: viewModel.launcherFootnote,
      },
    ],
    chatIntent: 'Help me think through the next agent profile Artemis should define from the current shell context.',
  });
}

async function handleShellActionClick(event) {
  // Past/Today meetings row click — delegated to meetings.js
  const meetingRow = event.target.closest('[data-meeting-id]');
  if (meetingRow) {
    _meetingsRowClick(meetingRow.dataset.meetingId, meetingRow.dataset.meetingTitle || '', appShellContent);
    return;
  }

  // Daily brief actions
  const briefBtn = event.target.closest('[data-action="generate-brief"], [data-action="refresh-brief"]');
  if (briefBtn) {
    briefBtn.disabled = true;
    briefBtn.textContent = 'Generating…';
    try {
      await generateBriefApi();
      loadCommandCenter();
    } catch {
      briefBtn.disabled = false;
      briefBtn.textContent = briefBtn.dataset.action === 'refresh-brief' ? 'Refresh' : 'Generate today\'s brief';
    }
    return;
  }

  const button = event.target.closest('[data-memory-action], [data-writing-action], [data-shell-action]');
  if (!button) return;
  if (button.dataset.memoryAction) {
    if (handleMemoryShellAction(button)) return;
  }
  if (button.dataset.writingAction) {
    if (handleWritingStudioAction(button)) return;
  }
  const action = button.dataset.shellAction;
  if (action === 'open-builder') {
    setState('view', 'chat');
    $.agentBtn?.click();
    return;
  }
  if (action === 'open-skills') {
    setState('sessionId', null);
    setState('view', 'skills');
    return;
  }
  if (action === 'open-project-files') {
    localStorage.setItem(DEV_PROJECT_FILES_FOCUS_STORAGE_KEY, '1');
    setState('view', 'chat');
    emit('dev-project-files:focus');
    return;
  }
  if (action === 'open-connectors') {
    // J3b-B removed the old artemis-connectors-modal; route to the new
    // integrations modal instead. Falls back to the MCP modal only if the
    // integrations module fails to load.
    import('../components/integrations-modal.js')
      .then((mod) => mod.openIntegrationsModal())
      .catch(() => {
        const mcp = document.querySelector('artemis-mcp-modal');
        if (mcp) mcp.querySelector('#mcp-modal')?.classList.remove('hidden');
      });
    return;
  }
  if (action === 'open-memory') {
    localStorage.removeItem(MODULE_FOCUS_STORAGE_KEY);
    localStorage.removeItem(OPERATIONS_FOCUS_STORAGE_KEY);
    setState('sessionId', null);
    setState('view', MEMORY_VIEW);
    return;
  }
  if (action === 'dashboard-capture-open') {
    dashboardCaptureState.open = true;
    dashboardCaptureState.error = '';
    dashboardCaptureState.savedMessage = '';
    if (normalizeAppView(getState('view')) === DEFAULT_APP_VIEW) {
      loadCommandCenter();
    }
    return;
  }
  if (action === 'dashboard-capture-close') {
    dashboardCaptureState.open = false;
    dashboardCaptureState.error = '';
    dashboardCaptureState.savedMessage = '';
    if (normalizeAppView(getState('view')) === DEFAULT_APP_VIEW) {
      loadCommandCenter();
    }
    return;
  }
  if (action === 'dashboard-capture-generate') {
    const formState = readDashboardCaptureForm();
    dashboardCaptureState.open = true;
    dashboardCaptureState.draft = formState.draft;
    dashboardCaptureState.source = formState.source;
    dashboardCaptureState.outcome = formState.outcome;
    dashboardCaptureState.savedMessage = '';

    if (!formState.draft) {
      dashboardCaptureState.error = 'Add a short capture first so Dashboard can propose the best next step.';
      dashboardCaptureState.proposals = [];
      dashboardCaptureState.summary = '';
      loadCommandCenter();
      return;
    }

    const jiraToday = buildDashboardJiraTodayModel(lastDashboardJiraOverview || null, readTimeReality());
    const okrThisWeek = buildDashboardOkrWeekModel(lastDashboardOkrOverview || null, readTimeReality());
    const proposals = buildDashboardCaptureProposals({
      ...formState,
      jiraToday,
      okrThisWeek,
    });

    dashboardCaptureState.proposals = proposals;
    dashboardCaptureState.summary = proposals[0]
      ? `This capture looks most like ${proposals[0].title.toLowerCase()}. You can still choose any of the other routes below.`
      : '';
    dashboardCaptureState.error = '';
    loadCommandCenter();
    return;
  }
  if (action === 'dashboard-capture-save-note') {
    const formState = readDashboardCaptureForm();
    const note = formState.draft;
    if (!note) {
      dashboardCaptureState.error = 'Add a short capture before saving a note.';
      dashboardCaptureState.savedMessage = '';
      loadCommandCenter();
      return;
    }

    dashboardCaptureState.open = true;
    dashboardCaptureState.draft = formState.draft;
    dashboardCaptureState.source = formState.source;
    dashboardCaptureState.outcome = formState.outcome;
    dashboardCaptureState.error = '';

    void (async () => {
      try {
        const projectPath = getActiveProjectPath();
        if (projectPath) {
          await createMemoryApi(projectPath, 'discovery', note);
          dashboardCaptureState.savedMessage = 'Saved to project memory so the note stays durable without forcing Jira or OKRs.';
        } else {
          saveDashboardCaptureLocalNote(note);
          dashboardCaptureState.savedMessage = 'Saved locally from Dashboard because no active project is selected.';
        }
        dashboardCaptureState.draft = '';
        dashboardCaptureState.summary = '';
        dashboardCaptureState.proposals = [];
      } catch (error) {
        console.error('Failed to save dashboard capture note:', error);
        dashboardCaptureState.savedMessage = '';
        dashboardCaptureState.error = 'Could not save the note right now. You can still route it through Jira or OKRs from the options above.';
      } finally {
        if (normalizeAppView(getState('view')) === DEFAULT_APP_VIEW) {
          loadCommandCenter();
        }
      }
    })();
    return;
  }
  if (action === 'open-chat-from-shell') {
    const intent = buildIntentWithTimeReality(button.dataset.shellIntent || '', readTimeReality());
    if (intent) {
      localStorage.setItem(SHELL_INTENT_STORAGE_KEY, intent);
      localStorage.setItem(COMPOSER_CONTEXT_NOTE_STORAGE_KEY, 'Dashboard handoff loaded into Chat.');
    }
    setState('view', 'chat');
    return;
  }
  if (action === 'open-session-from-shell') {
    const sessionId = button.dataset.shellSessionId;
    if (!sessionId) return;
    localStorage.setItem(COMPOSER_CONTEXT_NOTE_STORAGE_KEY, 'Resumed from Dashboard.');
    emit('session:switch', sessionId);
    setState('view', 'chat');
    return;
  }
  if (action === 'focus-task-command-from-shell') {
    applyTaskCommandFocus(button.dataset.shellTaskSection || '');
    return;
  }
  if (action === 'task-command-dismiss') {
    const itemId = button.dataset.taskCommandItemId || '';
    if (!itemId) return;
    writeTaskCommandItemState(itemId, { dismissed: true });
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-snooze') {
    const itemId = button.dataset.taskCommandItemId || '';
    if (!itemId) return;
    writeTaskCommandItemState(itemId, {
      snoozed: true,
      dismissed: false,
    });
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-undo-dismiss') {
    clearDismissedTaskCommandItemState();
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-undo-snooze') {
    clearSnoozedTaskCommandItemState();
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-restore-hidden') {
    clearHiddenTaskCommandItemState();
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-clear-focus') {
    clearEmphasizedTaskCommandItemState();
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-toggle-focused-only') {
    const currentPreferences = readTaskCommandPreferences();
    writeTaskCommandPreferences({
      focusedOnly: !currentPreferences.focusedOnly,
    });
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-clear-pins') {
    clearPinnedTaskCommandItemState();
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-clear-moves') {
    clearMovedTaskCommandItemState();
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-toggle-moved-only') {
    const currentPreferences = readTaskCommandPreferences();
    writeTaskCommandPreferences({
      movedOnly: !currentPreferences.movedOnly,
    });
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-restore-sections') {
    clearCollapsedTaskCommandSections();
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-pin') {
    const itemId = button.dataset.taskCommandItemId || '';
    if (!itemId) return;
    const currentState = readTaskCommandState()[itemId] || {};
    writeTaskCommandItemState(itemId, {
      pinned: !currentState.pinned,
      dismissed: false,
      snoozed: false,
    });
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-emphasize') {
    const itemId = button.dataset.taskCommandItemId || '';
    if (!itemId) return;
    const currentState = readTaskCommandState()[itemId] || {};
    writeTaskCommandItemState(itemId, {
      emphasized: !currentState.emphasized,
      dismissed: false,
      snoozed: false,
    });
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-toggle-pinned-only') {
    const currentPreferences = readTaskCommandPreferences();
    writeTaskCommandPreferences({
      pinnedOnly: !currentPreferences.pinnedOnly,
    });
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-toggle-section') {
    const sectionTitle = button.dataset.taskCommandSection || '';
    if (!TASK_COMMAND_SECTION_OPTIONS.includes(sectionTitle)) return;

    const currentPreferences = readTaskCommandPreferences();
    const collapsedSections = new Set(currentPreferences.collapsedSections);
    if (collapsedSections.has(sectionTitle)) {
      collapsedSections.delete(sectionTitle);
    } else {
      collapsedSections.add(sectionTitle);
    }

    writeTaskCommandPreferences({
      collapsedSections: [...collapsedSections],
    });
    loadCommandCenter();
    return;
  }
  if (action === 'task-command-reset-edits') {
    clearTaskCommandState();
    loadCommandCenter();
    return;
  }
  if (action === 'open-notification-history-from-shell') {
    const type = button.dataset.shellNotificationType || '';
    const status = button.dataset.shellNotificationStatus || '';
    emit('notification:show-history', { type, status });
    return;
  }
  if (action === 'open-shell-view') {
    const targetView = normalizeAppView(button.dataset.shellView);
    if (!isShellView(targetView)) return;

    const focusTarget = button.dataset.shellFocus || '';
    if (targetView === WORKSPACE_VIEW && focusTarget) {
      localStorage.setItem(MODULE_FOCUS_STORAGE_KEY, focusTarget);
    } else {
      localStorage.removeItem(MODULE_FOCUS_STORAGE_KEY);
    }
    if (targetView === OPERATIONS_VIEW && focusTarget) {
      localStorage.setItem(OPERATIONS_FOCUS_STORAGE_KEY, focusTarget);
    } else if (targetView !== OPERATIONS_VIEW) {
      localStorage.removeItem(OPERATIONS_FOCUS_STORAGE_KEY);
    }

    if (targetView !== 'chat') {
      setState('sessionId', null);
    }

    setState('view', targetView);

    if (targetView === WORKSPACE_VIEW && normalizeAppView(getState('view')) === WORKSPACE_VIEW) {
      queueMicrotask(() => applyStoredModuleFocus());
    }
    if (targetView === OPERATIONS_VIEW && normalizeAppView(getState('view')) === OPERATIONS_VIEW) {
      queueMicrotask(() => applyStoredOperationsFocus());
    }
  }
  if (action === 'slack-mention-resolve') {
    const mentionId = button.dataset.mentionId;
    if (!mentionId) return;
    // Optimistic removal: hide the row immediately, then POST to persist.
    const row = button.closest('[data-mention-id]');
    if (row) row.remove();
    // Update the header count if the counter element is present.
    const countEl = document.getElementById('slack-triage-count');
    if (countEl) {
      const current = parseInt(countEl.textContent.replace(/\D/g, ''), 10) || 0;
      const next = Math.max(0, current - 1);
      countEl.textContent = `(${next} unresolved)`;
    }
    // Check if the list is now empty and show the empty state.
    const listEl = document.getElementById('slack-triage-list');
    if (listEl && !listEl.querySelector('[data-mention-id]')) {
      listEl.innerHTML = '<p class="slack-triage-empty">Slack queue clear. Nicely done.</p>';
    }
    resolveSlackMentionApi(mentionId).catch((err) => {
      console.warn('slack-mention-resolve failed, will sync on next reload', err);
    });
    return;
  }
  if (action === 'meetings-tab-switch') {
    const tab = button.dataset.tab;
    handleMeetingsTabSwitch(tab);
    return;
  }
  if (action === 'meetings-search-submit') {
    handleMeetingsSearchSubmit();
    return;
  }
  // Row click for Past meeting list (no data-shell-action — use closest)
}

function handleTimeRealityChange(event) {
  const field = event.target.closest('[data-time-reality-field]');
  if (!field) return;

  const next = {
    ...readTimeReality(),
    [field.dataset.timeRealityField]: field.type === 'checkbox' ? !!field.checked : field.value,
  };

  writeTimeReality(next);
  if (normalizeAppView(getState('view')) === DEFAULT_APP_VIEW) {
    loadCommandCenter();
  }
}

function handleTaskCommandEditChange(event) {
  const moveSelect = event.target.closest('[data-task-command-edit="move-section"]');
  if (!moveSelect) return;

  const itemId = moveSelect.dataset.taskCommandItemId || '';
  const sectionTitle = moveSelect.value || '';
  if (!itemId || !sectionTitle) return;

  writeTaskCommandItemState(itemId, {
    sectionTitle,
    dismissed: false,
    snoozed: false,
  });

  if (normalizeAppView(getState('view')) === DEFAULT_APP_VIEW) {
    loadCommandCenter();
  }
}

async function loadCommandCenter() {
  if (!appShellContent) return;
  const loadToken = ++commandCenterLoadToken;

  try {
    const projectPath = getActiveProjectPath();
    const [analytics, notifications, sessions, calendarOverview, meetingsOverview, jiraOverview, okrOverview, slackSignals, briefResult, slackMentions] = await Promise.all([
      fetchAnalytics(projectPath || undefined).catch(() => ({})),
      fetchNotificationHistory({ limit: 12, unreadOnly: true }).catch(() => []),
      fetchSessions(projectPath || undefined).catch(() => []),
      fetchCalendarOverviewApi().catch(() => ({ status: 'error', today: {} })),
      fetchMeetingsOverviewApi().catch(() => ({ status: 'error', today: {} })),
      fetchJiraOverviewApi().catch(() => null),
      fetchOkrOverviewApi().catch(() => null),
      fetchSlackSignalsApi().catch(() => ({ connected: false, status: 'unavailable' })),
      fetchLatestBriefApi().catch(() => ({ brief: null, exists: false })),
      fetchSlackMentionsApi().catch(() => ({ mentions: [], total_unresolved: 0 })),
    ]);

    if (loadToken !== commandCenterLoadToken || normalizeAppView(getState('view')) !== DEFAULT_APP_VIEW) {
      return;
    }

    lastDashboardJiraOverview = jiraOverview || null;
    lastDashboardOkrOverview = okrOverview || null;

    const viewModel = buildCommandCenterViewModel({
      analytics,
      notifications,
      sessions,
      calendarOverview,
      meetingsOverview,
      jiraOverview,
      okrOverview,
      slackSignals,
      slackMentions,
      brief: briefResult?.brief ?? null,
    });
    appShellContent.innerHTML = renderCommandCenter(viewModel);
  } catch (error) {
    if (loadToken !== commandCenterLoadToken || normalizeAppView(getState('view')) !== DEFAULT_APP_VIEW) {
      return;
    }
    lastDashboardJiraOverview = null;
    lastDashboardOkrOverview = null;
    appShellContent.innerHTML = renderCommandCenterError();
    console.error('Failed to load Dashboard shell:', error);
  }
}

async function loadModulesShell() {
  if (!appShellContent) return;
  const loadToken = ++modulesLoadToken;

  try {
    const projectPath = getActiveProjectPath();
    const [analytics, providerStatuses, notifications] = await Promise.all([
      fetchAnalytics(projectPath || undefined).catch(() => ({})),
      fetchProviderStatuses().catch(() => []),
      fetchNotificationHistory({ limit: 12, unreadOnly: true }).catch(() => []),
    ]);

    if (loadToken !== modulesLoadToken || normalizeAppView(getState('view')) !== WORKSPACE_VIEW) {
      return;
    }

    const viewModel = buildModulesViewModel({ analytics, providerStatuses, notifications });
    appShellContent.innerHTML = renderModulesShell(viewModel);
    applyStoredModuleFocus();
  } catch (error) {
    if (loadToken !== modulesLoadToken || normalizeAppView(getState('view')) !== WORKSPACE_VIEW) {
      return;
    }
    appShellContent.innerHTML = renderModulesError();
    console.error('Failed to load modules shell:', error);
  }
}

// SWR cache keys for calendar data
const SWR_CAL_OVERVIEW_KEY = 'calendar:overview';
function _swrCalEventsKey(rangeStart, rangeEnd) {
  return `calendar:events:${rangeStart.toISOString()}:${rangeEnd.toISOString()}`;
}
// Drop all range-scoped calendar event entries (used after any mutation)
function _swrInvalidateCalendarEvents() {
  swr.invalidatePrefix('calendar:events:');
}

// Attach a transient "Updated just now" pill to the calendar toolbar.
// Fades out after 3 s so it's informative but never intrusive.
function _calShowUpdatedPill() {
  const toolbar = appShellContent?.querySelector('.cal-toolbar, .cal-header, [data-cal-nav]');
  if (!toolbar) return;
  const existing = appShellContent.querySelector('.swr-updated-pill');
  if (existing) existing.remove();
  const pill = document.createElement('span');
  pill.className = 'swr-updated-pill';
  pill.textContent = 'Updated just now';
  pill.style.cssText = [
    'display:inline-block',
    'margin-left:10px',
    'font-size:11px',
    'opacity:0.6',
    'transition:opacity 1s ease',
    'pointer-events:none',
  ].join(';');
  toolbar.closest('[data-cal-nav]')
    ? toolbar.after(pill)
    : toolbar.append(pill);
  requestAnimationFrame(() => {
    setTimeout(() => { pill.style.opacity = '0'; }, 2800);
    setTimeout(() => { pill.remove(); }, 3800);
  });
}

async function loadCalendarShell() {
  if (!appShellContent) return;
  const loadToken = ++calendarLoadToken;

  // ── Phase 1: try to render from SWR cache immediately ───────────────────
  const overviewResult = await swr(
    SWR_CAL_OVERVIEW_KEY,
    () => fetchCalendarOverviewApi().catch(() => null),
  );
  const calendarOverview = overviewResult.data;
  const overviewFromCache = overviewResult.source === 'cache';

  if (loadToken !== calendarLoadToken || normalizeAppView(getState('view')) !== CALENDAR_VIEW) return;

  // Not connected or error → fall back to setup/error view (existing flow)
  if (!calendarOverview || calendarOverview.status !== 'ready') {
    if (!overviewFromCache) {
      // Only show loading skeleton if we have no cached data at all
      appShellContent.innerHTML = renderCalendarShellLoading();
    }
    const notifications = await fetchNotificationHistory({ limit: 12, unreadOnly: true }).catch(() => []);
    if (loadToken !== calendarLoadToken || normalizeAppView(getState('view')) !== CALENDAR_VIEW) return;
    const timeReality = readTimeReality();
    const viewModel = buildCalendarModuleViewModel({ notifications, timeReality, calendarOverview });
    appShellContent.innerHTML = renderCalendarShell(viewModel);
    return;
  }

  // Google connected — read view state from localStorage
  const view = _calValidateView(localStorage.getItem(CAL_VIEW_STORAGE_KEY));
  const focusDate = _calParseFocusDate(localStorage.getItem(CAL_FOCUS_DATE_STORAGE_KEY));
  const { rangeStart, rangeEnd } = _calComputeRange(focusDate, view);
  const eventsKey = _swrCalEventsKey(rangeStart, rangeEnd);

  // Show loading skeleton only if we have nothing cached
  const cachedEvents = swr.peek(eventsKey);
  if (!cachedEvents) {
    appShellContent.innerHTML = renderCalendarShellLoading();
  }

  let events = [];
  let fetchError = null;
  try {
    const eventsResult = await swr(
      eventsKey,
      () => fetchCalendarEventsApi(rangeStart, rangeEnd),
    );
    events = eventsResult.data ?? [];
  } catch (err) {
    fetchError = err;
    console.error('Calendar events fetch failed:', err);
  }

  if (loadToken !== calendarLoadToken || normalizeAppView(getState('view')) !== CALENDAR_VIEW) return;

  appShellContent.innerHTML = renderCalendarInteractivePage(events, focusDate, view, fetchError);
  _wireCalendarPage(events, focusDate, view);

  // ── Phase 2: subscribe to background SWR refresh for this render ─────────
  // When a background revalidation completes, swap the events in place.
  function _onSwrFresh(e) {
    if (e.detail.key !== eventsKey) return;
    if (loadToken !== calendarLoadToken || normalizeAppView(getState('view')) !== CALENDAR_VIEW) {
      document.removeEventListener('swr:fresh', _onSwrFresh);
      return;
    }
    const freshEvents = e.detail.data ?? [];
    appShellContent.innerHTML = renderCalendarInteractivePage(freshEvents, focusDate, view, null);
    _wireCalendarPage(freshEvents, focusDate, view);
    _calShowUpdatedPill();
    document.removeEventListener('swr:fresh', _onSwrFresh);
  }
  document.addEventListener('swr:fresh', _onSwrFresh);
}

async function loadMeetingsShell() {
  if (!appShellContent) return;
  const loadToken = ++meetingsLoadToken;

  try {
    const [meetingsOverview, granolaOverview] = await Promise.all([
      fetchMeetingsOverviewApi().catch(() => null),
      fetchGranolaMeetingsApi().catch(() => null),
    ]);

    if (loadToken !== meetingsLoadToken || normalizeAppView(getState('view')) !== MEETINGS_VIEW) {
      return;
    }

    const timeReality = readTimeReality();
    const granolaConnected = granolaOverview?.connected === true;
    const viewModel = buildMeetingsModuleViewModel({ timeReality, meetingsOverview, granolaConnected, granolaOverview });
    appShellContent.innerHTML = renderMeetingsShell(viewModel);

    if (granolaConnected) {
      _renderMeetingsPastList(granolaOverview.meetings || [], appShellContent);
    }
  } catch (error) {
    if (loadToken !== meetingsLoadToken || normalizeAppView(getState('view')) !== MEETINGS_VIEW) {
      return;
    }
    appShellContent.innerHTML = renderMeetingsShellError();
    console.error('Failed to load meetings shell:', error);
  }
}

async function loadJiraShell() {
  if (!appShellContent) return;
  const loadToken = ++jiraLoadToken;

  try {
    const [jiraOverview] = await Promise.all([
      fetchJiraOverviewApi().catch(() => null),
    ]);

    if (loadToken !== jiraLoadToken || normalizeAppView(getState('view')) !== JIRA_VIEW) {
      return;
    }

    const viewModel = buildJiraDedicatedViewModel({ jiraOverview });
    appShellContent.innerHTML = renderJiraShell(viewModel);
    if (viewModel.statusTone === 'live') _wireJiraBoard(appShellContent);
  } catch (error) {
    if (loadToken !== jiraLoadToken || normalizeAppView(getState('view')) !== JIRA_VIEW) {
      return;
    }
    appShellContent.innerHTML = renderJiraShellError();
    console.error('Failed to load Jira shell:', error);
  }
}

// SWR cache key for OKR overview
const SWR_OKR_OVERVIEW_KEY = 'okr:overview';

async function loadOkrShell() {
  if (!appShellContent) return;
  const loadToken = ++okrLoadToken;

  // Show loading skeleton only if nothing is cached
  const hasCachedOkr = swr.peek(SWR_OKR_OVERVIEW_KEY) !== null;
  if (!hasCachedOkr) {
    appShellContent.innerHTML = renderOkrShellLoading();
  }

  try {
    if (loadToken !== okrLoadToken || normalizeAppView(getState('view')) !== OKR_VIEW) return;

    // ── Phase 1: render from SWR cache immediately ──────────────────────────
    const overviewResult = await swr(
      SWR_OKR_OVERVIEW_KEY,
      () => fetchOkrOverviewApi().catch(() => null),
    );
    const overview = overviewResult.data;

    if (loadToken !== okrLoadToken || normalizeAppView(getState('view')) !== OKR_VIEW) return;

    if (!overview) {
      appShellContent.innerHTML = renderOkrShellError();
      return;
    }
    appShellContent.innerHTML = renderOkrShell(overview);
    _wireOkrInteractions(appShellContent);

    // ── Phase 2: subscribe to background SWR refresh ────────────────────────
    function _onSwrFreshOkr(e) {
      if (e.detail.key !== SWR_OKR_OVERVIEW_KEY) return;
      if (loadToken !== okrLoadToken || normalizeAppView(getState('view')) !== OKR_VIEW) {
        document.removeEventListener('swr:fresh', _onSwrFreshOkr);
        return;
      }
      const freshOverview = e.detail.data;
      if (!freshOverview) return;
      appShellContent.innerHTML = renderOkrShell(freshOverview);
      _wireOkrInteractions(appShellContent);
      // Surface a subtle "updated" signal via page title tag if one exists
      const titleEl = appShellContent.querySelector('.okr-section-title, h2');
      if (titleEl) {
        const pill = document.createElement('span');
        pill.className = 'swr-updated-pill';
        pill.textContent = 'Updated';
        pill.style.cssText = [
          'display:inline-block',
          'margin-left:8px',
          'font-size:11px',
          'opacity:0.55',
          'transition:opacity 1s ease',
          'pointer-events:none',
        ].join(';');
        titleEl.append(pill);
        requestAnimationFrame(() => {
          setTimeout(() => { pill.style.opacity = '0'; }, 2500);
          setTimeout(() => { pill.remove(); }, 3500);
        });
      }
      document.removeEventListener('swr:fresh', _onSwrFreshOkr);
    }
    document.addEventListener('swr:fresh', _onSwrFreshOkr);

  } catch (error) {
    if (loadToken !== okrLoadToken || normalizeAppView(getState('view')) !== OKR_VIEW) return;
    appShellContent.innerHTML = renderOkrShellError();
    console.error('Failed to load OKR shell:', error);
  }
}

const _krDotClass    = { done: 'done', ontrack: '', atrisk: 'warn', notstarted: 'zero' };
const _krStatusLabel = { done: 'Done', ontrack: 'On track', atrisk: 'At risk', notstarted: 'Not started' };

function _applyKrDomUpdate(krEl, status, prog, note) {
  const dot    = krEl.querySelector('.okr-kr-dot');
  const pill   = krEl.querySelector('.okr-kr-status-pill');
  const progEl = krEl.querySelector('.okr-kr-prog');
  const bar    = krEl.querySelector('.okr-kr-prog-fill');
  const gapsCol = krEl.querySelector('.okr-kr-panel.gaps');
  const dc = _krDotClass[status] ?? '';
  const sl = _krStatusLabel[status] ?? status;
  if (dot)    dot.className  = `okr-kr-dot${dc ? ' ' + dc : ''}`;
  if (pill)   { pill.className = `okr-kr-status-pill ${status}`; pill.textContent = sl; }
  if (progEl) progEl.textContent = `${prog}%`;
  if (bar)    { bar.style.width = `${prog}%`; bar.className = `okr-kr-prog-fill${dc ? ' ' + dc : ''}`; }
  // G-P4: suppress gaps when done
  if (gapsCol) gapsCol.style.display = (status === 'done') ? 'none' : '';
  // G-P2: update note display
  if (note !== undefined) {
    let noteDisplay = krEl.querySelector('.okr-kr-note-display');
    const body = krEl.querySelector('.okr-kr-body');
    if (note.trim()) {
      if (!noteDisplay) {
        noteDisplay = document.createElement('div');
        noteDisplay.className = 'okr-kr-note-display';
        body?.insertBefore(noteDisplay, body.querySelector('.okr-kr-edit'));
      }
      noteDisplay.textContent = note;
    } else if (noteDisplay) {
      noteDisplay.remove();
    }
  }
}

function _applyKrTargetDomUpdate(krEl, targetText) {
  const targetPanel = krEl.querySelector('.okr-kr-panel-target');
  const targetTextEl = targetPanel?.querySelector('[data-okr-target-text]');
  if (targetTextEl) targetTextEl.textContent = targetText.trim() || 'No KR target defined.';
}

function _wireOkrInteractions(container) {
  // KR navigator selection
  container.addEventListener('click', (e) => {
    const actionToggle = e.target.closest('[data-okr-actions-toggle]');
    if (actionToggle) {
      const menu = actionToggle.closest('.okr-actions-menu');
      const popover = menu?.querySelector('[data-okr-actions-popover]');
      const opening = Boolean(popover?.hidden);
      container.querySelectorAll('[data-okr-actions-popover]').forEach((panel) => { panel.hidden = true; });
      container.querySelectorAll('[data-okr-actions-toggle]').forEach((btn) => btn.setAttribute('aria-expanded', 'false'));
      if (popover && opening) {
        popover.hidden = false;
        actionToggle.setAttribute('aria-expanded', 'true');
      }
      return;
    }

    if (!e.target.closest('.okr-actions-menu')) {
      container.querySelectorAll('[data-okr-actions-popover]').forEach((panel) => { panel.hidden = true; });
      container.querySelectorAll('[data-okr-actions-toggle]').forEach((btn) => btn.setAttribute('aria-expanded', 'false'));
    }

    const objectiveTab = e.target.closest('[data-okr-objective-tab]');
    if (objectiveTab) {
      const objectiveId = objectiveTab.dataset.okrObjectiveTab;
      const firstKr = objectiveTab.dataset.firstKr;
      container.querySelectorAll('[data-okr-objective-tab]').forEach((tab) => {
        tab.classList.toggle('active', tab.dataset.okrObjectiveTab === objectiveId);
      });
      container.querySelectorAll('[data-okr-objective-panel]').forEach((panel) => {
        panel.classList.toggle('active', panel.dataset.okrObjectivePanel === objectiveId);
      });
      if (firstKr) {
        container.querySelectorAll('[data-okr-select-kr]').forEach((row) => {
          row.classList.toggle('selected', row.dataset.okrSelectKr === firstKr);
        });
        container.querySelectorAll('[data-okr-detail-panel]').forEach((panel) => {
          panel.classList.toggle('active', panel.dataset.okrDetailPanel === firstKr);
        });
      }
      return;
    }

    const selectKr = e.target.closest('[data-okr-select-kr]');
    if (selectKr) {
      const id = selectKr.dataset.okrSelectKr;
      container.querySelectorAll('[data-okr-select-kr]').forEach((row) => row.classList.toggle('selected', row.dataset.okrSelectKr === id));
      container.querySelectorAll('[data-okr-detail-panel]').forEach((panel) => panel.classList.toggle('active', panel.dataset.okrDetailPanel === id));
      return;
    }

    // KR save
    const saveBtn = e.target.closest('[data-okr-save-kr]');
    if (saveBtn) {
      const id = Number(saveBtn.dataset.okrSaveKr);
      const krEl = saveBtn.closest('.okr-detail-panel');
      const statusSel  = krEl?.querySelector('[data-okr-status-select]');
      const progInput  = krEl?.querySelector('[data-okr-prog-input]');
      const noteInput  = krEl?.querySelector('[data-okr-note-input]');
      const targetInput = krEl?.querySelector('[data-okr-target-input]');
      if (!krEl || !statusSel || !progInput) return;
      const status = statusSel.value;
      const prog   = Math.max(0, Math.min(100, Number(progInput.value) || 0));
      const note   = noteInput?.value ?? '';
      const targetText = targetInput?.value ?? '';
      saveBtn.textContent = 'Saving…';
      saveBtn.disabled = true;
      updateOkrKrApi(id, { prog, status, note, target_text: targetText }).then(() => {
        swr.invalidate(SWR_OKR_OVERVIEW_KEY);
        _applyKrDomUpdate(krEl, status, prog, note);
        _applyKrTargetDomUpdate(krEl, targetText);
        saveBtn.textContent = 'Saved ✓';
        setTimeout(() => { saveBtn.textContent = 'Save'; saveBtn.disabled = false; }, 1500);
      }).catch(() => {
        saveBtn.textContent = 'Save';
        saveBtn.disabled = false;
      });
      return;
    }

    const targetEdit = e.target.closest('[data-okr-edit-target]');
    if (targetEdit) {
      const panel = targetEdit.closest('.okr-kr-panel-target');
      const display = panel?.querySelector('[data-okr-target-display]');
      const editor = panel?.querySelector('[data-okr-target-editor]');
      const input = panel?.querySelector('[data-okr-target-input]');
      if (!panel || !display || !editor || !input) return;
      display.hidden = true;
      editor.hidden = false;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      return;
    }

    const targetCancel = e.target.closest('[data-okr-cancel-target]');
    if (targetCancel) {
      const panel = targetCancel.closest('.okr-kr-panel-target');
      const display = panel?.querySelector('[data-okr-target-display]');
      const editor = panel?.querySelector('[data-okr-target-editor]');
      const input = panel?.querySelector('[data-okr-target-input]');
      const text = panel?.querySelector('[data-okr-target-text]')?.textContent || '';
      if (!panel || !display || !editor || !input) return;
      input.value = text === 'No KR target defined.' ? '' : text;
      editor.hidden = true;
      display.hidden = false;
      return;
    }

    // KR suggest progress
    const suggestBtn = e.target.closest('[data-okr-suggest-prog]');
    if (suggestBtn) {
      const id = Number(suggestBtn.dataset.okrSuggestProg);
      const krEl = suggestBtn.closest('.okr-detail-panel');
      const progInput = krEl?.querySelector('[data-okr-prog-input]');
      if (!progInput) return;
      suggestBtn.textContent = '…';
      suggestBtn.disabled = true;
      suggestOkrKrProgressApi(id).then((suggestion) => {
        const { suggested_prog, rationale, confidence, evidence_ids } = suggestion || {};
        progInput.value = suggested_prog;
        let hint = krEl.querySelector('.okr-kr-suggest-hint');
        if (!hint) {
          hint = document.createElement('div');
          hint.className = 'okr-kr-suggest-hint';
          suggestBtn.closest('.okr-kr-edit').appendChild(hint);
        }
        const confidenceText = confidence !== null && confidence !== undefined
          ? `Confidence: ${Math.round(Number(confidence) * 100)}%. `
          : '';
        const evidenceText = Array.isArray(evidence_ids) && evidence_ids.length
          ? `Evidence: ${evidence_ids.join(', ')}. `
          : '';
        hint.textContent = `${confidenceText}${evidenceText}${rationale || ''}`.trim();
        suggestBtn.textContent = 'Suggest';
        suggestBtn.disabled = false;
      }).catch(() => {
        suggestBtn.textContent = 'Suggest';
        suggestBtn.disabled = false;
      });
      return;
    }

    // Next-up dismiss
    const dismissBtn = e.target.closest('[data-okr-dismiss-next-up]');
    if (dismissBtn) {
      const id = dismissBtn.dataset.okrDismissNextUp;
      const item = dismissBtn.closest('.okr-next-item');
      if (item) item.remove();
      dismissOkrNextUpApi(id).catch(() => {});
      return;
    }

    const dispatchBtn = e.target.closest('[data-okr-dispatch-next-up]');
    if (dispatchBtn) {
      const encodedParams = dispatchBtn.dataset.okrDispatchParams || '';
      let dispatchParams = null;
      try {
        dispatchParams = JSON.parse(decodeURIComponent(encodedParams));
      } catch {
        dispatchParams = null;
      }
      emit('assistant:dispatch', {
        source: 'okr-next-up',
        id: dispatchBtn.dataset.okrDispatchNextUp || '',
        text: dispatchBtn.dataset.okrDispatchText || '',
        target: dispatchBtn.dataset.okrDispatchTarget || '',
        params: dispatchParams,
        rationale: dispatchBtn.dataset.okrDispatchRationale || '',
      });
      dispatchBtn.disabled = true;
      dispatchBtn.textContent = 'Queued';
      dispatchBtn.title = 'Queued in Artemis';
      return;
    }
  });

  container.addEventListener('change', (e) => {
    const evidenceSelect = e.target.closest('[data-okr-evidence-select]');
    if (!evidenceSelect) return;
    const id = Number(evidenceSelect.dataset.okrEvidenceSelect);
    const entry = evidenceSelect.closest('.okr-evidence-entry');
    evidenceSelect.disabled = true;
    updateOkrActivityApi(id, { krId: evidenceSelect.value || null }).then((updated) => {
      const meta = entry?.querySelector('.okr-evidence-meta');
      if (meta) {
        meta.innerHTML = `<span>${escapeHtml(updated.when || '')}</span><span>${escapeHtml(updated.kr_label || 'Unmapped')}</span>`;
      }
    }).finally(() => {
      evidenceSelect.disabled = false;
    });
  });

  // Activity submit
  const activityInput = container.querySelector('[data-okr-activity-input]');
  const activityBtn = container.querySelector('[data-okr-activity-submit]');
  const activityFeed = container.querySelector('[data-okr-activity-feed]');

  function submitActivity() {
    if (!activityInput || !activityInput.value.trim()) return;
    const text = activityInput.value.trim();
    activityInput.value = '';
    logOkrActivityApi(text).then((entry) => {
      swr.invalidate(SWR_OKR_OVERVIEW_KEY);
      if (!activityFeed) return;
      const el = document.createElement('div');
      el.className = 'okr-activity-feed-entry';
      el.innerHTML = `
        <div>${escapeHtml(entry.text)}</div>
        <div class="okr-activity-feed-meta">
          Just now · <span class="okr-activity-feed-kr">${escapeHtml(entry.kr_label || 'Unmapped')}</span>
        </div>`;
      activityFeed.insertBefore(el, activityFeed.firstChild);
    }).catch(() => {});
  }

  if (activityBtn) activityBtn.addEventListener('click', submitActivity);
  if (activityInput) {
    activityInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submitActivity();
    });
  }

  // Paste-capture button
  const pasteBtn = container.querySelector('[data-okr-paste-capture]');
  if (pasteBtn) {
    pasteBtn.addEventListener('click', () => _openPasteCaptureModal(activityFeed, _collectOkrKrs(container)));
  }

  // EOY review button
  const eoyBtn = container.querySelector('[data-okr-eoy-review]');
  if (eoyBtn) {
    eoyBtn.addEventListener('click', () => _openEoyReviewModal());
  }

  // Show archived toggle
  const archivedToggle = container.querySelector('[data-okr-show-archived]');
  const archivedSection = container.querySelector('.okr-archived-section');
  if (archivedToggle && archivedSection) {
    let archivedCache = null;
    archivedToggle.addEventListener('click', async () => {
      const isShowing = archivedToggle.dataset.okrShowArchived === 'true';
      if (isShowing) {
        archivedSection.hidden = true;
        archivedToggle.dataset.okrShowArchived = 'false';
        archivedToggle.textContent = 'Show archived';
        return;
      }
      archivedToggle.textContent = 'Loading…';
      archivedToggle.disabled = true;
      try {
        if (!archivedCache) {
          const { groups } = await getOkrArchivedApi();
          archivedCache = groups;
        }
        archivedSection.innerHTML = renderArchivedObjectives(archivedCache);
        archivedSection.hidden = false;
        archivedToggle.dataset.okrShowArchived = 'true';
        archivedToggle.textContent = 'Hide archived';
      } catch {
        archivedToggle.textContent = 'Show archived';
      } finally {
        archivedToggle.disabled = false;
      }
    });
  }

  // Update OKRs button
  const updateBtn = container.querySelector('[data-okr-update]');
  if (updateBtn) {
    updateBtn.addEventListener('click', () => _openOkrUpdateModal());
  }

  // Generate deck button
  const deckBtn = container.querySelector('[data-okr-generate-deck]');
  if (deckBtn) {
    deckBtn.addEventListener('click', () => _startOkrDeckGeneration(deckBtn));
  }

  // Next Up regenerate button
  const regenBtn = container.querySelector('[data-okr-regen-next-up]');
  if (regenBtn) {
    regenBtn.addEventListener('click', async () => {
      regenBtn.disabled = true;
      regenBtn.textContent = '…';
      try {
        const { nextUp } = await generateOkrNextUpApi();
        const list = container.querySelector('.okr-next-list');
        if (list) {
          const items = renderOkrNextUpItems(nextUp);
          list.innerHTML = items || '<div class="okr-next-empty">Nothing queued.</div>';
        }
        const countEl = container.querySelector('.okr-next-count');
        if (countEl) countEl.textContent = (nextUp || []).length;
      } catch {
        // Silent — don't interrupt the user
      } finally {
        regenBtn.disabled = false;
        regenBtn.textContent = '↺';
      }
    });
  }
}

function _collectOkrKrs(container) {
  return [...container.querySelectorAll('.okr-kr[data-kr-id]')].map((el) => ({
    id: Number(el.dataset.krId),
    title: el.dataset.krTitle || el.querySelector('.okr-kr-title')?.textContent?.trim() || `KR ${el.dataset.krId}`,
  })).filter((kr) => Number.isFinite(kr.id));
}

async function loadWorkflowsShell() {
  await loadWorkflows();
}

async function loadAgentsShell() {
  await loadAgents();
}

function loadOperationsShell() {
  if (!appShellContent) return;
  applyStoredOperationsFocus();
}

function getActiveProjectPath() {
  return $.projectSelect?.value || localStorage.getItem('artemis-cwd') || '';
}

function renderCommandCenterLoading() {
  return `
    <section class="dashboard-main-grid" aria-busy="true">
      <div class="dashboard-primary-column">
        <article class="shell-card shell-card-loading">
          <h3>Today</h3>
          <p>Loading today’s meetings, best work block, and what seems most worth touching now...</p>
        </article>
        <article class="shell-card shell-card-loading">
          <h3>Jira Today</h3>
          <p>Loading assigned Jira work that looks workable today...</p>
        </article>
      </div>
      <div class="dashboard-secondary-column">
        <article class="shell-card shell-card-loading">
          <h3>Needs Your Reply</h3>
          <p>Loading reply-needed items and the personal response queue...</p>
        </article>
        <article class="shell-card shell-card-loading">
          <h3>OKR This Week</h3>
          <p>Loading the objectives and KRs that seem most worth moving this week...</p>
        </article>
        <article class="shell-card shell-card-loading">
          <h3>Resume Work</h3>
          <p>Loading the most relevant recent sessions for a direct return into active work...</p>
        </article>
      </div>
    </section>
  `;
}

function renderModulesLoading() {
  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">Personal Workspace</div>
      <h2>Workspace</h2>
      <p>Workspace keeps Jira Board and OKR Studio. Calendar and Meetings have dedicated surfaces in the left rail.</p>
    </section>
    <section class="command-center-grid" aria-busy="true">
      <article class="shell-card shell-card-loading">
        <h3>Jira Board</h3>
        <p>Loading Jira Board's execution-risk surface...</p>
      </article>
      <article class="shell-card shell-card-loading">
        <h3>OKR Studio</h3>
        <p>Loading OKR Studio's evidence-to-narrative surface...</p>
      </article>
    </section>
  `;
}

function renderCalendarShellLoading() {
  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Calendar</span>
          <span class="page-hero-status">Loading</span>
        </div>
        <h1>Calendar</h1>
        <p class="page-hero-lede">Loading the live schedule read, focus blocks, and transition signals...</p>
      </div>
    </section>
    <section class="page-canvas" aria-busy="true">
      <article class="page-section col-span-8">
        <div class="page-section-header"><h3 class="page-section-title">Today's schedule</h3></div>
        <p>Loading events from the calendar source...</p>
      </article>
      <article class="page-section col-span-4">
        <div class="page-section-header"><h3 class="page-section-title">Day signals</h3></div>
        <p>Loading focus blocks, overload, and transitions...</p>
      </article>
    </section>
  `;
}

function renderMeetingsShellLoading() {
  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Meetings</span>
          <span class="page-hero-status">Loading</span>
        </div>
        <h1>Meetings</h1>
        <p class="page-hero-lede">Loading today's meeting objects, prep lens, and follow-up queue...</p>
      </div>
    </section>
    <section class="page-canvas" aria-busy="true">
      <article class="page-section col-span-4"><p>Loading today's meetings...</p></article>
      <article class="page-section col-span-5"><p>Loading prep readiness...</p></article>
      <article class="page-section col-span-3"><p>Loading follow-up queue...</p></article>
    </section>
  `;
}

function renderOperationsLoading() {
  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">Operations</div>
      <h2>Operations</h2>
      <p>Loading the operations launcher so Campaign Ops, Skills, Agents, Workflows, and Memory stay grouped together.</p>
    </section>
    <section class="command-center-grid" aria-busy="true">
      <article class="shell-card shell-card-loading">
        <h3>Campaign Ops</h3>
        <p>Loading the operations bridge to saved workflows and scheduled work...</p>
      </article>
      <article class="shell-card shell-card-loading">
        <h3>Skills</h3>
        <p>Loading the skills surface...</p>
      </article>
      <article class="shell-card shell-card-loading">
        <h3>Agents</h3>
        <p>Loading agent inventory and launchers...</p>
      </article>
      <article class="shell-card shell-card-loading">
        <h3>Memory</h3>
        <p>Loading project and workspace memory surfaces...</p>
      </article>
    </section>
  `;
}

function renderWorkflowsShellLoading() {
  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">Operations</div>
      <h2>Workflows</h2>
      <p>Loading the current workflow inventory so this shell reflects saved state instead of static placeholder copy.</p>
    </section>
    <section class="command-center-grid shell-destination-grid" aria-busy="true">
      <article class="shell-card shell-card-loading">
        <h3>Workflow Inventory</h3>
        <p>Reading saved workflows...</p>
      </article>
      <article class="shell-card shell-card-loading">
        <h3>Saved Workflows</h3>
        <p>Loading the current list of repeatable flows...</p>
      </article>
      <article class="shell-card shell-card-loading">
        <h3>Current Limits</h3>
        <p>Keeping the current builder limitations visible while the shell loads.</p>
      </article>
    </section>
  `;
}

function renderAgentsShellLoading() {
  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">Operations</div>
      <h2>Agents</h2>
      <p>Loading the current agent, chain, and DAG inventory so this shell reflects real saved launchers instead of static placeholder copy.</p>
    </section>
    <section class="command-center-grid shell-destination-grid" aria-busy="true">
      <article class="shell-card shell-card-loading">
        <h3>Agent Inventory</h3>
        <p>Reading saved agents...</p>
      </article>
      <article class="shell-card shell-card-loading">
        <h3>Saved Agents</h3>
        <p>Loading current worker profiles...</p>
      </article>
      <article class="shell-card shell-card-loading">
        <h3>Saved Launchers</h3>
        <p>Loading chain and DAG launchers...</p>
      </article>
    </section>
  `;
}

function renderCommandCenter(viewModel) {
  return `
    <section class="dashboard-main-grid">
      <div class="dashboard-primary-column">
        ${renderDailyBriefCard(viewModel.brief)}
        ${renderJiraToday(viewModel.jiraToday)}
        ${renderCaptureTodayWork(viewModel.captureWork)}
      </div>
      <div class="dashboard-secondary-column">
        ${renderNeedsYourReply(viewModel.replyWork)}
        ${renderOkrThisWeek(viewModel.okrThisWeek)}
        ${renderResumeWork(viewModel.resumeWork)}
      </div>
    </section>
  `;
}

function renderDailyBriefCard(brief) {
  if (!brief) {
    return `
      <article class="shell-card dashboard-brief-card dashboard-brief-empty">
        <div class="dashboard-brief-header">
          <div>
            <div class="shell-eyebrow">Daily Brief</div>
            <h3>No brief yet</h3>
          </div>
        </div>
        <p class="dashboard-brief-empty-copy">Artemis will pull together your Jira tickets, calendar, Slack signals, OKRs, and recent sessions — then give you a prioritized, opinionated brief for the day. It persists until you refresh it.</p>
        <div class="dashboard-brief-actions">
          <button class="btn btn-amber btn-sm" data-action="generate-brief">Generate today's brief</button>
        </div>
      </article>
    `;
  }

  const generatedAt = brief._generatedAt ? new Date(brief._generatedAt) : null;
  const ageLabel = generatedAt ? _briefAgeLabel(generatedAt) : '';
  const sourcePills = (brief.sourcesUsed ?? [])
    .map((s) => `<span class="dashboard-brief-source-pill">${escapeHtml(s)}</span>`)
    .join('');

  const priorityItems = (brief.priorities ?? []).map((p) => `
    <div class="dashboard-brief-priority">
      <span class="dashboard-brief-priority-rank">${p.rank}</span>
      <div class="dashboard-brief-priority-body">
        <strong>${escapeHtml(p.title)}${p.ticket ? ` <span class="dashboard-brief-ticket">${escapeHtml(p.ticket)}</span>` : ''}</strong>
        <p>${escapeHtml(p.why)}</p>
      </div>
    </div>
  `).join('');

  return `
    <article class="shell-card dashboard-brief-card">
      <div class="dashboard-brief-header">
        <div>
          <div class="shell-eyebrow">Daily Brief</div>
          <h3>${escapeHtml(brief.headline ?? 'Today\'s brief')}</h3>
        </div>
        <div class="dashboard-brief-meta">
          ${ageLabel ? `<span class="dashboard-brief-age">${escapeHtml(ageLabel)}</span>` : ''}
          <button class="btn btn-outline btn-xs dashboard-brief-refresh" data-action="refresh-brief" title="Regenerate brief">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            Refresh
          </button>
        </div>
      </div>

      ${brief.continuity ? `
        <div class="dashboard-brief-continuity">
          <span class="dashboard-brief-continuity-label">↻ Continuity</span>
          ${escapeHtml(brief.continuity)}
        </div>
      ` : ''}

      <div class="dashboard-brief-priorities">
        <div class="dashboard-brief-section-label">Priorities</div>
        ${priorityItems}
      </div>

      ${brief.context ? `
        <div class="dashboard-brief-context">
          <span class="dashboard-brief-context-label">Context</span>
          ${escapeHtml(brief.context)}
        </div>
      ` : ''}

      ${brief.defer ? `
        <div class="dashboard-brief-defer">
          <span class="dashboard-brief-defer-label">Defer</span>
          ${escapeHtml(brief.defer)}
        </div>
      ` : ''}

      ${sourcePills ? `<div class="dashboard-brief-sources">${sourcePills}</div>` : ''}
    </article>
  `;
}

function _briefAgeLabel(date) {
  const diffMs = Date.now() - date.getTime();
  const diffMins = Math.floor(diffMs / 60_000);
  if (diffMins < 2) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function renderModulesShell(viewModel) {
  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">Personal Workspace</div>
      <h2>Workspace</h2>
      <p>${escapeHtml(viewModel.heroCopy)}</p>
    </section>
    <section class="command-center-grid">
      <article class="shell-card command-card">
        <div class="command-card-header">
          <div>
            <div class="shell-eyebrow">Personal Workspace</div>
            <h3>Your work surfaces</h3>
          </div>
        </div>
        <p class="command-card-footnote">Use the rail to navigate to Calendar, Meetings, Jira Board, or OKR Studio — each is now a dedicated full-width page.</p>
      </article>
    </section>
  `;
}

function renderCalendarShell(viewModel) {
  const isLive = viewModel.badge === 'Live';
  const isError = viewModel.badge === 'Source error';
  const isSetup = viewModel.badge === 'Needs setup';

  const heroCopy = isLive
    ? `Reading live events from ${viewModel.sourceLabel || 'the configured ICS source'} for ${viewModel.dateLabel || 'today'}. Schedule, focus blocks, and transition buffers are all derived from real events.`
    : isSetup
      ? 'Calendar is ready for a live ICS source. Connect Google Calendar through the Connectors hub to replace shell inference with real event data.'
      : isError
        ? `Calendar has a source configured but the file at ${viewModel.sourcePath || 'the configured path'} could not be read. Fix the source in Connectors to restore the live read.`
        : 'Calendar reads time reality using shell signals. Connect a calendar source through the Connectors hub to replace inference with real event data.';

  const statusTone = viewModel.statusTone || (isLive ? 'live' : isError ? 'error' : 'setup');
  const heroActions = isLive
    ? viewModel.actions.slice(0, 2).map((action) => `
        <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
          ${escapeHtml(action.label)}
        </button>
      `).join('')
    : `
        <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="calendar">
          ${isError ? 'Fix calendar source' : 'Connect calendar source'}
        </button>
      `;

  const statBlock = (viewModel.summary || []).map((item) => `
    <div class="page-hero-stat">
      <div class="page-hero-stat-label">${escapeHtml(item.label)}</div>
      <div class="page-hero-stat-value">${escapeHtml(item.value)}</div>
    </div>
  `).join('');

  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Calendar</span>
          <span class="page-hero-status" data-tone="${statusTone}">${escapeHtml(viewModel.badge || 'Read-only')}</span>
          ${viewModel.dateLabel ? `<span class="page-section-meta">${escapeHtml(viewModel.dateLabel)}${viewModel.timezone ? ` · ${escapeHtml(viewModel.timezone)}` : ''}</span>` : ''}
        </div>
        <h1>Calendar</h1>
        <p class="page-hero-lede">${escapeHtml(heroCopy)}</p>
      </div>
      <div class="page-hero-actions">${heroActions}</div>
      <div class="page-hero-stats">${statBlock}</div>
    </section>
    ${isLive ? renderCalendarLiveCanvas(viewModel) : renderCalendarSetupCanvas(viewModel, { isError })}
  `;
}

function renderCalendarLiveCanvas(viewModel) {
  const events = viewModel.todayEvents || [];
  const dayStartHour = 7;
  const dayEndHour = 20;
  const allDay = events.filter((e) => e.isAllDay);
  const timed = events.filter((e) => !e.isAllDay);

  const positioned = timed.map((event) => {
    const start = parseClockLabel(event.startLabel);
    const end = parseClockLabel(event.endLabel);
    if (start == null || end == null) return null;
    const startMin = Math.max(start, dayStartHour * 60);
    const endMin = Math.min(end, dayEndHour * 60);
    if (endMin <= startMin) return null;
    const top = ((startMin - dayStartHour * 60) / 60) * 44;
    const height = Math.max(((endMin - startMin) / 60) * 44 - 2, 22);
    return { event, top, height };
  }).filter(Boolean);

  const now = new Date();
  const showNow = now.getHours() >= dayStartHour && now.getHours() < dayEndHour;
  const nowTop = showNow
    ? ((now.getHours() * 60 + now.getMinutes()) - dayStartHour * 60) / 60 * 44
    : null;

  const hourRows = [];
  for (let h = dayStartHour; h < dayEndHour; h += 1) {
    const ampm = h < 12 ? 'AM' : 'PM';
    const hr = h % 12 === 0 ? 12 : h % 12;
    hourRows.push(`<div class="day-grid-hour">${hr} ${ampm}</div>`);
  }

  const eventEls = positioned.map(({ event, top, height }) => `
    <div class="day-grid-event" data-status="${escapeAttribute(event.status || 'scheduled')}" style="top:${top}px;height:${height}px">
      <span class="day-grid-event-time">${escapeHtml(event.startLabel)} – ${escapeHtml(event.endLabel)}</span>
      <span class="day-grid-event-title">${escapeHtml(event.title || 'Untitled event')}</span>
      ${event.location ? `<span class="day-grid-event-meta">${escapeHtml(event.location)}</span>` : ''}
    </div>
  `).join('');

  const allDayEl = allDay.length
    ? `<div class="day-grid-allday">
        <span class="day-grid-allday-tag">All-day</span>
        <div>${allDay.map((e) => `<span class="day-grid-allday-row">${escapeHtml(e.title || 'Untitled')}</span>`).join(' ')}</div>
      </div>`
    : '';

  const focusBlockList = (viewModel.focusBlocks || []).slice(0, 5).map((block) => `
    <div class="page-list-row">
      <h4 class="page-list-row-title">${escapeHtml(block.startLabel)}–${escapeHtml(block.endLabel)}</h4>
      <span class="page-list-row-meta">${escapeHtml(block.durationLabel || '')}</span>
    </div>
  `).join('') || `<p class="page-section-footnote">No focus-sized free blocks remain in the working window.</p>`;

  const transitionList = (viewModel.transitionSignals || []).length
    ? viewModel.transitionSignals.map((line) => `<div class="page-list-row"><h4 class="page-list-row-title">${escapeHtml(line)}</h4></div>`).join('')
    : `<p class="page-section-footnote">No back-to-back or sub-15-minute transitions visible in today's window.</p>`;

  return `
    <section class="page-canvas">
      <article class="page-section col-span-8" data-page-section="day-grid">
        <div class="page-section-header">
          <div>
            <div class="page-section-eyebrow">Day grid</div>
            <h3 class="page-section-title">Today's schedule</h3>
          </div>
          <span class="page-section-meta">${escapeHtml(String(viewModel.meetingsCount || 0))} event${viewModel.meetingsCount === 1 ? '' : 's'} · ${formatMinutesAsReadableDuration(viewModel.busyMinutes || 0)} scheduled</span>
        </div>
        ${allDayEl}
        <div class="day-grid">
          <div class="day-grid-hours">${hourRows.join('')}</div>
          <div class="day-grid-track">
            ${nowTop != null ? `<div class="day-grid-now" style="top:${nowTop}px"></div>` : ''}
            ${eventEls}
            ${positioned.length === 0 && allDay.length === 0 ? '<p class="page-section-footnote" style="padding:18px">No events visible in today\'s working window.</p>' : ''}
          </div>
        </div>
        <p class="page-section-footnote">${escapeHtml(viewModel.sourceNote || '')}</p>
      </article>
      <article class="page-section col-span-4" data-page-section="day-signals">
        <div class="page-section-header">
          <div>
            <div class="page-section-eyebrow">Day signals</div>
            <h3 class="page-section-title">Focus &amp; transitions</h3>
          </div>
        </div>
        <div>
          <div class="page-section-eyebrow" style="margin-bottom:6px">Focus blocks</div>
          <div class="page-list">${focusBlockList}</div>
        </div>
        <div>
          <div class="page-section-eyebrow" style="margin-bottom:6px">Transition buffers</div>
          <div class="page-list">${transitionList}</div>
        </div>
        <div>
          <div class="page-section-eyebrow" style="margin-bottom:6px">Overload</div>
          <div class="page-list">
            ${(viewModel.overloadSignals || []).map((item) => `
              <div class="page-list-row">
                <h4 class="page-list-row-title">${escapeHtml(item.title)}</h4>
                <p class="page-list-row-detail">${escapeHtml(item.detail)}</p>
              </div>
            `).join('')}
          </div>
        </div>
        <div class="shell-actions">
          ${(viewModel.actions || []).map((action) => `
            <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
              ${escapeHtml(action.label)}
            </button>
          `).join('')}
        </div>
      </article>
    </section>
  `;
}

function renderCalendarSetupCanvas(viewModel, { isError }) {
  return `
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <h3>${isError ? 'Calendar source could not be read' : 'Connect a calendar source'}</h3>
          <p>${escapeHtml(viewModel.sourceNote || '')}</p>
          <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="calendar">
            ${isError ? 'Fix in Connectors' : 'Open Connectors'}
          </button>
        </div>
        <div class="page-section-header" style="margin-top:8px">
          <div>
            <div class="page-section-eyebrow">While you're disconnected</div>
            <h3 class="page-section-title">Manual time reality still applies</h3>
          </div>
        </div>
        <div class="page-list">
          ${(viewModel.overloadSignals || []).map((item) => `
            <div class="page-list-row">
              <h4 class="page-list-row-title">${escapeHtml(item.title)}</h4>
              <p class="page-list-row-detail">${escapeHtml(item.detail)}</p>
            </div>
          `).join('')}
        </div>
        <div class="shell-actions">
          ${(viewModel.actions || []).map((action) => `
            <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
              ${escapeHtml(action.label)}
            </button>
          `).join('')}
        </div>
      </article>
    </section>
  `;
}

function parseClockLabel(label) {
  if (!label || typeof label !== 'string') return null;
  const m = label.trim().match(/^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$/i);
  if (!m) return null;
  let hour = parseInt(m[1], 10);
  const minute = m[2] ? parseInt(m[2], 10) : 0;
  const ampm = m[3] ? m[3].toUpperCase() : null;
  if (ampm === 'AM') {
    if (hour === 12) hour = 0;
  } else if (ampm === 'PM') {
    if (hour !== 12) hour += 12;
  }
  return hour * 60 + minute;
}

function renderCalendarShellError() {
  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Calendar</span>
          <span class="page-hero-status" data-tone="error">Failed</span>
        </div>
        <h1>Calendar</h1>
        <p class="page-hero-lede">Calendar could not load. Check your network or calendar source configuration in Connectors.</p>
      </div>
      <div class="page-hero-actions">
        <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="calendar">Open Connectors</button>
      </div>
    </section>
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <h3>Unable to load Calendar</h3>
          <p>The Calendar surface encountered an error while loading. Reload to try again.</p>
        </div>
      </article>
    </section>
  `;
}

// ── Interactive Calendar (Phase 2) ───────────────────────────────────────────

const CAL_DAY_START = 7;  // 7 AM
const CAL_DAY_END   = 20; // 8 PM
const CAL_WEEK_CELL_H = 64;  // px per hour in week view
const CAL_DAY_CELL_H  = 72;  // px per hour in day view
const CAL_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const CAL_MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];

function _calValidateView(v) {
  return ['day', 'week', 'month'].includes(v) ? v : 'week';
}

// Format a Date as "YYYY-MM-DD" in LOCAL time (not UTC) to avoid day-shift bugs
function _calLocalDateStr(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Parse a stored focus-date string. Treat it as a local-time date by appending T00:00:00
// so that timezone offsets don't shift the day.
function _calParseFocusDate(str) {
  if (!str) return new Date();
  // Accept "YYYY-MM-DD" or full ISO — slice to date portion, parse as local midnight
  const dateOnly = String(str).slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateOnly)) return new Date();
  const d = new Date(`${dateOnly}T00:00:00`);
  return isNaN(d.getTime()) ? new Date() : d;
}

function _calComputeRange(focusDate, view) {
  const d = new Date(focusDate);
  d.setHours(0, 0, 0, 0);
  if (view === 'day') {
    const rangeStart = new Date(d);
    const rangeEnd = new Date(d);
    rangeEnd.setDate(rangeEnd.getDate() + 1);
    return { rangeStart, rangeEnd };
  }
  if (view === 'week') {
    const sun = new Date(d);
    sun.setDate(d.getDate() - d.getDay());
    const nextSun = new Date(sun);
    nextSun.setDate(sun.getDate() + 7);
    return { rangeStart: sun, rangeEnd: nextSun };
  }
  // month
  const first = new Date(d.getFullYear(), d.getMonth(), 1);
  const next = new Date(d.getFullYear(), d.getMonth() + 1, 1);
  return { rangeStart: first, rangeEnd: next };
}

function _calNavFocusDate(focusDate, view, direction) {
  const d = new Date(focusDate);
  if (view === 'day') d.setDate(d.getDate() + direction);
  else if (view === 'week') d.setDate(d.getDate() + direction * 7);
  else d.setMonth(d.getMonth() + direction);
  return d;
}

function _calFormatRangeLabel(focusDate, view) {
  const d = new Date(focusDate);
  if (view === 'day') {
    return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  }
  if (view === 'week') {
    const sun = new Date(d);
    sun.setDate(d.getDate() - d.getDay());
    const sat = new Date(sun);
    sat.setDate(sun.getDate() + 6);
    const s = sun.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const e = sat.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    return `${s} – ${e}`;
  }
  return `${CAL_MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

// Assign a color class to an event based on its title keywords
function _calEventKind(event) {
  const t = (event.title || '').toLowerCase();
  if (/standup|sync|1:1|meeting|call|interview/.test(t)) return 'amber';
  if (/review|demo|present/.test(t)) return 'rust';
  if (/focus|deep work|block/.test(t)) return 'sage';
  return 'slate';
}

function _calFmtTime(date) {
  const d = new Date(date);
  const h = d.getHours();
  const m = d.getMinutes();
  const ampm = h >= 12 ? 'pm' : 'am';
  const hr = h % 12 === 0 ? 12 : h % 12;
  return m === 0 ? `${hr}${ampm}` : `${hr}:${String(m).padStart(2, '0')}${ampm}`;
}

function _calIsSameDay(a, b) {
  const da = new Date(a), db = new Date(b);
  return da.getFullYear() === db.getFullYear() &&
    da.getMonth() === db.getMonth() &&
    da.getDate() === db.getDate();
}

// ── Render: toolbar ──────────────────────────────────────────────────────────

function renderCalendarToolbar(focusDate, view) {
  const rangeLabel = _calFormatRangeLabel(focusDate, view);
  const focusISO = new Date(focusDate).toISOString();
  return `
    <div class="cal-toolbar">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;flex:1;">
        <div class="cal-view-seg">
          ${['day','week','month'].map((v) => `
            <button class="cal-view-seg-btn${view === v ? ' active' : ''}"
                    data-cal-view="${v}">${v[0].toUpperCase() + v.slice(1)}</button>
          `).join('')}
        </div>
        <div style="display:flex;align-items:center;gap:4px;">
          <button class="cal-nav-btn" data-cal-nav="-1" aria-label="Previous">&#8249;</button>
          <button class="cal-nav-btn cal-nav-today" data-cal-nav="today">Today</button>
          <button class="cal-nav-btn" data-cal-nav="1" aria-label="Next">&#8250;</button>
        </div>
        <span class="cal-range-label" data-cal-focus="${escapeAttribute(focusISO)}">${escapeHtml(rangeLabel)}</span>
      </div>
      <button class="btn btn-ink btn-sm cal-new-event-btn" data-cal-new-event
              data-cal-focus="${escapeAttribute(focusISO)}"
              aria-label="Create new event">+ Event</button>
    </div>
  `;
}

// ── Lane assignment for overlapping events ───────────────────────────────────
// Returns a Map<uid, { lane: number, cols: number }> for a set of timed events.
// Events in the same overlap group share the same `cols` value; `lane` is the
// 0-based column index within that group.
function _assignEventLanes(events) {
  const sorted = [...events].sort((a, b) => new Date(a.start) - new Date(b.start));
  const laneEnds = []; // laneEnds[i] = end-ms of the last event placed in lane i
  const laneOf = new Map(); // uid → lane index
  for (const ev of sorted) {
    const evStart = new Date(ev.start).getTime();
    const evEnd   = new Date(ev.end).getTime();
    let idx = laneEnds.findIndex((end) => end <= evStart);
    if (idx === -1) idx = laneEnds.length;
    laneEnds[idx] = evEnd;
    laneOf.set(ev.uid, idx);
  }
  // Determine `cols` for each event: maximum lane index of any overlapping event + 1
  const result = new Map();
  for (const ev of sorted) {
    const evStart = new Date(ev.start).getTime();
    const evEnd   = new Date(ev.end).getTime();
    const maxLane = Math.max(
      ...sorted
        .filter((o) => new Date(o.start).getTime() < evEnd && new Date(o.end).getTime() > evStart)
        .map((o) => laneOf.get(o.uid)),
    );
    result.set(ev.uid, { lane: laneOf.get(ev.uid), cols: maxLane + 1 });
  }
  return result;
}

// ── Render: Day view ─────────────────────────────────────────────────────────

function renderCalendarDay(events, focusDate) {
  const today = new Date();
  const isToday = _calIsSameDay(focusDate, today);
  const d = new Date(focusDate);
  const dayLabel = d.toLocaleDateString('en-US', { weekday: 'long' });
  const dateLabel = d.toLocaleDateString('en-US', { month: 'long', day: 'numeric' });

  const dayEvents = events.filter((e) => {
    if (e.isAllDay) return _calIsSameDay(e.start, focusDate);
    const start = new Date(e.start);
    return _calIsSameDay(start, focusDate);
  });
  const allDay = dayEvents.filter((e) => e.isAllDay);
  const timed = dayEvents.filter((e) => !e.isAllDay);

  const now = new Date();
  const nowMin = isToday ? now.getHours() * 60 + now.getMinutes() : null;

  const allDayEl = allDay.length
    ? `<div class="cal-allday-row">${allDay.map((e) =>
        `<span class="cal-allday-chip">${escapeHtml(e.title)}</span>`
      ).join('')}</div>`
    : '';

  const hourCells = [];
  for (let h = CAL_DAY_START; h < CAL_DAY_END; h++) {
    const label = h > 12 ? `${h - 12} pm` : h === 12 ? '12 pm' : `${h} am`;
    const hourEvents = timed.filter((e) => {
      const startH = new Date(e.start).getHours();
      return startH === h;
    });
    const nowEl = (nowMin != null && nowMin >= h * 60 && nowMin < (h + 1) * 60)
      ? `<div class="cal-now" style="top:${((nowMin - h * 60) / 60) * CAL_DAY_CELL_H}px"></div>`
      : '';
    const eventEls = hourEvents.map((e) => {
      const startMs = new Date(e.start).getTime();
      const endMs = new Date(e.end).getTime();
      const startMin = new Date(e.start).getHours() * 60 + new Date(e.start).getMinutes();
      const offsetPx = ((startMin - h * 60) / 60) * CAL_DAY_CELL_H + 2;
      const durationMin = (endMs - startMs) / 60000;
      const heightPx = Math.max((durationMin / 60) * CAL_DAY_CELL_H - 4, 20);
      const kind = _calEventKind(e);
      return `
        <div class="cal-event ${kind}" draggable="true"
             style="top:${offsetPx}px;height:${heightPx}px;left:16px;right:16px;"
             data-event-id="${escapeAttribute(e.uid)}"
             data-event-start="${escapeAttribute(new Date(e.start).toISOString())}"
             data-event-end="${escapeAttribute(new Date(e.end).toISOString())}">
          <div class="cal-event-title">${escapeHtml(e.title)}</div>
          <div class="cal-event-meta">${escapeHtml(_calFmtTime(e.start))} – ${escapeHtml(_calFmtTime(e.end))}</div>
        </div>
      `;
    }).join('');
    hourCells.push(`
      <div class="cal-hour">${escapeHtml(label)}</div>
      <div class="cal-day-cell" data-cal-cell="1" data-hour="${h}" data-date="${_calLocalDateStr(new Date(focusDate))}" data-cell-h="${CAL_DAY_CELL_H}">
        ${nowEl}${eventEls}
      </div>
    `);
  }

  return `
    <div class="cal-day">
      <div class="cal-day-head">
        <div class="cal-day-label">${escapeHtml(dayLabel)}</div>
        <div class="cal-day-num">${escapeHtml(dateLabel)}</div>
      </div>
      ${allDayEl}
      <div class="cal-day-grid" style="overflow-y:auto;max-height:calc(100vh - 220px);">
        ${hourCells.join('')}
      </div>
    </div>
  `;
}

// ── Render: Week view ────────────────────────────────────────────────────────

function renderCalendarWeek(events, focusDate) {
  const today = new Date();
  const d = new Date(focusDate);

  // Compute Sun–Sat for the focus week
  const sun = new Date(d);
  sun.setDate(d.getDate() - d.getDay());
  sun.setHours(0, 0, 0, 0);
  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const day = new Date(sun);
    day.setDate(sun.getDate() + i);
    return day;
  });

  const nowMin = today.getHours() * 60 + today.getMinutes();
  const nowDayIdx = weekDays.findIndex((day) => _calIsSameDay(day, today));

  // All-day row
  const allDayRow = weekDays.map((day) => {
    const adEvents = events.filter((e) => e.isAllDay && _calIsSameDay(e.start, day));
    return `<div class="cal-cell" style="min-height:28px;height:auto;padding:2px 4px;">
      ${adEvents.map((e) => `<span class="cal-allday-chip">${escapeHtml(e.title)}</span>`).join('')}
    </div>`;
  }).join('');

  // Column headers
  const headers = weekDays.map((day, i) => {
    const isToday = _calIsSameDay(day, today);
    const dayShort = CAL_DAYS[i];
    const dateNum = day.getDate();
    return `<div class="cal-head${isToday ? ' today' : ''}">
      <div class="cal-head-day">${escapeHtml(dayShort)}</div>
      <div class="cal-head-num">${dateNum}</div>
    </div>`;
  }).join('');

  // Pre-compute overlap lanes per day so concurrent events render side-by-side
  const dayLaneMap = new Map();
  weekDays.forEach((day) => {
    const timedDayEvts = events.filter((e) => !e.isAllDay && _calIsSameDay(new Date(e.start), day));
    dayLaneMap.set(_calLocalDateStr(day), _assignEventLanes(timedDayEvts));
  });

  // Hour rows
  const hourRows = [];
  for (let h = CAL_DAY_START; h < CAL_DAY_END; h++) {
    const label = h > 12 ? `${h - 12} pm` : h === 12 ? '12 pm' : `${h} am`;
    const cells = weekDays.map((day, di) => {
      const dateStr = _calLocalDateStr(day);
      const laneMap = dayLaneMap.get(dateStr) || new Map();
      const hourEvents = events.filter((e) => {
        if (e.isAllDay) return false;
        const es = new Date(e.start);
        return _calIsSameDay(es, day) && es.getHours() === h;
      });
      const nowEl = (di === nowDayIdx && nowMin >= h * 60 && nowMin < (h + 1) * 60)
        ? `<div class="cal-now" style="top:${((nowMin - h * 60) / 60) * CAL_WEEK_CELL_H}px"></div>`
        : '';
      const eventEls = hourEvents.map((e) => {
        const startMs = new Date(e.start).getTime();
        const endMs = new Date(e.end).getTime();
        const startMin = new Date(e.start).getHours() * 60 + new Date(e.start).getMinutes();
        const offsetPx = ((startMin - h * 60) / 60) * CAL_WEEK_CELL_H + 2;
        const durationMin = (endMs - startMs) / 60000;
        const heightPx = Math.max((durationMin / 60) * CAL_WEEK_CELL_H - 4, 16);
        const kind = _calEventKind(e);
        const { lane = 0, cols = 1 } = laneMap.get(e.uid) || {};
        const pctW = 100 / cols;
        const pctL = lane * pctW;
        return `
          <div class="cal-event ${kind}" draggable="true"
               style="top:${offsetPx}px;height:${heightPx}px;left:calc(${pctL}% + 2px);width:calc(${pctW}% - 4px);right:auto;"
               data-event-id="${escapeAttribute(e.uid)}"
               data-event-start="${escapeAttribute(new Date(e.start).toISOString())}"
               data-event-end="${escapeAttribute(new Date(e.end).toISOString())}">
            <div class="cal-event-title">${escapeHtml(e.title)}</div>
            <div class="cal-event-meta">${escapeHtml(_calFmtTime(e.start))}</div>
          </div>
        `;
      }).join('');
      return `<div class="cal-cell" data-cal-cell="1" data-hour="${h}" data-date="${dateStr}" data-cell-h="${CAL_WEEK_CELL_H}">
        ${nowEl}${eventEls}
      </div>`;
    }).join('');

    hourRows.push(`
      <div class="cal-hour">${escapeHtml(label)}</div>
      ${cells}
    `);
  }

  return `
    <div class="cal-grid" style="overflow-y:auto;max-height:calc(100vh - 180px);">
      <div class="cal-corner"></div>
      ${headers}
      <div class="cal-hour" style="font-size:10px;color:var(--ink-5);text-align:right;padding:4px 6px;">All-day</div>
      ${allDayRow}
      ${hourRows.join('')}
    </div>
  `;
}

// ── Render: Month view ───────────────────────────────────────────────────────

function renderCalendarMonth(events, focusDate) {
  const today = new Date();
  const d = new Date(focusDate);
  const year = d.getFullYear();
  const month = d.getMonth();

  // Build calendar grid: start from Sunday of the week containing the 1st
  const firstOfMonth = new Date(year, month, 1);
  const startOffset = firstOfMonth.getDay(); // 0=Sun
  const gridStart = new Date(firstOfMonth);
  gridStart.setDate(1 - startOffset);

  const cells = [];
  const cur = new Date(gridStart);
  for (let i = 0; i < 42; i++) {
    cells.push(new Date(cur));
    cur.setDate(cur.getDate() + 1);
  }

  const headers = CAL_DAYS.map((d) => `<div class="cal-month-head-cell">${d}</div>`).join('');

  const cellEls = cells.map((cell) => {
    const isCurrentMonth = cell.getMonth() === month;
    const isToday = _calIsSameDay(cell, today);
    const dayEvents = events.filter((e) => _calIsSameDay(e.start, cell));
    const dotCount = Math.min(dayEvents.length, 4);
    const extra = dayEvents.length > 4 ? dayEvents.length - 4 : 0;
    const dateISO = _calLocalDateStr(cell);
    const cls = [
      'cal-month-cell',
      !isCurrentMonth ? 'next' : '',
      isToday ? 'today' : '',
    ].filter(Boolean).join(' ');
    const dots = Array.from({ length: dotCount }).map(() => '<span></span>').join('');
    const extraEl = extra > 0 ? `<span class="cal-month-more">+${extra}</span>` : '';
    return `
      <div class="${cls}" data-cal-month-cell="${dateISO}">
        <div class="cal-month-n">${cell.getDate()}</div>
        <div class="cal-month-dots">${dots}${extraEl}</div>
      </div>
    `;
  }).join('');

  return `
    <div class="cal-month">
      <div class="cal-month-head">${headers}</div>
      <div class="cal-month-grid">${cellEls}</div>
    </div>
  `;
}

// ── Render: full interactive page ────────────────────────────────────────────

function renderCalendarInteractivePage(events, focusDate, view, fetchError) {
  const errorBanner = fetchError
    ? `<div class="cal-fetch-error">
        Could not load events from Google Calendar.
        If you recently connected Google, <button class="cal-reconnect-btn" data-shell-action="open-connectors" data-connector-scope="calendar">Disconnect + reconnect in Connectors</button> to grant the <code>calendar.events</code> scope.
      </div>`
    : '';

  let viewEl = '';
  if (view === 'day') viewEl = renderCalendarDay(events, focusDate);
  else if (view === 'month') viewEl = renderCalendarMonth(events, focusDate);
  else viewEl = renderCalendarWeek(events, focusDate);

  return `
    <div class="cal-page">
      ${renderCalendarToolbar(focusDate, view)}
      ${errorBanner}
      ${viewEl}
    </div>
  `;
}

// ── Wire: event delegation ───────────────────────────────────────────────────

let _calDragState = null; // { eventId, startISO, endISO, durationMs }

function _wireCalendarPage(events, focusDate, view) {
  if (!appShellContent) return;

  // Single click handler via delegation
  appShellContent.addEventListener('click', function _calClick(e) {
    // View toggle
    const viewBtn = e.target.closest('[data-cal-view]');
    if (viewBtn) {
      const newView = viewBtn.dataset.calView;
      localStorage.setItem(CAL_VIEW_STORAGE_KEY, newView);
      appShellContent.removeEventListener('click', _calClick);
      appShellContent.removeEventListener('dragstart', _calDragStart);
      appShellContent.removeEventListener('dragover', _calDragOver);
      appShellContent.removeEventListener('drop', _calDrop);
      appShellContent.removeEventListener('dragend', _calDragEnd);
      loadCalendarShell();
      return;
    }

    // Navigation
    const navBtn = e.target.closest('[data-cal-nav]');
    if (navBtn) {
      const nav = navBtn.dataset.calNav;
      const currentFocusDate = _calParseFocusDate(
        appShellContent.querySelector('[data-cal-focus]')?.dataset?.calFocus
      );
      let newFocus;
      if (nav === 'today') {
        newFocus = new Date();
      } else {
        newFocus = _calNavFocusDate(currentFocusDate, view, parseInt(nav, 10));
      }
      localStorage.setItem(CAL_FOCUS_DATE_STORAGE_KEY, _calLocalDateStr(newFocus));
      appShellContent.removeEventListener('click', _calClick);
      appShellContent.removeEventListener('dragstart', _calDragStart);
      appShellContent.removeEventListener('dragover', _calDragOver);
      appShellContent.removeEventListener('drop', _calDrop);
      appShellContent.removeEventListener('dragend', _calDragEnd);
      loadCalendarShell();
      return;
    }

    // Month cell → switch to day view for that date
    const monthCell = e.target.closest('[data-cal-month-cell]');
    if (monthCell) {
      const dateStr = monthCell.dataset.calMonthCell; // already "YYYY-MM-DD" local
      localStorage.setItem(CAL_FOCUS_DATE_STORAGE_KEY, dateStr);
      localStorage.setItem(CAL_VIEW_STORAGE_KEY, 'day');
      appShellContent.removeEventListener('click', _calClick);
      appShellContent.removeEventListener('dragstart', _calDragStart);
      appShellContent.removeEventListener('dragover', _calDragOver);
      appShellContent.removeEventListener('drop', _calDrop);
      appShellContent.removeEventListener('dragend', _calDragEnd);
      loadCalendarShell();
      return;
    }

    // + Event button → open new-event modal
    const newEventBtn = e.target.closest('[data-cal-new-event]');
    if (newEventBtn) {
      const focusISO = appShellContent.querySelector('[data-cal-focus]')?.dataset?.calFocus;
      const defaultStart = focusISO ? _calParseFocusDate(focusISO) : new Date();
      _getOrCreateCalendarNewEventModal().open(defaultStart);
      return;
    }

    // Connectors reconnect button
    if (e.target.closest('[data-shell-action="open-connectors"]')) {
      emit('open-connectors', { scope: e.target.closest('[data-connector-scope]')?.dataset?.connectorScope });
      return;
    }

    // Event click → open detail drawer
    const eventEl = e.target.closest('[data-event-id]');
    if (eventEl) {
      const id = eventEl.dataset.eventId;
      _getOrCreateCalendarDrawer().open(id);
    }
  }, { once: false });

  // Drag handlers
  function _calDragStart(e) {
    const eventEl = e.target.closest('[data-event-id]');
    if (!eventEl) return;
    const startISO = eventEl.dataset.eventStart;
    const endISO = eventEl.dataset.eventEnd;
    const durationMs = new Date(endISO).getTime() - new Date(startISO).getTime();
    _calDragState = { eventId: eventEl.dataset.eventId, startISO, endISO, durationMs };
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', eventEl.dataset.eventId);
  }

  function _calDragOver(e) {
    const cell = e.target.closest('[data-cal-cell]');
    if (!cell || !_calDragState) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    // Highlight target cell
    appShellContent.querySelectorAll('.cal-drop-highlight').forEach((el) => el.classList.remove('cal-drop-highlight'));
    cell.classList.add('cal-drop-highlight');
  }

  function _calDrop(e) {
    const cell = e.target.closest('[data-cal-cell]');
    if (!cell || !_calDragState) return;
    e.preventDefault();
    appShellContent.querySelectorAll('.cal-drop-highlight').forEach((el) => el.classList.remove('cal-drop-highlight'));

    const { eventId, durationMs } = _calDragState;
    const cellH = parseInt(cell.dataset.cellH || '64', 10);
    const rect = cell.getBoundingClientRect();
    const relY = Math.max(0, e.clientY - rect.top);
    const rawMin = (relY / cellH) * 60;
    const snappedMin = Math.min(Math.round(rawMin / 15) * 15, 59);
    const hour = parseInt(cell.dataset.hour, 10);
    const dateStr = cell.dataset.date; // YYYY-MM-DD

    // dateStr is already "YYYY-MM-DD" local time (from _calLocalDateStr)
    const newStart = new Date(`${dateStr}T00:00:00`); // local midnight
    newStart.setHours(hour, snappedMin, 0, 0);
    const newEnd = new Date(newStart.getTime() + durationMs);

    const patch = {
      start: { dateTime: newStart.toISOString() },
      end: { dateTime: newEnd.toISOString() },
    };

    _calDragState = null;

    // Optimistic update: store old state, re-render with modified events
    const updatedEvents = events.map((ev) =>
      ev.uid === eventId ? { ...ev, start: newStart.toISOString(), end: newEnd.toISOString() } : ev
    );
    appShellContent.innerHTML = renderCalendarInteractivePage(updatedEvents, focusDate, view, null);
    _wireCalendarPage(updatedEvents, focusDate, view);

    updateCalendarEventApi(eventId, patch)
      .then(() => {
        _swrInvalidateCalendarEvents();
        loadCalendarShell();
      })
      .catch((err) => {
        console.error('Drag reschedule failed, rolling back:', err);
        _swrInvalidateCalendarEvents();
        loadCalendarShell();
      });
  }

  function _calDragEnd() {
    _calDragState = null;
    appShellContent.querySelectorAll('.cal-drop-highlight').forEach((el) => el.classList.remove('cal-drop-highlight'));
  }

  appShellContent.addEventListener('dragstart', _calDragStart);
  appShellContent.addEventListener('dragover', _calDragOver);
  appShellContent.addEventListener('drop', _calDrop);
  appShellContent.addEventListener('dragend', _calDragEnd);
}

// ── Meetings Past tab helpers ────────────────────────────────────────────────

function handleMeetingsTabSwitch(tab) {
  if (!appShellContent) return;
  const todayCanvas = appShellContent.querySelector('[data-meetings-canvas="today"]');
  const pastCanvas = appShellContent.querySelector('[data-meetings-canvas="past"]');
  const tabs = appShellContent.querySelectorAll('[data-meetings-tab-btn]');
  if (!todayCanvas || !pastCanvas) return;

  const isPast = tab === 'past';
  todayCanvas.classList.toggle('hidden', isPast);
  pastCanvas.classList.toggle('hidden', !isPast);
  tabs.forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
}

// Best-effort action-item extraction from Granola's summary/transcript text.
// Granola's notes often contain markdown bullets, "Action items:" headers,
// or "TODO:"-style prefixes. We grab any line that looks like an action.
function extractActionItemsFromText(text) {
  if (!text || typeof text !== 'string') return [];
  const items = [];
  const lines = text.split(/\r?\n/);
  let inActionBlock = false;
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { inActionBlock = false; continue; }
    if (/^#{1,6}\s*(action items?|next steps?|todos?|follow[- ]?ups?)\b/i.test(line)) {
      inActionBlock = true;
      continue;
    }
    // bullet line
    const bullet = line.match(/^[-*•]\s+(.+)$/);
    if (bullet && (inActionBlock || /\b(todo|action|follow up|will|should|owner)\b/i.test(bullet[1]))) {
      items.push(bullet[1].replace(/\s+/g, ' ').trim());
      continue;
    }
    // explicit prefix
    const prefixed = line.match(/^(?:todo|action(?: item)?|follow[- ]?up)\s*[:–-]\s*(.+)$/i);
    if (prefixed) items.push(prefixed[1].trim());
  }
  // Dedupe + cap
  return Array.from(new Set(items)).slice(0, 20);
}

async function handleMeetingsRowClick(meetingId, meetingTitle) {
  if (!meetingId || !appShellContent) return;

  const panel = appShellContent.querySelector('[data-meetings-transcript-panel]');
  if (!panel) return;

  panel.innerHTML = `<div class="meetings-transcript-loading">Loading transcript…</div>`;

  try {
    const result = await fetchGranolaTranscriptApi(meetingId);
    if (!result.connected) {
      panel.innerHTML = `<div class="page-section-footnote">Could not load this meeting.</div>`;
      return;
    }
    if (result.found === false) {
      panel.innerHTML = `<div class="page-section-footnote">No transcript available for this meeting.</div>`;
      return;
    }

    // Granola payload shape varies; surface what we have. Action items are
    // sometimes a structured array, sometimes embedded in summary text.
    const summary = result.summary || result.notes || '';
    const transcript = result.transcript || '';
    const actionItems = Array.isArray(result.action_items)
      ? result.action_items
      : extractActionItemsFromText(summary || transcript);
    const attendees = Array.isArray(result.attendees) ? result.attendees.join(', ') : '';

    const sectionsHtml = [];
    if (summary) {
      sectionsHtml.push(`
        <section class="meetings-detail-section">
          <h4 class="meetings-detail-heading">Summary</h4>
          <div class="meetings-detail-body">${escapeHtml(summary)}</div>
        </section>
      `);
    }
    if (actionItems.length) {
      sectionsHtml.push(`
        <section class="meetings-detail-section">
          <h4 class="meetings-detail-heading">Action items</h4>
          <ul class="meetings-detail-list">
            ${actionItems.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}
          </ul>
        </section>
      `);
    }
    if (transcript) {
      sectionsHtml.push(`
        <section class="meetings-detail-section">
          <h4 class="meetings-detail-heading">Transcript</h4>
          <pre class="meetings-transcript-body">${escapeHtml(transcript)}</pre>
        </section>
      `);
    }
    if (!sectionsHtml.length) {
      sectionsHtml.push(`<div class="page-section-footnote">No transcript or notes captured for this meeting yet.</div>`);
    }

    panel.innerHTML = `
      <div class="meetings-transcript-header">
        <strong>${escapeHtml(meetingTitle || 'Meeting')}</strong>
        ${attendees ? `<div class="page-section-meta">${escapeHtml(attendees)}</div>` : ''}
      </div>
      ${sectionsHtml.join('')}
    `;
  } catch {
    panel.innerHTML = `<div class="page-section-footnote">Failed to load transcript.</div>`;
  }
}

async function handleMeetingsSearchSubmit() {
  if (!appShellContent) return;
  const input = appShellContent.querySelector('[data-meetings-search-input]');
  const query = (input?.value || '').trim();
  if (!query) return;

  const resultsEl = appShellContent.querySelector('[data-meetings-search-results]');
  if (!resultsEl) return;
  resultsEl.innerHTML = `<div class="meetings-transcript-loading">Searching…</div>`;

  try {
    const result = await searchGranolaMeetingsApi(query);
    if (!result.connected) {
      resultsEl.innerHTML = `<div class="page-section-footnote">Search unavailable: ${escapeHtml(result.reason || 'disconnected')}</div>`;
      return;
    }
    const text = result.text || '';
    const citations = result.citations || [];

    // Render citations as clickable links inline in the text
    let rendered = escapeHtml(text);
    for (const { n, url } of citations) {
      const marker = escapeHtml(`[[${n}]]`);
      const link = `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">[[${n}]]</a>`;
      rendered = rendered.split(marker).join(link);
    }

    resultsEl.innerHTML = text
      ? `<div class="meetings-search-result-text">${rendered}</div>`
      : `<div class="page-section-footnote">No results found.</div>`;
  } catch {
    resultsEl.innerHTML = `<div class="page-section-footnote">Search failed. Try again.</div>`;
  }
}

function renderMeetingsPastList(meetings) {
  if (!appShellContent) return;
  const listEl = appShellContent.querySelector('[data-meetings-past-list]');
  if (!listEl) return;

  if (!meetings || meetings.length === 0) {
    listEl.innerHTML = `<div class="page-empty-state"><p>No meetings found in the last 30 days.</p></div>`;
    return;
  }

  listEl.innerHTML = meetings.map((m) => {
    const dateStr = m.dateMs ? new Date(m.dateMs).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
    const participants = (m.participants || []).slice(0, 3).join(', ');
    return `
      <div class="page-list-row meetings-past-row" data-meeting-id="${escapeAttribute(m.id)}" data-meeting-title="${escapeAttribute(m.title || '')}">
        <span class="meetings-past-row-date">${escapeHtml(dateStr)}</span>
        <span class="meetings-past-row-title">${escapeHtml(m.title || 'Untitled meeting')}</span>
        ${participants ? `<span class="meetings-past-row-participants">${escapeHtml(participants)}</span>` : ''}
      </div>
    `;
  }).join('');
}

function renderMeetingsPastCanvas(granolaConnected) {
  return _renderMeetingsPastCanvas(granolaConnected);
}

function renderMeetingsShell(viewModel) {
  const isLive = viewModel.badge === 'Live';
  const isError = viewModel.badge === 'Source error';
  const isSetup = viewModel.badge === 'Needs setup';

  const heroCopy = isLive
    ? `Reading live ICS events from ${viewModel.sourceLabel || 'the calendar source'} for ${viewModel.dateLabel || 'today'} to build real prep, notes, and follow-up objects.`
    : isSetup
      ? 'Meetings shares the calendar source. Connect Google Calendar through the Connectors hub, or wire Granola for transcripts and notes.'
      : isError
        ? `Meetings could not build today\'s meeting objects because the calendar source at ${viewModel.sourcePath || 'the configured path'} failed to load. Fix it in Connectors.`
        : 'Meetings reads meeting posture using shell signals. Connect the calendar spine and Granola through Connectors to replace proxy signals with real prep, notes, and follow-up.';

  const statusTone = viewModel.statusTone || (isLive ? 'live' : isError ? 'error' : 'setup');

  const heroActions = isLive
    ? viewModel.actions.slice(0, 2).map((action) => `
        <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
          ${escapeHtml(action.label)}
        </button>
      `).join('')
    : `
        <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="meetings">
          ${isError ? 'Fix meetings source' : 'Connect meeting sources'}
        </button>
      `;

  const statBlock = (viewModel.summary || []).map((item) => `
    <div class="page-hero-stat">
      <div class="page-hero-stat-label">${escapeHtml(item.label)}</div>
      <div class="page-hero-stat-value">${escapeHtml(item.value)}</div>
    </div>
  `).join('');

  const granolaConnected = viewModel.granolaConnected === true;
  const granolaTodayMode = viewModel.granolaTodayMode === true;

  // Granola post-meeting mode: slim header (no lede, no hero buttons),
  // compact stats inline with the title row, and a dedicated Today canvas
  // that mirrors Past (list + transcript/actions panel).
  if (granolaTodayMode) {
    const compactStats = (viewModel.summary || [])
      .map((item) => `<span class="page-section-meta">${escapeHtml(item.label)}: <strong>${escapeHtml(item.value)}</strong></span>`)
      .join('');
    return `
      <section class="page-hero page-hero--slim">
        <div class="page-hero-titleblock">
          <div class="page-hero-eyebrow-row">
            <span class="page-hero-eyebrow">Meetings</span>
            <span class="page-hero-status" data-tone="${statusTone}">${escapeHtml(viewModel.badge || 'Live')}</span>
            ${viewModel.dateLabel ? `<span class="page-section-meta">${escapeHtml(viewModel.dateLabel)}</span>` : ''}
            ${compactStats}
          </div>
          <h1>Meetings</h1>
        </div>
      </section>
      <nav class="meetings-tab-strip">
        <button type="button" class="meetings-tab-btn active" data-shell-action="meetings-tab-switch" data-tab="today" data-meetings-tab-btn>Today</button>
        <button type="button" class="meetings-tab-btn" data-shell-action="meetings-tab-switch" data-tab="past" data-meetings-tab-btn>Past</button>
      </nav>
      <div data-meetings-canvas="today">
        ${renderMeetingsGranolaTodayCanvas(viewModel)}
      </div>
      <div data-meetings-canvas="past" class="hidden">
        ${renderMeetingsPastCanvas(granolaConnected)}
      </div>
    `;
  }

  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Meetings</span>
          <span class="page-hero-status" data-tone="${statusTone}">${escapeHtml(viewModel.badge || 'Read-only')}</span>
          ${viewModel.dateLabel ? `<span class="page-section-meta">${escapeHtml(viewModel.dateLabel)}</span>` : ''}
        </div>
        <h1>Meetings</h1>
        <p class="page-hero-lede">${escapeHtml(heroCopy)}</p>
      </div>
      <div class="page-hero-actions">${heroActions}</div>
      <div class="page-hero-stats">${statBlock}</div>
    </section>
    <nav class="meetings-tab-strip">
      <button type="button" class="meetings-tab-btn active" data-shell-action="meetings-tab-switch" data-tab="today" data-meetings-tab-btn>Today</button>
      <button type="button" class="meetings-tab-btn${granolaConnected ? '' : ' meetings-tab-btn-dim'}" data-shell-action="meetings-tab-switch" data-tab="past" data-meetings-tab-btn>Past${granolaConnected ? '' : ' <span class="meetings-tab-badge">Connect Granola</span>'}</button>
    </nav>
    <div data-meetings-canvas="today">
      ${isLive ? renderMeetingsLiveCanvas(viewModel) : renderMeetingsSetupCanvas(viewModel, { isError })}
    </div>
    <div data-meetings-canvas="past" class="hidden">
      ${renderMeetingsPastCanvas(granolaConnected)}
    </div>
  `;
}

// Today canvas for the Granola post-meeting workflow — delegated to meetings.js.
function renderMeetingsGranolaTodayCanvas(viewModel) {
  return _renderGranolaTodayCanvas(viewModel);
}

function renderMeetingsLiveCanvas(viewModel) {
  const meetings = viewModel.todayMeetings || [];

  const meetingsList = meetings.length
    ? meetings.map((m) => `
        <div class="page-list-row" data-meeting-status="${escapeAttribute(m.status || 'scheduled')}">
          <h4 class="page-list-row-title">${escapeHtml(m.title || 'Untitled meeting')}</h4>
          <span class="page-list-row-meta">${escapeHtml(m.startLabel || '')}${m.endLabel ? `–${escapeHtml(m.endLabel)}` : ''}</span>
          ${m.location ? `<p class="page-list-row-detail">${escapeHtml(m.location)}</p>` : ''}
        </div>
      `).join('')
    : `<p class="page-section-footnote">No meetings on the calendar for today.</p>`;

  const readinessList = (viewModel.readinessNotes || []).length
    ? `<ul style="margin:0;padding-left:18px;color:var(--text-dim);font-size:13px;line-height:1.55">
         ${viewModel.readinessNotes.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}
       </ul>`
    : `<p class="page-section-footnote">No readiness notes — the day looks clean.</p>`;

  const prepList = (viewModel.prepLens || []).length
    ? viewModel.prepLens.map((item) => `
        <div class="page-list-row">
          <h4 class="page-list-row-title">${escapeHtml(item.title)}</h4>
          <p class="page-list-row-detail">${escapeHtml(item.detail)}</p>
        </div>
      `).join('')
    : `<p class="page-section-footnote">No prep items extracted from upcoming events.</p>`;

  const followUpList = (viewModel.followUpPressure || []).length
    ? viewModel.followUpPressure.map((item) => `
        <div class="page-list-row">
          <h4 class="page-list-row-title">${escapeHtml(item.title)}</h4>
          <p class="page-list-row-detail">${escapeHtml(item.detail)}</p>
        </div>
      `).join('')
    : `<p class="page-section-footnote">Follow-up queue is empty.</p>`;

  // Prep Lens and Follow-up Pressure columns removed (J6c — post-meeting surface only).
  return `
    <section class="page-canvas meetings-past-canvas">
      <article class="page-section col-span-4" data-page-section="meetings-today">
        <div class="page-section-header">
          <div>
            <div class="page-section-eyebrow">Today</div>
            <h3 class="page-section-title">Meetings</h3>
          </div>
          <span class="page-section-meta">${meetings.length} total</span>
        </div>
        <div class="page-list">${meetingsList}</div>
      </article>
      <article class="page-section col-span-8" data-page-section="meetings-transcript">
        <div class="page-section-header">
          <div>
            <div class="page-section-eyebrow">Post-meeting</div>
            <h3 class="page-section-title">Select a meeting</h3>
          </div>
        </div>
        <div class="meetings-transcript-panel" data-meetings-transcript-panel>
          <div class="page-section-footnote">Click a meeting to view its action items and transcript.</div>
        </div>
      </article>
    </section>
  `;
}

function renderMeetingsSetupCanvas(viewModel, { isError }) {
  return `
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <h3>${isError ? 'Meetings source failed' : 'Connect meeting sources'}</h3>
          <p>${escapeHtml(viewModel.sourceNote || '')}</p>
          <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="meetings">
            ${isError ? 'Fix in Connectors' : 'Open Connectors'}
          </button>
        </div>
        <div class="page-section-header" style="margin-top:8px">
          <div>
            <div class="page-section-eyebrow">While disconnected</div>
            <h3 class="page-section-title">Readiness notes (manual)</h3>
          </div>
        </div>
        <div>
          <ul style="margin:0;padding-left:18px;color:var(--text-dim);font-size:13px;line-height:1.55">
            ${(viewModel.readinessNotes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join('')}
          </ul>
        </div>
        <div class="page-section-header" style="margin-top:8px">
          <div>
            <div class="page-section-eyebrow">Follow-up posture</div>
            <h3 class="page-section-title">No real meeting objects yet</h3>
          </div>
        </div>
        <div class="page-list">
          ${(viewModel.followUpPressure || []).map((item) => `
            <div class="page-list-row">
              <h4 class="page-list-row-title">${escapeHtml(item.title)}</h4>
              <p class="page-list-row-detail">${escapeHtml(item.detail)}</p>
            </div>
          `).join('')}
        </div>
        <div class="shell-actions">
          ${(viewModel.actions || []).map((action) => `
            <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
              ${escapeHtml(action.label)}
            </button>
          `).join('')}
        </div>
      </article>
    </section>
  `;
}

function renderMeetingsShellError() {
  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Meetings</span>
          <span class="page-hero-status" data-tone="error">Failed</span>
        </div>
        <h1>Meetings</h1>
        <p class="page-hero-lede">Meetings could not load. Check your network or calendar source configuration in Connectors.</p>
      </div>
      <div class="page-hero-actions">
        <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="meetings">Open Connectors</button>
      </div>
    </section>
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <h3>Unable to load Meetings</h3>
          <p>The Meetings surface encountered an error while loading. Reload to try again.</p>
        </div>
      </article>
    </section>
  `;
}

// ── Jira Board dedicated page ────────────────────────────────────────────────

function renderJiraShellLoading() {
  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Personal Workspace</span>
          <span class="page-hero-status" data-tone="setup">Loading…</span>
        </div>
        <h1>Jira Board</h1>
        <p class="page-hero-lede">Loading your board…</p>
      </div>
    </section>
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <p>Fetching board status…</p>
        </div>
      </article>
    </section>
  `;
}

function renderJiraShell(viewModel) {
  const isLive = viewModel.statusTone === 'live';

  if (isLive) {
    return renderJiraBoardCanvas(viewModel);
  }

  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Personal Workspace</span>
          <span class="page-hero-status" data-tone="${escapeAttribute(viewModel.statusTone)}">${escapeHtml(viewModel.badge)}</span>
        </div>
        <h1>Jira Board</h1>
        <p class="page-hero-lede">Connect your Atlassian account through Connectors to see real tickets, sprint state, and delivery risk.</p>
      </div>
      <div class="page-hero-actions">
        <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="jira">
          Connect Jira
        </button>
      </div>
    </section>
    ${renderJiraSetupCanvas(viewModel)}
  `;
}

function renderJiraCard(card) {
  const commentSvg = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`;
  const attachSvg = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10l-8.5 8.5a5 5 0 0 1-7-7L14 3a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 6"/></svg>`;
  const clockSvg = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`;

  const tagsHtml = card.tags.length > 0
    ? `<div class="jira-card-tags">${card.tags.map(t => `<span class="jira-card-tag">${escapeHtml(t)}</span>`).join('')}</div>`
    : '';

  const metaItems = [
    card.commentCount > 0 ? `<span class="jira-card-meta-item" title="Comments">${commentSvg}${card.commentCount}</span>` : '',
    card.attachCount > 0 ? `<span class="jira-card-meta-item" title="Attachments">${attachSvg}${card.attachCount}</span>` : '',
    card.worklogTotal > 0 ? `<span class="jira-card-meta-item" title="Logged">${clockSvg}${card.worklogTotal}h</span>` : '',
  ].filter(Boolean).join('');

  const avatarColor = _jiraAvatarColor(card.assigneeId);
  const avatarInitials = escapeHtml(_jiraInitials(card.assigneeName));

  return `
    <div class="jira-card ${escapeAttribute(card.colKey)}"
      draggable="true"
      data-card-key="${escapeAttribute(card.key)}"
      data-col-key="${escapeAttribute(card.colKey)}"
      data-assignee-id="${escapeAttribute(card.assigneeId)}"
      data-assignee-name="${escapeAttribute(card.assigneeName)}"
      data-priority="${escapeAttribute(card.prioLabel || '')}"
      data-sprint="${escapeAttribute(card.sprint || '')}">
      <div class="jira-card-head">
        <span class="jira-card-id-pill">${escapeHtml(card.key)}</span>
        ${card.prioLabel ? `<span class="jira-card-prio ${escapeAttribute(card.prioCls)}">${escapeHtml(card.prioLabel)}</span>` : ''}
      </div>
      <div class="jira-card-title">${escapeHtml(card.title)}</div>
      ${tagsHtml}
      <div class="jira-card-foot">
        <div class="jira-card-avatar" style="background:${avatarColor}" title="${escapeAttribute(card.assigneeName || 'Unassigned')}">${avatarInitials}</div>
        <div class="jira-card-meta">
          ${metaItems}
          ${card.age ? `<span class="jira-card-age">${escapeHtml(card.age)}</span>` : ''}
        </div>
      </div>
    </div>
  `;
}

function renderJiraBoardCanvas(viewModel) {
  const { swimlanes = [], stats = {}, colStatusMap = {}, assignees = [], priorities = [], sprints = [], projectKey = '', siteUrl = '' } = viewModel;
  const COL_KEYS = ['todo', 'prog', 'blocked', 'review'];
  const COL_META = {
    todo:    { label: 'TO DO',       cls: 'todo' },
    prog:    { label: 'IN PROGRESS', cls: 'prog' },
    blocked: { label: 'BLOCKED',     cls: 'blocked' },
    review:  { label: 'IN REVIEW',   cls: 'review' },
  };

  const sprintBtn = sprints.length > 0
    ? `<button class="btn btn-outline btn-sm" data-jira-filter="sprint">Sprint</button>`
    : '';

  const toolbar = `
    <div class="jira-toolbar">
      <div class="jira-toolbar-group">
        <span class="jira-chip jira-chip-prog" data-stat="prog">${stats.prog || 0} in flight</span>
        <span class="jira-chip jira-chip-blocked" data-stat="blocked">${stats.blocked || 0} blocked</span>
        <span class="jira-chip jira-chip-review" data-stat="review">${stats.review || 0} in review</span>
      </div>
      <div class="jira-toolbar-group jira-toolbar-actions">
        <button class="btn btn-outline btn-sm" data-jira-filter="people">People</button>
        <button class="btn btn-outline btn-sm" data-jira-filter="priority">Priority</button>
        ${sprintBtn}
        <button class="btn btn-ink btn-sm" data-jira-action="new-issue">+ New issue</button>
        <button type="button" class="btn btn-outline btn-sm" data-jira-action="refresh" title="Re-fetch and re-render the board">↺ Refresh</button>
        <button type="button" class="btn btn-outline btn-sm jira-manage-btn" data-shell-action="open-connectors" data-connector-scope="jira" title="Manage Connection">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/></svg>
        </button>
      </div>
    </div>
    <div class="jira-filter-drop" data-filter-drop="people" hidden>
      ${assignees.map(a => `
        <label class="jira-filter-item">
          <input type="checkbox" value="${escapeAttribute(a.id)}" data-filter-val="people" checked>
          <span>${escapeHtml(a.name)}</span>
        </label>`).join('')}
    </div>
    <div class="jira-filter-drop" data-filter-drop="priority" hidden>
      ${priorities.map(p => `
        <label class="jira-filter-item">
          <input type="checkbox" value="${escapeAttribute(p)}" data-filter-val="priority">
          <span>${escapeHtml(p)}</span>
        </label>`).join('')}
    </div>
    ${sprints.length > 0 ? `
    <div class="jira-filter-drop" data-filter-drop="sprint" hidden>
      <label class="jira-filter-item">
        <input type="radio" name="jira-sprint-filter" value="" data-filter-val="sprint" checked>
        <span>All sprints</span>
      </label>
      ${sprints.map(s => `
        <label class="jira-filter-item">
          <input type="radio" name="jira-sprint-filter" value="${escapeAttribute(s)}" data-filter-val="sprint">
          <span>${escapeHtml(s)}</span>
        </label>`).join('')}
    </div>` : ''}
  `;

  const colHeads = `
    <div class="jira-col-heads">
      <div></div>
      ${COL_KEYS.map(k => `<div class="jira-col-head ${COL_META[k].cls}">${COL_META[k].label}</div>`).join('')}
    </div>
  `;

  const rows = swimlanes.map(({ person, cells }) => {
    const cols = COL_KEYS.map(k => {
      const cards = cells[k] || [];
      const colLabel = (COL_META[k].label || k).toLowerCase();
      const emptyStyle = cards.length > 0 ? ' style="display:none"' : '';
      return `
        <div class="jira-col"
          data-col-key="${escapeAttribute(k)}"
          data-assignee-id="${escapeAttribute(person.id)}"
          data-assignee-name="${escapeAttribute(person.name)}">
          <div class="jira-col-n">${cards.length} ${colLabel}</div>
          ${cards.map(renderJiraCard).join('')}
          <div class="jira-col-empty"${emptyStyle}>Drop here</div>
        </div>
      `;
    }).join('');

    return `
      <div class="jira-swim" data-person-id="${escapeAttribute(person.id)}">
        <div class="jira-swim-label">
          <div class="jira-swim-label-inner">
            <div class="jira-swim-avatar" style="background:${person.color}">${escapeHtml(person.initials)}</div>
            <span class="jira-swim-name" title="${escapeAttribute(person.name)}">${escapeHtml(person.name)}</span>
          </div>
        </div>
        ${cols}
      </div>
    `;
  }).join('');

  if (!swimlanes.length) {
    return `
      <div class="jira-wrap" data-col-status-map="${escapeAttribute(JSON.stringify(colStatusMap))}" data-project-key="${escapeAttribute(projectKey)}" data-site-url="${escapeAttribute(siteUrl)}">
        ${toolbar}
        <div style="padding:40px 0;text-align:center;color:var(--ink-4);font-size:13px;">No issues in active columns.</div>
      </div>
    `;
  }

  return `
    <div class="jira-wrap" data-col-status-map="${escapeAttribute(JSON.stringify(colStatusMap))}" data-project-key="${escapeAttribute(projectKey)}" data-site-url="${escapeAttribute(siteUrl)}">
      ${toolbar}
      ${colHeads}
      ${rows}
    </div>
  `;
}

function renderJiraSetupCanvas(viewModel) {
  return `
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <h3>Not connected to Jira</h3>
          <p>Jira Board shows real sprint columns, ticket assignments, and delivery risk once you connect your Atlassian account. Until then this page is intentionally empty.</p>
          <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="jira">
            Open Connectors to set up Jira
          </button>
        </div>
      </article>
      <article class="page-section col-span-6">
        <div class="page-section-header">
          <h2 class="page-section-title">What you'll get</h2>
        </div>
        <ul class="page-list">
          <li class="page-list-row"><div class="page-list-row-main"><span class="page-list-row-title">Live sprint columns</span><span class="page-list-row-meta">Backlog · In Progress · Review · Done</span></div></li>
          <li class="page-list-row"><div class="page-list-row-main"><span class="page-list-row-title">Delivery risk radar</span><span class="page-list-row-meta">Blocked tickets, stale items, missed due dates</span></div></li>
          <li class="page-list-row"><div class="page-list-row-main"><span class="page-list-row-title">Execution queue</span><span class="page-list-row-meta">Your assigned open work, ranked by urgency</span></div></li>
        </ul>
      </article>
      <article class="page-section col-span-6">
        <div class="page-section-header">
          <h2 class="page-section-title">How to connect</h2>
        </div>
        <ul class="page-list">
          <li class="page-list-row"><div class="page-list-row-main"><span class="page-list-row-title">1. Open Connectors</span><span class="page-list-row-meta">Use the button above or the Connectors link in Settings</span></div></li>
          <li class="page-list-row"><div class="page-list-row-main"><span class="page-list-row-title">2. Find the Jira card</span><span class="page-list-row-meta">Enter your Atlassian site URL, email, and API token</span></div></li>
          <li class="page-list-row"><div class="page-list-row-main"><span class="page-list-row-title">3. Save and reload</span><span class="page-list-row-meta">Your board will appear here automatically</span></div></li>
        </ul>
      </article>
    </section>
  `;
}

function renderJiraShellError() {
  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Personal Workspace</span>
          <span class="page-hero-status" data-tone="error">Failed</span>
        </div>
        <h1>Jira Board</h1>
        <p class="page-hero-lede">Jira Board could not load. Check your Atlassian credentials in Connectors.</p>
      </div>
      <div class="page-hero-actions">
        <button type="button" class="shell-action-btn" data-shell-action="open-connectors" data-connector-scope="jira">Open Connectors</button>
      </div>
    </section>
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <h3>Unable to load Jira Board</h3>
          <p>The Jira Board surface encountered an error while loading. Reload to try again.</p>
        </div>
      </article>
    </section>
  `;
}

// ── Jira drag/drop wiring ────────────────────────────────────────────────────

function _wireJiraBoard(container) {
  if (_jiraWireController) _jiraWireController.abort();
  _jiraWireController = new AbortController();
  const { signal } = _jiraWireController;

  let _dragKey = null;
  let _dragColKey = null;
  let _dragAssigneeId = null;
  let _dragTransitionsPromise = null;
  let _colStatusMap = {};
  let _siteUrl = '';

  const board = container.querySelector('.jira-wrap');
  if (board) {
    if (board.dataset.colStatusMap) {
      try { _colStatusMap = JSON.parse(board.dataset.colStatusMap); } catch (_) { /* ignore */ }
    }
    _siteUrl = board.dataset.siteUrl || '';
  }

  function _updateColCount(col) {
    if (!col) return;
    const cards = col.querySelectorAll('[data-card-key]');
    const n = cards.length;
    const colN = col.querySelector('.jira-col-n');
    const labels = { todo: 'to do', prog: 'in progress', blocked: 'blocked', review: 'in review' };
    if (colN) colN.textContent = `${n} ${labels[col.dataset.colKey] || ''}`.trim();
    const empty = col.querySelector('.jira-col-empty');
    if (empty) empty.style.display = n === 0 ? '' : 'none';
  }

  container.addEventListener('dragstart', (e) => {
    const card = e.target.closest('[data-card-key]');
    if (!card) return;
    _dragKey = card.dataset.cardKey;
    _dragColKey = card.dataset.colKey;
    _dragAssigneeId = card.dataset.assigneeId || '';
    _dragTransitionsPromise = fetchJiraIssueApi(_dragKey).catch(() => null);
    card.classList.add('dragging');
    if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', _dragKey); }
  }, { signal });

  container.addEventListener('dragend', (e) => {
    const card = e.target.closest('[data-card-key]');
    if (card) card.classList.remove('dragging');
    container.querySelectorAll('.jira-col.drop-target').forEach(c => c.classList.remove('drop-target'));
    _dragKey = null;
  }, { signal });

  container.addEventListener('dragover', (e) => {
    if (!_dragKey) return;
    const col = e.target.closest('.jira-col[data-col-key]');
    if (col) e.preventDefault();
  }, { signal });

  container.addEventListener('dragenter', (e) => {
    if (!_dragKey) return;
    const col = e.target.closest('.jira-col[data-col-key]');
    if (!col) return;
    container.querySelectorAll('.jira-col.drop-target').forEach(c => c.classList.remove('drop-target'));
    col.classList.add('drop-target');
  }, { signal });

  container.addEventListener('dragleave', (e) => {
    const col = e.target.closest('.jira-col[data-col-key]');
    if (col && !col.contains(e.relatedTarget)) col.classList.remove('drop-target');
  }, { signal });

  container.addEventListener('drop', async (e) => {
    const col = e.target.closest('.jira-col[data-col-key]');
    if (!col || !_dragKey) return;
    e.preventDefault();
    col.classList.remove('drop-target');

    const targetColKey = col.dataset.colKey;
    const targetAssigneeId = col.dataset.assigneeId || '';
    const key = _dragKey;
    const origColKey = _dragColKey;
    const origAssigneeId = _dragAssigneeId;
    const colChanged = targetColKey !== origColKey;
    const assigneeChanged = targetAssigneeId !== origAssigneeId;
    if (!colChanged && !assigneeChanged) return;

    // Optimistic move
    const card = container.querySelector(`[data-card-key="${CSS.escape(key)}"]`);
    const origCol = card ? card.closest('.jira-col[data-col-key]') : null;
    if (card) {
      card.dataset.colKey = targetColKey;
      card.dataset.assigneeId = targetAssigneeId;
      card.className = `jira-card ${targetColKey}`;
      const emptyEl = col.querySelector('.jira-col-empty');
      col.insertBefore(card, emptyEl || null);
      _updateColCount(origCol);
      _updateColCount(col);
    }

    try {
      const calls = [];
      if (colChanged) {
        const detail = await _dragTransitionsPromise;
        const transitions = detail?.transitions || [];
        const targetStatuses = _colStatusMap[targetColKey] || [];
        const transition = transitions.find(t => targetStatuses.includes(t.to));
        if (!transition) throw new Error(`No available transition to ${targetColKey} (statuses: ${targetStatuses.join(', ')})`);
        calls.push(transitionJiraIssueApi(key, transition.id));
      }
      if (assigneeChanged) {
        calls.push(changeJiraAssigneeApi(key, targetAssigneeId || null));
      }
      await Promise.all(calls);
      // Optimistic update is already applied; do not refetch here — an
      // immediate loadJiraShell() would race the Jira status-propagation
      // delay and re-render the board with stale data, reverting the card.
    } catch (err) {
      console.error('Jira drag/drop failed, rolling back:', err);
      // Roll back optimistic move synchronously, then re-render from server
      if (card && card.isConnected) {
        card.dataset.colKey = origColKey;
        card.dataset.assigneeId = origAssigneeId;
        card.className = `jira-card ${origColKey}`;
        if (origCol && origCol.isConnected && card.parentNode !== origCol) {
          try {
            const origEmpty = origCol.querySelector('.jira-col-empty');
            origCol.insertBefore(card, origEmpty || null);
          } catch (_) { /* DOM moved on; let the refetch handle it */ }
          _updateColCount(origCol);
          if (col && col !== origCol) _updateColCount(col);
        }
      }
      loadJiraShell();
    }
  }, { signal });

  // Card click → open detail drawer
  let _drawer = document.querySelector('artemis-jira-card-drawer');
  if (!_drawer) {
    _drawer = document.createElement('artemis-jira-card-drawer');
    document.body.appendChild(_drawer);
    _drawer.addEventListener('jira-drawer-close', () => loadJiraShell());
  }

  // New-issue modal
  let _modal = document.querySelector('artemis-jira-new-issue-modal');
  if (!_modal) {
    _modal = document.createElement('artemis-jira-new-issue-modal');
    document.body.appendChild(_modal);
    _modal.addEventListener('jira-issue-created', (e) => {
      loadJiraShell();
      const key = e.detail?.key;
      if (key) {
        // Brief delay so the board re-renders before opening the drawer
        setTimeout(() => _drawer.open(key, _colStatusMap, null, _siteUrl), 400);
      }
    });
  }

  // ── Filter logic ────────────────────────────────────────────────────────────

  const SWIMLANE_FILTER_KEY = 'artemis.jira.swimlane.filter';
  // IDs of swimlane rows to hide; empty = show all (default)
  let _swimlaneHideFilter = new Set();
  let _priorityFilter = new Set();
  let _sprintFilter = '';

  // Restore people filter from localStorage (unchecks saved hidden IDs)
  try {
    const stored = localStorage.getItem(SWIMLANE_FILTER_KEY);
    if (stored) {
      const hiddenIds = JSON.parse(stored);
      if (Array.isArray(hiddenIds)) {
        _swimlaneHideFilter = new Set(hiddenIds);
        hiddenIds.forEach(id => {
          const cb = container.querySelector(`[data-filter-val="people"][value="${CSS.escape(id)}"]`);
          if (cb) cb.checked = false;
        });
      }
    }
  } catch (_) { /* ignore corrupt storage */ }

  function _closeAllDropdowns(except) {
    container.querySelectorAll('.jira-filter-drop:not([hidden])').forEach(d => {
      if (d !== except) d.hidden = true;
    });
  }

  function _recount() {
    const chips = { prog: 0, blocked: 0, review: 0 };
    container.querySelectorAll('[data-card-key]:not(.jira-hidden)').forEach(card => {
      const swimlane = card.closest('[data-person-id]');
      if (swimlane?.classList.contains('jira-hidden')) return;
      const col = card.closest('[data-col-key]');
      if (!col) return;
      const key = col.dataset.colKey;
      if (chips[key] !== undefined) chips[key]++;
    });
    for (const [k, n] of Object.entries(chips)) {
      const chip = container.querySelector(`[data-stat="${k}"]`);
      const labels = { prog: 'in flight', blocked: 'blocked', review: 'in review' };
      if (chip) chip.textContent = `${n} ${labels[k]}`;
    }
  }

  function _applyFilters() {
    // People filter: show/hide entire swimlane rows
    container.querySelectorAll('[data-person-id]').forEach(row => {
      row.classList.toggle('jira-hidden', _swimlaneHideFilter.has(row.dataset.personId));
    });

    // Priority + sprint filter: show/hide individual cards
    container.querySelectorAll('[data-card-key]').forEach(card => {
      const prioMatch = _priorityFilter.size === 0 || _priorityFilter.has(card.dataset.priority || '');
      const sprintMatch = !_sprintFilter || card.dataset.sprint === _sprintFilter;
      card.classList.toggle('jira-hidden', !prioMatch || !sprintMatch);
    });

    // Recompute empty-cell placeholders
    container.querySelectorAll('[data-col-key]').forEach(col => {
      const visible = col.querySelectorAll('[data-card-key]:not(.jira-hidden)').length;
      const empty = col.querySelector('.jira-col-empty');
      if (empty) empty.style.display = visible === 0 ? '' : 'none';
    });

    _recount();
    _updateFilterButtonLabels();
  }

  function _updateFilterButtonLabels() {
    const peopleBtn = container.querySelector('[data-jira-filter="people"]');
    if (peopleBtn) {
      const hidden = _swimlaneHideFilter.size;
      peopleBtn.textContent = hidden > 0 ? `People (-${hidden})` : 'People';
    }
    const prioBtn = container.querySelector('[data-jira-filter="priority"]');
    if (prioBtn) prioBtn.textContent = _priorityFilter.size > 0 ? `Priority (${_priorityFilter.size})` : 'Priority';
    const sprintBtn = container.querySelector('[data-jira-filter="sprint"]');
    if (sprintBtn) sprintBtn.textContent = _sprintFilter ? _sprintFilter : 'Sprint';
  }

  // Apply persisted swimlane filter on initial render
  if (_swimlaneHideFilter.size > 0) _applyFilters();

  // Position a dropdown panel below its trigger button.
  // Must be relative to .jira-wrap (position:relative), which is the containing block.
  function _positionDrop(btn, drop) {
    const wrap = container.querySelector('.jira-wrap') || container;
    const btnRect = btn.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    drop.style.top = `${btnRect.bottom - wrapRect.top + wrap.scrollTop + 4}px`;
    drop.style.left = `${btnRect.left - wrapRect.left}px`;
  }

  container.addEventListener('click', (e) => {
    if (_dragKey) return;

    // Filter button toggle
    const filterBtn = e.target.closest('[data-jira-filter]');
    if (filterBtn) {
      const which = filterBtn.dataset.jiraFilter;
      const drop = container.querySelector(`[data-filter-drop="${which}"]`);
      if (!drop) return;
      const opening = drop.hidden;
      _closeAllDropdowns(opening ? drop : null);
      if (opening) {
        drop.hidden = false;
        _positionDrop(filterBtn, drop);
      } else {
        drop.hidden = true;
      }
      return;
    }

    // Close dropdowns on outside click
    if (!e.target.closest('[data-filter-drop]')) {
      _closeAllDropdowns(null);
    }

    // Refresh board button
    if (e.target.closest('[data-jira-action="refresh"]')) {
      loadJiraShell();
      return;
    }

    // New issue button
    if (e.target.closest('[data-jira-action="new-issue"]')) {
      const board = container.querySelector('.jira-wrap');
      const projectKey = board?.dataset.projectKey || '';
      _modal.open(projectKey, _colStatusMap);
      return;
    }

    // Card click → drawer
    const card = e.target.closest('[data-card-key]');
    if (!card) return;
    _drawer.open(card.dataset.cardKey, _colStatusMap, null, _siteUrl);
  }, { signal });

  // Filter checkbox / radio changes
  container.addEventListener('change', (e) => {
    const input = e.target.closest('[data-filter-val]');
    if (!input) return;
    const kind = input.dataset.filterVal;
    if (kind === 'people') {
      if (input.checked) _swimlaneHideFilter.delete(input.value);
      else _swimlaneHideFilter.add(input.value);
      try {
        localStorage.setItem(SWIMLANE_FILTER_KEY, JSON.stringify([..._swimlaneHideFilter]));
      } catch (_) { /* storage full */ }
    } else if (kind === 'priority') {
      if (input.checked) _priorityFilter.add(input.value);
      else _priorityFilter.delete(input.value);
    } else if (kind === 'sprint') {
      _sprintFilter = input.value;
    }
    _applyFilters();
  }, { signal });
}

// ── OKR Studio dedicated page ────────────────────────────────────────────────

function renderOkrShellLoading() {
  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Personal Workspace</span>
          <span class="page-hero-status" data-tone="setup">Loading…</span>
        </div>
        <h1>OKR Studio</h1>
        <p class="page-hero-lede">Loading your OKRs…</p>
      </div>
    </section>
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <p>Fetching OKR data…</p>
        </div>
      </article>
    </section>
  `;
}

function renderOkrShell(overview) {
  const { objectives = [], stats = [], activity = [], evidence = [], nextUp = [], quarter = {} } = overview;
  const totalKrs = objectives.reduce((sum, obj) => sum + (obj.krs || []).length, 0);
  const evidenceCount = hydrateOkrEvidenceFromActivity(objectives, activity, evidence);

  return `
    <div class="okr-top-stats">
      ${stats.map((s) => `
        <div class="okr-stat">
          <span class="okr-stat-n ${escapeAttribute(s.tone || '')} ${s.n === 0 ? 'zero' : ''}">${escapeHtml(String(s.n))}${escapeHtml(s.suffix || '')}</span>
          <span class="okr-stat-l">${escapeHtml(s.label)}</span>
        </div>
      `).join('')}
    </div>

    <div class="okr-body-grid">
      <div>
        <div class="okr-workspace-toolbar">
          <div class="okr-workspace-meta">
            <span>${escapeHtml(quarter.label || 'Q2 2026')}</span>
            <span>${objectives.length} objectives</span>
            <span>${totalKrs} KRs</span>
            <span>${evidenceCount} accomplishments</span>
          </div>
          <div class="okr-workspace-actions">
            <div class="okr-actions-menu">
              <button type="button" class="okr-actions-trigger" data-okr-actions-toggle aria-expanded="false">Actions</button>
              <div class="okr-actions-popover" data-okr-actions-popover hidden>
                <button type="button" data-shell-action="open-chat-from-shell" data-shell-intent="Help me write a personal retro based on my OKR progress this quarter.">Personal retro</button>
                <button type="button" data-okr-eoy-review>EOY review</button>
                <button type="button" data-okr-update>Update OKRs</button>
                <button type="button" data-okr-generate-deck>Generate deck</button>
              </div>
            </div>
            <button type="button" class="okr-archived-toggle btn btn-outline btn-xs" data-okr-show-archived="false">Show archived</button>
          </div>
        </div>
        ${renderOkrSplitView(objectives)}
        <div class="okr-archived-section" hidden></div>
      </div>

      <div class="okr-right-col">
        ${renderOkrActivityCard(activity)}
        ${renderOkrNextUpCard(nextUp)}
      </div>
    </div>
  `;
}

function hydrateOkrEvidenceFromActivity(objectives = [], activity = [], evidence = []) {
  const entriesByKr = new Map();
  const addEntry = (krId, entry) => {
    const id = Number(krId);
    if (!Number.isFinite(id)) return;
    const entries = entriesByKr.get(id) || [];
    if (!entries.some((item) => String(item.id) === String(entry.id))) {
      entries.push(entry);
      entriesByKr.set(id, entries);
    }
  };

  for (const group of evidence || []) {
    for (const entry of group.entries || []) addEntry(group.krId ?? entry.kr_id, entry);
  }
  for (const entry of activity || []) {
    if (entry.kr_id) addEntry(entry.kr_id, entry);
  }

  let count = 0;
  for (const obj of objectives || []) {
    for (const kr of obj.krs || []) {
      const entries = entriesByKr.get(Number(kr.id)) || [];
      if (!entries.length) {
        kr.evidence = kr.evidence || [];
        kr.evidence_count = Number(kr.evidence_count || 0);
        continue;
      }
      kr.evidence = entries.slice(0, 5);
      kr.evidence_count = entries.length;
      kr.latest_evidence = entries[0]?.text || kr.latest_evidence || null;
      count += entries.length;
    }
  }
  return count;
}

function renderOkrSplitView(objectives = []) {
  const preferred = findPreferredSelection(objectives);
  const firstKrId = preferred.kr?.id ?? null;
  const firstObjectiveId = preferred.objective?.id ?? objectives[0]?.id ?? null;
  if (!firstKrId) {
    return `
      <div class="okr-empty-board">
        ${objectives.length
          ? objectives.map((obj) => `<div class="okr-empty-objective"><strong>${escapeHtml(obj.title)}</strong><span>No active KRs found.</span></div>`).join('')
          : '<p>No objectives yet. Add your first objective to start tracking progress.</p>'}
      </div>`;
  }
  return `
    <div class="okr-split">
      <div class="okr-navigator" role="listbox" aria-label="Objective and KR navigator">
        <div class="okr-objective-tabs" role="tablist" aria-label="Objectives">
          ${objectives.map((obj) => {
            const krs = obj.krs || [];
            const isActive = Number(obj.id) === Number(firstObjectiveId);
            const firstKr = findPreferredKr([obj])?.id ?? '';
            const evidenceCount = krs.reduce((sum, kr) => sum + Number(kr.evidence_count || 0), 0);
            return `
              <button type="button" class="okr-objective-tab ${isActive ? 'active' : ''}" data-okr-objective-tab="${obj.id}" data-first-kr="${firstKr}">
                <span class="okr-objective-tab-title">${escapeHtml(obj.title)}</span>
                <span class="okr-objective-tab-meta">
                  <strong>${obj.progress}%</strong>
                  <small>${krs.length} KRs · ${evidenceCount} ev</small>
                </span>
                <span class="okr-objective-tab-track"><i style="width:${obj.progress}%"></i></span>
              </button>`;
          }).join('')}
        </div>
        ${objectives.map((obj) => renderOkrNavigatorObjective(obj, firstKrId, firstObjectiveId)).join('')}
      </div>
      <div class="okr-detail-stack">
        ${objectives.flatMap((obj) => (obj.krs || []).map((kr) => renderOkrDetailPanel(obj, kr, firstKrId))).join('')}
      </div>
    </div>
  `;
}

function findPreferredKr(objectives = []) {
  const krs = objectives.flatMap((obj) => obj.krs || []);
  return krs.find((kr) => Number(kr.evidence_count || 0) > 0) || krs[0] || null;
}

function findPreferredSelection(objectives = []) {
  for (const objective of objectives) {
    const kr = (objective.krs || []).find((item) => Number(item.evidence_count || 0) > 0);
    if (kr) return { objective, kr };
  }
  const objective = objectives.find((obj) => (obj.krs || []).length) || objectives[0] || null;
  return { objective, kr: objective?.krs?.[0] || null };
}

function renderOkrNavigatorObjective(obj, selectedKrId, selectedObjectiveId) {
  const statusLabels = { done: 'Done', ontrack: 'On track', atrisk: 'At risk', notstarted: 'Not started' };
  const dotClass = { done: 'done', ontrack: '', atrisk: 'warn', notstarted: 'zero' };
  const krs = obj.krs || [];
  const doneCount = krs.filter((kr) => kr.status === 'done').length;
  const atRiskCount = krs.filter((kr) => kr.status === 'atrisk').length;
  const evidenceCount = krs.reduce((sum, kr) => sum + Number(kr.evidence_count || 0), 0);

  return `
    <div class="okr-nav-panel ${Number(obj.id) === Number(selectedObjectiveId) ? 'active' : ''}" data-okr-objective-panel="${obj.id}">
      <div class="okr-nav-summary">
        <span>${doneCount}/${krs.length} done</span>
        <span>${atRiskCount} at risk</span>
        <span>${evidenceCount} evidence</span>
      </div>
      <div class="okr-krs">
        ${krs.map((kr) => {
          const dc = dotClass[kr.status] || '';
          const sl = statusLabels[kr.status] || kr.status;
          return `
            <button type="button" class="okr-kr ${Number(kr.id) === Number(selectedKrId) ? 'selected' : ''}" data-kr-id="${kr.id}" data-kr-title="${escapeAttribute(kr.title)}" data-okr-select-kr="${kr.id}">
              <div class="okr-kr-row">
                <span class="okr-kr-dot ${escapeAttribute(dc)}"></span>
                <span class="okr-kr-title">
                  <span>${escapeHtml(kr.title)}</span>
                  ${kr.latest_evidence ? `<small>${escapeHtml(kr.latest_evidence)}</small>` : ''}
                </span>
                <span class="okr-kr-evidence-chip">${Number(kr.evidence_count || 0)} ev</span>
                <span class="okr-kr-status-pill ${escapeAttribute(kr.status)}">${escapeHtml(sl)}</span>
                <span class="okr-kr-prog">${kr.prog}%</span>
              </div>
            </button>`;
        }).join('')}
      </div>
    </div>
  `;
}

function renderOkrDetailPanel(obj, kr, selectedKrId) {
  const statusLabels = { done: 'Done', ontrack: 'On track', atrisk: 'At risk', notstarted: 'Not started' };
  const dotClass = { done: 'done', ontrack: '', atrisk: 'warn', notstarted: 'zero' };
  const dc = dotClass[kr.status] || '';
  const sl = statusLabels[kr.status] || kr.status;
  const doneBullets = (kr.done || []).map((b) => `<li>${escapeHtml(b)}</li>`).join('');
  const gapsBullets = (kr.gaps || []).map((b) => `<li>${escapeHtml(b)}</li>`).join('');
  const evidenceItems = (kr.evidence || []).map((entry) => `
    <li>
      <span>${escapeHtml(entry.text)}</span>
      <small>${escapeHtml(entry.when || '')}${entry.mapping_confidence !== null && entry.mapping_confidence !== undefined ? ` · ${Math.round(Number(entry.mapping_confidence) * 100)}% match` : ''}</small>
    </li>`).join('');
  const isDone = kr.status === 'done';

  return `
    <section class="okr-detail-panel ${Number(kr.id) === Number(selectedKrId) ? 'active' : ''}" data-okr-detail-panel="${kr.id}" data-kr-id="${kr.id}" data-kr-title="${escapeAttribute(kr.title)}">
      <div class="okr-detail-head">
        <div>
          <div class="okr-detail-objective">${escapeHtml(obj.title)}</div>
          <h2>${escapeHtml(kr.title)}</h2>
        </div>
        <div class="okr-detail-status">
          <span class="okr-kr-status-pill ${escapeAttribute(kr.status)}">${escapeHtml(sl)}</span>
          <span class="okr-kr-prog">${kr.prog}%</span>
        </div>
      </div>
      <div class="okr-kr-prog-track"><div class="okr-kr-prog-fill ${escapeAttribute(dc)}" style="width:${kr.prog}%"></div></div>
      <div class="okr-kr-body">
        <div class="okr-kr-panel okr-kr-panel-target">
          <div class="okr-kr-section-label okr-kr-target-label">
            <span>KR target</span>
            <button type="button" class="okr-icon-btn" data-okr-edit-target="${kr.id}" title="Edit KR target" aria-label="Edit KR target">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.2 5.2 18.8 8.8M4 20l4.1-.8L19.4 7.9a2.5 2.5 0 0 0-3.5-3.5L4.6 15.7 4 20Z"/></svg>
            </button>
          </div>
          <div data-okr-target-display>
            <p data-okr-target-text>${escapeHtml(kr.target_text || 'No KR target defined.')}</p>
          </div>
          <div class="okr-kr-target-editor" data-okr-target-editor hidden>
            <textarea class="okr-kr-target-input" rows="4" data-okr-target-input="${kr.id}">${escapeHtml(kr.target_text || '')}</textarea>
            <div class="okr-kr-target-actions">
              <button type="button" class="btn btn-outline btn-sm" data-okr-cancel-target="${kr.id}">Cancel</button>
              <span>Use Save below to keep changes.</span>
            </div>
          </div>
        </div>
        <div class="okr-kr-panel okr-kr-panel-evidence">
          <div class="okr-kr-section-label done">Completed</div>
          ${evidenceItems ? `<ul class="okr-kr-evidence-list">${evidenceItems}</ul>` : '<div class="okr-kr-empty">No accomplishments mapped yet.</div>'}
        </div>
        ${isDone ? '' : `<div class="okr-kr-panel gaps">
          <div class="okr-kr-section-label gaps">Still to do</div>
          ${gapsBullets ? `<ul class="okr-kr-bullets">${gapsBullets}</ul>` : '<div class="okr-kr-empty">Nothing outstanding — closed and verified.</div>'}
        </div>`}
        ${kr.note ? `<div class="okr-kr-note-display">${escapeHtml(kr.note)}</div>` : ''}
        <div class="okr-kr-edit">
          <select class="okr-kr-status-select" data-okr-status-select="${kr.id}">
            <option value="done"       ${kr.status === 'done'       ? 'selected' : ''}>Done</option>
            <option value="ontrack"    ${kr.status === 'ontrack'    ? 'selected' : ''}>On track</option>
            <option value="atrisk"     ${kr.status === 'atrisk'     ? 'selected' : ''}>At risk</option>
            <option value="notstarted" ${kr.status === 'notstarted' ? 'selected' : ''}>Not started</option>
          </select>
          <div class="okr-kr-prog-edit">
            <input type="number" min="0" max="100" value="${kr.prog}"
                   class="okr-kr-prog-input" data-okr-prog-input="${kr.id}" />
            <span class="okr-kr-prog-unit">%</span>
            <button type="button" class="btn btn-outline btn-sm" data-okr-suggest-prog="${kr.id}">Suggest</button>
          </div>
          <span class="okr-kr-edit-spacer"></span>
          <button type="button" class="btn btn-amber btn-sm" data-okr-save-kr="${kr.id}">Save</button>
          <textarea class="okr-kr-note-input" rows="2" placeholder="Notes…" data-okr-note-input="${kr.id}">${escapeHtml(kr.note || '')}</textarea>
        </div>
      </div>
    </section>
  `;
}

function renderArchivedObjectives(groups) {
  if (!groups.length) {
    return `<div class="okr-archived-empty">No archived objectives found.</div>`;
  }
  return groups.map(({ year, objectives }) => `
    <div class="okr-archived-year">
      <div class="okr-archived-year-label">Archived — ${escapeHtml(String(year))}</div>
      ${objectives.map((obj) => `
        <div class="okr-archived-card">
          <div class="okr-archived-card-head">
            <span class="okr-archived-card-title">${escapeHtml(obj.title)}</span>
            <span class="okr-archived-card-prog">${escapeHtml(String(obj.progress ?? 0))}%</span>
          </div>
          <div class="okr-archived-krs">
            ${(obj.krs || []).map((kr) => {
              const done = kr.done || [];
              return `
                <div class="okr-archived-kr">
                  <div class="okr-archived-kr-title">${escapeHtml(kr.title)}</div>
                  ${done.length ? `
                    <ul class="okr-archived-kr-done">
                      ${done.map((b) => `<li>${escapeHtml(b)}</li>`).join('')}
                    </ul>` : ''}
                </div>`;
            }).join('')}
          </div>
        </div>`).join('')}
    </div>`).join('');
}

function renderOkrActivityCard(activity) {
  const entries = (activity || []).map((e) => `
    <div class="okr-activity-feed-entry">
      <div>${escapeHtml(e.text)}</div>
      <div class="okr-activity-feed-meta">
        ${escapeHtml(e.when || '')} · <span class="okr-activity-feed-kr">${escapeHtml(e.kr_label || 'Unmapped')}</span>
      </div>
    </div>`).join('');

  return `
    <div class="okr-side-card accent">
      <div class="okr-side-card-label">
        <span>⎔ Log activity</span>
        <span style="color:var(--ink-4)">⌘ + Enter</span>
      </div>
      <div class="okr-activity-input">
        <input data-okr-activity-input placeholder="What did you ship, learn, or unblock?" />
        <button type="button" class="btn btn-amber btn-sm" data-okr-activity-submit>→</button>
      </div>
      <div class="okr-activity-hint">
        Quick entries land as evidence. Paste capture lets you review wording and KR mapping before saving.
      </div>
      <div class="okr-activity-divider">Recent activity</div>
      <div class="okr-activity-feed" data-okr-activity-feed>
        ${entries || '<div class="hint">No activity yet — log something above.</div>'}
        <div class="hint">Activity older than 14 days rolls up into the narrative draft.</div>
      </div>
      <div class="okr-activity-divider">Quick capture</div>
      <div class="okr-activity-row">
        <button type="button" class="btn btn-outline btn-sm" data-okr-paste-capture>Paste text</button>
      </div>
    </div>
  `;
}

function renderOkrEvidenceCard(evidence, objectives = []) {
  const groups = Array.isArray(evidence) ? evidence : [];
  const rows = groups.slice(0, 6).map((group) => {
    const entries = (group.entries || []).slice(0, 4).map((entry) => `
      <div class="okr-evidence-entry" data-okr-evidence-entry="${entry.id}">
        <div class="okr-evidence-text">${escapeHtml(entry.text)}</div>
        <div class="okr-evidence-meta">
          <span>${escapeHtml(entry.when || '')}</span>
          ${entry.mapping_confidence !== null && entry.mapping_confidence !== undefined
            ? `<span>${Math.round(Number(entry.mapping_confidence) * 100)}% match</span>`
            : ''}
        </div>
        <select class="okr-evidence-select" data-okr-evidence-select="${entry.id}">
          ${renderOkrKrOptions(objectives, entry.kr_id)}
        </select>
      </div>`).join('');
    return `
      <div class="okr-evidence-group">
        <div class="okr-evidence-group-head">
          <span>${escapeHtml(group.krTitle || 'Unmapped')}</span>
          <span>${(group.entries || []).length}</span>
        </div>
        ${entries}
      </div>`;
  }).join('');

  return `
    <div class="okr-side-card">
      <div class="okr-side-card-label">
        <span>Accomplishments</span>
        <span style="color:var(--ink-4)">${groups.reduce((sum, group) => sum + (group.entries || []).length, 0)}</span>
      </div>
      <div class="okr-evidence-list">
        ${rows || '<div class="okr-next-empty">No cleaned accomplishments yet.</div>'}
      </div>
    </div>
  `;
}

function renderOkrKrOptions(objectives, selectedId) {
  const options = ['<option value="">Unmapped</option>'];
  for (const obj of objectives || []) {
    for (const kr of obj.krs || []) {
      options.push(`<option value="${escapeAttribute(String(kr.id))}" ${Number(selectedId) === Number(kr.id) ? 'selected' : ''}>${escapeHtml(kr.title)}</option>`);
    }
  }
  return options.join('');
}

function renderOkrNextUpItems(nextUp) {
  const prioColor = { high: 'var(--danger)', med: 'var(--amber-ink)', low: 'var(--ink-4)' };
  return (nextUp || []).map((n) => {
    const isDispatchable = n.action_type === 'dispatchable';
    const isAgent = n.source === 'agent';
    const rationaleAttr = n.rationale ? ` title="${escapeHtml(n.rationale)}"` : '';
    const sourceBadge = isAgent ? `<span class="okr-next-source-badge">AI</span>` : '';
    const dispatchBtn = isDispatchable
      ? `<button
          class="okr-next-dispatch-btn"
          type="button"
          data-okr-dispatch-next-up="${escapeHtml(String(n.id ?? ''))}"
          data-okr-dispatch-text="${escapeHtml(n.text || '')}"
          data-okr-dispatch-target="${escapeHtml(n.dispatch_target || '')}"
          data-okr-dispatch-params="${escapeHtml(encodeURIComponent(JSON.stringify(n.dispatch_params || null)))}"
          data-okr-dispatch-rationale="${escapeHtml(n.rationale || '')}"
          title="Dispatch via Artemis"
        >→ Artemis</button>`
      : '';
    const prioMeta = n.prio
      ? `<span class="prio" style="color:${prioColor[n.prio] || 'var(--ink-4)'}">${escapeHtml(n.prio)}</span>`
      : '';
    const refMeta = n.ref && n.ref !== '—' ? `<span class="ref">${escapeHtml(n.ref)}</span>` : '';
    return `
    <div class="okr-next-item">
      <span class="okr-next-icon">✓</span>
      <div>
        <div class="okr-next-text"${rationaleAttr}>${escapeHtml(n.text)}</div>
        <div class="okr-next-meta">
          ${refMeta}${prioMeta}${sourceBadge}${dispatchBtn}
        </div>
      </div>
      <span class="okr-next-close" data-okr-dismiss-next-up="${n.id}" title="Dismiss">×</span>
    </div>`;
  }).join('');
}

function renderOkrNextUpCard(nextUp) {
  const items = renderOkrNextUpItems(nextUp);
  return `
    <div class="okr-side-card">
      <div class="okr-side-card-label">
        <span>⟶ Next up for Jon</span>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="okr-next-count" style="color:var(--ink-4)">${(nextUp || []).length}</span>
          <button class="okr-next-regen-btn" data-okr-regen-next-up title="Regenerate with OKR Advisor">↺</button>
        </div>
      </div>
      <div class="okr-next-list">
        ${items || '<div class="okr-next-empty">Nothing queued.</div>'}
      </div>
    </div>
  `;
}

// ── Paste-capture modal ───────────────────────────────────────

function _openPasteCaptureModal(activityFeed, krOptions = []) {
  // Remove any existing modal
  document.getElementById('okr-paste-modal')?.remove();

  const modal = document.createElement('div');
  modal.id = 'okr-paste-modal';
  modal.className = 'okr-modal-backdrop';
  modal.innerHTML = `
    <div class="okr-modal" role="dialog" aria-modal="true" aria-label="Paste text to extract activities">
      <div class="okr-modal-head">
        <span class="okr-modal-title">Paste / import text</span>
        <button type="button" class="okr-modal-close" aria-label="Close">×</button>
      </div>
      <div class="okr-modal-body" data-paste-step="input">
        <textarea class="okr-paste-textarea" placeholder="Paste anything — meeting notes, a status update, a Slack export. Artemis extracts the work you did." rows="10"></textarea>
        <div class="okr-paste-hint">Artemis fixes spelling and grammar only, then suggests a KR match. Review before logging.</div>
      </div>
      <div class="okr-modal-foot">
        <button type="button" class="btn btn-amber btn-sm" data-paste-extract>Extract activities</button>
        <button type="button" class="btn btn-outline btn-sm" data-paste-cancel>Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const closeModal = () => modal.remove();

  modal.querySelector('[data-paste-cancel]').addEventListener('click', closeModal);
  modal.querySelector('.okr-modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

  const extractBtn = modal.querySelector('[data-paste-extract]');
  extractBtn.addEventListener('click', async () => {
    const textarea = modal.querySelector('.okr-paste-textarea');
    const raw = textarea?.value?.trim();
    if (!raw) return;

    extractBtn.textContent = 'Extracting…';
    extractBtn.disabled = true;

    let entries;
    try {
      const result = await extractOkrActivitiesApi(raw);
      entries = result.entries || [];
    } catch {
      extractBtn.textContent = 'Extract activities';
      extractBtn.disabled = false;
      const hint = modal.querySelector('.okr-paste-hint');
      if (hint) hint.textContent = 'Extraction failed. Please try again.';
      return;
    }

    if (!entries.length) {
      extractBtn.textContent = 'Extract activities';
      extractBtn.disabled = false;
      const hint = modal.querySelector('.okr-paste-hint');
      if (hint) hint.textContent = 'No extractable activities found. Try pasting more detail.';
      return;
    }

    // Shift to review step
    const body = modal.querySelector('.okr-modal-body');
    body.dataset.pasteStep = 'review';
    body.innerHTML = `
      <div class="okr-paste-review-label">Review extracted activities — uncheck any you don't want logged:</div>
      <div class="okr-paste-checklist">
        ${entries.map((e, i) => `
          <label class="okr-paste-check-row">
            <input type="checkbox" checked data-entry-idx="${i}" />
            <div class="okr-paste-check-text">
              <textarea class="okr-paste-check-body" rows="2" data-entry-text="${i}">${escapeHtml(e.text)}</textarea>
              <select class="okr-paste-kr-select" data-entry-kr="${i}">
                ${renderPasteKrOptions(krOptions, e.kr_id)}
              </select>
              <span class="okr-paste-check-kr">${escapeHtml(e.kr_label || 'Unmapped')}${e.mapping_confidence !== null && e.mapping_confidence !== undefined ? ` · ${Math.round(Number(e.mapping_confidence) * 100)}% match` : ''}</span>
            </div>
          </label>`).join('')}
      </div>`;

    const foot = modal.querySelector('.okr-modal-foot');
    foot.innerHTML = `
      <button type="button" class="btn btn-amber btn-sm" data-paste-confirm>Log checked entries</button>
      <button type="button" class="btn btn-outline btn-sm" data-paste-cancel>Cancel</button>`;

    foot.querySelector('[data-paste-cancel]').addEventListener('click', closeModal);
    foot.querySelector('[data-paste-confirm]').addEventListener('click', async () => {
      const checked = [...body.querySelectorAll('input[type=checkbox]:checked')]
        .map((cb) => {
          const idx = Number(cb.dataset.entryIdx);
          const entry = entries[idx];
          if (!entry) return null;
          const text = body.querySelector(`[data-entry-text="${idx}"]`)?.value?.trim() || entry.text;
          const krValue = body.querySelector(`[data-entry-kr="${idx}"]`)?.value || '';
          const kr = krOptions.find((option) => Number(option.id) === Number(krValue));
          return {
            ...entry,
            text,
            kr_id: kr ? kr.id : null,
            kr_label: kr ? kr.title : 'Unmapped',
          };
        })
        .filter(Boolean);
      if (!checked.length) { closeModal(); return; }

      const confirmBtn = foot.querySelector('[data-paste-confirm]');
      confirmBtn.textContent = 'Logging…';
      confirmBtn.disabled = true;

      try {
        const result = await bulkLogOkrActivitiesApi(checked);
        if (activityFeed && result.entries?.length) {
          for (const entry of [...result.entries].reverse()) {
            const el = document.createElement('div');
            el.className = 'okr-activity-feed-entry';
            el.innerHTML = `
              <div>${escapeHtml(entry.text)}</div>
              <div class="okr-activity-feed-meta">Just now · <span class="okr-activity-feed-kr">${escapeHtml(entry.kr_label || 'Unmapped')}</span></div>`;
            activityFeed.insertBefore(el, activityFeed.firstChild);
          }
        }
        closeModal();
        // Simple toast
        _showOkrToast(`Logged ${result.logged} activit${result.logged === 1 ? 'y' : 'ies'} from pasted text`);
      } catch {
        confirmBtn.textContent = 'Log checked entries';
        confirmBtn.disabled = false;
      }
    });
  });
}

function renderPasteKrOptions(krOptions, selectedId) {
  return [
    `<option value="">Unmapped</option>`,
    ...krOptions.map((kr) => `<option value="${escapeAttribute(String(kr.id))}" ${Number(selectedId) === Number(kr.id) ? 'selected' : ''}>${escapeHtml(kr.title)}</option>`),
  ].join('');
}

// ── EOY Review modal ──────────────────────────────────────────

function _openEoyReviewModal() {
  document.getElementById('okr-eoy-modal')?.remove();

  const currentYear = new Date().getFullYear();
  const modal = document.createElement('div');
  modal.id = 'okr-eoy-modal';
  modal.className = 'okr-modal-backdrop';
  modal.innerHTML = `
    <div class="okr-modal" role="dialog" aria-modal="true" aria-label="Generate EOY review">
      <div class="okr-modal-head">
        <span class="okr-modal-title">Generate end-of-year review</span>
        <button type="button" class="okr-modal-close" aria-label="Close">×</button>
      </div>
      <div class="okr-modal-body">
        <div class="okr-eoy-field">
          <label class="okr-eoy-label">Year</label>
          <select class="okr-eoy-year-select">
            <option value="${currentYear}" selected>${currentYear}</option>
            <option value="${currentYear - 1}">${currentYear - 1}</option>
          </select>
        </div>
        <div class="okr-eoy-field">
          <label class="okr-eoy-label">Additional context <span class="okr-eoy-optional">(optional)</span></label>
          <textarea class="okr-eoy-context" rows="4" placeholder="Anything not captured in your OKR data — major projects, one-off wins, context for gaps."></textarea>
        </div>
        <div class="okr-eoy-hint">Artemis reads your full OKR history and drafts all three review sections.</div>
      </div>
      <div class="okr-modal-foot">
        <button type="button" class="btn btn-amber btn-sm" data-eoy-generate>Generate</button>
        <button type="button" class="btn btn-outline btn-sm" data-eoy-cancel>Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const closeModal = () => modal.remove();
  modal.querySelector('[data-eoy-cancel]').addEventListener('click', closeModal);
  modal.querySelector('.okr-modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

  modal.querySelector('[data-eoy-generate]').addEventListener('click', async () => {
    const year = Number(modal.querySelector('.okr-eoy-year-select').value);
    const context = modal.querySelector('.okr-eoy-context').value.trim();
    const generateBtn = modal.querySelector('[data-eoy-generate]');
    generateBtn.textContent = 'Generating…';
    generateBtn.disabled = true;

    try {
      const { content } = await generateEoyReviewApi(year, context || '');
      modal.querySelector('.okr-modal-body').innerHTML = `
        <div class="okr-eoy-result">${content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
      `;
      const foot = modal.querySelector('.okr-modal-foot');
      foot.innerHTML = `
        <button type="button" class="btn btn-amber btn-sm" data-eoy-copy>Copy all</button>
        <button type="button" class="btn btn-outline btn-sm" data-eoy-close>Close</button>
      `;
      foot.querySelector('[data-eoy-copy]').addEventListener('click', async (e) => {
        await navigator.clipboard.writeText(content);
        e.target.textContent = 'Copied!';
        setTimeout(() => { e.target.textContent = 'Copy all'; }, 1500);
      });
      foot.querySelector('[data-eoy-close]').addEventListener('click', closeModal);
    } catch {
      generateBtn.textContent = 'Generate';
      generateBtn.disabled = false;
      const hint = modal.querySelector('.okr-eoy-hint');
      if (hint) hint.textContent = 'Generation failed. Please try again.';
    }
  });
}

// ── OKR Deck Generator ────────────────────────────────────────

let _deckPollTimer = null;

async function _startOkrDeckGeneration(btn) {
  if (btn.disabled) return;
  btn.disabled = true;
  btn.textContent = 'Generating…';
  _showOkrToast('Generating deck in background…');

  let jobId;
  try {
    const { jobId: id } = await startOkrDeckGenerationApi();
    jobId = id;
  } catch {
    btn.disabled = false;
    btn.textContent = 'Generate deck';
    _showOkrToast('Failed to start deck generation.');
    return;
  }

  if (_deckPollTimer) clearInterval(_deckPollTimer);
  _deckPollTimer = setInterval(async () => {
    try {
      const job = await pollOkrDeckStatusApi(jobId);
      if (job.status === 'done') {
        clearInterval(_deckPollTimer);
        _deckPollTimer = null;
        btn.disabled = false;
        btn.textContent = 'Generate deck';
        _showOkrToast(`Deck saved → ~/Desktop/OKR Decks/${job.fileName}`);
      } else if (job.status === 'error') {
        clearInterval(_deckPollTimer);
        _deckPollTimer = null;
        btn.disabled = false;
        btn.textContent = 'Generate deck';
        _showOkrToast('Deck generation failed. Try again.');
      }
    } catch {
      // Keep polling — transient fetch errors shouldn't stop us
    }
  }, 2000);
}

// ── OKR Update modal (merge-preserving import) ────────────────

function _openOkrUpdateModal() {
  document.getElementById('okr-update-modal')?.remove();

  const modal = document.createElement('div');
  modal.id = 'okr-update-modal';
  modal.className = 'okr-modal-backdrop';
  modal.innerHTML = `
    <div class="okr-modal okr-modal-wide" role="dialog" aria-modal="true" aria-label="Update OKRs">
      <div class="okr-modal-head">
        <span class="okr-modal-title">Update OKRs</span>
        <button type="button" class="okr-modal-close" aria-label="Close">×</button>
      </div>
      <div class="okr-modal-body" data-update-step="input">
        <textarea class="okr-paste-textarea" placeholder="Paste your updated OKRs — from Google Sheets, a doc, or any format. Artemis parses the structure and shows you a diff before writing anything." rows="12"></textarea>
        <div class="okr-paste-hint">Accomplishments and activity are never deleted — matched KRs preserve their full history.</div>
      </div>
      <div class="okr-modal-foot">
        <button type="button" class="btn btn-amber btn-sm" data-update-parse>Preview changes</button>
        <button type="button" class="btn btn-outline btn-sm" data-update-cancel>Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const closeModal = () => modal.remove();
  modal.querySelector('[data-update-cancel]').addEventListener('click', closeModal);
  modal.querySelector('.okr-modal-close').addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

  const parseBtn = modal.querySelector('[data-update-parse]');
  parseBtn.addEventListener('click', async () => {
    const textarea = modal.querySelector('.okr-paste-textarea');
    const raw = textarea?.value?.trim();
    if (!raw) return;

    parseBtn.textContent = 'Analyzing…';
    parseBtn.disabled = true;

    let result;
    try {
      result = await previewOkrUpdateApi(raw);
    } catch {
      parseBtn.textContent = 'Preview changes';
      parseBtn.disabled = false;
      const hint = modal.querySelector('.okr-paste-hint');
      if (hint) hint.textContent = 'Analysis failed. Please try again.';
      return;
    }

    const { previewId, diff } = result;
    const { matched = [], new_krs = [], new_objectives = [], dropped = [] } = diff;

    if (!matched.length && !new_krs.length && !new_objectives.length && !dropped.length) {
      parseBtn.textContent = 'Preview changes';
      parseBtn.disabled = false;
      const hint = modal.querySelector('.okr-paste-hint');
      if (hint) hint.textContent = 'No OKR structure found in the pasted text. Try pasting the full OKR sheet.';
      return;
    }

    // Force-unmatch tracking: Set of new_kr_keys the user has unmatched
    const forceUnmatched = new Set();

    function renderDiff() {
      const body = modal.querySelector('.okr-modal-body');
      body.dataset.updateStep = 'diff';

      const matchedRows = matched.map((m) => {
        const isUnmatched = forceUnmatched.has(m.new_kr_key);
        if (isUnmatched) return '';
        const preserved = m.preserved;
        const preservedNote = [
          preserved.done_count ? `${preserved.done_count} done bullet${preserved.done_count !== 1 ? 's' : ''}` : '',
          preserved.activity_count ? `${preserved.activity_count} activity entr${preserved.activity_count !== 1 ? 'ies' : 'y'}` : '',
        ].filter(Boolean).join(', ');
        const diffTitle = m.existing_title !== m.new_kr_title
          ? `<span class="okr-diff-old">${escapeHtml(m.existing_title)}</span> → <span class="okr-diff-new">${escapeHtml(m.new_kr_title)}</span>`
          : `<span class="okr-diff-same">${escapeHtml(m.new_kr_title)}</span>`;
        return `
          <div class="okr-diff-row matched" data-diff-key="${escapeAttribute(m.new_kr_key)}">
            <span class="okr-diff-badge match">match</span>
            <div class="okr-diff-info">
              <div class="okr-diff-title">${diffTitle}</div>
              ${preservedNote ? `<div class="okr-diff-meta">Preserves: ${escapeHtml(preservedNote)}</div>` : ''}
              <div class="okr-diff-obj">${escapeHtml(m.new_obj_title)}</div>
            </div>
            <button type="button" class="btn btn-outline btn-xs okr-diff-unmatch" data-unmatch="${escapeAttribute(m.new_kr_key)}" title="Treat as new KR instead">Unmatch</button>
          </div>`;
      }).filter(Boolean).join('');

      const newObjRows = new_objectives.map((obj) =>
        obj.krs.map((kr) => `
          <div class="okr-diff-row new-obj">
            <span class="okr-diff-badge new">new obj</span>
            <div class="okr-diff-info">
              <div class="okr-diff-title okr-diff-new">${escapeHtml(kr.title)}</div>
              <div class="okr-diff-obj">${escapeHtml(obj.title)} (new objective)</div>
            </div>
          </div>`).join('')
      ).join('');

      const newKrRows = new_krs.map((kr) => `
        <div class="okr-diff-row new-kr">
          <span class="okr-diff-badge new">new</span>
          <div class="okr-diff-info">
            <div class="okr-diff-title okr-diff-new">${escapeHtml(kr.title)}</div>
            <div class="okr-diff-obj">${escapeHtml(kr.parent_obj_title)}</div>
          </div>
        </div>`).join('');

      // Also show force-unmatched items as "new"
      const unmatchedRows = [...forceUnmatched].map((key) => {
        const m = matched.find((x) => x.new_kr_key === key);
        if (!m) return '';
        return `
          <div class="okr-diff-row new-kr unmatched">
            <span class="okr-diff-badge new">new</span>
            <div class="okr-diff-info">
              <div class="okr-diff-title okr-diff-new">${escapeHtml(m.new_kr_title)}</div>
              <div class="okr-diff-obj">${escapeHtml(m.new_obj_title)} <span class="okr-diff-unmatched-note">(manually unmatched)</span></div>
            </div>
          </div>`;
      }).filter(Boolean).join('');

      const droppedRows = dropped.map((d) => {
        const isNowDropped = !matched.find((m) => m.existing_id === d.id && !forceUnmatched.has(m.new_kr_key));
        if (!isNowDropped) return '';
        const histNote = [
          d.done_count ? `${d.done_count} done bullet${d.done_count !== 1 ? 's' : ''}` : '',
          d.activity_count ? `${d.activity_count} activit${d.activity_count !== 1 ? 'ies' : 'y'}` : '',
        ].filter(Boolean).join(', ');
        return `
          <div class="okr-diff-row dropped">
            <span class="okr-diff-badge drop">archive</span>
            <div class="okr-diff-info">
              <div class="okr-diff-title okr-diff-old">${escapeHtml(d.title)}</div>
              ${histNote ? `<div class="okr-diff-meta">History preserved: ${escapeHtml(histNote)}</div>` : ''}
              <div class="okr-diff-obj">${escapeHtml(d.obj_title)}</div>
            </div>
          </div>`;
      }).filter(Boolean).join('');

      const hasAny = matchedRows || newObjRows || newKrRows || unmatchedRows || droppedRows;

      body.innerHTML = `
        <div class="okr-diff-summary">
          ${matched.length - forceUnmatched.size > 0 ? `<span class="okr-diff-chip match">${matched.length - forceUnmatched.size} matched</span>` : ''}
          ${(new_krs.length + (new_objectives.reduce((s, o) => s + o.krs.length, 0)) + forceUnmatched.size) > 0 ? `<span class="okr-diff-chip new">${new_krs.length + new_objectives.reduce((s, o) => s + o.krs.length, 0) + forceUnmatched.size} new</span>` : ''}
          ${dropped.length + forceUnmatched.size > 0 ? `<span class="okr-diff-chip drop">${dropped.length + forceUnmatched.size} archive</span>` : ''}
        </div>
        <div class="okr-diff-list">
          ${hasAny ? (matchedRows + newObjRows + newKrRows + unmatchedRows + droppedRows) : '<div class="okr-diff-empty">Nothing to change.</div>'}
        </div>
        <div class="okr-diff-note">Accomplishments, activity entries, and progress are never deleted — archived KRs retain their full history.</div>
      `;
    }

    renderDiff();

    const foot = modal.querySelector('.okr-modal-foot');
    foot.innerHTML = `
      <button type="button" class="btn btn-amber btn-sm" data-update-commit>Apply update</button>
      <button type="button" class="btn btn-outline btn-sm" data-update-back>← Back</button>
      <button type="button" class="btn btn-outline btn-sm" data-update-cancel2>Cancel</button>
    `;

    foot.querySelector('[data-update-cancel2]').addEventListener('click', closeModal);
    foot.querySelector('[data-update-back]').addEventListener('click', () => {
      // Restore step 1
      const body = modal.querySelector('.okr-modal-body');
      body.dataset.updateStep = 'input';
      body.innerHTML = `
        <textarea class="okr-paste-textarea" placeholder="Paste your updated OKRs…" rows="12">${escapeHtml(raw)}</textarea>
        <div class="okr-paste-hint">Accomplishments and activity are never deleted — matched KRs preserve their full history.</div>
      `;
      foot.innerHTML = `
        <button type="button" class="btn btn-amber btn-sm" data-update-parse>Preview changes</button>
        <button type="button" class="btn btn-outline btn-sm" data-update-cancel>Cancel</button>
      `;
      foot.querySelector('[data-update-cancel]').addEventListener('click', closeModal);
      foot.querySelector('[data-update-parse]').addEventListener('click', () => parseBtn.click());
    });

    // Unmatch buttons (delegated)
    const body = modal.querySelector('.okr-modal-body');
    body.addEventListener('click', (e) => {
      const unmatchBtn = e.target.closest('[data-unmatch]');
      if (!unmatchBtn) return;
      const key = unmatchBtn.dataset.unmatch;
      forceUnmatched.add(key);
      renderDiff();
    });

    foot.querySelector('[data-update-commit]').addEventListener('click', async () => {
      const commitBtn = foot.querySelector('[data-update-commit]');
      commitBtn.textContent = 'Applying…';
      commitBtn.disabled = true;

      const overrides = [...forceUnmatched].map((key) => ({ new_kr_key: key, matched_existing_id: null }));

      try {
        const { overview } = await commitOkrUpdateApi(previewId, overrides);
        closeModal();
        const total = matched.length + new_krs.length + new_objectives.reduce((s, o) => s + o.krs.length, 0);
        _showOkrToast(`OKRs updated — ${total} KR${total !== 1 ? 's' : ''} processed, ${dropped.length} archived`);
        // Refresh the OKR shell in-place using the returned overview
        if (overview && typeof overview === 'object') {
          const shellContent = document.querySelector('[data-shell-page="okr"]') || document.getElementById('app-shell-content');
          if (shellContent) {
            const okrBody = shellContent.querySelector('.okr-body-grid')?.parentElement;
            if (okrBody) {
              okrBody.innerHTML = renderOkrShell(overview);
              _wireOkrInteractions(shellContent);
            }
          }
        }
      } catch {
        commitBtn.textContent = 'Apply update';
        commitBtn.disabled = false;
      }
    });
  });
}

// ── Shared toast ──────────────────────────────────────────────

function _showOkrToast(message) {
  const existing = document.getElementById('okr-toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.id = 'okr-toast';
  toast.className = 'okr-toast';
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

function renderOkrShellError() {
  return `
    <section class="page-hero">
      <div class="page-hero-titleblock">
        <div class="page-hero-eyebrow-row">
          <span class="page-hero-eyebrow">Personal Workspace</span>
          <span class="page-hero-status" data-tone="error">Failed</span>
        </div>
        <h1>OKR Studio</h1>
        <p class="page-hero-lede">OKR Studio could not load. Check your configuration or try again.</p>
      </div>
    </section>
    <section class="page-canvas">
      <article class="page-section col-span-12">
        <div class="page-empty-state">
          <h3>Unable to load OKR Studio</h3>
          <p>The OKR Studio surface encountered an error while loading. Reload to try again.</p>
        </div>
      </article>
    </section>
  `;
}

function renderOperationsShell() {
  return renderOperationsView(OPERATIONS_VIEW);
}

function applyStoredModuleFocus() {
  if (!appShellContent) return;
  const focusTarget = localStorage.getItem(MODULE_FOCUS_STORAGE_KEY);
  const cards = appShellContent.querySelectorAll('[data-module-focus-target]');
  cards.forEach((card) => card.classList.remove('shell-card-focused'));

  if (!focusTarget) return;

  const targetCard = [...cards].find((card) => card.dataset.moduleFocusTarget === focusTarget);
  if (!targetCard) return;

  targetCard.classList.add('shell-card-focused');
  if (typeof targetCard.scrollIntoView === 'function') {
    targetCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  localStorage.removeItem(MODULE_FOCUS_STORAGE_KEY);
}

function applyStoredOperationsFocus() {
  if (!appShellContent) return;
  const focusTarget = localStorage.getItem(OPERATIONS_FOCUS_STORAGE_KEY);
  const cards = appShellContent.querySelectorAll('[data-operations-focus-target]');
  cards.forEach((card) => card.classList.remove('shell-card-focused'));

  if (!focusTarget) return;

  const targetCard = [...cards].find((card) => card.dataset.operationsFocusTarget === focusTarget);
  if (!targetCard) return;

  targetCard.classList.add('shell-card-focused');
  if (typeof targetCard.scrollIntoView === 'function') {
    targetCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  localStorage.removeItem(OPERATIONS_FOCUS_STORAGE_KEY);
}

function applyTaskCommandFocus(sectionTitle = '') {
  if (!appShellContent) return;

  const taskCommandCard = appShellContent.querySelector('[data-task-command-root]');
  if (!taskCommandCard) return;

  const columns = taskCommandCard.querySelectorAll('[data-task-command-section]');
  columns.forEach((column) => column.classList.remove(TASK_COMMAND_FOCUS_CLASS));
  taskCommandCard.classList.remove('shell-card-focused');

  let target = taskCommandCard;
  if (sectionTitle) {
    const matchingColumn = [...columns].find((column) => column.dataset.taskCommandSection === sectionTitle);
    if (matchingColumn) {
      matchingColumn.classList.add(TASK_COMMAND_FOCUS_CLASS);
      target = matchingColumn;
    }
  }

  taskCommandCard.classList.add('shell-card-focused');
  if (typeof target.scrollIntoView === 'function') {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function renderCommandCenterError() {
  return `
    <section class="dashboard-main-grid">
      <div class="dashboard-primary-column">
        <article class="shell-card shell-card-error">
          <h3>Today</h3>
          <p>The Dashboard could not load today’s read. Use Meetings, Calendar, or Chat directly while this reloads.</p>
        </article>
        <article class="shell-card shell-card-error">
          <h3>Jira Today</h3>
          <p>Assigned Jira work could not be ranked right now. Open the Jira Board directly if you need it.</p>
        </article>
      </div>
      <div class="dashboard-secondary-column">
        <article class="shell-card shell-card-error">
          <h3>Needs Your Reply</h3>
          <p>Reply-needed items could not load, so use Chat or the notification history directly.</p>
        </article>
        <article class="shell-card shell-card-error">
          <h3>OKR This Week</h3>
          <p>OKR priorities could not load, so open OKR Studio directly if you need a weekly progress read.</p>
        </article>
        <article class="shell-card shell-card-error">
          <h3>Resume Work</h3>
          <p>Recent sessions are unavailable right now. Open Chat directly to recover manually.</p>
        </article>
      </div>
    </section>
  `;
}

function renderModulesError() {
  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">Personal Workspace</div>
      <h2>Workspace</h2>
      <p>The Workspace shell is mounted, but the current read-only inputs could not load. Chat is still available for manual prep while this surface stays isolated.</p>
    </section>
    <section class="command-center-grid">
      <article class="shell-card shell-card-error">
        <h3>Meetings</h3>
        <p>The Meetings surface could not assemble its prep, notes, and follow-up posture.</p>
      </article>
      <article class="shell-card shell-card-error">
        <h3>Calendar</h3>
        <p>The Calendar surface could not assemble its time-reality posture.</p>
      </article>
      <article class="shell-card shell-card-error">
        <h3>Jira Board</h3>
        <p>The Jira Board surface could not assemble its execution-risk posture.</p>
      </article>
      <article class="shell-card shell-card-error">
        <h3>OKR Studio</h3>
        <p>The OKR Studio surface could not assemble its evidence-to-narrative posture.</p>
      </article>
    </section>
  `;
}

function renderWorkflowsShellError({ bridgeSource = '' } = {}) {
  const bridgeNotice = bridgeSource === 'automations'
    ? `
      <article class="shell-card shell-card-error">
        <h3>Automations Bridge</h3>
        <p>You arrived from the temporary Automations shell bridge. This destination currently mirrors saved builder workflows because a first-class Automations workspace does not exist yet.</p>
      </article>
    `
    : '';

  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">Operations</div>
      <h2>Workflows</h2>
      <p>${bridgeSource === 'automations'
        ? 'The Automations bridge landed in Workflows, but the current saved workflow inventory could not load. The builder panel remains the live path while this temporary bridge stays explicit.'
        : 'The workflow shell is mounted, but the current saved workflow inventory could not load. The builder panel remains the live path for creating or launching workflows.'}</p>
    </section>
    <section class="command-center-grid shell-destination-grid">
      ${bridgeNotice}
      <article class="shell-card shell-card-error">
        <h3>Workflow Inventory</h3>
        <p>Saved workflow data is temporarily unavailable, so this shell cannot summarize the current builder inventory honestly.</p>
      </article>
      <article class="shell-card shell-card-error">
        <h3>Saved Workflows</h3>
        <p>The current saved workflow list could not be read.</p>
      </article>
      <article class="shell-card shell-card-error">
        <h3>Current Limits</h3>
        <p>The builder still lives in the sidebar, and that path remains the fallback while this shell reloads.</p>
      </article>
    </section>
  `;
}

function renderAgentsShellError() {
  return `
    <section class="shell-hero">
      <div class="shell-eyebrow">Operations</div>
      <h2>Agents</h2>
      <p>The agents shell is mounted, but the current saved builder inventory could not load. The builder panel remains the live path for creating or launching agents.</p>
    </section>
    <section class="command-center-grid shell-destination-grid">
      <article class="shell-card shell-card-error">
        <h3>Agent Inventory</h3>
        <p>Saved agent data is temporarily unavailable, so this shell cannot summarize the current builder inventory honestly.</p>
      </article>
      <article class="shell-card shell-card-error">
        <h3>Saved Agents</h3>
        <p>The current saved agent list could not be read.</p>
      </article>
      <article class="shell-card shell-card-error">
        <h3>Saved Launchers</h3>
        <p>The current chain and DAG launcher lists could not be read.</p>
      </article>
    </section>
  `;
}

function buildWorkflowsShellViewModel(workflows = [], { bridgeSource = '' } = {}) {
  const workflowCount = Array.isArray(workflows) ? workflows.length : 0;
  const topWorkflows = workflows.slice(0, 3).map((workflow) => ({
    title: workflow?.title || 'Untitled workflow',
    body: describeWorkflowInventoryItem(workflow),
  }));
  const cameFromAutomations = bridgeSource === 'automations';

  return {
    heroCopy: cameFromAutomations
      ? workflowCount
        ? `You arrived here through the temporary Automations bridge. Artemis does not have a first-class Automations workspace yet, so this shell is showing the ${workflowCount} saved ${pluralize('workflow', workflowCount)} that currently exist in the current workflow system.`
        : 'You arrived here through the temporary Automations bridge. Artemis does not have a first-class Automations workspace yet, and no saved builder workflows exist today, so this shell is showing the truthful empty state.'
      : workflowCount
        ? `This destination now reads ${workflowCount} saved ${pluralize('workflow', workflowCount)} from the current workflow config so the shell reflects real app state instead of placeholder copy.`
      : 'This destination is now grounded in the real current config, but no saved workflows exist yet, so the shell stays honest about the empty state.',
    stateCopy: workflowCount
      ? `The workflow builder still launches from the existing Agents & Workflows sidebar, but this shell now reflects the current saved inventory: ${workflowCount} saved ${pluralize('workflow', workflowCount)} are ready to review or launch from that builder path.`
      : 'The workflow builder still launches from the existing Agents & Workflows sidebar. No saved workflows are configured yet, so this shell is showing the truthful empty state instead of future-looking filler.',
    readinessLabel: workflowCount ? `${workflowCount} saved` : 'Empty today',
    readinessPoints: [
      'Workflow orchestration needs a durable home in the new shell instead of remaining coupled to the legacy analytics-first landing path.',
      'This pass stays behavior-preserving: no builder rewrite, no execution-model changes, and no new workflow runtime behavior.',
      workflowCount
        ? 'The existing builder path remains the only way to create or run workflows in the current app, but the shell now shows what is actually saved there today.'
        : 'The existing builder path remains the only way to create the first workflow in the current app.',
    ],
    bridgeRail: cameFromAutomations
      ? {
        eyebrow: 'Temporary shell bridge',
        title: 'Automations Today',
        badge: workflowCount ? `${workflowCount} workflow-backed` : 'No workflow backing yet',
        items: workflowCount
          ? [
            {
              title: 'Current bridge behavior',
              body: 'Automations currently lands here because the app only has a saved workflow builder inventory today, not a separate scheduled-runs workspace.',
            },
            {
              title: 'What is truthful right now',
              body: `The shell can honestly show ${workflowCount} saved ${pluralize('workflow', workflowCount)} that could later become automation inputs, but it cannot claim visible schedules, triggers, or run history yet.`,
            },
          ]
          : [
            {
              title: 'Current bridge behavior',
              body: 'Automations currently lands here because the app has no separate Automations workspace yet.',
            },
            {
              title: 'What is truthful right now',
              body: 'No saved builder workflows exist, so the bridge cannot imply that scheduled or triggered work is already configured.',
            },
          ],
        footnote: workflowCount
          ? 'This bridge stays narrow on purpose: it reuses current workflow inventory as the closest honest surface without pretending an Automations system already exists.'
          : 'This bridge stays narrow on purpose: it shows the empty workflow-backed reality instead of pretending there are already scheduled or triggered runs to manage.',
      }
      : null,
    inventoryBadge: workflowCount ? `${workflowCount} saved` : 'No saved workflows',
    workflowItems: topWorkflows.length ? topWorkflows : [
      {
        title: 'No saved workflows yet',
        body: 'The shell checked the current workflow config and found no saved workflows. Use Open Builder to create the first repeatable flow.',
      },
    ],
    inventoryFootnote: workflowCount > 3
      ? `${workflowCount - 3} additional saved ${pluralize('workflow', workflowCount - 3)} remain in the current builder inventory beyond the items shown here.`
      : workflowCount
        ? 'This list is pulled from the same saved workflow config the current builder uses today.'
        : 'This empty state is pulled from the same saved workflow config the current builder uses today.',
  };
}

function buildAgentsShellViewModel({ agents = [], chains = [], dags = [] }) {
  const agentCount = Array.isArray(agents) ? agents.length : 0;
  const chainCount = Array.isArray(chains) ? chains.length : 0;
  const dagCount = Array.isArray(dags) ? dags.length : 0;
  const launcherCount = chainCount + dagCount;
  const topAgents = agents.slice(0, 3).map((agent) => ({
    title: agent?.title || 'Untitled agent',
    body: describeAgentInventoryItem(agent),
  }));

  const launcherItems = [];
  if (chainCount) {
    launcherItems.push(...chains.slice(0, 2).map((chain) => ({
      title: chain?.title || 'Untitled chain',
      body: describeChainInventoryItem(chain),
    })));
  }
  if (dagCount) {
    launcherItems.push(...dags.slice(0, 2).map((dag) => ({
      title: dag?.title || 'Untitled DAG',
      body: describeDagInventoryItem(dag),
    })));
  }

  return {
    heroCopy: agentCount || launcherCount
      ? `This destination now reads the real builder inventory: ${agentCount} saved ${pluralize('agent', agentCount)}, ${chainCount} ${pluralize('chain', chainCount)}, and ${dagCount} ${pluralize('DAG', dagCount)} are currently configured in the app.`
      : 'This destination is now grounded in the real current config, but no agents, chains, or DAGs exist yet, so the shell stays honest about the empty state.',
    stateCopy: agentCount || launcherCount
      ? `The current agent launchers still live in the existing builder sidebar, but this shell now reflects what is actually saved there today across workers and launch patterns.`
      : 'The current agent launchers still live in the existing builder sidebar. No agents, chains, or DAGs are configured yet, so this shell is showing the truthful empty state instead of future-looking filler.',
    readinessLabel: agentCount || launcherCount ? `${agentCount + launcherCount} saved` : 'Empty today',
    readinessPoints: [
      'Agent profiles need their own durable home for purpose, memory, provider policy, skills, and performance history.',
      'This pass does not rebuild agent creation, launch, monitoring, or persistence behavior.',
      agentCount || launcherCount
        ? 'The existing builder path remains the current path for creating and running agents today, but the shell now shows what is actually saved there.'
        : 'The existing builder path remains the current path for creating the first worker or launcher today.',
    ],
    agentBadge: agentCount ? `${agentCount} saved` : 'No saved agents',
    agentItems: topAgents.length ? topAgents : [
      {
        title: 'No saved agents yet',
        body: 'The shell checked the current agent config and found no saved agents. Use Open Builder to create the first reusable worker.',
      },
    ],
    agentFootnote: agentCount > 3
      ? `${agentCount - 3} additional saved ${pluralize('agent', agentCount - 3)} remain in the current builder inventory beyond the items shown here.`
      : agentCount
        ? 'This list is pulled from the same saved agent config the current builder uses today.'
        : 'This empty state is pulled from the same saved agent config the current builder uses today.',
    launcherBadge: launcherCount ? `${launcherCount} launchers` : 'No launchers',
    launcherItems: launcherItems.length ? launcherItems.slice(0, 3) : [
      {
        title: 'No saved chains or DAGs yet',
        body: 'The shell checked the current launcher config and found no saved launchers. Use Open Builder when you want a reusable chain or dependency graph.',
      },
    ],
    launcherFootnote: launcherCount > 3
      ? `${launcherCount - 3} additional saved ${pluralize('launcher', launcherCount - 3)} remain in the current builder inventory beyond the items shown here.`
      : launcherCount
        ? 'Chains and DAGs still launch from the builder sidebar today; this shell now simply reflects the real saved inventory.'
        : 'This empty state is pulled from the current chain/DAG config the launcher uses today.',
  };
}

function buildCommandCenterViewModel({
  analytics = {},
  notifications = [],
  sessions = [],
  calendarOverview = {},
  meetingsOverview = {},
  jiraOverview = null,
  okrOverview = null,
  slackSignals = null,
  slackMentions = null,
  brief = null,
}) {
  const timeReality = readTimeReality();
  const jiraToday = buildDashboardJiraTodayModel(jiraOverview, timeReality);
  const okrThisWeek = buildDashboardOkrWeekModel(okrOverview, timeReality);
  const replyWork = buildDashboardReplyWorkModel(notifications, slackSignals, slackMentions);
  const resumeWork = buildResumeWorkModel({
    analytics,
    sessions,
  });
  const systemIssues = buildSystemIssues({}, analytics);

  return {
    todayPlan: buildDashboardTodayPlanModel({
      analytics,
      meetingsOverview,
      calendarOverview,
      timeReality,
      jiraToday,
      okrThisWeek,
    }),
    replyWork,
    jiraToday,
    okrThisWeek,
    resumeWork,
    needsAttention: {
      systemIssues,
      queueItems: buildNeedsAttentionItems(analytics, notifications, systemIssues, slackSignals),
      sourceNote: buildNeedsAttentionSourceNote(slackSignals),
    },
    captureWork: buildDashboardCaptureModel({
      jiraToday,
      okrThisWeek,
      resumeWork,
    }),
    brief,
  };
}

function buildResumeWorkModel({ analytics = {}, notifications = [], sessions = [] }) {
  const sortedSessions = Array.isArray(sessions)
    ? [...sessions].sort((left, right) => Number(right?.last_used_at || 0) - Number(left?.last_used_at || 0))
    : [];
  const recentSessions = sortedSessions.slice(0, 4).map((session) => {
    const relativeTime = formatRelativeSessionTime(session?.last_used_at);
    return {
      id: session?.id || '',
      title: session?.title || session?.project_name || 'Session',
      summary: String(session?.summary || '').trim()
        || (relativeTime
          ? `Last active ${relativeTime}. Continue this conversation where the current project context already exists.`
          : 'Continue this conversation where the current project context already exists.'),
      providerLabel: formatProviderLabel(session?.provider_id) || 'Unknown source',
      modeLabel: describeSessionMode(session?.mode),
      relativeTime: relativeTime || 'Recent',
      isFork: Boolean(session?.parent_session_id),
    };
  });
  const sessionCount = Number(analytics?.overview?.sessions || 0);
  const unreadCount = notifications.filter((item) => !item?.read_at).length;

  return {
    items: recentSessions,
    summary: recentSessions.length
      ? 'The fastest path back into real work is usually an existing session, not another planning detour. Resume the strongest recent thread first.'
      : 'No saved session is active right now. If today is a lighter structured-work day, the next best move may be to capture what you are working on instead of forcing a Jira or OKR item.',
    sourceNote: recentSessions.length
      ? `Showing ${recentSessions.length} recent session${recentSessions.length === 1 ? '' : 's'} from ${sessionCount.toLocaleString()} recorded conversation${sessionCount === 1 ? '' : 's'} for this project.`
      : 'Capture today\'s work is ready when no structured Jira or OKR item clearly deserves the day yet.',
  };
}

function renderResumeWork(resumeWork) {
  const items = Array.isArray(resumeWork?.items) ? resumeWork.items : [];
  return `
    <article class="shell-card command-card dashboard-card dashboard-card-resume">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Continuation layer</div>
          <h3>Resume Work</h3>
        </div>
        <span class="command-card-badge">${items.length ? `${items.length} recent` : 'No sessions yet'}</span>
      </div>
      <p class="resume-work-summary">${escapeHtml(resumeWork?.summary || '')}</p>
      <div class="resume-work-list">
        ${items.length ? items.map((item) => `
          <article class="resume-work-item">
            <div class="resume-work-item-topline">
              <h4>${escapeHtml(item.title)}</h4>
              <div class="resume-work-meta">
                <span class="resume-work-meta-chip">${escapeHtml(item.providerLabel)}</span>
                <span class="resume-work-meta-chip">${escapeHtml(item.modeLabel)}</span>
                <span class="resume-work-meta-chip">${escapeHtml(item.relativeTime)}</span>
                ${item.isFork ? '<span class="resume-work-meta-chip">Fork</span>' : ''}
              </div>
            </div>
            <p>${escapeHtml(item.summary)}</p>
            <div class="shell-actions resume-work-actions">
              <button
                type="button"
                class="shell-action-btn"
                data-shell-action="open-session-from-shell"
                data-shell-session-id="${escapeAttribute(item.id)}"
              >
                Resume Session
              </button>
            </div>
          </article>
        `).join('') : `
          <article class="resume-work-item resume-work-item-empty">
            <h4>Start the first real thread</h4>
            <p>Open Chat from the Dashboard and give Artemis one concrete goal, repo task, or question. The resume lane becomes useful as soon as that first working session exists.</p>
            <div class="shell-actions resume-work-actions">
              <button
                type="button"
                class="shell-action-btn"
                data-shell-action="open-chat-from-shell"
                data-shell-intent="Help me start the first focused work session from Dashboard."
              >
                Start in Chat
              </button>
              <button
                type="button"
                class="shell-action-btn shell-action-btn-secondary"
                data-shell-action="dashboard-capture-open"
              >
                Capture today's work
              </button>
            </div>
          </article>
        `}
      </div>
      <p class="command-card-footnote">${escapeHtml(resumeWork?.sourceNote || '')}</p>
    </article>
  `;
}

function _slackTsAgo(tsSeconds) {
  const diffMs = Date.now() - tsSeconds * 1000;
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/** Resolve sender name from J9b API shape (mention.sender.name) or legacy flat fields. */
function _mentionSenderLabel(mention) {
  if (mention.sender && mention.sender.name) return mention.sender.name;
  return mention.sender_name || mention.sender_user_id || 'someone';
}

/** Resolve channel label from J9b API shape (mention.channel.name/is_im) or legacy flat fields. */
function _mentionChannelLabel(mention) {
  if (mention.channel) {
    if (mention.channel.is_im) return 'DM';
    return '#' + (mention.channel.name || mention.channel.id || 'unknown');
  }
  const rawId = mention.channel_name || mention.channel_id || 'unknown';
  return '#' + rawId;
}

/** Render a single message sub-row inside a grouped sender card. */
function _renderSlackMentionSubRow(mention) {
  const senderLabel = _mentionSenderLabel(mention);
  const channelLabel = _mentionChannelLabel(mention);
  const snippet = mention.text
    ? mention.text.slice(0, 120) + (mention.text.length > 120 ? '…' : '')
    : '';

  const draftIntent = `Help me draft a short Slack reply to ${senderLabel} in ${channelLabel}. They said: '${mention.text || ''}'. Match my voice (concise, direct, lowercase). Don't invent context — if I haven't given you enough info, ask me.`;

  return `
    <div class="slack-triage-sub-row" data-mention-id="${escapeAttribute(mention.id)}">
      ${snippet ? `<p class="slack-triage-snippet">• ${escapeHtml(snippet)}</p>` : ''}
      <div class="slack-triage-actions">
        <button
          type="button"
          class="shell-action-btn shell-action-btn-xs"
          data-shell-action="open-chat-from-shell"
          data-shell-intent="${escapeAttribute(draftIntent)}"
        >Draft reply</button>
        <a
          href="${escapeAttribute(mention.permalink || '#')}"
          target="_blank"
          rel="noopener noreferrer"
          class="shell-action-btn shell-action-btn-xs shell-action-btn-secondary"
        >Open in Slack</a>
        <button
          type="button"
          class="shell-action-btn shell-action-btn-xs shell-action-btn-secondary"
          data-shell-action="slack-mention-resolve"
          data-mention-id="${escapeAttribute(mention.id)}"
        >Mark resolved</button>
      </div>
    </div>
  `;
}

/**
 * Group consecutive mentions by sender+channel and render as grouped cards.
 *
 * Mentions from the same sender+channel that appear consecutively are collapsed
 * into a single card with one header row and individual sub-rows per message.
 */
function _renderSlackMentionList(mentions) {
  if (!mentions.length) return '';

  // Build groups: consecutive runs with the same (sender.id, channel.id) key
  const groups = [];
  let currentKey = null;
  let currentGroup = null;

  for (const m of mentions) {
    const senderId = (m.sender && m.sender.id) || m.sender_user_id || '';
    const channelId = (m.channel && m.channel.id) || m.channel_id || '';
    const key = `${senderId}::${channelId}`;

    if (key !== currentKey) {
      if (currentGroup) groups.push(currentGroup);
      currentGroup = { key, mentions: [m], first: m };
      currentKey = key;
    } else {
      currentGroup.mentions.push(m);
    }
  }
  if (currentGroup) groups.push(currentGroup);

  return groups.map(({ first, mentions: groupMentions }) => {
    const senderLabel = _mentionSenderLabel(first);
    const channelLabel = _mentionChannelLabel(first);
    const tsNum = parseFloat(first.ts || '0');
    const timeLabel = tsNum > 0 ? _slackTsAgo(tsNum) : '';
    const headerParts = [senderLabel, channelLabel, timeLabel].filter(Boolean);

    return `
      <article class="slack-triage-group">
        <div class="slack-triage-meta">${escapeHtml(headerParts.join(' · '))}</div>
        ${groupMentions.map(_renderSlackMentionSubRow).join('')}
      </article>
    `;
  }).join('');
}

function _buildTriageChatIntent(mentionItems) {
  const top5 = mentionItems.slice(0, 5);
  const bullets = top5.map((m) => {
    const sender = _mentionSenderLabel(m);
    const channel = _mentionChannelLabel(m);
    const snippet = m.text ? m.text.slice(0, 80) + (m.text.length > 80 ? '…' : '') : '';
    return `- ${sender} in ${channel}: ${snippet}`;
  }).join('\n');
  return `Help me triage these Slack mentions. Here are the top ${top5.length}:\n${bullets}\nWhich need a reply now, which can wait, what's the fastest response?`;
}

function renderNeedsYourReply(radar) {
  const cards = Array.isArray(radar?.cards) ? radar.cards : [];
  const mentionItems = Array.isArray(radar?.slackMentionItems) ? radar.slackMentionItems : [];
  const totalUnresolved = radar?.slackTotalUnresolved ?? 0;

  const slackQueueSection = (() => {
    const headerCount = totalUnresolved > 0
      ? `Slack mentions <span class="slack-triage-count" id="slack-triage-count">(${totalUnresolved} unresolved)</span>`
      : 'Slack mentions';
    const triageChatIntent = mentionItems.length > 0 ? _buildTriageChatIntent(mentionItems) : '';

    const listHtml = mentionItems.length > 0
      ? _renderSlackMentionList(mentionItems)
      : '<p class="slack-triage-empty">Slack queue clear. Nicely done.</p>';

    return `
      <div class="command-subsection" id="slack-triage-section">
        <div class="command-subsection-title command-subsection-title-flex">
          <span>${headerCount}</span>
          ${triageChatIntent ? `
            <button
              type="button"
              class="shell-action-btn shell-action-btn-xs"
              data-shell-action="open-chat-from-shell"
              data-shell-intent="${escapeAttribute(triageChatIntent)}"
            >Triage in Chat</button>
          ` : ''}
        </div>
        <div class="slack-triage-list" id="slack-triage-list">
          ${listHtml}
        </div>
      </div>
    `;
  })();

  return `
    <article class="shell-card command-card dashboard-card dashboard-card-radar">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Response work</div>
          <h3>Needs Your Reply</h3>
        </div>
        <span class="command-card-badge">Personal</span>
      </div>
      ${slackQueueSection}
      <div class="dashboard-radar-grid">
        ${cards.map((card) => `
          <article class="dashboard-radar-item">
            <div class="shell-eyebrow dashboard-radar-eyebrow">${escapeHtml(card.eyebrow)}</div>
            <h4>${escapeHtml(card.title)}</h4>
            <p>${escapeHtml(card.detail)}</p>
            <div class="shell-actions dashboard-radar-actions">
              ${card.sourceSessionId ? `
                <button
                  type="button"
                  class="shell-action-btn shell-action-btn-secondary"
                  data-shell-action="open-session-from-shell"
                  data-shell-session-id="${escapeAttribute(card.sourceSessionId)}"
                >
                  ${escapeHtml(card.primaryAction || 'Open Session')}
                </button>
              ` : ''}
              ${!card.sourceSessionId && card.notificationType ? `
                <button
                  type="button"
                  class="shell-action-btn shell-action-btn-secondary"
                  data-shell-action="open-notification-history-from-shell"
                  data-shell-notification-type="${escapeAttribute(card.notificationType)}"
                  data-shell-notification-status="${escapeAttribute(card.notificationStatus || 'unread')}"
                >
                  ${escapeHtml(card.primaryAction || 'Open Queue')}
                </button>
              ` : ''}
              ${card.connectorScope ? `
                <button
                  type="button"
                  class="shell-action-btn shell-action-btn-secondary"
                  data-shell-action="open-connectors"
                  data-connector-scope="${escapeAttribute(card.connectorScope || '')}"
                >
                  ${escapeHtml(card.primaryAction || 'Open Connectors')}
                </button>
              ` : ''}
              ${card.shellView ? `
                <button
                  type="button"
                  class="shell-action-btn shell-action-btn-secondary"
                  data-shell-action="open-shell-view"
                  data-shell-view="${escapeAttribute(card.shellView)}"
                >
                  ${escapeHtml(card.primaryAction || card.actionLabel || 'Open')}
                </button>
              ` : ''}
              ${card.secondaryIntent ? `
                <button
                  type="button"
                  class="shell-action-btn"
                  data-shell-action="open-chat-from-shell"
                  data-shell-intent="${escapeAttribute(card.secondaryIntent || '')}"
                >
                  Ask Artemis
                </button>
              ` : ''}
            </div>
          </article>
        `).join('')}
      </div>
      <p class="command-card-footnote">${escapeHtml(radar?.footnote || '')}</p>
    </article>
  `;
}

function buildModulesViewModel({
  analytics = {},
  providerStatuses = {},
  notifications = [],
}) {
  const timeReality = readTimeReality();
  return {
    heroCopy: buildModulesHeroCopy(timeReality, notifications),
    jira: buildJiraModuleViewModel({ analytics, providerStatuses, notifications, timeReality }),
    okr: buildOkrModuleViewModel({ analytics, providerStatuses, notifications, timeReality }),
  };
}

function formatRelativeSessionTime(timestampSeconds) {
  if (!timestampSeconds) return '';

  const date = new Date(Number(timestampSeconds) * 1000);
  const diffMs = Date.now() - date.getTime();
  const diffMinutes = Math.max(0, Math.round(diffMs / 60000));

  if (diffMinutes < 1) return 'just now';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;

  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.round(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;

  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function describeSessionMode(mode = '') {
  if (mode === 'parallel') return 'Parallel';
  if (mode === 'both') return 'Single + parallel';
  return 'Single';
}

function buildModulesHeroCopy(timeReality, notifications) {
  if (timeReality.meetingLoad !== 'unknown' || timeReality.nextHardStop) {
    return `Workspace keeps Jira Board and OKR Studio. Calendar and Meetings have dedicated surfaces in the left rail. ${describeTimeRealityLead(timeReality)} Unread operational signals: ${notifications.length}.`.trim();
  }
  return `Workspace keeps Jira Board and OKR Studio. Calendar and Meetings have their own dedicated pages — use the left rail to navigate there directly. Unread operational signals: ${notifications.length}.`;
}

function buildCriticalItems(analytics, notifications, systemIssues) {
  const items = [];

  if (systemIssues.length) {
    items.push({
      title: systemIssues[0].title,
      why: systemIssues[0].detail,
      nextStep: systemIssues[0].nextStep,
      tone: 'warning',
    });
  }

  const unreadApprovals = notifications.filter((item) => item.type === 'approval');
  if (unreadApprovals.length) {
    items.push({
      title: `Review ${unreadApprovals.length} approval${unreadApprovals.length === 1 ? '' : 's'} waiting on you`,
      why: 'Pending approvals can block delegated work, launches, or follow-up automation.',
      nextStep: 'Open Chat and clear the highest-impact approval first.',
      tone: 'accent',
    });
  }

  const providerFailures = Array.isArray(analytics?.recentProviderFailures) ? analytics.recentProviderFailures : [];
  if (providerFailures.length) {
    items.push({
      title: 'Stabilize provider failures before adding new work',
      why: `${providerFailures.length} recent provider launch or sign-in issue${providerFailures.length === 1 ? '' : 's'} could make planned delegation unreliable.`,
      nextStep: 'Check the top provider issue in Needs Attention, then decide whether to retry or switch sources.',
      tone: 'warning',
    });
  }

  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors : [];
  if (recentErrors.length) {
    items.push({
      title: 'Clear repeated runtime or tool failures',
      why: 'Recent errors are still surfacing in the live system, so cleanup here reduces surprise later in the day.',
      nextStep: 'Inspect the failing tool or session noted in Needs Attention before starting something large.',
      tone: 'neutral',
    });
  }

  const sessions = Number(analytics?.overview?.sessions || 0);
  if (sessions > 0) {
    items.push({
      title: 'Use Dashboard as the ranked operating layer, not the analytics home',
      why: `The app already has ${sessions.toLocaleString()} recorded sessions, so the shell can now compress that activity into a narrower action frame.`,
      nextStep: 'Work the top Now item first, then decide what stays personal versus delegated.',
      tone: 'neutral',
    });
  }

  if (!items.length) {
    items.push({
      title: 'Create a focused block before the queue fills up',
      why: 'The current shell inputs are quiet, which makes this a good time to protect one deliberate work block.',
      nextStep: 'Open Quick Action and ask Artemis to turn your top goal into a compact day plan.',
      tone: 'accent',
    });
  }

  return items.slice(0, 5);
}

function buildPrepNotes(analytics, notifications, systemIssues, timeReality, meetingsOverview = {}, calendarOverview = {}) {
  const notes = [];
  const meetingCount = Number(meetingsOverview?.today?.meetingsCount || calendarOverview?.today?.meetingsCount || 0);
  const prepCount = Number(calendarOverview?.today?.prepCount || 0);
  const freeBlocks = Array.isArray(calendarOverview?.today?.freeBlocks) ? calendarOverview.today.freeBlocks : [];

  if (meetingCount) {
    notes.push(`${meetingCount} meeting${meetingCount === 1 ? '' : 's'} ${meetingCount === 1 ? 'is' : 'are'} shaping the day${prepCount ? `, with ${prepCount} prep-heavy item${prepCount === 1 ? '' : 's'} visible from the calendar feed` : ''}.`);
  } else if (freeBlocks.length) {
    notes.push(`The connected calendar shows at least ${freeBlocks.length} free block${freeBlocks.length === 1 ? '' : 's'}, so protect one before the queue expands.`);
  }

  if (notifications.length) {
    notes.push(`${notifications.length} unread operational signal${notifications.length === 1 ? '' : 's'} could still turn into response work or approvals today.`);
  }

  const timeRealityNote = buildTimeRealityPrepNote(timeReality);
  if (timeRealityNote) {
    notes.push(timeRealityNote);
  }

  return notes.slice(0, 3);
}

function buildTaskSections(criticalItems, queueItems, timeReality) {
  const localState = readTaskCommandState();
  const localPreferences = readTaskCommandPreferences();
  const topQueue = queueItems.slice(0, 5);
  const nowEffort = inferNowEffort(timeReality);
  const todayContext = inferTodayContext(timeReality);
  const wrapTaskItem = (item, overrides = {}) => ({
    itemId: buildTaskCommandItemId(item, overrides),
    title: item.title,
    whyNow: item.why || item.detail,
    owner: overrides.owner || 'You',
    effort: overrides.effort || '15-30m',
    context: overrides.context || todayContext,
    mode: overrides.mode || 'Do today',
    intent: item.intent || '',
    sourceSessionId: item.sourceSessionId || '',
    notificationType: item.notificationType || '',
    notificationStatus: item.notificationStatus || '',
    pinned: false,
  });
  const placeholderForSection = (
    sectionTitle,
    title = `No ${sectionTitle.toLowerCase()} items yet`,
    whyNow = 'This section stays visible so the shell can grow into the fuller planner without changing the IA again.',
  ) => ({
    itemId: '',
    title,
    whyNow,
    owner: 'Shell',
    effort: 'N/A',
    context: 'Slice 1',
    mode: 'Watch',
    intent: '',
    sourceSessionId: '',
    notificationType: '',
    notificationStatus: '',
    pinned: false,
    isPlaceholder: true,
    sectionTitle,
  });

  const baseSections = [
    {
      title: 'Now',
      items: criticalItems.slice(0, 3).map((item, index) => wrapTaskItem(item, {
        owner: index === 0 ? 'You' : 'Shared',
        effort: index === 0 ? nowEffort : '20-30m',
        context: index === 0 ? todayContext : 'Needs Attention',
        mode: index === 0 ? 'Do now' : 'Draft',
      })),
    },
    {
      title: 'Today',
      items: dedupeByTitle(
        criticalItems.slice(3, 5).concat(topQueue.slice(0, 1)),
      ).slice(0, 3).map((item) => wrapTaskItem(item, {
        owner: 'You',
        effort: '15-30m',
        context: todayContext,
        mode: 'Do today',
      })),
    },
    {
      title: 'Delegate',
      items: topQueue.filter((item) => item.kind === 'system' || item.kind === 'runtime').slice(0, 2).map((item) => wrapTaskItem(item, {
        owner: 'Artemis',
        effort: 'Async',
        context: 'Operations',
        mode: 'Delegate',
      })),
    },
    {
      title: 'Later / Watch',
      items: dedupeByTitle(topQueue.slice(1, 3)).map((item) => wrapTaskItem(item, {
        owner: 'Watch',
        effort: 'Low',
        context: 'Queue',
        mode: 'Watch',
      })),
    },
  ];

  const sectionMap = new Map(TASK_COMMAND_SECTION_OPTIONS.map((title) => [title, []]));

  baseSections.forEach((section) => {
    section.items.forEach((item) => {
      const itemState = localState[item.itemId] || {};
      if (itemState.dismissed || itemState.snoozed) return;

      const targetSection = TASK_COMMAND_SECTION_OPTIONS.includes(itemState.sectionTitle)
        ? itemState.sectionTitle
        : section.title;

      sectionMap.get(targetSection)?.push({
        ...item,
        emphasized: Boolean(itemState.emphasized),
        moved: TASK_COMMAND_SECTION_OPTIONS.includes(itemState.sectionTitle) && itemState.sectionTitle !== section.title,
        pinned: Boolean(itemState.pinned),
        sectionTitle: targetSection,
      });
    });
  });

  return TASK_COMMAND_SECTION_OPTIONS.map((sectionTitle) => {
    const items = (sectionMap.get(sectionTitle) || []).filter((item) => (
      (localPreferences.pinnedOnly ? item.pinned : true)
      && (localPreferences.focusedOnly ? item.emphasized : true)
      && (localPreferences.movedOnly ? item.moved : true)
    )).sort((left, right) => {
      if (left.pinned === right.pinned) return 0;
      return left.pinned ? -1 : 1;
    });

    const emptyTitle = localPreferences.focusedOnly
      ? `No focused ${sectionTitle.toLowerCase()} items yet`
      : localPreferences.movedOnly
        ? `No moved ${sectionTitle.toLowerCase()} items yet`
      : localPreferences.pinnedOnly
        ? `No pinned ${sectionTitle.toLowerCase()} items yet`
        : `No ${sectionTitle.toLowerCase()} items yet`;
    const emptyBody = localPreferences.focusedOnly
      ? 'Focused-only mode is hiding the rest of this section locally.'
      : localPreferences.movedOnly
        ? 'Moved-only mode is hiding the rest of this section locally.'
      : localPreferences.pinnedOnly
        ? 'Pinned-only mode is hiding the rest of this section locally.'
        : 'This section stays visible so the shell can grow into the fuller planner without changing the IA again.';

    return {
      title: sectionTitle,
      collapsed: localPreferences.collapsedSections.includes(sectionTitle),
      visibleCount: items.length,
      items: items.length ? items : [placeholderForSection(
        sectionTitle,
        emptyTitle,
        emptyBody,
      )],
    };
  });
}

function buildNeedsAttentionItems(analytics, notifications, systemIssues, slackSignals = null) {
  const items = [];

  if (slackSignals?.connected) {
    const slackItems = [
      {
        key: 'missedMentions',
        title: slackSignals.missedMentions ? `${slackSignals.missedMentions} missed Slack mention${slackSignals.missedMentions === 1 ? '' : 's'}` : '',
        detail: slackSignals.missedMentions ? 'Slack has direct mentions that may need triage or a quick response.' : '',
        nextStep: 'Use Chat to decide which mentions need a reply now versus a parked follow-up.',
        urgency: 'Reply',
      },
      {
        key: 'unreadDMs',
        title: slackSignals.unreadDMs ? `${slackSignals.unreadDMs} unread Slack DM${slackSignals.unreadDMs === 1 ? '' : 's'}` : '',
        detail: slackSignals.unreadDMs ? 'Direct messages usually need a faster pass than general channel noise.' : '',
        nextStep: 'Clear the DMs that block decisions or coordination first.',
        urgency: 'Act now',
      },
      {
        key: 'replyNeededThreads',
        title: slackSignals.replyNeededThreads ? `${slackSignals.replyNeededThreads} reply-needed Slack thread${slackSignals.replyNeededThreads === 1 ? '' : 's'}` : '',
        detail: slackSignals.replyNeededThreads ? 'Slack threads look like they still need follow-up from you.' : '',
        nextStep: 'Draft the replies that unblock teammates or keep a thread from drifting.',
        urgency: 'Triage',
      },
    ].filter((item) => item.title);

    slackItems.forEach((item) => {
      items.push({
        title: item.title,
        detail: item.detail,
        status: 'Slack',
        urgency: item.urgency,
        reason: buildAttentionReason({
          kind: 'slack',
          status: 'Slack',
          detail: item.detail,
        }),
        nextStep: item.nextStep,
        actionLabel: buildAttentionActionLabel('slack'),
        intent: buildAttentionIntent(item.title, item.nextStep),
        kind: 'slack',
      });
    });
  }

  notifications.slice(0, 6).forEach((item) => {
    const kind = item.type === 'error' ? 'runtime' : item.type === 'approval' ? 'approval' : 'operational';
    items.push({
      title: item.title || 'Unread operational item',
      detail: compactDashboardText(item.body || describeNotification(item.type)),
      status: item.read_at ? 'Read' : 'Unread',
      urgency: inferAttentionUrgency(kind),
      reason: buildAttentionReason({
        kind,
        status: item.read_at ? 'Read' : 'Unread',
        detail: compactDashboardText(item.body || describeNotification(item.type)),
      }),
      nextStep: buildNotificationNextStep(item),
      actionLabel: buildAttentionActionLabel(kind),
      intent: buildAttentionIntent(item.title || 'Unread operational item', buildNotificationNextStep(item)),
      kind,
      sourceSessionId: item.source_session_id || '',
      notificationType: item.type || '',
      notificationStatus: item.read_at ? 'read' : 'unread',
    });
  });

  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors : [];
  recentErrors.slice(0, 2).forEach((item) => {
    items.push({
      title: `Recent error in ${item.tool || 'runtime'}`,
      detail: compactDashboardText(item.preview || 'A recent error still needs a clean next step.'),
      status: formatProviderLabel(item.provider_id),
      urgency: inferAttentionUrgency('runtime'),
      reason: buildAttentionReason({
        kind: 'runtime',
        status: formatProviderLabel(item.provider_id),
        detail: compactDashboardText(item.preview || 'A recent error still needs a clean next step.'),
      }),
      nextStep: 'Open Dashboard or Chat to inspect the failing tool path before delegating more work.',
      actionLabel: buildAttentionActionLabel('runtime'),
      intent: buildAttentionIntent(`Recent error in ${item.tool || 'runtime'}`, 'Inspect the failing tool path and decide whether to retry, reroute, or park it.'),
      kind: 'runtime',
    });
  });

  systemIssues.slice(0, 2).forEach((issue) => {
    items.push({
      title: issue.title,
      detail: issue.detail,
      status: issue.status,
      urgency: inferAttentionUrgency('system'),
      reason: buildAttentionReason({
        kind: 'system',
        status: issue.status,
        detail: issue.detail,
      }),
      nextStep: issue.nextStep,
      actionLabel: buildAttentionActionLabel('system'),
      intent: buildAttentionIntent(issue.title, issue.nextStep),
      kind: 'system',
    });
  });

  if (!items.length) {
    items.push({
      title: 'No urgent interventions are currently ranked',
      detail: 'The shell did not find high-signal unread items, provider failures, or runtime exceptions to elevate right now.',
      status: 'Stable',
      urgency: 'Watch',
      reason: 'The shell only promotes a small queue here, and the current lightweight signals are staying relatively quiet.',
      nextStep: 'Use Quick Action to ask Artemis for a narrower project-specific sweep.',
      actionLabel: 'Run quick sweep',
      intent: buildAttentionIntent('No urgent interventions are currently ranked', 'Run a narrower project-specific sweep and tell me what deserves attention next.'),
      kind: 'operational',
    });
  }

  return dedupeByTitle(items).slice(0, 4);
}

function buildNeedsAttentionSourceNote(slackSignals = null) {
  if (slackSignals?.connected) {
    const parts = [];
    if (Number.isFinite(slackSignals?.missedMentions) && slackSignals.missedMentions > 0) {
      parts.push(`${slackSignals.missedMentions} mention${slackSignals.missedMentions === 1 ? '' : 's'}`);
    }
    if (Number.isFinite(slackSignals?.unreadDMs) && slackSignals.unreadDMs > 0) {
      parts.push(`${slackSignals.unreadDMs} unread DM${slackSignals.unreadDMs === 1 ? '' : 's'}`);
    }
    if (Number.isFinite(slackSignals?.replyNeededThreads) && slackSignals.replyNeededThreads > 0) {
      parts.push(`${slackSignals.replyNeededThreads} reply-needed thread${slackSignals.replyNeededThreads === 1 ? '' : 's'}`);
    }
    const summary = parts.length ? parts.join(' · ') : 'no elevated Slack follow-up';
    return `Slack stays a narrow signal source here via Codex: ${summary}.`;
  }
  return 'This queue stays intentionally narrow: approvals, failures, and selective Slack follow-up only.';
}

function buildSystemIssues(providerStatuses, analytics) {
  const issues = [];
  const statuses = Object.values(providerStatuses || {});

  statuses.forEach((status) => {
    if (!status || status.id === 'hermes') return;
    if (status.available === false) {
      issues.push({
        title: `${formatProviderLabel(status.id)} is unavailable`,
        detail: `${formatProviderLabel(status.id)} is not currently installed or reachable from this shell.`,
        status: status.label || 'Unavailable',
        nextStep: 'Use another connected provider before routing new delegated work here.',
      });
      return;
    }
    if (status.connected === false) {
      issues.push({
        title: `${formatProviderLabel(status.id)} needs attention`,
        detail: compactDashboardText(`${formatProviderLabel(status.id)} is visible to the shell but is not ready for normal work.`),
        status: status.label || 'Attention needed',
        nextStep: `Reconnect or avoid ${formatProviderLabel(status.id)} until the runtime is healthy again.`,
      });
    }
  });

  const providerFailures = Array.isArray(analytics?.recentProviderFailures) ? analytics.recentProviderFailures : [];
  providerFailures.slice(0, 2).forEach((item) => {
    issues.push({
      title: `${formatProviderLabel(item.provider_id)} failure: ${item.title || 'Launch issue'}`,
      detail: compactDashboardText(item.body || 'A provider launch or sign-in failure was recorded before a normal reply could complete.'),
      status: 'Recent failure',
      nextStep: 'Retry only after confirming the provider is connected, or switch to a healthy source.',
    });
  });

  return dedupeByTitle(issues).slice(0, 2);
}

function buildModuleRail() {
  return [
    { title: 'Calendar', body: 'Time reality, prep windows, overload, and future work-block awareness.', state: 'Read-only', shellView: CALENDAR_VIEW, actionLabel: 'Open Calendar' },
    { title: 'Meetings', body: 'Prep, notes, follow-up, and decision extraction surface.', state: 'Read-only', shellView: MEETINGS_VIEW, actionLabel: 'Open Meetings' },
    { title: 'Jira Board', body: 'Operational risk, deadlines, and execution queue entry point.', state: 'Read-only', shellView: 'workspace', shellFocus: 'jira-board', actionLabel: 'Open Jira Board' },
    { title: 'OKR Studio', body: 'Goal health, evidence capture, and update-risk workspace.', state: 'Read-only', shellView: 'workspace', shellFocus: 'okr-studio', actionLabel: 'Open OKR Studio' },
    { title: 'Campaign Ops', body: 'Marketing campaign portfolio, gates, handoffs, and reporting.', state: 'Preview', shellView: 'automations', actionLabel: 'Open Campaign Ops' },
    { title: 'Agents', body: 'Durable worker profiles will live here once the agent surfaces fully settle.', state: 'Deferred', shellView: 'agents', actionLabel: 'Open Agents' },
  ];
}

function buildWorkspaceRail() {
  return buildModuleRail().filter((item) => ['Calendar', 'Meetings', 'Jira Board', 'OKR Studio'].includes(item.title));
}

function buildOperationsRail() {
  return [
    { title: 'Agents', body: 'Open the dedicated agent roster, profile, and run-health surface.', state: 'Live', shellView: 'agents', actionLabel: 'Open Agents' },
    { title: 'Skills', body: 'Open the approved capability library and proposal review surface.', state: 'Live', shellView: 'skills', actionLabel: 'Open Skills' },
    { title: 'Workflows', body: 'Open the workflow builder and inspector for saved recipes.', state: 'Live', shellView: 'workflows', actionLabel: 'Open Workflows' },
    { title: 'Campaign Ops', body: 'Open the marketing campaign portfolio, human gates, rejected repository, and reporting contract.', state: 'Preview', shellView: 'automations', actionLabel: 'Open Campaign Ops' },
    { title: 'Memory', body: 'Open the memory surface for scoped knowledge and cleanup.', state: 'Live', shellView: MEMORY_VIEW, actionLabel: 'Open Memory' },
  ];
}

function buildMeetingsModuleViewModel({
  analytics = {},
  providerStatuses = {},
  notifications = [],
  timeReality,
  meetingsOverview = null,
  granolaConnected = false,
  granolaOverview = null,
}) {
  if (meetingsOverview?.status === 'ready') {
    return { ...buildLiveMeetingsModuleViewModel(meetingsOverview, timeReality), granolaConnected };
  }
  if (meetingsOverview?.status === 'not_configured' || meetingsOverview?.status === 'source_error') {
    return { ...buildMeetingsSetupViewModel(meetingsOverview, timeReality), granolaConnected };
  }
  // J6a path: /api/meetings/overview no longer emits the Node-era 'ready'
  // shape, but Granola is connected and has real meeting data. Surface a
  // Live view filtered to today's meetings.
  if (granolaConnected) {
    return buildGranolaLiveMeetingsViewModel({ granolaOverview, timeReality });
  }

  return {
    badge: 'Read-only',
    statusTone: 'setup',
    granolaConnected,
    summary: [
      { label: 'Meeting Load', value: formatMeetingLoad(timeReality.meetingLoad) },
      { label: 'Next Hard Stop', value: timeReality.nextHardStop || 'Not set' },
      { label: 'Module State', value: 'Read-only / manual-first' },
    ],
    readinessNotes: buildMeetingReadinessNotes(timeReality, notifications, providerStatuses, analytics),
    prepLens: buildMeetingPrepLens(timeReality, notifications, providerStatuses, analytics),
    followUpPressure: buildMeetingFollowUpPressure(notifications, providerStatuses, analytics),
    sourceNote: `This Meetings surface stays read-only, uses the same local shell signals, and does not widen Chat, Task Command, or planner behavior. ${buildManualTimeRealityStatus(timeReality)}`.trim(),
    actions: [
      { label: 'Prep a Meeting', intent: 'Help me prep for today\'s most important meeting.' },
      { label: 'Sort Follow-Up', intent: 'Help me turn today\'s meeting follow-up into a compact action list.' },
      { label: 'Reframe the Day', intent: 'Help me reorganize today around my meeting load and hard stop.' },
    ],
  };
}

// Build a Live-style viewmodel from Granola's meeting list. Filters to today
// using either `date_ms` (preferred) or by parsing `date` text.
function buildGranolaLiveMeetingsViewModel({ granolaOverview, timeReality }) {
  const all = Array.isArray(granolaOverview?.meetings) ? granolaOverview.meetings : [];
  const now = new Date();
  const sod = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const eod = sod + 86_400_000;

  const meetingMs = (m) => {
    if (typeof m.date_ms === 'number' && m.date_ms > 0) return m.date_ms;
    const parsed = m.date ? Date.parse(m.date) : NaN;
    return Number.isFinite(parsed) ? parsed : 0;
  };

  const todayMeetings = all
    .map((m) => ({ m, ts: meetingMs(m) }))
    .filter(({ ts }) => ts >= sod && ts < eod)
    .sort((a, b) => a.ts - b.ts)
    .map(({ m, ts }) => {
      const d = ts > 0 ? new Date(ts) : null;
      const startLabel = d
        ? d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
        : (m.date || '');
      return {
        id: m.id,
        title: m.title || 'Untitled meeting',
        startLabel,
        endLabel: '',
        location: (m.participants || []).join(', '),
        status: ts < Date.now() ? 'past' : 'scheduled',
      };
    });

  const nextUpcoming = todayMeetings.find((m) => m.status === 'scheduled');
  const nextLabel = nextUpcoming
    ? `${nextUpcoming.startLabel} · ${nextUpcoming.title}`
    : (todayMeetings.length ? 'All today\'s meetings done' : 'No meetings today');

  return {
    badge: 'Live',
    statusTone: 'live',
    granolaConnected: true,
    granolaTodayMode: true,
    dateLabel: now.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }),
    sourceLabel: 'Granola',
    nextMeetingLabel: nextLabel,
    todayMeetings,
    summary: [
      { label: 'Today', value: `${todayMeetings.length} meeting${todayMeetings.length === 1 ? '' : 's'}` },
      { label: 'Next', value: nextLabel },
      { label: 'Source', value: 'Granola' },
    ],
    readinessNotes: [],
    prepLens: [],
    followUpPressure: [],
    actions: [
      { label: 'Prep a Meeting', intent: 'Help me prep for today\'s next meeting using my Granola schedule.' },
      { label: 'Sort Follow-Up', intent: 'Help me turn today\'s meeting follow-up into a compact action list.' },
      { label: 'Reframe the Day', intent: 'Help me reorganize today around my Granola meetings.' },
    ],
  };
}

function buildLiveMeetingsModuleViewModel(meetingsOverview, timeReality) {
  const today = meetingsOverview.today || {};
  const nextMeeting = meetingsOverview.nextMeeting
    ? `${meetingsOverview.nextMeeting.startLabel} · ${meetingsOverview.nextMeeting.title}`
    : 'No more meetings today';

  return {
    badge: 'Live',
    statusTone: 'live',
    sourceLabel: meetingsOverview.sourceName || 'calendar file',
    configPath: meetingsOverview.configPath || 'calendar-source.json',
    dateLabel: meetingsOverview.dateLabel || '',
    nextMeetingLabel: nextMeeting,
    todayMeetings: (today.meetings || []).map((m) => ({ ...m })),
    summary: [
      { label: 'Next Meeting', value: nextMeeting },
      { label: 'Notes Captured', value: String(today.notesCount || 0) },
      { label: 'Follow-Up Queue', value: String(today.followUpCount || 0) },
    ],
    readinessNotes: (today.readinessNotes || []).slice(0, 4),
    prepLens: (today.prepItems || []).slice(0, 6).map((item) => ({
      title: item.title,
      detail: item.detail,
    })),
    followUpPressure: (today.followUpItems || []).slice(0, 6).map((item) => ({
      title: item.title,
      detail: item.detail,
    })),
    sourceNote: `Meetings is now using live ICS events as the scheduling spine for ${meetingsOverview.dateLabel}. Source: ${meetingsOverview.sourceName || 'calendar file'} at ${meetingsOverview.configPath}. ${describeTimeRealityLead(timeReality) || buildManualTimeRealityStatus(timeReality)}`.trim(),
    actions: [
      { label: 'Prep a Meeting', intent: `Help me prep today's next meeting using my live schedule. Next meeting: ${nextMeeting}.` },
      { label: 'Sort Follow-Up', intent: `Help me turn today's captured meeting follow-up into a compact action list. Current queue: ${today.followUpCount || 0}.` },
      { label: 'Reframe the Day', intent: `Help me reorganize today around my live meetings schedule. Next meeting: ${nextMeeting}.` },
    ],
  };
}

function buildMeetingsSetupViewModel(meetingsOverview, timeReality) {
  const isError = meetingsOverview?.status === 'source_error';
  const sourcePath = meetingsOverview?.sourcePath || 'No .ics file configured yet';

  return {
    badge: isError ? 'Source error' : 'Needs setup',
    statusTone: isError ? 'error' : 'setup',
    sourceLabel: meetingsOverview?.providerLabel || 'Calendar-backed Meetings',
    configPath: meetingsOverview?.configPath || 'calendar-source.json',
    sourcePath,
    summary: [
      { label: 'Source', value: meetingsOverview?.providerLabel || 'Calendar-backed Meetings' },
      { label: 'Config', value: meetingsOverview?.configPath || 'calendar-source.json' },
      { label: 'Module State', value: isError ? 'Configured / unreadable' : 'Waiting for live calendar source' },
    ],
    readinessNotes: [
      isError
        ? `Meetings could not assemble today's meeting objects because the calendar source at ${sourcePath} failed to load.`
        : 'Meetings is waiting for the same ICS calendar source that powers Calendar before it can build prep, notes, and follow-up objects.',
      buildManualTimeRealityStatus(timeReality),
    ],
    prepLens: [{
      title: isError ? 'Calendar source failed' : 'Connect the calendar spine first',
      detail: isError
        ? `Fix the calendar source path in ${meetingsOverview?.configPath || 'calendar-source.json'} so Meetings can rebuild from real events.`
        : `Add a readable .ics path in ${meetingsOverview?.configPath || 'calendar-source.json'} so Meetings can derive real prep objects from the live schedule.`,
    }],
    followUpPressure: [{
      title: 'No real meeting objects yet',
      detail: 'Follow-up stays intentionally conservative until the live calendar source is connected and Artemis can see actual meetings.',
    }],
    sourceNote: isError
      ? `Meetings setup is partially in place, but Artemis could not read ${sourcePath}.`
      : `Meetings is ready to use the same local ICS source as Calendar. Configure ${meetingsOverview?.configPath || 'calendar-source.json'} first, then this surface will replace proxy meeting posture with real objects.`,
    actions: [
      { label: 'Prep a Meeting', intent: 'Help me prep today\'s meetings while I finish connecting the live calendar source.' },
      { label: 'Sort Follow-Up', intent: 'Help me sort likely meeting follow-up while the live meetings source is still being configured.' },
      { label: 'Reframe the Day', intent: 'Help me reorganize today while my meetings source is still coming online.' },
    ],
  };
}

function buildCalendarModuleViewModel({
  analytics = {},
  providerStatuses = {},
  notifications = [],
  timeReality,
  calendarOverview = null,
}) {
  if (calendarOverview?.status === 'ready') {
    return buildLiveCalendarModuleViewModel(calendarOverview, notifications);
  }
  if (calendarOverview?.status === 'not_configured' || calendarOverview?.status === 'source_error') {
    return buildCalendarSetupViewModel(calendarOverview, timeReality);
  }

  return {
    badge: 'Read-only',
    statusTone: 'setup',
    summary: [
      { label: 'Next Hard Stop', value: timeReality.nextHardStop || 'Not set' },
      { label: 'Focus Bias', value: formatFocusPreference(timeReality.focusPreference) },
      { label: 'Module State', value: 'Read-only / manual-first' },
    ],
    scheduleRead: buildCalendarScheduleRead(timeReality, notifications, analytics),
    workBlockLens: buildCalendarWorkBlockLens(timeReality, notifications, analytics),
    overloadSignals: buildCalendarOverloadSignals(timeReality, providerStatuses, notifications, analytics),
    transitionBuffers: buildCalendarTransitionBuffers(timeReality, providerStatuses, notifications, analytics),
    sourceNote: `This first Calendar slice stays read-only and manual-first. It uses the same local shell signals as Meetings, does not fetch live events, and does not change Morning Brief, Task Command, or planner behavior. ${buildManualTimeRealityStatus(timeReality)}`.trim(),
    actions: [
      { label: 'Frame My Day', intent: 'Help me frame today around my current time reality and likely calendar pressure.' },
      { label: 'Protect a Work Block', intent: 'Help me protect the best work block based on my current time reality.' },
      { label: 'Prep Transitions', intent: 'Help me prep transitions and buffers around today\'s likely meeting pressure.' },
    ],
  };
}

function buildLiveCalendarModuleViewModel(calendarOverview, notifications = []) {
  const today = calendarOverview.today || {};
  const nextEvent = calendarOverview.nextEvent
    ? `${calendarOverview.nextEvent.startLabel} · ${calendarOverview.nextEvent.title}`
    : 'No more meetings today';
  const longestFreeBlock = [...(today.focusBlocks || []), ...(today.freeBlocks || [])]
    .sort((left, right) => (right.durationMinutes || 0) - (left.durationMinutes || 0))[0] || null;
  const unreadSignals = notifications.filter((item) => !item?.read_at).length;

  return {
    badge: 'Live',
    statusTone: 'live',
    sourceLabel: calendarOverview.sourceName || 'calendar file',
    configPath: calendarOverview.configPath || 'calendar-source.json',
    dateLabel: calendarOverview.dateLabel || '',
    timezone: calendarOverview.timezone || '',
    nextEventLabel: nextEvent,
    longestFreeBlockLabel: longestFreeBlock ? `${longestFreeBlock.startLabel}–${longestFreeBlock.endLabel}` : null,
    todayEvents: (today.events || []).map((event) => ({ ...event })),
    focusBlocks: (today.focusBlocks || []).map((block) => ({ ...block })),
    freeBlocks: (today.freeBlocks || []).map((block) => ({ ...block })),
    transitionSignals: (today.transitionSignals || []).slice(),
    meetingsCount: today.meetingsCount || 0,
    busyMinutes: today.busyMinutes || 0,
    prepCount: today.prepCount || 0,
    summary: [
      { label: 'Next Event', value: nextEvent },
      { label: 'Meetings Today', value: String(today.meetingsCount || 0) },
      { label: 'Best Free Block', value: longestFreeBlock ? `${longestFreeBlock.startLabel}–${longestFreeBlock.endLabel}` : 'None open' },
    ],
    scheduleRead: (today.events || []).slice(0, 4).map((event) => ({
      title: `${event.startLabel}–${event.endLabel} · ${event.title}`,
      detail: event.location
        ? `${event.durationLabel} · ${event.location}`
        : `${event.durationLabel} · ${event.status === 'upcoming' ? 'Upcoming' : event.status === 'in_progress' ? 'In progress' : 'Completed'}`,
      tone: event.status === 'in_progress' ? 'live' : 'scheduled',
    })),
    workBlockLens: (today.freeBlocks || []).slice(0, 3).map((block) => ({
      title: `${block.startLabel}–${block.endLabel}`,
      detail: `${block.durationLabel} of open time is currently visible from the calendar source.`,
      tone: (block.durationMinutes || 0) >= 60 ? 'focus' : 'buffer',
    })),
    overloadSignals: [
      {
        title: today.busyMinutes >= 300 ? 'Meeting-heavy day' : 'Calendar load looks manageable',
        detail: `${today.meetingsCount || 0} event${today.meetingsCount === 1 ? '' : 's'} and ${formatMinutesAsReadableDuration(today.busyMinutes || 0)} of scheduled time are visible today.`,
      },
      {
        title: today.prepCount ? 'Prep window is tightening' : 'Prep load is light',
        detail: today.prepCount
          ? `${today.prepCount} upcoming event${today.prepCount === 1 ? '' : 's'} start within the next 90 minutes.`
          : 'No upcoming event starts inside the next 90 minutes.',
      },
      {
        title: unreadSignals ? 'Operational signals may spill into the calendar' : 'Calendar read is cleaner today',
        detail: unreadSignals
          ? `${unreadSignals} unread notification${unreadSignals === 1 ? '' : 's'} still sit outside the schedule and may compress buffers.`
          : 'Unread notifications are quiet enough that the calendar can be read more directly.',
      },
    ],
    transitionBuffers: (today.transitionSignals || []).length
      ? today.transitionSignals.map((detail, index) => ({
        title: `Transition ${index + 1}`,
        detail,
      }))
      : [{
        title: 'Buffers are visible',
        detail: 'No back-to-back or sub-15-minute transitions are visible in today\'s current calendar window.',
      }],
    sourceNote: `Calendar is now reading the configured ICS source (${calendarOverview.sourceName || 'calendar file'}) for ${calendarOverview.dateLabel}. Config lives at ${calendarOverview.configPath}.`,
    actions: [
      { label: 'Frame My Day', intent: `Help me frame today using my live calendar. Next event: ${nextEvent}.` },
      { label: 'Protect a Work Block', intent: `Help me protect the best visible free block today. Best open block: ${longestFreeBlock ? `${longestFreeBlock.startLabel}-${longestFreeBlock.endLabel}` : 'none visible yet'}.` },
      { label: 'Prep Transitions', intent: `Help me prep the tightest transitions in my live calendar for ${calendarOverview.dateLabel}.` },
    ],
  };
}

function buildCalendarSetupViewModel(calendarOverview, timeReality) {
  const isError = calendarOverview?.status === 'source_error';
  const sourcePath = calendarOverview?.sourcePath || 'No .ics file configured yet';
  return {
    badge: isError ? 'Source error' : 'Needs setup',
    statusTone: isError ? 'error' : 'setup',
    sourceLabel: calendarOverview?.providerLabel || 'Google Calendar ICS',
    configPath: calendarOverview?.configPath || 'calendar-source.json',
    sourcePath,
    summary: [
      { label: 'Source', value: calendarOverview?.providerLabel || 'Google Calendar ICS' },
      { label: 'Config', value: calendarOverview?.configPath || 'calendar-source.json' },
      { label: 'Module State', value: isError ? 'Configured / unreadable' : 'Waiting for .ics path' },
    ],
    scheduleRead: [{
      title: isError ? 'Calendar source could not be read' : 'Connect a local calendar export',
      detail: isError
        ? `Artemis tried to read ${sourcePath} and failed. Point calendar-source.json at a readable .ics file.`
        : `Add an .ics path in ${calendarOverview?.configPath || 'calendar-source.json'} so Calendar can read real events instead of shell inference only.`,
    }],
    workBlockLens: [{
      title: 'First live source',
      detail: 'This slice expects a local .ics source, which can be a Google Calendar export or a synced private ICS mirror.',
    }],
    overloadSignals: [{
      title: 'Manual time reality still applies',
      detail: buildManualTimeRealityStatus(timeReality),
    }],
    transitionBuffers: [{
      title: 'No live edges yet',
      detail: 'Transition buffers will start reading from real event gaps as soon as the calendar source is configured.',
    }],
    sourceNote: isError
      ? `Calendar setup is partially in place, but Artemis could not read ${sourcePath}.`
      : `Calendar is ready for its first live source. Configure ${calendarOverview?.configPath || 'calendar-source.json'} with a local .ics path to replace the synthetic shell read.`,
    actions: [
      { label: 'Frame My Day', intent: 'Help me frame today while I finish connecting my calendar source.' },
      { label: 'Protect a Work Block', intent: 'Help me protect a work block while my calendar source is still being configured.' },
      { label: 'Prep Transitions', intent: 'Help me think through likely transitions before my live calendar source is wired.' },
    ],
  };
}

function formatMinutesAsReadableDuration(minutes) {
  if (!Number.isFinite(minutes) || minutes <= 0) return '0 min';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

function buildJiraModuleViewModel({ analytics = {}, providerStatuses = {}, notifications = [], timeReality }) {
  return {
    summary: [
      { label: 'Board Read', value: summarizeJiraBoardRead(timeReality, notifications, analytics) },
      { label: 'Next Hard Stop', value: timeReality.nextHardStop || 'Not set' },
      { label: 'Module State', value: 'Read-only / local-signal only' },
    ],
    queueRead: buildJiraQueueRead(timeReality, notifications, analytics),
    deliveryPressure: buildJiraDeliveryPressure(timeReality, notifications, analytics),
    executionRisk: buildJiraExecutionRisk(providerStatuses, notifications, analytics),
    sourceNote: `This first Jira Board slice stays read-only and local-signal driven. It does not connect to Jira, does not claim real tickets or sprint state, and does not change Morning Brief, Task Command, or planner behavior. ${buildManualTimeRealityStatus(timeReality)}`.trim(),
    actions: [
      { label: 'Triage Delivery Risk', intent: 'Help me triage delivery risk from the current Jira Board signals.' },
      { label: 'Surface Likely Blockers', intent: 'Help me turn the current Jira Board signals into likely blockers and next actions.' },
      { label: 'Draft a Status Update', intent: 'Help me draft a concise status update from the current Jira Board posture.' },
    ],
  };
}

function buildOkrModuleViewModel({ analytics = {}, providerStatuses = {}, notifications = [], timeReality }) {
  return {
    summary: [
      { label: 'Goal Posture', value: summarizeOkrPosture(timeReality, notifications, analytics) },
      { label: 'Next Hard Stop', value: timeReality.nextHardStop || 'Not set' },
      { label: 'Module State', value: 'Read-only / local-signal only' },
    ],
    goalHealth: buildOkrGoalHealth(timeReality, notifications, analytics),
    evidenceInbox: buildOkrEvidenceInbox(notifications, analytics),
    updateRisk: buildOkrUpdateRisk(providerStatuses, notifications, analytics),
    sourceNote: `This first OKR Studio slice stays read-only and local-signal driven. It does not connect to OKR systems, does not claim real objective status or milestone progress, and does not change Morning Brief, Task Command, or planner behavior. ${buildManualTimeRealityStatus(timeReality)}`.trim(),
    actions: [
      { label: 'Draft an OKR Update', intent: 'Help me draft a concise OKR update from the current OKR Studio posture.' },
      { label: 'Capture Wins', intent: 'Help me turn today\'s visible signals into wins, evidence, and notes for OKR tracking.' },
      { label: 'Spot Goal Risk', intent: 'Help me surface likely OKR risk from the current shell signals.' },
    ],
  };
}

// ── Jira swimlane helpers ──────────────────────────────────────

const _JIRA_AVATAR_PALETTE = [
  '#8B6B3D', '#5E7A8B', '#7E5A7E', '#4F7A65',
  '#8B4513', '#4A6741', '#7A5C6E', '#5A6B8A',
];

function _jiraAvatarColor(id) {
  if (!id) return '#8F8576';
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return _JIRA_AVATAR_PALETTE[h % _JIRA_AVATAR_PALETTE.length];
}

function _jiraInitials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function _jiraPrioCls(priority) {
  const p = (priority || '').toLowerCase();
  if (p === 'high' || p === 'highest' || p === 'critical') return 'high';
  if (p === 'medium') return 'med';
  return 'low';
}

function _jiraAge(isoDate) {
  if (!isoDate) return '';
  const ms = Date.now() - new Date(isoDate).getTime();
  const days = Math.floor(ms / 86400000);
  if (days < 1) return '<1d';
  if (days < 7) return `${days}d`;
  if (days < 30) return `${Math.floor(days / 7)}w`;
  if (days < 365) return `${Math.floor(days / 30)}mo`;
  return `${Math.floor(days / 365)}y`;
}

function buildJiraDedicatedViewModel({ jiraOverview = null } = {}) {
  if (!jiraOverview || !jiraOverview.connected) {
    return {
      badge: 'Needs setup',
      statusTone: 'setup',
      sourceLabel: '',
      columns: null,
      swimlanes: null,
    };
  }

  const rawCols = jiraOverview.columns || [];

  // Build colStatusMap: colKey → [status name, …] from actual card data.
  // Used by drag/drop to resolve transition IDs without needing config.
  const colStatusMap = {};
  for (const col of rawCols) {
    const statuses = [...new Set((col.items || []).map(i => i.status).filter(Boolean))];
    // Fallback to the column label if no cards carry a status
    colStatusMap[col.key] = statuses.length ? statuses : [col.label];
  }

  // Collect assignees who appear in any visible column
  const peopleMap = new Map(); // accountId → { id, name, initials, color }
  for (const col of rawCols) {
    for (const item of col.items || []) {
      if (item.assigneeId && !peopleMap.has(item.assigneeId)) {
        peopleMap.set(item.assigneeId, {
          id: item.assigneeId,
          name: item.assignee || item.assigneeId,
          initials: _jiraInitials(item.assignee),
          color: _jiraAvatarColor(item.assigneeId),
        });
      }
    }
  }

  const people = [...peopleMap.values()].sort((a, b) => a.name.localeCompare(b.name));
  // Unassigned row always appears last
  const UNASSIGNED_ID = '__unassigned__';
  people.push({ id: UNASSIGNED_ID, name: 'Unassigned', initials: '–', color: '#B8AE9D' });

  const COL_KEYS = ['todo', 'prog', 'blocked', 'review'];
  const colByKey = {};
  for (const col of rawCols) colByKey[col.key] = col;

  // Build swimlanes
  const swimlanes = people.map(person => {
    const cells = {};
    for (const key of COL_KEYS) {
      const col = colByKey[key];
      if (!col) { cells[key] = []; continue; }
      cells[key] = (col.items || []).filter(item =>
        person.id === UNASSIGNED_ID ? !item.assigneeId : item.assigneeId === person.id
      ).map(item => ({
        key: item.key,
        title: item.title,
        prioCls: _jiraPrioCls(item.priority),
        prioLabel: item.priority || '',
        tags: (item.labels || []).slice(0, 3),
        assigneeId: item.assigneeId || '',
        assigneeName: item.assignee || '',
        commentCount: item.commentCount || 0,
        attachCount: item.attachmentCount || 0,
        worklogTotal: Math.round((item.worklogTotal || 0) * 10) / 10,
        age: _jiraAge(item.created),
        colKey: key,
        sprint: item.sprint || '',
      }));
    }
    // Skip person rows with zero cards across all columns
    const total = COL_KEYS.reduce((s, k) => s + cells[k].length, 0);
    return total > 0 || person.id === UNASSIGNED_ID ? { person, cells } : null;
  }).filter(Boolean);

  const allItems = rawCols.flatMap(c => c.items || []);
  const stats = {
    prog: allItems.filter(i => (colByKey.prog?.items || []).includes(i)).length
      || (colByKey.prog?.items || []).length,
    blocked: (colByKey.blocked?.items || []).length,
    review: (colByKey.review?.items || []).length,
  };

  // Derive filter option lists from actual issue data.
  const assignees = [...peopleMap.values()].sort((a, b) => a.name.localeCompare(b.name));
  assignees.push({ id: '__unassigned__', name: 'Unassigned' });

  const prioritySet = new Set();
  for (const col of rawCols) {
    for (const item of col.items || []) {
      if (item.priority) prioritySet.add(item.priority);
    }
  }
  const PRIORITY_ORDER = ['Highest', 'High', 'Medium', 'Low', 'Lowest'];
  const priorities = [
    ...PRIORITY_ORDER.filter(p => prioritySet.has(p)),
    ...[...prioritySet].filter(p => !PRIORITY_ORDER.includes(p)).sort(),
  ];
  if (allItems.some(i => !i.priority)) priorities.push('None');

  const sprintSet = new Set();
  for (const col of rawCols) {
    for (const item of col.items || []) {
      if (item.sprint) sprintSet.add(item.sprint);
    }
  }
  const sprints = [...sprintSet].sort();

  return {
    badge: 'Live',
    statusTone: 'live',
    sourceLabel: jiraOverview.siteUrl || 'Atlassian',
    siteUrl: jiraOverview.siteUrl || '',
    columns: rawCols,
    swimlanes,
    stats,
    colStatusMap,
    assignees,
    priorities,
    sprints,
    projectKey: jiraOverview.savedConfig?.projectKey || '',
  };
}


function buildMeetingReadinessNotes(timeReality, notifications, providerStatuses, analytics) {
  const notes = [];

  if (timeReality.meetingLoad === 'heavy') {
    notes.push('Meeting load is manually marked heavy, so this module is treating prep and transitions as part of the work rather than hidden overhead.');
  } else if (timeReality.meetingLoad === 'moderate') {
    notes.push('Meeting load is manually marked moderate, so prep should stay compact and tied to the highest-signal conversation.');
  } else if (timeReality.meetingLoad === 'light') {
    notes.push('Meeting load is manually marked light, which makes this a good day to keep prep narrow and protect deeper work around it.');
  } else {
    notes.push('Meeting load is still unset, so this module is staying conservative until you tell it how crowded the day is.');
  }

  if (timeReality.nextHardStop) {
    notes.push(`The next hard stop is ${timeReality.nextHardStop}, so any meeting prep should be biased toward the block before then.`);
  }

  if (timeReality.focusPreference === 'after-meetings') {
    notes.push('Focus bias is set to after meetings, so this slice is assuming deep work belongs after the coordination layer clears.');
  }

  if (notifications.length) {
    notes.push(`${notifications.length} unread operational notification${notifications.length === 1 ? '' : 's'} are available as a lightweight proxy for follow-up pressure until a true meetings/notes adapter exists.`);
  }

  const disconnectedProviders = Object.values(providerStatuses || {}).filter((status) => status?.available && status.connected === false).length;
  if (disconnectedProviders) {
    notes.push(`${disconnectedProviders} provider${disconnectedProviders === 1 ? '' : 's'} still need attention, so this module is staying read-only instead of pretending meeting actions can execute safely.`);
  }

  const providerFailures = Number(analytics?.providerFailures?.reduce?.((sum, item) => sum + Number(item.failures || 0), 0) || 0);
  if (providerFailures) {
    notes.push(`Recent provider failures (${providerFailures}) are a reminder to treat meeting prep and follow-up as planning support first, not auto-run automation.`);
  }

  return notes.slice(0, 4);
}

function buildMeetingPrepLens(timeReality, notifications, providerStatuses, analytics) {
  const prepLens = [];
  const approvals = notifications.filter((item) => item.type === 'approval');
  const agentSignals = notifications.filter((item) => item.type === 'agent');
  const disconnectedProviders = Object.values(providerStatuses || {}).filter((status) => status?.available && status.connected === false);
  const providerFailures = Number(analytics?.providerFailures?.reduce?.((sum, item) => sum + Number(item.failures || 0), 0) || 0);

  prepLens.push({
    title: 'Prep posture',
    detail: describeMeetingPrepPosture(timeReality),
  });

  if (approvals.length) {
    prepLens.push({
      title: 'Decision prep likely matters',
      detail: `${approvals.length} unread approval${approvals.length === 1 ? '' : 's'} suggest at least one conversation may need a crisp decision frame or pre-read before it starts.`,
    });
  } else if (agentSignals.length) {
    prepLens.push({
      title: 'Follow-up context is active',
      detail: `${agentSignals.length} unread agent signal${agentSignals.length === 1 ? '' : 's'} suggest a meeting may need a short status check or unblock note, even though this module still has no true notes adapter.`,
    });
  } else {
    prepLens.push({
      title: 'Prep stays intentionally narrow',
      detail: 'No direct meeting objects are connected yet, so this slice is only elevating likely prep pressure instead of pretending it knows the exact agenda.',
    });
  }

  if (disconnectedProviders.length || providerFailures) {
    prepLens.push({
      title: 'Execution constraint',
      detail: `${disconnectedProviders.length || providerFailures} provider signal${disconnectedProviders.length + providerFailures === 1 ? '' : 's'} are unstable, so prep support should bias toward checklists, talking points, and draft notes rather than automation.`,
    });
  } else {
    prepLens.push({
      title: 'Execution constraint',
      detail: 'Connected providers look stable enough for chat support, but this slice still stays read-only until calendar and meetings adapters land.',
    });
  }

  return prepLens.slice(0, 3);
}

function buildMeetingFollowUpPressure(notifications, providerStatuses, analytics) {
  const followUp = [];
  const unreadNotifications = notifications.filter((item) => !item.read_at);
  const approvals = unreadNotifications.filter((item) => item.type === 'approval');
  const agentSignals = unreadNotifications.filter((item) => item.type === 'agent');
  const runtimeSignals = unreadNotifications.filter((item) => item.type === 'error');
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors : [];
  const disconnectedProviders = Object.values(providerStatuses || {}).filter((status) => status?.available && status.connected === false);

  followUp.push({
    title: 'Current pressure signal',
    detail: unreadNotifications.length
      ? `${unreadNotifications.length} unread operational item${unreadNotifications.length === 1 ? '' : 's'} are acting as a temporary proxy for meeting follow-up pressure.`
      : 'No unread operational signals are currently pushing follow-up pressure into the shell.',
  });

  if (approvals.length || agentSignals.length) {
    followUp.push({
      title: 'Likely post-meeting outputs',
      detail: buildMeetingFollowUpOutputsCopy(approvals.length, agentSignals.length),
    });
  } else {
    followUp.push({
      title: 'Likely post-meeting outputs',
      detail: 'The shell is not seeing strong approval or agent signals right now, so follow-up likely stays limited to personal notes or a short recap.',
    });
  }

  if (runtimeSignals.length || recentErrors.length || disconnectedProviders.length) {
    followUp.push({
      title: 'Risk to clean handoff',
      detail: 'Provider/runtime instability is still visible, so any follow-up that depends on delegation or tool runs should be treated as draft-first and verified before sending.',
    });
  } else {
    followUp.push({
      title: 'Risk to clean handoff',
      detail: 'No major runtime risk is being elevated here, but this slice still cannot verify true meeting completion because notes and calendar connectors are not wired in yet.',
    });
  }

  return followUp.slice(0, 3);
}

function buildCalendarScheduleRead(timeReality, notifications, analytics) {
  const items = [];
  const unreadNotifications = notifications.filter((item) => !item.read_at);
  const providerFailures = Number(analytics?.providerFailures?.reduce?.((sum, item) => sum + Number(item.failures || 0), 0) || 0);

  items.push({
    title: 'Schedule posture',
    detail: describeCalendarPosture(timeReality),
  });

  if (timeReality.nextHardStop) {
    items.push({
      title: 'Known boundary',
      detail: `The only explicit calendar boundary in this slice is the manual hard stop at ${timeReality.nextHardStop}, so all timing advice stays anchored to that known edge instead of inventing live events.`,
    });
  } else {
    items.push({
      title: 'Known boundary',
      detail: 'No hard stop is set yet, so this slice can only suggest broad timing posture rather than a confident sequence of meeting windows.',
    });
  }

  items.push({
    title: 'Confidence level',
    detail: unreadNotifications.length || providerFailures
      ? `This read is using ${unreadNotifications.length} unread operational signal${unreadNotifications.length === 1 ? '' : 's'} and ${providerFailures} recent provider failure${providerFailures === 1 ? '' : 's'} as indirect calendar pressure hints.`
      : 'This read has low confidence by design because no live calendar objects are wired in yet.',
  });

  return items.slice(0, 3);
}

function buildCalendarWorkBlockLens(timeReality, notifications, analytics) {
  const items = [];
  const unreadApprovals = notifications.filter((item) => item.type === 'approval' && !item.read_at).length;
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;

  items.push({
    title: 'Best candidate block',
    detail: inferBestWorkBlock(analytics, timeReality),
  });

  if (timeReality.meetingLoad === 'heavy') {
    items.push({
      title: 'Protection rule',
      detail: 'A heavy meeting day suggests protecting one smaller deliberate block instead of assuming multiple deep-work windows will survive.',
    });
  } else if (timeReality.focusPreference === 'after-meetings') {
    items.push({
      title: 'Protection rule',
      detail: 'The declared focus bias is after meetings, so the safest block to protect is the first clean window after coordination ends.',
    });
  } else {
    items.push({
      title: 'Protection rule',
      detail: 'Without live event timing, this slice is protecting the most likely clean block rather than claiming exact availability.',
    });
  }

  items.push({
    title: 'What could break the block',
    detail: unreadApprovals || recentErrors
      ? `${unreadApprovals} approval signal${unreadApprovals === 1 ? '' : 's'} and ${recentErrors} recent runtime error${recentErrors === 1 ? '' : 's'} could still puncture the block if they are not triaged first.`
      : 'No strong approval or runtime interruptions are being elevated right now, but true event collisions still cannot be verified in this slice.',
  });

  return items.slice(0, 3);
}

function buildCalendarOverloadSignals(timeReality, providerStatuses, notifications, analytics) {
  const items = [];
  const disconnectedProviders = Object.values(providerStatuses || {}).filter((status) => status?.available && status.connected === false).length;
  const unreadNotifications = notifications.filter((item) => !item.read_at).length;
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;

  items.push({
    title: 'Load signal',
    detail: timeReality.meetingLoad === 'heavy'
      ? 'Manual time reality says the day is meeting-heavy, so overload risk is being treated as real even without a connected calendar.'
      : timeReality.meetingLoad === 'moderate'
        ? 'Manual time reality says the day has moderate meeting pressure, so this slice is looking for compression rather than full overload.'
        : timeReality.meetingLoad === 'light'
          ? 'Manual time reality says the meeting load is light, so overload is more likely to come from interruptions than from the schedule itself.'
          : 'Meeting load is not set, so overload detection stays conservative and avoids pretending the calendar is known.',
  });

  items.push({
    title: 'Compression risk',
    detail: timeReality.nextHardStop
      ? `The next known compression point is ${timeReality.nextHardStop}; this slice assumes prep, transitions, and interruptions all have to fit before that wall.`
      : 'No hard stop is set, so compression risk is estimated from indirect operational pressure only.',
  });

  items.push({
    title: 'Operational spillover',
    detail: disconnectedProviders || unreadNotifications || recentErrors
      ? `${unreadNotifications} unread item${unreadNotifications === 1 ? '' : 's'}, ${recentErrors} recent error${recentErrors === 1 ? '' : 's'}, and ${disconnectedProviders} disconnected provider${disconnectedProviders === 1 ? '' : 's'} are being treated as spillover risk around the schedule.`
      : 'No strong operational spillover is being elevated, but this slice still cannot separate true calendar overload from a quiet day with missing event data.',
  });

  return items.slice(0, 3);
}

function buildCalendarTransitionBuffers(timeReality, providerStatuses, notifications, analytics) {
  const items = [];
  const unreadNotifications = notifications.filter((item) => !item.read_at);
  const approvals = unreadNotifications.filter((item) => item.type === 'approval').length;
  const runtimeSignals = unreadNotifications.filter((item) => item.type === 'error').length;
  const disconnectedProviders = Object.values(providerStatuses || {}).filter((status) => status?.available && status.connected === false).length;
  const providerFailures = Number(analytics?.providerFailures?.reduce?.((sum, item) => sum + Number(item.failures || 0), 0) || 0);

  items.push({
    title: 'Buffer posture',
    detail: describeCalendarBufferPosture(timeReality),
  });

  items.push({
    title: 'What likely needs margin',
    detail: approvals || runtimeSignals
      ? `${approvals} approval signal${approvals === 1 ? '' : 's'} and ${runtimeSignals} runtime signal${runtimeSignals === 1 ? '' : 's'} suggest the day may need small decision or recovery buffers between work blocks, even though exact events are still unknown.`
      : 'With no strong approval or runtime pressure visible, this slice assumes only light transition buffers until a real calendar adapter exists.',
  });

  items.push({
    title: 'Confidence guardrail',
    detail: disconnectedProviders || providerFailures
      ? `Provider instability is still visible (${disconnectedProviders} disconnected, ${providerFailures} recent failure${providerFailures === 1 ? '' : 's'}), so transition advice stays draft-like instead of pretending exact buffer windows are known.`
      : 'This section is still an inference layer only: it can suggest where buffers probably belong, but it cannot verify real meeting edges without live calendar data.',
  });

  return items.slice(0, 3);
}

function buildJiraQueueRead(timeReality, notifications, analytics) {
  const items = [];
  const unreadNotifications = notifications.filter((item) => !item.read_at);
  const approvals = unreadNotifications.filter((item) => item.type === 'approval').length;
  const agentSignals = unreadNotifications.filter((item) => item.type === 'agent').length;
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;
  const providerFailures = Number(analytics?.providerFailures?.reduce?.((sum, item) => sum + Number(item.failures || 0), 0) || 0);

  items.push({
    title: 'Board posture',
    detail: describeJiraBoardPosture(timeReality),
  });

  items.push({
    title: 'What is surfacing',
    detail: approvals || agentSignals
      ? `${approvals} approval signal${approvals === 1 ? '' : 's'} and ${agentSignals} agent signal${agentSignals === 1 ? '' : 's'} are acting as the current proxy for work that may be waiting on decisions, unblock notes, or owner clarity.`
      : 'No strong approval or agent signals are currently surfacing likely queue movement, so this slice assumes the board may be quieter than it appears.',
  });

  items.push({
    title: 'Confidence level',
    detail: unreadNotifications.length || recentErrors || providerFailures
      ? `This read is intentionally low confidence: it is derived from ${unreadNotifications.length} unread operational signal${unreadNotifications.length === 1 ? '' : 's'}, ${recentErrors} recent runtime error${recentErrors === 1 ? '' : 's'}, and ${providerFailures} recent provider failure${providerFailures === 1 ? '' : 's'} instead of real Jira tickets.`
      : 'This read is intentionally low confidence because no Jira adapter, ticket list, or sprint metadata is wired in yet.',
  });

  return items.slice(0, 3);
}

function buildJiraDeliveryPressure(timeReality, notifications, analytics) {
  const items = [];
  const unreadNotifications = notifications.filter((item) => !item.read_at).length;
  const approvals = notifications.filter((item) => item.type === 'approval' && !item.read_at).length;
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;
  const sessions = Number(analytics?.overview?.sessions || 0);

  items.push({
    title: 'Pressure posture',
    detail: describeJiraDeliveryPressure(timeReality, unreadNotifications, approvals),
  });

  items.push({
    title: 'Likely board pressure',
    detail: approvals
      ? `${approvals} unread approval${approvals === 1 ? '' : 's'} suggest the board may have at least one item waiting on a decision before it can move forward.`
      : unreadNotifications
        ? `${unreadNotifications} unread operational signal${unreadNotifications === 1 ? '' : 's'} suggest follow-through pressure may exist even though the actual board rows are unknown.`
        : 'No strong operational pressure is being elevated, so this slice cannot justify claiming delivery compression from the board alone.',
  });

  items.push({
    title: 'History guardrail',
    detail: sessions || recentErrors
      ? `The shell can see ${sessions.toLocaleString()} recorded session${sessions === 1 ? '' : 's'} and ${recentErrors} recent runtime error${recentErrors === 1 ? '' : 's'}, which helps frame delivery risk loosely but still does not reveal true Jira scope, assignees, or due dates.`
      : 'With few supporting shell signals, this slice avoids pretending it knows delivery status, deadline health, or sprint burn.',
  });

  return items.slice(0, 3);
}

function buildJiraExecutionRisk(providerStatuses, notifications, analytics) {
  const items = [];
  const disconnectedProviders = Object.values(providerStatuses || {}).filter((status) => status?.available && status.connected === false).length;
  const providerFailures = Number(analytics?.providerFailures?.reduce?.((sum, item) => sum + Number(item.failures || 0), 0) || 0);
  const runtimeSignals = notifications.filter((item) => item.type === 'error' && !item.read_at).length;
  const agentSignals = notifications.filter((item) => item.type === 'agent' && !item.read_at).length;

  items.push({
    title: 'Automation constraint',
    detail: disconnectedProviders || providerFailures
      ? `Provider instability is still visible (${disconnectedProviders} disconnected, ${providerFailures} recent failure${providerFailures === 1 ? '' : 's'}), so this Jira slice should be used for triage framing, not as proof that ticket execution can safely auto-run.`
      : 'Providers currently look stable enough for chat support, but this slice still stops at read-only framing because no Jira execution path is wired in.',
  });

  items.push({
    title: 'Blocker proxy',
    detail: runtimeSignals || agentSignals
      ? `${runtimeSignals} runtime signal${runtimeSignals === 1 ? '' : 's'} and ${agentSignals} agent signal${agentSignals === 1 ? '' : 's'} are being treated as the closest local proxy for blockers or stalled delivery.`
      : 'No strong blocker proxy is visible right now, so this slice cannot honestly claim the board is blocked or healthy.',
  });

  items.push({
    title: 'Decision boundary',
    detail: 'This module can suggest where execution risk probably sits, but it cannot verify ticket owners, sprint commitments, or deadline movement until a real Jira adapter exists.',
  });

  return items.slice(0, 3);
}

function buildOkrGoalHealth(timeReality, notifications, analytics) {
  const items = [];
  const unreadNotifications = notifications.filter((item) => !item.read_at).length;
  const approvals = notifications.filter((item) => item.type === 'approval' && !item.read_at).length;
  const sessions = Number(analytics?.overview?.sessions || 0);

  items.push({
    title: 'Goal posture',
    detail: describeOkrGoalPosture(timeReality),
  });

  items.push({
    title: 'What is visible',
    detail: approvals
      ? `${approvals} unread approval${approvals === 1 ? '' : 's'} suggest at least one initiative may be waiting on a decision before progress can be confidently reported.`
      : unreadNotifications
        ? `${unreadNotifications} unread operational signal${unreadNotifications === 1 ? '' : 's'} suggest there may be movement worth capturing, even though no real OKR objects are connected yet.`
        : 'No strong operational movement is visible right now, so this slice avoids pretending it knows true objective health.',
  });

  items.push({
    title: 'History guardrail',
    detail: sessions
      ? `The shell can see ${sessions.toLocaleString()} recorded session${sessions === 1 ? '' : 's'}, which helps frame whether recent work likely produced goal movement, but it still does not reveal real objective metrics or target values.`
      : 'With little supporting shell history, this slice keeps goal health deliberately conservative.',
  });

  return items.slice(0, 3);
}

function buildOkrEvidenceInbox(notifications, analytics) {
  const items = [];
  const approvals = notifications.filter((item) => item.type === 'approval' && !item.read_at).length;
  const agentSignals = notifications.filter((item) => item.type === 'agent' && !item.read_at).length;
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;

  items.push({
    title: 'Capture posture',
    detail: approvals || agentSignals
      ? `${approvals} approval signal${approvals === 1 ? '' : 's'} and ${agentSignals} agent signal${agentSignals === 1 ? '' : 's'} are the current proxy for wins, decisions, and evidence worth capturing for later OKR updates.`
      : 'This slice is currently treating the shell as a lightweight capture inbox for wins and notes, not as proof that objective progress has already been logged elsewhere.',
  });

  items.push({
    title: 'What likely belongs here',
    detail: recentErrors
      ? `${recentErrors} recent runtime error${recentErrors === 1 ? '' : 's'} suggest the evidence inbox should capture both progress and drag, so later updates can reflect risk honestly instead of only listing wins.`
      : 'The best candidates here are notable wins, decisions, completed drafts, and anything that would help a later OKR update say what moved this week.',
  });

  items.push({
    title: 'Confidence guardrail',
    detail: 'This inbox is still inference-only: it can collect likely evidence and talking points, but it cannot verify that any item maps to a specific objective until a real OKR adapter exists.',
  });

  return items.slice(0, 3);
}

function buildOkrUpdateRisk(providerStatuses, notifications, analytics) {
  const items = [];
  const disconnectedProviders = Object.values(providerStatuses || {}).filter((status) => status?.available && status.connected === false).length;
  const providerFailures = Number(analytics?.providerFailures?.reduce?.((sum, item) => sum + Number(item.failures || 0), 0) || 0);
  const unreadNotifications = notifications.filter((item) => !item.read_at).length;
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;

  items.push({
    title: 'Reporting constraint',
    detail: disconnectedProviders || providerFailures
      ? `Provider instability is still visible (${disconnectedProviders} disconnected, ${providerFailures} recent failure${providerFailures === 1 ? '' : 's'}), so OKR update help should stay draft-first and human-reviewed instead of pretending safe publication paths exist.`
      : 'Providers currently look stable enough for chat support, but this slice still stops at read-only framing because no OKR system write path is wired in.',
  });

  items.push({
    title: 'Likely blind spot',
    detail: unreadNotifications || recentErrors
      ? `${unreadNotifications} unread operational signal${unreadNotifications === 1 ? '' : 's'} and ${recentErrors} recent runtime error${recentErrors === 1 ? '' : 's'} suggest any update could miss drag, blockers, or slipped work if it only highlights visible wins.`
      : 'No strong drag signal is being elevated, but this slice still cannot prove whether goals are on track because milestone data is absent.',
  });

  items.push({
    title: 'Decision boundary',
    detail: 'This module can suggest where an OKR update looks thin, risky, or incomplete, but it cannot verify target movement, confidence scores, or owner alignment until a real OKR adapter exists.',
  });

  return items.slice(0, 3);
}

function describeCalendarPosture(timeReality) {
  if (timeReality.meetingLoad === 'heavy' && timeReality.nextHardStop) {
    return `The calendar posture is compressed: a heavy meeting day plus the ${timeReality.nextHardStop} hard stop suggests tighter buffers, faster prep, and fewer safe windows than the shell can fully verify.`;
  }
  if (timeReality.meetingLoad === 'heavy') {
    return 'The calendar posture is compressed because the day is marked meeting-heavy, even though no live event stream is connected yet.';
  }
  if (timeReality.nextHardStop) {
    return `The calendar posture is anchored to the hard stop at ${timeReality.nextHardStop}, so this slice is optimizing around one known boundary rather than a full event timeline.`;
  }
  if (timeReality.focusPreference === 'after-meetings') {
    return 'The calendar posture is biased toward clearing coordination first, then protecting the first deeper block after meetings.';
  }
  return 'The calendar posture is still approximate because this slice is using manual time reality instead of connected event objects.';
}

function describeCalendarBufferPosture(timeReality) {
  if (timeReality.meetingLoad === 'heavy' && timeReality.nextHardStop) {
    return `A heavy meeting day with the ${timeReality.nextHardStop} hard stop suggests keeping transitions short but intentional, because even small overruns could consume the best remaining work block.`;
  }
  if (timeReality.meetingLoad === 'heavy') {
    return 'A heavy meeting day suggests planning for brief buffers between coordination blocks, even though this slice cannot see the real event spacing yet.';
  }
  if (timeReality.nextHardStop) {
    return `The hard stop at ${timeReality.nextHardStop} is the clearest known edge, so transition guidance is biased toward preserving margin before that boundary.`;
  }
  if (timeReality.focusPreference === 'after-meetings') {
    return 'Because focus is biased after meetings, the safest buffer assumption is a short reset window before the deeper block begins.';
  }
  return 'Transition guidance stays approximate here because the shell knows your stated time posture, not the actual meeting edges.';
}

function describeJiraBoardPosture(timeReality) {
  if (timeReality.meetingLoad === 'heavy' && timeReality.nextHardStop) {
    return `The board posture is compressed: a heavy meeting day plus the ${timeReality.nextHardStop} hard stop suggests less room for unplanned execution, but this slice cannot verify which tickets are actually due.`;
  }
  if (timeReality.meetingLoad === 'heavy') {
    return 'The board posture is compressed because the day is manually marked meeting-heavy, so any delivery read should assume less execution bandwidth than a clean workday.';
  }
  if (timeReality.nextHardStop) {
    return `The board posture is anchored to the manual hard stop at ${timeReality.nextHardStop}, so queue risk is read through time compression rather than real Jira deadlines.`;
  }
  return 'The board posture is approximate because this slice reads delivery risk from local shell signals rather than connected Jira work items.';
}

function describeOkrGoalPosture(timeReality) {
  if (timeReality.meetingLoad === 'heavy' && timeReality.nextHardStop) {
    return `The OKR posture is compressed: a heavy meeting day plus the ${timeReality.nextHardStop} hard stop suggests goal progress may be real but under-documented unless wins are captured quickly.`;
  }
  if (timeReality.meetingLoad === 'heavy') {
    return 'The OKR posture is compressed because a meeting-heavy day makes it easier for progress to happen in fragments without being captured cleanly.';
  }
  if (timeReality.nextHardStop) {
    return `The OKR posture is anchored to the hard stop at ${timeReality.nextHardStop}, so this slice is biased toward compact evidence capture before the day narrows.`;
  }
  if (timeReality.focusPreference === 'after-meetings') {
    return 'The OKR posture is biased toward capturing progress after coordination clears, when there is more room to summarize what actually moved.';
  }
  return 'The OKR posture is approximate because this slice reads goal health from local shell signals rather than connected objectives, milestones, or scorecards.';
}

function summarizeOkrPosture(timeReality, notifications, analytics) {
  const unreadNotifications = notifications.filter((item) => !item.read_at).length;
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;
  if (timeReality.meetingLoad === 'heavy') {
    return 'Capture-first day';
  }
  if (timeReality.nextHardStop && unreadNotifications) {
    return 'Update risk visible';
  }
  if (recentErrors) {
    return 'Mixed progress / drag';
  }
  if (unreadNotifications) {
    return 'Movement worth reviewing';
  }
  return 'Low-confidence read';
}

function describeJiraDeliveryPressure(timeReality, unreadNotifications, approvals) {
  if (timeReality.meetingLoad === 'heavy' && approvals) {
    return 'Meeting-heavy time reality plus active approval pressure suggests any delivery queue movement may depend on fast decision-making more than raw execution time.';
  }
  if (timeReality.nextHardStop && unreadNotifications) {
    return `The hard stop at ${timeReality.nextHardStop} plus current operational signals suggest delivery pressure is probably real, but the board itself is still invisible in this slice.`;
  }
  if (unreadNotifications) {
    return 'Unread operational signals suggest there may be delivery pressure around the board, but this slice cannot distinguish ticket churn from general operational noise.';
  }
  return 'Delivery pressure stays conservative here because no ticket-level Jira facts are connected yet.';
}

function describeMeetingPrepPosture(timeReality) {
  if (timeReality.meetingLoad === 'heavy' && timeReality.nextHardStop) {
    return `A heavy meeting day plus the ${timeReality.nextHardStop} hard stop suggests prep should stay lightweight, decision-oriented, and front-loaded before the day compresses.`;
  }
  if (timeReality.meetingLoad === 'heavy') {
    return 'A heavy meeting day suggests prep should focus on decision points, blockers, and one-page context instead of broad background reading.';
  }
  if (timeReality.nextHardStop) {
    return `The next hard stop at ${timeReality.nextHardStop} suggests any prep should happen before that boundary and stay scoped to the most important conversation.`;
  }
  if (timeReality.focusPreference === 'after-meetings') {
    return 'Focus is biased after meetings, so prep should aim to clear coordination quickly and protect the deeper block that follows.';
  }
  return 'Manual time reality is still light on specifics, so prep posture stays conservative and assumes only one meeting deserves deeper attention.';
}

function buildMeetingFollowUpOutputsCopy(approvalCount, agentSignalCount) {
  if (approvalCount && agentSignalCount) {
    return `${approvalCount} approval${approvalCount === 1 ? '' : 's'} and ${agentSignalCount} agent signal${agentSignalCount === 1 ? '' : 's'} suggest likely follow-up includes decisions, owner assignment, and a short unblock message.`;
  }
  if (approvalCount) {
    return `${approvalCount} unread approval${approvalCount === 1 ? '' : 's'} suggest follow-up may need a decision log, sign-off note, or explicit approval request.`;
  }
  return `${agentSignalCount} unread agent signal${agentSignalCount === 1 ? '' : 's'} suggest follow-up may need delegated tasks, a short recap, or an unblock handoff.`;
}

function buildSourceNote(providerStatuses, notifications, timeReality) {
  const connectedProviders = Object.values(providerStatuses || {}).filter((status) => status?.connected).length;
  return `Live inputs today: ${connectedProviders} connected provider${connectedProviders === 1 ? '' : 's'} and ${notifications.length} unread notification${notifications.length === 1 ? '' : 's'} feeding the shell. ${buildManualTimeRealityStatus(timeReality)} Calendar, Meetings, Jira Board, and OKR Studio live in Workspace, while Files now sits under Dev Projects.`;
}

function inferBestWorkBlock(analytics, timeReality) {
  if (timeReality.nextHardStop) {
    return `Use the block before ${timeReality.nextHardStop} for the highest-signal work`;
  }
  if (timeReality.focusPreference === 'early') {
    return 'Protect the earliest open block before meetings spread';
  }
  if (timeReality.focusPreference === 'midday') {
    return 'Aim for a quiet midday block for the hardest work';
  }
  if (timeReality.focusPreference === 'afternoon') {
    return 'Protect an afternoon block after morning coordination clears';
  }
  if (timeReality.focusPreference === 'after-meetings') {
    return 'Take the first clean block after meetings end';
  }
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;
  if (recentErrors) {
    return 'Protect the next clean 45m after triaging the top runtime issue';
  }
  const sessions = Number(analytics?.overview?.sessions || 0);
  if (sessions > 0) {
    return 'Take the next uninterrupted 60m block before re-entering the queue';
  }
  return 'Hold one 60m block manually until calendar inputs are connected';
}

function renderMorningBrief(brief) {
  return `
    <article class="shell-card command-card dashboard-card dashboard-card-morning">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Decision layer</div>
          <h3>Morning Brief</h3>
        </div>
        <span class="command-card-badge">Read-only</span>
      </div>
      <div class="brief-summary-grid">
        ${brief.summary.map((item) => `
          <div class="brief-summary-tile" data-tone="${item.tone}">
            <div class="brief-summary-label">${escapeHtml(item.label)}</div>
            <div class="brief-summary-value">${escapeHtml(item.value)}</div>
          </div>
        `).join('')}
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Critical Items</div>
        <div class="brief-critical-list">
          ${brief.criticalItems.map((item, index) => `
            <article class="brief-critical-item" data-tone="${item.tone}">
              <div class="brief-critical-rank">${index + 1}</div>
              <div class="brief-critical-body">
                <h4>${escapeHtml(item.title)}</h4>
                <p>${escapeHtml(item.why)}</p>
                <div class="command-item-next">${escapeHtml(item.nextStep)}</div>
              </div>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Time Reality Input</div>
        ${renderTimeRealityInput(brief.timeReality)}
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Prep / Collision Notes</div>
        <ul class="command-bullet-list">
          ${brief.prepNotes.map((note) => `<li>${escapeHtml(note)}</li>`).join('')}
        </ul>
      </div>
      <div class="shell-actions">
        ${brief.actions.map((action) => `
          <button
            type="button"
            class="shell-action-btn"
            data-shell-action="focus-task-command-from-shell"
            data-shell-task-section="${escapeAttribute(action.taskSection || '')}"
          >
            ${escapeHtml(action.label)}
          </button>
        `).join('')}
      </div>
      <p class="command-card-footnote">Morning Brief now hands off into Task Command directly for plan work. ${escapeHtml(brief.sourceNote)}</p>
    </article>
  `;
}

function renderTaskCommand(taskCommand) {
  return `
    <article class="shell-card command-card dashboard-card dashboard-card-task" data-task-command-root="true">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Action layer</div>
          <h3>Task Command</h3>
        </div>
        <span class="command-card-badge">Local-first V1</span>
      </div>
      ${renderTaskCommandLocalControls(taskCommand.localEditState)}
      <div class="task-command-grid">
        ${taskCommand.sections.map((section) => `
          <section
            class="task-command-column${section.collapsed ? ' task-command-column-collapsed' : ''}"
            data-task-command-section="${escapeAttribute(section.title)}"
          >
            <div class="task-command-section-header">
              <div class="command-subsection-title">${escapeHtml(section.title)}</div>
              <button
                type="button"
                class="task-command-inline-btn"
                data-shell-action="task-command-toggle-section"
                data-task-command-section="${escapeAttribute(section.title)}"
              >
                ${section.collapsed ? 'Expand section' : 'Collapse section'}
              </button>
            </div>
            ${section.collapsed ? `
              <p class="task-command-section-note">
                Hidden locally for now. ${section.visibleCount} ${pluralize('item', section.visibleCount)} still live in this section.
              </p>
            ` : `
              <div class="task-command-list">
                ${section.items.map((item) => `
                  <article class="task-command-item${item.emphasized ? ' task-command-item-emphasized' : ''}">
                    <h4>${escapeHtml(item.title)}</h4>
                    <p>${escapeHtml(item.whyNow)}</p>
                    <div class="task-command-meta">
                      <span>${escapeHtml(item.owner)}</span>
                      <span>${escapeHtml(item.effort)}</span>
                      <span>${escapeHtml(item.context)}</span>
                      <span>${escapeHtml(item.mode)}</span>
                      ${item.emphasized ? '<span>Focus</span>' : ''}
                    </div>
                    ${renderTaskCommandItemActions(item)}
                  </article>
                `).join('')}
              </div>
            `}
          </section>
        `).join('')}
      </div>
      <p class="command-card-footnote">${escapeHtml(taskCommand.sourceNote)}</p>
    </article>
  `;
}

function renderTaskCommandLocalControls(localEditState = {}) {
  const hasDismissedItems = Number(localEditState.dismissedCount || 0) > 0;
  const hasSnoozedItems = Number(localEditState.snoozedCount || 0) > 0;
  const hasLocalEdits = Number(localEditState.editedCount || 0) > 0;
  const hasEmphasizedItems = Number(localEditState.emphasizedCount || 0) > 0;
  const hasPinnedItems = Number(localEditState.pinnedCount || 0) > 0;
  const hasMovedItems = Number(localEditState.movedCount || 0) > 0;
  const hasCollapsedSections = Number(localEditState.collapsedSectionCount || 0) > 0;
  const pinnedOnly = Boolean(localEditState.pinnedOnly);
  const focusedOnly = Boolean(localEditState.focusedOnly);
  const movedOnly = Boolean(localEditState.movedOnly);

  if (!hasDismissedItems && !hasSnoozedItems && !hasLocalEdits && !hasEmphasizedItems && !hasPinnedItems && !hasMovedItems && !hasCollapsedSections && !pinnedOnly && !focusedOnly && !movedOnly) {
    return '';
  }

  const summary = hasDismissedItems
    ? `${localEditState.dismissedCount} dismissed ${pluralize('item', localEditState.dismissedCount)} hidden locally`
    : hasSnoozedItems
      ? `${localEditState.snoozedCount} snoozed ${pluralize('item', localEditState.snoozedCount)} hidden until you bring ${localEditState.snoozedCount === 1 ? 'it' : 'them'} back`
    : pinnedOnly
      ? `Pinned-only mode is showing ${localEditState.pinnedCount} pinned ${pluralize('item', localEditState.pinnedCount)}`
      : focusedOnly
        ? `Focused-only mode is showing ${localEditState.emphasizedCount} focused ${pluralize('item', localEditState.emphasizedCount)}`
      : movedOnly
        ? `Moved-only mode is showing ${localEditState.movedCount} moved ${pluralize('item', localEditState.movedCount)}`
      : hasCollapsedSections
        ? `${localEditState.collapsedSectionCount} collapsed ${pluralize('section', localEditState.collapsedSectionCount)} hidden locally`
        : hasEmphasizedItems
          ? `${localEditState.emphasizedCount} focused ${pluralize('item', localEditState.emphasizedCount)} highlighted locally`
          : `${localEditState.editedCount} local ${pluralize('edit', localEditState.editedCount)} applied`;

  return `
    <div class="task-command-local-controls">
      <p class="task-command-local-note">${escapeHtml(summary)}. These edits stay in this browser only.</p>
      <div class="task-command-local-actions">
        ${(hasPinnedItems || pinnedOnly) ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-toggle-pinned-only"
          >
            ${pinnedOnly ? 'Show all tasks' : 'Show pinned only'}
          </button>
        ` : ''}
        ${(hasEmphasizedItems || focusedOnly) ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-toggle-focused-only"
          >
            ${focusedOnly ? 'Show all tasks' : 'Show focused only'}
          </button>
        ` : ''}
        ${(hasMovedItems || movedOnly) ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-toggle-moved-only"
          >
            ${movedOnly ? 'Show all tasks' : 'Show moved only'}
          </button>
        ` : ''}
        ${hasDismissedItems ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-undo-dismiss"
          >
            Undo dismiss
          </button>
        ` : ''}
        ${hasSnoozedItems ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-undo-snooze"
          >
            Undo snooze
          </button>
        ` : ''}
        ${(hasDismissedItems || hasSnoozedItems) ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-restore-hidden"
          >
            Restore hidden tasks
          </button>
        ` : ''}
        ${hasEmphasizedItems ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-clear-focus"
          >
            Clear focus
          </button>
        ` : ''}
        ${hasPinnedItems ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-clear-pins"
          >
            Clear pins
          </button>
        ` : ''}
        ${hasMovedItems ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-clear-moves"
          >
            Clear moves
          </button>
        ` : ''}
        ${hasCollapsedSections ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-restore-sections"
          >
            Restore sections
          </button>
        ` : ''}
        ${hasLocalEdits ? `
          <button
            type="button"
            class="task-command-inline-btn"
            data-shell-action="task-command-reset-edits"
          >
            Reset task edits
          </button>
        ` : ''}
      </div>
    </div>
  `;
}

function renderTaskCommandItemActions(item) {
  let primaryAction = '';

  if (item.sourceSessionId) {
    primaryAction = `
      <button
        type="button"
        class="shell-action-btn shell-action-btn-secondary"
        data-shell-action="open-session-from-shell"
        data-shell-session-id="${escapeAttribute(item.sourceSessionId)}"
      >
        Open Session
      </button>
    `;
  } else if (item.notificationType) {
    primaryAction = `
      <button
        type="button"
        class="shell-action-btn shell-action-btn-secondary"
        data-shell-action="open-notification-history-from-shell"
        data-shell-notification-type="${escapeAttribute(item.notificationType)}"
        data-shell-notification-status="${escapeAttribute(item.notificationStatus || 'unread')}"
      >
        Open Queue
      </button>
    `;
  } else if (item.intent) {
    primaryAction = `
      <button
        type="button"
        class="shell-action-btn"
        data-shell-action="open-chat-from-shell"
        data-shell-intent="${escapeAttribute(item.intent)}"
      >
        Ask Artemis
      </button>
    `;
  }

  if (item.isPlaceholder || !item.itemId) {
    return primaryAction ? `<div class="shell-actions task-command-actions">${primaryAction}</div>` : '';
  }

  return `
    <div class="shell-actions task-command-actions">
      ${primaryAction}
    </div>
    <div class="task-command-edit-row">
      <button
        type="button"
        class="task-command-inline-btn"
        data-shell-action="task-command-emphasize"
        data-task-command-item-id="${escapeAttribute(item.itemId)}"
      >
        ${item.emphasized ? 'Clear focus' : 'Highlight'}
      </button>
      <button
        type="button"
        class="task-command-inline-btn"
        data-shell-action="task-command-pin"
        data-task-command-item-id="${escapeAttribute(item.itemId)}"
      >
        ${item.pinned ? 'Unpin' : 'Pin'}
      </button>
      <label class="task-command-move-label">
        <span>Move</span>
        <select data-task-command-edit="move-section" data-task-command-item-id="${escapeAttribute(item.itemId)}">
          ${TASK_COMMAND_SECTION_OPTIONS.map((option) => `
            <option value="${escapeAttribute(option)}"${item.sectionTitle === option ? ' selected' : ''}>${escapeHtml(option)}</option>
          `).join('')}
        </select>
      </label>
      <button
        type="button"
        class="task-command-inline-btn task-command-inline-btn-danger"
        data-shell-action="task-command-snooze"
        data-task-command-item-id="${escapeAttribute(item.itemId)}"
      >
        Snooze
      </button>
      <button
        type="button"
        class="task-command-inline-btn task-command-inline-btn-danger"
        data-shell-action="task-command-dismiss"
        data-task-command-item-id="${escapeAttribute(item.itemId)}"
      >
        Dismiss
      </button>
    </div>
  `;
}

function renderNeedsAttention(needsAttention) {
  return `
    <article class="shell-card command-card dashboard-card dashboard-card-queue">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Operational queue</div>
          <h3>Needs Attention</h3>
        </div>
        <span class="command-card-badge">${needsAttention.queueItems.length} ranked</span>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">System Strip</div>
        <div class="system-strip">
          ${(needsAttention.systemIssues.length ? needsAttention.systemIssues : [{
            title: 'Providers and connectors look stable',
            status: 'Healthy',
            detail: 'No immediate shell-level provider or connector issue is being elevated right now.',
            nextStep: 'Keep the strip quiet unless a real operational issue emerges.',
          }]).map((item) => `
            <article class="system-strip-item">
              <div class="system-strip-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>${escapeHtml(item.status)}</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Ranked Queue</div>
        <div class="attention-list">
          ${needsAttention.queueItems.map((item, index) => `
            <article class="attention-item" data-kind="${escapeAttribute(item.kind)}">
              <div class="attention-rank">${index + 1}</div>
              <div class="attention-body">
                <div class="attention-topline">
                  <h4>${escapeHtml(item.title)}</h4>
                  <div class="attention-meta">
                    <span class="attention-chip">${escapeHtml(item.urgency)}</span>
                    <span>${escapeHtml(item.status)}</span>
                  </div>
                </div>
                <p>${escapeHtml(item.detail)}</p>
                <div class="attention-context">
                  <div class="attention-context-label">Why this surfaced</div>
                  <div>${escapeHtml(item.reason)}</div>
                </div>
                <div class="command-item-next">
                  <span class="attention-context-label">Suggested move</span>
                  <span>${escapeHtml(item.nextStep)}</span>
                </div>
                <div class="shell-actions attention-actions">
                  ${item.sourceSessionId ? `
                    <button
                      type="button"
                      class="shell-action-btn shell-action-btn-secondary"
                      data-shell-action="open-session-from-shell"
                      data-shell-session-id="${escapeAttribute(item.sourceSessionId)}"
                    >
                      Open Session
                    </button>
                  ` : ''}
                  ${!item.sourceSessionId && item.notificationType ? `
                    <button
                      type="button"
                      class="shell-action-btn shell-action-btn-secondary"
                      data-shell-action="open-notification-history-from-shell"
                      data-shell-notification-type="${escapeAttribute(item.notificationType)}"
                      data-shell-notification-status="${escapeAttribute(item.notificationStatus || 'unread')}"
                    >
                      Open Queue
                    </button>
                  ` : ''}
                  <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(item.intent)}">
                    ${escapeHtml(item.actionLabel)}
                  </button>
                </div>
              </div>
            </article>
          `).join('')}
        </div>
      </div>
      <p class="command-card-footnote">${escapeHtml(needsAttention.sourceNote)}</p>
    </article>
  `;
}

function describeDashboardMeetingLoad(meetingsOverview = {}, calendarOverview = {}, timeReality = {}) {
  const meetingCount = Number(meetingsOverview?.today?.meetingsCount || calendarOverview?.today?.meetingsCount || 0);
  if (meetingCount) return `${meetingCount} scheduled`;
  if (timeReality?.meetingLoad === 'heavy') return 'Heavy day';
  if (timeReality?.meetingLoad === 'light') return 'Light day';
  return 'Still manual';
}

function buildMeetingsRadarCopy({ meetingCount = 0, timeReality = {}, nextFocusWindow = null }) {
  if (meetingCount && nextFocusWindow) {
    return `${meetingCount} meeting${meetingCount === 1 ? '' : 's'} are visible. Protect ${formatDashboardWindow(nextFocusWindow)} for deeper work.`;
  }
  if (meetingCount) {
    return `${meetingCount} meeting${meetingCount === 1 ? '' : 's'} are visible today. Use the Meetings page for prep and follow-up.`;
  }
  if (nextFocusWindow) {
    return `Calendar looks open enough to protect ${formatDashboardWindow(nextFocusWindow)} for focused work.`;
  }
  return `No live meeting pressure is visible yet, so the page is leaning on ${describeTimeRealityLead(timeReality).toLowerCase()}.`;
}

function buildRepliesRadarCopy({ approvalCount = 0, unreadCount = 0 }) {
  if (approvalCount) {
    return `${approvalCount} approval${approvalCount === 1 ? '' : 's'} or drafted responses are already waiting on you. Clear those before new coordination work stacks up.`;
  }
  if (unreadCount) {
    return `${unreadCount} unread signal${unreadCount === 1 ? '' : 's'} may still need a reply, decision, or quick follow-up.`;
  }
  return 'Nothing urgent is queued right now, but this is where response work should stay visible instead of getting lost in other surfaces.';
}

function formatDashboardWindow(block) {
  const label = String(block?.label || '').trim();
  if (label) return label;
  const start = String(block?.start || '').trim();
  const end = String(block?.end || '').trim();
  return start && end ? `${start}-${end}` : 'the next open window';
}

function compactDashboardText(value, max = 160) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}

function safePositiveNumber(value) {
  return Number.isFinite(value) && value > 0;
}

function buildDashboardTodayPlanModel({ analytics = {}, meetingsOverview = {}, calendarOverview = {}, timeReality = {}, jiraToday, okrThisWeek }) {
  const meetingCount = Number(meetingsOverview?.today?.meetingsCount || calendarOverview?.today?.meetingsCount || 0);
  const nextMeeting = meetingsOverview?.nextMeeting
    ? `${meetingsOverview.nextMeeting.startLabel} · ${meetingsOverview.nextMeeting.title}`
    : calendarOverview?.nextEvent
      ? `${calendarOverview.nextEvent.startLabel} · ${calendarOverview.nextEvent.title}`
      : 'No scheduled meeting in focus';

  const bestBlock = inferBestWorkBlock(analytics, timeReality);
  const topWork = jiraToday?.topItem?.title || okrThisWeek?.topItem?.title || 'Protect the best work block and move one meaningful thing forward.';
  const planNotes = [
    meetingCount
      ? `${meetingCount} meeting${meetingCount === 1 ? '' : 's'} are visible today. Next: ${nextMeeting}.`
      : 'Calendar looks relatively open today, so the main question is what deserves your best block.',
    jiraToday?.summary || 'No assigned Jira ticket is clearly demanding today yet.',
    okrThisWeek?.summary || 'No KR is clearly surfacing as the week\'s next update candidate yet.',
  ].filter(Boolean);

  return {
    summary: [
      { label: 'Best Work Block', value: bestBlock, tone: 'accent' },
      { label: 'Meetings', value: meetingCount ? `${meetingCount} today` : 'Light day', tone: 'neutral' },
      { label: 'Best Bet', value: topWork, tone: 'neutral' },
    ],
    notes: planNotes.slice(0, 3),
    actions: [
      { label: 'Open Meetings', shellView: MEETINGS_VIEW },
      { label: 'Open Calendar', shellView: CALENDAR_VIEW },
      { label: 'Ask Artemis', intent: `Help me turn today's schedule and current work into a tighter day plan. Best work block: ${bestBlock}.` },
    ],
  };
}

function renderTodayPlan(todayPlan) {
  return `
    <article class="shell-card command-card dashboard-card dashboard-card-morning">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Today</div>
          <h3>Today</h3>
        </div>
        <span class="command-card-badge">Personal</span>
      </div>
      <div class="brief-summary-grid">
        ${todayPlan.summary.map((item) => `
          <div class="brief-summary-tile" data-tone="${item.tone}">
            <div class="brief-summary-label">${escapeHtml(item.label)}</div>
            <div class="brief-summary-value">${escapeHtml(item.value)}</div>
          </div>
        `).join('')}
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Today\'s read</div>
        <ul class="command-bullet-list">
          ${todayPlan.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join('')}
        </ul>
      </div>
      <div class="shell-actions">
        ${todayPlan.actions.map((action) => action.shellView ? `
          <button
            type="button"
            class="shell-action-btn shell-action-btn-secondary"
            data-shell-action="open-shell-view"
            data-shell-view="${escapeAttribute(action.shellView)}"
          >
            ${escapeHtml(action.label)}
          </button>
        ` : `
          <button
            type="button"
            class="shell-action-btn"
            data-shell-action="open-chat-from-shell"
            data-shell-intent="${escapeAttribute(action.intent || '')}"
          >
            ${escapeHtml(action.label)}
          </button>
        `).join('')}
        <button
          type="button"
          class="shell-action-btn shell-action-btn-secondary"
          data-shell-action="dashboard-capture-open"
        >
          Capture today's work
        </button>
      </div>
    </article>
  `;
}

function buildDashboardCaptureModel({ jiraToday, okrThisWeek, resumeWork }) {
  const draft = String(dashboardCaptureState.draft || '').trim();
  const proposals = Array.isArray(dashboardCaptureState.proposals) ? dashboardCaptureState.proposals : [];
  const recommended = proposals[0] || null;
  const lightDay = !jiraToday?.topItem && !okrThisWeek?.topItem;
  const resumeCount = Array.isArray(resumeWork?.items) ? resumeWork.items.length : 0;

  return {
    open: Boolean(dashboardCaptureState.open),
    draft: dashboardCaptureState.draft || '',
    source: dashboardCaptureState.source || 'build',
    outcome: dashboardCaptureState.outcome || 'progress',
    summary: dashboardCaptureState.summary
      || (recommended
        ? `Best next move: ${recommended.title}.`
        : lightDay
          ? 'Lighter structured-work day: capture what moved so the next action can land somewhere durable.'
          : 'Use this when work happened between the obvious Jira, reply, or OKR lanes.'),
    helper: lightDay
      ? '1. Capture the work. 2. Classify it. 3. Route it to Jira, OKRs, or a saved note.'
      : 'Use this for progress that happened off-ticket, meeting follow-up worth routing, or work that should become the next durable artifact.',
    footnote: resumeCount
      ? 'This is the structured alternative to starting another vague planning session.'
      : 'This keeps lighter days from disappearing into uncaptured build work.',
    proposals,
    recommendedKind: recommended?.kind || '',
    error: dashboardCaptureState.error || '',
    savedMessage: dashboardCaptureState.savedMessage || '',
  };
}

function renderCaptureTodayWork(captureWork) {
  const proposals = Array.isArray(captureWork?.proposals) ? captureWork.proposals : [];
  return `
    <article class="shell-card command-card dashboard-card dashboard-card-capture${captureWork?.open ? ' dashboard-card-capture-open' : ''}">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Structured flow</div>
          <h3>Capture today's work</h3>
        </div>
        <span class="command-card-badge">${captureWork?.open ? 'Open' : 'Ready'}</span>
      </div>
      <p class="command-card-footnote">${escapeHtml(captureWork?.summary || '')}</p>
      <div class="dashboard-capture-helper">${escapeHtml(captureWork?.helper || '')}</div>
      ${captureWork?.savedMessage ? `<div class="dashboard-capture-status dashboard-capture-status-success">${escapeHtml(captureWork.savedMessage)}</div>` : ''}
      ${captureWork?.error ? `<div class="dashboard-capture-status dashboard-capture-status-error">${escapeHtml(captureWork.error)}</div>` : ''}
      ${captureWork?.open ? `
        <div class="dashboard-capture-form">
          <label class="shell-field">
            <span>What happened?</span>
            <textarea
              rows="4"
              class="dashboard-capture-textarea"
              placeholder="Shipped a fix, closed a loop, made progress, or uncovered work that should go somewhere durable."
              data-dashboard-capture-field="draft"
            >${escapeHtml(captureWork?.draft || '')}</textarea>
          </label>
          <div class="dashboard-capture-field-grid">
            <label class="shell-field">
              <span>Where did it happen?</span>
              <select data-dashboard-capture-field="source">
                ${renderDashboardCaptureOption('build', 'Build / coding work', captureWork?.source)}
                ${renderDashboardCaptureOption('meeting', 'Meeting follow-up', captureWork?.source)}
                ${renderDashboardCaptureOption('jira', 'Jira-adjacent work', captureWork?.source)}
                ${renderDashboardCaptureOption('okr', 'OKR progress', captureWork?.source)}
                ${renderDashboardCaptureOption('note', 'General note', captureWork?.source)}
              </select>
            </label>
            <label class="shell-field">
              <span>What kind of update is it?</span>
              <select data-dashboard-capture-field="outcome">
                ${renderDashboardCaptureOption('progress', 'Progress shipped', captureWork?.outcome)}
                ${renderDashboardCaptureOption('decision', 'Decision / unblock', captureWork?.outcome)}
                ${renderDashboardCaptureOption('new-task', 'New task to create', captureWork?.outcome)}
                ${renderDashboardCaptureOption('note', 'Just save the note', captureWork?.outcome)}
              </select>
            </label>
          </div>
          <div class="shell-actions dashboard-capture-actions">
            <button
              type="button"
              class="shell-action-btn"
              data-shell-action="dashboard-capture-generate"
            >
              Propose next step
            </button>
            <button
              type="button"
              class="shell-action-btn shell-action-btn-secondary"
              data-shell-action="dashboard-capture-close"
            >
              Close
            </button>
          </div>
        </div>
        ${proposals.length ? `
          <div class="dashboard-capture-options">
            ${proposals.map((proposal, index) => `
              <article class="dashboard-capture-option${index === 0 ? ' recommended' : ''}">
                <div class="dashboard-capture-option-topline">
                  <h4>${escapeHtml(proposal.title)}</h4>
                  <span class="dashboard-capture-option-rank">${index === 0 ? 'Recommended' : 'Option'}</span>
                </div>
                <p>${escapeHtml(proposal.detail)}</p>
                <div class="shell-actions dashboard-capture-option-actions">
                  ${proposal.intent ? `
                    <button
                      type="button"
                      class="shell-action-btn"
                      data-shell-action="open-chat-from-shell"
                      data-shell-intent="${escapeAttribute(proposal.intent)}"
                    >
                      ${escapeHtml(proposal.actionLabel)}
                    </button>
                  ` : `
                    <button
                      type="button"
                      class="shell-action-btn"
                      data-shell-action="dashboard-capture-save-note"
                    >
                      ${escapeHtml(proposal.actionLabel)}
                    </button>
                  `}
                </div>
              </article>
            `).join('')}
          </div>
        ` : ''}
      ` : `
        <div class="shell-actions dashboard-capture-actions">
          <button
            type="button"
            class="shell-action-btn"
            data-shell-action="dashboard-capture-open"
          >
            Start capture
          </button>
        </div>
      `}
      <p class="command-card-footnote">${escapeHtml(captureWork?.footnote || '')}</p>
    </article>
  `;
}

function renderDashboardCaptureOption(value, label, currentValue) {
  return `<option value="${escapeAttribute(value)}"${currentValue === value ? ' selected' : ''}>${escapeHtml(label)}</option>`;
}

function summarizeDashboardCaptureText(text, max = 220) {
  return compactDashboardText(text, max);
}

function normalizeDashboardCaptureForm(formState = {}) {
  return {
    draft: String(formState.draft || '').trim(),
    source: String(formState.source || dashboardCaptureState.source || 'build'),
    outcome: String(formState.outcome || dashboardCaptureState.outcome || 'progress'),
  };
}

function buildDashboardCaptureProposals({ draft, source, outcome, jiraToday, okrThisWeek }) {
  const text = String(draft || '').trim();
  const lower = text.toLowerCase();
  const ticketMatch = text.match(/\b[A-Z][A-Z0-9]+-\d+\b/);
  const ticketKey = ticketMatch?.[0] || jiraToday?.topItem?.key || '';
  const ticketTitle = jiraToday?.topItem?.title || 'the closest existing ticket';
  const krTitle = okrThisWeek?.topItem?.title || 'the KR that best matches this progress';
  const attachScore = (ticketMatch ? 55 : 0) + (source === 'jira' ? 24 : 0) + (outcome === 'decision' ? 6 : 0) + (jiraToday?.topItem ? 14 : 0);
  const createScore = (outcome === 'new-task' ? 48 : 0) + (/\b(task|todo|follow[- ]?up|bug|issue|ticket|need to)\b/.test(lower) ? 18 : 0) + (source === 'meeting' ? 8 : 0);
  const okrScore = (source === 'okr' ? 34 : 0) + (outcome === 'progress' ? 18 : 0) + (/\b(progress|shipped|launched|improved|result|okr|kr|objective)\b/.test(lower) ? 16 : 0) + (okrThisWeek?.topItem ? 12 : 0);
  const saveScore = 10 + (outcome === 'note' ? 26 : 0) + (source === 'note' ? 12 : 0);
  const summary = summarizeDashboardCaptureText(text);

  return [
    {
      kind: 'attach-jira',
      score: attachScore,
      title: ticketKey ? `Attach to ${ticketKey}` : 'Attach to existing Jira',
      detail: ticketKey
        ? `This reads like follow-through that likely belongs on ${ticketKey} — ${ticketTitle}.`
        : 'This looks like work that may belong on an existing Jira item if we match it against the current queue first.',
      actionLabel: ticketKey ? `Attach to ${ticketKey}` : 'Match existing Jira',
      intent: `Help me attach this captured work to the right Jira item${ticketKey ? `, probably ${ticketKey}` : ''}. Captured work: ${summary}.`,
    },
    {
      kind: 'create-jira',
      score: createScore,
      title: 'Create Jira task',
      detail: 'Use this when the capture is real work but does not cleanly map to an existing ticket yet.',
      actionLabel: 'Draft Jira task',
      intent: `Turn this captured work into a concise Jira task with a clear title, description, and next step. Captured work: ${summary}.`,
    },
    {
      kind: 'update-okr',
      score: okrScore,
      title: 'Update OKR',
      detail: `Best when this is progress evidence that should move ${krTitle} or a nearby goal narrative this week.`,
      actionLabel: 'Draft OKR update',
      intent: `Help me turn this captured work into an OKR update${krTitle ? `, likely for ${krTitle}` : ''}. Captured work: ${summary}.`,
    },
    {
      kind: 'save-note',
      score: saveScore,
      title: 'Save note',
      detail: 'Keep it as durable project memory when it should be remembered but does not need Jira or OKR structure right now.',
      actionLabel: 'Save note',
      intent: '',
    },
  ].sort((left, right) => right.score - left.score);
}

function readDashboardCaptureForm(container = appShellContent) {
  return normalizeDashboardCaptureForm({
    draft: container?.querySelector('[data-dashboard-capture-field="draft"]')?.value || dashboardCaptureState.draft,
    source: container?.querySelector('[data-dashboard-capture-field="source"]')?.value || dashboardCaptureState.source,
    outcome: container?.querySelector('[data-dashboard-capture-field="outcome"]')?.value || dashboardCaptureState.outcome,
  });
}

function saveDashboardCaptureLocalNote(text) {
  const next = {
    createdAt: new Date().toISOString(),
    content: text,
  };
  try {
    const current = JSON.parse(localStorage.getItem(DASHBOARD_CAPTURE_LOCAL_NOTES_STORAGE_KEY) || '[]');
    const list = Array.isArray(current) ? current : [];
    list.unshift(next);
    localStorage.setItem(DASHBOARD_CAPTURE_LOCAL_NOTES_STORAGE_KEY, JSON.stringify(list.slice(0, 20)));
  } catch {
    // Ignore storage failures; caller still gets the success state.
  }
}

function buildDashboardReplyWorkModel(notifications = [], slackSignals = null, slackMentions = null) {
  const approvalItems = notifications
    .filter((item) => item.type === 'approval' && !item.read_at)
    .slice(0, 3)
    .map((item) => ({
      eyebrow: 'Approval',
      title: item.title || 'Reply needed',
      detail: compactDashboardText(item.body || 'A response or decision is waiting on you.'),
      primaryAction: item.source_session_id ? 'Open Session' : 'Open Queue',
      sourceSessionId: item.source_session_id || '',
      notificationType: item.type || 'approval',
      notificationStatus: 'unread',
      secondaryIntent: buildAttentionIntent(item.title || 'Reply needed', buildNotificationNextStep(item)),
    }));

  const topSlackFollowup = buildTopSlackFollowup(slackSignals);
  const slackCards = [];
  if (slackSignals?.connected) {
    // J9b: missedMentions is now surfaced directly in the triage queue above
    // the cards grid, so we skip the redundant "N missed mentions" card here.
    // Only unread DMs and reply-needed threads get their own cards.
    if (safePositiveNumber(slackSignals.unreadDMs)) {
      slackCards.push({
        eyebrow: 'Slack DMs',
        title: `${slackSignals.unreadDMs} unread DM${slackSignals.unreadDMs === 1 ? '' : 's'}`,
        detail: 'Unread direct messages are treated as a narrower, higher-signal reply queue than general channel chatter.',
        primaryAction: 'Ask Artemis',
        secondaryIntent: buildAttentionIntent(
          `${slackSignals.unreadDMs} unread Slack DM${slackSignals.unreadDMs === 1 ? '' : 's'}`,
          'Help me decide which unread DMs need a reply now versus later.',
        ),
      });
    }
    if (safePositiveNumber(slackSignals.replyNeededThreads)) {
      slackCards.push({
        eyebrow: 'Slack threads',
        title: `${slackSignals.replyNeededThreads} reply-needed thread${slackSignals.replyNeededThreads === 1 ? '' : 's'}`,
        detail: 'Threads that still need your follow-up can stay visible here instead of disappearing into Slack history.',
        primaryAction: 'Ask Artemis',
        secondaryIntent: buildAttentionIntent(
          `${slackSignals.replyNeededThreads} reply-needed Slack thread${slackSignals.replyNeededThreads === 1 ? '' : 's'}`,
          'Help me draft or prioritize the Slack thread replies that matter most.',
        ),
      });
    }
  }

  // J9: attach the raw mentions list so renderNeedsYourReply can render the triage queue.
  const mentionItems = Array.isArray(slackMentions?.mentions) ? slackMentions.mentions.slice(0, 5) : [];
  const totalUnresolved = typeof slackMentions?.total_unresolved === 'number' ? slackMentions.total_unresolved : 0;

  // J9c: when the Slack mentions triage queue above is non-empty, skip the
  // "No urgent replies right now" fallback card — it contradicts the populated queue.
  const hasMentionTriage = mentionItems.length > 0 || totalUnresolved > 0;
  const cards = approvalItems.length || slackCards.length
    ? approvalItems.concat(slackCards).slice(0, 3)
    : hasMentionTriage
      ? []
      : [{
          eyebrow: 'Slack',
          title: 'No urgent replies right now',
          detail: slackSignals?.connected
            ? 'Slack is connected, but no missed mentions, unread DMs, or reply-needed threads were elevated into this reply lane.'
            : 'Slack follow-up is available when connected, but no missed mentions, unread DMs, or reply-needed threads are being elevated here yet. Use Connectors if you want to verify the link.',
          primaryAction: 'Open Connectors',
          connectorScope: 'slack',
        }];

  return {
    cards,
    topSlackFollowup,
    slackMentionItems: mentionItems,
    slackTotalUnresolved: totalUnresolved,
    footnote: approvalItems.length || slackCards.length || hasMentionTriage
      ? 'Keep this section focused on things waiting on you directly, not general system noise.'
      : 'If the reply queue is quiet, use Capture today\'s work to route progress into Jira, OKRs, or a saved note.',
  };
}

function buildTopSlackFollowup(slackSignals = null) {
  if (!slackSignals?.connected) return null;
  const positiveCount = (value) => (Number.isFinite(value) && value > 0 ? value : 0);

  const candidates = [
    {
      key: 'missedMentions',
      eyebrow: 'Slack mentions',
      count: positiveCount(slackSignals.missedMentions),
      title: slackSignals.missedMentions === 1 ? '1 missed mention is leading the Slack queue' : `${slackSignals.missedMentions || 0} missed mentions are leading the Slack queue`,
      detail: 'Use Artemis to decide which mention needs a reply now, which one can wait, and what short response would unblock things fastest.',
      actionLabel: 'Triage in Chat',
      intent: buildAttentionIntent(
        `${slackSignals.missedMentions} missed Slack mention${slackSignals.missedMentions === 1 ? '' : 's'}`,
        'Summarize the top Slack mention follow-up I should handle first, explain why it ranks highest, and help me draft a concise reply plan without turning this into a Slack inbox.',
      ),
    },
    {
      key: 'unreadDMs',
      eyebrow: 'Slack DMs',
      count: positiveCount(slackSignals.unreadDMs),
      title: slackSignals.unreadDMs === 1 ? '1 unread DM is leading the Slack queue' : `${slackSignals.unreadDMs || 0} unread DMs are leading the Slack queue`,
      detail: 'Use Artemis to narrow the DM follow-up to the one conversation most likely to unblock a decision or teammate quickly.',
      actionLabel: 'Triage in Chat',
      intent: buildAttentionIntent(
        `${slackSignals.unreadDMs} unread Slack DM${slackSignals.unreadDMs === 1 ? '' : 's'}`,
        'Summarize the unread Slack DM most worth handling first, explain the triage logic, and help me sketch a reply plan without opening a full DM workspace.',
      ),
    },
    {
      key: 'replyNeededThreads',
      eyebrow: 'Slack threads',
      count: positiveCount(slackSignals.replyNeededThreads),
      title: slackSignals.replyNeededThreads === 1 ? '1 reply-needed thread is leading the Slack queue' : `${slackSignals.replyNeededThreads || 0} reply-needed threads are leading the Slack queue`,
      detail: 'Use Artemis to identify the thread that matters most, then frame the quickest response path before Slack context disappears into scrollback.',
      actionLabel: 'Triage in Chat',
      intent: buildAttentionIntent(
        `${slackSignals.replyNeededThreads} reply-needed Slack thread${slackSignals.replyNeededThreads === 1 ? '' : 's'}`,
        'Summarize the Slack thread reply I should prioritize first, explain the triage logic, and help me outline a concise follow-up without making Slack the center of this shell.',
      ),
    },
  ].filter((candidate) => candidate.count);

  if (!candidates.length) return null;

  candidates.sort((left, right) => {
    if (right.count !== left.count) return right.count - left.count;
    const priority = ['replyNeededThreads', 'unreadDMs', 'missedMentions'];
    return priority.indexOf(left.key) - priority.indexOf(right.key);
  });

  return candidates[0];
}

function buildDashboardJiraTodayModel(jiraOverview, timeReality = {}) {
  const currentUserId = jiraOverview?.currentUser?.accountId || '';
  const allItems = Array.isArray(jiraOverview?.columns)
    ? jiraOverview.columns.flatMap((col) => (col.items || []).map((item) => ({ ...item, columnKey: col.key, columnLabel: col.label })))
    : [];
  const assignedItems = currentUserId
    ? allItems.filter((item) => item.assigneeId === currentUserId)
    : [];
  const rankedItems = assignedItems
    .map((item) => ({ ...item, dashboardScore: scoreDashboardJiraItem(item, timeReality) }))
    .sort((left, right) => right.dashboardScore - left.dashboardScore)
    .slice(0, 3);

  return {
    items: rankedItems,
    topItem: rankedItems[0] || null,
    summary: rankedItems.length
      ? `You have ${rankedItems.length} Jira ${rankedItems.length === 1 ? 'ticket' : 'tickets'} that look workable today, led by ${rankedItems[0].key}.`
      : 'No assigned Jira ticket stands out as today\'s best next move.',
    emptyTitle: 'Looks like a lighter Jira day',
    emptyBody: 'No assigned Jira ticket is clearly due for today. That usually means today may be better used for untracked build work, exploratory work, or progress that should be captured after the fact.',
  };
}

function renderJiraToday(jiraToday) {
  const items = Array.isArray(jiraToday?.items) ? jiraToday.items : [];
  return `
    <article class="shell-card command-card dashboard-card dashboard-card-task">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Assigned work</div>
          <h3>Jira Today</h3>
        </div>
        <span class="command-card-badge">${items.length ? `${items.length} ranked` : 'Light day'}</span>
      </div>
      <p class="command-card-footnote">${escapeHtml(jiraToday?.summary || '')}</p>
      <div class="task-command-list">
        ${items.length ? items.map((item) => `
          <article class="task-command-item">
            <h4>${escapeHtml(item.key)} — ${escapeHtml(item.title)}</h4>
            <p>${escapeHtml(describeDashboardJiraItem(item))}</p>
            <div class="task-command-meta">
              <span>${escapeHtml(item.columnLabel || item.status || 'Open')}</span>
              <span>${escapeHtml(item.priority || 'Unprioritized')}</span>
              <span>${escapeHtml(formatRelativeIssueUpdate(item.updated || item.created || ''))}</span>
            </div>
          </article>
        `).join('') : `
          <article class="resume-work-item resume-work-item-empty">
            <h4>${escapeHtml(jiraToday?.emptyTitle || 'Looks like a lighter Jira day')}</h4>
            <p>${escapeHtml(jiraToday?.emptyBody || '')}</p>
            <div class="shell-actions resume-work-actions">
              <button
                type="button"
                class="shell-action-btn shell-action-btn-secondary"
                data-shell-action="dashboard-capture-open"
              >
                Capture today's work
              </button>
            </div>
          </article>
        `}
      </div>
      <div class="shell-actions">
        <button
          type="button"
          class="shell-action-btn shell-action-btn-secondary"
          data-shell-action="open-shell-view"
          data-shell-view="${escapeAttribute(JIRA_VIEW)}"
        >
          Open Jira Board
        </button>
      </div>
    </article>
  `;
}

function buildDashboardOkrWeekModel(okrOverview, timeReality = {}) {
  const objectives = Array.isArray(okrOverview?.objectives) ? okrOverview.objectives : [];
  const nextUp = Array.isArray(okrOverview?.nextUp) ? okrOverview.nextUp : [];
  const krItems = [];

  objectives.forEach((objective) => {
    (objective.krs || []).forEach((kr) => {
      if ((kr.status || '').toLowerCase() === 'done') return;
      krItems.push({
        objectiveTitle: objective.title || 'Objective',
        title: kr.title || kr.target || 'KR',
        progress: Number(kr.prog ?? kr.progress ?? 0),
        status: kr.status || 'ontrack',
        note: kr.note || '',
      });
    });
  });

  const ranked = krItems
    .map((kr) => ({ ...kr, dashboardScore: scoreDashboardOkrItem(kr, nextUp, timeReality) }))
    .sort((left, right) => right.dashboardScore - left.dashboardScore)
    .slice(0, 3);

  return {
    items: ranked,
    topItem: ranked[0] || null,
    summary: ranked.length
      ? `These KR${ranked.length === 1 ? '' : 's'} look like the best candidates for progress this week.`
      : 'No KR is clearly demanding movement this week yet.',
    emptyTitle: 'Looks like a lighter OKR week',
    emptyBody: 'If no KR obviously needs movement right now, that likely means the best move is either deeper build work or capturing work in a way that can update Jira or OKRs afterward.',
  };
}

function renderOkrThisWeek(okrThisWeek) {
  const items = Array.isArray(okrThisWeek?.items) ? okrThisWeek.items : [];
  return `
    <article class="shell-card command-card dashboard-card dashboard-card-queue">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Progress this week</div>
          <h3>OKR This Week</h3>
        </div>
        <span class="command-card-badge">${items.length ? `${items.length} ranked` : 'Light week'}</span>
      </div>
      <p class="command-card-footnote">${escapeHtml(okrThisWeek?.summary || '')}</p>
      <div class="attention-list">
        ${items.length ? items.map((item) => `
          <article class="attention-item" data-kind="operational">
            <div class="attention-rank">${escapeHtml(String(item.progress || 0))}%</div>
            <div class="attention-body">
              <div class="attention-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <div class="attention-meta">
                  <span class="attention-chip">${escapeHtml(formatOkrStatusLabel(item.status))}</span>
                </div>
              </div>
              <p>${escapeHtml(item.objectiveTitle)}</p>
              ${item.note ? `<div class="attention-context"><div class="attention-context-label">Context</div><div>${escapeHtml(compactDashboardText(item.note, 120))}</div></div>` : ''}
            </div>
          </article>
        `).join('') : `
          <article class="resume-work-item resume-work-item-empty">
            <h4>${escapeHtml(okrThisWeek?.emptyTitle || 'Looks like a lighter OKR week')}</h4>
            <p>${escapeHtml(okrThisWeek?.emptyBody || '')}</p>
            <div class="shell-actions resume-work-actions">
              <button
                type="button"
                class="shell-action-btn shell-action-btn-secondary"
                data-shell-action="dashboard-capture-open"
              >
                Capture today's work
              </button>
            </div>
          </article>
        `}
      </div>
      <div class="shell-actions">
        <button
          type="button"
          class="shell-action-btn shell-action-btn-secondary"
          data-shell-action="open-shell-view"
          data-shell-view="${escapeAttribute(OKR_VIEW)}"
        >
          Open OKR Studio
        </button>
      </div>
    </article>
  `;
}

function scoreDashboardJiraItem(item, timeReality = {}) {
  let score = 0;
  const col = String(item.columnKey || '').toLowerCase();
  if (col === 'prog') score += 60;
  else if (col === 'review') score += 45;
  else if (col === 'blocked') score += 35;
  else score += 25;

  const priority = String(item.priority || '').toLowerCase();
  if (priority === 'highest' || priority === 'critical') score += 30;
  else if (priority === 'high') score += 24;
  else if (priority === 'medium') score += 14;
  else if (priority === 'low') score += 6;

  score += recencyScore(item.updated || item.created || '');
  if (timeReality?.meetingLoad === 'heavy' && col === 'prog') score += 8;
  if (item.commentCount) score += Math.min(6, Number(item.commentCount));
  return score;
}

function scoreDashboardOkrItem(kr, nextUp = [], timeReality = {}) {
  let score = 0;
  const status = String(kr.status || '').toLowerCase();
  if (status === 'atrisk') score += 40;
  else if (status === 'ontrack') score += 20;
  else if (status === 'notstarted') score += 14;

  const progress = Number(kr.progress || 0);
  if (progress >= 50 && progress < 100) score += 18;
  else if (progress < 50) score += 10;

  if (nextUp.some((item) => String(item.text || '').toLowerCase().includes(String(kr.title || '').toLowerCase()))) {
    score += 20;
  }

  if (timeReality?.meetingLoad === 'light') score += 4;
  return score;
}

function recencyScore(iso) {
  const ts = Date.parse(iso || '');
  if (!Number.isFinite(ts)) return 0;
  const ageDays = (Date.now() - ts) / 86400000;
  if (ageDays <= 1) return 24;
  if (ageDays <= 3) return 16;
  if (ageDays <= 7) return 10;
  if (ageDays <= 14) return 4;
  return 0;
}

function describeDashboardJiraItem(item) {
  const parts = [];
  if (item.columnLabel) parts.push(item.columnLabel);
  if (item.priority) parts.push(`${item.priority} priority`);
  const relative = formatRelativeIssueUpdate(item.updated || item.created || '');
  if (relative) parts.push(relative);
  return `${parts.join(' · ')}. ${buildDashboardJiraRecommendation(item)}`.trim();
}

function buildDashboardJiraRecommendation(item) {
  const col = String(item.columnKey || '').toLowerCase();
  if (col === 'prog') return 'Already moving, so this is a strong candidate for today\'s best work block.';
  if (col === 'review') return 'Likely close enough to unblock with one concentrated pass today.';
  if (col === 'blocked') return 'May need a decision or unblock before it becomes real work again.';
  return 'Looks workable today if you need a concrete assigned task to move forward.';
}

function formatRelativeIssueUpdate(iso) {
  const ts = Date.parse(iso || '');
  if (!Number.isFinite(ts)) return '';
  const ageDays = Math.floor((Date.now() - ts) / 86400000);
  if (ageDays <= 0) return 'updated today';
  if (ageDays === 1) return 'updated yesterday';
  if (ageDays < 7) return `updated ${ageDays}d ago`;
  return `updated ${Math.floor(ageDays / 7)}w ago`;
}

function formatOkrStatusLabel(status = '') {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'atrisk') return 'At risk';
  if (normalized === 'notstarted') return 'Not started';
  if (normalized === 'done') return 'Done';
  return 'On track';
}

function renderModuleRail(modules, {
  eyebrow = 'Workspace modules',
  title = 'Module Entry Points',
  badge = 'Secondary',
} = {}) {
  return `
    <article class="shell-card command-card">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">${escapeHtml(eyebrow)}</div>
          <h3>${escapeHtml(title)}</h3>
        </div>
        <span class="command-card-badge">${escapeHtml(badge)}</span>
      </div>
      <div class="module-grid">
        ${modules.map((item) => `
          <article class="module-tile">
            <div class="module-tile-topline">
              <h4>${escapeHtml(item.title)}</h4>
              <span>${escapeHtml(item.state)}</span>
            </div>
            <p>${escapeHtml(item.body)}</p>
            ${item.shellView || item.shellAction ? `
              <div class="shell-actions module-tile-actions">
                <button
                  type="button"
                  class="shell-action-btn"
                  data-shell-action="${escapeAttribute(item.shellAction || 'open-shell-view')}"
                  ${item.shellView ? `data-shell-view="${escapeAttribute(item.shellView)}"` : ''}
                  ${item.shellFocus ? `data-shell-focus="${escapeAttribute(item.shellFocus)}"` : ''}
                  ${item.shellOrigin ? `data-shell-origin="${escapeAttribute(item.shellOrigin)}"` : ''}
                >
                  ${escapeHtml(item.actionLabel || 'Open')}
                </button>
              </div>
            ` : ''}
          </article>
        `).join('')}
      </div>
    </article>
  `;
}

function renderMeetingsModule(meetings) {
  return `
    <article class="shell-card command-card" data-module-focus-target="meetings">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Personal Workspace</div>
          <h3>Meetings</h3>
        </div>
        <span class="command-card-badge">${escapeHtml(meetings.badge || 'Read-only')}</span>
      </div>
      <div class="brief-summary-grid module-summary-grid">
        ${meetings.summary.map((item) => `
          <div class="brief-summary-tile">
            <div class="brief-summary-label">${escapeHtml(item.label)}</div>
            <div class="brief-summary-value">${escapeHtml(item.value)}</div>
          </div>
        `).join('')}
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Readiness Notes</div>
        <ul class="command-bullet-list">
          ${meetings.readinessNotes.map((note) => `<li>${escapeHtml(note)}</li>`).join('')}
        </ul>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Prep Lens</div>
        <div class="module-grid">
          ${meetings.prepLens.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Read-only</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Follow-Up Pressure</div>
        <div class="module-grid">
          ${meetings.followUpPressure.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Proxy signal</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="shell-actions">
        ${meetings.actions.map((action) => `
          <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
            ${escapeHtml(action.label)}
          </button>
        `).join('')}
      </div>
      <p class="command-card-footnote">${escapeHtml(meetings.sourceNote)}</p>
    </article>
  `;
}

function renderCalendarModule(calendar) {
  return `
    <article class="shell-card command-card" data-module-focus-target="calendar">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Personal Workspace</div>
          <h3>Calendar</h3>
        </div>
        <span class="command-card-badge">${escapeHtml(calendar.badge || 'Read-only')}</span>
      </div>
      <div class="brief-summary-grid module-summary-grid">
        ${calendar.summary.map((item) => `
          <div class="brief-summary-tile">
            <div class="brief-summary-label">${escapeHtml(item.label)}</div>
            <div class="brief-summary-value">${escapeHtml(item.value)}</div>
          </div>
        `).join('')}
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Schedule Read</div>
        <div class="module-grid">
          ${calendar.scheduleRead.map((item) => `
            <article class="module-tile module-tile-calendar">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>${escapeHtml(item.tone || 'Live read')}</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Work Block Lens</div>
        <div class="module-grid">
          ${calendar.workBlockLens.map((item) => `
            <article class="module-tile module-tile-calendar">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>${escapeHtml(item.tone || 'Live read')}</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Overload Signals</div>
        <div class="module-grid">
          ${calendar.overloadSignals.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Proxy signal</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Transition Buffers</div>
        <div class="module-grid">
          ${calendar.transitionBuffers.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Low confidence</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="shell-actions">
        ${calendar.actions.map((action) => `
          <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
            ${escapeHtml(action.label)}
          </button>
        `).join('')}
      </div>
      <p class="command-card-footnote">${escapeHtml(calendar.sourceNote)}</p>
    </article>
  `;
}

function renderJiraModule(jira) {
  return `
    <article class="shell-card command-card" data-module-focus-target="jira-board">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Personal Workspace</div>
          <h3>Jira Board</h3>
        </div>
        <span class="command-card-badge">Read-only</span>
      </div>
      <div class="brief-summary-grid module-summary-grid">
        ${jira.summary.map((item) => `
          <div class="brief-summary-tile">
            <div class="brief-summary-label">${escapeHtml(item.label)}</div>
            <div class="brief-summary-value">${escapeHtml(item.value)}</div>
          </div>
        `).join('')}
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Queue Read</div>
        <div class="module-grid">
          ${jira.queueRead.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Low confidence</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Delivery Pressure</div>
        <div class="module-grid">
          ${jira.deliveryPressure.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Proxy signal</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Execution Risk</div>
        <div class="module-grid">
          ${jira.executionRisk.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Read-only</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="shell-actions">
        ${jira.actions.map((action) => `
          <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
            ${escapeHtml(action.label)}
          </button>
        `).join('')}
      </div>
      <p class="command-card-footnote">${escapeHtml(jira.sourceNote)}</p>
    </article>
  `;
}

function renderOkrModule(okr) {
  return `
    <article class="shell-card command-card" data-module-focus-target="okr-studio">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Personal Workspace</div>
          <h3>OKR Studio</h3>
        </div>
        <span class="command-card-badge">Read-only</span>
      </div>
      <div class="brief-summary-grid module-summary-grid">
        ${okr.summary.map((item) => `
          <div class="brief-summary-tile">
            <div class="brief-summary-label">${escapeHtml(item.label)}</div>
            <div class="brief-summary-value">${escapeHtml(item.value)}</div>
          </div>
        `).join('')}
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Goal Health</div>
        <div class="module-grid">
          ${okr.goalHealth.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Low confidence</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Evidence Inbox</div>
        <div class="module-grid">
          ${okr.evidenceInbox.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Proxy signal</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="command-subsection">
        <div class="command-subsection-title">Update Risk</div>
        <div class="module-grid">
          ${okr.updateRisk.map((item) => `
            <article class="module-tile">
              <div class="module-tile-topline">
                <h4>${escapeHtml(item.title)}</h4>
                <span>Read-only</span>
              </div>
              <p>${escapeHtml(item.detail)}</p>
            </article>
          `).join('')}
        </div>
      </div>
      <div class="shell-actions">
        ${okr.actions.map((action) => `
          <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
            ${escapeHtml(action.label)}
          </button>
        `).join('')}
      </div>
      <p class="command-card-footnote">${escapeHtml(okr.sourceNote)}</p>
    </article>
  `;
}

function renderFloatingAssistant(assistant) {
  return `
    <section class="shell-note command-assistant-note">
      <div class="command-card-header">
        <div>
          <div class="shell-eyebrow">Floating assistant</div>
          <div class="shell-note-title">Quick Action / Deep Co-Work</div>
        </div>
        <span class="command-card-badge">Shell handoff</span>
      </div>
      <p>${escapeHtml(assistant.summary)}</p>
      <div class="command-subsection">
        <div class="command-subsection-title">How To Use It</div>
        <p class="command-card-footnote">${escapeHtml(assistant.roleNote || '')}</p>
      </div>
      <div class="assistant-mode-grid">
        ${(assistant.modeCards || []).map((mode) => `
          <article class="module-tile assistant-mode-tile">
            <div class="module-tile-topline">
              <h4>${escapeHtml(mode.title)}</h4>
              <span>${escapeHtml(mode.eyebrow)}</span>
            </div>
            <p>${escapeHtml(mode.detail)}</p>
          </article>
        `).join('')}
      </div>
      <div class="shell-actions">
        ${assistant.actions.map((action) => `
          <button type="button" class="shell-action-btn" data-shell-action="open-chat-from-shell" data-shell-intent="${escapeAttribute(action.intent)}">
            ${escapeHtml(action.label)}
          </button>
        `).join('')}
      </div>
      <p class="command-card-footnote">Both actions still reuse the current shell-to-chat intent handoff. This pass does not auto-send, create a new runtime path, or broaden planner behavior.</p>
    </section>
  `;
}

function renderTimeRealityInput(timeReality) {
  return `
    <div class="time-reality-grid">
      <label class="shell-field">
        <span>Meeting Load</span>
        <select data-time-reality-field="meetingLoad">
          ${renderTimeRealityOption('unknown', 'Not set', timeReality.meetingLoad)}
          ${renderTimeRealityOption('light', 'Light', timeReality.meetingLoad)}
          ${renderTimeRealityOption('moderate', 'Moderate', timeReality.meetingLoad)}
          ${renderTimeRealityOption('heavy', 'Heavy', timeReality.meetingLoad)}
        </select>
      </label>
      <label class="shell-field">
        <span>Focus Bias</span>
        <select data-time-reality-field="focusPreference">
          ${renderTimeRealityOption('unspecified', 'No preference', timeReality.focusPreference)}
          ${renderTimeRealityOption('early', 'Early block', timeReality.focusPreference)}
          ${renderTimeRealityOption('midday', 'Midday block', timeReality.focusPreference)}
          ${renderTimeRealityOption('afternoon', 'Afternoon block', timeReality.focusPreference)}
          ${renderTimeRealityOption('after-meetings', 'After meetings', timeReality.focusPreference)}
        </select>
      </label>
      <label class="shell-field">
        <span>Next Hard Stop</span>
        <input
          type="text"
          value="${escapeAttribute(timeReality.nextHardStop)}"
          placeholder="e.g. 11:30 AM"
          data-time-reality-field="nextHardStop"
        />
      </label>
    </div>
  `;
}

function renderTimeRealityOption(value, label, currentValue) {
  return `<option value="${escapeAttribute(value)}"${currentValue === value ? ' selected' : ''}>${escapeHtml(label)}</option>`;
}

function describeNotification(type) {
  if (type === 'approval') return 'An approval request is waiting on a user decision.';
  if (type === 'error') return 'A runtime error is still visible in the notification stream.';
  if (type === 'agent') return 'An agent run surfaced an item that still needs intervention.';
  if (type === 'workflow') return 'A workflow-related event still needs review.';
  return 'An operational item is waiting in the notification stream.';
}

function buildNotificationNextStep(item) {
  if (item.type === 'approval') return 'Review the pending approval before more delegated work stacks behind it.';
  if (item.type === 'error') return 'Inspect the failing runtime path, then decide whether to retry or route around it.';
  if (item.type === 'agent' || item.type === 'workflow') return 'Confirm whether this delegated work should resume, be revised, or be parked.';
  return 'Open Chat to decide whether this item belongs in the active plan or the watch list.';
}

function inferAttentionUrgency(kind) {
  if (kind === 'approval') return 'Act now';
  if (kind === 'slack') return 'Reply';
  if (kind === 'runtime' || kind === 'system') return 'Stabilize';
  return 'Triage';
}

function buildAttentionReason({ kind, status, detail }) {
  if (kind === 'approval') {
    return `This surfaced because delegated work is waiting on a user decision, and the queue treats ${status.toLowerCase()} approvals as blockers first.`;
  }
  if (kind === 'runtime') {
    return `This surfaced because a recent runtime/tool failure can distort the rest of the plan until it is understood. Current signal: ${detail}`;
  }
  if (kind === 'system') {
    return `This surfaced because provider readiness affects what Artemis can safely route or delegate next. Current state: ${status}.`;
  }
  if (kind === 'slack') {
    return `This surfaced because Slack is connected, but Artemis only promotes the narrowest follow-up signals here so coordination work stays visible without taking over the shell. Current signal: ${detail}`;
  }
  return 'This surfaced because the shell saw an operational signal that may deserve a deliberate next step before the day gets noisier.';
}

function buildAttentionActionLabel(kind) {
  if (kind === 'approval') return 'Clear approval';
  if (kind === 'slack') return 'Plan reply';
  if (kind === 'runtime') return 'Triage failure';
  if (kind === 'system') return 'Stabilize provider';
  return 'Review item';
}

function buildAttentionIntent(title, nextStep) {
  return `Help me handle this Needs Attention item: ${title}. Suggested move: ${nextStep}`;
}

function dedupeByTitle(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = item.title;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function buildTaskCommandItemId(item, overrides = {}) {
  return [
    item.sourceSessionId || '',
    item.notificationType || '',
    overrides.owner || '',
    overrides.mode || '',
    item.title || '',
    item.why || item.detail || '',
  ].join('::');
}

function formatProviderLabel(providerId) {
  if (providerId === 'claude-code') return 'Claude Code';
  if (providerId === 'codex') return 'Codex';
  if (providerId === 'local') return 'Local';
  return providerId || 'System';
}

function formatMeetingLoad(meetingLoad) {
  if (meetingLoad === 'heavy') return 'Heavy';
  if (meetingLoad === 'moderate') return 'Moderate';
  if (meetingLoad === 'light') return 'Light';
  return 'Not set';
}

function formatFocusPreference(focusPreference) {
  if (focusPreference === 'early') return 'Early block';
  if (focusPreference === 'midday') return 'Midday block';
  if (focusPreference === 'afternoon') return 'Afternoon block';
  if (focusPreference === 'after-meetings') return 'After meetings';
  return 'Not set';
}

function summarizeJiraBoardRead(timeReality, notifications, analytics) {
  const unreadNotifications = notifications.filter((item) => !item.read_at).length;
  const recentErrors = Array.isArray(analytics?.recentErrors) ? analytics.recentErrors.length : 0;

  if (timeReality.meetingLoad === 'heavy' && timeReality.nextHardStop) {
    return 'Compressed / inferential';
  }
  if (unreadNotifications || recentErrors) {
    return 'Signal-derived';
  }
  return 'Low-confidence';
}

function pluralize(word, count) {
  return count === 1 ? word : `${word}s`;
}

function describeWorkflowInventoryItem(workflow) {
  const stepCount = Array.isArray(workflow?.steps) ? workflow.steps.length : 0;
  const stepLabels = (workflow?.steps || [])
    .map((step) => String(step?.label || '').trim())
    .filter(Boolean)
    .slice(0, 2);
  const description = String(workflow?.description || '').trim();

  if (description) {
    return `${description} ${stepCount ? `Currently ${stepCount} ${pluralize('step', stepCount)}.` : ''}`.trim();
  }
  if (stepLabels.length) {
    return `${stepCount} ${pluralize('step', stepCount)} saved: ${stepLabels.join(' -> ')}${stepCount > stepLabels.length ? '...' : ''}`;
  }
  if (stepCount) {
    return `Currently ${stepCount} saved ${pluralize('step', stepCount)} with no additional description yet.`;
  }
  return 'Saved in the current workflow config, but still missing step detail.';
}

function describeAgentInventoryItem(agent) {
  const description = String(agent?.description || '').trim();
  const systemPrompt = String(agent?.systemPrompt || agent?.prompt || '').trim();

  if (description) {
    return description;
  }
  if (systemPrompt) {
    return `${systemPrompt.slice(0, 110)}${systemPrompt.length > 110 ? '...' : ''}`;
  }
  return 'Saved in the current agent config, but still missing a visible summary.';
}

function describeChainInventoryItem(chain) {
  const agentCount = Array.isArray(chain?.agents) ? chain.agents.length : 0;
  const description = String(chain?.description || '').trim();

  if (description) {
    return `${description} ${agentCount ? `Currently ${agentCount} ${pluralize('linked agent', agentCount)}.` : ''}`.trim();
  }
  if (agentCount) {
    return `Runs ${agentCount} saved ${pluralize('agent', agentCount)} in a fixed left-to-right sequence.`;
  }
  return 'Saved as a chain, but still missing linked agent detail.';
}

function describeDagInventoryItem(dag) {
  const nodeCount = Array.isArray(dag?.nodes) ? dag.nodes.length : 0;
  const edgeCount = Array.isArray(dag?.edges) ? dag.edges.length : 0;
  const description = String(dag?.description || '').trim();

  if (description) {
    return `${description} ${nodeCount ? `Currently ${nodeCount} ${pluralize('node', nodeCount)}${edgeCount ? ` and ${edgeCount} ${pluralize('edge', edgeCount)}` : ''}.` : ''}`.trim();
  }
  if (nodeCount || edgeCount) {
    return `Runs as a saved dependency graph with ${nodeCount} ${pluralize('node', nodeCount)} and ${edgeCount} ${pluralize('edge', edgeCount)}.`;
  }
  return 'Saved as a DAG, but still missing graph detail.';
}

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll('\n', '&#10;');
}

function readTimeReality() {
  try {
    const parsed = JSON.parse(localStorage.getItem(TIME_REALITY_STORAGE_KEY) || '{}');
    return normalizeTimeReality(parsed);
  } catch {
    return normalizeTimeReality({});
  }
}

function readTaskCommandState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(TASK_COMMAND_STATE_STORAGE_KEY) || '{}');
    return typeof parsed === 'object' && parsed ? parsed : {};
  } catch {
    return {};
  }
}

function clearTaskCommandState() {
  localStorage.removeItem(TASK_COMMAND_STATE_STORAGE_KEY);
}

function readTaskCommandPreferences() {
  const state = readTaskCommandState();
  const rawMeta = state[TASK_COMMAND_STATE_META_KEY];
  const parsedMeta = typeof rawMeta === 'object' && rawMeta ? rawMeta : {};
  return {
    collapsedSections: Array.isArray(parsedMeta.collapsedSections)
      ? parsedMeta.collapsedSections.filter((title) => TASK_COMMAND_SECTION_OPTIONS.includes(title))
      : [],
    focusedOnly: Boolean(parsedMeta.focusedOnly),
    movedOnly: Boolean(parsedMeta.movedOnly),
    pinnedOnly: Boolean(parsedMeta.pinnedOnly),
  };
}

function writeTimeReality(value) {
  const normalized = normalizeTimeReality(value);
  localStorage.setItem(TIME_REALITY_STORAGE_KEY, JSON.stringify(normalized));
}

function writeTaskCommandItemState(itemId, patch) {
  if (!itemId) return;

  const currentState = readTaskCommandState();
  const existing = currentState[itemId] || {};

  currentState[itemId] = {
    ...existing,
    ...patch,
  };

  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function writeTaskCommandPreferences(patch) {
  const currentState = readTaskCommandState();
  const currentPreferences = readTaskCommandPreferences();

  currentState[TASK_COMMAND_STATE_META_KEY] = {
    ...currentPreferences,
    ...patch,
  };

  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function clearDismissedTaskCommandItemState() {
  const currentState = readTaskCommandState();
  let changed = false;

  Object.entries(currentState).forEach(([itemId, itemState]) => {
    if (!itemState?.dismissed) return;
    currentState[itemId] = {
      ...itemState,
      dismissed: false,
    };
    changed = true;
  });

  if (!changed) return;
  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function clearSnoozedTaskCommandItemState() {
  const currentState = readTaskCommandState();
  let changed = false;

  Object.entries(currentState).forEach(([itemId, itemState]) => {
    if (!itemState?.snoozed) return;
    currentState[itemId] = {
      ...itemState,
      snoozed: false,
    };
    changed = true;
  });

  if (!changed) return;
  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function clearHiddenTaskCommandItemState() {
  const currentState = readTaskCommandState();
  let changed = false;

  Object.entries(currentState).forEach(([itemId, itemState]) => {
    if (itemId === TASK_COMMAND_STATE_META_KEY || !itemState || typeof itemState !== 'object') return;
    if (!itemState.dismissed && !itemState.snoozed) return;
    currentState[itemId] = {
      ...itemState,
      dismissed: false,
      snoozed: false,
    };
    changed = true;
  });

  if (!changed) return;
  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function clearEmphasizedTaskCommandItemState() {
  const currentState = readTaskCommandState();
  const currentPreferences = readTaskCommandPreferences();
  let changed = false;

  Object.entries(currentState).forEach(([itemId, itemState]) => {
    if (itemId === TASK_COMMAND_STATE_META_KEY || !itemState?.emphasized) return;
    currentState[itemId] = {
      ...itemState,
      emphasized: false,
    };
    changed = true;
  });

  if (currentPreferences.focusedOnly) {
    currentState[TASK_COMMAND_STATE_META_KEY] = {
      ...currentPreferences,
      focusedOnly: false,
    };
    changed = true;
  }

  if (!changed) return;
  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function clearPinnedTaskCommandItemState() {
  const currentState = readTaskCommandState();
  const currentPreferences = readTaskCommandPreferences();
  let changed = false;

  Object.entries(currentState).forEach(([itemId, itemState]) => {
    if (itemId === TASK_COMMAND_STATE_META_KEY || !itemState?.pinned) return;
    currentState[itemId] = {
      ...itemState,
      pinned: false,
    };
    changed = true;
  });

  if (currentPreferences.pinnedOnly) {
    currentState[TASK_COMMAND_STATE_META_KEY] = {
      ...currentPreferences,
      pinnedOnly: false,
    };
    changed = true;
  }

  if (!changed) return;
  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function clearMovedTaskCommandItemState() {
  const currentState = readTaskCommandState();
  const currentPreferences = readTaskCommandPreferences();
  let changed = false;

  Object.entries(currentState).forEach(([itemId, itemState]) => {
    if (itemId === TASK_COMMAND_STATE_META_KEY || !itemState || typeof itemState !== 'object' || !itemState.sectionTitle) return;
    const nextState = { ...itemState };
    delete nextState.sectionTitle;
    currentState[itemId] = nextState;
    changed = true;
  });

  if (currentPreferences.movedOnly) {
    currentState[TASK_COMMAND_STATE_META_KEY] = {
      ...currentPreferences,
      movedOnly: false,
    };
    changed = true;
  }

  if (!changed) return;
  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function clearCollapsedTaskCommandSections() {
  const currentState = readTaskCommandState();
  const currentPreferences = readTaskCommandPreferences();
  if (!currentPreferences.collapsedSections.length) return;

  currentState[TASK_COMMAND_STATE_META_KEY] = {
    ...currentPreferences,
    collapsedSections: [],
  };

  localStorage.setItem(TASK_COMMAND_STATE_STORAGE_KEY, JSON.stringify(currentState));
}

function summarizeTaskCommandLocalState() {
  const state = readTaskCommandState();
  const entries = Object.entries(state)
    .filter(([itemId, itemState]) => itemId !== TASK_COMMAND_STATE_META_KEY && itemState && typeof itemState === 'object')
    .map(([, itemState]) => itemState);
  const preferences = readTaskCommandPreferences();
  return {
    editedCount: entries.length,
    collapsedSectionCount: preferences.collapsedSections.length,
    dismissedCount: entries.filter((itemState) => itemState.dismissed).length,
    emphasizedCount: entries.filter((itemState) => itemState.emphasized).length,
    pinnedCount: entries.filter((itemState) => itemState.pinned).length,
    movedCount: entries.filter((itemState) => TASK_COMMAND_SECTION_OPTIONS.includes(itemState.sectionTitle)).length,
    snoozedCount: entries.filter((itemState) => itemState.snoozed).length,
    focusedOnly: preferences.focusedOnly,
    movedOnly: preferences.movedOnly,
    pinnedOnly: preferences.pinnedOnly,
  };
}

function normalizeTimeReality(value) {
  return {
    meetingLoad: normalizeTimeRealityEnum(value?.meetingLoad, ['unknown', 'light', 'moderate', 'heavy'], 'unknown'),
    focusPreference: normalizeTimeRealityEnum(value?.focusPreference, ['unspecified', 'early', 'midday', 'afternoon', 'after-meetings'], 'unspecified'),
    nextHardStop: String(value?.nextHardStop || '').trim().slice(0, 40),
  };
}

function normalizeTimeRealityEnum(value, allowed, fallback) {
  return allowed.includes(value) ? value : fallback;
}

function describeTimeRealityLead(timeReality) {
  if (timeReality.nextHardStop) {
    return `The current manual time reality says you have a hard stop at ${timeReality.nextHardStop}.`;
  }
  if (timeReality.meetingLoad === 'heavy') {
    return 'The current manual time reality says the day is meeting-heavy.';
  }
  if (timeReality.focusPreference === 'afternoon') {
    return 'The current manual time reality is biasing toward an afternoon focus block.';
  }
  if (timeReality.focusPreference === 'early') {
    return 'The current manual time reality is biasing toward an early protected block.';
  }
  return '';
}

function buildTimeRealityPrepNote(timeReality) {
  if (timeReality.meetingLoad === 'heavy' && timeReality.nextHardStop) {
    return `The shell is treating today as meeting-heavy, so it is biasing toward a shorter work block before ${timeReality.nextHardStop}.`;
  }
  if (timeReality.meetingLoad === 'heavy') {
    return 'The shell is treating today as meeting-heavy, so prep and transition overhead are being weighted more heavily than usual.';
  }
  if (timeReality.focusPreference === 'after-meetings') {
    return 'The shell is holding the deepest work until after meetings so the read-only plan does not over-promise early focus time.';
  }
  if (timeReality.nextHardStop) {
    return `The shell is compressing the active plan around the next hard stop at ${timeReality.nextHardStop}.`;
  }
  return '';
}

function inferNowEffort(timeReality) {
  if (timeReality.meetingLoad === 'heavy') return '20-30m';
  if (timeReality.nextHardStop) return '30-45m';
  return '45-60m';
}

function inferTodayContext(timeReality) {
  if (timeReality.nextHardStop) return `Work before ${timeReality.nextHardStop}`;
  if (timeReality.focusPreference === 'after-meetings') return 'After meetings';
  if (timeReality.focusPreference === 'afternoon') return 'Afternoon block';
  if (timeReality.focusPreference === 'midday') return 'Midday block';
  if (timeReality.focusPreference === 'early') return 'Early block';
  return 'Plan';
}

function buildTaskSourceNote(timeReality) {
  const timeStatus = buildManualTimeRealityStatus(timeReality);
  return `Light local edits are now available in Slice 3: pin, move, and dismiss only affect this browser for now. ${timeStatus}`.trim();
}

function buildManualTimeRealityStatus(timeReality) {
  if (timeReality.nextHardStop || timeReality.meetingLoad !== 'unknown' || timeReality.focusPreference !== 'unspecified') {
    return 'Manual time reality is currently shaping the shell. ';
  }
  return 'Manual time reality is still unset, so the shell is staying conservative. ';
}

function buildIntentWithTimeReality(intent, timeReality) {
  if (!intent) return '';
  const fragments = [];
  if (timeReality.meetingLoad !== 'unknown') {
    fragments.push(`meeting load: ${timeReality.meetingLoad}`);
  }
  if (timeReality.focusPreference !== 'unspecified') {
    fragments.push(`focus bias: ${timeReality.focusPreference}`);
  }
  if (timeReality.nextHardStop) {
    fragments.push(`next hard stop: ${timeReality.nextHardStop}`);
  }
  if (!fragments.length) return intent;
  return `${intent}\n\nTime reality today: ${fragments.join(', ')}.`;
}
