"""Test fixtures for Brand Signals tests.

Mirrors ``artemis/screentime/tests/conftest.py``: NullPool per-test engine, a
hard live-DB guard, and TRUNCATE of only the brand_signal_* tables before each
test. Requires a migrated test DB (``artemis_test_sentiment``, at 0119+).

The pure tests in this package (themes, composer) do not need a database, but
conftest loads for the whole directory, hence the guard rather than a skip.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
import artemis.sentiment.models  # noqa: F401 — registers Brand Signals models
from artemis.db import attach_pgvector_codec
from artemis.sentiment.models import BRAND_SIGNAL_TABLES

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database. "
        "Set ARTEMIS_DB_URL/ARTEMIS_TEST_DB_URL=...artemis_test_sentiment."
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
            text(f"TRUNCATE TABLE {', '.join(BRAND_SIGNAL_TABLES)} RESTART IDENTITY CASCADE")
        )
    async with artemis.db.SessionLocal() as session:
        yield session
