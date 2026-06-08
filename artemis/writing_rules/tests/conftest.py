"""Test fixtures for writing_rules tests.

Requires artemis_test DB, already migrated via `alembic upgrade head`.
Truncates relevant tables before each test for isolation.

Use:
    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test \
    uv run pytest artemis/writing_rules/tests/ -q
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.marketing.models  # noqa: F401 — campaign_deliverables FK dep
import artemis.pipelines.models  # noqa: F401 — pipeline_runs FK dep of signal_queue
import artemis.writing_rules.models  # noqa: F401 — registers writing_rules models
from artemis.db import attach_pgvector_codec

# Hard guard against live-DB destruction.
_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "TRUNCATE on the live DB would destroy production data. "
        "Set ARTEMIS_DB_URL=...artemis_test."
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

# Truncation order: child tables first (FK constraints).
_TRUNCATE_SQL = text(
    "TRUNCATE "
    "templates, "
    "claims, "
    "tag_values, "
    "tag_dimensions, "
    "writing_training_candidates, "
    "writing_draft_thread_messages, "
    "writing_rules, "
    "writing_examples, "
    "writing_sources, "
    "writing_folders, "
    "writing_profiles, "
    "campaign_state_transitions, "
    "approvals, "
    "campaign_sends, "
    "campaign_deliverables, "
    "content_asset_links, "
    "content_assets, "
    "campaign_briefs, "
    "campaign_candidate_signals, "
    "campaign_candidates, "
    "scout_runs, "
    "qualifier_rule_applications, "
    "skipped_signals, "
    "district_contacts, "
    "districts, "
    "district_tier_bands, "
    "district_data_meta, "
    "signal_queue, "
    "rulesets, "
    "territory_config, "
    "signal_reason_codes "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture()
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


@pytest.fixture()
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the FastAPI app via ASGI transport (no real server).

    Mirrors the pattern from artemis/marketing/tests/conftest.py so that
    endpoint tests in writing_rules can use both `client` and `db_session`.
    """
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
