# CC8 — Pipeline Run-Lock (serialize concurrent runs of the same pipeline)

**Paste-into:** terminal-Lead → Claude Code Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc8-run-lock`
**Browser smoke owner:** Lead, post-merge — fire 3 concurrent triggers; only 1 runs, 2 rejected.
**Report back to me by:** Jon pastes terminal-Lead's relay.
**LOC cap:** ~150.
**Priority:** HIGH — solidity gate (alongside CC9) before SP1 + cron.

---

## Why this exists

CC7 smoke at concurrency=3: only 1/3 marketing.main runs reached Gate-1; the other 2 timed out on the Claude CLI (300s per-tool-run) because 30+ subprocesses competed for resources. Same-pipeline concurrent runs are an anti-pattern anyway (would double-emit, race on memory_layer dedup, etc.). The fix is **a run-lock: one active run per pipeline at a time.**

For a cron-driven pipeline (every 4h), this matches actual cadence — if a previous run isn't done by the next tick, skipping is correct (the previous run is probably hung).

---

## Scope

### Part A — Run-lock at trigger sites
In `artemis/pipelines/routes.py` — at every trigger site (manual POST `/api/pipelines/{id}/run`, resume after gate, plus any scheduler-trigger call sites at lines ~326, ~504, ~592 already identified by CC7):

Before creating + dispatching the run, query `pipeline_runs` for any existing **in-flight** run for this `pipeline_id` (status IN (`queued`, `running`, `awaiting_approval`)). If one exists:
- For **manual** triggers (POST): return **HTTP 409 Conflict** with `{error: "pipeline_run_in_flight", in_flight_run_id: <existing_id>, message: "..."}`.
- For **scheduler/cron** triggers: log a WARNING and skip the cycle (don't create a new run). The next scheduler tick re-checks. (Match the convention used elsewhere — `apscheduler` jobs often log + skip.)

Use a single helper: `await acquire_run_lock(session, pipeline_id) -> existing_run | None`. Race-safe enough for a single-process app (acquire+create in one transaction); a true distributed lock can come later if/when we scale out.

### Part B — Cron/scheduler call-site
In `artemis/pipelines/scheduler.py` (and any other scheduled-trigger call site — grep `_execute_pipeline_run`, `create_pipeline_run`, cron job registration), apply the same lock check before triggering.

### Part C — Tests
`artemis/pipelines/tests/test_run_lock.py`:
1. Trigger run #1 (status queued/running) → trigger run #2 manually → 409 Conflict, only 1 run exists.
2. After run #1 reaches terminal (succeeded/failed/cancelled), trigger run #3 → succeeds (lock released).
3. `awaiting_approval` counts as in-flight: trigger during gate-suspended state → 409.
4. Scheduler trigger while in-flight → logs + skips, no new run created.
5. Different pipelines do NOT block each other (lock is per-pipeline).

---

## Files owned
- EDIT: `artemis/pipelines/routes.py` (helper + 3 trigger sites)
- EDIT: `artemis/pipelines/scheduler.py` (scheduler trigger guard)
- NEW: `artemis/pipelines/tests/test_run_lock.py`

**Do not touch:** the executor body, agent/MCP/tool path, qualifier, gate-card logic, seed, blueprints.

---

## Acceptance criteria
1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/pipelines/tests/test_run_lock.py -v` — all pass. **Paste.**
2. The 4 trigger sites use `acquire_run_lock`. **Paste a grep showing no bare `create_pipeline_run` without the lock check.**
3. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
4. `git diff --stat` + `git log --oneline -1` on `worker/cc8-run-lock`. **Paste.**

---

## Hard constraints
- 409 on manual; log+skip on scheduler. (Don't queue-serial for v1 — adds complexity, scheduler retries naturally.)
- The lock is in-process (single-row guard via the DB query). Don't introduce a distributed locking system.
- `awaiting_approval` counts as in-flight (a gate-suspended run is still active).
- Local-only git. Worker commits on `worker/cc8-run-lock`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC8 — Run-Lock report
1. Commit / branch / worktree
2. LOC diff stats
3. The acquire_run_lock helper + the 4 trigger sites updated (paste signature + the grep)
4. Test pass summary
5. check.sh summary
6. Anything surprising — especially any other trigger site I missed
```
