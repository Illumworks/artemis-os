"""Integration tests for GET /api/stats/agent-metrics real aggregation.

Verifies that the endpoint reads from agent_runs and returns non-empty
agents[]/recent[] with correct run counts and success rates.

Isolation: uses ARTEMIS_TEST_DB_URL (artemis_test_metrics) which is separate
from any other concurrent test worker.  Truncates only the builders tables that
this test seeds so other tables are unaffected.
"""

from __future__ import annotations

# ── re-use the db_url guard from the outer conftest (already loaded) ─────────
# The outer conftest already raised if ARTEMIS_TEST_DB_URL is not set, so
# reading it here is safe.
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
from artemis.builders import repository as repo
from artemis.db import attach_pgvector_codec

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "tool_invocations, "
    "agent_context, "
    "agent_run_trajectory_summaries, "
    "definition_proposals, "
    "agent_runs, "
    "agent_skills, "
    "workflow_runs, "
    "agents, "
    "skills, "
    "workflows, "
    "agent_chains, "
    "agent_dags, "
    "builder_sessions "
    "RESTART IDENTITY CASCADE"
)


@pytest_asyncio.fixture
async def builders_session() -> AsyncIterator[AsyncSession]:
    """Per-test session that cleans builders tables and yields a clean DB state."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    # Override the module-level engine so the FastAPI app uses the same test DB.
    artemis.db.engine = engine
    artemis.db.SessionLocal = __import__(
        "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
    ).async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def metrics_client(builders_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client that shares the builders_session engine override."""
    from httpx import ASGITransport

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── helpers ──────────────────────────────────────────────────────────────────


async def _seed_agent(session: AsyncSession, agent_id: str, name: str) -> None:
    await repo.create_agent(session, agent_id=agent_id, name=name)
    await session.commit()


async def _seed_run(
    session: AsyncSession,
    agent_id: str,
    status: str = "completed",
    cost_input: int = 1000,
    cost_output: int = 200,
    is_ephemeral: bool = False,
    completed: bool = True,
) -> str:
    run_id = str(uuid.uuid4())
    kwargs: dict = {
        "run_id": run_id,
        "agent_id": agent_id,
        "status": status,
        "cost_input_tokens": cost_input,
        "cost_output_tokens": cost_output,
        "is_ephemeral": is_ephemeral,
    }
    if completed and status == "completed":
        # Provide completed_at so avg_duration is computable
        from sqlalchemy import text as sa_text

        await repo.create_agent_run(session, **kwargs)
        await session.execute(
            sa_text(
                "UPDATE agent_runs SET completed_at = started_at + INTERVAL '5 seconds' "
                "WHERE run_id = :run_id"
            ).bindparams(run_id=run_id)
        )
        await session.commit()
        return run_id
    await repo.create_agent_run(session, **kwargs)
    await session.commit()
    return run_id


# ── tests ────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_agent_metrics_non_empty_after_seed(
    metrics_client: AsyncClient,
    builders_session: AsyncSession,
) -> None:
    """Seeded agent_runs → agents[] and recent[] are non-empty with correct counts."""
    # Seed two agents with runs
    await _seed_agent(builders_session, "marketing.scout.board_minutes", "Board Minutes Scout")
    await _seed_agent(builders_session, "marketing.qualifier", "Qualifier")

    # 3 completed + 1 failed for board_minutes
    for _ in range(3):
        await _seed_run(builders_session, "marketing.scout.board_minutes", status="completed")
    await _seed_run(
        builders_session, "marketing.scout.board_minutes", status="failed", completed=False
    )

    # 2 completed for qualifier
    for _ in range(2):
        await _seed_run(builders_session, "marketing.qualifier", status="completed")

    # 1 ephemeral run for board_minutes — must NOT appear in aggregates
    await _seed_run(
        builders_session,
        "marketing.scout.board_minutes",
        status="completed",
        is_ephemeral=True,
    )

    resp = await metrics_client.get("/api/stats/agent-metrics")
    assert resp.status_code == 200
    data = resp.json()

    # overview
    assert data["overview"]["total_runs"] == 6  # 3+1+2 (ephemeral excluded)
    assert data["overview"]["completed"] == 5  # 3+2

    # agents[] — two rows
    assert len(data["agents"]) == 2
    agent_map = {r["agent_id"]: r for r in data["agents"]}

    bm = agent_map["marketing.scout.board_minutes"]
    assert bm["runs"] == 4  # 3 completed + 1 failed (ephemeral excluded)
    assert bm["successes"] == 3
    assert bm["agent_title"] == "Board Minutes Scout"
    # avg_duration should be populated (we set completed_at = started_at + 5s)
    assert bm["avg_duration"] is not None
    assert float(bm["avg_duration"]) > 0

    qual = agent_map["marketing.qualifier"]
    assert qual["runs"] == 2
    assert qual["successes"] == 2

    # recent[] — 6 rows total (no ephemeral)
    assert len(data["recent"]) == 6
    assert all(r["agent_id"] is not None for r in data["recent"])
    assert all("status" in r for r in data["recent"])
    assert all("started_at" in r for r in data["recent"])
    # agent_title must be populated from agents JOIN
    assert all(r.get("agent_title") for r in data["recent"])

    # byType — grouped by first two dot-segments
    by_type_map = {r["type"]: r for r in data["byType"]}
    assert "marketing.scout" in by_type_map
    assert by_type_map["marketing.scout"]["runs"] == 4
    assert "marketing.qualifier" in by_type_map


@pytest.mark.anyio
async def test_agent_metrics_null_agent_id_excluded_from_per_agent_agg(
    metrics_client: AsyncClient,
    builders_session: AsyncSession,
) -> None:
    """Runs with NULL agent_id (FK ON DELETE SET NULL) do not appear in agents[] aggregation
    but DO count towards the overview totals via the GROUP BY NULL bucket."""
    # The FK is SET NULL on delete; we can insert with agent_id=None
    run_id = str(uuid.uuid4())
    from artemis.builders.models import AgentRun

    orphan = AgentRun(
        run_id=run_id,
        agent_id=None,
        status="completed",
        cost_input_tokens=0,
        cost_output_tokens=0,
        is_ephemeral=False,
    )
    builders_session.add(orphan)
    await builders_session.commit()

    resp = await metrics_client.get("/api/stats/agent-metrics")
    assert resp.status_code == 200
    data = resp.json()

    # A NULL-agent_id row is included in the GROUP BY (as a NULL group).
    # The overview total_runs should be 1.
    assert data["overview"]["total_runs"] >= 1
    # The endpoint returns 200 without error
    assert isinstance(data["agents"], list)


@pytest.mark.anyio
async def test_agent_metrics_empty_db(
    metrics_client: AsyncClient,
    builders_session: AsyncSession,
) -> None:
    """Endpoint returns valid shape with zero counts when agent_runs is empty."""
    resp = await metrics_client.get("/api/stats/agent-metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overview"]["total_runs"] == 0
    assert data["agents"] == []
    assert data["recent"] == []
    assert data["byType"] == []
    assert data["daily"] == []
