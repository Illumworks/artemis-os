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
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.marketing.models  # noqa: F401 — registers all marketing models on Base.metadata
from artemis.config import settings
from artemis.db import attach_pgvector_codec

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)
_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_engine)

# Order matters: child tables first (FK constraints)
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
    "territory_config "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session. Marketing tables are truncated before the test runs."""
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session
