// integration-card.js
// Renders a single integration provider card in two states: disconnected and connected.
//
// Provider shape:   { id: string, name: string, tagline: string }
// ConnectedData:    null | { id: number, workspace_name: string | null, connected_at: string }
// ConfigStatus:     null | { ever_configured: boolean, configured_keys: Record<string, boolean> }

import { openCredentialEntryModal } from './credential-entry-modal.js';
import { openJiraTeamPicker } from './jira-team-picker.js';

const _PROVIDER_FIELDS = {
  slack: [
    { key: 'client_id', label: 'Client ID', helper: "From your Slack app's Basic Information page.", sensitive: true },
    { key: 'client_secret', label: 'Client Secret', helper: 'Keep this secret — never share it.', sensitive: true },
    { key: 'signing_secret', label: 'Signing Secret', helper: 'Used to verify incoming event payloads from Slack.', sensitive: true },
  ],
  gcal: [
    { key: 'client_id', label: 'Client ID', helper: 'From Google Cloud Console → APIs & Services → Credentials.', sensitive: true },
    { key: 'client_secret', label: 'Client Secret', helper: 'Keep this secret — never share it.', sensitive: true },
  ],
  jira: [
    { key: 'site_url', label: 'Atlassian Site URL', helper: 'e.g. https://yourorg.atlassian.net', sensitive: false },
    { key: 'email', label: 'Email', helper: 'The email tied to your Atlassian account.', sensitive: false },
    { key: 'api_token', label: 'API Token', helper: 'Get one at id.atlassian.com → API tokens.', sensitive: true },
  ],
  // Granola: Connect button reads local Granola.app state first; OAuth is
  // the fallback for users without the desktop app. Both OAuth fields optional —
  // leave blank if Granola.app is installed and signed in.
  granola: [
    { key: 'client_id', label: 'OAuth Client ID', helper: 'Optional — only if Granola.app is not installed. Register at mcp-auth.granola.ai.', sensitive: false },
    { key: 'client_secret', label: 'OAuth Client Secret', helper: 'Optional — only if Granola.app is not installed.', sensitive: true },
  ],
  // SFDC-1: Client Credentials (server-to-server) — no redirect URL, so this
  // is a config-only provider like Jira, not a real OAuth dance like Slack/GCal.
  // Reddit: app-only OAuth (client credentials) for the parent-sentiment watch.
  // Read-only, public posts. No user login, so no redirect flow here.
  reddit: [
    { key: 'client_id', label: 'Client ID', helper: 'The string shown UNDER the app name at reddit.com/prefs/apps.', sensitive: true },
    { key: 'client_secret', label: 'Client Secret', helper: 'The value labelled "secret". Keep this private.', sensitive: true },
    { key: 'user_agent', label: 'User Agent', helper: 'Reddit requires a descriptive one and rate-limits generic ones harder. Format: python:amira-sentiment-watch:v1.0 (by /u/your-username)', sensitive: false },
  ],
  salesforce: [
    { key: 'client_id', label: 'Client ID (Consumer Key)', helper: 'From Neil — the Connected App configured for the Client Credentials flow.', sensitive: true },
    { key: 'client_secret', label: 'Client Secret (Consumer Secret)', helper: 'Keep this secret — never share it.', sensitive: true },
    { key: 'login_url', label: 'Login URL', helper: 'https://login.salesforce.com for production, https://test.salesforce.com for a sandbox, or your My Domain URL.', sensitive: false },
  ],
};

function _formatDate(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
  } catch {
    return isoString;
  }
}

function _logoEl(provider) {
  const el = document.createElement('div');
  el.className = `integration-card__logo integration-card__logo--${provider.id}`;
  el.setAttribute('aria-hidden', 'true');
  el.textContent = provider.name.charAt(0).toUpperCase();
  return el;
}

function _showTestResult(span, ok) {
  span.textContent = ok ? '✓' : '✗';
  span.dataset.testResult = ok ? 'ok' : 'fail';
  span.classList.remove('hidden');
  setTimeout(() => {
    span.textContent = '';
    span.removeAttribute('data-test-result');
    span.classList.add('hidden');
  }, 2000);
}

async function _connectProvider(provider) {
  // Granola: try local desktop-app path first; fall back to OAuth if unavailable.
  if (provider.id === 'granola') {
    const localRes = await fetch('/api/integrations/granola/connect-local', { method: 'POST' });
    if (localRes.ok) {
      // Local connect succeeded — reload page so the card shows as connected.
      window.location.href = '/?granola_connected=1';
      return;
    }
    // Local failed — fall through to OAuth start
    // Wire shape is flat ({error, fallback}) because main.py unwraps
    // HTTPException.detail when it's a dict. Nested shape kept for compat.
    const errBody = await localRes.json().catch(() => ({}));
    const nested = typeof errBody?.detail === 'object' && errBody.detail !== null ? errBody.detail : {};
    const fallback = errBody?.fallback ?? nested.fallback;
    if (fallback !== 'oauth') {
      throw new Error(errBody?.error ?? nested.error ?? `HTTP ${localRes.status}`);
    }
    // Fall through to OAuth
    const oauthRes = await fetch('/api/integrations/granola/oauth/start');
    if (!oauthRes.ok) {
      const ob = await oauthRes.json().catch(() => ({}));
      throw new Error(ob.detail || ob.error || `OAuth start failed: HTTP ${oauthRes.status}`);
    }
    const { url } = await oauthRes.json();
    window.location.href = url;
    return;
  }

  const res = await fetch(`/api/integrations/${provider.id}/oauth/start`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  const { url } = await res.json();
  window.location.href = url;
}

async function _disconnectProvider(container, provider, connectedId) {
  const res = await fetch(`/api/integrations/${connectedId}`, { method: 'DELETE' });
  if (res.ok || res.status === 204) {
    // Re-render disconnected state
    renderIntegrationCard(container, provider, null);
  }
}

async function _verifyProvider(provider, testResultSpan) {
  try {
    const res = await fetch(`/api/integrations/${provider.id}/verify`);
    _showTestResult(testResultSpan, res.ok);
  } catch {
    _showTestResult(testResultSpan, false);
  }
}

/**
 * Render a single integration provider card into `container`.
 *
 * @param {HTMLElement} container
 * @param {{ id: string, name: string, tagline: string }} provider
 * @param {{ id: number, workspace_name: string|null, connected_at: string, status?: string, last_refresh_attempt_at?: string|null }|null} connectedData
 * @param {{ ever_configured: boolean, configured_keys: Record<string,boolean> }|null} [configStatus]
 */
export function renderIntegrationCard(container, provider, connectedData, configStatus = null) {
  container.innerHTML = '';

  const needsReauth = connectedData !== null && connectedData.status === 'needs_reauth';

  const card = document.createElement('div');
  card.className = 'integration-card';

  // ── Header ──────────────────────────────────────────────────────────────────
  const header = document.createElement('div');
  header.className = 'integration-card__header';

  header.appendChild(_logoEl(provider));

  const meta = document.createElement('div');
  meta.className = 'integration-card__meta';

  const nameEl = document.createElement('div');
  nameEl.className = 'integration-card__name';
  nameEl.textContent = provider.name;

  const taglineEl = document.createElement('div');
  taglineEl.className = 'integration-card__tagline';
  taglineEl.textContent = provider.tagline;

  meta.appendChild(nameEl);
  meta.appendChild(taglineEl);
  header.appendChild(meta);

  if (needsReauth) {
    const pill = document.createElement('div');
    pill.className = 'integration-card__status integration-card__status--reauth';
    pill.innerHTML = '<span class="integration-card__status-dot integration-card__status-dot--reauth" aria-hidden="true"></span>Needs reconnect';
    header.appendChild(pill);
  } else if (connectedData) {
    const pill = document.createElement('div');
    pill.className = 'integration-card__status';
    pill.innerHTML = '<span class="integration-card__status-dot" aria-hidden="true"></span>Connected';
    header.appendChild(pill);
  } else if (configStatus !== null && !configStatus.ever_configured) {
    const needsPill = document.createElement('div');
    needsPill.className = 'integration-card__needs-setup';
    needsPill.textContent = 'Needs setup';
    header.appendChild(needsPill);
  }

  // Gear button — visible only when the provider HAS inline credential fields.
  // Granola (and other zero-field providers) hide the gear since clicking it
  // would show an empty modal — confusing. Those providers configure via
  // the Connect button directly (local-state or OAuth bounce).
  const _fields = _PROVIDER_FIELDS[provider.id] || [];
  if (_fields.length > 0) {
    const gearBtn = document.createElement('button');
    gearBtn.type = 'button';
    gearBtn.className = 'integration-card__gear-btn';
    gearBtn.setAttribute('aria-label', `Configure ${provider.name} credentials`);
    gearBtn.innerHTML = '&#9881;';
    gearBtn.addEventListener('click', () => {
      openCredentialEntryModal({
        provider: provider.id,
        fields: _fields,
        onSaved: () => renderIntegrationCard(container, provider, connectedData, null),
      });
    });
    header.appendChild(gearBtn);
  }

  card.appendChild(header);

  // ── Body ─────────────────────────────────────────────────────────────────────
  if (needsReauth) {
    // Needs-reauth state — amber banner, reconnect CTA, optional try-refresh
    const body = document.createElement('div');
    body.className = 'integration-card__body integration-card__body--reauth';

    const reauthMsg = document.createElement('div');
    reauthMsg.className = 'integration-card__reauth-msg';
    reauthMsg.textContent =
      'The connection expired and needs to be re-authorized. Click Reconnect to refresh your access.';
    body.appendChild(reauthMsg);

    if (connectedData.last_refresh_attempt_at) {
      const attemptEl = document.createElement('div');
      attemptEl.className = 'integration-card__connected-since';
      attemptEl.textContent = `Last refresh attempt: ${_formatDate(connectedData.last_refresh_attempt_at)}`;
      body.appendChild(attemptEl);
    }

    card.appendChild(body);

    // ── Actions ──────────────────────────────────────────────────────────────
    const actions = document.createElement('div');
    actions.className = 'integration-card__actions';

    // Primary CTA — same OAuth flow as initial Connect
    const reconnectBtn = document.createElement('button');
    reconnectBtn.type = 'button';
    reconnectBtn.className = 'integration-card__connect-btn integration-card__connect-btn--reauth';
    reconnectBtn.textContent = 'Reconnect';
    reconnectBtn.addEventListener('click', async () => {
      reconnectBtn.disabled = true;
      reconnectBtn.textContent = 'Redirecting…';
      try {
        await _connectProvider(provider);
      } catch (err) {
        reconnectBtn.disabled = false;
        reconnectBtn.textContent = 'Reconnect';
        const errEl = document.createElement('span');
        errEl.className = 'integration-card__error';
        errEl.textContent = err.message || 'Could not start reconnect.';
        actions.appendChild(errEl);
        setTimeout(() => errEl.remove(), 4000);
      }
    });

    // Secondary affordance — try the refresh endpoint without a full OAuth dance
    const tryRefreshBtn = document.createElement('button');
    tryRefreshBtn.type = 'button';
    tryRefreshBtn.className = 'integration-card__try-refresh-btn';
    tryRefreshBtn.textContent = 'Try refresh';
    const refreshResultSpan = document.createElement('span');
    refreshResultSpan.className = 'integration-card__refresh-result hidden';
    refreshResultSpan.setAttribute('aria-live', 'polite');

    tryRefreshBtn.addEventListener('click', async () => {
      tryRefreshBtn.disabled = true;
      tryRefreshBtn.textContent = 'Trying…';
      refreshResultSpan.textContent = '';
      refreshResultSpan.className = 'integration-card__refresh-result hidden';
      try {
        const res = await fetch(`/api/integrations/${connectedData.id}/refresh`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (data.outcome === 'refreshed') {
          // Re-render as active
          renderIntegrationCard(container, provider, {
            ...connectedData,
            status: 'active',
            last_refresh_attempt_at: null,
          }, configStatus);
          return;
        }
        // refresh_token_expired or anything else — stay in needs_reauth, show inline error
        refreshResultSpan.textContent =
          data.outcome === 'refresh_token_expired'
            ? 'Refresh failed — full reconnect needed.'
            : `Refresh failed (${data.outcome || 'unknown'}).`;
        refreshResultSpan.className = 'integration-card__refresh-result integration-card__refresh-result--error';
      } catch {
        refreshResultSpan.textContent = 'Refresh request failed. Try again.';
        refreshResultSpan.className = 'integration-card__refresh-result integration-card__refresh-result--error';
      } finally {
        tryRefreshBtn.disabled = false;
        tryRefreshBtn.textContent = 'Try refresh';
      }
    });

    actions.appendChild(reconnectBtn);
    actions.appendChild(tryRefreshBtn);
    actions.appendChild(refreshResultSpan);
    card.appendChild(actions);
  } else if (connectedData) {
    // Connected state
    const body = document.createElement('div');
    body.className = 'integration-card__body';

    if (connectedData.workspace_name) {
      const workspaceEl = document.createElement('div');
      workspaceEl.className = 'integration-card__workspace-name';
      workspaceEl.textContent = connectedData.workspace_name;
      body.appendChild(workspaceEl);
    }

    const sinceEl = document.createElement('div');
    sinceEl.className = 'integration-card__connected-since';
    sinceEl.textContent = `Connected ${_formatDate(connectedData.connected_at)}`;
    body.appendChild(sinceEl);

    card.appendChild(body);

    // ── Actions ──────────────────────────────────────────────────────────────
    const actions = document.createElement('div');
    actions.className = 'integration-card__actions';

    const disconnectBtn = document.createElement('button');
    disconnectBtn.type = 'button';
    disconnectBtn.className = 'integration-card__disconnect-btn';
    disconnectBtn.textContent = 'Disconnect';
    disconnectBtn.addEventListener('click', () => {
      _disconnectProvider(container, provider, connectedData.id);
    });

    const testResultSpan = document.createElement('span');
    testResultSpan.className = 'integration-card__test-result hidden';
    testResultSpan.setAttribute('aria-live', 'polite');

    const testLink = document.createElement('button');
    testLink.type = 'button';
    testLink.className = 'integration-card__test-link';
    testLink.textContent = 'Test connection';
    testLink.addEventListener('click', () => {
      _verifyProvider(provider, testResultSpan);
    });

    actions.appendChild(disconnectBtn);
    actions.appendChild(testLink);
    actions.appendChild(testResultSpan);

    if (provider.id === 'jira') {
      const manageTeamBtn = document.createElement('button');
      manageTeamBtn.type = 'button';
      manageTeamBtn.className = 'integration-card__manage-team-btn';
      manageTeamBtn.textContent = 'Manage team';
      manageTeamBtn.addEventListener('click', () => openJiraTeamPicker());
      actions.appendChild(manageTeamBtn);
    }

    card.appendChild(actions);
  } else {
    // Disconnected state
    const actions = document.createElement('div');
    actions.className = 'integration-card__actions';

    const connectBtn = document.createElement('button');
    connectBtn.type = 'button';
    connectBtn.className = 'integration-card__connect-btn';
    connectBtn.textContent = `Connect ${provider.name}`;
    // Zero-field providers (e.g. Granola — local-state / OAuth, no inline creds)
    // skip the "needs setup" gate: their Connect button IS the setup. The
    // gear is hidden for them too, so gating on ever_configured would dead-end.
    const hasInlineFields = _fields.length > 0;
    const notConfigured =
      hasInlineFields && configStatus !== null && !configStatus.ever_configured;
    if (notConfigured) {
      connectBtn.disabled = true;
      connectBtn.title = 'Enter credentials via the ⚙ gear button first.';
    }
    connectBtn.addEventListener('click', async () => {
      connectBtn.disabled = true;
      connectBtn.textContent = 'Redirecting…';
      try {
        await _connectProvider(provider);
      } catch (err) {
        connectBtn.disabled = false;
        connectBtn.textContent = `Connect ${provider.name}`;
        // surface error inline
        const errEl = document.createElement('span');
        errEl.className = 'integration-card__error';
        errEl.textContent = err.message || 'Could not start connection.';
        actions.appendChild(errEl);
        setTimeout(() => errEl.remove(), 4000);
      }
    });

    actions.appendChild(connectBtn);
    card.appendChild(actions);
  }

  container.appendChild(card);
}
