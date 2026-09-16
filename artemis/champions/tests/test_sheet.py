"""Tab derivation for the Champions sheet.

The sheet is a view over the database and is rebuilt from scratch every run, so
what matters here is that the derived tabs cannot state something the data does
not support -- particularly that "we never looked" cannot render as "we looked
and it was fine".
"""

from __future__ import annotations

from datetime import UTC, datetime

from artemis.champions.models import ChampionsItem
from artemis.champions.sheet import _needs_decision, _rollup, _themes, _yn


def _item(**kw: object) -> ChampionsItem:
    base: dict[str, object] = {
        "external_id": "d-1",
        "item_type": "discussion",
        "posted_at": datetime(2026, 9, 1, tzinfo=UTC),
        "is_amira_staff": False,
    }
    base.update(kw)
    return ChampionsItem(**base)  # type: ignore[arg-type]


class TestUnknownIsNotFalse:
    def test_blank_for_unknown(self) -> None:
        """An unclassified row must not read as a clean one in the sheet."""
        assert _yn(None) == ""
        assert _yn(False) == "FALSE"
        assert _yn(True) == "TRUE"


class TestRollups:
    def test_counts_items_issues_and_friction(self) -> None:
        items = [
            _item(external_id="a", pod="Texas", product_issue=True),
            _item(external_id="b", pod="Texas", adoption_friction=True),
            _item(external_id="c", pod="Texas"),
            _item(external_id="d", pod="Midwest", product_issue=True),
        ]
        rows = _rollup(items, "pod")
        assert rows[0][:5] == ["Texas", "3", "1", "1", "0"]
        assert rows[1][:5] == ["Midwest", "1", "1", "0", "0"]

    def test_missing_key_becomes_unassigned_not_dropped(self) -> None:
        """An item with no pod still has to be counted somewhere, or the totals
        silently stop adding up to the corpus."""
        rows = _rollup([_item(pod=None)], "pod")
        assert rows[0][0] == "Unassigned" and rows[0][1] == "1"

    def test_most_recent_is_the_latest_not_the_last_seen(self) -> None:
        items = [
            _item(external_id="a", pod="Texas", posted_at=datetime(2026, 9, 9, tzinfo=UTC)),
            _item(external_id="b", pod="Texas", posted_at=datetime(2026, 3, 1, tzinfo=UTC)),
        ]
        assert _rollup(items, "pod")[0][5] == "2026-09-09"


class TestThemes:
    def test_counts_states_and_distinct_districts(self) -> None:
        items = [
            _item(external_id="a", theme="Reports & data", state="TX", district="Alpha ISD"),
            _item(external_id="b", theme="Reports & data", state="GA", district="Alpha ISD"),
            _item(external_id="c", theme="Reports & data", state="TX", district="Beta ISD"),
        ]
        row = _themes(items)[0]
        assert row[0] == "Reports & data" and row[1] == "3"
        assert row[2] == "GA, TX"
        assert row[3] == "2", "districts must be distinct, not a row count"

    def test_unclassified_items_are_not_given_a_theme(self) -> None:
        assert _themes([_item(theme=None)]) == []


class TestNeedsADecision:
    def test_ranked_by_how_many_items_each_domain_costs(self) -> None:
        """The highest-value decision has to be the top row, or the tab is a
        list nobody works through."""
        items = [
            *[
                _item(
                    external_id=f"a{i}",
                    author_email_domain="big.org",
                    pod_resolved_at=datetime.now(UTC),
                )
                for i in range(5)
            ],
            _item(
                external_id="b", author_email_domain="small.org", pod_resolved_at=datetime.now(UTC)
            ),
        ]
        rows = _needs_decision(items)
        assert [r[0] for r in rows] == ["big.org", "small.org"]
        assert rows[0][1] == "5"

    def test_placed_items_are_not_decisions(self) -> None:
        item = _item(
            author_email_domain="rusd.org",
            district="Riverside USD",
            pod_resolved_at=datetime.now(UTC),
        )
        assert _needs_decision([item]) == []

    def test_staff_are_not_decisions(self) -> None:
        item = _item(
            author_email_domain="amiralearning.com",
            is_amira_staff=True,
            pod_resolved_at=datetime.now(UTC),
        )
        assert _needs_decision([item]) == []

    def test_a_domain_never_looked_up_is_not_yet_a_decision(self) -> None:
        """Only a domain we ASKED about and could not place is a human's problem.
        Without this, a run that skipped resolution fills the tab with noise."""
        assert (
            _needs_decision([_item(author_email_domain="unknown.org", pod_resolved_at=None)]) == []
        )
