"""Test fixtures for memory keystone tests.

Requires a running Postgres at ARTEMIS_DB_URL (or ARTEMIS_TEST_DB_URL override),
already migrated via `alembic upgrade head`. The fixtures do NOT create or drop
the schema — that's owned by Alembic. Per-test isolation comes from a TRUNCATE
before each test.

Why this shape:
- Function-scoped fixture loops (pyproject.toml) avoid cross-loop asyncpg
  binding errors. A session-scoped schema setup fixture would clash with
  function-scoped tests.
- NullPool: each test gets fresh connections, no pool-cached connections
  bound to a previous loop.
- pgvector codec attached per engine (see artemis/db.py).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.memory.models  # noqa: F401 — registers MemoryEmbedding on Base.metadata
from artemis.config import settings
from artemis.db import attach_pgvector_codec

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)
_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_engine)

_TRUNCATE_SQL = text(
    "TRUNCATE memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session. Memory tables are truncated before the test runs."""
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session
