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
    </div>

    <p class="welcome-footer-note">
      You can manage these later in <strong>Settings → Integrations</strong>.
    </p>

  </div>
</div>`;

    this._bindEvents();
  }

  _providerCardHTML(provider) {
    const badgeId = `welcome-badge-${provider.id}`;
    const btnId = `welcome-add-${provider.id}`;
    const cardClass = provider.primary
      ? 'welcome-provider-card welcome-provider-card--primary'
      : 'welcome-provider-card';

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

    // Keyboard shortcut: Enter or Escape to continue (if at least one configured)
    document.addEventListener('keydown', this._onKeyDown.bind(this));
  }

  _onKeyDown(e) {
    const overlay = this.querySelector('.welcome-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;

    if ((e.key === 'Enter' || e.key === 'Escape') && this._configuredSet.size > 0) {
      this.hide();
    }
  }

  // ── Provider status ─────────────────────────────────────────────────────────

  /** Mark a provider as configured — update badge, enable Continue if appropriate. */
  _markConfigured(providerId) {
    this._configuredSet.add(providerId);

    const badge = this.querySelector(`#welcome-badge-${providerId}`);
    if (badge) {
      badge.textContent = 'Configured';
      badge.classList.remove('welcome-provider-badge--unconfigured');
      badge.classList.add('welcome-provider-badge--configured');
    }

    const btn = this.querySelector(`#welcome-add-${providerId}`);
    if (btn) {
      btn.textContent = 'Update key';
    }

    this._refreshContinueBtn();
  }

  _refreshContinueBtn() {
    const continueBtn = this.querySelector('#welcome-continue');
    if (!continueBtn) return;
    continueBtn.disabled = this._configuredSet.size === 0;
  }

  /** On mount, silently check which providers are already configured (e.g. via env var). */
  async _checkAllProviders() {
    await Promise.all(
      _PROVIDERS.map(async (p) => {
        try {
          const res = await fetch(`/api/integrations/providers/${p.id}/config`);
          if (!res.ok) return;
          const data = await res.json();
          if (data?.ever_configured || data?.configured_keys?.api_key) {
            this._markConfigured(p.id);
          }
        } catch {
          // non-fatal — badge stays "Not configured"
        }
      }),
    );

    // If everything is already set, hide immediately — this is a returning user
    if (this._configuredSet.size === _PROVIDERS.length) {
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
