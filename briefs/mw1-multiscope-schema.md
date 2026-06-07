# MW1 — Multi-scope observation schema + minimal wing metadata

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/mw1-multiscope-schema`
**Browser smoke owner:** Lead, post-merge — verify migration applied cleanly, existing observations all have 1 row in the new join table mirroring their scope_id, M6 listing still works.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~120 (schema + migration + model wiring + backward-compat reads + tests).
**Priority:** HIGH — foundation brief for the Memory Wings UI stream + multi-scope writes throughout the platform. After MW1, observations can legitimately belong to multiple scopes (D6 — the single most important architectural decision from the 2026-05-29 lock).

---

## Why this exists

Per `docs/memory-shell-vision-2026-05-29.md` D6 (LOCKED): Memory observations need to be many-to-many with scopes.

**End-goal driver:** as Salesforce / ChurnZero / Gong integration lands, a single observation legitimately belongs to multiple scopes simultaneously. The M5 LAUSD signal observation today is scoped only to `workspace:marketing`, but it's also about:
- `district:LAUSD` (the entity)
- `campaign:reading_growth` (the campaign family)
- `account:salesforce-XYZ` (the CRM account, when CRM integration lands)
- `person:carvalho` (the contact)

Forcing single-scope means either picking one arbitrarily (loses info) or duplicating the observation across scopes (violates content_hash idempotency + bloats the lossless ledger). MW1 makes scope properly many-to-many.

**Secondary benefit:** MC1 already needs multi-scope (per its brief, writes to both `agent:<id>` and `workspace:platform`). Today MC1 has to write 2 observations as a workaround. After MW1, MC1 writes 1 observation with 2 scope-join rows — cleaner.

---

## Scope

### Part A — New join table

Add migration `0048_memory_observation_scopes.py`:

```sql
CREATE TABLE memory_observation_scopes (
    observation_id BIGINT NOT NULL REFERENCES memory_observations(id) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (observation_id, scope_kind, scope_id),
    FOREIGN KEY (scope_kind, scope_id) REFERENCES memory_scopes(scope_kind, scope_id) ON DELETE RESTRICT
);

CREATE INDEX idx_memory_observation_scopes_obs ON memory_observation_scopes(observation_id);
CREATE INDEX idx_memory_observation_scopes_scope ON memory_observation_scopes(scope_kind, scope_id);
CREATE INDEX idx_memory_observation_scopes_primary 
  ON memory_observation_scopes(observation_id) 
  WHERE is_primary = TRUE;
```

Constraints:
- One row per (observation_id, scope_kind, scope_id) — no duplicate scopes per observation.
- `is_primary=TRUE` should be exactly one row per observation (the "primary" scope — backward compat with the legacy `memory_observations.scope_kind/scope_id` columns). Enforce via partial unique index or app-level check.

### Part B — Add new columns to `memory_observations`

```sql
ALTER TABLE memory_observations 
  ADD COLUMN wing TEXT NOT NULL DEFAULT 'durable',
  ADD COLUMN confidence_origin TEXT;
```

`wing` enum (app-level, not DB-enforced): `working | durable`. (D1 locked: 2-value enum, no `needs_review`.)

`confidence_origin` is a free-text source label, e.g. `m1_trajectory`, `m5_signal_qualification`, `mc_definition_proposal`, `mc_signal_gate1`, `mc_skill_promotion`, `mc_pipeline_gate`, `mc_fa_marketing`, `fa_write_memory`, `fa_conversation`. Used for auditability and retrieval ranking.

### Part C — Backfill existing observations

Migration includes a backfill that:

1. For each existing row in `memory_observations`, INSERT into `memory_observation_scopes`:
   - `observation_id = id`
   - `scope_kind = scope_kind` (mirror from observation)
   - `scope_id = scope_id` (mirror)
   - `weight = 1.0`
   - `is_primary = TRUE`
2. Set `wing = 'durable'` for all existing observations (the current default for backfilled writes).
3. Set `confidence_origin = NULL` (legacy rows don't have a known origin; future writes set this).

Backfill is idempotent: re-running the migration is a no-op if rows already exist.

### Part D — Helper APIs in `artemis/memory/store.py`

Add helpers for writing/reading multi-scope:

```python
async def add_observation_scope(
    session: AsyncSession,
    observation_id: int,
    scope_kind: str,
    scope_id: str,
    weight: float = 1.0,
    is_primary: bool = False,
) -> None:
    """Idempotent: INSERT … ON CONFLICT DO NOTHING."""

async def list_scopes_for_observation(
    session: AsyncSession,
    observation_id: int,
) -> list[tuple[str, str, float, bool]]:
    """Returns list of (scope_kind, scope_id, weight, is_primary)."""

async def list_observations_for_scope(
    session: AsyncSession,
    scope_kind: str,
    scope_id: str,
    *,
    is_primary: bool | None = None,
) -> list[MemoryObservation]:
    """Reverse lookup. is_primary filter optional."""
```

### Part E — Backward-compat for existing readers

Today's M6 endpoints (`/api/memory/observations`, `/api/memory/scopes`, etc.) query `memory_observations.scope_kind/scope_id` directly. After MW1, these queries should:

- For listing: keep using the legacy columns (they reflect the primary scope per the backfill).
- For filtering by scope: use the join table for "give me all observations that include scope X" queries (covers non-primary scopes).

**Decision (lock):** keep the legacy `scope_kind`/`scope_id` columns on `memory_observations` for now. They reflect the primary scope. New code (MC1-MC5, future writes) should also write to the join table. Old code (M6) keeps working via the primary scope. No big refactor needed in MW1.

A later brief (MW2 or MW3) can do the deeper migration to join-table-as-source-of-truth if useful.

### Part F — Update `write_observation` in `artemis/memory/store.py`

Modify the signature to accept optional secondary scopes:

```python
async def write_observation(
    session: AsyncSession,
    *,
    scope: Scope,                              # primary scope (unchanged)
    content: str,
    additional_scopes: list[Scope] | None = None,   # NEW
    wing: Literal["working", "durable"] = "durable",
    confidence_origin: str | None = None,
    # ... existing params ...
) -> MemoryObservation:
    """Write observation. Primary scope goes in memory_observations.scope_kind/scope_id 
    AND as is_primary=True in the join table. Additional scopes go to the join table only."""
```

Behavior:
- The primary `scope` writes to BOTH the legacy columns (for backward-compat reads) AND the join table with `is_primary=TRUE`.
- Each `additional_scopes` entry writes only to the join table with `is_primary=FALSE`.
- `wing` and `confidence_origin` populate the new columns.

### Part G — Tests

`artemis/memory/tests/test_mw1_multiscope_schema.py`:

1. **Migration applies cleanly.** Run migration. Verify table exists, indexes present, FK constraints set.
2. **Backfill populates correctly.** Pre-migration fixture: 3 observations across 2 scopes. Run migration. Verify (a) 3 rows in join table; (b) each is_primary=TRUE; (c) scope_kind/scope_id matches the source observation.
3. **`add_observation_scope` is idempotent.** Add same (obs, scope) twice. Verify exactly 1 row.
4. **`list_scopes_for_observation` returns primary + secondary correctly.** Observation with 3 scopes (1 primary, 2 secondary). Verify returned list has all 3 with correct is_primary flags.
5. **`list_observations_for_scope` finds non-primary matches.** Observation A has primary `workspace:marketing` + secondary `district:LAUSD`. Query for scope=`district:LAUSD`. Verify A is in the result.
6. **`write_observation` with additional_scopes writes correctly.** Call with primary + 2 additional. Verify (a) legacy columns set to primary; (b) 3 join rows (1 primary + 2 secondary).
7. **`wing` defaults to 'durable'; can be set to 'working'.** Two writes, one default, one explicit `wing="working"`. Verify both land correctly.
8. **`confidence_origin` round-trips.** Write with `confidence_origin="mc_definition_proposal"`. Read back. Verify match.

---

## Files owned

- NEW: `alembic/versions/0048_memory_observation_scopes.py` (migration + backfill)
- EDIT: `artemis/memory/models.py` (add MemoryObservationScope ORM model + new columns on MemoryObservation)
- EDIT: `artemis/memory/store.py` (add helpers + extend write_observation signature)
- EDIT: `artemis/memory/__init__.py` (export new helpers)
- NEW: `artemis/memory/tests/test_mw1_multiscope_schema.py`

---

## Acceptance criteria

1. `uv run alembic upgrade head` shows `0048_memory_observation_scopes`. **Paste.**
2. **Backfill validation:** `psql -c "SELECT COUNT(*) FROM memory_observations o JOIN memory_observation_scopes s ON s.observation_id = o.id WHERE s.is_primary = TRUE;"` returns the same count as `SELECT COUNT(*) FROM memory_observations`. **Paste.**
3. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/memory/tests/test_mw1_multiscope_schema.py -v` — all 8 tests pass. **Paste.**
4. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
5. **Backward-compat check (Lead does this post-merge):**
   - `curl /api/memory/observations` — still returns the same shape M6 returns today (legacy scope columns)
   - `curl /api/memory/scopes` — same
   - Memory shell at `/#/memory` — still renders correctly (no UI regression)
   - **Paste the responses + screenshot or DOM snippet showing the shell still works.**
6. `git diff --stat` + `git log --oneline -1` on `worker/mw1-multiscope-schema`. **Paste.**

---

## Hard constraints

- **Lossless invariant.** Backfill MUST NOT modify any existing memory_observation row's data. Only ADD to the new join table. No deletes, no updates to existing scope_kind/scope_id columns.
- **Migration is forward-only.** No `op.execute("DELETE FROM ...")` in upgrade. Downgrade can drop the table; that's acceptable since the legacy columns remain authoritative.
- **Backward-compat for M6.** All existing routes must continue to return the same shapes. New columns are additive; new table is queried only by new code paths.
- **`is_primary` invariant.** Each observation must have exactly one `is_primary=TRUE` row in the join table. Enforce via app-level check in `write_observation` (raise if caller tries to write multiple primaries).
- **Don't change existing M1/M5 callers in this brief.** MW1 makes multi-scope POSSIBLE. MC1+future writes USE it. Old M1/M5 writes keep working via the primary-scope-only path.
- **Local-only git.** Worker commits on `worker/mw1-multiscope-schema`; terminal-Lead merges after Lead approves.

---

## What's deliberately NOT in MW1

- ❌ `attention_band`, `attention_reason`, `risk_band` columns — KILLED per D2 (use existing score/hit_count instead)
- ❌ `durability` enum — KILLED per simplification (just `wing` is enough)
- ❌ `shared_primitive_note` — KILLED (use evidence chain)
- ❌ Promotion state machine — KILLED per the corrected mental model (memory = observation layer, not decision layer)
- ❌ UI changes — that's MW2/MW3/MW4
- ❌ Auto-aging policy — KILLED per D9 (operator-driven `valid_until`)
- ❌ Privacy / visibility column — DEFERRED per D10 (scope IS the privacy boundary; explicit visibility column waits until multi-user)
- ❌ Retroactive multi-scope writes for M1/M5/M3+M4 outputs — those continue writing primary-scope-only; can be enriched later

The brief is intentionally minimal: just the multi-scope foundation + 2 new metadata columns. Everything else builds on this.

---

## Report-back format

```
MW1 — Multi-scope schema report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Migration apply confirmation
4. Backfill row-count verification (must match pre-migration observation count)
5. Tests added + pass count (especially #2 backfill, #5 non-primary scope lookup)
6. Backward-compat verification — M6 endpoints + UI still work
7. check.sh summary
8. Anything surprising — especially around the is_primary invariant, FK constraint behavior on scope deletion (won't happen given DO NOTHING on conflict, but verify), or interaction with existing search_observations
```

---

**Worker: MW1 is the structural foundation. After it lands, observations are properly many-to-many with scopes — which means MC1-MC5 carryover writes can land cleanly (one obs, multiple scope rows) instead of duplicating observations. The Salesforce/ChurnZero/Gong integration future-proofing starts here. End-state memory becomes a real graph (entities + scopes + observations + evidence) instead of a flat list with hierarchical scope strings.**
