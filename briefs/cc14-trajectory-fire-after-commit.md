# CC14 — Trajectory Summarizer Fires AFTER Agent-Run Commit (the 4th blocker)

**Paste-into:** terminal-Lead → Claude Code Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc14-trajectory-post-commit`
**Browser smoke owner:** Lead, post-merge — trigger one pipeline run, confirm `agent_run_trajectory_summaries` count jumps by ~10 with real LLM-extracted content.
**Report back to me by:** Jon pastes terminal-Lead's relay.
**LOC cap:** ~150.
**Priority:** HIGH — closes the self-improvement loop. CC10+CC11+CC13+CC14 together unlock it; alone, each is necessary but not sufficient.

---

## Why this exists — the 4th hollowness layer (surfaced by CC13's logger.exception)

CC13 eliminated the lookup race + tightened the bare-except to `logger.exception` — and the bug it was hiding is now visible. Every summarize attempt during a pipeline run logs:

```
ForeignKeyViolationError: insert or update on table "agent_run_trajectory_summaries"
violates foreign key constraint "fk_trajectory_summaries_run"
DETAIL: Key (run_id)=(294) is not present in table "agent_runs".
```

The LLM call succeeds; real trajectory content is generated; the INSERT then fails because the `agent_runs.id` referenced doesn't exist *from the summarizer's session's perspective*. The pipeline executor holds **one long transaction across many agent_runs** — flushing each but committing only at the end. The summarizer's separate session can't see any of them until that final commit, and by then the summarizer tasks have already errored.

CC13 moved the race from "SELECT returns None" to "INSERT fails FK." Same structural issue underneath: **the summarizer is being asked to act on rows that aren't yet globally visible.** This brief eliminates the race by triggering the summarizer at a point where the FK target IS guaranteed to be visible.

---

## Scope

### Part A — Investigate first (~20 LOC of findings in the report)

Before coding, answer these in the report:
- Where does the **pipeline executor** commit? Per-node, per-batch, or per-pipeline? Look at `artemis/pipelines/routes.py::_execute_pipeline_run` and `artemis/pipelines/node_executors/agent_executor.py` — find every `session.commit()` and characterize the transaction lifetime.
- Is `run_agent` called with a session it controls or one the caller controls? (Determines whether `run_agent` can commit safely without breaking the executor.)
- Does `agent_executor.execute_agent_node` commit after a single agent run finishes, or does the outer executor batch?

Paste the findings as Part A of the report. This determines the cleanest landing spot for Part B.

### Part B — Defer `summarize_async` until AFTER the agent_run row is committed

Pick the cleanest landing point based on Part A's findings. The two viable shapes:

**B1 (preferred if executor commits per-node):** Move the `summarize_async` call OUT of `run_agent` and INTO `execute_agent_node` (or wherever the per-node commit happens). Call it AFTER the commit, with the snapshot built from the just-committed `AgentRun` row. The summarizer's new session can now see the FK target.

**B2 (preferred if executor batches commits):** Keep `summarize_async` where it is in `run_agent` but precede it with an explicit `await session.commit()` of just the agent_run row (preserve the existing `await session.flush()` for in-session visibility; add a commit so other sessions see it). Document the change to the executor's transaction model + verify no downstream code assumes the larger transaction is still open.

**Decide between B1 and B2 in the report based on Part A.** B1 is structurally cleaner (decouples summarization from `run_agent`'s session); B2 is smaller-diff if the executor's transaction model permits it.

### Part C — Tests

`artemis/builder/tests/test_trajectory_post_commit.py`:
1. After `summarize_async(snapshot)` is invoked at the new (post-commit) site, the summary write succeeds — the FK target is visible. Use a real-DB integration test (this is the regression that pure-mock tests missed).
2. The summarize is NOT called from inside the larger pipeline transaction (i.e., calling code committed first). Spy/assert on the call ordering: `commit()` happened before `summarize_async()`.
3. (Regression) CC10/CC11/CC13 tests still pass.

### Part D — Defensive: log every summary insert (success + failure)

Inside `_safe_summarize` (CC13 already changed bare-except to `logger.exception`). Now add: on SUCCESSFUL insert, also `logger.info("trajectory_summarizer: run_pk=%s summarized (worked=%s..., stalled=%s..., missing=%s...)", ...)`. So future smoke tests can grep success lines, not just failure lines. Costs ~5 LOC; pays off forever.

---

## Files owned
- EDIT: `artemis/builder/trajectory_summarizer.py` (depending on B1/B2 choice)
- EDIT: `artemis/builders/executor.py` (if B1: remove the call from `run_agent`)
- EDIT: `artemis/pipelines/node_executors/agent_executor.py` (if B1: add the call after the per-node commit)
- NEW: `artemis/builder/tests/test_trajectory_post_commit.py`

**Do not touch:** the snapshot dataclass, the prompt, the GC retention, the registered tools, the pipeline state machine. Trigger-point relocation only.

---

## Acceptance criteria (demonstrate each)
1. Part A findings (the 3 investigation questions) paste in the report.
2. B1 or B2 choice + WHY (based on Part A).
3. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/ -v` — all pass. **Paste.**
4. **DB proof (the real headline)** — finally goes from N to N+~10 on a real run:
   - **Before:** `SELECT count(*) FROM agent_run_trajectory_summaries` = current.
   - Trigger ONE real pipeline run (cancel in-flight first via `POST /api/pipeline-runs/{id}/cancel`).
   - Wait for terminal.
   - **After:** count jumps by ≈ the number of agent_runs in that pipeline (~10).
   - **Paste before/after + 2 sample rows with real `what_worked` / `what_stalled` / `what_was_missing`.**
5. **Log proof:** `grep "trajectory_summarizer" preview-logs` shows **zero** `ForeignKeyViolationError` AND shows `summarized` info-logs (from Part D) for each agent_run. **Paste a sample.**
6. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
7. `git diff --stat` + `git log --oneline -1` on `worker/cc14-trajectory-post-commit`. **Paste.**

---

## Hard constraints
- The summarize_async call MUST fire after the agent_run row is committed (FK target visible). No retry loops, no FK constraint dropping.
- Local-only git. Worker commits on `worker/cc14-trajectory-post-commit`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC14 — Trajectory Post-Commit report
1. Commit / branch / worktree
2. LOC diff stats
3. Part A findings — executor commit pattern + run_agent session ownership + per-node commit location
4. Choice: B1 (move to executor) or B2 (commit-then-summarize in run_agent) — and WHY
5. Test pass summary
6. DB proof: before/after count + 2 sample rows
7. Log proof: no FK violations + info-logs for each summary
8. check.sh summary
9. Anything surprising
```

---

**Worker: CC10 (GC) + CC11 (KeyError) + CC13 (lookup race) all worked AT THEIR LAYERS — each surfaced the next. CC14 is the LAST one because there's no deeper layer: once the FK target is visible at the right time, summaries land structurally. The DB count finally going up by ~10 (not 1, not 0) on a real run is the proof. If it doesn't, that's a finding to surface honestly, not paper over.**
