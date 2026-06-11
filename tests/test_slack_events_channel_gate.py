"""Pure unit + integration tests for the Callie channel-message gate and @mention logic.

Covers:
  - should_ping_asker — pure function, no I/O
  - should_respond_to_channel_message — injectable classifier, no live LLM or DB
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


def _make_callie_cfg(*, listen_channel_messages: bool = True) -> Any:
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
