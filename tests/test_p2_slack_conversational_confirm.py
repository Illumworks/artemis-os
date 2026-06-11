"""Tests for the P2 conversational Slack confirm flow.

Covers:
  1. Slack session with a pending layer-3 confirmation + affirmative reply
     → resolves "run" + resumes (tool impl mocked); result posted to Slack.
  2. Negative reply ("no") → resolves "cancel"; cancel ack posted.
  3. Unrelated reply ("neither") → normal new handle_turn; pending stays intact.
  4. Web /tool-confirm path regression — still works (no regression).

All tests are DB-backed via the isolated artemis_test_p2_confirm DB.
LLM calls (handle_turn, resume_after_confirm, confirm classifier) are mocked.
Slack client is mocked so no real network calls are made.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers for route_inbound
# ---------------------------------------------------------------------------

_TEAM_ID = "T_TEST_CONFIRM"
_CHANNEL_ID = "D_CONFIRM_DM"
_USER_ID = "U_JON"
_AGENT_ID = "artemis"
_SESSION_ID = f"slack-{_AGENT_ID}-{_TEAM_ID}-{_CHANNEL_ID}-_"


def _make_event_data(text: str = "go") -> dict[str, Any]:
    return {
        "team_id": _TEAM_ID,
        "channel": _CHANNEL_ID,
        "user": _USER_ID,
        "text": text,
        "ts": "999.000",
        "thread_ts": None,
    }


def _make_mock_session_local() -> MagicMock:
    """Build a mock asyncpg session context manager that does nothing."""
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session_local


def _make_mock_agent_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.access_token = "xoxb-test"
    return cfg


async def _run_route_inbound(
    text: str,
    *,
    confirm_classifier_verdict: str,
    handle_turn_result: MagicMock | None = None,
    resume_result: MagicMock | None = None,
    pending_confirmation: Any = None,
) -> list[str]:
    """Run route_inbound with all external deps mocked.

    Returns the list of texts posted to Slack.
    """
    from artemis.floating_artemis.authority import confirmation_store
    from artemis.routes.integrations_slack_events import route_inbound

    # Seed a pending confirmation if provided
    if pending_confirmation is not None:
        confirmation_store.add(pending_confirmation)

    posted_texts: list[str] = []

    mock_session_local = _make_mock_session_local()
    mock_agent_cfg = _make_mock_agent_cfg()

    async def _fake_post_message(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted_texts.append(text)

    mock_slack_client = MagicMock()
    mock_slack_client.post_message = _fake_post_message

    # Default handle_turn result: normal response, no pending
    if handle_turn_result is None:
        handle_turn_result = MagicMock()
        handle_turn_result.response_text = "Normal reply."
        handle_turn_result.pending_tool_use_id = None

    # Default resume_after_confirm result
    if resume_result is None:
        resume_result = MagicMock()
        resume_result.response_text = "Done — applied the change."

    async def _confirm_classifier(t: str) -> str:
        return confirm_classifier_verdict

    try:
        with (
            patch("artemis.db.SessionLocal", mock_session_local),
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
                "artemis.floating_artemis.chat.handle_turn",
                new_callable=AsyncMock,
                return_value=handle_turn_result,
            ),
            patch(
                "artemis.floating_artemis.chat.resume_after_confirm",
                new_callable=AsyncMock,
                return_value=resume_result,
            ),
            patch(
                "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
                new_callable=AsyncMock,
                return_value=mock_agent_cfg,
            ),
            patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack_client),
        ):
            await route_inbound(
                _make_event_data(text),
                agent_id=_AGENT_ID,
                confirm_classifier=_confirm_classifier,
            )
    finally:
        # Clean up any leftover pending confirmations so tests don't bleed into each other
        confirmation_store.clear_session(_SESSION_ID)

    return posted_texts


def _make_pending(tool_use_id: str = "tuid-001") -> Any:
    from artemis.floating_artemis.authority import PendingConfirmation

    return PendingConfirmation(
        session_id=_SESSION_ID,
        tool_use_id=tool_use_id,
        tool_name="update_okr_kr",
        tool_input={"kr_id": "KR-2", "value": 65},
        layer=3,
    )


# ---------------------------------------------------------------------------
# Test 1: Affirmative reply → resolves "run" + resumes + posts result
# ---------------------------------------------------------------------------


async def test_affirmative_reply_resolves_run_and_posts_result() -> None:
    """'go' resolves the pending to 'run', resumes, posts the result text."""

    pending = _make_pending("tuid-aff-001")
    resume_result = MagicMock()
    resume_result.response_text = "Done — updated KR2 to 65%."

    posted = await _run_route_inbound(
        "go",
        confirm_classifier_verdict="YES",
        pending_confirmation=pending,
        resume_result=resume_result,
    )

    # Result text should be posted
    assert len(posted) == 1, f"Expected 1 post, got {posted!r}"
    assert "Done" in posted[0] or "KR2" in posted[0] or "65" in posted[0], (
        f"Unexpected post text: {posted[0]!r}"
    )

    # Pending should be cleared (resolved during resume_after_confirm mock called with "run")
    # The mock bypasses real store.resolve — but we can verify resume_after_confirm was called
    # with decision="run" by checking the mock was called (captured via patch)


async def test_affirmative_reply_calls_resume_with_run_decision() -> None:
    """Affirmative reply → resume_after_confirm called with decision='run'."""
    pending = _make_pending("tuid-aff-002")

    mock_resume = AsyncMock()
    mock_resume.return_value = MagicMock(response_text="Done.")

    from artemis.floating_artemis.authority import confirmation_store

    confirmation_store.add(pending)

    posted: list[str] = []
    mock_session_local = _make_mock_session_local()
    mock_agent_cfg = _make_mock_agent_cfg()

    async def _fake_post_message(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted.append(text)

    mock_slack_client = MagicMock()
    mock_slack_client.post_message = _fake_post_message

    async def _yes_classifier(t: str) -> str:
        return "YES"

    try:
        with (
            patch("artemis.db.SessionLocal", mock_session_local),
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
                "artemis.floating_artemis.chat.resume_after_confirm",
                mock_resume,
            ),
            patch(
                "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
                new_callable=AsyncMock,
                return_value=mock_agent_cfg,
            ),
            patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack_client),
        ):
            from artemis.routes.integrations_slack_events import route_inbound

            await route_inbound(
                _make_event_data("go"),
                agent_id=_AGENT_ID,
                confirm_classifier=_yes_classifier,
            )
    finally:
        confirmation_store.clear_session(_SESSION_ID)

    mock_resume.assert_awaited_once()
    call_kwargs = mock_resume.call_args.kwargs
    assert call_kwargs.get("decision") == "run", (
        f"Expected decision='run', got {call_kwargs.get('decision')!r}"
    )
    assert call_kwargs.get("tool_use_id") == "tuid-aff-002"
    assert call_kwargs.get("session_id") == _SESSION_ID


# ---------------------------------------------------------------------------
# Test 2: Negative reply → resolves "cancel" + posts ack
# ---------------------------------------------------------------------------


async def test_negative_reply_calls_resume_with_cancel_decision() -> None:
    """'no' → resume_after_confirm called with decision='cancel'."""
    pending = _make_pending("tuid-neg-001")

    mock_resume = AsyncMock()
    mock_resume.return_value = MagicMock(response_text="Cancelled — I won't update that.")

    from artemis.floating_artemis.authority import confirmation_store

    confirmation_store.add(pending)

    posted: list[str] = []
    mock_session_local = _make_mock_session_local()
    mock_agent_cfg = _make_mock_agent_cfg()

    async def _fake_post_message(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted.append(text)

    mock_slack_client = MagicMock()
    mock_slack_client.post_message = _fake_post_message

    async def _no_classifier(t: str) -> str:
        return "NO"

    try:
        with (
            patch("artemis.db.SessionLocal", mock_session_local),
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
                "artemis.floating_artemis.chat.resume_after_confirm",
                mock_resume,
            ),
            patch(
                "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
                new_callable=AsyncMock,
                return_value=mock_agent_cfg,
            ),
            patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack_client),
        ):
            from artemis.routes.integrations_slack_events import route_inbound

            await route_inbound(
                _make_event_data("no"),
                agent_id=_AGENT_ID,
                confirm_classifier=_no_classifier,
            )
    finally:
        confirmation_store.clear_session(_SESSION_ID)

    mock_resume.assert_awaited_once()
    call_kwargs = mock_resume.call_args.kwargs
    assert call_kwargs.get("decision") == "cancel", (
        f"Expected decision='cancel', got {call_kwargs.get('decision')!r}"
    )
    # A reply text should be posted
    assert len(posted) == 1, f"Expected 1 post for cancel ack, got {posted!r}"


# ---------------------------------------------------------------------------
# Test 3: Unrelated reply → new handle_turn; pending stays intact
# ---------------------------------------------------------------------------


async def test_unrelated_reply_neither_falls_through_to_handle_turn() -> None:
    """'neither' reply → handle_turn is called, pending confirmation stays in store."""
    from artemis.floating_artemis.authority import confirmation_store

    pending = _make_pending("tuid-nei-001")
    confirmation_store.add(pending)

    mock_handle = AsyncMock()
    normal_result = MagicMock()
    normal_result.response_text = "Sure, here's your answer."
    normal_result.pending_tool_use_id = None
    mock_handle.return_value = normal_result

    mock_resume = AsyncMock()  # Should NOT be called

    posted: list[str] = []
    mock_session_local = _make_mock_session_local()
    mock_agent_cfg = _make_mock_agent_cfg()

    async def _fake_post_message(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted.append(text)

    mock_slack_client = MagicMock()
    mock_slack_client.post_message = _fake_post_message

    async def _neither_classifier(t: str) -> str:
        return "NEITHER"

    try:
        with (
            patch("artemis.db.SessionLocal", mock_session_local),
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
                "artemis.floating_artemis.chat.handle_turn",
                mock_handle,
            ),
            patch(
                "artemis.floating_artemis.chat.resume_after_confirm",
                mock_resume,
            ),
            patch(
                "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
                new_callable=AsyncMock,
                return_value=mock_agent_cfg,
            ),
            patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack_client),
        ):
            from artemis.routes.integrations_slack_events import route_inbound

            await route_inbound(
                _make_event_data("What's the weather like?"),
                agent_id=_AGENT_ID,
                confirm_classifier=_neither_classifier,
            )
    finally:
        # Pending should still exist (NEITHER doesn't resolve it)
        remaining = confirmation_store.list_for_session(_SESSION_ID)
        confirmation_store.clear_session(_SESSION_ID)

    # handle_turn was called (normal turn)
    mock_handle.assert_awaited_once()
    # resume_after_confirm was NOT called
    mock_resume.assert_not_awaited()
    # Pending was NOT resolved (still existed after route_inbound)
    assert len(remaining) == 1, (
        f"Expected 1 pending remaining (NEITHER should not resolve), got {remaining!r}"
    )
    assert remaining[0].tool_use_id == "tuid-nei-001"


async def test_no_pending_normal_turn_runs_normally() -> None:
    """When no pending confirmation exists, route_inbound runs handle_turn normally."""
    from artemis.floating_artemis.authority import confirmation_store

    # Ensure no pending for our session
    confirmation_store.clear_session(_SESSION_ID)

    mock_handle = AsyncMock()
    normal_result = MagicMock()
    normal_result.response_text = "Here's your update."
    normal_result.pending_tool_use_id = None
    mock_handle.return_value = normal_result

    mock_resume = AsyncMock()  # Should NOT be called

    posted: list[str] = []
    mock_session_local = _make_mock_session_local()
    mock_agent_cfg = _make_mock_agent_cfg()

    async def _fake_post_message(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted.append(text)

    mock_slack_client = MagicMock()
    mock_slack_client.post_message = _fake_post_message

    # Classifier should NOT be called when there's no pending
    classifier_called: list[str] = []

    async def _classifier_should_not_be_called(t: str) -> str:
        classifier_called.append(t)
        return "YES"

    with (
        patch("artemis.db.SessionLocal", mock_session_local),
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
            "artemis.floating_artemis.chat.handle_turn",
            mock_handle,
        ),
        patch(
            "artemis.floating_artemis.chat.resume_after_confirm",
            mock_resume,
        ),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=mock_agent_cfg,
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack_client),
    ):
        from artemis.routes.integrations_slack_events import route_inbound

        await route_inbound(
            _make_event_data("What's the latest?"),
            agent_id=_AGENT_ID,
            confirm_classifier=_classifier_should_not_be_called,
        )

    mock_handle.assert_awaited_once()
    mock_resume.assert_not_awaited()
    assert len(posted) == 1
    assert classifier_called == [], "Classifier must not run when no pending confirmation"


# ---------------------------------------------------------------------------
# Test 4: Tool-pending result → proposal text posted to Slack
# ---------------------------------------------------------------------------


async def test_tool_pending_result_posts_proposal_text() -> None:
    """When handle_turn yields tool_pending, the proposal text is posted to Slack."""
    from artemis.floating_artemis.authority import confirmation_store

    # No pre-existing pending
    confirmation_store.clear_session(_SESSION_ID)

    mock_handle = AsyncMock()
    # handle_turn returns tool_pending with proposal text
    pending_result = MagicMock()
    pending_result.response_text = "I'm going to update KR2 to 65%. Say 'go' to confirm."
    pending_result.pending_tool_use_id = "tuid-proposal-001"
    mock_handle.return_value = pending_result

    posted: list[str] = []
    mock_session_local = _make_mock_session_local()
    mock_agent_cfg = _make_mock_agent_cfg()

    async def _fake_post_message(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted.append(text)

    mock_slack_client = MagicMock()
    mock_slack_client.post_message = _fake_post_message

    with (
        patch("artemis.db.SessionLocal", mock_session_local),
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
            "artemis.floating_artemis.chat.handle_turn",
            mock_handle,
        ),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=mock_agent_cfg,
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack_client),
    ):
        from artemis.routes.integrations_slack_events import route_inbound

        await route_inbound(
            _make_event_data("run the OKR check-in"),
            agent_id=_AGENT_ID,
        )

    assert len(posted) == 1, f"Expected proposal to be posted, got {posted!r}"
    assert "KR2" in posted[0] or "go" in posted[0] or "65" in posted[0], (
        f"Unexpected proposal text: {posted[0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: Web /tool-confirm path regression
# ---------------------------------------------------------------------------


def _make_signed_request(
    body_dict: dict[str, Any], secret: str = "test-secret"
) -> tuple[bytes, dict[str, str]]:
    body_bytes = json.dumps(body_dict).encode()
    ts = str(int(time.time()))
    base = f"v0:{ts}:{body_bytes.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/json",
    }
    return body_bytes, headers


async def test_web_tool_confirm_path_still_works() -> None:
    """POST /api/floating-artemis/sessions/{id}/tool-confirm still routes correctly.

    Regression guard: the web confirm path must not be broken by the Slack changes.
    """
    from collections.abc import AsyncIterator

    from httpx import ASGITransport, AsyncClient

    from artemis.floating_artemis.authority import PendingConfirmation, confirmation_store
    from artemis.main import app

    # Seed a pending confirmation
    session_id = "web-test-confirm-session"
    tool_use_id = "tuid-web-001"
    pending = PendingConfirmation(
        session_id=session_id,
        tool_use_id=tool_use_id,
        tool_name="update_okr_kr",
        tool_input={"kr_id": "KR-1", "value": 50},
        layer=3,
    )
    confirmation_store.add(pending)

    # Mock resume_after_confirm so no real DB/LLM call needed
    mock_resume = AsyncMock(return_value=MagicMock(response_text="Done."))

    from artemis.db import get_session

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_session] = _override_session

    try:
        with patch(
            "artemis.routes.floating_artemis.resume_after_confirm",
            mock_resume,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/floating-artemis/sessions/{session_id}/tool-confirm",
                    json={"tool_use_id": tool_use_id, "decision": "run"},
                )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["tool_use_id"] == tool_use_id
        assert data["decision"] == "run"
    finally:
        app.dependency_overrides.pop(get_session, None)
        confirmation_store.clear_session(session_id)


async def test_web_tool_confirm_404_when_not_found() -> None:
    """POST /tool-confirm with unknown tool_use_id returns 404."""
    from collections.abc import AsyncIterator

    from httpx import ASGITransport, AsyncClient

    from artemis.db import get_session
    from artemis.main import app

    async def _override_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_session] = _override_session

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/floating-artemis/sessions/nonexistent-session/tool-confirm",
                json={"tool_use_id": "nonexistent-id", "decision": "run"},
            )

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# Test 6: _default_confirm_classifier pure logic (no LLM)
# ---------------------------------------------------------------------------


def test_default_confirm_classifier_system_prompt_is_conservative() -> None:
    """The confirm classifier system prompt mentions conservative defaults."""
    from artemis.routes.integrations_slack_events import _CONFIRM_CLASSIFIER_SYSTEM

    # System prompt should guide toward NEITHER when uncertain
    assert "NEITHER" in _CONFIRM_CLASSIFIER_SYSTEM
    assert "conservative" in _CONFIRM_CLASSIFIER_SYSTEM.lower()


async def test_default_confirm_classifier_failure_returns_neither() -> None:
    """When the LLM call fails, _default_confirm_classifier returns 'NEITHER'."""
    from artemis.routes.integrations_slack_events import _default_confirm_classifier

    with patch(
        "artemis.agent.client.AnthropicAdapter.complete",
        new_callable=AsyncMock,
        side_effect=RuntimeError("API unavailable"),
    ):
        result = await _default_confirm_classifier("go ahead")

    assert result == "NEITHER", f"Expected NEITHER on failure, got {result!r}"
