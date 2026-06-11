/**
 * nav-badges.js — Live nav rail badge counts
 *
 * Fetches real counts for Calendar, Meetings, and Jira Board from the same
 * overview APIs the dashboard already calls. Updates the badge elements in the
 * rail once data arrives. Badges start hidden (see index.html) and are shown
 * only when a meaningful non-zero count is available.
 *
 * Design decisions per item:
 *   Calendar  → today's event count from /api/calendar/overview
 *   Meetings  → today's meeting count from /api/meetings/overview
 *   Jira      → total open items across all board columns from /api/jira/overview
 *   Skills    → REMOVED (total count is not actionable as a nav badge)
 *   Agents    → REMOVED (total count is not actionable as a nav badge)
 *   Memory    → REMOVED (observation count in the thousands, not meaningful)
 *   Campaigns → REMOVED (no boot-time fetch; adding one would slow shell load)
 */

function _setBadge(id, count) {
  const el = document.getElementById(id);
  if (!el) return;
  if (count > 0) {
    el.textContent = String(count);
    el.removeAttribute('hidden');
  } else {
    el.setAttribute('hidden', '');
  }
}

async function _loadCalendarBadge() {
  try {
    const res = await fetch('/api/calendar/overview');
    if (!res.ok) return;
    const data = await res.json();
    const count = Number(data?.today?.meetingsCount || 0);
    _setBadge('nav-badge-calendar', count);
  } catch {
    // leave hidden on error
  }
}

async function _loadMeetingsBadge() {
  try {
    const res = await fetch('/api/meetings/overview');
    if (!res.ok) return;
    const data = await res.json();
    const count = Number(data?.today?.meetingsCount || 0);
    _setBadge('nav-badge-meetings', count);
  } catch {
    // leave hidden on error
  }
}

async function _loadJiraBadge() {
  try {
    const res = await fetch('/api/jira/overview');
    if (!res.ok) return;
    const data = await res.json();
    // Count all open items across every board column
    const columns = Array.isArray(data?.columns) ? data.columns : [];
    const total = columns.reduce((sum, col) => sum + (Array.isArray(col.items) ? col.items.length : 0), 0);
    _setBadge('nav-badge-jira', total);
  } catch {
    // leave hidden on error
  }
}

// Fire all three in parallel — each is a lightweight JSON read
Promise.all([
  _loadCalendarBadge(),
  _loadMeetingsBadge(),
  _loadJiraBadge(),
]);
