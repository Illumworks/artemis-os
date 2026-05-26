"""Test fixtures for artemis.tools tests.

Requires Postgres at ARTEMIS_TEST_DB_URL migrated to head.
Combines marketing + builders + pipelines TRUNCATE sets.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builders.models  # noqa: F401
import artemis.db
import artemis.marketing.models  # noqa: F401
import artemis.pipelines.models  # noqa: F401  — pipeline_runs is FK dep of signal_queue
from artemis.db import attach_pgvector_codec

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(bind=_test_engine, expire_on_commit=False, class_=AsyncSession)

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "campaign_state_transitions, approvals, campaign_deliverables, content_asset_links, "
    "content_assets, campaign_briefs, campaign_candidates, scout_runs, "
    "qualifier_rule_applications, skipped_signals, signal_queue, rulesets, "
    "territory_config, signal_reason_codes, "
    "pipeline_ai_conversations, pipeline_runs, pipelines, "
    "agent_context, agent_run_trajectory_summaries, definition_proposals, agent_runs, "
    "agent_skills, workflow_runs, agents, skills, workflows, agent_chains, agent_dags, "
    "builder_sessions "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session with clean tables."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()
