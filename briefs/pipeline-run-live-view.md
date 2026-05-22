# Pipeline Run Live-View — Canvas Execution Visualization

**Owner:** Sonnet Worker via Agent({isolation: "worktree"})
**Branch:** `worker/pipeline-run-live-view`
**LOC budget:** ~700 (cap 950)
**Depends on:** PIPE4 merged; provider cascade wire-up merged.

## Why

PIPE4 executes pipelines correctly but invisibly. Click Run → "queued" toast → silence, while 13 nodes succeed and pipeline suspends at gate_1 in the background. Operator can't see what's happening. n8n-style live execution view closes this gap.

## Scope

### Background polling

When a pipeline canvas is open AND there's an active pipeline_run for it:
- Poll `GET /api/pipelines/{id}/runs?status=running,queued,awaiting_approval&limit=5` every 1.5s
- Get the latest pipeline_run's full `node_states` JSONB
- Update canvas node visualizations from polled state

Polling starts when canvas opens with an active run AND stops when:
- Run reaches terminal state (`succeeded` / `failed` / `cancelled`)
- Canvas closes
- 5 minutes of polling with no state changes (auto-pause; resume on user interaction)

### Canvas node state visualization

Each node card shows current state via CSS class on the node element:
- `.pcv-node--pending` — default look (gray, no animation)
- `.pcv-node--running` — pulse / shimmer animation, accent border, "Running" badge
- `.pcv-node--suspended` — paused-icon overlay, warning border, "Awaiting approval" badge (human_gate suspended)
- `.pcv-node--succeeded` — green checkmark badge, success border
- `.pcv-node--failed` — red outline, error badge, click to expand error message
- `.pcv-node--partial_complete` — amber border, "Stopped: cost cap" or similar reason badge

Animation specifics:
- Running node: 1.5s pulse cycle, subtle (don't make it distracting if pipeline has 10+ running nodes simultaneously)
- Suspended: static dimmed look, no pulse
- Succeeded: brief celebratory glow on first transition, then steady

### Run controls overlay

Bottom-right floating panel that appears when there's an active run:
- Run ID (truncated, click to copy full)
- Overall status badge
- Progress: "13/21 nodes complete"
- Started timestamp + elapsed time
- "View in run history →" link
- "Cancel run" button (POST /api/pipeline-runs/{id}/cancel)
- For `awaiting_approval`: "Approve at Gate 1 →" link to Approval Queue

### Run history surface

New page or section: **Operations → Pipelines → Run History** (or inline drawer on Pipelines list):
- Table of recent pipeline_runs (across all pipelines or filtered)
- Columns: pipeline name, started_at, duration, status, trigger (manual/scheduled/webhook), nodes complete, actions
- Click row → opens that run's pipeline canvas with `node_states` visualization (read-only replay of where it got)
- Per-row actions: cancel (if running), resume (if awaiting_approval — opens Approval Queue), retry (creates new run)

### "Execute Workflow" inline button + queue visibility

Per Jon's earlier ask:
- "Run" button on canvas now: creates run + immediately polls
- New "Execute Workflow" toolbar button (or rename Run → Execute):
  - Same as Run but explicitly shows the "queued" → "running" transition in the bottom-right overlay
- Pipelines list page: per-card mini-progress when there's an active run ("⚡ Running: 4/21 nodes")

### Stale toast text cleanup (folds in)

Remove or rewrite stale messages:
- "Run queued — execution engine arrives in PIPE4." → "Run started (#abc1234). Watch progress on canvas."
- "Run queued — execution wired in PIPE4." → same

### Out of scope

- WebSocket / SSE for true real-time (polling is sufficient for v1)
- Live cost tracking visualization (cost shows in run history, not on canvas)
- Run comparison UI (compare two runs side by side)
- Replay mode (step through a finished run's node states one at a time)
- Node-level logs visualization (logs shown in inline modal on click, not as overlay)

## Files expected

| File | LOC |
|---|---|
| `public/js/components/pipeline-canvas.js` (poll loop, node-state CSS application) | ~150 delta |
| `public/js/components/pipeline-run-overlay.js` (new — bottom-right control overlay) | ~150 |
| `public/js/features/pipeline-run-history.js` (new — run history table + replay) | ~200 |
| `public/css/features/pipelines.css` (node-state visualizations + overlay + history styles) | ~120 delta |
| `public/js/core/api.js` (poll helper for run history) | ~30 delta |
| Tests | ~150 |

**Total: ~800 LOC.** Cap 950.

## Tests

- Mock pipeline_run JSON; verify each status class applies to nodes correctly
- Poll loop fires every 1.5s when canvas open with active run; stops on terminal state
- Run overlay shows correct progress text, buttons (cancel/resume) appear conditionally
- Run history table loads + sorts; click-to-replay opens canvas with node_states applied

## Invariants

- Polling has clear stop conditions (don't poll forever)
- No WebSocket dependency
- node --check on all modified JS
- `./scripts/check.sh` passes within exempt set
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, screenshots (canvas with running run / suspended at gate / completed run / run history table), test pass count, branch + worktree path.
