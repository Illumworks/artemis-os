# Bundle A — Memory substrate refinement (CC27 + CC28)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/bundle-a-substrate`
**Browser smoke owner:** Lead, post-merge — re-run any MC4 helper invocation, verify it writes to `pipeline:<id>` (not the `workspace:pipeline-{id}` workaround) and that `memory_evidence.source_id` accepts a string UUID without hashing.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~150 (2 schema extensions + 1 migration + helper refactors + tests).
**Priority:** HIGH — blocks Salesforce/ChurnZero/Gong integration (which adds `district`, `account`, `person`, etc. scopes and requires string-ID evidence). Both findings emerged from MC2-MC5 bundle implementation 2026-05-30.

---

## Why this exists

Two architectural workarounds the MC2-MC5 Worker had to use:

**CC27:** `ScopeKind` Literal is too narrow — `pipeline:<id>` doesn't exist as a kind, so MC4 wrote to `workspace:pipeline-<id>` as a workaround. Looking ahead to integrations (Salesforce/ChurnZero/Gong), we need `district`, `account`, `person`, `meeting` scope kinds too.

**CC28:** `memory_evidence.source_id` is `BigInteger` — but real source IDs include strings (skill slugs, pipeline_run UUIDs, FA session UUIDs). The Worker used SHA-256 hashing to fit BigInt → functional but breaks round-trip queries ("give me evidence for skill X" can't query back from a hash).

Bundle these because: both fixes touch memory module substrate, both unblock future integration work, both are surgical schema changes.

---

## Scope

### Part A — CC27: Extend `ScopeKind` Literal

**File:** `artemis/memory/schemas.py`

**Current:**
```python
ScopeKind = Literal["project", "workspace", "brand", "agent", "skill", "global"]
```

**New:**
```python
ScopeKind = Literal[
    "project",
    "workspace",
    "brand",
    "agent",
    "skill",
    "global",
    "pipeline",          # CC27: pipeline-run scope (MC4 needed this)
    "district",          # CC27: K-12 district context (Salesforce-ready)
    "account",           # CC27: CRM account (Salesforce/ChurnZero-ready)
    "person",            # CC27: contact/lead/employee
    "meeting",           # CC27: granola meeting transcript scope
    "personal",          # CC27: per-user personal scope (D10 privacy boundary)
]
```

**No migration needed** — `memory_scopes.scope_kind` is a `TEXT` column. The Literal is Pydantic-level only.

**Then update MC4's helper** in `artemis/builder/memory_carryover.py`:
```python
# Before (workaround):
primary = Scope(scope_kind="workspace", scope_id=f"pipeline-{pipeline_id}")

# After:
primary = Scope(scope_kind="pipeline", scope_id=pipeline_id)
```

**Backfill the existing MC4 observations** (currently 1 row in DB with scope_kind=`workspace`, scope_id=`pipeline-marketing.main`). Per the lossless invariant: do NOT modify the original observation. Instead, ADD a new scope-join row mapping the same observation to `pipeline:marketing.main` (is_primary=false). Document the historical workaround in code comments.

The MC4 helper going forward writes new observations to `pipeline:<id>` cleanly.

### Part B — CC28: Widen `memory_evidence.source_id` to TEXT

**Files:** 
- `alembic/versions/0049_memory_evidence_source_id_text.py` (NEW migration)
- `artemis/memory/models.py` (update `MemoryEvidence.source_id` type annotation)
- `artemis/memory/store.py` (update `link_evidence` signature)
- `artemis/builder/memory_carryover.py` (remove `_source_id_to_int` hash logic)

**Migration shape:**
```python
def upgrade():
    # Add a new TEXT column, populate from existing BigInt values stringified
    op.add_column("memory_evidence", 
        sa.Column("source_id_text", sa.Text(), nullable=True))
    op.execute("UPDATE memory_evidence SET source_id_text = source_id::text")
    op.alter_column("memory_evidence", "source_id_text", nullable=False)
    
    # Drop the old BigInt column, rename text column to source_id
    op.drop_column("memory_evidence", "source_id")
    op.alter_column("memory_evidence", "source_id_text", new_column_name="source_id")
    
    # Recreate the unique constraint if it existed on source_kind+source_id
    op.create_unique_constraint(
        "uq_evidence_obs_source",
        "memory_evidence",
        ["observation_id", "source_kind", "source_id"],
    )

def downgrade():
    # Reverse: text → bigint, casting (will fail if any non-numeric rows exist;
    # the downgrade is best-effort)
    ...
```

**Model update:**
```python
class MemoryEvidence(Base):
    ...
    source_id: Mapped[str] = mapped_column(Text, nullable=False)  # was BigInteger
```

**`link_evidence` signature update:**
```python
async def link_evidence(
    session: AsyncSession,
    observation_id: int,
    source_kind: EvidenceSourceKind,
    source_id: str,  # was int
    *,
    weight: float = 1.0,
) -> None:
    ...
```

**Backward-compat:** existing int source_ids (signal_queue:182, agent_run:329, etc.) become string representations of the same numbers. Existing M1/M5/MC1 helpers continue working — they're already passing `str(signal_id)` or `str(run_id)` in many code paths; just need to verify everywhere and update typing.

**Remove `_source_id_to_int` from `memory_carryover.py`** — no longer needed. The helpers can now pass raw strings (skill slugs, UUIDs, session IDs) directly to `link_evidence`.

**Refactor the 3 MC helpers that used the hash workaround:**
- MC3 (`write_skill_promotion_observation`) — passes `skill_slug` directly
- MC4 (`write_pipeline_gate_decision_observation`) — passes `pipeline_run_id` UUID directly
- MC5 (`write_fa_marketing_approval_observation`) — passes `fa_session_id` directly

### Part C — Reconciliation helpers (small, optional)

After CC28, the existing hashed source_ids from MC3/MC4/MC5 observations are stale (integer hashes pointing to nothing). Per lossless invariant: don't modify them.

Add a small utility `artemis/builder/memory_carryover.py:_reconciliation_log` that documents which observations have legacy hashed source_ids:

```python
# Module-level constant — observations created before CC28 used SHA-256 hashes
# for non-numeric source_ids. These IDs cannot be round-tripped to their
# original values. Listed here for audit purposes; queries against these
# observations' evidence should NOT trust source_id as a valid lookup key.
_LEGACY_HASHED_OBSERVATION_IDS: tuple[int, ...] = (29, 30, 31)  # MC3/MC4/MC5 smokes
```

Or just document in code comments. Don't try to "fix" the hashed values — that violates lossless.

### Part D — Tests

`artemis/memory/tests/test_bundle_a_substrate.py`:

1. **CC27 — `Scope(scope_kind="pipeline", scope_id="marketing.main")` validates without error.** Same for `district`, `account`, `person`, `meeting`, `personal`.
2. **CC27 — Invalid scope_kind raises Pydantic ValidationError.** E.g. `Scope(scope_kind="nope", ...)` rejected.
3. **CC27 — MC4 helper writes to `pipeline:<id>` scope_kind correctly.** Direct invocation; verify scope_kind=`pipeline` in `memory_observation_scopes`.
4. **CC28 — `link_evidence(source_id="skill-some-slug")` succeeds.** Verify row lands in `memory_evidence` with source_id as the literal string.
5. **CC28 — `link_evidence(source_id="numeric-string-like-182")` succeeds.** Round-trip query returns the same string.
6. **CC28 — MC3 helper writes evidence with `source_id=skill_slug` (no hash).** After direct MC3 invocation, query `memory_evidence WHERE observation_id=<new>` returns the original slug.
7. **CC28 — MC5 helper writes evidence with `source_id=fa_session_id` (no hash).** Same round-trip check.
8. **Migration 0049 applies cleanly.** Migration test: run upgrade, verify column type is TEXT, verify existing rows survived (their BigInt values became strings).
9. **No regression on M5/MC1.** Run a fresh M5 invocation (qualify a pending_qualification signal). Verify evidence row source_id is now `"182"` (string, was 182 int) but otherwise unchanged.

---

## Files owned

- EDIT: `artemis/memory/schemas.py` (CC27 — extend ScopeKind)
- NEW: `alembic/versions/0049_memory_evidence_source_id_text.py` (CC28 — migration)
- EDIT: `artemis/memory/models.py` (CC28 — source_id type annotation)
- EDIT: `artemis/memory/store.py` (CC28 — link_evidence signature)
- EDIT: `artemis/builder/memory_carryover.py` (CC27 — MC4 uses pipeline scope; CC28 — remove _source_id_to_int)
- EDIT: `artemis/marketing/repository.py` (CC28 — verify link_evidence callers pass strings, not ints)
- EDIT: `artemis/tools/signal_queue_ops.py` (CC28 — same, verify str(signal_id) in evidence link)
- NEW: `artemis/memory/tests/test_bundle_a_substrate.py`

---

## Acceptance criteria

1. `uv run alembic upgrade head` shows `0049_memory_evidence_source_id_text`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/memory/tests/test_bundle_a_substrate.py -v` — all 9 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Backfill check:** `SELECT COUNT(*) FROM memory_evidence WHERE source_id ~ '^[0-9]+$';` — should be the same count as pre-migration (existing int IDs survived as string representations). **Paste.**
5. **MC3/MC5 follow-up:** invoke MC3 helper with skill_slug + MC5 helper with fa_session_id (UUID-shaped string). Query `memory_evidence` for the new observations — `source_id` should be the original strings, NOT hashes. **Paste the queries.**
6. **MC4 follow-up:** invoke MC4 helper. Query `memory_observation_scopes` for the new observation — scope_kind should be `pipeline` (not `workspace`). **Paste.**
7. `git diff --stat` + `git log --oneline -1` on `worker/bundle-a-substrate`. **Paste.**

---

## Hard constraints

- **Lossless invariant.** Existing observations with hashed source_ids stay verbatim — don't try to "fix" them. Document via code comment.
- **Backward-compat for int callers.** Existing code passing `source_id=182` (int) should continue to work via implicit string conversion OR explicit `str(182)` updates at each call site. Verify the type-checker is happy after the change.
- **Migration is lossless.** No DELETE statements. Pure column-type widening with safe conversion.
- **`link_evidence` signature stays Pydantic-typed.** Use `EvidenceSourceKind` (CC23) for source_kind. New `source_id: str` (not `str | int`) — callers update to pass strings.
- **No new dependencies.** All work in the existing memory module.
- **Local-only git.** Worker commits on `worker/bundle-a-substrate`; terminal-Lead merges after Lead approves.

---

## Coordination with parallel Bundle B (CC21+CC22)

Bundle B runs in parallel, touches DIFFERENT files (`tool_invocations` model + migration 0050, `definition_proposals` model + migration 0051 — wait actually MIGRATION NUMBERING).

**Migration coordination:** Both bundles add migrations. Use the next-available migration numbers — coordinate via the migration-numbering convention. Bundle A claims **0049** (memory_evidence widening). Bundle B claims **0050** (tool_invocations) and **0051** (definition_proposals.rejection_reason). If both bundles land in different orders, the second to merge needs to rebase migration numbers.

**File overlap:** zero — different model files entirely.

---

## Report-back format

```
Bundle A — Memory substrate refinement report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Migration apply confirmation
4. Test pass count (9 new + regressions on existing M1/M5/MC1 + M3+M4 tests)
5. CC27 verification — MC4 writes to pipeline:<id> scope_kind
6. CC28 verification — skill slug + UUID source_ids round-trip cleanly (paste SELECT showing actual strings)
7. Backfill verification — existing int source_ids survived as strings
8. check.sh summary
9. Anything surprising — especially around the existing M5/MC1 callers and whether they were already passing strings or needed updates
```

---

**Worker: Bundle A clears the substrate technical debt that accumulated during the MC2-MC5 rapid stream. After this lands, future integrations (Salesforce/ChurnZero/Gong) can use proper scope kinds (district, account, person) and string-ID evidence (CRM record UUIDs, etc.) without workarounds. The platform is structurally ready for that integration work.**
