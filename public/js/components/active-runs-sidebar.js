import { getActiveRuns } from '../core/floating-artemis-api.js';
import { escapeHtml } from '../core/utils.js';

const CLOSE_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;

const MAX_RECENT = 10;

function formatTs(isoString) {
  if (!isoString) return '';
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function statusChipClass(status) {
  if (status === 'running') return 'fa-run-chip-running';
  if (status === 'error') return 'fa-run-chip-error';
  return 'fa-run-chip-done';
}

function runLabel(run) {
  const parts = [run.run_type, run.subject_id].filter(Boolean);
  return parts.join(' · ') || run.run_id;
}

function renderCard(run) {
  const label = escapeHtml(runLabel(run));
  const chipClass = statusChipClass(run.status);
  const chipLabel = escapeHtml(run.status ?? '');
  const ts = formatTs(run.started_at);
  return `
    <div class="fa-sidebar-item">
      <div class="fa-sidebar-item-hd">
        <span class="fa-sidebar-item-name">${label}</span>
        <span class="fa-run-chip ${chipClass}">${chipLabel}</span>
      </div>
      <div class="fa-sidebar-item-meta">
        <span>${escapeHtml(ts)}</span>
      </div>
    </div>`.trim();
}

function renderSection(title, runs, emptyText) {
  const count = runs.length;
  const cardsHtml = count
    ? runs.map(renderCard).join('')
    : `<div class="fa-sidebar-empty">${escapeHtml(emptyText)}</div>`;
  return `
    <div class="fa-sidebar-section">
      <div class="fa-sidebar-section-hd">
        <span>${escapeHtml(title)}</span>
        <span class="fa-sidebar-count">${count}</span>
      </div>
      <div class="fa-sidebar-section-body">${cardsHtml}</div>
    </div>`.trim();
}

export class ActiveRunsSidebar {
  constructor(containerEl) {
    this._el = containerEl;
    this._open = false;
    this._render();
  }

  _render() {
    this._el.innerHTML = `
      <div class="fa-sidebar-hd">
        <span class="fa-sidebar-title">Activity</span>
        <button class="fa-sidebar-close" aria-label="Close activity sidebar">${CLOSE_ICON}</button>
      </div>
      <div class="fa-sidebar-body"></div>`.trim();
    this._body = this._el.querySelector('.fa-sidebar-body');
    this._el.querySelector('.fa-sidebar-close').addEventListener('click', () => this.close());
  }

  async refresh() {
    let runs = [];
    try {
      const data = await getActiveRuns();
      runs = data?.runs ?? [];
    } catch (err) {
      console.warn('[ActiveRunsSidebar] refresh failed', err);
    }

    const live = runs.filter((r) => r.status === 'running');
    const recent = runs
      .filter((r) => r.status !== 'running')
      .sort((a, b) => {
        const ta = a.completed_at ?? a.started_at ?? '';
        const tb = b.completed_at ?? b.started_at ?? '';
        return tb.localeCompare(ta);
      })
      .slice(0, MAX_RECENT);

    this._body.innerHTML =
      renderSection('Live', live, 'No active runs') +
      renderSection('Recent', recent, 'No recent runs');
  }

  open() {
    if (this._open) return;
    this._open = true;
    this._el.classList.add('fa-sidebar--open');
    this.refresh();
  }

  close() {
    if (!this._open) return;
    this._open = false;
    this._el.classList.remove('fa-sidebar--open');
  }

  toggle() {
    this._open ? this.close() : this.open();
  }

  isOpen() {
    return this._open;
  }
}
