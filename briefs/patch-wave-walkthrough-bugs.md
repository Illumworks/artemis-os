# Patch — Wave Walkthrough Bugs (3 fixes in one brief)

**Owner:** Codex (paste-ready, mechanical)
**Branch:** `codex/patch-wave-walkthrough-bugs`
**LOC budget:** ~200 (estimate; honest overrun OK up to ~280)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE1 + agent-provider-invariant-ui + OPS-UI-1 all merged.

## Why this brief exists

Jon's walkthrough of the merged wave surfaced three small bugs that undermine the work that just landed. Each is a single-file or near-single-file fix. Bundling them avoids three trip-cycles.

## Bug #1 — Pipelines: enable/disable toggle missing

**What's wrong:** Pipelines list cards show Pause + Edit JSON + Run buttons, but no toggle. PIPE1 brief specified: "Every pipeline must be enable/disable-able from the list view without entering the editor."

**Fix:** Replace the Pause button with a proper toggle component on each pipeline card.

- Visual: a small toggle switch (on/off) inline in the card header, near the status indicator. Use the existing app toggle pattern if one exists; otherwise a simple `<button role="switch" aria-checked="...">` with on/off label.
- Behavior: toggles the pipeline between `status='active'` and `status='paused'` via `POST /api/pipelines/{id}/enable` and `POST /api/pipelines/{id}/disable` (routes exist from PIPE1).
- Status indicator: keep the existing dot/badge that shows current state.
- Remove the Pause button (toggle subsumes it).

**Files likely:** `public/js/features/pipelines.js`, `public/css/features/pipelines.css`.

## Bug #2 — Pipelines: Run toast is off-brand black box

**What's wrong:** PIPE1's Run button correctly POSTs to `/api/pipelines/{id}/run` (which records intent in `pipeline_runs` until PIPE4 wires execution). But the toast that says "Run queued — execution engine arrives in PIPE4" renders as a dark/empty black box, off the Artemis design language.

**Fix:** Use the existing app toast/notification component instead of whatever Codex shipped.

- Survey first: grep `public/js/` for "toast" or "notify" or "showMessage" — there's almost certainly an existing pattern. Use it.
- If no shared component exists: render an inline `<div class="toast">` styled with existing tokens (`--surface-2`, `--text`, `--accent`, etc.). Use the same visual treatment that Pipelines uses for its empty-state CTA, or that Approvals uses for "approval recorded" feedback.
- Toast content: "Run queued — execution engine arrives in PIPE4." Plus a small subtitle if there's room: "Status will appear in run history."

**Files likely:** `public/js/features/pipelines.js`, possibly `public/css/features/pipelines.css` or a shared toast CSS file if one exists.

## Bug #3 — Agents: provider/model selection doesn't persist

**What's wrong:** Provider/model dropdowns in the Agent Card detail panel are visible and clickable per the screenshot, but selecting a different value and saving doesn't persist. Either the PATCH isn't firing, the response isn't refreshing the panel, or the change handler is broken.

**Fix:** Trace the picker → save → refresh flow and find the broken link. Three likely culprits:

- **(a)** The dropdown's `onchange` doesn't update local state, so save reads the old value
- **(b)** The save handler PATCHes but doesn't await + refresh the panel
- **(c)** The PATCH fires but the backend validator rejects it silently (check network tab for 422s)

**Diagnostic steps Codex should take:**

1. In `public/js/features/operations-shell.js` (or wherever the Agent Card detail panel renders), find the provider/model picker save handler.
2. Add a `console.log` temporarily to verify the new value reaches the PATCH call. If not, fix the state binding.
3. If PATCH fires but the panel doesn't update: ensure the response is awaited and the agent state is refreshed (re-fetch or update store).
4. If PATCH returns 422: check the validator — was the M5 agents seeded with `fallback_model` populated? The validator might reject because `fallback_model` is missing even when `fallback_provider` is set.

**Files likely:** `public/js/features/operations-shell.js`, possibly `public/js/core/api.js` (the PATCH wrapper), possibly `artemis/routes/builders/agents.py` (the validator).

**Bonus diagnostic — confirm legacy agent state:**

Run the audit script Codex shipped:
```bash
uv run python scripts/audit_agent_providers.py
```

If Smoke Test Agent + WS Integration Agent still show as missing fallback, your fix lets you actually populate them via the UI.

## Out of scope

- Redesigning the toast component globally. Just match the existing app pattern; if no pattern exists, do a minimal styled `<div>`.
- Adding a toggle component library. Build a minimal `<button role="switch">` if no shared toggle exists.
- Fixing other Agent Card edit fields (persona, instructions, etc.). Only provider/model.
- Backfilling fallback_provider on the 2 legacy agents — Jon will do that via the now-working UI after this patch ships.

## Invariants

1. **No new design tokens.** Use existing palette and spacing.
2. **No new dependencies.** Vanilla JS + existing patterns only.
3. **No regression** in existing Pipelines or Agent Card behavior.
4. **`./scripts/check.sh`** must pass within exempt set.

## Files expected

- `public/js/features/pipelines.js` — ~30 LOC delta (toggle + toast)
- `public/css/features/pipelines.css` — ~30 LOC delta (toggle + toast styling)
- `public/js/features/operations-shell.js` — ~30 LOC delta (provider save flow fix)
- Possibly `public/js/core/api.js` — ~10 LOC delta if PATCH wrapper needs touch
- Possibly `artemis/routes/builders/agents.py` — ~10 LOC delta if validator needs touch
- Tests: `tests/test_patch_wave_bugs.py` (new) — ~50 LOC sanity tests

**Total honest estimate: ~160 LOC. Cap 280.**

## Test plan

1. **Toggle:** click toggle on Pipelines card → status flips → DB reflects. Refresh page → state persists.
2. **Toast:** click Run → toast appears with brand styling (not black box). Toast auto-dismisses after a few seconds OR has a close affordance.
3. **Provider save:** select new preferred provider + model on an agent → click Save → PATCH succeeds → panel re-renders with new values. Refresh page → state persists.
4. **Audit script:** run before fix → 2 violations. After Jon manually sets fallback on the 2 legacy agents → 0 violations.

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set before declaring done
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new JS console errors

## What "done" looks like

1. Toggle works on Pipelines cards.
2. Run toast uses brand styling.
3. Agent provider/model save persists.
4. Audit script runs cleanly.
5. Tests pass.
6. `check.sh` passes within exempt set.

## Report Codex submits

1. `git diff --stat` output.
2. Description of each fix (1 sentence each).
3. For bug #3: which of the 3 likely culprits (a/b/c) was the actual cause.
4. Test pass count.
5. Branch.
