"""Test fixtures for the ws package tests.

The executor integration tests (test_e2_executor_integration.py) need a live
Postgres session. We reuse the same engine/session setup as the builders tests
rather than duplicating it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builders.models  # noqa: F401 — registers builder models on Base.metadata
import artemis.db
from artemis.config import settings
from artemis.db import attach_pgvector_codec

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "agent_context, "
    "agent_runs, "
    "workflow_runs, "
    "agents, "
    "skills, "
    "workflows, "
    "agent_chains, "
    "agent_dags "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()
