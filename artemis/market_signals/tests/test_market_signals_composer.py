"""Tests for Callie's combined daily #market-signals brief.

The failure modes these guard against are all ones that would ship looking
correct: an emoji marker the house lint removes, a double post, and a failed post
that silently eats a day of every feed's signals.
"""

from __future__ import annotations

from typing import Any
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

    async def resolver(module_path: str, func_name: str) -> object:
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
    # Must not even BUILD: building marks feed items reported.
    build.assert_not_awaited()


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


# ── Selection: what earns a slot in the campaign section ──────────────────────


def test_the_commonest_story_type_cannot_fill_the_brief() -> None:
    """LEADER_TRANSITION_FORMAL was 86 of ~200 qualified signals over three days.

    Ranked by urgency and district size alone, the first real brief was six
    superintendent hires and IXL's Missouri DOE approval — an actual competitor
    event — fell off the end. Priority puts buying intent first; the per-code cap
    stops any one story type owning the section even so. "Three superintendents
    changed" is one fact, not three.
    """
    from artemis.market_signals import campaign_section as cs

    assert cs._code_priority([{"code": "PROCUREMENT_LITERACY_RFP"}]) < cs._code_priority(
        [{"code": "LEADER_TRANSITION_FORMAL"}]
    )
    assert cs._code_priority([{"code": "VENDOR_APPROVED_LIST"}]) < cs._code_priority(
        [{"code": "LEADER_TRANSITION_FORMAL"}]
    )
    assert cs._MAX_PER_CODE < cs._MAX_SIGNALS, "the cap must actually constrain"


def test_reason_code_parsing_tolerates_every_shape_it_might_meet() -> None:
    """Presentation order must never be the thing that breaks a brief."""
    from artemis.market_signals.campaign_section import _code_priority, _primary_code

    assert _primary_code([{"code": "POLICY_LIT_MANDATE"}]) == "POLICY_LIT_MANDATE"
    assert _primary_code(["POLICY_LIT_MANDATE"]) == "POLICY_LIT_MANDATE"
    assert _primary_code("POLICY_LIT_MANDATE") == "POLICY_LIT_MANDATE"
    junk_values: list[Any] = [None, [], {}, 42, [{"nope": 1}]]
    for junk in junk_values:
        assert _primary_code(junk) == "" or isinstance(_primary_code(junk), str)
        # Unknown/unreadable sorts mid-pack rather than first or vanishing.
        assert _code_priority(junk) == 6


def test_a_state_level_signal_is_not_ranked_last_for_having_no_district() -> None:
    """State mandates and competitor approvals have no district at all.

    An earlier tier-only ranking dropped exactly those, which are often the most
    valuable lines in the brief. Tier must break ties, never decide.
    """
    from artemis.market_signals.campaign_section import _code_priority, _tier_rank

    state_mandate: dict[str, Any] = {
        "district_tier": "",
        "reason_codes": [{"code": "POLICY_LIT_MANDATE"}],
    }
    d1_hire: dict[str, Any] = {
        "district_tier": "D1",
        "reason_codes": [{"code": "LEADER_TRANSITION_FORMAL"}],
    }

    def sort_key(m: dict[str, Any]) -> tuple[int, int]:
        return (_code_priority(m.get("reason_codes")), _tier_rank(m))

    assert sort_key(state_mandate) < sort_key(d1_hire)
