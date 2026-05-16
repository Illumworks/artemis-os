// Notification bell — badge, dropdown, read/unread management
import { on, emit } from '../core/events.js';

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

let unreadCount = 0;
let notifications = [];
let dropdownOpen = false;
let autoReadTimer = null;

const bellBtn = document.getElementById('notif-bell-btn');
const badge = document.getElementById('notif-badge');
const dropdown = document.getElementById('notif-dropdown');

function init() {
  if (!bellBtn || !badge || !dropdown) return;
  fetchUnreadCount();
  on('ws:message', handleWsMessage);
  on('ws:reconnected', fetchUnreadCount);
  bellBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleDropdown(); });
  document.addEventListener('click', handleOutsideClick);
}

async function fetchUnreadCount() {
  try {
    const res = await fetch('/api/notifications/unread-count');
    if (!res.ok) return;
    const data = await res.json();
    updateBadge(data.count);
  } catch { /* network error */ }
}

function toggleDropdown() {
  dropdownOpen = !dropdownOpen;
  if (dropdownOpen) {
    fetchAndRender();
    dropdown.classList.remove('hidden');
  } else {
    dropdown.classList.add('hidden');
    clearAutoRead();
  }
}

function closeDropdown() {
  dropdownOpen = false;
  dropdown.classList.add('hidden');
  clearAutoRead();
}

async function fetchAndRender() {
  try {
    const res = await fetch('/api/notifications/history?limit=15');
    if (!res.ok) return;
    notifications = await res.json();
    renderDropdown();
    startAutoRead();
  } catch { /* network error */ }
}

function renderDropdown() {
  if (!notifications.length) {
    dropdown.innerHTML = `
      <div class="notif-dropdown-header">Notifications</div>
      <div class="notif-empty">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
        No notifications yet
      </div>`;
    return;
  }

  const countLabel = unreadCount > 0 ? `<span>${unreadCount} unread</span>` : '';
  let html = `<div class="notif-dropdown-header">Notifications ${countLabel}</div>`;
  html += '<div class="notif-list">';
  for (const n of notifications) {
    html += renderItem(n);
  }
  html += '</div>';
  html += `<div class="notif-footer">
    <button class="notif-footer-btn" data-action="mark-all-read">Mark all read</button>
    <button class="notif-footer-btn" data-action="view-all">View All</button>
  </div>`;

  dropdown.innerHTML = html;

  // Wire events
  dropdown.querySelector('[data-action="mark-all-read"]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    markAllRead();
  });
  dropdown.querySelector('[data-action="view-all"]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    closeDropdown();
    emit('notification:show-history');
  });

  // Wire individual items
  for (const el of dropdown.querySelectorAll('.notif-item')) {
    el.addEventListener('click', () => onNotifClick(el.dataset.id));
  }
  for (const el of dropdown.querySelectorAll('.notif-dot')) {
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = parseInt(e.target.closest('.notif-item').dataset.id);
      if (e.target.classList.contains('unread')) markAsRead([id]);
    });
  }
}

function renderItem(n) {
  const isUnread = !n.read_at;
  const icon = TYPE_ICONS[n.type] || TYPE_ICONS.default;
  return `<div class="notif-item ${isUnread ? 'unread' : ''}" data-id="${n.id}" data-session="${n.source_session_id || ''}">
    <span class="notif-dot ${isUnread ? 'unread' : 'read'}"></span>
    <span class="notif-icon">${icon}</span>
    <div class="notif-content">
      <div class="notif-title">${escapeHtml(n.title)}</div>
      ${n.body ? `<div class="notif-body">${escapeHtml(n.body)}</div>` : ''}
    </div>
    <span class="notif-time">${timeAgo(n.created_at)}</span>
  </div>`;
}

function updateBadge(count) {
  if (!bellBtn || !badge) return;
  unreadCount = count;
  if (count > 0) {
    badge.textContent = count > 99 ? '99+' : count;
    badge.classList.remove('hidden');
    bellBtn.classList.add('has-unread');
  } else {
    badge.classList.add('hidden');
    bellBtn.classList.remove('has-unread');
  }
}

function handleWsMessage(msg) {
  if (msg.type === 'notification:new') {
    updateBadge(msg.unreadCount);
    if (dropdownOpen) {
      notifications.unshift(msg.notification);
      if (notifications.length > 15) notifications.pop();
      renderDropdown();
    }
  } else if (msg.type === 'notification:read') {
    updateBadge(msg.unreadCount);
    if (dropdownOpen) {
      for (const n of notifications) {
        if (msg.ids.length === 0 || msg.ids.includes(n.id)) {
          n.read_at = Math.floor(Date.now() / 1000);
        }
      }
      renderDropdown();
    }
  }
}

function handleOutsideClick(e) {
  if (dropdownOpen && !e.target.closest('.notif-bell')) {
    closeDropdown();
  }
}

// ── Read strategies ──────────────────────────────────────
async function markAsRead(ids) {
  try {
    const res = await fetch('/api/notifications/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    if (!res.ok) return;
    const data = await res.json();
    updateBadge(data.unreadCount);
    for (const n of notifications) {
      if (ids.includes(n.id)) n.read_at = Math.floor(Date.now() / 1000);
    }
    renderDropdown();
  } catch { /* network error */ }
}

async function markAllRead() {
  try {
    const res = await fetch('/api/notifications/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ all: true }),
    });
    if (!res.ok) return;
    const data = await res.json();
    updateBadge(data.unreadCount);
    for (const n of notifications) n.read_at = Math.floor(Date.now() / 1000);
    renderDropdown();
  } catch { /* network error */ }
}

function startAutoRead() {
  clearAutoRead();
  const unreadIds = notifications.filter(n => !n.read_at).map(n => n.id);
  if (unreadIds.length === 0) return;
  autoReadTimer = setTimeout(() => markAsRead(unreadIds), 1500);
}

function clearAutoRead() {
  if (autoReadTimer) { clearTimeout(autoReadTimer); autoReadTimer = null; }
}

function onNotifClick(idStr) {
  const id = parseInt(idStr);
  const n = notifications.find(n => n.id === id);
  if (!n) return;
  if (!n.read_at) markAsRead([id]);
  if (n.source_session_id) {
    closeDropdown();
    emit('session:switch', n.source_session_id);
  }
}

// ── Helpers ──────────────────────────────────────────────
function timeAgo(unixTs) {
  const diff = Math.floor(Date.now() / 1000) - unixTs;
  if (diff < 60) return 'now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d`;
  return new Date(unixTs * 1000).toLocaleDateString();
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
