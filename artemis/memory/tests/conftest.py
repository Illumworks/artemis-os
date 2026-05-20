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
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.memory.models  # noqa: F401 — registers all models on Base.metadata including MemoryConflict
from artemis.db import attach_pgvector_codec

# Hard guard against live-DB destruction. This conftest TRUNCATEs tables;
# if ARTEMIS_DB_URL does not contain "artemis_test", refuse to load.
_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "TRUNCATE on the live DB would destroy production data. Set ARTEMIS_DB_URL=...artemis_test."
    )
_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_engine)

_TRUNCATE_SQL = text(
    # M2: conflicts depend on memory_observations; truncate first
    "TRUNCATE memory_conflicts, "
    # Graph tables (depend on memory_entities + memory_observations)
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    # B1/B2 tables
    "memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes, "
    # M1: raw_inputs (observations FK to it; CASCADE handles raw_input_id → SET NULL)
    "raw_inputs RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session. Memory tables are truncated before the test runs."""
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session


@pytest.fixture
def test_session_factory() -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Session factory for injection into graph_extractor in tests.

    Creates fresh NullPool sessions on the same test engine, avoiding the
    'Future attached to a different loop' error that would occur if the
    production SessionLocal (with its connection pool) were used.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            yield session

    return _factory
