"""Test fixtures for artemis.costs tests.

Requires a running Postgres at ARTEMIS_TEST_DB_URL (or ARTEMIS_DB_URL override),
already migrated to include the cost_events table. Per-test isolation via TRUNCATE.

Run against artemis_test_cost_p1:
    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_cost_p1 \\
    uv run pytest artemis/costs/tests/ -v
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builders.models  # noqa: F401 — registers AgentRun, WorkflowRun on Base.metadata
import artemis.costs.models  # noqa: F401 — registers CostEvent on Base.metadata
import artemis.floating_artemis.models  # noqa: F401 — registers FA models on Base.metadata
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

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "cost_events, "
    # Source tables used by backfill tests — truncate to avoid cross-test pollution
    "floating_artemis_messages, floating_artemis_sessions, "
    "agent_runs, "
    "workflow_runs "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session with a TRUNCATED cost_events table."""
    async with AsyncSession(_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session
