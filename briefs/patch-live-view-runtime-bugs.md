# Patch — Live-View Runtime Bugs (Lane A walkthrough findings)

**Owner:** Codex (paste-ready)
**Branch:** `codex/patch-live-view-runtime-bugs`
**LOC budget:** ~120 (cap 180)
**Depends on:** Lane A (pipeline-run-live-view), Lane C (empty signals), Lane E (stragglers) all merged.

## Why

Jon's walkthrough surfaced 4 Lane A runtime bugs (visible only in browser, missed by Worker since no app was running in worktree):

1. Overlay shows "Started 5272m 23s ago" — fetches wrong/stale run
2. "View in run history" routes to Focus dashboard — wrong deep-link
3. Cancel Run button no-op — runs already terminal from fast-path
4. Nodes don't animate — partially fast-path (architecturally correct), needs verification with longer-running scenario

## Fixes

### Bug 1 — Overlay fetches wrong run

The active-run polling fetches the wrong row from `/api/pipelines/{id}/runs`. Likely sort order or status filter wrong — pulls oldest queued run from days ago instead of latest run for this pipeline.

**Fix in `public/js/components/pipeline-run-overlay.js`** (or wherever live-view polling lives):

```javascript
// Make the query explicit: most recent run for this pipeline, status-filtered
const res = await fetch(`/api/pipelines/${pipelineId}/runs?limit=1&sort=created_at_desc`);
const runs = (await res.json()).runs || [];
const activeRun = runs[0];

// Only treat as "active" if status is running/queued/awaiting_approval AND created within last 24 hours
if (activeRun && ['queued', 'running', 'awaiting_approval'].includes(activeRun.status)) {
  const ageMinutes = (Date.now() - new Date(activeRun.created_at)) / 60000;
  if (ageMinutes < 1440) {  // 24 hours
    showOverlay(activeRun);
  }
}
```

Backend may also need to add sort param to `/api/pipelines/{id}/runs` if not already supported — add `sort=created_at_desc` query param handling. Likely already returns newest first; verify.

### Bug 2 — "View in run history" routes to Focus

Find the link element in `pipeline-run-overlay.js` or `pipeline-run-history.js`. Currently probably `href="#/focus"` or empty hash. Should route to the actual run-history surface Lane A added.

**Fix:**

```javascript
// Was:
viewHistoryLink.href = "#/focus";  // or whatever was wrong

// Now:
viewHistoryLink.href = "#/operations/pipelines";  // or "#/operations/run-history" if that's the new view name
// Wire to setState('view', 'operations/run-history') if state-driven
viewHistoryLink.addEventListener('click', (e) => {
  e.preventDefault();
  setState('view', 'operations/pipelines');  // or whatever maps to the run history surface
});
```

If Lane A created a new view called `pipelines/run-history` or similar, route there. If Run History is a sub-section of Pipelines, route to `#/operations/pipelines` with a query param `?tab=history`.

**Worker should grep for "run-history" in `public/js/` to find the correct view name added by Lane A.**

### Bug 3 — Cancel Run button no-op for terminal runs

`POST /api/pipeline-runs/{id}/cancel` likely returns 409 or 400 if run is already `succeeded` / `skipped` / `failed`. UI should:

1. Disable the Cancel button when `activeRun.status` is in terminal set: `["succeeded", "failed", "cancelled", "partial_complete", "skipped"]`
2. Hide the entire overlay when run is terminal (it's no longer "active")

**Fix:**

```javascript
const TERMINAL_STATES = new Set(['succeeded', 'failed', 'cancelled', 'partial_complete', 'skipped']);

// In overlay render logic:
if (TERMINAL_STATES.has(activeRun.status)) {
  // Either hide overlay entirely, OR show with cancel button disabled + label "Run completed"
  cancelBtn.disabled = true;
  cancelBtn.title = `Run already ${activeRun.status}`;
  // Maybe show subtle "✓ Completed" indicator instead of progress bar
}
```

### Bug 4 — Node animation not visible (probably architectural, not bug)

Lane C's empty-signals fast-path completes the pipeline in <1 second. Polling interval is 1.5s. Run terminal before first poll → all nodes show `succeeded` state immediately, never visible as `running`.

**This is correct behavior; not a bug.** Animation will be visible when:
- Real scout adapters ship (next-session work)
- Or a test pipeline with intentionally slow nodes is created

**Optional polish (out of scope but flag):** add a 1-2 second minimum-visible-state delay for fast-completing pipelines so the user sees nodes flash through running state. Bank for next-session UI polish if Jon wants.

### Verification approach

Worker MUST do browser smoke this time. After implementing:
1. Run dev server against branch
2. Open Marketing Pipeline canvas
3. Click Run → overlay shows the JUST-created run (not 5272m old)
4. After run completes (~1s for empty-signals fast-path): cancel button disabled with "Run already succeeded" tooltip
5. Click "View in run history" → routes to Run History surface, NOT Focus
6. Pipeline_run row appears in run history table

If verification fails: don't ship. Surface to Lead.

## Out of scope

- Adding minimum-visible-state animation delay (banked)
- Real-time WebSocket updates (polling sufficient)
- Bulk run management
- Triggering with synthetic delays for animation testing

## Files

| File | LOC |
|---|---|
| `public/js/components/pipeline-run-overlay.js` | ~50 delta |
| `public/js/features/pipeline-run-history.js` (if route was added there) | ~10 delta |
| `public/js/components/pipeline-canvas.js` (if View link lives here) | ~10 delta |
| `artemis/pipelines/repository.py` (sort param if backend changes needed) | ~10 delta |
| Tests | ~40 |

**Total: ~120 LOC.** Cap 180.

## Invariants

- node --check on all modified JS
- conftest hard-fail on non-test DB
- `./scripts/check.sh` passes within exempt set
- Browser smoke MUST be done by Worker this time (don't skip — Lane A skipped, that's why these bugs landed)
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, screenshots demonstrating each fix (overlay showing fresh run / Cancel disabled on terminal run / View link routes correctly to run history), test pass count, browser smoke confirmation (paste console output if any errors), branch.

---

**Lead notes (not for Codex):**
- This patch is small but high-leverage. After it lands, the live-view surface is genuinely demoable.
- Browser smoke for Worker / Codex on UI changes is now hardened: this patch's own invariant requires it. Future briefs should also enforce.
- Bug 4 (no animation on fast-path) is correct behavior; animation will appear naturally once scouts produce real signals (next-session adapter work).
