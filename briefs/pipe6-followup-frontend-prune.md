# PIPE6-FOLLOWUP — Prune dormant Workflows + Automations frontend

**Paste-into:** Codex (second account, parallel with DIST1 — zero file overlap; pure frontend, no migration, no Python).
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `medium`. Mechanical removal, but it touches `main.js` (the app entrypoint import list) — a wrong delete breaks the whole frontend, so it needs to verify each removal has no remaining references and prove the app still loads. Not `low`.
**Target branch:** `worker/pipe6-followup-frontend-prune`
**Browser smoke owner:** Worker MUST run it (acceptance gate); Lead re-verifies post-merge.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~300 (mostly deletions).
**Priority:** MEDIUM — cleanup. Safe to run parallel with DIST1.

---

## Why this exists

PIPE6 (commit c25eb4e, D6 lock) sunset Workflows + Automations: backend routes now return **410 Gone**, and the sidebar tabs were removed from `operations-shell.js`. But the **frontend modules and their API wrappers were left in place and still loaded.** Verified:
- `public/js/main.js` still imports `./features/workflows.js` and `./components/workflow-modal.js`.
- `public/js/core/api.js` still exports `fetchWorkflows`, `createWorkflow`, `updateWorkflow`, `deleteWorkflowApi`, `_normaliseWorkflow` (+ likely automations equivalents) — all calling endpoints that now 410.

This is dead weight loaded on every page. Prune it.

---

## Scope

### Part A — Inventory first (do this before deleting anything)

1. `grep -rniE "workflow|automation" public/js/` — produce the full reference map.
2. For each Workflows/Automations symbol (feature module, component, api wrapper, import), determine whether **anything still-live** references it. "Still-live" = reachable from a non-deprecated surface (Pipelines, Operations shell minus the removed tabs, Agents, Marketing, Memory, etc.).
3. **Paste this inventory in your report before the deletion diff** so the Lead can sanity-check what you classified as dead.

**Distinguish carefully:**
- `workflow-modal.js`, `features/workflows.js`, `features/automations.js` (if present) → dead UI, remove.
- `api.js` wrappers calling `/api/workflows*` or `/api/automations*` → dead, remove.
- **BUT:** anything named "pipeline" is LIVE (Pipelines replaced Workflows — do NOT touch). And check the `dag-editor.js` reference in api.js line 1109 comment — verify whether dag-editor is shared with Pipelines before removing anything it depends on.

### Part B — Remove dead frontend

- Delete dead feature modules + components.
- Remove their imports from `main.js`.
- Remove dead `api.js` wrappers + the `_normaliseWorkflow` helper (and automations equivalents) **only if nothing live calls them**.
- Remove any now-orphaned CSS / templates specific to those modules.

### Part C — Verify the app still loads (HARD GATE)

This touches `main.js`. You MUST prove the app boots clean:
1. Start the app (`uv run uvicorn artemis.main:app` or the documented dev command).
2. Load the page in a browser / headless check.
3. **Paste the browser console** — zero errors, zero failed module imports.
4. Click into **Pipelines** (the live replacement) and confirm it renders + lists pipelines.
5. Confirm Operations shell renders without the removed tabs and without console errors.

If any live surface breaks, STOP and report — do not force the deletion.

---

## Files likely owned (verify against your Part A inventory)

- EDIT: `public/js/main.js` (remove dead imports)
- DELETE: `public/js/features/workflows.js`, `public/js/features/automations.js` (if present), `public/js/components/workflow-modal.js`
- EDIT: `public/js/core/api.js` (remove dead wrappers + helpers)
- POSSIBLE: orphaned CSS/template files

---

## Acceptance criteria

1. **Part A inventory pasted** — the reference map + dead/live classification. **Required before diff.**
2. `git rm` / deletions listed; `git diff --stat`. **Paste.**
3. **Browser-load smoke pasted:** console clean, Pipelines renders + lists, Operations shell clean. **Paste console output verbatim.**
4. `grep -rniE "fetchWorkflows|createWorkflow|workflow-modal|features/workflows|fetchAutomations" public/js/` returns ONLY historical comments, no live imports/calls. **Paste.**
5. `./scripts/check.sh` JS lint/format passes (no Python touched). **Paste.**
6. `git log --oneline -1` on the branch. **Paste.**

---

## Hard constraints

- **Pipelines is LIVE — never touch anything pipeline-named.** Workflows ≠ Pipelines.
- **Verify-before-delete.** Every removal must have zero live references (Part A proves it).
- **Browser-load gate is mandatory** — this touches the app entrypoint.
- **No backend/Python edits.** If you find a backend reference that needs changing, STOP and report (that's out of scope — backend already 410s correctly).
- **Local-only git.** Branch `worker/pipe6-followup-frontend-prune`. No push.

---

## Coordination with DIST1 (in flight, parallel)

DIST1 is 100% backend Python (`artemis/marketing/*`, migration 0054). This brief is 100% frontend JS. **Zero overlap, no migration.** Safe to run concurrently. Merge order doesn't matter.

---

## Report-back format

```
PIPE6-FOLLOWUP — frontend prune report
1. Commit / branch
2. Part A inventory (reference map + dead/live classification)
3. Deletions + diff stat
4. Browser-load smoke (console verbatim, Pipelines + Operations confirmed)
5. grep verification (no live refs remain)
6. check.sh JS summary
7. Anything surprising — esp. shared deps between dead modules and live surfaces (dag-editor?)
```
