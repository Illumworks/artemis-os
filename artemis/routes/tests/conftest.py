"""Test fixtures for routes tests (calendar, meetings).

Requires a running Postgres at ARTEMIS_DB_URL (or ARTEMIS_TEST_DB_URL override),
already migrated via `alembic upgrade head`. Fixtures do NOT create or drop the
schema — that's owned by Alembic. Per-test isolation via TRUNCATE before each test.

Mirrors the pattern from artemis/builders/tests/conftest.py:
- NullPool so each test gets fresh connections, no pool-cached state
- attach_pgvector_codec defensive call
- TRUNCATE integration tables before each test, RESTART IDENTITY CASCADE
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.integrations.models  # noqa: F401 — registers integration models on Base.metadata
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

# Child tables first (FK constraints); integration_configs has no FK to integrations
_TRUNCATE_SQL = text("TRUNCATE integrations, integration_configs RESTART IDENTITY CASCADE")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session with a fresh NullPool engine."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the FastAPI app via ASGI transport."""
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
