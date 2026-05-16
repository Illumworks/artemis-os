// Web Component: Connectors hub.
// User-facing setup surface for data sources (Calendar, Granola, Jira, etc.).
// Replaces the raw MCP Servers modal as the primary connectors UX —
// the raw MCP editor is still reachable via the Advanced section.
class ArtemisConnectorsModal extends HTMLElement {
  connectedCallback() {
    this.innerHTML = `
      <div id="connectors-modal" class="modal-overlay hidden" data-modal="connectors">
        <div class="modal connectors-modal" role="dialog" aria-labelledby="connectors-modal-title">
          <div class="modal-header">
            <div>
              <div class="connectors-modal-eyebrow">Data sources</div>
              <h3 id="connectors-modal-title">Connectors</h3>
            </div>
            <button class="modal-close" data-connectors-action="close" aria-label="Close">&times;</button>
          </div>
          <p class="connectors-modal-lede">
            Connect Artemis to the systems that hold your real schedule, meetings, work queue, and goals.
            Each connector below shows its current status. Artemis stays honest about what is and isn't wired.
          </p>
          <div class="connectors-list" data-connectors-list></div>
          <details class="connectors-advanced">
            <summary>Advanced — raw MCP servers</summary>
            <p class="connectors-modal-footnote">
              MCP servers are the underlying transport for several connectors. Editing them directly is
              for power users — most setup should happen through the connector cards above.
            </p>
            <button type="button" class="modal-btn-cancel" data-connectors-action="open-mcp">Open raw MCP server editor</button>
          </details>
        </div>
      </div>
    `;

    this.overlay = this.querySelector('#connectors-modal');
    this.list = this.querySelector('[data-connectors-list]');

    this.overlay.addEventListener('click', (e) => {
      if (e.target === this.overlay) this.close();
    });
    this.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-connectors-action]');
      if (!btn) return;
      const action = btn.dataset.connectorsAction;
      if (action === 'close') this.close();
      else if (action === 'open-mcp') {
        const mcp = document.querySelector('artemis-mcp-modal');
        mcp?.querySelector('#mcp-modal')?.classList.remove('hidden');
        this.close();
      } else if (action === 'copy-config-path') {
        const path = btn.dataset.path || '';
        if (path && navigator.clipboard?.writeText) {
          navigator.clipboard.writeText(path).then(() => {
            const original = btn.textContent;
            btn.textContent = 'Copied';
            setTimeout(() => { btn.textContent = original; }, 1400);
          }).catch(() => {});
        }
      }
    });
  }

  async open(scopeHint = '') {
    if (!this.overlay) return;
    this.overlay.classList.remove('hidden');
    this.list.innerHTML = `<p class="connectors-modal-footnote">Loading connector status...</p>`;
    const connectors = await this.loadConnectors();
    this.render(connectors, scopeHint);
  }

  close() {
    this.overlay?.classList.add('hidden');
  }

  async loadConnectors() {
    let calendarOverview = null;
    let jiraOverview = null;
    let granolaOverview = null;
    let googleOverview = null;
    let slackOverview = null;
    let slackTokenStatus = null;
    let providerStatus = null;
    let slackCache = null;
    try {
      const [calRes, jiraRes, granolaRes, googleRes, slackRes, slackTokenRes, provRes, slackCacheRes] = await Promise.allSettled([
        fetch('/api/calendar/overview'),
        fetch('/api/jira/overview'),
        fetch('/api/granola/overview'),
        fetch('/api/google/overview'),
        fetch('/api/slack/overview'),
        fetch('/api/slack/token-status'),
        fetch('/api/providers/status'),
        fetch('/api/slack/cache'),
      ]);
      if (calRes.status === 'fulfilled' && calRes.value.ok) calendarOverview = await calRes.value.json();
      if (jiraRes.status === 'fulfilled' && jiraRes.value.ok) jiraOverview = await jiraRes.value.json();
      if (granolaRes.status === 'fulfilled' && granolaRes.value.ok) granolaOverview = await granolaRes.value.json();
      if (googleRes.status === 'fulfilled' && googleRes.value.ok) googleOverview = await googleRes.value.json();
      if (slackRes.status === 'fulfilled' && slackRes.value.ok) slackOverview = await slackRes.value.json();
      if (slackTokenRes.status === 'fulfilled' && slackTokenRes.value.ok) slackTokenStatus = await slackTokenRes.value.json();
      if (provRes.status === 'fulfilled' && provRes.value.ok) providerStatus = await provRes.value.json();
      if (slackCacheRes.status === 'fulfilled' && slackCacheRes.value.ok) slackCache = await slackCacheRes.value.json();
    } catch {}

    const googleConnected = Boolean(googleOverview?.connected);
    const googleHasContactsScope = Boolean(googleOverview?.hasContactsScope);
    const googleDocsImportReady = Boolean(googleOverview?.docsImportReady);
    const googleDocsExportReady = Boolean(googleOverview?.docsExportReady);
    const googleStatus = googleConnected && !googleHasContactsScope
      ? {
          tone: 'setup',
          label: 'Reconnect needed',
          detail: `Connected as ${googleOverview.email || 'Google'} but missing one or more Google workspace scopes. Calendar is wired; reconnect below to finish Docs and attendee access.`,
          needsContactsReconnect: true,
        }
      : googleConnected
        ? {
            tone: 'live',
            label: 'Connected',
            detail: `Google workspace active${googleOverview.email ? ` as ${googleOverview.email}` : ''} — ${calendarOverview?.today?.meetingsCount ?? 0} calendar event(s) today, Docs import ${googleDocsImportReady ? 'ready' : 'not ready'}, Docs export ${googleDocsExportReady ? 'ready' : 'not ready'}.`,
          }
        : googleOverview?.hasOauth
          ? { tone: 'setup', label: 'Reconnect needed', detail: 'OAuth credentials saved but no active session. Click Connect with Google.' }
          : { tone: 'setup', label: 'Not connected', detail: 'Register an OAuth app at console.cloud.google.com, paste Client ID + Secret below, then Connect with Google.' };

    // Legacy ICS status (shown in the ICS fallback section of the Google card)
    const icsStatus = calendarOverview?.status === 'ready' && calendarOverview?.providerLabel !== 'Google Calendar'
      ? { configured: true, detail: `ICS file: ${calendarOverview.sourceName || 'configured'}` }
      : { configured: false, detail: '' };

    const jiraStatus = jiraOverview?.connected
      ? {
          tone: 'live',
          label: 'Connected',
          detail: `Reading from ${jiraOverview.siteUrl || 'Atlassian'}${jiraOverview.authMethod === 'oauth' ? ' (OAuth)' : ' (API token)'}.`,
        }
      : jiraOverview?.error
        ? { tone: 'error', label: 'Connection error', detail: `Could not reach Jira: ${jiraOverview.error}` }
        : jiraOverview?.hasOauth
          ? { tone: 'setup', label: 'Reconnect needed', detail: 'OAuth credentials saved but no active session. Click Connect with Jira.' }
          : { tone: 'setup', label: 'Not connected', detail: 'Register an OAuth app at developer.atlassian.com, paste Client ID + Secret below, then Connect with Jira.' };

    const granolaStatus = granolaOverview?.connected
      ? {
          tone: 'live',
          label: 'Connected',
          detail: `Granola is active${granolaOverview.authMethod === 'oauth' ? ' (OAuth)' : ' (desktop app)'} — ${granolaOverview.recentMeetings?.length ?? 0} recent meeting(s) found. Transcripts will enrich today's Meetings view.`,
        }
      : granolaOverview?.reason === 'no_token' || granolaOverview?.reason === 'no_local_token'
        ? { tone: 'error', label: 'Not signed in', detail: granolaOverview.detail || 'No Granola credentials. Connect with Granola or sign in to the desktop app.' }
        : granolaOverview?.reason === 'api_error'
          ? { tone: 'error', label: 'Connection error', detail: `Could not reach Granola: ${granolaOverview.error}` }
          : { tone: 'setup', label: 'Not connected', detail: 'Connect with Granola via OAuth, or enable the toggle below if you have the Granola desktop app signed in locally.' };

    const userTokenConfigured = Boolean(slackTokenStatus?.configured);
    const slackStatus = userTokenConfigured
      ? {
          tone: 'live',
          label: 'Connected',
          detail: 'User token saved — Artemis can send and read Slack messages as you (no bot badge).',
        }
      : slackOverview?.connected
        ? {
            tone: 'setup',
            label: 'Read-only via Codex',
            detail: 'Reading Slack signals via Codex. Add your user token below to enable sending messages as yourself.',
          }
        : {
            tone: 'setup',
            label: 'Not connected',
            detail: 'Paste your Slack user token (xoxp-…) below to let Artemis send messages as you. Get one from api.slack.com → Your apps → OAuth & Permissions → OAuth Tokens.',
          };

    const geminiConfigured = Boolean(providerStatus?.gemini?.configured);
    const openrouterConfigured = Boolean(providerStatus?.openrouter?.configured);

    return [
      {
        id: 'google-calendar',
        name: 'Google Calendar + Docs',
        sub: 'calendar, Docs import, and Docs export via OAuth',
        status: googleStatus,
        configPath: googleOverview?.configPath || 'config/google-source.json',
        googleOverview,
        calendarOverview,
        icsStatus,
        howTo: null,
      },
      {
        id: 'granola',
        name: 'Granola',
        sub: 'meeting transcripts and notes',
        status: granolaStatus,
        configPath: granolaOverview?.configPath || 'config/granola-source.json',
        granolaOverview,
        howTo: null,
      },
      {
        id: 'jira',
        name: 'Jira',
        sub: 'execution queue and delivery risk',
        status: jiraStatus,
        configPath: jiraOverview?.configPath || 'config/jira-source.json',
        jiraOverview,
        jiraAllUsers: jiraOverview?.connected ? await fetch('/api/jira/assignable-users').then((r) => r.ok ? r.json() : []).catch(() => []) : [],
        howTo: null,
      },
      {
        id: 'slack',
        name: 'Slack',
        sub: 'send and receive messages as yourself — no bot badge',
        status: slackStatus,
        userTokenConfigured,
        slackCache,
      },
      {
        id: 'gemini',
        name: 'Google Gemini',
        sub: 'AI model provider — Gemini 2.0 Flash, 1.5 Pro',
        status: geminiConfigured
          ? { tone: 'live', label: 'Connected', detail: 'GEMINI_API_KEY is set. Gemini models are available in the session and agent model pickers.' }
          : { tone: 'setup', label: 'Not connected', detail: 'Paste your Gemini API key below. Get one free at aistudio.google.com → Get API key.' },
        configured: geminiConfigured,
      },
      {
        id: 'openrouter',
        name: 'OpenRouter',
        sub: 'AI model router — Claude, GPT-4o, Llama, Mistral & more',
        status: openrouterConfigured
          ? { tone: 'live', label: 'Connected', detail: 'OPENROUTER_API_KEY is set. OpenRouter models (including free-tier Llama + Mistral) are available in the session and agent model pickers.' }
          : { tone: 'setup', label: 'Not connected', detail: 'Paste your OpenRouter API key below. Free-tier models are available with no billing setup at openrouter.ai.' },
        configured: openrouterConfigured,
      },
    ];
  }

  render(connectors, scopeHint) {
    this.list.innerHTML = connectors.map((c) => {
      const highlighted = scopeHint === c.id;
      const body = c.id === 'jira'
        ? this._renderJiraForm(c, highlighted)
        : c.id === 'granola'
          ? this._renderGranolaForm(c, highlighted)
          : c.id === 'google-calendar'
            ? this._renderGoogleCalendarForm(c, highlighted)
            : c.id === 'slack'
              ? this._renderSlackForm(c, highlighted)
              : c.id === 'gemini'
                ? this._renderApiKeyForm(c, highlighted, 'gemini', 'aistudio.google.com → Get API key', 'AIza…')
                : c.id === 'openrouter'
                  ? this._renderApiKeyForm(c, highlighted, 'openrouter', 'openrouter.ai → Keys', 'sk-or-…')
                  : (c.howTo?.length ? `
          <details class="connector-card-howto" ${highlighted ? 'open' : ''}>
            <summary>Setup steps</summary>
            <ol>${c.howTo.map((step) => `<li>${escapeText(step)}</li>`).join('')}</ol>
            ${c.configPath ? `
              <div class="connector-card-config">
                <span class="connector-card-config-label">Config file</span>
                <code>${escapeText(c.configPath)}</code>
                <button type="button" class="modal-btn-cancel" data-connectors-action="copy-config-path" data-path="${escapeAttr(c.configPath)}">Copy path</button>
              </div>
            ` : ''}
          </details>
        ` : '');
      return `
        <article class="connector-card" data-connector-id="${escapeAttr(c.id)}" ${highlighted ? 'data-highlighted="1"' : ''}>
          <div class="connector-card-row">
            <div class="connector-card-titleblock">
              <h4>${escapeText(c.name)}</h4>
              <div class="connector-card-sub">${escapeText(c.sub)}</div>
            </div>
            <span class="connector-card-status" data-tone="${escapeAttr(c.status.tone)}">${escapeText(c.status.label)}</span>
          </div>
          <p class="connector-card-detail">${escapeText(c.status.detail)}</p>
          ${body}
        </article>
      `;
    }).join('');

    this.list.querySelectorAll('[data-jira-save]').forEach((form) => {
      form.addEventListener('submit', (e) => this._handleJiraSave(e));
    });
    this.list.querySelectorAll('[data-jira-connect]').forEach((btn) => {
      btn.addEventListener('click', (e) => this._handleJiraConnect(e));
    });
    this.list.querySelectorAll('[data-jira-disconnect]').forEach((btn) => {
      btn.addEventListener('click', (e) => this._handleJiraDisconnect(e));
    });
    this.list.querySelectorAll('[data-jira-team-save]').forEach((btn) => {
      btn.addEventListener('click', () => this._handleJiraTeamSave());
    });
    this.list.querySelectorAll('[data-jira-team-clear]').forEach((btn) => {
      btn.addEventListener('click', () => this._handleJiraTeamClear());
    });
    this.list.querySelectorAll('[data-granola-save]').forEach((form) => {
      form.addEventListener('submit', (e) => this._handleGranolaSave(e));
    });
    this.list.querySelectorAll('[data-granola-connect]').forEach((btn) => {
      btn.addEventListener('click', (e) => this._handleGranolaConnect(e));
    });
    this.list.querySelectorAll('[data-granola-disconnect]').forEach((btn) => {
      btn.addEventListener('click', (e) => this._handleGranolaDisconnect(e));
    });
    this.list.querySelectorAll('[data-google-save]').forEach((form) => {
      form.addEventListener('submit', (e) => this._handleGoogleSave(e));
    });
    this.list.querySelectorAll('[data-google-connect]').forEach((btn) => {
      btn.addEventListener('click', (e) => this._handleGoogleConnect(e));
    });
    this.list.querySelectorAll('[data-google-disconnect]').forEach((btn) => {
      btn.addEventListener('click', (e) => this._handleGoogleDisconnect(e));
    });
    this.list.querySelectorAll('[data-apikey-save]').forEach((form) => {
      form.addEventListener('submit', (e) => this._handleApiKeySave(e));
    });
    this.list.querySelectorAll('[data-apikey-clear]').forEach((btn) => {
      btn.addEventListener('click', (e) => this._handleApiKeyClear(e));
    });
    this.list.querySelectorAll('[data-slack-token-save]').forEach((form) => {
      form.addEventListener('submit', (e) => this._handleSlackTokenSave(e));
    });
    this.list.querySelectorAll('[data-slack-token-clear]').forEach((btn) => {
      btn.addEventListener('click', () => this._handleSlackTokenClear());
    });
    this.list.querySelectorAll('[data-slack-sync]').forEach((btn) => {
      btn.addEventListener('click', () => this._handleSlackSync());
    });
  }

  _renderSlackForm(c, open) {
    const configured = Boolean(c.userTokenConfigured);
    const cache = c.slackCache || {};
    const channelCount = cache.channels?.length ?? 0;
    const userCount = cache.users?.length ?? 0;
    const hasCacheData = channelCount > 0 || userCount > 0;
    const syncedAt = cache.syncedAt ? new Date(cache.syncedAt).toLocaleString() : null;

    return `
      <details class="connector-card-howto" ${open ? 'open' : ''}>
        <summary>Setup</summary>
        <p class="connector-card-detail" style="margin-top:0.5rem;font-size:0.85rem;opacity:0.8">
          ${configured
            ? 'User token is saved. Messages sent via Artemis will appear as you — no bot badge. Remove the token below to disconnect.'
            : 'A <strong>user token</strong> (xoxp-…) lets Artemis send Slack messages that appear to come directly from you. '
              + 'Get one at <strong>api.slack.com → Your Apps</strong> → select or create an app → OAuth &amp; Permissions → '
              + 'install to your workspace → copy the <em>User OAuth Token</em>.'}
        </p>
        <form data-slack-token-save class="connector-jira-form">
          <label class="connector-field">
            <span>User token</span>
            <input type="password" name="token" placeholder="xoxp-…"
              value="${configured ? '••••••••' : ''}" autocomplete="new-password" />
          </label>
          <div class="connector-field-row">
            <button type="submit" class="shell-action-btn">Save token</button>
            ${configured ? `<button type="button" class="modal-btn-cancel" data-slack-token-clear>Remove token</button>` : ''}
            <span class="connector-save-status" data-slack-token-status></span>
          </div>
        </form>
        ${configured ? `
          <hr style="margin:1rem 0;opacity:0.2" />
          <div class="connector-slack-cache-row">
            <div class="connector-slack-cache-info">
              ${hasCacheData
                ? `<span class="connector-cache-badge">${channelCount} channels · ${userCount} users</span>
                   <span class="connector-cache-synced">Last synced ${syncedAt}</span>`
                : `<span class="connector-cache-badge connector-cache-badge--empty">No cache yet</span>
                   <span class="connector-cache-synced">Sync to avoid rate limits when sending by channel name</span>`}
            </div>
            <button type="button" class="shell-action-btn connector-slack-sync-btn" data-slack-sync>
              ${hasCacheData ? 'Re-sync' : 'Sync Channels &amp; Users'}
            </button>
          </div>
          <span class="connector-save-status" data-slack-sync-status></span>
        ` : ''}
      </details>
    `;
  }

  async _handleSlackTokenSave(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const status = this.list.querySelector('[data-slack-token-status]');
    const token = form.querySelector('input[name="token"]')?.value || '';
    if (token === '••••••••') {
      if (status) { status.textContent = 'No change — token already saved.'; status.style.color = ''; }
      return;
    }
    if (!token.trim()) {
      if (status) { status.textContent = 'Enter a token first.'; status.style.color = 'var(--color-error, red)'; }
      return;
    }
    try {
      if (status) { status.textContent = 'Saving…'; status.style.color = ''; }
      const res = await fetch('/api/slack/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      if (status) { status.textContent = 'Saved.'; status.style.color = 'var(--color-success, green)'; }
      const fresh = await this.loadConnectors();
      this.render(fresh, 'slack');
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }

  async _handleSlackTokenClear() {
    const status = this.list.querySelector('[data-slack-token-status]');
    if (!confirm('Remove the Slack user token? Artemis will no longer be able to send Slack messages as you.')) return;
    try {
      if (status) { status.textContent = 'Removing…'; status.style.color = ''; }
      const res = await fetch('/api/slack/token', { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fresh = await this.loadConnectors();
      this.render(fresh, 'slack');
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }

  async _handleSlackSync() {
    const syncStatus = this.list.querySelector('[data-slack-sync-status]');
    const syncBtn = this.list.querySelector('[data-slack-sync]');
    if (syncBtn) { syncBtn.disabled = true; syncBtn.textContent = 'Syncing…'; }
    if (syncStatus) { syncStatus.textContent = 'Fetching channels and users — may take 15–20s for large workspaces…'; syncStatus.style.color = ''; }
    try {
      const res = await fetch('/api/slack/sync');
      const body = await res.json().catch(() => ({}));
      if (res.status === 429) {
        const secs = body.retryAfter || 60;
        if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = 'Re-sync'; }
        if (syncStatus) {
          syncStatus.textContent = `Slack rate limit hit — wait ${secs}s then click Re-sync.`;
          syncStatus.style.color = 'var(--color-error, red)';
        }
        return;
      }
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      if (syncStatus) {
        syncStatus.textContent = `Synced ${body.channels} channels and ${body.users} users.`;
        syncStatus.style.color = 'var(--color-success, green)';
      }
      const fresh = await this.loadConnectors();
      this.render(fresh, 'slack');
    } catch (err) {
      if (syncBtn) { syncBtn.disabled = false; syncBtn.textContent = 'Re-sync'; }
      if (syncStatus) { syncStatus.textContent = `Sync failed: ${err.message}`; syncStatus.style.color = 'var(--color-error, red)'; }
    }
  }

  _renderGranolaForm(c, open) {
    const ov = c.granolaOverview || {};
    const hasOauth = Boolean(ov.hasOauth);
    return `
      <details class="connector-card-howto" ${open ? 'open' : ''}>
        <summary>Setup</summary>
        <p class="connector-card-detail" style="margin-top:0.5rem;font-size:0.85rem;opacity:0.8">
          ${hasOauth
            ? 'Artemis is connected to Granola via OAuth. Tokens refresh automatically; disconnect to revoke this server’s access.'
            : 'Connect with Granola via OAuth (recommended for hosted/multi-user) or fall back to your local desktop app session.'}
        </p>
        <div class="connector-field-row" style="margin-top:0.75rem;gap:0.5rem;flex-wrap:wrap">
          ${hasOauth
            ? `<button type="button" class="modal-btn-cancel" data-granola-disconnect>Disconnect Granola</button>`
            : `<button type="button" class="shell-action-btn" data-granola-connect>Connect with Granola</button>`}
          <span class="connector-save-status" data-granola-oauth-status></span>
        </div>
        <hr style="margin:1rem 0;opacity:0.2" />
        <p class="connector-card-detail" style="margin:0;font-size:0.8rem;opacity:0.7">
          Fallback: use the locally-installed Granola desktop app session.
        </p>
        <form data-granola-save class="connector-jira-form">
          <div class="connector-field-row" style="align-items:center;gap:0.75rem">
            <label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;font-size:0.9rem">
              <input type="checkbox" name="enabled" value="true" ${ov.connected || ov.reason !== 'disabled' ? 'checked' : ''} style="width:auto" />
              Enable Granola transcript enrichment
            </label>
          </div>
          <div class="connector-field-row" style="margin-top:0.75rem">
            <button type="submit" class="shell-action-btn">Save</button>
            <span class="connector-save-status" data-granola-save-status></span>
          </div>
        </form>
        ${c.configPath ? `
          <div class="connector-card-config" style="margin-top:0.75rem">
            <span class="connector-card-config-label">Config file</span>
            <code>${escapeText(c.configPath)}</code>
            <button type="button" class="modal-btn-cancel" data-connectors-action="copy-config-path" data-path="${escapeAttr(c.configPath)}">Copy path</button>
          </div>
        ` : ''}
      </details>
    `;
  }

  _handleGranolaConnect(e) {
    e.preventDefault();
    const status = this.list.querySelector('[data-granola-oauth-status]');
    if (status) {
      status.textContent = 'Opening Granola sign-in…';
      status.style.color = '';
    }
    const popup = window.open(
      '/api/granola/oauth-start',
      'granola-oauth',
      'width=520,height=720',
    );
    if (!popup) {
      if (status) {
        status.textContent = 'Popup blocked — allow popups for this site.';
        status.style.color = 'var(--color-error, red)';
      }
      return;
    }
    const startedAt = Date.now();
    const poll = setInterval(async () => {
      const closed = popup.closed;
      const timedOut = Date.now() - startedAt > 5 * 60 * 1000;
      if (!closed && !timedOut) return;
      clearInterval(poll);
      if (timedOut && !closed) { try { popup.close(); } catch {} }
      try {
        if (status) status.textContent = 'Refreshing connector status…';
        const fresh = await this.loadConnectors();
        this.render(fresh, 'granola');
      } catch {
        if (status) {
          status.textContent = 'Could not refresh status — reload the page.';
          status.style.color = 'var(--color-error, red)';
        }
      }
    }, 1000);
  }

  async _handleGranolaDisconnect(e) {
    e.preventDefault();
    const status = this.list.querySelector('[data-granola-oauth-status]');
    if (status) { status.textContent = 'Disconnecting…'; status.style.color = ''; }
    try {
      const res = await fetch('/api/granola/oauth-disconnect', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fresh = await this.loadConnectors();
      this.render(fresh, 'granola');
    } catch (err) {
      if (status) {
        status.textContent = `Error: ${err.message}`;
        status.style.color = 'var(--color-error, red)';
      }
    }
  }

  async _handleGranolaSave(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const status = form.querySelector('[data-granola-save-status]');
    const checkbox = form.querySelector('input[name="enabled"]');
    const enabled = checkbox ? checkbox.checked : false;
    try {
      if (status) status.textContent = 'Saving…';
      const res = await fetch('/api/granola/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (status) {
        status.textContent = 'Saved. Reload Artemis to apply.';
        status.style.color = 'var(--color-success, green)';
      }
    } catch (err) {
      if (status) {
        status.textContent = `Error: ${err.message}`;
        status.style.color = 'var(--color-error, red)';
      }
    }
  }

  _renderGoogleCalendarForm(c, open) {
    const ov = c.googleOverview || {};
    const hasOauth = Boolean(ov.connected || ov.hasOauth);
    const hasClientCreds = Boolean(ov.hasOauth);
    const email = ov.email || '';
    const ics = c.icsStatus || {};
    const needsReconnect = Boolean(c.status?.needsContactsReconnect);
    const forceOpen = open || needsReconnect;
    const docsImportStatus = ov.connected
      ? (ov.docsImportReady ? 'Ready' : 'Reconnect needed')
      : 'Not connected';
    const docsExportStatus = ov.connected
      ? (ov.docsExportReady ? 'Ready' : 'Reconnect needed')
      : 'Not connected';
    return `
      <details class="connector-card-howto" ${forceOpen ? 'open' : ''}>
        <summary>Setup</summary>
        ${needsReconnect ? `
          <div style="margin-top:0.5rem;padding:0.6rem 0.75rem;background:var(--color-warning-bg,#fff8e1);border:1px solid var(--color-warning-border,#ffe082);border-radius:6px;font-size:0.85rem">
            <strong>Action required:</strong> Reconnect Google to grant the missing workspace scopes for attendee autocomplete and Google Docs flows. Click <em>Reconnect with Google</em> below — you'll be prompted to approve the new permissions.
          </div>
        ` : ''}
        <div class="connector-card-config" style="margin-top:0.75rem">
          <span class="connector-card-config-label">Capabilities</span>
          <code>Calendar: ${ov.connected ? 'Connected' : 'Not connected'}</code>
          <code>Docs import: ${docsImportStatus}</code>
          <code>Docs export: ${docsExportStatus}</code>
        </div>
        <p class="connector-card-detail" style="margin-top:0.5rem;font-size:0.85rem;opacity:0.8">
          ${hasOauth && ov.connected
            ? `Connected as <strong>${escapeText(email)}</strong>. Calendar events sync live, and this same connector powers Writing Studio Google Docs import/export. Disconnect to revoke access.`
            : 'Register an OAuth app at <a href="https://console.cloud.google.com" target="_blank" rel="noopener">console.cloud.google.com</a>, enable the Google Calendar API, set the redirect URI to <code>http://localhost:9009/api/google/oauth-callback</code>, then paste Client ID + Secret below.'}
        </p>
        <form data-google-save class="connector-jira-form">
          <label class="connector-field">
            <span>OAuth Client ID</span>
            <input type="text" name="oauthClientId" placeholder="From Google Cloud Console → Credentials → your OAuth client" value="" autocomplete="off" />
          </label>
          <label class="connector-field">
            <span>OAuth Client Secret</span>
            <input type="password" name="oauthClientSecret" placeholder="From Google Cloud Console → Credentials → your OAuth client" value="${hasClientCreds ? '••••••••' : ''}" autocomplete="new-password" />
          </label>
          <div class="connector-field-row">
            <button type="submit" class="shell-action-btn">Save credentials</button>
            <span class="connector-save-status" data-google-save-status></span>
          </div>
        </form>
        <div class="connector-field-row" style="margin-top:0.75rem;gap:0.5rem;flex-wrap:wrap">
          ${ov.connected
            ? `<button type="button" class="shell-action-btn" data-google-connect ${hasClientCreds ? '' : 'disabled title="Save Client ID + Secret first"'}>${needsReconnect ? 'Reconnect with Google' : 'Reconnect with Google'}</button>
               <button type="button" class="modal-btn-cancel" data-google-disconnect>Disconnect Google</button>`
            : `<button type="button" class="shell-action-btn" data-google-connect ${hasClientCreds ? '' : 'disabled title="Save Client ID + Secret first"'}>Connect with Google</button>`}
          <span class="connector-save-status" data-google-oauth-status></span>
        </div>
        <hr style="margin:1rem 0;opacity:0.2" />
        <details>
          <summary style="cursor:pointer;font-size:0.85rem;opacity:0.7">Fallback: Manual ICS export (no OAuth required)</summary>
          <p class="connector-card-detail" style="margin:0.5rem 0;font-size:0.8rem;opacity:0.7">
            In Google Calendar → Settings → "Settings for my calendars" → Integrate calendar → copy the "Secret address in iCal format". Save the file locally, then edit <code>calendar-source.json</code> with <code>"provider":"ics"</code> and <code>"icsPath"</code> set to that file path.
            ${ics.configured ? `<br/>Current ICS: ${escapeText(ics.detail)}` : ''}
          </p>
        </details>
        ${c.configPath ? `
          <div class="connector-card-config" style="margin-top:0.75rem">
            <span class="connector-card-config-label">Config file</span>
            <code>${escapeText(c.configPath)}</code>
            <button type="button" class="modal-btn-cancel" data-connectors-action="copy-config-path" data-path="${escapeAttr(c.configPath)}">Copy path</button>
          </div>
        ` : ''}
      </details>
    `;
  }

  async _handleGoogleSave(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const status = form.querySelector('[data-google-save-status]');
    const data = Object.fromEntries(new FormData(form).entries());
    if (data.oauthClientSecret === '••••••••') delete data.oauthClientSecret;
    const oauth = {};
    if (data.oauthClientId !== undefined) oauth.clientId = data.oauthClientId;
    if (data.oauthClientSecret !== undefined) oauth.clientSecret = data.oauthClientSecret;
    try {
      if (status) status.textContent = 'Saving…';
      const res = await fetch('/api/google/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ oauth }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (status) { status.textContent = 'Saved.'; status.style.color = 'var(--color-success, green)'; }
      const fresh = await this.loadConnectors();
      this.render(fresh, 'google-calendar');
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }

  _handleGoogleConnect(e) {
    e.preventDefault();
    const status = this.list.querySelector('[data-google-oauth-status]');
    if (status) { status.textContent = 'Opening Google sign-in…'; status.style.color = ''; }
    const popup = window.open('/api/google/oauth-start', 'google-oauth', 'width=520,height=720');
    if (!popup) {
      if (status) { status.textContent = 'Popup blocked — allow popups for this site.'; status.style.color = 'var(--color-error, red)'; }
      return;
    }
    const startedAt = Date.now();
    const poll = setInterval(async () => {
      const closed = popup.closed;
      const timedOut = Date.now() - startedAt > 5 * 60 * 1000;
      if (!closed && !timedOut) return;
      clearInterval(poll);
      if (timedOut && !closed) { try { popup.close(); } catch {} }
      try {
        if (status) status.textContent = 'Refreshing connector status…';
        const fresh = await this.loadConnectors();
        this.render(fresh, 'google-calendar');
      } catch {
        if (status) { status.textContent = 'Could not refresh status — reload the page.'; status.style.color = 'var(--color-error, red)'; }
      }
    }, 1000);
  }

  async _handleGoogleDisconnect(e) {
    e.preventDefault();
    const status = this.list.querySelector('[data-google-oauth-status]');
    if (status) { status.textContent = 'Disconnecting…'; status.style.color = ''; }
    try {
      const res = await fetch('/api/google/oauth-disconnect', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fresh = await this.loadConnectors();
      this.render(fresh, 'google-calendar');
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }

  _renderJiraForm(c, open) {
    const ov = c.jiraOverview || {};
    const saved = ov.savedConfig || {};
    const hasOauth = Boolean(ov.hasOauth);
    const hasClientCreds = Boolean(saved.oauth?.clientId);
    const allUsers = c.jiraAllUsers || [];
    const savedMembers = Array.isArray(saved.teamMembers) ? saved.teamMembers : [];
    return `
      <details class="connector-card-howto" ${open ? 'open' : ''}>
        <summary>Setup</summary>
        <p class="connector-card-detail" style="margin-top:0.5rem;font-size:0.85rem;opacity:0.8">
          ${hasOauth
            ? 'Artemis is connected to Jira via OAuth. Tokens refresh automatically; disconnect to revoke this server’s access.'
            : 'Atlassian sites with SSO require OAuth 2.0 (3LO). Register an app at developer.atlassian.com → OAuth 2.0 integration, set the Callback URL to <code>http://localhost:9009/api/jira/oauth-callback</code>, enable scopes <code>read:jira-work read:jira-user offline_access</code>, then paste the Client ID + Secret below.'}
        </p>
        <form data-jira-save class="connector-jira-form">
          <label class="connector-field">
            <span>OAuth Client ID</span>
            <input type="text" name="oauthClientId" placeholder="From developer.atlassian.com → your app → Settings" value="${escapeAttr(saved.oauth?.clientId || '')}" autocomplete="off" />
          </label>
          <label class="connector-field">
            <span>OAuth Client Secret</span>
            <input type="password" name="oauthClientSecret" placeholder="From developer.atlassian.com → your app → Settings" value="${saved.oauth?.clientSecretSet ? '••••••••' : ''}" autocomplete="new-password" />
          </label>
          <label class="connector-field">
            <span>Project key <span style="font-weight:400;opacity:0.7">(optional — leave blank for all projects)</span></span>
            <input type="text" name="projectKey" placeholder="e.g. PROJ" value="${escapeAttr(saved.projectKey || '')}" autocomplete="off" />
          </label>
          <div class="connector-field-row">
            <button type="submit" class="shell-action-btn">Save credentials</button>
            <span class="connector-save-status" data-jira-save-status></span>
          </div>
        </form>
        <div class="connector-field-row" style="margin-top:0.75rem;gap:0.5rem;flex-wrap:wrap">
          ${hasOauth
            ? `<button type="button" class="modal-btn-cancel" data-jira-disconnect>Disconnect Jira</button>`
            : `<button type="button" class="shell-action-btn" data-jira-connect ${hasClientCreds ? '' : 'disabled title="Save Client ID + Secret first"'}>Connect with Jira</button>`}
          <span class="connector-save-status" data-jira-oauth-status></span>
        </div>
        <hr style="margin:1rem 0;opacity:0.2" />
        ${allUsers.length > 0 ? `
        <details>
          <summary style="cursor:pointer;font-size:0.85rem">Assignee filter <span style="opacity:0.6;font-weight:400">(${savedMembers.length > 0 ? `${savedMembers.length} selected` : 'showing all'})</span></summary>
          <p style="font-size:0.8rem;opacity:0.7;margin:0.5rem 0">
            Check the people who should appear in the assignee dropdown. Leave all unchecked to show everyone.
          </p>
          <div class="connector-team-filter" style="display:flex;flex-direction:column;gap:4px;max-height:220px;overflow-y:auto;margin-bottom:0.75rem">
            ${allUsers.map((u) => `
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.875rem;padding:4px 0">
                <input type="checkbox" data-jira-team-member="${escapeAttr(u.accountId)}"
                  ${savedMembers.includes(u.accountId) ? 'checked' : ''} />
                ${u.avatarUrl ? `<img src="${escapeAttr(u.avatarUrl)}" width="20" height="20" style="border-radius:50%;flex-shrink:0" alt="" />` : ''}
                <span>${escapeText(u.displayName)}</span>
              </label>
            `).join('')}
          </div>
          <div class="connector-field-row">
            <button type="button" class="shell-action-btn" data-jira-team-save>Save filter</button>
            <button type="button" class="modal-btn-cancel" data-jira-team-clear>Clear (show all)</button>
            <span class="connector-save-status" data-jira-team-status></span>
          </div>
        </details>
        ` : ''}
        <hr style="margin:1rem 0;opacity:0.2" />
        <details>
          <summary style="cursor:pointer;font-size:0.85rem;opacity:0.7">Fallback: Basic auth with API token (only works on sites without SSO)</summary>
          <form data-jira-save class="connector-jira-form" style="margin-top:0.75rem">
            <label class="connector-field">
              <span>Atlassian site URL</span>
              <input type="url" name="siteUrl" placeholder="https://yoursite.atlassian.net" value="${escapeAttr(saved.siteUrl || '')}" autocomplete="off" />
            </label>
            <label class="connector-field">
              <span>Account email</span>
              <input type="email" name="email" placeholder="you@example.com" value="${escapeAttr(saved.email || '')}" autocomplete="off" />
            </label>
            <label class="connector-field">
              <span>API token</span>
              <input type="password" name="apiToken" placeholder="From id.atlassian.com → Security → API tokens" value="${saved.apiTokenSet ? '••••••••' : ''}" autocomplete="new-password" />
            </label>
            <div class="connector-field-row">
              <button type="submit" class="shell-action-btn">Save Basic-auth credentials</button>
              <span class="connector-save-status" data-jira-save-status></span>
            </div>
          </form>
        </details>
        ${c.configPath ? `
          <div class="connector-card-config" style="margin-top:0.75rem">
            <span class="connector-card-config-label">Config file</span>
            <code>${escapeText(c.configPath)}</code>
            <button type="button" class="modal-btn-cancel" data-connectors-action="copy-config-path" data-path="${escapeAttr(c.configPath)}">Copy path</button>
          </div>
        ` : ''}
      </details>
    `;
  }

  async _handleJiraSave(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const status = form.querySelector('[data-jira-save-status]');
    const data = Object.fromEntries(new FormData(form).entries());
    if (data.apiToken === '••••••••') delete data.apiToken;
    if (data.oauthClientSecret === '••••••••') delete data.oauthClientSecret;
    // Move OAuth fields under nested `oauth` so the route's allowedOauth list
    // accepts them; flat fields stay flat for the Basic-auth fallback form.
    const body = {};
    const oauth = {};
    for (const [k, v] of Object.entries(data)) {
      if (k === 'oauthClientId') oauth.clientId = v;
      else if (k === 'oauthClientSecret') oauth.clientSecret = v;
      else body[k] = v;
    }
    if (Object.keys(oauth).length) body.oauth = oauth;
    try {
      if (status) status.textContent = 'Saving…';
      const res = await fetch('/api/jira/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (status) { status.textContent = 'Saved.'; status.style.color = 'var(--color-success, green)'; }
      // Re-render so the Connect button enables once Client ID is set.
      const fresh = await this.loadConnectors();
      this.render(fresh, 'jira');
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }

  async _handleJiraTeamSave() {
    const status = this.list.querySelector('[data-jira-team-status]');
    const checked = [...this.list.querySelectorAll('[data-jira-team-member]:checked')];
    const teamMembers = checked.map((cb) => cb.dataset.jiraTeamMember);
    try {
      if (status) status.textContent = 'Saving…';
      const res = await fetch('/api/jira/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ teamMembers }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (status) { status.textContent = `Saved — ${teamMembers.length > 0 ? `${teamMembers.length} member${teamMembers.length === 1 ? '' : 's'} selected` : 'filter cleared'}.`; status.style.color = 'var(--color-success, green)'; }
      const fresh = await this.loadConnectors();
      this.render(fresh, 'jira');
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }

  async _handleJiraTeamClear() {
    this.list.querySelectorAll('[data-jira-team-member]').forEach((cb) => { cb.checked = false; });
    await this._handleJiraTeamSave();
  }

  _handleJiraConnect(e) {
    e.preventDefault();
    const status = this.list.querySelector('[data-jira-oauth-status]');
    if (status) { status.textContent = 'Opening Atlassian sign-in…'; status.style.color = ''; }
    const popup = window.open('/api/jira/oauth-start', 'jira-oauth', 'width=520,height=720');
    if (!popup) {
      if (status) { status.textContent = 'Popup blocked — allow popups for this site.'; status.style.color = 'var(--color-error, red)'; }
      return;
    }
    const startedAt = Date.now();
    const poll = setInterval(async () => {
      const closed = popup.closed;
      const timedOut = Date.now() - startedAt > 5 * 60 * 1000;
      if (!closed && !timedOut) return;
      clearInterval(poll);
      if (timedOut && !closed) { try { popup.close(); } catch {} }
      try {
        if (status) status.textContent = 'Refreshing connector status…';
        const fresh = await this.loadConnectors();
        this.render(fresh, 'jira');
      } catch {
        if (status) { status.textContent = 'Could not refresh status — reload the page.'; status.style.color = 'var(--color-error, red)'; }
      }
    }, 1000);
  }

  // ── Generic API-key connector ─────────────────────────────────────────────

  _renderApiKeyForm(c, open, providerId, keySource, placeholder) {
    const configured = Boolean(c.configured);
    return `
      <details class="connector-card-howto" ${open ? 'open' : ''}>
        <summary>Setup</summary>
        <p class="connector-card-detail" style="margin-top:0.5rem;font-size:0.85rem;opacity:0.8">
          ${configured
            ? 'API key is saved. Remove it below to disconnect.'
            : `Get your key at <strong>${escapeText(keySource)}</strong>, paste it below, and click Save. The key is stored in <code>~/.artemis/.env</code> and takes effect immediately — no restart needed.`}
        </p>
        <form data-apikey-save data-provider-id="${escapeAttr(providerId)}" class="connector-jira-form">
          <label class="connector-field">
            <span>API key</span>
            <input type="password" name="apiKey" placeholder="${escapeAttr(placeholder)}" value="${configured ? '••••••••' : ''}" autocomplete="new-password" />
          </label>
          <div class="connector-field-row">
            <button type="submit" class="shell-action-btn">Save</button>
            ${configured ? `<button type="button" class="modal-btn-cancel" data-apikey-clear data-provider-id="${escapeAttr(providerId)}">Remove key</button>` : ''}
            <span class="connector-save-status" data-apikey-status-${escapeAttr(providerId)}></span>
          </div>
        </form>
      </details>
    `;
  }

  async _handleApiKeySave(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const providerId = form.dataset.providerId;
    const status = this.list.querySelector(`[data-apikey-status-${providerId}]`);
    const data = Object.fromEntries(new FormData(form).entries());
    if (data.apiKey === '••••••••') {
      if (status) { status.textContent = 'No change — key already saved.'; status.style.color = ''; }
      return;
    }
    if (!data.apiKey?.trim()) {
      if (status) { status.textContent = 'Enter a key first.'; status.style.color = 'var(--color-error, red)'; }
      return;
    }
    try {
      if (status) { status.textContent = 'Saving…'; status.style.color = ''; }
      const res = await fetch(`/api/providers/${providerId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apiKey: data.apiKey.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (status) { status.textContent = 'Saved.'; status.style.color = 'var(--color-success, green)'; }
      const fresh = await this.loadConnectors();
      this.render(fresh, providerId);
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }

  async _handleApiKeyClear(e) {
    e.preventDefault();
    const providerId = e.currentTarget.dataset.providerId;
    const status = this.list.querySelector(`[data-apikey-status-${providerId}]`);
    if (!confirm(`Remove the ${providerId} API key? You can add it again any time.`)) return;
    try {
      if (status) { status.textContent = 'Removing…'; status.style.color = ''; }
      const res = await fetch(`/api/providers/${providerId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fresh = await this.loadConnectors();
      this.render(fresh, providerId);
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }

  async _handleJiraDisconnect(e) {
    e.preventDefault();
    const status = this.list.querySelector('[data-jira-oauth-status]');
    if (status) { status.textContent = 'Disconnecting…'; status.style.color = ''; }
    try {
      const res = await fetch('/api/jira/oauth-disconnect', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const fresh = await this.loadConnectors();
      this.render(fresh, 'jira');
    } catch (err) {
      if (status) { status.textContent = `Error: ${err.message}`; status.style.color = 'var(--color-error, red)'; }
    }
  }
}

function escapeText(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function escapeAttr(value) { return escapeText(value); }

customElements.define('artemis-connectors-modal', ArtemisConnectorsModal);
