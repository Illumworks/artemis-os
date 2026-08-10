"""Unit tests for the Slack message-identity dedup layer.

Covers:
  - Two events with the same client_msg_id but different event_ids → only the
    first dispatches, the second is dropped.
  - Two events sharing the same channel_id + ts (no client_msg_id) → same behaviour.
  - Different messages → both dispatch.
  - Missing client_msg_id falls back to channel_id:ts key.
  - TTL eviction: after the TTL window a key can be seen again.
  - _check_and_set_msg_dedup is atomic / race-safe (concurrent coroutines, only
    one wins).

These tests NEVER touch a live DB, Slack, or Anthropic key.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers shared with test_slack_events_channel_gate
# ---------------------------------------------------------------------------


def _make_callie_cfg() -> Any:
    from artemis.routes.integrations_slack_events import _SlackAgentConfig

    return _SlackAgentConfig(
        agent_id="callie",
        signing_secret="secret",
        access_token="xoxb-callie",
        bot_user_id="BCALLIE",
        authed_user_id="U_OWNER",
        allowed_user_ids=("U_JON",),
        allowed_channel_ids=("C_MARKETING",),
        listen_channel_messages=True,
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
    client_msg_id: str | None = None,
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
    if client_msg_id is not None:
        ev["client_msg_id"] = client_msg_id
    return ev


async def _yes_classifier(text: str) -> bool:
    return True


async def _run_handle_mentionable(
    *,
    event: dict[str, Any],
    payload: dict[str, Any] | None = None,
    agent_cfg: Any = None,
    upsert_return: bool = True,
    classifier: Callable[[str], Awaitable[bool]] | None = None,
) -> list[tuple[Any, ...]]:
    """Run _handle_mentionable_event with all external deps mocked.

    Returns the list of (func, args, kwargs) captured from BackgroundTasks.add_task.
    """
    from fastapi import BackgroundTasks

    from artemis.routes.integrations_slack_events import _handle_mentionable_event

    if payload is None:
        payload = _make_payload()
    if agent_cfg is None:
        agent_cfg = _make_callie_cfg()
    if classifier is None:
        classifier = _yes_classifier

    bg = BackgroundTasks()
    dispatched: list[tuple[Any, ...]] = []

    def _capture_add_task(func: Any, *args: Any, **kwargs: Any) -> None:
        dispatched.append((func, args, kwargs))

    bg.add_task = _capture_add_task  # type: ignore[method-assign]

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
            return_value=None,
        ),
        patch(
            "artemis.routes.integrations_slack_events.route_inbound",
            new_callable=AsyncMock,
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

    return dispatched


# ---------------------------------------------------------------------------
# Helpers for the low-level cache functions
# ---------------------------------------------------------------------------


def _reset_dedup_cache() -> None:
    """Clear the module-level dedup cache between tests."""
    from artemis.routes.integrations_slack_events import _msg_dedup_cache

    _msg_dedup_cache.clear()


# ---------------------------------------------------------------------------
# _check_and_set_msg_dedup — unit tests
# ---------------------------------------------------------------------------


async def test_check_and_set_first_call_returns_true() -> None:
    """First call for a key returns True (this is the first delivery)."""
    _reset_dedup_cache()
    from artemis.routes.integrations_slack_events import _check_and_set_msg_dedup

    result = await _check_and_set_msg_dedup("client_msg_id_abc123")
    assert result is True


async def test_check_and_set_second_call_returns_false() -> None:
    """Second call for the same key within the TTL window returns False (duplicate)."""
    _reset_dedup_cache()
    from artemis.routes.integrations_slack_events import _check_and_set_msg_dedup

    await _check_and_set_msg_dedup("client_msg_id_dup")
    result = await _check_and_set_msg_dedup("client_msg_id_dup")
    assert result is False


async def test_check_and_set_different_keys_both_true() -> None:
    """Different keys each return True on first call."""
    _reset_dedup_cache()
    from artemis.routes.integrations_slack_events import _check_and_set_msg_dedup

    r1 = await _check_and_set_msg_dedup("C001:111.111")
    r2 = await _check_and_set_msg_dedup("C001:222.222")
    assert r1 is True
    assert r2 is True


async def test_ttl_eviction_allows_reuse() -> None:
    """After the TTL expires the key is evicted and accepted again."""
    _reset_dedup_cache()
    import artemis.routes.integrations_slack_events as mod
    from artemis.routes.integrations_slack_events import _check_and_set_msg_dedup

    key = "C_TTL:999.000"
    # First call: accepted.
    assert await _check_and_set_msg_dedup(key) is True

    # Back-date the insertion so the entry looks stale.
    mod._msg_dedup_cache[key] = time.monotonic() - mod._MSG_DEDUP_TTL_SECS - 1.0

    # After eviction a second call should be accepted again.
    assert await _check_and_set_msg_dedup(key) is True


async def test_concurrent_same_key_only_one_wins() -> None:
    """Concurrent coroutines racing on the same key → exactly one True returned."""
    _reset_dedup_cache()
    from artemis.routes.integrations_slack_events import _check_and_set_msg_dedup

    key = "C_RACE:123.456"
    # Launch many coroutines simultaneously
    results = await asyncio.gather(*[_check_and_set_msg_dedup(key) for _ in range(10)])
    trues = [r for r in results if r is True]
    assert len(trues) == 1, f"Expected exactly one winner, got {len(trues)}: {results}"


# ---------------------------------------------------------------------------
# _handle_mentionable_event — message-identity dedup integration
# ---------------------------------------------------------------------------


async def test_same_client_msg_id_second_event_dropped() -> None:
    """Two events with same client_msg_id (different event_ids) → only first dispatches."""
    _reset_dedup_cache()

    shared_client_msg_id = "cm_abc123"

    # First event: app_mention
    ev1 = _make_channel_event(
        event_type="app_mention",
        channel_type="",
        client_msg_id=shared_client_msg_id,
        ts="200.001",
    )
    dispatched1 = await _run_handle_mentionable(
        event=ev1,
        payload=_make_payload(event_id="Ev_MENTION"),
    )
    assert len(dispatched1) == 1, "First event (app_mention) must dispatch"

    # Second event: message — same client_msg_id, different event_id
    ev2 = _make_channel_event(
        event_type="message",
        channel_type="channel",
        client_msg_id=shared_client_msg_id,
        ts="200.001",
    )
    dispatched2 = await _run_handle_mentionable(
        event=ev2,
        payload=_make_payload(event_id="Ev_MESSAGE"),
    )
    assert len(dispatched2) == 0, "Second event (message, same client_msg_id) must be dropped"


async def test_same_channel_ts_no_client_msg_id_second_dropped() -> None:
    """Fallback key (channel:ts): two events sharing channel + ts → only first dispatches."""
    _reset_dedup_cache()

    shared_ts = "300.001"
    channel = "C_MARKETING"

    ev1 = _make_channel_event(event_type="app_mention", channel_type="", ts=shared_ts)
    dispatched1 = await _run_handle_mentionable(
        event=ev1,
        payload=_make_payload(event_id="Ev_A"),
    )
    assert len(dispatched1) == 1

    ev2 = _make_channel_event(event_type="message", channel_type="channel", ts=shared_ts)
    dispatched2 = await _run_handle_mentionable(
        event=ev2,
        payload=_make_payload(event_id="Ev_B"),
    )
    assert len(dispatched2) == 0, "Second event with same channel:ts must be dropped"


async def test_different_messages_both_dispatch() -> None:
    """Two different messages (different ts / client_msg_id) → both dispatch."""
    _reset_dedup_cache()

    ev1 = _make_channel_event(ts="400.001", client_msg_id="cm_first")
    dispatched1 = await _run_handle_mentionable(event=ev1, payload=_make_payload(event_id="Ev_1"))
    assert len(dispatched1) == 1

    ev2 = _make_channel_event(ts="400.002", client_msg_id="cm_second")
    dispatched2 = await _run_handle_mentionable(event=ev2, payload=_make_payload(event_id="Ev_2"))
    assert len(dispatched2) == 1


async def test_missing_client_msg_id_uses_channel_ts_fallback() -> None:
    """No client_msg_id in event → falls back to channel_id:ts key; dedup still works."""
    _reset_dedup_cache()

    ts = "500.001"
    # Both events omit client_msg_id — fallback key = "C_MARKETING:500.001"
    ev1 = _make_channel_event(event_type="app_mention", channel_type="", ts=ts)
    ev2 = _make_channel_event(event_type="message", channel_type="channel", ts=ts)

    d1 = await _run_handle_mentionable(event=ev1, payload=_make_payload(event_id="Ev_X"))
    d2 = await _run_handle_mentionable(event=ev2, payload=_make_payload(event_id="Ev_Y"))

    assert len(d1) == 1, "First event must dispatch"
    assert len(d2) == 0, "Second event with same channel:ts must be deduped"


async def test_event_id_dedup_still_works() -> None:
    """Existing event_id dedup (upsert returns False) still drops without hitting msg cache."""
    _reset_dedup_cache()

    ev = _make_channel_event(ts="600.001", client_msg_id="cm_evt_dedup")
    dispatched = await _run_handle_mentionable(
        event=ev,
        payload=_make_payload(event_id="Ev_DUP"),
        upsert_return=False,  # simulates event_id already seen in DB
    )
    assert dispatched == [], "Event_id dedup must still drop the event"

    # The message-identity cache must NOT have been populated by a dropped-at-event_id event
    from artemis.routes.integrations_slack_events import _msg_dedup_cache

    assert "cm_evt_dedup" not in _msg_dedup_cache, (
        "Event dropped at event_id dedup must not populate the message-identity cache"
    )
