# CC13 — Trajectory Summarizer: Pass Run Data, Don't Re-Query (the 3rd blocker)

**Paste-into:** terminal-Lead → Claude Code Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc13-trajectory-pass-data`
**Browser smoke owner:** Lead, post-merge — trigger one pipeline run, confirm summaries count goes from 1 → ~10 with real content.
**Report back to me by:** Jon pastes terminal-Lead's relay.
**LOC cap:** ~120.
**Priority:** HIGH — closes the self-improvement loop. CC10+CC11+CC13 together unlock it; alone, each is necessary but not sufficient.

---

## Why this exists — the 3rd hollowness layer behind the self-improvement loop

CC10 (retain task) and CC11 (escape braces) both landed. The smoke this turn proves they work as far as they go: the summarizer task NOW FIRES (CC10 ✓), and the format string NO LONGER raises (CC11 ✓). But **0 new summaries landed across ~11 agent_runs** in a real pipeline run. The server logs show:

```
trajectory_summarizer: run_id=276 not found
trajectory_summarizer: run_id=277 not found
... (11 consecutive misses)
trajectory_summarizer: run_id=286 not found
```

**Root cause — transaction visibility race.** In `artemis/builders/executor.py` lines ~396-401:
```python
await session.flush()            # writes to DB, NOT committed
await summarize_async(run.id)    # fires async task with run.id
```

`summarize_async` schedules a background task that **opens its own session** and queries `SELECT FROM agent_runs WHERE id = :run_id`. But the caller hasn't committed yet — the row is visible only to the caller's transaction. The async task's new session can't see it → `scalar_one_or_none()` returns None → the summarizer logs "run_id=X not found" and returns silently. By the time the caller commits (later, in the pipeline executor loop), the summarizer has already given up.

**This is also another silent-failure pattern** — a logger.warning that *looks* benign and gets ignored until someone reads the logs carefully. Sibling to the bare-`except Exception` swallow we banked.

---

## Scope

### Part A — Change the summarizer to accept data directly, eliminate the lookup

Refactor `artemis/builder/trajectory_summarizer.py`:

1. Define a small dataclass (or NamedTuple) for the input the summarizer needs:
   ```python
   @dataclass(frozen=True)
   class _AgentRunSnapshot:
       run_id: str          # the UUID string (from AgentRun.run_id), used for log + insert FK key
       run_pk: int          # the AgentRun.id (primary key), needed for the trajectory_summary FK
       agent_id: str | None
       status: str
       user_message: str | None
       error: str | None
   ```

2. Change `summarize_async(run_id: int)` → `summarize_async(snapshot: _AgentRunSnapshot)`. Same retained-task pattern (CC10) — just pass the snapshot, not the int.

3. In `_do_summarize`, **delete the SELECT lookup entirely**. Build the LLM prompt directly from the snapshot fields. Write the `agent_run_trajectory_summaries` row with `agent_run_id=snapshot.run_pk` (the FK target — which by the time the task actually runs and the LLM completes, is committed by the caller).

4. The "run_id=X not found" warning goes away — the lookup that produced it is gone.

### Part B — Caller passes the snapshot at the call site

In `artemis/builders/executor.py` near line 401 (the `await summarize_async(run.id)` call), build the snapshot from the in-scope `run` object and pass it:
```python
from artemis.builder.trajectory_summarizer import summarize_async, _AgentRunSnapshot
snapshot = _AgentRunSnapshot(
    run_id=run.run_id,          # UUID string
    run_pk=run.id,              # PK int
    agent_id=run.agent_id,
    status=run.status,
    user_message=run.user_message,
    error=run.error,
)
await summarize_async(snapshot)
```

If `_AgentRunSnapshot` is a private dataclass, export it from the module so the caller can construct one. (Or expose a thin builder function `_AgentRunSnapshot.from_run(run)` if cleaner.)

### Part C — Tighten the bare-except swallow (Task #28's first surface)

While in `_safe_summarize` (the wrapper that catches Exception), change:
```python
except Exception:
    pass   # silent
```
to:
```python
except Exception:
    logger.exception("trajectory_summarizer: unhandled exception during summarize")
```

Same behavior (the loop survives), but FUTURE bugs scream instead of hide. This is the smallest possible step on the bare-except cleanup (task #28); the broader sweep is a separate stream.

### Part D — Tests

`artemis/builder/tests/test_trajectory_summarizer_no_lookup.py`:
1. Calling `summarize_async(snapshot)` doesn't query the DB for the run row (mock or spy on `session.execute` — assert it's not called for an AgentRun SELECT).
2. The LLM is called with the snapshot's data embedded in the prompt.
3. The trajectory_summary row is written with the right `agent_run_id` (the run_pk).
4. (Regression) When the LLM returns invalid JSON, the fallback path still produces a row with all-null fields (don't lose that defensive behavior).
5. CC10's GC-retention test still passes (task is retained until done).

---

## Files owned
- EDIT: `artemis/builder/trajectory_summarizer.py` (refactor signature + delete the lookup + bare-except → logger.exception)
- EDIT: `artemis/builders/executor.py` (build the snapshot at the call site)
- NEW: `artemis/builder/tests/test_trajectory_summarizer_no_lookup.py`

**Do not touch:** the prompt template (CC11), the retained-task pattern (CC10), the schemas, the routes that READ summaries. Lookup elimination only.

---

## Acceptance criteria (demonstrate each)
1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/ -v` — all pass (incl. CC10/CC11 regressions). **Paste.**
2. **DB proof (the headline)** — the smoke that finally works:
   - **Before:** `SELECT count(*) FROM agent_run_trajectory_summaries` = current.
   - Trigger ONE real pipeline run (manual POST `/api/pipelines/marketing.main/run` — should 202; cancel any in-flight first). Wait for terminal.
   - **After:** count jumps by ≈ the number of agent_runs in that pipeline (~10 — scouts + qualifier + brief composer + adapter). **Paste before/after + 2 sample rows showing real `what_worked`/`what_stalled`/`what_was_missing` content.**
3. **Server logs:** `grep "trajectory_summarizer" <log>` shows NO "run_id=X not found" warnings, AND shows `trajectory_summarizer: run_id=... summarized` messages for the new runs. **Paste a sample.**
4. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
5. `git diff --stat` + `git log --oneline -1` on `worker/cc13-trajectory-pass-data`. **Paste.**

---

## Hard constraints
- Eliminate the AgentRun lookup entirely — pass data in, don't re-query.
- Keep the retained-task pattern (CC10). Keep the brace escapes (CC11). Don't touch the prompt body.
- The bare-except → logger.exception is the only behavior change inside `_safe_summarize`; otherwise that wrapper stays.
- Local-only git. Worker commits on `worker/cc13-trajectory-pass-data`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC13 — Trajectory Pass Data report
1. Commit / branch / worktree
2. LOC diff stats
3. The new signature + caller change (paste both)
4. Test pass summary
5. DB proof (the headline): before/after count + 2 sample rows with real content
6. Log proof: no "run_id=X not found"; "summarized" messages present
7. check.sh summary
8. Anything surprising — especially anything else the bare-except was hiding
```

---

**Worker: CC10 fixed the GC layer. CC11 fixed the format-string layer. CC13 fixes the lookup-race layer — and that's the LAST one. The DB count finally going up by ~10 (not just by 1) on a real pipeline run is the moment the self-improvement loop is actually alive. Once landed, Lead opens the Agent Builder on a scout and verifies the loop's CONSUMER side — Builder reading summaries + proposing changes — is also real (or finds CC14).**
