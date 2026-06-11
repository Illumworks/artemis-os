from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from artemis.routes.integrations_slack_events import _SlackAgentConfig, route_inbound

pytestmark = pytest.mark.asyncio


def _signed_headers(body: bytes, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    base = f"v0:{timestamp}:{body.decode()}"
    digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"v0={digest}",
    }


def _callie_cfg(
    *,
    channel_ids: tuple[str, ...] = ("C0B9CHVC7KQ", "C_MARKETING"),
) -> _SlackAgentConfig:
    return _SlackAgentConfig(
        agent_id="callie",
        signing_secret="callie-signing-secret",
        access_token="xoxb-callie",
        bot_user_id="U0B9S32PTAM",
        authed_user_id="",
        allowed_user_ids=(),
        allowed_channel_ids=channel_ids,
        listen_channel_messages=True,
    )


async def _make_client() -> AsyncClient:
    from artemis.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_route_inbound_uses_agent_specific_session_key_and_reply_token() -> None:
    first_session = AsyncMock()
    second_session = AsyncMock()
    slack_client = AsyncMock()
    turn_result = SimpleNamespace(response_text="Here is the angle.")

    class _SessionContext:
        def __init__(self, session: AsyncMock) -> None:
            self._session = session

        async def __aenter__(self) -> AsyncMock:
            return self._session

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _SessionFactory:
        def __init__(self, *sessions: AsyncMock) -> None:
            self._sessions = list(sessions)

        def __call__(self) -> _SessionContext:
            return _SessionContext(self._sessions.pop(0))

    with (
        patch("artemis.db.SessionLocal", new=_SessionFactory(first_session, second_session)),
        patch(
            "artemis.routes.integrations_slack_events.repo.get_slack_user",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(session_id="slack-callie-T1-C0B9CHVC7KQ-_"),
        ),
        patch(
            "artemis.floating_artemis.chat.handle_turn",
            new_callable=AsyncMock,
            return_value=turn_result,
        ) as mock_handle_turn,
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=_callie_cfg(),
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=slack_client),
    ):
        await route_inbound(
            {
                "team_id": "T1",
                "channel": "C0B9CHVC7KQ",
                "text": "What do you recommend?",
                "user": "U_TEAMMATE",
            },
            agent_id="callie",
        )

    assert mock_handle_turn.await_args.kwargs["session_id"] == "slack-callie-T1-C0B9CHVC7KQ-_"
    assert mock_handle_turn.await_args.kwargs["user_text"] == "What do you recommend?"
    slack_client.post_message.assert_awaited_once_with(
        channel="C0B9CHVC7KQ",
        text="Here is the angle.",
        thread_ts=None,
    )


async def test_callie_events_path_routes_allowed_channel_messages() -> None:
    payload = {
        "type": "event_callback",
        "event_id": "Ev-callie-route",
        "team_id": "T001",
        "event": {
            "type": "message",
            "channel_type": "channel",
            "channel": "C0B9CHVC7KQ",
            "user": "U_TEAMMATE",
            "text": "Callie, what's the readout?",
            "ts": "123.456",
        },
    }
    body = json.dumps(payload).encode()
    headers = _signed_headers(body, "callie-signing-secret")

    from artemis.db import get_session
    from artemis.main import app

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_session] = _override_session
    try:
        with (
            patch(
                "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
                new_callable=AsyncMock,
                return_value=_callie_cfg(),
            ),
            patch(
                "artemis.routes.integrations_slack_events.repo.upsert_slack_inbound",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "artemis.routes.integrations_slack_events.route_inbound",
                new_callable=AsyncMock,
            ) as mock_route_inbound,
        ):
            async with await _make_client() as client:
                response = await client.post(
                    "/api/integrations/slack/events/callie",
                    content=body,
                    headers=headers,
                )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert mock_route_inbound.await_args.kwargs["agent_id"] == "callie"
    assert mock_route_inbound.await_args.args[0]["channel"] == "C0B9CHVC7KQ"


async def test_callie_events_path_rejects_artemis_signed_payload() -> None:
    payload = {
        "type": "event_callback",
        "event_id": "Ev-callie-badsig",
        "team_id": "T001",
        "event": {
            "type": "message",
            "channel_type": "channel",
            "channel": "C0B9CHVC7KQ",
            "user": "U_TEAMMATE",
            "text": "Wrong secret",
            "ts": "456.789",
        },
    }
    body = json.dumps(payload).encode()
    headers = _signed_headers(body, "artemis-signing-secret")

    with patch(
        "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
        new_callable=AsyncMock,
        return_value=_callie_cfg(),
    ):
        async with await _make_client() as client:
            response = await client.post(
                "/api/integrations/slack/events/callie",
                content=body,
                headers=headers,
            )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid signature"}
