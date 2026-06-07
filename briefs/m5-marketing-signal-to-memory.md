# M5 — Marketing signal → memory observation (signal genealogy)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/m5-signal-to-memory`
**Browser smoke owner:** Lead, post-merge — trigger a pipeline run (or wait for next), verify each qualified signal lands a drawer + observation in memory with evidence chain back to the signal row.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~120 (writer + scope helper + failure isolation + tests).
**Priority:** HIGH — second production memory write path. Independent of M1; can fire in parallel as part of Round 1 of the memory keystone P4 stream.

---

## Why this exists

Per `docs/memory-audit-2026-05-29.md` finding #B + the hollowness audit:

> The marketing pipeline writes ZERO observations to memory. Signals flow through `signal_queue` → `qualified_signals` only. The decision rationale ("this signal scored 78 because criteria X, Y, Z fired") never becomes a memory observation. **Signal genealogy is lost.**

The reference plan's keystone goal was a memory system where "agents can reason transitively over relationships (campaigns ↔ posts ↔ channels ↔ outcomes)." Today the marketing data lives in a parallel-stream silo. M5 connects it.

After M5: when an operator (or a future agent) asks *"why did we qualify this kind of signal three months ago"*, the question has a real answer queryable via memory retrieval — not just by reading raw `signal_queue` rows. The qualifier's reasoning chain (signal → reason codes → verdict) becomes durable memory.

---

## Scope

### Part A — Write a memory drawer + observation when a signal qualifies

In `artemis/tools/signal_queue_ops.py` (the `signal_queue.update_status` tool), find the path where a transition lands successfully and the new status is `qualified`. After the transition succeeds, write:

1. **Drawer** (verbatim evidence) — scope `workspace:marketing`:
   - `content`: the signal's raw fields (headline, source_url, source_type, reason_codes, raw payload as JSON-stringified)
   - `source`: `signal_queue:<signal_id>`
   - This is the immutable "what we saw" record

2. **Observation** (curated summary) — same scope:
   - `content`: a single coherent paragraph: `"Qualified signal {id}: {headline}. Source: {source_type}. Reason codes: {csv}. District: {district_id_or_unknown}. Pipeline run: {pipeline_run_id}."`
   - This is the "what we decided" record

3. **Evidence link** between observation and drawer:
   ```python
   await link_evidence(
       db_session,
       observation_id=obs_id,
       source_kind="memory_drawer",
       source_id=drawer_id,
       weight=1.0,
   )
   ```

Also add a second evidence link from observation → signal_queue row:
   ```python
   await link_evidence(
       db_session,
       observation_id=obs_id,
       source_kind="signal_queue",
       source_id=signal_id,
       weight=1.0,
   )
   ```

This creates a queryable chain: observation → drawer (verbatim source) + observation → signal_queue (DB row).

### Part B — Scope auto-creation

Same pattern as M1. Ensure `(scope_kind="workspace", scope_id="marketing")` exists before writing. Use the `get_or_create_scope` helper (M1 should add it; if M5 lands first, M5 adds it; either way reuse).

If both M1 and M5 land in parallel, both may add the helper — terminal-Lead handles the merge conflict by keeping the more complete version. Both implementations should be identical: idempotent fetch-or-create, returns the scope row.

### Part C — Failure isolation

Memory write failure MUST NOT break the qualifier or signal_queue update. Wrap in try/except that logs `logger.warning("M5 memory write failed for signal_id=%s: %s", signal_id, exc, exc_info=True)` and continues. The `signal_queue.update_status` tool returns success even if the memory write fails — the durability invariant is the signal_queue transition, memory is additive.

### Part D — Only fire on `qualified` (not other transitions)

Be strict about WHICH transitions trigger memory writes. The relevant transitions:

- `pending_qualification` → `qualified` ✅ — write drawer + observation
- `pending_qualification` → `rejected_hard_filter` ❌ — do NOT write (failed qualifications aren't observation-worthy yet; may add later)
- `pending_qualification` → `suppressed_stale` ❌ — do NOT write
- `qualified` → `approved` — separate brief later (M5-B if needed); approval is a different lifecycle event
- All other transitions ❌ — no memory write

The brief targets ONLY the qualification moment. Other transitions can become future briefs if we want richer signal genealogy.

### Part E — Tests

`artemis/tools/tests/test_m5_signal_to_memory.py`:

1. **Qualified transition writes drawer + observation.** Fixture: pending_qualification signal. Call `signal_queue.update_status` with `newStatus="qualified"`. Verify:
   - `memory_drawers` row created in scope `workspace:marketing` with the signal content
   - `memory_observations` row created in same scope
   - `memory_evidence` rows linking the observation to both the drawer and the signal_queue row
2. **Non-qualified transitions do NOT write.** Call `signal_queue.update_status` with `newStatus="rejected_hard_filter"`. Verify `memory_drawers` count + `memory_observations` count unchanged.
3. **Idempotency.** Call `signal_queue.update_status` twice for the same signal (the second call would error from the state machine, but mock past that). Verify exactly one drawer + one observation for that signal — `write_drawer`/`write_observation` idempotent on content hash.
4. **Scope auto-creation.** With no pre-existing `workspace:marketing` scope, qualify a signal. Verify scope row created exactly once.
5. **Failure isolation.** Monkeypatch `write_drawer` to raise. Verify (a) `signal_queue.signal_status` still becomes `qualified`, (b) function returns normally, (c) warning logged.
6. **Provenance verification.** After qualification, query the new observation. Verify its evidence list contains both a `memory_drawer` link and a `signal_queue` link.

---

## Files owned

- EDIT: `artemis/tools/signal_queue_ops.py` (add memory write after qualified transition + failure isolation)
- EDIT: `artemis/memory/store.py` OR `artemis/memory/repository.py` (add `get_or_create_scope` helper if not present — coordinate with M1)
- NEW: `artemis/tools/tests/test_m5_signal_to_memory.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/tools/tests/test_m5_signal_to_memory.py -v` — all 6 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Manual smoke (Lead does this post-merge):**
   - Pre-state: `SELECT COUNT(*) FROM memory_drawers; SELECT COUNT(*) FROM memory_observations;`
   - Trigger a pipeline run (need to wait for current in-flight or cancel it first)
   - Post-state: drawer + observation counts both increase by the number of qualified signals
   - Verify: `SELECT scope_kind, scope_id FROM memory_scopes WHERE scope_kind='workspace';` shows the marketing scope
   - Verify: query observation → evidence chain back to signal_queue row
   - **Paste the SQL output.**
5. `git diff --stat` + `git log --oneline -1` on `worker/m5-signal-to-memory`. **Paste.**

---

## Hard constraints

- **Lossless invariant.** No deletes. Drawer + observation written-once. Idempotent on content hash via existing `write_drawer`/`write_observation`.
- **No schema changes.** Migration 0047 unchanged.
- **Don't write on every status transition.** Only on `qualified`. Other lifecycle events deliberately deferred.
- **Failure isolation is non-negotiable.** Memory write failure cannot break the signal_queue update — that's a hard durability boundary.
- **Coordinate with M1 on `get_or_create_scope`.** Both briefs need the helper. If a merge conflict arises, terminal-Lead picks the more complete version; both are designed to be identical.
- **Don't depend on M1 landing first.** M5 can fire independently. If M1 lands first, M5 reuses its helper; if M5 lands first, M1 reuses M5's helper. Order-independent.
- **Local-only git.** Worker commits on `worker/m5-signal-to-memory`; terminal-Lead merges after Lead approves.

---

## Knock-on effects

After M5 lands, marketing signal genealogy becomes queryable. Future briefs that can build on it:

- **M5-B (banked):** rejected signal → memory observation (with rationale). Currently failed qualifications vanish; with M5-B they'd become observable.
- **Cross-signal pattern queries:** "show me all qualified signals with reason_code LEADER_TRANSITION_FORMAL in the last 30 days" becomes a single memory retrieval call instead of a complex multi-table join.
- **Builder grounding (CC20-extended):** the Builder could later use a `read_signal_memory` tool to ground proposals about marketing agents against actual signal history.

The Worker should NOT do those follow-on briefs in M5. Flag them in the report's "Anything surprising" section if observed during implementation.

---

## Report-back format

```
M5 — Marketing signal → memory observation report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially test #1 drawer+observation+evidence chain, #5 failure isolation)
4. Live smoke results — PASTE: pre/post counts, scope row, evidence chain query result
5. Coordination with M1 — did get_or_create_scope conflict? How resolved?
6. check.sh summary
7. Anything surprising — especially around the qualifier code path that calls update_status, or interactions between memory write and the existing tool_invocations logging
```

---

**Worker: M5 is the second of three Round-1 memory writes (M1 trajectory + M5 signal + M6 UI). Together they break the keystone's 1-row dormancy. After M5, every qualified marketing signal produces durable, queryable memory — signal genealogy is no longer lost.**
