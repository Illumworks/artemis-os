import { createJiraIssueApi, fetchJiraAssignableUsersApi } from '../core/api.js';

const PRIORITY_OPTIONS = ['Highest', 'High', 'Medium', 'Low', 'Lowest'];

function escapeHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

class JiraNewIssueModal extends HTMLElement {
  constructor() {
    super();
    this._projectKey = '';
    this._assignableUsers = [];
    this._onBackdropClick = this._onBackdropClick.bind(this);
  }

  connectedCallback() {
    this.addEventListener('click', this._onBackdropClick);
  }

  disconnectedCallback() {
    this.removeEventListener('click', this._onBackdropClick);
  }

  _onBackdropClick(e) {
    if (e.target === this.querySelector('.jira-modal-backdrop')) this.close();
  }

  async open(projectKey, _colStatusMap) {
    this._projectKey = projectKey || '';
    this._assignableUsers = [];
    this._render();

    // Fetch assignable users in background
    if (projectKey) {
      try {
        this._assignableUsers = await fetchJiraAssignableUsersApi(projectKey);
        this._updateAssigneeOptions();
      } catch {
        // Non-fatal — user can still submit without assignee
      }
    }
  }

  close() {
    this.innerHTML = '';
  }

  _render() {
    this.innerHTML = `
      <div class="jira-modal-backdrop">
        <div class="jira-modal" role="dialog" aria-modal="true" aria-labelledby="jira-modal-title">
          <div class="jira-modal-head">
            <span id="jira-modal-title" class="jira-modal-title">New Issue</span>
            <button class="jira-modal-close" type="button" aria-label="Close">&#x2715;</button>
          </div>
          <form class="jira-modal-form" novalidate>
            <div class="jira-modal-field">
              <label class="jira-modal-label" for="jni-summary">Title <span class="jira-modal-req">*</span></label>
              <input id="jni-summary" class="jira-modal-input" type="text" placeholder="Issue summary" autocomplete="off" required>
            </div>
            <div class="jira-modal-field">
              <label class="jira-modal-label" for="jni-description">Description</label>
              <textarea id="jni-description" class="jira-modal-textarea" rows="4" placeholder="What needs to be done?"></textarea>
            </div>
            <div class="jira-modal-row">
              <div class="jira-modal-field">
                <label class="jira-modal-label" for="jni-assignee">Assignee</label>
                <select id="jni-assignee" class="jira-modal-select">
                  <option value="">Unassigned</option>
                </select>
              </div>
              <div class="jira-modal-field">
                <label class="jira-modal-label" for="jni-priority">Priority</label>
                <select id="jni-priority" class="jira-modal-select">
                  ${PRIORITY_OPTIONS.map(p => `<option value="${escapeHtml(p)}"${p === 'Medium' ? ' selected' : ''}>${escapeHtml(p)}</option>`).join('')}
                </select>
              </div>
            </div>
            <div class="jira-modal-field">
              <label class="jira-modal-label" for="jni-labels">Labels <span class="jira-modal-hint">(comma-separated)</span></label>
              <input id="jni-labels" class="jira-modal-input" type="text" placeholder="e.g. frontend, bug">
            </div>
            <div class="jira-modal-err" hidden></div>
            <div class="jira-modal-foot">
              <button type="button" class="btn btn-outline btn-sm jira-modal-cancel">Cancel</button>
              <button type="submit" class="btn btn-ink btn-sm jira-modal-submit">Create issue</button>
            </div>
          </form>
        </div>
      </div>
    `;

    this.querySelector('.jira-modal-close').addEventListener('click', () => this.close());
    this.querySelector('.jira-modal-cancel').addEventListener('click', () => this.close());
    this.querySelector('.jira-modal-form').addEventListener('submit', (e) => this._handleSubmit(e));

    // Focus summary input
    requestAnimationFrame(() => this.querySelector('#jni-summary')?.focus());
  }

  _updateAssigneeOptions() {
    const select = this.querySelector('#jni-assignee');
    if (!select) return;
    const currentVal = select.value;
    select.innerHTML = `<option value="">Unassigned</option>` +
      this._assignableUsers.map(u =>
        `<option value="${escapeHtml(u.accountId)}">${escapeHtml(u.displayName)}</option>`
      ).join('');
    if (currentVal) select.value = currentVal;
  }

  async _handleSubmit(e) {
    e.preventDefault();
    const summary = this.querySelector('#jni-summary')?.value.trim() || '';
    if (!summary) {
      this._showErr('Title is required.');
      this.querySelector('#jni-summary')?.focus();
      return;
    }

    const description = this.querySelector('#jni-description')?.value.trim() || '';
    const assigneeAccountId = this.querySelector('#jni-assignee')?.value || null;
    const priorityName = this.querySelector('#jni-priority')?.value || 'Medium';
    const labelsRaw = this.querySelector('#jni-labels')?.value || '';
    const labels = labelsRaw.split(',').map(l => l.trim()).filter(Boolean);

    const submitBtn = this.querySelector('.jira-modal-submit');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating…';
    this._clearErr();

    try {
      const result = await createJiraIssueApi({
        projectKey: this._projectKey,
        summary,
        description,
        assigneeAccountId: assigneeAccountId || null,
        priorityName,
        labels,
      });
      this.dispatchEvent(new CustomEvent('jira-issue-created', {
        bubbles: true,
        detail: { key: result.key },
      }));
      this.close();
    } catch (err) {
      this._showErr(err.message || 'Failed to create issue. Please try again.');
      submitBtn.disabled = false;
      submitBtn.textContent = 'Create issue';
    }
  }

  _showErr(msg) {
    const el = this.querySelector('.jira-modal-err');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
  }

  _clearErr() {
    const el = this.querySelector('.jira-modal-err');
    if (el) { el.textContent = ''; el.hidden = true; }
  }
}

if (!customElements.get('artemis-jira-new-issue-modal')) {
  customElements.define('artemis-jira-new-issue-modal', JiraNewIssueModal);
}
