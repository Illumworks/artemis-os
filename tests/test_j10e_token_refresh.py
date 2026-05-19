"""Tests for J10e proactive OAuth token refresh.

Covers:
  - Each refresher's REFRESHED / REFRESH_TOKEN_EXPIRED / TRANSIENT_FAILURE paths
  - Scheduler dispatch: skip-healthy, skip-cooldown, dispatch-correct-refresher,
    unknown-provider no-op
  - Repository persistence functions (round-trip)
  - Two back-to-back ticks make exactly one refresh HTTP call (cooldown)
  - Regression: existing granola/client.py::_ensure_fresh_token still works
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
import artemis.integrations.models  # noqa: F401 — register models on Base.metadata
from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.integrations.crypto import decrypt_credentials, encrypt_credentials
from artemis.integrations.token_refresh.base import RefreshOutcome, RefreshResult
from artemis.integrations.token_refresh.providers.gcal import GCalTokenRefresher
from artemis.integrations.token_refresh.providers.granola import GranolaTokenRefresher
from artemis.integrations.token_refresh.providers.slack import SlackTokenRefresher

pytestmark = pytest.mark.asyncio

# ── Test DB wiring (mirror tests/test_j1_integration_ui.py) ──────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text("TRUNCATE integrations RESTART IDENTITY CASCADE")


async def _refetch(session: AsyncSession, integration_id: int) -> Any:
    """Discard any cached ORM state for this id, then SELECT it fresh.

    The scheduler opens its own session via `_db.SessionLocal()` and commits
    there. The test's `db_session` is a different session whose identity-map
    cache still holds the pre-refresh row. Expiring the row forces SQLAlchemy
    to re-issue the SELECT and pick up the scheduler's writes.
    """
    from sqlalchemy import select

    from artemis.integrations.models import Integration

    result = await session.execute(select(Integration).where(Integration.id == integration_id))
    row = result.scalar_one()
    await session.refresh(row)
    return row


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


# ── Mock helpers ─────────────────────────────────────────────────────────────


class _MockResp:
    def __init__(self, status_code: int, data: Any, text_: str | None = None) -> None:
        self.status_code = status_code
        self._data = data
        self.is_success = 200 <= status_code < 300
        self.text = text_ if text_ is not None else str(data)

    def json(self) -> Any:
        return self._data


def _patch_httpx(resp: Any) -> Any:
    """Return a context manager that patches httpx.AsyncClient.post to return resp.

    `resp` may be a single response or a list of responses (returned in order).
    """
    cm = patch("httpx.AsyncClient")
    return _PatchedHTTPX(cm, resp)


class _PatchedHTTPX:
    def __init__(self, cm: Any, resp: Any) -> None:
        self._cm = cm
        self._resp = resp
        self._post: AsyncMock | None = None

    def __enter__(self) -> AsyncMock:
        mock_cls = self._cm.__enter__()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        if isinstance(self._resp, list):
            mock_ctx.post = AsyncMock(side_effect=list(self._resp))
        else:
            mock_ctx.post = AsyncMock(return_value=self._resp)
        mock_cls.return_value = mock_ctx
        self._post = mock_ctx.post
        return mock_ctx.post

    def __exit__(self, *args: Any) -> None:
        self._cm.__exit__(*args)


# ── Slice A: Refresher classes ───────────────────────────────────────────────


async def test_granola_refresher_refreshed() -> None:
    """200 response with new tokens → REFRESHED with new_creds."""
    resp = _MockResp(
        200,
        {"access_token": "new_acc", "refresh_token": "new_rt", "expires_in": 3600},
    )
    with _patch_httpx(resp):
        result = await GranolaTokenRefresher().refresh(
            {
                "access_token": "old_acc",
                "refresh_token": "old_rt",
                "client_id": "cid",
                "client_secret": "csec",
                "expires_at": time.time() + 100,
            }
        )
    assert result.outcome == RefreshOutcome.REFRESHED
    assert result.new_creds is not None
    assert result.new_creds["access_token"] == "new_acc"
    assert result.new_creds["refresh_token"] == "new_rt"
    assert float(result.new_creds["expires_at"]) > time.time() + 3500  # type: ignore[arg-type]


async def test_granola_refresher_token_expired() -> None:
    """400 invalid_grant → REFRESH_TOKEN_EXPIRED."""
    resp = _MockResp(400, {"error": "invalid_grant"}, text_='{"error":"invalid_grant"}')
    with _patch_httpx(resp):
        result = await GranolaTokenRefresher().refresh(
            {"refresh_token": "old_rt", "client_id": "cid"}
        )
    assert result.outcome == RefreshOutcome.REFRESH_TOKEN_EXPIRED
    assert result.new_creds is None


async def test_granola_refresher_transient_failure() -> None:
    """5xx → TRANSIENT_FAILURE."""
    resp = _MockResp(503, {}, text_="Service Unavailable")
    with _patch_httpx(resp):
        result = await GranolaTokenRefresher().refresh(
            {"refresh_token": "old_rt", "client_id": "cid"}
        )
    assert result.outcome == RefreshOutcome.TRANSIENT_FAILURE


async def test_granola_refresher_no_refresh_token() -> None:
    """Missing refresh_token → NO_REFRESH_TOKEN."""
    result = await GranolaTokenRefresher().refresh({"client_id": "cid"})
    assert result.outcome == RefreshOutcome.NO_REFRESH_TOKEN


async def test_gcal_refresher_refreshed() -> None:
    """Google 200 with access_token → REFRESHED."""
    resp = _MockResp(200, {"access_token": "g_new", "expires_in": 3600})
    with _patch_httpx(resp):
        result = await GCalTokenRefresher().refresh(
            {
                "access_token": "g_old",
                "refresh_token": "g_rt",
                "client_id": "gcid",
                "client_secret": "gsec",
            }
        )
    assert result.outcome == RefreshOutcome.REFRESHED
    assert result.new_creds is not None
    assert result.new_creds["access_token"] == "g_new"
    # Google rarely rotates refresh_token; ours should be carried forward.
    assert result.new_creds["refresh_token"] == "g_rt"


async def test_gcal_refresher_token_expired() -> None:
    resp = _MockResp(400, {"error": "invalid_grant"}, text_='{"error":"invalid_grant"}')
    with _patch_httpx(resp):
        result = await GCalTokenRefresher().refresh(
            {"refresh_token": "rt", "client_id": "cid", "client_secret": "csec"}
        )
    assert result.outcome == RefreshOutcome.REFRESH_TOKEN_EXPIRED


async def test_gcal_refresher_transient_failure() -> None:
    resp = _MockResp(502, {}, text_="Bad Gateway")
    with _patch_httpx(resp):
        result = await GCalTokenRefresher().refresh(
            {"refresh_token": "rt", "client_id": "cid", "client_secret": "csec"}
        )
    assert result.outcome == RefreshOutcome.TRANSIENT_FAILURE


async def test_slack_refresher_refreshed() -> None:
    """Slack 200 with ok=true and rotating tokens → REFRESHED."""
    resp = _MockResp(
        200,
        {
            "ok": True,
            "access_token": "xoxe.xoxp-new",
            "refresh_token": "xoxe-1-new",
            "expires_in": 43200,
        },
    )
    with _patch_httpx(resp):
        result = await SlackTokenRefresher().refresh(
            {
                "access_token": "xoxe.xoxp-old",
                "refresh_token": "xoxe-1-old",
                "client_id": "scid",
                "client_secret": "ssec",
            }
        )
    assert result.outcome == RefreshOutcome.REFRESHED
    assert result.new_creds is not None
    assert result.new_creds["access_token"] == "xoxe.xoxp-new"
    assert result.new_creds["refresh_token"] == "xoxe-1-new"


async def test_slack_refresher_no_refresh_token() -> None:
    """xoxb bot tokens lack refresh_token → NO_REFRESH_TOKEN."""
    result = await SlackTokenRefresher().refresh({"access_token": "xoxb-...", "token_type": "bot"})
    assert result.outcome == RefreshOutcome.NO_REFRESH_TOKEN


async def test_slack_refresher_token_expired() -> None:
    """Slack ok=false with invalid_refresh_token → REFRESH_TOKEN_EXPIRED."""
    resp = _MockResp(200, {"ok": False, "error": "invalid_refresh_token"})
    with _patch_httpx(resp):
        result = await SlackTokenRefresher().refresh({"refresh_token": "xoxe-1-rt"})
    assert result.outcome == RefreshOutcome.REFRESH_TOKEN_EXPIRED


async def test_slack_refresher_transient_failure() -> None:
    """Slack ok=false with an unknown error → TRANSIENT_FAILURE."""
    resp = _MockResp(200, {"ok": False, "error": "ratelimited"})
    with _patch_httpx(resp):
        result = await SlackTokenRefresher().refresh({"refresh_token": "xoxe-1-rt"})
    assert result.outcome == RefreshOutcome.TRANSIENT_FAILURE


# ── Slice C: Repository functions ────────────────────────────────────────────


async def test_persist_refreshed_credentials_round_trip(db_session: AsyncSession) -> None:
    """persist_refreshed_credentials re-encrypts and bumps timestamps."""
    from artemis.integrations import repository as repo

    integration = await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-1",
        encrypted_credentials=encrypt_credentials(
            {"access_token": "old", "refresh_token": "rt", "expires_at": 1.0}
        ),
        display_name="ws-1",
    )
    await db_session.commit()

    new_creds: dict[str, object] = {
        "access_token": "new",
        "refresh_token": "rt2",
        "expires_at": time.time() + 3600,
    }
    await repo.persist_refreshed_credentials(
        db_session, integration_id=integration.id, new_creds=new_creds
    )
    await db_session.commit()

    refreshed = await repo.get_by_id(db_session, integration.id)
    decoded = decrypt_credentials(bytes(refreshed.encrypted_credentials))
    assert decoded["access_token"] == "new"
    assert decoded["refresh_token"] == "rt2"
    assert refreshed.last_refresh_attempt_at is not None
    assert refreshed.last_verified_at is not None


async def test_mark_needs_reauth(db_session: AsyncSession) -> None:
    from artemis.integrations import repository as repo

    integration = await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-na",
        encrypted_credentials=encrypt_credentials({"access_token": "x"}),
    )
    await db_session.commit()

    await repo.mark_needs_reauth(db_session, integration.id)
    await db_session.commit()

    row = await repo.get_by_id(db_session, integration.id)
    assert row.status == "needs_reauth"
    assert row.last_refresh_attempt_at is not None


async def test_mark_refresh_attempted(db_session: AsyncSession) -> None:
    from artemis.integrations import repository as repo

    integration = await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-att",
        encrypted_credentials=encrypt_credentials({"access_token": "x"}),
    )
    await db_session.commit()
    assert integration.last_refresh_attempt_at is None

    await repo.mark_refresh_attempted(db_session, integration.id)
    await db_session.commit()

    row = await repo.get_by_id(db_session, integration.id)
    assert row.last_refresh_attempt_at is not None
    assert row.status == "active"  # not changed


# ── Slice B: Scheduler dispatch ──────────────────────────────────────────────


class _FakeRefresher:
    """Records calls and returns a configured result."""

    def __init__(self, provider: str, result: RefreshResult) -> None:
        self.provider = provider
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def refresh(self, creds: dict[str, object]) -> RefreshResult:
        self.calls.append(creds)
        return self.result


async def test_scheduler_skips_healthy_token(db_session: AsyncSession) -> None:
    """Token expiring >30 min from now → no refresher call."""
    from artemis.integrations import repository as repo
    from artemis.integrations.token_refresh import scheduler as sched

    integration = await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-healthy",
        encrypted_credentials=encrypt_credentials(
            {
                "access_token": "a",
                "refresh_token": "rt",
                "client_id": "cid",
                "expires_at": time.time() + 3600,  # 1 hour out — well past leeway
            }
        ),
    )
    await db_session.commit()

    fake = _FakeRefresher(
        "granola",
        RefreshResult(outcome=RefreshOutcome.REFRESHED, new_creds={"access_token": "z"}),
    )
    with patch.dict(sched.REFRESHERS, {"granola": fake}, clear=False):
        await sched.run_refresh_tick()

    assert fake.calls == []  # never dispatched
    row = await _refetch(db_session, integration.id)
    assert row.last_refresh_attempt_at is None  # healthy, untouched


async def test_scheduler_dispatches_when_expiring_soon(db_session: AsyncSession) -> None:
    """Token expiring inside leeway window → refresher called, creds persisted."""
    from artemis.integrations import repository as repo
    from artemis.integrations.token_refresh import scheduler as sched

    integration = await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-soon",
        encrypted_credentials=encrypt_credentials(
            {
                "access_token": "old",
                "refresh_token": "rt",
                "client_id": "cid",
                "expires_at": time.time() + 60,  # 1 min out — inside leeway
            }
        ),
    )
    await db_session.commit()

    new_creds: dict[str, object] = {
        "access_token": "fresh",
        "refresh_token": "rt2",
        "client_id": "cid",
        "expires_at": time.time() + 3600,
    }
    fake = _FakeRefresher(
        "granola", RefreshResult(outcome=RefreshOutcome.REFRESHED, new_creds=new_creds)
    )
    with patch.dict(sched.REFRESHERS, {"granola": fake}, clear=False):
        await sched.run_refresh_tick()

    assert len(fake.calls) == 1
    row = await _refetch(db_session, integration.id)
    decoded = decrypt_credentials(bytes(row.encrypted_credentials))
    assert decoded["access_token"] == "fresh"
    assert row.last_refresh_attempt_at is not None


async def test_scheduler_cooldown_skips_second_tick(db_session: AsyncSession) -> None:
    """Two back-to-back ticks make exactly one refresh HTTP call per integration."""
    from artemis.integrations import repository as repo
    from artemis.integrations.token_refresh import scheduler as sched

    await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-cooldown",
        encrypted_credentials=encrypt_credentials(
            {
                "access_token": "a",
                "refresh_token": "rt",
                "client_id": "cid",
                "expires_at": time.time() + 60,
            }
        ),
    )
    await db_session.commit()

    fake = _FakeRefresher(
        "granola",
        RefreshResult(
            outcome=RefreshOutcome.REFRESHED,
            new_creds={
                "access_token": "new",
                "refresh_token": "rt",
                "client_id": "cid",
                "expires_at": time.time()
                + 60,  # still inside leeway → would-refresh-again-if-not-cooldown
            },
        ),
    )
    with patch.dict(sched.REFRESHERS, {"granola": fake}, clear=False):
        await sched.run_refresh_tick()
        await sched.run_refresh_tick()

    assert len(fake.calls) == 1, (
        f"Expected exactly 1 refresh call across two ticks (cooldown), got {len(fake.calls)}"
    )


async def test_scheduler_marks_needs_reauth_on_refresh_token_expired(
    db_session: AsyncSession,
) -> None:
    """REFRESH_TOKEN_EXPIRED → status='needs_reauth', creds unchanged."""
    from artemis.integrations import repository as repo
    from artemis.integrations.token_refresh import scheduler as sched

    original_creds = {
        "access_token": "dead",
        "refresh_token": "dead_rt",
        "client_id": "cid",
        "expires_at": time.time() + 60,
    }
    integration = await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-dead",
        encrypted_credentials=encrypt_credentials(original_creds),
    )
    await db_session.commit()
    original_blob = bytes(integration.encrypted_credentials)

    fake = _FakeRefresher("granola", RefreshResult(outcome=RefreshOutcome.REFRESH_TOKEN_EXPIRED))
    with patch.dict(sched.REFRESHERS, {"granola": fake}, clear=False):
        await sched.run_refresh_tick()

    row = await _refetch(db_session, integration.id)
    assert row.status == "needs_reauth"
    # Creds blob untouched.
    assert bytes(row.encrypted_credentials) == original_blob


async def test_scheduler_handles_unknown_provider(db_session: AsyncSession) -> None:
    """Integration with no registered refresher is a no-op."""
    from artemis.integrations import repository as repo
    from artemis.integrations.token_refresh import scheduler as sched

    # Use 'jira' — present in _KNOWN_PROVIDERS but no token refresher registered.
    integration = await repo.upsert_integration(
        db_session,
        provider="jira",
        workspace_id="ws-jira",
        encrypted_credentials=encrypt_credentials(
            {"site_url": "x", "expires_at": time.time() + 60}
        ),
    )
    await db_session.commit()

    # Should not raise, should not modify the row.
    await sched.run_refresh_tick()

    row = await _refetch(db_session, integration.id)
    assert row.status == "active"
    assert row.last_refresh_attempt_at is None


async def test_scheduler_dispatches_correct_refresher_per_provider(
    db_session: AsyncSession,
) -> None:
    """Two active integrations dispatch to their respective refreshers."""
    from artemis.integrations import repository as repo
    from artemis.integrations.token_refresh import scheduler as sched

    await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-g",
        encrypted_credentials=encrypt_credentials(
            {
                "access_token": "g_a",
                "refresh_token": "g_rt",
                "client_id": "g_cid",
                "expires_at": time.time() + 60,
            }
        ),
    )
    await repo.upsert_integration(
        db_session,
        provider="gcal",
        workspace_id="ws-c",
        encrypted_credentials=encrypt_credentials(
            {
                "access_token": "c_a",
                "refresh_token": "c_rt",
                "client_id": "c_cid",
                "client_secret": "c_sec",
                "expires_at": time.time() + 60,
            }
        ),
    )
    await db_session.commit()

    granola_fake = _FakeRefresher(
        "granola",
        RefreshResult(
            outcome=RefreshOutcome.REFRESHED,
            new_creds={
                "access_token": "g_new",
                "refresh_token": "g_rt",
                "expires_at": time.time() + 3600,
            },
        ),
    )
    gcal_fake = _FakeRefresher(
        "gcal",
        RefreshResult(
            outcome=RefreshOutcome.REFRESHED,
            new_creds={
                "access_token": "c_new",
                "refresh_token": "c_rt",
                "expires_at": time.time() + 3600,
            },
        ),
    )
    with patch.dict(sched.REFRESHERS, {"granola": granola_fake, "gcal": gcal_fake}, clear=False):
        await sched.run_refresh_tick()

    assert len(granola_fake.calls) == 1
    assert granola_fake.calls[0]["access_token"] == "g_a"
    assert len(gcal_fake.calls) == 1
    assert gcal_fake.calls[0]["access_token"] == "c_a"


async def test_scheduler_transient_failure_bumps_attempt_at(db_session: AsyncSession) -> None:
    """TRANSIENT_FAILURE bumps last_refresh_attempt_at but leaves status active."""
    from artemis.integrations import repository as repo
    from artemis.integrations.token_refresh import scheduler as sched

    integration = await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-trans",
        encrypted_credentials=encrypt_credentials(
            {
                "access_token": "a",
                "refresh_token": "rt",
                "client_id": "cid",
                "expires_at": time.time() + 60,
            }
        ),
    )
    await db_session.commit()

    fake = _FakeRefresher(
        "granola",
        RefreshResult(outcome=RefreshOutcome.TRANSIENT_FAILURE, error="network blip"),
    )
    with patch.dict(sched.REFRESHERS, {"granola": fake}, clear=False):
        await sched.run_refresh_tick()

    row = await _refetch(db_session, integration.id)
    assert row.status == "active"
    assert row.last_refresh_attempt_at is not None


async def test_scheduler_start_and_stop() -> None:
    """start_token_refresh_scheduler registers job; stop_token_refresh_scheduler shuts down."""
    import artemis.integrations.token_refresh.scheduler as sched_module
    from artemis.integrations.token_refresh.scheduler import (
        get_token_refresh_scheduler,
        start_token_refresh_scheduler,
        stop_token_refresh_scheduler,
    )

    sched_module._scheduler = None

    start_token_refresh_scheduler()
    scheduler = get_token_refresh_scheduler()
    assert scheduler.running
    jobs = scheduler.get_jobs()
    assert any(j.id == "token_refresh" for j in jobs)

    stop_token_refresh_scheduler()
    assert sched_module._scheduler is None


# ── Regression: existing lazy-refresh path still works ───────────────────────


async def test_existing_granola_ensure_fresh_token_still_works() -> None:
    """granola/client.py::_ensure_fresh_token still refreshes a stale token.

    This is the backstop the brief explicitly wants preserved unchanged.
    """
    from artemis.integrations.granola.client import GranolaClient

    refresh_called: list[str] = []

    async def on_refresh(*, access_token: str, refresh_token: str, expires_at: float) -> None:
        refresh_called.append(access_token)

    refresh_resp = _MockResp(
        200,
        {"access_token": "new_tok", "refresh_token": "new_rt", "expires_in": 3600},
    )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=refresh_resp)
        mock_cls.return_value = mock_ctx

        client = GranolaClient(
            access_token="old_tok",
            refresh_token="rt",
            client_id="cid",
            expires_at=time.time() - 100,  # expired
            on_tokens_refreshed=on_refresh,
        )
        token = await client._ensure_fresh_token()

    assert token == "new_tok"
    assert refresh_called == ["new_tok"]


# ── Slice D: Manual refresh endpoint ─────────────────────────────────────────


async def test_refresh_endpoint_refreshed(db_session: AsyncSession) -> None:
    """POST /api/integrations/{id}/refresh returns outcome JSON on REFRESHED."""
    from httpx import ASGITransport, AsyncClient

    from artemis.integrations import repository as repo
    from artemis.integrations.token_refresh import providers as providers_pkg
    from artemis.main import app

    integration = await repo.upsert_integration(
        db_session,
        provider="granola",
        workspace_id="ws-ep",
        encrypted_credentials=encrypt_credentials(
            {
                "access_token": "a",
                "refresh_token": "rt",
                "client_id": "cid",
                "expires_at": time.time() + 60,
            }
        ),
    )
    await db_session.commit()

    new_expires = time.time() + 3600
    fake = _FakeRefresher(
        "granola",
        RefreshResult(
            outcome=RefreshOutcome.REFRESHED,
            new_creds={
                "access_token": "fresh",
                "refresh_token": "rt",
                "expires_at": new_expires,
            },
        ),
    )
    with patch.dict(providers_pkg.REFRESHERS, {"granola": fake}, clear=False):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(f"/api/integrations/{integration.id}/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "refreshed"
    assert body["new_expires_at"] is not None


async def test_refresh_endpoint_not_found() -> None:
    """POST /api/integrations/99999/refresh → 404."""
    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/integrations/99999/refresh")
    assert resp.status_code == 404


async def test_refresh_endpoint_no_refresher(db_session: AsyncSession) -> None:
    """Provider without a registered refresher → outcome='no_refresher'."""
    from httpx import ASGITransport, AsyncClient

    from artemis.integrations import repository as repo
    from artemis.main import app

    integration = await repo.upsert_integration(
        db_session,
        provider="jira",
        workspace_id="ws-jira-ep",
        encrypted_credentials=encrypt_credentials({"site_url": "x"}),
    )
    await db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(f"/api/integrations/{integration.id}/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "no_refresher"
    assert body["new_expires_at"] is None
