// integrations.js
// Integrations page orchestrator.
//
// Responsibilities:
//   - init(container) called when the integrations view becomes active
//   - Fetches GET /api/integrations and renders provider cards
//   - Fetches GET /api/connectors and renders API Connectors section
//   - Renders a Marketing Google (Docs) card backed by GET /api/google/status?purpose=marketing
//   - Checks window.location.search for ?slack_connected=1 → one-time success toast
//   - cleanup() tears down any listeners

import { renderIntegrationCard } from '../components/integration-card.js';
import { renderApiConnectorsSection } from '../components/api-connectors.js';

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
    id: 'reddit',
    name: 'Reddit',
    tagline: 'Read-only: public posts for the parent-sentiment watch.',
  },
  {
    id: 'salesforce',
    name: 'Salesforce',
    tagline: 'Read-only: customer accounts, open opportunities, sales contact history.',
  },
];

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

// ── Module state ──────────────────────────────────────────────────────────────

let _container = null;

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Initialize the integrations page.
 * @param {HTMLElement} container - DOM element to render into
 */
export async function init(container) {
  _container = container;
  _checkConnectionToast();
  await _loadAndRender();
}

/**
 * Tear down listeners and reset module state.
 */
export function cleanup() {
  _container = null;
}

// ── Private ───────────────────────────────────────────────────────────────────

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
  if (params.get('gmail_connected') === '1') {
    _showToast('Gmail connected successfully.');
    window.history.replaceState({}, '', window.location.pathname);
  }
  if (params.get('granola_connected') === '1') {
    _showToast('Granola connected successfully.');
    window.history.replaceState({}, '', window.location.pathname);
  }
  if (params.get('granola_error')) {
    _showToast(`Granola connection failed: ${params.get('granola_error')}`, true);
    window.history.replaceState({}, '', window.location.pathname);
  }
  if (params.get('google_connected') === '1') {
    _showToast('Marketing Google account connected successfully.');
    window.history.replaceState({}, '', window.location.pathname);
  }
}

async function _loadAndRender() {
  if (!_container) return;

  _container.innerHTML = '';

  const page = document.createElement('div');
  page.className = 'integrations-page';

  const title = document.createElement('h2');
  title.className = 'integrations-section-title';
  title.textContent = 'Integrations';
  page.appendChild(title);

  const grid = document.createElement('div');
  grid.className = 'integration-cards-grid';
  page.appendChild(grid);

  // Marketing Google (Docs) section — OAuth credential separate from personal Google
  const mktgGoogleTitle = document.createElement('h2');
  mktgGoogleTitle.className = 'integrations-section-title';
  mktgGoogleTitle.textContent = 'Marketing Google (Docs)';
  page.appendChild(mktgGoogleTitle);

  const mktgGoogleSubtitle = document.createElement('p');
  mktgGoogleSubtitle.className = 'integrations-section-subtitle';
  mktgGoogleSubtitle.textContent = 'Connect the marketing Google account (amiracentral@) so Callie can read and write campaign documents.';
  page.appendChild(mktgGoogleSubtitle);

  const mktgGoogleSection = document.createElement('div');
  mktgGoogleSection.id = 'marketing-google-section';
  page.appendChild(mktgGoogleSection);

  // API Connectors section below OAuth integrations
  const connTitle = document.createElement('h2');
  connTitle.className = 'integrations-section-title';
  connTitle.textContent = 'API Connectors';
  page.appendChild(connTitle);

  const connSubtitle = document.createElement('p');
  connSubtitle.className = 'integrations-section-subtitle';
  connSubtitle.textContent = 'API key–based credentials used by agents at runtime. Agents reference connectors by ID; credentials are encrypted at rest.';
  page.appendChild(connSubtitle);

  const connSection = document.createElement('div');
  connSection.id = 'api-connectors-section';
  page.appendChild(connSection);

  _container.appendChild(page);

  // Fetch connected integrations
  let connected = [];
  try {
    const res = await fetch('/api/integrations');
    if (res.ok) {
      connected = await res.json();
    }
  } catch {
    // Non-fatal: render as disconnected
  }

  // Render a card per known provider (fetch configStatus alongside)
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

  // Load and render Marketing Google (Docs) card
  await _loadAndRenderMarketingGoogle(mktgGoogleSection);

  // Load and render API Connectors
  await renderApiConnectorsSection(connSection);
}

// ── Marketing Google (Docs) section ───────────────────────────────────────────

async function _loadAndRenderMarketingGoogle(container) {
  let status = null;
  try {
    const res = await fetch('/api/google/status?purpose=marketing');
    if (res.ok) status = await res.json();
  } catch { /* non-fatal — render as disconnected */ }

  _renderMarketingGoogleCard(container, status);
}

function _renderMarketingGoogleCard(container, status) {
  container.innerHTML = '';

  const connected = status?.connected === true;
  const email = status?.email ?? null;

  const card = document.createElement('div');
  card.className = `integration-card marketing-google-card${connected ? ' marketing-google-card--connected' : ''}`;

  // ── Header ──
  const header = document.createElement('div');
  header.className = 'integration-card__header';

  const logo = document.createElement('div');
  logo.className = 'integration-card__logo integration-card__logo--google';
  logo.setAttribute('aria-hidden', 'true');
  logo.textContent = 'G';
  header.appendChild(logo);

  const meta = document.createElement('div');
  meta.className = 'integration-card__meta';

  const nameEl = document.createElement('div');
  nameEl.className = 'integration-card__name';
  nameEl.textContent = 'Marketing Google (Docs)';

  const taglineEl = document.createElement('div');
  taglineEl.className = 'integration-card__tagline';
  taglineEl.textContent = email
    ? `Connected as ${email}`
    : 'Connect the marketing Google account for Callie’s Docs access.';
  meta.appendChild(nameEl);
  meta.appendChild(taglineEl);
  header.appendChild(meta);

  if (connected) {
    const pill = document.createElement('div');
    pill.className = 'integration-card__status';
    pill.innerHTML = '<span class="integration-card__status-dot" aria-hidden="true"></span>Connected';
    header.appendChild(pill);
  }

  card.appendChild(header);

  // ── Actions ──
  const actions = document.createElement('div');
  actions.className = 'integration-card__actions';

  if (connected) {
    const reconnectBtn = document.createElement('button');
    reconnectBtn.type = 'button';
    reconnectBtn.className = 'integration-card__connect-btn';
    reconnectBtn.textContent = 'Reconnect';
    reconnectBtn.addEventListener('click', () => {
      window.location.href = '/api/google/oauth/start?purpose=marketing';
    });

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
    connectBtn.addEventListener('click', () => {
      window.location.href = '/api/google/oauth/start?purpose=marketing';
    });
    actions.appendChild(connectBtn);
  }

  card.appendChild(actions);
  container.appendChild(card);
}
