# CC7 — Pipeline Run Dispatch Durability (fix the orphan bug)

**Paste-into:** terminal-Lead. It spawns a Claude Code Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/cc7-dispatch-durability`
**Browser smoke owner:** Lead (this session), post-merge — fire 3 runs back-to-back, all execute (no orphans).
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** ~150.
**Priority:** HIGH — this is the solidity gate before SP1 and before any cron-driven use.

---

## Why this exists — the robustness finding

Robustness check (Lead, 2026-05-27): pipeline run #1 succeeded end-to-end; **run #2 FAILED with "Orphaned queued run (executor never started)"** — it emitted 6 scout signals but the run never executed (empty node_states), and the orphan-sweeper reaped it after 5 min.

**Root cause:** all 3 dispatch sites in `artemis/pipelines/routes.py` (lines ~326, ~504, ~592) call:
```python
asyncio.create_task(_execute_pipeline_run(run_id))   # return value discarded
```
The asyncio event loop holds only a **weak** reference to tasks. An unreferenced fire-and-forget task can be **garbage-collected before/during execution** (documented Python footgun). Intermittently, the executor never runs → the run orphans → reaped as failed. For a cron-driven pipeline this is a silent-failure trap.

---

## Scope

### Part A — Retain task references (fixes the GC footgun)

Add a module-level task registry and a helper in `artemis/pipelines/routes.py`:
```python
_BACKGROUND_TASKS: set[asyncio.Task] = set()

def _dispatch_execution(run_id: str) -> None:
    task = asyncio.create_task(_execute_pipeline_run(run_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
```
Replace all 3 bare `asyncio.create_task(_execute_pipeline_run(run_id))` calls with `_dispatch_execution(run_id)`. The strong reference in `_BACKGROUND_TASKS` keeps the task alive until it completes; the done-callback prevents the set from leaking.

### Part B — Self-heal: re-dispatch orphaned/queued runs (don't just fail them)

`sweep_orphaned_queued_runs` in `artemis/pipelines/scheduler.py` currently marks stale-queued runs as failed. Change the behavior so a run that's been `queued` without execution is **re-dispatched once** before being failed:
- On sweep, for each `queued` run older than a short threshold (e.g. 1 min) with empty node_states and no prior re-dispatch: call `_dispatch_execution(run_id)` (or the executor entry) and mark it re-dispatched (e.g. a `metadata.redispatch_count`).
- Only mark `failed` (orphaned) if it's already been re-dispatched once and still didn't start (genuine stuck), OR exceeds the original 5-min threshold.
- This makes dispatch durable across the GC case AND a server restart (a run queued when the server bounced gets picked up on the next sweep).

Keep it idempotent + safe: never re-dispatch a run that's actually running or terminal. Guard on `status='queued'` + empty/absent node_states.

(If wiring re-dispatch into the scheduler is more than ~60 LOC or risks double-execution, ship Part A alone + leave a clear TODO for Part B — Part A is the core fix; flag the decision in the report.)

### Part C — Tests

`artemis/pipelines/tests/test_dispatch_durability.py`:
1. `_dispatch_execution` retains the task in `_BACKGROUND_TASKS` while running and discards it on completion (no leak).
2. The 3 route handlers use `_dispatch_execution` (not bare create_task) — assert via a spy/mock that a dispatched run actually executes (the task isn't dropped).
3. (Part B) `sweep_orphaned_queued_runs` re-dispatches a queued run with empty node_states instead of immediately failing it; only fails after a re-dispatch didn't take. (Adapt the existing `test_sweep_orphaned_queued_runs_marks_old_rows_failed` to the new behavior.)

---

## Files owned
- EDIT: `artemis/pipelines/routes.py` (the helper + 3 call sites)
- EDIT: `artemis/pipelines/scheduler.py` (re-dispatch on sweep — Part B)
- NEW: `artemis/pipelines/tests/test_dispatch_durability.py`
- EDIT (if needed): `artemis/pipelines/tests/test_pipe4_executor.py` (adapt the existing orphan-sweep test to re-dispatch behavior)

**Do not touch:** the executor logic itself (`_execute_pipeline_run` body), the agent/MCP/tool path, blueprints, the seed. This is dispatch-durability only.

---

## Acceptance criteria (demonstrate each)
1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/pipelines/tests/test_dispatch_durability.py artemis/pipelines/tests/test_pipe4_executor.py -v` — all pass. **Paste.**
2. The 3 dispatch sites now use `_dispatch_execution`. **Paste the grep showing no bare `asyncio.create_task(_execute_pipeline_run` remains.**
3. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
4. `git diff --stat` + `git log --oneline -1` on `worker/cc7-dispatch-durability`. **Paste.**

(The real proof — 3 back-to-back runs all execute — is Lead's post-merge smoke, not a Worker task.)

---

## Hard constraints
- Part A is mandatory + the core fix. Part B (re-dispatch) preferred but may be deferred with a TODO if it risks double-execution — flag the call.
- Never cause double-execution of a run. Guard re-dispatch on `status='queued'` + no node_states.
- Don't touch the executor body or the MCP/agent path.
- Local-only git. Worker commits on `worker/cc7-dispatch-durability`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC7 — Dispatch Durability report
1. Commit / branch / worktree
2. LOC diff stats
3. Part A done (task refs retained); Part B done or deferred-with-TODO + why
4. grep proof: no bare create_task(_execute_pipeline_run) remains (acceptance #2)
5. Test pass summary
6. check.sh summary
7. Anything surprising
```

---

**Claude Code Worker: Part A (retain task refs) is the documented fix for the GC footgun that orphaned run #2. Operating principle: do not introduce double-execution — re-dispatch must guard on queued+no-node-states. If Part B is risky, ship Part A + TODO and say so.**
