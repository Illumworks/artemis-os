import { escapeHtml } from "../core/utils.js";
import { renderPermissionCard } from "./dev-projects-permission-card.js";

function markdown(text) {
  const escaped = escapeHtml(text || "");
  return escaped
    .replace(/```([a-z]*)\n([\s\S]*?)```/g, (_m, lang, code) => `<pre><code class="language-${escapeHtml(lang)}">${code}</code></pre>`)
    .replace(/\n/g, "<br>");
}

export function renderMessage(message) {
  const blocks = message.content || [];
  const body = blocks.map((block) => {
    if (block.type === "tool_use") {
      return `<div class="dev-tool-card"><strong>${escapeHtml(block.name)}</strong><pre>${escapeHtml(JSON.stringify(block.input || {}, null, 2))}</pre></div>`;
    }
    if (block.type === "tool_result") {
      return `<div class="dev-tool-result${block.is_error ? " error" : ""}"><pre>${escapeHtml(block.content || "")}</pre></div>`;
    }
    return `<div class="dev-message-text">${markdown(block.text || block.content || "")}</div>`;
  }).join("");
  return `
    <article class="dev-message dev-message-${escapeHtml(message.role)}" data-message-id="${message.id}">
      <div class="dev-message-role">${escapeHtml(message.role)}</div>
      <div class="dev-message-body">${body}</div>
      ${message.role === "assistant" ? `<button class="dev-fork-btn" data-fork-at="${message.id}">Fork from here</button>` : ""}
    </article>
  `;
}

export function renderMessages(messages) {
  return messages.map(renderMessage).join("");
}

export function appendPermission(container, event) {
  container.insertAdjacentHTML("beforeend", renderPermissionCard(event));
  container.scrollTop = container.scrollHeight;
}

