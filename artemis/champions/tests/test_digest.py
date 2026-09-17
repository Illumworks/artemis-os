"""Digest composition. Nothing here sends; that is the point of the split.

The format is Hannah's: every post from the window, sorted by state, five
columns. The flag is an annotation on a row that would have been there anyway,
which is what makes cutting the flag rate from 45% to ~2% safe -- nothing leaves
the digest, the pennant just gets rare.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
