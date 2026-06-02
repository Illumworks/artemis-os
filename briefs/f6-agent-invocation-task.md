# F6 — Agent Invocation Task (close the loop) + regional_news tool fix

**Paste-into:** terminal-Lead. It spawns a Sonnet Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/f6-invocation-task`
**Browser smoke owner:** Lead (this session), post-merge — real pipeline run, verify signals appear.
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** 150 (full-diff insertions including tests). Hard stop at 200.

---

## Why this brief exists — the smoke finding

Phase BH wired everything: rich prompts (F2), tools (P2/P3), spec-sourced reason codes (F1/F5), blueprints (P1/P4). But the closing real-LLM pipeline smoke (run `967e4933`) produced **zero signals** despite scouts having real fetch tools + `signal_queue.write`. Root cause:

Every scout responded **conversationally** — e.g. *"I have the Regional News Scout spec. What's your ask? Are you: Implementing this scout..."* — instead of executing a scan. Why: `agent_executor.execute_agent_node()` calls `run_agent()` WITHOUT a `user_message`, so it falls back to `agent.goal` ("Catch signals Starbridge misses.") — a *descriptive* sentence, not an *imperative task*. The agent sees "here's who you are" + "here's your goal" and asks "what do you want me to do?" It never calls its tools.

The fix: the pipeline must invoke agents with an **imperative task message** that tells them to act *now* using their tools.

Plus a P1 consistency gap surfaced: `marketing.scout.regional_news` is missing `signal_queue.write` (and the standard helper tools the other 8 scouts have). Fix that too so regional_news can emit.

---

## Scope

### Part A — Imperative invocation task in agent_executor

In `artemis/pipelines/node_executors/agent_executor.py`, before the `run_agent(...)` call (~line 143), synthesize an imperative task and pass it as `user_message`.

Forward-compatible design — honor a per-node override if present, else synthesize by agent role:

```python
config = node.get("config") or {}
instruction = config.get("instruction")  # future: per-node task editable in canvas
if not instruction:
    if agent_id.startswith("marketing.scout."):
        instruction = (
            "Execute your scan NOW. Use your tools: call your fetch tools "
            "(e.g. news_api.search, state_doe.fetch, board_minutes.fetch) to pull "
            "current items from your sources, evaluate each against your allowed "
            "reason codes, and call signal_queue.write for EACH qualifying signal "
            "(one call per signal). Use reason_codes.get_allowlist if unsure which "
            "codes you may emit. When done, briefly report how many signals you "
            "emitted. If nothing qualifies this run, say so explicitly — do not ask "
            "for clarification; you are running autonomously."
        )
    elif agent_id.startswith("marketing.qualifier."):
        instruction = (
            "Process the pending signals NOW. Use your tools to read context and "
            "apply your qualification logic. Do not ask for clarification; act "
            "autonomously and report your result."
        )
    elif agent_id.startswith("marketing.content."):
        instruction = (
            "Assemble your deliverable NOW from the qualified inputs in context. "
            "Use your tools. Do not ask for clarification; act autonomously."
        )
    else:
        instruction = "Execute your task now using your available tools. Act autonomously; do not ask for clarification."
```

Then pass it: `run_agent(session=session, agent_id=agent_id, shared_context=shared_context, model_adapter=resolved_adapter, user_message=instruction)`.

(Verify `run_agent`'s signature accepts `user_message` — it does; `effective_message = user_message or agent.goal or "Please proceed."` It's just never passed today.)

### Part B — Fix regional_news tools

In `docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md`, the `## Tools required` section currently yields only `["news_api.search", "board_minutes.fetch", "state_doe.fetch", "pdf_extractor.extract"]`. Add the standard scout emit + helper tools that the other 8 scouts have, so it parses to include:
- `signal_queue.write`
- `memory_layer.upsert_last_seen`
- `territory_config.get_priority_states`
- `reason_codes.get_allowlist`

Match the format the seed parser extracts (the code-fence list under `## Tools required`). Then re-seed (see Part C).

### Part C — Re-seed + verify

After Part B, re-seed:
```bash
uv run python -c "
import asyncio
from artemis.db import SessionLocal
from artemis.marketing.seeds.marketing_agents import seed_marketing_agents
async def main():
    async with SessionLocal() as s:
        print(await seed_marketing_agents(s)); await s.commit()
asyncio.run(main())
"
```
Confirm regional_news now has `signal_queue.write` in its tools.

### Part D — Tests

`artemis/pipelines/tests/test_agent_invocation_task.py` (new):
1. Scout node → assert the synthesized `user_message` passed to `run_agent` contains "signal_queue.write" and "Execute your scan" (mock `run_agent`, capture kwargs).
2. Node with explicit `config.instruction = "custom task"` → assert that custom string is passed, not the synthesized one.
3. Qualifier node → assert the qualifier imperative is used.
4. Non-marketing agent → assert the generic fallback is used.

Mock `run_agent` to capture the `user_message` kwarg; don't make real LLM calls in tests.

---

## Files owned by this stream

- EDIT: `artemis/pipelines/node_executors/agent_executor.py` (Part A)
- EDIT: `docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md` (Part B)
- NEW: `artemis/pipelines/tests/test_agent_invocation_task.py` (Part D)

**Do not touch:** run_agent / executor.py (just call it with the new kwarg), the tool files, josh_spec, the seed loader logic (only re-run it), other blueprints.

---

## Acceptance criteria (Worker must demonstrate each)

1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/pipelines/tests/test_agent_invocation_task.py -v` — all pass. **Paste.**
2. regional_news tools after re-seed include signal_queue.write: `psql -d artemis_os -tAc "SELECT tools FROM agents WHERE agent_id='marketing.scout.regional_news';"`. **Paste.**
3. **The real loop closes** — trigger a pipeline run and confirm signals appear:
   ```bash
   before=$(psql -d artemis_os -tAc "SELECT count(*) FROM signal_queue;")
   curl -s -X POST http://localhost:8000/api/pipelines/marketing.main/run -H "Content-Type: application/json" -d '{"triggered_by":"f6-verify"}'
   # wait for the run to reach terminal (poll runs?limit=1 status), then:
   after=$(psql -d artemis_os -tAc "SELECT count(*) FROM signal_queue;")
   echo "before=$before after=$after"
   psql -d artemis_os -t -A -F' | ' -c "SELECT discovered_by, headline, reason_codes FROM signal_queue ORDER BY id DESC LIMIT 5;"
   ```
   **Paste output. `after` MUST be greater than `before`** — at least one real signal emitted. (Requires uvicorn running with the merged code: `pkill -9 -f uvicorn; uv run uvicorn artemis.main:app --port 8000 &` then `uv run alembic upgrade head` if needed.)
4. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste summary.**
5. `git diff --stat` ≤ 150 (200 hard stop). **Paste.**
6. `git log --oneline -1` on `worker/f6-invocation-task`. **Paste.**

---

## Hard constraints

- LOC cap: 150 (200 hard stop).
- The headline acceptance is #3: a real pipeline run must produce ≥1 new signal. If it doesn't, do NOT claim success — diagnose (did the scout call tools? check agent_runs + logs) and report what you found.
- Do not modify run_agent's internals — only pass the new user_message kwarg.
- Local-only git. Worker commits on `worker/f6-invocation-task`; terminal-Lead merges after Lead approves.

---

## Report-back format (Worker pastes verbatim, filled in)

```
F6 — Agent Invocation Task report

1. Commit hash / branch / worktree
2. LOC diff stats
3. Test pass summary (acceptance #1)
4. regional_news tools after re-seed (acceptance #2)
5. REAL LOOP CLOSE: before/after signal counts + the signal rows (acceptance #3)
6. check.sh summary
7. If signals did NOT appear: full diagnosis (scout output_summary, did it call tools, agent_runs detail)
8. Anything surprising
```

---

**End of brief. Sonnet Worker: the headline is acceptance #3 — real signals in signal_queue after a real run. Operating principle: never claim the loop closed without the before/after count proving it. If the LLM still goes conversational despite the imperative, that's a finding to report, not to paper over.**
