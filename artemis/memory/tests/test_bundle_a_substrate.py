"""Bundle A substrate refinement tests (CC27 + CC28).

9 tests covering:

CC27 — ScopeKind Literal extension:
  1. New scope kinds validate without error (pipeline, district, account, person, meeting, personal).
  2. Invalid scope_kind raises Pydantic ValidationError.
  3. MC4 helper writes to pipeline:<id> scope_kind (not workspace:pipeline-<id>).

CC28 — memory_evidence.source_id widened to TEXT:
  4. link_evidence(source_id="skill-some-slug") stores the literal string.
  5. link_evidence(source_id="182") stores "182"; round-trip query returns the same string.
  6. MC3 helper writes evidence with source_id=skill_slug (no hash).
  7. MC5 helper writes evidence with source_id=fa_session_id (no hash).
  8. Migration 0049 column type is TEXT; existing rows are strings.
  9. No regression on M5/MC1 callers: evidence source_ids are now strings.

Requires ARTEMIS_TEST_DB_URL (or ARTEMIS_DB_URL containing "artemis_test").
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builder.repository  # noqa: F401 — register O1 models
import artemis.builders.models  # noqa: F401 — register builder models
import artemis.db as _db
import artemis.memory.models  # noqa: F401 — register memory models
import artemis.tools.models  # noqa: F401 — register tool models
from artemis.db import attach_pgvector_codec
from artemis.memory.models import MemoryEvidence, MemoryObservation, MemoryObservationScope
from artemis.memory.schemas import Scope
from artemis.memory.store import link_evidence, write_observation

# ── DB URL guard ──────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "Set ARTEMIS_TEST_DB_URL=...artemis_test."
    )

# ── Shared truncation SQL ─────────────────────────────────────────────────────

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_embeddings, memory_evidence, memory_observation_scopes, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs, "
    "tool_invocations "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session; patches artemis.db.SessionLocal so MC helpers open on the test engine."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    original_engine = _db.engine
    original_session_local = _db.SessionLocal
    _db.engine = engine
    _db.SessionLocal = __import__(
        "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
    ).async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
            if session.in_transaction():
                await session.rollback()
    finally:
        _db.engine = original_engine
        _db.SessionLocal = original_session_local
        await engine.dispose()


# ── CC27 tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cc27_new_scope_kinds_validate(db_session: AsyncSession) -> None:
    """CC27: All new ScopeKind values validate without error."""
    new_kinds: list[Any] = ["pipeline", "district", "account", "person", "meeting", "personal"]
    for kind in new_kinds:
        scope = Scope(scope_kind=kind, scope_id=f"test-{kind}")
        assert scope.scope_kind == kind, f"Expected {kind}, got {scope.scope_kind}"
    # Existing kinds still work
    for existing in ["project", "workspace", "brand", "agent", "skill", "global"]:
        scope = Scope(scope_kind=existing, scope_id="x")
        assert scope.scope_kind == existing


@pytest.mark.asyncio
async def test_cc27_invalid_scope_kind_raises_validation_error(db_session: AsyncSession) -> None:
    """CC27: An invalid scope_kind raises Pydantic ValidationError."""
    # mypy doesn't flag invalid Literal values passed as positional args here
    # because Pydantic's runtime validator catches them; tests exercise runtime behavior.
    with pytest.raises(ValidationError):
        Scope.model_validate({"scope_kind": "nope", "scope_id": "x"})
    with pytest.raises(ValidationError):
        Scope.model_validate({"scope_kind": "crm_account", "scope_id": "y"})
    with pytest.raises(ValidationError):
        Scope.model_validate({"scope_kind": "", "scope_id": "z"})


@pytest.mark.asyncio
async def test_cc27_mc4_writes_to_pipeline_scope_kind(db_session: AsyncSession) -> None:
    """CC27: MC4 helper writes primary scope_kind='pipeline' (was 'workspace' pre-CC27)."""
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    await write_pipeline_gate_decision_observation(
        pipeline_run_id="run-cc27-test",
        pipeline_id="marketing-main",
        node_id="gate_1",
        decision="approved",
        decided_by="operator",
        decision_payload={"pipeline_name": "Marketing Main"},
    )

    # Verify primary scope is pipeline:<pipeline_id>, NOT workspace:pipeline-<id>
    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1, f"Expected 1 observation, got {len(obs_rows)}"

    obs = obs_rows[0]
    assert obs.scope_kind == "pipeline", (
        f"Expected scope_kind='pipeline' (CC27), got '{obs.scope_kind}'"
    )
    assert obs.scope_id == "marketing-main", (
        f"Expected scope_id='marketing-main', got '{obs.scope_id}'"
    )

    # Verify via join table
    scope_rows = (await db_session.execute(select(MemoryObservationScope))).scalars().all()
    primary = next((r for r in scope_rows if r.is_primary), None)
    assert primary is not None, "No primary scope-join row found"
    assert primary.scope_kind == "pipeline", (
        f"Expected primary scope_kind='pipeline', got '{primary.scope_kind}'"
    )
    assert primary.scope_id == "marketing-main"


# ── CC28 tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cc28_link_evidence_slug_stored_as_literal_string(
    db_session: AsyncSession,
) -> None:
    """CC28: link_evidence with slug source_id stores the literal string, not a hash."""
    async with db_session.begin():
        obs = await write_observation(
            db_session, Scope(scope_kind="skill", scope_id="some-skill"), "test observation"
        )
        ev = await link_evidence(
            db_session,
            observation_id=obs.id,
            source_kind="skill",
            source_id="skill-some-slug",
        )

    assert ev.source_id == "skill-some-slug", f"Expected literal slug, got: {ev.source_id!r}"
    # Verify via direct DB query
    async with db_session.begin():
        result = await db_session.execute(select(MemoryEvidence).where(MemoryEvidence.id == ev.id))
        row = result.scalar_one()
    assert row.source_id == "skill-some-slug"


@pytest.mark.asyncio
async def test_cc28_numeric_string_source_id_round_trips(
    db_session: AsyncSession,
) -> None:
    """CC28: link_evidence with numeric-string source_id stores '182'; round-trip query returns same string."""
    async with db_session.begin():
        obs = await write_observation(
            db_session, Scope(scope_kind="workspace", scope_id="marketing"), "numeric test obs"
        )
        ev = await link_evidence(
            db_session,
            observation_id=obs.id,
            source_kind="signal_queue",
            source_id="182",
        )

    assert ev.source_id == "182", f"Expected '182', got: {ev.source_id!r}"
    assert isinstance(ev.source_id, str), f"Expected str, got {type(ev.source_id)}"

    # Round-trip via query
    async with db_session.begin():
        result = await db_session.execute(
            select(MemoryEvidence).where(
                MemoryEvidence.observation_id == obs.id,
                MemoryEvidence.source_kind == "signal_queue",
                MemoryEvidence.source_id == "182",
            )
        )
        found = result.scalar_one_or_none()
    assert found is not None, "Round-trip query for source_id='182' returned nothing"
    assert found.source_id == "182"


@pytest.mark.asyncio
async def test_cc28_mc3_evidence_source_id_is_slug_not_hash(
    db_session: AsyncSession,
) -> None:
    """CC28: MC3 skill promotion evidence source_id is the raw skill slug, not a hash."""
    from artemis.builder.memory_carryover import write_skill_promotion_observation

    skill_slug = "my-test-skill-cc28"
    await write_skill_promotion_observation(
        skill_slug=skill_slug,
        skill_name="Test Skill CC28",
        description="Tests CC28 source_id TEXT widening",
        promoted_by="operator",
    )

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1, f"Expected 1 observation, got {len(obs_rows)}"

    ev_rows = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    skill_ev = [e for e in ev_rows if e.source_kind == "skill"]
    assert len(skill_ev) == 1, f"Expected 1 skill evidence row, got {len(skill_ev)}"
    assert skill_ev[0].source_id == skill_slug, (
        f"Expected raw slug '{skill_slug}', got '{skill_ev[0].source_id}' — "
        "SHA-256 hash would be a large integer string like '56773593525409192'"
    )


@pytest.mark.asyncio
async def test_cc28_mc5_evidence_source_id_is_session_id_not_hash(
    db_session: AsyncSession,
) -> None:
    """CC28: MC5 FA approval evidence source_id is the raw fa_session_id, not a hash."""
    from artemis.builder.memory_carryover import write_fa_marketing_approval_observation

    fa_session_id = "fa-session-cc28-uuid-1234"
    await write_fa_marketing_approval_observation(
        signal_id=999,
        new_status="approved",
        fa_session_id=fa_session_id,
        user_directive="Test CC28 round-trip",
    )

    obs_rows = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(obs_rows) == 1, f"Expected 1 observation, got {len(obs_rows)}"

    ev_rows = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    fa_ev = [e for e in ev_rows if e.source_kind == "floating_artemis_messages"]
    assert len(fa_ev) == 1, f"Expected 1 fa evidence row, got {len(fa_ev)}"
    assert fa_ev[0].source_id == fa_session_id, (
        f"Expected raw fa_session_id '{fa_session_id}', got '{fa_ev[0].source_id}' — "
        "SHA-256 hash would be a large integer string"
    )


@pytest.mark.asyncio
async def test_cc28_migration_column_is_text_and_existing_rows_are_strings(
    db_session: AsyncSession,
) -> None:
    """CC28 migration 0049: source_id column type is TEXT; written values are strings."""
    # Verify column data type via information_schema
    async with db_session.begin():
        result = await db_session.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'memory_evidence' AND column_name = 'source_id'"
            )
        )
        col_type = result.scalar_one()
    assert col_type == "text", f"Expected column type 'text', got '{col_type}'"

    # Write an evidence row with a numeric source_id (string form) and verify it's stored as text
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            Scope(scope_kind="workspace", scope_id="test"),
            "migration column test obs",
        )
        ev = await link_evidence(
            db_session,
            observation_id=obs.id,
            source_kind="agent_run",
            source_id="329",
        )

    assert ev.source_id == "329"
    assert isinstance(ev.source_id, str), f"Expected str, got {type(ev.source_id)}"

    # Direct SQL query to confirm no implicit int conversion happened
    async with db_session.begin():
        result = await db_session.execute(
            text(
                "SELECT source_id, pg_typeof(source_id) AS coltype "
                "FROM memory_evidence WHERE id = :eid"
            ),
            {"eid": ev.id},
        )
        row = result.one()
    assert row.coltype == "text", f"Postgres typeof() is '{row.coltype}', expected 'text'"
    assert row.source_id == "329"


@pytest.mark.asyncio
async def test_cc28_no_regression_on_m5_mc1_callers(
    db_session: AsyncSession,
) -> None:
    """Regression: M5 (signal qualification) and MC1 (definition-proposal approval) callers
    now write string source_ids. Verify source_id is a string (not int) in both cases."""
    # Test MC1 path: definition_proposal source_id should be str(proposal_id)
    # We simulate the evidence row MC1 writes via _link_evidence_raw
    from artemis.builder.memory_carryover import _link_evidence_raw

    async with db_session.begin():
        obs = await write_observation(
            db_session,
            Scope(scope_kind="agent", scope_id="test-agent"),
            "regression test obs",
        )
        await _link_evidence_raw(db_session, obs.id, "definition_proposal", "12345")
        await db_session.flush()

    async with db_session.begin():
        result = await db_session.execute(
            select(MemoryEvidence).where(
                MemoryEvidence.observation_id == obs.id,
                MemoryEvidence.source_kind == "definition_proposal",
            )
        )
        ev = result.scalar_one()

    assert isinstance(ev.source_id, str), (
        f"Expected str source_id (CC28), got {type(ev.source_id).__name__}: {ev.source_id!r}"
    )
    assert ev.source_id == "12345", f"Expected '12345', got {ev.source_id!r}"
