// jira-team-picker.js
// Search-as-you-type picker for managing the Jira marketing team filter.
//
// Flow:
//   1. GET /api/jira/team-members → { saved: string[], all_assignable: [...] }
//   2. Pre-select chips for saved accountIds.
//   3. Debounced search filters all_assignable by displayName / email.
//   4. PUT /api/jira/team-members { members: string[] } on save.

const DEBOUNCE_MS = 250;

function _debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function _overlay() {
  const el = document.createElement('div');
  el.className = 'jira-team-picker-overlay';
  return el;
}

function _chipEl(user, onRemove) {
  const chip = document.createElement('span');
  chip.className = 'jira-team-picker__chip';
  chip.dataset.accountId = user.accountId;

  const label = document.createElement('span');
  label.className = 'jira-team-picker__chip-label';
  label.textContent = user.displayName || user.accountId;
  chip.appendChild(label);

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'jira-team-picker__chip-remove';
  removeBtn.setAttribute('aria-label', `Remove ${user.displayName || user.accountId}`);
  removeBtn.textContent = '×';
  removeBtn.addEventListener('click', () => onRemove(user.accountId));
  chip.appendChild(removeBtn);

  return chip;
}

function _dropdownRowEl(user, onSelect) {
  const row = document.createElement('button');
  row.type = 'button';
  row.className = 'jira-team-picker__dropdown-row';
  row.dataset.accountId = user.accountId;

  const name = document.createElement('span');
  name.className = 'jira-team-picker__row-name';
  name.textContent = user.displayName || user.accountId;
  row.appendChild(name);

  if (user.emailAddress) {
    const email = document.createElement('span');
    email.className = 'jira-team-picker__row-email';
    email.textContent = user.emailAddress;
    row.appendChild(email);
  }

  row.addEventListener('click', () => onSelect(user));
  return row;
}

export function openJiraTeamPicker() {
  const overlay = _overlay();
  document.body.appendChild(overlay);

  const modal = document.createElement('div');
  modal.className = 'jira-team-picker';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-label', 'Manage Jira team');

  // ── Header ─────────────────────────────────────────────────────────────────
  const header = document.createElement('div');
  header.className = 'jira-team-picker__header';

  const title = document.createElement('h2');
  title.className = 'jira-team-picker__title';
  title.textContent = 'Manage team';
  header.appendChild(title);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'jira-team-picker__close';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.innerHTML = '&times;';
  header.appendChild(closeBtn);

  modal.appendChild(header);

  // ── Body ───────────────────────────────────────────────────────────────────
  const body = document.createElement('div');
  body.className = 'jira-team-picker__body';

  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.className = 'jira-team-picker__search';
  searchInput.placeholder = 'Search by name or email…';
  searchInput.setAttribute('autocomplete', 'off');
  body.appendChild(searchInput);

  const dropdown = document.createElement('div');
  dropdown.className = 'jira-team-picker__dropdown hidden';
  body.appendChild(dropdown);

  const chipsLabel = document.createElement('div');
  chipsLabel.className = 'jira-team-picker__chips-label';
  chipsLabel.textContent = 'Team members';
  body.appendChild(chipsLabel);

  const chipsArea = document.createElement('div');
  chipsArea.className = 'jira-team-picker__chips';
  body.appendChild(chipsArea);

  const emptyState = document.createElement('p');
  emptyState.className = 'jira-team-picker__empty-state hidden';
  emptyState.textContent = 'No team members set — assignee dropdowns show your whole org.';
  body.appendChild(emptyState);

  modal.appendChild(body);

  // ── Footer ─────────────────────────────────────────────────────────────────
  const footer = document.createElement('div');
  footer.className = 'jira-team-picker__footer';

  const errorEl = document.createElement('p');
  errorEl.className = 'jira-team-picker__error hidden';
  footer.appendChild(errorEl);

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'jira-team-picker__save-btn';
  saveBtn.textContent = 'Save team';
  footer.appendChild(saveBtn);

  modal.appendChild(footer);
  overlay.appendChild(modal);

  // ── State ──────────────────────────────────────────────────────────────────
  let allAssignable = [];
  const selectedMap = new Map(); // accountId → user object

  function _close() {
    overlay.remove();
  }

  function _renderChips() {
    chipsArea.innerHTML = '';
    if (selectedMap.size === 0) {
      emptyState.classList.remove('hidden');
      chipsLabel.classList.add('hidden');
    } else {
      emptyState.classList.add('hidden');
      chipsLabel.classList.remove('hidden');
      for (const user of selectedMap.values()) {
        chipsArea.appendChild(_chipEl(user, (id) => {
          selectedMap.delete(id);
          _renderChips();
        }));
      }
    }
  }

  function _renderDropdown(query) {
    const q = query.trim().toLowerCase();
    if (!q) {
      dropdown.classList.add('hidden');
      dropdown.innerHTML = '';
      return;
    }
    const matches = allAssignable.filter((u) => {
      const name = (u.displayName || '').toLowerCase();
      const email = (u.emailAddress || '').toLowerCase();
      return name.includes(q) || email.includes(q);
    });
    dropdown.innerHTML = '';
    if (matches.length === 0) {
      dropdown.classList.add('hidden');
      return;
    }
    dropdown.classList.remove('hidden');
    for (const user of matches) {
      if (selectedMap.has(user.accountId)) continue;
      dropdown.appendChild(_dropdownRowEl(user, (u) => {
        selectedMap.set(u.accountId, u);
        searchInput.value = '';
        dropdown.classList.add('hidden');
        dropdown.innerHTML = '';
        _renderChips();
        searchInput.focus();
      }));
    }
    if (!dropdown.firstChild) {
      dropdown.classList.add('hidden');
    }
  }

  const _debouncedSearch = _debounce((q) => _renderDropdown(q), DEBOUNCE_MS);

  searchInput.addEventListener('input', () => _debouncedSearch(searchInput.value));

  closeBtn.addEventListener('click', _close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) _close(); });

  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    errorEl.classList.add('hidden');
    try {
      const res = await fetch('/api/jira/team-members', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ members: [...selectedMap.keys()] }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      _close();
    } catch (err) {
      errorEl.textContent = err.message || 'Save failed.';
      errorEl.classList.remove('hidden');
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save team';
    }
  });

  // ── Load ───────────────────────────────────────────────────────────────────
  chipsLabel.classList.add('hidden');
  emptyState.classList.add('hidden');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Loading…';

  fetch('/api/jira/team-members')
    .then((r) => r.json())
    .then(({ saved, all_assignable }) => {
      allAssignable = all_assignable || [];
      const byId = new Map(allAssignable.map((u) => [u.accountId, u]));
      for (const id of (saved || [])) {
        const user = byId.get(id) || { accountId: id, displayName: id, emailAddress: '' };
        selectedMap.set(id, user);
      }
      _renderChips();
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save team';
      searchInput.focus();
    })
    .catch((err) => {
      errorEl.textContent = `Could not load team data: ${err.message}`;
      errorEl.classList.remove('hidden');
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save team';
    });
}
