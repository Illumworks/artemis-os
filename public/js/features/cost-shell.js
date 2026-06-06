/**
 * Cost shell — full-page Cost visibility dashboard (Phase 2).
 *
 * Four tabs: Spend (live) · Routing opportunities · Cloud infra · Budgets
 * Phase 2 renders only the Spend tab. The other three show "coming soon" placeholders.
 *
 * UX GUARD — MANDATORY:
 *   The hero headline and "This month" card MUST frame the dollar amount as
 *   synthetic API cost, not "a bill".  Jon is on a flat subscription; these
 *   numbers represent what the same call volume would cost at on-demand API rates.
 *   Lead will fail the smoke if any copy implies Jon is being charged per-token.
 *
 * Mounts into #app-shell-content when view === 'cost'.
 */

// ── State ─────────────────────────────────────────────────────────────────────

let _state = {
  window: 'this-month',      // 'today' | 'this-week' | 'this-month' | 'last-30-days' | 'custom'
  customFrom: null,
  customTo: null,
  providerFilter: null,
  modelFilter: null,
  data: null,                // last fetched /api/costs/summary response
  loading: false,
  error: null,
  tab: 'spend',              // 'spend' | 'routing' | 'cloud' | 'budgets'
  sortCol: 'cost_usd',       // top-calls sort column
  sortDir: 'desc',           // 'asc' | 'desc'
  routingData: null,         // last fetched /api/costs/routing-opportunities response
  routingLoading: false,
  routingError: null,
};

// ── Escape helper ─────────────────────────────────────────────────────────────

const _esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );

// ── Time-window → API params ──────────────────────────────────────────────────

function _windowParams(state) {
  const now = new Date();
  const pad2 = (n) => String(n).padStart(2, '0');
  const iso = (d) => d.toISOString().replace(/\.\d{3}Z$/, 'Z');

  switch (state.window) {
    case 'today': {
      const start = new Date(now);
      start.setUTCHours(0, 0, 0, 0);
      return { from: iso(start), to: iso(now) };
    }
    case 'this-week': {
      const start = new Date(now);
      const dow = start.getUTCDay(); // 0=Sun
      start.setUTCDate(start.getUTCDate() - dow);
      start.setUTCHours(0, 0, 0, 0);
      return { from: iso(start), to: iso(now) };
    }
    case 'this-month': {
      const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
      return { from: iso(start), to: iso(now) };
    }
    case 'last-30-days': {
      const start = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
      return { from: iso(start), to: iso(now) };
    }
    case 'custom': {
      return {
        from: state.customFrom ? state.customFrom + 'T00:00:00Z' : undefined,
        to: state.customTo ? state.customTo + 'T23:59:59Z' : undefined,
      };
    }
    default:
      return {};
  }
}

// ── Fetch ─────────────────────────────────────────────────────────────────────

async function _fetchSummary() {
  _state.loading = true;
  _state.error = null;
  _renderCostShell();

  const params = new URLSearchParams();
  const wp = _windowParams(_state);
  if (wp.from) params.set('from', wp.from);
  if (wp.to) params.set('to', wp.to);
  if (_state.providerFilter) params.set('provider', _state.providerFilter);
  if (_state.modelFilter) params.set('model', _state.modelFilter);

  try {
    const resp = await fetch(`/api/costs/summary?${params}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    _state.data = await resp.json();
  } catch (err) {
    _state.error = err.message || 'Failed to load cost data.';
  } finally {
    _state.loading = false;
    _renderCostShell();
  }
}

// ── Format helpers ────────────────────────────────────────────────────────────

function _fmtUsd(n) {
  if (n == null) return '$0.00';
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _fmtTokens(n) {
  if (n == null || n === 0) return '0';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K';
  return String(n);
}

function _pct(a, b) {
  if (!b || b === 0) return null;
  return Math.round(((a - b) / b) * 100);
}

function _relativeTime(iso) {
  const diff = (Date.now() - new Date(iso)) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.round(diff / 60) + 'm ago';
  if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
  return Math.round(diff / 86400) + 'd ago';
}

// ── Render ────────────────────────────────────────────────────────────────────

let _mountEl = null;

export function loadCostShell(container) {
  _mountEl = container;
  _state.tab = 'spend';
  _fetchSummary();
}

function _renderCostShell() {
  if (!_mountEl) return;
  _mountEl.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'cost-shell';
  el.innerHTML = _buildShellHTML();
  _mountEl.appendChild(el);
  _wireEvents(el);
}

function _buildShellHTML() {
  const { data, loading, error, tab } = _state;

  const heroHTML = _buildHero(data);
  const tabsHTML = _buildTabs();
  const bodyHTML = loading
    ? `<div class="cost-loading">Loading cost data…</div>`
    : error
    ? `<div class="cost-error">Error: ${_esc(error)}</div>${_buildTabBody(data, tab)}`
    : _buildTabBody(data, tab);

  return `
    <div class="cost-header">
      <h1 class="cost-title">Cost</h1>
    </div>
    ${heroHTML}
    <div class="cost-tabs">${tabsHTML}</div>
    <div class="cost-tab-body">${bodyHTML}</div>
  `;
}

// ── Hero block ─────────────────────────────────────────────────────────────────
// UX GUARD: must frame dollar amount as synthetic API cost, not "your bill."
// Copy spec:
//   Headline: "Projected API cost this month: $X · last month: $Y (Δ%) · actual subscription: flat"
//   Subhead:  "Subscription is flat; this number shows what the same volume of calls would cost
//              at on-demand API rates — useful for cloud-deployment projections and routing decisions."

function _buildHero(data) {
  if (!data) {
    return `
      <div class="cost-hero">
        <p class="cost-hero-headline">Projected API cost this month: —</p>
        <p class="cost-hero-subhead">Subscription is flat; this number shows what the same volume of calls would cost at on-demand API rates — useful for cloud-deployment projections and routing decisions.</p>
      </div>`;
  }

  const thisCost = _fmtUsd(data.totals.cost_usd);
  const lastCost = _fmtUsd(data.prior_totals.cost_usd);
  const pctDelta = _pct(data.totals.cost_usd, data.prior_totals.cost_usd);
  const deltaStr = pctDelta != null
    ? (pctDelta >= 0 ? `up ${Math.abs(pctDelta)}%` : `down ${Math.abs(pctDelta)}%`)
    : '';

  // Horizontal sparkline — normalize daily bars to max
  const days = data.daily || [];
  const maxCost = days.reduce((m, d) => Math.max(m, d.cost_usd), 0.001);
  const sparkBars = days.slice(-30).map((d) => {
    const pct = Math.max(2, Math.round((d.cost_usd / maxCost) * 100));
    return `<div class="cost-sparkline-bar" style="height:${pct}%" title="${d.date}: ${_fmtUsd(d.cost_usd)}"></div>`;
  }).join('');

  return `
    <div class="cost-hero">
      <p class="cost-hero-headline">Projected API cost this month: ${_esc(thisCost)} · last month: ${_esc(lastCost)} ${deltaStr ? '(' + _esc(deltaStr) + ')' : ''} · actual subscription: flat</p>
      <p class="cost-hero-subhead">Subscription is flat; this number shows what the same volume of calls would cost at on-demand API rates — useful for cloud-deployment projections and routing decisions.</p>
      <div class="cost-sparkline">${sparkBars}</div>
    </div>`;
}

// ── Tabs row ──────────────────────────────────────────────────────────────────

function _buildTabs() {
  const tabs = [
    { id: 'spend', label: 'Spend' },
    { id: 'routing', label: 'Routing opportunities' },
    { id: 'cloud', label: 'Cloud infra' },
    { id: 'budgets', label: 'Budgets' },
  ];
  return tabs
    .map(
      (t) =>
        `<button class="cost-tab${_state.tab === t.id ? ' active' : ''}" data-cost-tab="${_esc(t.id)}">${_esc(t.label)}</button>`
    )
    .join('');
}

// ── Tab body dispatcher ───────────────────────────────────────────────────────

function _buildTabBody(data, tab) {
  if (tab === 'spend') return _buildSpendTab(data);
  if (tab === 'routing') return _buildRoutingTab();
  if (tab === 'cloud') return _buildPlaceholder(
    'Cloud infra',
    'Coming in a follow-up phase. This tab will project what running Artemis on Fly.io would cost monthly, combining compute, Postgres, and storage estimates with your synthetic API spend.'
  );
  if (tab === 'budgets') return _buildPlaceholder(
    'Budgets',
    'Coming in a follow-up phase. This tab will let you set soft spend thresholds per feature or model and receive in-app alerts when they are crossed.'
  );
  return '';
}

function _buildPlaceholder(title, desc) {
  return `
    <div class="cost-placeholder">
      <p class="cost-placeholder-title">${_esc(title)}</p>
      <p class="cost-placeholder-desc">${_esc(desc)}</p>
    </div>`;
}

// ── Routing opportunities tab ──────────────────────────────────────────────────

async function _fetchRoutingOpportunities() {
  _state.routingLoading = true;
  _state.routingError = null;
  _renderCostShell();

  const params = new URLSearchParams();
  const wp = _windowParams(_state);
  if (wp.from) params.set('from', wp.from);
  if (wp.to) params.set('to', wp.to);

  try {
    const resp = await fetch(`/api/costs/routing-opportunities?${params}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    _state.routingData = await resp.json();
  } catch (err) {
    _state.routingError = err.message || 'Failed to load routing opportunities.';
  } finally {
    _state.routingLoading = false;
    _renderCostShell();
  }
}

function _buildRoutingTab() {
  if (_state.routingLoading) {
    return `<div class="cost-loading">Loading routing opportunities…</div>`;
  }
  if (_state.routingError) {
    return `<div class="cost-error">Error: ${_esc(_state.routingError)}</div>`;
  }
  if (!_state.routingData) {
    // Trigger fetch then show loader
    _fetchRoutingOpportunities();
    return `<div class="cost-loading">Loading routing opportunities…</div>`;
  }

  const { monthly_pace, opportunities } = _state.routingData;
  const availSavings = _fmtUsd(monthly_pace.projected_savings_usd_if_all_available_applied);
  const setupSavings = monthly_pace.current_total_usd - monthly_pace.projected_total_usd
    - monthly_pace.projected_savings_usd_if_all_available_applied;

  let summaryHtml = `
    <div class="cost-routing-summary">
      <p>If you applied the available recommendations below, you'd save approximately
      <strong>${_esc(availSavings)} / month</strong> at the current pace.</p>
      ${setupSavings > 1 ? `<p class="cost-routing-setup-note">${_esc(_fmtUsd(setupSavings))} / month more is available after provider setup.</p>` : ''}
    </div>`;

  if (!opportunities || opportunities.length === 0) {
    return summaryHtml + `<div class="cost-placeholder"><p class="cost-placeholder-desc">No routing opportunities found for this window. Try a wider date range or use the system more to generate data.</p></div>`;
  }

  const cards = opportunities.map((opp) => _buildOpportunityCard(opp)).join('');

  return `
    ${summaryHtml}
    <div class="cost-opportunity-list">${cards}</div>
    <p class="cost-routing-footer">
      Recommendations use your captured token volumes. Apply buttons change routing immediately — next call uses the new cascade.
      For full per-feature control, open the <a class="cost-routing-link" href="#" data-action="open-routing">Routing page</a>.
    </p>`;
}

function _buildOpportunityCard(opp) {
  const overrideBadge = opp.current_routing_is_override
    ? `<span class="cost-opp-badge">Using custom override &mdash; <a href="#" class="cost-routing-link" data-action="open-routing">manage in Routing &rarr;</a></span>`
    : '';

  const alts = opp.alternatives.map((alt) => {
    const isAvailable = alt.availability === 'available';
    const savingsStr = _fmtUsd(alt.savings_usd);
    const altCostStr = _fmtUsd(alt.monthly_pace_usd);
    const cascadeStr = (alt.apply_cascade || []).map((s) => s.model ? `${s.provider}/${s.model}` : s.provider).join(' → ');

    if (isAvailable) {
      return `
        <div class="cost-opp-alt">
          <div class="cost-opp-alt-header">
            <span class="cost-opp-alt-label">&rarr; ${_esc(alt.provider)} ${_esc(alt.model)}</span>
            <span class="cost-opp-alt-cost">${_esc(altCostStr)}/mo</span>
            <span class="cost-opp-alt-savings">Save ${_esc(savingsStr)}/mo</span>
          </div>
          <p class="cost-opp-tradeoff">&#9888; ${_esc(alt.tradeoff_note)}</p>
          <p class="cost-opp-cascade">Cascade: ${_esc(cascadeStr)}</p>
          <button class="cost-opp-apply-btn routing-btn routing-btn--primary routing-btn--small"
            data-apply-tag="${_esc(opp.feature_tag)}"
            data-apply-alt='${JSON.stringify(alt)}'>Apply this routing</button>
        </div>`;
    } else {
      return `
        <div class="cost-opp-alt cost-opp-alt--setup-required">
          <div class="cost-opp-alt-header">
            <span class="cost-opp-alt-label">&rarr; ${_esc(alt.provider)} ${_esc(alt.model)}</span>
            <span class="cost-opp-alt-cost">${_esc(altCostStr)}/mo</span>
            <span class="cost-opp-alt-savings cost-opp-savings--dim">Save ${_esc(savingsStr)}/mo (setup required)</span>
          </div>
          <p class="cost-opp-tradeoff">&#9888; ${_esc(alt.tradeoff_note)}</p>
          ${alt.setup_hint ? `<p class="cost-opp-setup-hint">${_esc(alt.setup_hint)} <a href="#" class="cost-routing-link" data-action="open-connectors">Open Connectors &rarr;</a></p>` : ''}
        </div>`;
    }
  }).join('');

  return `
    <div class="cost-opp-card">
      <div class="cost-opp-card-header">
        <strong class="cost-opp-feature">${_esc(opp.feature_tag.replace(/_/g, ' '))}</strong>
        <span class="cost-opp-current">Currently: ${_esc(opp.current.provider)} &middot; ${_esc(opp.current.model)} &middot; ${_esc(_fmtUsd(opp.current.monthly_pace_usd))}/mo</span>
        ${overrideBadge}
      </div>
      <div class="cost-opp-alts">${alts}</div>
    </div>`;
}

function _showApplyModal(featureTag, alt) {
  const featureLabel = featureTag.replace(/_/g, ' ');
  const cascadeStr = (alt.apply_cascade || []).map((s) => s.model ? `${s.provider}/${s.model}` : s.provider).join(' → ');

  const overlay = document.createElement('div');
  overlay.className = 'routing-modal-overlay';
  overlay.innerHTML = `
    <div class="routing-modal" role="dialog" aria-modal="true">
      <div class="routing-modal-header">
        <h3 class="routing-modal-title">Apply routing for ${_esc(featureLabel)}</h3>
        <button class="routing-modal-close" aria-label="Close">&times;</button>
      </div>
      <div class="routing-modal-body">
        <p>Switch <strong>${_esc(featureLabel)}</strong> to <strong>${_esc(alt.provider)}/${_esc(alt.model)}</strong>?</p>
        <p class="routing-modal-desc">Cascade: ${_esc(cascadeStr)}</p>
        <div class="routing-form-field" style="margin-top:14px">
          <label class="routing-label">Reason (required)</label>
          <input class="routing-input" id="cost-apply-reason" type="text"
            placeholder="Why are you changing this routing?" />
        </div>
      </div>
      <div class="routing-modal-footer">
        <button class="routing-btn routing-btn--secondary" data-modal-action="cancel">Cancel</button>
        <button class="routing-btn routing-btn--primary" data-modal-action="confirm">Apply</button>
      </div>
    </div>`;

  document.body.appendChild(overlay);

  const close = () => overlay.remove();
  overlay.querySelector('.routing-modal-close').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', function onEsc(e) {
    if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); }
  });
  overlay.querySelector('[data-modal-action="cancel"]').addEventListener('click', close);
  overlay.querySelector('[data-modal-action="confirm"]').addEventListener('click', async () => {
    const reason = document.getElementById('cost-apply-reason')?.value?.trim();
    if (!reason) {
      _showToast('Reason is required.', 'error');
      return;
    }
    try {
      const resp = await fetch(`/api/routing/features/${encodeURIComponent(featureTag)}/override`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cascade: alt.apply_cascade, reason }),
      });
      if (resp.ok) {
        close();
        _showToast('Routing updated. Next call uses the new cascade.', 'success');
        // Invalidate routing data so it refetches on next render
        _state.routingData = null;
        _renderCostShell();
      } else {
        const err = await resp.json().catch(() => ({}));
        _showToast(`Failed: ${err.error || resp.status}`, 'error');
      }
    } catch (err) {
      _showToast(`Error: ${err.message}`, 'error');
    }
  });
}

function _showToast(msg, kind = 'info') {
  const t = document.createElement('div');
  t.className = `routing-toast routing-toast--${kind}`;
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('routing-toast--visible'));
  setTimeout(() => {
    t.classList.remove('routing-toast--visible');
    setTimeout(() => t.remove(), 300);
  }, 3500);
}

// ── Spend tab ─────────────────────────────────────────────────────────────────

function _buildSpendTab(data) {
  return `
    ${_buildToolbar()}
    ${_buildCardGrid(data)}
    ${_buildBreakdowns(data)}
    ${_buildDailyChart(data)}
    ${_buildTopCalls(data)}
  `;
}

// Toolbar: time window + provider/model filter dropdowns + custom date range

function _buildToolbar() {
  const windowOpts = [
    ['today', 'Today'],
    ['this-week', 'This week'],
    ['this-month', 'This month'],
    ['last-30-days', 'Last 30 days'],
    ['custom', 'Custom'],
  ].map(([v, l]) =>
    `<option value="${v}"${_state.window === v ? ' selected' : ''}>${_esc(l)}</option>`
  ).join('');

  const customRange = _state.window === 'custom' ? `
    <div class="cost-toolbar-custom">
      <span>From</span>
      <input type="date" id="cost-custom-from" value="${_esc(_state.customFrom || '')}">
      <span>to</span>
      <input type="date" id="cost-custom-to" value="${_esc(_state.customTo || '')}">
    </div>` : '';

  return `
    <div class="cost-toolbar">
      <select id="cost-window-select">${windowOpts}</select>
      <select id="cost-provider-select">
        <option value="">All providers</option>
        <option value="anthropic"${_state.providerFilter === 'anthropic' ? ' selected' : ''}>Anthropic</option>
        <option value="openai"${_state.providerFilter === 'openai' ? ' selected' : ''}>OpenAI</option>
        <option value="gemini"${_state.providerFilter === 'gemini' ? ' selected' : ''}>Gemini</option>
        <option value="claude-code"${_state.providerFilter === 'claude-code' ? ' selected' : ''}>Claude Code CLI</option>
      </select>
      ${customRange}
    </div>`;
}

// Card grid: 4 cards
// UX GUARD: "This month" card must say "Subscription is flat — this is on-demand API equivalent."

function _buildCardGrid(data) {
  if (!data) {
    return `<div class="cost-card-grid">
      ${_card('Projected API cost this month', '—', '', 'Subscription is flat — this is on-demand API equivalent.')}
      ${_card('Today (projected API)', '—', '', '')}
      ${_card('Tokens', '—', '', '')}
      ${_card('Cache savings', '—', '', 'This period')}
    </div>`;
  }

  // Card 1: This month
  const thisCost = _fmtUsd(data.totals.cost_usd);
  const pct1 = _pct(data.totals.cost_usd, data.prior_totals.cost_usd);
  const delta1 = pct1 != null
    ? `vs last: ${_fmtUsd(data.prior_totals.cost_usd)} (${pct1 >= 0 ? '+' : ''}${pct1}%)`
    : '';
  const deltaClass1 = pct1 == null ? '' : pct1 > 0 ? 'up' : 'down';

  // Card 2: Today
  const todayCost = _fmtUsd(data.today.cost_usd);
  const pct2 = _pct(data.today.cost_usd, data.today.avg_daily_cost_usd);
  const delta2 = data.today.avg_daily_cost_usd
    ? `vs avg daily: ${_fmtUsd(data.today.avg_daily_cost_usd)} (${pct2 >= 0 ? '+' : ''}${pct2}%)`
    : '';
  const deltaClass2 = pct2 == null ? '' : pct2 > 0 ? 'up' : 'down';

  // Card 3: Tokens
  const inTok = _fmtTokens(data.totals.input_tokens);
  const outTok = _fmtTokens(data.totals.output_tokens);

  // Card 4: Cache savings
  const savings = _fmtUsd(data.totals.cache_savings_usd);

  return `<div class="cost-card-grid">
    ${_card('Projected API cost this month', thisCost, delta1, 'Subscription is flat — this is on-demand API equivalent.', deltaClass1)}
    ${_card('Today (projected API)', todayCost, delta2, '', deltaClass2)}
    ${_card('Tokens', `${_esc(inTok)} in`, '', `${_esc(outTok)} out`)}
    ${_card('Cache savings', savings, '', 'This period')}
  </div>`;
}

function _card(label, value, delta, subtext, deltaClass = '') {
  return `
    <div class="cost-card">
      <p class="cost-card-label">${_esc(label)}</p>
      <p class="cost-card-value">${_esc(value)}</p>
      ${delta ? `<p class="cost-card-delta ${_esc(deltaClass)}">${_esc(delta)}</p>` : ''}
      ${subtext ? `<p class="cost-card-subtext">${_esc(subtext)}</p>` : ''}
    </div>`;
}

// Two-column breakdown: Spend by source bucket + Spend by model

function _buildBreakdowns(data) {
  if (!data) return '<div class="cost-breakdowns"></div>';

  const featureRows = (data.by_feature || []).slice(0, 8).map((r) => `
    <div class="cost-breakdown-row" data-filter-feature="${_esc(r.feature_tag)}">
      <span class="cost-breakdown-label" title="${_esc(r.feature_tag)}">${_esc(r.feature_tag.replace(/_/g, ' '))}</span>
      <div class="cost-breakdown-bar-wrap">
        <div class="cost-breakdown-bar" style="width:${Math.round(r.share * 100)}%"></div>
      </div>
      <span class="cost-breakdown-amount">${_esc(_fmtUsd(r.cost_usd))}</span>
      <span class="cost-breakdown-share">${Math.round(r.share * 100)}%</span>
    </div>`).join('');

  const modelRows = (data.by_model || []).slice(0, 8).map((r) => `
    <div class="cost-breakdown-row" data-filter-model="${_esc(r.model)}">
      <span class="cost-breakdown-label" title="${_esc(r.provider + '/' + r.model)}">${_esc(r.model)}</span>
      <div class="cost-breakdown-bar-wrap">
        <div class="cost-breakdown-bar" style="width:${Math.round(r.share * 100)}%"></div>
      </div>
      <span class="cost-breakdown-amount">${_esc(_fmtUsd(r.cost_usd))}</span>
      <span class="cost-breakdown-share">${Math.round(r.share * 100)}%</span>
    </div>`).join('');

  return `
    <div class="cost-breakdowns">
      <div class="cost-breakdown">
        <p class="cost-breakdown-title">Spend by source bucket</p>
        ${featureRows || '<p style="font-size:12px;color:var(--color-text-secondary)">No data for this window.</p>'}
      </div>
      <div class="cost-breakdown">
        <p class="cost-breakdown-title">Spend by model</p>
        ${modelRows || '<p style="font-size:12px;color:var(--color-text-secondary)">No data for this window.</p>'}
      </div>
    </div>`;
}

// Daily chart — horizontal bar list, last 30 days

function _buildDailyChart(data) {
  if (!data || !data.daily || data.daily.length === 0) return '';

  const days = data.daily.slice(-30);
  const maxCost = days.reduce((m, d) => Math.max(m, d.cost_usd), 0.001);

  const bars = days.map((d) => {
    const pct = Math.max(1, Math.round((d.cost_usd / maxCost) * 100));
    const label = d.date.slice(5); // MM-DD
    return `
      <div class="cost-daily-row">
        <span class="cost-daily-label">${_esc(label)}</span>
        <div class="cost-daily-bar-wrap">
          <div class="cost-daily-bar" style="width:${pct}%" title="${_esc(d.date)}: ${_esc(_fmtUsd(d.cost_usd))}"></div>
        </div>
        <span class="cost-daily-amount">${_esc(_fmtUsd(d.cost_usd))}</span>
      </div>`;
  }).join('');

  return `
    <div class="cost-daily-section">
      <p class="cost-daily-title">Daily spend (API-equivalent)</p>
      <div class="cost-daily-chart">${bars}</div>
    </div>`;
}

// Top calls table — sortable client-side, max 20 rows (no pagination in Phase 2)

function _buildTopCalls(data) {
  if (!data || !data.top_calls || data.top_calls.length === 0) return '';

  const { sortCol, sortDir } = _state;
  const rows = [...data.top_calls].sort((a, b) => {
    const av = a[sortCol] ?? 0;
    const bv = b[sortCol] ?? 0;
    if (av < bv) return sortDir === 'asc' ? -1 : 1;
    if (av > bv) return sortDir === 'asc' ? 1 : -1;
    return 0;
  });

  const thSort = (col, label) => {
    const icon = sortCol === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';
    return `<th data-sort="${_esc(col)}">${_esc(label)}${icon}</th>`;
  };

  const tableRows = rows.map((r) => `
    <tr>
      <td>${_esc(r.feature_tag.replace(/_/g, ' '))}</td>
      <td>${_esc(r.model)}</td>
      <td>${_esc(_fmtTokens(r.input_tokens + r.output_tokens))}</td>
      <td>${_esc(_fmtUsd(r.cost_usd))}</td>
      <td>${_esc(_relativeTime(r.created_at))}</td>
    </tr>`).join('');

  return `
    <div class="cost-top-calls">
      <p class="cost-top-calls-title">Top calls this period</p>
      <table class="cost-table" id="cost-top-calls-table">
        <thead>
          <tr>
            ${thSort('feature_tag', 'Feature')}
            ${thSort('model', 'Model')}
            ${thSort('input_tokens', 'Tokens')}
            ${thSort('cost_usd', 'Cost')}
            <th>When</th>
          </tr>
        </thead>
        <tbody>${tableRows}</tbody>
      </table>
    </div>`;
}

// ── Event wiring ──────────────────────────────────────────────────────────────

function _wireEvents(el) {
  // Tab switching
  el.querySelectorAll('[data-cost-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const newTab = btn.dataset.costTab;
      _state.tab = newTab;
      // Trigger routing fetch when switching to routing tab for the first time
      if (newTab === 'routing' && !_state.routingData && !_state.routingLoading) {
        _fetchRoutingOpportunities();
      } else {
        _renderCostShell();
      }
    });
  });

  // Apply buttons in routing tab
  el.querySelectorAll('[data-apply-tag]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tag = btn.dataset.applyTag;
      let alt;
      try { alt = JSON.parse(btn.dataset.applyAlt); } catch { return; }
      _showApplyModal(tag, alt);
    });
  });

  // Routing / Connectors deep-links
  el.querySelectorAll('[data-action="open-routing"]').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      if (typeof window.__artemisSetState === 'function') {
        window.__artemisSetState('view', 'routing');
      }
    });
  });
  el.querySelectorAll('[data-action="open-connectors"]').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      if (typeof window.__artemisSetState === 'function') {
        window.__artemisSetState('view', 'integrations');
      }
    });
  });

  // Time window select
  const winSel = el.querySelector('#cost-window-select');
  if (winSel) {
    winSel.addEventListener('change', () => {
      _state.window = winSel.value;
      if (_state.window !== 'custom') {
        _state.customFrom = null;
        _state.customTo = null;
      }
      _fetchSummary();
    });
  }

  // Provider filter
  const provSel = el.querySelector('#cost-provider-select');
  if (provSel) {
    provSel.addEventListener('change', () => {
      _state.providerFilter = provSel.value || null;
      _fetchSummary();
    });
  }

  // Custom date inputs
  const fromIn = el.querySelector('#cost-custom-from');
  const toIn = el.querySelector('#cost-custom-to');
  if (fromIn) {
    fromIn.addEventListener('change', () => {
      _state.customFrom = fromIn.value || null;
      if (_state.customFrom && _state.customTo) _fetchSummary();
      else _renderCostShell();
    });
  }
  if (toIn) {
    toIn.addEventListener('change', () => {
      _state.customTo = toIn.value || null;
      if (_state.customFrom && _state.customTo) _fetchSummary();
      else _renderCostShell();
    });
  }

  // Breakdown row click → filter toolbar
  el.querySelectorAll('[data-filter-feature]').forEach((row) => {
    row.addEventListener('click', () => {
      const tag = row.dataset.filterFeature;
      // Toggle: click same tag to clear
      // (feature_tag filter not exposed in toolbar dropdown; just refetches)
      // No-op for now — Phase 3 can add a feature_tag filter control.
    });
  });

  // Top-calls table sort
  const tbl = el.querySelector('#cost-top-calls-table');
  if (tbl) {
    tbl.querySelectorAll('th[data-sort]').forEach((th) => {
      th.addEventListener('click', () => {
        const col = th.dataset.sort;
        if (_state.sortCol === col) {
          _state.sortDir = _state.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          _state.sortCol = col;
          _state.sortDir = 'desc';
        }
        _renderCostShell();
      });
    });
  }
}
