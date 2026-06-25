"""Tests for the forge_mode column on DevSession (Forge Phase 3, chunk 3.1).

Covers:
  - create_session with forge_mode="write" persists and reads back correctly
  - create_session default leaves forge_mode as None
  - update_session can flip read <-> write
  - update_session normalizes unknown values to None
  - update_session without forge_mode kwarg leaves existing value untouched

Engine strategy mirrors test_workspace_memory.py:
  - NullPool engine at module level
  - artemis.db.engine + SessionLocal overridden so get_session uses test engine
  - Per-test fixture TRUNCATEs dev_sessions and dev_projects before and after

Requires the test database (artemis_test) to have migration 0104 applied.
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
        f"REFUSING TO LOAD test_forge_mode_column: db_url={_DB_URL!r} is not a test database."
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

# Import repo AFTER engine override so lazy imports pick up the test engine.
from artemis.dev_projects import repository as repo  # noqa: E402

_TRUNCATE = text("TRUNCATE dev_sessions, dev_projects RESTART IDENTITY CASCADE")
_INSERT_PROJECT = text(
    "INSERT INTO dev_projects (name, path) VALUES ('Test', '/tmp/forge-mode-test') RETURNING id"
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


# ── create_session ────────────────────────────────────────────────────────────


async def test_create_session_write_mode_persists(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    row = await repo.create_session(db_session, project_id=project_id, forge_mode="write")
    assert row.forge_mode == "write"  # check before commit (commit expires the instance)
    await db_session.commit()

    # Reload from DB to confirm it round-trips.
    db_session.expire_all()
    reloaded = await repo.get_session(db_session, row.id)
    assert reloaded.forge_mode == "write"


async def test_create_session_read_mode_persists(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    row = await repo.create_session(db_session, project_id=project_id, forge_mode="read")
    await db_session.commit()

    assert row.forge_mode == "read"


async def test_create_session_default_is_none(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    row = await repo.create_session(db_session, project_id=project_id)
    await db_session.commit()

    assert row.forge_mode is None


async def test_create_session_unknown_mode_normalizes_to_none(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    # "execute" is not a valid forge_mode value; should be coerced to None.
    row = await repo.create_session(db_session, project_id=project_id, forge_mode="execute")
    await db_session.commit()

    assert row.forge_mode is None


# ── update_session ────────────────────────────────────────────────────────────


async def test_update_session_flip_none_to_write(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    row = await repo.create_session(db_session, project_id=project_id)
    await db_session.commit()
    assert row.forge_mode is None

    updated = await repo.update_session(db_session, row.id, forge_mode="write")
    await db_session.commit()

    assert updated.forge_mode == "write"


async def test_update_session_flip_write_to_read(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    row = await repo.create_session(db_session, project_id=project_id, forge_mode="write")
    await db_session.commit()

    updated = await repo.update_session(db_session, row.id, forge_mode="read")
    await db_session.commit()

    assert updated.forge_mode == "read"


async def test_update_session_clear_to_none(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    row = await repo.create_session(db_session, project_id=project_id, forge_mode="write")
    await db_session.commit()

    # Pass forge_mode=None explicitly — sentinel allows this to write None.
    updated = await repo.update_session(db_session, row.id, forge_mode=None)
    await db_session.commit()

    assert updated.forge_mode is None


async def test_update_session_unknown_mode_normalizes_to_none(db_session: AsyncSession) -> None:
    project_id = await _make_project(db_session)

    row = await repo.create_session(db_session, project_id=project_id, forge_mode="write")
    await db_session.commit()

    updated = await repo.update_session(db_session, row.id, forge_mode="badvalue")
    await db_session.commit()

    assert updated.forge_mode is None


async def test_update_session_without_forge_mode_leaves_value_untouched(
    db_session: AsyncSession,
) -> None:
    project_id = await _make_project(db_session)

    row = await repo.create_session(db_session, project_id=project_id, forge_mode="write")
    await db_session.commit()

    # Update a different field — forge_mode must not be touched.
    updated = await repo.update_session(db_session, row.id, title="New title")
    await db_session.commit()

    assert updated.forge_mode == "write"
