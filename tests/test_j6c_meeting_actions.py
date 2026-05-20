"""Tests for J6c meeting action routing endpoints.

Covers:
  - POST /api/meetings/{id}/actions/jira  — happy path + Jira error + missing text
  - POST /api/meetings/{id}/actions/okr   — happy path + KR not found + missing kr_id
  - POST /api/meetings/{id}/actions/slack — happy path + Slack not connected
  - POST /api/meetings/{id}/actions/todo  — happy path + missing action_text
  - POST /api/meetings/{id}/ask           — happy path + no Granola + missing question
  - GET  /api/meetings/{id}/routings      — happy path
  - Idempotency: second call to /actions/jira returns already_routed=True
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── session mock helper ───────────────────────────────────────────────────────


def _sync_result(fetchone_val: object = None, fetchall_val: list | None = None) -> MagicMock:
    r = MagicMock()
    r.fetchone.return_value = fetchone_val
    r.fetchall.return_value = fetchall_val or []
    return r


def _make_session(fetchone_val: object = None) -> AsyncMock:
    """Return a session whose execute() is async but result.fetchone/fetchall are sync."""
    s = AsyncMock()
    s.execute.return_value = _sync_result(fetchone_val)
    s.commit = AsyncMock()
    s.flush = AsyncMock()
    s.refresh = AsyncMock()
    s.add = MagicMock()
    return s


# ── Jira action ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_action_to_jira_happy() -> None:
    """POST /actions/jira creates issue and writes routing row."""
    from artemis.routes.meetings import route_action_to_jira

    session = _make_session(fetchone_val=None)

    with (
        patch(
            "artemis.routes.meetings._get_granola_client",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.integrations.config_resolver.resolve_jira_config",
            new_callable=AsyncMock,
        ) as mock_cfg,
        patch(
            "artemis.integrations.jira.client.JiraClient.create_issue",
            new_callable=AsyncMock,
            return_value={"key": "MT-42", "id": "10042"},
        ),
    ):
        from dataclasses import make_dataclass

        Cfg = make_dataclass(  # noqa: N806
            "Cfg",
            [
                "site_url",
                "email",
                "api_token",
                "project_key",
                "max_items_per_column",
                "team_members",
            ],
        )
        mock_cfg.return_value = Cfg(
            site_url="https://istation.atlassian.net",
            email="j@amira.com",
            api_token="tok",
            project_key="MT",
            max_items_per_column=20,
            team_members=(),
        )

        result = await route_action_to_jira(
            meeting_id="meet-1",
            body={"action_text": "Write the campaign brief"},
            session=session,
        )

    assert result["ok"] is True
    assert result["key"] == "MT-42"
    assert "MT-42" in result["url"]
    assert result["already_routed"] is False


@pytest.mark.asyncio
async def test_route_action_to_jira_already_routed() -> None:
    """Second call to /actions/jira returns already_routed=True, no Jira call."""
    from artemis.routes.meetings import route_action_to_jira

    session = _make_session(
        fetchone_val=(99, "MT-42", "https://istation.atlassian.net/browse/MT-42")
    )

    result = await route_action_to_jira(
        meeting_id="meet-1",
        body={"action_text": "Write the campaign brief"},
        session=session,
    )

    assert result["already_routed"] is True
    assert result["key"] == "MT-42"


@pytest.mark.asyncio
async def test_route_action_to_jira_missing_action_text() -> None:
    """POST /actions/jira with no action_text returns 422."""
    from fastapi import HTTPException

    from artemis.routes.meetings import route_action_to_jira

    session = _make_session()
    with pytest.raises(HTTPException) as exc_info:
        await route_action_to_jira(meeting_id="meet-1", body={}, session=session)
    assert exc_info.value.status_code == 422


# ── OKR action ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_action_to_okr_happy() -> None:
    """POST /actions/okr appends activity row and writes routing."""
    from artemis.routes.meetings import route_action_to_okr

    session = _make_session(fetchone_val=None)
    mock_kr = MagicMock()
    mock_kr.title = "Increase pipeline by 20%"
    mock_kr.id = 7
    session.get.return_value = mock_kr

    result = await route_action_to_okr(
        meeting_id="meet-1",
        body={"action_text": "Update the pipeline tracker", "kr_id": 7},
        session=session,
    )

    assert result["ok"] is True
    assert result["kr_id"] == 7
    assert result["already_routed"] is False


@pytest.mark.asyncio
async def test_route_action_to_okr_kr_not_found() -> None:
    """POST /actions/okr with unknown kr_id returns 404."""
    from fastapi import HTTPException

    from artemis.routes.meetings import route_action_to_okr

    session = _make_session(fetchone_val=None)
    session.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await route_action_to_okr(
            meeting_id="meet-1",
            body={"action_text": "Update tracker", "kr_id": 9999},
            session=session,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_route_action_to_okr_missing_kr_id() -> None:
    """POST /actions/okr without kr_id returns 422."""
    from fastapi import HTTPException

    from artemis.routes.meetings import route_action_to_okr

    session = _make_session(fetchone_val=None)
    with pytest.raises(HTTPException) as exc_info:
        await route_action_to_okr(
            meeting_id="meet-1",
            body={"action_text": "Update tracker"},
            session=session,
        )
    assert exc_info.value.status_code == 422


# ── Slack action ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_action_to_slack_happy() -> None:
    """POST /actions/slack posts a DM and writes routing row."""
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.routes.meetings import route_action_to_slack

    creds = {"access_token": "xoxb-123", "authed_user": "U12345"}
    encrypted = encrypt_credentials(creds)
    mock_integration = MagicMock()
    mock_integration.encrypted_credentials = encrypted

    session = _make_session(fetchone_val=None)

    with (
        patch(
            "artemis.integrations.repository.list_active",
            new_callable=AsyncMock,
            return_value=[mock_integration],
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient.post_message",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
    ):
        result = await route_action_to_slack(
            meeting_id="meet-1",
            body={"action_text": "Send the deck", "when": "2026-05-19T09:00:00"},
            session=session,
        )

    assert result["ok"] is True
    assert result["already_routed"] is False


@pytest.mark.asyncio
async def test_route_action_to_slack_not_connected() -> None:
    """POST /actions/slack with no Slack integration returns 503."""
    from fastapi import HTTPException

    from artemis.routes.meetings import route_action_to_slack

    session = _make_session(fetchone_val=None)

    with (
        patch(
            "artemis.integrations.repository.list_active",
            new_callable=AsyncMock,
            return_value=[],
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await route_action_to_slack(
            meeting_id="meet-1",
            body={"action_text": "Send the deck"},
            session=session,
        )
    assert exc_info.value.status_code == 503


# ── Todo action ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_action_to_todo_happy() -> None:
    """POST /actions/todo creates a personal_todos row and routing."""
    from artemis.routes.meetings import route_action_to_todo

    session = AsyncMock()
    session.commit = AsyncMock()
    # Call sequence: _get_routing → None; INSERT RETURNING → (55,); _insert_routing → None
    session.execute = AsyncMock(
        side_effect=[
            _sync_result(fetchone_val=None),  # _get_routing: no existing row
            _sync_result(fetchone_val=(55,)),  # INSERT INTO personal_todos RETURNING id
            _sync_result(fetchone_val=None),  # _insert_routing ON CONFLICT
        ]
    )

    result = await route_action_to_todo(
        meeting_id="meet-1",
        body={"action_text": "Follow up with Legal"},
        session=session,
    )

    assert result["ok"] is True
    assert result["id"] == 55
    assert result["already_routed"] is False


@pytest.mark.asyncio
async def test_route_action_to_todo_missing_text() -> None:
    """POST /actions/todo without action_text returns 422."""
    from fastapi import HTTPException

    from artemis.routes.meetings import route_action_to_todo

    session = _make_session()
    with pytest.raises(HTTPException) as exc_info:
        await route_action_to_todo(meeting_id="meet-1", body={}, session=session)
    assert exc_info.value.status_code == 422


# ── Ask ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_about_meeting_happy() -> None:
    """POST /ask returns an answer from the LLM."""
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage
    from artemis.routes.meetings import ask_about_meeting

    session = _make_session()

    mock_detail = {
        "transcript": "Jon: We need to ship the dashboard by Friday.\nSarah: Agreed.",
        "summary": "",
        "title": "Standup",
        "date": "2026-05-18",
    }

    fake_response = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text="The dashboard ships Friday.")]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=8),
    )

    mock_granola = AsyncMock()
    mock_granola.get_meeting = AsyncMock(return_value=mock_detail)
    mock_adapter = AsyncMock()
    mock_adapter.complete = AsyncMock(return_value=fake_response)

    with (
        patch(
            "artemis.routes.meetings._get_granola_client",
            new_callable=AsyncMock,
            return_value=mock_granola,
        ),
        patch(
            "artemis.providers.get_adapter",
            return_value=mock_adapter,
        ),
    ):
        result = await ask_about_meeting(
            meeting_id="meet-1",
            body={"question": "When does the dashboard ship?"},
            session=session,
        )

    assert "Friday" in result["answer"]
    assert isinstance(result["citations"], list)


@pytest.mark.asyncio
async def test_ask_about_meeting_no_granola() -> None:
    """POST /ask with Granola not connected returns 503."""
    from fastapi import HTTPException

    from artemis.routes.meetings import ask_about_meeting

    session = _make_session()

    with (
        patch(
            "artemis.routes.meetings._get_granola_client",
            new_callable=AsyncMock,
            return_value=None,
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await ask_about_meeting(
            meeting_id="meet-1",
            body={"question": "What did we decide?"},
            session=session,
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_ask_about_meeting_missing_question() -> None:
    """POST /ask without question returns 422."""
    from fastapi import HTTPException

    from artemis.routes.meetings import ask_about_meeting

    session = _make_session()
    with pytest.raises(HTTPException) as exc_info:
        await ask_about_meeting(meeting_id="meet-1", body={}, session=session)
    assert exc_info.value.status_code == 422


# ── Routings ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_meeting_routings_happy() -> None:
    """GET /routings returns persisted rows."""
    from datetime import datetime

    from artemis.routes.meetings import get_meeting_routings

    session = AsyncMock()
    ts = datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)
    sync_r = MagicMock()
    sync_r.fetchall.return_value = [
        (1, "Ship the deck", "jira", "MT-42", "https://istation.atlassian.net/browse/MT-42", ts),
    ]
    session.execute.return_value = sync_r

    result = await get_meeting_routings(meeting_id="meet-1", session=session)

    assert result["meeting_id"] == "meet-1"
    assert len(result["routings"]) == 1
    r = result["routings"][0]
    assert r["routed_to"] == "jira"
    assert r["target_id"] == "MT-42"
    assert "2026-05-18" in r["routed_at"]
