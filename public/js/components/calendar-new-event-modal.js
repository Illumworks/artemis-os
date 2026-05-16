import { createCalendarEventApi, searchContactsApi } from '../core/api.js';

function _esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _fmtDatetimeLocal(d) {
  if (!d) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function _isValidEmail(v) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());
}

class ArtemisCalendarNewEventModal extends HTMLElement {
  constructor() {
    super();
    this._attendees = [];
    this._acSuggestions = [];
    this._acIndex = -1;
    this._acDebounceTimer = null;
    this._onBackdrop = this._onBackdrop.bind(this);
    this._onKey = this._onKey.bind(this);
  }

  connectedCallback() {
    this.addEventListener('click', this._onBackdrop);
  }

  disconnectedCallback() {
    this.removeEventListener('click', this._onBackdrop);
    document.removeEventListener('keydown', this._onKey);
  }

  _onBackdrop(e) {
    if (e.target === this.querySelector('.cal-modal-backdrop')) this.close();
  }

  _onKey(e) {
    if (e.key === 'Escape') this.close();
  }

  open(defaultStart) {
    this._attendees = [];

    // Default start = defaultStart rounded to next 30-min boundary
    const base = defaultStart ? new Date(defaultStart) : new Date();
    base.setSeconds(0, 0);
    const rem = base.getMinutes() % 30;
    if (rem !== 0) base.setMinutes(base.getMinutes() + (30 - rem));
    const end = new Date(base.getTime() + 30 * 60 * 1000);

    this._render(_fmtDatetimeLocal(base), _fmtDatetimeLocal(end));
    document.addEventListener('keydown', this._onKey);

    // Focus title after render
    requestAnimationFrame(() => this.querySelector('#cnm-title')?.focus());
  }

  close() {
    this.innerHTML = '';
    this._attendees = [];
    document.removeEventListener('keydown', this._onKey);
  }

  _render(defaultStart = '', defaultEnd = '') {
    this.innerHTML = `
      <div class="cal-modal-backdrop">
        <div class="modal cal-new-event-modal" role="dialog" aria-modal="true" aria-labelledby="cnm-heading">
          <div class="modal-header">
            <div>
              <div style="font-size:10.5px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:var(--ink-4);margin-bottom:2px">Calendar</div>
              <h3 id="cnm-heading" style="margin:0;font-size:17px;font-weight:600">New Event</h3>
            </div>
            <button class="modal-close" data-cnm-close aria-label="Close">&times;</button>
          </div>

          <div class="modal-body">
            <div class="cnm-field">
              <label class="cnm-label" for="cnm-title">Title <span class="cnm-req">*</span></label>
              <input id="cnm-title" class="cal-field-input" type="text"
                     placeholder="Event title" autocomplete="off" required>
            </div>

            <div class="cnm-row">
              <div class="cnm-field">
                <label class="cnm-label" for="cnm-start">Start <span class="cnm-req">*</span></label>
                <input id="cnm-start" class="cal-field-input" type="datetime-local"
                       value="${_esc(defaultStart)}" required>
              </div>
              <div class="cnm-field">
                <label class="cnm-label" for="cnm-end">End <span class="cnm-req">*</span></label>
                <input id="cnm-end" class="cal-field-input" type="datetime-local"
                       value="${_esc(defaultEnd)}" required>
              </div>
            </div>

            <div class="cnm-field">
              <label class="cnm-label" for="cnm-location">Location</label>
              <input id="cnm-location" class="cal-field-input" type="text"
                     placeholder="Add location" autocomplete="off">
            </div>

            <div class="cnm-field">
              <label class="cnm-label">Attendees</label>
              <div class="cal-chip-row" id="cnm-chips"></div>
              <div style="position:relative">
                <div style="display:flex;gap:6px;margin-top:6px">
                  <input id="cnm-attendee-input" class="cal-field-input" type="text"
                         placeholder="name@example.com" autocomplete="off"
                         role="combobox" aria-autocomplete="list" aria-expanded="false"
                         aria-controls="cnm-ac-list" aria-haspopup="listbox"
                         style="flex:1">
                  <button type="button" id="cnm-attendee-add" class="btn btn-outline btn-sm">Add</button>
                </div>
                <ul id="cnm-ac-list" role="listbox"
                    style="display:none;position:absolute;left:0;right:0;top:calc(100% + 2px);z-index:9999;
                           list-style:none;margin:0;padding:4px 0;
                           background:var(--surface-2,#1e1e2e);border:1px solid var(--border,#333);
                           border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.4);max-height:220px;overflow-y:auto"></ul>
              </div>
              <div id="cnm-attendee-err" style="font-size:11.5px;color:var(--danger);margin-top:4px;display:none"></div>
            </div>

            <div class="cnm-field">
              <label class="cnm-label" for="cnm-description">Description</label>
              <textarea id="cnm-description" class="cal-field-textarea" rows="3"
                        placeholder="Add description"></textarea>
            </div>

            <label class="cnm-notify-row">
              <input type="checkbox" id="cnm-no-notify">
              <span style="font-size:12.5px;color:var(--ink-3)">Don't notify attendees</span>
            </label>

            <div id="cnm-err" class="cal-field-err" hidden></div>
          </div>

          <div class="modal-footer">
            <button type="button" class="btn btn-outline btn-sm" data-cnm-close>Cancel</button>
            <button type="button" id="cnm-submit" class="btn btn-ink btn-sm">Create event</button>
          </div>
        </div>
      </div>
    `;

    this._wireChips();
    this._wireForm();
  }

  _renderChips() {
    const row = this.querySelector('#cnm-chips');
    if (!row) return;
    row.innerHTML = this._attendees.map((email, i) => `
      <span class="cal-chip">
        ${_esc(email)}
        <button type="button" class="cal-chip-remove" data-cnm-remove="${i}" aria-label="Remove ${_esc(email)}">&times;</button>
      </span>
    `).join('');
    row.querySelectorAll('[data-cnm-remove]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.dataset.cnmRemove);
        this._attendees.splice(idx, 1);
        this._renderChips();
      });
    });
  }

  _wireChips() {
    const input = this.querySelector('#cnm-attendee-input');
    const addBtn = this.querySelector('#cnm-attendee-add');
    const errEl = this.querySelector('#cnm-attendee-err');
    const list = this.querySelector('#cnm-ac-list');

    const closeDropdown = () => {
      this._acSuggestions = [];
      this._acIndex = -1;
      list.style.display = 'none';
      list.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
    };

    const selectSuggestion = (contact) => {
      input.value = contact.email;
      closeDropdown();
      tryAdd();
    };

    const renderDropdown = (contacts) => {
      this._acSuggestions = contacts;
      this._acIndex = -1;
      if (!contacts.length) { closeDropdown(); return; }
      list.innerHTML = contacts.map((c, i) => {
        const label = c.name ? `${_esc(c.name)} &lt;${_esc(c.email)}&gt;` : _esc(c.email);
        return `<li role="option" data-ac-idx="${i}"
                    style="padding:7px 12px;cursor:pointer;font-size:13px;line-height:1.4;
                           color:var(--ink-1,#e2e8f0);white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
                    aria-selected="false">${label}</li>`;
      }).join('');
      list.style.display = '';
      input.setAttribute('aria-expanded', 'true');
      list.querySelectorAll('li').forEach((li) => {
        li.addEventListener('mousedown', (e) => {
          e.preventDefault();
          const idx = Number(li.dataset.acIdx);
          selectSuggestion(this._acSuggestions[idx]);
        });
        li.addEventListener('mouseover', () => {
          list.querySelectorAll('li').forEach((el) => { el.style.background = ''; el.setAttribute('aria-selected', 'false'); });
          li.style.background = 'var(--surface-3,#2a2a3e)';
          li.setAttribute('aria-selected', 'true');
          this._acIndex = Number(li.dataset.acIdx);
        });
      });
    };

    const fetchSuggestions = async (q) => {
      if (!q || q.length < 2) { closeDropdown(); return; }
      try {
        const { contacts } = await searchContactsApi(q);
        if (input.value.trim() !== q) return;
        const filtered = (contacts || []).filter((c) => !this._attendees.includes(c.email));
        renderDropdown(filtered);
      } catch {}
    };

    const tryAdd = () => {
      const val = input.value.trim();
      errEl.style.display = 'none';
      closeDropdown();
      if (!val) return;
      if (!_isValidEmail(val)) {
        errEl.textContent = 'Enter a valid email address.';
        errEl.style.display = '';
        return;
      }
      if (this._attendees.includes(val)) {
        errEl.textContent = 'Already added.';
        errEl.style.display = '';
        return;
      }
      this._attendees.push(val);
      input.value = '';
      this._renderChips();
    };

    addBtn.addEventListener('click', tryAdd);

    input.addEventListener('input', () => {
      clearTimeout(this._acDebounceTimer);
      const q = input.value.trim();
      if (!q || q.length < 2) { closeDropdown(); return; }
      this._acDebounceTimer = setTimeout(() => fetchSuggestions(q), 250);
    });

    input.addEventListener('keydown', (e) => {
      const items = list.querySelectorAll('li');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (!items.length) return;
        items.forEach((el) => { el.style.background = ''; el.setAttribute('aria-selected', 'false'); });
        this._acIndex = (this._acIndex + 1) % items.length;
        items[this._acIndex].style.background = 'var(--surface-3,#2a2a3e)';
        items[this._acIndex].setAttribute('aria-selected', 'true');
        items[this._acIndex].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (!items.length) return;
        items.forEach((el) => { el.style.background = ''; el.setAttribute('aria-selected', 'false'); });
        this._acIndex = (this._acIndex - 1 + items.length) % items.length;
        items[this._acIndex].style.background = 'var(--surface-3,#2a2a3e)';
        items[this._acIndex].setAttribute('aria-selected', 'true');
        items[this._acIndex].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (this._acIndex >= 0 && this._acSuggestions[this._acIndex]) {
          selectSuggestion(this._acSuggestions[this._acIndex]);
        } else {
          tryAdd();
        }
      } else if (e.key === 'Escape') {
        if (list.style.display !== 'none') {
          e.stopPropagation();
          closeDropdown();
        }
      }
    });

    input.addEventListener('blur', () => {
      setTimeout(() => closeDropdown(), 150);
    });
  }

  _wireForm() {
    this.querySelector('[data-cnm-close]')?.addEventListener('click', () => this.close());
    // Second close button (Cancel)
    this.querySelectorAll('[data-cnm-close]').forEach((btn) =>
      btn.addEventListener('click', () => this.close())
    );

    this.querySelector('#cnm-submit').addEventListener('click', () => this._submit());
  }

  async _submit() {
    const titleEl    = this.querySelector('#cnm-title');
    const startEl    = this.querySelector('#cnm-start');
    const endEl      = this.querySelector('#cnm-end');
    const locationEl = this.querySelector('#cnm-location');
    const descEl     = this.querySelector('#cnm-description');
    const noNotify   = this.querySelector('#cnm-no-notify');
    const submitBtn  = this.querySelector('#cnm-submit');
    const errEl      = this.querySelector('#cnm-err');

    errEl.hidden = true;

    // Validate required fields
    if (!titleEl.value.trim()) {
      errEl.textContent = 'Title is required.';
      errEl.hidden = false;
      titleEl.focus();
      return;
    }
    if (!startEl.value) {
      errEl.textContent = 'Start time is required.';
      errEl.hidden = false;
      startEl.focus();
      return;
    }
    if (!endEl.value) {
      errEl.textContent = 'End time is required.';
      errEl.hidden = false;
      endEl.focus();
      return;
    }

    const startDate = new Date(startEl.value);
    const endDate   = new Date(endEl.value);
    if (isNaN(startDate.getTime()) || isNaN(endDate.getTime())) {
      errEl.textContent = 'Invalid date/time values.';
      errEl.hidden = false;
      return;
    }
    if (endDate <= startDate) {
      errEl.textContent = 'End time must be after start time.';
      errEl.hidden = false;
      endEl.focus();
      return;
    }

    const payload = {
      summary: titleEl.value.trim(),
      start: { dateTime: startDate.toISOString() },
      end:   { dateTime: endDate.toISOString() },
      sendUpdates: noNotify.checked ? 'none' : 'all',
    };
    if (locationEl.value.trim()) payload.location = locationEl.value.trim();
    if (descEl.value.trim())     payload.description = descEl.value.trim();
    if (this._attendees.length)  payload.attendees = this._attendees.map((e) => ({ email: e }));

    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating…';

    try {
      const created = await createCalendarEventApi(payload);
      this.dispatchEvent(new CustomEvent('created', { bubbles: true, detail: { event: created } }));
      this.close();
    } catch (err) {
      errEl.textContent = err.message || 'Failed to create event.';
      errEl.hidden = false;
      submitBtn.disabled = false;
      submitBtn.textContent = 'Create event';
    }
  }
}

customElements.define('artemis-calendar-new-event-modal', ArtemisCalendarNewEventModal);
