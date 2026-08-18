# Build brief — Composer polish pass: selection toolbar, full-doc preview, rewrite intent

**Agent:** terminal (composer FE — owns `composer-v5.js`/`.css`). **Branch:** `worker/composer-polish-pass`
off **current `main`** (pull first — it has the fence-leak + sans-serif fixes at 271bec0). **Own git worktree,
cd inside it, own test DB `artemis_test_polish`.** **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md` + the current `composer-v5.js`. Three issues from Jon's live use. All
lossless, no backend schema changes expected.

## Issue 1 — selection toolbar doesn't appear on a full-paragraph selection
**Repro (Jon):** highlighting an entire paragraph does NOT pop the floating rewrite toolbar; you have to
select *less* for it to appear. Likely the toolbar position/visibility is computed from the selection
geometry (e.g. `coordsAtPos`/getBoundingClientRect of the range) and a full-block selection produces a
degenerate/zero or off-screen rect, so it's suppressed or rendered off-screen.
**Fix:** the toolbar must appear reliably for ANY non-empty selection, including a whole paragraph or
multiple paragraphs. Anchor it to the selection (e.g. the end-of-selection caret coords, or clamp to the
viewport) so it's always visible. Verify with: single word, partial sentence, ONE full paragraph, and TWO
paragraphs selected — toolbar appears and is on-screen in all four.

## Issue 2 — no preview of the proposed copy before "Apply to document"
Today the chat-compose deliverable is hidden; the user clicks **Apply to document** blind (especially for a
"write me a 1-page doc" request — they can't read it first). 
**Fix:** when an assistant turn has a deliverable, show the **proposed copy in a readable preview** before
applying — expandable to read the whole thing — with **Apply** and **Discard** actions. Match the existing
rewrite-span accept/reject popover styling (the "PROPOSED REWRITE / BEFORE-AFTER" affordance) for visual
consistency — reuse those classes/patterns; don't invent a new look. Apply still uses `replaceEditorContent`
+ autosave (lossless, reversible via the existing Undo apply). Discard just dismisses the preview, leaving
the document untouched. (Design note: Jon is Creative Director on this — keep it consistent with the existing
popover; no bold new styling.)

## Issue 3 — let the user say what's wrong with a section (not just "rewrite")
There's already a custom-instruction path on the selection toolbar, but it's buried; today rewriting feels
like "rolling the dice." 
**Fix:** surface a clear, always-visible **"What should change?"** text input in the rewrite flow (e.g. on
the selection toolbar or its expanded state) so the user types the specific critique ("too jargony", "make
it shorter", "wrong audience") and that instruction drives `rewrite-span` (it already accepts an
`instruction`). The quick presets (Shorten/Lengthen/Tone/Make-on-brand) stay; the free-text intent is the
headline. Keep it discoverable, not hidden behind a submenu.

## Acceptance (verify the EFFECT — browser, with screenshots)
- Toolbar: appears on a full-paragraph AND two-paragraph selection (the bug), still works on small selections.
- Preview: ask for a multi-paragraph doc → the proposed copy is readable in a preview BEFORE applying; Apply
  updates the doc; Discard leaves it unchanged; Undo still reverts an applied change. NO fence markers appear
  anywhere (regression guard — main already fixed the leak; confirm it stays clean).
- Rewrite intent: a "What should change?" input is visible in the rewrite flow; typing a critique produces a
  rewrite that reflects it; the AFTER text is clean (no ```artemis-draft``` markers).
- No console errors; claim flags / comments / pagination / autosave / Disregard still work. `./scripts/check.sh`
  for any touched Python (note PRE-EXISTING failures separately — there are known unrelated ones).

## Constraints
Lossless (Apply reversible; nothing deleted). Reuse the existing selection-toolbar, rewrite-span accept/reject
popover, `replaceEditorContent`, and `composeWritingDraftApi`/`rewriteSpanApi` — don't fork. The deliverable
preview must NOT re-introduce fence markers into the document. Isolated worktree + own test DB
(`artemis_test_polish`). **Do NOT merge** — report branch + SHA + worktree + browser screenshots for each of
the three. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies +
merges.
