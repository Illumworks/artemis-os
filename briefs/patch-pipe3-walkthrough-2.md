# Patch — PIPE3 Walkthrough 2 (5 small fixes)

**Owner:** Codex (paste-ready, mechanical)
**Branch:** `codex/patch-pipe3-walkthrough-2`
**LOC budget:** ~140 (honest overrun OK to ~200)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE3 + PIPE3 patch + cron presets all merged (current lead at `976b21b` or later).

## Why this brief exists

Jon's second walkthrough surfaced 4 functional bugs + 1 small spec addition. All small, all mechanical-or-near-mechanical. One consolidated patch.

## Fixes

### Bug #1 — Pipeline delete kebab dropdown won't close

**Symptom:** Click kebab → dropdown opens. Click Archive (or any item, or outside the dropdown) → action fires but dropdown stays open. Requires page refresh to close.

**Diagnosis:** missing outside-click handler OR missing close-on-action handler.

**Fix:**
- Add an outside-click listener that closes the dropdown when the user clicks anywhere not inside it
- AND close the dropdown immediately after any action (Archive / Restore / Permanently delete) is clicked, before the action's confirmation dialog opens

```javascript
// Pattern (adjust to existing class names):
document.addEventListener("click", (e) => {
  if (!e.target.closest(".pipeline-kebab-menu") && !e.target.closest(".pipeline-kebab-trigger")) {
    closeAllKebabMenus();
  }
});

// In each action handler:
function handleArchive(pipelineId) {
  closeAllKebabMenus();
  showConfirmDialog(...);
}
```

### Bug #2 — Cron preset "title" stuck on "Every 4 hours" when value changes

**Symptom:** In trigger_scheduled form, the human-readable preview text BELOW the picker updates correctly when you change values (e.g., 4 → 5 hours). But the **larger title/header at the top** stays "Every 4 hours."

**Diagnosis:** The form has TWO display points for the human-readable summary — the small preview below the picker (correctly reactive via `_sync`) and a larger title/header at the top (NOT bound to `_sync`).

**Fix:** find the header element (likely the form's "Schedule" or "Every X" big label) and bind it to `_sync` like the other reactive parts. May share the same `humanizeCron` function. Single-line addition in the `_sync` function:

```javascript
function _sync() {
  // ... existing code ...
  if (titleEl) titleEl.textContent = humanizeCron(currentCron);
  // ... rest
}
```

If the title isn't in the form itself but on the canvas node card (the node's `label` field) — that's a different surface and should ALSO update. Check both. If the node-card label is showing stale data, the node card needs to re-read `node.config` when the form saves.

### Bug #3 — Cron round-trip puts Weekly pattern back into Custom mode

**Symptom:** User switched from Hourly → Weekly → Custom → saved. On reopen, form defaults to Custom mode instead of Weekly (or whichever was the most specific match for the saved cron string).

**Diagnosis:** Two possibilities:
1. The parse-and-match algorithm in `cron-utils.js` isn't recognizing the Weekly pattern (e.g., `0 9 * * 1-5`)
2. The form is persisting the user's last-selected mode as preference and respecting it on reopen, overriding the natural match

**Fix:**
- The brief was explicit: "match against preset patterns in priority order (Every N → Daily → Weekly → Monthly → Custom)." Saved cron is the source of truth; reopen picks the most specific preset that matches.
- Identify whether `cron-utils.js`'s `matchPreset(cronString)` function exists and is being called on reopen
- If it exists: trace why the Weekly pattern falls through. Common issues: regex too strict, doesn't recognize `1-5` shorthand for Mon-Fri, doesn't recognize `*/N` for "Every N"
- If user's last-selected mode is being persisted: REMOVE that persistence — saved cron is the source of truth, not user's preference

**Verify:** save in Weekly mode → reload → opens in Weekly mode with Mon-Fri checked.

### Bug #4 — human_gate approvers: can't add people beyond hardcoded 3

**Symptom:** Approvers multi-select shows Josh, Angela, Jon. Jon wants to add MORE people. Brief specified "Free-text fallback (type email + Enter)" but it doesn't appear to be wired.

**Fix:** add the free-text input below the hardcoded list:

```javascript
const freetextInput = document.createElement("input");
freetextInput.placeholder = "Add another approver email…";
freetextInput.addEventListener("keypress", (e) => {
  if (e.key === "Enter") {
    const email = e.target.value.trim();
    if (email && isValidEmail(email)) {
      addApprover(email);
      e.target.value = "";
    }
  }
});
```

Added emails show as a chip in the selected-approvers list with an × to remove. Multi-select can have any combination of hardcoded + freetext.

### New field — `human_gate` escalation_to

**Why:** PIPE4 (execution engine) will deliver approval asks via Slack DM. When timeout passes AND `on_timeout = "escalate"`, the system needs to know WHO to escalate to. Per Jon's call: secondary approver is configured **per-gate at gate creation time**, not pre-configured globally.

**Add to the human_gate form:** below the "On timeout" dropdown, when `on_timeout = "escalate"` is selected, show a conditional field:

```
Escalate to: [searchable approver picker — same UX as the primary approvers list]
```

Persists to `config.escalation_to` (array of email strings). Hidden when `on_timeout` is NOT `escalate`.

When form saves, if `on_timeout = "escalate"` and `escalation_to` is empty, show inline validation error: "Specify at least one escalation approver."

## Out of scope (banked for polish pass)

- Cost cap (?) tooltip gets cut off by browser border — positioning issue, bank for polish pass
- on_timeout inline explanations feel like visual noise — bank for polish pass (consider hiding behind hover/(?) icon)
- `/api/google/overview` 404 in console — bank for next dead-endpoints sweep
- Approval delivery mechanism (Slack DM wire-up) — PIPE4 territory, not this brief

## Invariants

1. **Existing PIPE3 + PIPE3 patch behaviors unchanged.** No regression in agent picker, model dropdown, palette drag, archive flow.
2. **escalation_to is optional unless on_timeout=escalate.** Saving with `auto_approve` or `auto_reject` shouldn't require escalation_to.
3. **No new dependencies.**

## Files expected

| File | LOC |
|---|---|
| `public/js/features/pipelines.js` | ~25 delta (kebab close handlers) |
| `public/js/components/node-config-forms/trigger-scheduled-form.js` | ~30 delta (title reactivity binding) |
| `public/js/components/cron-utils.js` | ~30 delta (parse-and-match fix) |
| `public/js/components/node-config-forms/human-gate-form.js` | ~50 delta (freetext approver input + escalation_to conditional field) |
| `tests/unit/frontend/test_pipe3_walkthrough_2.py` (new or appended) | ~30 |

**Total: ~165 LOC.** Cap 200.

## Test plan

1. Click pipeline kebab → click outside → dropdown closes
2. Click pipeline kebab → click Archive → dropdown closes, confirmation dialog opens
3. Trigger_scheduled: change "4" to "5" → title at top updates to "Every 5 hours"
4. Trigger_scheduled: save in Weekly mode → reload → opens in Weekly mode with correct days/time
5. Human_gate: type a new email + Enter → email added to approvers list
6. Human_gate: select on_timeout=escalate → escalation_to field appears; save with empty → validation error; save with email populated → persists
7. Human_gate: select on_timeout=auto_approve → escalation_to field hidden, save succeeds

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- **`node --check`** on every modified JS file before committing (this prevents the third class of bug we've seen this session). Don't ship a .js file you haven't node-checked.
- Browser smoke: no new console errors

## What "done" looks like

1. All 4 bugs fixed; new escalation_to field works.
2. Tests pass.
3. `check.sh` passes within exempt set.

## Report Codex submits

1. `git diff --stat`
2. For Bug #3: paste the `matchPreset` algorithm's flow — why was Weekly falling through to Custom?
3. Screenshot of human_gate with escalation_to field visible when on_timeout=escalate
4. Test pass count
5. Branch
