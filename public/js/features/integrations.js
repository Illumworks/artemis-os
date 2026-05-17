// integrations.js
// Integrations page orchestrator.
//
// Responsibilities:
//   - init(container) called when the integrations view becomes active
//   - Fetches GET /api/integrations and renders provider cards
//   - Checks window.location.search for ?slack_connected=1 → one-time success toast
//   - cleanup() tears down any listeners

import { renderIntegrationCard } from '../components/integration-card.js';

// ── Known providers ───────────────────────────────────────────────────────────

const PROVIDERS = [
  {
    id: 'slack',
    name: 'Slack',
    tagline: 'Post messages, read channels, get mentioned.',
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
    // Strip the query param from the URL without a full reload
    const clean = window.location.pathname;
    window.history.replaceState({}, '', clean);
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

  // Render a card per known provider
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

    renderIntegrationCard(cardContainer, provider, connectedData);
  }
}
