"""Tests for J9 — Slack triage: mention queue + resolution endpoints.

Covers:
  1. test_list_mentions_empty — no rows → empty list, total 0.
  2. test_list_mentions_returns_unresolved_only — resolved rows excluded.
  3. test_list_mentions_limit — at most 20 by default, respects limit param.
  4. test_resolve_marks_row_resolved — POST sets resolved_at, returns new_total.
  5. test_resolve_idempotent — POSTing resolve twice returns no error.
  6. test_resolve_404_on_unknown_id — unknown event_id returns 404.
  7. test_signals_missedmentions_excludes_resolved — J8 count filters resolved_at IS NULL.
  8. test_triage_permalink_format — permalink uses workspace-direct archives format.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.integrations.models  # noqa: F401 — register ORM models
from artemis.config import settings
from artemis.db import attach_pgvector_codec

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


async def _seed_integration(session: AsyncSession) -> None:
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import encrypt_credentials

    creds = encrypt_credentials({"access_token": "xoxb-test", "token_type": "bot"})
    await repo.upsert_integration(
        session,
        provider="slack",
        workspace_id="T001",
        encrypted_credentials=creds,
        display_name="Test",
        bot_user_id="UBOT",
        scopes=["app_mentions:read"],
    )
    await session.commit()


async def _seed_message(
    session: AsyncSession,
    event_id: str,
    *,
    resolved: bool = False,
    received_offset_h: int = 1,
    ts: str | None = None,
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from artemis.integrations.models import SlackInboundMessage

    now = datetime.now(UTC)
    resolved_at = now if resolved else None
    msg_ts = ts or str(time.time())

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
            received_at=now - timedelta(hours=received_offset_h),
            resolved_at=resolved_at,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    await session.execute(stmt)
    await session.commit()


def _reset_signals_cache() -> None:
    import artemis.integrations.slack.signals as sig_mod

    sig_mod._cache = None
    sig_mod._cache_ts = 0.0


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_list_mentions_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    """No rows → 200 with empty list and total_unresolved 0."""
    resp = await client.get("/api/slack/signals/mentions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mentions"] == []
    assert body["total_unresolved"] == 0


async def test_list_mentions_returns_unresolved_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Resolved rows must not appear in the list; total_unresolved reflects only unresolved."""
    await _seed_message(db_session, "Ev-open-1", resolved=False)
    await _seed_message(db_session, "Ev-open-2", resolved=False)
    await _seed_message(db_session, "Ev-resolved-1", resolved=True)

    resp = await client.get("/api/slack/signals/mentions")
    assert resp.status_code == 200
    body = resp.json()

    ids = {m["id"] for m in body["mentions"]}
    assert "Ev-open-1" in ids
    assert "Ev-open-2" in ids
    assert "Ev-resolved-1" not in ids
    assert body["total_unresolved"] == 2


async def test_list_mentions_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    """limit param is respected."""
    for i in range(5):
        await _seed_message(db_session, f"Ev-limit-{i}", resolved=False)

    resp = await client.get("/api/slack/signals/mentions", params={"limit": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["mentions"]) == 3
    # total_unresolved is the full count, not limited
    assert body["total_unresolved"] == 5


async def test_resolve_marks_row_resolved(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /resolve sets resolved_at; returns ok=True and new_total_unresolved."""
    await _seed_message(db_session, "Ev-to-resolve", resolved=False)
    await _seed_message(db_session, "Ev-other", resolved=False)

    resp = await client.post("/api/slack/signals/mentions/Ev-to-resolve/resolve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["new_total_unresolved"] == 1  # one remaining

    # Confirm persistence: re-fetch the list
    list_resp = await client.get("/api/slack/signals/mentions")
    assert list_resp.status_code == 200
    ids = {m["id"] for m in list_resp.json()["mentions"]}
    assert "Ev-to-resolve" not in ids
    assert "Ev-other" in ids


async def test_resolve_idempotent(client: AsyncClient, db_session: AsyncSession) -> None:
    """Resolving an already-resolved row must not error and must return a valid total."""
    await _seed_message(db_session, "Ev-idempotent", resolved=False)

    resp1 = await client.post("/api/slack/signals/mentions/Ev-idempotent/resolve")
    assert resp1.status_code == 200
    assert resp1.json()["new_total_unresolved"] == 0

    # Second resolve — should still be 200, count doesn't go negative
    resp2 = await client.post("/api/slack/signals/mentions/Ev-idempotent/resolve")
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["ok"] is True
    assert body2["new_total_unresolved"] == 0


async def test_resolve_404_on_unknown_id(client: AsyncClient, db_session: AsyncSession) -> None:
    """Unknown event_id returns 404."""
    resp = await client.post("/api/slack/signals/mentions/no-such-event/resolve")
    assert resp.status_code == 404


async def test_signals_missedmentions_excludes_resolved(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """J8 GET /api/slack/signals missedMentions must not count resolved rows."""
    _reset_signals_cache()
    await _seed_integration(db_session)

    # Two unresolved
    await _seed_message(db_session, "Ev-sig-open-1", resolved=False)
    await _seed_message(db_session, "Ev-sig-open-2", resolved=False)
    # One resolved — must NOT be counted
    await _seed_message(db_session, "Ev-sig-resolved-1", resolved=True)

    with patch(
        "artemis.integrations.slack.client.SlackClient._post",
        new_callable=AsyncMock,
        return_value={"ok": True, "channels": []},
    ):
        resp = await client.get("/api/slack/signals", params={"refresh": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["missedMentions"] == 2


async def test_triage_permalink_format(db_session: AsyncSession) -> None:
    """Permalink uses workspace-direct archives/<channel>/p<ts_nodot> format."""
    from artemis.integrations.slack.triage import _make_permalink

    link = _make_permalink("C123ABC", "1716000000.123456")
    assert link == "https://amiralearning.slack.com/archives/C123ABC/p1716000000123456"
