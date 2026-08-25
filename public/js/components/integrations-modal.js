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
import { renderApiConnectorsSection } from './api-connectors.js';

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
  {
    id: 'jira',
    name: 'Jira',
    tagline: 'Browse issues, log work, and create tickets.',
  },
  {
    id: 'granola',
    name: 'Granola',
    tagline: 'Meeting transcripts and notes.',
  },
  {
    id: 'salesforce',
    name: 'Salesforce',
    tagline: 'Read-only: customer accounts, open opportunities, sales contact history.',
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
  if (params.get('jira_connected') === '1') {
    _showToast('Jira connected successfully.');
    window.history.replaceState({}, '', window.location.pathname);
  }
  if (params.get('granola_connected') === '1') {
    _showToast('Granola connected successfully.');
    window.history.replaceState({}, '', window.location.pathname);
  }
  if (params.get('google_connected') === '1') {
    _showToast('Google connected successfully.');
    window.history.replaceState({}, '', window.location.pathname);
  }
  if (params.get('granola_error')) {
    _showToast(`Granola connection failed: ${params.get('granola_error')}`, true);
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
          status: match.status,
          last_refresh_attempt_at: match.last_refresh_attempt_at || null,
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

  // Marketing Google (Docs) is a separate credential (google_credentials, purpose=marketing),
  // not a standard /api/integrations provider — render a custom card after the grid.
  await _appendMarketingGoogleCard(grid);
}

async function _appendMarketingGoogleCard(grid) {
  const cardContainer = document.createElement('div');
  grid.appendChild(cardContainer);
  let status = null;
  try {
    const res = await fetch('/api/google/status?purpose=marketing');
    if (res.ok) status = await res.json();
  } catch { /* non-fatal — render as disconnected */ }
  _renderMarketingGoogleCard(cardContainer, status);
}

function _renderMarketingGoogleCard(container, status) {
  container.innerHTML = '';
  const connected = status?.connected === true;
  const email = status?.email ?? null;

  const card = document.createElement('div');
  card.className = `integration-card${connected ? ' integration-card--connected' : ''}`;

  const header = document.createElement('div');
  header.className = 'integration-card__header';
  const logo = document.createElement('div');
  logo.className = 'integration-card__logo';
  logo.setAttribute('aria-hidden', 'true');
  logo.textContent = 'G';
  header.appendChild(logo);
  const meta = document.createElement('div');
  meta.className = 'integration-card__meta';
  const nameEl = document.createElement('div');
  nameEl.className = 'integration-card__name';
  nameEl.textContent = 'Marketing Google (Docs)';
  const tagline = document.createElement('div');
  tagline.className = 'integration-card__tagline';
  tagline.textContent = email
    ? `Connected as ${email}`
    : "Marketing account for Callie's Docs access.";
  meta.appendChild(nameEl);
  meta.appendChild(tagline);
  header.appendChild(meta);
  if (connected) {
    const pill = document.createElement('div');
    pill.className = 'integration-card__status';
    pill.innerHTML = '<span class="integration-card__status-dot" aria-hidden="true"></span>Connected';
    header.appendChild(pill);
  }
  card.appendChild(header);

  const actions = document.createElement('div');
  actions.className = 'integration-card__actions';
  const startConnect = () => { window.location.href = '/api/google/oauth/start?purpose=marketing'; };
  if (connected) {
    const reconnectBtn = document.createElement('button');
    reconnectBtn.type = 'button';
    reconnectBtn.className = 'integration-card__connect-btn';
    reconnectBtn.textContent = 'Reconnect';
    reconnectBtn.addEventListener('click', startConnect);
    const disconnectBtn = document.createElement('button');
    disconnectBtn.type = 'button';
    disconnectBtn.className = 'integration-card__disconnect-btn';
    disconnectBtn.textContent = 'Disconnect';
    disconnectBtn.addEventListener('click', async () => {
      disconnectBtn.disabled = true;
      disconnectBtn.textContent = 'Disconnecting…';
      try {
        const res = await fetch('/api/google/disconnect?purpose=marketing', { method: 'POST' });
        if (res.ok) {
          _showToast('Marketing Google account disconnected.');
          _renderMarketingGoogleCard(container, null);
        } else {
          _showToast('Disconnect failed. Please try again.', true);
          disconnectBtn.disabled = false;
          disconnectBtn.textContent = 'Disconnect';
        }
      } catch (e) {
        _showToast(`Disconnect failed: ${e}`, true);
        disconnectBtn.disabled = false;
        disconnectBtn.textContent = 'Disconnect';
      }
    });
    actions.appendChild(reconnectBtn);
    actions.appendChild(disconnectBtn);
  } else {
    const connectBtn = document.createElement('button');
    connectBtn.type = 'button';
    connectBtn.className = 'integration-card__connect-btn';
    connectBtn.textContent = 'Connect Marketing Google';
    connectBtn.addEventListener('click', startConnect);
    actions.appendChild(connectBtn);
  }
  card.appendChild(actions);
  container.appendChild(card);
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

  // ── API Connectors ────────────────────────────────────────────────────────
  // Key-based credentials (Vista Social, Starbridge, model providers). These
  // are not OAuth, so they get their own section rather than a provider card.
  const connTitle = document.createElement('h3');
  connTitle.className = 'integrations-modal__section-title';
  connTitle.textContent = 'API Connectors';
  body.appendChild(connTitle);

  const connSubtitle = document.createElement('p');
  connSubtitle.className = 'integrations-modal__section-subtitle';
  connSubtitle.textContent =
    'Key-based credentials used by agents at runtime. Encrypted at rest.';
  body.appendChild(connSubtitle);

  const connSection = document.createElement('div');
  connSection.id = 'api-connectors-section';
  body.appendChild(connSection);

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
  renderApiConnectorsSection(connSection);

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
