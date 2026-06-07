# H3 — Trajectory summarizer Pydantic + Builder revalidation (close the producer→Builder pollution chain)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/h3-trajectory-pydantic`
**Browser smoke owner:** Lead, post-merge — trigger a pipeline run, verify summaries land with constrained shape; open Builder against the agent, verify the Builder's read of summaries treats them as LLM-generated content with provenance.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~150 (Pydantic models + revalidation wrapper + Builder read-site hardening + tests).
**Priority:** HIGH — closes the worst-shape pollution chain (🔴 surface #2 per `docs/hallucination-audit-2026-05-29.md`). Trajectory summaries feed the Builder; a hallucinated summary corrupts every subsequent Builder reasoning turn.

---

## Why this exists

Per the hallucination audit:

> The trajectory summarizer (`artemis/builder/trajectory_summarizer.py:241-294`) emits JSON `{what_worked, what_stalled, what_was_missing}` with `json.loads` only. NO Pydantic validation. NO length limit. NO enum check. Written directly to `agent_run_trajectory_summaries`. **Read back by the Builder when an operator opens an edit session.** A hallucinated summary ("agent successfully called tool X" when it actually crashed) pollutes the Builder's reasoning the next time anyone touches that agent.

This is the second-order amplification CC19's smoke surfaced empirically. The summarizer's output appears to be accurate today, but there's nothing enforcing that it stays accurate. And the Builder treats summaries as if they were source-of-truth observations, when in fact they're LLM-generated narrative.

H3 has two halves:

1. **Validation at write time** (the producer side) — Pydantic constraints on what the summarizer can emit, with retry-on-failure
2. **Provenance at read time** (the consumer side) — Builder treats summaries as LLM-generated content with explicit provenance, not as source-of-truth facts

After H3, summaries are bounded in shape AND the Builder knows it's reasoning over inferred content (which informs how it should ground via CC20's tools BEFORE proposing).

---

## Scope

### Part A — Pydantic model for the trajectory summary emission

In `artemis/builder/trajectory_summarizer.py` (or new `artemis/builder/trajectory_schemas.py`), define:

```python
class TrajectorySummary(BaseModel):
    what_worked: str | None = Field(default=None, max_length=2000)
    what_stalled: str | None = Field(default=None, max_length=2000)
    what_was_missing: str | None = Field(default=None, max_length=2000)
    
    # Future-proofing: explicit confidence + provenance markers
    confidence: Literal["high", "medium", "low"] = "medium"
    evidence_tool_calls: list[str] = Field(default_factory=list)  # tool names cited
    evidence_signal_ids: list[int] = Field(default_factory=list)  # signal IDs cited
    
    model_config = ConfigDict(extra="forbid")
```

Replace the bare `json.loads` + dict access at lines 267-288 with:

```python
try:
    parsed_summary = TrajectorySummary.model_validate_json(clean)
except ValidationError as exc:
    logger.warning("trajectory_summarizer: validation failed for run_id=%s: %s", snapshot.run_id, exc)
    parsed_summary = TrajectorySummary()  # all-null, safe default
```

The all-null default preserves the audit row but doesn't pollute Builder reasoning.

### Part B — Retry-on-validation-failure (the producer-side recovery loop)

Same pattern as H2. If the summarizer's first LLM call produces invalid JSON, retry once with the validation error appended to the user message. Capped at 1 retry to avoid loops.

Implementation: wrap the `run_turn` call in a small helper:

```python
async def _summarize_with_retry(snapshot, adapter, db_session, max_retries=1):
    for attempt in range(max_retries + 1):
        result = await run_turn(...)
        text = _extract_text(result)
        try:
            return TrajectorySummary.model_validate_json(_strip_markdown(text))
        except ValidationError as exc:
            if attempt < max_retries:
                # Append error to next turn's context
                ...
            else:
                logger.warning("trajectory_summarizer: persistent validation failure: %s", exc)
                return TrajectorySummary()
```

### Part C — Builder read-site hardening (the consumer-side fix)

In `artemis/builder/agent_builder.py:371-384` (where the Builder reads trajectory summaries via `read_recent_runs` and injects them into its system prompt), add a provenance marker:

When the Builder's system prompt receives trajectory summaries, frame them as LLM-generated inferences NOT as ground truth:

```python
# In build_edit_session_opener or wherever summaries are injected
summary_block = f"""
## Recent agent run analysis (LLM-generated trajectory summaries — treat as inferences, not facts)

The following summaries were produced by the trajectory_summarizer LLM after each run.
They reflect what the analyzer THOUGHT happened, not necessarily what actually happened.
Before proposing changes based on them, verify against the actual tool_invocations + agent_runs
records using the grounding tools (read_tool_signatures, read_db_schema).

{formatted_summaries}
"""
```

This is verbal — but the Builder LLM is sensitive to "this is inference vs this is fact" framing in its system prompt. Combined with CC20's grounding tools, the Builder is now structurally instructed to cross-check inferences against schema truth before proposing.

### Part D — Provenance marker on the read API itself

The `read_recent_runs` tool (in CC20-extended MCP + the existing in-process closure) currently returns raw trajectory summary fields. Add a wrapper that:

1. Returns the summary content as today
2. AND returns a `provenance` field: `{"source": "llm_trajectory_summarizer", "confidence": "<from-schema>", "generated_at": "<timestamp>", "model": "<provider>"}`
3. The Builder LLM sees the provenance and can reason about reliability

Example return shape:
```json
[
  {
    "run_id": 329,
    "what_worked": "...",
    "what_stalled": "...",
    "what_was_missing": "...",
    "provenance": {
      "source": "llm_trajectory_summarizer",
      "confidence": "medium",
      "generated_at": "2026-05-29T13:35:31Z",
      "model": "claude-sonnet-4-6"
    }
  }
]
```

### Part E — Tests

`artemis/builder/tests/test_h3_trajectory_summarizer_pydantic.py`:

1. **Valid summary passes Pydantic.** Use a real fixture from the existing `agent_run_trajectory_summaries` table; validate it parses cleanly under the new model.
2. **Oversized field is rejected.** LLM emits `what_worked` with 5000 chars (above the 2000 limit). Validation fails; null fallback used.
3. **Extra fields are rejected.** LLM emits `{"what_worked": "...", "hallucinated_field": "..."}`. `extra="forbid"` triggers rejection.
4. **Validation failure triggers retry.** Mock the adapter to return invalid JSON on first call, valid on second. Verify the second call's result lands.
5. **Persistent validation failure produces null row.** Mock adapter to always return invalid JSON. Verify the trajectory row lands with NULL fields (preserves audit) and a warning is logged.
6. **Builder read-site provenance.** Call `read_recent_runs` via the registry. Verify each returned summary has a `provenance` block with the expected fields.
7. **End-to-end Builder revalidation behavior.** Mock a Builder session that opens an edit on an agent with summaries. Verify the system prompt contains the "LLM-generated inferences" framing.

---

## Files owned

- NEW or EDIT: `artemis/builder/trajectory_schemas.py` (Pydantic model for trajectory summary)
- EDIT: `artemis/builder/trajectory_summarizer.py` (use Pydantic; retry-on-failure helper)
- EDIT: `artemis/builder/agent_builder.py` (provenance framing in `build_edit_session_opener`)
- EDIT: `artemis/builder/engine.py` (the `read_recent_runs` implementation — add `provenance` field to return shape)
- EDIT: `artemis/tools/mcp_server.py` (the `builder_read_recent_runs` MCP tool — same provenance addition)
- NEW: `artemis/builder/tests/test_h3_trajectory_summarizer_pydantic.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047` unchanged. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_h3_trajectory_summarizer_pydantic.py -v` — all 7 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt failures. **Paste.**
4. **Live smoke (Lead does this post-merge):**
   - Trigger a pipeline run (or wait for the next scheduled one)
   - Inspect new `agent_run_trajectory_summaries` rows — verify all field lengths under 2000 chars, all rows have one of three valid `confidence` values if writing the new field
   - Open Builder for an agent with summaries via API (curl session creation)
   - Inspect the response — verify the system prompt contains "LLM-generated trajectory summaries — treat as inferences, not facts"
   - Verify `read_recent_runs` response includes the `provenance` block
   - **Paste the validation evidence.**
5. `git diff --stat` + `git log --oneline -1` on `worker/h3-trajectory-pydantic`. **Paste.**

---

## Hard constraints

- **No regression on existing trajectory summaries.** The 35 existing summaries in `agent_run_trajectory_summaries` should continue to be readable. The provenance field is additive on read; the Pydantic validation applies to NEW writes only. Don't backfill or migrate old rows.
- **Failure isolation.** Same as M1's pattern — memory/summary write failure must NOT break the agent run or the trajectory_summarizer's main flow. Validation failure → null row + warning, not exception.
- **Don't change the trajectory summarizer's prompt** in this brief (that would be H3-prime if the prompt itself needs updating). H3 is about enforcing what the prompt already aims for.
- **Local-only git.** Worker commits on `worker/h3-trajectory-pydantic`; terminal-Lead merges after Lead approves.

---

## Knock-on for M1

M1 (memory: trajectory → observation) was queued to fire next. **M1 should fire AFTER H3 merges.** Reason: M1 writes trajectory summary content to `memory_observations` as durable memory. If the summary content is unvalidated, M1 amplifies the pollution into a third surface (memory → Builder → Floating Artemis chain). With H3 in place, M1 writes verified observations.

Brief sequencing dependency now explicit:
- H3 BLOCKS M1 (data quality precondition)
- H1 BLOCKS none (independent; H1 is platform-level)
- H2 BLOCKS none (independent; scout-specific)
- H4 BLOCKS none (independent; meeting-specific)

---

## Report-back format

```
H3 — Trajectory summarizer Pydantic + Builder revalidation report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially the retry-on-failure test #4 and provenance test #6)
4. Live smoke results — PASTE the system_prompt framing change from Builder and the provenance block from read_recent_runs
5. Pydantic model location — chose new file vs existing? Reasoning?
6. Retry implementation — single-retry capped? Any infinite-loop guards?
7. Helper extraction — did you use H2's shared `validate_llm_json_emission` helper (if it exists)? Or factor your own?
8. check.sh summary
9. Anything surprising — especially around how the Builder's prompt-build path treats the new provenance metadata
```

---

**Worker: H3 closes the worst-shape pollution chain in the anti-hallucination stream — the trajectory_summarizer → Builder pipeline. After H3, summaries are bounded in shape (Pydantic on emit) AND the Builder is explicitly framed to treat them as inferences (provenance on read). Combined with CC20 (already merged) grounding tools, the Builder now has both factual grounding AND clear separation between "inferred" and "verified" content. M1 (memory) becomes safe to fire next.**
