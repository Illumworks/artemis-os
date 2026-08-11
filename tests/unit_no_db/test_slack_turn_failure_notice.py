"""Stream 2c — an agent must not go silent when a turn dies.

2026-07-20: a Claude CLI subscription-auth outage 401'd every turn. Sara asked
Kai three questions across 19 hours and got NOTHING back: no answer, no error,
no notice to her or anyone else. route_inbound logged the exception and
returned. Sara wrote "Looks like Kai fell asleep at the wheel here", and Jon
only picked it up by chance a day later and relayed her questions by hand.

Agent-agnostic on purpose: Artemis and Callie fail exactly the same way.
"""

from __future__ import annotations

from typing import Any

import pytest

import artemis.routes.integrations_slack_events as events


@pytest.fixture(autouse=True)
def _clear_throttle() -> Any:
    events._failure_notice_sent_at.clear()
    yield
    events._failure_notice_sent_at.clear()


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def _fake_post(**kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(events, "_post_slack_message", _fake_post)
    return sent


async def _notify(session_id: str = "slack-kai-T1-C1-_") -> None:
    await events._post_turn_failure_notice(
        session_id=session_id,
        normalized_agent="kai",
        team_id="T1",
        channel_id="C1",
        reply_thread_ts=None,
    )


async def test_failure_posts_a_notice(posted: list[dict[str, Any]]) -> None:
    await _notify()
    assert len(posted) == 1
    assert posted[0]["channel_id"] == "C1"


async def test_notice_admits_the_failure_without_inventing_a_cause(
    posted: list[dict[str, Any]],
) -> None:
    """F2 applies here too: 'I can't reach my tools' is the whole message.

    The agent has no visibility into provider health, so naming a cause would be
    the same fabrication as "the search pipeline is missing it".
    """
    await _notify()
    text = posted[0]["outbound_text"].lower()
    assert "can't get to my tools" in text
    assert "not ignoring you" in text
    for invented in ("outage", "api", "auth", "401", "provider", "rate limit", "pipeline"):
        assert invented not in text, f"notice must not name a cause: {invented!r}"


async def test_repeat_failures_in_one_session_are_throttled(
    posted: list[dict[str, Any]],
) -> None:
    """One outage should not produce a wall of identical apologies."""
    for _ in range(4):
        await _notify()
    assert len(posted) == 1


async def test_each_session_gets_its_own_notice(posted: list[dict[str, Any]]) -> None:
    """Throttling is per conversation. A different thread still hears back."""
    await _notify("slack-kai-T1-C1-_")
    await _notify("slack-kai-T1-C1-1786.1")
    assert len(posted) == 2


async def test_notice_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs on an error path. It must never raise or mask the original error."""

    async def _boom(**kwargs: Any) -> None:
        raise RuntimeError("slack down too")

    monkeypatch.setattr(events, "_post_slack_message", _boom)
    await _notify()  # must not raise


def test_notice_respects_agent_style_rules() -> None:
    """Kai's rules (no em dashes, no emojis) apply to anything posted as him."""
    text = events._TURN_FAILURE_NOTICE
    assert "—" not in text and "–" not in text
    assert not any(ord(ch) > 0x2100 for ch in text)
