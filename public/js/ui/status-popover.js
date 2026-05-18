import { $ } from '../core/dom.js';
import * as api from '../core/api.js';
import { openIntegrationsModal } from '../components/integrations-modal.js';

function timeAgo(timestamp) {
  const diff = Math.floor((Date.now() - new Date(timestamp).getTime()) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const READY_CLASS = 'status-orb-ready';
const DEGRADED_CLASS = 'status-orb-degraded';
const ATTENTION_CLASS = 'status-orb-attention';

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function buildStatusModel(providerStatuses = [], alerts = {}, integrations = []) {
  // /api/stats/providers returns: [{provider_id, name, configured, healthy}]
  const providerList = Array.isArray(providerStatuses) ? providerStatuses : [];
  const providerRows = providerList.map((p) => ({
    name: p.name || p.provider_id || 'Unknown',
    label: p.configured ? 'Ready' : 'No key',
    tone: p.configured ? 'ready' : 'warn',
  }));
  const providerIssues = providerRows.filter((row) => row.tone !== 'ready').length;

  // /api/integrations returns active provider rows: [{provider, status, display_name, ...}]
  const integrationList = Array.isArray(integrations) ? integrations : [];
  const connectorRows = integrationList.map((row) => ({
    name: row.display_name || row.provider,
    label: row.status === 'active' ? 'Connected' : (row.status || 'Unknown'),
    tone: row.status === 'active' ? 'ready' : 'warn',
  }));

  const recentFailures = (alerts.providerFailures || []).slice(0, 3);
  const recentErrors = (alerts.runtimeErrors || []).slice(0, 3);
  const alertCount = recentFailures.length + recentErrors.length;
  const issueCount = providerIssues + (alertCount > 0 ? 1 : 0);

  let summary = 'All systems ready';
  let subtitle = 'Providers and integrations are healthy.';
  if (providerRows.length === 0) {
    summary = 'Provider check pending';
    subtitle = 'Waiting for provider status to load.';
  } else if (issueCount === 1) {
    summary = '1 issue needs attention';
    subtitle = 'One surface needs a quick check before Artemis keeps going.';
  } else if (issueCount > 1) {
    summary = `${issueCount} services degraded`;
    subtitle = 'A few surfaces need attention, but the shell is still usable.';
  }

  return {
    summary,
    subtitle,
    issueCount,
    providerRows,
    connectorRows,
    recentFailures,
    recentErrors,
  };
}

function renderAlertsGroup(recentFailures, recentErrors) {
  const allAlerts = [
    ...recentFailures.map((f) => ({
      tone: 'alert',
      title: f.title || `${escapeHtml(f.provider_id)} failure`,
      time: f.timestamp,
    })),
    ...recentErrors.map((e) => ({
      tone: 'warn',
      title: `${escapeHtml(e.tool)} error`,
      time: e.timestamp,
    })),
  ].sort((a, b) => new Date(b.time) - new Date(a.time)).slice(0, 5);

  if (!allAlerts.length) return '';

  return `
    <div class="status-pop-group">
      <div class="status-pop-group-label">Recent errors</div>
      ${allAlerts.map((a) => `
        <div class="status-pop-row">
          <span class="status-pop-row-dot ${escapeHtml(a.tone)}"></span>
          <span class="status-pop-row-name">${escapeHtml(a.title)}</span>
          <span class="status-pop-row-status">${escapeHtml(timeAgo(a.time))}</span>
        </div>
      `).join('')}
    </div>
  `;
}

function renderPopover(model) {
  if (!$.statusPopover) return;

  const alertsGroup = renderAlertsGroup(model.recentFailures, model.recentErrors);

  $.statusPopover.innerHTML = `
    <div class="status-pop-head">
      <div class="status-pop-head-dot"></div>
      <div>
        <div class="status-pop-title">${escapeHtml(model.summary)}</div>
        <div class="status-pop-subtitle">${escapeHtml(model.subtitle)}</div>
      </div>
    </div>
    <div class="status-pop-body">
      <div class="status-pop-group">
        <div class="status-pop-group-label">Providers</div>
        ${model.providerRows.map((row) => `
          <div class="status-pop-row">
            <span class="status-pop-row-dot ${escapeHtml(row.tone)}"></span>
            <span class="status-pop-row-name">${escapeHtml(row.name)}</span>
            <span class="status-pop-row-status ${escapeHtml(row.tone)}">${escapeHtml(row.label)}</span>
          </div>
        `).join('')}
      </div>
      <div class="status-pop-group">
        <div class="status-pop-group-label">Integrations</div>
        ${model.connectorRows.length === 0
          ? `<div class="status-pop-row">
              <span class="status-pop-row-dot warn"></span>
              <span class="status-pop-row-name">None connected</span>
              <button class="status-pop-row-action status-pop-open-integrations" type="button">Open Integrations</button>
            </div>`
          : model.connectorRows.map((row) => `
              <div class="status-pop-row">
                <span class="status-pop-row-dot ${escapeHtml(row.tone)}"></span>
                <span class="status-pop-row-name">${escapeHtml(row.name)}</span>
                <span class="status-pop-row-status ${escapeHtml(row.tone)}">${escapeHtml(row.label)}</span>
              </div>
            `).join('')}
      </div>
      ${alertsGroup}
    </div>
    <div class="status-pop-foot">
      <span class="status-pop-foot-hint">Updated just now</span>
    </div>
  `;

  // Wire the "Open Integrations" button (rendered only when no connectors are active)
  const openIntegBtn = $.statusPopover.querySelector('.status-pop-open-integrations');
  if (openIntegBtn) {
    openIntegBtn.addEventListener('click', () => {
      closeStatusPopover();
      openIntegrationsModal();
    });
  }
}

function applyOrbState(summary, issueCount) {
  if (!$.statusOrbBtn) return;
  $.statusOrbBtn.classList.remove(READY_CLASS, DEGRADED_CLASS, ATTENTION_CLASS);
  if (issueCount <= 0) {
    $.statusOrbBtn.classList.add(READY_CLASS);
  } else if (issueCount === 1) {
    $.statusOrbBtn.classList.add(ATTENTION_CLASS);
  } else {
    $.statusOrbBtn.classList.add(DEGRADED_CLASS);
  }
  $.statusOrbBtn.setAttribute('title', summary);
  $.statusOrbBtn.setAttribute('aria-label', `System status: ${summary}`);
}

async function refreshStatusPopover({ open = false } = {}) {
  if (!$.statusPopover || !$.statusOrbBtn) return;

  let providerStatuses = [];
  let alerts = {};
  let integrations = [];
  try {
    [providerStatuses, alerts, integrations] = await Promise.all([
      api.fetchProviderStatuses().catch(() => []),
      api.fetchSystemAlerts().catch(() => ({})),
      fetch('/api/integrations').then((r) => (r.ok ? r.json() : [])).catch(() => []),
    ]);
  } catch {
    /* leave defaults */
  }

  const model = buildStatusModel(providerStatuses, alerts, integrations);
  renderPopover(model);
  applyOrbState(model.summary, model.issueCount);

  if (open) {
    $.statusPopover.classList.remove('hidden');
    $.statusOrbBtn.setAttribute('aria-expanded', 'true');
    positionStatusPopover();
  }
}

function positionStatusPopover() {
  if (!$.statusPopover || !$.statusOrbBtn) return;
  // Anchor under the orb button, aligned toward the rail so the popover stays
  // inside the viewport. Fixed positioning means we set inline top/left.
  const rect = $.statusOrbBtn.getBoundingClientRect();
  const popWidth = 320;
  const gap = 10;
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - popWidth - 12));
  const top = rect.bottom + gap;
  $.statusPopover.style.top = `${Math.round(top)}px`;
  $.statusPopover.style.left = `${Math.round(left)}px`;
}

function closeStatusPopover() {
  if (!$.statusPopover || !$.statusOrbBtn) return;
  $.statusPopover.classList.add('hidden');
  $.statusOrbBtn.setAttribute('aria-expanded', 'false');
}

function toggleStatusPopover() {
  if (!$.statusPopover || !$.statusOrbBtn) return;
  const willOpen = $.statusPopover.classList.contains('hidden');
  if (willOpen) {
    refreshStatusPopover({ open: true });
  } else {
    closeStatusPopover();
  }
}

function initStatusPopover() {
  if (!$.statusOrbBtn || !$.statusPopover) return;

  $.statusOrbBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleStatusPopover();
  });

  $.statusPopover.addEventListener('click', (event) => {
    event.stopPropagation();
  });

  document.addEventListener('click', (event) => {
    if (!$.statusPopover?.contains(event.target) && !$.statusOrbBtn?.contains(event.target)) {
      closeStatusPopover();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeStatusPopover();
  });

  $.projectSelect?.addEventListener('change', () => {
    refreshStatusPopover({ open: !$.statusPopover.classList.contains('hidden') });
  });

  refreshStatusPopover();
}

initStatusPopover();
