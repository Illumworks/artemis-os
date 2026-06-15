"""Route-level tests for the agency messaging send brief."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_event_data(text: str) -> dict[str, Any]:
    return {
        "team_id": "T_TEST",
        "channel": "D_JON",
        "user": "U_JON",
        "text": text,
        "ts": "1710000000.000100",
        "thread_ts": None,
    }


def _make_mock_session_local() -> MagicMock:
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session_local


async def test_route_inbound_reply_radar_command_stages_proposal_and_skips_handle_turn() -> None:
    from artemis.routes.integrations_slack_events import route_inbound

    posted: list[str] = []

    async def _fake_post_slack_message(**kwargs: Any) -> None:
        posted.append(str(kwargs["outbound_text"]))

    mock_session_local = _make_mock_session_local()
    fake_action = MagicMock(id=42)
    fake_radar_item = MagicMock(label="#ops thread")

    with (
        patch("artemis.db.SessionLocal", mock_session_local),
        patch(
            "artemis.proactivity.repository.get_live_okr_checkin_breadcrumb",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.integrations.repository.get_slack_user",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            new_callable=AsyncMock,
            side_effect=ValueError("not found"),
        ),
        patch("artemis.floating_artemis.repository.create_session", new_callable=AsyncMock),
        patch(
            "artemis.proactivity.commitments.try_apply_commitment_reply",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.proactivity.agency_gate.propose_radar_slack_reply",
            new_callable=AsyncMock,
            return_value=(fake_action, fake_radar_item),
        ) as mock_propose,
        patch(
            "artemis.routes.integrations_slack_events._post_slack_message",
            new=AsyncMock(side_effect=_fake_post_slack_message),
        ),
        patch("artemis.floating_artemis.chat.handle_turn", new_callable=AsyncMock) as mock_handle,
    ):
        await route_inbound(_make_event_data("reply radar 12 Thanks, I am on it."))

    assert posted == [
        "Proposed reply to radar item #12 (#ops thread). Check your DM and reply yes A42 to send it."
    ]
    mock_propose.assert_awaited_once()
    call = mock_propose.await_args.kwargs
    assert call["radar_item_id"] == 12
    assert call["reply_text"] == "Thanks, I am on it."
    assert call["requested_by"] == "artemis"
    assert call["target_user_id"] == "U_JON"
    mock_handle.assert_not_awaited()


# ── Helper ────────────────────────────────────────────────────────────────────


def _make_action(
    *,
    status: str = "approved",
    action_type: str = "slack.send",
    payload: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a minimal ProposedAction-like mock for executor-level tests."""
    from artemis.proactivity.models import ProposedAction

    action = MagicMock(spec=ProposedAction)
    action.status = status
    action.action_type = action_type
    action.payload = payload or {}
    return action


# ── Test 1: gate blocks non-approved status (slack.send AND gmail.send) ───────


async def test_execute_proposed_action_raises_for_non_approved_slack_send() -> None:
    """Gate raises ValueError when status != 'approved' for a slack.send action.
    The network client is never constructed."""
    from artemis.proactivity.agency_gate import execute_proposed_action

    action = _make_action(status="proposed", action_type="slack.send")
    mock_session = AsyncMock()

    # SlackClient is imported inside the function body, so patch at source module.
    with (
        patch("artemis.integrations.slack.client.SlackClient") as mock_slack_cls,
        patch(
            "artemis.proactivity.agency_gate._resolve_slack_user_token",
            new_callable=AsyncMock,
        ) as mock_resolve,
        pytest.raises(ValueError, match="only 'approved' proposals may execute"),
    ):
        await execute_proposed_action(mock_session, action)

    mock_slack_cls.assert_not_called()
    mock_resolve.assert_not_awaited()


async def test_execute_proposed_action_raises_for_non_approved_gmail_send() -> None:
    """Gate raises ValueError when status != 'approved' for a gmail.send action.
    The Gmail client resolver is never called."""
    from artemis.proactivity.agency_gate import execute_proposed_action

    action = _make_action(status="pending", action_type="gmail.send")
    mock_session = AsyncMock()

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_personal_gmail_client",
            new_callable=AsyncMock,
        ) as mock_resolve,
        pytest.raises(ValueError, match="only 'approved' proposals may execute"),
    ):
        await execute_proposed_action(mock_session, action)

    mock_resolve.assert_not_awaited()


# ── Test 2: _execute_slack_send happy path ────────────────────────────────────


async def test_execute_slack_send_happy_path() -> None:
    """_execute_slack_send posts to Slack with the exact payload fields and
    returns the expected dict including message_ts from posted['ts']."""
    from artemis.proactivity.agency_gate import _execute_slack_send

    payload = {
        "channel": "C_GENERAL",
        "text": "Hello from Jon",
        "thread_ts": "1710000000.000200",
    }
    action = _make_action(action_type="slack.send", payload=payload)
    mock_session = AsyncMock()

    fake_posted = {"ts": "1710000001.000001", "ok": True}
    mock_post_message = AsyncMock(return_value=fake_posted)
    mock_slack_instance = MagicMock()
    mock_slack_instance.post_message = mock_post_message

    # SlackClient is imported inside the function body — patch at source module.
    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_slack_user_token",
            new_callable=AsyncMock,
            return_value="xoxp-fake-token",
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient",
            return_value=mock_slack_instance,
        ) as mock_slack_cls,
    ):
        result = await _execute_slack_send(mock_session, action)

    mock_slack_cls.assert_called_once_with(token="xoxp-fake-token")
    mock_post_message.assert_awaited_once_with(
        channel="C_GENERAL",
        text="Hello from Jon",
        thread_ts="1710000000.000200",
    )
    assert result["channel"] == "C_GENERAL"
    assert result["text"] == "Hello from Jon"
    assert result["thread_ts"] == "1710000000.000200"
    assert result["message_ts"] == "1710000001.000001"


# ── Test 3: _execute_slack_send error paths ───────────────────────────────────


async def test_execute_slack_send_raises_runtime_error_when_token_is_none() -> None:
    """_execute_slack_send raises RuntimeError when the token resolves to None.
    post_message must never be called."""
    from artemis.proactivity.agency_gate import _execute_slack_send

    action = _make_action(
        action_type="slack.send",
        payload={"channel": "C_GENERAL", "text": "hi"},
    )
    mock_session = AsyncMock()

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_slack_user_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient",
        ) as mock_slack_cls,
        pytest.raises(RuntimeError, match="No active Slack user token"),
    ):
        await _execute_slack_send(mock_session, action)

    mock_slack_cls.assert_not_called()


async def test_execute_slack_send_raises_value_error_when_channel_missing() -> None:
    """_execute_slack_send raises ValueError when channel is absent from payload."""
    from artemis.proactivity.agency_gate import _execute_slack_send

    action = _make_action(
        action_type="slack.send",
        payload={"text": "hi"},  # no channel
    )
    mock_session = AsyncMock()
    mock_slack_instance = MagicMock()
    mock_slack_instance.post_message = AsyncMock()

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_slack_user_token",
            new_callable=AsyncMock,
            return_value="xoxp-fake-token",
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient",
            return_value=mock_slack_instance,
        ),
        pytest.raises(ValueError, match="must include channel"),
    ):
        await _execute_slack_send(mock_session, action)

    mock_slack_instance.post_message.assert_not_awaited()


async def test_execute_slack_send_raises_value_error_when_text_missing() -> None:
    """_execute_slack_send raises ValueError when text is absent from payload."""
    from artemis.proactivity.agency_gate import _execute_slack_send

    action = _make_action(
        action_type="slack.send",
        payload={"channel": "C_GENERAL"},  # no text
    )
    mock_session = AsyncMock()
    mock_slack_instance = MagicMock()
    mock_slack_instance.post_message = AsyncMock()

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_slack_user_token",
            new_callable=AsyncMock,
            return_value="xoxp-fake-token",
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient",
            return_value=mock_slack_instance,
        ),
        pytest.raises(ValueError, match="must include text"),
    ):
        await _execute_slack_send(mock_session, action)

    mock_slack_instance.post_message.assert_not_awaited()


# ── Test 4: _execute_gmail_send happy path ────────────────────────────────────


async def test_execute_gmail_send_happy_path() -> None:
    """_execute_gmail_send calls send_message with all payload fields and maps
    id → message_id and threadId → thread_id in the returned dict."""
    from artemis.proactivity.agency_gate import _execute_gmail_send

    payload = {
        "to": "recipient@example.com",
        "subject": "Re: Q2 plan",
        "body": "Looks great, let's proceed.",
        "thread_id": "thread-abc-123",
        "in_reply_to": "<msg-id@mail.gmail.com>",
    }
    action = _make_action(action_type="gmail.send", payload=payload)
    mock_session = AsyncMock()

    fake_sent = {"id": "msg-001", "threadId": "thread-abc-123"}
    mock_send_message = AsyncMock(return_value=fake_sent)
    mock_gmail_client = MagicMock()
    mock_gmail_client.send_message = mock_send_message

    with patch(
        "artemis.proactivity.agency_gate._resolve_personal_gmail_client",
        new_callable=AsyncMock,
        return_value=mock_gmail_client,
    ):
        result = await _execute_gmail_send(mock_session, action)

    mock_send_message.assert_awaited_once_with(
        to="recipient@example.com",
        subject="Re: Q2 plan",
        body="Looks great, let's proceed.",
        thread_id="thread-abc-123",
        in_reply_to="<msg-id@mail.gmail.com>",
    )
    assert result["message_id"] == "msg-001"
    assert result["thread_id"] == "thread-abc-123"
    assert result["to"] == "recipient@example.com"
    assert result["subject"] == "Re: Q2 plan"


# ── Test 5: _execute_gmail_send error paths ───────────────────────────────────


async def test_execute_gmail_send_raises_value_error_when_to_missing() -> None:
    """_execute_gmail_send raises ValueError when 'to' is absent; send_message never called."""
    from artemis.proactivity.agency_gate import _execute_gmail_send

    action = _make_action(
        action_type="gmail.send",
        payload={"body": "some body"},  # no to
    )
    mock_session = AsyncMock()
    mock_gmail_client = MagicMock()
    mock_gmail_client.send_message = AsyncMock()

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_personal_gmail_client",
            new_callable=AsyncMock,
            return_value=mock_gmail_client,
        ),
        pytest.raises(ValueError, match="must include to"),
    ):
        await _execute_gmail_send(mock_session, action)

    mock_gmail_client.send_message.assert_not_awaited()


async def test_execute_gmail_send_raises_value_error_when_body_missing() -> None:
    """_execute_gmail_send raises ValueError when 'body' is absent; send_message never called."""
    from artemis.proactivity.agency_gate import _execute_gmail_send

    action = _make_action(
        action_type="gmail.send",
        payload={"to": "someone@example.com", "subject": "Hello"},  # no body
    )
    mock_session = AsyncMock()
    mock_gmail_client = MagicMock()
    mock_gmail_client.send_message = AsyncMock()

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_personal_gmail_client",
            new_callable=AsyncMock,
            return_value=mock_gmail_client,
        ),
        pytest.raises(ValueError, match="must include body"),
    ):
        await _execute_gmail_send(mock_session, action)

    mock_gmail_client.send_message.assert_not_awaited()


# ── Test 6: gate-gating idempotency / only-approved-can-reach-executor ────────


async def test_execute_proposed_action_only_approved_reaches_executor() -> None:
    """Non-approved statuses (proposed, rejected, executed, failed, expired) all
    raise ValueError BEFORE any executor or network client is touched."""
    from artemis.proactivity.agency_gate import execute_proposed_action

    non_approved_statuses = ["proposed", "rejected", "executed", "failed", "expired"]
    mock_session = AsyncMock()

    for status in non_approved_statuses:
        action = _make_action(status=status, action_type="slack.send")

        with (
            patch("artemis.integrations.slack.client.SlackClient") as mock_slack_cls,
            patch(
                "artemis.proactivity.agency_gate._resolve_slack_user_token",
                new_callable=AsyncMock,
            ) as mock_resolve,
            patch(
                "artemis.proactivity.agency_gate._resolve_personal_gmail_client",
                new_callable=AsyncMock,
            ) as mock_gmail_resolve,
        ):
            with pytest.raises(ValueError, match="only 'approved' proposals may execute"):
                await execute_proposed_action(mock_session, action)

            mock_slack_cls.assert_not_called()
            mock_resolve.assert_not_awaited()
            mock_gmail_resolve.assert_not_awaited()
