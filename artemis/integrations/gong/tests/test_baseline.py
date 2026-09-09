"""Deciding when a district's calls are unusual rather than merely typical.

Trackers fire constantly: across 639 recent linked calls, `Next steps` on 58%,
`Pricing` 50%, `Objections` 47%, `Customer concerns` 47%. Alerting on a raw hit
would alert on half of all conversations, which is the weather rather than a
warning.
"""

from __future__ import annotations

from artemis.integrations.gong.baseline import (
    MIN_CALLS_FOR_SIGNAL,
    PortfolioRates,
    account_deviation,
    portfolio_rates,
)
from artemis.integrations.gong.client import CallContext


def _call(account: str, trackers: dict[str, int]) -> CallContext:
    return CallContext(
        call_id="c",
        started="2026-05-01T10:00:00Z",
        duration_seconds=600,
        title="Call",
        system="Zoom",
        account_name=account,
        trackers=trackers,
    )


# ── a small sample cannot be unusual ─────────────────────────────────────────


def test_one_call_firing_a_tracker_is_not_a_finding() -> None:
    """A 100% rate on one call is arithmetic, not evidence.

    A false "this account is unhappy" costs more than a missed one: it sends
    someone into a call braced for a problem that is not there.
    """
    dev = account_deviation(
        "A District",
        [_call("A District", {"Customer concerns": 3})],
        PortfolioRates(rates={"Customer concerns": 0.47}, call_count=600),
    )

    assert dev.insufficient
    assert not dev.has_signal
    assert "below the" in dev.describe()


def test_the_floor_is_stated_and_conservative() -> None:
    assert MIN_CALLS_FOR_SIGNAL >= 3


def test_an_insufficient_sample_says_so_rather_than_reporting_calm() -> None:
    """ "Nothing unusual" and "not enough calls to tell" are different claims."""
    dev = account_deviation("A District", [_call("A District", {})], PortfolioRates())

    assert "Not a finding either way" in dev.describe()


# ── the bar rises as the sample shrinks ──────────────────────────────────────


def test_a_small_sample_must_clear_a_higher_bar_than_a_large_one() -> None:
    """The first version used one margin below ten calls and flagged 48% of
    judged accounts. Half the portfolio being unusual means the word has stopped
    meaning anything."""
    from artemis.integrations.gong.baseline import _required_margin

    assert _required_margin(4) > _required_margin(10) > _required_margin(30)


def test_three_of_four_against_the_base_rate_is_not_enough() -> None:
    """72% on four calls happens by chance against a 47% norm."""
    calls = [_call("D", {"Customer concerns": 1}) for _ in range(3)] + [_call("D", {})]
    dev = account_deviation(
        "D", calls, PortfolioRates(rates={"Customer concerns": 0.47}, call_count=600)
    )

    assert not dev.elevated_concern


def test_a_sustained_pattern_over_many_calls_is_flagged() -> None:
    """Idaho DOE: objections on 100% of nine calls, against a 47% norm."""
    calls = [_call("Idaho", {"Objections (tracker)": 2}) for _ in range(9)]
    dev = account_deviation(
        "Idaho", calls, PortfolioRates(rates={"Objections (tracker)": 0.47}, call_count=600)
    )

    assert dev.has_signal
    assert "Objections (tracker)" in dev.elevated_concern


# ── what is and is not scored ────────────────────────────────────────────────


def test_procedural_trackers_are_ignored() -> None:
    """`Next steps` fires on 58% of healthy calls and says nothing about mood."""
    calls = [_call("D", {"Next steps": 1}) for _ in range(10)]
    dev = account_deviation("D", calls, PortfolioRates(rates={"Next steps": 0.58}))

    assert not dev.has_signal


def test_advocacy_is_scored_as_well_as_concern() -> None:
    """The half nobody currently hears about: a district getting value.

    `Amira Solving Problems` elevated is a case-study lead.
    """
    calls = [_call("D", {"Amira Solving Problems": 1}) for _ in range(10)]
    dev = account_deviation(
        "D", calls, PortfolioRates(rates={"Amira Solving Problems": 0.33}, call_count=600)
    )

    assert dev.elevated_advocacy
    assert "positive" in dev.describe()


def test_the_description_never_implies_we_know_what_was_said() -> None:
    calls = [_call("D", {"Customer concerns": 1}) for _ in range(10)]
    dev = account_deviation(
        "D", calls, PortfolioRates(rates={"Customer concerns": 0.40}, call_count=600)
    )

    assert "never what anyone said" in dev.describe()


# ── the portfolio floor ──────────────────────────────────────────────────────


def test_portfolio_rates_ignore_calls_with_no_account() -> None:
    """2,582 imported calls carry no account link; they cannot inform a per-account norm."""
    linked = _call("D", {"Customer concerns": 1})
    unlinked = CallContext(call_id="u", started=None, duration_seconds=1, title=None, system=None)

    rates = portfolio_rates([linked, unlinked])

    assert rates.call_count == 1
    assert rates.rate_for("Customer concerns") == 1.0


def test_an_empty_portfolio_does_not_divide_by_zero() -> None:
    rates = portfolio_rates([])
    assert rates.call_count == 0
    assert rates.rate_for("anything") == 0.0
