# CC17 — MCP Tool Invocation Log (make tool_calls visible for claude-code path)

**Paste-into:** terminal-Lead → Claude Code Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc17-mcp-tool-log`
**Browser smoke owner:** Lead, post-merge — confirm tool_calls in snapshots match signal_queue ground truth.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~300 (migration + MCP server logging + snapshot extraction rewrite + tests).
**Priority:** HIGH — closes the 6th layer; makes the summarizer's diagnoses TRUE not just plausible.

---

## Why this exists — the 6th layer (visibility, not behavior)

CC16's smoke produced 11 diagnostic summaries that named specific tools, env vars, signal IDs. Apparent finding: *"agents narrate without acting; called zero tools; hallucinated qualification tables."* That conclusion turned out to be **partially wrong because the input data was wrong.**

Lead verified directly: signal_queue row 181's `provenance.agent_run_id` = `7a1958d8-...`, which IS the regional_news run that CC16 reported with `tool_calls=[]`. So regional_news REALLY called `signal_queue.write` via MCP. The summarizer LLM, given `tool_calls=[]` + `signals_emitted=1`, reasonably concluded *"the agent claimed to call write but it doesn't appear in trace, suggesting tool call capture failed or the agent fabricated results."* — and was *correct* about the cause (capture failed), but the summary's framing made it sound like an agent bug, not an extraction bug.

**The root cause is in CC16's extraction**: it walks `result.messages` from `run_turn`. But for **claude-code-provider agents on the subscription/MCP path** (CC2's bridge), `run_turn` is bypassed — `run_with_tools` calls `claude -p --mcp-config`, and claude-code runs its OWN tool loop inside the subprocess. The `ToolUseBlock`/`ToolResultBlock` events happen *inside* claude-code's process, not in artemis's `result.messages`. So CC16 sees an empty messages list and reports `tool_calls=[]`.

**The MCP server (our process) sees every tool invocation.** That's the right place to record them. CC17 wires that up so snapshot extraction reads from a tool-invocation log, not from `result.messages`.

---

## Scope

### Part A — Investigate first (~30 LOC findings)

Before coding, answer these in the report:

1. **Confirm the extraction-blindness hypothesis** for a known case: `provenance.agent_run_id` from `signal_queue` row 181 vs the corresponding agent_run's snapshot `tool_calls`. (Lead already verified — repeat for due diligence.) Are there ANY agents in the last smoke whose snapshot tool_calls is non-empty? If yes, which provider were they on?
2. **MCP server's existing logging.** `artemis/tools/mcp_server.py` — does it log each tool invocation today, even to stderr? If yes, what shape (run_id-correlatable)? Salvageable as a starting point?
3. **Which call site to log from.** In the MCP server, where do tool calls land — a central handler, or per-tool? One central log point is cleaner.
4. **Will a tool_invocations table conflict with anything?** Check for existing tables or naming clashes.

Paste answers as Part A.

### Part B — New `tool_invocations` table (migration)

Create a new alembic migration:

```python
op.create_table(
    "tool_invocations",
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("agent_run_id", sa.Text, nullable=False, index=True),  # UUID — matches agent_runs.run_id
    sa.Column("pipeline_run_id", sa.Text, nullable=True, index=True),  # UUID or None
    sa.Column("tool_name", sa.Text, nullable=False),     # e.g. "signal_queue.write" (artemis-style, NOT mcp__artemis__...)
    sa.Column("args_summary", sa.Text, nullable=True),    # truncated/JSON summary, ≤500 chars
    sa.Column("result_preview", sa.Text, nullable=True),  # tool impl's return string, truncated ≤500 chars
    sa.Column("success", sa.Boolean, nullable=False),
    sa.Column("invoked_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
)
```

No FK on agent_run_id — tool invocations may land *during* the same long pipeline transaction (same CC14 race shape). Logical reference, indexed, no FK constraint.

### Part C — MCP server logs every invocation

In `artemis/tools/mcp_server.py`, around the central tool-dispatch point (each tool's handler), wrap the impl call:

```python
# pseudo:
async def _dispatch(tool_name: str, args: dict) -> str:
    args_summary = _summarize_args(args, max_len=500)  # truncate
    try:
        result = await impl(args)
        await _log_invocation(tool_name=tool_name, args_summary=args_summary,
                              result_preview=result[:500] if isinstance(result, str) else None,
                              success=not result.startswith(("VALIDATION_ERROR", "PERMISSION_DENIED", "STUB:")))
        return result
    except Exception as exc:
        await _log_invocation(tool_name=tool_name, args_summary=args_summary,
                              result_preview=f"EXCEPTION: {exc!s}"[:500], success=False)
        raise
```

Tool name is the **artemis-style** name (`signal_queue.write`), not the MCP-prefixed name (`mcp__artemis__signal_queue_write`) — use `artemis_tool_name(mcp_name)` (the existing CC1 helper) to translate.

The MCP server has its own session (per CC1) — log inserts into its session, committed independently (each invocation is its own committed fact, like signal_queue.write itself).

### Part D — Snapshot extraction reads from tool_invocations

In `artemis/builders/executor.py`'s snapshot builder (where CC16 walks `result.messages`):

For **claude-code provider** runs, walking `result.messages` won't find tool calls. After commit, query `tool_invocations` by `agent_run_id`:

```python
invocations = await session.execute(
    select(ToolInvocation)
    .where(ToolInvocation.agent_run_id == run.run_id)
    .order_by(ToolInvocation.invoked_at)
)
tool_calls = tuple(
    _ToolCallSummary(name=row.tool_name, success=row.success,
                     result_preview=row.result_preview or "")
    for row in invocations.scalars().all()
)
```

For **anthropic/in-process** runs (run_turn path), CC16's existing extraction from `result.messages` still works — that path captures tool_use/tool_result blocks correctly. **Keep both paths** — the extractor picks based on provider OR (cleaner) always queries `tool_invocations` first and falls back to message-walking if empty. Worker decides which is cleaner; document the choice.

### Part E — Tests

`artemis/tools/tests/test_tool_invocation_log.py`:
1. A call to a real tool through the MCP dispatch helper writes one row to `tool_invocations` with the right `agent_run_id`, `tool_name` (artemis-style), `success=True`.
2. A failing tool call (impl raises) still logs, with `success=False` and the exception summary in `result_preview`.
3. STUB / VALIDATION_ERROR / PERMISSION_DENIED returns are logged with `success=False` (so the summarizer can see them).

`artemis/builders/tests/test_snapshot_from_invocations.py`:
4. Snapshot extraction reads from `tool_invocations` for an agent_run, ordered, mapped to `_ToolCallSummary`.
5. If `tool_invocations` is empty AND `result.messages` has tool_use blocks, falls back to message-walking (in-process path).
6. Regression: CC10/11/13/14/16 tests still pass.

### Part F — Smoke verification (Lead's job, but list here so the worker knows what's checked)

Post-merge: trigger one pipeline run. For the run where regional_news emits a signal:
- `tool_invocations` has rows for that agent_run including `tool_name='signal_queue.write'`.
- The snapshot's `tool_calls` reflects those rows.
- The trajectory summary now says something like *"called signal_queue.write 1× (success); also called news_api.search Nx"* — not *"tool_calls is empty, agent fabricated."*

For the qualifier:
- `tool_invocations` shows whether it called `signal_queue.get`, `signal_queue.update_status`, etc.
- The summary then either says *"qualifier called update_status 7× transitioning signals to qualified"* OR *"qualifier really called zero tools — actual hallucination, not extraction bug"*. **Either answer is useful** — it tells us the truth.

---

## Files owned
- NEW: an alembic migration for `tool_invocations`
- NEW: `artemis/tools/models.py` (or extend existing) — `ToolInvocation` ORM
- EDIT: `artemis/tools/mcp_server.py` — central dispatch wrapper that logs
- EDIT: `artemis/builders/executor.py` — snapshot extraction reads from `tool_invocations`
- NEW: `artemis/tools/tests/test_tool_invocation_log.py`
- NEW: `artemis/builders/tests/test_snapshot_from_invocations.py`

**Do not touch:** the in-process run_turn path (CC2's anthropic-provider path stays unchanged; its `result.messages` extraction in CC16 remains the fallback). Trajectory_summarizer prompt + dataclass stay unchanged (CC16's snapshot shape is correct; only the *source* of `tool_calls` changes).

---

## Acceptance criteria

1. Part A findings (4 investigation questions) paste in the report.
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/tools/tests/ artemis/builders/tests/ -v` — all pass incl. CC10-CC16 regressions. **Paste.**
3. `uv run alembic upgrade head` runs cleanly. **Paste.**
4. **DB proof (the headline)** — for a real pipeline run, `tool_invocations` has rows for regional_news that include `signal_queue.write`. Snapshot's `tool_calls` for that run reflects those rows.
5. **Summary content proof** — sample 2 trajectory summaries from a real run. They should now say specific things like *"called signal_queue.write 1× successfully"* OR (where the agent really did nothing) *"called zero tools"* with CONFIDENCE not speculation. **Paste both.**
6. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
7. `git diff --stat` + `git log --oneline -1` on `worker/cc17-mcp-tool-log`. **Paste.**

---

## Hard constraints

- The MCP server is the ground-truth source. Don't try to reverse-engineer tool calls from claude-code's stdout — log them at the source.
- `tool_invocations.agent_run_id` is a **UUID string** (matches `agent_runs.run_id`), not the int PK. No FK constraint (avoid CC14-style race).
- `tool_name` stored as artemis-style (`signal_queue.write`), not MCP-prefixed.
- Both extraction paths (MCP via `tool_invocations`, in-process via `result.messages`) coexist. Don't break the anthropic provider's path.
- The MCP server's logging session is its own — independent commit. Each invocation is its own committed fact.
- Local-only git. Worker commits on `worker/cc17-mcp-tool-log`; terminal-Lead merges after Lead approves.

---

## Report-back format

```
CC17 — MCP Tool Invocation Log report
1. Commit / branch / worktree
2. LOC diff stats
3. Part A findings — extraction-blindness confirmed + existing MCP logging + central dispatch + table conflicts
4. Migration + ToolInvocation model (paste shape)
5. Test pass summary
6. DB proof — tool_invocations has rows for regional_news including signal_queue.write
7. Summary content proof — 2 sample rows that name tools with confidence
8. check.sh summary
9. Anything surprising — especially about how MCP central-dispatches today
```

---

**Worker: this is the 6th layer of self-improvement hollowness — and it's a visibility bug, not a behavior bug. Agents ARE calling tools; we couldn't see them. The MCP server is *our process*, so it knows every call. Log them at the source and snapshot extraction reads truth. The headline (#5) is summary rows that name specific tools with confidence — and if some agent really DID call zero tools, the summary will now say so honestly.**
