import { renderMarkdown, highlightCodeBlocks, addCopyButtons } from '../ui/formatting.js';
import { escapeHtml } from '../core/utils.js';

export class ChatStream {
  constructor(containerEl) {
    this._el = containerEl;
    this._thinking = null;
    this._calibrating = null;
    this._empty = null;
  }

  // ── History ──────────────────────────────────────────────────────────────

  renderHistory(messages) {
    if (!messages || messages.length === 0) {
      this.showEmpty();
      return;
    }
    for (const msg of messages) {
      const text = msg.content?.[0]?.text ?? '';
      if (msg.role === 'user') {
        this.appendUser(text);
      } else if (msg.role === 'assistant') {
        const handle = this.beginAssistant();
        handle.rawText = text;
        handle.el.querySelector('.fa-msg-body').innerHTML = renderMarkdown(text);
        this.finalizeAssistant(handle);
      }
    }
  }

  // ── Messages ─────────────────────────────────────────────────────────────

  appendUser(text) {
    this.removeEmpty();
    const el = document.createElement('div');
    el.className = 'fa-msg fa-msg-user';
    el.textContent = text;
    this._el.appendChild(el);
    this._scrollToBottom();
  }

  beginAssistant() {
    this.removeEmpty();
    this.removeThinking();
    const el = document.createElement('div');
    el.className = 'fa-msg fa-msg-assistant';
    el.innerHTML = `
      <div class="fa-msg-head">
        <span class="fa-msg-author">✦ Artemis</span>
      </div>
      <div class="fa-msg-body"></div>
    `.trim();
    this._el.appendChild(el);
    this._scrollToBottom();
    return { el, rawText: '' };
  }

  appendToken(handle, token) {
    handle.rawText += token;
    handle.el.querySelector('.fa-msg-body').innerHTML = renderMarkdown(handle.rawText);
    this._scrollToBottom();
  }

  finalizeAssistant(handle) {
    const body = handle.el.querySelector('.fa-msg-body');
    highlightCodeBlocks(body);
    addCopyButtons(body);
    this._scrollToBottom();
  }

  appendError(text) {
    const el = document.createElement('div');
    el.className = 'fa-msg fa-msg-error';
    el.textContent = text;
    this._el.appendChild(el);
    this._scrollToBottom();
  }

  // ── Tool indicator ────────────────────────────────────────────────────────

  appendToolIndicator(toolName, toolInput) {
    const detail = this._summariseInput(toolInput);
    const el = document.createElement('div');
    el.className = 'fa-tool-indicator';
    el.textContent = `↳ ${escapeHtml(toolName)}${detail ? ': ' + escapeHtml(detail) : ''}`;
    this._el.appendChild(el);
    this._scrollToBottom();
  }

  _summariseInput(input) {
    if (!input) return '';
    if (typeof input === 'string') return input.slice(0, 80);
    const first = Object.values(input)[0];
    if (first == null) return '';
    return String(first).slice(0, 80);
  }

  // ── Thinking indicator ────────────────────────────────────────────────────

  showThinking(text = 'Thinking…') {
    if (this._thinking) return;
    const el = document.createElement('div');
    el.className = 'fa-thinking';
    el.innerHTML = `<span class="fa-thinking-dots">...</span><span class="fa-thinking-text">${escapeHtml(text)}</span>`;
    this._thinking = el;
    this._el.appendChild(el);
    this._scrollToBottom();
  }

  removeThinking() {
    if (!this._thinking) return;
    this._thinking.remove();
    this._thinking = null;
  }

  // ── Calibrating ───────────────────────────────────────────────────────────

  showCalibrating(step = 'Catching up on context…') {
    if (this._calibrating) {
      this._calibrating.querySelector('.fa-calibrating-text').textContent = step;
      return;
    }
    const el = document.createElement('div');
    el.className = 'fa-calibrating';
    el.innerHTML = `<span class="fa-calibrating-pulse"></span><span class="fa-calibrating-text">${escapeHtml(step)}</span>`;
    this._calibrating = el;
    this._el.appendChild(el);
    this._scrollToBottom();
  }

  removeCalibrating() {
    if (!this._calibrating) return;
    this._calibrating.remove();
    this._calibrating = null;
  }

  // ── Empty state ───────────────────────────────────────────────────────────

  showEmpty() {
    if (this._empty) return;
    const el = document.createElement('div');
    el.className = 'fa-empty';
    el.innerHTML = `
      <div class="fa-empty-mark">
        <img src="/icons/aIcon.png" alt="" width="28" height="28">
      </div>
      <div class="fa-empty-text">Ask anything, delegate a task,<br>or pick a quick action below.</div>
    `.trim();
    this._empty = el;
    this._el.appendChild(el);
  }

  removeEmpty() {
    if (!this._empty) return;
    this._empty.remove();
    this._empty = null;
  }

  // ── Utility ───────────────────────────────────────────────────────────────

  clear() {
    this._el.innerHTML = '';
    this._thinking = null;
    this._calibrating = null;
    this._empty = null;
  }

  _scrollToBottom() {
    this._el.scrollTop = this._el.scrollHeight;
  }
}
