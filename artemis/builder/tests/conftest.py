"""Test fixtures for artemis/builder tests (O1 trajectory summarizer).

Requires a running Postgres at ARTEMIS_TEST_DB_URL (or ARTEMIS_DB_URL with
"artemis_test" in the URL), already migrated via `alembic upgrade head`.

Per-test isolation via TRUNCATE, same pattern as artemis/builders/tests/conftest.py.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builder.repository  # noqa: F401 — ensure O1 models are registered
import artemis.builders.models  # noqa: F401 — registers all builder models on Base.metadata
import artemis.db
from artemis.db import attach_pgvector_codec

# Hard guard against live-DB destruction.
_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "TRUNCATE on the live DB would destroy production data. Set ARTEMIS_DB_URL=...artemis_test."
    )

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

# Child tables first (FK constraints).
_TRUNCATE_SQL = text(
    "TRUNCATE "
    "agent_context, "
    "agent_run_trajectory_summaries, "
    "definition_proposals, "
    "agent_runs, "
    "agent_skills, "
    "workflow_runs, "
    "agents, "
    "skills, "
    "workflows, "
    "agent_chains, "
    "agent_dags, "
    "builder_sessions "
    "RESTART IDENTITY CASCADE"
)


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
