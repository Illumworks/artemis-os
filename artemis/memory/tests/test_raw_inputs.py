"""Tests for raw_inputs: insert, hash chain, archive placeholder behaviour."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.hashchain import verify_chain
from artemis.memory.raw_inputs import RawInput, insert_raw_input

# ── Basic insert ──────────────────────────────────────────────────────────────


async def test_insert_persists_row(db_session: AsyncSession) -> None:
    async with db_session.begin_nested():
        row = await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="global",
            scope_id="default",
            payload={"text": "hello"},
        )
    assert row.id is not None
    assert row.this_hash
    assert row.prev_hash is None  # first row


async def test_first_row_has_null_prev_hash(db_session: AsyncSession) -> None:
    async with db_session.begin_nested():
        row = await insert_raw_input(
            db_session,
            source_kind="system",
            scope_kind="global",
            scope_id="default",
            payload={"init": True},
        )
    assert row.prev_hash is None


async def test_chain_links_consecutive_rows(db_session: AsyncSession) -> None:
    async with db_session.begin_nested():
        r1 = await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="global",
            scope_id="d",
            payload={"n": 1},
        )
        r2 = await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="global",
            scope_id="d",
            payload={"n": 2},
        )
    assert r2.prev_hash == r1.this_hash


async def test_walk_chain_n_rows_all_valid(db_session: AsyncSession) -> None:
    n = 8
    async with db_session.begin_nested():
        for i in range(n):
            await insert_raw_input(
                db_session,
                source_kind="agent_observation",
                scope_kind="global",
                scope_id="x",
                payload={"i": i},
            )
    result = await verify_chain(db_session)
    assert result.ok
    assert result.row_count == n


async def test_corrupt_payload_detected(db_session: AsyncSession) -> None:
    async with db_session.begin_nested():
        row = await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="global",
            scope_id="d",
            payload={"real": "data"},
        )

    # Corrupt the payload directly in the DB (bypasses Python logic).
    await db_session.execute(
        update(RawInput).where(RawInput.id == row.id).values(payload={"tampered": True})
    )
    await db_session.flush()

    result = await verify_chain(db_session)
    assert not result.ok
    assert result.first_break_id == row.id


async def test_corrupt_this_hash_detected(db_session: AsyncSession) -> None:
    async with db_session.begin_nested():
        row = await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="global",
            scope_id="d",
            payload={"x": 1},
        )

    await db_session.execute(
        update(RawInput).where(RawInput.id == row.id).values(this_hash="deadbeef" * 8)
    )
    await db_session.flush()

    result = await verify_chain(db_session)
    assert not result.ok
    assert result.first_break_id == row.id


async def test_scoped_chain_walk_returns_only_that_scope(db_session: AsyncSession) -> None:
    async with db_session.begin_nested():
        await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="project",
            scope_id="proj-1",
            payload={"a": 1},
        )
        await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="global",
            scope_id="default",
            payload={"b": 2},
        )
        await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="project",
            scope_id="proj-1",
            payload={"c": 3},
        )

    result = await verify_chain(db_session, scope_kind="project", scope_id="proj-1")
    assert result.ok
    assert result.row_count == 2


async def test_archived_placeholder_does_not_break_chain(db_session: AsyncSession) -> None:
    async with db_session.begin_nested():
        row = await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="global",
            scope_id="d",
            payload={"old": True},
        )
        await insert_raw_input(
            db_session,
            source_kind="user_turn",
            scope_kind="global",
            scope_id="d",
            payload={"new": True},
        )

    # Simulate archiving: null payload, set archived_at.
    await db_session.execute(
        update(RawInput)
        .where(RawInput.id == row.id)
        .values(payload=None, archived_at=datetime.now(UTC))
    )
    await db_session.flush()

    result = await verify_chain(db_session)
    # Archived rows skip hash recompute; chain linkage is still verified.
    assert result.ok
