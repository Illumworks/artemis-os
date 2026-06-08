// Composer v5 — ProseMirror editor + chat-left/doc-right shell.
//
// Stage 1 (foundation): mounts an editable ProseMirror document in the right
// column, restores draft content on open, autosaves debounced ~1.2s after
// typing stops (lossless — does NOT cut a new version row; that's the explicit
// Save-Version button), keeps the existing left chat working, and exposes the
// 💬 Comments-rail toggle. Later stages (selection→AI edit, claim flags,
// pagination, comments, Google Doc, Actions) attach to this foundation.
//
// All ProseMirror imports are bare specifiers that resolve via the import map
// in public/index.html to local files in /public/vendor/prosemirror/ — no
// runtime CDN.

import { EditorState } from "prosemirror-state";
import { EditorView } from "prosemirror-view";
import { DOMParser as PMDOMParser, DOMSerializer, Schema } from "prosemirror-model";
import { schema as basicSchema } from "prosemirror-schema-basic";
import { addListNodes } from "prosemirror-schema-list";
import { exampleSetup } from "prosemirror-example-setup";

import { escapeHtml } from "../core/utils.js";
import {
  composeWritingDraftApi,
  fetchWritingDraft,
  updateWritingDraftApi,
} from "../core/api.js";

// Schema: paragraphs, headings, lists, bold/italic — the prototype's basic set.
const composerSchema = new Schema({
  nodes: addListNodes(basicSchema.spec.nodes, "paragraph block*", "block"),
  marks: basicSchema.spec.marks,
});

const AUTOSAVE_DELAY_MS = 1200;

const esc = (v) => escapeHtml(v ?? "");

// Per-mount runtime state. The writing-studio shell may re-render the surface
// many times; we destroy and recreate per mount but cache the editor instance
// inside the closure of mountComposerV5.

/**
 * Mount the v5 composer into the given root element.
 *
 * @param {HTMLElement} rootEl
 * @param {object}      params
 * @param {object}      params.draft         — current selected draft (with .id, .title, .content, .threadMessages, .versions, .status)
 * @param {Array}       params.allDrafts     — sibling drafts for the picker
 * @param {object}      params.callbacks
 *   onDraftReloaded(draft)  — called after live save / version save so host can refresh
 *   onSelectDraft(draftId)  — host should load that draft
 *   onOpenVersionHistory()  — host opens the version-history panel
 *   onError(msg)            — host displays a status error
 *   onStatus(msg)           — host displays a status string
 */
export function mountComposerV5(rootEl, { draft, allDrafts = [], callbacks = {} } = {}) {
  if (!rootEl) return null;
  if (!draft) {
    rootEl.innerHTML = renderEmptyState();
    return null;
  }

  rootEl.innerHTML = renderShell({ draft, allDrafts });

  const editorHost = rootEl.querySelector('[data-cv5="editor"]');
  const savingEl = rootEl.querySelector('[data-cv5="saving"]');
  const docGridEl = rootEl.querySelector('[data-cv5="doc-grid"]');
  const commentsToggleEl = rootEl.querySelector('[data-cv5="comments-toggle"]');
  const chatThreadEl = rootEl.querySelector('[data-cv5="chat-thread"]');
  const chatInputEl = rootEl.querySelector('[data-cv5="chat-input"]');
  const chatSendEl = rootEl.querySelector('[data-cv5="chat-send"]');
  const saveVersionEl = rootEl.querySelector('[data-cv5="save-version"]');
  const draftsBtnEl = rootEl.querySelector('[data-cv5="drafts-btn"]');
  const draftsWrapEl = rootEl.querySelector('[data-cv5="drafts-wrap"]');
  const pickerEl = rootEl.querySelector('[data-cv5="drafts-picker"]');
  const historyBtnEl = rootEl.querySelector('[data-cv5="open-history"]');

  // ── ProseMirror editor ─────────────────────────────────────────────────────
  const initialDoc = textToProseMirrorDoc(draft.content || "", composerSchema);
  const state = EditorState.create({
    doc: initialDoc,
    schema: composerSchema,
    plugins: exampleSetup({ schema: composerSchema, menuBar: false }),
  });
  const view = new EditorView(editorHost, {
    state,
    dispatchTransaction(tr) {
      const next = view.state.apply(tr);
      view.updateState(next);
      if (tr.docChanged) scheduleAutosave();
    },
  });

  // ── Autosave plumbing ──────────────────────────────────────────────────────
  let autosaveTimer = null;
  let inflight = null;          // {promise, content}
  let pendingContent = null;    // text snapshot waiting if a save is in flight
  let dirty = false;            // true while there are unsaved local changes
  let lastSavedContent = serializeDocToText(view.state.doc);
  let destroyed = false;
  let currentDraftId = draft.id;

  function setSavingIndicator(state, message) {
    if (!savingEl) return;
    savingEl.classList.remove("is-busy", "is-error");
    if (state === "busy") {
      savingEl.classList.add("is-busy");
      savingEl.textContent = message || "Saving…";
    } else if (state === "error") {
      savingEl.classList.add("is-error");
      savingEl.textContent = message || "Save failed";
    } else if (state === "saved") {
      savingEl.textContent = message || "Saved";
    } else {
      savingEl.textContent = "";
    }
  }

  function scheduleAutosave() {
    dirty = true;
    if (autosaveTimer) clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(() => {
      autosaveTimer = null;
      void runAutosave();
    }, AUTOSAVE_DELAY_MS);
  }

  async function runAutosave() {
    if (destroyed) return;
    const snapshot = serializeDocToText(view.state.doc);
    if (snapshot === lastSavedContent) {
      dirty = false;
      return;
    }
    if (inflight) {
      // A save is already in flight — queue the latest snapshot for after it
      // resolves, so we never drop the user's last keystrokes.
      pendingContent = snapshot;
      return;
    }
    inflight = { content: snapshot };
    setSavingIndicator("busy", "Saving…");
    try {
      await updateWritingDraftApi(currentDraftId, { liveContent: snapshot });
      lastSavedContent = snapshot;
      dirty = false;
      setSavingIndicator("saved", "Saved");
    } catch (err) {
      console.error("[composer-v5] autosave failed:", err);
      setSavingIndicator("error", "Save failed");
    } finally {
      inflight = null;
      if (pendingContent !== null && !destroyed) {
        // Re-run with the queued snapshot so the very latest typing lands.
        pendingContent = null;
        void runAutosave();
      }
    }
  }

  async function flushPendingAutosave() {
    if (autosaveTimer) {
      clearTimeout(autosaveTimer);
      autosaveTimer = null;
    }
    if (dirty || inflight) {
      await runAutosave();
      // If another flush queued during inflight, the finally re-runs it.
      while (inflight) {
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => setTimeout(r, 30));
      }
    }
  }

  // ── Save-version (explicit) ────────────────────────────────────────────────
  if (saveVersionEl) {
    saveVersionEl.addEventListener("click", async () => {
      saveVersionEl.disabled = true;
      try {
        // 1. Flush any pending live autosave so the version captures the user's
        //    very latest text — never silently drop keystrokes.
        await flushPendingAutosave();
        // 2. Mint an explicit version row. Backend also clears live_content.
        const content = serializeDocToText(view.state.doc);
        await updateWritingDraftApi(currentDraftId, {
          content,
          changeNote: "Composer save",
          source: "composer-v5",
        });
        lastSavedContent = content;
        dirty = false;
        setSavingIndicator("saved", "Version saved");
        callbacks.onStatus?.("Version saved.");
        callbacks.onDraftReloaded?.(currentDraftId);
      } catch (err) {
        console.error("[composer-v5] save-version failed:", err);
        setSavingIndicator("error", "Save failed");
        callbacks.onError?.(err.message || "Save version failed.");
      } finally {
        saveVersionEl.disabled = false;
      }
    });
  }

  // ── 💬 Comments rail toggle ────────────────────────────────────────────────
  if (commentsToggleEl && docGridEl) {
    commentsToggleEl.addEventListener("click", () => {
      const hidden = docGridEl.classList.toggle("is-comments-hidden");
      commentsToggleEl.classList.toggle("off", hidden);
      commentsToggleEl.title = hidden ? "Show comments" : "Hide comments";
    });
  }

  // ── Drafts picker (Stage 1: minimal popover; Stage 3 builds the full UX) ──
  if (draftsBtnEl && draftsWrapEl) {
    draftsBtnEl.addEventListener("click", (e) => {
      e.stopPropagation();
      draftsWrapEl.classList.toggle("is-open");
    });
    document.addEventListener("click", (e) => {
      if (!draftsWrapEl.contains(e.target)) draftsWrapEl.classList.remove("is-open");
    });
    if (pickerEl) {
      pickerEl.addEventListener("click", (e) => {
        const row = e.target.closest("[data-cv5-draft-id]");
        if (!row) return;
        const id = Number(row.dataset.cv5DraftId);
        if (!id) return;
        draftsWrapEl.classList.remove("is-open");
        callbacks.onSelectDraft?.(id);
      });
    }
  }

  if (historyBtnEl) {
    historyBtnEl.addEventListener("click", () => callbacks.onOpenVersionHistory?.());
  }

  // ── Chat (LEFT column) ─────────────────────────────────────────────────────
  // Reuses the existing compose endpoint. Stage 1 keeps the existing thread
  // working; replies that produce/rewrite a draft also refresh the editor.
  const chatHistory = Array.isArray(draft.threadMessages) ? [...draft.threadMessages] : [];
  renderChatThread();

  function renderChatThread() {
    if (!chatThreadEl) return;
    if (!chatHistory.length) {
      chatThreadEl.innerHTML = `
        <div class="cv5-chat-empty">Ask Amira to draft, rewrite, or refine the document on the right.</div>
      `;
      return;
    }
    chatThreadEl.innerHTML = chatHistory.map(renderChatMessage).join("");
    chatThreadEl.scrollTop = chatThreadEl.scrollHeight;
  }

  function renderChatMessage(m) {
    const role = m.role === "user" ? "user" : "assistant";
    const label = m.label || (role === "user" ? "You" : "Amira");
    const pending = m.pending ? " pending" : "";
    const text = m.text || m.content || "";
    return `
      <div class="cv5-msg ${role}${pending}">
        <div class="cv5-msg-role">${esc(label)}</div>
        <div class="cv5-msg-bub">${esc(text)}</div>
      </div>
    `;
  }

  function autoGrowChatInput() {
    if (!chatInputEl) return;
    chatInputEl.style.height = "auto";
    chatInputEl.style.height = Math.min(chatInputEl.scrollHeight, 140) + "px";
  }

  if (chatInputEl) {
    chatInputEl.addEventListener("input", autoGrowChatInput);
    chatInputEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void sendChatMessage();
      }
    });
  }
  if (chatSendEl) {
    chatSendEl.addEventListener("click", () => void sendChatMessage());
  }

  async function sendChatMessage() {
    if (!chatInputEl) return;
    const request = chatInputEl.value.trim();
    if (!request) return;

    // 1. Flush autosave so the AI sees the user's latest edits as draft context.
    await flushPendingAutosave();

    chatInputEl.value = "";
    autoGrowChatInput();
    const pendingUserId = `cv5-u-${Date.now()}`;
    const pendingAsstId = `cv5-a-${Date.now() + 1}`;
    chatHistory.push({ id: pendingUserId, role: "user", label: "You", text: request });
    chatHistory.push({ id: pendingAsstId, role: "assistant", label: "Amira", text: "Drafting…", pending: true });
    renderChatThread();

    try {
      const resp = await composeWritingDraftApi(currentDraftId, {
        request,
        selectedText: "",
        attachments: [],
      });
      const persistedUser = resp.persistedMessages?.user;
      const persistedAsst = resp.persistedMessages?.assistant;
      // Replace the pending entries with the persisted server-side records.
      const idxU = chatHistory.findIndex((e) => e.id === pendingUserId);
      if (idxU >= 0 && persistedUser) chatHistory[idxU] = persistedUser;
      const idxA = chatHistory.findIndex((e) => e.id === pendingAsstId);
      if (idxA >= 0) {
        chatHistory[idxA] = persistedAsst || {
          id: `cv5-a-${Date.now()}`,
          role: "assistant",
          label: "Amira",
          text: resp.responseText || "",
        };
      }
      renderChatThread();
      // If the compose engine produced a new draft body, refresh the editor
      // so the chat reply lands as the visible doc. (Stage 1 keeps the
      // existing compose path; a chat that *replaces* the draft body comes
      // back via /drafts/{id} → versions[0].content or live_content.)
      try {
        const refreshed = await fetchWritingDraft(currentDraftId);
        if (refreshed?.content && refreshed.content !== lastSavedContent) {
          replaceEditorContent(refreshed.content);
          lastSavedContent = refreshed.content;
        }
      } catch (e) {
        // non-fatal — chat still landed
      }
      callbacks.onStatus?.("Drafted with profile, rules, and draft context.");
    } catch (err) {
      console.error("[composer-v5] compose failed:", err);
      const idxA = chatHistory.findIndex((e) => e.id === pendingAsstId);
      if (idxA >= 0) chatHistory.splice(idxA, 1);
      renderChatThread();
      callbacks.onError?.(err.message || "Drafting failed.");
    }
  }

  function replaceEditorContent(newText) {
    const newDoc = textToProseMirrorDoc(newText, composerSchema);
    const newState = EditorState.create({
      doc: newDoc,
      schema: composerSchema,
      plugins: exampleSetup({ schema: composerSchema, menuBar: false }),
    });
    view.updateState(newState);
  }

  // ── Public handle ──────────────────────────────────────────────────────────
  return {
    destroy() {
      destroyed = true;
      if (autosaveTimer) clearTimeout(autosaveTimer);
      try { view.destroy(); } catch (_) { /* noop */ }
    },
    flush: flushPendingAutosave,
    getEditorView: () => view,
  };
}

// ─── render templates ────────────────────────────────────────────────────────

function renderEmptyState() {
  return `
    <div class="cv5-root">
      <div class="cv5-empty">
        <h3>No draft selected</h3>
        <p>Open a draft to load the editable document and the writing chat.</p>
      </div>
    </div>
  `;
}

function renderShell({ draft, allDrafts }) {
  const titleText = draft.title || `Draft ${draft.id}`;
  const status = (draft.status || "draft").replace(/_/g, " ");
  const versionsCount = Array.isArray(draft.versions) ? draft.versions.length : 0;
  const varsLabel = versionsCount > 0 ? `${versionsCount} version${versionsCount === 1 ? "" : "s"}` : "0 versions";
  return `
    <div class="cv5-root">
      <header class="cv5-hdr">
        <div class="cv5-hdr-drafts-wrap" data-cv5="drafts-wrap">
          <button type="button" class="cv5-hdr-drafts-btn" data-cv5="drafts-btn" aria-haspopup="listbox" aria-expanded="false" title="Open drafts">
            <span aria-hidden="true">📁</span>
            <span style="color: var(--cv5-ink3);">▾</span>
          </button>
          <div class="cv5-picker" data-cv5="drafts-picker" role="listbox" aria-label="Drafts">
            <div class="cv5-picker-head">Drafts</div>
            ${renderPickerRows(allDrafts, draft.id)}
          </div>
        </div>
        <span class="cv5-hdr-title" title="${esc(titleText)}">${esc(titleText)}</span>
        <span class="cv5-hdr-status">${esc(status)}</span>
        <div class="cv5-hdr-spacer"></div>
        <button type="button" class="cv5-hdr-ind is-placeholder" disabled aria-disabled="true" title="Variants — Stage 8">◳ ${esc(varsLabel)}</button>
        <button type="button" class="cv5-hdr-ind is-placeholder" disabled aria-disabled="true" title="Rules — Stage 8">✓ rules</button>
        <button type="button" class="cv5-hdr-ind" data-cv5="comments-toggle" title="Show / hide comments rail">💬 Comments</button>
        <button type="button" class="cv5-hdr-ind" data-cv5="open-history" title="Version history">⟲ History</button>
        <button type="button" class="cv5-hdr-gdoc is-placeholder" disabled aria-disabled="true" title="Google Doc — Stage 7">⊞ Google Doc</button>
        <button type="button" class="cv5-hdr-ind is-placeholder" disabled aria-disabled="true" title="More actions — Stage 8">⋯</button>
        <span class="cv5-hdr-saving" data-cv5="saving" aria-live="polite"></span>
        <button type="button" class="cv5-btn-primary" data-cv5="save-version">Save version</button>
      </header>

      <div class="cv5-panes">
        <section class="cv5-chat-col" aria-label="Writing chat">
          <div class="cv5-chat-eyebrow">Amira · writing chat</div>
          <div class="cv5-chat-thread" data-cv5="chat-thread"></div>
          <div class="cv5-chat-composer">
            <div class="cv5-chat-cbox">
              <textarea class="cv5-chat-input" data-cv5="chat-input" rows="1" placeholder="Ask Amira to draft, rewrite, or refine…" aria-label="Message Amira"></textarea>
              <button type="button" class="cv5-chat-send" data-cv5="chat-send" aria-label="Send">↑</button>
            </div>
          </div>
        </section>

        <section class="cv5-doc-col" aria-label="Document">
          <div class="cv5-doc-scroll">
            <div class="cv5-doc-grid" data-cv5="doc-grid">
              <div class="cv5-paper">
                <div data-cv5="editor"></div>
              </div>
              <aside class="cv5-margin-col" aria-label="Comments">
                <div class="cv5-margin-empty">
                  Comments anchor here. (Stage 6 adds the floating Google-Docs-style
                  margin comments with @-mention + ping; this stage ships the rail
                  toggle only.)
                </div>
              </aside>
            </div>
          </div>
        </section>
      </div>
    </div>
  `;
}

function renderPickerRows(drafts, activeId) {
  if (!Array.isArray(drafts) || drafts.length === 0) {
    return `<div class="cv5-picker-empty">No drafts yet.</div>`;
  }
  return drafts
    .slice(0, 50)
    .map((d) => {
      const cls = d.id === activeId ? "cv5-picker-row is-active" : "cv5-picker-row";
      const title = d.title || `Draft ${d.id}`;
      const meta = [d.asset_type, d.status].filter(Boolean).join(" · ");
      return `
        <button type="button" class="${cls}" data-cv5-draft-id="${d.id}">
          <span aria-hidden="true">📄</span>
          <span>${esc(title)}</span>
          <span class="cv5-picker-row-meta">${esc(meta)}</span>
        </button>
      `;
    })
    .join("");
}

// ─── ProseMirror ↔ stored content (plain-text-with-markdown) ────────────────
//
// PERSISTED-CONTENT-FORMAT DECISION (flagged in the brief):
// We persist the draft body as **plain text with light Markdown**
// (`#` headings, `-`/`*` lists, `1.` lists, blank-line paragraph breaks,
// `**bold**` / `*italic*` inline). The existing draft `content` field in
// `campaign_deliverables.deliverable_metadata.versions[].content` already
// holds this format — it's what the compose engine injects into the LLM
// system prompt, what `renderWritingRichText` parses for the chat-thread
// preview, and what Save-as-template / Google Doc consumers read. Storing
// HTML would silently pollute the LLM prompt and break every consumer.
//
// On open: parse the stored text → ProseMirror doc.
// On save: serialize the ProseMirror doc → stored text.
// Lossless for the Stage-1 schema (paragraphs, headings, lists, bold/italic).

function textToProseMirrorDoc(text, schema) {
  // Build HTML from a tiny Markdown subset (matches parseWritingRichText in
  // writing-studio.js), then let ProseMirror's DOM parser turn that HTML
  // into a doc that honors the schema.
  const html = textToHtml(text || "");
  const tmpl = document.createElement("div");
  tmpl.innerHTML = html;
  return PMDOMParser.fromSchema(schema).parse(tmpl);
}

function textToHtml(src) {
  const raw = String(src || "").replace(/\r\n/g, "\n").trim();
  if (!raw) return "<p></p>";
  const lines = raw.split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(6, heading[1].length);
      out.push(`<h${level}>${inline(heading[2].trim())}</h${level}>`);
      i += 1;
      continue;
    }
    const ulMatch = line.match(/^(\s*)[-*•]\s+(.+)$/);
    const olMatch = line.match(/^(\s*)(\d+)[.)]\s+(.+)$/);
    if (ulMatch || olMatch) {
      const ordered = Boolean(olMatch);
      const items = [];
      while (i < lines.length) {
        const cand = lines[i];
        const u2 = cand.match(/^(\s*)[-*•]\s+(.+)$/);
        const o2 = cand.match(/^(\s*)(\d+)[.)]\s+(.+)$/);
        if (!(u2 || o2)) break;
        const txt = (o2 ? o2[3] : u2[2]).trim();
        if (txt) items.push(txt);
        i += 1;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${tag}>`);
      continue;
    }
    const para = [];
    while (i < lines.length) {
      const cand = lines[i];
      if (!cand.trim()) break;
      if (/^(#{1,6})\s+/.test(cand)) break;
      if (/^(\s*)[-*•]\s+/.test(cand)) break;
      if (/^(\s*)(\d+)[.)]\s+/.test(cand)) break;
      para.push(cand.trim());
      i += 1;
    }
    if (para.length) {
      out.push(`<p>${inline(para.join(" "))}</p>`);
    }
  }
  return out.join("") || "<p></p>";

  function inline(s) {
    const escaped = String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return escaped
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  }
}

function serializeDocToText(doc) {
  const lines = [];
  doc.forEach((node) => {
    lines.push(serializeBlock(node));
  });
  return lines.filter((l) => l !== null).join("\n\n").trim();
}

function serializeBlock(node, indent = 0) {
  const name = node.type.name;
  if (name === "paragraph") {
    return serializeInline(node);
  }
  if (name === "heading") {
    const level = Math.min(6, Math.max(1, node.attrs.level || 1));
    return `${"#".repeat(level)} ${serializeInline(node)}`;
  }
  if (name === "bullet_list" || name === "ordered_list") {
    const ordered = name === "ordered_list";
    const items = [];
    let n = 1;
    node.forEach((child) => {
      // each list_item contains paragraph(s) and possibly nested lists
      let firstParaText = "";
      const nested = [];
      child.forEach((blk) => {
        if (blk.type.name === "paragraph" && !firstParaText) {
          firstParaText = serializeInline(blk);
        } else {
          nested.push(serializeBlock(blk, indent + 1));
        }
      });
      const bullet = ordered ? `${n}.` : "-";
      items.push(`${"  ".repeat(indent)}${bullet} ${firstParaText}`);
      if (nested.length) items.push(nested.join("\n"));
      n += 1;
    });
    return items.join("\n");
  }
  if (name === "blockquote") {
    const inner = [];
    node.forEach((child) => inner.push(serializeBlock(child)));
    return inner
      .join("\n\n")
      .split("\n")
      .map((l) => `> ${l}`)
      .join("\n");
  }
  if (name === "code_block") {
    return "```\n" + node.textContent + "\n```";
  }
  if (name === "horizontal_rule") {
    return "---";
  }
  // Fallback: textContent
  return node.textContent || "";
}

function serializeInline(node) {
  const parts = [];
  node.forEach((child) => {
    if (child.isText) {
      let text = child.text || "";
      const hasBold = child.marks.some((m) => m.type.name === "strong");
      const hasItalic = child.marks.some((m) => m.type.name === "em");
      if (hasBold) text = `**${text}**`;
      if (hasItalic) text = `*${text}*`;
      parts.push(text);
    } else if (child.type.name === "hard_break") {
      parts.push("\n");
    } else {
      parts.push(child.textContent || "");
    }
  });
  return parts.join("").trim();
}
