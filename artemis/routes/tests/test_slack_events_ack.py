"""The acknowledgement that tells someone their question was picked up.

Callie's median turn is 29 seconds and her p90 is 76. Slack shows nothing during
that, so "thinking" and "the bot is down" look identical — which is how the
2026-07-20 provider outage stayed invisible for 19 hours while Sara asked Kai
three questions.
"""

from __future__ import annotations

import pytest

from artemis.routes.integrations_slack_events import _ack_seen


@pytest.mark.asyncio
async def test_a_broken_acknowledgement_cannot_break_the_turn(monkeypatch) -> None:
    """It runs before `handle_turn`. An ack that can raise is worse than no ack."""

    async def _boom(*_a: object, **_kw: object) -> object:
        raise RuntimeError("slack config exploded")

    monkeypatch.setattr(
        "artemis.routes.integrations_slack_events._resolve_agent_slack_config", _boom
    )

    # The assertion is that this returns rather than raising.
    await _ack_seen(normalized_agent="callie", team_id="T1", channel_id="C1", message_ts="123.456")


@pytest.mark.asyncio
async def test_no_timestamp_means_nothing_to_react_to(monkeypatch) -> None:
    """Some events carry no ts. React to nothing rather than guessing at one."""
    called = False

    async def _tracker(*_a: object, **_kw: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("should not have been reached")

    monkeypatch.setattr(
        "artemis.routes.integrations_slack_events._resolve_agent_slack_config", _tracker
    )

    await _ack_seen(normalized_agent="callie", team_id="T1", channel_id="C1", message_ts="")

    assert called is False


def test_the_acknowledgement_happens_before_the_turn() -> None:
    """Ordering is the whole point: after `handle_turn` it would arrive with the
    answer and tell nobody anything."""
    import inspect

    from artemis.routes import integrations_slack_events as mod

    src = inspect.getsource(mod.route_inbound)
    assert "_ack_seen" in src
    assert src.index("_ack_seen") < src.index("await handle_turn")
