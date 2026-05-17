// credential-entry-modal.js
// Design: fluidity, simplicity, purposefulness, naturalness, spacious, open.
// One modal, spacious column layout. Single primary action. No tabs, no nested expand.
//
// Usage:
//   openCredentialEntryModal({ provider, fields, onSaved })
//
//   provider: string — e.g. 'slack'
//   fields:   Array<{ key: string, label: string, helper: string, sensitive: boolean }>
//   onSaved:  () => void — called after a successful POST

/**
 * Open the credential-entry modal for a provider.
 *
 * @param {{ provider: string, fields: Array<{key:string,label:string,helper:string,sensitive:boolean}>, onSaved: () => void }} opts
 */
export function openCredentialEntryModal({ provider, fields, onSaved }) {
  // Remove any existing instance
  document.getElementById('credential-entry-overlay')?.remove();

  const overlay = document.createElement('div');
  overlay.id = 'credential-entry-overlay';
  overlay.className = 'credential-entry-overlay';

  const modal = document.createElement('div');
  modal.className = 'credential-entry-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-label', `Configure ${_titleCase(provider)}`);

  // ── Header ───────────────────────────────────────────────────────────────────
  const header = document.createElement('div');
  header.className = 'credential-entry-modal__header';

  const title = document.createElement('h3');
  title.className = 'credential-entry-modal__title';
  title.textContent = `Configure ${_titleCase(provider)}`;
  header.appendChild(title);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'credential-entry-modal__close';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.innerHTML = '&times;';
  closeBtn.addEventListener('click', _close);
  header.appendChild(closeBtn);

  modal.appendChild(header);

  // ── Form ─────────────────────────────────────────────────────────────────────
  const form = document.createElement('form');
  form.className = 'credential-entry-modal__form';
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    _handleSave(provider, fields, form, saveBtn, errorEl, onSaved);
  });

  // Fetch current config status, then populate placeholders
  _loadConfigStatus(provider).then((status) => {
    fields.forEach(({ key, label, helper, sensitive }) => {
      const row = document.createElement('div');
      row.className = 'credential-entry-modal__field';

      const lbl = document.createElement('label');
      lbl.className = 'credential-entry-modal__label';
      lbl.setAttribute('for', `cred-${provider}-${key}`);
      lbl.textContent = label;
      row.appendChild(lbl);

      const inputWrap = document.createElement('div');
      inputWrap.className = 'credential-entry-modal__input-wrap';

      const input = document.createElement('input');
      input.id = `cred-${provider}-${key}`;
      input.name = key;
      input.type = sensitive ? 'password' : 'text';
      input.className = 'credential-entry-modal__input';
      input.autocomplete = 'off';
      input.spellcheck = false;

      const isSet = status?.configured_keys?.[key] === true;
      if (isSet) {
        input.placeholder = '••••••• (set — leave blank to keep)';
      }

      inputWrap.appendChild(input);

      if (sensitive) {
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'credential-entry-modal__show-toggle';
        toggle.setAttribute('aria-label', 'Toggle visibility');
        toggle.textContent = 'Show';
        toggle.addEventListener('click', () => {
          const isHidden = input.type === 'password';
          input.type = isHidden ? 'text' : 'password';
          toggle.textContent = isHidden ? 'Hide' : 'Show';
        });
        inputWrap.appendChild(toggle);
      }

      row.appendChild(inputWrap);

      if (helper) {
        const helperEl = document.createElement('p');
        helperEl.className = 'credential-entry-modal__helper';
        helperEl.textContent = helper;
        row.appendChild(helperEl);
      }

      form.appendChild(row);
    });
  });

  // ── Actions ──────────────────────────────────────────────────────────────────
  const actions = document.createElement('div');
  actions.className = 'credential-entry-modal__actions';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'submit';
  saveBtn.className = 'credential-entry-modal__save-btn';
  saveBtn.textContent = 'Save';

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'credential-entry-modal__cancel-btn';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.addEventListener('click', _close);

  const errorEl = document.createElement('p');
  errorEl.className = 'credential-entry-modal__error hidden';
  errorEl.setAttribute('aria-live', 'polite');

  actions.appendChild(errorEl);
  actions.appendChild(cancelBtn);
  actions.appendChild(saveBtn);
  form.appendChild(actions);
  modal.appendChild(form);

  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  // Close on backdrop click
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) _close();
  });

  // Trap focus on first input (after async fields render)
  setTimeout(() => modal.querySelector('input')?.focus(), 80);
}

// ── Private helpers ───────────────────────────────────────────────────────────

function _close() {
  document.getElementById('credential-entry-overlay')?.remove();
}

function _titleCase(str) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}

async function _loadConfigStatus(provider) {
  try {
    const res = await fetch(`/api/integrations/providers/${provider}/config`);
    if (res.ok) return await res.json();
  } catch {
    // non-fatal — placeholders just won't show "set" state
  }
  return null;
}

async function _handleSave(provider, fields, form, saveBtn, errorEl, onSaved) {
  const data = {};
  for (const { key } of fields) {
    const input = form.querySelector(`[name="${key}"]`);
    if (input?.value?.trim()) {
      data[key] = input.value.trim();
    }
  }

  if (Object.keys(data).length === 0) {
    _showError(errorEl, 'Enter at least one field to save.');
    return;
  }

  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving…';
  _hideError(errorEl);

  try {
    const res = await fetch(`/api/integrations/providers/${provider}/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || body.error || `HTTP ${res.status}`);
    }
    _close();
    onSaved?.();
  } catch (err) {
    _showError(errorEl, err.message || 'Save failed.');
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save';
  }
}

function _showError(el, msg) {
  el.textContent = msg;
  el.classList.remove('hidden');
}

function _hideError(el) {
  el.textContent = '';
  el.classList.add('hidden');
}
