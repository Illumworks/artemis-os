// api-connectors.js
// The "API Connectors" section: encrypted per-source credentials used by agents
// at runtime.
//
// This lives in its own component because it has to appear on the live
// Integrations *modal*. It used to exist only inside features/integrations.js
// — the rail page, which integrations-modal.js retired. The section was
// therefore unreachable: the backend accepted connectors, the CRUD API worked,
// and no one could add one through the UI at all. Extracted rather than copied,
// so the two surfaces cannot drift.
//
// Usage:
//   import { renderApiConnectorsSection } from './api-connectors.js';
//   await renderApiConnectorsSection(containerEl);

// Connector kinds for the "Add connector" dropdown.
//
// The authoritative registry is artemis/connectors/kinds.py, served by
// GET /api/connectors/kinds. This array is only a fallback for when that call
// fails — it is deliberately NOT the source of truth. It used to be, and the
// result was that a kind added server-side never appeared in this dropdown, so
// the backend accepted connectors nobody could actually create.
let CONNECTOR_KINDS = [
  { id: 'starbridge', label: 'Starbridge', fields: ['api_key', 'api_url'], secret_fields: ['api_key'] },
  { id: 'openai', label: 'OpenAI', fields: ['api_key', 'organization'], secret_fields: ['api_key'] },
  { id: 'anthropic', label: 'Anthropic', fields: ['api_key'], secret_fields: ['api_key'] },
  { id: 'gemini', label: 'Google Gemini', fields: ['api_key'], secret_fields: ['api_key'] },
  { id: 'tavily', label: 'Tavily', fields: ['api_key'], secret_fields: ['api_key'] },
];

// OAuth-managed kinds are linkable but not creatable through this form, so the
// dropdown hides them; their credentials come from the OAuth flow instead.
async function _loadConnectorKinds() {
  try {
    const res = await fetch('/api/connectors/kinds');
    if (!res.ok) return;
    const kinds = await res.json();
    if (Array.isArray(kinds) && kinds.length) {
      CONNECTOR_KINDS = kinds.filter((k) => !k.oauth_managed);
    }
  } catch {
    // Keep the fallback list — an unreachable API should not block the form.
  }
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function _showToast(message, isError = false) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast${isError ? ' error' : ''}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ── API Connectors section ─────────────────────────────────────────────────────

export async function renderApiConnectorsSection(container) {
  let connectors = [];
  try {
    const res = await fetch('/api/connectors');
    if (res.ok) connectors = await res.json();
  } catch { /* non-fatal */ }

  _renderConnectorCards(container, connectors);
}

function _renderConnectorCards(container, connectors) {
  container.innerHTML = '';

  if (connectors.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'integrations-empty-state';
    empty.textContent = 'No API connectors yet. Add one below.';
    container.appendChild(empty);
  } else {
    const cardGrid = document.createElement('div');
    cardGrid.className = 'connector-cards-grid';
    for (const c of connectors) {
      cardGrid.appendChild(_buildConnectorCard(c, container, connectors));
    }
    container.appendChild(cardGrid);
  }

  // "Add connector" button
  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'integration-connect-btn';
  addBtn.textContent = '+ Add connector';
  addBtn.addEventListener('click', async () => {
    await _loadConnectorKinds();
    _showAddConnectorModal(container, connectors);
  });
  container.appendChild(addBtn);
}

function _buildConnectorCard(connector, listContainer, allConnectors) {
  const card = document.createElement('div');
  card.className = `connector-card connector-status-${connector.status}`;

  const kindLabel = CONNECTOR_KINDS.find((k) => k.id === connector.kind)?.label || connector.kind;

  card.innerHTML = `
    <div class="connector-card-header">
      <span class="connector-kind-badge">${_escHtml(kindLabel)}</span>
      <span class="connector-status-pill connector-status-${_escHtml(connector.status)}">${_escHtml(connector.status)}</span>
    </div>
    <div class="connector-card-name">${_escHtml(connector.name)}</div>
    ${connector.last_validated_at ? `<div class="connector-card-meta">Validated ${_escHtml(new Date(connector.last_validated_at).toLocaleDateString())}</div>` : ''}
    <div class="connector-card-actions">
      <button type="button" class="connector-test-btn" data-id="${_escAttr(connector.id)}">Test</button>
      <button type="button" class="connector-edit-btn" data-id="${_escAttr(connector.id)}">Edit</button>
      <button type="button" class="connector-delete-btn" data-id="${_escAttr(connector.id)}">Delete</button>
    </div>
    <div class="connector-test-result" id="connector-test-${_escAttr(connector.id)}"></div>
  `;

  card.querySelector('.connector-test-btn').addEventListener('click', async () => {
    const resultEl = card.querySelector(`#connector-test-${connector.id}`);
    if (resultEl) resultEl.textContent = 'Testing…';
    try {
      const r = await fetch(`/api/connectors/${connector.id}/test`, { method: 'POST' });
      const body = await r.json();
      if (resultEl) {
        resultEl.textContent = body.ok ? `OK: ${body.message}` : `Failed: ${body.message}`;
        resultEl.className = `connector-test-result ${body.ok ? 'test-ok' : 'test-fail'}`;
      }
    } catch (e) {
      if (resultEl) { resultEl.textContent = `Error: ${e}`; resultEl.className = 'connector-test-result test-fail'; }
    }
  });

  card.querySelector('.connector-delete-btn').addEventListener('click', async () => {
    if (!confirm(`Delete connector "${connector.name}"? It will be disabled (recoverable).`)) return;
    try {
      const r = await fetch(`/api/connectors/${connector.id}`, { method: 'DELETE' });
      if (r.ok || r.status === 204) {
        _showToast(`Connector "${connector.name}" disabled.`);
        const updated = allConnectors.filter((c) => c.id !== connector.id);
        _renderConnectorCards(listContainer, updated);
      }
    } catch (e) {
      _showToast(`Delete failed: ${e}`, true);
    }
  });

  return card;
}

function _showAddConnectorModal(listContainer, existingConnectors) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay connector-modal-overlay';

  overlay.innerHTML = `
    <div class="modal connector-modal" role="dialog" aria-modal="true" aria-label="Add connector">
      <div class="modal-header">
        <h3 class="modal-title">Add connector</h3>
        <button type="button" class="modal-close" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body">
        <label class="connector-form-field">
          <span>Kind</span>
          <select id="connector-kind-select">
            ${CONNECTOR_KINDS.map((k) => `<option value="${_escAttr(k.id)}">${_escHtml(k.label)}</option>`).join('')}
          </select>
        </label>
        <label class="connector-form-field">
          <span>Name</span>
          <input type="text" id="connector-name-input" placeholder="e.g. Starbridge — production">
        </label>
        <div id="connector-fields-container"></div>
        <div class="connector-modal-actions">
          <button type="button" id="connector-save-btn" class="integration-connect-btn">Save</button>
          <button type="button" id="connector-cancel-btn" class="integration-settings-btn">Cancel</button>
        </div>
        <div id="connector-form-error" class="connector-form-error" style="display:none"></div>
      </div>
    </div>
  `;

  const close = () => overlay.remove();
  overlay.querySelector('.modal-close').addEventListener('click', close);
  overlay.querySelector('#connector-cancel-btn').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const kindSelect = overlay.querySelector('#connector-kind-select');
  const fieldsContainer = overlay.querySelector('#connector-fields-container');

  function renderFields(kindId) {
    const kind = CONNECTOR_KINDS.find((k) => k.id === kindId);
    if (!kind) { fieldsContainer.innerHTML = ''; return; }
    fieldsContainer.innerHTML = kind.fields.map((f) => `
      <label class="connector-form-field">
        <span>${_escHtml(f.replace(/_/g, ' '))}${f === 'organization' ? ' (optional)' : ''}</span>
        <input type="${(kind.secret_fields || []).includes(f) ? 'password' : 'text'}"
               id="connector-field-${_escAttr(f)}"
               name="${_escAttr(f)}"
               autocomplete="off"
               placeholder="${_escAttr(f)}">
      </label>
    `).join('');
  }

  renderFields(kindSelect.value);
  kindSelect.addEventListener('change', () => renderFields(kindSelect.value));

  overlay.querySelector('#connector-save-btn').addEventListener('click', async () => {
    const kind = kindSelect.value;
    const name = overlay.querySelector('#connector-name-input').value.trim();
    const errorEl = overlay.querySelector('#connector-form-error');
    if (!name) { errorEl.textContent = 'Name is required.'; errorEl.style.display = ''; return; }
    const credentials = {};
    const kindDef = CONNECTOR_KINDS.find((k) => k.id === kind);
    for (const f of (kindDef?.fields || [])) {
      const val = overlay.querySelector(`#connector-field-${f}`)?.value?.trim();
      if (val) credentials[f] = val;
    }
    try {
      const r = await fetch('/api/connectors/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, name, credentials }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        errorEl.textContent = err.error || `HTTP ${r.status}`;
        errorEl.style.display = '';
        return;
      }
      const newConn = await r.json();
      _showToast(`Connector "${newConn.name}" added.`);
      close();
      _renderConnectorCards(listContainer, [newConn, ...existingConnectors]);
    } catch (e) {
      errorEl.textContent = String(e);
      errorEl.style.display = '';
    }
  });

  document.body.appendChild(overlay);
  overlay.querySelector('#connector-name-input').focus();
}

// ── HTML helpers ──────────────────────────────────────────────────────────────

function _escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

function _escAttr(str) {
  return String(str ?? '').replace(/['"]/g, '').replace(/[<>&]/g, '');
}

