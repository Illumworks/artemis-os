// Notification history modal — full paginated view with filters
import { on } from '../core/events.js';

const TYPE_ICONS = {
  session: buildIcon('message'),
  agent: buildIcon('agent'),
  workflow: buildIcon('workflow'),
  chain: buildIcon('chain'),
  dag: buildIcon('dag'),
  error: buildIcon('warning'),
  approval: buildIcon('approval'),
  provider_failure: buildIcon('warning'),
  default: buildIcon('notification'),
};

let overlay = null;
let items = [];
let offset = 0;
let hasMore = true;
let filterType = '';
let filterStatus = '';
let selectedIds = new Set();

function init() {
  on('notification:show-history', openModal);
}

function openModal(options = {}) {
  if (overlay) return;
  items = [];
  offset = 0;
  hasMore = true;
  filterType = typeof options?.type === 'string' ? options.type : '';
  filterStatus = typeof options?.status === 'string' ? options.status : '';
  selectedIds.clear();

  overlay = document.createElement('div');
  overlay.className = 'notif-history-overlay';
  overlay.innerHTML = `
    <div class="notif-history-modal">
      <div class="notif-history-header">
        <h2>Notification History</h2>
        <button class="notif-history-close">&times;</button>
      </div>
      <div class="notif-history-filters">
        <select class="notif-filter-select" id="notif-filter-type">
          <option value="">All Types</option>
          <option value="agent">Agent</option>
          <option value="error">Error</option>
          <option value="provider_failure">Provider Failure</option>
          <option value="workflow">Workflow</option>
          <option value="chain">Chain</option>
          <option value="dag">DAG</option>
          <option value="session">Session</option>
          <option value="approval">Approval</option>
        </select>
        <select class="notif-filter-select" id="notif-filter-status">
          <option value="">All</option>
          <option value="unread">Unread Only</option>
          <option value="read">Read Only</option>
        </select>
        <div class="notif-bulk-actions">
          <button class="notif-bulk-btn" id="notif-bulk-read">Mark Selected Read</button>
          <button class="notif-bulk-btn danger" id="notif-bulk-purge">Purge Old</button>
        </div>
      </div>
      <div class="notif-history-list" id="notif-history-list"></div>
      <div class="notif-load-more" id="notif-load-more" style="display:none">
        <button class="notif-load-more-btn">Load More</button>
      </div>
    </div>`;

  document.body.appendChild(overlay);

  const typeSelect = overlay.querySelector('#notif-filter-type');
  if (typeSelect) {
    typeSelect.value = filterType;
  }
  const statusSelect = overlay.querySelector('#notif-filter-status');
  if (statusSelect) {
    statusSelect.value = filterStatus;
  }

  // Wire events
  overlay.querySelector('.notif-history-close')?.addEventListener('click', closeModal);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });
  overlay.querySelector('#notif-filter-type')?.addEventListener('change', (e) => { filterType = e.target.value; resetAndFetch(); });
  overlay.querySelector('#notif-filter-status')?.addEventListener('change', (e) => { filterStatus = e.target.value; resetAndFetch(); });
  overlay.querySelector('#notif-bulk-read')?.addEventListener('click', bulkMarkRead);
  overlay.querySelector('#notif-bulk-purge')?.addEventListener('click', bulkPurge);
  overlay.querySelector('#notif-load-more .notif-load-more-btn')?.addEventListener('click', fetchMore);

  // Keyboard
  const onKey = (e) => { if (e.key === 'Escape') closeModal(); };
  document.addEventListener('keydown', onKey);
  overlay._keyHandler = onKey;

  fetchMore();
}

function closeModal() {
  if (!overlay) return;
  if (overlay._keyHandler) {
    document.removeEventListener('keydown', overlay._keyHandler);
  }
  overlay.remove();
  overlay = null;
}

function resetAndFetch() {
  items = [];
  offset = 0;
  hasMore = true;
  selectedIds.clear();
  const list = overlay?.querySelector('#notif-history-list');
  if (list) list.innerHTML = '';
  fetchMore();
}

async function fetchMore() {
  if (!hasMore) return;
  const params = new URLSearchParams({ limit: '30', offset: String(offset) });
  if (filterType) params.set('type', filterType);
  if (filterStatus === 'unread') params.set('unread_only', 'true');

  try {
    const res = await fetch(`/api/notifications/history?${params}`);
    if (!res.ok) return;
    let batch = await res.json();

    // Client-side filter for "read only"
    if (filterStatus === 'read') {
      batch = batch.filter(n => n.read_at);
    }

    if (batch.length < 30) hasMore = false;
    offset += batch.length;
    items.push(...batch);
    renderList();
  } catch { /* network error */ }
}

function renderList() {
  const list = overlay?.querySelector('#notif-history-list');
  if (!list) return;

  if (items.length === 0) {
    list.innerHTML = '<div class="notif-empty" style="padding:40px">No notifications found</div>';
    toggleLoadMore(false);
    return;
  }

  let html = '';
  for (const n of items) {
    const isUnread = !n.read_at;
    const checked = selectedIds.has(n.id) ? 'checked' : '';
    const icon = TYPE_ICONS[n.type] || TYPE_ICONS.default;
    html += `<div class="notif-history-item ${isUnread ? 'unread' : ''}" data-id="${n.id}">
      <input type="checkbox" ${checked} data-id="${n.id}">
      <span class="notif-icon">${icon}</span>
      <div class="notif-content">
        <div class="notif-title">${escapeHtml(n.title)}</div>
        ${n.body ? `<div class="notif-body">${escapeHtml(n.body)}</div>` : ''}
      </div>
      <span class="notif-time">${formatTime(n.created_at)}</span>
    </div>`;
  }
  list.innerHTML = html;

  // Wire checkboxes
  for (const cb of list.querySelectorAll('input[type="checkbox"]')) {
    cb.addEventListener('change', (e) => {
      const id = parseInt(e.target.dataset.id);
      if (e.target.checked) selectedIds.add(id); else selectedIds.delete(id);
    });
  }

  toggleLoadMore(hasMore);
}

function toggleLoadMore(show) {
  const el = overlay?.querySelector('#notif-load-more');
  if (el) el.style.display = show ? 'flex' : 'none';
}

async function bulkMarkRead() {
  if (selectedIds.size === 0) return;
  const ids = [...selectedIds];
  try {
    await fetch('/api/notifications/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    for (const n of items) {
      if (ids.includes(n.id)) n.read_at = Math.floor(Date.now() / 1000);
    }
    selectedIds.clear();
    renderList();
  } catch { /* network error */ }
}

async function bulkPurge() {
  try {
    await fetch('/api/notifications/old', { method: 'DELETE' });
    resetAndFetch();
  } catch { /* network error */ }
}

function formatTime(unixTs) {
  const d = new Date(unixTs * 1000);
  const diff = Math.floor(Date.now() / 1000) - unixTs;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function buildIcon(type) {
  const common = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"';

  switch (type) {
    case 'message':
      return `<svg ${common} aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`;
    case 'agent':
      return `<svg ${common} aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M9 3h6"/><path d="M12 7V3"/><path d="M9 17v2"/><path d="M15 17v2"/><path d="M5 9H3"/><path d="M5 15H3"/><path d="M21 9h-2"/><path d="M21 15h-2"/><path d="M10 11h4"/><path d="M10 14h4"/></svg>`;
    case 'workflow':
      return `<svg ${common} aria-hidden="true"><circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M8 6h8"/><path d="M7.5 7.5l3 7"/><path d="M16.5 7.5l-3 7"/></svg>`;
    case 'chain':
      return `<svg ${common} aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l1.41-1.41a5 5 0 0 0-7.07-7.07L10 5"/><path d="M14 11a5 5 0 0 0-7.07 0L5.51 12.41a5 5 0 0 0 7.07 7.07L14 19"/></svg>`;
    case 'dag':
      return `<svg ${common} aria-hidden="true"><circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M8 6h8"/><path d="M6.8 7.6 11 16"/><path d="M17.2 7.6 13 16"/></svg>`;
    case 'warning':
      return `<svg ${common} aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>`;
    case 'approval':
      return `<svg ${common} aria-hidden="true"><rect x="4" y="11" width="16" height="9" rx="2"/><path d="M8 11V8a4 4 0 1 1 8 0v3"/></svg>`;
    case 'notification':
    default:
      return `<svg ${common} aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>`;
  }
}

init();
