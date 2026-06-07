# M2 — Builder reads agent memory (cross-run grounding for proposals)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/m2-builder-reads-memory`
**Browser smoke owner:** Lead, post-merge — re-run a Builder session targeting an agent that has observations (after M1 has run), verify the Builder's system prompt includes a "Prior observations" section + the Builder uses memory facts when proposing.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~150 (MCP tool + builder integration + tests).
**Priority:** HIGH — Round 2 of memory keystone P4. Independent of M3+M4. Closes the read side of the Builder-memory loop.

---

## Why this exists

Per the memory audit M2 spec: after M1 lands (which it has — merge `b0bfefd`), every agent run produces a memory observation scoped to `agent:<agent_id>`. M2 wires the consumer side: when the Builder opens an edit session for an agent, it retrieves that agent's memory observations and grounds against them.

Today the Builder reads `read_recent_runs` (the last 10 trajectory summaries) but has NO mechanism to read the agent's full history. After 100 runs, the most useful patterns might be 50 runs ago. M2 gives the Builder access to the curated, deduplicated, retrieval-ranked memory of every observation ever written about that agent.

Combined with CC20's grounding tools (which give factual access to schemas/enums), M2 gives the Builder factual + experiential grounding: *"these are the valid states (CC20) AND these are the patterns we've seen across all runs (M2)."* Proposals become both factually correct AND historically informed.

---

## Scope

### Part A — New MCP tool: `builder_search_memory`

Extend the Builder MCP set (CC19/CC20) with a fourth grounding tool:

**`builder_search_memory`** — input: `agent_id` (string, dotted slug), `query` (optional string for semantic search), `limit` (default 10, max 50).

**Behavior:**

1. Resolve the agent's memory scope: `(scope_kind="agent", scope_id=agent_id)`.
2. Call `search_observations(scope, query=query, limit=limit)` from `artemis.memory.retrieval` — the existing hybrid retrieval (BM25 + semantic + recency).
3. For each returned observation, include:
   - `id`, `content`, `created_at`, `confidence`, `superseded_by`
   - `evidence_summary`: top 3 evidence links with their previews (from `list_evidence_for_observation`)
4. Return as JSON array.

If the scope doesn't exist yet (no observations have been written for this agent), return an empty array — not an error.

**Return shape:**

```json
[
  {
    "id": 42,
    "content": "Run 329 stalled — ILLEGAL_TRANSITION...",
    "created_at": "2026-05-29T13:35:31Z",
    "confidence": 0.8,
    "superseded_by": null,
    "evidence_summary": [
      {"source_kind": "agent_run", "source_id": "329", "preview": "..."},
      ...
    ]
  },
  ...
]
```

### Part B — Add to AGENT_BUILDER_SYSTEM_PROMPT

Update the system prompt (the section CC20 already edited about grounding) to MANDATE memory retrieval in edit sessions:

```
- After read_recent_runs() but BEFORE proposing changes:
  - Call read_tool_signatures(agent_id) to ground against actual tool schemas (CC20)
  - Call read_db_schema(table_names) for any DB tables your proposal touches (CC20)
  - Call read_skill_catalog() if co-proposing a skill (CC20)
  - **Call search_memory(agent_id) to retrieve curated observations across the agent's full history (M2).**
  - Recent runs (read_recent_runs) show only the latest N. search_memory surfaces durable patterns from across all runs.
  - Treat memory observations as more authoritative than recent trajectory summaries — they have evidence chains and may be superseded versions of the same patterns.
```

### Part C — Inject memory into the edit-session opener

In `build_edit_session_opener` (`artemis/builder/agent_builder.py`, where the system prompt is assembled), after the existing trajectory-summary block from H3, add a new section:

```python
# After H3's trajectory summary block:
memory_observations = await search_observations(
    scope=Scope(scope_kind="agent", scope_id=agent_id),
    limit=10,
    db_session=db_session,
)
if memory_observations:
    memory_block = "\n\n## Prior observations (memory keystone — curated across all runs)\n\n"
    memory_block += "These observations are written-once, evidence-linked summaries. They reflect patterns the platform considers significant across this agent's full history — not just the last 10 runs.\n\n"
    for obs in memory_observations:
        memory_block += f"- (obs #{obs.id}, {obs.created_at.isoformat()}): {obs.content}\n"
    system_prompt += memory_block
```

This is the "fact" half of grounding (memory is curated truth). The H3 trajectory-summary block is the "inference" half (LLM-generated summaries with provenance markers). Together they give the Builder a layered view.

### Part D — Session lifecycle pattern (per M1's surprise)

M1's Worker surfaced: agent-runtime memory writes need their own `SessionLocal()` to avoid SAVEPOINT/TRUNCATE deadlocks against concurrent test workers. **M2 reads memory, doesn't write — uses the existing db_session passed in. No new session needed for reads.**

However, if a new session IS needed in any code path you add (e.g. for an MCP-server-side query in a subprocess), follow M1's pattern: open a fresh `SessionLocal()`, use within a context manager, commit explicitly.

### Part E — Tests

`artemis/builder/tests/test_m2_builder_reads_memory.py`:

1. **`builder_search_memory` returns matched observations.** Fixture: agent_id `marketing.qualifier.brief_composer` with 3 observations in scope `agent:marketing.qualifier.brief_composer`. Call the tool. Verify all 3 returned with evidence summaries.
2. **Empty scope returns empty array (not error).** Agent with no observations. Call tool. Verify `[]`.
3. **Query-based retrieval narrows results.** 5 observations with diverse content. Query for content matching only 2. Verify returned set narrows.
4. **Edit-session opener injects memory.** Open a Builder session for an agent with observations. Verify the resulting system prompt contains "## Prior observations" + at least one observation content.
5. **Empty memory doesn't break opener.** Open Builder session for an agent with no observations. Verify system prompt assembles without the memory block (or with an empty block) and doesn't error.
6. **Integration: Builder session producing a proposal cites memory observations.** End-to-end smoke (Worker may make this API-gated like CC20's): Builder session sees memory + proposes + citations include observation references.

---

## Files owned

- EDIT: `artemis/tools/mcp_server.py` (add `builder_search_memory` MCP tool)
- EDIT: `artemis/builder/agent_builder.py` (add in-process closure for the tool registry; update system prompt)
- EDIT: `artemis/builder/engine.py` (if the tool implementation goes here per CC20's pattern) + likely add `search_memory_for_agent` helper
- NEW: `artemis/builder/tests/test_m2_builder_reads_memory.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_m2_builder_reads_memory.py -v` — all 6 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Live smoke (Lead does this post-merge, API-gated):**
   - Create a Builder session targeting brief_composer (or any agent with at least 1 memory observation written by M1)
   - Send a review message
   - Verify the Builder's response references memory observations (via citations or content)
   - **Paste the assistant_text excerpt that shows memory grounding.**
5. `git diff --stat` + `git log --oneline -1` on `worker/m2-builder-reads-memory`. **Paste.**

---

## Hard constraints

- **Read-only.** M2 only READS memory. No memory writes. No supersession.
- **No schema changes.** Migration 0047 unchanged.
- **Lossless invariant respected by transitivity** — read paths don't violate it.
- **No new session needed for reads.** Reuse the db_session passed in.
- **Local-only git.** Worker commits on `worker/m2-builder-reads-memory`; terminal-Lead merges after Lead approves.

---

## Coordination with M3+M4 (firing in parallel)

M2 and M3+M4 fire in parallel. They touch different files (M2: `builder/`, M3+M4: `floating_artemis/`). No expected conflicts.

Both depend on M1's `get_or_create_scope` helper being present — which it is (merged at b0bfefd). Both depend on the M6 stats endpoints if the Worker wants to verify population — those are live at d7fc20c.

---

## Report-back format

```
M2 — Builder reads agent memory report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially test #4 opener injection, #6 end-to-end)
4. Live smoke result — PASTE the Builder's response excerpt showing memory grounding
5. MCP tool registration — confirmed accessible via subscription path (claude-code subprocess can call builder_search_memory)?
6. check.sh summary
7. Anything surprising — especially around interaction with M1's session pattern or existing search_observations API
```

---

**Worker: M2 closes the read side of the Builder-memory loop. After M2, the Builder reasons over an agent's curated full history (memory observations from M1) PLUS recent trajectory summaries (with H3 provenance) PLUS grounded schema facts (CC20). Three layers of grounding before any proposal lands. The hallucination class that produced CC19's smoke becomes structurally impossible.**
