"""Tests for the hash chain primitives in hashchain.py."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.hashchain import (
    canonical_form,
    compute_this_hash,
    verify_chain,
)
from artemis.memory.raw_inputs import insert_raw_input


def _sample_canon(**overrides: object) -> str:
    defaults = dict(
        source_kind="user_turn",
        source_id="sess-1",
        actor="jon",
        scope_kind="global",
        scope_id="default",
        payload={"text": "hello"},
        created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        prev_hash=None,
    )
    defaults.update(overrides)
    return canonical_form(**defaults)  # type: ignore[arg-type]


# ── canonical_form ────────────────────────────────────────────────────────────


def test_canonical_form_is_deterministic() -> None:
    a = _sample_canon()
    b = _sample_canon()
    assert a == b


def test_canonical_form_key_order_independent() -> None:
    """Payload with different key insertion order produces same canonical form."""
    a = _sample_canon(payload={"z": 1, "a": 2})
    b = _sample_canon(payload={"a": 2, "z": 1})
    assert a == b


def test_compute_this_hash_deterministic() -> None:
    canon = _sample_canon()
    assert compute_this_hash(canon) == compute_this_hash(canon)


def test_compute_this_hash_changes_with_content() -> None:
    h1 = compute_this_hash(_sample_canon(payload={"x": 1}))
    h2 = compute_this_hash(_sample_canon(payload={"x": 2}))
    assert h1 != h2


# ── verify_chain on DB ────────────────────────────────────────────────────────


async def test_verify_chain_empty_table_ok(db_session: AsyncSession) -> None:
    result = await verify_chain(db_session)
    assert result.ok
    assert result.row_count == 0


async def test_verify_chain_single_row_ok(db_session: AsyncSession) -> None:
    async with db_session.begin_nested():
        await insert_raw_input(
            db_session,
            source_kind="system",
            scope_kind="global",
            scope_id="default",
            payload={"boot": True},
        )
    result = await verify_chain(db_session)
    assert result.ok
    assert result.row_count == 1


async def test_verify_chain_ten_rows_break_at_five(db_session: AsyncSession) -> None:
    from sqlalchemy import update

    from artemis.memory.raw_inputs import RawInput

    rows = []
    async with db_session.begin_nested():
        for i in range(10):
            r = await insert_raw_input(
                db_session,
                source_kind="agent_observation",
                scope_kind="global",
                scope_id="default",
                payload={"i": i},
            )
            rows.append(r)

    # Tamper with row at index 4 (0-based) → 5th row.
    target = rows[4]
    await db_session.execute(
        update(RawInput).where(RawInput.id == target.id).values(payload={"tampered": True})
    )
    await db_session.flush()

    result = await verify_chain(db_session)
    assert not result.ok
    assert result.first_break_id == target.id
