"""Dated readings, so a district getting worse is visible at all.

The brief computes deviations fresh each run and discards them. "Pinellas raised
product feedback on 100% of calls" is a fact about now; "Pinellas went from 40%
to 100% over six weeks" is the thing worth acting on, and no snapshot can say it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from artemis.integrations.gong.baseline import AccountDeviation
from artemis.integrations.gong.snapshots import SNAPSHOT_CATEGORY, _content, compare


def _dev(**kw: Any) -> AccountDeviation:
    base: dict[str, Any] = {
        "account_name": "A District",
        "calls_considered": 9,
        "elevated_concern": {"Customer concerns": 1.0},
        "elevated_advocacy": {},
    }
    base.update(kw)
    return AccountDeviation(**base)


# ── what gets stored ─────────────────────────────────────────────────────────


def test_the_stored_line_is_counts_and_rates_only() -> None:
    """Rule 4: derived signal, never a word anyone said. It cannot contain one --
    the client has no transcript method -- but the format should make that
    obvious to anyone reading a row."""
    line = _content(_dev(), date(2026, 9, 9))

    assert "2026-09-09" in line
    assert "A District" in line
    assert "Customer concerns 100%" in line
    assert "calls=9" in line


def test_the_line_carries_the_sample_size() -> None:
    """100% of 3 calls and 100% of 30 are different claims."""
    assert "calls=3" in _content(_dev(calls_considered=3), date(2026, 9, 9))


def test_the_category_is_registered_so_it_does_not_decay_as_unknown() -> None:
    """A dated reading is not less true for being old.

    On the 0.95 default a snapshot halves in a fortnight and the trend quietly
    loses its early end, which is the half that makes it a trend.
    """
    from artemis.memory.maintenance import _DECAY_FACTORS, KNOWN_CATEGORIES

    assert SNAPSHOT_CATEGORY in KNOWN_CATEGORIES
    assert _DECAY_FACTORS[SNAPSHOT_CATEGORY] >= 0.99


# ── the trend, which the portfolio baseline cannot see ───────────────────────


def test_a_rising_rate_is_reported_as_worsening() -> None:
    """The comparison the portfolio baseline cannot make: this district to itself."""
    verdict = compare(_dev(elevated_concern={"Customer concerns": 1.0}), {"Customer concerns": 0.4})

    assert verdict.direction == "worsening"
    assert "up from" in verdict.describe()


def test_a_falling_rate_is_reported_as_improving() -> None:
    verdict = compare(_dev(elevated_concern={"Customer concerns": 0.5}), {"Customer concerns": 0.9})

    assert verdict.direction == "improving"
    assert "down from" in verdict.describe()


def test_a_small_move_is_not_a_trend() -> None:
    """On a nine-call window one call joining or leaving flips the number."""
    verdict = compare(
        _dev(elevated_concern={"Customer concerns": 0.95}), {"Customer concerns": 0.90}
    )

    assert verdict.direction == "steady"


def test_the_first_reading_says_so_rather_than_implying_calm() -> None:
    """ "No change" and "nothing to compare against" are different claims."""
    verdict = compare(_dev(), {})

    assert verdict.direction == "no_history"
    assert "nothing to compare" in verdict.describe()


def test_a_tracker_with_no_prior_reading_is_not_called_worsening() -> None:
    """A tracker appearing for the first time has not risen; it has appeared."""
    verdict = compare(
        _dev(elevated_concern={"Customer concerns": 1.0}), {"Objections (tracker)": 0.5}
    )

    assert verdict.direction == "no_history"


def test_a_district_that_stopped_being_flagged_is_improving() -> None:
    verdict = compare(_dev(elevated_concern={}), {"Customer concerns": 0.9})

    assert verdict.direction == "improving"


# ── what is deliberately not stored ──────────────────────────────────────────


def test_only_accounts_with_a_signal_are_written() -> None:
    """Recording "nothing unusual" for 300 districts daily would bury the readings
    that matter in their own noise, and an absent row already means that."""
    import inspect

    from artemis.integrations.gong.snapshots import write_snapshots

    src = inspect.getsource(write_snapshots)
    assert "if not dev.has_signal" in src
    assert "continue" in src


# ── reading a stored line back ───────────────────────────────────────────────


def test_a_stored_line_reads_back_to_the_same_numbers() -> None:
    """`_content` became a wire format the moment anything parsed it. The two
    have to move together, and this is what says so when one of them changes."""
    from artemis.integrations.gong.snapshots import parse_snapshot

    dev = _dev(
        calls_considered=11,
        elevated_concern={"Objections (tracker)": 0.82, "Product feedback": 0.91},
        elevated_advocacy={"Amira Solving Problems": 0.75},
    )

    back = parse_snapshot(_content(dev, date(2026, 9, 9)))

    assert back is not None
    assert back.on == date(2026, 9, 9)
    assert back.account_name == "A District"
    assert back.calls == 11
    # A real tracker name with brackets in it. Splitting on ")" loses this one.
    assert back.concern == {"Objections (tracker)": 0.82, "Product feedback": 0.91}
    assert back.advocacy == {"Amira Solving Problems": 0.75}


def test_an_empty_half_reads_back_as_empty_not_as_a_tracker_called_none() -> None:
    from artemis.integrations.gong.snapshots import parse_snapshot

    back = parse_snapshot(_content(_dev(elevated_advocacy={}), date(2026, 9, 9)))

    assert back is not None
    assert back.advocacy == {}


def test_a_line_this_module_did_not_write_is_skipped_not_raised() -> None:
    """A row from an older writer should cost a comparison, not the brief."""
    from artemis.integrations.gong.snapshots import parse_snapshot

    assert parse_snapshot("some other observation entirely") is None
    assert parse_snapshot("[gong|not-a-date|A District] calls=1 concern=() advocacy=()") is None


# ── which past reading gets compared against ─────────────────────────────────


def test_yesterdays_reading_is_not_a_comparison() -> None:
    """The section scores a 120-day window, so yesterday's reading shares 119
    days of calls with today's. Comparing against it reports "steady" forever."""
    from artemis.integrations.gong.snapshots import select_previous

    rows = ("[gong|2026-09-08|A District] calls=9 concern=(Customer concerns 40%) advocacy=(none)",)

    assert select_previous(rows, before=date(2026, 9, 9)) == {}


def test_the_most_recent_reading_that_is_old_enough_wins() -> None:
    """Rows arrive newest-first; the first eligible one per district is the one
    that makes the comparison most current without making it meaningless."""
    from artemis.integrations.gong.snapshots import select_previous

    rows = (
        "[gong|2026-09-08|A District] calls=9 concern=(Customer concerns 90%) advocacy=(none)",
        "[gong|2026-08-01|A District] calls=9 concern=(Customer concerns 40%) advocacy=(none)",
        "[gong|2026-06-01|A District] calls=9 concern=(Customer concerns 10%) advocacy=(none)",
    )

    found = select_previous(rows, before=date(2026, 9, 9))

    assert found["A District"].on == date(2026, 8, 1)
    assert found["A District"].concern == {"Customer concerns": 0.4}


def test_the_comparison_carries_the_date_it_is_comparing_to() -> None:
    """ "Up from 40%" invites the question. "Up from 40% on 01 Aug" answers it."""
    from artemis.integrations.gong.snapshots import select_previous, trend_for

    rows = ("[gong|2026-08-01|A District] calls=9 concern=(Customer concerns 40%) advocacy=(none)",)
    previous = select_previous(rows, before=date(2026, 9, 9))

    verdict = trend_for(_dev(elevated_concern={"Customer concerns": 1.0}), previous["A District"])

    assert verdict.direction == "worsening"
    assert verdict.since == date(2026, 8, 1)
    assert "01 Aug" in verdict.describe()


def test_a_district_with_no_stored_past_gets_no_history() -> None:
    from artemis.integrations.gong.snapshots import select_previous, trend_for

    previous = select_previous((), before=date(2026, 9, 9))

    verdict = trend_for(_dev(), previous.get("A District"))

    assert verdict.direction == "no_history"


# ── what the brief line actually says ────────────────────────────────────────


def test_the_brief_clause_says_the_direction_and_the_date() -> None:
    from artemis.integrations.gong.snapshots import TrendVerdict

    verdict = TrendVerdict(
        "A District", "worsening", 1.0, 0.4, "Customer concerns", date(2026, 8, 1)
    )

    assert verdict.brief_clause() == " (up from 40% on 01 Aug)"


def test_the_brief_clause_is_silent_when_there_is_nothing_to_add() -> None:
    """A line reading "(no change)" spends attention to say nothing; absence
    already says it."""
    from artemis.integrations.gong.snapshots import TrendVerdict

    assert TrendVerdict("A District", "steady", 0.9, 0.85, "X").brief_clause() == ""
    assert TrendVerdict("A District", "no_history").brief_clause() == ""


def test_the_brief_section_both_reads_and_writes_the_snapshots() -> None:
    """The failure this guards: `write_snapshots` existed, was tested, and was
    called by nothing for a day. A trend store nothing writes to stays empty,
    and one nothing reads is a table nobody looks at."""
    import inspect

    from artemis.integrations.gong import brief_section

    src = inspect.getsource(brief_section.build_gong_section)
    assert "_trends(" in src, "the section must compare against stored readings"
    assert "brief_clause()" in src, "and put the comparison on the line"
    assert "_record(" in src, "and store today's reading for tomorrow"


def test_one_line_passed_as_a_string_is_refused_rather_than_answered_wrong() -> None:
    """`str` satisfies `Iterable[str]`, so this type-checks, iterates characters,
    parses none of them and returns "no history" -- a wrong answer with no error
    anywhere. Two of these tests passed for exactly that reason before the guard.
    """
    import pytest

    from artemis.integrations.gong.snapshots import select_previous

    with pytest.raises(TypeError):
        select_previous(
            "[gong|2026-08-01|A District] calls=9 concern=(X 40%) advocacy=(none)",
            before=date(2026, 9, 9),
        )
