"""Tests for the Slack Events API receiver (J1 sub-agent 2).

Covers:
  - url_verification challenge round-trip
  - HMAC signature rejection (bad sig, stale timestamp)
  - app_mention deduplication
  - app_mention happy path
  - im message routing
  - unknown event types are silently ignored
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


# ── Test client factory ───────────────────────────────────────────────────────


async def _make_client() -> AsyncClient:
    from artemis.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Signing helper ────────────────────────────────────────────────────────────


def _make_signed_request(
    body_dict: dict[str, Any],
    secret: str = "test-secret",
    ts_offset: float = 0.0,
) -> tuple[bytes, dict[str, str]]:
    """Return (body_bytes, headers) with a valid Slack HMAC-SHA256 signature.

    ``ts_offset`` lets tests produce stale timestamps (pass e.g. -400 for 400 s ago).
    """
    body_bytes = json.dumps(body_dict).encode()
    timestamp = str(int(time.time()) + int(ts_offset))
    base = f"v0:{timestamp}:{body_bytes.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": sig,
        "Content-Type": "application/json",
    }
    return body_bytes, headers


# ── url_verification ──────────────────────────────────────────────────────────


async def test_url_verification_returns_challenge() -> None:
    """Slack URL verification: respond with the challenge value, no HMAC required."""
    payload = {"type": "url_verification", "challenge": "test-challenge"}

    async with await _make_client() as client:
        resp = await client.post(
            "/api/integrations/slack/events",
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"challenge": "test-challenge"}


# ── HMAC verification ─────────────────────────────────────────────────────────


async def test_signature_mismatch_returns_401() -> None:
    """Requests with a wrong HMAC signature must be rejected with 401."""
    payload = {
        "type": "event_callback",
        "event_id": "Ev001",
        "team_id": "T001",
        "event": {"type": "app_mention", "channel": "C001", "user": "U001", "ts": "1.0"},
    }
    body_bytes = json.dumps(payload).encode()
    timestamp = str(int(time.time()))

    with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "real-secret"}):
        async with await _make_client() as client:
            resp = await client.post(
                "/api/integrations/slack/events",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Slack-Request-Timestamp": timestamp,
                    "X-Slack-Signature": "v0=badsignature",
                },
            )

    assert resp.status_code == 401


async def test_replay_attack_returns_401() -> None:
    """Requests with a timestamp more than 5 minutes old must be rejected."""
    payload = {
        "type": "event_callback",
        "event_id": "Ev002",
        "team_id": "T001",
        "event": {"type": "app_mention", "channel": "C001", "user": "U001", "ts": "2.0"},
    }
    secret = "test-secret"
    # Produce a valid sig but for a stale timestamp (>300 s in the past)
    body_bytes, headers = _make_signed_request(payload, secret=secret, ts_offset=-400.0)

    with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}):
        async with await _make_client() as client:
            resp = await client.post(
                "/api/integrations/slack/events",
                content=body_bytes,
                headers=headers,
            )

    assert resp.status_code == 401


# ── app_mention deduplication ─────────────────────────────────────────────────


async def test_app_mention_event_deduped() -> None:
    """Second POST with the same event_id must not invoke route_inbound."""
    payload = {
        "type": "event_callback",
        "event_id": "Ev-dedup",
        "team_id": "T001",
        "event": {
            "type": "app_mention",
            "channel": "C001",
            "user": "U001",
            "text": "<@UBOT> hello",
            "ts": "3.0",
        },
    }
    secret = "test-secret"
    body_bytes, headers = _make_signed_request(payload, secret=secret)

    from artemis.db import get_session
    from artemis.main import app

    mock_db_session = AsyncMock()

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield mock_db_session

    app.dependency_overrides[get_session] = _override_session

    try:
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}):
            # First call — upsert returns True (newly inserted)
            with (
                patch(
                    "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                    new_callable=AsyncMock,
                    return_value=True,
                ) as mock_upsert,
                patch(
                    "artemis.routes.integrations_slack_events.route_inbound",
                    new_callable=AsyncMock,
                ),
            ):
                async with await _make_client() as client:
                    resp1 = await client.post(
                        "/api/integrations/slack/events",
                        content=body_bytes,
                        headers=headers,
                    )
                assert resp1.status_code == 200
                # route_inbound is scheduled via background task / ensure_future;
                # verify upsert was called
                mock_upsert.assert_awaited_once()

            # Second call — upsert returns False (duplicate)
            body_bytes2, headers2 = _make_signed_request(payload, secret=secret)
            with (
                patch(
                    "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                    new_callable=AsyncMock,
                    return_value=False,
                ),
                patch(
                    "artemis.routes.integrations_slack_events.route_inbound",
                    new_callable=AsyncMock,
                ) as mock_route2,
            ):
                async with await _make_client() as client:
                    resp2 = await client.post(
                        "/api/integrations/slack/events",
                        content=body_bytes2,
                        headers=headers2,
                    )
                assert resp2.status_code == 200
                mock_route2.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_session, None)


# ── app_mention happy path ────────────────────────────────────────────────────


async def test_app_mention_event_happy_path() -> None:
    """Valid signed app_mention event_callback returns 200."""
    payload = {
        "type": "event_callback",
        "event_id": "Ev-happy",
        "team_id": "T001",
        "event": {
            "type": "app_mention",
            "channel": "C001",
            "user": "U001",
            "text": "<@UBOT> do a thing",
            "ts": "4.0",
        },
    }
    secret = "test-secret"
    body_bytes, headers = _make_signed_request(payload, secret=secret)

    from artemis.db import get_session
    from artemis.main import app

    mock_db_session = AsyncMock()

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield mock_db_session

    app.dependency_overrides[get_session] = _override_session

    try:
        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}),
            patch(
                "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "artemis.routes.integrations_slack_events.route_inbound",
                new_callable=AsyncMock,
            ),
        ):
            async with await _make_client() as client:
                resp = await client.post(
                    "/api/integrations/slack/events",
                    content=body_bytes,
                    headers=headers,
                )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    assert resp.json().get("ok") is True


# ── im message routing ────────────────────────────────────────────────────────


async def test_im_message_event_routed() -> None:
    """Valid signed im message event_callback returns 200."""
    payload = {
        "type": "event_callback",
        "event_id": "Ev-im",
        "team_id": "T001",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D001",
            "user": "U001",
            "text": "hey artemis",
            "ts": "5.0",
        },
    }
    secret = "test-secret"
    body_bytes, headers = _make_signed_request(payload, secret=secret)

    from artemis.db import get_session
    from artemis.main import app

    mock_db_session = AsyncMock()

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield mock_db_session

    app.dependency_overrides[get_session] = _override_session

    try:
        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}),
            patch(
                "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "artemis.routes.integrations_slack_events.route_inbound",
                new_callable=AsyncMock,
            ),
        ):
            async with await _make_client() as client:
                resp = await client.post(
                    "/api/integrations/slack/events",
                    content=body_bytes,
                    headers=headers,
                )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    assert resp.json().get("ok") is True


# ── unknown event type ────────────────────────────────────────────────────────


async def test_unknown_event_type_ignored() -> None:
    """An unrecognised inner event type is silently ignored with 200."""
    payload = {
        "type": "event_callback",
        "event_id": "Ev-unk",
        "team_id": "T001",
        "event": {
            "type": "reaction_added",
            "channel": "C001",
            "user": "U001",
            "ts": "6.0",
        },
    }
    secret = "test-secret"
    body_bytes, headers = _make_signed_request(payload, secret=secret)

    from artemis.db import get_session
    from artemis.main import app

    mock_db_session = AsyncMock()

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield mock_db_session

    app.dependency_overrides[get_session] = _override_session

    try:
        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}),
            patch(
                "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                new_callable=AsyncMock,
            ) as mock_upsert,
        ):
            async with await _make_client() as client:
                resp = await client.post(
                    "/api/integrations/slack/events",
                    content=body_bytes,
                    headers=headers,
                )
            mock_upsert.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
