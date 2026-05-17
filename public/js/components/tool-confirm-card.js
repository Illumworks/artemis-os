import { toolConfirm } from '../core/floating-artemis-api.js';
import { escapeHtml } from '../core/utils.js';

const PROPOSE_PREFIX = 'propose_';
const MAX_FIELD_LEN = 120;
const PREVIEW_FIELDS = ['name', 'description', 'task'];
const MAX_VISIBLE_PENDING = 2;

function isPropose(toolName) {
  return toolName.startsWith(PROPOSE_PREFIX);
}

function truncate(str) {
  const s = String(str);
  return s.length > MAX_FIELD_LEN ? s.slice(0, MAX_FIELD_LEN) + '…' : s;
}

function renderPreview(toolInput) {
  const rows = [];
  for (const key of PREVIEW_FIELDS) {
    if (key in toolInput && rows.length < 3) {
      const label = key.charAt(0).toUpperCase() + key.slice(1);
      const value = escapeHtml(truncate(toolInput[key]));
      const cssClass = key === 'name' ? 'fa-confirm-preview-name' : 'fa-confirm-preview-desc';
      rows.push(`<div class="${cssClass}">${escapeHtml(label)}: ${value}</div>`);
    }
  }
  if (rows.length === 0) {
    const keys = Object.keys(toolInput).slice(0, 3);
    for (const key of keys) {
      const value = escapeHtml(truncate(toolInput[key]));
      rows.push(`<div class="fa-confirm-preview-desc">${escapeHtml(key)}: ${value}</div>`);
    }
  }
  return rows.join('');
}

function buildProposeCard(toolUseId, toolName, toolInput) {
  const el = document.createElement('div');
  el.className = 'fa-confirm-card fa-confirm-propose';
  el.dataset.toolUseId = toolUseId;
  el.innerHTML = `
    <div class="fa-confirm-head">
      <span class="fa-confirm-type-badge">Propose</span>
      <span class="fa-confirm-tool-name">${escapeHtml(toolName)}</span>
    </div>
    <div class="fa-confirm-preview">${renderPreview(toolInput)}</div>
    <div class="fa-confirm-actions">
      <button class="fa-btn fa-btn-primary" data-action="save">Save</button>
      <button class="fa-btn fa-btn-secondary" data-action="save-run">Save &amp; Run</button>
      <button class="fa-btn fa-btn-ghost" data-action="cancel">Cancel</button>
    </div>
  `;
  return el;
}

function buildSpawnCard(toolUseId, toolName, toolInput, layer) {
  const layerClass = layer >= 4 ? 'fa-confirm-layer-destructive' : '';
  const el = document.createElement('div');
  el.className = 'fa-confirm-card fa-confirm-spawn';
  el.dataset.toolUseId = toolUseId;
  el.innerHTML = `
    <div class="fa-confirm-head">
      <span class="fa-confirm-type-badge fa-confirm-type-spawn">Action</span>
      <span class="fa-confirm-tool-name">${escapeHtml(toolName)}</span>
      <span class="fa-confirm-layer-badge ${layerClass}">Layer ${escapeHtml(String(layer))}</span>
    </div>
    <div class="fa-confirm-preview">${renderPreview(toolInput)}</div>
    <div class="fa-confirm-actions">
      <button class="fa-btn fa-btn-primary" data-action="run">Run</button>
      <button class="fa-btn fa-btn-ghost" data-action="cancel">Cancel</button>
    </div>
  `;
  return el;
}

function collapseToOneLiner(cardEl, text) {
  cardEl.className = 'fa-confirm-card fa-confirm-collapsed';
  cardEl.innerHTML = `<span class="fa-confirm-done-text">${escapeHtml(text)}</span>`;
}

function autoCollapseOldest(containerEl) {
  const pending = Array.from(
    containerEl.querySelectorAll('.fa-confirm-card:not(.fa-confirm-collapsed)')
  );
  if (pending.length > MAX_VISIBLE_PENDING) {
    const oldest = pending[0];
    const toolName = oldest.querySelector('.fa-confirm-tool-name')?.textContent ?? 'tool';
    collapseToOneLiner(oldest, `${toolName} — superseded`);
  }
}

function attachHandlers(cardEl, { sessionId, toolUseId, toolName, toolInput, onConfirm, propose }) {
  const actionsEl = cardEl.querySelector('.fa-confirm-actions');
  if (!actionsEl) return;

  actionsEl.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn || btn.disabled) return;

    const action = btn.dataset.action;

    // Disable all buttons immediately to prevent double-submit
    actionsEl.querySelectorAll('button').forEach((b) => { b.disabled = true; });

    if (action === 'cancel') {
      try {
        await toolConfirm(sessionId, toolUseId, 'cancel');
      } catch (_) { /* best-effort */ }
      collapseToOneLiner(cardEl, 'Cancelled.');
      if (typeof onConfirm === 'function') await onConfirm('cancel');
      return;
    }

    if (propose) {
      // save or save-run
      const variant = action === 'save-run' ? 'save_run' : 'save';
      try {
        await toolConfirm(sessionId, toolUseId, 'run');
      } catch (_) {
        actionsEl.querySelectorAll('button').forEach((b) => { b.disabled = false; });
        return;
      }
      const agentName = toolInput?.name ? `"${toolInput.name}"` : toolName;
      const suffix = variant === 'save_run' ? ' — running' : '';
      collapseToOneLiner(cardEl, `✓ Saved ${agentName} → view in /agents${suffix}`);
      if (typeof onConfirm === 'function') await onConfirm('run');
      return;
    }

    // spawn / other: run
    const runningEl = document.createElement('span');
    runningEl.className = 'fa-confirm-done-text';
    runningEl.textContent = '↳ Running…';
    cardEl.className = 'fa-confirm-card fa-confirm-collapsed';
    cardEl.innerHTML = '';
    cardEl.appendChild(runningEl);

    try {
      await toolConfirm(sessionId, toolUseId, 'run');
      runningEl.textContent = '✓ Done.';
    } catch (_) {
      runningEl.textContent = 'Error — could not confirm.';
    }
    if (typeof onConfirm === 'function') await onConfirm('run');
  });
}

export class ToolConfirmCard {
  static create(containerEl, {
    sessionId,
    toolUseId,
    toolName,
    toolInput,
    layer,
    onConfirm,
  }) {
    const propose = isPropose(toolName);
    const cardEl = propose
      ? buildProposeCard(toolUseId, toolName, toolInput)
      : buildSpawnCard(toolUseId, toolName, toolInput, layer);

    attachHandlers(cardEl, { sessionId, toolUseId, toolName, toolInput, onConfirm, propose });

    containerEl.appendChild(cardEl);
    autoCollapseOldest(containerEl);

    return cardEl;
  }
}

export function createConfirmCard(containerEl, options) {
  return ToolConfirmCard.create(containerEl, options);
}
