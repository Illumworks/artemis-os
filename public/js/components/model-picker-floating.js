// Model picker for Floating Artemis sessions.
//
// Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.
//
// A single compact glyph in the panel header that shows the active provider+model.
// Click → spacious single-column dropdown listing providers as section headers
// with their models underneath. Click a model → PATCH session, update local state,
// close.
//
// Default/null state shows "Default (Sonnet 4.6)" which remains the active fallback
// when no provider is set on the session. Providers whose API key is absent are shown
// disabled with a "Configure in Integrations" hint.

const _BASE = '/api/floating-artemis';

// ── Model picker custom element ───────────────────────────────────────────────

class ModelPickerFloating extends HTMLElement {
  connectedCallback() {
    this._sessionId = null;
    this._providers = [];
    this._activeProvider = null;
    this._activeModel = null;
    this._open = false;
    this._buildDOM();
    this._bindEvents();
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /** Set the session to operate on and load models + current selection. */
  async setSession(sessionId, { provider = null, model = null } = {}) {
    this._sessionId = sessionId;
    this._activeProvider = provider;
    this._activeModel = model;
    this._updateLabel();
    await this._loadModels();
  }

  /** Refresh from a session object returned by the API. */
  refreshFromSession(sessionData) {
    this._activeProvider = sessionData?.provider ?? null;
    this._activeModel = sessionData?.model ?? null;
    this._updateLabel();
  }

  // ── DOM ────────────────────────────────────────────────────────────────────

  _buildDOM() {
    this.innerHTML = `
      <div class="mp-float">
        <button class="mp-float-trigger" aria-haspopup="listbox" aria-expanded="false" title="Switch model">
          <svg class="mp-float-icon" width="12" height="12" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
            aria-hidden="true">
            <circle cx="12" cy="12" r="3"/>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06
              a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65
              1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06
              A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65
              1.65 0 0 0 4.6 9z"/>
          </svg>
          <span class="mp-float-label">Default (Sonnet 4.6)</span>
          <svg class="mp-float-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"
            aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <div class="mp-float-dropdown" role="listbox" aria-label="Select model" hidden>
          <div class="mp-float-list"></div>
        </div>
      </div>
    `.trim();
  }

  // ── Events ─────────────────────────────────────────────────────────────────

  _bindEvents() {
    const trigger = this.querySelector('.mp-float-trigger');
    const dropdown = this.querySelector('.mp-float-dropdown');

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      this._open ? this._closeDropdown() : this._openDropdown();
    });

    // Close on outside click
    document.addEventListener('click', (e) => {
      if (this._open && !this.contains(e.target)) this._closeDropdown();
    });

    // Close on Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this._open) this._closeDropdown();
    });
  }

  // ── Dropdown open/close ────────────────────────────────────────────────────

  _openDropdown() {
    this._open = true;
    const dropdown = this.querySelector('.mp-float-dropdown');
    const trigger = this.querySelector('.mp-float-trigger');
    dropdown.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    trigger.classList.add('is-active');
    this._renderList();
  }

  _closeDropdown() {
    this._open = false;
    const dropdown = this.querySelector('.mp-float-dropdown');
    const trigger = this.querySelector('.mp-float-trigger');
    dropdown.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.classList.remove('is-active');
  }

  // ── Label ──────────────────────────────────────────────────────────────────

  _updateLabel() {
    const label = this.querySelector('.mp-float-label');
    if (!label) return;
    if (!this._activeProvider) {
      label.textContent = 'Default (Sonnet 4.6)';
    } else {
      const modelPart = this._activeModel || 'default';
      label.textContent = `${_providerShortName(this._activeProvider)} · ${_modelShortLabel(modelPart)}`;
    }
  }

  // ── Model list ─────────────────────────────────────────────────────────────

  async _loadModels() {
    try {
      const res = await fetch(`${_BASE}/models`);
      if (!res.ok) return;
      const data = await res.json();
      this._providers = data.providers || [];
    } catch {
      // Silently fail — UI still usable with cached state
    }
  }

  _renderList() {
    const list = this.querySelector('.mp-float-list');
    if (!list) return;

    const fragments = [];

    // Default option (revert to Anthropic)
    const isDefault = !this._activeProvider;
    fragments.push(`
      <button class="mp-float-model-row${isDefault ? ' is-selected' : ''}"
        data-provider="" data-model="" role="option" aria-selected="${isDefault}">
        <span class="mp-float-model-name">Default (Sonnet 4.6)</span>
        <span class="mp-float-model-hint">Anthropic · auto</span>
      </button>
    `);

    for (const prov of this._providers) {
      const hasKey = prov.configured !== false; // undefined = assume available; false = no key
      fragments.push(`<div class="mp-float-provider-hd">${_escHtml(prov.name)}</div>`);

      if (!hasKey) {
        const isLocal = prov.subscriptionOrLocal === true;
        const dimLabel = isLocal ? 'Not detected' : 'No key configured';
        const actionLabel = isLocal ? 'Install &rarr;' : 'Configure &rarr;';
        const titleAttr = isLocal
          ? `${_escHtml(prov.name)} binary or server not found`
          : `No API key configured for ${_escHtml(prov.name)}`;
        // Show a single action row; clicking navigates to integrations for both cases
        fragments.push(`
          <button class="mp-float-model-row mp-float-model-row--configure"
            data-configure-provider="${_escHtml(prov.id)}"
            role="option" aria-selected="false"
            title="${titleAttr}">
            <span class="mp-float-model-name mp-float-model-name--dim">${dimLabel}</span>
            <span class="mp-float-configure-link">${actionLabel}</span>
          </button>
        `);
        continue;
      }

      for (const m of prov.models) {
        const isActive = this._activeProvider === prov.id && this._activeModel === m.id;
        fragments.push(`
          <button class="mp-float-model-row${isActive ? ' is-selected' : ''}"
            data-provider="${_escHtml(prov.id)}" data-model="${_escHtml(m.id)}"
            role="option" aria-selected="${isActive}">
            <span class="mp-float-model-name">${_escHtml(m.label)}${m.default ? ' <span class="mp-float-default-badge">default</span>' : ''}</span>
          </button>
        `);
      }
    }

    list.innerHTML = fragments.join('');

    // Wire clicks — selectable rows
    list.querySelectorAll('.mp-float-model-row:not(.mp-float-model-row--configure)').forEach((btn) => {
      btn.addEventListener('click', () => {
        const p = btn.dataset.provider || null;
        const m = btn.dataset.model || null;
        this._selectModel(p, m);
      });
    });

    // Wire clicks — configure rows (navigate to integrations view)
    list.querySelectorAll('.mp-float-model-row--configure').forEach((btn) => {
      btn.addEventListener('click', () => {
        this._closeDropdown();
        _navigateToIntegrations();
      });
    });
  }

  // ── Selection ──────────────────────────────────────────────────────────────

  async _selectModel(provider, model) {
    if (!this._sessionId) { this._closeDropdown(); return; }
    this._closeDropdown();

    try {
      const res = await fetch(
        `${_BASE}/sessions/${encodeURIComponent(this._sessionId)}/model`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider, model }),
        },
      );
      if (!res.ok) {
        console.warn('[ModelPicker] PATCH /model failed', res.status);
        return;
      }
      const updated = await res.json();
      this._activeProvider = updated.provider ?? null;
      this._activeModel = updated.model ?? null;
      this._updateLabel();

      // Notify parent elements (floating_artemis.js can listen if needed)
      this.dispatchEvent(new CustomEvent('mp:model-changed', {
        detail: { provider: this._activeProvider, model: this._activeModel },
        bubbles: true,
      }));
    } catch (err) {
      console.warn('[ModelPicker] model update error', err);
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

function _providerShortName(id) {
  const MAP = {
    anthropic: 'Anthropic',
    'claude-code': 'Claude CLI',
    codex: 'Codex CLI',
    gemini: 'Gemini',
    'lm-studio': 'LM Studio',
    openrouter: 'OpenRouter',
  };
  return MAP[id] ?? id;
}

function _modelShortLabel(id) {
  // Best-effort: shorten known aliases for display
  if (!id) return 'default';
  const known = {
    'claude-sonnet-4-6': 'Sonnet 4.6',
    'claude-opus-4-7': 'Opus 4.7',
    'claude-haiku-4-5': 'Haiku 4.5',
    'gemini-2.5-flash': 'Flash 2.5',
    'gemini-2.5-pro': 'Pro 2.5',
    'gemini-flash-2': 'Flash 2.0',
    'llama-3.3-70b-free': 'Llama 3.3 70B',
    'llama-4-maverick-free': 'Llama 4 Maverick',
  };
  return known[id] ?? id;
}

/** Navigate to the integrations view via the SPA state router. */
function _navigateToIntegrations() {
  // Prefer the app's state router when available; fall back to hash navigation.
  if (typeof setState === 'function') {
    setState('view', 'integrations');
  } else {
    window.location.hash = '#integrations';
  }
}

customElements.define('model-picker-floating', ModelPickerFloating);

export { ModelPickerFloating };
