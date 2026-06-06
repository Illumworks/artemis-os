/**
 * Routing shell — the full-page Routing control surface.
 *
 * Three sections (no tabs — scannable):
 *   1. Provider health
 *   2. Default cascade
 *   3. Per-feature overrides + change log link
 *
 * Mounts into #app-shell-content when view === 'routing'.
 */

import { setState } from '../core/store.js';
import { INTEGRATIONS_VIEW } from '../core/navigation.js';

const API = {
  health: () => fetch('/api/routing/health'),
  features: () => fetch('/api/routing/features'),
  defaultCascade: () => fetch('/api/routing/default-cascade'),
  setFeatureOverride: (tag, body) =>
    fetch(`/api/routing/features/${encodeURIComponent(tag)}/override`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  deleteFeatureOverride: (tag) =>
    fetch(`/api/routing/features/${encodeURIComponent(tag)}/override`, {
      method: 'DELETE',
    }),
  setDefaultCascade: (body) =>
    fetch('/api/routing/default-cascade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  changesLog: (limit = 50, offset = 0) =>
    fetch(`/api/routing/changes-log?limit=${limit}&offset=${offset}`),
};

// ── helpers ───────────────────────────────────────────────────────────────────

const _esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );

function showToast(msg, kind = 'info') {
  const t = document.createElement('div');
  t.className = `routing-toast routing-toast--${kind}`;
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('routing-toast--visible'));
  setTimeout(() => {
    t.classList.remove('routing-toast--visible');
    setTimeout(() => t.remove(), 300);
  }, 3500);
}

function statusIcon(available) {
  return available
    ? `<span class="routing-status-icon routing-status-icon--ok" aria-label="available">&#10003;</span>`
    : `<span class="routing-status-icon routing-status-icon--fail" aria-label="unavailable">&#10007;</span>`;
}

// ── Section 1: Provider health ─────────────────────────────────────────────

function renderHealthSection(providers) {
  const rows = providers
    .map((p) => {
      const ok = p.available;
      const detail = ok
        ? _esc(
            [p.version, p.latency_ms != null ? `${p.latency_ms}ms` : null]
              .filter(Boolean)
              .join(' · ')
          )
        : _esc(p.error || 'unavailable');
      const extraLink =
        !ok && ['anthropic', 'openai', 'gemini', 'openrouter'].includes(p.provider)
          ? `<a class="routing-link" data-provider="${_esc(p.provider)}" data-action="open-connectors" href="#">Open Connectors &#8594;</a>`
          : !ok && p.provider === 'codex'
          ? `<a class="routing-link" data-action="codex-setup" href="#">Setup instructions</a>`
          : '';
      const modelList =
        ok && p.models && p.models.length
          ? ` &middot; models: ${p.models.map((m) => `<code class="routing-code">${_esc(m)}</code>`).join(', ')}`
          : '';

      return `
        <tr class="routing-health-row${ok ? '' : ' routing-health-row--fail'}">
          <td class="routing-health-status">${statusIcon(ok)}</td>
          <td class="routing-health-provider">${_esc(p.provider)}</td>
          <td class="routing-health-detail">${detail}${modelList} ${extraLink}</td>
        </tr>`;
    })
    .join('');

  return `
    <section class="routing-section" data-section="health">
      <h2 class="routing-section-title">Provider health</h2>
      <table class="routing-health-table">
        <tbody>${rows}</tbody>
      </table>
      <button class="routing-btn routing-btn--secondary" data-action="refresh-health">
        Refresh health
      </button>
    </section>`;
}

// ── Section 2: Default cascade ─────────────────────────────────────────────

function renderDefaultCascadeSection(cascade) {
  const items = cascade
    .map((p, i) => `<li class="routing-cascade-item">${i + 1}. ${_esc(p)}</li>`)
    .join('');
  return `
    <section class="routing-section" data-section="default-cascade">
      <h2 class="routing-section-title">Default cascade</h2>
      <p class="routing-section-desc">Applies to any feature without a custom override.</p>
      <ol class="routing-cascade-list">${items}</ol>
      <button class="routing-btn routing-btn--secondary" data-action="edit-default-cascade">
        Edit default cascade
      </button>
    </section>`;
}

// ── Section 3: Per-feature overrides ──────────────────────────────────────

function cascadeLabel(cascade) {
  return cascade.map((s) => s.provider || s).join(' → ');
}

function renderFeaturesSection(features) {
  const rows = features
    .map(
      (f) => `
      <tr class="routing-feature-row" data-tag="${_esc(f.feature_tag)}">
        <td class="routing-feature-name">
          <span class="routing-feature-label">${_esc(f.label)}</span>
          <span class="routing-feature-tag">${_esc(f.feature_tag)}</span>
        </td>
        <td class="routing-feature-cascade">${_esc(cascadeLabel(f.current_cascade))}</td>
        <td class="routing-feature-status">
          <span class="routing-badge routing-badge--${f.is_override ? 'custom' : 'default'}">
            ${f.is_override ? 'Custom' : 'Default'}
          </span>
        </td>
        <td class="routing-feature-actions">
          <button class="routing-btn routing-btn--small" data-action="edit-feature" data-tag="${_esc(f.feature_tag)}">Edit</button>
          ${f.is_override ? `<button class="routing-btn routing-btn--small routing-btn--danger" data-action="reset-feature" data-tag="${_esc(f.feature_tag)}">Reset to default</button>` : ''}
        </td>
      </tr>`
    )
    .join('');

  return `
    <section class="routing-section" data-section="features">
      <h2 class="routing-section-title">Per-feature routing</h2>
      <table class="routing-features-table">
        <thead>
          <tr>
            <th>Feature</th>
            <th>Current cascade</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <button class="routing-btn routing-btn--secondary" data-action="show-change-log">
        Show change log &#8594;
      </button>
    </section>`;
}

// ── Full page render ───────────────────────────────────────────────────────

async function loadRoutingShell(container) {
  container.innerHTML = `
    <div class="routing-shell">
      <header class="routing-header">
        <h1 class="routing-title">Routing</h1>
        <p class="routing-subtitle">Control which AI provider handles each feature.</p>
      </header>
      <div class="routing-loading">Loading...</div>
    </div>`;

  try {
    const [healthRes, featuresRes, defaultCascadeRes] = await Promise.all([
      API.health(),
      API.features(),
      API.defaultCascade(),
    ]);

    const [healthData, featuresData, cascadeData] = await Promise.all([
      healthRes.json(),
      featuresRes.json(),
      defaultCascadeRes.json(),
    ]);

    const shell = container.querySelector('.routing-shell');
    shell.querySelector('.routing-loading').remove();
    shell.insertAdjacentHTML(
      'beforeend',
      renderHealthSection(healthData.providers || []) +
        renderDefaultCascadeSection(cascadeData.cascade || []) +
        renderFeaturesSection(featuresData.features || [])
    );

    wireActions(container, featuresData.features || []);
  } catch (err) {
    const shell = container.querySelector('.routing-shell');
    shell.innerHTML += `<div class="routing-error">Failed to load routing data: ${_esc(String(err))}</div>`;
  }
}

// ── Action wiring ──────────────────────────────────────────────────────────

function wireActions(container, features) {
  container.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;

    if (action === 'refresh-health') {
      await loadRoutingShell(container);
      return;
    }

    if (action === 'open-connectors') {
      e.preventDefault();
      setState('view', INTEGRATIONS_VIEW);
      return;
    }

    if (action === 'codex-setup') {
      e.preventDefault();
      showCodexSetupModal();
      return;
    }

    if (action === 'edit-default-cascade') {
      const res = await API.defaultCascade();
      const data = await res.json();
      showDefaultCascadeModal(data.cascade || [], container);
      return;
    }

    if (action === 'edit-feature') {
      const tag = btn.dataset.tag;
      const feature = features.find((f) => f.feature_tag === tag);
      if (feature) showFeatureEditModal(feature, container, features);
      return;
    }

    if (action === 'reset-feature') {
      const tag = btn.dataset.tag;
      const feature = features.find((f) => f.feature_tag === tag);
      if (!feature) return;
      const confirmed = confirm(
        `Reset "${feature.label}" to default cascade?\n\nThis will deactivate the custom override.`
      );
      if (!confirmed) return;
      const res = await API.deleteFeatureOverride(tag);
      if (res.ok) {
        showToast('Override reset. Feature will use default cascade.', 'success');
        await loadRoutingShell(container);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(`Failed to reset: ${err.error || res.status}`, 'error');
      }
      return;
    }

    if (action === 'show-change-log') {
      showChangeLogModal();
      return;
    }
  });
}

// ── Modals ────────────────────────────────────────────────────────────────

const KNOWN_PROVIDERS = [
  'claude-code', 'codex', 'lm-studio', 'anthropic', 'openai', 'gemini', 'openrouter',
];

function createModal(title, bodyHtml, onConfirm) {
  const overlay = document.createElement('div');
  overlay.className = 'routing-modal-overlay';
  overlay.innerHTML = `
    <div class="routing-modal" role="dialog" aria-modal="true">
      <div class="routing-modal-header">
        <h3 class="routing-modal-title">${_esc(title)}</h3>
        <button class="routing-modal-close" aria-label="Close">&times;</button>
      </div>
      <div class="routing-modal-body">${bodyHtml}</div>
      <div class="routing-modal-footer">
        <button class="routing-btn routing-btn--secondary" data-modal-action="cancel">Cancel</button>
        <button class="routing-btn routing-btn--primary" data-modal-action="confirm">Apply</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const close = () => overlay.remove();
  overlay.querySelector('.routing-modal-close').addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', function onEsc(e) {
    if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); }
  });
  overlay.querySelector('[data-modal-action="cancel"]').addEventListener('click', close);
  overlay.querySelector('[data-modal-action="confirm"]').addEventListener('click', () => {
    onConfirm(overlay, close);
  });
  return overlay;
}

function showCodexSetupModal() {
  createModal(
    'Set up Codex CLI',
    `<p>Codex is installed but the binary is not on your PATH. Run this command in Terminal:</p>
     <pre class="routing-code-block">ln -s /Applications/Codex.app/Contents/MacOS/codex /usr/local/bin/codex</pre>
     <p>Then restart Artemis OS for the PATH to be picked up.</p>`,
    (_overlay, close) => close()
  );
}

function showDefaultCascadeModal(currentCascade, container) {
  const opts = KNOWN_PROVIDERS.map(
    (p) => `<option value="${_esc(p)}">${_esc(p)}</option>`
  ).join('');

  let items = [...currentCascade];
  const listId = 'routing-default-cascade-list';

  const renderList = () =>
    items
      .map(
        (p, i) => `
      <li class="routing-cascade-edit-item" data-idx="${i}">
        <span class="routing-cascade-handle">&#8597;</span>
        <span>${_esc(p)}</span>
        <button class="routing-btn routing-btn--small routing-btn--danger" data-remove="${i}">&times;</button>
      </li>`
      )
      .join('');

  const overlay = createModal(
    'Edit default cascade',
    `<p>Drag to reorder. The first available provider is used for each call.</p>
     <ul class="routing-cascade-edit-list" id="${listId}">${renderList()}</ul>
     <div class="routing-cascade-add">
       <select class="routing-select" id="routing-add-provider-select">${opts}</select>
       <button class="routing-btn routing-btn--secondary" id="routing-add-provider-btn">Add</button>
     </div>
     <div class="routing-form-field">
       <label class="routing-label">Reason (required)</label>
       <input class="routing-input" id="routing-default-cascade-reason" type="text" placeholder="Why are you changing the default cascade?" />
     </div>`,
    async (_overlay, close) => {
      const reason = document.getElementById('routing-default-cascade-reason')?.value?.trim();
      if (!reason) { showToast('Reason is required.', 'error'); return; }
      const res = await API.setDefaultCascade({ cascade: items, reason });
      if (res.ok) {
        showToast('Default cascade updated.', 'success');
        close();
        await loadRoutingShell(container);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(`Failed: ${err.error || res.status}`, 'error');
      }
    }
  );

  // Wire remove buttons and add
  overlay.addEventListener('click', (e) => {
    const removeBtn = e.target.closest('[data-remove]');
    if (removeBtn) {
      const idx = parseInt(removeBtn.dataset.remove, 10);
      items.splice(idx, 1);
      const list = document.getElementById(listId);
      if (list) list.innerHTML = renderList();
    }
  });
  document.getElementById('routing-add-provider-btn')?.addEventListener('click', () => {
    const sel = document.getElementById('routing-add-provider-select');
    if (sel && !items.includes(sel.value)) {
      items.push(sel.value);
      const list = document.getElementById(listId);
      if (list) list.innerHTML = renderList();
    }
  });
}

function showFeatureEditModal(feature, container, allFeatures) {
  const cascade = [...feature.current_cascade];
  let steps = cascade.map((s) =>
    typeof s === 'string' ? { provider: s } : { ...s }
  );

  const providerOpts = KNOWN_PROVIDERS.map(
    (p) => `<option value="${_esc(p)}">${_esc(p)}</option>`
  ).join('');

  const stepsId = 'routing-feature-steps';

  const renderSteps = () =>
    steps
      .map(
        (s, i) => `
        <li class="routing-cascade-edit-item" data-step-idx="${i}">
          <span class="routing-cascade-handle">&#8597;</span>
          <select class="routing-select routing-select--sm" data-step-provider="${i}">
            ${KNOWN_PROVIDERS.map(
              (p) =>
                `<option value="${_esc(p)}"${s.provider === p ? ' selected' : ''}>${_esc(p)}</option>`
            ).join('')}
          </select>
          <input class="routing-input routing-input--sm" data-step-model="${i}" type="text"
            placeholder="model (optional)" value="${_esc(s.model || '')}" />
          <button class="routing-btn routing-btn--small routing-btn--danger" data-remove-step="${i}">&times;</button>
        </li>`
      )
      .join('');

  const overlay = createModal(
    `Routing for ${feature.label}`,
    `<p class="routing-modal-desc">Set the ordered cascade of providers for this feature. The override takes effect on the next call.</p>
     <ul class="routing-cascade-edit-list" id="${stepsId}">${renderSteps()}</ul>
     <button class="routing-btn routing-btn--secondary" id="routing-add-step-btn">Add provider step</button>
     <div class="routing-form-field" style="margin-top:12px">
       <label class="routing-label">Reason (required)</label>
       <input class="routing-input" id="routing-feature-reason" type="text"
         placeholder="Why are you changing this routing?" />
     </div>`,
    async (_overlay, close) => {
      // Collect current step values from DOM
      const stepsList = document.getElementById(stepsId);
      if (stepsList) {
        const items = stepsList.querySelectorAll('.routing-cascade-edit-item');
        steps = Array.from(items).map((item, i) => {
          const provSel = item.querySelector(`[data-step-provider="${i}"]`);
          const modelInput = item.querySelector(`[data-step-model="${i}"]`);
          const step = { provider: provSel?.value || '' };
          if (modelInput?.value?.trim()) step.model = modelInput.value.trim();
          return step;
        });
      }

      const reason = document.getElementById('routing-feature-reason')?.value?.trim();
      if (!reason) { showToast('Reason is required.', 'error'); return; }
      if (steps.length === 0) { showToast('Cascade must have at least one step.', 'error'); return; }

      const res = await API.setFeatureOverride(feature.feature_tag, { cascade: steps, reason });
      if (res.ok) {
        const data = await res.json();
        if (data.warnings && data.warnings.length) {
          data.warnings.forEach((w) => showToast(w, 'warn'));
        }
        showToast('Routing updated. Next call will use the new cascade.', 'success');
        close();
        await loadRoutingShell(container);
      } else {
        const err = await res.json().catch(() => ({}));
        showToast(`Failed: ${err.error || res.status}`, 'error');
      }
    }
  );

  overlay.addEventListener('click', (e) => {
    const removeBtn = e.target.closest('[data-remove-step]');
    if (removeBtn) {
      const idx = parseInt(removeBtn.dataset.removeStep, 10);
      steps.splice(idx, 1);
      const list = document.getElementById(stepsId);
      if (list) list.innerHTML = renderSteps();
    }
  });

  document.getElementById('routing-add-step-btn')?.addEventListener('click', () => {
    steps.push({ provider: 'claude-code' });
    const list = document.getElementById(stepsId);
    if (list) list.innerHTML = renderSteps();
  });
}

async function showChangeLogModal() {
  const res = await API.changesLog(50, 0);
  const data = await res.json().catch(() => ({ changes: [] }));
  const rows = (data.changes || [])
    .map((c) => {
      const before = c.before ? JSON.stringify(c.before.cascade || c.before) : '—';
      const after = JSON.stringify(c.after.cascade || c.after);
      return `
        <tr>
          <td>${_esc(new Date(c.changed_at).toLocaleString())}</td>
          <td>${_esc(c.scope)}${c.scope_value ? ` / ${_esc(c.scope_value)}` : ''}</td>
          <td class="routing-log-change"><code>${_esc(before)}</code> &#8594; <code>${_esc(after)}</code></td>
          <td>${_esc(c.reason || '')}</td>
          <td>${_esc(c.changed_by)}</td>
        </tr>`;
    })
    .join('');

  createModal(
    'Routing change log',
    `<table class="routing-log-table">
       <thead><tr><th>Time</th><th>Scope</th><th>Change</th><th>Reason</th><th>By</th></tr></thead>
       <tbody>${rows || '<tr><td colspan="5">No changes logged yet.</td></tr>'}</tbody>
     </table>`,
    (_overlay, close) => close()
  );
}

export { loadRoutingShell };
