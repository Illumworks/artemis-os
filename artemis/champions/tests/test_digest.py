"""Digest composition. Nothing here sends; that is the point of the split.

The format is Hannah's: every post from the window, sorted by state, five
columns. The flag is an annotation on a row that would have been there anyway,
which is what makes cutting the flag rate from 45% to ~2% safe -- nothing leaves
the digest, the pennant just gets rare.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from artemis.champions.digest import (
    Digest,
    _district,
    _sorted_rows,
    _summary_cell,
    render_slack,
    render_text,
)
from artemis.champions.models import ChampionsItem


def _item(**kw: object) -> ChampionsItem:
    base: dict[str, object] = {
        "external_id": "d-1",
        "item_type": "discussion",
        "posted_at": datetime(2026, 9, 10, tzinfo=UTC),
        "is_amira_staff": False,
        "summary": "something happened",
    }
    base.update(kw)
    return ChampionsItem(**base)


def _digest(items: list[ChampionsItem], staff: list[ChampionsItem] | None = None) -> Digest:
    d = Digest(since=datetime(2026, 9, 7, tzinfo=UTC), until=datetime(2026, 9, 14, tzinfo=UTC))
    d.items = items
    d.staff = staff or []
    d.flagged = [i for i in items if i.product_issue]
    d.friction = [i for i in items if i.adoption_friction and not i.product_issue]
    return d


class TestDistrictFallsBackToDomain:
    """Hannah's digest shows the domain always, so a blank cell reads as a
    regression even when it is honest."""

    def test_real_district_when_resolved(self) -> None:
        assert _district(_item(district="Harford County", author_email_domain="hcps.org")) == (
            "Harford County"
        )

    def test_domain_when_unresolved(self) -> None:
        assert _district(_item(author_email_domain="k12.nd.us")) == "k12.nd.us"

    def test_a_shared_domain_is_never_shown_as_one_arbitrary_district(self) -> None:
        item = _item(author_email_domain="k12.nd.us", state="ND", district=None)
        assert _district(item) == "k12.nd.us"


class TestEveryPostAppears:
    def test_unflagged_posts_are_in_the_table(self) -> None:
        """The whole reason the flag change is safe."""
        items = [
            _item(external_id="a", state="CA", summary="praise"),
            _item(external_id="b", state="CA", product_issue=True, summary="a real fault"),
        ]
        body = render_text(_digest(items))
        assert "praise" in body and "a real fault" in body
        assert "2 new items" in body

    def test_slack_carries_the_same_rows_as_the_email(self) -> None:
        items = [
            _item(external_id="a", state="CA", summary="first thing"),
            _item(external_id="b", state="TX", summary="second thing"),
        ]
        d = _digest(items)
        slack = render_slack(d)
        for summary in ("first thing", "second thing"):
            assert summary in slack and summary in render_text(d)


class TestSorting:
    def test_state_then_date(self) -> None:
        items = [
            _item(external_id="a", state="TX", posted_at=datetime(2026, 9, 8, tzinfo=UTC)),
            _item(external_id="b", state="CA", posted_at=datetime(2026, 9, 12, tzinfo=UTC)),
            _item(external_id="c", state="CA", posted_at=datetime(2026, 9, 9, tzinfo=UTC)),
        ]
        assert [i.external_id for i in _sorted_rows(items)] == ["c", "b", "a"]

    def test_unplaced_sorts_last(self) -> None:
        """It is a data gap, not a state; above real states it reads like one."""
        items = [
            _item(external_id="a", state=None),
            _item(external_id="b", state="WY"),
        ]
        assert [i.external_id for i in _sorted_rows(items)] == ["b", "a"]


class TestMarkers:
    def test_flag_is_prefixed_into_the_summary_cell(self) -> None:
        """Slack is narrow; Summary is the column carrying the information."""
        cell = _summary_cell(_item(product_issue=True, summary="it broke"), flag="[!]")
        assert cell.startswith("[!]") and "it broke" in cell

    def test_an_unflagged_post_carries_no_marker(self) -> None:
        assert _summary_cell(_item(summary="all fine")) == "all fine"

    def test_escalation_shows_alongside_the_flag(self) -> None:
        cell = _summary_cell(
            _item(product_issue=True, escalation=True, summary="x"), flag="[!]", esc="[!!]"
        )
        assert "[!!]" in cell and "[!]" in cell


class TestStaffPostsAreSeparate:
    def test_staff_are_listed_below_and_not_in_the_table(self) -> None:
        d = _digest(
            [_item(external_id="a", state="CA", summary="educator post")],
            staff=[_item(external_id="s", is_amira_staff=True, title="Monthly News")],
        )
        body = render_text(d)
        assert "AMIRA TEAM POSTS" in body
        table = body[: body.index("AMIRA TEAM POSTS")]
        assert "educator post" in table and "Monthly News" not in table


class TestSlackBlocks:
    """Slack has no tables, so structure comes from headers, dividers and one
    grouped block per state. Its limits are hard failures, not soft ones."""

    def test_structure_leads_with_the_header_and_the_spreadsheet(self) -> None:
        """Hannah asked for the spreadsheet at the top: it is what people act
        from, and in the footer nobody scrolled to it."""
        from artemis.champions.digest import SHEET_URL, render_slack_blocks

        blocks = render_slack_blocks(_digest([_item(state="CA", summary="a thing")]))
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"
        assert SHEET_URL in str(blocks[1])
        assert blocks[2]["type"] == "context"
        assert blocks[3]["type"] == "divider"
        assert blocks[-1]["type"] == "context"

    def test_every_item_appears_somewhere(self) -> None:
        from artemis.champions.digest import render_slack_blocks

        items = [
            _item(external_id="a", state="CA", summary="first thing"),
            _item(external_id="b", state="TX", summary="second thing"),
        ]
        rendered = str(render_slack_blocks(_digest(items)))
        assert "first thing" in rendered and "second thing" in rendered
        assert "*CA*" in rendered and "*TX*" in rendered

    def test_never_exceeds_slacks_block_limit(self) -> None:
        """Over 50 blocks Slack rejects the whole message, so a busy week must
        degrade to a truncation notice rather than failing to post at all."""
        from artemis.champions.digest import render_slack_blocks

        items = [
            _item(external_id=f"d-{i}", state=f"S{i:02d}", summary=f"item {i}") for i in range(120)
        ]
        blocks = render_slack_blocks(_digest(items))
        assert len(blocks) <= 50
        assert "truncated" in str(blocks)

    def test_section_text_stays_under_slacks_character_cap(self) -> None:
        from artemis.champions.digest import render_slack_blocks

        items = [_item(external_id=f"d-{i}", state="CA", summary="x" * 200) for i in range(40)]
        for block in render_slack_blocks(_digest(items)):
            text = block.get("text")
            if isinstance(text, dict):
                assert len(str(text.get("text", ""))) <= 3000


class TestRecipients:
    """The list is recorded in code; sending to it still has to be deliberate."""

    def test_the_confirmed_list(self) -> None:
        from artemis.champions.deliver import DIGEST_RECIPIENTS

        assert DIGEST_RECIPIENTS == (
            "success@amiralearning.com",
            "jaclyn.wright@amiralearning.com",
            "amy.scholz@amiralearning.com",
            "hannah.slater@amiralearning.com",
        )

    def test_angela_is_not_on_it(self) -> None:
        """Removed 2026-09-17, leaving the company."""
        from artemis.champions.deliver import DIGEST_RECIPIENTS

        assert not any("angela" in r for r in DIGEST_RECIPIENTS)

    @pytest.mark.asyncio
    async def test_send_email_still_refuses_an_empty_recipient_list(self) -> None:
        """Recording who should receive the digest must not make it possible to
        send to them by forgetting an argument."""
        from artemis.champions.deliver import send_email

        with pytest.raises(ValueError, match="explicit recipients"):
            await send_email(None, _digest([_item()]), to=[])  # type: ignore[arg-type]
