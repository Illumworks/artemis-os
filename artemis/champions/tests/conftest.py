"""Fixtures for the Champions digest tests.

Run against an isolated database -- concurrent pytest runs deadlock on TRUNCATE:

    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_a \\
    uv run pytest artemis/champions/tests/ -v
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.champions.models  # noqa: F401 — registers Champions models on Base.metadata
from artemis.db import attach_pgvector_codec

# Hard guard against live-DB destruction.
_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "Set ARTEMIS_TEST_DB_URL=...artemis_test... to avoid live-DB destruction."
    )

_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_engine)

_TRUNCATE_SQL = text("TRUNCATE champions_items, champions_runs RESTART IDENTITY CASCADE")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session
