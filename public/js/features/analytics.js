// Analytics dashboard — renders inside the account popup
import { $ } from '../core/dom.js';
import { escapeHtml } from '../core/utils.js';
import { getState } from '../core/store.js';
import * as api from '../core/api.js';

function formatCost(n) {
  if (n >= 100) return '$' + n.toFixed(0);
  if (n >= 1) return '$' + n.toFixed(2);
  return '$' + n.toFixed(4);
}

function formatNumber(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}

function pct(value, max) {
  return max > 0 ? Math.round((value / max) * 100) : 0;
}

function normalizeProviderId(providerId) {
  const value = String(providerId ?? "").trim().toLowerCase();
  if (!value || value === "null" || value === "<null>" || value === "undefined") return "claude-code";
  if (value === "claude" || value === "claude_code") return "claude-code";
  if (value === "ollama") return "local";
  if (value === "openai-codex" || value === "codex-cli") return "codex";
  return value;
}

function formatProviderLabel(providerId) {
  const normalized = normalizeProviderId(providerId);
  return {
    "claude-code": "Claude Code",
    codex: "Codex",
    local: "Local",
    hermes: "Hermes",
  }[normalized] || "Unknown";
}

function providerClassName(providerId) {
  return `analytics-provider-pill analytics-provider-pill-${normalizeProviderId(providerId).replace(/[^a-z0-9_-]/gi, "-")}`;
}

function buildProviderFailureMap(items) {
  const map = new Map();
  for (const item of items || []) {
    map.set(normalizeProviderId(item.provider), Number(item.failures || 0));
  }
  return map;
}

function renderBarChart(container, items, labelKey, valueKey, formatFn = String) {
  container.innerHTML = '';
  if (!items || items.length === 0) {
    container.innerHTML = '<div class="analytics-empty">No data</div>';
    return;
  }
  const maxVal = Math.max(...items.map(d => d[valueKey]), 0.001);
  for (const item of items) {
    const p = pct(item[valueKey], maxVal);
    const row = document.createElement('div');
    row.className = 'cost-chart-row';
    row.innerHTML = `
      <span class="cost-chart-label">${escapeHtml(String(item[labelKey]))}</span>
      <div class="cost-chart-bar-bg"><div class="cost-chart-bar" style="width:${p}%"></div></div>
      <span class="cost-chart-value">${formatFn(item[valueKey])}</span>
    `;
    container.appendChild(row);
  }
}

function sortTable(tbody, colIdx, numeric) {
  const current = tbody.dataset.sortCol === String(colIdx) && tbody.dataset.sortDir === 'desc' ? 'asc' : 'desc';
  tbody.dataset.sortCol = colIdx;
  tbody.dataset.sortDir = current;
  const rows = [...tbody.querySelectorAll('tr')];
  rows.sort((a, b) => {
    const aVal = a.children[colIdx].textContent;
    const bVal = b.children[colIdx].textContent;
    if (!numeric) return current === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    const aNum = parseFloat(aVal.replace(/[$,%km]/g, '')) || 0;
    const bNum = parseFloat(bVal.replace(/[$,%km]/g, '')) || 0;
    return current === 'asc' ? aNum - bNum : bNum - aNum;
  });
  tbody.innerHTML = '';
  rows.forEach(r => tbody.appendChild(r));
}

function renderTable(container, columns, rows) {
  if (!rows || rows.length === 0) {
    container.innerHTML += '<div class="analytics-empty">No data</div>';
    return;
  }
  const table = document.createElement('table');
  table.className = 'cost-table';

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  columns.forEach((col, idx) => {
    const th = document.createElement('th');
    th.textContent = col.label;
    th.onclick = () => sortTable(tbody, idx, col.numeric);
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    columns.forEach(col => {
      const td = document.createElement('td');
      const val = row[col.key];
      td.textContent = col.format ? col.format(val) : (val ?? '');
      if (col.title && row[col.title]) td.title = row[col.title];
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);

  const wrap = document.createElement('div');
  wrap.className = 'cost-table-container';
  wrap.appendChild(table);
  container.appendChild(wrap);
}

function section(title, helperText = "") {
  const div = document.createElement('div');
  div.className = 'analytics-section';
  const h4 = document.createElement('h4');
  h4.textContent = title;
  div.appendChild(h4);
  if (helperText) {
    const helper = document.createElement('p');
    helper.className = 'analytics-section-helper';
    helper.textContent = helperText;
    div.appendChild(helper);
  }
  return div;
}

function renderProviderBreakdown(container, items, failureItems = []) {
  if (!items || items.length === 0) {
    container.innerHTML += '<div class="analytics-empty">No data</div>';
    return;
  }

  const rail = document.createElement('div');
  rail.className = 'analytics-provider-rail';
  const maxQueries = Math.max(...items.map(item => item.queries), 1);
  const providerFailures = buildProviderFailureMap(failureItems);

  for (const item of items) {
    const card = document.createElement('div');
    card.className = 'analytics-provider-card';
    const failedQueries = Number(item.failedQueries || 0);
    const failureRate = item.queries > 0 ? ((failedQueries / item.queries) * 100).toFixed(1) : '0.0';
    const launchFailures = providerFailures.get(normalizeProviderId(item.provider)) || 0;
    card.innerHTML = `
      <div class="analytics-provider-card-header">
        <span class="${providerClassName(item.provider)}">${escapeHtml(formatProviderLabel(item.provider))}</span>
        <span class="analytics-provider-card-cost">${formatCost(item.totalCost || 0)}</span>
      </div>
      <div class="analytics-provider-card-bar">
        <div class="analytics-provider-card-bar-fill" style="width:${pct(item.queries, maxQueries)}%"></div>
      </div>
      <div class="analytics-provider-card-stats">
        <span>${formatNumber(item.sessions)} sessions</span>
        <span>${formatNumber(item.queries)} queries</span>
        <span>${formatNumber(item.tokens || 0)} tokens</span>
        <span>${formatNumber(failedQueries)} failed (${failureRate}%)</span>
        <span>${formatNumber(launchFailures)} launch/auth failures</span>
      </div>
    `;
    rail.appendChild(card);
  }

  container.appendChild(rail);
}

function renderProviderFailureList(container, items) {
  if (!items || items.length === 0) {
    container.innerHTML += '<div class="analytics-empty">No recent provider failures</div>';
    return;
  }

  const list = document.createElement('div');
  list.className = 'analytics-error-list';
  for (const failure of items) {
    const item = document.createElement('div');
    item.className = 'analytics-error-item analytics-provider-failure-item';
    const ts = new Date(failure.timestamp * 1000);
    const timeStr = ts.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + ts.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    item.innerHTML = `
      <div class="analytics-error-item-header">
        <span class="${providerClassName(failure.provider_id)}">${escapeHtml(formatProviderLabel(failure.provider_id))}</span>
        <span class="analytics-error-tool">${escapeHtml(failure.stage || 'chat')}</span>
        <span class="analytics-provider-failure-code">${escapeHtml(failure.code || 'provider_error')}</span>
        <span class="analytics-error-time">${timeStr}</span>
      </div>
      <div class="analytics-error-preview">${escapeHtml(failure.body || '')}</div>
    `;
    list.appendChild(item);
  }
  container.appendChild(list);
}

function renderAnalytics(data, el) {
  el.innerHTML = '';

  // 1. Overview cards
  const o = data.overview;
  const cardsDiv = document.createElement('div');
  cardsDiv.className = 'analytics-cards';
  cardsDiv.innerHTML = `
    <div class="cost-card">
      <div class="cost-card-label">Total Cost</div>
      <div class="cost-card-value">${formatCost(o.totalCost)}</div>
    </div>
    <div class="cost-card">
      <div class="cost-card-label">Sessions</div>
      <div class="cost-card-value">${formatNumber(o.sessions)}</div>
    </div>
    <div class="cost-card">
      <div class="cost-card-label">Queries</div>
      <div class="cost-card-value">${formatNumber(o.queries)}</div>
    </div>
    <div class="cost-card">
      <div class="cost-card-label">Turns</div>
      <div class="cost-card-value">${formatNumber(o.totalTurns)}</div>
    </div>
    <div class="cost-card">
      <div class="cost-card-label">Output Tokens</div>
      <div class="cost-card-value">${formatNumber(o.totalOutputTokens)}</div>
    </div>
    <div class="cost-card">
      <div class="cost-card-label">Errors</div>
      <div class="cost-card-value${o.errorRate > 5 ? ' analytics-error-value' : ''}">${data.errorCategories ? formatNumber(data.errorCategories.reduce((s, c) => s + c.count, 0)) : '0'} <span style="font-size:10px;color:var(--text-dim)">(${o.errorRate.toFixed(1)}%)</span></div>
      ${data.errorCategories && data.errorCategories.length > 0 ? `<div style="font-size:9px;color:var(--text-dim);margin-top:2px">Top: ${escapeHtml(data.errorCategories[0].category)}</div>` : ''}
    </div>
  `;
  el.appendChild(cardsDiv);

  // 2. Daily Activity
  const dailySec = section('Daily Activity (Last 30 Days)');
  const dailyChart = document.createElement('div');
  dailyChart.className = 'cost-chart';
  renderBarChart(dailyChart, data.dailyBreakdown, 'date', 'cost', v => formatCost(v));
  dailyChart.querySelectorAll('.cost-chart-label').forEach(lbl => {
    lbl.textContent = lbl.textContent.slice(5);
  });
  dailySec.appendChild(dailyChart);
  el.appendChild(dailySec);

  // 3. Hourly Activity
  const hourlySec = section('Activity by Hour');
  const hourlyChart = document.createElement('div');
  hourlyChart.className = 'cost-chart';
  const hourMap = new Map(data.hourlyActivity.map(h => [h.hour, h]));
  const allHours = [];
  for (let h = 0; h < 24; h++) {
    allHours.push(hourMap.get(h) || { hour: h, queries: 0, cost: 0 });
  }
  renderBarChart(hourlyChart, allHours, 'hour', 'queries', v => String(v));
  hourlyChart.querySelectorAll('.cost-chart-label').forEach(lbl => {
    const h = parseInt(lbl.textContent);
    lbl.textContent = `${h.toString().padStart(2, '0')}:00`;
  });
  hourlySec.appendChild(hourlyChart);
  el.appendChild(hourlySec);

  // 4. Project Breakdown
  if (data.providerBreakdown && data.providerBreakdown.length > 0) {
    const providerSec = section(
      'Provider Breakdown',
      'Shows which provider is doing the work across sessions, queries, tokens, and cost.'
    );
    renderProviderBreakdown(providerSec, data.providerBreakdown, data.providerFailures);
    el.appendChild(providerSec);
  }

  if (data.recentProviderFailures && data.recentProviderFailures.length > 0) {
    const providerFailSec = section(
      'Provider Failures',
      'Tracks launch or sign-in problems that stop a provider before a normal reply is created.'
    );
    renderProviderFailureList(providerFailSec, data.recentProviderFailures);
    el.appendChild(providerFailSec);
  }

  // 4b. Project Breakdown
  if (data.projectBreakdown.length > 1) {
    const projSec = section('Project Breakdown');
    renderTable(projSec, [
      { label: 'Project', key: 'name' },
      { label: 'Sessions', key: 'sessions', numeric: true },
      { label: 'Queries', key: 'queries', numeric: true },
      { label: 'Cost', key: 'totalCost', numeric: true, format: formatCost },
      { label: 'Avg Cost', key: 'avgCost', numeric: true, format: formatCost },
      { label: 'Avg Turns', key: 'avgTurns', numeric: true, format: v => String(Math.round(v)) },
    ], data.projectBreakdown);
    el.appendChild(projSec);
  }

  // 5. Tool Usage
  if (data.toolUsage.length > 0) {
    const toolSec = section('Tool Usage');
    const toolChart = document.createElement('div');
    toolChart.className = 'cost-chart';
    const errorMap = new Map((data.toolErrors || []).map(e => [e.name, e]));
    const maxCount = Math.max(...data.toolUsage.map(t => t.count), 1);
    for (const tool of data.toolUsage) {
      const p = pct(tool.count, maxCount);
      const row = document.createElement('div');
      row.className = 'cost-chart-row';
      const err = errorMap.get(tool.name);
      const errHtml = err
        ? ` <span class="analytics-error-badge">${err.errors} err (${err.errorRate.toFixed(0)}%)</span>`
        : '';
      row.innerHTML = `
        <span class="cost-chart-label">${escapeHtml(tool.name)}</span>
        <div class="cost-chart-bar-bg"><div class="cost-chart-bar" style="width:${p}%"></div></div>
        <span class="cost-chart-value">${tool.count}${errHtml}</span>
      `;
      toolChart.appendChild(row);
    }
    toolSec.appendChild(toolChart);
    el.appendChild(toolSec);
  }

  // 5b. Error Categories
  if (data.errorCategories && data.errorCategories.length > 0) {
    const errCatSec = section(
      'Error Categories',
      'Groups recent failures by type so repeated problem patterns are easier to spot.'
    );
    const errCatChart = document.createElement('div');
    errCatChart.className = 'cost-chart analytics-error-bar';
    const totalErrors = data.errorCategories.reduce((s, c) => s + c.count, 0);
    const maxCat = Math.max(...data.errorCategories.map(c => c.count), 1);
    for (const cat of data.errorCategories) {
      const p = pct(cat.count, maxCat);
      const catPct = totalErrors > 0 ? (cat.count / totalErrors * 100).toFixed(1) : '0';
      const row = document.createElement('div');
      row.className = 'cost-chart-row';
      row.innerHTML = `
        <span class="cost-chart-label analytics-error-category-label">${escapeHtml(cat.category)}</span>
        <div class="cost-chart-bar-bg"><div class="cost-chart-bar" style="width:${p}%"></div></div>
        <span class="cost-chart-value">${cat.count} (${catPct}%)</span>
      `;
      errCatChart.appendChild(row);
    }
    errCatSec.appendChild(errCatChart);
    el.appendChild(errCatSec);
  }

  // 5c. Error Timeline
  if (data.errorTimeline && data.errorTimeline.length > 0) {
    const errTimeSec = section('Error Timeline (Last 30 Days)');
    const errTimeChart = document.createElement('div');
    errTimeChart.className = 'cost-chart analytics-error-bar';
    renderBarChart(errTimeChart, data.errorTimeline, 'date', 'errors', String);
    errTimeChart.querySelectorAll('.cost-chart-label').forEach(lbl => {
      lbl.textContent = lbl.textContent.slice(5); // MM-DD
    });
    errTimeSec.appendChild(errTimeChart);
    el.appendChild(errTimeSec);
  }

  // 5d. Top Failing Tools
  if (data.errorsByTool && data.errorsByTool.length > 0) {
    const errToolSec = section('Top Failing Tools');
    const errToolChart = document.createElement('div');
    errToolChart.className = 'cost-chart analytics-error-bar';
    const toolMap = new Map();
    for (const row of data.errorsByTool) {
      if (!toolMap.has(row.tool)) toolMap.set(row.tool, { tool: row.tool, errors: 0, categories: [] });
      const entry = toolMap.get(row.tool);
      entry.errors += row.errors;
      if (entry.categories.length < 2) entry.categories.push(row.category);
    }
    const toolList = [...toolMap.values()].sort((a, b) => b.errors - a.errors);
    const maxToolErr = Math.max(...toolList.map(t => t.errors), 1);
    for (const tool of toolList) {
      const p = pct(tool.errors, maxToolErr);
      const badges = tool.categories.map(c => `<span class="analytics-error-category-badge">${escapeHtml(c)}</span>`).join('');
      const row = document.createElement('div');
      row.className = 'cost-chart-row';
      row.innerHTML = `
        <span class="cost-chart-label analytics-error-category-label">${escapeHtml(tool.tool)}</span>
        <div class="cost-chart-bar-bg"><div class="cost-chart-bar" style="width:${p}%"></div></div>
        <span class="cost-chart-value">${tool.errors}${badges}</span>
      `;
      errToolChart.appendChild(row);
    }
    errToolSec.appendChild(errToolChart);
    el.appendChild(errToolSec);
  }

  // 5e. Recent Errors
  if (data.recentErrors && data.recentErrors.length > 0) {
    const errRecSec = section('Recent Errors');
    const errList = document.createElement('div');
    errList.className = 'analytics-error-list';
    for (const err of data.recentErrors) {
      const item = document.createElement('div');
      item.className = 'analytics-error-item';
      const ts = new Date(err.timestamp * 1000);
      const timeStr = ts.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + ts.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
      item.innerHTML = `
        <div class="analytics-error-item-header">
          <span class="${providerClassName(err.provider_id)}">${escapeHtml(formatProviderLabel(err.provider_id))}</span>
          <span class="analytics-error-tool">${escapeHtml(err.tool)}</span>
          ${err.session_title ? `<span style="font-size:10px;color:var(--text-dim)">${escapeHtml(err.session_title)}</span>` : ''}
          <span class="analytics-error-time">${timeStr}</span>
        </div>
        <div class="analytics-error-preview">${escapeHtml(err.preview || '')}</div>
      `;
      item.addEventListener('click', () => {
        const existing = item.querySelector('.analytics-error-full');
        if (existing) { existing.remove(); return; }
        const full = document.createElement('div');
        full.className = 'analytics-error-full';
        full.textContent = err.full_content || err.preview || '';
        item.appendChild(full);
      });
      errList.appendChild(item);
    }
    errRecSec.appendChild(errList);
    el.appendChild(errRecSec);
  }

  // 6. Top Sessions
  if (data.topSessions.length > 0) {
    const sesSec = section('Top Sessions by Cost');
    renderTable(sesSec, [
      { label: 'Session', key: 'title', format: v => v || 'Untitled' },
      { label: 'Project', key: 'project' },
      { label: 'Provider', key: 'provider_id', format: formatProviderLabel },
      { label: 'Cost', key: 'cost', numeric: true, format: formatCost },
      { label: 'Turns', key: 'turns', numeric: true },
      { label: 'Duration', key: 'duration_min', numeric: true, format: v => v > 0 ? Math.round(v) + 'm' : '-' },
    ], data.topSessions);
    el.appendChild(sesSec);
  }

  // 7. Session Depth
  if (data.sessionDepth.length > 0) {
    const depthSec = section('Session Depth');
    const depthChart = document.createElement('div');
    depthChart.className = 'cost-chart';
    renderBarChart(depthChart, data.sessionDepth, 'bucket', 'count', String);
    depthSec.appendChild(depthChart);
    el.appendChild(depthSec);
  }

  // 8. Message Length Distribution
  if (data.msgLength.length > 0) {
    const msgSec = section('Message Length Distribution');
    const msgChart = document.createElement('div');
    msgChart.className = 'cost-chart';
    renderBarChart(msgChart, data.msgLength, 'bucket', 'count', String);
    msgSec.appendChild(msgChart);
    el.appendChild(msgSec);
  }

  // 9. Top Bash Commands
  if (data.topBashCommands.length > 0) {
    const bashSec = section('Top Bash Commands');
    renderTable(bashSec, [
      { label: 'Command', key: 'command' },
      { label: 'Count', key: 'count', numeric: true },
    ], data.topBashCommands);
    el.appendChild(bashSec);
  }

  // 10. Top Files
  if (data.topFiles.length > 0) {
    const filesSec = section('Top Files');
    renderTable(filesSec, [
      { label: 'Path', key: 'path', format: v => v ? v.split('/').slice(-2).join('/') : '', title: 'path' },
      { label: 'Tool', key: 'tool' },
      { label: 'Count', key: 'count', numeric: true },
    ], data.topFiles);
    el.appendChild(filesSec);
  }
}

// ── Account popup analytics ────────────────────────────
const sidebarAnalyticsTrigger = document.getElementById('sidebar-user-pill-btn');
const analyticsFilter = document.getElementById('sidebar-analytics-filter') || document.getElementById('home-analytics-filter');
const analyticsContent = document.getElementById('sidebar-analytics-content') || document.getElementById('home-analytics-content');

async function loadAnalyticsData(projectPath) {
  if (!analyticsContent) return;
  analyticsContent.innerHTML = '<div class="analytics-loading">Loading analytics...</div>';
  try {
    const data = await api.fetchAnalytics(projectPath || undefined);
    renderAnalytics(data, analyticsContent);
  } catch (err) {
    analyticsContent.innerHTML = `<div class="analytics-empty">Failed to load analytics: ${escapeHtml(err.message)}</div>`;
  }
}

async function populateAnalyticsFilter() {
  if (!analyticsFilter) return;
  let projects = getState('projectsData');
  if (!projects || projects.length === 0) {
    try { projects = await api.fetchProjects(); } catch { projects = []; }
  }
  const selectedValue = analyticsFilter.value;
  analyticsFilter.innerHTML = '<option value="">All Projects</option>';
  for (const p of projects) {
    const opt = document.createElement('option');
    opt.value = p.path;
    opt.textContent = p.name;
    analyticsFilter.appendChild(opt);
  }
  if (selectedValue && [...analyticsFilter.options].some((option) => option.value === selectedValue)) {
    analyticsFilter.value = selectedValue;
  }
}

export async function loadSidebarAnalytics() {
  if (!analyticsFilter || !analyticsContent) return;
  await populateAnalyticsFilter();
  await loadAnalyticsData(analyticsFilter.value);
}

export const loadHomeAnalytics = loadSidebarAnalytics;

analyticsFilter?.addEventListener('change', () => {
  loadAnalyticsData(analyticsFilter.value);
});

sidebarAnalyticsTrigger?.addEventListener('click', () => {
  loadSidebarAnalytics();
});
