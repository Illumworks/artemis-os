/**
 * Web Components for meeting action routing pickers.
 *
 * Four components (light DOM, modal overlay pattern):
 *   artemis-meeting-jira-picker  — confirm + create Jira issue
 *   artemis-meeting-okr-picker   — pick a KR then confirm
 *   artemis-meeting-slack-picker — pick when to send reminder
 *   artemis-meeting-todo-confirm — immediate confirm (no extra input)
 *
 * Pattern mirrors artemis-orchestrate-modal: connectedCallback renders
 * innerHTML; logic wired in connectedCallback via querySelector.
 */

import { _addPillToRow } from '../features/meetings.js';

// ── shared modal helpers ───────────────────────────────────────────────────

function _overlay(id, bodyHtml) {
  return `
    <div id="${id}" class="modal-overlay hidden">
      <div class="modal agent-form-modal">
        ${bodyHtml}
      </div>
    </div>
  `;
}

function _postAction(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((r) => r.json());
}

// ── Jira picker ────────────────────────────────────────────────────────────

class ArtemisMeetingJiraPicker extends HTMLElement {
  connectedCallback() {
    this.innerHTML = _overlay('meeting-jira-modal', `
      <div class="modal-header">
        <h3>Convert to Jira issue</h3>
        <button id="jira-picker-close" class="modal-close">&times;</button>
      </div>
      <div class="af-section">
        <div class="af-section-label">Action item</div>
        <div id="jira-picker-text" class="page-section-footnote" style="margin:0 0 12px"></div>
        <div class="af-section-label">Project</div>
        <input id="jira-picker-project" class="meetings-ask-input" value="MT" style="margin-bottom:12px;width:80px" />
      </div>
      <div class="modal-actions">
        <button type="button" id="jira-picker-cancel" class="modal-btn-cancel">Cancel</button>
        <button type="button" id="jira-picker-confirm" class="modal-btn-save">Create issue</button>
      </div>
      <div id="jira-picker-status" style="margin-top:8px;font-size:13px"></div>
    `);

    this._overlay = this.querySelector('#meeting-jira-modal');
    this._textEl = this.querySelector('#jira-picker-text');
    this._projectInput = this.querySelector('#jira-picker-project');
    this._status = this.querySelector('#jira-picker-status');
    this._confirmBtn = this.querySelector('#jira-picker-confirm');

    this.querySelector('#jira-picker-close').addEventListener('click', () => this._close());
    this.querySelector('#jira-picker-cancel').addEventListener('click', () => this._close());
    this._overlay.addEventListener('click', (e) => { if (e.target === this._overlay) this._close(); });

    this._confirmBtn.addEventListener('click', () => this._confirm());
  }

  open(actionText, meetingId, rowEl) {
    this._actionText = actionText;
    this._meetingId = meetingId;
    this._rowEl = rowEl;
    this._textEl.textContent = actionText;
    this._status.textContent = '';
    this._overlay.classList.remove('hidden');
  }

  _close() {
    this._overlay.classList.add('hidden');
  }

  async _confirm() {
    this._confirmBtn.disabled = true;
    this._status.textContent = 'Creating…';
    try {
      const data = await _postAction(
        `/api/meetings/${encodeURIComponent(this._meetingId)}/actions/jira`,
        { action_text: this._actionText }
      );
      if (data.ok) {
        this._status.innerHTML = `Done: <a href="${data.url}" target="_blank" rel="noopener">${data.key}</a>`;
        _addPillToRow(this._rowEl, {
          routed_to: 'jira',
          action_text: this._actionText,
          target_id: data.key,
          target_url: data.url,
        });
        setTimeout(() => this._close(), 1500);
      } else {
        this._status.textContent = data.error || 'Failed.';
      }
    } catch (err) {
      this._status.textContent = 'Request failed.';
    } finally {
      this._confirmBtn.disabled = false;
    }
  }
}

customElements.define('artemis-meeting-jira-picker', ArtemisMeetingJiraPicker);

// ── OKR picker ─────────────────────────────────────────────────────────────

class ArtemisMeetingOkrPicker extends HTMLElement {
  connectedCallback() {
    this.innerHTML = _overlay('meeting-okr-modal', `
      <div class="modal-header">
        <h3>Update OKR key result</h3>
        <button id="okr-picker-close" class="modal-close">&times;</button>
      </div>
      <div class="af-section">
        <div class="af-section-label">Action item</div>
        <div id="okr-picker-text" class="page-section-footnote" style="margin:0 0 12px"></div>
        <div class="af-section-label">Key result</div>
        <select id="okr-picker-kr" style="width:100%;padding:6px 8px;border-radius:6px;border:1px solid var(--surface-outline);background:var(--surface);color:var(--text);font-size:13px;margin-bottom:4px">
          <option value="">Loading key results…</option>
        </select>
      </div>
      <div class="modal-actions">
        <button type="button" id="okr-picker-cancel" class="modal-btn-cancel">Cancel</button>
        <button type="button" id="okr-picker-confirm" class="modal-btn-save">Add evidence</button>
      </div>
      <div id="okr-picker-status" style="margin-top:8px;font-size:13px"></div>
    `);

    this._overlay = this.querySelector('#meeting-okr-modal');
    this._textEl = this.querySelector('#okr-picker-text');
    this._krSelect = this.querySelector('#okr-picker-kr');
    this._status = this.querySelector('#okr-picker-status');
    this._confirmBtn = this.querySelector('#okr-picker-confirm');

    this.querySelector('#okr-picker-close').addEventListener('click', () => this._close());
    this.querySelector('#okr-picker-cancel').addEventListener('click', () => this._close());
    this._overlay.addEventListener('click', (e) => { if (e.target === this._overlay) this._close(); });
    this._confirmBtn.addEventListener('click', () => this._confirm());
  }

  async open(actionText, meetingId, rowEl) {
    this._actionText = actionText;
    this._meetingId = meetingId;
    this._rowEl = rowEl;
    this._textEl.textContent = actionText;
    this._status.textContent = '';
    this._overlay.classList.remove('hidden');
    await this._loadKrs();
  }

  _close() {
    this._overlay.classList.add('hidden');
  }

  async _loadKrs() {
    this._krSelect.innerHTML = '<option value="">Loading…</option>';
    try {
      const data = await fetch('/api/okr/overview').then((r) => r.json());
      const krs = [];
      for (const obj of (data.objectives || [])) {
        for (const kr of (obj.krs || obj.keyResults || obj.key_results || [])) {
          krs.push({ id: kr.id, label: `${obj.title} — ${kr.title}` });
        }
      }
      if (krs.length) {
        this._krSelect.innerHTML = krs
          .map((kr) => `<option value="${kr.id}">${kr.label}</option>`)
          .join('');
      } else {
        this._krSelect.innerHTML = '<option value="">No key results found</option>';
      }
    } catch {
      this._krSelect.innerHTML = '<option value="">Failed to load KRs</option>';
    }
  }

  async _confirm() {
    const krId = this._krSelect.value;
    if (!krId) {
      this._status.textContent = 'Select a key result first.';
      return;
    }
    this._confirmBtn.disabled = true;
    this._status.textContent = 'Adding evidence…';
    try {
      const data = await _postAction(
        `/api/meetings/${encodeURIComponent(this._meetingId)}/actions/okr`,
        { action_text: this._actionText, kr_id: parseInt(krId, 10) }
      );
      if (data.ok) {
        this._status.textContent = 'Evidence added.';
        _addPillToRow(this._rowEl, {
          routed_to: 'okr',
          action_text: this._actionText,
          target_id: String(data.kr_id),
          target_url: null,
        });
        setTimeout(() => this._close(), 1200);
      } else {
        this._status.textContent = data.error || 'Failed.';
      }
    } catch {
      this._status.textContent = 'Request failed.';
    } finally {
      this._confirmBtn.disabled = false;
    }
  }
}

customElements.define('artemis-meeting-okr-picker', ArtemisMeetingOkrPicker);

// ── Slack picker ────────────────────────────────────────────────────────────

class ArtemisMeetingSlackPicker extends HTMLElement {
  connectedCallback() {
    const now = new Date();
    const plus1h = new Date(now.getTime() + 3600_000);
    const tomorrow9 = new Date(now);
    tomorrow9.setDate(tomorrow9.getDate() + 1);
    tomorrow9.setHours(9, 0, 0, 0);

    const fmtIso = (d) => d.toISOString().slice(0, 16);
    const fmtLabel = (d) => d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

    this.innerHTML = _overlay('meeting-slack-modal', `
      <div class="modal-header">
        <h3>Schedule Slack reminder</h3>
        <button id="slack-picker-close" class="modal-close">&times;</button>
      </div>
      <div class="af-section">
        <div class="af-section-label">Action item</div>
        <div id="slack-picker-text" class="page-section-footnote" style="margin:0 0 12px"></div>
        <div class="af-section-label">When</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <button type="button" class="shell-action-btn slack-when-preset" data-when="${fmtIso(plus1h)}">+1 hour (${fmtLabel(plus1h)})</button>
          <button type="button" class="shell-action-btn slack-when-preset" data-when="${fmtIso(tomorrow9)}">Tomorrow 9am</button>
        </div>
        <input type="datetime-local" id="slack-picker-when" value="${fmtIso(plus1h)}"
               style="width:100%;padding:6px 8px;border-radius:6px;border:1px solid var(--surface-outline);background:var(--surface);color:var(--text);font-size:13px" />
      </div>
      <div class="modal-actions">
        <button type="button" id="slack-picker-cancel" class="modal-btn-cancel">Cancel</button>
        <button type="button" id="slack-picker-confirm" class="modal-btn-save">Send reminder</button>
      </div>
      <div id="slack-picker-status" style="margin-top:8px;font-size:13px"></div>
    `);

    this._overlay = this.querySelector('#meeting-slack-modal');
    this._textEl = this.querySelector('#slack-picker-text');
    this._whenInput = this.querySelector('#slack-picker-when');
    this._status = this.querySelector('#slack-picker-status');
    this._confirmBtn = this.querySelector('#slack-picker-confirm');

    this.querySelector('#slack-picker-close').addEventListener('click', () => this._close());
    this.querySelector('#slack-picker-cancel').addEventListener('click', () => this._close());
    this._overlay.addEventListener('click', (e) => { if (e.target === this._overlay) this._close(); });
    this._confirmBtn.addEventListener('click', () => this._confirm());
    this.querySelectorAll('.slack-when-preset').forEach((btn) => {
      btn.addEventListener('click', () => { this._whenInput.value = btn.dataset.when; });
    });
  }

  open(actionText, meetingId, rowEl) {
    this._actionText = actionText;
    this._meetingId = meetingId;
    this._rowEl = rowEl;
    this._textEl.textContent = actionText;
    this._status.textContent = '';
    this._overlay.classList.remove('hidden');
  }

  _close() {
    this._overlay.classList.add('hidden');
  }

  async _confirm() {
    this._confirmBtn.disabled = true;
    this._status.textContent = 'Sending…';
    try {
      const data = await _postAction(
        `/api/meetings/${encodeURIComponent(this._meetingId)}/actions/slack`,
        { action_text: this._actionText, when: this._whenInput.value }
      );
      if (data.ok) {
        const label = data.when || 'sent';
        this._status.textContent = `Reminder sent (${label}).`;
        _addPillToRow(this._rowEl, {
          routed_to: 'slack',
          action_text: this._actionText,
          target_id: null,
          target_url: null,
        });
        setTimeout(() => this._close(), 1200);
      } else {
        this._status.textContent = data.error || 'Failed.';
      }
    } catch {
      this._status.textContent = 'Request failed.';
    } finally {
      this._confirmBtn.disabled = false;
    }
  }
}

customElements.define('artemis-meeting-slack-picker', ArtemisMeetingSlackPicker);
