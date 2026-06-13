"""Test fixtures for proactivity scheduler tests."""
# ruff: noqa: E402

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import artemis.brief.models  # noqa: F401
import artemis.db
import artemis.identity.models  # noqa: F401
import artemis.integrations.models  # noqa: F401
import artemis.meetings.models  # noqa: F401
import artemis.memory.models  # noqa: F401
import artemis.okr.models  # noqa: F401
import artemis.proactivity.models  # noqa: F401
from artemis.db import attach_pgvector_codec
from artemis.meetings.models import MeetingActionItemDismissal  # noqa: F401 — register table
from artemis.proactivity.models import RadarSurfacedItem  # noqa: F401 — register table

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "TRUNCATE on the live DB would destroy production data."
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

_TRUNCATE_SQL = text(
    "TRUNCATE morning_brief_deliveries, "
    "okr_checkin_breadcrumbs, "
    "brief_snapshots, "
    "commitments, "
    "radar_surfaced_items, "
    "meeting_action_item_dismissals, "
    "meeting_summaries, "
    "memory_conflicts, "
    "memory_relation_rejections, "
    "memory_relations, "
    "memory_entity_mentions, "
    "memory_entity_aliases, "
    "memory_entities, "
    "memory_observation_scopes, "
    "memory_embeddings, "
    "memory_evidence, "
    "memory_observations, "
    "memory_drawers, "
    "memory_scopes, "
    "raw_inputs, "
    "integrations, "
    "integration_configs, "
    "users "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()
