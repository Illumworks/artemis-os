import { escapeHtml } from "../core/utils.js";

export function railMarkup() {
  return `
    <aside class="dev-annotation-rail" id="dev-annotation-rail" aria-label="Annotation rail">
      <div class="dev-rail-header">
        <strong>Annotations</strong>
        <button class="dev-icon-btn" id="dev-rail-close" title="Close" aria-label="Close">×</button>
      </div>
      <input class="dev-url-input" id="dev-rail-url" placeholder="http://localhost:3000">
      <iframe class="dev-preview-frame" id="dev-preview-frame" title="Project preview"></iframe>
      <textarea class="dev-note-input" id="dev-note-input" rows="4" placeholder="Annotate this page..."></textarea>
      <button class="dev-btn primary" id="dev-note-send">Send to chat</button>
      <div class="dev-annotation-list" id="dev-annotation-list"></div>
    </aside>
  `;
}

export function renderAnnotations(annotations) {
  if (!annotations?.length) return `<div class="dev-empty-small">No annotations yet</div>`;
  return annotations.map((item) => `
    <button class="dev-annotation-item" data-url="${escapeHtml(item.url || "")}" data-note="${escapeHtml(item.note)}">
      <span>${escapeHtml(item.url || "No URL")}</span>
      <strong>${escapeHtml(item.note)}</strong>
    </button>
  `).join("");
}

