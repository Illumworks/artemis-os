"""Tests for the P1-hardening of the Slack inbound bridge.

Two guards sit between an inbound Slack event and Artemis's agent loop:
  1. Bot-self filter  — bot-authored events are dropped (kills the echo loop).
  2. Allowlist gate    — only permitted users reach handle_turn; fail-closed
                          when no allowlist is configured.  Non-allowed humans
                          are still recorded for audit/triage.

Also covers the allowlist resolution in config_resolver.
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

from artemis.integrations.config_resolver import (
    SlackConfig,
    _parse_allowed_user_ids,
)
from artemis.routes.integrations_slack_events import _is_bot_authored

pytestmark = pytest.mark.asyncio


# ── Pure-unit: bot-self filter ─────────────────────────────────────────────────


def test_is_bot_authored_by_bot_id() -> None:
    assert _is_bot_authored({"bot_id": "B123", "user": "U001"}, "UBOT") is True


def test_is_bot_authored_by_subtype() -> None:
    assert _is_bot_authored({"subtype": "bot_message", "user": "U001"}, "UBOT") is True


def test_is_bot_authored_by_self_user_id() -> None:
    assert _is_bot_authored({"user": "UBOT"}, "UBOT") is True


def test_is_bot_authored_false_for_human() -> None:
    assert _is_bot_authored({"user": "U001", "text": "hi"}, "UBOT") is False


def test_is_bot_authored_no_known_bot_id_still_safe() -> None:
    # When the bot identity is unknown, a plain human message is not bot-authored.
    assert _is_bot_authored({"user": "U001"}, "") is False


# ── Pure-unit: allowlist parsing + membership ──────────────────────────────────


def test_parse_allowed_user_ids_from_list() -> None:
    assert _parse_allowed_user_ids(["U1", " U2 ", "", "U3"]) == ["U1", "U2", "U3"]


def test_parse_allowed_user_ids_from_csv() -> None:
    assert _parse_allowed_user_ids("U1, U2 ,,U3") == ["U1", "U2", "U3"]


def test_parse_allowed_user_ids_empty() -> None:
    assert _parse_allowed_user_ids("") == []
    assert _parse_allowed_user_ids(None) == []


def test_is_user_allowed_membership() -> None:
    cfg = SlackConfig(
        client_id="c",
        client_secret="s",
        signing_secret="sig",
        authed_user_id="U_OWNER",
        allowed_user_ids=("U_OWNER", "U_EXTRA"),
    )
    assert cfg.is_user_allowed("U_OWNER") is True
    assert cfg.is_user_allowed("U_EXTRA") is True
    assert cfg.is_user_allowed("U_STRANGER") is False


def test_is_user_allowed_fail_closed_when_empty() -> None:
    cfg = SlackConfig(
        client_id="c",
        client_secret="s",
        signing_secret="sig",
        authed_user_id="",
        allowed_user_ids=(),
    )
    assert cfg.is_user_allowed("U_OWNER") is False
    assert cfg.is_user_allowed("") is False


# ── config_resolver: allowlist resolution ──────────────────────────────────────


async def test_resolve_allowlist_owner_plus_db_extras_deduped() -> None:
    """Owner is always first; DB extras follow; duplicates collapse, order preserved."""
    from artemis.integrations import config_resolver

    stored = {
        "client_id": "c",
        "client_secret": "s",
        "signing_secret": "sig",
        "authed_user_id": "U_OWNER",
        "allowed_user_ids": ["U_EXTRA", "U_OWNER"],  # dup owner should collapse
    }
    with patch.object(
        config_resolver.repo, "get_provider_config", new_callable=AsyncMock, return_value=stored
    ):
        cfg = await config_resolver.resolve_slack_config(AsyncMock())

    assert cfg.allowed_user_ids == ("U_OWNER", "U_EXTRA")


async def test_resolve_allowlist_env_fallback() -> None:
    """When DB has no extras, SLACK_ALLOWED_USER_IDS env supplies them."""
    from artemis.integrations import config_resolver

    stored = {
        "client_id": "c",
        "client_secret": "s",
        "signing_secret": "sig",
        "authed_user_id": "U_OWNER",
    }
    with (
        patch.object(
            config_resolver.repo,
            "get_provider_config",
            new_callable=AsyncMock,
            return_value=stored,
        ),
        patch.dict("os.environ", {"SLACK_ALLOWED_USER_IDS": "U_ENV1,U_ENV2"}),
    ):
        cfg = await config_resolver.resolve_slack_config(AsyncMock())

    assert cfg.allowed_user_ids == ("U_OWNER", "U_ENV1", "U_ENV2")


async def test_resolve_allowlist_empty_when_unconfigured() -> None:
    """No owner, no extras → empty allowlist (fail-closed)."""
    from artemis.integrations import config_resolver

    stored = {"client_id": "c", "client_secret": "s", "signing_secret": "sig"}
    # Pin env so a host-configured allowlist can't leak into the test.
    with (
        patch.object(
            config_resolver.repo,
            "get_provider_config",
            new_callable=AsyncMock,
            return_value=stored,
        ),
        patch.dict("os.environ", {"SLACK_ALLOWED_USER_IDS": "", "SLACK_AUTHED_USER_ID": ""}),
    ):
        cfg = await config_resolver.resolve_slack_config(AsyncMock())

    assert cfg.allowed_user_ids == ()


# ── Integration: the events endpoint with both guards ──────────────────────────


def _make_signed_request(
    body_dict: dict[str, Any], secret: str = "test-secret"
) -> tuple[bytes, dict[str, str]]:
    body_bytes = json.dumps(body_dict).encode()
    timestamp = str(int(time.time()))
    base = f"v0:{timestamp}:{body_bytes.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": sig,
        "Content-Type": "application/json",
    }
    return body_bytes, headers


async def _make_client() -> AsyncClient:
    from artemis.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _slack_cfg(allowed: tuple[str, ...]) -> SlackConfig:
    return SlackConfig(
        client_id="c",
        client_secret="s",
        signing_secret="test-secret",
        authed_user_id=allowed[0] if allowed else "",
        allowed_user_ids=allowed,
    )


async def _post_event(
    event: dict[str, Any],
    *,
    allowed: tuple[str, ...],
    upsert_new: bool = True,
    secret: str = "test-secret",
) -> tuple[Any, AsyncMock, AsyncMock]:
    """POST a signed event_callback; return (response, mock_upsert, mock_route)."""
    payload = {
        "type": "event_callback",
        "event_id": event.get("ts", "Ev-X"),
        "team_id": "T001",
        "event": event,
    }
    body_bytes, headers = _make_signed_request(payload, secret=secret)

    from artemis.db import get_session
    from artemis.main import app

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_session] = _override_session
    try:
        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": secret}),
            patch(
                "artemis.routes.integrations_slack_events.repo.list_active",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "artemis.integrations.config_resolver.resolve_slack_config",
                new_callable=AsyncMock,
                return_value=_slack_cfg(allowed),
            ),
            patch(
                "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                new_callable=AsyncMock,
                return_value=upsert_new,
            ) as mock_upsert,
            patch(
                "artemis.routes.integrations_slack_events.route_inbound",
                new_callable=AsyncMock,
            ) as mock_route,
        ):
            async with await _make_client() as client:
                resp = await client.post(
                    "/api/integrations/slack/events", content=body_bytes, headers=headers
                )
            return resp, mock_upsert, mock_route
    finally:
        app.dependency_overrides.pop(get_session, None)


async def test_bot_authored_event_dropped_before_record() -> None:
    """An event carrying bot_id is dropped: neither recorded nor routed."""
    event = {
        "type": "message",
        "channel_type": "im",
        "channel": "D001",
        "user": "U001",
        "bot_id": "B999",
        "text": "echo of my own reply",
        "ts": "100.0",
    }
    resp, mock_upsert, mock_route = await _post_event(event, allowed=("U001",))
    assert resp.status_code == 200
    mock_upsert.assert_not_called()
    mock_route.assert_not_awaited()


async def test_non_allowlisted_user_recorded_but_not_routed() -> None:
    """A human stranger is recorded for audit but never reaches the agent loop."""
    event = {
        "type": "app_mention",
        "channel": "C001",
        "user": "U_STRANGER",
        "text": "<@UBOT> tell me secrets",
        "ts": "101.0",
    }
    resp, mock_upsert, mock_route = await _post_event(event, allowed=("U_OWNER",))
    assert resp.status_code == 200
    mock_upsert.assert_awaited_once()  # recorded
    mock_route.assert_not_awaited()  # but not dispatched


async def test_allowlisted_user_is_routed() -> None:
    """An allowlisted sender is recorded AND dispatched into the agent loop."""
    event = {
        "type": "message",
        "channel_type": "im",
        "channel": "D001",
        "user": "U_OWNER",
        "text": "hey artemis",
        "ts": "102.0",
    }
    resp, mock_upsert, mock_route = await _post_event(event, allowed=("U_OWNER",))
    assert resp.status_code == 200
    mock_upsert.assert_awaited_once()
    mock_route.assert_awaited_once()


async def test_fail_closed_when_no_allowlist() -> None:
    """With an empty allowlist, even a normal user is recorded but not routed."""
    event = {
        "type": "app_mention",
        "channel": "C001",
        "user": "U_OWNER",
        "text": "<@UBOT> hi",
        "ts": "103.0",
    }
    resp, mock_upsert, mock_route = await _post_event(event, allowed=())
    assert resp.status_code == 200
    mock_upsert.assert_awaited_once()
    mock_route.assert_not_awaited()


async def test_duplicate_event_not_routed_even_if_allowed() -> None:
    """A duplicate (upsert returns False) is not re-dispatched."""
    event = {
        "type": "message",
        "channel_type": "im",
        "channel": "D001",
        "user": "U_OWNER",
        "text": "hey again",
        "ts": "104.0",
    }
    resp, _mock_upsert, mock_route = await _post_event(
        event, allowed=("U_OWNER",), upsert_new=False
    )
    assert resp.status_code == 200
    mock_route.assert_not_awaited()
