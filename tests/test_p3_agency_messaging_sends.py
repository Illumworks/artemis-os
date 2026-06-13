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
