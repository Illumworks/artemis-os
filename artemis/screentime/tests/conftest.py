"""Test fixtures for Screen-Time Watch tests.

Mirrors artemis/marketing/tests/conftest.py: NullPool per-test engine, hard
live-DB guard, TRUNCATE only the screentime_* tables before each test. Requires
a migrated test DB (artemis_test_screentime, migrated to 0102).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
import artemis.screentime.models  # noqa: F401 — registers screentime models on Base.metadata
from artemis.db import attach_pgvector_codec
from artemis.screentime.models import SCREENTIME_TABLES

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database. "
        "Set ARTEMIS_DB_URL/ARTEMIS_TEST_DB_URL=...artemis_test_screentime."
    )

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    bind=_test_engine, expire_on_commit=False, class_=AsyncSession
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with _test_engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE TABLE {', '.join(SCREENTIME_TABLES)} RESTART IDENTITY CASCADE")
        )
    async with artemis.db.SessionLocal() as session:
        yield session
