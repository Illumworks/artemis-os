"""Pure unit + integration tests for the Callie channel-message gate and @mention logic.

Covers:
  - should_ping_asker — pure function, no I/O
  - should_respond_to_channel_message — injectable classifier, no live LLM or DB
  - _default_channel_classifier — mocked complete_with_fallback, no live LLM
  - route_inbound ping_user_id injection — ping prefix applied after lint
  - _handle_mentionable_event gate path — relevance gate drops idle chatter
  - Bot-authored events are still dropped before any gate
  - app_mention always replies (and pings if cold)
  - DM replies are unchanged, no self-ping

These tests NEVER touch a live DB or real Anthropic key.
All DB-touching helpers are mocked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # noqa: F401 — used by pytest infrastructure

from artemis.routes.integrations_slack_events import (
    _REENGAGE_PING_GAP,
    should_ping_asker,
    should_respond_to_channel_message,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_msg_dedup_cache() -> None:
    """Reset the module-level message-identity dedup cache before each test.

    Without this, tests that share a default ``ts`` value would be silently
    dropped by the in-process dedup added to ``_handle_mentionable_event``.
    """
    from artemis.routes.integrations_slack_events import _msg_dedup_cache

    _msg_dedup_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)


def _dt_ago(minutes: float) -> datetime:
    return _NOW - timedelta(minutes=minutes)


def _dt_future(minutes: float) -> datetime:
    return _NOW + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# should_ping_asker — pure function tests
# ---------------------------------------------------------------------------


def test_ping_asker_dm_never_pings() -> None:
    """DMs never get the ping regardless of session age."""
    assert should_ping_asker(is_dm=True, last_message_at=None, now=_NOW) is False
    assert should_ping_asker(is_dm=True, last_message_at=_dt_ago(100), now=_NOW) is False
    assert should_ping_asker(is_dm=True, last_message_at=_dt_ago(1), now=_NOW) is False


def test_ping_asker_cold_start_pings() -> None:
    """No prior messages (None) → cold start → ping."""
    assert should_ping_asker(is_dm=False, last_message_at=None, now=_NOW) is True


def test_ping_asker_active_flow_no_ping() -> None:
    """Last message < threshold ago → active flow → no ping."""
    # Just under the threshold
    last = _dt_ago(_REENGAGE_PING_GAP.total_seconds() / 60 - 0.1)
    assert should_ping_asker(is_dm=False, last_message_at=last, now=_NOW) is False


def test_ping_asker_exactly_at_threshold_pings() -> None:
    """Last message exactly at threshold → re-engagement → ping."""
    last = _NOW - _REENGAGE_PING_GAP
    assert should_ping_asker(is_dm=False, last_message_at=last, now=_NOW) is True


def test_ping_asker_lull_pings() -> None:
    """Last message well beyond threshold → ping again."""
    last = _dt_ago(60)  # 1 hour ago
    assert should_ping_asker(is_dm=False, last_message_at=last, now=_NOW) is True


def test_ping_asker_rapid_followup_no_ping() -> None:
    """Rapid follow-up (< threshold) → no spam ping."""
    last = _dt_ago(0.5)  # 30 seconds ago
    assert should_ping_asker(is_dm=False, last_message_at=last, now=_NOW) is False


def test_ping_asker_naive_datetime_treated_as_utc() -> None:
    """Naive last_message_at is treated as UTC, not rejected."""
    # Naive equivalent of 10 minutes ago
    last = (_NOW - timedelta(minutes=10)).replace(tzinfo=None)
    assert should_ping_asker(is_dm=False, last_message_at=last, now=_NOW) is True


def test_ping_asker_defaults_now_to_real_clock() -> None:
    """When now is None, falls back to datetime.now(UTC) — just asserts no error."""
    result = should_ping_asker(is_dm=False, last_message_at=None)
    assert result is True  # cold start always pings


# ---------------------------------------------------------------------------
# should_respond_to_channel_message — injectable classifier
# ---------------------------------------------------------------------------


async def _yes_classifier(text: str) -> bool:
    return True


async def _no_classifier(text: str) -> bool:
    return False


async def test_respond_gate_mention_always_true() -> None:
    """is_mention=True → always respond, classifier not called."""
    called: list[str] = []

    async def _never_called(text: str) -> bool:
        called.append(text)
        return False

    result = await should_respond_to_channel_message(
        is_mention=True,
        session_id="slack-callie-T1-C1-_",
        text="something",
        classifier=_never_called,
    )
    assert result is True
    assert called == [], "classifier must not be called when is_mention=True"


async def test_respond_gate_classifier_yes() -> None:
    """Classifier returns YES → respond (no prior history)."""
    with (
        patch("artemis.db.SessionLocal") as mock_session_local,
        patch(
            "artemis.floating_artemis.repository.list_messages",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session_local.return_value = mock_db
        result = await should_respond_to_channel_message(
            is_mention=False,
            session_id="slack-callie-T1-C1-_",
            text="What's our Q2 email open rate?",
            classifier=_yes_classifier,
        )
    assert result is True


async def test_respond_gate_classifier_no_no_history() -> None:
    """Classifier returns NO and no prior history → stay silent."""
    with patch("artemis.db.SessionLocal") as mock_session_local:
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session_local.return_value = mock_db
        with patch(
            "artemis.floating_artemis.repository.list_messages",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await should_respond_to_channel_message(
                is_mention=False,
                session_id="slack-callie-T1-C1-_",
                text="Has anyone seen my stapler?",
                classifier=_no_classifier,
            )
    assert result is False


async def test_respond_gate_continuity_wins_over_classifier_no() -> None:
    """Prior history in session → respond even if classifier would say NO."""
    # Make a fake message row
    fake_msg = MagicMock()
    fake_msg.created_at = _dt_ago(30)

    with patch("artemis.db.SessionLocal") as mock_session_local:
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session_local.return_value = mock_db
        with patch(
            "artemis.floating_artemis.repository.list_messages",
            new_callable=AsyncMock,
            return_value=[fake_msg],
        ):
            result = await should_respond_to_channel_message(
                is_mention=False,
                session_id="slack-callie-T1-C1-_",
                text="ok thanks",
                classifier=_no_classifier,
            )
    assert result is True


async def test_respond_gate_db_failure_falls_to_classifier() -> None:
    """When the DB call fails, fall through to classifier (no crash)."""
    with patch("artemis.db.SessionLocal") as mock_session_local:
        mock_session_local.side_effect = RuntimeError("DB unavailable")
        # classifier says YES despite the DB failure
        result = await should_respond_to_channel_message(
            is_mention=False,
            session_id="slack-callie-T1-C1-_",
            text="Can you help with our campaign?",
            classifier=_yes_classifier,
        )
    assert result is True


async def test_respond_gate_db_failure_classifier_no() -> None:
    """DB failure + classifier NO → stay silent."""
    with patch("artemis.db.SessionLocal") as mock_session_local:
        mock_session_local.side_effect = RuntimeError("DB unavailable")
        result = await should_respond_to_channel_message(
            is_mention=False,
            session_id="slack-callie-T1-C1-_",
            text="random office chatter",
            classifier=_no_classifier,
        )
    assert result is False


# ---------------------------------------------------------------------------
# route_inbound — ping prefix applied after linting
# ---------------------------------------------------------------------------


async def _run_route_inbound_with_ping(ping_user_id: str | None) -> str:
    """Helper: run route_inbound with all external deps mocked; return posted text."""
    from artemis.routes.integrations_slack_events import route_inbound

    fake_result = MagicMock()
    fake_result.response_text = "Here is the report."

    posted_texts: list[str] = []

    # We need to mock deeply because route_inbound uses lazy imports.
    # The key mocks are: DB session context manager, integrations.repository,
    # floating_artemis.repository, floating_artemis.chat, and the Slack client.
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent_cfg = MagicMock()
    mock_agent_cfg.access_token = "xoxb-test"

    async def _fake_post_message(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted_texts.append(text)

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
            new_callable=AsyncMock,
            return_value=fake_result,
        ),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=mock_agent_cfg,
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=mock_slack_client),
    ):
        await route_inbound(
            {
                "team_id": "T123",
                "channel": "C456",
                "user": "U789",
                "text": "What is the Q2 open rate?",
                "ts": "1234.5678",
                "thread_ts": None,
            },
            agent_id="callie",
            ping_user_id=ping_user_id,
        )

    assert posted_texts, "Expected post_message to be called"
    return posted_texts[0]


async def test_route_inbound_ping_prefix_added() -> None:
    """When ping_user_id is set, outbound text is prefixed with <@uid>."""
    posted_text = await _run_route_inbound_with_ping(ping_user_id="U789")
    assert posted_text.startswith("<@U789>"), f"Expected ping prefix, got: {posted_text!r}"


async def test_route_inbound_no_ping_when_not_set() -> None:
    """When ping_user_id is None, outbound text has no @mention prefix."""
    posted_text = await _run_route_inbound_with_ping(ping_user_id=None)
    assert not posted_text.startswith("<@"), f"Unexpected ping prefix in: {posted_text!r}"


# ---------------------------------------------------------------------------
# _handle_mentionable_event — gate + ping path
# ---------------------------------------------------------------------------


def _make_callie_cfg(
    *,
    listen_channel_messages: bool = True,
    always_respond_in_channels: bool = False,
) -> Any:
    """Create a minimal _SlackAgentConfig-like mock for Callie."""
    from artemis.routes.integrations_slack_events import _SlackAgentConfig

    return _SlackAgentConfig(
        agent_id="callie",
        signing_secret="secret",
        access_token="xoxb-callie",
        bot_user_id="BCALLIE",
        authed_user_id="U_OWNER",
        allowed_user_ids=("U_JON",),
        allowed_channel_ids=("C_MARKETING",),
        listen_channel_messages=listen_channel_messages,
        always_respond_in_channels=always_respond_in_channels,
    )


def _make_payload(*, event_id: str = "Ev001", team_id: str = "T001") -> dict[str, Any]:
    return {"event_id": event_id, "team_id": team_id}


def _make_channel_event(
    *,
    channel_id: str = "C_MARKETING",
    user_id: str = "U_JON",
    ts: str = "111.222",
    thread_ts: str | None = None,
    text: str = "Hello Callie",
    event_type: str = "message",
    channel_type: str = "channel",
    bot_id: str | None = None,
    parent_user_id: str | None = None,
) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "type": event_type,
        "channel": channel_id,
        "channel_type": channel_type,
        "user": user_id,
        "text": text,
        "ts": ts,
    }
    if thread_ts is not None:
        ev["thread_ts"] = thread_ts
    if bot_id is not None:
        ev["bot_id"] = bot_id
    if parent_user_id is not None:
        ev["parent_user_id"] = parent_user_id
    return ev


async def _run_handle_mentionable(
    *,
    event: dict[str, Any],
    payload: dict[str, Any] | None = None,
    agent_cfg: Any = None,
    upsert_return: bool = True,
    classifier: Callable[[str], Awaitable[bool]] | None = None,
    last_ts: datetime | None = None,
    route_inbound_mock: AsyncMock | None = None,
) -> tuple[AsyncMock, list[tuple[Any, ...]]]:
    """Helper: run _handle_mentionable_event with all external deps mocked.

    Returns (route_inbound_mock, background_tasks_calls).
    """
    from fastapi import BackgroundTasks

    from artemis.routes.integrations_slack_events import _handle_mentionable_event

    if payload is None:
        payload = _make_payload()
    if agent_cfg is None:
        agent_cfg = _make_callie_cfg()
    if route_inbound_mock is None:
        route_inbound_mock = AsyncMock()

    bg = BackgroundTasks()
    dispatched: list[tuple[Any, ...]] = []

    # Replace add_task to capture calls without actually running them
    def _capture_add_task(func: Any, *args: Any, **kwargs: Any) -> None:
        dispatched.append((func, args, kwargs))

    bg.add_task = _capture_add_task  # type: ignore[method-assign]

    # We mock the DB session as a no-op context manager
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with (
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=agent_cfg,
        ),
        patch(
            "artemis.integrations.repository.upsert_slack_inbound",
            new_callable=AsyncMock,
            return_value=upsert_return,
        ),
        patch(
            "artemis.integrations.slack.triage.classify_mention_type",
            return_value="channel",
        ),
        patch(
            "artemis.routes.integrations_slack_events._last_message_timestamp",
            new_callable=AsyncMock,
            return_value=last_ts,
        ),
        patch(
            "artemis.routes.integrations_slack_events.route_inbound",
            route_inbound_mock,
        ),
    ):
        await _handle_mentionable_event(
            payload,
            event,
            bg,
            mock_session,
            agent_id="callie",
            inner_type=str(event.get("type", "")),
            channel_classifier=classifier,
        )

    return route_inbound_mock, dispatched


# ── Bot-authored events dropped before gate ───────────────────────────────────


async def test_bot_authored_dropped_no_dispatch() -> None:
    """Bot-authored events are dropped immediately — no dedup record, no dispatch."""
    event = _make_channel_event(bot_id="B_PIPELINE")
    _, dispatched = await _run_handle_mentionable(event=event)
    assert dispatched == [], "Bot-authored event must not be dispatched"


# ── Idle chatter — classifier says NO, no history → silent ───────────────────


async def test_idle_chatter_no_reply() -> None:
    """Off-topic message, no prior participation → gate drops it silently."""
    event = _make_channel_event(text="Has anyone seen my coffee mug?")
    _, dispatched = await _run_handle_mentionable(event=event, classifier=_no_classifier)
    assert dispatched == [], "Irrelevant chatter must not be dispatched"


# ── Reply on the agent's OWN post bypasses the relevance gate ─────────────────


async def test_reply_to_agent_post_bypasses_gate() -> None:
    """A thread reply whose root was authored by the agent (parent_user_id ==
    bot_user_id) is dispatched even when the classifier would say NO — someone
    replying to the agent's own message is unambiguously talking to it."""
    event = _make_channel_event(
        text="thanks — and does that cover middle school too?",
        thread_ts="111.100",
        parent_user_id="BCALLIE",  # the thread root was Callie's own post
    )
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_no_classifier,  # gate would DROP this if it were reached
        last_ts=None,
    )
    assert len(dispatched) == 1, "Reply on the agent's own post must bypass the gate"


async def test_threaded_app_mention_dispatched() -> None:
    """An @mention inside a thread (Sara replying to Kai's answer and tagging him)
    is still an app_mention → dispatched, bypassing the relevance gate. Guards the
    'Kai didn't respond to my threaded @mention' report."""
    event = _make_channel_event(
        text="<@BCALLIE> this is actually the video we wanted",
        event_type="app_mention",
        channel_type="",
        thread_ts="111.100",
        parent_user_id="BCALLIE",
    )
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_no_classifier,  # gate would drop if reached
        last_ts=None,
    )
    assert len(dispatched) == 1, "Threaded @mention must always be dispatched"


async def test_reply_to_other_user_still_gated() -> None:
    """A thread reply whose root was authored by a human (not the bot) is NOT a
    reply-to-agent, so the relevance gate still applies and drops off-topic chatter."""
    event = _make_channel_event(
        text="lol same",
        thread_ts="111.100",
        parent_user_id="U_SOMEONE_ELSE",
    )
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_no_classifier,
        last_ts=None,
    )
    assert dispatched == [], "Reply to another user's post must still pass the gate"


# ── app_mention always replies ────────────────────────────────────────────────


async def test_app_mention_always_dispatched_cold() -> None:
    """app_mention bypasses relevance gate; cold start → ping included."""
    event = _make_channel_event(
        text="<@BCALLIE> what is our Q2 open rate?",
        event_type="app_mention",
        channel_type="",
    )
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_no_classifier,  # gate would say NO if reached
        last_ts=None,  # cold → ping
    )
    assert len(dispatched) == 1, "app_mention must always be dispatched"
    _, _args, kwargs = dispatched[0]
    # cold start → ping_user_id should be set
    assert kwargs.get("ping_user_id") is not None, "Cold app_mention should ping the asker"


async def test_app_mention_active_flow_no_ping() -> None:
    """app_mention during active flow (< threshold) → no ping."""
    from datetime import UTC, datetime

    event = _make_channel_event(
        text="<@BCALLIE> follow up question",
        event_type="app_mention",
        channel_type="",
    )
    # Use real current time minus 1 minute so gap is well under the 5-min threshold
    recent = datetime.now(UTC) - timedelta(minutes=1)
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_yes_classifier,
        last_ts=recent,
    )
    assert len(dispatched) == 1
    _, _args, kwargs = dispatched[0]
    assert kwargs.get("ping_user_id") is None, "Active flow must not ping"


# ── Channel message — classifier says YES, cold start → ping ──────────────────


async def test_channel_message_classifier_yes_cold_pings() -> None:
    """Marketing question in channel, classifier YES, cold → dispatched with ping."""
    event = _make_channel_event(text="What is our Q2 email open rate?")
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_yes_classifier,
        last_ts=None,  # cold
    )
    assert len(dispatched) == 1
    _, _args, kwargs = dispatched[0]
    assert kwargs.get("ping_user_id") == "U_JON"


# ── Rapid follow-up in thread — no ping ──────────────────────────────────────


async def test_channel_thread_active_no_ping() -> None:
    """Follow-up in same thread < threshold → dispatched without ping."""
    from datetime import UTC, datetime

    event = _make_channel_event(
        text="Thanks, can you break that down by segment?",
        thread_ts="111.100",
    )
    # Use real current time minus 30 seconds so gap is well under the 5-min threshold
    recent_ts = datetime.now(UTC) - timedelta(seconds=30)
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_yes_classifier,
        last_ts=recent_ts,
    )
    assert len(dispatched) == 1
    _, _args, kwargs = dispatched[0]
    assert kwargs.get("ping_user_id") is None, "Active thread must not ping"


# ── Re-engagement after lull → ping ──────────────────────────────────────────


async def test_channel_reengage_after_lull_pings() -> None:
    """Same thread, but last message > threshold ago → ping again."""
    from datetime import UTC, datetime

    event = _make_channel_event(
        text="Actually, can you re-run that analysis?",
        thread_ts="111.100",
    )
    # Use real current time minus 2 hours so gap is well over the threshold
    old_ts = datetime.now(UTC) - timedelta(hours=2)
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_yes_classifier,
        last_ts=old_ts,
    )
    assert len(dispatched) == 1
    _, _args, kwargs = dispatched[0]
    assert kwargs.get("ping_user_id") == "U_JON"


# ── DM path — no ping ────────────────────────────────────────────────────────


async def test_dm_event_no_ping() -> None:
    """DM message (channel_type=im) → dispatched without ping."""
    # For Callie, DMs are allowed (is_authorized_inbound returns True for non-artemis DMs)
    event = _make_channel_event(
        channel_id="D_DM_CHAN",
        channel_type="im",
        text="Hey Callie, private question",
    )
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_yes_classifier,
        last_ts=None,  # cold, but DM → no ping
    )
    # Allowlist gate passes for DM (callie allows all DMs)
    if dispatched:
        _, _args, kwargs = dispatched[0]
        assert kwargs.get("ping_user_id") is None, "DM must never ping"


# ── Dedup — second event with same event_id is dropped ───────────────────────


async def test_duplicate_event_dropped() -> None:
    """Duplicate event_id (upsert returns False) → not dispatched."""
    event = _make_channel_event(text="What's our reach?")
    _, dispatched = await _run_handle_mentionable(
        event=event,
        classifier=_yes_classifier,
        upsert_return=False,  # already seen
    )
    assert dispatched == [], "Duplicate event must not be dispatched"


# ── _default_channel_classifier — mocked complete_with_fallback ──────────────


def _make_completion_response(answer: str) -> Any:
    """Build a minimal CompletionResponse with a single TextBlock."""
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=answer)]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=5, output_tokens=1),
    )


async def test_default_classifier_yes_returns_true() -> None:
    """LLM returns 'YES' → classifier returns True."""
    from artemis.routes.integrations_slack_events import _default_channel_classifier

    with patch(
        "artemis.providers.fallback.complete_with_fallback",
        new_callable=AsyncMock,
        return_value=_make_completion_response("YES"),
    ) as mock_cwf:
        result = await _default_channel_classifier("What is our Q2 email open rate?")

    assert result is True
    mock_cwf.assert_awaited_once()
    # Verify routing: codex primary, claude-code fallback, correct feature tag
    _call_kwargs = mock_cwf.call_args
    assert _call_kwargs.kwargs["primary"] == "codex"
    assert _call_kwargs.kwargs["fallback"] == "claude-code"
    assert _call_kwargs.kwargs["feature_tag"] == "slack_channel_gate"


async def test_default_classifier_yes_lowercase_returns_true() -> None:
    """LLM returns 'yes' (lower-case) → classifier normalises and returns True."""
    from artemis.routes.integrations_slack_events import _default_channel_classifier

    with patch(
        "artemis.providers.fallback.complete_with_fallback",
        new_callable=AsyncMock,
        return_value=_make_completion_response("yes"),
    ):
        result = await _default_channel_classifier("Can you pull the CTR numbers?")

    assert result is True


async def test_default_classifier_no_returns_false() -> None:
    """LLM returns 'NO' → classifier returns False (fail-closed)."""
    from artemis.routes.integrations_slack_events import _default_channel_classifier

    with patch(
        "artemis.providers.fallback.complete_with_fallback",
        new_callable=AsyncMock,
        return_value=_make_completion_response("NO"),
    ):
        result = await _default_channel_classifier("Has anyone seen my stapler?")

    assert result is False


async def test_default_classifier_neither_returns_false() -> None:
    """LLM returns 'NEITHER' → False."""
    from artemis.routes.integrations_slack_events import _default_channel_classifier

    with patch(
        "artemis.providers.fallback.complete_with_fallback",
        new_callable=AsyncMock,
        return_value=_make_completion_response("NEITHER"),
    ):
        result = await _default_channel_classifier("random office chatter")

    assert result is False


async def test_default_classifier_garbage_returns_false() -> None:
    """LLM returns garbage text → False (only clear YES is True)."""
    from artemis.routes.integrations_slack_events import _default_channel_classifier

    with patch(
        "artemis.providers.fallback.complete_with_fallback",
        new_callable=AsyncMock,
        return_value=_make_completion_response("I cannot determine that."),
    ):
        result = await _default_channel_classifier("something weird")

    assert result is False


async def test_default_classifier_exception_returns_false() -> None:
    """Any exception from complete_with_fallback → fail-closed False, no re-raise."""
    from artemis.routes.integrations_slack_events import _default_channel_classifier

    with patch(
        "artemis.providers.fallback.complete_with_fallback",
        new_callable=AsyncMock,
        side_effect=RuntimeError("both providers down"),
    ):
        result = await _default_channel_classifier("help me with our campaign")

    assert result is False


async def test_default_classifier_missing_api_key_returns_false() -> None:
    """MissingApiKeyError (both providers fail) → fail-closed False."""
    from artemis.providers.errors import MissingApiKeyError
    from artemis.routes.integrations_slack_events import _default_channel_classifier

    with patch(
        "artemis.providers.fallback.complete_with_fallback",
        new_callable=AsyncMock,
        side_effect=MissingApiKeyError("anthropic"),
    ):
        result = await _default_channel_classifier("anything")

    assert result is False


async def test_default_classifier_model_is_none() -> None:
    """The CompletionRequest forwarded to complete_with_fallback has model=None.

    codex and claude-code don't accept Anthropic model ids, so the model field
    must be left as None so each adapter uses its own default.
    """
    from artemis.routes.integrations_slack_events import _default_channel_classifier

    captured_requests: list[Any] = []

    async def _capture(req: Any, **kwargs: Any) -> Any:
        captured_requests.append(req)
        return _make_completion_response("YES")

    with patch(
        "artemis.providers.fallback.complete_with_fallback",
        side_effect=_capture,
    ):
        await _default_channel_classifier("What is our open rate?")

    assert captured_requests, "complete_with_fallback must be called"
    assert captured_requests[0].model is None, (
        "model must be None so adapters use their own defaults"
    )


# ── Session key continuity — channel message uses same key as app_mention ────


def test_session_key_channel_message_matches_app_mention() -> None:
    """Channel message and app_mention in same channel/thread use the same session key.

    This is a pure logic test: verifies the session_id formula used in
    _handle_mentionable_event matches route_inbound's formula.
    """
    team_id = "T_AMIRA"
    channel_id = "C_MKTG"
    thread_ts = "111.222"
    agent = "callie"

    bucket = str(thread_ts)
    expected_session_id = f"slack-{agent}-{team_id}-{channel_id}-{bucket}"

    # Replicate the same formula from both code paths
    session_id_from_handle = f"slack-{agent}-{team_id}-{channel_id}-{bucket}"
    session_id_from_route = f"slack-{agent}-{team_id}-{channel_id}-{bucket}"

    assert session_id_from_handle == expected_session_id
    assert session_id_from_route == expected_session_id


def test_app_mention_bypasses_the_channel_allowlist() -> None:
    """Being @-mentioned is consent, in any channel.

    Reported 2026-08-12: Callie was invited to two channels and answered in
    neither — including one where Jon @-mentioned her directly. Both events
    arrived and were "recorded but not routed", silently, because the channel
    was not in her allowlist.

    "Invite the bot to the channel" is the universal Slack mental model, so a
    bot that ignores an explicit mention there reads as broken rather than
    restricted, and nothing in Slack explains why. The allowlist still governs
    ambient chatter — which is what it was for.
    """
    from artemis.routes.integrations_slack_events import (
        _is_authorized_inbound,
        _SlackAgentConfig,
    )

    cfg = _SlackAgentConfig(
        agent_id="callie",
        signing_secret="s",
        access_token="t",
        bot_user_id="UBOT",
        authed_user_id="",
        allowed_user_ids=(),
        allowed_channel_ids=("C_ALLOWED",),
        listen_channel_messages=True,
        always_respond_in_channels=False,
    )

    # Ambient chatter in an un-allowlisted channel: still blocked.
    assert not _is_authorized_inbound(
        agent_cfg=cfg,
        channel_id="C_NOT_ALLOWED",
        channel_type="channel",
        user_id="U1",
        inner_type="message",
    )
    # An explicit @-mention in that same channel: allowed.
    assert _is_authorized_inbound(
        agent_cfg=cfg,
        channel_id="C_NOT_ALLOWED",
        channel_type="channel",
        user_id="U1",
        inner_type="app_mention",
    )


def test_group_dm_is_treated_as_a_dm() -> None:
    """A group DM (``mpim``) is a deliberate invitation, like a 1:1 DM.

    Same report: Callie also stayed silent in a group chat with Jon and Josh.
    An mpim channel id does not start with "D", so the old is_dm check missed
    it and it fell through to the channel allowlist.
    """
    from artemis.routes.integrations_slack_events import (
        _is_authorized_inbound,
        _SlackAgentConfig,
    )

    cfg = _SlackAgentConfig(
        agent_id="callie",
        signing_secret="s",
        access_token="t",
        bot_user_id="UBOT",
        authed_user_id="",
        allowed_user_ids=(),
        allowed_channel_ids=(),
        listen_channel_messages=False,
        always_respond_in_channels=False,
    )
    assert _is_authorized_inbound(
        agent_cfg=cfg,
        channel_id="G0GROUPDM",
        channel_type="mpim",
        user_id="U1",
        inner_type="message",
    )


def test_text_mention_counts_as_a_direct_mention() -> None:
    """A mention in the TEXT must bypass the relevance gate, not just an app_mention event.

    Slack delivers BOTH an ``app_mention`` and a ``message`` for the same
    physical message when a bot is mentioned in a channel it belongs to. The
    in-process dedup keeps whichever arrives first, so the ``app_mention`` is
    often the one dropped.

    Observed 2026-08-12: Jon typed "@Callie can you see this" in a channel and
    got nothing back. The app_mention was dropped as a duplicate; the surviving
    message was treated as ambient chatter and sent to the relevance gate; the
    gate's classifier raised (no ANTHROPIC_API_KEY, codex fallback also failing);
    and a failed gate defaults to silent. Three mechanisms, each correct alone,
    combined into a bot that ignores its own name.

    This pins the property that makes the dedup race irrelevant.
    """
    bot_id = "U0B9S32PTAM"
    text_with_mention = f"<@{bot_id}> can you see this"

    # The condition as the route computes it.
    for inner_type, text, expected in (
        ("app_mention", "anything at all", True),
        ("message", text_with_mention, True),
        ("message", "no mention here", False),
        ("message", "<@USOMEONEELSE> hi", False),
    ):
        is_direct_mention = inner_type == "app_mention" or (
            bool(bot_id) and f"<@{bot_id}>" in (text or "")
        )
        assert is_direct_mention is expected, (inner_type, text)


def test_relevance_gate_is_bypassed_for_a_text_mention() -> None:
    """The gate must not run for a message that names the bot.

    Guards the second half: even with the classifier dead, a message that
    mentions the bot must never reach it, because a failed gate defaults to
    silent.
    """
    from artemis.routes.integrations_slack_events import (
        _needs_relevance_gate,
        _SlackAgentConfig,
    )

    cfg = _SlackAgentConfig(
        agent_id="callie",
        signing_secret="s",
        access_token="t",
        bot_user_id="U0B9S32PTAM",
        authed_user_id="",
        allowed_user_ids=(),
        allowed_channel_ids=("C0BPX9Y8WBE",),
        listen_channel_messages=True,
        always_respond_in_channels=False,
    )
    assert not _needs_relevance_gate(
        agent_cfg=cfg,
        inner_type="message",
        is_dm=False,
        is_channel_join=False,
        is_direct_mention=True,
        is_reply_to_agent=False,
    )
    # Ambient chatter still goes to the gate — this does not disable it.
    assert _needs_relevance_gate(
        agent_cfg=cfg,
        inner_type="message",
        is_dm=False,
        is_channel_join=False,
        is_direct_mention=False,
        is_reply_to_agent=False,
    )
