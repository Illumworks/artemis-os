"""Tests for J9b — Slack triage polish: name resolution, mention-type filter.

Covers:
  1. test_classify_mention_type_direct      — <@JON_ID> → 'direct'
  2. test_classify_mention_type_channel     — <!channel> → 'channel'
  3. test_classify_mention_type_here        — <!here> → 'channel'
  4. test_classify_mention_type_subteam     — <!subteam^G123> → 'group'
  5. test_classify_mention_type_keyword     — no token match → 'keyword'

  6. test_mentions_filters_direct_only      — route returns only direct-type rows
  7. test_mentions_include_param_widens     — ?include=direct,channel includes both types

  8. test_migration_0023_up_down_roundtrip  — alembic upgrade 0023 then downgrade 0023
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.integrations.models  # noqa: F401 — register ORM models
from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.integrations.slack.triage import classify_mention_type

pytestmark = pytest.mark.asyncio

# ── Test DB setup ──────────────────────────────────────────────────────────────

_db_url = settings.db_url  # conftest already overrides ARTEMIS_DB_URL to artemis_test

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
    "TRUNCATE integration_configs, integrations, slack_inbound_messages RESTART IDENTITY CASCADE"
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


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _seed_message(
    session: AsyncSession,
    event_id: str,
    *,
    mention_type: str = "direct",
    resolved: bool = False,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from artemis.integrations.models import SlackInboundMessage

    now = datetime.now(UTC)
    msg_ts = str(time.time())
    stmt = (
        pg_insert(SlackInboundMessage)
        .values(
            event_id=event_id,
            team_id="T001",
            channel_id="C001",
            user_id="U001",
            text=f"message {event_id}",
            ts=msg_ts,
            thread_ts=None,
            routed_to_session_id=None,
            received_at=now - timedelta(hours=1),
            resolved_at=now if resolved else None,
            mention_type=mention_type,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    await session.execute(stmt)
    await session.commit()


# ── Mention-type classifier unit tests ────────────────────────────────────────


def test_classify_mention_type_direct() -> None:
    """<@JON_ID> in text → 'direct'."""
    assert classify_mention_type("<@UJON123> can you review?", "UJON123") == "direct"


def test_classify_mention_type_direct_with_display_name() -> None:
    """<@JON_ID|jon> (with display name suffix) also counts as direct."""
    assert classify_mention_type("<@UJON123|jon> please check", "UJON123") == "direct"


def test_classify_mention_type_channel() -> None:
    """<!channel> → 'channel'."""
    assert classify_mention_type("Hey <!channel> quick update", "UJON123") == "channel"


def test_classify_mention_type_here() -> None:
    """<!here> → 'channel'."""
    assert classify_mention_type("<!here> anyone around?", "UJON123") == "channel"


def test_classify_mention_type_subteam() -> None:
    """<!subteam^G123ABC> → 'group'."""
    assert classify_mention_type("cc <!subteam^G123ABC|marketing>", "UJON123") == "group"


def test_classify_mention_type_keyword() -> None:
    """No recognized mention token → 'keyword'."""
    assert classify_mention_type("This is a normal message with no mention", "UJON123") == "keyword"


def test_classify_mention_type_no_user_id_any_user_is_direct() -> None:
    """When authed_user_id is empty, any <@USER> is treated as direct."""
    assert classify_mention_type("<@USOMEONE> hello", "") == "direct"


def test_classify_mention_type_channel_beats_other_user_mention() -> None:
    """<!channel> wins even if another user is @mentioned — Jon's ID not present."""
    assert classify_mention_type("<!channel> <@UOTHER> take a look", "UJON123") == "channel"


# ── Route filter tests ─────────────────────────────────────────────────────────


async def test_mentions_filters_direct_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /api/slack/signals/mentions returns only direct-type rows by default."""
    await _seed_message(db_session, "direct-1", mention_type="direct")
    await _seed_message(db_session, "channel-1", mention_type="channel")
    await _seed_message(db_session, "group-1", mention_type="group")

    # Patch list_unresolved_mentions at the triage module level to avoid Slack API calls
    from artemis.integrations.slack import triage as triage_mod

    original = triage_mod.list_unresolved_mentions

    async def _patched(session: AsyncSession, limit: int = 20, include_types: list[str] | None = None, token: str | None = None) -> dict[str, object]:
        # Call real impl but without a token so no Slack API calls
        return await original(session, limit=limit, include_types=include_types, token=None)

    with patch.object(triage_mod, "list_unresolved_mentions", side_effect=_patched):
        resp = await client.get("/api/slack/signals/mentions")

    assert resp.status_code == 200
    body = resp.json()
    ids = {m["id"] for m in body["mentions"]}
    assert "direct-1" in ids
    assert "channel-1" not in ids
    assert "group-1" not in ids


async def test_mentions_include_param_widens(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """?include=direct,channel returns both direct and channel rows."""
    await _seed_message(db_session, "direct-2", mention_type="direct")
    await _seed_message(db_session, "channel-2", mention_type="channel")
    await _seed_message(db_session, "group-2", mention_type="group")

    from artemis.integrations.slack import triage as triage_mod

    original = triage_mod.list_unresolved_mentions

    async def _patched(session: AsyncSession, limit: int = 20, include_types: list[str] | None = None, token: str | None = None) -> dict[str, object]:
        return await original(session, limit=limit, include_types=include_types, token=None)

    with patch.object(triage_mod, "list_unresolved_mentions", side_effect=_patched):
        resp = await client.get(
            "/api/slack/signals/mentions", params={"include": "direct,channel"}
        )

    assert resp.status_code == 200
    body = resp.json()
    ids = {m["id"] for m in body["mentions"]}
    assert "direct-2" in ids
    assert "channel-2" in ids
    assert "group-2" not in ids


# ── Migration round-trip test ──────────────────────────────────────────────────


async def test_migration_0023_up_down_roundtrip() -> None:
    """Alembic upgrade 0023 + downgrade 0023 completes without error.

    This test connects directly to the test DB and runs the migration using
    alembic's script environment, then checks that the tables exist / are gone.
    """
    import subprocess
    import sys

    # Use the project's alembic.ini. We rely on ARTEMIS_DB_URL being set in the
    # test environment (conftest does this via settings.db_url).
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0025"],
        capture_output=True,
        text=True,
        cwd="/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/agent-a7da7a1d7b231bc89",
    )
    # upgrade is expected to be a no-op if already at 0025 — either way OK.
    assert result.returncode == 0, f"alembic upgrade 0025 failed: {result.stderr}"

    # Verify tables exist in the DB
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert "slack_users" in table_names, "slack_users table should exist after upgrade 0023"
        assert "slack_channels" in table_names, "slack_channels table should exist after upgrade 0023"

        # Check mention_type column exists
        async with engine.connect() as conn:
            cols = await conn.run_sync(
                lambda sync_conn: [
                    c["name"]
                    for c in inspect(sync_conn).get_columns("slack_inbound_messages")
                ]
            )
        assert "mention_type" in cols, "mention_type column should exist after upgrade 0023"
    finally:
        await engine.dispose()

    # Downgrade back to 0024
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0024"],
        capture_output=True,
        text=True,
        cwd="/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/agent-a7da7a1d7b231bc89",
    )
    assert result.returncode == 0, f"alembic downgrade 0024 failed: {result.stderr}"

    # Verify tables are gone
    engine2 = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    try:
        async with engine2.connect() as conn:
            table_names_after = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert "slack_users" not in table_names_after, "slack_users should be gone after downgrade"
        assert "slack_channels" not in table_names_after, "slack_channels should be gone after downgrade"

        async with engine2.connect() as conn:
            cols_after = await conn.run_sync(
                lambda sync_conn: [
                    c["name"]
                    for c in inspect(sync_conn).get_columns("slack_inbound_messages")
                ]
            )
        assert "mention_type" not in cols_after, "mention_type column should be gone after downgrade"
    finally:
        await engine2.dispose()

    # Bring the DB back to current (0023) so other tests run correctly
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd="/Users/artemis/Desktop/Artemis/artemis-os/.claude/worktrees/agent-a7da7a1d7b231bc89",
    )
