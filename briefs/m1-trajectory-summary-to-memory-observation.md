# M1 — Trajectory summary → memory observation

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/m1-trajectory-to-memory`
**Browser smoke owner:** Lead, post-merge — trigger any agent run, query `memory_observations`, verify a new row landed scoped to that agent with citation back to the run.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~120 (writer + tests + one ContextVar pattern).
**Priority:** HIGH — first agent-runtime memory write path. Per the memory audit (`docs/memory-audit-2026-05-29.md`), this is the highest-leverage move from M1-M6. Once it lands, every agent run produces a machine-readable observation in `memory_observations`; the keystone flips from 1-row dormant to alive.

**Prerequisites — now satisfied:**
- ✅ H3 (`worker/h3-trajectory-pydantic`) merged at b1c60fb — trajectory summaries are now Pydantic-validated (`TrajectorySummary` schema with `extra="forbid"`, max length constraints). M1 writes from validated content.
- ✅ H1+H2+H4+CC20 also merged — full anti-hallucination stack live.
- ✅ First-ever engine.commit() exercised in production (Proposal #4 approved for brief_composer) — self-improvement loop closed empirically. Memory M1 is now safe to wire because the producer side is anti-hallucination-hardened.

---

## Why this exists

Per `docs/memory-audit-2026-05-29.md`: the memory substrate (~5,800 LOC: store, retrieval, embeddings, consolidator, graph, conflict detection) is architecturally complete through P3. Live Postgres state:

```
memory_drawers      |   0
memory_observations |   1   ← one row, user-written via Floating Artemis tool
memory_evidence     |   0
```

Eleven tables; one row of real data. P4 (agent integration) is unstarted. Nothing in production runtime auto-writes memory from agent runs.

Meanwhile, `agent_run_trajectory_summaries` has 35 rows from the CC10-CC18 stream. Every pipeline run produces a diagnostic summary (`what_worked` / `what_stalled` / `what_was_missing`). These summaries are the most observation-shaped data in the platform — they are exactly the "agent learns something from its own run" output. **And they bypass memory entirely.**

M1 closes that gap. The keystone plan (P4) called for agents to write observations through structured channels. M1 is the smallest such channel: the trajectory summarizer (already firing) gains one extra step that writes the summary to memory as an observation.

---

## Scope

### Part A — Write a memory observation when a trajectory summary lands

In `artemis/builder/trajectory_summarizer.py` (the module that produces summaries — already wired into the post-agent-run flow per CC14):

After `create_trajectory_summary` successfully writes its row to `agent_run_trajectory_summaries`, ALSO write a memory observation. The observation:

- **Scope:** `(scope_kind="agent", scope_id=<agent_id>)` — the dotted agent_id string (e.g. `marketing.qualifier.brief_composer`).
- **Content:** a single coherent paragraph composed from `what_worked` + `what_stalled` + `what_was_missing`. Format:

  ```
  Run {run_id} ({generated_at_iso}). What worked: {what_worked}. What stalled: {what_stalled}. What was missing: {what_was_missing}.
  ```

  If any field is empty, omit that clause cleanly.
- **Evidence link:** after writing the observation, call `link_evidence(observation_id, source_kind="agent_run", source_id=run_id, weight=1.0)`. This establishes the citation chain back to the run that produced the summary.
- **Idempotency:** `write_observation` already idempotent on content hash (per memory README). No additional dedup needed.

### Part B — Scope auto-creation

The `agent` scope kind exists in `memory_scopes` per the schema. M1 needs to ensure the scope row exists for each unique agent_id before writing the observation. Pattern:

```python
scope = await get_or_create_scope(db_session, scope_kind="agent", scope_id=agent_id)
obs_id = await write_observation(db_session, scope=scope, content=content, ...)
await link_evidence(db_session, obs_id, source_kind="agent_run", source_id=run_id)
```

The helper `get_or_create_scope` may not exist yet — if not, add it to `artemis/memory/store.py` as a small additive helper (or use `artemis.memory.repository` if scope creation lives there). Idempotent: returns existing scope if present.

### Part C — Failure isolation

Memory write failure MUST NOT break the trajectory summarizer or the agent run. If the observation write fails (embedding service down, DB write error, scope creation race), log a warning and proceed. The summary row in `agent_run_trajectory_summaries` is the durable source-of-truth; memory observation is an additive layer.

Wrap the memory write in a try/except that logs `logger.warning("M1 memory observation write failed for run_id=%s agent_id=%s: %s", run_id, agent_id, exc, exc_info=True)` and continues. Never raises.

### Part D — Tests

`artemis/builder/tests/test_m1_trajectory_to_memory.py`:

1. **Integration test:** invoke `create_trajectory_summary` with a fixture agent_run + summary content. Verify (a) the `agent_run_trajectory_summaries` row lands as before, AND (b) a new row in `memory_observations` with the expected `scope_kind="agent"`, `scope_id=<agent_id>`, content matching the formatted paragraph, AND (c) a `memory_evidence` row linking the observation to the agent_run.
2. **Idempotency test:** call `create_trajectory_summary` twice with the same content. Verify `memory_observations` has exactly one matching row (not duplicated). The second call should be a no-op (or find the existing observation via content hash).
3. **Failure isolation test:** monkeypatch `write_observation` to raise an exception. Verify (a) the trajectory summary still lands in `agent_run_trajectory_summaries`, (b) the function returns normally (no exception bubbled), and (c) a warning is logged.
4. **Scope creation test:** with no pre-existing `(agent, marketing.scout.federal_funding)` scope, run the summarizer. Verify the scope row is created exactly once + the observation links to it.

### Part E — Verification fixture

Add to the smoke recipe (no code change, just documentation in the brief): after merge, Lead runs `psql -c "SELECT COUNT(*) FROM memory_observations;"` before and after the next pipeline run. The delta should match the number of agent runs that produced trajectory summaries in that pipeline run.

---

## Files owned

- EDIT: `artemis/builder/trajectory_summarizer.py` (add memory write + scope creation + failure isolation)
- EDIT: `artemis/memory/store.py` OR `artemis/memory/repository.py` (add `get_or_create_scope` helper if not present — confirm in Part A; if it already exists, no edit here)
- NEW: `artemis/builder/tests/test_m1_trajectory_to_memory.py`

---

## Acceptance criteria

1. **Migration check** — no schema changes. `uv run alembic current` shows `0047` unchanged. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_m1_trajectory_to_memory.py -v` — all 4 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt failures. **Paste.**
4. **Manual smoke (Lead does this post-merge):**
   - Pre-state: `SELECT COUNT(*) FROM memory_observations;` returns N (currently 1; expect higher after smoke baseline)
   - Trigger a pipeline run OR re-run an agent that produces a trajectory summary
   - Post-state: same query returns N+K where K matches the count of new `agent_run_trajectory_summaries` rows
   - Verify: `SELECT scope_kind, scope_id, LEFT(content, 100) FROM memory_observations ORDER BY id DESC LIMIT 5;` shows agent-scoped observations
   - Verify: `SELECT * FROM memory_evidence WHERE source_kind='agent_run' ORDER BY id DESC LIMIT 5;` shows the citation chain back to runs
5. `git diff --stat` + `git log --oneline -1` on `worker/m1-trajectory-to-memory`. **Paste.**

---

## Hard constraints

- **Lossless invariant.** No deletes anywhere. Observations are written-once. If duplicate content arrives, `write_observation` no-ops via content hash. The brief MUST NOT introduce any path that could delete or destructively update a memory row.
- **No schema changes.** All required tables and columns exist. No migration 0048.
- **Embedding is best-effort.** `write_observation` already handles embedding failures gracefully per the existing implementation. The brief inherits that behavior.
- **Failure isolation is non-negotiable.** Memory write failure must NOT break the trajectory summarizer (which itself was just stabilized through CC10-CC14). Wrap in try/except with logger.warning.
- **Scope kind is fixed to `agent`.** Per the memory audit's open question: scope choice `agent:<agent_id>` is the lean. Don't use `workspace:marketing` or `brand:<brand_id>` in M1; those are later phases.
- **Observation only.** M1 writes ONLY an observation, NOT a verbatim drawer. The trajectory summary is already a curated summary; treating it as an observation directly is correct. Drawers for verbatim agent_run output is a later phase (M3 territory — Floating Artemis verbatim conversation drawers).
- **Local-only git.** Worker commits on `worker/m1-trajectory-to-memory`; terminal-Lead merges after Lead approves.

---

## Knock-on effects to anticipate (for the report)

After M1 lands, the next surgical brief becomes much easier to design because real observations exist:

- **M2 — Builder reads agent memory.** Once observations are flowing, the Builder can search across an agent's entire history (not just last 10 runs) before proposing. Same MCP pattern as CC19 — add a `builder_search_memory` tool scoped to the Builder session.
- **The grounding concern from CC19's smoke** (Builder hallucinated state names). M1 doesn't directly fix this, but it sets up M2 which adds memory-based grounding. The valid state enum could become a memory observation written from the Josh spec parser — then the Builder retrieves it as grounded fact.
- **Marketing signal genealogy** becomes possible after M5 (qualified signal → memory observation). M1 is the precedent that establishes the write-from-runtime pattern.

The Worker should NOT do M2/M5 work in this brief. M1 is one surgical write path. Knock-ons go in the report-back's "Anything surprising" section if observed during implementation.

---

## Report-back format

```
M1 — Trajectory summary → memory observation report
1. Commit / branch / worktree
2. LOC diff stats
3. Test pass summary (4 new tests + any regressions)
4. Manual smoke result — PASTE the SELECT showing the observation + evidence chain
5. Idempotency confirmation — second run of same summary did not duplicate
6. Failure isolation confirmation — what happens when memory write raises
7. check.sh summary
8. Anything surprising — especially around scope auto-creation, content hash collisions, or interactions with the embedding writer
```

---

**Worker: this brief opens the memory keystone for production agent traffic for the first time. Eleven tables, one row today. After M1, every agent run produces a new observation linked back to its run. The keystone plan's P4 (agent integration) starts here. Subsequent briefs (M2 Builder-reads-memory, M3 Floating Artemis auto-write, M4 Floating Artemis auto-read, M5 signal-genealogy, M6 memory shell UI) all build on the precedent M1 establishes. Make it boring and reliable — failure isolation is the load-bearing constraint.**
