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

import { EditorState, Plugin, PluginKey } from "prosemirror-state";
import { EditorView, Decoration, DecorationSet } from "prosemirror-view";
import { DOMParser as PMDOMParser, DOMSerializer, Schema } from "prosemirror-model";
import { schema as basicSchema } from "prosemirror-schema-basic";
import { addListNodes } from "prosemirror-schema-list";
import { exampleSetup } from "prosemirror-example-setup";

import { escapeHtml } from "../core/utils.js";
import {
  applyWritingTemplateApi,
  approveClaimApi,
  composeWritingDraftApi,
  createClaimApi,
  createWritingDraftApi,
  createWritingFolderApi,
  fetchWritingDraft,
  fetchWritingStudioOverview,
  listWritingTemplatesApi,
  rewriteSpanApi,
  scanDraftClaimsApi,
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
 * @param {Array}       params.allFolders    — folders (Stage 3 picker tree)
 * @param {object}      params.callbacks
 *   onDraftReloaded(draft)  — called after live save / version save so host can refresh
 *   onSelectDraft(draftId)  — host should load that draft
 *   onOpenVersionHistory()  — host opens the version-history panel
 *   onError(msg)            — host displays a status error
 *   onStatus(msg)           — host displays a status string
 */
export function mountComposerV5(rootEl, { draft, allDrafts = [], allFolders = [], callbacks = {} } = {}) {
  if (!rootEl) return null;
  if (!draft) {
    rootEl.innerHTML = renderEmptyState();
    return null;
  }

  // Stage-3 picker state — kept in mount closure so expand/collapse persists
  // across re-renders inside this mount. Keys are stringified folder ids; the
  // sentinel `"ungrouped"` controls the "Ungrouped" group, and `"all"` is
  // computed from the others (it's the full list and is always shown expanded).
  const pickerExpandedFolders = new Set();
  // Default: expand the folder that contains the current draft, if any.
  if (draft.folder_id != null) pickerExpandedFolders.add(String(draft.folder_id));

  let currentDrafts = Array.isArray(allDrafts) ? allDrafts.slice() : [];
  let currentFolders = Array.isArray(allFolders) ? allFolders.slice() : [];

  rootEl.innerHTML = renderShell({ draft, allDrafts: currentDrafts, allFolders: currentFolders, expandedFolders: pickerExpandedFolders });

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

  // ── Stage 4: Claim-flags plugin ────────────────────────────────────────────
  //
  // Stores the current set of claim-flag decorations (orange double-underlines).
  // Decorations are updated by calling updateClaimDecorations(flags) where
  // flags is the array returned by POST .../claim-scan.
  //
  // Plugin state = { decoSet: DecorationSet }.
  // We update via a transaction meta key so ProseMirror correctly maps positions
  // through document changes.

  const claimFlagsKey = new PluginKey("claimFlags");

  const claimFlagsPlugin = new Plugin({
    key: claimFlagsKey,
    state: {
      init(_config, editorState) {
        return { decoSet: DecorationSet.empty };
      },
      apply(tr, pluginState, _oldState, newState) {
        const meta = tr.getMeta(claimFlagsKey);
        if (meta && meta.flags !== undefined) {
          // Rebuild decorations from the new flags array.
          const decos = meta.flags.map((f) =>
            Decoration.inline(f.pmFrom, f.pmTo, {
              class: "cv5-claim-flag cv5-claim-flag--glow",
              "data-claim-reason": f.reason,
              "data-claim-text": f.text,
              "data-claim-nearest": JSON.stringify(f.nearestApproved || []),
              title: "Claim not in Register — click to resolve",
            })
          );
          return { decoSet: DecorationSet.create(newState.doc, decos) };
        }
        // Map existing decorations through any document changes.
        return { decoSet: pluginState.decoSet.map(tr.mapping, newState.doc) };
      },
    },
    props: {
      decorations(state) {
        return this.getState(state).decoSet;
      },
    },
  });

  // ── ProseMirror editor ─────────────────────────────────────────────────────
  const initialDoc = textToProseMirrorDoc(draft.content || "", composerSchema);
  const state = EditorState.create({
    doc: initialDoc,
    schema: composerSchema,
    plugins: [...exampleSetup({ schema: composerSchema, menuBar: false }), claimFlagsPlugin],
  });
  const view = new EditorView(editorHost, {
    state,
    dispatchTransaction(tr) {
      const next = view.state.apply(tr);
      view.updateState(next);
      if (tr.docChanged) {
        scheduleAutosave();
        scheduleScan();
      }
    },
  });

  // ── Stage 4: Claim-scan + decoration wiring ───────────────────────────────

  // Live scan state — tracks inflight scan and debounce timer.
  let scanTimer = null;
  const SCAN_DEBOUNCE_MS = 1800; // trigger scan 1.8s after last keypress

  // serializeDocToTextWithMap — single-pass serializer that emits the same
  // plain-text representation as serializeDocToText() while simultaneously
  // building a posMap array:
  //
  //   posMap[i]  = the PM position of the content char at text index i
  //   posMap[text.length]  = the PM end position (for inclusive end-of-text flags)
  //
  // For prefix chars (e.g. "# ", "- ", "1. ") and inter-block separators ("\n\n"),
  // the posMap entries point to the START position of the next real-content
  // character in the doc (or the last recorded position if there is none) so
  // that a flag whose text.slice(start, end) spans prefix chars will still
  // resolve to a PM range that brackets the actual content — not the phantom
  // chars that exist only in the serialization.
  //
  // Convention: posMap entries are filled LEFT-to-RIGHT.  Prefix/separator
  // entries are backfilled once the first real-content pos is known.
  function serializeDocToTextWithMap(doc) {
    const chars = [];   // individual text chars (joined at the end)
    const posMap = [];  // posMap[i] = PM pos of chars[i]; posMap[chars.length] = pos-after-last

    // pending: indices of posMap slots emitted for prefix/separator chars that
    // don't yet have a real PM pos.  Filled by flush() once a real pos appears.
    let pending = [];

    // Last real PM position seen — used to compute the pos-after-last terminal.
    let lastRealPMPos = 0;

    function flush(pmPos) {
      for (const idx of pending) posMap[idx] = pmPos;
      pending = [];
    }

    function emitReal(ch, pmPos) {
      flush(pmPos);
      posMap.push(pmPos);
      chars.push(ch);
      lastRealPMPos = pmPos;
    }

    function emitPhantom(str) {
      for (const ch of str) {
        pending.push(posMap.length);
        posMap.push(-1); // placeholder; filled by flush()
        chars.push(ch);
      }
    }

    let firstBlock = true;

    doc.forEach((blockNode, blockOffset) => {
      const serialized = serializeBlockWithMap(blockNode, blockOffset, 0);
      if (serialized === null) return;

      if (!firstBlock) {
        // "\n\n" separator between top-level blocks is phantom.
        emitPhantom("\n\n");
      }
      firstBlock = false;

      serialized.forEach(([ch, pmPos]) => {
        if (pmPos === -1) {
          emitPhantom(ch);
        } else {
          emitReal(ch, pmPos);
        }
      });
    });

    // Terminal slot: posMap[chars.length] = one position PAST the last real
    // content char, so that posMap[f.end] gives a correct exclusive PM end.
    // Use lastRealPMPos + 1 so the terminal exactly brackets the last char.
    // If the doc is empty, fall back to doc.content.size.
    const terminalPos = chars.length > 0 ? lastRealPMPos + 1 : doc.content.size;
    flush(terminalPos); // also fill any trailing phantom slots
    posMap.push(terminalPos);

    // Mirror serializeDocToText's final .trim() while keeping posMap aligned.
    // Calculate the leading/trailing whitespace count so we can slice both
    // arrays in lockstep.
    const rawText = chars.join("");
    const trimmedText = rawText.trim();
    if (!trimmedText) {
      return { text: "", posMap: [terminalPos] };
    }
    const leadTrim = rawText.length - rawText.trimStart().length;
    // posMap has rawText.length + 1 entries.
    // trimmedText[j] == rawText[leadTrim + j], so slicedPosMap[j] = posMap[leadTrim + j].
    // We need trimmedText.length + 1 entries (including the exclusive-end slot).
    const slicedPosMap = posMap.slice(leadTrim, leadTrim + trimmedText.length + 1);

    return { text: trimmedText, posMap: slicedPosMap };
  }

  // serializeBlockWithMap — mirrors serializeBlock() but returns an array of
  // [char, pmPos] pairs.  pmPos === -1 means "phantom" (prefix/separator char
  // that has no PM counterpart).
  function serializeBlockWithMap(node, nodeOffset, indent) {
    const name = node.type.name;

    if (name === "paragraph") {
      return serializeInlineWithMap(node, nodeOffset);
    }

    if (name === "heading") {
      const level = Math.min(6, Math.max(1, node.attrs.level || 1));
      const prefix = "#".repeat(level) + " ";
      const phantom = prefix.split("").map((ch) => [ch, -1]);
      const inlinePairs = serializeInlineWithMap(node, nodeOffset);
      return [...phantom, ...inlinePairs];
    }

    if (name === "bullet_list" || name === "ordered_list") {
      const ordered = name === "ordered_list";
      const result = [];
      let n = 1;
      let firstItem = true;
      node.forEach((child, childOffset) => {
        if (!firstItem) {
          result.push(["\n", -1]);
        }
        firstItem = false;

        const bullet = ordered ? `${n}.` : "-";
        const bulletPrefix = "  ".repeat(indent) + bullet + " ";
        for (const ch of bulletPrefix) result.push([ch, -1]);
        n += 1;

        let firstPara = true;
        child.forEach((blk, blkOffset) => {
          const blkAbsOffset = nodeOffset + 1 /* list open */ + childOffset + 1 /* item open */ + blkOffset;
          if (blk.type.name === "paragraph" && firstPara) {
            firstPara = false;
            const pairs = serializeInlineWithMap(blk, blkAbsOffset);
            result.push(...pairs);
          } else if (!firstPara) {
            const nested = serializeBlockWithMap(blk, blkAbsOffset, indent + 1);
            if (nested) {
              result.push(["\n", -1]);
              result.push(...nested);
            }
          }
        });
      });
      return result;
    }

    if (name === "blockquote") {
      const result = [];
      let firstInner = true;
      node.forEach((child, childOffset) => {
        if (!firstInner) result.push(["\n", -1], ["\n", -1]);
        firstInner = false;
        const inner = serializeBlockWithMap(child, nodeOffset + 1 + childOffset, indent);
        if (inner) {
          result.push([">", -1], [" ", -1]);
          result.push(...inner);
        }
      });
      return result;
    }

    if (name === "code_block") {
      const result = [];
      for (const ch of "```\n") result.push([ch, -1]);
      // textContent inline — use the opening pos of the code_block + 1 for the
      // node-open token, then character offsets within.
      const textContent = node.textContent || "";
      for (let i = 0; i < textContent.length; i++) {
        result.push([textContent[i], nodeOffset + 1 + i]);
      }
      for (const ch of "\n```") result.push([ch, -1]);
      return result;
    }

    if (name === "horizontal_rule") {
      return ["---"].join("").split("").map((ch) => [ch, -1]);
    }

    // Fallback
    const fallback = node.textContent || "";
    return fallback.split("").map((ch, i) => [ch, nodeOffset + 1 + i]);
  }

  // serializeInlineWithMap — mirrors serializeInline() but returns [char, pmPos]
  // pairs.  Each content char maps to the PM position of its text node.
  // Bold/italic wrappers (**  **  *  *) are phantom chars (pmPos === -1).
  function serializeInlineWithMap(node, nodeOffset) {
    const result = [];
    node.forEach((child, childOffset) => {
      const childAbsPos = nodeOffset + 1 /* node open token */ + childOffset;
      if (child.isText) {
        const text = child.text || "";
        const hasBold   = child.marks.some((m) => m.type.name === "strong");
        const hasItalic = child.marks.some((m) => m.type.name === "em");
        if (hasBold)   for (const ch of "**") result.push([ch, -1]);
        if (hasItalic) for (const ch of "*")  result.push([ch, -1]);
        for (let i = 0; i < text.length; i++) {
          result.push([text[i], childAbsPos + i]);
        }
        if (hasBold)   for (const ch of "**") result.push([ch, -1]);
        if (hasItalic) for (const ch of "*")  result.push([ch, -1]);
      } else if (child.type.name === "hard_break") {
        result.push(["\n", -1]);
      } else {
        const fallback = child.textContent || "";
        for (let i = 0; i < fallback.length; i++) {
          result.push([fallback[i], childAbsPos + i]);
        }
      }
    });
    // serializeInline trims — drop leading/trailing phantom spaces but keep
    // content chars.  For the posMap version we trim the phantom trailing
    // whitespace by just not trimming content chars at all; the serializer
    // contract is that the text() output matches serializeDocToText().
    // We preserve the trim by stripping leading/trailing phantom-only chars.
    let lo = 0;
    while (lo < result.length && result[lo][1] === -1 && result[lo][0].trim() === "") lo += 1;
    let hi = result.length;
    while (hi > lo && result[hi - 1][1] === -1 && result[hi - 1][0].trim() === "") hi -= 1;
    return result.slice(lo, hi);
  }

  async function runClaimScan() {
    if (destroyed) return;
    const { text: fullText, posMap } = serializeDocToTextWithMap(view.state.doc);
    if (!fullText.trim()) return;

    let result;
    try {
      result = await scanDraftClaimsApi(currentDraftId, { text: fullText });
    } catch (err) {
      // Scan failures are non-fatal — don't surface to user.
      console.warn("[composer-v5] claim-scan failed:", err);
      return;
    }
    if (destroyed) return;

    const docSize = view.state.doc.content.size;
    const flags = (result.flags || []).map((f) => {
      // posMap[i]             = PM pos of text char i (inclusive start)
      // posMap[text.length]   = PM pos one past the last char (exclusive end)
      // f.start / f.end are Python-slice offsets (exclusive end) into fullText.
      const clampStart = Math.max(0, Math.min(f.start, posMap.length - 1));
      const clampEnd   = Math.max(0, Math.min(f.end,   posMap.length - 1));
      const pmFrom = posMap[clampStart] ?? 0;
      const pmTo   = posMap[clampEnd]   ?? docSize;
      return { ...f, pmFrom, pmTo };
    }).filter((f) => f.pmFrom < f.pmTo);

    // Dispatch a transaction carrying the new flags for the plugin to pick up.
    const tr = view.state.tr.setMeta(claimFlagsKey, { flags });
    view.dispatch(tr);

    // One-time entrance glow: remove the glow class after the animation.
    setTimeout(() => {
      editorHost.querySelectorAll(".cv5-claim-flag--glow").forEach((el) => {
        el.classList.remove("cv5-claim-flag--glow");
      });
    }, 1200);
  }

  function scheduleScan() {
    if (scanTimer) clearTimeout(scanTimer);
    scanTimer = setTimeout(() => {
      scanTimer = null;
      void runClaimScan();
    }, SCAN_DEBOUNCE_MS);
  }

  // Kick off an initial scan after mount (after a short delay to let the
  // editor paint first — keeps the initial load from blocking the UI).
  setTimeout(() => void runClaimScan(), 500);

  // ── Stage 4: Claim-flag popover ───────────────────────────────────────────
  //
  // A fixed-position popover that appears when the user clicks a flagged span.
  // Contains: reason + nearest-approved context + Approve / Edit / Find source.
  // Approve → POST /claims (create as approved) → re-scan → flag clears.

  const claimPopover = document.createElement("div");
  claimPopover.className = "cv5-claim-popover";
  claimPopover.setAttribute("role", "dialog");
  claimPopover.setAttribute("aria-label", "Claim flag");
  claimPopover.style.display = "none";
  document.body.appendChild(claimPopover);

  let activeClaimFlag = null; // the DOM element that opened the popover

  function positionClaimPopover(flagEl) {
    const r = flagEl.getBoundingClientRect();
    const m = 12;
    const popW = claimPopover.offsetWidth || 264;
    const popH = claimPopover.offsetHeight || 160;
    let left = Math.min(r.left, window.innerWidth - popW - m);
    if (left < m) left = m;
    let top = r.bottom + 8;
    if (top + popH > window.innerHeight - m) top = Math.max(m, r.top - popH - 8);
    claimPopover.style.left = left + "px";
    claimPopover.style.top  = top + "px";
  }

  function openClaimPopover(flagEl) {
    if (activeClaimFlag === flagEl && claimPopover.style.display !== "none") {
      closeClaimPopover();
      return;
    }
    activeClaimFlag = flagEl;
    const reason    = flagEl.dataset.claimReason || "quantified";
    const claimText = flagEl.dataset.claimText   || flagEl.textContent || "";
    let nearest = [];
    try { nearest = JSON.parse(flagEl.dataset.claimNearest || "[]"); } catch (_) { /* noop */ }

    const reasonLabel = {
      quantified:   "Quantified claim",
      superlative:  "Superlative / exclusivity claim",
      comparative:  "Comparative claim",
    }[reason] || "Strong claim";

    const nearestHtml = nearest.length
      ? `<div class="cv5-claim-pop-nearest">
           <div class="cv5-claim-pop-nearest-label">Nearest approved:</div>
           ${nearest.map((n) => `
             <div class="cv5-claim-pop-nearest-item">
               <span class="cv5-claim-pop-sim">${Math.round(n.similarity * 100)}%</span>
               <span>${esc(n.phrasing)}</span>
             </div>`).join("")}
         </div>`
      : "";

    claimPopover.innerHTML = `
      <div class="cv5-claim-pop-hd">
        <span class="cv5-claim-pop-icon" aria-hidden="true">⚠</span>
        ${esc(reasonLabel)} — not in Register
      </div>
      <div class="cv5-claim-pop-body">Verify the source and approve before sending, or edit to match a registered claim.</div>
      ${nearestHtml}
      <div class="cv5-claim-pop-actions">
        <button type="button" class="cv5-claim-btn cv5-claim-btn-approve" data-claim-text="${esc(claimText)}">✓ Approve claim</button>
        <button type="button" class="cv5-claim-btn cv5-claim-btn-edit">Edit</button>
        <button type="button" class="cv5-claim-btn cv5-claim-btn-source">Find source</button>
      </div>
    `;

    claimPopover.style.display = "block";
    positionClaimPopover(flagEl);

    // Wire Approve button.
    claimPopover.querySelector(".cv5-claim-btn-approve").addEventListener("click", async (e) => {
      e.stopPropagation();
      const text = e.currentTarget.dataset.claimText || claimText;
      await handleApprove(text);
    });

    // Edit: close popover, let user edit.
    claimPopover.querySelector(".cv5-claim-btn-edit").addEventListener("click", () => {
      closeClaimPopover();
    });

    // Find source: stub — close popover for now.
    claimPopover.querySelector(".cv5-claim-btn-source").addEventListener("click", () => {
      closeClaimPopover();
      console.info("[composer-v5] Find source: not yet implemented.");
    });
  }

  function closeClaimPopover() {
    claimPopover.style.display = "none";
    claimPopover.innerHTML = "";
    activeClaimFlag = null;
  }

  async function handleApprove(claimText) {
    // Create the claim as APPROVED directly (status=approved is the default
    // in the create endpoint, or we create as proposed then approve — but the
    // server's create_claim default status is "proposed" so we use the two-
    // step path: create → approve).
    const uniqueCode = "user-" + Date.now();
    try {
      const created = await createClaimApi({
        claimCode: uniqueCode,
        category: "user-approved",
        approvedPhrasing: claimText,
        notes: "Approved from composer claim flag",
      });
      // The create endpoint creates as "proposed"; approve it immediately.
      if (created.status !== "approved") {
        await approveClaimApi(created.id);
      }
      // Green flash on the flag element then close popover + re-scan.
      if (activeClaimFlag) {
        activeClaimFlag.classList.add("cv5-claim-flag--approved");
        setTimeout(() => {
          if (activeClaimFlag) activeClaimFlag.classList.remove("cv5-claim-flag--approved");
        }, 1600);
      }
      closeClaimPopover();
      // Re-scan so the newly approved claim suppresses this span.
      setTimeout(() => void runClaimScan(), 300);
      callbacks.onStatus?.("Claim approved and added to the Register.");
    } catch (err) {
      console.error("[composer-v5] approve claim failed:", err);
      callbacks.onError?.(err.message || "Failed to approve claim.");
    }
  }

  // Delegate click on flagged spans inside the editor.
  editorHost.addEventListener("click", (e) => {
    const flagEl = e.target.closest(".cv5-claim-flag");
    if (flagEl) {
      e.stopPropagation();
      openClaimPopover(flagEl);
      return;
    }
    // Click outside a flag but inside editor — close popover.
    closeClaimPopover();
  });

  // Close popover when clicking outside.
  document.addEventListener("click", (e) => {
    if (
      claimPopover.style.display !== "none" &&
      !claimPopover.contains(e.target) &&
      !e.target.closest(".cv5-claim-flag")
    ) {
      closeClaimPopover();
    }
  });

  // ── Selection toolbar (Stage 2) ────────────────────────────────────────────
  //
  // A floating toolbar that appears when the user selects non-empty text.
  // Buttons: Rewrite · Shorten · Lengthen · Tone · Make on-brand · ✎ custom
  //
  // The toolbar is a single <div> injected into the document body (so it can
  // use fixed positioning and not be clipped by the editor's overflow). It is
  // shared per composer mount and destroyed with the composer.
  //
  // Accept/reject UX: after the AI returns a span rewrite, the toolbar hides
  // and a compact preview popover appears near the selected range showing the
  // original and proposed text.  The user accepts (replaces the span in PM) or
  // rejects (no-op) from the popover.

  const selToolbar = document.createElement("div");
  selToolbar.className = "cv5-sel-toolbar";
  selToolbar.setAttribute("role", "toolbar");
  selToolbar.setAttribute("aria-label", "Text editing actions");
  selToolbar.innerHTML = `
    <button type="button" class="cv5-sel-btn" data-cv5-sel-action="Rewrite">Rewrite</button>
    <button type="button" class="cv5-sel-btn" data-cv5-sel-action="Shorten">Shorten</button>
    <button type="button" class="cv5-sel-btn" data-cv5-sel-action="Lengthen">Lengthen</button>
    <button type="button" class="cv5-sel-btn" data-cv5-sel-action="Make more formal">Tone</button>
    <div class="cv5-sel-divider" aria-hidden="true"></div>
    <button type="button" class="cv5-sel-btn cv5-sel-btn-brand" data-cv5-sel-action="Make on-brand">Make on-brand</button>
    <div class="cv5-sel-divider" aria-hidden="true"></div>
    <button type="button" class="cv5-sel-btn cv5-sel-btn-claim" data-cv5-sel-action="__add_claim__" title="Add selection to Claims Register as approved">＋ Add to Claims</button>
    <div class="cv5-sel-divider" aria-hidden="true"></div>
    <button type="button" class="cv5-sel-btn cv5-sel-btn-edit" data-cv5-sel-action="__custom__" title="Custom instruction">✎</button>
  `;
  document.body.appendChild(selToolbar);

  // Track current selection for the toolbar + accept/reject flow.
  let selectionRange = null;   // {from, to} in PM positions
  let selectionText = "";      // plain text of the selected span
  let pendingRewrite = null;   // {originalText, rewrittenText, from, to} during accept/reject

  // Accept/reject popover — shown after AI returns a rewrite.
  const rewritePopover = document.createElement("div");
  rewritePopover.className = "cv5-rewrite-popover";
  rewritePopover.setAttribute("role", "dialog");
  rewritePopover.setAttribute("aria-label", "AI rewrite preview");
  rewritePopover.style.display = "none";
  document.body.appendChild(rewritePopover);

  function positionNearSelection(el) {
    // Place the element just above the selection's bounding rect.
    const domSel = window.getSelection();
    if (!domSel || domSel.rangeCount === 0) return;
    const rect = domSel.getRangeAt(0).getBoundingClientRect();
    if (!rect || (rect.width === 0 && rect.height === 0)) return;
    const elW = el.offsetWidth || 260;
    const gap = 8;
    let left = rect.left + rect.width / 2 - elW / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - elW - 8));
    let top = rect.top - el.offsetHeight - gap;
    if (top < 8) top = rect.bottom + gap; // flip below
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }

  function showSelToolbar() {
    selToolbar.classList.add("is-visible");
    // Must be visible before measuring offsetWidth for positioning.
    positionNearSelection(selToolbar);
  }

  function hideSelToolbar() {
    selToolbar.classList.remove("is-visible");
  }

  function showRewritePopover(originalText, rewrittenText) {
    rewritePopover.innerHTML = `
      <div class="cv5-rwp-label">Proposed rewrite</div>
      <div class="cv5-rwp-diff">
        <div class="cv5-rwp-original">
          <div class="cv5-rwp-badge cv5-rwp-badge-orig">Before</div>
          <div class="cv5-rwp-text">${esc(originalText)}</div>
        </div>
        <div class="cv5-rwp-arrow" aria-hidden="true">→</div>
        <div class="cv5-rwp-proposed">
          <div class="cv5-rwp-badge cv5-rwp-badge-new">After</div>
          <div class="cv5-rwp-text">${esc(rewrittenText)}</div>
        </div>
      </div>
      <div class="cv5-rwp-actions">
        <button type="button" class="cv5-rwp-reject">Reject</button>
        <button type="button" class="cv5-rwp-accept">Accept</button>
      </div>
    `;
    rewritePopover.style.display = "block";
    positionNearSelection(rewritePopover);

    rewritePopover.querySelector(".cv5-rwp-accept").addEventListener("click", acceptRewrite);
    rewritePopover.querySelector(".cv5-rwp-reject").addEventListener("click", rejectRewrite);
  }

  function hideRewritePopover() {
    rewritePopover.style.display = "none";
    rewritePopover.innerHTML = "";
    pendingRewrite = null;
  }

  function acceptRewrite() {
    if (!pendingRewrite) return;
    const { rewrittenText, from, to } = pendingRewrite;
    // Replace ONLY the selected span in the PM document.
    // Parse the rewritten text using the same textToProseMirrorDoc helper so
    // headings/lists/bold are handled correctly — but for a span replacement
    // we want the *inline* content only.  Use a simple text node insertion
    // that respects ProseMirror's schema.
    const tr = view.state.tr;
    // Insert as plain text — the same format the editor stores. Any markdown
    // in the rewritten text will be re-parsed on the next Save/load cycle.
    // For now, replace the range with a plain-text paragraph content.
    // Build a slice from the replacement text.
    const replacementDoc = textToProseMirrorDoc(rewrittenText, composerSchema);
    // Collect all inline text from the replacement doc (handles bold/italic marks).
    const fragment = replacementDoc.content;
    tr.replaceWith(from, to, fragment);
    view.dispatch(tr);
    // Stage-1 autosave will fire because the transaction changed the doc.
    scheduleAutosave();
    hideRewritePopover();
    callbacks.onStatus?.("Span replaced. Autosaving…");
  }

  function rejectRewrite() {
    // Pure no-op — doc is unchanged.
    hideRewritePopover();
    callbacks.onStatus?.("Rewrite rejected — document unchanged.");
  }

  async function triggerSpanRewrite(instruction) {
    if (!selectionRange || !selectionText.trim()) return;
    const { from, to } = selectionRange;
    const originalText = selectionText;

    hideSelToolbar();
    callbacks.onStatus?.("Rewriting…");

    // Flush autosave so the AI sees the user's latest edits as full-draft context.
    await flushPendingAutosave();
    const fullText = serializeDocToText(view.state.doc);

    try {
      const resp = await rewriteSpanApi(currentDraftId, {
        selectedText: originalText,
        instruction,
        fullText,
      });
      const rewrittenText = resp.rewrittenText || "";
      if (!rewrittenText.trim()) {
        callbacks.onError?.("AI returned an empty rewrite. Try again.");
        return;
      }
      // Log trace for the tag-scoped-rules showcase (visible in browser console).
      const ruleCount = resp.trace?.rules?.length ?? 0;
      const ruleTitles = (resp.trace?.rules ?? []).map((r) => r.title).join(", ");
      console.info(
        `[composer-v5] rewrite-span trace: ${ruleCount} rules applied. Titles: ${ruleTitles || "(none)"}`,
        resp.trace
      );
      // Store the pending rewrite so accept/reject can reference it.
      pendingRewrite = { originalText, rewrittenText, from, to };
      showRewritePopover(originalText, rewrittenText);
      callbacks.onStatus?.("Review the proposed rewrite.");
    } catch (err) {
      console.error("[composer-v5] rewrite-span failed:", err);
      callbacks.onError?.(err.message || "Span rewrite failed. Try again.");
    }
  }

  // Custom-ask: show a small inline input in the toolbar for free-text.
  let customAskActive = false;
  function showCustomAskInput() {
    customAskActive = true;
    selToolbar.innerHTML = `
      <input
        type="text"
        class="cv5-sel-custom-input"
        placeholder="Describe what you want…"
        autofocus
        aria-label="Custom rewrite instruction"
      />
      <button type="button" class="cv5-sel-btn cv5-sel-btn-go">Go ↑</button>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-cancel">✕</button>
    `;
    selToolbar.classList.add("is-visible");
    positionNearSelection(selToolbar);
    const input = selToolbar.querySelector(".cv5-sel-custom-input");
    input?.focus();
    selToolbar.querySelector(".cv5-sel-btn-go").addEventListener("click", () => {
      const val = input?.value?.trim();
      if (val) void triggerSpanRewrite(val);
      resetToolbarButtons();
    });
    selToolbar.querySelector(".cv5-sel-btn-cancel").addEventListener("click", () => {
      resetToolbarButtons();
      hideSelToolbar();
    });
    input?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const val = input.value?.trim();
        if (val) void triggerSpanRewrite(val);
        resetToolbarButtons();
      } else if (e.key === "Escape") {
        resetToolbarButtons();
        hideSelToolbar();
      }
    });
  }

  function resetToolbarButtons() {
    customAskActive = false;
    selToolbar.innerHTML = `
      <button type="button" class="cv5-sel-btn" data-cv5-sel-action="Rewrite">Rewrite</button>
      <button type="button" class="cv5-sel-btn" data-cv5-sel-action="Shorten">Shorten</button>
      <button type="button" class="cv5-sel-btn" data-cv5-sel-action="Lengthen">Lengthen</button>
      <button type="button" class="cv5-sel-btn" data-cv5-sel-action="Make more formal">Tone</button>
      <div class="cv5-sel-divider" aria-hidden="true"></div>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-brand" data-cv5-sel-action="Make on-brand">Make on-brand</button>
      <div class="cv5-sel-divider" aria-hidden="true"></div>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-claim" data-cv5-sel-action="__add_claim__" title="Add selection to Claims Register as approved">＋ Add to Claims</button>
      <div class="cv5-sel-divider" aria-hidden="true"></div>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-edit" data-cv5-sel-action="__custom__" title="Custom instruction">✎</button>
    `;
  }

  // Delegate click on toolbar buttons.
  selToolbar.addEventListener("click", (e) => {
    if (customAskActive) return;
    const btn = e.target.closest("[data-cv5-sel-action]");
    if (!btn) return;
    const action = btn.dataset.cv5SelAction;
    if (!action) return;
    if (action === "__custom__") {
      showCustomAskInput();
      return;
    }
    if (action === "__add_claim__") {
      void handleAddToClaims();
      return;
    }
    void triggerSpanRewrite(action);
  });

  // ── Stage 4: "Add to Claims Register" from the toolbar ────────────────────
  async function handleAddToClaims() {
    const text = selectionText.trim();
    if (!text) return;
    hideSelToolbar();
    const uniqueCode = "user-" + Date.now();
    try {
      const created = await createClaimApi({
        claimCode: uniqueCode,
        category: "user-approved",
        approvedPhrasing: text,
        notes: "Added from composer selection toolbar",
      });
      // If created as proposed, approve immediately.
      if (created.status !== "approved") {
        await approveClaimApi(created.id);
      }
      callbacks.onStatus?.("Added to Claims Register as approved.");
      // Re-scan so this claim now suppresses any similar flags.
      setTimeout(() => void runClaimScan(), 300);
    } catch (err) {
      console.error("[composer-v5] add-to-claims failed:", err);
      callbacks.onError?.(err.message || "Failed to add claim.");
    }
  }

  // Hook into the ProseMirror view to detect selection changes.
  // We wrap dispatchTransaction to call updateSelectionState after each transaction.
  function updateSelectionState() {
    if (destroyed) return;
    const { selection } = view.state;
    const isEmpty = selection.empty;
    if (isEmpty) {
      selectionRange = null;
      selectionText = "";
      if (!pendingRewrite) hideSelToolbar(); // keep toolbar visible during accept/reject
      return;
    }
    const { from, to } = selection;
    selectionRange = { from, to };
    selectionText = view.state.doc.textBetween(from, to, " ");
    if (!pendingRewrite) showSelToolbar(); // don't move toolbar while popover is open
  }

  // Listen on selectionchange (fires even when PM handles it internally).
  const handleDocSelectionChange = () => {
    // Defer to next microtask so PM has updated its state.
    Promise.resolve().then(() => updateSelectionState());
  };
  document.addEventListener("selectionchange", handleDocSelectionChange);

  // Hide toolbar when clicking outside the toolbar, popover, and editor.
  const handleDocClick = (e) => {
    if (
      selToolbar.contains(e.target) ||
      rewritePopover.contains(e.target) ||
      editorHost.contains(e.target)
    ) {
      return;
    }
    hideSelToolbar();
    if (!pendingRewrite) hideRewritePopover();
  };
  document.addEventListener("click", handleDocClick, true);

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

  // ── Stage-3 drafts picker (Finder-style tree + single "+" create menu) ────
  //
  // Header button toggles the popover. The popover is a folder TREE: each
  // top-level folder row is a button that expands/collapses to reveal the
  // drafts filed under it. An "All drafts" group shows every non-archived
  // draft; an "Ungrouped" group shows drafts with no folder_id. The "+" in
  // the popover header opens a small create menu (New draft / New from
  // template / New folder).
  //
  // Outside-click and Escape close the popover. Selecting a draft routes
  // through onSelectDraft (host re-loads).

  function rerenderPickerOnly() {
    const newBody = renderPickerBody({
      allDrafts: currentDrafts,
      allFolders: currentFolders,
      activeId: draft.id,
      expandedFolders: pickerExpandedFolders,
    });
    if (pickerEl) {
      // Preserve the header (with "+") + replace only the body.
      const headEl = pickerEl.querySelector('[data-cv5="picker-head"]');
      const bodyEl = pickerEl.querySelector('[data-cv5="picker-body"]');
      if (bodyEl) bodyEl.outerHTML = newBody;
      if (headEl) headEl.querySelector('[data-cv5="picker-plus"]')?.setAttribute("aria-expanded", "false");
    }
  }

  function openPicker() {
    if (!draftsWrapEl) return;
    draftsWrapEl.classList.add("is-open");
    draftsBtnEl?.setAttribute("aria-expanded", "true");
  }
  function closePicker() {
    if (!draftsWrapEl) return;
    draftsWrapEl.classList.remove("is-open");
    draftsBtnEl?.setAttribute("aria-expanded", "false");
    // Also close the +-menu when the picker closes.
    pickerEl?.querySelector('[data-cv5="picker-create-menu"]')?.classList.remove("is-open");
    pickerEl?.querySelector('[data-cv5="picker-templates-menu"]')?.classList.remove("is-open");
  }

  if (draftsBtnEl && draftsWrapEl) {
    draftsBtnEl.addEventListener("click", (e) => {
      e.stopPropagation();
      const wasOpen = draftsWrapEl.classList.contains("is-open");
      if (wasOpen) closePicker();
      else openPicker();
    });
  }

  // Outside-click handler closes the picker (and the "+" menu inside it).
  function handlePickerOutsideClick(e) {
    if (!draftsWrapEl) return;
    if (!draftsWrapEl.contains(e.target)) closePicker();
  }
  document.addEventListener("click", handlePickerOutsideClick);

  // Escape key closes the picker.
  function handlePickerEscape(e) {
    if (e.key !== "Escape") return;
    if (draftsWrapEl?.classList.contains("is-open")) {
      closePicker();
    }
  }
  document.addEventListener("keydown", handlePickerEscape);

  if (pickerEl) {
    pickerEl.addEventListener("click", async (e) => {
      // ── "+" toggle (root of the create menu) ────────────────────────────
      const plusBtn = e.target.closest('[data-cv5="picker-plus"]');
      if (plusBtn) {
        e.stopPropagation();
        const menu = pickerEl.querySelector('[data-cv5="picker-create-menu"]');
        const tplMenu = pickerEl.querySelector('[data-cv5="picker-templates-menu"]');
        tplMenu?.classList.remove("is-open");
        menu?.classList.toggle("is-open");
        return;
      }

      // ── Create-menu items ───────────────────────────────────────────────
      const action = e.target.closest("[data-cv5-create]");
      if (action) {
        e.stopPropagation();
        const kind = action.dataset.cv5Create;
        const menu = pickerEl.querySelector('[data-cv5="picker-create-menu"]');
        const tplMenu = pickerEl.querySelector('[data-cv5="picker-templates-menu"]');
        if (kind === "draft") {
          menu?.classList.remove("is-open");
          await handleCreateBlankDraft();
        } else if (kind === "template") {
          // Open the templates submenu (load if not yet loaded).
          menu?.classList.remove("is-open");
          await openTemplatesSubmenu(tplMenu);
        } else if (kind === "folder") {
          menu?.classList.remove("is-open");
          await handleCreateFolder();
        }
        return;
      }

      // ── Pick a template from the submenu ────────────────────────────────
      const tplRow = e.target.closest("[data-cv5-template-id]");
      if (tplRow) {
        e.stopPropagation();
        const templateId = Number(tplRow.dataset.cv5TemplateId);
        if (templateId) await handleApplyTemplate(templateId);
        return;
      }

      // ── Folder expand/collapse ─────────────────────────────────────────
      const folderRow = e.target.closest("[data-cv5-folder-key]");
      if (folderRow) {
        e.stopPropagation();
        const key = folderRow.dataset.cv5FolderKey;
        if (pickerExpandedFolders.has(key)) pickerExpandedFolders.delete(key);
        else pickerExpandedFolders.add(key);
        rerenderPickerOnly();
        return;
      }

      // ── Select a draft (file row) ──────────────────────────────────────
      const draftRow = e.target.closest("[data-cv5-draft-id]");
      if (draftRow) {
        e.stopPropagation();
        const id = Number(draftRow.dataset.cv5DraftId);
        if (!id) return;
        closePicker();
        callbacks.onSelectDraft?.(id);
      }
    });
  }

  // ── Picker action handlers ────────────────────────────────────────────────

  async function handleCreateBlankDraft() {
    // Reuse the existing POST /drafts path. That endpoint requires a
    // candidate_id; we use the current draft's candidate_id so the new draft
    // hangs off the same campaign context (status "generating" — same as the
    // existing manual-create flow).
    try {
      const candidateId = draft.candidate_id;
      if (!candidateId) {
        callbacks.onError?.("No campaign context — open a draft first.");
        return;
      }
      const created = await createWritingDraftApi({ candidate_id: candidateId });
      callbacks.onStatus?.("New draft created.");
      closePicker();
      callbacks.onSelectDraft?.(created.id);
    } catch (err) {
      console.error("[composer-v5] create blank draft failed:", err);
      callbacks.onError?.(err.message || "Failed to create draft.");
    }
  }

  let templatesCache = null;
  async function openTemplatesSubmenu(tplMenu) {
    if (!tplMenu) return;
    if (!templatesCache) {
      try {
        templatesCache = await listWritingTemplatesApi("active");
      } catch (err) {
        console.error("[composer-v5] list templates failed:", err);
        callbacks.onError?.(err.message || "Failed to load templates.");
        templatesCache = [];
      }
    }
    tplMenu.innerHTML = renderTemplatesSubmenu(templatesCache);
    tplMenu.classList.add("is-open");
  }

  async function handleApplyTemplate(templateId) {
    try {
      // Apply on the current folder (if any) so the new draft is filed
      // alongside its siblings; otherwise the backend places it under the
      // template-workspace candidate (lands in All drafts).
      const folderId = draft.folder_id ?? null;
      const payload = folderId != null ? { folderId } : {};
      const applied = await applyWritingTemplateApi(templateId, payload);
      callbacks.onStatus?.("Draft created from template.");
      closePicker();
      callbacks.onSelectDraft?.(applied.id);
    } catch (err) {
      console.error("[composer-v5] apply template failed:", err);
      callbacks.onError?.(err.message || "Failed to apply template.");
    }
  }

  async function handleCreateFolder() {
    const name = window.prompt("Folder name:");
    if (!name || !name.trim()) return;
    try {
      const folder = await createWritingFolderApi({ name: name.trim() });
      // Refresh the local tree state with the new folder so it appears
      // immediately without a host re-mount.
      try {
        const overview = await fetchWritingStudioOverview();
        if (Array.isArray(overview?.folders)) currentFolders = overview.folders;
        if (Array.isArray(overview?.drafts)) currentDrafts = overview.drafts;
      } catch (_) {
        // Best-effort refresh — fall back to optimistic insert.
        currentFolders = [...currentFolders, folder];
      }
      // Auto-expand the new folder so the user sees it.
      pickerExpandedFolders.add(String(folder.id));
      rerenderPickerOnly();
      callbacks.onStatus?.(`Folder "${folder.name}" created.`);
    } catch (err) {
      console.error("[composer-v5] create folder failed:", err);
      callbacks.onError?.(err.message || "Failed to create folder.");
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
      // Stage-2 cleanup: remove floating toolbar + popover and event listeners.
      document.removeEventListener("selectionchange", handleDocSelectionChange);
      document.removeEventListener("click", handleDocClick, true);
      try { document.body.removeChild(selToolbar); } catch (_) { /* noop */ }
      try { document.body.removeChild(rewritePopover); } catch (_) { /* noop */ }
      // Stage-4 cleanup: scan timer + claim popover.
      if (scanTimer) { clearTimeout(scanTimer); scanTimer = null; }
      try { document.body.removeChild(claimPopover); } catch (_) { /* noop */ }
      // Stage-3 cleanup: drafts-picker listeners.
      document.removeEventListener("click", handlePickerOutsideClick);
      document.removeEventListener("keydown", handlePickerEscape);
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

function renderShell({ draft, allDrafts, allFolders, expandedFolders }) {
  const titleText = draft.title || `Draft ${draft.id}`;
  const status = (draft.status || "draft").replace(/_/g, " ");
  const versionsCount = Array.isArray(draft.versions) ? draft.versions.length : 0;
  const varsLabel = versionsCount > 0 ? `${versionsCount} version${versionsCount === 1 ? "" : "s"}` : "0 versions";
  return `
    <div class="cv5-root">
      <header class="cv5-hdr">
        <div class="cv5-hdr-drafts-wrap" data-cv5="drafts-wrap">
          <button type="button" class="cv5-hdr-drafts-btn" data-cv5="drafts-btn" aria-haspopup="menu" aria-expanded="false" title="Open drafts">
            <span aria-hidden="true">📁</span>
            <span style="color: var(--cv5-ink3);">▾</span>
          </button>
          <div class="cv5-picker" data-cv5="drafts-picker" role="menu" aria-label="Drafts">
            <div class="cv5-picker-head" data-cv5="picker-head">
              <span class="cv5-picker-head-lbl">Drafts</span>
              <button type="button" class="cv5-picker-plus" data-cv5="picker-plus" title="New" aria-label="New draft, folder, or from template" aria-haspopup="menu" aria-expanded="false">+</button>
              <div class="cv5-picker-menu" data-cv5="picker-create-menu" role="menu" aria-label="Create">
                <button type="button" class="cv5-picker-menu-row" data-cv5-create="draft" role="menuitem">
                  <span aria-hidden="true">📄</span>
                  <span>New draft</span>
                </button>
                <button type="button" class="cv5-picker-menu-row" data-cv5-create="template" role="menuitem">
                  <span aria-hidden="true">📋</span>
                  <span>New from template…</span>
                </button>
                <button type="button" class="cv5-picker-menu-row" data-cv5-create="folder" role="menuitem">
                  <span aria-hidden="true">📁</span>
                  <span>New folder</span>
                </button>
              </div>
              <div class="cv5-picker-menu cv5-picker-menu--templates" data-cv5="picker-templates-menu" role="menu" aria-label="Templates"></div>
            </div>
            ${renderPickerBody({ allDrafts, allFolders, activeId: draft.id, expandedFolders })}
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

function renderPickerBody({ allDrafts, allFolders, activeId, expandedFolders }) {
  const drafts = Array.isArray(allDrafts) ? allDrafts : [];
  const folders = Array.isArray(allFolders) ? allFolders : [];

  if (drafts.length === 0 && folders.length === 0) {
    return `<div class="cv5-picker-body" data-cv5="picker-body"><div class="cv5-picker-empty">No drafts yet.</div></div>`;
  }

  // Build folder index + draft groupings.
  const folderById = new Map(folders.map((f) => [f.id, f]));
  const draftsByFolderId = new Map();
  const ungrouped = [];
  for (const d of drafts) {
    if (d.folder_id != null && folderById.has(d.folder_id)) {
      if (!draftsByFolderId.has(d.folder_id)) draftsByFolderId.set(d.folder_id, []);
      draftsByFolderId.get(d.folder_id).push(d);
    } else {
      ungrouped.push(d);
    }
  }

  // "All drafts" — always-expanded flat list at the top. Mirrors the mock's
  // ▾ All drafts row; we use the same .cv5-picker-folder visual treatment but
  // disable the collapse toggle so it remains a stable "everything" view.
  const allRows = drafts
    .slice()
    .sort(_byUpdatedAtDesc)
    .map((d) => renderTreeFileRow(d, activeId, /*indent=*/1))
    .join("");

  // Per-folder sections — sorted by name.
  const sortedFolders = folders
    .slice()
    .sort((a, b) => (a.name || "").localeCompare(b.name || ""));

  const folderSections = sortedFolders
    .map((f) => {
      const key = String(f.id);
      const expanded = expandedFolders.has(key);
      const inside = (draftsByFolderId.get(f.id) || []).slice().sort(_byUpdatedAtDesc);
      const insideRows = expanded
        ? inside.map((d) => renderTreeFileRow(d, activeId, /*indent=*/1)).join("")
        : "";
      const count = inside.length;
      const caret = expanded ? "▾" : "▸";
      return `
        <button type="button" class="cv5-picker-row cv5-picker-folder" data-cv5-folder-key="${esc(key)}" aria-expanded="${expanded}">
          <span class="cv5-picker-caret" aria-hidden="true">${caret}</span>
          <span aria-hidden="true">📁</span>
          <span class="cv5-picker-folder-name">${esc(f.name || "Untitled folder")}</span>
          <span class="cv5-picker-row-meta">${count > 0 ? count : ""}</span>
        </button>
        ${insideRows}
      `;
    })
    .join("");

  // "Ungrouped" — drafts without a folder.  Hidden when empty.
  const ungroupedKey = "ungrouped";
  let ungroupedSection = "";
  if (ungrouped.length > 0) {
    const expanded = expandedFolders.has(ungroupedKey);
    const caret = expanded ? "▾" : "▸";
    const insideRows = expanded
      ? ungrouped.slice().sort(_byUpdatedAtDesc).map((d) => renderTreeFileRow(d, activeId, /*indent=*/1)).join("")
      : "";
    ungroupedSection = `
      <button type="button" class="cv5-picker-row cv5-picker-folder" data-cv5-folder-key="${ungroupedKey}" aria-expanded="${expanded}">
        <span class="cv5-picker-caret" aria-hidden="true">${caret}</span>
        <span aria-hidden="true">📁</span>
        <span class="cv5-picker-folder-name">Ungrouped</span>
        <span class="cv5-picker-row-meta">${ungrouped.length}</span>
      </button>
      ${insideRows}
    `;
  }

  // "All drafts" — collapsible like other folders. Default expanded only if
  // the user previously expanded it (we don't auto-pre-expand to keep the
  // popover compact when there are many drafts).
  const allKey = "all";
  const allExpanded = expandedFolders.has(allKey);
  const allCaret = allExpanded ? "▾" : "▸";
  const allSection = `
    <button type="button" class="cv5-picker-row cv5-picker-folder cv5-picker-folder--all" data-cv5-folder-key="${allKey}" aria-expanded="${allExpanded}">
      <span class="cv5-picker-caret" aria-hidden="true">${allCaret}</span>
      <span aria-hidden="true">📁</span>
      <span class="cv5-picker-folder-name">All drafts</span>
      <span class="cv5-picker-row-meta">${drafts.length}</span>
    </button>
    ${allExpanded ? allRows : ""}
  `;

  return `
    <div class="cv5-picker-body" data-cv5="picker-body">
      ${allSection}
      ${folderSections}
      ${ungroupedSection}
    </div>
  `;
}

function renderTreeFileRow(d, activeId, indent) {
  const isActive = d.id === activeId;
  const cls = isActive ? "cv5-picker-row cv5-picker-file is-active" : "cv5-picker-row cv5-picker-file";
  const title = d.title || `Draft ${d.id}`;
  const meta = [d.asset_type, d.status].filter(Boolean).join(" · ");
  return `
    <button type="button" class="${cls}" data-cv5-draft-id="${d.id}" data-cv5-indent="${indent}">
      <span class="cv5-picker-row-icon" aria-hidden="true">📄</span>
      <span class="cv5-picker-row-title">${esc(title)}</span>
      ${meta ? `<span class="cv5-picker-row-meta">${esc(meta)}</span>` : ""}
    </button>
  `;
}

function _byUpdatedAtDesc(a, b) {
  const at = a.updated_at || "";
  const bt = b.updated_at || "";
  if (at && bt) return bt.localeCompare(at);
  if (at) return -1;
  if (bt) return 1;
  return (b.id || 0) - (a.id || 0);
}

function renderTemplatesSubmenu(templates) {
  if (!Array.isArray(templates) || templates.length === 0) {
    return `<div class="cv5-picker-menu-empty">No active templates.</div>`;
  }
  return templates
    .map((t) => {
      const meta = t.asset_type || t.assetType || "";
      return `
        <button type="button" class="cv5-picker-menu-row" data-cv5-template-id="${t.id}" role="menuitem">
          <span aria-hidden="true">📋</span>
          <span class="cv5-picker-row-title">${esc(t.name || `Template ${t.id}`)}</span>
          ${meta ? `<span class="cv5-picker-row-meta">${esc(meta)}</span>` : ""}
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
