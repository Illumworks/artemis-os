"""Route tests for GET /api/dev-projects/sessions/{session_id}/active-run.

Covers:
  - No active run -> returns {"active_run": null}.
  - Active run with two log entries -> returns them in seq order.

Engine strategy mirrors test_forge_runs.py:
  - Override artemis.db.engine + SessionLocal before importing app modules that
    use the DB.
  - Per-test fixture TRUNCATEs relevant tables.

Run on main (Lead) after `alembic upgrade head` with the test DB available.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
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
        f"REFUSING TO LOAD test_active_run_route: db_url={_DB_URL!r} is not a test database."
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

# Import after engine override so everything picks up the test engine.
from artemis.dev_projects.forge_runs import append_log, create_run  # noqa: E402
from artemis.main import app  # noqa: E402

_TRUNCATE = text(
    "TRUNCATE forge_run_log, forge_runs, dev_sessions, dev_projects RESTART IDENTITY CASCADE"
)
_INSERT_PROJECT = text(
    "INSERT INTO dev_projects (name, path) VALUES ('AR Test', '/tmp/ar-test') RETURNING id"
)
_INSERT_SESSION = text(
    "INSERT INTO dev_sessions (project_id, provider) VALUES (:pid, 'claude-code') RETURNING id"
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


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _make_project_and_session(session: AsyncSession) -> tuple[int, int]:
    proj = await session.execute(_INSERT_PROJECT)
    project_id: int = proj.scalar_one()
    sess = await session.execute(_INSERT_SESSION, {"pid": project_id})
    dev_session_id: int = sess.scalar_one()
    await session.commit()
    return project_id, dev_session_id


# ── no active run ─────────────────────────────────────────────────────────────


async def test_active_run_none_when_no_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """Session exists but has no ForgeRun -> active_run is null."""
    _, dev_session_id = await _make_project_and_session(db_session)

    response = await client.get(f"/api/dev-projects/sessions/{dev_session_id}/active-run")
    assert response.status_code == 200
    assert response.json() == {"active_run": None}


# ── active run with log entries ───────────────────────────────────────────────


async def test_active_run_returns_log_in_seq_order(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Active run with two log entries -> both returned in seq 0, 1 order."""
    project_id, dev_session_id = await _make_project_and_session(db_session)

    await create_run(
        db_session,
        run_id="run_route_001",
        dev_session_id=dev_session_id,
        project_id=project_id,
    )
    await db_session.commit()

    await append_log(
        db_session,
        run_id="run_route_001",
        kind="token",
        payload={"text": "Hello"},
    )
    await db_session.commit()

    await append_log(
        db_session,
        run_id="run_route_001",
        kind="message",
        payload={"role": "assistant"},
    )
    await db_session.commit()

    response = await client.get(f"/api/dev-projects/sessions/{dev_session_id}/active-run")
    assert response.status_code == 200
    data = response.json()

    run = data["active_run"]
    assert run is not None
    assert run["run_id"] == "run_route_001"
    assert run["status"] == "running"
    assert run["started_at"] is not None
    assert run["completed_at"] is None
    assert run["error"] is None

    log = run["log"]
    assert len(log) == 2
    assert log[0] == {"seq": 0, "kind": "token", "payload": {"text": "Hello"}}
    assert log[1] == {"seq": 1, "kind": "message", "payload": {"role": "assistant"}}


# ── unknown session returns null (no 404) ─────────────────────────────────────


async def test_active_run_unknown_session_returns_null(client: AsyncClient) -> None:
    """Unknown session_id -> active_run: null (no run found, no 404)."""
    response = await client.get("/api/dev-projects/sessions/99999/active-run")
    assert response.status_code == 200
    assert response.json() == {"active_run": None}
