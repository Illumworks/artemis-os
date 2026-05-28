# CC16 — Trajectory Snapshot Enrichment (give the summarizer something real to analyze)

**Paste-into:** terminal-Lead → Claude Code Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc16-snapshot-enrichment`
**Browser smoke owner:** Lead, post-merge — trigger one pipeline run, sample summaries, confirm content is now *diagnostic* (cites specific tool calls / signal counts), not meta-complaint about missing data.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~250 (snapshot dataclass expansion + extraction helpers + prompt update + tests).
**Priority:** HIGH — closes the 5th and likely final layer of the self-improvement loop.

---

## Why this exists — the 5th layer surfaced by CC14's smoke

CC14's smoke showed 11/11 summaries committed (perfect 1:1 with agent_runs), but the content is hollow. Every summary reads like:

> *what_was_missing: "No conversation messages were provided in the run data, so specific tool call sequences, signal counts emitted, or any per-source fetch failures cannot be observed from this record alone."*

The LLM is correctly recognizing it has nothing to analyze. `AgentRunSnapshot` carries `status`/`error`/`user_message` only — not the conversation transcript, tool calls, or emission counts. The summarizer's prompt asks for "what worked / stalled / missing"; with no transcript, the LLM produces meta-complaints instead of diagnoses.

CC14 made the WRITES succeed. CC16 makes the WRITES MEANINGFUL.

---

## Scope

### Part A — Investigate where the conversation + tool-call data lives (~20 LOC findings)

Before coding, answer these in the report:

1. After `run_turn()` returns, the agent's full conversation (`result.messages` — including `ToolUseBlock` + `ToolResultBlock`) is in scope at `run_agent`. Confirm. Does it persist to `agent_runs.shared_context` or `agent_context` table, or only live in memory until run_agent returns?
2. Are tool_use blocks structured enough to extract `(tool_name, args_summary, result_summary, success_flag)` from? Sample one from a real run if you can.
3. Where do we count "signals emitted by THIS agent_run"? `signal_queue.provenance->>'agent_run_id' = agent_run.run_id`? Confirm and paste a count query.
4. Are tool call counts attributable per-run (does the agent loop log them, or do we have to walk the messages)?

Paste answers as Part A of the report. This determines the cleanest extraction approach.

### Part B — Expand `AgentRunSnapshot` with the structured extract

In `artemis/builder/trajectory_summarizer.py`, expand the snapshot:

```python
@dataclass(frozen=True)
class _ToolCallSummary:
    name: str                       # e.g. "signal_queue.write"
    success: bool
    result_preview: str              # first ~100 chars of the result/error, no PII

@dataclass(frozen=True)
class _AgentRunSnapshot:
    # existing (CC13):
    run_id: str
    run_pk: int
    agent_id: str | None
    status: str
    user_message: str | None
    error: str | None
    # NEW (CC16):
    tool_calls: tuple[_ToolCallSummary, ...]   # ordered, what the agent actually did
    signals_emitted: int                        # rows in signal_queue with provenance.agent_run_id == run_id
    final_text: str | None                      # the agent's final assistant message, truncated to ~500 chars
    duration_ms: int | None                     # wall-clock for the run (for "took 4m12s" context)
```

**Why structured extract vs raw transcript:** the raw conversation can be many KB — eats tokens, dilutes the signal. A structured extract is denser + the LLM can reason directly over it ("you called news_api.search 3 times, signal_queue.write 0 times — your prompt may not be encouraging emission").

### Part C — Build the snapshot at the call site

In `artemis/builders/executor.py` where `run_agent` builds the snapshot (post-CC13/CC14), walk `result.messages` and produce the structured fields:

- For each `ToolUseBlock` in the conversation, find the matching `ToolResultBlock` (by `tool_use_id`), build a `_ToolCallSummary`.
- Count rows in `signal_queue` with `provenance->>'agent_run_id' == run.run_id` for `signals_emitted` (one query, post-commit since CC14 already committed the agent_run; this query happens BEFORE we fire summarize_async).
- Extract `final_text` from the last assistant message's text blocks, truncate to 500 chars.
- Compute `duration_ms` from `started_at`/`completed_at`.

Keep the extraction in a testable helper: `def _build_snapshot(run: AgentRun, result: RunResult, signals_emitted: int) -> _AgentRunSnapshot`.

### Part D — Update the prompt to use the new fields

`_TRAJECTORY_PROMPT` currently feeds `{run_data}` JSON. Expand the JSON to include the new fields (or restructure the prompt to render `tool_calls`, `final_text` etc. as readable sections). The prompt's instructions stay — extract what_worked / what_stalled / what_was_missing — but now the LLM has substance.

Suggested prompt addition:
```
Look at the tool_calls sequence: which tools did the agent call, did they
succeed, did the agent never call expected tools (e.g. did a scout never call
signal_queue.write)? Look at signals_emitted: did the agent produce work
product? Look at final_text: did the agent state confusion, ask for
clarification, or describe what it did?
```

### Part E — Tests

`artemis/builder/tests/test_trajectory_snapshot_enrichment.py`:
1. `_build_snapshot` extracts `_ToolCallSummary` tuples from a fake `RunResult` with tool_use+tool_result blocks.
2. `signals_emitted` count is correctly attributed via `provenance->>'agent_run_id'`.
3. `final_text` truncates at 500 chars and uses the LAST assistant message.
4. End-to-end (integration): run summarize() with a snapshot containing tool calls — the LLM prompt JSON includes them — assert via spy on the adapter mock.
5. Regression: CC10/CC11/CC13/CC14 tests still pass.

### Part F — Defensive observability (Part D of CC14 was lost — re-add it here)

CC14 was supposed to add `logger.info("trajectory_summarizer: run_pk=%s summarized ...")` on successful insert (CC14's Part D). The CC14 smoke showed this didn't make it to the uvicorn log output. Add it (or fix it) here so smoke runs can grep for success. ~5 LOC.

---

## Files owned

- EDIT: `artemis/builder/trajectory_summarizer.py` (expand snapshot dataclass + prompt + the info log)
- EDIT: `artemis/builders/executor.py` (build the enriched snapshot at the call site)
- NEW: `artemis/builder/tests/test_trajectory_snapshot_enrichment.py`

**Do not touch:** the FK / commit ordering (CC14), the retained-task pattern (CC10), the brace escapes (CC11), the registered tools, the pipeline state machine, anything outside the snapshot+extract+prompt path.

---

## Acceptance criteria

1. Part A findings (4 investigation questions) in the report.
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/ -v` — all pass, including CC10-CC14 regressions. **Paste.**
3. **DB proof (the real headline)** — summaries now have diagnostic content, not meta-complaints:
   - Trigger one pipeline run (cancel in-flight first).
   - Wait for terminal (~14 min).
   - Query 2 summary rows; their `what_worked` / `what_stalled` / `what_was_missing` should reference **specific tool calls** (by name), **signal emission counts**, or **specific assistant statements** — NOT "no conversation messages were provided."
   - **Paste 2 sample rows.** Lead will verify the content is diagnostic, not generic.
4. **Log proof:** grep server logs — `trajectory_summarizer: run_pk=N summarized` info lines now visible (Part F). **Paste a sample.**
5. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
6. `git diff --stat` + `git log --oneline -1` on `worker/cc16-snapshot-enrichment`. **Paste.**

---

## Hard constraints

- Use a **structured extract**, not the raw transcript. Token budget matters; raw messages are KB.
- Do not change the FK / commit semantics (CC14 stays). Do not touch the GC retention (CC10 stays).
- The `signals_emitted` query reads from `signal_queue`. Run it AFTER the agent_run is committed (CC14 makes this safe). One simple query.
- Truncate `final_text` to ~500 chars. Truncate `result_preview` per tool-call to ~100 chars. No PII concerns at this scale, but bounded sizes prevent prompt bloat.
- Local-only git. Worker commits on `worker/cc16-snapshot-enrichment`; terminal-Lead merges after Lead approves.

---

## Report-back format

```
CC16 — Snapshot Enrichment report
1. Commit / branch / worktree
2. LOC diff stats
3. Part A findings — where conversation + tool calls + signals_emitted live
4. _ToolCallSummary + _AgentRunSnapshot final shape (paste the dataclasses)
5. Test pass summary
6. DB proof — 2 sample summary rows with diagnostic content (acceptance #3)
7. Log proof — summarized info lines visible (acceptance #4)
8. check.sh summary
9. Anything surprising
```

---

**Worker: CC14 made the writes succeed. CC16 makes them MEAN something. The win condition is a summary row that names a specific tool call or signal count — e.g., "Scout called news_api.search 3 times but signal_queue.write 0 times; final_text suggests the agent treated its prompt as a spec to discuss." That's diagnostic. "No conversation messages were provided" is the failure signal — if you see that in your sample, something's still wrong with the extraction. The smoke (acceptance #3) is the bar; unit-test-green is not "the loop is meaningful."**
