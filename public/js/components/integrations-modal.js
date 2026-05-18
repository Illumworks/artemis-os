// integrations-modal.js
// Design: fluidity, simplicity, purposefulness, naturalness, spacious, open.
//
// Surfaces the Integrations card grid as a centred lightbox — the rail page
// variant has been retired. All entry points (user popover "Connectors",
// status popover "Open Integrations", welcome overlay footer link, and any
// setState('view', 'integrations') call) converge here.
//
// Usage:
//   import { openIntegrationsModal } from '../components/integrations-modal.js';
//   openIntegrationsModal();

import { renderIntegrationCard } from './integration-card.js';

// ── Known providers ───────────────────────────────────────────────────────────

const PROVIDERS = [
  {
    id: 'slack',
    name: 'Slack',
    tagline: 'Post messages, read channels, get mentioned.',
  },
  {
    id: 'gcal',
    name: 'Google Calendar',
    tagline: 'Read your calendar; create, update, and remove events.',
  },
];

// ── Toast helper ──────────────────────────────────────────────────────────────

function _showToast(message, isError = false) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast${isError ? ' error' : ''}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ── OAuth redirect toast (fires once per page load, same as the old page) ─────

function _checkConnectionToast() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('slack_connected') === '1') {
    _showToast('Slack connected successfully.');
    window.history.replaceState({}, '', window.location.pathname);
  }
  if (params.get('gcal_connected') === '1') {
    _showToast('Google Calendar connected successfully.');
    window.history.replaceState({}, '', window.location.pathname);
  }
}

// ── Card rendering (extracted from integrations.js _loadAndRender) ────────────

async function _populateGrid(grid) {
  // Fetch connected integrations
  let connected = [];
  try {
    const res = await fetch('/api/integrations');
    if (res.ok) connected = await res.json();
  } catch {
    // Non-fatal: render as disconnected
  }

  for (const provider of PROVIDERS) {
    const cardContainer = document.createElement('div');
    grid.appendChild(cardContainer);

    const match = connected.find((row) => row.provider === provider.id);
    const connectedData = match
      ? {
          id: match.id,
          workspace_name: match.display_name || null,
          connected_at: match.connected_at,
        }
      : null;

    let configStatus = null;
    try {
      const cfgRes = await fetch(`/api/integrations/providers/${provider.id}/config`);
      if (cfgRes.ok) configStatus = await cfgRes.json();
    } catch {
      // non-fatal
    }

    renderIntegrationCard(cardContainer, provider, connectedData, configStatus);
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Open the Integrations modal.
 * Idempotent — calling while the modal is already open is a no-op.
 */
export function openIntegrationsModal() {
  // Idempotency guard
  if (document.getElementById('integrations-modal-overlay')) return;

  _checkConnectionToast();

  // ── Backdrop overlay ──────────────────────────────────────────────────────
  const overlay = document.createElement('div');
  overlay.id = 'integrations-modal-overlay';
  overlay.className = 'integrations-modal-overlay';
  overlay.setAttribute('role', 'presentation');

  // ── Modal panel ───────────────────────────────────────────────────────────
  const modal = document.createElement('div');
  modal.className = 'integrations-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-label', 'Integrations');

  // ── Header ────────────────────────────────────────────────────────────────
  const header = document.createElement('div');
  header.className = 'integrations-modal__header';

  const title = document.createElement('h2');
  title.className = 'integrations-modal__title';
  title.textContent = 'Integrations';

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'integrations-modal__close';
  closeBtn.setAttribute('aria-label', 'Close integrations');
  closeBtn.innerHTML = '&times;';
  closeBtn.addEventListener('click', _close);

  header.appendChild(title);
  header.appendChild(closeBtn);

  // ── Body (card grid) ──────────────────────────────────────────────────────
  const body = document.createElement('div');
  body.className = 'integrations-modal__body';

  const grid = document.createElement('div');
  grid.className = 'integration-cards-grid';
  body.appendChild(grid);

  // ── Footer hint ───────────────────────────────────────────────────────────
  const footer = document.createElement('p');
  footer.className = 'integrations-modal__footer';
  footer.textContent = 'OAuth connections redirect here and reconnect automatically.';

  modal.appendChild(header);
  modal.appendChild(body);
  modal.appendChild(footer);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  // Populate cards async — modal is already visible, cards stream in
  _populateGrid(grid);

  // ── Backdrop click closes ─────────────────────────────────────────────────
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) _close();
  });

  // ── ESC closes ────────────────────────────────────────────────────────────
  const _onKeyDown = (e) => {
    if (e.key === 'Escape') {
      _close();
      document.removeEventListener('keydown', _onKeyDown);
    }
  };
  document.addEventListener('keydown', _onKeyDown);

  // Focus the close button for keyboard accessibility
  setTimeout(() => closeBtn.focus(), 60);
}

// ── Private ───────────────────────────────────────────────────────────────────

function _close() {
  const overlay = document.getElementById('integrations-modal-overlay');
  if (!overlay) return;
  overlay.classList.add('integrations-modal-overlay--out');
  // Remove after exit animation completes (200 ms)
  setTimeout(() => overlay.remove(), 220);
}
