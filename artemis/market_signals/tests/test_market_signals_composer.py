"""Tests for Callie's combined daily #market-signals brief.

The failure modes these guard against are all ones that would ship looking
correct: an emoji marker the house lint removes, a double post, and a failed post
that silently eats a day of every feed's signals.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from artemis.market_signals import composer


class _FakeSession:
    """Minimal session: records commits/rollbacks, no DB."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, *_a: object, **_k: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_a_quiet_day_posts_nothing() -> None:
    """No section has anything -> no brief. A daily 'nothing today' post trains
    people to stop opening the channel."""
    session = _FakeSession()
    with patch.object(composer, "_resolve_section", new=AsyncMock(return_value=None)):
        assert await composer.build_daily_brief(session) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_one_dead_section_does_not_cost_the_others_their_brief() -> None:
    """A section that raises is omitted; the brief still goes out.

    The contract says sections never raise, but the whole point of a combined
    brief is that three feeds share it -- one feed's bug must not silence the
    other two.
    """
    session = _FakeSession()

    async def exploding(_s: object) -> str | None:
        raise RuntimeError("feed is broken")

    async def fine(_s: object) -> str | None:
        return "something worth reading"

    async def resolver(module_path: str, func_name: str):  # type: ignore[no-untyped-def]
        return exploding if "crisis" in module_path else fine

    with (
        patch.object(composer, "_resolve_section", new=resolver),
        patch.object(composer, "_mention_text", new=AsyncMock(return_value="<@U1>")),
    ):
        body = await composer.build_daily_brief(session)  # type: ignore[arg-type]

    assert body is not None
    assert "something worth reading" in body
    assert "feed is broken" not in body


@pytest.mark.asyncio
async def test_a_failed_post_rolls_back_the_reported_markers() -> None:
    """The bug this exists for: sections mark items reported WHILE building.

    ``write_observation`` leaves the transaction to its caller, so a failed post
    must roll back — otherwise the markers are durable, the brief was never
    delivered, and that day's signals are lost from every future brief while
    looking perfectly handled.
    """
    session = _FakeSession()

    with (
        patch.object(composer, "_reserve_today", new=AsyncMock(return_value=True)),
        patch.object(composer, "_release_today", new=AsyncMock()) as release,
        patch.object(composer, "build_daily_brief", new=AsyncMock(return_value="a real brief")),
        patch(
            "artemis.proactivity.commitments._get_slack_token_for_agent",
            new=AsyncMock(return_value="xoxb-test"),
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient.post_message",
            new=AsyncMock(side_effect=RuntimeError("slack down")),
        ),
    ):
        result = await composer.post_daily_brief(session)  # type: ignore[arg-type]

    assert result["posted"] is False
    assert session.rollbacks == 1, "the reported-markers must be discarded"
    assert session.commits == 0, "nothing may be committed when delivery failed"
    release.assert_awaited_once()  # the day is handed back so a retry is allowed


@pytest.mark.asyncio
async def test_a_second_run_the_same_day_does_not_post_again() -> None:
    """Once-per-day is enforced by the unique constraint, not by the scheduler,
    so a misfire or a manual run cannot double-post."""
    session = _FakeSession()
    with (
        patch.object(composer, "_reserve_today", new=AsyncMock(return_value=False)),
        patch.object(composer, "build_daily_brief", new=AsyncMock()) as build,
    ):
        result = await composer.post_daily_brief(session)  # type: ignore[arg-type]

    assert result == {"posted": False, "reason": "already_reserved"}
    build.assert_not_awaited(), "must not even BUILD, since building marks items reported"


def test_the_hot_marker_survives_the_house_lint() -> None:
    """A 🔥 prefix is stripped by ``lint_agent_text`` (emoji are house style
    violations), which would have made hot and standard signals identical in the
    posted brief while the code looked right. Verified against the real linter.
    """
    from artemis.market_signals import campaign_section
    from artemis.writing_rules.agent_lint import lint_agent_text

    line = "- *Hot* <https://example.com|A headline> (Somewhere)"
    assert "*Hot*" in lint_agent_text(line)
    assert "🔥" not in lint_agent_text("- 🔥 x")
    # And the module must not have regressed to an emoji marker. Checked on the
    # code that ASSIGNS the prefix, not the whole source -- the comment there
    # names the emoji precisely so nobody reintroduces it.
    import inspect

    code_lines = [
        ln
        for ln in inspect.getsource(campaign_section).splitlines()
        if "prefix =" in ln and not ln.strip().startswith("#")
    ]
    assert code_lines, "the prefix assignment moved -- update this test"
    assert all(ln.isascii() for ln in code_lines), f"emoji marker is back: {code_lines}"
