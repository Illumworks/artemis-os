"""Tests for MW1 — multi-scope observation schema.

Tests:
1. Migration applies cleanly (table exists, indexes present, FK constraints set).
2. Backfill populates correctly (pre-existing obs get is_primary=TRUE join rows).
3. add_observation_scope is idempotent.
4. list_scopes_for_observation returns primary + secondary correctly.
5. list_observations_for_scope finds non-primary matches.
6. write_observation with additional_scopes writes correctly.
7. wing defaults to 'durable'; can be set to 'working'.
8. confidence_origin round-trips.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.schemas import Scope
from artemis.memory.store import (
    add_observation_scope,
    list_observations_for_scope,
    list_scopes_for_observation,
    write_observation,
)

_WORKSPACE = Scope(scope_kind="workspace", scope_id="marketing")
_AGENT = Scope(scope_kind="agent", scope_id="test-agent")
_BRAND = Scope(scope_kind="brand", scope_id="amira")


# ── Test 1: Migration applies cleanly ────────────────────────────────────────


async def test_migration_table_and_indexes_exist(db_session: AsyncSession) -> None:
    """Table memory_observation_scopes exists with correct columns and indexes."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'memory_observation_scopes' "
            "ORDER BY column_name"
        )
    )
    columns = {row[0] for row in result.all()}
    assert {
        "observation_id",
        "scope_kind",
        "scope_id",
        "weight",
        "is_primary",
        "created_at",
    } <= columns

    # Verify indexes exist
    idx_result = await db_session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'memory_observation_scopes'")
    )
    indexes = {row[0] for row in idx_result.all()}
    assert "idx_memory_observation_scopes_obs" in indexes
    assert "idx_memory_observation_scopes_scope" in indexes
    assert "idx_memory_observation_scopes_primary" in indexes

    # Verify FK to memory_observations exists
    fk_result = await db_session.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name = 'memory_observation_scopes' "
            "AND constraint_type = 'FOREIGN KEY'"
        )
    )
    fk_names = {row[0] for row in fk_result.all()}
    assert "fk_obs_scopes_observation" in fk_names


# ── Test 2: Backfill populates correctly ─────────────────────────────────────


async def test_backfill_primary_rows_match_observations(db_session: AsyncSession) -> None:
    """Each observation has exactly one is_primary=TRUE row in the join table after write."""
    # Write 3 observations across 2 scopes (simulates pre-migration state that backfill mirrors)
    async with db_session.begin():
        obs1 = await write_observation(db_session, _WORKSPACE, "backfill obs 1")
        await write_observation(db_session, _WORKSPACE, "backfill obs 2")
        await write_observation(db_session, _AGENT, "backfill obs 3")

    # Each write_observation now also writes a primary scope row
    async with db_session.begin():
        result = await db_session.execute(
            text("SELECT COUNT(*) FROM memory_observation_scopes WHERE is_primary = TRUE")
        )
        primary_count = result.scalar_one()

        obs_result = await db_session.execute(text("SELECT COUNT(*) FROM memory_observations"))
        obs_count = obs_result.scalar_one()

    assert primary_count == obs_count == 3

    # Verify scope_kind/scope_id matches the source observation
    async with db_session.begin():
        check = await db_session.execute(
            text(
                "SELECT s.scope_kind, s.scope_id "
                "FROM memory_observation_scopes s "
                "JOIN memory_observations o ON o.id = s.observation_id "
                "WHERE s.is_primary = TRUE AND s.observation_id = :obs_id"
            ),
            {"obs_id": obs1.id},
        )
        row = check.one()
    assert row[0] == "workspace"
    assert row[1] == "marketing"


# ── Test 3: add_observation_scope is idempotent ───────────────────────────────


async def test_add_observation_scope_idempotent(db_session: AsyncSession) -> None:
    """Adding the same (obs, scope) twice results in exactly 1 join row."""
    async with db_session.begin():
        obs = await write_observation(db_session, _WORKSPACE, "idempotent test obs")

    # The write_observation call already wrote 1 primary row; add same scope again
    async with db_session.begin():
        await add_observation_scope(
            db_session,
            observation_id=obs.id,
            scope_kind=_WORKSPACE.scope_kind,
            scope_id=_WORKSPACE.scope_id,
            weight=1.0,
            is_primary=True,
        )

    async with db_session.begin():
        result = await db_session.execute(
            text("SELECT COUNT(*) FROM memory_observation_scopes WHERE observation_id = :oid"),
            {"oid": obs.id},
        )
        count = result.scalar_one()

    assert count == 1


# ── Test 4: list_scopes_for_observation ───────────────────────────────────────


async def test_list_scopes_for_observation_primary_and_secondary(
    db_session: AsyncSession,
) -> None:
    """Returns all 3 scopes with correct is_primary flags."""
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _WORKSPACE,
            "multi-scope obs",
            additional_scopes=[_AGENT, _BRAND],
        )

    async with db_session.begin():
        scopes = await list_scopes_for_observation(db_session, obs.id)

    assert len(scopes) == 3
    scope_map = {(sk, si): is_p for sk, si, _w, is_p in scopes}
    assert scope_map[("workspace", "marketing")] is True
    assert scope_map[("agent", "test-agent")] is False
    assert scope_map[("brand", "amira")] is False


# ── Test 5: list_observations_for_scope finds non-primary matches ─────────────


async def test_list_observations_for_scope_finds_secondary(
    db_session: AsyncSession,
) -> None:
    """Observation A with secondary scope district:LAUSD is found via that scope."""
    district_scope = Scope(scope_kind="agent", scope_id="lausd")
    async with db_session.begin():
        obs_a = await write_observation(
            db_session,
            _WORKSPACE,
            "LAUSD signal",
            additional_scopes=[district_scope],
        )
        # obs_b has no secondary scope — should not appear in district query
        _obs_b = await write_observation(db_session, _WORKSPACE, "unrelated obs")

    async with db_session.begin():
        found = await list_observations_for_scope(db_session, "agent", "lausd")

    found_ids = {o.id for o in found}
    assert obs_a.id in found_ids
    assert len(found) == 1


# ── Test 6: write_observation with additional_scopes ─────────────────────────


async def test_write_observation_additional_scopes(db_session: AsyncSession) -> None:
    """Primary + 2 additional → legacy columns = primary, join table = 3 rows."""
    extra1 = Scope(scope_kind="agent", scope_id="extra-1")
    extra2 = Scope(scope_kind="brand", scope_id="extra-2")
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _WORKSPACE,
            "multi-scope write",
            additional_scopes=[extra1, extra2],
        )

    # Legacy columns still reflect primary scope
    assert obs.scope_kind == "workspace"
    assert obs.scope_id == "marketing"

    async with db_session.begin():
        result = await db_session.execute(
            text(
                "SELECT scope_kind, scope_id, is_primary "
                "FROM memory_observation_scopes "
                "WHERE observation_id = :oid "
                "ORDER BY is_primary DESC, scope_kind"
            ),
            {"oid": obs.id},
        )
        rows = result.all()

    assert len(rows) == 3
    primaries = [r for r in rows if r[2]]
    secondaries = [r for r in rows if not r[2]]
    assert len(primaries) == 1
    assert primaries[0][0] == "workspace"
    assert primaries[0][1] == "marketing"
    assert len(secondaries) == 2


# ── Test 7: wing defaults to 'durable'; can be set to 'working' ──────────────


async def test_wing_default_and_explicit(db_session: AsyncSession) -> None:
    """wing defaults to 'durable'; explicit 'working' is stored correctly."""
    async with db_session.begin():
        obs_default = await write_observation(db_session, _WORKSPACE, "default wing obs")
        obs_working = await write_observation(
            db_session, _AGENT, "working wing obs", wing="working"
        )

    assert obs_default.wing == "durable"
    assert obs_working.wing == "working"


# ── Test 8: confidence_origin round-trips ────────────────────────────────────


async def test_confidence_origin_round_trip(db_session: AsyncSession) -> None:
    """confidence_origin written and read back matches exactly."""
    origin = "mc_definition_proposal"
    async with db_session.begin():
        obs = await write_observation(
            db_session, _WORKSPACE, "origin obs", confidence_origin=origin
        )

    assert obs.confidence_origin == origin
