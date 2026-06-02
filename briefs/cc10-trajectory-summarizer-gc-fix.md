# CC10 — Trajectory Summarizer GC Fix (unlock the self-improvement loop)

**Paste-into:** terminal-Lead → Claude Code Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc10-trajectory-gc-fix`
**Browser smoke owner:** Lead, post-merge — trigger a pipeline run; confirm summaries land in `agent_run_trajectory_summaries`.
**Report back to me by:** Jon pastes terminal-Lead's relay.
**LOC cap:** ~80.
**Priority:** HIGH — unlocks the entire self-improvement loop (audit finding A1).

---

## Why this exists — the audit finding

`docs/agent-audit-2026-05-28.md` A1: **0 trajectory summaries across 236 agent runs.** The cause is the **identical Python GC footgun CC7 fixed for pipeline dispatch**, this time in `artemis/builder/trajectory_summarizer.py:51`:

```python
asyncio.create_task(_safe_summarize(run_id), name=f"trajectory_summarize_{run_id}")
# return value discarded → event loop's weak ref → GC collects before it runs
```

Per-run summaries never land → Builder has nothing to read → definition_proposals = 0, skill proposals = 0. The original goal ("agents reflect on each run; Builder proposes updates + new skills with run-id citations") has been wired but inert for the lifetime of the app. **Fix this one line and the entire self-improvement loop comes alive.**

---

## Scope

### Part A — Retain the task (CC7-pattern, in the summarizer module)

In `artemis/builder/trajectory_summarizer.py`:
- Add a module-level `set[asyncio.Task]` and a helper:
  ```python
  _BACKGROUND_TASKS: set[asyncio.Task] = set()
  ```
- Refactor `summarize_async(run_id)` (currently does the bare `create_task`) to:
  ```python
  task = asyncio.create_task(_safe_summarize(run_id), name=f"trajectory_summarize_{run_id}")
  _BACKGROUND_TASKS.add(task)
  task.add_done_callback(_BACKGROUND_TASKS.discard)
  ```
- The strong ref keeps the task alive; the done-callback prevents the set from leaking.

### Part B — Tests

`artemis/builder/tests/test_trajectory_summarizer_gc.py` (or extend existing tests):
1. After `summarize_async(run_id)`, the task is in `_BACKGROUND_TASKS` until it completes; then it's discarded. Use `asyncio.wait` to drain the task in test, then assert the set is empty.
2. Regression: with mocked LLM adapter returning valid JSON for a real agent_run row → after the task drains, there's exactly one row in `agent_run_trajectory_summaries` for that run_id.
3. Regression: the existing `test_o1_trajectory_wire_regression.py` still passes (run_agent's row shape unchanged; summarize_async still fire-and-forget from the caller's perspective).

### Part C — Verify on the dev DB (real proof, not just unit tests)

After the fix is in, trigger ONE pipeline run, wait for the agent runs to complete, then confirm:
- Trajectory summaries committed for the new agent_runs:
  ```sql
  SELECT count(*) FROM agent_run_trajectory_summaries;  -- should jump from 0 by ~the number of agent runs that just ran
  ```
- Sample a few summary rows — `what_worked`, `what_stalled`, `what_was_missing` populated with real content (not all null).

This is the headline proof: zero → non-zero summaries.

---

## Files owned
- EDIT: `artemis/builder/trajectory_summarizer.py` (one helper + the refactor at line ~51)
- NEW or EXTEND: `artemis/builder/tests/test_trajectory_summarizer_gc.py`

**Do not touch:** the summarizer's prompt, the LLM call shape, the `_do_summarize` body, the schemas, the routes that READ summaries, the agent_builder.py flow. This is the GC fix only.

---

## Acceptance criteria (demonstrate each)
1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/ -v` — all pass. **Paste.**
2. The existing `test_o1_trajectory_wire_regression.py` still passes (unchanged run-row shape). **Paste the relevant test lines.**
3. **DB proof (the headline):** trigger a real pipeline run (or call summarize_async on an existing committed agent_run id directly), wait for completion, then `SELECT count(*), count(*) FILTER (WHERE what_worked IS NOT NULL) FROM agent_run_trajectory_summaries;` — both go UP from 0. **Paste before/after counts + 1 sample row.**
4. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
5. `git diff --stat` + `git log --oneline -1` on `worker/cc10-trajectory-gc-fix`. **Paste.**

---

## Hard constraints
- Apply the CC7 pattern exactly (module-level set + done callback). Don't introduce a different ownership pattern.
- Do NOT touch `_do_summarize`'s body or the LLM call — those are separate concerns. If you find the `AgentRun.id == run_id` lookup has a transaction-visibility issue (the run might not be committed yet when the task fires), DO NOT fix it in this brief — flag it in the report as a separate finding. We want to ISOLATE the GC fix and observe what's left.
- Local-only git. Worker commits on `worker/cc10-trajectory-gc-fix`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC10 — Trajectory Summarizer GC Fix report
1. Commit / branch / worktree
2. LOC diff stats
3. The retained-task pattern applied at line ~51 (paste the new code block)
4. Test pass summary (acceptance #1, #2)
5. DB proof: before/after summary count + 1 sample row (acceptance #3) — the headline
6. check.sh summary
7. Anything surprising — especially: did EVERY agent_run in the smoke get a summary, or only some? If some are still missing, that's a separate finding (likely the transaction-visibility issue) — flag it for a follow-up brief.
```

---

**Claude Code Worker: this is the bug that's kept the entire self-improvement loop dormant since day one. The fix is small; the proof (acceptance #3) is the count going from 0 to non-zero. If summaries land but the BODY is empty (all-null fields), that's the LLM/parse layer — also flag, separate fix.**
