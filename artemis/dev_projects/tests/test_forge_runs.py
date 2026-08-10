"""Unit tests for artemis.dev_projects.forge_runs.

Covers:
  - create_run -> get_active_run_for_session returns the run
  - append_log twice -> seq 0 then 1; get_run_log returns them in order
  - complete_run sets status + completed_at; get_active_run_for_session then
    returns None (run is no longer "running")

Engine strategy (mirrors test_workspace_memory.py):
  - NullPool engine at module level.
  - artemis.db.engine + SessionLocal overridden so get_session uses test engine.
  - Per-test fixture TRUNCATEs the relevant tables before and after.

Requires the test database (artemis_test) to have 0103 migration applied.
Run on main after `alembic upgrade head`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD test_forge_runs: db_url={_DB_URL!r} is not a test database."
    )

_test_engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    _test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)

# Import repo functions AFTER engine override so any lazy imports pick up test engine.
from artemis.dev_projects.forge_runs import (  # noqa: E402
    append_log,
    complete_run,
    create_run,
    get_active_run_for_session,
    get_run_log,
)

_TRUNCATE = text(
    "TRUNCATE forge_run_log, forge_runs, dev_sessions, dev_projects "
    "RESTART IDENTITY CASCADE"
)
_INSERT_PROJECT = text(
    "INSERT INTO dev_projects (name, path) VALUES ('Forge Test', '/tmp/forge-test')"
    " RETURNING id"
)
_INSERT_SESSION = text(
    "INSERT INTO dev_sessions (project_id, provider) VALUES (:pid, 'claude-code')"
    " RETURNING id"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session with table reset before and after."""
    async with AsyncSession(_test_engine, expire_on_commit=False) as session:
        await session.execute(_TRUNCATE)
        await session.commit()
        yield session
        await session.execute(_TRUNCATE)
        await session.commit()


async def _make_project_and_session(session: AsyncSession) -> tuple[int, int]:
    """Insert a dev_project + dev_session and return (project_id, session_id)."""
    proj_result = await session.execute(_INSERT_PROJECT)
    project_id: int = proj_result.scalar_one()
    sess_result = await session.execute(_INSERT_SESSION, {"pid": project_id})
    dev_session_id: int = sess_result.scalar_one()
    await session.commit()
    return project_id, dev_session_id


# ── create_run / get_active_run_for_session ───────────────────────────────────


async def test_create_run_and_get_active(db_session: AsyncSession) -> None:
    project_id, dev_session_id = await _make_project_and_session(db_session)

    run = await create_run(
        db_session,
        run_id="run_test_001",
        dev_session_id=dev_session_id,
        project_id=project_id,
    )
    await db_session.commit()

    assert run.id is not None
    assert run.run_id == "run_test_001"
    assert run.status == "running"
    assert run.completed_at is None

    active = await get_active_run_for_session(db_session, dev_session_id)
    assert active is not None
    assert active.run_id == "run_test_001"


# ── append_log / get_run_log ──────────────────────────────────────────────────


async def test_append_log_seq_ordering(db_session: AsyncSession) -> None:
    project_id, dev_session_id = await _make_project_and_session(db_session)

    await create_run(
        db_session,
        run_id="run_test_002",
        dev_session_id=dev_session_id,
        project_id=project_id,
    )
    await db_session.commit()

    entry0 = await append_log(
        db_session,
        run_id="run_test_002",
        kind="token",
        payload={"text": "Hello"},
    )
    await db_session.commit()

    entry1 = await append_log(
        db_session,
        run_id="run_test_002",
        kind="message",
        payload={"role": "assistant"},
    )
    await db_session.commit()

    # seq starts at 0 and increments by 1
    assert entry0.seq == 0
    assert entry1.seq == 1

    log = await get_run_log(db_session, "run_test_002")
    assert len(log) == 2
    assert log[0].seq == 0
    assert log[0].kind == "token"
    assert log[1].seq == 1
    assert log[1].kind == "message"


# ── complete_run ──────────────────────────────────────────────────────────────


async def test_complete_run_clears_active(db_session: AsyncSession) -> None:
    project_id, dev_session_id = await _make_project_and_session(db_session)

    await create_run(
        db_session,
        run_id="run_test_003",
        dev_session_id=dev_session_id,
        project_id=project_id,
    )
    await db_session.commit()

    # Confirm it's active before completing.
    active = await get_active_run_for_session(db_session, dev_session_id)
    assert active is not None

    await complete_run(db_session, run_id="run_test_003", status="completed")
    await db_session.commit()

    # Must no longer appear as active.
    active_after = await get_active_run_for_session(db_session, dev_session_id)
    assert active_after is None

    # completed_at and status are persisted.
    db_session.expire_all()
    from sqlalchemy import select  # noqa: PLC0415

    from artemis.dev_projects.models import ForgeRun  # noqa: PLC0415

    result = await db_session.execute(
        select(ForgeRun).where(ForgeRun.run_id == "run_test_003")
    )
    run = result.scalar_one()
    assert run.status == "completed"
    assert run.completed_at is not None
