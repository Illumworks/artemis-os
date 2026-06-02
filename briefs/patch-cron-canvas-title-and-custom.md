# Patch — Cron Canvas Title + Custom Mode Persistence

**Owner:** Codex (paste-ready, mechanical)
**Branch:** `codex/patch-cron-canvas-title-and-custom`
**LOC budget:** ~120 (honest overrun OK to ~180)
**Depends on:** PIPE3 + cron-picker-presets + patch-pipe3-walkthrough-2 all merged.

## Why

Jon's 2026-05-22 walkthrough surfaced two cron-related bugs:

1. **Node card title on canvas doesn't update** when the form saves. Changing "Every 4 Hours" to "Every 5 hours" in the form (via the cron picker) saves the cron string correctly, but the node card on the canvas still shows "Every 4 Hours" as its title until page refresh.

2. **Custom mode doesn't persist on save** properly. User goes Hourly → Weekly → Custom → Save. On reopen, form doesn't open in the saved mode.

These are NOT the same bugs Codex 2 fixed last patch:
- The previous bug #2 was the **form's internal preview** ("title at top" referring to the form drawer header). That's fixed and reactive.
- This bug is the **node card on the canvas** — entirely different surface; the card reads from canvas state, not the form's reactive bindings.

The previous bug #3 verified that `parseCron("0 9 * * 1-5") → weekly` round-trips. This new bug is the explicit-Custom case the previous test didn't cover.

## Scope

### Bug #1 — Canvas node card title doesn't update on form save

**Diagnosis:** The node card's title displays `node.label`. When the trigger_scheduled form saves, it updates `node.config.cron` but doesn't update `node.label`. Refreshing rebuilds the card from JSONB state which auto-derives the label, but in-session edits don't trigger the re-derive.

**Fix:**

1. In `trigger-scheduled-form.js` save handler: when committing changes, ALSO update `node.label` to the humanized cron preview. Something like:
   ```javascript
   function onSave() {
     const cron = compileCronFromPresets(/* current state */);
     node.config.cron = cron;
     node.config.timezone = currentTimezone;
     node.label = humanizeCron(cron);  // <-- ADD THIS
     // ... persist to backend, close drawer
   }
   ```

2. After save, trigger a canvas re-render of THAT specific node so the new label appears immediately (without full page refresh). The canvas already has a `_redrawNode(nodeId)` or equivalent function from PIPE2; call it after save.

3. Same fix applies to ALL form types where the label is derived from config (e.g., agent_invocation node's title shows the agent name — if user changes the agent, the card title should update). Check `agent-invocation-form.js`, `human-gate-form.js`, etc. for similar gaps. Brief lists those as "verify" items, not "must fix" — if they already update correctly, leave them alone.

### Bug #2 — Custom mode doesn't persist on save

**Diagnosis options:**

The brief's original parse-and-match algorithm: open the saved cron string, run through preset matchers in priority order (Every N → Daily → Weekly → Monthly → Custom). The most specific match wins. Custom is the fallback.

This is BY DESIGN — if a user typed `0 9 * * 1-5` in Custom mode, on reopen the form should show Weekly (the most specific match). User can switch back to Custom if they prefer.

**BUT** — Jon's case: user explicitly chose Custom mode, saved, reopened, expected to see Custom. That's a user intent conflict with the design.

Two possible fixes:

**Option A (preserve user intent — recommended):**
- Persist the user's `selected_mode` in `node.config.preferred_mode` JSONB field
- On reopen: if `preferred_mode` is set, open in that mode (use stored value)
- If `preferred_mode` is empty: use the parse-and-match priority algorithm
- This honors the user's explicit choice without abandoning the auto-match for new pipelines

**Option B (always auto-match — current design):**
- Keep parse-and-match algorithm
- User has to always re-pick Custom mode after reopen
- Annoying for power users who EXPLICITLY want Custom

Jon's wording ("instead of custom showing captured it is not defaulting to weekly") suggests he expects the saved mode to be preserved, not auto-matched. **Go with Option A.**

**Fix:**

1. In `trigger-scheduled-form.js`: save the current mode to `node.config.preferred_mode` alongside the cron string
2. On form open: if `node.config.preferred_mode` is set AND valid, open in that mode with state derived from the cron string
3. If `preferred_mode` is missing or invalid: fall back to parse-and-match
4. The mode-selector pills should highlight the active mode based on this priority

### Bug #2 sub-case — Test what Jon actually experienced

Either fix above won't fix the symptom unless we know what Jon actually saw. Reproduce his scenario:
1. Create / open marketing pipeline trigger node (currently `0 */4 * * *`)
2. Switch mode picker: Every N → Weekly → Custom (note: Custom should show raw cron textarea)
3. Save
4. Reload page, open same trigger node
5. Observe which mode the form opens in

If it opens in Custom: this whole bug is bunk; Jon may have been confused.
If it opens in Every N (default) or Weekly: that's the bug to fix per Option A above.

**Worker should reproduce first, then fix.** Don't fix blindly.

### Tests

1. Save trigger node with new cron → node card title updates on canvas without refresh
2. Save in Custom mode → reopen → form opens in Custom mode (per Option A)
3. Save in Weekly mode → reopen → form opens in Weekly mode (per Option A; also tests preserved-mode != fallback)
4. Old pipelines without `preferred_mode` field still open correctly via parse-and-match
5. New field doesn't break export/import (if shipped)

## Out of scope

- Other node types' card-title-update bug (verify, but only fix trigger_scheduled in this brief; if others are broken, log them and Lead scopes a follow-up)
- Refactoring the parse-and-match algorithm
- New cron presets (Daily / Weekly / etc. already shipped)

## Files

| File | LOC |
|---|---|
| `public/js/components/node-config-forms/trigger-scheduled-form.js` | ~50 delta (preferred_mode persistence + label update on save) |
| `public/js/components/pipeline-canvas.js` | ~15 delta (expose _redrawNode helper if not already accessible) |
| `public/js/components/pipeline-node-card.js` | ~5 delta (if needed for label rebinding) |
| Tests | ~40 |

**Total: ~110 LOC.** Cap 180.

## Invariants

- `node.label` stays derived (auto-updates when config changes) but ALSO writable (user can override via inline edit if PIPE2 supports it)
- `preferred_mode` defaults gracefully — old pipelines without it still work via parse-and-match
- node --check on modified JS
- ./scripts/check.sh passes within exempt set
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, paste the new save handler showing label update + preferred_mode persistence, screenshot of node card updating after form save, screenshot of form reopening in saved mode (Custom and Weekly), test pass count, branch.

---

**Lead notes (not for Codex):**
- This is the small UX patch that closes the loop on cron picker UX. After this lands, the cron picker feels finished.
- The "preferred_mode persistence" pattern likely applies to other forms eventually (e.g., conditional form's predicate-builder vs JSONLogic toggle could persist). Bank for later.
- Two open questions for Lead post-Codex: (a) is the node card title fix consistent across other node types? (b) Does the preferred_mode pattern generalize?
