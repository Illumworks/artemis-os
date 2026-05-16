"""Test fixtures for memory keystone tests.

Requires a running Postgres at ARTEMIS_DB_URL (or ARTEMIS_TEST_DB_URL override).
Tables are created from SQLAlchemy metadata at session start and dropped at session end.
Each test gets a fresh session; memory tables are truncated before each test for isolation.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Register models on Base.metadata before create_all
import artemis.memory.models  # noqa: F401
from artemis.config import settings
from artemis.db import Base
from artemis.memory.models import (
    MemoryDrawer,
    MemoryEvidence,
    MemoryObservation,
    MemoryScope,
)

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)
_engine = create_async_engine(_db_url, echo=False, pool_pre_ping=True)

_MEMORY_TABLES = [
    MemoryScope.__table__,
    MemoryDrawer.__table__,
    MemoryObservation.__table__,
    MemoryEvidence.__table__,
]

_TRUNCATE_SQL = text(
    "TRUNCATE memory_evidence, memory_observations, memory_drawers, memory_scopes "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_memory_schema() -> AsyncIterator[None]:
    async with _engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=_MEMORY_TABLES, checkfirst=True)
        )
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.drop_all(c, tables=list(reversed(_MEMORY_TABLES)))
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session. Memory tables are truncated before the test runs."""
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session
