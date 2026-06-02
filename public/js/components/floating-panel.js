// Floating Artemis panel — custom element <floating-artemis-panel>.
//
// Receives orchestration events from floating_artemis.js (dispatched as
// CustomEvents on this element). Sends messages directly via floating-artemis-api.js.
//
// Event contract (received from floating_artemis.js):
//   fa:event         — { detail: WS event object }
//   fa:history       — { detail: { messages: [...] } }
//   fa:page-changed  — { detail: { page, ref_id } }
//   fa:calibrating   — { detail: { step } }
//   fa:fresh-start   — clear state, load new history
//   fa:opened        — panel was opened
//   fa:closed        — panel was closed
//
// Events dispatched upward:
//   fa:request-fresh — user clicked "Start fresh"

import { ChatStream } from './chat-stream.js';
import { ToolConfirmCard } from './tool-confirm-card.js';
import { ActiveRunsSidebar } from './active-runs-sidebar.js';
import { MemoryInspector } from './memory-inspector.js';
import './model-picker-floating.js';
import {
  sendMessage,
  stopTurn,
  getFASessionId,
} from '../core/floating-artemis-api.js';
import { startFresh, getCurrentSessionId } from '../features/floating_artemis.js';
import { escapeHtml } from '../core/utils.js';

// View → short label for the context chip
const VIEW_LABELS = {
  'command-center': 'Focus',
  dashboard: 'Dashboard',
  workspace: 'Workspace',
  calendar: 'Calendar',
  jira: 'Jira',
  'jira-board': 'Jira',
  okr: 'OKR Studio',
  'okr-studio': 'OKR Studio',
  operations: 'Operations',
  memory: 'Memory',
  'writing-studio': 'Writing Studio',
  'marketing-os': 'Marketing OS',
  'marketing-dashboard': 'Marketing',
  'marketing-campaigns': 'Campaigns',
  'marketing-signals': 'Signals',
  'marketing-approvals': 'Approvals',
  'marketing-rulesets': 'Rulesets',
  'signal-playbook': 'Signal Playbook',
  'marketing-scout-runs': 'Scout Runs',
  agents: 'Agents',
  workflows: 'Workflows',
  skills: 'Skills',
  automations: 'Automations',
};

// Quick chips per view — { label, prompt, autoSend? }
const CONTEXT_CHIPS = {
  'marketing-signals': [
    { label: "Summarize signals", prompt: "Summarize the latest signals. Which ones are most significant?", autoSend: true },
    { label: "What needs review?", prompt: "Which signals haven't been reviewed and need attention?", autoSend: true },
  ],
  'marketing-campaigns': [
    { label: "What's behind?", prompt: "Which campaigns are behind schedule or at risk? Give me a concise status.", autoSend: true },
    { label: "New brief", prompt: "Draft a campaign brief for " },
  ],
  'marketing-approvals': [
    { label: "What's pending?", prompt: "What's in the approval queue right now? Give me a summary.", autoSend: true },
  ],
  agents: [
    { label: "What ran today?", prompt: "What agents ran today and what were the results?", autoSend: true },
  ],
  _default: [
    { label: "What's next?", prompt: "What should I focus on right now? Give me a short prioritized list.", autoSend: true },
  ],
};

function _getChips(view) {
  return CONTEXT_CHIPS[view] ?? CONTEXT_CHIPS._default;
}

// ── SVG icons ─────────────────────────────────────────────────────────────────

const ICON_ACTIVITY = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>`;
const ICON_MEMORY   = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`;
const ICON_REFRESH  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>`;
const ICON_SETTINGS = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06-.06a2 2 0 1 1-2.83-2.83l.06.06A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9z"/></svg>`;
const ICON_CLOSE    = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
const ICON_SEND     = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5"/><path d="M6 11l6-6 6 6"/></svg>`;
const ICON_STOP     = `<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`;

// ── Custom element ────────────────────────────────────────────────────────────

class FloatingArtemisPanel extends HTMLElement {
  connectedCallback() {
    this._streaming = false;
    this._currentHandle = null;
    this._currentView = 'command-center';
    this._systemPromptSuffix = localStorage.getItem('artemis-fa-system-prompt') ?? '';
    this._buildDOM();
    this._bindEvents();
  }

  // ── DOM ────────────────────────────────────────────────────────────────────

  _buildDOM() {
    this.innerHTML = `
      <div class="fa-panel">
        <div class="fa-panel-chat-pane">

          <div class="fa-panel-header">
            <div class="fa-header-brand">
              <img src="/icons/aIcon.png" alt="" class="fa-header-icon" width="20" height="20">
              <span class="fa-header-name">Artemis</span>
            </div>
            <div class="fa-header-context-chip">
              <span class="fa-context-dot"></span>
              <span class="fa-context-label">Focus</span>
            </div>
            <div class="fa-header-actions">
              <model-picker-floating class="fa-model-picker"></model-picker-floating>
              <button class="fa-hdr-btn fa-btn-activity" title="Activity sidebar">${ICON_ACTIVITY}</button>
              <button class="fa-hdr-btn fa-btn-memory" title="Memory inspector">${ICON_MEMORY}</button>
              <button class="fa-hdr-btn fa-btn-fresh" title="Start fresh">${ICON_REFRESH}</button>
              <button class="fa-hdr-btn fa-btn-settings" title="Settings">${ICON_SETTINGS}</button>
              <button class="fa-hdr-btn fa-btn-close" title="Close">${ICON_CLOSE}</button>
            </div>
          </div>

          <div class="fa-panel-body"></div>

          <div class="fa-panel-quick"></div>

          <div class="fa-panel-input">
            <div class="fa-input-context">
              <span class="fa-input-page-label">Focus</span>
            </div>
            <div class="fa-composer">
              <textarea class="fa-textarea" rows="1" placeholder="Ask, delegate, or reshape the plan…"></textarea>
              <div class="fa-composer-actions">
                <button type="button" class="fa-send-btn" title="Send (Enter)">${ICON_SEND}</button>
                <button type="button" class="fa-stop-btn" title="Stop">${ICON_STOP}</button>
              </div>
            </div>
          </div>

        </div>

        <div class="fa-sidebar"></div>
        <div class="fa-memory-inspector-wrap"></div>

        <div class="fa-settings-overlay">
          <div class="fa-settings-hd">
            <span>Settings</span>
            <button class="fa-hdr-btn fa-settings-close">${ICON_CLOSE}</button>
          </div>
          <div class="fa-settings-body">
            <label class="fa-settings-label">Additional instructions (appended to her core persona):</label>
            <textarea class="fa-settings-textarea" placeholder="E.g. always reply in bullet points…" rows="4"></textarea>
          </div>
          <div class="fa-settings-actions">
            <button class="fa-btn-ghost fa-settings-cancel-btn">Cancel</button>
            <button class="fa-btn-primary fa-settings-save-btn">Save</button>
          </div>
        </div>

      </div>
    `.trim();

    // ── Sub-component wiring ─────────────────────────────────────────────────
    this._stream = new ChatStream(this.querySelector('.fa-panel-body'));
    this._sidebar = new ActiveRunsSidebar(this.querySelector('.fa-sidebar'));
    this._memInspector = new MemoryInspector(this.querySelector('.fa-memory-inspector-wrap'));
    this._modelPicker = this.querySelector('.fa-model-picker');

    this._stream.showEmpty();
  }

  // ── Event binding ─────────────────────────────────────────────────────────

  _bindEvents() {
    // Header buttons
    this.querySelector('.fa-btn-activity').addEventListener('click', () => this._sidebar.toggle());
    this.querySelector('.fa-btn-memory').addEventListener('click', () => this._memInspector.toggle());
    this.querySelector('.fa-btn-fresh').addEventListener('click', () => this._requestFresh());
    this.querySelector('.fa-btn-settings').addEventListener('click', () => this._openSettings());
    this.querySelector('.fa-btn-close').addEventListener('click', () => this.removeAttribute('open'));

    // Settings overlay
    this.querySelector('.fa-settings-close').addEventListener('click', () => this._closeSettings());
    this.querySelector('.fa-settings-cancel-btn').addEventListener('click', () => this._closeSettings());
    this.querySelector('.fa-settings-save-btn').addEventListener('click', () => this._saveSettings());

    // Input
    const textarea = this.querySelector('.fa-textarea');
    const sendBtn = this.querySelector('.fa-send-btn');
    const stopBtn = this.querySelector('.fa-stop-btn');

    sendBtn.addEventListener('click', () => this._send());
    stopBtn.addEventListener('click', () => this._stop());

    textarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this._send(); }
    });
    textarea.addEventListener('input', () => {
      textarea.style.height = 'auto';
      textarea.style.height = Math.min(textarea.scrollHeight, 160) + 'px';
    });
    const composer = this.querySelector('.fa-composer');
    textarea.addEventListener('focus', () => composer.classList.add('is-focused'));
    textarea.addEventListener('blur', () => composer.classList.remove('is-focused'));

    // CustomEvents from floating_artemis.js
    this.addEventListener('fa:event', (e) => this._handleFAEvent(e.detail));
    this.addEventListener('fa:history', (e) => this._renderHistory(e.detail.messages));
    this.addEventListener('fa:session-ready', (e) => this._onSessionReady(e.detail));
    this.addEventListener('fa:page-changed', (e) => this._updatePage(e.detail.page));
    this.addEventListener('fa:calibrating', (e) => this._stream.showCalibrating(e.detail.step));
    this.addEventListener('fa:fresh-start', () => { this._stream.clear(); this._stream.showEmpty(); });
    this.addEventListener('fa:opened', () => this.querySelector('.fa-textarea').focus());
    this.addEventListener('fa:memory-read', (e) => {
      const { observations, turn_id } = e.detail || {};
      this._memInspector?.update(observations || [], turn_id);
    });
  }

  // ── Outgoing messages ─────────────────────────────────────────────────────

  async _send() {
    const textarea = this.querySelector('.fa-textarea');
    const text = textarea.value.trim();
    if (!text || this._streaming) return;
    const sessionId = getCurrentSessionId();
    if (!sessionId) return;

    this._stream.appendUser(text);
    textarea.value = '';
    textarea.style.height = 'auto';

    this._setStreaming(true);
    this._stream.showThinking('Thinking…');

    try {
      await sendMessage(sessionId, text);
      // Response arrives via WS → fa:event → _handleFAEvent
    } catch (err) {
      this._stream.removeThinking();
      this._stream.appendError(`Send failed: ${err.message}`);
      this._setStreaming(false);
    }
  }

  async _stop() {
    const sessionId = getCurrentSessionId();
    if (!sessionId) return;
    try { await stopTurn(sessionId); } catch {}
    this._setStreaming(false);
  }

  _setStreaming(on) {
    this._streaming = on;
    this.querySelector('.fa-send-btn').classList.toggle('fa-hidden', on);
    this.querySelector('.fa-stop-btn').classList.toggle('fa-visible', on);
  }

  // ── WS event handler ──────────────────────────────────────────────────────

  _handleFAEvent(event) {
    switch (event.type) {
      case 'floating_artemis.turn_started':
        this._stream.removeCalibrating();
        if (!this._currentHandle) {
          this._stream.showThinking('Thinking…');
        }
        break;

      case 'floating_artemis.message':
        this._stream.removeThinking();
        this._stream.removeCalibrating();
        if (!this._currentHandle) {
          this._currentHandle = this._stream.beginAssistant();
        }
        // Backend sends full text per assistant-message block. appendToken
        // accumulates rawText so multi-block turns concatenate correctly.
        this._stream.appendToken(this._currentHandle, event.text ?? '');
        break;

      case 'floating_artemis.turn_complete':
        if (this._currentHandle) {
          this._stream.finalizeAssistant(this._currentHandle);
          this._currentHandle = null;
        }
        this._stream.removeThinking();
        this._setStreaming(false);
        this._sidebar.refresh();
        break;

      case 'floating_artemis.tool_started':
        this._stream.appendToolIndicator(event.tool ?? '', event.tool_input ?? {});
        break;

      case 'floating_artemis.tool_pending':
        this._stream.removeThinking();
        ToolConfirmCard.create(this.querySelector('.fa-panel-body'), {
          sessionId: event.session_id,
          toolUseId: event.tool_use_id,
          toolName: event.tool_name,
          toolInput: event.tool_input ?? {},
          layer: event.layer ?? 3,
          onConfirm: () => {},
        });
        break;

      case 'floating_artemis.failed':
        this._stream.removeThinking();
        this._stream.appendError(event.error ?? 'Something went wrong.');
        this._setStreaming(false);
        break;

      case 'floating_artemis.stopped':
        this._stream.removeThinking();
        this._currentHandle = null;
        this._setStreaming(false);
        break;
    }
  }

  // ── History ────────────────────────────────────────────────────────────────

  _renderHistory(messages) {
    this._stream.clear();
    this._stream.renderHistory(messages);
  }

  // ── Session ready (picker wiring) ─────────────────────────────────────────

  _onSessionReady({ sessionId, provider, model } = {}) {
    if (this._modelPicker && sessionId) {
      this._modelPicker.setSession(sessionId, { provider: provider ?? null, model: model ?? null });
    }
  }

  // ── Page context chip ─────────────────────────────────────────────────────

  _updatePage(view) {
    this._currentView = view;
    const label = VIEW_LABELS[view] ?? view ?? 'Focus';
    this.querySelector('.fa-context-label').textContent = label;
    this.querySelector('.fa-input-page-label').textContent = label;
    this._renderChips(view);
  }

  _renderChips(view) {
    const el = this.querySelector('.fa-panel-quick');
    const chips = _getChips(view);
    if (!chips.length) { el.innerHTML = ''; return; }
    const textarea = this.querySelector('.fa-textarea');
    el.innerHTML = chips.map((c) =>
      `<button class="fa-quick-chip${c.autoSend ? ' fa-chip-auto' : ''}" data-prompt="${escapeHtml(c.prompt)}">${escapeHtml(c.label)}</button>`
    ).join('');
    el.querySelectorAll('.fa-quick-chip').forEach((btn) => {
      btn.addEventListener('click', () => {
        textarea.value = btn.dataset.prompt || btn.textContent.trim();
        textarea.dispatchEvent(new Event('input'));
        if (btn.classList.contains('fa-chip-auto')) { this._send(); }
        else { textarea.focus(); }
      });
    });
  }

  // ── Start fresh ───────────────────────────────────────────────────────────

  _requestFresh() {
    // Dispatch upward so floating_artemis.js can handle the archive + new session
    this.dispatchEvent(new CustomEvent('fa:request-fresh', { bubbles: true }));
  }

  // ── Settings ──────────────────────────────────────────────────────────────

  _openSettings() {
    const overlay = this.querySelector('.fa-settings-overlay');
    this.querySelector('.fa-settings-textarea').value = this._systemPromptSuffix;
    overlay.classList.add('fa-settings-open');
  }

  _closeSettings() {
    this.querySelector('.fa-settings-overlay').classList.remove('fa-settings-open');
  }

  _saveSettings() {
    this._systemPromptSuffix = this.querySelector('.fa-settings-textarea').value.trim();
    localStorage.setItem('artemis-fa-system-prompt', this._systemPromptSuffix);
    this._closeSettings();
  }
}

customElements.define('floating-artemis-panel', FloatingArtemisPanel);
