const CLOSE_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;

export class MemoryInspector {
  constructor(containerEl) {
    this._el = containerEl;
    this._open = false;
    this._observations = null;
    this._render();
    this._el.querySelector('.fa-mi-close').addEventListener('click', () => this.close());
  }

  _render() {
    this._el.innerHTML = `
      <div class="fa-mi-hd">
        <span class="fa-mi-title">Memory</span>
        <button class="fa-mi-close" aria-label="Close memory inspector">${CLOSE_ICON}</button>
      </div>
      <div class="fa-mi-body"></div>`.trim();
    this._body = this._el.querySelector('.fa-mi-body');
    this._el.querySelector('.fa-mi-close').addEventListener('click', () => this.close());
    this._renderBody();
  }

  _renderBody() {
    if (!this._observations || this._observations.length === 0) {
      this._body.innerHTML = `
        <div class="fa-mi-empty">
          <p>Memory provenance inspector</p>
          <p class="fa-mi-coming-soon">Full wiring in a future phase.</p>
        </div>`.trim();
      return;
    }

    const items = this._observations
      .map((obs) => {
        const label = obs.label ?? obs.observation_id ?? '';
        const preview = obs.content ?? obs.text ?? '';
        return `
          <div class="fa-mi-obs-item">
            <span class="fa-mi-obs-label">${_esc(label)}</span>
            <p class="fa-mi-obs-preview">${_esc(preview)}</p>
          </div>`.trim();
      })
      .join('');

    this._body.innerHTML = `<div class="fa-mi-obs-list">${items}</div>`;
  }

  open() {
    if (this._open) return;
    this._open = true;
    this._el.classList.add('fa-mi--open');
  }

  close() {
    if (!this._open) return;
    this._open = false;
    this._el.classList.remove('fa-mi--open');
  }

  toggle() {
    this._open ? this.close() : this.open();
  }

  isOpen() {
    return this._open;
  }

  setObservations(observations) {
    this._observations = Array.isArray(observations) ? observations : [];
    this._renderBody();
  }
}

function _esc(str) {
  const div = document.createElement('div');
  div.textContent = String(str ?? '');
  return div.innerHTML;
}
