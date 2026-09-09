"""Dated readings, so a district getting worse is visible at all.

The brief computes deviations fresh each run and discards them. "Pinellas raised
product feedback on 100% of calls" is a fact about now; "Pinellas went from 40%
to 100% over six weeks" is the thing worth acting on, and no snapshot can say it.
"""

from __future__ import annotations

from datetime import date

from artemis.integrations.gong.baseline import AccountDeviation
from artemis.integrations.gong.snapshots import SNAPSHOT_CATEGORY, _content, compare


def _dev(**kw: object) -> AccountDeviation:
    base: dict = {
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
