// welcome-overlay.js
// First-run / no-provider onboarding overlay for the Python rebuild.
//
// Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.
// High-stakes first impression — single column, generous breathing room, one primary CTA.
//
// The overlay shows when no LLM provider key is configured.
// Provider status pills update live as keys land via credential-entry-modal.
// "Continue" becomes enabled once at least one provider is configured.

import { openCredentialEntryModal } from './credential-entry-modal.js';

// ── Provider definitions ──────────────────────────────────────────────────────

const _PROVIDERS = [
  {
    id: 'anthropic',
    name: 'Anthropic',
    description: 'Powers Artemis core chat. Required for most features.',
    fields: [
      {
        key: 'api_key',
        label: 'API key',
        helper: 'Find yours at console.anthropic.com → API Keys.',
        sensitive: true,
      },
    ],
    primary: true,
  },
  {
    id: 'claude-code',
    name: 'Claude Code CLI',
    description: 'Use your Claude Max subscription — no API key needed.',
    subscriptionOrLocal: true,
    installHint: 'Install: npm install -g @anthropic-ai/claude-code',
    primary: false,
  },
  {
    id: 'codex',
    name: 'Codex CLI',
    description: 'Use your ChatGPT Plus subscription — no API key needed.',
    subscriptionOrLocal: true,
    installHint: 'Install: npm install -g @openai/codex',
    primary: false,
  },
  {
    id: 'gemini',
    name: 'Gemini',
    description: 'Google\'s Gemini models — Flash and Pro variants.',
    fields: [
      {
        key: 'api_key',
        label: 'API key',
        helper: 'Find yours at aistudio.google.com → Get API key.',
        sensitive: true,
      },
    ],
    primary: false,
  },
  {
    id: 'lm-studio',
    name: 'LM Studio',
    description: 'Run any open model locally — no cloud, no key.',
    subscriptionOrLocal: true,
    installHint: 'Download LM Studio at lmstudio.ai and start the local server.',
    primary: false,
  },
  {
    id: 'openai',
    name: 'OpenAI',
    description: 'GPT-4o and other OpenAI models.',
    fields: [
      {
        key: 'api_key',
        label: 'API key',
        helper: 'Find yours at platform.openai.com → API keys.',
        sensitive: true,
      },
    ],
    primary: false,
  },
];

// ── Custom element ────────────────────────────────────────────────────────────

class WelcomeOverlay extends HTMLElement {
  connectedCallback() {
    this._configuredSet = new Set();
    this._render();
    this._checkAllProviders();
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /** Show the overlay (no-op if already visible). */
  show() {
    const el = this.querySelector('.welcome-overlay');
    if (el) el.classList.remove('hidden');
  }

  /** Dismiss the overlay with a fade, then hide. */
  hide() {
    const el = this.querySelector('.welcome-overlay');
    if (!el) return;
    el.classList.add('hiding');
    setTimeout(() => el.classList.add('hidden'), 520);
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  _render() {
    this.innerHTML = `
<div class="welcome-overlay" role="dialog" aria-modal="true" aria-label="Welcome to Artemis">
  <div class="welcome-container">

    <div class="welcome-mascot">
      <img src="/icons/artemis-mark.png" alt="Artemis" class="welcome-mark" draggable="false">
    </div>

    <h1 class="welcome-title">Welcome to <span>Artemis</span></h1>

    <p class="welcome-description">
      Artemis runs as your operations chief. Connect at least one LLM provider
      to start chatting — everything else is optional.
    </p>

    <div class="welcome-providers" aria-label="LLM providers">
      ${_PROVIDERS.map((p) => this._providerCardHTML(p)).join('')}
    </div>

    <div class="welcome-actions">
      <button id="welcome-continue" class="welcome-btn-primary" disabled>
        Continue
      </button>
      <button id="welcome-skip" class="welcome-btn-secondary" type="button">
        Skip — set up later
      </button>
    </div>

    <p class="welcome-footer-note">
      Keys are optional for first launch — Artemis can run with the keys already
      in your environment. Manage everything in <strong>Settings → Integrations</strong>.
    </p>

  </div>
</div>`;

    this._bindEvents();
  }

  _providerCardHTML(provider) {
    const badgeId = `welcome-badge-${provider.id}`;
    const cardClass = provider.primary
      ? 'welcome-provider-card welcome-provider-card--primary'
      : 'welcome-provider-card';

    // Subscription / local providers (no API key): show install hint instead
    // of an "Add key" button. Badge updates live via _markConfigured().
    if (provider.subscriptionOrLocal) {
      return `
<div class="${cardClass}" data-provider="${provider.id}">
  <div class="welcome-provider-info">
    <div class="welcome-provider-name">${_escHtml(provider.name)}</div>
    <div class="welcome-provider-desc">${_escHtml(provider.description)}</div>
    <div class="welcome-provider-hint">${_escHtml(provider.installHint || '')}</div>
  </div>
  <div class="welcome-provider-actions">
    <span id="${badgeId}" class="welcome-provider-badge welcome-provider-badge--unconfigured" aria-live="polite">
      Not detected
    </span>
  </div>
</div>`;
    }

    const btnId = `welcome-add-${provider.id}`;
    return `
<div class="${cardClass}" data-provider="${provider.id}">
  <div class="welcome-provider-info">
    <div class="welcome-provider-name">${_escHtml(provider.name)}</div>
    <div class="welcome-provider-desc">${_escHtml(provider.description)}</div>
  </div>
  <div class="welcome-provider-actions">
    <span id="${badgeId}" class="welcome-provider-badge welcome-provider-badge--unconfigured" aria-live="polite">
      Not configured
    </span>
    <button id="${btnId}" class="welcome-add-btn" type="button"
      data-provider="${provider.id}" aria-label="Add ${_escHtml(provider.name)} key">
      Add key
    </button>
  </div>
</div>`;
  }

  // ── Events ──────────────────────────────────────────────────────────────────

  _bindEvents() {
    // Provider "Add key" buttons
    this.querySelectorAll('.welcome-add-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const providerId = btn.dataset.provider;
        const providerDef = _PROVIDERS.find((p) => p.id === providerId);
        if (!providerDef) return;

        openCredentialEntryModal({
          provider: providerId,
          fields: providerDef.fields,
          onSaved: () => {
            this._markConfigured(providerId);
          },
        });
      });
    });

    // Continue button
    const continueBtn = this.querySelector('#welcome-continue');
    if (continueBtn) {
      continueBtn.addEventListener('click', () => this.hide());
    }

    // Skip button — always dismissable; keys are optional
    const skipBtn = this.querySelector('#welcome-skip');
    if (skipBtn) {
      skipBtn.addEventListener('click', () => this.hide());
    }

    // Keyboard shortcut: Escape always dismisses, Enter dismisses if any configured
    document.addEventListener('keydown', this._onKeyDown.bind(this));
  }

  _onKeyDown(e) {
    const overlay = this.querySelector('.welcome-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;

    if (e.key === 'Escape') {
      this.hide();
    } else if (e.key === 'Enter' && this._configuredSet.size > 0) {
      this.hide();
    }
  }

  // ── Provider status ─────────────────────────────────────────────────────────

  /** Mark a provider as configured — update badge, enable Continue if appropriate. */
  _markConfigured(providerId) {
    this._configuredSet.add(providerId);

    const providerDef = _PROVIDERS.find((p) => p.id === providerId);
    const badge = this.querySelector(`#welcome-badge-${providerId}`);
    if (badge) {
      badge.textContent = providerDef?.subscriptionOrLocal ? 'Detected' : 'Configured';
      badge.classList.remove('welcome-provider-badge--unconfigured');
      badge.classList.add('welcome-provider-badge--configured');
    }

    // Only key-based providers have an "Add key" button
    if (!providerDef?.subscriptionOrLocal) {
      const btn = this.querySelector(`#welcome-add-${providerId}`);
      if (btn) {
        btn.textContent = 'Update key';
      }
    }

    this._refreshContinueBtn();
  }

  _refreshContinueBtn() {
    const continueBtn = this.querySelector('#welcome-continue');
    if (!continueBtn) return;
    continueBtn.disabled = this._configuredSet.size === 0;
  }

  /** On mount, check which providers are already configured.
   * /api/stats/providers reflects both DB-stored keys AND env vars
   * (ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY) so anything Artemis
   * can already authenticate with counts as configured — no need to re-paste.
   */
  async _checkAllProviders() {
    try {
      const res = await fetch('/api/stats/providers');
      if (!res.ok) return;
      const rows = await res.json();
      if (!Array.isArray(rows)) return;
      for (const row of rows) {
        if (row?.configured) {
          this._markConfigured(row.provider_id);
        }
      }
    } catch {
      // non-fatal — badges stay "Not configured", user can skip
    }

    // If at least one provider is already wired (typical case — Anthropic via
    // env var), self-dismiss so the operator doesn't see the welcome at all.
    if (this._configuredSet.size > 0) {
      this.hide();
    }
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Register ──────────────────────────────────────────────────────────────────

customElements.define('artemis-welcome-overlay', WelcomeOverlay);

export { WelcomeOverlay };
