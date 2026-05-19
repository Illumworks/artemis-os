import { escapeHtml } from "../core/utils.js";

// v3 right preview rail — replaces the v2 floating annotation popup.
// Width and chrome match the Claude Code Desktop right rail. The body is
// driven by `renderAnnotations`; when empty, the rail shows a small
// empty-state message and no preview affordances.
export function railMarkup() {
  return `
    <aside class="dp-rail" id="dev-annotation-rail" aria-label="Preview rail">
      <div class="dp-rail-header">
        <strong class="dp-rail-title">Preview</strong>
        <button class="dp-icon-btn" id="dev-rail-close" title="Close" aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <div class="dp-rail-body" id="dev-annotation-list" data-empty="true"></div>
      <div class="dp-rail-tools" id="dev-rail-tools" hidden>
        <input class="dp-rail-url" id="dev-rail-url" placeholder="http://localhost:3000">
        <div class="dp-rail-preview" id="dev-preview-wrap">
          <iframe class="dp-rail-frame" id="dev-preview-frame" title="Project preview"></iframe>
          <button class="dp-rail-target-overlay hidden" id="dev-target-overlay" type="button">
            <span>Click the page area you mean</span>
          </button>
        </div>
        <div class="dp-rail-tool-row">
          <button class="dev-btn" id="dev-target-pick" type="button">Pick page target</button>
        </div>
        <textarea class="dp-rail-note" id="dev-note-input" rows="3" placeholder="Annotate this page…"></textarea>
        <button class="dev-btn primary" id="dev-note-send">Send to chat</button>
      </div>
    </aside>
  `;
}

export function renderAnnotations(annotations) {
  if (!annotations?.length) {
    return `<div class="dp-rail-empty">Nothing to preview yet. Open a link, file, or annotation to see it here.</div>`;
  }
  return annotations.map((item) => `
    <button class="dev-annotation-item" data-url="${escapeHtml(item.url || "")}" data-note="${escapeHtml(item.note)}">
      <span>${escapeHtml(item.url || "No URL")}</span>
      <strong>${escapeHtml(item.note)}</strong>
    </button>
  `).join("");
}
