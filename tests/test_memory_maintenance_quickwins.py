"""Tests for memory quick-wins: maintenance route + daily scheduler."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
import artemis.memory.models  # noqa: F401 — register memory tables on Base.metadata
from artemis.db import attach_pgvector_codec
from artemis.memory.models import MemoryObservation
from artemis.memory.schemas import Scope
from artemis.memory.store import write_observation
from artemis.memory.tests.test_b2_embeddings import MockProvider

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)

_TEST_ENGINE = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_TEST_ENGINE)
db_module.engine = _TEST_ENGINE
db_module.SessionLocal = async_sessionmaker(
    bind=_TEST_ENGINE,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text(
    """
    TRUNCATE
        memory_conflicts,
        memory_relation_rejections,
        memory_relations,
        memory_entity_mentions,
        memory_entity_aliases,
        memory_entities,
        memory_observation_scopes,
        memory_embeddings,
        memory_evidence,
        memory_observations,
        memory_drawers,
        memory_scopes,
        raw_inputs
    RESTART IDENTITY CASCADE
    """
)

_SCOPE = Scope(scope_kind="workspace", scope_id="default")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    db_module.engine = engine
    db_module.SessionLocal = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    async with AsyncSession(engine, expire_on_commit=False) as setup_session, setup_session.begin():
        await setup_session.execute(_TRUNCATE_SQL)

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


async def test_maintain_endpoint_returns_counts_and_decays_scores(
    db_session: AsyncSession,
    http_client: AsyncClient,
) -> None:
    provider = MockProvider()
    async with db_session.begin():
        discovery = await write_observation(
            db_session,
            _SCOPE,
            "discovery observation to decay",
            category="discovery",
            embedding_provider=provider,
        )
        warning = await write_observation(
            db_session,
            _SCOPE,
            "warning observation should not decay",
            category="warning",
            embedding_provider=provider,
        )

    discovery_score = discovery.score
    warning_score = warning.score

    response = await http_client.post("/api/memory/maintain")
    assert response.status_code == 200
    payload = response.json()
    assert payload["discovery"] >= 1
    assert payload["warning"] == 0

    db_session.expire_all()
    refreshed_discovery = await db_session.get(MemoryObservation, discovery.id)
    refreshed_warning = await db_session.get(MemoryObservation, warning.id)
    assert refreshed_discovery is not None
    assert refreshed_warning is not None
    assert refreshed_discovery.score == pytest.approx(discovery_score * 0.93, rel=1e-4)
    assert refreshed_warning.score == pytest.approx(warning_score, rel=1e-6)


async def test_scheduled_memory_maintenance_job_decays_scores_in_own_session(
    db_session: AsyncSession,
) -> None:
    from artemis.memory.scheduler import _run_memory_maintenance_job

    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "scheduled maintenance target",
            category="decision",
            embedding_provider=provider,
        )

    original_score = obs.score
    await _run_memory_maintenance_job()

    db_session.expire_all()
    refreshed = await db_session.get(MemoryObservation, obs.id)
    assert refreshed is not None
    assert refreshed.score == pytest.approx(original_score * 0.97, rel=1e-4)


async def test_memory_scheduler_start_and_stop() -> None:
    import artemis.memory.scheduler as sched_module
    from artemis.memory.scheduler import (
        JOB_ID,
        get_memory_scheduler,
        start_memory_scheduler,
        stop_memory_scheduler,
    )

    sched_module._scheduler = None

    start_memory_scheduler()
    scheduler = get_memory_scheduler()
    assert scheduler.running
    jobs = scheduler.get_jobs()
    assert any(job.id == JOB_ID for job in jobs)

    stop_memory_scheduler()
    assert sched_module._scheduler is None


async def test_memory_scheduler_idempotent_start() -> None:
    import artemis.memory.scheduler as sched_module
    from artemis.memory.scheduler import (
        JOB_ID,
        get_memory_scheduler,
        start_memory_scheduler,
        stop_memory_scheduler,
    )

    sched_module._scheduler = None

    start_memory_scheduler()
    start_memory_scheduler()

    jobs = get_memory_scheduler().get_jobs()
    memory_jobs = [job for job in jobs if job.id == JOB_ID]
    assert len(memory_jobs) == 1

    stop_memory_scheduler()
