# Patch — PIPE3 Walkthrough Bugs (6 fixes + 2 tooltip clarifications)

**Owner:** Codex (paste-ready, mechanical)
**Branch:** `codex/patch-pipe3-walkthrough-bugs`
**LOC budget:** ~350 (honest overrun OK to ~450)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE3 merged. Independent of pipeline-delete brief and cron-UX brief.

## Why this brief exists

Jon's PIPE3 walkthrough surfaced 6 bugs and 2 UX clarifications. All small, all mechanical-or-near-mechanical. One consolidated patch fixes them in one Codex paste.

## Fixes (in priority order)

### Bug #1 — `agent_invocation` picker JS TypeError

**Symptom:** typing in the agent picker throws:
```
Uncaught TypeError: (a.agent_id ?? a.id ?? "").toLowerCase is not a function
  at agent-invocation-form.js:111
```

**Diagnosis:** the filter logic at `agent-invocation-form.js:111` calls `.toLowerCase()` on `a.agent_id ?? a.id ?? ""`. When `a.agent_id` is a non-string value (number? object? null that's not falsy enough?), `.toLowerCase` doesn't exist.

**Fix:** coerce to string before calling `.toLowerCase()`:
```javascript
const haystack = String(a.agent_id ?? a.id ?? "").toLowerCase();
```

Or use a defensive helper:
```javascript
const safeStr = (v) => typeof v === "string" ? v : String(v ?? "");
const haystack = safeStr(a.agent_id ?? a.id).toLowerCase();
```

Verify: type any character in the agent picker → no console error, results filter correctly.

### Bug #2 — `agent_invocation` model picker should be dropdown (not freeform text)

**Symptom:** when "Provider override" section is expanded, the Provider field is a dropdown but the Model field is a text input. User has to type the model name from memory.

**Fix:** make Model a dropdown filtered by the selected Provider. When Provider changes, Model dropdown re-populates with that provider's models.

- Use the existing provider/model picker component if Codex 2 (agent-provider-invariant-ui brief) created one. Survey first: `grep -rn "PROVIDER_PICKERS\|model-selector\|provider-model" public/js/` to find the existing helper.
- If it exists, reuse it in `agent-invocation-form.js`.
- If it doesn't exist: build minimal version — provider dropdown + model dropdown that filters by `PROVIDER_MODELS[selectedProvider]` map (the static list of available models per provider).
- The freeform agent_id field stays as-is (separate issue — see judgment call note below).

**Tooltip on cost cap field:** add a small `(?)` icon next to "Cost cap" with tooltip text:
> "Stops execution when total LLM cost for this run exceeds this cap. Applies to all provider modes (API, CLI, local). Run is marked partial_complete and the cap-hit reason is logged."

### Bug #3 — `trigger_scheduled` cron preview doesn't update reactively

**Symptom:** the human-readable preview ("Every 4 hours") shows correctly on initial render but doesn't update when the cron input changes.

**Diagnosis:** the cron-to-human parser runs on mount but isn't bound to the input's `onchange` event.

**Fix:** add an `onChange` handler to the cron input that re-runs the parser and updates the preview. Use `oninput` for live update as the user types, not just `onchange` on blur.

```javascript
cronInput.addEventListener("input", (e) => {
  preview.textContent = humanizeCron(e.target.value);
  nextRunEl.textContent = computeNextRun(e.target.value, timezone);
});
```

### Bug #4 — `trigger_scheduled` missing "Next run preview"

**Symptom:** PIPE3 brief specified a read-only "Next run: 2026-05-22 04:00 CDT" — not visible in the form.

**Fix:** add the next-run preview field below the human-readable preview. Compute client-side from cron + timezone:

```javascript
function computeNextRun(cronStr, timezone) {
  // Use a small cron parser — cronstrue is overkill; a minimal "next run" calculator
  // suffices for v1. Common cron patterns: `0 */4 * * *`, `0 9 * * 1-5`, `*/15 * * * *`.
  // For complex patterns, fall back to "Next run: see scheduler"
  try {
    const next = parseCronNext(cronStr, timezone);
    return next.toLocaleString("en-US", { timeZone: timezone });
  } catch {
    return "Next run: see scheduler";
  }
}
```

If implementing a full cron parser is overkill, accept the "see scheduler" fallback for complex patterns; the simple common patterns (hourly, daily, weekly at fixed time) should compute correctly.

### Bug #5 — `human_gate` approvers list missing 2 of 3 emails

**Symptom:** the multi-select dropdown only shows Jon's email. Brief specified all 3 (`josh@amiralearning.com`, `angela@amiralearning.com`, `jon@amiralearning.com`).

**Fix:** ensure the hardcoded approver list includes all 3:

```javascript
const APPROVERS = [
  { email: "josh@amiralearning.com", name: "Josh" },
  { email: "angela@amiralearning.com", name: "Angela" },
  { email: "jon@amiralearning.com", name: "Jon" },
];
```

Plus free-text fallback (type any email + Enter to add) stays as-is.

**Tooltip on on_timeout dropdown values:**

Add small explanatory tooltips OR helper text below the dropdown:
- `escalate`: "If approver doesn't respond within timeout, ping a secondary approver."
- `auto_approve`: "If timeout passes without response, automatically approve."
- `auto_reject`: "If timeout passes without response, automatically reject."

Display these inline (small italic text below the dropdown) so users see them without hovering.

### Bug #6 — Drag-and-drop from palette to canvas broken

**Symptom:** dragging a node type from the left palette onto the canvas does nothing. Only right-click "Add node…" works.

**Diagnosis:** PIPE3 added new form modules and may have touched the drawer/canvas event handlers in a way that broke the palette's HTML5 drag-and-drop API binding. Or the canvas's drop zone listener was removed.

**Fix:** investigate `public/js/components/pipeline-palette.js` and `public/js/components/pipeline-canvas.js`:

1. Confirm palette items still have `draggable="true"` attribute
2. Confirm `dragstart` handler on palette sets `dataTransfer` correctly
3. Confirm canvas has `dragover` listener with `event.preventDefault()` (required for drop zones)
4. Confirm canvas has `drop` listener that creates the new node at the drop coordinates

If PIPE3's drawer mount accidentally added an event handler that stops propagation OR captured the dragover before canvas saw it, fix the propagation.

**Verify:** drag any palette item → canvas → drop → new node appears at drop position with sensible defaults.

## Out of scope (handled in other briefs)

- **Cron picker preset UX** (Daily / Hourly / Weekly easy mode) — separate brief: `cron-picker-presets.md`
- **Agent picker freeform validation warning** — banked for later polish; the freeform mode is intentional for forward-reference
- **Delete pipelines** — separate brief: `pipeline-delete-with-confirmation.md`
- **Drag-and-drop accessibility (keyboard add)** — defer
- **Skill_call form** — defer until Skills lifecycle ships

## Invariants

1. **No new design tokens.** Existing palette.
2. **No new dependencies.** No cron parsing library — minimal client-side compute OR fall-back gracefully.
3. **No regression** in existing PIPE3 form behavior. Tests pass before AND after.
4. **All 6 fixes must be testable.** Each gets at least one Vitest/pytest unit test.

## Files expected

| File | LOC |
|---|---|
| `public/js/components/node-config-forms/agent-invocation-form.js` | ~80 delta (picker fix, model dropdown, cost cap tooltip) |
| `public/js/components/node-config-forms/trigger-scheduled-form.js` | ~80 delta (reactive preview, next-run preview) |
| `public/js/components/node-config-forms/human-gate-form.js` | ~40 delta (approver list, on_timeout tooltips) |
| `public/js/components/pipeline-palette.js` | ~20 delta (drag fix investigation; may be canvas-side) |
| `public/js/components/pipeline-canvas.js` | ~30 delta (drop zone fix if needed) |
| `public/css/features/pipelines.css` | ~40 delta (tooltip styling, dropdown styling) |
| `tests/unit/frontend/test_pipe3_walkthrough_patch.js` (new or appended) | ~80 |

**Total: ~370 LOC.** Cap 450.

## Test plan

1. **Agent picker:** type a character → no console error, results filter
2. **Provider override:** select different provider → model dropdown updates with that provider's models
3. **Cost cap tooltip:** hover the (?) icon → tooltip visible with the spec text
4. **Cron preview:** type a new cron → human-readable preview updates live
5. **Next run preview:** populate cron `0 9 * * 1-5` + America/Chicago → shows next weekday at 9am CDT
6. **Approvers list:** open dropdown → 3 named approvers visible (Josh, Angela, Jon)
7. **on_timeout tooltips:** all 3 values visible inline with descriptions
8. **Drag-from-palette:** drag any palette item → canvas → drop → new node created at drop position

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new console errors

## What "done" looks like

1. All 6 bugs fixed; all 2 tooltips visible.
2. Drag-from-palette works.
3. Tests pass.
4. `check.sh` passes within exempt set.
5. Full-diff insertions ≤ 450.

## Report Codex submits

1. `git diff --stat` output.
2. For Bug #6: was it palette-side or canvas-side? What event listener was missing/wrong?
3. Screenshots: agent picker filtering, model dropdown updating, cron live preview, next-run preview, 3 approvers visible, drag working.
4. Test pass count.
5. Branch.

---

**Lead notes (not for Codex):**
- The picker JS error (Bug #1) is the smoking gun for why agent search felt broken in Jon's walk. Fix that and the picker becomes usable.
- The drag regression (Bug #6) is the highest-value workflow fix — users will use drag-and-drop as the primary node creation path; right-click is the discovery fallback.
- Tooltips matter because PIPE3 forms are the user's first interaction with concepts like cost cap and gate timeout. Without tooltips, the configuration is opaque.
