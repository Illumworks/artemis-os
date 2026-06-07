# CC20 — Builder Grounding Tools (Layer 5: stop the Builder from hallucinating facts)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc20-builder-grounding`
**Browser smoke owner:** Lead, post-merge — re-run the same `brief_composer` smoke from CC19, verify the new proposal cites the ACTUAL valid lifecycle states (`pending_qualification`, `qualified`, `suppressed_stale`, `rejected_hard_filter`, `archived`, `held_pending_corroboration`) instead of hallucinated ones.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~250 (3 new MCP tools + grounding data extractors + tests).
**Priority:** HIGH — without grounding, every Builder proposal carries the same hallucination class of bug. Approving them blindly teaches agents wrong facts, defeating the loop.

---

## Why this exists — what the CC19 smoke surfaced

CC19 closed Layer 4 of the self-improvement hollowness — the Builder can now call tools, and `definition_proposals` accepts rows. The platform's first two Builder-produced proposals landed (session 12, target=17, brief_composer).

**Both had to be rejected.** Proposal 1 enumerated "valid lifecycle states" as `qualified | disqualified | needs_enrichment`. Empirically the actual states in `signal_queue.signal_status` are: `pending_qualification` (4 rows), `qualified` (78), `suppressed_stale` (70), `rejected_hard_filter` (42), `archived` (1), and `held_pending_corroboration` (`qualifier_rule_layer.py:224`). **The Builder invented states that don't exist** — the same class of bug that caused Run #329's `ILLEGAL_TRANSITION` failures, which is exactly what Proposal 1 was trying to fix.

Proposal 2's skill definition referenced the same hallucinated states in its `status` parameter description.

The Builder LLM had access to:
- `builder_read_recent_runs` — trajectory summaries (saw the failure but not the truth)
- `builder_read_capabilities` — providers/models/integrations (not signal-queue states)
- `builder_read_existing` — list existing agents/skills (definitions, not state machines)

None of these tools surface the actual valid state enum for a tool, the actual parameter schema, or the actual DB constraints. The Builder inferred plausible-sounding states from the failure mode. Plausibility ≠ truth.

**Layer 5 = grounding gap.** The Builder must ground against the actual codebase + DB before proposing. CC20 adds the tools that make that possible.

---

## Scope

### Part A — Add three grounding tools to the Builder MCP set

Extend the Builder MCP server (`artemis/tools/mcp_server.py`, in the section CC19 added for builder_* tools) with three new tools:

#### 1. `builder_read_tool_signatures`

**Purpose:** return the actual signatures + parameter schemas + valid enum values for tools an agent has access to.

**Input:** `agent_id` (string, dotted slug).

**Behavior:**
1. Look up the agent's `tools` array from `agents` table.
2. For each tool name, look up its registered factory in `artemis.tools.registry` (the in-process registry CC1 uses).
3. For each tool, extract: `name`, `description`, `input_schema` (the JSONSchema from the Tool definition), `allowed_status_values` (if the tool operates on `signal_queue.signal_status` or similar enum field, query `SELECT DISTINCT signal_status FROM signal_queue` AND scan codebase for `CHECK` constraints OR Pydantic Literal types).
4. Return as JSON.

**Implementation strategy for `allowed_status_values`:** read from `artemis/marketing/models.py` docstrings (e.g. line 127: `"status enum: pending | dry_run_passed | committed | failed"`), table CHECK constraints (`pg_constraint` lookups), and Pydantic Literal types in `artemis/marketing/schemas.py`. Combine into a single authoritative list. Cache per-process.

**Return shape:**
```json
{
  "agent_id": "marketing.qualifier.brief_composer",
  "tools": [
    {
      "name": "signal_queue.update_status",
      "description": "Transition a signal to a new lifecycle state.",
      "input_schema": {"type": "object", "properties": {...}, "required": [...]},
      "allowed_status_values": ["pending_qualification", "qualified", "suppressed_stale", "rejected_hard_filter", "archived", "held_pending_corroboration"]
    },
    ...
  ]
}
```

#### 2. `builder_read_db_schema`

**Purpose:** expose the actual DB schema for tables an agent's tools write to, so the Builder grounds against real columns, FK constraints, CHECK constraints, and NOT-NULL.

**Input:** `table_names` (array of strings) — e.g. `["signal_queue", "signal_briefs"]`.

**Behavior:**
1. For each requested table, query `information_schema.columns` + `pg_constraint`.
2. Return columns (name, type, nullable, default), CHECK constraints, FK relationships, and unique constraints.

**Return shape:** an array of `{table, columns: [...], constraints: [...]}` records. Cached per-process.

**Why this matters:** Builder can verify that proposed agent behavior matches actual schema constraints. If a proposal says "set signal_status to X," the Builder can verify X is in `allowed_status_values` BEFORE staging the proposal.

#### 3. `builder_read_skill_catalog`

**Purpose:** list ALL registered tools across the platform (not just an agent's current `tools`) so the Builder knows what already exists when co-proposing a skill.

**Input:** none (or optional `kind` filter).

**Behavior:**
1. Iterate `artemis.tools.registry.known_tool_names()` — the source of truth for what tools exist.
2. For each, include its name + description + input_schema.
3. Also list all `skills` table rows (the curated skill definitions, separate from the tool registry).

**Why this matters:** Proposal 2 in CC19's smoke co-proposed `signal_queue.list` as a new skill — but the Builder had no way to verify that tool name wasn't already registered under a different namespace. `builder_read_skill_catalog` makes the check possible.

### Part B — Update the Builder system prompt to mandate grounding

In `artemis/builder/agent_builder.py`, the `AGENT_BUILDER_SYSTEM_PROMPT` constant. Add to the "If the user is opening an existing agent (edit session)" section:

```
- After read_recent_runs(), BEFORE calling propose():
  - Call read_tool_signatures(agent_id) to load the actual parameter schemas + allowed enum values for every tool the agent uses.
  - For any tool that writes to a DB table referenced by your proposed system prompt, call read_db_schema with that table name.
  - For any skill you intend to co-propose, call read_skill_catalog to confirm the name isn't already taken.
- NEVER enumerate enum values, status names, or parameter constraints from inference. ALWAYS read them via the grounding tools first.
- If a grounding tool returns data that contradicts your proposed change, revise BEFORE calling propose().
```

This is the load-bearing change. The system prompt becomes a contract that requires factual grounding before proposing.

### Part C — Tests

`artemis/builder/tests/test_cc20_grounding_tools.py`:

1. **`builder_read_tool_signatures` returns real allowed_status_values.** Fixture: brief_composer agent with `signal_queue.update_status` in its tools. Call the tool. Verify `allowed_status_values` includes ALL the real states from the DB + code (not just the ones currently in `signal_queue`). Negative test: `disqualified` and `needs_enrichment` are NOT in the list.
2. **`builder_read_db_schema` returns CHECK constraints.** Pass `["definition_proposals"]`. Verify the response includes `ck_definition_proposals_kind` and `ck_definition_proposals_proposed_by` constraints with their allowed values.
3. **`builder_read_skill_catalog` returns all registered tools.** Verify `signal_queue.write`, `signal_queue.update_status`, `signal_briefs.write` are all listed (these exist in registry).
4. **End-to-end regrounding smoke (integration).** Create a Builder session targeting brief_composer. Send the same review message as CC19 smoke. Verify the resulting proposal's system prompt enumerates the REAL valid states (not the hallucinated ones from CC19's smoke). This proves the system prompt change is load-bearing.

### Part D — Banked follow-ups (DOCUMENT in the report, do NOT implement in CC20)

The CC19 smoke surfaced two other gaps the Worker should flag in the report but NOT fix in this brief:

1. **`tool_invocations` table doesn't capture builder_* tool calls.** The table requires `agent_run_id` NOT NULL, but Builder tools have `builder_session_id` instead. CC17's tool-invocation logging skips Builder calls. Worth a follow-up brief (call it CC21) to add `builder_session_id` column + relax the NOT-NULL or use a sentinel.
2. **`definition_proposals` has no `rejection_reason` column.** When Lead rejected proposals 1 and 2 from CC19's smoke, the WHY was lost. Future rejections should capture the reason so the Builder can learn from rejection patterns. Worth a separate brief (CC22) to add `rejection_reason TEXT NULL` + thread it through the `/reject` endpoint + Inbox UI.

The Worker may flag additional gaps but should not implement them in CC20.

---

## Files owned

- EDIT: `artemis/tools/mcp_server.py` (add 3 new builder_* tools to the existing CC19 builder scope)
- EDIT: `artemis/builder/agent_builder.py` (update `AGENT_BUILDER_SYSTEM_PROMPT` to mandate grounding before propose)
- NEW: `artemis/builder/grounding.py` (extractor helpers: `extract_allowed_status_values`, `extract_db_constraints`, `extract_tool_registry`)
- NEW: `artemis/builder/tests/test_cc20_grounding_tools.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047` unchanged. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_cc20_grounding_tools.py -v` — all 4 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt failures. **Paste.**
4. **End-to-end smoke (Lead does this post-merge):**
   - Create Builder session for brief_composer (target_id=17, same as CC19 smoke)
   - Send same review message: "Please review this agents recent runs and propose specific improvements..."
   - Verify the resulting proposal's `proposed_definition.system_prompt` enumerates the REAL valid states: contains at least `pending_qualification` and `qualified`, does NOT contain `disqualified` or `needs_enrichment`.
   - **Paste the proposed_definition.system_prompt excerpt that shows the state list.**
5. `git diff --stat` + `git log --oneline -1` on `worker/cc20-builder-grounding`. **Paste.**

---

## Hard constraints

- **No schema changes.** Pure tool additions + system prompt update.
- **Grounding tools are READ-ONLY.** They expose facts about the codebase + DB. They do NOT modify state. They MUST NOT call any write function.
- **Cache per-process.** Schema extraction is non-trivial — cache the result in module-level state. Memory cost is tiny; latency benefit is real for Builder responsiveness.
- **MCP scoping inherits from CC19.** All three new tools are scoped to `builder_session_id` (mutually exclusive with `agent_id`/`run_id`). Same CLI arg pattern.
- **Preserve subscription-only invariant.** No external API calls. Pure local introspection.
- **Local-only git.** Worker commits on `worker/cc20-builder-grounding`; terminal-Lead merges after Lead approves.

---

## What success looks like

After CC20 lands, re-running the CC19 smoke produces a proposal whose `valid lifecycle states` section reads:

```
**Valid lifecycle states — use only these, nothing else**
- `pending_qualification` — awaiting qualification scoring
- `qualified` — passes all hard filters; routed to a campaign type
- `suppressed_stale` — older than the staleness threshold; not routed
- `rejected_hard_filter` — fails a hard filter; not routed
- `held_pending_corroboration` — qualified but awaiting a second signal source
- `archived` — terminal state; no further processing
```

…instead of the hallucinated `disqualified`/`needs_enrichment` from CC19's smoke. **The Builder is now grounded.** It reasons about diagnoses (which it does well) AND verifies facts (which it didn't do before).

---

## Report-back format

```
CC20 — Builder grounding tools report
1. Commit / branch / worktree
2. LOC diff stats
3. Tests added + pass count (especially the regrounding smoke test #4)
4. End-to-end smoke output — PASTE the proposed_definition.system_prompt enumeration of valid states
5. The 3 grounding tool outputs for brief_composer — PASTE one example response from each
6. Banked follow-ups noticed during implementation (especially around schema extraction edge cases)
7. check.sh summary
8. Anything surprising — especially around tool-registry introspection or pg_constraint queries
```

---

**Worker: CC20 closes Layer 5 of the self-improvement hollowness — the Builder's grounding gap. CC19 made it possible for the Builder to call tools. CC20 makes the tool outputs factually correct. After CC20, an operator can approve a Builder proposal with materially less risk of accidentally teaching the agent a wrong fact. M1 (memory: trajectory summary → observation) becomes the natural next brief because once observations exist, they too can become a grounding signal for future Builder reasoning.**
