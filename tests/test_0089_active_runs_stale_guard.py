"""Regression test for migration 0089: v_floating_artemis_active_runs stale guard.

Verifies:
- A 'running' run with started_at older than 2 hours is EXCLUDED from the view.
- A 'running' run with a recent started_at is INCLUDED in the view.
- A 'queued' run with a recent started_at is INCLUDED.
- A 'completed' run (any age) is EXCLUDED.

DB: uses ARTEMIS_TEST_DB_URL (set by conftest) which must be at head (0089+).
"""

from __future__ import annotations

import os as _os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

_db_url = _os.environ.get("ARTEMIS_TEST_DB_URL") or _os.environ.get(
    "ARTEMIS_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)

if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session that cleans up agent_runs before/after each test."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            # Clean up before test
            await session.execute(text("DELETE FROM agent_runs"))
            await session.commit()
            yield session
            # Clean up after test
            await session.execute(text("DELETE FROM agent_runs"))
            await session.commit()
    finally:
        await engine.dispose()


async def _count_active_runs(session: AsyncSession) -> int:
    """Return the number of rows in v_floating_artemis_active_runs."""
    result = await session.execute(text("SELECT COUNT(*) FROM v_floating_artemis_active_runs"))
    return int(result.scalar_one())


async def _insert_agent_run(
    session: AsyncSession,
    *,
    run_id: str,
    status: str,
    started_at_offset_hours: float,
) -> None:
    """Insert a row into agent_runs with started_at = now() + offset hours.

    agent_id is NULL to avoid the FK constraint on agents(agent_id).
    The offset is embedded directly in the SQL literal to avoid asyncpg
    interval-parameter issues.
    """
    offset_secs = int(started_at_offset_hours * 3600)
    await session.execute(
        text(f"""
            INSERT INTO agent_runs (run_id, agent_id, status, started_at, owner_user_id)
            VALUES (
                :run_id,
                NULL,
                :status,
                now() + interval '{offset_secs} seconds',
                NULL
            )
        """),  # noqa: S608 — offset_secs is a computed int, not user input
        {"run_id": run_id, "status": status},
    )
    await session.commit()


async def test_stale_running_run_excluded(db_session: AsyncSession) -> None:
    """A 'running' row started >2h ago must NOT appear in the active-runs view."""
    await _insert_agent_run(
        db_session,
        run_id="stale-run-001",
        status="running",
        started_at_offset_hours=-3,  # 3 hours ago — stale
    )
    count = await _count_active_runs(db_session)
    assert count == 0, (
        f"Stale running run (3h old) must be excluded from active-runs view; got count={count}"
    )


async def test_fresh_running_run_included(db_session: AsyncSession) -> None:
    """A 'running' row started <2h ago must appear in the active-runs view."""
    await _insert_agent_run(
        db_session,
        run_id="fresh-run-001",
        status="running",
        started_at_offset_hours=-0.5,  # 30 minutes ago — fresh
    )
    count = await _count_active_runs(db_session)
    assert count == 1, (
        f"Fresh running run (30m old) must be counted in active-runs view; got count={count}"
    )


async def test_fresh_queued_run_included(db_session: AsyncSession) -> None:
    """A 'queued' row started <2h ago must appear in the active-runs view."""
    await _insert_agent_run(
        db_session,
        run_id="queued-run-001",
        status="queued",
        started_at_offset_hours=-0.1,  # 6 minutes ago — fresh
    )
    count = await _count_active_runs(db_session)
    assert count == 1, f"Fresh queued run must be counted in active-runs view; got count={count}"


async def test_completed_run_excluded(db_session: AsyncSession) -> None:
    """A 'completed' row must NOT appear in the active-runs view regardless of age."""
    await _insert_agent_run(
        db_session,
        run_id="completed-run-001",
        status="completed",
        started_at_offset_hours=-0.5,
    )
    count = await _count_active_runs(db_session)
    assert count == 0, f"Completed run must never appear in active-runs view; got count={count}"


async def test_mixed_stale_and_fresh_only_fresh_counted(db_session: AsyncSession) -> None:
    """With one stale and one fresh running run, only the fresh one is counted."""
    await _insert_agent_run(
        db_session,
        run_id="mix-stale-001",
        status="running",
        started_at_offset_hours=-4,  # stale
    )
    await _insert_agent_run(
        db_session,
        run_id="mix-fresh-001",
        status="running",
        started_at_offset_hours=-1,  # fresh (1h ago is within the 2h window)
    )
    count = await _count_active_runs(db_session)
    assert count == 1, f"Mixed stale+fresh: only the fresh run should appear; got count={count}"
