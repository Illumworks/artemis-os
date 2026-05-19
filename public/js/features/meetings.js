/**
 * Meetings feature module — J6c post-meeting workflow.
 *
 * Extracted from home.js and extended with:
 *  - tabs: Actions (default) + Transcript
 *  - AI ask composer on Transcript tab
 *  - 4 follow-up action kebab menus per action item
 *  - Persisted routing pills (loaded from /api/meetings/{id}/routings)
 *  - Web Components: artemis-meeting-jira-picker, artemis-meeting-okr-picker,
 *    artemis-meeting-slack-picker, artemis-meeting-todo-confirm
 */

// ── escape helpers (inlined so this module has no deps on home.js) ─────────

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeAttribute(str) {
  return escapeHtml(str);
}

// ── Action item extraction ──────────────────────────────────────────────────
// Identical logic as the copy in home.js; kept here so home.js can import it.

export function extractActionItemsFromText(text) {
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
    const bullet = line.match(/^[-*•]\s+(.+)$/);
    if (bullet && (inActionBlock || /\b(todo|action|follow up|will|should|owner)\b/i.test(bullet[1]))) {
      items.push(bullet[1].replace(/\s+/g, ' ').trim());
      continue;
    }
    const prefixed = line.match(/^(?:todo|action(?: item)?|follow[- ]?up)\s*[:–-]\s*(.+)$/i);
    if (prefixed) items.push(prefixed[1].trim());
  }
  return Array.from(new Set(items)).slice(0, 20);
}

// ── Per-meeting cache ───────────────────────────────────────────────────────

// Stores the last-loaded meeting detail so tabs don't re-fetch.
// Shape: { meetingId, title, summary, transcript, actionItems, attendees, routings }
let _meetingCache = null;

// ── Routing pill renderer ───────────────────────────────────────────────────

function _routingPill(routing) {
  const labels = { jira: 'Jira', okr: 'OKR', slack: 'Slack', todo: 'Todo' };
  const label = labels[routing.routed_to] || routing.routed_to;
  const extra = routing.target_id
    ? (routing.target_url
        ? ` <a href="${escapeAttribute(routing.target_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(routing.target_id)}</a>`
        : ` ${escapeHtml(routing.target_id)}`)
    : '';
  return `<span class="meeting-action-pill" data-routed-to="${escapeAttribute(routing.routed_to)}">${escapeHtml(label)}${extra}</span>`;
}

// ── Actions tab ─────────────────────────────────────────────────────────────

function _renderActionsTab(actionItems, routings) {
  if (!actionItems.length) {
    return `<div class="page-section-footnote">No action items extracted from this meeting.</div>`;
  }

  // Build a map: action_text → [routing, ...]
  const routingMap = {};
  for (const r of (routings || [])) {
    if (!routingMap[r.action_text]) routingMap[r.action_text] = [];
    routingMap[r.action_text].push(r);
  }

  return `
    <div class="meetings-actions-list" data-meetings-actions-list>
      ${actionItems.map((item) => {
        const pills = (routingMap[item] || []).map(_routingPill).join('');
        return `
          <div class="meetings-action-row" data-action-text="${escapeAttribute(item)}">
            <span class="meetings-action-text">${escapeHtml(item)}</span>
            <span class="meetings-action-pills">${pills}</span>
            <div class="meetings-action-kebab-wrap" style="position:relative">
              <button type="button" class="meetings-action-kebab" title="Actions" data-action="meetings-action-kebab">&#8943;</button>
              <div class="meetings-action-menu hidden" data-meetings-action-menu>
                <button type="button" class="meetings-action-menu-item" data-route="jira">Convert to Jira issue</button>
                <button type="button" class="meetings-action-menu-item" data-route="okr">Update OKR key result</button>
                <button type="button" class="meetings-action-menu-item" data-route="slack">Schedule Slack reminder</button>
                <button type="button" class="meetings-action-menu-item" data-route="todo">Save as personal todo</button>
              </div>
            </div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

// ── Transcript tab ──────────────────────────────────────────────────────────

function _renderTranscriptTab(transcript) {
  const transcriptHtml = transcript
    ? `<pre class="meetings-transcript-body">${escapeHtml(transcript)}</pre>`
    : `<div class="page-section-footnote">No transcript captured for this meeting.</div>`;

  return `
    <div class="meetings-ask-composer" data-meetings-ask-composer>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <input
          type="text"
          class="meetings-ask-input"
          placeholder="Ask a question about this transcript…"
          data-meetings-ask-input
          aria-label="Ask about meeting"
        />
        <button type="button" class="shell-action-btn" data-action="meetings-ask-submit">Ask</button>
      </div>
      <div class="meetings-ask-answer hidden" data-meetings-ask-answer></div>
    </div>
    ${transcriptHtml}
  `;
}

// ── Right-panel renderer ────────────────────────────────────────────────────

function _renderPanelTabs(activeTab) {
  return `
    <nav class="meetings-tab-strip" style="margin-bottom:12px">
      <button type="button" class="meetings-tab-btn${activeTab === 'actions' ? ' active' : ''}"
              data-action="meetings-panel-tab" data-tab="actions">Actions</button>
      <button type="button" class="meetings-tab-btn${activeTab === 'transcript' ? ' active' : ''}"
              data-action="meetings-panel-tab" data-tab="transcript">Transcript</button>
    </nav>
  `;
}

export function renderMeetingPanel(activeTab = 'actions') {
  if (!_meetingCache) {
    return `<div class="page-section-footnote">Click a meeting to view its summary, action items, and transcript.</div>`;
  }

  const { title, attendees, actionItems, transcript, routings } = _meetingCache;
  const attendeesHtml = attendees ? `<div class="page-section-meta">${escapeHtml(attendees)}</div>` : '';

  const tabContent = activeTab === 'transcript'
    ? _renderTranscriptTab(transcript)
    : _renderActionsTab(actionItems, routings);

  return `
    <div class="meetings-transcript-header">
      <strong>${escapeHtml(title || 'Meeting')}</strong>
      ${attendeesHtml}
    </div>
    ${_renderPanelTabs(activeTab)}
    <div data-meetings-panel-content>
      ${tabContent}
    </div>
  `;
}

// ── Load meeting detail ─────────────────────────────────────────────────────

/**
 * Render summary/action items/transcript sections from a cached summary row.
 * Mirrors the home.js single-column renderer but populates _meetingCache so
 * the panel tabs (Actions / Transcript) work correctly.
 */
function _renderSummaryDetail(panelEl, meetingId, meetingTitle, summaryData, routings) {
  const summary = summaryData.summary || '';
  const transcript = summaryData.transcript || '';
  const rawItems = summaryData.action_items;
  const actionItems = Array.isArray(rawItems) && rawItems.length
    ? rawItems.map((item) => (typeof item === 'string' ? item : (item.text || '')))
    : extractActionItemsFromText(summary || transcript);

  _meetingCache = {
    meetingId,
    title: meetingTitle || summaryData.title || 'Meeting',
    summary,
    transcript,
    actionItems,
    attendees: '',
    routings,
  };

  panelEl.innerHTML = renderMeetingPanel('actions');
  _wirePanel(panelEl, meetingId);
}

export async function loadMeetingDetail(meetingId, meetingTitle, panelEl) {
  if (!meetingId || !panelEl) return;

  panelEl.innerHTML = `<div class="meetings-transcript-loading">Loading…</div>`;

  // Always load routings in parallel regardless of transcript source.
  const routingsPromise = fetch(`/api/meetings/${encodeURIComponent(meetingId)}/routings`)
    .then((r) => r.json())
    .catch(() => ({ routings: [] }));

  try {
    // 1. Try cached summary first (instant, survives token expiry).
    const summaryRes = await fetch(`/api/meetings/${encodeURIComponent(meetingId)}/summary`);
    if (summaryRes.ok) {
      const [summaryData, routingsRes] = await Promise.all([
        summaryRes.json(),
        routingsPromise,
      ]);
      const routings = routingsRes.routings || [];
      _renderSummaryDetail(panelEl, meetingId, meetingTitle, summaryData, routings);
      return;
    }

    // 2. Cache miss (404) — fall back to live Granola fetch.
    if (summaryRes.status !== 404) {
      // Unexpected error from summary endpoint; log and fall through to live.
      console.warn(`meetings: summary endpoint returned ${summaryRes.status} for ${meetingId}`);
    } else {
      console.warn(`meetings: no cached summary for ${meetingId}, fetching live from Granola`);
    }

    const [detailRes, routingsRes] = await Promise.all([
      fetch(`/api/granola/transcript/${encodeURIComponent(meetingId)}`).then((r) => r.json()),
      routingsPromise,
    ]);

    if (!detailRes.connected) {
      panelEl.innerHTML = `<div class="page-section-footnote">Could not load this meeting.</div>`;
      return;
    }
    if (detailRes.found === false) {
      panelEl.innerHTML = `<div class="page-section-footnote">No transcript available for this meeting.</div>`;
      return;
    }

    const summary = detailRes.summary || detailRes.notes || '';
    const transcript = detailRes.transcript || '';
    const rawItems = detailRes.action_items;
    const actionItems = Array.isArray(rawItems) && rawItems.length
      ? rawItems.map((item) => (typeof item === 'string' ? item : (item.text || '')))
      : extractActionItemsFromText(summary || transcript);
    const attendees = Array.isArray(detailRes.attendees)
      ? detailRes.attendees.join(', ')
      : '';
    const routings = routingsRes.routings || [];

    _meetingCache = {
      meetingId,
      title: meetingTitle || detailRes.title || 'Meeting',
      summary,
      transcript,
      actionItems,
      attendees,
      routings,
    };

    panelEl.innerHTML = renderMeetingPanel('actions');
    _wirePanel(panelEl, meetingId);
  } catch {
    panelEl.innerHTML = `<div class="page-section-footnote">Failed to load meeting.</div>`;
  }
}

// ── Panel tab switch ────────────────────────────────────────────────────────

function _switchPanelTab(panelEl, tab) {
  const content = panelEl.querySelector('[data-meetings-panel-content]');
  if (!content) return;

  // Update tab buttons
  panelEl.querySelectorAll('[data-action="meetings-panel-tab"]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });

  content.innerHTML = tab === 'transcript'
    ? _renderTranscriptTab(_meetingCache?.transcript || '')
    : _renderActionsTab(_meetingCache?.actionItems || [], _meetingCache?.routings || []);

  if (tab !== 'transcript') {
    _wireActions(content, _meetingCache?.meetingId || '');
  }
}

// ── Ask submit ──────────────────────────────────────────────────────────────

async function _handleAskSubmit(panelEl, meetingId) {
  const input = panelEl.querySelector('[data-meetings-ask-input]');
  const answerEl = panelEl.querySelector('[data-meetings-ask-answer]');
  if (!input || !answerEl) return;

  const question = (input.value || '').trim();
  if (!question) return;

  answerEl.classList.remove('hidden');
  answerEl.textContent = 'Thinking…';

  try {
    const res = await fetch(`/api/meetings/${encodeURIComponent(meetingId)}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    if (data.answer) {
      answerEl.innerHTML = `<strong>Answer:</strong> ${escapeHtml(data.answer)}`;
    } else {
      answerEl.textContent = data.error || 'No answer returned.';
    }
  } catch {
    answerEl.textContent = 'Ask failed. Please try again.';
  }
}

// ── Action routing ──────────────────────────────────────────────────────────

async function _routeAction(actionText, route, meetingId, rowEl) {
  switch (route) {
    case 'jira': {
      const modal = document.querySelector('artemis-meeting-jira-picker');
      if (modal) modal.open(actionText, meetingId, rowEl);
      break;
    }
    case 'okr': {
      const modal = document.querySelector('artemis-meeting-okr-picker');
      if (modal) modal.open(actionText, meetingId, rowEl);
      break;
    }
    case 'slack': {
      const modal = document.querySelector('artemis-meeting-slack-picker');
      if (modal) modal.open(actionText, meetingId, rowEl);
      break;
    }
    case 'todo': {
      await _routeToTodo(actionText, meetingId, rowEl);
      break;
    }
  }
}

async function _routeToTodo(actionText, meetingId, rowEl) {
  try {
    const res = await fetch(`/api/meetings/${encodeURIComponent(meetingId)}/actions/todo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action_text: actionText }),
    });
    const data = await res.json();
    if (data.ok) {
      _addPillToRow(rowEl, { routed_to: 'todo', target_id: String(data.id), target_url: null, action_text: actionText });
    } else {
      console.warn('Todo route failed:', data);
    }
  } catch (err) {
    console.error('_routeToTodo error:', err);
  }
}

// Append a routing pill to the action row and update the cache.
export function _addPillToRow(rowEl, routing) {
  if (!rowEl) return;
  const pillsEl = rowEl.querySelector('.meetings-action-pills');
  if (pillsEl) {
    const existing = pillsEl.querySelector(`[data-routed-to="${routing.routed_to}"]`);
    if (!existing) {
      pillsEl.insertAdjacentHTML('beforeend', _routingPill(routing));
    }
  }
  // Update cache
  if (_meetingCache) {
    const alreadyIn = _meetingCache.routings.some(
      (r) => r.action_text === routing.action_text && r.routed_to === routing.routed_to
    );
    if (!alreadyIn) _meetingCache.routings.push(routing);
  }
}

// ── Kebab menu wiring ───────────────────────────────────────────────────────

function _wireActions(containerEl, meetingId) {
  // Delegate all clicks in the action list
  containerEl.addEventListener('click', (e) => {
    // Kebab toggle
    const kebabBtn = e.target.closest('[data-action="meetings-action-kebab"]');
    if (kebabBtn) {
      const menu = kebabBtn.nextElementSibling;
      if (menu) menu.classList.toggle('hidden');
      e.stopPropagation();
      return;
    }
    // Menu item
    const menuItem = e.target.closest('[data-meetings-action-menu] .meetings-action-menu-item');
    if (menuItem) {
      const menu = menuItem.closest('[data-meetings-action-menu]');
      const row = menuItem.closest('[data-action-text]');
      const actionText = row?.dataset.actionText || '';
      const route = menuItem.dataset.route;
      if (menu) menu.classList.add('hidden');
      if (actionText && route) {
        _routeAction(actionText, route, meetingId, row);
      }
      return;
    }
  });

  // Close open menus on outside click
  document.addEventListener(
    'click',
    () => {
      containerEl.querySelectorAll('[data-meetings-action-menu]').forEach((m) => m.classList.add('hidden'));
    },
    { once: false }
  );
}

// ── Full panel wiring ───────────────────────────────────────────────────────

function _wirePanel(panelEl, meetingId) {
  panelEl.addEventListener('click', (e) => {
    // Panel tab switch
    const tabBtn = e.target.closest('[data-action="meetings-panel-tab"]');
    if (tabBtn) {
      _switchPanelTab(panelEl, tabBtn.dataset.tab);
      return;
    }
    // Ask submit
    if (e.target.closest('[data-action="meetings-ask-submit"]')) {
      _handleAskSubmit(panelEl, meetingId);
      return;
    }
  });

  // Wire action tab's kebab menus
  const actionsContainer = panelEl.querySelector('[data-meetings-panel-content]');
  if (actionsContainer) {
    _wireActions(actionsContainer, meetingId);
  }
}

// ── handleMeetingsRowClick (drop-in replacement for home.js) ────────────────

export async function handleMeetingsRowClick(meetingId, meetingTitle, appShellContent) {
  if (!meetingId || !appShellContent) return;

  const panel = appShellContent.querySelector('[data-meetings-transcript-panel]');
  if (!panel) return;

  await loadMeetingDetail(meetingId, meetingTitle, panel);
}

// ── Granola Today canvas with side-by-side layout ──────────────────────────

export function renderMeetingsGranolaTodayCanvas(viewModel) {
  const meetings = viewModel.todayMeetings || [];
  const list = meetings.length
    ? meetings.map((m) => `
        <div class="page-list-row meetings-past-row"
             data-meeting-id="${escapeAttribute(m.id || '')}"
             data-meeting-title="${escapeAttribute(m.title || '')}"
             data-meeting-status="${escapeAttribute(m.status || 'scheduled')}">
          <span class="meetings-past-row-date">${escapeHtml(m.startLabel || '')}</span>
          <span class="meetings-past-row-title">${escapeHtml(m.title || 'Untitled meeting')}</span>
          ${m.location ? `<span class="meetings-past-row-participants">${escapeHtml(m.location)}</span>` : ''}
        </div>
      `).join('')
    : `<div class="page-empty-state"><p>No meetings on the calendar for today.</p></div>`;

  return `
    <section class="page-canvas meetings-past-canvas">
      <article class="page-section col-span-4" data-page-section="meetings-today-list">
        <div class="page-section-header">
          <div>
            <div class="page-section-eyebrow">Today &middot; ${escapeHtml(String(meetings.length))} total</div>
            <h3 class="page-section-title">Meetings</h3>
          </div>
        </div>
        <div class="page-list" data-meetings-today-list>${list}</div>
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

// ── Past canvas renderer ───────────────────────────────────────────────────

export function renderMeetingsPastCanvas(granolaConnected) {
  if (!granolaConnected) {
    return `
      <section class="page-canvas" data-meetings-canvas="past">
        <article class="page-section col-span-12">
          <div class="page-empty-state">
            <h3>Connect Granola to browse past meetings</h3>
            <p>Past meeting transcripts are pulled from Granola. Connect it through the Connectors hub.</p>
          </div>
        </article>
      </section>
    `;
  }

  return `
    <section class="page-canvas meetings-past-canvas" data-meetings-canvas="past">
      <article class="page-section col-span-4" data-page-section="meetings-past-list-col">
        <div class="page-section-header">
          <div>
            <div class="page-section-eyebrow">Last 30 days</div>
            <h3 class="page-section-title">Past Meetings</h3>
          </div>
        </div>
        <div class="meetings-search-row">
          <input
            type="search"
            class="meetings-search-input"
            placeholder="Search meetings&hellip;"
            data-meetings-search-input
            aria-label="Search meetings"
          />
          <button type="button" class="shell-action-btn" data-shell-action="meetings-search-submit">Search</button>
        </div>
        <div data-meetings-search-results class="meetings-search-results hidden"></div>
        <div class="page-list" data-meetings-past-list>
          <div class="meetings-transcript-loading">Loading meetings&hellip;</div>
        </div>
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

// ── renderMeetingsPastList ──────────────────────────────────────────────────

export function renderMeetingsPastList(meetings, appShellContent) {
  if (!appShellContent) return;
  const listEl = appShellContent.querySelector('[data-meetings-past-list]');
  if (!listEl) return;

  if (!meetings || meetings.length === 0) {
    listEl.innerHTML = `<div class="page-empty-state"><p>No meetings found in the last 30 days.</p></div>`;
    return;
  }

  listEl.innerHTML = meetings.map((m) => {
    const dateStr = m.dateMs
      ? new Date(m.dateMs).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
      : '';
    const participants = (m.participants || []).slice(0, 3).join(', ');
    return `
      <div class="page-list-row meetings-past-row"
           data-meeting-id="${escapeAttribute(m.id)}"
           data-meeting-title="${escapeAttribute(m.title || '')}">
        <span class="meetings-past-row-date">${escapeHtml(dateStr)}</span>
        <span class="meetings-past-row-title">${escapeHtml(m.title || 'Untitled meeting')}</span>
        ${participants ? `<span class="meetings-past-row-participants">${escapeHtml(participants)}</span>` : ''}
      </div>
    `;
  }).join('');
}

// ── Clear cache when switching away from Meetings view ─────────────────────

export function clearMeetingCache() {
  _meetingCache = null;
}
