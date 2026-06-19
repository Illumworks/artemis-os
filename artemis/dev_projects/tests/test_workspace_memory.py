"""Unit tests for artemis.dev_projects.workspace_memory.

Covers:
  - ensure_workspace_memory creates a fresh row when none exists
  - update_workspace_memory sets plan/progress/file_map newest-wins and
    leaves untouched fields intact
  - append_decision twice accumulates 2 entries in order (losslessness +
    flag_modified works)

Engine strategy (mirrors tests/test_signal_routing_status.py):
  - NullPool engine at module level.
  - artemis.db.engine + SessionLocal overridden so get_session uses test engine.
  - Per-test fixture TRUNCATEs the table before and after.

Requires the test database (artemis_test) to have 0101 migration applied.
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
        f"REFUSING TO LOAD test_workspace_memory: db_url={_DB_URL!r} is not a test database."
    )

_test_engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(  # type: ignore[assignment]
    _test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)

# Import repo functions AFTER engine override so any lazy imports pick up test engine.
from artemis.dev_projects.workspace_memory import (  # noqa: E402
    append_decision,
    ensure_workspace_memory,
    get_workspace_memory,
    update_workspace_memory,
)

_TRUNCATE = text(
    "TRUNCATE project_workspace_memory, dev_projects RESTART IDENTITY CASCADE"
)
_INSERT_PROJECT = text(
    "INSERT INTO dev_projects (name, path) VALUES ('Test Project', '/tmp/test-proj') RETURNING id"
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


async def _make_project(session: AsyncSession) -> int:
    result = await session.execute(_INSERT_PROJECT)
    project_id: int = result.scalar_one()
    await session.commit()
    return project_id


# ── ensure_workspace_memory ───────────────────────────────────────────────────


async def test_ensure_creates_row(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    # Nothing exists yet.
    assert await get_workspace_memory(db_session, project_id) is None

    row = await ensure_workspace_memory(db_session, project_id)
    await db_session.commit()

    assert row.id is not None
    assert row.project_id == project_id
    assert row.plan is None
    assert row.progress is None
    assert row.decisions == []
    assert row.file_map == {}
    assert row.open_threads == []


async def test_ensure_is_idempotent(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    row1 = await ensure_workspace_memory(db_session, project_id)
    await db_session.commit()
    row2 = await ensure_workspace_memory(db_session, project_id)

    assert row1.id == row2.id


# ── update_workspace_memory ───────────────────────────────────────────────────


async def test_update_sets_provided_fields(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)
    await ensure_workspace_memory(db_session, project_id)
    await db_session.commit()

    row = await update_workspace_memory(
        db_session,
        project_id,
        plan="Build the thing",
        progress="In progress",
        file_map={"src/main.py": "entry point"},
    )
    await db_session.commit()

    assert row.plan == "Build the thing"
    assert row.progress == "In progress"
    assert row.file_map == {"src/main.py": "entry point"}
    # untouched fields stay at defaults
    assert row.decisions == []
    assert row.open_threads == []


async def test_update_partial_leaves_other_fields_intact(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    await update_workspace_memory(db_session, project_id, plan="Initial plan", progress="alpha")
    await db_session.commit()

    # Update only progress — plan must survive.
    await update_workspace_memory(db_session, project_id, progress="beta")
    await db_session.commit()

    row = await get_workspace_memory(db_session, project_id)
    assert row is not None
    assert row.plan == "Initial plan"
    assert row.progress == "beta"


# ── append_decision ───────────────────────────────────────────────────────────


async def test_append_decision_accumulates_in_order(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    await append_decision(db_session, project_id, "First decision")
    await db_session.commit()

    await append_decision(db_session, project_id, "Second decision")
    await db_session.commit()

    row = await get_workspace_memory(db_session, project_id)
    assert row is not None
    assert len(row.decisions) == 2
    assert row.decisions[0]["text"] == "First decision"
    assert row.decisions[1]["text"] == "Second decision"
    # both entries must have an iso timestamp
    assert "ts" in row.decisions[0]
    assert "ts" in row.decisions[1]


async def test_append_decision_does_not_clobber_existing(db_session: AsyncSession) -> None:
    """Prove flag_modified works: second append keeps the first entry."""
    project_id = await _make_project(db_session)

    await append_decision(db_session, project_id, "Keep me")
    await db_session.commit()

    # Refresh from DB to simulate a new request context (expire_all is sync).
    db_session.expire_all()

    await append_decision(db_session, project_id, "Add me too")
    await db_session.commit()

    row = await get_workspace_memory(db_session, project_id)
    assert row is not None
    texts = [e["text"] for e in row.decisions]
    assert "Keep me" in texts
    assert "Add me too" in texts
