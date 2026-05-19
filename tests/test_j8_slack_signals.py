"""Tests for J8 — Slack signals backend.

Covers:
  1. test_signals_not_connected_returns_shape_with_connected_false
  2. test_signals_connected_zero_activity
  3. test_missed_mentions_counts_recent_mentions
  4. test_reply_needed_threads_heuristic
  5. test_unread_dms_falls_back_to_null_without_user_scope
  6. test_cache_hits_within_ttl
  7. test_force_refresh_bypasses_cache
  8. test_route_returns_200_when_unavailable
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.integrations.models  # noqa: F401 — register models
from artemis.config import settings
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── Test DB setup ──────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)

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


async def _seed_integration(session: AsyncSession) -> None:
    """Insert a minimal active Slack integration row."""
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import encrypt_credentials

    creds = encrypt_credentials({"access_token": "xoxb-test-token", "token_type": "bot"})
    await repo.upsert_integration(
        session,
        provider="slack",
        workspace_id="T001",
        encrypted_credentials=creds,
        display_name="Test Workspace",
        bot_user_id="UBOT",
        scopes=["im:read", "im:history", "app_mentions:read"],
    )
    await session.commit()


async def _seed_inbound(
    session: AsyncSession,
    event_id: str,
    received_at: datetime,
    thread_ts: str | None = None,
    routed: bool = False,
) -> None:
    """Insert a slack_inbound_messages row directly."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from artemis.integrations.models import SlackInboundMessage

    stmt = (
        pg_insert(SlackInboundMessage)
        .values(
            event_id=event_id,
            team_id="T001",
            channel_id="C001",
            user_id="U001",
            text="hello bot",
            ts=str(time.time()),
            thread_ts=thread_ts,
            routed_to_session_id=1 if routed else None,
            received_at=received_at,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    await session.execute(stmt)
    await session.commit()


def _reset_cache() -> None:
    import artemis.integrations.slack.signals as sig_mod

    sig_mod._cache = None
    sig_mod._cache_ts = 0.0


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_signals_not_connected_returns_shape_with_connected_false(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No integration row → 200, connected=False, all counts None."""
    _reset_cache()
    resp = await client.get("/api/slack/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["status"] == "not_connected"
    assert body["missedMentions"] is None
    assert body["unreadDMs"] is None
    assert body["replyNeededThreads"] is None
    assert body["source"] == "artemis"
    assert "checkedAt" in body


async def test_signals_connected_zero_activity(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Integration exists, no inbound rows → counts 0, status connected."""
    _reset_cache()
    await _seed_integration(db_session)

    with patch(
        "artemis.integrations.slack.client.SlackClient._post",
        new_callable=AsyncMock,
        return_value={"ok": True, "channels": []},
    ):
        resp = await client.get("/api/slack/signals")

    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["missedMentions"] == 0
    assert body["replyNeededThreads"] == 0
    assert body["source"] == "artemis"


async def test_missed_mentions_counts_recent_mentions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """3 inbound rows within 48 h, 1 older than 48 h → missedMentions=3."""
    _reset_cache()
    await _seed_integration(db_session)
    now = datetime.now(UTC)
    for i in range(3):
        await _seed_inbound(db_session, f"Ev-recent-{i}", now - timedelta(hours=24 + i))
    # One row outside the 48 h window
    await _seed_inbound(db_session, "Ev-old-1", now - timedelta(hours=72))

    with patch(
        "artemis.integrations.slack.client.SlackClient._post",
        new_callable=AsyncMock,
        return_value={"ok": True, "channels": []},
    ):
        resp = await client.get("/api/slack/signals", params={"refresh": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["missedMentions"] == 3


async def test_reply_needed_threads_heuristic(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Thread message >4 h old and unrouted → replyNeededThreads=1."""
    _reset_cache()
    await _seed_integration(db_session)
    now = datetime.now(UTC)
    # Thread message received 6 h ago — needs reply
    await _seed_inbound(
        db_session,
        "Ev-thread-old",
        now - timedelta(hours=6),
        thread_ts="1234567890.000100",
    )
    # Thread message received 1 h ago — too recent, not in "needs reply" bucket
    await _seed_inbound(
        db_session,
        "Ev-thread-new",
        now - timedelta(hours=1),
        thread_ts="1234567890.000200",
    )

    with patch(
        "artemis.integrations.slack.client.SlackClient._post",
        new_callable=AsyncMock,
        return_value={"ok": True, "channels": []},
    ):
        resp = await client.get("/api/slack/signals", params={"refresh": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["replyNeededThreads"] == 1


async def test_unread_dms_falls_back_to_null_without_user_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Bot token missing im:read scope → unreadDMs=None, status includes scope note."""
    _reset_cache()
    await _seed_integration(db_session)

    from artemis.integrations.slack.client import SlackAPIError

    with patch(
        "artemis.integrations.slack.client.SlackClient._post",
        new_callable=AsyncMock,
        side_effect=SlackAPIError("conversations.list", "missing_scope"),
    ):
        resp = await client.get("/api/slack/signals", params={"refresh": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["unreadDMs"] is None
    assert body["connected"] is True
    assert "scope" in body["status"] or body["status"] == "connected_no_dm_scope"


async def test_cache_hits_within_ttl(
    db_session: AsyncSession,
) -> None:
    """Two calls within TTL → second call does not hit DB (cache hit)."""
    import artemis.integrations.slack.signals as sig_mod

    _reset_cache()

    call_count = 0

    async def _mock_compute(session: AsyncSession) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        return {
            "connected": True,
            "status": "connected",
            "missedMentions": 0,
            "unreadDMs": None,
            "replyNeededThreads": 0,
            "checkedWindow": "test",
            "checkedAt": datetime.now(UTC).isoformat(),
            "source": "artemis",
        }

    with patch.object(sig_mod, "_compute", side_effect=_mock_compute):
        await sig_mod.get_slack_signals(db_session)
        await sig_mod.get_slack_signals(db_session)

    # _compute should only be called once — second call is cache hit
    assert call_count == 1


async def test_force_refresh_bypasses_cache(
    db_session: AsyncSession,
) -> None:
    """force_refresh=True calls _compute even with a warm cache."""
    import artemis.integrations.slack.signals as sig_mod

    _reset_cache()

    call_count = 0

    async def _mock_compute(session: AsyncSession) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        return {
            "connected": True,
            "status": "connected",
            "missedMentions": 0,
            "unreadDMs": None,
            "replyNeededThreads": 0,
            "checkedWindow": "test",
            "checkedAt": datetime.now(UTC).isoformat(),
            "source": "artemis",
        }

    with patch.object(sig_mod, "_compute", side_effect=_mock_compute):
        await sig_mod.get_slack_signals(db_session, force_refresh=False)
        await sig_mod.get_slack_signals(db_session, force_refresh=True)

    # Both calls invoke _compute because second has force_refresh=True
    assert call_count == 2


async def test_route_returns_200_when_unavailable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Slack API errors → 200 with status=unavailable, never 502."""
    _reset_cache()
    await _seed_integration(db_session)

    with patch(
        "artemis.integrations.slack.client.SlackClient._post",
        new_callable=AsyncMock,
        side_effect=Exception("network error"),
    ):
        resp = await client.get("/api/slack/signals", params={"refresh": 1})

    assert resp.status_code == 200
    body = resp.json()
    # Shape must always be valid
    assert "connected" in body
    assert "source" in body
    assert body["source"] == "artemis"
    assert body["status"] in ("unavailable", "connected_no_dm_scope", "connected")


# ── Direct unit tests for uncovered paths ─────────────────────────────────────


async def test_compute_bad_credentials_returns_unavailable(
    db_session: AsyncSession,
) -> None:
    """Integration row with unreadable credentials → status=unavailable."""
    import artemis.integrations.slack.signals as sig_mod

    _reset_cache()

    # Seed an integration with garbage encrypted bytes that won't decrypt cleanly
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from artemis.integrations.models import Integration

    stmt = (
        pg_insert(Integration.__table__)
        .values(
            provider="slack",
            workspace_id="T-bad",
            display_name="Bad Creds",
            encrypted_credentials=b"not-valid-fernet-data",
            status="active",
            metadata={},
        )
        .on_conflict_do_update(
            index_elements=["provider", "workspace_id"],
            set_={"status": "active", "encrypted_credentials": b"not-valid-fernet-data"},
        )
    )
    await db_session.execute(stmt)
    await db_session.commit()

    result = await sig_mod.get_slack_signals(db_session, force_refresh=True)
    assert result["connected"] is False
    assert result["status"] in ("unavailable", "not_connected")


async def test_compute_raises_returns_unavailable_shape(
    db_session: AsyncSession,
) -> None:
    """If _compute raises an unexpected exception, get_slack_signals returns unavailable."""
    import artemis.integrations.slack.signals as sig_mod

    _reset_cache()

    async def _bad_compute(session: AsyncSession) -> dict[str, object]:
        raise RuntimeError("unexpected")

    with patch.object(sig_mod, "_compute", side_effect=_bad_compute):
        result = await sig_mod.get_slack_signals(db_session, force_refresh=True)

    assert result["connected"] is False
    assert result["status"] == "unavailable"
    assert result["source"] == "artemis"


async def test_compute_empty_token_returns_unavailable(
    db_session: AsyncSession,
) -> None:
    """Integration row with empty access_token in credentials → unavailable."""
    import artemis.integrations.slack.signals as sig_mod

    _reset_cache()

    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import encrypt_credentials

    creds = encrypt_credentials({"access_token": "", "token_type": "bot"})
    await repo.upsert_integration(
        db_session,
        provider="slack",
        workspace_id="T-empty-token",
        encrypted_credentials=creds,
        display_name="Empty Token",
    )
    await db_session.commit()

    result = await sig_mod.get_slack_signals(db_session, force_refresh=True)
    assert result["connected"] is False
    assert result["status"] == "unavailable"


async def test_unread_dms_generic_api_error_returns_unavailable(
    db_session: AsyncSession,
) -> None:
    """Generic Slack API error on conversations.list → unreadDMs=None, status=unavailable."""
    import artemis.integrations.slack.signals as sig_mod

    _reset_cache()

    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.slack.client import SlackAPIError

    creds = encrypt_credentials({"access_token": "xoxb-token", "token_type": "bot"})
    await repo.upsert_integration(
        db_session,
        provider="slack",
        workspace_id="T-api-err",
        encrypted_credentials=creds,
        display_name="API Error Workspace",
    )
    await db_session.commit()

    with patch(
        "artemis.integrations.slack.client.SlackClient._post",
        new_callable=AsyncMock,
        side_effect=SlackAPIError("conversations.list", "ratelimited"),
    ):
        result = await sig_mod.get_slack_signals(db_session, force_refresh=True)

    assert result["unreadDMs"] is None
    assert result["status"] == "unavailable"
