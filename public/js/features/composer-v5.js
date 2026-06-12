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
  createDraftCommentApi,
  createWritingDraftApi,
  createWritingFolderApi,
  createWritingTemplateApi,
  deleteWritingFolderApi,
  dismissClaimApi,
  exportWritingDraftToGoogleDocApi,
  fetchAccountInfo,
  fetchTeammatesApi,
  fetchWritingDraft,
  fetchWritingStudioOverview,
  googleDisconnectApi,
  googleStatusApi,
  importWritingDraftFromGoogleDocApi,
  listDraftCommentsApi,
  listWritingTemplatesApi,
  reopenCommentApi,
  resolveCommentApi,
  rewriteSpanApi,
  scanDraftClaimsApi,
  updateWritingDraftApi,
  decideApprovalApi,
  listApprovalsApi,
  attachDeliverableApi,
  fetchMarketingCampaignsApi,
  listWritingTrainingCandidatesApi,
  createWritingTrainingCandidateApi,
  decideWritingTrainingCandidateApi,
  listWritingRulesApi,
  listWritingExamplesApi,
  listWritingSourcesApi,
  updateWritingRuleApi,
  updateWritingExampleApi,
  updateWritingSourceApi,
  updateWritingFolderApi,
  deleteWritingRuleApi,
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
  const draftTitleEl = rootEl.querySelector('[data-cv5="draft-title"]');

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
              "data-claim-from": String(f.pmFrom),
              "data-claim-to": String(f.pmTo),
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

  // ── Stage 6: Comments highlight plugin ────────────────────────────────────
  //
  // Stores comment-anchor highlight decorations (amber/yellow inline spans).
  // Updated via setMeta payload: { anchors: [{pmFrom, pmTo, commentId}] }.
  // Sibling to claimFlagsPlugin — same pattern, different visual.

  const commentsKey = new PluginKey("comments");

  const commentsPlugin = new Plugin({
    key: commentsKey,
    state: {
      init(_config, _editorState) {
        return { decoSet: DecorationSet.empty };
      },
      apply(tr, pluginState, _oldState, newState) {
        const meta = tr.getMeta(commentsKey);
        if (meta && meta.anchors !== undefined) {
          const decos = meta.anchors.map((a) =>
            Decoration.inline(a.pmFrom, a.pmTo, {
              class: "cv5-comment-anchor-hl",
              "data-comment-id": String(a.commentId),
            })
          );
          return { decoSet: DecorationSet.create(newState.doc, decos) };
        }
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
    plugins: [...exampleSetup({ schema: composerSchema, menuBar: false }), claimFlagsPlugin, commentsPlugin],
  });
  const view = new EditorView(editorHost, {
    state,
    dispatchTransaction(tr) {
      const next = view.state.apply(tr);
      view.updateState(next);
      if (tr.docChanged) {
        scheduleAutosave();
        scheduleScan();
        // Stage 5: recompute page breaks after any document change.
        schedulePageBreakUpdate();
        // Stage 6: reflow comment card positions after doc edits.
        scheduleCommentsReflow();
      }
      // Stage 2: the selection toolbar follows the editor's OWN selection state,
      // so it tracks drags/clicks in the editor and ignores everything else.
      if (typeof updateSelectionState === "function") updateSelectionState();
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
    // Read the PM range that was stamped on the decoration element.
    const flagPmFrom = flagEl.dataset.claimFrom ? Number(flagEl.dataset.claimFrom) : null;
    const flagPmTo   = flagEl.dataset.claimTo   ? Number(flagEl.dataset.claimTo)   : null;
    let nearest = [];
    try { nearest = JSON.parse(flagEl.dataset.claimNearest || "[]"); } catch (_) { /* noop */ }

    const reasonLabel = {
      quantified:   "Quantified claim",
      superlative:  "Superlative / exclusivity claim",
      comparative:  "Comparative claim",
    }[reason] || "Strong claim";

    const nearestHtml = nearest.length
      ? `<div class="cv5-claim-pop-nearest">
           <div class="cv5-claim-pop-nearest-label">Nearest approved (click to replace):</div>
           ${nearest.map((n) => `
             <button type="button" class="cv5-claim-pop-nearest-item cv5-claim-pop-nearest-item--btn"
               data-phrasing="${esc(n.phrasing)}"
               title="Click to replace flagged text with this phrasing">
               <span class="cv5-claim-pop-sim">${Math.round(n.similarity * 100)}%</span>
               <span>${esc(n.phrasing)}</span>
             </button>`).join("")}
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
        <button type="button" class="cv5-claim-btn cv5-claim-btn-disregard" data-claim-text="${esc(claimText)}" title="Not a market claim — dismiss and never re-flag">Disregard</button>
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

    // ("Find source" removed — was a no-op. The claim `source` label remains in
    // the data model so the action can be revived later.)

    // Disregard: dismiss the flag for this draft — it will not re-appear on re-scan.
    claimPopover.querySelector(".cv5-claim-btn-disregard").addEventListener("click", async (e) => {
      e.stopPropagation();
      const text = e.currentTarget.dataset.claimText || claimText;
      await handleDisregard(text);
    });

    // Click a "Nearest approved" item → replace the flagged span with that phrasing.
    claimPopover.querySelectorAll(".cv5-claim-pop-nearest-item--btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const phrasing = btn.dataset.phrasing || "";
        if (!phrasing) return;
        handleClaimReplace(phrasing, flagPmFrom, flagPmTo);
      });
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

  async function handleDisregard(spanText) {
    // Dismiss the flag permanently for this draft: POST claim-dismiss, then
    // re-scan so the decoration is removed immediately.
    try {
      await dismissClaimApi(currentDraftId, spanText);
      closeClaimPopover();
      // Re-scan so the dismissed span no longer appears as a decoration.
      setTimeout(() => void runClaimScan(), 300);
      callbacks.onStatus?.("Flag dismissed — won't re-appear on re-scan.");
    } catch (err) {
      console.error("[composer-v5] disregard claim failed:", err);
      callbacks.onError?.(err.message || "Failed to disregard claim.");
    }
  }

  function handleClaimReplace(approvedPhrasing, pmFrom, pmTo) {
    // Replace the flagged claim's text span with the approved phrasing.
    // Single-span replace: tr.replaceWith(from, to, schema.text(...)) — same
    // pattern as the rewrite-span Accept handler (lossless, undoable).
    // Falls back to a best-effort text search if PM positions are stale.
    let from = pmFrom != null ? pmFrom : null;
    let to   = pmTo   != null ? pmTo   : null;

    // If positions are missing or stale (0/0), skip — we can't replace blindly.
    if (from === null || to === null || from >= to) {
      callbacks.onError?.("Could not locate the claim span to replace. Re-scan and try again.");
      return;
    }

    // Clamp to doc bounds in case the doc was edited since the decoration was set.
    const docSize = view.state.doc.content.size;
    from = Math.max(0, Math.min(from, docSize));
    to   = Math.max(from, Math.min(to, docSize));
    if (from >= to) {
      callbacks.onError?.("Claim span position is out of bounds — please re-scan.");
      return;
    }

    // Build the replacement as a plain text node (same as rewrite-span Accept).
    const tr = view.state.tr;
    tr.replaceWith(from, to, composerSchema.text(approvedPhrasing));
    view.dispatch(tr);

    closeClaimPopover();
    // Autosave (lossless — normal edit, fully undoable).
    scheduleAutosave();
    // Re-scan: the text now matches an approved claim, so the flag should clear.
    setTimeout(() => void runClaimScan(), 300);
    callbacks.onStatus?.("Claim replaced with approved phrasing. Autosaving…");
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
    <div class="cv5-sel-intent-row">
      <input
        type="text"
        class="cv5-sel-intent-input"
        placeholder="What should change?"
        aria-label="What should change? (custom rewrite instruction)"
        data-cv5-sel-intent
      />
      <button type="button" class="cv5-sel-btn cv5-sel-btn-go-intent" data-cv5-sel-action="__intent_go__" title="Rewrite with this instruction">↑</button>
    </div>
    <div class="cv5-sel-presets-row">
      <button type="button" class="cv5-sel-btn cv5-sel-btn-preset" data-cv5-sel-action="Rewrite">Rewrite</button>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-preset" data-cv5-sel-action="Shorten">Shorten</button>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-preset" data-cv5-sel-action="Lengthen">Lengthen</button>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-preset" data-cv5-sel-action="Make more formal">Tone</button>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-brand cv5-sel-btn-preset" data-cv5-sel-action="Make on-brand">On-brand</button>
      <div class="cv5-sel-divider" aria-hidden="true"></div>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-claim" data-cv5-sel-action="__add_claim__" title="Add selection to Claims Register as approved">＋ Claim</button>
      <button type="button" class="cv5-sel-btn cv5-sel-btn-comment" data-cv5-sel-action="__comment__" title="Add a comment to the selected text">💬</button>
    </div>
  `;
  document.body.appendChild(selToolbar);

  // Wire the intent input: Enter key submits; the Go button also submits.
  selToolbar.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches(".cv5-sel-intent-input")) {
      e.preventDefault();
      const val = e.target.value.trim();
      if (val) void triggerSpanRewrite(val);
      e.target.value = "";
    }
  });

  // Keep the editor's selection alive when the user mousedowns on a toolbar
  // BUTTON. Without this, the mousedown blurs the contenteditable, the DOM
  // selection collapses, selectionchange fires, and updateSelectionState hides
  // the toolbar — so the click never lands ("clicking it closes it"). The text
  // input is exempt because it legitimately needs focus.
  selToolbar.addEventListener("mousedown", (e) => {
    if (e.target.closest(".cv5-sel-intent-input")) return;
    e.preventDefault();
  });

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
    // Position from the EDITOR's authoritative selection coords (view.coordsAtPos),
    // never the DOM selection — so the toolbar always sits over the editor's
    // selection and can't be dragged to the chat box's cursor.
    if (!selectionRange) return;
    let startC;
    let endC;
    try {
      startC = view.coordsAtPos(selectionRange.from);
      endC = view.coordsAtPos(selectionRange.to);
    } catch (_) {
      return; // doc changed underneath — skip; next update repositions
    }
    const elW = el.offsetWidth || 280;
    const gap = 8;
    // Anchor above the START of the selection, clamped on-screen.
    let left = startC.left - elW / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - elW - 8));
    let top = startC.top - el.offsetHeight - gap;
    if (top < 8) top = endC.bottom + gap; // flip below if no room above
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

  // Custom-ask: no longer a separate mode — the intent input is always visible.
  // Keep this no-op for any remaining callers during the transition.
  let customAskActive = false;
  function showCustomAskInput() {
    // Intent input is always in the toolbar; just focus it.
    const input = selToolbar.querySelector(".cv5-sel-intent-input");
    input?.focus();
  }

  function resetToolbarButtons() {
    customAskActive = false;
    // Clear the intent input so it's fresh for the next selection.
    const input = selToolbar.querySelector(".cv5-sel-intent-input");
    if (input) input.value = "";
  }

  // Delegate click on toolbar buttons.
  selToolbar.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-cv5-sel-action]");
    if (!btn) return;
    const action = btn.dataset.cv5SelAction;
    if (!action) return;
    if (action === "__intent_go__") {
      // Submit the current value of the intent input.
      const input = selToolbar.querySelector(".cv5-sel-intent-input");
      const val = input?.value?.trim();
      if (val) {
        void triggerSpanRewrite(val);
        if (input) input.value = "";
      }
      return;
    }
    if (action === "__custom__") {
      // Legacy: focus the intent input.
      showCustomAskInput();
      return;
    }
    if (action === "__add_claim__") {
      void handleAddToClaims();
      return;
    }
    if (action === "__comment__") {
      hideSelToolbar();
      openCommentComposer();
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

  // ── Selection-driven toolbar visibility ─────────────────────────────────────
  // Driven by ProseMirror's OWN update cycle (dispatchTransaction calls this
  // after every editor transaction), so it reacts ONLY to the editor — never to
  // the chat box or other page activity. Shows when the editor has a non-empty
  // selection and focus; hides on collapse/blur. (Dismissal on an outside press
  // is handled separately by handleOutsidePointerDown.)
  function updateSelectionState() {
    if (destroyed) return;
    // Using the toolbar's own input — the editor blurs and collapses; don't react.
    if (selToolbar.contains(document.activeElement)) return;
    // Don't touch the toolbar during a rewrite accept/reject.
    if (pendingRewrite) return;
    // Editor not focused (clicked into the chat / nav) → no toolbar.
    if (!view.hasFocus()) { selectionRange = null; selectionText = ""; hideSelToolbar(); return; }

    const { selection } = view.state;
    if (selection.empty) { selectionRange = null; selectionText = ""; hideSelToolbar(); return; }
    selectionRange = { from: selection.from, to: selection.to };
    selectionText = view.state.doc.textBetween(selection.from, selection.to, " ");
    showSelToolbar();
  }

  // Hide on any press OUTSIDE the editor + toolbar (chat box, nav, paper margin).
  // HIDE-ONLY — never shows — so page-wide activity (e.g. the chat box) can't
  // flash the toolbar in or reposition it. Showing is driven solely by the
  // editor's own transactions (updateSelectionState via dispatchTransaction).
  const handleOutsidePointerDown = (e) => {
    if (selToolbar.contains(e.target) || rewritePopover.contains(e.target) || editorHost.contains(e.target)) return;
    hideSelToolbar();
    if (!pendingRewrite) hideRewritePopover();
  };
  document.addEventListener("mousedown", handleOutsidePointerDown, true);
  document.addEventListener("click", handleOutsidePointerDown, true);

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

      // ── Rename-folder button ────────────────────────────────────────────
      const renameBtn = e.target.closest("[data-cv5-rename-folder]");
      if (renameBtn) {
        e.stopPropagation();
        const folderId = Number(renameBtn.dataset.cv5RenameFolder);
        const currentName = renameBtn.dataset.cv5FolderName || "";
        if (!folderId) return;
        await handleRenameFolder(folderId, currentName, renameBtn);
        return;
      }

      // ── Delete-folder button ────────────────────────────────────────────
      const delBtn = e.target.closest("[data-cv5-delete-folder]");
      if (delBtn) {
        e.stopPropagation();
        const folderId = Number(delBtn.dataset.cv5DeleteFolder);
        if (folderId) await handleDeleteFolder(folderId);
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
    // POST /drafts with no candidate_id — the backend attaches the templates
    // placeholder candidate so the deliverable row satisfies the FK constraint.
    // This path works regardless of whether the current draft has a campaign
    // context, fixing the silent no-op on no-candidate drafts.
    try {
      const created = await createWritingDraftApi({});
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

  async function handleDeleteFolder(folderId) {
    // Only real (numeric) folder IDs are deletable — synthetic groups
    // ("all", "ungrouped") are never passed here.
    const folder = currentFolders.find((f) => f.id === folderId);
    const name = folder?.name || `Folder ${folderId}`;
    const confirmed = window.confirm(
      `Delete folder "${name}"?\n\nDrafts inside will remain available in All drafts.`
    );
    if (!confirmed) return;
    try {
      await deleteWritingFolderApi(folderId);
      // Remove from local state so the folder disappears immediately.
      pickerExpandedFolders.delete(String(folderId));
      try {
        const overview = await fetchWritingStudioOverview();
        if (Array.isArray(overview?.folders)) currentFolders = overview.folders;
        if (Array.isArray(overview?.drafts)) currentDrafts = overview.drafts;
      } catch (_) {
        // Best-effort refresh — optimistic removal on failure.
        currentFolders = currentFolders.filter((f) => f.id !== folderId);
        // Clear folder_id from any drafts that were in this folder so they
        // fall back to Ungrouped immediately.
        currentDrafts = currentDrafts.map((d) =>
          d.folder_id === folderId ? { ...d, folder_id: null } : d
        );
      }
      rerenderPickerOnly();
      callbacks.onStatus?.(`Folder "${name}" deleted.`);
    } catch (err) {
      console.error("[composer-v5] delete folder failed:", err);
      callbacks.onError?.(err.message || "Failed to delete folder.");
    }
  }

  async function handleRenameFolder(folderId, currentName, triggerEl) {
    // Inline rename: replace the folder-name span in the picker row with an
    // <input> that commits on Enter/blur and cancels on Escape.
    const folderWrap = triggerEl?.closest(".cv5-picker-folder-wrap");
    const nameSpan = folderWrap?.querySelector(".cv5-picker-folder-name");
    if (!nameSpan) {
      // Fallback: prompt if the picker row can't be found in DOM.
      const newName = window.prompt("Rename folder:", currentName);
      if (!newName?.trim() || newName.trim() === currentName) return;
      await _commitFolderRename(folderId, currentName, newName.trim());
      return;
    }

    // Hide the span and insert an input in its place.
    nameSpan.style.display = "none";
    triggerEl.style.display = "none";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "cv5-picker-folder-rename-input";
    input.value = currentName;
    input.setAttribute("aria-label", "Rename folder");
    nameSpan.parentNode.insertBefore(input, nameSpan.nextSibling);
    input.focus();
    input.select();

    let committed = false;
    async function commit() {
      if (committed) return;
      committed = true;
      input.remove();
      nameSpan.style.display = "";
      triggerEl.style.display = "";
      const newName = input.value.trim();
      if (!newName || newName === currentName) return;
      await _commitFolderRename(folderId, currentName, newName);
    }
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); void commit(); }
      if (e.key === "Escape") {
        committed = true;
        input.remove();
        nameSpan.style.display = "";
        triggerEl.style.display = "";
      }
    });
    input.addEventListener("blur", () => void commit());
  }

  async function _commitFolderRename(folderId, oldName, newName) {
    try {
      await updateWritingFolderApi(folderId, { name: newName });
      // Update local folder cache.
      const local = currentFolders.find((f) => f.id === folderId);
      if (local) local.name = newName;
      rerenderPickerOnly();
      callbacks.onStatus?.(`Folder renamed to "${newName}".`);
    } catch (err) {
      console.error("[composer-v5] rename folder failed:", err);
      callbacks.onError?.(err.message || "Failed to rename folder.");
    }
  }

  if (historyBtnEl) {
    historyBtnEl.addEventListener("click", () => callbacks.onOpenVersionHistory?.());
  }

  // ── Draft title inline rename ──────────────────────────────────────────────
  //
  // Clicking the draft name in the top bar replaces it with an <input> for
  // inline editing. On Enter or blur, PUT /api/writing-studio/drafts/{id}
  // with { title } — persists through reload.

  function _startDraftRename() {
    if (!draftTitleEl || draftTitleEl.dataset.cv5Renaming === "1") return;
    draftTitleEl.dataset.cv5Renaming = "1";
    const currentTitle = draftTitleEl.textContent.trim();
    const input = document.createElement("input");
    input.type = "text";
    input.className = "cv5-hdr-title-input";
    input.value = currentTitle;
    input.setAttribute("aria-label", "Rename draft");
    draftTitleEl.style.display = "none";
    draftTitleEl.parentNode.insertBefore(input, draftTitleEl.nextSibling);
    input.focus();
    input.select();

    async function _commitRename() {
      const newTitle = input.value.trim();
      input.remove();
      draftTitleEl.style.display = "";
      delete draftTitleEl.dataset.cv5Renaming;
      if (!newTitle || newTitle === currentTitle) return;
      draftTitleEl.textContent = newTitle;
      draftTitleEl.title = newTitle;
      try {
        await updateWritingDraftApi(currentDraftId, { title: newTitle });
        // Update local draft cache so the picker reflects the new name.
        const local = currentDrafts.find((d) => d.id === currentDraftId);
        if (local) local.title = newTitle;
        rerenderPickerOnly();
      } catch (err) {
        console.error("[composer-v5] rename draft failed:", err);
        callbacks.onError?.(err.message || "Failed to rename draft.");
        // Revert display.
        draftTitleEl.textContent = currentTitle;
        draftTitleEl.title = currentTitle;
      }
    }

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); void _commitRename(); }
      if (e.key === "Escape") {
        input.remove();
        draftTitleEl.style.display = "";
        delete draftTitleEl.dataset.cv5Renaming;
      }
    });
    input.addEventListener("blur", () => void _commitRename());
  }

  if (draftTitleEl) {
    draftTitleEl.addEventListener("click", _startDraftRename);
    draftTitleEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); _startDraftRename(); }
    });
  }

  // ── Stage 5: format-aware pagination ──────────────────────────────────────
  //
  // Long-form asset types (guide, paper, whitepaper, etc.) get visual page
  // breaks as an overlay inside .cv5-paper. The doc model is NEVER touched —
  // no PM transaction, no autosave effect, purely presentation.
  //
  // Implementation: add a sibling <div class="cv5-page-breaks"> inside
  // .cv5-paper with pointer-events:none containing N horizontal rule lines
  // spaced PAGE_HEIGHT_PX apart. Recompute N on doc changes + resize.

  const LONG_FORM_ASSET_TYPES = new Set([
    "guide", "paper", "whitepaper", "white_paper", "white paper",
    "long_form", "long-form", "long form", "field_guide", "field guide",
  ]);
  const PAGE_HEIGHT_PX = 920; // ~letter-feel at 15px/1.8lh

  function isLongForm(assetType) {
    if (!assetType) return false;
    return LONG_FORM_ASSET_TYPES.has(assetType.toLowerCase().trim());
  }

  const paperEl = rootEl.querySelector('[data-cv5="paper"]');
  let pageBreaksEl = null;
  let paginationResizeObserver = null;
  let paginationRaf = null;

  function mountPagination() {
    if (!paperEl) return;
    if (!pageBreaksEl) {
      pageBreaksEl = document.createElement("div");
      pageBreaksEl.className = "cv5-page-breaks";
      pageBreaksEl.setAttribute("aria-hidden", "true");
      paperEl.appendChild(pageBreaksEl);
      paperEl.classList.add("cv5-paper--paginated");
    }
    updatePageBreaks();
    if (!paginationResizeObserver) {
      paginationResizeObserver = new ResizeObserver(() => schedulePageBreakUpdate());
      paginationResizeObserver.observe(paperEl);
    }
  }

  function unmountPagination() {
    if (paginationResizeObserver) {
      paginationResizeObserver.disconnect();
      paginationResizeObserver = null;
    }
    if (pageBreaksEl && paperEl) {
      try { paperEl.removeChild(pageBreaksEl); } catch (_) { /* noop */ }
      pageBreaksEl = null;
    }
    paperEl?.classList.remove("cv5-paper--paginated");
  }

  function schedulePageBreakUpdate() {
    if (paginationRaf) cancelAnimationFrame(paginationRaf);
    paginationRaf = requestAnimationFrame(() => {
      paginationRaf = null;
      updatePageBreaks();
    });
  }

  function updatePageBreaks() {
    if (!pageBreaksEl || !paperEl) return;
    // paperEl's scrollHeight includes padding, so measure the content height
    // as the total height of all block children of the editor host.
    const totalH = editorHost ? editorHost.scrollHeight : paperEl.scrollHeight;
    const n = Math.max(0, Math.floor(totalH / PAGE_HEIGHT_PX));
    // Build N break lines.
    let html = "";
    for (let i = 1; i <= n; i++) {
      html += `<div class="cv5-page-break-line" style="top:${i * PAGE_HEIGHT_PX}px"></div>`;
    }
    pageBreaksEl.innerHTML = html;
  }

  if (isLongForm(draft.asset_type)) {
    mountPagination();
  }

  // ── Stage 8: ⋯ Actions menu ───────────────────────────────────────────────
  //
  // The ⋯ button in the header opens a small popover. Items:
  //   - Save as template (real): POSTs to /api/writing-studio/templates
  //   - Repurpose: stub toast
  //   - Brand + readability check: stub toast
  //
  // Reuses the .cv5-picker-menu / .cv5-picker-menu-row CSS family so it
  // matches the "+" create menu in the drafts picker (brief requirement).

  const actionsWrapEl = rootEl.querySelector('[data-cv5="actions-wrap"]');
  const actionsBtnEl  = rootEl.querySelector('[data-cv5="actions-btn"]');
  const actionsMenuEl = rootEl.querySelector('[data-cv5="actions-menu"]');

  function openActionsMenu() {
    if (!actionsWrapEl) return;
    actionsMenuEl?.classList.add("is-open");
    actionsBtnEl?.setAttribute("aria-expanded", "true");
  }

  function closeActionsMenu() {
    if (!actionsWrapEl) return;
    actionsMenuEl?.classList.remove("is-open");
    actionsBtnEl?.setAttribute("aria-expanded", "false");
  }

  if (actionsBtnEl) {
    actionsBtnEl.addEventListener("click", (e) => {
      e.stopPropagation();
      const isOpen = actionsMenuEl?.classList.contains("is-open");
      if (isOpen) closeActionsMenu();
      else openActionsMenu();
    });
  }

  // Outside-click closes the actions menu independently of the picker.
  function handleActionsOutsideClick(e) {
    if (!actionsWrapEl) return;
    if (!actionsWrapEl.contains(e.target)) closeActionsMenu();
  }
  document.addEventListener("click", handleActionsOutsideClick);

  // ESC closes the actions menu.
  function handleActionsEscape(e) {
    if (e.key !== "Escape") return;
    if (actionsMenuEl?.classList.contains("is-open")) closeActionsMenu();
  }
  document.addEventListener("keydown", handleActionsEscape);

  // State for the campaign-action buttons — populated on draft open.
  let _pendingCampaignApproval = null; // Approval row if this draft has a pending content_draft approval
  let _draftCampaignDeliverableId = null; // campaign_deliverables.id for this draft (if linked)

  async function _refreshCampaignActionState() {
    // Fetch pending content_draft approvals to see if this draft is pending review.
    _pendingCampaignApproval = null;
    try {
      const approvals = await listApprovalsApi({ status: "pending", kind: "content_draft" });
      if (Array.isArray(approvals)) {
        // Match by checking if this deliverable's id appears in any approval's context.
        for (const approval of approvals) {
          const deliverableIds = approval?.pipe4Context?.context?.deliverable_ids || [];
          const deliverables = approval?.pipe4Context?.context?.deliverables || [];
          const allIds = [
            ...deliverableIds.map(String),
            ...deliverables.map((d) => String(d?.id)),
          ];
          if (draft.id && allIds.includes(String(draft.id))) {
            _pendingCampaignApproval = approval;
            break;
          }
        }
      }
    } catch {
      // non-fatal
    }

    // Show/hide the "Approve for campaign" button based on whether there's a pending approval.
    const approveBtn = actionsMenuEl?.querySelector('[data-cv5-action="approve-for-campaign"]');
    if (approveBtn) {
      if (_pendingCampaignApproval) {
        approveBtn.removeAttribute("hidden");
      } else {
        approveBtn.setAttribute("hidden", "");
      }
    }
  }

  // Refresh campaign action state on mount.
  _refreshCampaignActionState().catch(() => {});

  if (actionsMenuEl) {
    actionsMenuEl.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-cv5-action]");
      if (!btn) return;
      e.stopPropagation();
      const action = btn.dataset.cv5Action;
      closeActionsMenu();

      if (action === "save-as-template") {
        await handleSaveAsTemplate();
      } else if (action === "repurpose") {
        callbacks.onStatus?.("Repurpose — coming soon.");
      } else if (action === "brand-check") {
        callbacks.onStatus?.("Brand + readability check — coming soon.");
      } else if (action === "approve-for-campaign") {
        await handleApproveForCampaign();
      } else if (action === "attach-to-campaign") {
        await handleAttachToCampaign();
      }
    });
  }

  async function handleApproveForCampaign() {
    if (!_pendingCampaignApproval) {
      callbacks.onStatus?.("No pending review for this draft.");
      return;
    }
    try {
      await decideApprovalApi(_pendingCampaignApproval.id, { decision: "approved", reviewer: "operator" });
      _pendingCampaignApproval = null;
      // Hide the button after approving
      const approveBtn = actionsMenuEl?.querySelector('[data-cv5-action="approve-for-campaign"]');
      approveBtn?.setAttribute("hidden", "");
      callbacks.onStatus?.("Draft approved for campaign.");
    } catch (err) {
      console.error("[composer-v5] approve-for-campaign failed:", err);
      callbacks.onError?.(err.message || "Failed to approve draft.");
    }
  }

  async function handleAttachToCampaign() {
    // Fetch all campaigns so the operator can pick one.
    let campaigns = [];
    try {
      campaigns = await fetchMarketingCampaignsApi();
    } catch (err) {
      callbacks.onError?.("Failed to load campaigns.");
      return;
    }
    if (!Array.isArray(campaigns) || campaigns.length === 0) {
      callbacks.onStatus?.("No campaigns available to attach to.");
      return;
    }

    // Show a simple picker inline in the actions area.
    _showCampaignAttachPicker(campaigns);
  }

  function _showCampaignAttachPicker(campaigns) {
    // Remove existing picker if any.
    rootEl.querySelector(".cv5-campaign-attach-picker")?.remove();

    const picker = document.createElement("div");
    picker.className = "cv5-picker-menu cv5-campaign-attach-picker is-open";
    picker.style.cssText = "position:absolute;right:0;top:100%;z-index:200;min-width:260px;";
    picker.innerHTML = `
      <div style="padding:6px 10px;font-size:11px;color:var(--text-dim);border-bottom:1px solid var(--border)">
        Attach to campaign
      </div>
      <ul style="list-style:none;margin:0;padding:4px 0;max-height:240px;overflow-y:auto">
        ${campaigns.map((c) => `
          <li class="cv5-picker-menu-row" data-attach-campaign-id="${escapeHtml(String(c.id))}" style="cursor:pointer">
            <span>${escapeHtml(c.name || c.family || String(c.id))}</span>
          </li>
        `).join("")}
      </ul>
    `;

    // Attach relative to the actions-wrap
    const actionsWrap = rootEl.querySelector('[data-cv5="actions-wrap"]');
    if (!actionsWrap) return;
    actionsWrap.style.position = "relative";
    actionsWrap.appendChild(picker);

    picker.querySelectorAll("[data-attach-campaign-id]").forEach((li) => {
      li.addEventListener("click", async () => {
        const candidateId = Number(li.dataset.attachCampaignId);
        li.style.opacity = "0.5";
        picker.remove();
        try {
          await attachDeliverableApi(draft.id, candidateId);
          callbacks.onStatus?.("Draft attached to campaign.");
        } catch (err) {
          console.error("[composer-v5] attach-to-campaign failed:", err);
          callbacks.onError?.(err.message || "Failed to attach draft to campaign.");
        }
      });
    });

    function handleOutside(e) {
      if (!picker.contains(e.target)) {
        picker.remove();
        document.removeEventListener("click", handleOutside);
      }
    }
    setTimeout(() => document.addEventListener("click", handleOutside), 0);
  }

  async function handleSaveAsTemplate() {
    const defaultName = draft.title || "Untitled";
    const name = window.prompt("Template name:", defaultName);
    if (name === null) return; // user cancelled
    const trimmed = name.trim();
    if (!trimmed) {
      callbacks.onError?.("Template name cannot be empty.");
      return;
    }

    const templateKey = trimmed
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "");
    if (!templateKey) {
      callbacks.onError?.("Template name produced an empty key — please use letters or numbers.");
      return;
    }

    const body = serializeDocToText(view.state.doc);
    const assetType = draft.asset_type || undefined;

    const payload = { templateKey, name: trimmed, body };
    if (assetType) payload.assetType = assetType;

    try {
      const created = await createWritingTemplateApi(payload);
      // Invalidate the picker's templates cache so the next open of
      // "New from template…" re-fetches and shows the new template.
      templatesCache = null;
      callbacks.onStatus?.(`Template "${created.name || trimmed}" saved.`);
    } catch (err) {
      console.error("[composer-v5] save-as-template failed:", err);
      // Surface 409 (key collision) and other errors via onError.
      callbacks.onError?.(err.message || "Failed to save template.");
    }
  }

  // ── Stage 7: Google Docs header connect / import / export ─────────────────
  //
  // The "⊞ Google Doc" header button opens a small menu whose contents depend
  // on whether the current user has a connected Google account:
  //
  //   Not connected:
  //     [Connect Google]
  //
  //   Connected (shows account email):
  //     [Import from Google Doc…]
  //     [Export to Google Doc]
  //     ───
  //     [Disconnect]
  //
  // Status is fetched on mount (non-blocking; menu renders after).
  // Connect → redirect to /api/google/oauth/start (same-tab); the server
  // redirects back to /?google_connected=1 after OAuth completes.  On return,
  // re-check status so the menu reflects the updated state.
  //
  // Import → URL prompt → POST …/google-doc/import → load returned content
  // into the editor via replaceEditorContent (same path as draft reload, lossless).
  // 409 google_not_connected → prompt to Connect first.
  //
  // Export → POST …/google-doc/export → toast the returned Doc URL.

  const gdocWrapEl = rootEl.querySelector('[data-cv5="gdoc-wrap"]');
  const gdocBtnEl  = rootEl.querySelector('[data-cv5="gdoc-btn"]');
  const gdocMenuEl = rootEl.querySelector('[data-cv5="gdoc-menu"]');

  // Current Google connection state (populated by refreshGdocStatus).
  let gdocStatus = null; // { connected: bool, email?: string } | null

  function openGdocMenu() {
    if (!gdocMenuEl) return;
    gdocMenuEl.classList.add("is-open");
    gdocBtnEl?.setAttribute("aria-expanded", "true");
  }

  function closeGdocMenu() {
    if (!gdocMenuEl) return;
    gdocMenuEl.classList.remove("is-open");
    gdocBtnEl?.setAttribute("aria-expanded", "false");
  }

  function renderGdocMenu() {
    if (!gdocMenuEl) return;
    if (gdocStatus === null) {
      // Still loading — show a loading placeholder row.
      gdocMenuEl.innerHTML = `<div class="cv5-picker-menu-empty">Checking Google status…</div>`;
      return;
    }
    if (!gdocStatus.connected) {
      gdocMenuEl.innerHTML = `
        <button type="button" class="cv5-picker-menu-row" data-cv5-gdoc-action="connect" role="menuitem">
          <span aria-hidden="true">🔗</span>
          <span>Connect Google account</span>
        </button>
      `;
    } else {
      const emailLabel = gdocStatus.email
        ? `<div class="cv5-gdoc-email">${esc(gdocStatus.email)}</div>`
        : "";
      gdocMenuEl.innerHTML = `
        ${emailLabel}
        <button type="button" class="cv5-picker-menu-row" data-cv5-gdoc-action="import" role="menuitem">
          <span aria-hidden="true">⬇</span>
          <span>Import from Google Doc…</span>
        </button>
        <button type="button" class="cv5-picker-menu-row" data-cv5-gdoc-action="export" role="menuitem">
          <span aria-hidden="true">⬆</span>
          <span>Export to Google Doc</span>
        </button>
        <div class="cv5-gdoc-separator"></div>
        <button type="button" class="cv5-picker-menu-row cv5-gdoc-disconnect-row" data-cv5-gdoc-action="disconnect" role="menuitem">
          <span aria-hidden="true">✕</span>
          <span>Disconnect</span>
        </button>
      `;
    }
  }

  async function refreshGdocStatus() {
    try {
      gdocStatus = await googleStatusApi();
    } catch (err) {
      console.warn("[composer-v5] google status fetch failed:", err);
      gdocStatus = { connected: false };
    }
    renderGdocMenu();
    // Update the button label to reflect connected state.
    if (gdocBtnEl && gdocStatus?.connected) {
      gdocBtnEl.classList.add("is-connected");
    } else if (gdocBtnEl) {
      gdocBtnEl.classList.remove("is-connected");
    }
  }

  // Fire the status fetch immediately on mount (non-blocking).
  refreshGdocStatus();

  // If the user just completed the OAuth flow the URL will have ?google_connected=1.
  // Show a brief status toast and clean up the param so it doesn't persist on reload.
  {
    const _params = new URLSearchParams(window.location.search);
    if (_params.get("google_connected") === "1") {
      callbacks.onStatus?.("Google account connected.");
      _params.delete("google_connected");
      const _newSearch = _params.toString();
      window.history.replaceState(
        {},
        "",
        window.location.pathname + (_newSearch ? `?${_newSearch}` : "") + window.location.hash
      );
    }
  }

  // Wire the toggle button.
  if (gdocBtnEl) {
    gdocBtnEl.addEventListener("click", (e) => {
      e.stopPropagation();
      const isOpen = gdocMenuEl?.classList.contains("is-open");
      if (isOpen) closeGdocMenu();
      else {
        renderGdocMenu(); // ensure fresh render before opening
        openGdocMenu();
      }
    });
  }

  // Outside-click closes the gdoc menu.
  function handleGdocOutsideClick(e) {
    if (!gdocWrapEl) return;
    if (!gdocWrapEl.contains(e.target)) closeGdocMenu();
  }
  document.addEventListener("click", handleGdocOutsideClick);

  // ESC closes the gdoc menu.
  function handleGdocEscape(e) {
    if (e.key !== "Escape") return;
    if (gdocMenuEl?.classList.contains("is-open")) closeGdocMenu();
  }
  document.addEventListener("keydown", handleGdocEscape);

  // Handle menu action clicks.
  if (gdocMenuEl) {
    gdocMenuEl.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-cv5-gdoc-action]");
      if (!btn) return;
      e.stopPropagation();
      const action = btn.dataset.cv5GdocAction;
      closeGdocMenu();

      if (action === "connect") {
        // Same-tab redirect to Google OAuth consent flow.
        // The backend redirects back to /?google_connected=1 after completion.
        window.location.href = "/api/google/oauth/start";

      } else if (action === "import") {
        await handleGdocImport();

      } else if (action === "export") {
        await handleGdocExport();

      } else if (action === "disconnect") {
        await handleGdocDisconnect();
      }
    });
  }

  async function handleGdocImport() {
    const docUrl = window.prompt("Paste a Google Doc URL to import:");
    if (docUrl === null) return; // user cancelled
    const trimmedUrl = docUrl.trim();
    if (!trimmedUrl) {
      callbacks.onError?.("Please enter a Google Doc URL.");
      return;
    }

    callbacks.onStatus?.("Importing from Google Doc…");
    try {
      const result = await importWritingDraftFromGoogleDocApi(draft.id, { docUrl: trimmedUrl });
      // Load the imported content into the editor (lossless — goes through
      // live_content path, same as draft reload).
      if (result.importedContent) {
        replaceEditorContent(result.importedContent);
      }
      callbacks.onStatus?.("Imported from Google Doc.");
    } catch (err) {
      console.error("[composer-v5] gdoc import failed:", err);
      // _readJsonOrThrow throws Error with message = payload.error string.
      // "Connect Google first" is the 409 google_not_connected payload.
      if (_isGdocNotConnectedError(err)) {
        const doConnect = window.confirm(
          "Your Google account isn't connected. Connect now?"
        );
        if (doConnect) window.location.href = "/api/google/oauth/start";
      } else {
        callbacks.onError?.(err.message || "Import from Google Doc failed.");
      }
    }
  }

  async function handleGdocExport() {
    callbacks.onStatus?.("Exporting to Google Doc…");
    try {
      const result = await exportWritingDraftToGoogleDocApi(draft.id);
      const docUrl = result.docUrl || result.url;
      if (docUrl) {
        window.open(docUrl, "_blank", "noopener,noreferrer");
        callbacks.onStatus?.(`Opened in Google Docs. ${docUrl}`);
      } else {
        callbacks.onStatus?.("Exported to Google Doc.");
      }
    } catch (err) {
      console.error("[composer-v5] gdoc export failed:", err);
      if (_isGdocNotConnectedError(err)) {
        const doConnect = window.confirm(
          "Your Google account isn't connected. Connect now?"
        );
        if (doConnect) window.location.href = "/api/google/oauth/start";
      } else {
        callbacks.onError?.(err.message || "Export to Google Doc failed.");
      }
    }
  }

  // Returns true if the error represents a 409 google_not_connected condition.
  // _readJsonOrThrow throws Error with message = payload.error, which the
  // backend sets to "Connect Google first" for that code.
  function _isGdocNotConnectedError(err) {
    const msg = (err?.message || "").toLowerCase();
    return msg.includes("connect google") || msg.includes("google_not_connected");
  }

  async function handleGdocDisconnect() {
    const confirmed = window.confirm("Disconnect your Google account?");
    if (!confirmed) return;
    try {
      await googleDisconnectApi();
      gdocStatus = { connected: false };
      renderGdocMenu();
      gdocBtnEl?.classList.remove("is-connected");
      callbacks.onStatus?.("Google account disconnected.");
    } catch (err) {
      console.error("[composer-v5] gdoc disconnect failed:", err);
      callbacks.onError?.("Failed to disconnect Google account.");
    }
  }

  // ── Stage 6: Floating margin comments ─────────────────────────────────────
  //
  // Comments are fetched on draft load and after any mutation (create / reply /
  // resolve / reopen).  Each top-level comment:
  //   1. Paints an inline amber highlight over its anchored span (commentsPlugin).
  //   2. Renders a floating card in .cv5-margin-col, vertically aligned to the
  //      top of the highlighted span (via view.coordsAtPos).
  //
  // Re-anchor: if the stored offsets have drifted (doc was edited), we fall back
  // to a text search for anchoredText.  If nothing matches, the card still shows
  // with an "anchor lost" badge (lossless — comments are never dropped).

  const marginColEl = rootEl.querySelector(".cv5-margin-col");

  // Current user cache (filled on first fetch).
  let currentUser = null;
  async function ensureCurrentUser() {
    if (currentUser) return currentUser;
    try {
      currentUser = await fetchAccountInfo();
    } catch (_) {
      currentUser = { email: "", name: "You" };
    }
    return currentUser;
  }

  // Live comment state.
  let commentsData = [];            // top-level CommentRead objects (with .replies)
  let commentsRefreshTimer = null;
  const COMMENTS_REFLOW_DEBOUNCE_MS = 300;

  // ── helpers ───────────────────────────────────────────────────────────────

  function _initials(nameOrEmail) {
    const s = (nameOrEmail || "?").trim();
    const parts = s.split(/[\s@.]+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return s.slice(0, 2).toUpperCase();
  }

  function _formatTimestamp(isoStr) {
    if (!isoStr) return "";
    try {
      const d = new Date(isoStr);
      const now = new Date();
      const diffMs = now - d;
      const diffMin = Math.floor(diffMs / 60000);
      if (diffMin < 1) return "just now";
      if (diffMin < 60) return `${diffMin}m ago`;
      const diffHr = Math.floor(diffMin / 60);
      if (diffHr < 24) return `${diffHr}h ago`;
      return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    } catch (_) {
      return "";
    }
  }

  // Parse @email tokens from a body string.
  function _parseMentions(body) {
    const tokens = [];
    const re = /@([^\s@,;:!?'"()\[\]]+)/g;
    let m;
    while ((m = re.exec(body)) !== null) {
      tokens.push(m[1]);
    }
    return tokens;
  }

  // Render a body string with @mention tokens highlighted.
  function _renderBody(body) {
    return esc(body).replace(/@([^\s@,;:!?'"()\[\]]+)/g,
      (_, tok) => `<span class="cv5-comment-mention">@${esc(tok)}</span>`
    );
  }

  // Resolve anchorStart/anchorEnd → {pmFrom, pmTo, lost}.
  // Uses the posMap from serializeDocToTextWithMap (REUSING the Stage-4 map).
  function _resolveAnchor(comment) {
    const { text: fullText, posMap } = serializeDocToTextWithMap(view.state.doc);
    const docSize = view.state.doc.content.size;

    let pmFrom = null;
    let pmTo   = null;
    let lost   = false;

    if (comment.anchorStart != null && comment.anchorEnd != null) {
      const clampStart = Math.max(0, Math.min(comment.anchorStart, posMap.length - 1));
      const clampEnd   = Math.max(0, Math.min(comment.anchorEnd,   posMap.length - 1));
      const f = posMap[clampStart] ?? 0;
      const t = posMap[clampEnd]   ?? docSize;
      if (f < t) {
        // Verify the text matches (best-effort drift check).
        const slicedText = fullText.slice(comment.anchorStart, comment.anchorEnd);
        if (
          !comment.anchoredText ||
          slicedText === comment.anchoredText ||
          slicedText.trim() === (comment.anchoredText || "").trim()
        ) {
          pmFrom = f;
          pmTo   = t;
        }
      }
    }

    // If positions didn't resolve or text drifted, try text search for anchoredText.
    if ((pmFrom === null || pmTo === null) && comment.anchoredText) {
      const needle = comment.anchoredText.trim();
      const idx = fullText.indexOf(needle);
      if (idx >= 0) {
        const searchEnd = idx + needle.length;
        const cf = Math.max(0, Math.min(idx,       posMap.length - 1));
        const ct = Math.max(0, Math.min(searchEnd, posMap.length - 1));
        const sf = posMap[cf] ?? 0;
        const st = posMap[ct] ?? docSize;
        if (sf < st) {
          pmFrom = sf;
          pmTo   = st;
        }
      }
    }

    if (pmFrom === null || pmTo === null) {
      lost = true;
      pmFrom = 0;
      pmTo   = 0;
    }

    return { pmFrom, pmTo, lost };
  }

  // ── Main render pipeline ──────────────────────────────────────────────────

  // commentExpandedSet: set of comment IDs (as strings) that are in expanded state.
  // Default is expanded for open comments, collapsed for resolved.
  const commentExpandedSet = new Set();

  async function fetchAndRenderComments() {
    if (destroyed) return;
    try {
      const all = await listDraftCommentsApi(currentDraftId);
      commentsData = Array.isArray(all) ? all : [];
    } catch (err) {
      console.warn("[composer-v5] comments fetch failed:", err);
      return;
    }
    if (destroyed) return;
    _renderComments();
  }

  function _renderComments() {
    if (!marginColEl) return;

    // 1. Resolve anchors for all top-level comments (only top-level get cards).
    const resolved = commentsData.map((c) => {
      const { pmFrom, pmTo, lost } = _resolveAnchor(c);
      return { comment: c, pmFrom, pmTo, lost };
    });

    // 2. Update the comments plugin highlight decorations.
    const anchors = resolved
      .filter((r) => !r.lost && r.pmFrom < r.pmTo)
      .map((r) => ({ pmFrom: r.pmFrom, pmTo: r.pmTo, commentId: r.comment.id }));

    const tr = view.state.tr.setMeta(commentsKey, { anchors });
    view.dispatch(tr);

    // 3. Clear and rebuild margin cards.
    marginColEl.innerHTML = "";

    if (commentsData.length === 0) {
      marginColEl.innerHTML = `
        <div class="cv5-margin-empty">
          Select text and click <strong>💬 Comment</strong> to add a comment.
        </div>`;
      return;
    }

    // 4. Compute vertical positions from the editor + render cards.
    //    We need the editor to have painted; use coordsAtPos for each anchor.
    const paperRect = paperEl ? paperEl.getBoundingClientRect() : null;
    const marginRect = marginColEl.getBoundingClientRect();

    // Track the bottom of the last card so we can push-down on collision.
    let nextAvailableTop = 0;

    for (const r of resolved) {
      const { comment, pmFrom, pmTo, lost } = r;

      // Skip replies (they're rendered inside their parent card).
      if (comment.parentId != null) continue;

      // Default expansion: open comments expanded, resolved collapsed.
      const isExpanded = commentExpandedSet.has(String(comment.id))
        ? true
        : commentExpandedSet.has(String(comment.id) + ":collapsed")
          ? false
          : comment.status === "open";

      // Compute top position.
      let cardTop = nextAvailableTop;
      if (!lost && pmFrom > 0 && paperRect && marginRect) {
        try {
          const coords = view.coordsAtPos(pmFrom);
          // coords.top is relative to viewport; we need it relative to margin col.
          const relativeTop = coords.top - marginRect.top + marginColEl.scrollTop;
          cardTop = Math.max(nextAvailableTop, relativeTop);
        } catch (_) {
          // coordsAtPos can throw if doc changed; fall through to nextAvailableTop
        }
      }

      // Render the card.
      const cardEl = document.createElement("div");
      cardEl.className = "cv5-comment-wrap";
      cardEl.style.top = cardTop + "px";
      cardEl.dataset.commentId = String(comment.id);
      cardEl.innerHTML = _renderCommentCard(comment, isExpanded, lost);
      marginColEl.appendChild(cardEl);

      // Estimate card height for collision avoidance.
      // We can't measure before inserting, so use a rough estimate.
      // On next reflow (RAF) we'll adjust — good enough for v1.
      const estimatedH = isExpanded ? 120 + (comment.replies || []).length * 70 : 42;
      nextAvailableTop = cardTop + estimatedH + 8;
    }

    // Wire card interactions after DOM is populated.
    _wireCardInteractions();
  }

  function _renderCommentCard(comment, isExpanded, lost) {
    const author = comment.author || {};
    const ini = _initials(author.name || author.email || "?");
    const authorLabel = esc(author.name || author.email || "Unknown");
    const timeLabel   = _formatTimestamp(comment.createdAt);
    const isResolved  = comment.status === "resolved";

    const lostBadge = lost
      ? `<span class="cv5-comment-lost-badge" title="Original anchor position could not be found">⚠ anchor lost</span>`
      : "";

    const resolvedBanner = isResolved
      ? `<div class="cv5-comment-resolved-bar">
           Resolved by ${esc((comment.resolvedBy?.name || comment.resolvedBy?.email) ?? "someone")}
           <button type="button" class="cv5-comment-action-btn" data-cv5-comment-reopen="${comment.id}">Reopen</button>
         </div>`
      : "";

    const mentionCount = (comment.mentions || []).length;
    const pingHtml = mentionCount > 0
      ? `<div class="cv5-comment-ping">🔔 ${mentionCount} notified</div>`
      : "";

    // Render replies.
    const repliesHtml = (comment.replies || []).map((r) => _renderReply(r)).join("");

    // Reply input (only for open comments when expanded).
    const replyInputHtml = !isResolved ? `
      <div class="cv5-comment-reply-row">
        <div class="cv5-comment-reply-input-wrap">
          <input
            type="text"
            class="cv5-comment-reply-input"
            placeholder="Reply…"
            data-cv5-reply-for="${comment.id}"
            aria-label="Reply to comment"
          />
          <button type="button" class="cv5-comment-reply-submit" data-cv5-reply-submit="${comment.id}" aria-label="Send reply">↑</button>
        </div>
      </div>` : "";

    const collapsedChip = `
      <div class="cv5-comment-chip" data-cv5-comment-expand="${comment.id}">
        <div class="cv5-comment-av cv5-comment-av--sm">${esc(ini)}</div>
        <span class="cv5-comment-chip-preview">${esc((comment.body || "").slice(0, 40))}${(comment.body || "").length > 40 ? "…" : ""}</span>
        ${isResolved ? `<span class="cv5-comment-chip-resolved">✓</span>` : ""}
      </div>`;

    const fullCard = `
      <div class="cv5-comment-card${isResolved ? " is-resolved" : ""}${lost ? " is-anchor-lost" : ""}">
        <div class="cv5-comment-connector"></div>
        <div class="cv5-comment-card-inner">
          ${lostBadge}
          ${resolvedBanner}
          <div class="cv5-comment-card-top">
            <div class="cv5-comment-av">${esc(ini)}</div>
            <span class="cv5-comment-name">${authorLabel}</span>
            <span class="cv5-comment-time">${timeLabel}</span>
            <button type="button" class="cv5-comment-collapse-btn" data-cv5-comment-collapse="${comment.id}" title="Collapse">⌄</button>
          </div>
          <div class="cv5-comment-body">${_renderBody(comment.body || "")}</div>
          ${pingHtml}
          <div class="cv5-comment-foot">
            ${!isResolved ? `<button type="button" class="cv5-comment-action-btn" data-cv5-comment-resolve="${comment.id}">Resolve</button>` : ""}
          </div>
          ${repliesHtml.length > 0 ? `<div class="cv5-comment-replies">${repliesHtml}</div>` : ""}
          ${replyInputHtml}
        </div>
      </div>`;

    return isExpanded ? fullCard : collapsedChip;
  }

  function _renderReply(reply) {
    const author = reply.author || {};
    const ini = _initials(author.name || author.email || "?");
    const authorLabel = esc(author.name || author.email || "Unknown");
    const timeLabel   = _formatTimestamp(reply.createdAt);
    return `
      <div class="cv5-comment-reply">
        <div class="cv5-comment-av cv5-comment-av--sm">${esc(ini)}</div>
        <div class="cv5-comment-reply-body">
          <div class="cv5-comment-reply-meta">
            <span class="cv5-comment-name">${authorLabel}</span>
            <span class="cv5-comment-time">${timeLabel}</span>
          </div>
          <div class="cv5-comment-body">${_renderBody(reply.body || "")}</div>
        </div>
      </div>`;
  }

  function _wireCardInteractions() {
    if (!marginColEl) return;

    // Collapse a card.
    marginColEl.querySelectorAll("[data-cv5-comment-collapse]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.dataset.cv5CommentCollapse;
        commentExpandedSet.delete(id);
        commentExpandedSet.add(id + ":collapsed");
        _renderComments();
      });
    });

    // Expand from chip.
    marginColEl.querySelectorAll("[data-cv5-comment-expand]").forEach((chip) => {
      chip.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = chip.dataset.cv5CommentExpand;
        commentExpandedSet.delete(id + ":collapsed");
        commentExpandedSet.add(id);
        _renderComments();
      });
    });

    // Resolve.
    marginColEl.querySelectorAll("[data-cv5-comment-resolve]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = Number(btn.dataset.cv5CommentResolve);
        btn.disabled = true;
        try {
          await resolveCommentApi(id);
          await fetchAndRenderComments();
          callbacks.onStatus?.("Comment resolved.");
        } catch (err) {
          console.error("[composer-v5] resolve comment failed:", err);
          callbacks.onError?.(err.message || "Failed to resolve comment.");
          btn.disabled = false;
        }
      });
    });

    // Reopen.
    marginColEl.querySelectorAll("[data-cv5-comment-reopen]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = Number(btn.dataset.cv5CommentReopen);
        btn.disabled = true;
        try {
          await reopenCommentApi(id);
          await fetchAndRenderComments();
          callbacks.onStatus?.("Comment reopened.");
        } catch (err) {
          console.error("[composer-v5] reopen comment failed:", err);
          callbacks.onError?.(err.message || "Failed to reopen comment.");
          btn.disabled = false;
        }
      });
    });

    // Reply submit (button click).
    marginColEl.querySelectorAll("[data-cv5-reply-submit]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const parentId = Number(btn.dataset.cv5ReplySubmit);
        const input = marginColEl.querySelector(`[data-cv5-reply-for="${parentId}"]`);
        if (!input) return;
        const body = input.value.trim();
        if (!body) return;
        await _submitReply(parentId, body, input, btn);
      });
    });

    // Reply submit (Enter key on input).
    marginColEl.querySelectorAll(".cv5-comment-reply-input").forEach((input) => {
      input.addEventListener("keydown", async (e) => {
        if (e.key !== "Enter" || e.shiftKey) return;
        e.preventDefault();
        const parentId = Number(input.dataset.cv5ReplyFor);
        const btn = marginColEl.querySelector(`[data-cv5-reply-submit="${parentId}"]`);
        const body = input.value.trim();
        if (!body) return;
        await _submitReply(parentId, body, input, btn);
      });
    });
  }

  async function _submitReply(parentId, body, inputEl, btnEl) {
    if (inputEl) inputEl.disabled = true;
    if (btnEl)   btnEl.disabled   = true;
    const mentions = _parseMentions(body);
    try {
      await createDraftCommentApi(currentDraftId, {
        body,
        parentId,
        mentions,
      });
      await fetchAndRenderComments();
    } catch (err) {
      console.error("[composer-v5] reply failed:", err);
      callbacks.onError?.(err.message || "Failed to post reply.");
      if (inputEl) { inputEl.disabled = false; inputEl.focus(); }
      if (btnEl)   btnEl.disabled = false;
    }
  }

  // ── Comments reflow ───────────────────────────────────────────────────────
  //
  // After doc edits we recompute card vertical positions without re-fetching.

  let commentsReflowRaf = null;
  let commentsReflowTimer = null;

  function scheduleCommentsReflow() {
    if (commentsReflowTimer) clearTimeout(commentsReflowTimer);
    commentsReflowTimer = setTimeout(() => {
      commentsReflowTimer = null;
      if (commentsReflowRaf) cancelAnimationFrame(commentsReflowRaf);
      commentsReflowRaf = requestAnimationFrame(() => {
        commentsReflowRaf = null;
        if (!destroyed) _renderComments();
      });
    }, COMMENTS_REFLOW_DEBOUNCE_MS);
  }

  // Kick off initial fetch after the editor has painted.
  setTimeout(() => void fetchAndRenderComments(), 600);

  // ── Comment composer popover ──────────────────────────────────────────────
  //
  // Opens when the user clicks "💬 Comment" in the selection toolbar.
  // Captures the PM selection → char offsets → POST.

  const commentComposerEl = document.createElement("div");
  commentComposerEl.className = "cv5-comment-composer";
  commentComposerEl.setAttribute("role", "dialog");
  commentComposerEl.setAttribute("aria-label", "Add comment");
  commentComposerEl.style.display = "none";
  document.body.appendChild(commentComposerEl);

  let commentAnchorRange = null;  // { from, to, anchorStart, anchorEnd, anchoredText }
  // Tracked at mount level so closeCommentComposer can remove any orphaned mention dropdown.
  let _activeMentionDropdownEl = null;

  function _removeMentionDropdown() {
    if (_activeMentionDropdownEl) {
      try { document.body.removeChild(_activeMentionDropdownEl); } catch (_) { /* noop */ }
      _activeMentionDropdownEl = null;
    }
  }

  function openCommentComposer() {
    if (!selectionRange) return;
    const { from, to } = selectionRange;

    // Convert PM positions to char offsets using serializeDocToTextWithMap.
    // posMap[charIdx] = pmPos.  We need the inverse: for pmPos in [from,to],
    // find the range of char indices that map into that PM span.
    const { text: fullText, posMap } = serializeDocToTextWithMap(view.state.doc);
    let anchorStart = null;
    let anchorEnd   = null;
    for (let i = 0; i < posMap.length; i++) {
      const pmPos = posMap[i];
      if (pmPos >= from && anchorStart === null) anchorStart = i;
      if (pmPos < to) anchorEnd = i + 1;
    }
    if (anchorStart === null) anchorStart = 0;
    if (anchorEnd   === null) anchorEnd   = 0;
    const anchoredText = fullText.slice(anchorStart, anchorEnd);

    commentAnchorRange = { from, to, anchorStart, anchorEnd, anchoredText };

    // Show "commenting as" user.
    const me = currentUser;
    const commentingAs = me ? esc(me.name || me.email || "") : "";

    commentComposerEl.innerHTML = `
      <div class="cv5-cc-header">
        <span class="cv5-cc-title">Add comment</span>
        ${commentingAs ? `<span class="cv5-cc-as">as <strong>${commentingAs}</strong></span>` : ""}
      </div>
      <div class="cv5-cc-anchor-preview" title="${esc(anchoredText)}">${esc(anchoredText.slice(0, 60))}${anchoredText.length > 60 ? "…" : ""}</div>
      <textarea
        class="cv5-cc-body"
        rows="3"
        placeholder="Add a comment… Type @ to mention someone"
        aria-label="Comment body"
        autofocus
      ></textarea>
      <div class="cv5-cc-actions">
        <button type="button" class="cv5-cc-cancel">Cancel</button>
        <button type="button" class="cv5-cc-submit">Comment</button>
      </div>
    `;
    commentComposerEl.style.display = "block";
    positionNearSelection(commentComposerEl);

    const textarea = commentComposerEl.querySelector(".cv5-cc-body");
    textarea?.focus();

    // ── @-mention autocomplete ─────────────────────────────────────────────────
    //
    // When the user types "@" followed by characters, show a dropdown of matching
    // teammates from GET /api/users. Arrow keys + Enter / click to select.
    // Inserting a match appends "@Name " and records the user's email in the
    // mentions array (server normalises them). Dismisses on Escape or outside click.
    //
    // Teammates are loaded once (lazy, cached for this composer session) and
    // filtered client-side for instant feedback.

    let _teammates = null;          // null = not yet loaded, [] or populated after fetch
    let _mentionDropdown = null;    // the floating <div>
    let _mentionAtPos = -1;         // index of the "@" that opened the dropdown
    let _dropdownActiveIdx = -1;    // keyboard-active row index (-1 = none)

    async function _ensureTeammates() {
      if (_teammates !== null) return _teammates;
      try {
        _teammates = await fetchTeammatesApi();
      } catch (_) {
        _teammates = [];
      }
      return _teammates;
    }

    function _closeMentionDropdown() {
      if (_mentionDropdown) {
        try { document.body.removeChild(_mentionDropdown); } catch (_) { /* noop */ }
        _mentionDropdown = null;
        _activeMentionDropdownEl = null;
      }
      _mentionAtPos = -1;
      _dropdownActiveIdx = -1;
    }

    function _selectMentionItem(item) {
      if (!textarea) return;
      const val = textarea.value;
      // Replace "@partial" from _mentionAtPos to the current cursor position.
      const before = val.slice(0, _mentionAtPos);
      const insertName = item.name || item.email;
      const after  = val.slice(textarea.selectionEnd);
      // Insert "@Name " (trailing space to continue typing).
      textarea.value = before + "@" + insertName + " " + after;
      // Move cursor after the inserted text.
      const newPos = before.length + insertName.length + 2; // "@" + name + " "
      textarea.setSelectionRange(newPos, newPos);
      _closeMentionDropdown();
      textarea.focus();
    }

    function _renderMentionDropdown(matches) {
      _closeMentionDropdown();
      if (!matches.length) return;

      const dropdown = document.createElement("div");
      dropdown.className = "cv5-mention-dropdown";
      document.body.appendChild(dropdown);
      _mentionDropdown = dropdown;
      _activeMentionDropdownEl = dropdown;   // track at mount level for cleanup
      _dropdownActiveIdx = -1;

      matches.forEach((item, idx) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "cv5-mention-item";
        btn.innerHTML = `
          <span class="cv5-mention-item-name">${esc(item.name || item.email)}</span>
          ${item.name ? `<span class="cv5-mention-item-email">${esc(item.email)}</span>` : ""}
        `;
        btn.addEventListener("mousedown", (e) => {
          // Prevent textarea blur so we can still modify its value.
          e.preventDefault();
          _selectMentionItem(item);
        });
        dropdown.appendChild(btn);
      });

      // Position below / above the textarea caret.
      // Anchor to the textarea's bottom-left as a simple first pass.
      if (textarea) {
        const tRect = textarea.getBoundingClientRect();
        const dropH = Math.min(180, matches.length * 46 + 8);
        const dropW = 240;
        let left = tRect.left;
        let top  = tRect.bottom + 4;
        if (top + dropH > window.innerHeight - 8) top = Math.max(8, tRect.top - dropH - 4);
        if (left + dropW > window.innerWidth - 8) left = Math.max(8, window.innerWidth - dropW - 8);
        dropdown.style.left = left + "px";
        dropdown.style.top  = top  + "px";
      }
    }

    function _setDropdownActive(idx) {
      if (!_mentionDropdown) return;
      const items = _mentionDropdown.querySelectorAll(".cv5-mention-item");
      items.forEach((el, i) => el.classList.toggle("is-active", i === idx));
      _dropdownActiveIdx = idx;
      if (idx >= 0 && items[idx]) items[idx].scrollIntoView({ block: "nearest" });
    }

    async function _onTextareaInput() {
      if (!textarea) return;
      const val  = textarea.value;
      const pos  = textarea.selectionEnd;

      // Find the last "@" before the cursor that has no space between it and cursor.
      let atIdx = -1;
      for (let i = pos - 1; i >= 0; i--) {
        if (val[i] === "@") { atIdx = i; break; }
        if (val[i] === " " || val[i] === "\n") break; // space breaks the token
      }

      if (atIdx < 0) {
        _closeMentionDropdown();
        return;
      }

      _mentionAtPos = atIdx;
      const partial = val.slice(atIdx + 1, pos).toLowerCase();

      const teammates = await _ensureTeammates();
      const matches = partial
        ? teammates.filter(
            (t) =>
              (t.name || "").toLowerCase().includes(partial) ||
              t.email.toLowerCase().includes(partial)
          )
        : teammates.slice(0, 8); // show first 8 when just "@" typed

      if (matches.length === 0) {
        _closeMentionDropdown();
        return;
      }

      _renderMentionDropdown(matches);
    }

    textarea?.addEventListener("input", () => void _onTextareaInput());

    commentComposerEl.querySelector(".cv5-cc-cancel")?.addEventListener("click", () => {
      _closeMentionDropdown();
      closeCommentComposer();
    });
    commentComposerEl.querySelector(".cv5-cc-submit")?.addEventListener("click", () => {
      _closeMentionDropdown();
      void submitComment();
    });

    textarea?.addEventListener("keydown", (e) => {
      // Dropdown navigation.
      if (_mentionDropdown) {
        const items = _mentionDropdown.querySelectorAll(".cv5-mention-item");
        if (e.key === "ArrowDown") {
          e.preventDefault();
          _setDropdownActive(Math.min(_dropdownActiveIdx + 1, items.length - 1));
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          _setDropdownActive(Math.max(_dropdownActiveIdx - 1, 0));
          return;
        }
        if (e.key === "Enter" && _dropdownActiveIdx >= 0) {
          e.preventDefault();
          // Resolve the item from the current teammate filter.
          const activeBtn = _mentionDropdown.querySelectorAll(".cv5-mention-item")[_dropdownActiveIdx];
          if (activeBtn) activeBtn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
          return;
        }
        if (e.key === "Escape") {
          _closeMentionDropdown();
          return;
        }
      }
      // Submit / close shortcuts.
      if (e.key === "Enter" && e.metaKey) {
        e.preventDefault();
        _closeMentionDropdown();
        void submitComment();
      } else if (e.key === "Escape") {
        _closeMentionDropdown();
        closeCommentComposer();
      }
    });

    // Close the dropdown when the textarea loses focus (unless mousedown on a dropdown item).
    textarea?.addEventListener("blur", () => {
      // Small delay so mousedown on dropdown item fires before blur closes it.
      setTimeout(() => {
        if (_mentionDropdown && !_mentionDropdown.contains(document.activeElement)) {
          _closeMentionDropdown();
        }
      }, 150);
    });
  }

  function closeCommentComposer() {
    _removeMentionDropdown();
    commentComposerEl.style.display = "none";
    commentComposerEl.innerHTML = "";
    commentAnchorRange = null;
  }

  async function submitComment() {
    const textarea = commentComposerEl.querySelector(".cv5-cc-body");
    const body = textarea?.value?.trim();
    if (!body) return;
    if (!commentAnchorRange) return;

    const { anchorStart, anchorEnd, anchoredText } = commentAnchorRange;
    const mentions = _parseMentions(body);

    const submitBtn = commentComposerEl.querySelector(".cv5-cc-submit");
    if (submitBtn) submitBtn.disabled = true;
    if (textarea)  textarea.disabled  = true;

    try {
      await createDraftCommentApi(currentDraftId, {
        body,
        anchorStart,
        anchorEnd,
        anchoredText,
        mentions,
      });
      closeCommentComposer();
      hideSelToolbar();
      await fetchAndRenderComments();
      callbacks.onStatus?.("Comment added.");
    } catch (err) {
      console.error("[composer-v5] create comment failed:", err);
      callbacks.onError?.(err.message || "Failed to post comment.");
      if (submitBtn) submitBtn.disabled = false;
      if (textarea)  textarea.disabled  = false;
    }
  }

  // Close comment composer on outside click.
  function handleCommentComposerOutsideClick(e) {
    if (
      commentComposerEl.style.display !== "none" &&
      !commentComposerEl.contains(e.target) &&
      !selToolbar.contains(e.target)
    ) {
      closeCommentComposer();
    }
  }
  document.addEventListener("click", handleCommentComposerOutsideClick, true);

  // Fetch current user now so it's ready when the composer opens.
  void ensureCurrentUser();

  // ── Chat (LEFT column) ─────────────────────────────────────────────────────
  // Reuses the existing compose endpoint. Stage 1 keeps the existing thread
  // working; replies that produce/rewrite a draft also refresh the editor.
  const chatHistory = Array.isArray(draft.threadMessages) ? [...draft.threadMessages] : [];
  renderChatThread();

  // ── Apply-to-document undo state ─────────────────────────────────────────
  // Holds the previous editor content so the user can revert after Apply.
  let applyUndoContent = null;

  // ── Deliverable preview popover ───────────────────────────────────────────
  // Shown when the user clicks "Preview & apply" in the chat thread.
  // Matches the rewrite-span accept/reject popover pattern (cv5-rwp-* classes).
  const deliverablePreviewPopover = document.createElement("div");
  deliverablePreviewPopover.className = "cv5-rewrite-popover cv5-deliverable-preview";
  deliverablePreviewPopover.setAttribute("role", "dialog");
  deliverablePreviewPopover.setAttribute("aria-label", "Proposed document");
  deliverablePreviewPopover.style.display = "none";
  document.body.appendChild(deliverablePreviewPopover);

  let pendingDeliverableContent = null; // string — the deliverable text staged for apply

  function openDeliverablePreview(newContent, triggerEl) {
    pendingDeliverableContent = newContent;
    // Truncate for the preview — show up to ~600 chars then a "Show full…" toggle.
    const PREVIEW_MAX = 600;
    const truncated = newContent.length > PREVIEW_MAX;
    const previewText = truncated ? newContent.slice(0, PREVIEW_MAX) + "…" : newContent;

    deliverablePreviewPopover.innerHTML = `
      <div class="cv5-rwp-label">Proposed document</div>
      <div class="cv5-deliverable-preview-body">
        <div class="cv5-deliverable-preview-text" data-cv5-deliverable-preview-text>${esc(previewText)}</div>
        ${truncated ? `<button type="button" class="cv5-deliverable-preview-expand" data-cv5-deliverable-expand>Show full document ▾</button>` : ""}
      </div>
      <div class="cv5-rwp-actions">
        <button type="button" class="cv5-rwp-reject" data-cv5-deliverable-discard>Discard</button>
        <button type="button" class="cv5-rwp-accept" data-cv5-deliverable-apply>Apply to document</button>
      </div>
    `;
    deliverablePreviewPopover.style.display = "block";

    // Position the popover near the trigger button (in the chat thread).
    if (triggerEl) {
      const btnRect = triggerEl.getBoundingClientRect();
      const popW = 520; // approximate; CSS constrains max-width
      const m = 10;
      let left = btnRect.left;
      if (left + popW > window.innerWidth - m) left = Math.max(m, window.innerWidth - popW - m);
      let top = btnRect.bottom + 8;
      const popH = deliverablePreviewPopover.offsetHeight || 360;
      if (top + popH > window.innerHeight - m) top = Math.max(m, btnRect.top - popH - 8);
      deliverablePreviewPopover.style.left = `${left}px`;
      deliverablePreviewPopover.style.top = `${top}px`;
    }

    // Wire expand toggle.
    deliverablePreviewPopover.querySelector("[data-cv5-deliverable-expand]")?.addEventListener("click", (e) => {
      const textEl = deliverablePreviewPopover.querySelector("[data-cv5-deliverable-preview-text]");
      const btn = e.currentTarget;
      if (textEl && pendingDeliverableContent) {
        if (btn.dataset.expanded === "1") {
          textEl.textContent = previewText;
          btn.textContent = "Show full document ▾";
          delete btn.dataset.expanded;
        } else {
          textEl.textContent = pendingDeliverableContent;
          btn.textContent = "Show less ▴";
          btn.dataset.expanded = "1";
        }
      }
    });

    // Wire Discard.
    deliverablePreviewPopover.querySelector("[data-cv5-deliverable-discard]")?.addEventListener("click", () => {
      closeDeliverablePreview();
      callbacks.onStatus?.("Discarded — document unchanged.");
    });

    // Wire Apply.
    deliverablePreviewPopover.querySelector("[data-cv5-deliverable-apply]")?.addEventListener("click", () => {
      if (!pendingDeliverableContent) return;
      applyUndoContent = serializeDocToText(view.state.doc);
      replaceEditorContent(pendingDeliverableContent);
      scheduleAutosave();
      callbacks.onStatus?.("Draft applied to document. Autosaving…");
      closeDeliverablePreview();
      // Re-render the chat thread to reveal the Undo button.
      renderChatThread();
    });
  }

  function closeDeliverablePreview() {
    deliverablePreviewPopover.style.display = "none";
    deliverablePreviewPopover.innerHTML = "";
    pendingDeliverableContent = null;
  }

  // Close preview on outside click.
  function handleDeliverablePreviewOutsideClick(e) {
    if (
      deliverablePreviewPopover.style.display !== "none" &&
      !deliverablePreviewPopover.contains(e.target) &&
      !e.target.closest(".cv5-preview-apply-btn")
    ) {
      closeDeliverablePreview();
    }
  }
  document.addEventListener("click", handleDeliverablePreviewOutsideClick, true);

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

    // Show the Apply affordance ONLY when the model emitted an explicit
    // ```artemis-draft``` fence (parsed server-side into m.deliverable). No
    // heuristic fallback: a fence-less reply is conversational, so applying it
    // would dump chat prose into the document. Fence present = real revised copy.
    const showApply = role === "assistant" && !m.pending && typeof m.deliverable === "string";

    // The deliverable text to apply: the fence content only.
    const applyPayload = showApply ? m.deliverable : "";

    const applyBtn = showApply
      ? `<div class="cv5-msg-apply-row">
           <button type="button" class="cv5-apply-btn cv5-preview-apply-btn" data-cv5-preview-apply="${esc(applyPayload)}" title="Preview the proposed document before applying">Preview &amp; apply…</button>
           ${applyUndoContent !== null ? `<button type="button" class="cv5-apply-undo">Undo apply</button>` : ""}
         </div>`
      : "";

    return `
      <div class="cv5-msg ${role}${pending}">
        <div class="cv5-msg-role">${esc(label)}</div>
        <div class="cv5-msg-bub">${esc(text)}</div>
        ${applyBtn}
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

  // ── Apply-to-document event delegation ────────────────────────────────────
  // Delegated on chatThreadEl so it works after innerHTML replaces (no memory
  // leak — one listener per mount, removed on destroy).
  function handleChatThreadClick(evt) {
    // "Preview & apply…" opens the preview popover — does NOT apply directly.
    const previewBtn = evt.target.closest(".cv5-preview-apply-btn");
    if (previewBtn) {
      evt.stopPropagation();
      const newContent = previewBtn.dataset.cv5PreviewApply;
      if (!newContent) return;
      openDeliverablePreview(newContent, previewBtn);
      return;
    }
    const undoBtn = evt.target.closest(".cv5-apply-undo");
    if (undoBtn) {
      if (applyUndoContent === null) return;
      replaceEditorContent(applyUndoContent);
      scheduleAutosave();
      callbacks.onStatus?.("Apply undone — previous document restored. Autosaving…");
      applyUndoContent = null;
      closeDeliverablePreview();
      renderChatThread();
      return;
    }
  }
  if (chatThreadEl) {
    chatThreadEl.addEventListener("click", handleChatThreadClick);
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
        // Use chatMessage (fence-stripped) for display; fall back to responseText
        // for backward compat if the field is absent (old server).
        const displayText = resp.chatMessage ?? resp.responseText ?? "";
        const asstEntry = persistedAsst
          ? { ...persistedAsst, text: persistedAsst.text || displayText }
          : {
              id: `cv5-a-${Date.now()}`,
              role: "assistant",
              label: "Amira",
              text: displayText,
            };
        // Attach deliverable + originating request so renderChatMessage can
        // show the Apply button and the fallback heuristic has the request.
        if (typeof resp.deliverable === "string") {
          asstEntry.deliverable = resp.deliverable;
        }
        asstEntry._request = request;
        chatHistory[idxA] = asstEntry;
      }
      renderChatThread();
      // Stage-1 path: if the compose engine produced a new draft body via the
      // old "versions" mechanism, still refresh the editor.  With the new fence
      // path the user explicitly applies via the Apply button — so we don't
      // auto-replace here.
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
      // Refresh proposed-rules badge/panel so any AI-proposed candidates from
      // this compose turn surface immediately without requiring panel reopen.
      void _refreshProposedCandidates();
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
      // Include claim-flags + comments plugins so highlights survive a content refresh.
      plugins: [...exampleSetup({ schema: composerSchema, menuBar: false }), claimFlagsPlugin, commentsPlugin],
    });
    view.updateState(newState);
    // Re-render comment cards after the doc is replaced.
    scheduleCommentsReflow();
  }

  // ── Rules panel (proposed rules + manual propose) ─────────────────────────
  //
  // Opened by clicking the "Rules" button in the top action bar.
  // Shows:
  //   1. Proposed candidates (status="proposed") with Approve / Reject per item.
  //   2. A manual "+ Propose a rule" textarea form (min 10 chars).
  //
  // The panel is a popover div anchored to the chat column, above the composer.
  // It refreshes its candidate list every time it opens AND after compose returns.

  const rulesBtnEl = rootEl.querySelector('[data-cv5="rules-btn"]');
  const rulesBadgeEl = rootEl.querySelector('[data-cv5="rules-badge"]');

  // Floating rules panel — appended to document.body (fixed position, same
  // pattern as selToolbar / rewritePopover).
  const rulesPanel = document.createElement("div");
  rulesPanel.className = "cv5-rules-panel";
  rulesPanel.setAttribute("role", "dialog");
  rulesPanel.setAttribute("aria-label", "Proposed rules");
  rulesPanel.style.display = "none";
  document.body.appendChild(rulesPanel);

  let rulesPanelOpen = false;
  let rulesProposedCache = [];

  function _positionRulesPanel() {
    if (!rulesBtnEl) return;
    const rect = rulesBtnEl.getBoundingClientRect();
    const panelW = 420;
    let left = rect.left;
    left = Math.max(8, Math.min(left, window.innerWidth - panelW - 8));
    // Position above the button; if no room, position below.
    const gap = 6;
    const panelH = rulesPanel.offsetHeight || 300;
    let top = rect.top - panelH - gap;
    if (top < 8) top = rect.bottom + gap;
    rulesPanel.style.left = `${left}px`;
    rulesPanel.style.top = `${top}px`;
  }

  function _renderRulesPanel() {
    const proposed = rulesProposedCache;
    const candidateRows = proposed.length > 0
      ? proposed.map((c) => `
          <div class="cv5-rules-candidate" data-cv5-candidate-id="${esc(String(c.id ?? ""))}">
            <div class="cv5-rules-candidate-text">${esc(c.proposed_text || c.proposedText || "")}</div>
            ${c.rationale ? `<div class="cv5-rules-candidate-rationale">${esc(c.rationale)}</div>` : ""}
            <div class="cv5-rules-candidate-actions">
              <button type="button" class="cv5-rules-approve-btn" data-cv5-decide="approved" data-cv5-candidate-id="${esc(String(c.id ?? ""))}" ${c.id == null ? "disabled" : ""}>Approve</button>
              <button type="button" class="cv5-rules-reject-btn" data-cv5-decide="rejected" data-cv5-candidate-id="${esc(String(c.id ?? ""))}" ${c.id == null ? "disabled" : ""}>Reject</button>
            </div>
          </div>
        `).join("")
      : `<div class="cv5-rules-empty">No proposed rules yet. Run a compose turn and Amira may propose rules, or add one manually below.</div>`;

    rulesPanel.innerHTML = `
      <div class="cv5-rules-panel-hdr">
        <span class="cv5-rules-panel-title">Proposed Rules</span>
        <button type="button" class="cv5-rules-panel-close" aria-label="Close">✕</button>
      </div>
      <div class="cv5-rules-candidates-list">
        ${candidateRows}
      </div>
      <div class="cv5-rules-propose-section">
        <div class="cv5-rules-propose-label">+ Propose a rule manually</div>
        <textarea
          class="cv5-rules-propose-input"
          data-cv5="rules-propose-input"
          rows="2"
          placeholder="Describe a writing rule (min 10 characters)…"
          aria-label="Propose a writing rule"
        ></textarea>
        <button type="button" class="cv5-rules-propose-submit" data-cv5="rules-propose-submit">Propose</button>
      </div>
    `;
  }

  function openRulesPanel() {
    if (rulesPanelOpen) { closeRulesPanel(); return; }
    rulesPanelOpen = true;
    rulesPanel.style.display = "block";
    _renderRulesPanel();
    _positionRulesPanel();
    // Wire close button (re-rendered on each open).
    rulesPanel.querySelector(".cv5-rules-panel-close")?.addEventListener("click", closeRulesPanel);
    // Refresh candidates from server (non-blocking).
    void _refreshProposedCandidates();
  }

  function closeRulesPanel() {
    rulesPanelOpen = false;
    rulesPanel.style.display = "none";
  }

  async function _refreshProposedCandidates() {
    try {
      const data = await listWritingTrainingCandidatesApi("proposed");
      // The endpoint returns { training_candidates: [...] }
      rulesProposedCache = Array.isArray(data?.training_candidates)
        ? data.training_candidates
        : Array.isArray(data) ? data : [];
    } catch (err) {
      console.warn("[composer-v5] failed to load proposed candidates:", err);
    }
    // Update badge.
    const count = rulesProposedCache.length;
    if (rulesBadgeEl) {
      if (count > 0) {
        rulesBadgeEl.textContent = String(count);
        rulesBadgeEl.removeAttribute("hidden");
      } else {
        rulesBadgeEl.setAttribute("hidden", "");
      }
    }
    // Re-render panel if open.
    if (rulesPanelOpen) {
      _renderRulesPanel();
      rulesPanel.querySelector(".cv5-rules-panel-close")?.addEventListener("click", closeRulesPanel);
    }
  }

  // Delegated click handler for the rules panel (approve/reject + propose).
  rulesPanel.addEventListener("click", async (e) => {
    const decideBtn = e.target.closest("[data-cv5-decide]");
    if (decideBtn) {
      const decision = decideBtn.dataset.cv5Decide;    // "approved" | "rejected"
      const candidateId = Number(decideBtn.dataset.cv5CandidateId);
      if (!candidateId || !decision) return;
      decideBtn.disabled = true;
      try {
        await decideWritingTrainingCandidateApi(candidateId, decision);
        // Remove from local cache and re-render.
        rulesProposedCache = rulesProposedCache.filter((c) => c.id !== candidateId);
        _renderRulesPanel();
        rulesPanel.querySelector(".cv5-rules-panel-close")?.addEventListener("click", closeRulesPanel);
        callbacks.onStatus?.(decision === "approved" ? "Rule approved and added to your voice profile." : "Rule rejected.");
        // Refresh the badge count.
        if (rulesBadgeEl) {
          const n = rulesProposedCache.length;
          if (n > 0) { rulesBadgeEl.textContent = String(n); rulesBadgeEl.removeAttribute("hidden"); }
          else rulesBadgeEl.setAttribute("hidden", "");
        }
      } catch (err) {
        console.error("[composer-v5] decide training candidate failed:", err);
        callbacks.onError?.(err.message || "Failed to record decision.");
        decideBtn.disabled = false;
      }
      return;
    }

    const submitBtn = e.target.closest('[data-cv5="rules-propose-submit"]');
    if (submitBtn) {
      const input = rulesPanel.querySelector('[data-cv5="rules-propose-input"]');
      const text = (input?.value || "").trim();
      if (text.length < 10) {
        callbacks.onError?.("Proposed rule must be at least 10 characters.");
        return;
      }
      submitBtn.disabled = true;
      try {
        await createWritingTrainingCandidateApi({
          proposedText: text,
          candidateType: "rule",
          draftId: currentDraftId || undefined,
        });
        if (input) input.value = "";
        callbacks.onStatus?.("Rule proposed. It will appear in the list above.");
        // Refresh from server to pick up the new candidate.
        await _refreshProposedCandidates();
      } catch (err) {
        console.error("[composer-v5] create training candidate failed:", err);
        callbacks.onError?.(err.message || "Failed to propose rule.");
      } finally {
        submitBtn.disabled = false;
      }
      return;
    }
  });

  if (rulesBtnEl) {
    rulesBtnEl.addEventListener("click", (e) => {
      e.stopPropagation();
      openRulesPanel();
    });
  }

  // Close rules panel on outside click.
  function handleRulesPanelOutsideClick(e) {
    if (!rulesPanelOpen) return;
    if (!rulesPanel.contains(e.target) && e.target !== rulesBtnEl && !rulesBtnEl?.contains(e.target)) {
      closeRulesPanel();
    }
  }
  document.addEventListener("click", handleRulesPanelOutsideClick);

  // Close rules panel on Escape.
  function handleRulesPanelEscape(e) {
    if (e.key === "Escape" && rulesPanelOpen) closeRulesPanel();
  }
  document.addEventListener("keydown", handleRulesPanelEscape);

  // Fetch initial badge count non-blocking on mount.
  void _refreshProposedCandidates();

  // ── Memory lightbox (view + edit rules / sources / examples) ──────────────
  //
  // Opened by clicking the "Memory" button in the top action bar.
  // Full-screen modal overlay with tabbed view: Rules | Sources | Examples.
  // Each item shows its text/content with an Edit button that reveals an
  // inline textarea + Save button, wired to the correct PATCH endpoint.

  const memoryBtnEl = rootEl.querySelector('[data-cv5="memory-btn"]');

  // Full-screen lightbox: outer = backdrop overlay, inner card = .cv5-memory-lightbox-card
  const memoryPanel = document.createElement("div");
  memoryPanel.className = "cv5-memory-overlay";
  memoryPanel.setAttribute("role", "dialog");
  memoryPanel.setAttribute("aria-modal", "true");
  memoryPanel.setAttribute("aria-label", "Voice memory");
  memoryPanel.style.display = "none";
  document.body.appendChild(memoryPanel);

  let memoryPanelOpen = false;
  let memoryActiveTab = "rules"; // "rules" | "sources" | "examples"
  let memoryRules = [];
  let memorySources = [];
  let memoryExamples = [];
  let memoryLoaded = false;

  function _renderMemoryItem(item, type) {
    // item has fields that differ per type:
    //   rules:   body, title, ruleType (camelCase from backend)
    //   sources: normalizedContent (compose engine uses this), title, sourceType
    //   examples: body, title, exampleType
    const itemId = item.id;
    const rawBody = type === "sources"
      ? (item.normalizedContent || item.normalized_content || item.originalContent || item.original_content || "")
      : (item.body || item.content || "");
    const title = item.title || item.name || item.label || "";
    const ruleType = item.ruleType || item.rule_type || item.exampleType || item.example_type || item.sourceType || item.source_type || item.type || "";
    const metaLabel = [ruleType, title].filter(Boolean).join(" · ");
    return `
      <div class="cv5-memory-item" data-cv5-memory-id="${esc(String(itemId))}" data-cv5-memory-type="${esc(type)}">
        <div class="cv5-memory-item-body-wrap">
          ${metaLabel ? `<div class="cv5-memory-item-meta">${esc(metaLabel)}</div>` : ""}
          <div class="cv5-memory-item-body" data-cv5-memory-display="${esc(String(itemId))}">${esc(rawBody)}</div>
          <textarea
            class="cv5-memory-item-edit-input"
            data-cv5-memory-edit-input="${esc(String(itemId))}"
            rows="3"
            style="display:none"
            aria-label="Edit ${esc(type)} text"
          >${esc(rawBody)}</textarea>
        </div>
        <div class="cv5-memory-item-actions">
          <button type="button" class="cv5-memory-edit-btn" data-cv5-memory-edit="${esc(String(itemId))}" title="Edit">Edit</button>
          <button type="button" class="cv5-memory-save-btn" data-cv5-memory-save="${esc(String(itemId))}" style="display:none" title="Save changes">Save</button>
          <button type="button" class="cv5-memory-cancel-btn" data-cv5-memory-cancel="${esc(String(itemId))}" style="display:none" title="Cancel">Cancel</button>
          ${type === "rules" ? `<button type="button" class="cv5-memory-delete-btn" data-cv5-memory-delete="${esc(String(itemId))}" title="Delete this rule permanently">Delete</button>` : ""}
        </div>
      </div>
    `;
  }

  function _renderMemoryPanel() {
    const tabs = ["rules", "sources", "examples"];
    const tabHtml = tabs.map((t) => `
      <button type="button" class="cv5-memory-tab ${memoryActiveTab === t ? "is-active" : ""}" data-cv5-memory-tab="${t}">
        ${t.charAt(0).toUpperCase() + t.slice(1)}
      </button>
    `).join("");

    let bodyHtml = "";
    if (!memoryLoaded) {
      bodyHtml = `<div class="cv5-memory-loading">Loading…</div>`;
    } else {
      const items = memoryActiveTab === "rules" ? memoryRules
        : memoryActiveTab === "sources" ? memorySources
        : memoryExamples;
      if (items.length === 0) {
        bodyHtml = `<div class="cv5-memory-empty">No ${memoryActiveTab} yet.</div>`;
      } else {
        bodyHtml = items.map((item) => _renderMemoryItem(item, memoryActiveTab)).join("");
      }
    }

    memoryPanel.innerHTML = `
      <div class="cv5-memory-lightbox-card">
        <div class="cv5-memory-panel-hdr">
          <span class="cv5-memory-panel-title">Voice Memory</span>
          <button type="button" class="cv5-memory-panel-close" aria-label="Back to draft">&#8592; Back to draft</button>
        </div>
        <div class="cv5-memory-tabs">${tabHtml}</div>
        <div class="cv5-memory-body">${bodyHtml}</div>
      </div>
    `;
  }

  async function _loadMemoryData() {
    memoryLoaded = false;
    _renderMemoryPanel();
    try {
      const [rules, sources, examples] = await Promise.all([
        listWritingRulesApi(),
        listWritingSourcesApi(),
        listWritingExamplesApi(),
      ]);
      memoryRules = Array.isArray(rules) ? rules : [];
      memorySources = Array.isArray(sources) ? sources : [];
      memoryExamples = Array.isArray(examples) ? examples : [];
      memoryLoaded = true;
    } catch (err) {
      console.error("[composer-v5] load memory data failed:", err);
      callbacks.onError?.("Failed to load voice memory.");
      memoryLoaded = true; // render empty state
    }
    if (memoryPanelOpen) {
      _renderMemoryPanel();
      _wireMemoryPanelHandlers();
    }
  }

  function _wireMemoryPanelHandlers() {
    memoryPanel.querySelector(".cv5-memory-panel-close")?.addEventListener("click", closeMemoryPanel);
  }

  function openMemoryPanel() {
    if (memoryPanelOpen) { closeMemoryPanel(); return; }
    memoryPanelOpen = true;
    memoryPanel.style.display = "flex";
    _renderMemoryPanel();
    _wireMemoryPanelHandlers();
    // Load data from server (non-blocking).
    void _loadMemoryData();
  }

  function closeMemoryPanel() {
    memoryPanelOpen = false;
    memoryPanel.style.display = "none";
  }

  // Delegated click handler for the memory panel (tabs + edit/save/cancel).
  memoryPanel.addEventListener("click", async (e) => {
    // ── Backdrop click: clicking the overlay itself (not the card) dismisses ─
    if (e.target === memoryPanel) { closeMemoryPanel(); return; }

    // ── Tab switch ───────────────────────────────────────────────────────────
    const tabBtn = e.target.closest("[data-cv5-memory-tab]");
    if (tabBtn) {
      memoryActiveTab = tabBtn.dataset.cv5MemoryTab;
      _renderMemoryPanel();
      _wireMemoryPanelHandlers();
      return;
    }

    // ── Edit button ──────────────────────────────────────────────────────────
    const editBtn = e.target.closest("[data-cv5-memory-edit]");
    if (editBtn) {
      const itemId = editBtn.dataset.cv5MemoryEdit;
      const displayEl = memoryPanel.querySelector(`[data-cv5-memory-display="${CSS.escape(itemId)}"]`);
      const inputEl   = memoryPanel.querySelector(`[data-cv5-memory-edit-input="${CSS.escape(itemId)}"]`);
      const saveBtn   = memoryPanel.querySelector(`[data-cv5-memory-save="${CSS.escape(itemId)}"]`);
      const cancelBtn = memoryPanel.querySelector(`[data-cv5-memory-cancel="${CSS.escape(itemId)}"]`);
      if (displayEl && inputEl) {
        displayEl.style.display = "none";
        inputEl.style.display = "block";
        inputEl.focus();
      }
      editBtn.style.display = "none";
      if (saveBtn)   saveBtn.style.display = "inline-flex";
      if (cancelBtn) cancelBtn.style.display = "inline-flex";
      return;
    }

    // ── Cancel button ────────────────────────────────────────────────────────
    const cancelBtn = e.target.closest("[data-cv5-memory-cancel]");
    if (cancelBtn) {
      const itemId = cancelBtn.dataset.cv5MemoryCancel;
      const displayEl = memoryPanel.querySelector(`[data-cv5-memory-display="${CSS.escape(itemId)}"]`);
      const inputEl   = memoryPanel.querySelector(`[data-cv5-memory-edit-input="${CSS.escape(itemId)}"]`);
      const editBtn_  = memoryPanel.querySelector(`[data-cv5-memory-edit="${CSS.escape(itemId)}"]`);
      const saveBtn   = memoryPanel.querySelector(`[data-cv5-memory-save="${CSS.escape(itemId)}"]`);
      if (displayEl && inputEl) {
        // Restore original text.
        const items = memoryActiveTab === "rules" ? memoryRules
          : memoryActiveTab === "sources" ? memorySources
          : memoryExamples;
        const original = items.find((it) => String(it.id) === itemId);
        if (original && inputEl) {
          inputEl.value = memoryActiveTab === "sources"
            ? (original.normalizedContent || original.normalized_content || original.originalContent || original.original_content || "")
            : (original.body || original.content || "");
        }
        displayEl.style.display = "block";
        inputEl.style.display = "none";
      }
      if (editBtn_)  editBtn_.style.display = "inline-flex";
      cancelBtn.style.display = "none";
      if (saveBtn) saveBtn.style.display = "none";
      return;
    }

    // ── Save button ──────────────────────────────────────────────────────────
    const saveBtn = e.target.closest("[data-cv5-memory-save]");
    if (saveBtn) {
      const itemId = Number(saveBtn.dataset.cv5MemorySave);
      const inputEl = memoryPanel.querySelector(`[data-cv5-memory-edit-input="${CSS.escape(String(itemId))}"]`);
      const newText = (inputEl?.value || "").trim();
      if (!newText) {
        callbacks.onError?.("Text cannot be empty.");
        return;
      }
      saveBtn.disabled = true;
      try {
        if (memoryActiveTab === "rules") {
          const updated = await updateWritingRuleApi(itemId, { body: newText });
          // Update local cache.
          const idx = memoryRules.findIndex((r) => r.id === itemId);
          if (idx >= 0) memoryRules[idx] = updated;
        } else if (memoryActiveTab === "sources") {
          // Sources have normalizedContent (used by the compose engine) and
          // originalContent (immutable provenance).  Update normalizedContent
          // so Amira sees the edits; leave originalContent as-is.
          const updated = await updateWritingSourceApi(itemId, { normalizedContent: newText, originalContent: newText });
          const idx = memorySources.findIndex((s) => s.id === itemId);
          if (idx >= 0) memorySources[idx] = updated;
        } else {
          // Examples: WritingExampleUpdate.body (not .content)
          const updated = await updateWritingExampleApi(itemId, { body: newText });
          const idx = memoryExamples.findIndex((ex) => ex.id === itemId);
          if (idx >= 0) memoryExamples[idx] = updated;
        }
        // Re-render the panel so the saved value shows in display mode.
        _renderMemoryPanel();
        _wireMemoryPanelHandlers();
        callbacks.onStatus?.("Memory updated.");
      } catch (err) {
        console.error("[composer-v5] update memory item failed:", err);
        callbacks.onError?.(err.message || "Failed to save.");
        saveBtn.disabled = false;
      }
      return;
    }

    // ── Delete button (rules only; PERMANENT hard delete) ──────────────────────
    const deleteBtn = e.target.closest("[data-cv5-memory-delete]");
    if (deleteBtn) {
      const itemId = Number(deleteBtn.dataset.cv5MemoryDelete);
      const rule = memoryRules.find((r) => r.id === itemId);
      const label = String(rule?.title || rule?.body || "this rule").slice(0, 80);
      // Irreversible (unlike drafts, which are soft-deleted), so confirm first.
      if (!window.confirm(`Delete this rule permanently?\n\n"${label}"\n\nThis cannot be undone.`)) return;
      deleteBtn.disabled = true;
      try {
        await deleteWritingRuleApi(itemId);
        const idx = memoryRules.findIndex((r) => r.id === itemId);
        if (idx >= 0) memoryRules.splice(idx, 1);
        _renderMemoryPanel();
        _wireMemoryPanelHandlers();
        callbacks.onStatus?.("Rule deleted.");
      } catch (err) {
        console.error("[composer-v5] delete rule failed:", err);
        callbacks.onError?.(err.message || "Failed to delete rule.");
        deleteBtn.disabled = false;
      }
      return;
    }
  });

  if (memoryBtnEl) {
    memoryBtnEl.addEventListener("click", (e) => {
      e.stopPropagation();
      openMemoryPanel();
    });
  }

  // Note: backdrop click is handled inside the delegated click handler above
  // (e.target === memoryPanel → closeMemoryPanel).

  // Close memory panel on Escape.
  function handleMemoryPanelEscape(e) {
    if (e.key === "Escape" && memoryPanelOpen) closeMemoryPanel();
  }
  document.addEventListener("keydown", handleMemoryPanelEscape);

  // ── Public handle ──────────────────────────────────────────────────────────
  return {
    destroy() {
      destroyed = true;
      if (autosaveTimer) clearTimeout(autosaveTimer);
      try { view.destroy(); } catch (_) { /* noop */ }
      // Stage-2 cleanup: remove floating toolbar + popover and event listeners.
      document.removeEventListener("mousedown", handleOutsidePointerDown, true);
      document.removeEventListener("click", handleOutsidePointerDown, true);
      try { document.body.removeChild(selToolbar); } catch (_) { /* noop */ }
      try { document.body.removeChild(rewritePopover); } catch (_) { /* noop */ }
      // Stage-4 cleanup: scan timer + claim popover.
      if (scanTimer) { clearTimeout(scanTimer); scanTimer = null; }
      try { document.body.removeChild(claimPopover); } catch (_) { /* noop */ }
      // Stage-3 cleanup: drafts-picker listeners.
      document.removeEventListener("click", handlePickerOutsideClick);
      document.removeEventListener("keydown", handlePickerEscape);
      // Stage-5 cleanup: pagination overlay + resize observer.
      unmountPagination();
      if (paginationRaf) { cancelAnimationFrame(paginationRaf); paginationRaf = null; }
      // Stage-8 cleanup: actions menu listeners.
      document.removeEventListener("click", handleActionsOutsideClick);
      document.removeEventListener("keydown", handleActionsEscape);
      // Stage-7 cleanup: gdoc menu listeners.
      document.removeEventListener("click", handleGdocOutsideClick);
      document.removeEventListener("keydown", handleGdocEscape);
      // Stage-6 cleanup: comment composer popover + timers.
      if (commentsRefreshTimer) { clearTimeout(commentsRefreshTimer); commentsRefreshTimer = null; }
      if (commentsReflowTimer)  { clearTimeout(commentsReflowTimer);  commentsReflowTimer  = null; }
      if (commentsReflowRaf)    { cancelAnimationFrame(commentsReflowRaf); commentsReflowRaf = null; }
      document.removeEventListener("click", handleCommentComposerOutsideClick, true);
      _removeMentionDropdown();
      try { document.body.removeChild(commentComposerEl); } catch (_) { /* noop */ }
      // Apply-to-document + deliverable preview cleanup.
      if (chatThreadEl) chatThreadEl.removeEventListener("click", handleChatThreadClick);
      document.removeEventListener("click", handleDeliverablePreviewOutsideClick, true);
      try { document.body.removeChild(deliverablePreviewPopover); } catch (_) { /* noop */ }
      // Rules panel cleanup.
      document.removeEventListener("click", handleRulesPanelOutsideClick);
      document.removeEventListener("keydown", handleRulesPanelEscape);
      try { document.body.removeChild(rulesPanel); } catch (_) { /* noop */ }
      // Memory lightbox cleanup.
      document.removeEventListener("keydown", handleMemoryPanelEscape);
      try { document.body.removeChild(memoryPanel); } catch (_) { /* noop */ }
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
        <span class="cv5-hdr-title" data-cv5="draft-title" title="Click to rename" tabindex="0" role="button" aria-label="Draft name: ${esc(titleText)} — click to rename">${esc(titleText)}</span>
        <span class="cv5-hdr-status">${esc(status)}</span>
        <div class="cv5-hdr-spacer"></div>
        <button type="button" class="cv5-hdr-ind" data-cv5="rules-btn" title="Proposed rules &amp; manual propose">Rules<span class="cv5-rules-badge" data-cv5="rules-badge" hidden></span></button>
        <button type="button" class="cv5-hdr-ind" data-cv5="memory-btn" title="View &amp; edit voice memory (rules, sources, examples)">Memory</button>
        <button type="button" class="cv5-hdr-ind" data-cv5="comments-toggle" title="Show / hide comments rail">💬 Comments</button>
        <button type="button" class="cv5-hdr-ind" data-cv5="open-history" title="Version history">⟲ History</button>
        <div class="cv5-gdoc-wrap" data-cv5="gdoc-wrap">
          <button type="button" class="cv5-hdr-gdoc" data-cv5="gdoc-btn" aria-haspopup="menu" aria-expanded="false" title="Google Docs">⊞ Google Doc</button>
          <div class="cv5-picker-menu cv5-gdoc-menu" data-cv5="gdoc-menu" role="menu" aria-label="Google Docs"></div>
        </div>
        <div class="cv5-actions-wrap" data-cv5="actions-wrap">
          <button type="button" class="cv5-hdr-ind" data-cv5="actions-btn" aria-haspopup="menu" aria-expanded="false" title="More actions">⋯</button>
          <div class="cv5-picker-menu cv5-actions-menu" data-cv5="actions-menu" role="menu" aria-label="Actions">
            <button type="button" class="cv5-picker-menu-row" data-cv5-action="save-as-template" role="menuitem">
              <span aria-hidden="true">📋</span>
              <span>Save as template</span>
            </button>
            <button type="button" class="cv5-picker-menu-row" data-cv5-action="repurpose" role="menuitem">
              <span aria-hidden="true">♻</span>
              <span>Repurpose</span>
            </button>
            <button type="button" class="cv5-picker-menu-row" data-cv5-action="brand-check" role="menuitem">
              <span aria-hidden="true">✓</span>
              <span>Brand + readability check</span>
            </button>
            <button type="button" class="cv5-picker-menu-row" data-cv5-action="approve-for-campaign" role="menuitem" hidden>
              <span aria-hidden="true">✅</span>
              <span>Approve for campaign</span>
            </button>
            <button type="button" class="cv5-picker-menu-row" data-cv5-action="attach-to-campaign" role="menuitem">
              <span aria-hidden="true">🔗</span>
              <span>Attach to campaign</span>
            </button>
          </div>
        </div>
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
            <div class="cv5-chat-tools-row" style="display:none" aria-hidden="true"></div>
          </div>
        </section>

        <section class="cv5-doc-col" aria-label="Document">
          <div class="cv5-doc-scroll">
            <div class="cv5-doc-grid" data-cv5="doc-grid">
              <div class="cv5-paper" data-cv5="paper">
                <div data-cv5="editor"></div>
              </div>
              <aside class="cv5-margin-col" aria-label="Comments">
                <div class="cv5-margin-empty">Select text and click 💬 Comment to add a comment.</div>
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
        <div class="cv5-picker-folder-wrap">
          <button type="button" class="cv5-picker-row cv5-picker-folder" data-cv5-folder-key="${esc(key)}" aria-expanded="${expanded}">
            <span class="cv5-picker-caret" aria-hidden="true">${caret}</span>
            <span aria-hidden="true">📁</span>
            <span class="cv5-picker-folder-name">${esc(f.name || "Untitled folder")}</span>
            <span class="cv5-picker-row-meta">${count > 0 ? count : ""}</span>
          </button>
          <button type="button" class="cv5-picker-folder-rename" data-cv5-rename-folder="${f.id}" data-cv5-folder-name="${esc(f.name || "")}" title="Rename folder" aria-label="Rename folder ${esc(f.name || "Untitled folder")}">Rename</button>
          <button type="button" class="cv5-picker-folder-del" data-cv5-delete-folder="${f.id}" title="Delete folder" aria-label="Delete folder ${esc(f.name || "Untitled folder")}">🗑</button>
        </div>
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
