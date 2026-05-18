import {
  fetchCalendarEventApi,
  updateCalendarEventApi,
  deleteCalendarEventApi,
  respondToCalendarEventApi,
} from '../core/api.js';

// ---------------------------------------------------------------------------
// People search autocomplete helpers
// ---------------------------------------------------------------------------

let _peopleDebounceTimer = null;

/**
 * Fetch people matching `q` from /api/people/search.
 * Returns [] on any network / parse error so the UI never crashes.
 */
async function _searchPeople(q, limit = 8) {
  if (!q || q.trim().length < 2) return [];
  try {
    const params = new URLSearchParams({ q: q.trim(), limit: String(limit) });
    const resp = await fetch(`/api/people/search?${params}`);
    if (!resp.ok) return [];
    return await resp.json();
  } catch {
    return [];
  }
}

/**
 * Render a source pill string.
 */
function _sourcePill(source) {
  const labels = { gcal: 'Google', slack: 'Slack', both: 'Both' };
  const colors = { gcal: '#4285F4', slack: '#611f69', both: '#1a7f5a' };
  const label = labels[source] || source;
  const color = colors[source] || '#666';
  return `<span style="font-size:9.5px;padding:1px 5px;border-radius:9px;background:${color}18;color:${color};font-weight:600;letter-spacing:.3px;flex-shrink:0">${_esc(label)}</span>`;
}

function _isValidEmail(v) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());
}

function _esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _fmtDatetimeLocal(isoOrDate) {
  if (!isoOrDate) return '';
  const d = new Date(isoOrDate);
  if (isNaN(d.getTime())) return '';
  // datetime-local value format: YYYY-MM-DDTHH:MM
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const RSVP_LABELS = { accepted: 'Accepted', declined: 'Declined', tentative: 'Tentative', needsAction: 'Invited' };
const RSVP_CLS   = { accepted: 'sage', declined: 'rust', tentative: 'amber', needsAction: 'slate' };

class ArtemisCalendarEventDrawer extends HTMLElement {
  connectedCallback() {
    this._eventId = null;
    this._event = null;
    this._dirty = {};
    this._attendees = [];
    this.innerHTML = '';
  }

  async open(eventId) {
    this._eventId = eventId;
    this._event = null;
    this._dirty = {};
    this._attendees = [];
    this._render({ loading: true });

    try {
      this._event = await fetchCalendarEventApi(eventId);
      this._render({ loading: false });
    } catch (err) {
      this._render({ loading: false, error: err.message || 'Failed to load event' });
    }
  }

  close() {
    this.innerHTML = '';
    this._eventId = null;
    this._event = null;
    this._dirty = {};
  }

  _render({ loading = false, error = null } = {}) {
    const ev = this._event;

    // Create outer shell once — the CSS entrance animation fires here, on the
    // first call only.  Subsequent renders (loading → loaded) update the head
    // and body in-place so the animation does not restart and cause a flicker.
    if (!this.querySelector('.drawer')) {
      this.innerHTML = `
        <div class="drawer-backdrop" data-cal-drawer-backdrop></div>
        <div class="drawer" role="dialog" aria-label="Event details">
          <div class="drawer-head" id="cal-drawer-head"></div>
          <div id="cal-drawer-content"></div>
        </div>
      `;
      this.querySelector('[data-cal-drawer-backdrop]')
        ?.addEventListener('click', () => this.close());
    }

    // Update head in-place (title is only available after the event loads)
    const headEl = this.querySelector('#cal-drawer-head');
    if (headEl) {
      headEl.innerHTML = `
        <div>
          <div class="drawer-section-label">Calendar Event</div>
          ${ev ? `<h2 style="font-size:17px;font-weight:600;margin:4px 0 0;line-height:1.3">${_esc(ev.summary || ev.title || 'Untitled event')}</h2>` : ''}
        </div>
        <button class="drawer-close" data-cal-drawer-close aria-label="Close">✕</button>
      `;
      headEl.querySelector('[data-cal-drawer-close]')
        ?.addEventListener('click', () => this.close());
    }

    // Update body content in-place — no animation re-trigger
    const contentEl = this.querySelector('#cal-drawer-content');
    if (contentEl) {
      contentEl.innerHTML = loading
        ? `<div class="drawer-body"><p style="color:var(--ink-3)">Loading event…</p></div>`
        : error
          ? `<div class="drawer-body"><p style="color:var(--danger)">${_esc(error)}</p></div>`
          : this._renderBody(ev);
    }

    if (!loading && !error && ev) {
      this._wireForm();
    }
  }

  _renderRsvpStrip(ev) {
    const self = Array.isArray(ev.attendees) && ev.attendees.find((a) => a.self);
    if (!self) return '';
    const cur = self.responseStatus || 'needsAction';
    const options = [
      { value: 'accepted',  label: 'Accept',   cls: 'sage'  },
      { value: 'declined',  label: 'Decline',  cls: 'rust'  },
      { value: 'tentative', label: 'Tentative', cls: 'amber' },
    ];
    const btns = options.map(({ value, label, cls }) => {
      const active = cur === value ? ` cal-rsvp-btn--active cal-rsvp-${cls}` : '';
      return `<button type="button" class="cal-rsvp-btn${active}" data-ced-rsvp="${_esc(value)}">${_esc(label)}</button>`;
    }).join('');
    return `
      <div class="cal-rsvp-strip" id="ced-rsvp-strip">
        <span class="cal-rsvp-strip-label">Your RSVP</span>
        ${btns}
        <span class="cal-rsvp-strip-err" id="ced-rsvp-err" hidden></span>
      </div>
    `;
  }

  _renderBody(ev) {
    if (!ev) return '<div class="drawer-body"></div>';

    // Google raw event shape: summary, start.dateTime, end.dateTime, location, description, attendees
    const startISO = ev.start?.dateTime || ev.start?.date || '';
    const endISO   = ev.end?.dateTime   || ev.end?.date   || '';

    const attendeeRows = Array.isArray(ev.attendees) && ev.attendees.length
      ? ev.attendees.map((a) => {
          const status = a.responseStatus || 'needsAction';
          const label = RSVP_LABELS[status] || status;
          const cls = RSVP_CLS[status] || 'slate';
          const removeBtn = !a.self
            ? `<button type="button" class="cal-chip-remove" data-ced-remove-attendee="${_esc(a.email || '')}" aria-label="Remove ${_esc(a.email || '')}" title="Remove attendee">&times;</button>`
            : '';
          return `
            <div class="cal-attendee-row" data-ced-attendee="${_esc(a.email || '')}">
              <span class="cal-attendee-name">${_esc(a.displayName || a.email || '')}</span>
              <span class="cal-attendee-email">${_esc(a.email || '')}</span>
              <span class="cal-rsvp-badge cal-rsvp-${cls}">${_esc(label)}</span>
              ${removeBtn}
            </div>
          `;
        }).join('')
      : '<p style="font-size:12.5px;color:var(--ink-4)" id="ced-no-attendees">No attendees</p>';

    return `
      <div class="drawer-body">
        ${this._renderRsvpStrip(ev)}
        <div class="drawer-section">
          <div class="drawer-section-label">Title</div>
          <input id="ced-title" class="cal-field-input" type="text"
                 value="${_esc(ev.summary || ev.title || '')}"
                 placeholder="Event title" autocomplete="off">
        </div>

        <div class="drawer-section">
          <div class="drawer-section-label">Time</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
            <div>
              <label style="font-size:10.5px;color:var(--ink-4);display:block;margin-bottom:3px">Start</label>
              <input id="ced-start" class="cal-field-input" type="datetime-local"
                     value="${_esc(_fmtDatetimeLocal(startISO))}">
            </div>
            <div>
              <label style="font-size:10.5px;color:var(--ink-4);display:block;margin-bottom:3px">End</label>
              <input id="ced-end" class="cal-field-input" type="datetime-local"
                     value="${_esc(_fmtDatetimeLocal(endISO))}">
            </div>
          </div>
        </div>

        <div class="drawer-section">
          <div class="drawer-section-label">Location</div>
          <input id="ced-location" class="cal-field-input" type="text"
                 value="${_esc(ev.location || '')}"
                 placeholder="Add location">
        </div>

        <div class="drawer-section">
          <div class="drawer-section-label">Description</div>
          <textarea id="ced-description" class="cal-field-textarea" rows="4"
                    placeholder="Add description">${_esc(ev.description || '')}</textarea>
        </div>

        <div class="drawer-section">
          <div class="drawer-section-label">Attendees (<span id="ced-attendee-count">${Array.isArray(ev.attendees) ? ev.attendees.length : 0}</span>)</div>
          <div class="cal-attendees-list" id="ced-attendees-list">${attendeeRows}</div>
          <div style="display:flex;gap:6px;margin-top:8px;position:relative">
            <div style="flex:1;position:relative">
              <input id="ced-attendee-input" class="cal-field-input" type="text"
                     placeholder="Name or email…" autocomplete="off" name="attendee_email" style="width:100%;box-sizing:border-box">
              <ul id="ced-people-dropdown"
                  role="listbox"
                  style="display:none;position:absolute;top:calc(100% + 3px);left:0;right:0;
                         margin:0;padding:4px 0;list-style:none;
                         background:var(--surface-2,#fff);
                         border:1px solid var(--border-soft,#e0e0e0);
                         border-radius:var(--r-md,8px);
                         box-shadow:0 4px 16px rgba(0,0,0,.10);
                         z-index:200;max-height:260px;overflow-y:auto">
              </ul>
            </div>
            <button type="button" id="ced-attendee-add" class="btn btn-outline btn-sm">Add</button>
          </div>
          <div id="ced-attendee-err" style="font-size:11.5px;color:var(--danger);margin-top:4px;display:none"></div>
        </div>

        <label class="cnm-notify-row" style="margin-top:10px">
          <input type="checkbox" id="ced-no-notify">
          <span style="font-size:12.5px;color:var(--ink-3)">Don't notify attendees</span>
        </label>

        <div id="ced-err" class="cal-field-err" hidden></div>

        <div style="display:flex;gap:10px;margin-top:8px;flex-wrap:wrap;">
          <button id="ced-save" class="btn btn-ink btn-sm" disabled>Save changes</button>
          <button id="ced-delete" class="btn btn-outline btn-sm" style="color:var(--danger);border-color:var(--danger)">Delete event</button>
        </div>

        <div id="ced-delete-confirm" hidden style="margin-top:12px;padding:12px;background:var(--rust-soft);border-radius:var(--r-sm);border:1px solid var(--danger)">
          <p style="font-size:13px;margin:0 0 10px;">Delete this event from Google Calendar?
            <br><small style="color:var(--ink-3)">Updates won't be sent to attendees.</small></p>
          <div style="display:flex;gap:8px;">
            <button id="ced-delete-confirm-yes" class="btn btn-sm" style="background:var(--danger);color:#fff;border:none;">Yes, delete</button>
            <button id="ced-delete-confirm-no" class="btn btn-outline btn-sm">Cancel</button>
          </div>
        </div>
      </div>
    `;
  }

  _wireForm() {
    const titleEl    = this.querySelector('#ced-title');
    const startEl    = this.querySelector('#ced-start');
    const endEl      = this.querySelector('#ced-end');
    const locationEl = this.querySelector('#ced-location');
    const descEl     = this.querySelector('#ced-description');
    const saveBtn    = this.querySelector('#ced-save');
    const deleteBtn  = this.querySelector('#ced-delete');
    const deleteConf = this.querySelector('#ced-delete-confirm');
    const confYes    = this.querySelector('#ced-delete-confirm-yes');
    const confNo     = this.querySelector('#ced-delete-confirm-no');
    const errEl      = this.querySelector('#ced-err');
    const noNotify   = this.querySelector('#ced-no-notify');

    const ev = this._event;
    const origStart = ev.start?.dateTime || ev.start?.date || '';
    const origEnd   = ev.end?.dateTime   || ev.end?.date   || '';

    // Seed attendee list from the loaded event (copy so we can mutate)
    this._attendees = Array.isArray(ev.attendees)
      ? ev.attendees.map((a) => ({ ...a }))
      : [];
    const origEmails = this._attendees.map((a) => a.email).sort().join(',');

    const markDirty = () => {
      const changed = {};
      if (titleEl.value !== (ev.summary || ev.title || '')) changed.summary = titleEl.value;
      if (startEl.value !== _fmtDatetimeLocal(origStart)) {
        const d = new Date(startEl.value);
        if (!isNaN(d.getTime())) changed.start = { dateTime: d.toISOString() };
      }
      if (endEl.value !== _fmtDatetimeLocal(origEnd)) {
        const d = new Date(endEl.value);
        if (!isNaN(d.getTime())) changed.end = { dateTime: d.toISOString() };
      }
      if (locationEl.value !== (ev.location || '')) changed.location = locationEl.value;
      if (descEl.value !== (ev.description || '')) changed.description = descEl.value;
      // Attendees dirty check
      const curEmails = this._attendees.map((a) => a.email).sort().join(',');
      if (curEmails !== origEmails) changed.attendees = this._attendees;
      this._dirty = changed;
      saveBtn.disabled = Object.keys(changed).length === 0;
    };

    [titleEl, startEl, endEl, locationEl, descEl].forEach((el) => el?.addEventListener('input', markDirty));

    // Attendee chip-input with people-search autocomplete
    const attendeeInput = this.querySelector('#ced-attendee-input');
    const attendeeAddBtn = this.querySelector('#ced-attendee-add');
    const attendeeErr = this.querySelector('#ced-attendee-err');
    const dropdown = this.querySelector('#ced-people-dropdown');

    // --- Autocomplete state ---
    let _acResults = [];      // current result list
    let _acIndex = -1;        // keyboard-highlighted index

    const _closeDropdown = () => {
      dropdown.style.display = 'none';
      _acResults = [];
      _acIndex = -1;
    };

    const _renderDropdown = (results) => {
      _acResults = results;
      _acIndex = -1;
      if (!results.length) { _closeDropdown(); return; }

      dropdown.innerHTML = results.map((p, i) => `
        <li role="option"
            data-ac-idx="${i}"
            style="display:flex;align-items:center;gap:8px;padding:9px 12px;cursor:pointer;
                   font-size:13px;line-height:1.3;transition:background .1s">
          <div style="flex:1;min-width:0">
            <div style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(p.name || p.email)}</div>
            <div style="font-size:11.5px;color:var(--ink-4);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_esc(p.email)}</div>
          </div>
          ${_sourcePill(p.source)}
        </li>
      `).join('');

      dropdown.style.display = 'block';

      // Hover highlight
      dropdown.querySelectorAll('li').forEach((li) => {
        li.addEventListener('mouseenter', () => {
          dropdown.querySelectorAll('li').forEach((l) => l.style.background = '');
          li.style.background = 'var(--surface-3, #f5f5f5)';
          _acIndex = Number(li.dataset.acIdx);
        });
        li.addEventListener('mouseleave', () => { li.style.background = ''; });
        li.addEventListener('mousedown', (e) => {
          // mousedown fires before blur, so we can act before the input loses focus
          e.preventDefault();
          _selectResult(Number(li.dataset.acIdx));
        });
      });
    };

    const _highlightItem = (idx) => {
      dropdown.querySelectorAll('li').forEach((li, i) => {
        li.style.background = i === idx ? 'var(--surface-3, #f5f5f5)' : '';
      });
    };

    const _selectResult = (idx) => {
      const person = _acResults[idx];
      if (!person) return;
      attendeeInput.value = person.email;
      _closeDropdown();
      // Auto-add the selected person
      _doAddAttendee(person.email, person.name || undefined);
    };

    // Core add logic (shared between autocomplete-select and manual add)
    const _doAddAttendee = (email, displayName) => {
      const val = email.trim().toLowerCase();
      attendeeErr.style.display = 'none';
      if (!val) return;
      if (!_isValidEmail(val)) {
        attendeeErr.textContent = 'Enter a valid email address.';
        attendeeErr.style.display = '';
        return;
      }
      if (this._attendees.some((a) => a.email.toLowerCase() === val)) {
        attendeeErr.textContent = 'Already added.';
        attendeeErr.style.display = '';
        return;
      }
      const entry = { email: val, responseStatus: 'needsAction' };
      if (displayName) entry.displayName = displayName;
      this._attendees.push(entry);
      attendeeInput.value = '';
      _closeDropdown();
      this._refreshAttendeeList();
      markDirty();
    };

    const tryAddAttendee = () => {
      _doAddAttendee(attendeeInput.value);
    };

    // Debounced input → search
    attendeeInput.addEventListener('input', () => {
      clearTimeout(_peopleDebounceTimer);
      const q = attendeeInput.value.trim();
      if (q.length < 2) { _closeDropdown(); return; }
      _peopleDebounceTimer = setTimeout(async () => {
        const results = await _searchPeople(q, 8);
        _renderDropdown(results);
      }, 200);
    });

    // Keyboard navigation
    attendeeInput.addEventListener('keydown', (e) => {
      if (dropdown.style.display !== 'none') {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          _acIndex = Math.min(_acIndex + 1, _acResults.length - 1);
          _highlightItem(_acIndex);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          _acIndex = Math.max(_acIndex - 1, -1);
          _highlightItem(_acIndex);
          return;
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          if (_acIndex >= 0) { _selectResult(_acIndex); } else { tryAddAttendee(); }
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          _closeDropdown();
          return;
        }
      } else if (e.key === 'Enter') {
        e.preventDefault();
        tryAddAttendee();
      }
    });

    // Close dropdown when focus leaves the input
    attendeeInput.addEventListener('blur', () => {
      // Small delay so mousedown on a list item fires first
      setTimeout(_closeDropdown, 150);
    });

    attendeeAddBtn.addEventListener('click', tryAddAttendee);

    // Remove attendee via delegated click on the list
    const listEl = this.querySelector('#ced-attendees-list');
    listEl?.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-ced-remove-attendee]');
      if (!btn) return;
      const email = btn.dataset.cedRemoveAttendee;
      this._attendees = this._attendees.filter((a) => a.email !== email);
      this._refreshAttendeeList();
      markDirty();
    });

    saveBtn.addEventListener('click', async () => {
      if (Object.keys(this._dirty).length === 0) return;
      const sendUpdates = noNotify?.checked ? 'none' : 'all';
      const patch = { ...this._dirty, sendUpdates };
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
      errEl.hidden = true;
      try {
        await updateCalendarEventApi(this._eventId, patch);
        this._dirty = {};
        saveBtn.textContent = 'Saved ✓';
        setTimeout(() => { if (saveBtn) { saveBtn.textContent = 'Save changes'; } }, 1500);
        this.dispatchEvent(new CustomEvent('change', { bubbles: true, detail: { eventId: this._eventId } }));
      } catch (err) {
        errEl.textContent = err.message || 'Save failed';
        errEl.hidden = false;
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save changes';
      }
    });

    deleteBtn.addEventListener('click', () => {
      deleteConf.hidden = false;
      deleteBtn.hidden = true;
    });

    confNo.addEventListener('click', () => {
      deleteConf.hidden = true;
      deleteBtn.hidden = false;
    });

    confYes.addEventListener('click', async () => {
      confYes.disabled = true;
      confYes.textContent = 'Deleting…';
      errEl.hidden = true;
      try {
        await deleteCalendarEventApi(this._eventId, 'none');
        this.dispatchEvent(new CustomEvent('change', { bubbles: true, detail: { eventId: this._eventId, deleted: true } }));
        this.close();
      } catch (err) {
        errEl.textContent = err.message || 'Delete failed';
        errEl.hidden = false;
        confYes.disabled = false;
        confYes.textContent = 'Yes, delete';
      }
    });

    // RSVP strip
    const rsvpStrip = this.querySelector('#ced-rsvp-strip');
    if (rsvpStrip) {
      rsvpStrip.addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-ced-rsvp]');
        if (!btn) return;
        const response = btn.dataset.cedRsvp;
        const rsvpErr = this.querySelector('#ced-rsvp-err');
        if (rsvpErr) rsvpErr.hidden = true;

        // Optimistic: highlight chosen button
        const prevActive = rsvpStrip.querySelector('.cal-rsvp-btn--active');
        const prevClasses = prevActive ? [...prevActive.classList] : [];
        rsvpStrip.querySelectorAll('.cal-rsvp-btn').forEach((b) => {
          const map = { accepted: 'sage', declined: 'rust', tentative: 'amber' };
          b.classList.remove('cal-rsvp-btn--active', 'cal-rsvp-sage', 'cal-rsvp-rust', 'cal-rsvp-amber');
          if (b.dataset.cedRsvp === response) {
            b.classList.add('cal-rsvp-btn--active', `cal-rsvp-${map[response] || ''}`);
          }
        });

        try {
          await respondToCalendarEventApi(this._eventId, response);
          // Update internal event state so re-render is accurate
          const self = Array.isArray(this._event?.attendees) && this._event.attendees.find((a) => a.self);
          if (self) self.responseStatus = response;
          this.dispatchEvent(new CustomEvent('change', { bubbles: true, detail: { eventId: this._eventId } }));
        } catch (err) {
          // Revert
          rsvpStrip.querySelectorAll('.cal-rsvp-btn').forEach((b) => {
            b.classList.remove('cal-rsvp-btn--active', 'cal-rsvp-sage', 'cal-rsvp-rust', 'cal-rsvp-amber');
          });
          if (prevActive) prevClasses.forEach((c) => prevActive.classList.add(c));
          if (rsvpErr) { rsvpErr.textContent = err.message || 'RSVP failed'; rsvpErr.hidden = false; }
        }
      });
    }
  }

  _refreshAttendeeList() {
    const listEl = this.querySelector('#ced-attendees-list');
    const countEl = this.querySelector('#ced-attendee-count');
    if (!listEl) return;

    if (!this._attendees.length) {
      listEl.innerHTML = '<p style="font-size:12.5px;color:var(--ink-4)" id="ced-no-attendees">No attendees</p>';
    } else {
      listEl.innerHTML = this._attendees.map((a) => {
        const status = a.responseStatus || 'needsAction';
        const label = RSVP_LABELS[status] || status;
        const cls = RSVP_CLS[status] || 'slate';
        const removeBtn = !a.self
          ? `<button type="button" class="cal-chip-remove" data-ced-remove-attendee="${_esc(a.email || '')}" aria-label="Remove ${_esc(a.email || '')}" title="Remove attendee">&times;</button>`
          : '';
        return `
          <div class="cal-attendee-row" data-ced-attendee="${_esc(a.email || '')}">
            <span class="cal-attendee-name">${_esc(a.displayName || a.email || '')}</span>
            <span class="cal-attendee-email">${_esc(a.email || '')}</span>
            <span class="cal-rsvp-badge cal-rsvp-${cls}">${_esc(label)}</span>
            ${removeBtn}
          </div>
        `;
      }).join('');
    }
    if (countEl) countEl.textContent = String(this._attendees.length);
  }
}

customElements.define('artemis-calendar-event-drawer', ArtemisCalendarEventDrawer);
