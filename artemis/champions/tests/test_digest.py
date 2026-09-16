"""Digest composition. Nothing here sends; that is the point of the split."""

from __future__ import annotations

from datetime import UTC, datetime

from artemis.champions.digest import Digest, _by_state, _where, render_slack, render_text
from artemis.champions.models import ChampionsItem


def _item(**kw: object) -> ChampionsItem:
    base: dict[str, object] = {
        "external_id": "d-1",
        "item_type": "discussion",
        "posted_at": datetime(2026, 9, 1, tzinfo=UTC),
        "is_amira_staff": False,
        "summary": "something happened",
    }
    base.update(kw)
    return ChampionsItem(**base)


def _digest(items: list[ChampionsItem]) -> Digest:
    d = Digest(since=datetime(2026, 8, 17, tzinfo=UTC), until=datetime(2026, 9, 16, tzinfo=UTC))
    d.items = items
    d.flagged = [i for i in items if i.product_issue]
    d.friction = [i for i in items if i.adoption_friction and not i.product_issue]
    return d


class TestNamingWhere:
    def test_district_and_state_when_known(self) -> None:
        assert _where(_item(district="Harford County", state="MD")) == "Harford County (MD)"

    def test_unplaced_is_stated_not_hidden(self) -> None:
        """A reader must be able to tell "we do not know" from "no district"."""
        assert _where(_item(author_email_domain="k12.nd.us")) == "unplaced — k12.nd.us"


class TestOrdering:
    def test_unplaced_sorts_last_in_the_state_table(self) -> None:
        """It is a data gap, not a state; above real states it reads as one."""
        items = [
            _item(external_id="a", state="MD"),
            _item(external_id="b", state=None),
            _item(external_id="c", state=None),
            _item(external_id="d", state=None),
        ]
        assert [name for name, _ in _by_state(items)] == ["MD", "Unplaced"]


class TestTheFlaggedItemsComeFirst:
    def test_product_issues_precede_the_counts(self) -> None:
        """A digest that opens with totals trains people to skim past the part
        that needed them."""
        body = render_text(_digest([_item(product_issue=True, district="Alpha ISD", state="TX")]))
        assert body.index("PRODUCT ISSUES") < body.index("BY STATE")

    def test_no_issues_says_so_rather_than_omitting_the_section(self) -> None:
        """A missing section is indistinguishable from a broken run."""
        body = render_text(_digest([_item()]))
        assert "PRODUCT ISSUES" in body and "None this period." in body
        assert "none this period" in render_slack(_digest([_item()])).lower()


class TestAdoptionFrictionIsNotFlagged:
    def test_friction_is_listed_separately_and_unmarked(self) -> None:
        items = [
            _item(external_id="a", product_issue=True, district="Alpha ISD"),
            _item(external_id="b", adoption_friction=True, district="Beta ISD"),
        ]
        body = render_text(_digest(items))
        assert "ADOPTION FRICTION" in body
        assert body.index("PRODUCT ISSUES") < body.index("ADOPTION FRICTION")
        # Beta is in the friction list, and must not appear under the flag.
        flagged_block = body[body.index("PRODUCT ISSUES") : body.index("ADOPTION FRICTION")]
        assert "Beta ISD" not in flagged_block

    def test_an_item_that_is_both_counts_only_as_a_product_issue(self) -> None:
        d = _digest([_item(product_issue=True, adoption_friction=True, district="Alpha ISD")])
        assert len(d.flagged) == 1 and d.friction == []
