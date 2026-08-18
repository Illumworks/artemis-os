# Build brief — Composer chat: "Apply to document" (B2)

**Agent:** terminal (composer FE — owns `composer-v5.js`; small coordinated backend contract change in the
compose path). **Branch:** `worker/composer-apply-to-document` off `main`. **Own git worktree, cwd inside.
Own test DB** (`artemis_test_applydoc`). **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md`
+ the current `composer-v5.js` (the chat-compose handler that imports `composeWritingDraftApi`) and
`docs/COMPOSER-REBUILD-PLAN.md`.

**SEQUENCING — FE on `composer-v5.js`: this serializes AFTER `worker/composer-picker-fixes` and
`worker/claim-precision-disregard` land on main (all three touch `composer-v5.js`).** Branch off main only
once those are merged; confirm with Lead before starting. The backend part can be drafted earlier but
verify against current main.

## Why
Today when Jon asks the chat to change the copy ("delete the first three lines", "make the opening punchier"),
the AI returns the revised copy **into the chat thread only** — it never updates the document on the right.
He wants the revised copy to land in the editable document, with him in control (AI proposes → he applies).

## The interaction with the natural-tone work (read first)
The `composer-chat-natural-tone` brief (B1) makes replies conversational. That means `responseText` will mix
a conversational message ("Sure — here's a tighter opening:") with the actual deliverable copy. We must NOT
dump the conversational chatter into the document. So this brief introduces a **clean split** between the
chat message and the apply-able deliverable.

## Backend contract change — split chat message from deliverable copy
The compose path must return the deliverable copy **separately** from the conversational message so the FE
can apply only the copy.

**CRITICAL (repo lesson):** if you change the shape the LLM must emit, you MUST update the **prompt template**
in the same change, or the model keeps emitting the old shape and the new field silently empties. The prompt
template lives in `compose_engine.py` (`user_parts`, ~line 435-443).

Design (robust, demo-safe — prefer a forgiving delimiter over strict JSON):
- Instruct the model (in the compose prompt) to, **when it produces revised/!new draft copy**, wrap that copy
  in a fenced block: ` ```artemis-draft ` … ` ``` ` — and keep any conversational lead-in OUTSIDE the fence.
  When the turn is just a question/answer with no new copy, emit NO fence.
- Backend (`routes/writing_studio.py` compose handler): parse the response. Return added fields alongside the
  existing `responseText`: `chatMessage` (response with the fenced block removed — what shows in chat) and
  `deliverable` (the copy inside the fence, or `null` if none). Keep `responseText` as-is for backward compat.
  Persist the assistant thread message as `chatMessage` (so history reads clean), but do NOT lose the
  deliverable — it's returned for the FE to apply (and the user's Apply action autosaves it into the draft,
  which is the lossless record).
- **Resilient fallback:** if no fence is present but the reply is clearly a full rewrite, `deliverable` may be
  null — the FE then falls back (below). Never error or empty the chat because a fence was missing.

## FE — "Apply to document" (composer-v5.js)
- The chat thread shows `chatMessage` (clean conversational text).
- When a turn has a `deliverable`, render an **"Apply to document"** action on that assistant message
  (mirror the highlight→AI-edit accept/reject affordance already in the file — reuse its styling/pattern).
- **Apply** → replace the editor body with the deliverable via the existing `replaceEditorContent`
  (lossless — autosave persists `live_content`; the user can still Save-version for a checkpoint). Offer a
  **revert/undo** (or reject-before-apply preview) consistent with the existing rewrite-span accept/reject so
  it's never a silent destructive overwrite.
- **Fallback** when `deliverable` is null but the user clearly got rewritten copy: still offer "Apply to
  document" on the message applying the message text (current behavior + a button) — so worst case Jon is
  never worse off than today.
- Reuse the existing compose API helper (`composeWritingDraftApi`) — extend it to surface the new fields;
  don't fork the call.

## Acceptance (verify the EFFECT — browser)
- Ask the chat "delete the first three lines and tighten the opening." The chat shows a natural message; an
  "Apply to document" button appears; clicking it updates the DOCUMENT on the right (not just the chat).
  Prove the editor content actually changed and autosaved (reload → change persists as live_content; versions
  untouched until Save-version). Screenshot before/after.
- Ask a pure question ("what's the proof pack for the growth stat?") → conversational answer, NO Apply button,
  document unchanged.
- Apply is reversible (undo/revert or reject-preview) — no silent destructive overwrite; lossless.
- Claim flags / comments / pagination / autosave still work; no console errors. `./scripts/check.sh` for
  touched Python (note PRE-EXISTING separately).

## Constraints
Lossless (AI proposes → user applies; apply is reversible; versions preserved). Update the prompt template in
the SAME change as the response-shape change (no silent-empty). Reuse `replaceEditorContent`, the
rewrite-span accept/reject pattern, and `composeWritingDraftApi`; don't fork. No migration expected (live
content already persists). Isolated worktree + own test DB. **Do NOT merge** — report branch + SHA + worktree
+ the browser before/after (apply-updates-the-doc) + the no-fence/question fallback proof. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
