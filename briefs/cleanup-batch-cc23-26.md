# Cleanup batch — CC23 + CC24 + CC25 + CC26 (single Worker, four small fixes)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cleanup-batch-cc23-26`
**Browser smoke owner:** Lead, post-merge — open Memory shell, verify (a) Drawers tab empty-state copy says "Select a drawer" not "Select an observation"; (b) observation detail returns correct evidence_count.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~120 (four small fixes across different files + small tests).
**Priority:** MEDIUM — cleanup batch. Fires in parallel with MC2-MC5 carryover bundle (different files, zero overlap).

---

## Why this exists

Four banked findings accumulated during the memory keystone work. Each is small. Bundling them into one Worker reduces dispatch overhead vs four separate Workers. Different files = zero conflict between fixes.

---

## Scope — Four independent fixes

### CC23 — Extend `EvidenceSourceKind` Literal

**File:** `artemis/memory/schemas.py`

**Current state:** `EvidenceSourceKind = Literal["drawer", "observation"]` (line 11). Only covers memory-internal sources. External sources (agent_run, signal_queue, definition_proposal, etc.) require raw `pg_insert` escape hatch.

**Fix:** Extend the Literal to include the external source kinds that production code is already using via raw inserts:

```python
EvidenceSourceKind = Literal[
    "drawer",
    "observation",
    "agent_run",
    "signal_queue",
    "definition_proposal",
    "pipeline_run",
    "skill",
    "floating_artemis_messages",
    "meeting",
]
```

**Then update `link_evidence` in `artemis/memory/store.py`** so its `source_kind` parameter accepts the broader Literal. Most callers should NOT need code changes — they were already passing string values; now the type-checker validates them.

**Verify:** existing callers (M1, M5, MC1) keep working unchanged. Their raw `pg_insert` escape hatches become unnecessary but harmless — flag in the report which call sites could be cleaned up in a follow-up.

### CC24 — M6 `evidence_count` off-by-one fix

**File:** `artemis/routes/memory.py` (or wherever the observation detail query lives) + possibly `artemis/memory/repository.py`

**Current bug:** `GET /api/memory/observations/{id}` returns `observation.evidence_count: 1` even when the evidence array contains 2+ rows. Verified empirically: observation #2 has 2 evidence links (drawer + signal_queue) but evidence_count=1.

**Fix:** find the query that computes `evidence_count`. Likely a `COUNT()` with a missing JOIN or a wrong WHERE clause. Make it return the actual count of evidence rows for that observation.

The frontend (M6 memory shell) doesn't use `evidence_count` directly — it computes from `len(evidence)`. So fixing this is metadata correctness, not behavior change.

**Test:** route test that creates an observation with 3 evidence rows, calls GET, verifies `evidence_count == 3`.

### CC25 — Memory shell empty-state copy fix

**File:** `public/js/features/memory-shell.js`

**Current bug:** when on the Drawers tab with no row selected, the empty-state pane says "Select an observation" instead of "Select a drawer".

**Fix:** render-time check of current tab state (`m6State.tab`). Empty-state text becomes:
- Observations tab → "Select an observation / Click any row to see the full content and evidence chain."
- Drawers tab → "Select a drawer / Click any row to see the verbatim content and source."

~5 LOC change in the renderM6Shell function (search for the existing "Select an observation" string).

### CC26 — pgvector embedding serialization fix

**File:** `artemis/memory/retrieval.py`

**Current bug:** Line 280 builds `vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"` then passes as `:_qvec` bind param. asyncpg's pgvector adapter calls `pgvector.Vector(value)` → `np.asarray(value, dtype='>f4')` which fails on a string. Result: "Semantic search failed" logged on every call; falls back gracefully to lexical+recency.

**Fix:** stop pre-stringifying the vector. Pass the list of floats directly:

```python
# Before:
vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"
# ...
sem_params = {**base_params, "_qvec": vec_str, ...}

# After:
import numpy as np
# Pass numpy array (proper pgvector binary serialization):
sem_params = {**base_params, "_qvec": np.asarray(query_vec, dtype='>f4'), ...}
```

OR pass the Python list directly if SQLAlchemy's pgvector type handler accepts that. **Try both; pick whichever works without changing the SQL CAST.**

**Test:** call `search_observations(query="something", scope_set=[...])` against a database with at least 1 embedding row. Verify no "Semantic search failed" warning logged. Verify semantic similarity scores returned for matching rows.

If the embedding query still fails, document the next-layer issue and revert this specific fix; the lexical+recency fallback continues to work as it has been.

---

## Files owned

- EDIT: `artemis/memory/schemas.py` (CC23 — extend Literal)
- EDIT: `artemis/memory/store.py` (CC23 — propagate broader Literal to link_evidence)
- EDIT: `artemis/routes/memory.py` or `artemis/memory/repository.py` (CC24 — evidence_count query)
- EDIT: `public/js/features/memory-shell.js` (CC25 — empty-state copy)
- EDIT: `artemis/memory/retrieval.py` (CC26 — pgvector serialization)
- NEW or EDIT: `artemis/memory/tests/test_cleanup_batch.py` (small tests for each fix)

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0048`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/memory/tests/test_cleanup_batch.py -v` — tests for each of the four fixes pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **No regressions in existing memory tests** (M1, M5, M6, MW1 test suites). **Paste pytest summary.**
5. **Manual verification (Lead does this post-merge):**
   - CC23: confirm `link_evidence` accepts `"agent_run"` and `"signal_queue"` without raw pg_insert workaround
   - CC24: curl `/api/memory/observations/2` → verify `evidence_count: 2` (was 1)
   - CC25: open Memory shell, click Drawers tab → verify empty-state copy says "Select a drawer"
   - CC26: run a search via M2's `builder_search_memory` → verify no "Semantic search failed" warning in logs
   - **Paste verification outputs.**
6. `git diff --stat` + `git log --oneline -1` on `worker/cleanup-batch-cc23-26`. **Paste.**

---

## Hard constraints

- **Each fix is independently revertable.** If CC26 turns out to be deeper than expected, leave it banked and ship the other 3.
- **Backward compatibility on CC23.** Existing string-based `source_kind` values in DB rows continue to work. New code uses the broader Literal for type safety.
- **CC24 doesn't change the frontend.** Memory shell UI computes count from `len(evidence)` not the metadata field — the fix is for API consumers + future-proofing.
- **CC25 is pure copy change.** No state machine, no new logic. Just the empty-state string.
- **CC26 must not regress the lexical+recency fallback.** If the semantic path still fails for some reason, the catch-and-fall-back pattern stays in place.
- **No schema changes.** Migration 0048 unchanged.
- **Local-only git.** Worker commits on `worker/cleanup-batch-cc23-26`; terminal-Lead merges after Lead approves.

---

## Coordination with parallel Worker A (MC2-MC5 bundle)

Worker A fires in parallel with this brief, touching DIFFERENT files. No expected conflicts.

**One file Worker A might also touch:** if the MC2-MC5 work prompts Worker A to want to remove the raw `pg_insert` escape hatch (now that CC23 extends the Literal), they should be coordinated. Worker A is instructed to leave the escape hatch as-is and let CC23's broader Literal naturally cover the new source_kinds. No code change needed in Worker A's files.

---

## Report-back format

```
Cleanup batch (CC23 + CC24 + CC25 + CC26) report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count
4. Each fix verified independently:
   - CC23: Literal extended, source_kind values listed
   - CC24: route test confirms evidence_count = len(evidence)
   - CC25: empty-state copy verified for both tabs
   - CC26: search_observations semantic path no longer falls back (or banked deeper if so)
5. Regression check: M1/M5/M6/MW1 test suites still green
6. check.sh summary
7. Anything surprising
```

---

**Worker: four small fixes bundled to reduce dispatch overhead. Different files, independent fixes. Each is independently revertable if needed. After this batch lands, memory's substrate is materially cleaner — type-safe evidence linking, correct metadata, accurate semantic search.**
