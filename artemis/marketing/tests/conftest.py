"""Test fixtures for Marketing OS tests.

Requires a running Postgres at ARTEMIS_DB_URL (or ARTEMIS_TEST_DB_URL override),
already migrated via `alembic upgrade head`. The fixtures do NOT create or drop
the schema — that's owned by Alembic. Per-test isolation comes from TRUNCATE
before each test.

Mirrors the pattern from artemis/memory/tests/conftest.py:
- NullPool so each test gets fresh connections, no pool-cached state
- attach_pgvector_codec defensive call (marketing tables don't use vectors, but
  importing artemis.memory.models may cascade)
- TRUNCATE the marketing tables before each test, RESTART IDENTITY CASCADE

Engine is created per-test (not module-level) so each asyncio function scope
gets a fresh asyncpg connection that is not bound to a previous event loop.
This avoids "Event loop is closed" errors when tests that use both `db_session`
and `client` run in sequence.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.marketing.models  # noqa: F401 — registers all marketing models on Base.metadata
from artemis.config import settings
from artemis.db import attach_pgvector_codec

# Hard guard against live-DB destruction. This conftest TRUNCATEs tables;
# if ARTEMIS_DB_URL does not contain "artemis_test", refuse to load.
_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "TRUNCATE on the live DB would destroy production data. Set ARTEMIS_DB_URL=...artemis_test."
    )

# Replace the main app engine with a NullPool engine at import time.
# This prevents "Future attached to a different loop" errors when the ASGI
# client makes HTTP requests across per-function asyncio loop boundaries.
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

# Order matters: child tables first (FK constraints).
# signal_reason_codes has no FK children from marketing tables — safe to truncate last.
_TRUNCATE_SQL = text(
    "TRUNCATE "
    "approvals, "
    "campaign_deliverables, "
    "content_asset_links, "
    "content_assets, "
    "campaign_briefs, "
    "campaign_candidates, "
    "scout_runs, "
    "signal_queue, "
    "rulesets, "
    "territory_config, "
    "signal_reason_codes "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session with a fresh engine.

    Each asyncio function-scope test gets a brand new NullPool engine so there
    are no connections bound to a closed event loop from prior tests.
    """
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
    """HTTP client bound to the FastAPI app via ASGI transport (no real server).

    Re-declares the root tests/conftest.py fixture so marketing tests can use
    both `client` and `db_session` in the same test without pytest fixture
    scoping issues across testpath roots.
    """
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
