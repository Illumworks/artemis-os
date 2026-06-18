import {
  fetchJiraIssueApi,
  fetchJiraAssignableUsersApi,
  addJiraCommentApi,
  addJiraWorklogApi,
  uploadJiraAttachmentApi,
  changeJiraAssigneeApi,
  transitionJiraIssueApi,
  updateJiraDescriptionApi,
} from '../core/api.js';
import {
  _buildMentionRegex,
  _buildCommentThread,
} from './jira-comment-helpers.js';

// Map colKey → display label + CSS class
const COL_META = {
  todo:    { label: 'To Do',       cls: 'todo' },
  prog:    { label: 'In Progress', cls: 'prog' },
  blocked: { label: 'Blocked',     cls: 'blocked' },
  review:  { label: 'In Review',   cls: 'review' },
  done:    { label: 'Done',        cls: 'done' },
};
const COL_ORDER = ['todo', 'prog', 'blocked', 'review', 'done'];

function _initials(name = '') {
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return (name.slice(0, 2) || '??').toUpperCase();
}

function _avatarColor(name = '') {
  const COLORS = ['#c97d3e','#3e8bc9','#7b4fc9','#3eb87b','#c93e6e','#3ec9c0','#c9b33e'];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return COLORS[h % COLORS.length];
}

function _relTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 2) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function _ext(name = '') {
  const m = name.match(/\.([a-z0-9]+)$/i);
  return m ? m[1].toLowerCase() : 'file';
}

function _kindCls(name = '') {
  const e = _ext(name);
  if (['jpg','jpeg','png','gif','webp','svg'].includes(e)) return 'img';
  if (e === 'pdf') return 'pdf';
  return '';
}

function _fmtSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function _esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Render comment body: URLs → clickable links, @mentions → chips, rest → escaped.
// knownNames: optional string[] of known display names for bounded mention matching.
// Uses _buildMentionRegex (imported from jira-comment-helpers.js).
function _renderText(raw, knownNames) {
  if (!raw) return '';
  const CHUNK_RE = _buildMentionRegex(knownNames || []);
  let result = '';
  let lastIdx = 0;
  let m;
  while ((m = CHUNK_RE.exec(raw)) !== null) {
    result += _esc(raw.slice(lastIdx, m.index));
    if (m[1]) {
      result += `<a href="${_esc(m[1])}" target="_blank" rel="noopener noreferrer" class="jira-comment-link">${_esc(m[1])}</a>`;
    } else {
      const name = m[2].replace(/^@@?/, '');
      result += `<span class="jira-mention">@${_esc(name)}</span>`;
    }
    lastIdx = m.index + m[0].length;
  }
  result += _esc(raw.slice(lastIdx));
  return result;
}

// Collect all known display names from comments, assignee and assignable users list.
// Used to feed both _buildCommentThread and _renderText for bounded mention matching.
function _gatherKnownNames(comments, assigneeName, assignableUsers) {
  const names = new Set();
  for (const c of (comments || [])) {
    const n = (c.author || '').trim();
    if (n) names.add(n);
  }
  if (assigneeName && assigneeName !== 'Unassigned') names.add(assigneeName.trim());
  for (const u of (assignableUsers || [])) {
    const n = (u.displayName || u.name || '').trim();
    if (n) names.add(n);
  }
  return [...names];
}

// Render a single comment row as an HTML string.
// indent: true → render as a nested reply (indented + connector line).
function _renderCommentHtml(c, knownNames, { indent = false } = {}) {
  const cName = c.author || 'Unknown';
  const cColor = _avatarColor(cName);
  const cInit = _initials(cName);
  const indentCls = indent ? ' jira-comment--reply' : '';
  return `
    <div class="jira-comment${indentCls}">
      <span class="jira-card-avatar jira-comment-avatar" style="background:${_esc(cColor)}">${_esc(cInit)}</span>
      <div style="flex:1;min-width:0">
        <div class="jira-comment-head">
          <span class="jira-comment-author">${_esc(cName)}</span>
          <span class="jira-comment-when">${_esc(_relTime(c.created))}</span>
          <button class="jira-reply-btn" data-jira-drawer-action="reply-to" data-reply-author="${_esc(cName)}" title="Reply to ${_esc(cName)}">Reply</button>
        </div>
        <div class="jira-comment-text">${_renderText(c.body || c.text || '', knownNames)}</div>
      </div>
    </div>
  `;
}

// Build the full threaded comment list HTML from a flat comments array.
function _renderThreadedComments(comments, knownNames) {
  const threads = _buildCommentThread(comments, knownNames);
  return threads.map(({ comment, replies }) => {
    const parentHtml = _renderCommentHtml(comment, knownNames, { indent: false });
    if (replies.length === 0) return parentHtml;
    const repliesHtml = replies.map(r => _renderCommentHtml(r, knownNames, { indent: true })).join('');
    return `${parentHtml}<div class="jira-comment-replies">${repliesHtml}</div>`;
  }).join('');
}

class ArtemisJiraCardDrawer extends HTMLElement {
  connectedCallback() {
    this._issueKey = null;
    this._issue = null;
    this._colStatusMap = {};
    this._assignableUsers = null;
    this._assignMenuOpen = false;
    this._siteUrl = '';
    this._ac = null;
    this._pendingMentions = new Map();
    this._mentionCursor = -1;
    // Accumulates {filename, url} for files dropped/attached to the reply area
    // before the comment is submitted. Cleared on submit (success or failure reset).
    this._pendingCommentAttachments = [];
    this.innerHTML = '';
  }

  // Called from home.js when a card is clicked.
  // colStatusMap: { todo: ['To Do', ...], prog: ['In Progress', ...], ... }
  // assignableUsers: array from fetchJiraAssignableUsersApi (may be null; lazily fetched)
  // siteUrl: fallback base URL when the API response doesn't include issue.url
  open(issueKey, colStatusMap = {}, assignableUsers = null, siteUrl = '') {
    // Abort all listeners from any previous open before re-binding.
    if (this._ac) this._ac.abort();
    this._ac = new AbortController();

    this._issueKey = issueKey;
    this._colStatusMap = colStatusMap;
    this._assignableUsers = assignableUsers;
    this._assignMenuOpen = false;
    this._siteUrl = siteUrl || '';
    this._pendingMentions = new Map();
    this._mentionCursor = -1;
    this._pendingCommentAttachments = [];
    this._renderLoading();
    this._fetchAndRender();
  }

  close() {
    if (this._ac) { this._ac.abort(); this._ac = null; }
    this.innerHTML = '';
    this._issue = null;
    this._issueKey = null;
    this.dispatchEvent(new CustomEvent('jira-drawer-close', { bubbles: true }));
  }

  _renderLoading() {
    this.innerHTML = `
      <div class="drawer-backdrop" data-jira-drawer-action="close"></div>
      <div class="drawer" role="dialog" aria-label="Jira issue details">
        <div class="drawer-head">
          <div style="flex:1;min-width:0">
            <div style="font-size:12px;color:var(--ink-5);margin-bottom:6px">${_esc(this._issueKey)}</div>
            <div style="font-size:14px;color:var(--ink-4)">Loading…</div>
          </div>
          <button class="drawer-close" data-jira-drawer-action="close" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>
      </div>
    `;
    // Single delegated listener for the loading state — aborted when open() resets _ac.
    this.addEventListener('click', (e) => {
      if (e.target.closest('[data-jira-drawer-action="close"]')) this.close();
    }, { signal: this._ac.signal });
  }

  async _fetchAndRender() {
    try {
      const issue = await fetchJiraIssueApi(this._issueKey);
      this._issue = issue;

      // Lazily fetch assignable users if not provided
      if (!this._assignableUsers && issue.projectKey) {
        try {
          const users = await fetchJiraAssignableUsersApi(issue.projectKey);
          this._assignableUsers = users;
        } catch (_) { /* optional */ }
      }

      this._renderIssue();
    } catch (err) {
      this._renderError(err.message || 'Failed to load issue');
    }
  }

  _renderError(msg) {
    const drawer = this.querySelector('.drawer');
    if (drawer) {
      const body = drawer.querySelector('.drawer-body') || drawer;
      body.innerHTML = `<div class="jira-err visible" style="margin-top:16px">${_esc(msg)}</div>`;
    }
  }

  _renderIssue() {
    const issue = this._issue;
    const key = issue.key || this._issueKey;
    const title = issue.title || issue.summary || key;
    const status = issue.status || '';
    const description = issue.description || '';
    const labels = issue.labels || [];
    const comments = issue.comments || [];
    const worklogs = issue.worklogs || [];
    const attachments = issue.attachments || [];
    const transitions = issue.transitions || [];
    const assigneeName = issue.assigneeName || issue.assignee || 'Unassigned';
    const assigneeId = issue.assigneeId || '';

    // Determine active colKey from current status
    const activeColKey = this._statusToColKey(status);

    // Status segment HTML
    const segHtml = COL_ORDER.map(k => {
      const m = COL_META[k];
      const isActive = k === activeColKey;
      return `<button class="jira-state-opt ${m.cls}${isActive ? ' active' : ''}"
        data-jira-transition-col="${_esc(k)}"
        title="${_esc(m.label)}">${_esc(m.label)}</button>`;
    }).join('');

    // Assignee button
    const aColor = _avatarColor(assigneeName);
    const aInit = _initials(assigneeName);
    const assigneeHtml = `
      <div class="jira-assign-wrap">
        <button class="jira-assign-current" data-jira-drawer-action="toggle-assign">
          <span class="jira-card-avatar" style="background:${_esc(aColor)}">${_esc(aInit)}</span>
          <span data-jira-assignee-name>${_esc(assigneeName)}</span>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        <div class="jira-assign-menu hidden" data-jira-assign-menu>
          ${this._renderAssignMenu(assigneeId)}
        </div>
      </div>
    `;

    // Build the set of known names for mention-boundary matching.
    // Includes: all comment authors + assignee + any assignable users.
    const knownNames = _gatherKnownNames(comments, assigneeName, this._assignableUsers);

    // Comments HTML — threaded
    const commentsHtml = comments.length === 0
      ? `<div class="jira-empty-line">No comments yet. Start the thread.</div>`
      : _renderThreadedComments(comments, knownNames);

    // Worklogs HTML — API returns timeSpentSeconds, convert to hours for display
    const _secsToHrs = (s) => {
      const h = (s || 0) / 3600;
      return h === Math.floor(h) ? String(Math.floor(h)) : h.toFixed(1);
    };
    const totalHrs = worklogs.reduce((a, w) => a + (w.timeSpentSeconds || 0), 0) / 3600;
    const worklogHtml = worklogs.length === 0
      ? `<div class="jira-empty-line">No time logged yet.</div>`
      : worklogs.map((w, i) => `
          <div class="jira-time-row" data-worklog-idx="${i}">
            <span class="jira-time-who">${_esc(_initials(w.author || ''))}</span>
            <span class="jira-time-hrs">${_esc(_secsToHrs(w.timeSpentSeconds))}h</span>
            <span class="jira-time-note">${_esc(w.comment || '')}</span>
            <span class="jira-time-when">${_esc(_relTime(w.started || w.created))}</span>
          </div>
        `).join('');

    // Attachments HTML
    const attachHtml = attachments.length === 0
      ? ''
      : attachments.map(a => {
          const kc = _kindCls(a.filename || a.name || '');
          const ext = _ext(a.filename || a.name || 'file');
          const nm = a.filename || a.name || 'attachment';
          const sz = _fmtSize(a.size);
          const href = a.id ? `/api/jira/attachment/${encodeURIComponent(a.id)}` : '';
          const isImage = kc === 'img' || (a.mimeType || '').startsWith('image/');
          const inlineHref = href ? `${href}?inline=1` : '';
          const previewHtml = isImage && inlineHref
            ? `<a href="${_esc(inlineHref)}" target="_blank" rel="noopener noreferrer" class="jira-attach-preview-link" title="Preview ${_esc(nm)}">
                 <img class="jira-attach-thumb" src="${_esc(inlineHref)}" alt="${_esc(nm)}" loading="lazy"/>
               </a>`
            : '';
          return `
            <div class="jira-attach${isImage ? ' jira-attach-image' : ''}">
              ${previewHtml}
              <div class="jira-attach-meta">
                <span class="jira-attach-kind ${_esc(kc)}">${_esc(ext)}</span>
                ${href
                  ? `<a class="jira-attach-name jira-attach-link" href="${_esc(isImage ? inlineHref : href)}" ${isImage ? `target="_blank" rel="noopener noreferrer"` : `download="${_esc(nm)}"`} title="${_esc(nm)}">${_esc(nm)}</a>`
                  : `<span class="jira-attach-name" title="${_esc(nm)}">${_esc(nm)}</span>`}
                <span class="jira-attach-size">${_esc(sz)}</span>
              </div>
            </div>
          `;
        }).join('');

    // Priority pill
    const prio = issue.priority || '';
    const prioPill = prio ? `<span class="jira-card-prio ${_esc(prio.toLowerCase())}">${_esc(prio)}</span>` : '';

    const issueUrl = issue.url || (this._siteUrl ? `${this._siteUrl}/browse/${key}` : '');
    const keyEl = issueUrl
      ? `<a class="jira-card-id-pill jira-key-link" href="${_esc(issueUrl)}" target="_blank" rel="noopener noreferrer" title="Open in Jira">${_esc(key)}</a>`
      : `<span class="jira-card-id-pill">${_esc(key)}</span>`;

    const drawerEl = this.querySelector('.drawer');
    const innerHtml = `
        <div class="drawer-head">
          <div style="flex:1;min-width:0">
            <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
              ${keyEl}
              ${prioPill}
            </div>
            <div style="font-family:var(--font-display);font-size:18px;font-weight:500;letter-spacing:-0.015em;line-height:1.3;color:var(--ink)">${_esc(title)}</div>
          </div>
          <button class="drawer-close" data-jira-drawer-action="close" aria-label="Close">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>

        <div class="drawer-body">
          <div class="jira-detail-meta">
            <div class="jira-detail-meta-col">
              <div class="jira-detail-label">Status</div>
              <div class="jira-state-seg" data-jira-state-seg>
                ${segHtml}
              </div>
              <div class="jira-err" data-jira-err="transition"></div>
            </div>
            <div class="jira-detail-meta-col">
              <div class="jira-detail-label">Assignee</div>
              ${assigneeHtml}
              <div class="jira-err" data-jira-err="assignee"></div>
            </div>
          </div>

          <div class="drawer-section">
            <div class="drawer-section-label" style="display:flex;align-items:center;justify-content:space-between">
              Description
              <span class="jira-desc-saving hidden" data-jira-desc-saving>Saving…</span>
            </div>
            <textarea class="jira-desc-edit meeting-notes-area" data-jira-desc
              rows="4"
              placeholder="Add a description…"
              style="width:100%;box-sizing:border-box;resize:vertical">${_esc(description)}</textarea>
            <div class="jira-err" data-jira-err="description"></div>
            ${labels.length ? `<div class="jira-card-tags" style="margin-top:6px">${labels.map(l => `<span class="jira-card-tag">#${_esc(l)}</span>`).join('')}</div>` : ''}
          </div>

          <div class="drawer-section">
            <div class="drawer-section-label">Comments · <span data-jira-comment-count>${comments.length}</span></div>
            <div class="jira-comments" data-jira-comments>${commentsHtml}</div>
            <div class="jira-reply" data-jira-reply-wrap>
              <div class="jira-reply-context hidden" data-jira-reply-context>
                <span data-jira-reply-context-label></span>
                <button class="jira-reply-context-clear" data-jira-drawer-action="clear-reply-context" title="Cancel reply">×</button>
              </div>
              <div class="jira-reply-area-wrap" data-jira-reply-area-wrap>
                <textarea data-jira-reply rows="2"
                  class="meeting-notes-area"
                  placeholder="Write a comment or @mention a teammate…"
                  style="width:100%;box-sizing:border-box"></textarea>
                <div class="jira-mention-dropdown hidden" data-jira-mention-dropdown></div>
              </div>
              <div class="meeting-notes-foot">
                <span style="font-size:11px;color:var(--ink-5)">⌘+Enter to send</span>
                <input type="file" multiple style="display:none" data-jira-comment-file-input/>
                <button class="jira-comment-attach-btn" data-jira-drawer-action="comment-attach" title="Attach file">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                </button>
                <button class="btn btn-amber btn-sm" data-jira-drawer-action="submit-comment">Comment</button>
              </div>
              <div class="jira-err" data-jira-err="comment"></div>
            </div>
          </div>

          <div class="drawer-section">
            <div class="drawer-section-label">Time logged · <span data-jira-total-hrs>${totalHrs.toFixed(2).replace(/\.00$/, '')}h</span></div>
            <div class="jira-timelog" data-jira-timelog>${worklogHtml}</div>
            <div class="jira-time-add">
              <input class="auto-edit-input" data-jira-time-hrs
                style="width:90px;margin-top:0" type="number" step="0.25" min="0.25" placeholder="Hours"/>
              <input class="auto-edit-input" data-jira-time-note
                style="flex:1;margin-top:0" type="text" placeholder="What did you work on?"/>
              <button class="btn btn-outline btn-sm" data-jira-drawer-action="submit-time">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                Log
              </button>
            </div>
            <div class="jira-err" data-jira-err="timelog"></div>
          </div>

          <div class="drawer-section">
            <div class="drawer-section-label">Attachments · <span data-jira-attach-count>${attachments.length}</span></div>
            <div class="jira-attach-list" data-jira-attach-list>${attachHtml}</div>
            <label class="jira-attach-drop" data-jira-attach-drop>
              <input type="file" multiple style="display:none" data-jira-file-input/>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>
              <span>Drop files or click to attach</span>
            </label>
            <div class="jira-attach-progress hidden" data-jira-attach-progress></div>
            <div class="jira-err" data-jira-err="attach"></div>
          </div>
        </div>
    `;

    if (drawerEl) {
      drawerEl.innerHTML = innerHtml;
    } else {
      // Safety fallback: shell not present (shouldn't happen), do a full replace.
      this.innerHTML = `
        <div class="drawer-backdrop" data-jira-drawer-action="close"></div>
        <div class="drawer" role="dialog" aria-label="${_esc(key)} details">${innerHtml}</div>
      `;
    }

    this._bindEvents();
  }

  _renderAssignMenu(currentAssigneeId) {
    const users = this._assignableUsers;
    if (!users || users.length === 0) {
      return `<div style="padding:8px 10px;font-size:12px;color:var(--ink-5)">No users available</div>`;
    }
    return users.map(u => {
      const name = u.displayName || u.name || 'Unknown';
      const id = u.accountId || u.id || '';
      const color = _avatarColor(name);
      const init = _initials(name);
      const active = id === currentAssigneeId ? ' style="font-weight:600"' : '';
      return `
        <button class="jira-assign-item" data-jira-assign-id="${_esc(id)}" data-jira-assign-name="${_esc(name)}"${active}>
          <span class="jira-card-avatar" style="background:${_esc(color)}">${_esc(init)}</span>
          <span>${_esc(name)}</span>
        </button>
      `;
    }).join('');
  }

  _bindEvents() {
    const sig = this._ac.signal;

    // Single delegated click listener — handles all actions including close.
    this.addEventListener('click', this._onClick.bind(this), { signal: sig });

    // Description: save on blur or ⌘+Enter
    const descArea = this.querySelector('[data-jira-desc]');
    if (descArea) {
      descArea.addEventListener('blur', () => this._saveDescription(descArea.value), { signal: sig });
      descArea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); descArea.blur(); }
      }, { signal: sig });
    }

    // Reply textarea ⌘+Enter + @mention autocomplete
    const replyArea = this.querySelector('[data-jira-reply]');
    if (replyArea) {
      replyArea.addEventListener('keydown', (e) => {
        const dd = this.querySelector('[data-jira-mention-dropdown]');
        const open = dd && !dd.classList.contains('hidden');
        if (open) {
          if (e.key === 'ArrowDown') { e.preventDefault(); this._moveMentionCursor(1); return; }
          if (e.key === 'ArrowUp')   { e.preventDefault(); this._moveMentionCursor(-1); return; }
          if (e.key === 'Enter' || e.key === 'Tab') {
            const active = dd.querySelector('.jira-mention-item.active');
            if (active) { e.preventDefault(); active.click(); return; }
          }
          if (e.key === 'Escape') { this._closeMentionDropdown(); return; }
        }
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) this._submitComment();
      }, { signal: sig });
      replyArea.addEventListener('input', () => this._onReplyInput(replyArea), { signal: sig });
      replyArea.addEventListener('blur', () => setTimeout(() => this._closeMentionDropdown(), 150), { signal: sig });
    }

    // Comment file input
    const commentFileInput = this.querySelector('[data-jira-comment-file-input]');
    if (commentFileInput) {
      commentFileInput.addEventListener('change', async (e) => {
        const files = [...e.target.files];
        e.target.value = '';
        await this._uploadCommentFiles(files);
      }, { signal: sig });
    }

    // Drag-drop on comment reply area
    const replyWrap = this.querySelector('[data-jira-reply-area-wrap]');
    if (replyWrap) {
      replyWrap.addEventListener('dragover', (e) => { e.preventDefault(); replyWrap.classList.add('drag-over'); }, { signal: sig });
      replyWrap.addEventListener('dragleave', (e) => { if (!replyWrap.contains(e.relatedTarget)) replyWrap.classList.remove('drag-over'); }, { signal: sig });
      replyWrap.addEventListener('drop', async (e) => {
        e.preventDefault();
        replyWrap.classList.remove('drag-over');
        const files = [...(e.dataTransfer?.files || [])];
        if (files.length) await this._uploadCommentFiles(files);
      }, { signal: sig });
    }

    // File input change (attachments section)
    const fileInput = this.querySelector('[data-jira-file-input]');
    if (fileInput) {
      fileInput.addEventListener('change', (e) => {
        this._uploadFiles([...e.target.files]);
        e.target.value = '';
      }, { signal: sig });
    }

    // Drag-drop on attach zone
    const drop = this.querySelector('[data-jira-attach-drop]');
    if (drop) {
      drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag-over'); }, { signal: sig });
      drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'), { signal: sig });
      drop.addEventListener('drop', (e) => {
        e.preventDefault();
        drop.classList.remove('drag-over');
        const files = [...(e.dataTransfer?.files || [])];
        if (files.length) this._uploadFiles(files);
      }, { signal: sig });
    }

    // Close assign menu on outside click — capture phase so it fires before toggle-assign.
    document.addEventListener('click', (e) => {
      if (!e.target.closest('[data-jira-assign-menu]') && !e.target.closest('[data-jira-drawer-action="toggle-assign"]')) {
        this._closeAssignMenu();
      }
    }, { capture: true, signal: sig });
  }

  _onClick(e) {
    const btn = e.target.closest('[data-jira-drawer-action]');
    if (!btn) {
      // Check transition segment
      const transBtn = e.target.closest('[data-jira-transition-col]');
      if (transBtn) { this._doTransition(transBtn.dataset.jiraTransitionCol); return; }
      // Check assignee item
      const assignItem = e.target.closest('[data-jira-assign-id]');
      if (assignItem) { this._doAssign(assignItem.dataset.jiraAssignId, assignItem.dataset.jiraAssignName); return; }
      return;
    }

    const action = btn.dataset.jiraDrawerAction;
    if (action === 'close') this.close();
    else if (action === 'submit-comment') this._submitComment();
    else if (action === 'submit-time') this._submitTime();
    else if (action === 'toggle-assign') this._toggleAssignMenu();
    else if (action === 'reply-to') this._setReplyTo(btn.dataset.replyAuthor);
    else if (action === 'clear-reply-context') this._clearReplyTo();
    else if (action === 'comment-attach') {
      this.querySelector('[data-jira-comment-file-input]')?.click();
    }
  }

  _showErr(key, msg) {
    const el = this.querySelector(`[data-jira-err="${key}"]`);
    if (!el) return;
    el.textContent = msg;
    el.classList.add('visible');
    setTimeout(() => { el.classList.remove('visible'); }, 5000);
  }

  _clearErr(key) {
    const el = this.querySelector(`[data-jira-err="${key}"]`);
    if (el) el.classList.remove('visible');
  }

  async _saveDescription(text) {
    if (!this._issue) return;
    const newText = text.trim();
    const prevText = this._issue.description || '';
    if (newText === prevText) return;

    const savingEl = this.querySelector('[data-jira-desc-saving]');
    if (savingEl) savingEl.classList.remove('hidden');
    this._clearErr('description');

    try {
      await updateJiraDescriptionApi(this._issueKey, newText);
      this._issue.description = newText;
    } catch (err) {
      // Revert textarea to saved value
      const descArea = this.querySelector('[data-jira-desc]');
      if (descArea) descArea.value = prevText;
      this._showErr('description', err.message || 'Failed to save description');
    } finally {
      if (savingEl) savingEl.classList.add('hidden');
    }
  }

  async _submitComment() {
    const area = this.querySelector('[data-jira-reply]');
    const text = area?.value.trim();
    if (!text) return;

    const mentions = [...this._pendingMentions.entries()].map(([name, id]) => ({ name, id }));
    this._pendingMentions.clear();
    this._closeMentionDropdown();

    // Snapshot and clear pending attachment refs before the API call.
    const attachmentRefs = [...this._pendingCommentAttachments];
    this._pendingCommentAttachments = [];

    const btn = this.querySelector('[data-jira-drawer-action="submit-comment"]');
    if (btn) { btn.disabled = true; btn.textContent = 'Sending…'; }
    area.value = '';
    // Clear attachment chips from the reply area.
    this.querySelectorAll('.jira-attach-chip').forEach(c => c.remove());
    this._clearErr('comment');

    // Optimistic append — replaced by server version on success.
    const thread = this.querySelector('[data-jira-comments]');
    const countEl = this.querySelector('[data-jira-comment-count]');
    const placeholder = thread?.querySelector('.jira-empty-line');
    if (placeholder) placeholder.remove();

    const tempId = `temp-${Date.now()}`;
    const meColor = _avatarColor('Me');
    const meInit = 'Me';
    const tempEl = document.createElement('div');
    tempEl.className = 'jira-comment';
    tempEl.dataset.tempId = tempId;
    // Build optimistic body: text + linked attachment chips (if any).
    const attachHtml = attachmentRefs.map(r =>
      `<div class="jira-comment-text" style="font-size:12px">
         📎 <a href="${_esc(r.url)}" target="_blank" rel="noopener noreferrer" class="jira-comment-link">${_esc(r.filename)}</a>
       </div>`
    ).join('');
    tempEl.innerHTML = `
      <span class="jira-card-avatar jira-comment-avatar" style="background:${meColor}">${meInit}</span>
      <div style="flex:1;min-width:0">
        <div class="jira-comment-head">
          <span class="jira-comment-author">You</span>
          <span class="jira-comment-when">just now</span>
        </div>
        <div class="jira-comment-text">${_renderText(text, _gatherKnownNames(this._issue?.comments || [], this._issue?.assigneeName || '', this._assignableUsers))}</div>
        ${attachHtml}
      </div>
    `;
    thread?.appendChild(tempEl);
    if (countEl) countEl.textContent = (parseInt(countEl.textContent) || 0) + 1;

    try {
      await addJiraCommentApi(this._issueKey, text, mentions, attachmentRefs);
      // Part 1 — refresh: replace the optimistic temp element with the canonical
      // server comment (real id, real timestamp, ADF-rendered body with links).
      // We re-fetch the full issue so counts and any concurrent edits are reconciled.
      try {
        const fresh = await fetchJiraIssueApi(this._issueKey);
        if (fresh && Array.isArray(fresh.comments)) {
          this._issue = { ...this._issue, ...fresh, comments: fresh.comments };
          this._refreshComments(fresh.comments);
        }
      } catch (_) {
        // Refresh is best-effort — leave the optimistic temp if the re-fetch fails.
      }
    } catch (err) {
      tempEl.remove();
      if (countEl) countEl.textContent = Math.max(0, (parseInt(countEl.textContent) || 0) - 1);
      area.value = text;
      // Restore attachment refs so the user can retry.
      this._pendingCommentAttachments = attachmentRefs;
      attachmentRefs.forEach(r => {
        const chip = document.createElement('div');
        chip.className = 'jira-attach-chip';
        chip.title = r.filename;
        chip.innerHTML = `<span>📎 ${_esc(r.filename)}</span>`;
        this.querySelector('[data-jira-reply-area-wrap]')?.insertAdjacentElement('afterbegin', chip);
      });
      this._showErr('comment', err.message || 'Failed to post comment');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Comment'; }
    }
  }

  // ── Re-render comments section from a fresh server list ─────────────────────

  _refreshComments(comments) {
    const thread = this.querySelector('[data-jira-comments]');
    const countEl = this.querySelector('[data-jira-comment-count]');
    if (!thread) return;
    if (!comments || comments.length === 0) {
      thread.innerHTML = `<div class="jira-empty-line">No comments yet. Start the thread.</div>`;
      if (countEl) countEl.textContent = '0';
      return;
    }
    const assigneeName = this._issue?.assigneeName || this._issue?.assignee || '';
    const knownNames = _gatherKnownNames(comments, assigneeName, this._assignableUsers);
    thread.innerHTML = _renderThreadedComments(comments, knownNames);
    if (countEl) countEl.textContent = String(comments.length);
  }

  // ── @mention autocomplete ────────────────────────────────────────────────────

  _onReplyInput(area) {
    const val = area.value;
    const pos = area.selectionStart ?? val.length;
    // Find the @-token immediately before the cursor
    const before = val.slice(0, pos);
    const m = before.match(/(?:^|[\s\n])(@{1,2}(\S*))$/);
    if (!m || !this._assignableUsers?.length) { this._closeMentionDropdown(); return; }
    const query = m[2].toLowerCase();
    const matches = this._assignableUsers
      .filter(u => (u.displayName || u.name || '').toLowerCase().includes(query))
      .slice(0, 6);
    if (!matches.length) { this._closeMentionDropdown(); return; }
    this._mentionCursor = 0;
    this._showMentionDropdown(matches, m[1], pos - m[1].length);
  }

  _showMentionDropdown(users, token, tokenStart) {
    const dd = this.querySelector('[data-jira-mention-dropdown]');
    if (!dd) return;
    dd.dataset.tokenStart = tokenStart;
    dd.dataset.tokenLen = token.length;
    dd.innerHTML = users.map((u, i) => {
      const name = u.displayName || u.name || 'Unknown';
      const id = u.accountId || u.id || '';
      const color = _avatarColor(name);
      const init = _initials(name);
      return `<button class="jira-mention-item${i === 0 ? ' active' : ''}" data-mention-id="${_esc(id)}" data-mention-name="${_esc(name)}">
        <span class="jira-card-avatar" style="background:${_esc(color)};width:20px;height:20px;font-size:9px">${_esc(init)}</span>
        <span>${_esc(name)}</span>
      </button>`;
    }).join('');
    dd.classList.remove('hidden');

    // Bind item clicks
    dd.querySelectorAll('.jira-mention-item').forEach(btn => {
      btn.addEventListener('mousedown', (e) => {
        e.preventDefault();
        this._insertMention(btn.dataset.mentionId, btn.dataset.mentionName);
      });
    });
  }

  _moveMentionCursor(dir) {
    const dd = this.querySelector('[data-jira-mention-dropdown]');
    if (!dd || dd.classList.contains('hidden')) return;
    const items = [...dd.querySelectorAll('.jira-mention-item')];
    if (!items.length) return;
    items[this._mentionCursor]?.classList.remove('active');
    this._mentionCursor = Math.max(0, Math.min(items.length - 1, this._mentionCursor + dir));
    items[this._mentionCursor]?.classList.add('active');
  }

  _insertMention(id, name) {
    const area = this.querySelector('[data-jira-reply]');
    const dd = this.querySelector('[data-jira-mention-dropdown]');
    if (!area || !dd) return;
    const tokenStart = parseInt(dd.dataset.tokenStart, 10);
    const tokenLen = parseInt(dd.dataset.tokenLen, 10);
    const val = area.value;
    const insert = `@${name} `;
    area.value = val.slice(0, tokenStart) + insert + val.slice(tokenStart + tokenLen);
    area.selectionStart = area.selectionEnd = tokenStart + insert.length;
    area.focus();
    this._pendingMentions.set(name, id);
    this._closeMentionDropdown();
  }

  _closeMentionDropdown() {
    const dd = this.querySelector('[data-jira-mention-dropdown]');
    dd?.classList.add('hidden');
    this._mentionCursor = -1;
  }

  // ── Upload files from comment area ──────────────────────────────────────────

  async _uploadCommentFiles(files) {
    // Upload each file to the issue and accumulate {filename, url} refs.
    // The refs are passed to addJiraCommentApi when the comment is submitted,
    // so each attachment is linked inside the comment body (ADF link mark)
    // rather than appended as a bare "📎 filename" text token.
    const area = this.querySelector('[data-jira-reply]');
    const wrap = this.querySelector('[data-jira-reply-area-wrap]');
    for (const file of files) {
      try {
        const result = await uploadJiraAttachmentApi(this._issueKey, file);
        // Record the ref so it's threaded into the comment on submit.
        const ref = { filename: file.name, url: result?.url || '' };
        this._pendingCommentAttachments.push(ref);
        // Show a visual chip inside the reply area so the user knows the file
        // was uploaded and will be linked when they hit Comment.
        const chip = document.createElement('div');
        chip.className = 'jira-attach-chip';
        chip.title = file.name;
        chip.innerHTML = `<span>📎 ${_esc(file.name)}</span>`;
        wrap?.insertAdjacentElement('afterbegin', chip);
        // Update the attachment section count.
        const countEl = this.querySelector('[data-jira-attach-count]');
        if (countEl) countEl.textContent = (parseInt(countEl.textContent) || 0) + 1;
      } catch (err) {
        this._showErr('comment', `Attach failed: ${err.message || file.name}`);
      }
    }
  }

  async _submitTime() {
    const hrsInput = this.querySelector('[data-jira-time-hrs]');
    const noteInput = this.querySelector('[data-jira-time-note]');
    const hrs = parseFloat(hrsInput?.value);
    if (!hrs || hrs <= 0) return;
    const note = noteInput?.value.trim() || '';

    const btn = this.querySelector('[data-jira-drawer-action="submit-time"]');
    if (btn) { btn.disabled = true; }
    this._clearErr('timelog');

    const origHrs = hrsInput.value;
    const origNote = noteInput.value;
    hrsInput.value = '';
    noteInput.value = '';

    // Optimistic append
    const logContainer = this.querySelector('[data-jira-timelog]');
    const totalEl = this.querySelector('[data-jira-total-hrs]');
    const placeholder = logContainer?.querySelector('.jira-empty-line');
    if (placeholder) placeholder.remove();

    const tempEl = document.createElement('div');
    tempEl.className = 'jira-time-row';
    tempEl.innerHTML = `
      <span class="jira-time-who">Me</span>
      <span class="jira-time-hrs">${_esc(String(hrs))}h</span>
      <span class="jira-time-note">${_esc(note)}</span>
      <span class="jira-time-when">just now</span>
    `;
    logContainer?.appendChild(tempEl);

    if (totalEl) {
      const prev = parseFloat(totalEl.textContent) || 0;
      totalEl.textContent = `${(prev + hrs).toFixed(2).replace(/\.00$/, '')}h`;
    }

    try {
      await addJiraWorklogApi(this._issueKey, hrs, note);
    } catch (err) {
      tempEl.remove();
      hrsInput.value = origHrs;
      noteInput.value = origNote;
      if (totalEl) {
        const cur = parseFloat(totalEl.textContent) || 0;
        totalEl.textContent = `${Math.max(0, cur - hrs).toFixed(2).replace(/\.00$/, '')}h`;
      }
      this._showErr('timelog', err.message || 'Failed to log time');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async _doTransition(targetColKey) {
    if (!this._issue) return;
    const transitions = this._issue.transitions || [];
    const statusNames = this._colStatusMap[targetColKey] || [COL_META[targetColKey]?.label || targetColKey];

    // Find a transition whose `to` name matches one of the target column's status names
    let match = transitions.find(t =>
      statusNames.some(s => s.toLowerCase() === (t.to || t.name || '').toLowerCase())
    );
    // Fallback: match by name directly against col label
    if (!match) {
      const label = COL_META[targetColKey]?.label || targetColKey;
      match = transitions.find(t => (t.name || '').toLowerCase().includes(label.toLowerCase()));
    }

    if (!match) {
      this._showErr('transition', `No transition found to "${COL_META[targetColKey]?.label || targetColKey}"`);
      return;
    }

    // Optimistic: update segment
    const seg = this.querySelector('[data-jira-state-seg]');
    const prevActive = seg?.querySelector('.jira-state-opt.active');
    const prevColKey = prevActive?.dataset.jiraTransitionCol;
    seg?.querySelectorAll('.jira-state-opt').forEach(b => {
      b.classList.toggle('active', b.dataset.jiraTransitionCol === targetColKey);
      b.classList.toggle('saving', b.dataset.jiraTransitionCol === targetColKey);
    });

    try {
      await transitionJiraIssueApi(this._issueKey, match.id);
      // Update internal state
      if (this._issue) this._issue.status = match.to || match.name;
    } catch (err) {
      // Revert
      seg?.querySelectorAll('.jira-state-opt').forEach(b => {
        b.classList.toggle('active', b.dataset.jiraTransitionCol === prevColKey);
      });
      this._showErr('transition', err.message || 'Transition failed');
    } finally {
      seg?.querySelectorAll('.jira-state-opt.saving').forEach(b => b.classList.remove('saving'));
    }
  }

  async _doAssign(accountId, displayName) {
    if (!this._issue) return;
    this._closeAssignMenu();

    const prevId = this._issue.assigneeId || '';
    const prevName = this._issue.assigneeName || 'Unassigned';

    // Optimistic update
    this._issue.assigneeId = accountId;
    this._issue.assigneeName = displayName;
    const nameEl = this.querySelector('[data-jira-assignee-name]');
    const avatarEl = nameEl?.previousElementSibling;
    if (nameEl) nameEl.textContent = displayName;
    if (avatarEl) {
      avatarEl.textContent = _initials(displayName);
      avatarEl.style.background = _avatarColor(displayName);
    }
    this._clearErr('assignee');

    try {
      await changeJiraAssigneeApi(this._issueKey, accountId);
    } catch (err) {
      // Revert
      this._issue.assigneeId = prevId;
      this._issue.assigneeName = prevName;
      if (nameEl) nameEl.textContent = prevName;
      if (avatarEl) {
        avatarEl.textContent = _initials(prevName);
        avatarEl.style.background = _avatarColor(prevName);
      }
      this._showErr('assignee', err.message || 'Reassign failed');
    }
  }

  async _uploadFiles(files) {
    const progress = this.querySelector('[data-jira-attach-progress]');
    const list = this.querySelector('[data-jira-attach-list]');
    const countEl = this.querySelector('[data-jira-attach-count]');
    if (progress) { progress.textContent = `Uploading ${files.length} file(s)…`; progress.classList.remove('hidden'); }
    this._clearErr('attach');

    let uploaded = 0;
    for (const file of files) {
      try {
        const result = await uploadJiraAttachmentApi(this._issueKey, file);
        uploaded++;

        // Append to list
        const uploadedId = result?.id;
        const kc = _kindCls(file.name);
        const ext = _ext(file.name);
        const isImg = kc === 'img';
        const dlHref = uploadedId ? `/api/jira/attachment/${encodeURIComponent(uploadedId)}` : '';
        const el = document.createElement('div');
        el.className = `jira-attach${isImg ? ' jira-attach-image' : ''}`;
        const inlineDlHref = isImg && dlHref ? `${dlHref}?inline=1` : dlHref;
        const thumbHtml = isImg && dlHref
          ? `<a href="${_esc(inlineDlHref)}" target="_blank" rel="noopener noreferrer" class="jira-attach-preview-link"><img class="jira-attach-thumb" src="${_esc(inlineDlHref)}" alt="${_esc(file.name)}" loading="lazy"/></a>`
          : '';
        el.innerHTML = `
          ${thumbHtml}
          <div class="jira-attach-meta">
            <span class="jira-attach-kind ${_esc(kc)}">${_esc(ext)}</span>
            ${dlHref
              ? `<a class="jira-attach-name jira-attach-link" href="${_esc(isImg ? inlineDlHref : dlHref)}" ${isImg ? `target="_blank" rel="noopener noreferrer"` : `download="${_esc(file.name)}"`} title="${_esc(file.name)}">${_esc(file.name)}</a>`
              : `<span class="jira-attach-name" title="${_esc(file.name)}">${_esc(file.name)}</span>`}
            <span class="jira-attach-size">${_esc(_fmtSize(file.size))}</span>
          </div>
        `;
        list?.appendChild(el);
        if (countEl) countEl.textContent = (parseInt(countEl.textContent) || 0) + 1;
        if (progress) progress.textContent = `Uploaded ${uploaded} / ${files.length}`;
      } catch (err) {
        this._showErr('attach', `Failed to upload "${file.name}": ${err.message}`);
      }
    }

    if (progress) {
      progress.textContent = uploaded === files.length ? `${uploaded} file(s) uploaded` : `${uploaded} / ${files.length} uploaded`;
      setTimeout(() => progress.classList.add('hidden'), 3000);
    }
  }

  _setReplyTo(authorName) {
    const ctx = this.querySelector('[data-jira-reply-context]');
    const label = this.querySelector('[data-jira-reply-context-label]');
    const area = this.querySelector('[data-jira-reply]');
    if (ctx) ctx.classList.remove('hidden');
    if (label) label.textContent = `Replying to ${authorName}`;
    if (area) {
      const prefix = `@${authorName} `;
      if (!area.value.startsWith(prefix)) area.value = prefix;
      area.focus();
      area.selectionStart = area.selectionEnd = area.value.length;
    }
  }

  _clearReplyTo() {
    const ctx = this.querySelector('[data-jira-reply-context]');
    if (ctx) ctx.classList.add('hidden');
    const area = this.querySelector('[data-jira-reply]');
    if (area && area.value.startsWith('@')) {
      const afterMention = area.value.replace(/^@\S+\s*/, '');
      area.value = afterMention;
    }
  }

  _toggleAssignMenu() {
    const menu = this.querySelector('[data-jira-assign-menu]');
    if (!menu) return;
    this._assignMenuOpen = !this._assignMenuOpen;
    menu.classList.toggle('hidden', !this._assignMenuOpen);
  }

  _closeAssignMenu() {
    const menu = this.querySelector('[data-jira-assign-menu]');
    if (menu) menu.classList.add('hidden');
    this._assignMenuOpen = false;
  }

  _statusToColKey(status = '') {
    const s = status.toLowerCase();
    for (const [colKey, names] of Object.entries(this._colStatusMap)) {
      if (names.some(n => n.toLowerCase() === s)) return colKey;
    }
    // Fallback heuristics
    if (s.includes('progress')) return 'prog';
    if (s.includes('block')) return 'blocked';
    if (s.includes('review')) return 'review';
    if (s === 'done' || s === 'closed' || s === 'resolved') return 'done';
    return 'todo';
  }

  disconnectedCallback() {
    if (this._ac) { this._ac.abort(); this._ac = null; }
  }
}

if (!customElements.get('artemis-jira-card-drawer')) {
  customElements.define('artemis-jira-card-drawer', ArtemisJiraCardDrawer);
}
