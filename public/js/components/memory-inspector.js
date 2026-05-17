// memory-inspector.js — Floating Artemis provenance tray
//
// Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.
//
// Shows which memory observations Artemis read on the last turn.
// Per-turn, not session-wide. Closed by default; toggled from the panel header.
// State: observations list + isOpen (persisted to localStorage).

const _STORAGE_KEY = 'artemis-memory-inspector-open';

const _MEMORY_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`;

const _CLOSE_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;

/**
 * MemoryInspector — provenance tray for the Floating Artemis panel.
 *
 * Usage:
 *   const inspector = new MemoryInspector(wrapperEl, toggleButtonEl);
 *   inspector.update(observations, timestamp);
 */
export class MemoryInspector {
  /**
   * @param {HTMLElement} wrapperEl   — .fa-memory-inspector-wrap container
   * @param {HTMLElement | null} toggleEl — panel header button that triggers open/close
   */
  constructor(wrapperEl, toggleEl = null) {
    this._wrap = wrapperEl;
    this._toggleEl = toggleEl;

    // Restore open state from localStorage (default: closed)
    this._isOpen = localStorage.getItem(_STORAGE_KEY) === 'true';

    /** @type {Array<{id:number,drawer:string,text:string,score:number,sources:string[],why:string|null}>} */
    this._observations = [];
    this._timestamp = null;

    this._render();

    // Wire toggle button
    if (this._toggleEl) {
      this._toggleEl.addEventListener('click', () => this.toggle());
    }

    // Apply initial open state without animation
    if (this._isOpen) {
      this._wrap.classList.add('fa-mi--open');
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  open() {
    if (this._isOpen) return;
    this._isOpen = true;
    localStorage.setItem(_STORAGE_KEY, 'true');
    this._wrap.classList.add('fa-mi--open');
  }

  close() {
    if (!this._isOpen) return;
    this._isOpen = false;
    localStorage.setItem(_STORAGE_KEY, 'false');
    this._wrap.classList.remove('fa-mi--open');
  }

  toggle() {
    this._isOpen ? this.close() : this.open();
  }

  isOpen() {
    return this._isOpen;
  }

  /**
   * Update observations from a MemoryReadEvent.
   * @param {Array} observations
   * @param {string|null} timestamp  — ISO string or null
   */
  update(observations, timestamp = null) {
    this._observations = Array.isArray(observations) ? [...observations] : [];
    this._timestamp = timestamp || null;
    this._renderBody();
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  _render() {
    this._wrap.innerHTML = `
      <div class="fa-mi-inner">
        <div class="fa-mi-hd">
          <span class="fa-mi-title">Memory</span>
          <button class="fa-mi-close" aria-label="Close memory inspector">${_CLOSE_ICON}</button>
        </div>
        <div class="fa-mi-meta"></div>
        <div class="fa-mi-body"></div>
      </div>`.trim();

    this._metaEl = this._wrap.querySelector('.fa-mi-meta');
    this._bodyEl = this._wrap.querySelector('.fa-mi-body');

    this._wrap.querySelector('.fa-mi-close').addEventListener('click', () => this.close());

    this._renderBody();
  }

  _renderBody() {
    this._renderMeta();

    if (!this._observations.length) {
      this._bodyEl.innerHTML =
        '<p class="fa-mi-empty">She didn\'t pull from memory on this turn.</p>';
      return;
    }

    // Sort by score desc (already sorted server-side, but be defensive)
    const sorted = [...this._observations].sort((a, b) => b.score - a.score);
    this._bodyEl.innerHTML = sorted.map((obs) => this._renderCard(obs)).join('');
  }

  _renderMeta() {
    if (!this._timestamp && !this._observations.length) {
      this._metaEl.innerHTML = '';
      return;
    }

    const timeStr = this._timestamp ? _formatTimestamp(this._timestamp) : '';
    const count = this._observations.length;
    const label = count === 1 ? '1 observation' : `${count} observations`;

    this._metaEl.innerHTML = `<div class="fa-mi-meta-row">${
      timeStr ? `<span class="fa-mi-meta-time">Last turn · ${_esc(timeStr)}</span>` : ''
    }<span class="fa-mi-meta-count">Read ${_esc(label)}</span></div>`;
  }

  _renderCard(obs) {
    const scorePct = Math.round(Math.min(1, Math.max(0, obs.score)) * 100);
    const sourcesStr = (obs.sources || []).join(', ') || obs.drawer;
    const whyHtml = obs.why ? `<p class="fa-mi-card-why">${_esc(obs.why)}</p>` : '';

    return `<div class="fa-mi-card">
      <div class="fa-mi-card-hd">
        <span class="fa-mi-drawer-pill">${_esc(obs.drawer)}</span>
        <div class="fa-mi-score-wrap" title="Score ${scorePct}%">
          <div class="fa-mi-score-bar" style="width:${scorePct}%"></div>
        </div>
      </div>
      <p class="fa-mi-card-text">${_esc(obs.text)}</p>
      <p class="fa-mi-card-sources">${_esc(sourcesStr)}</p>
      ${whyHtml}
    </div>`;
  }
}

// ── Toggle button factory ─────────────────────────────────────────────────────

/**
 * Create a header button that can be inserted into the FA panel header.
 * @returns {HTMLButtonElement}
 */
export function createInspectorToggleButton() {
  const btn = document.createElement('button');
  btn.className = 'fa-hdr-btn fa-mi-toggle-btn';
  btn.setAttribute('aria-label', 'Toggle memory inspector');
  btn.setAttribute('title', 'Memory provenance');
  btn.innerHTML = _MEMORY_ICON;
  return btn;
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function _esc(str) {
  const div = document.createElement('div');
  div.textContent = String(str ?? '');
  return div.innerHTML;
}

function _formatTimestamp(isoStr) {
  try {
    return new Date(isoStr).toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}
