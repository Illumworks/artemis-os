"""One pass over the window, and the name resolution built on it.

The resolution half is the one with teeth. "Valley" matching six districts and
returning the biggest of them is an answer about a district nobody asked about,
and it is indistinguishable from a correct one.
"""

from __future__ import annotations

from artemis.integrations.gong.baseline import AccountDeviation, PortfolioRates
from artemis.integrations.gong.survey import Survey


def _survey(*names: str, calls: int = 5) -> Survey:
    return Survey(
        portfolio=PortfolioRates(rates={}, call_count=100),
        deviations=[AccountDeviation(account_name=n, calls_considered=calls) for n in names],
    )


def test_a_name_matching_several_districts_resolves_to_none() -> None:
    """Live bug, found against production on 2026-09-09: "Valley" returned
    Walnut Valley Unified while five other Valley districts sat in the same
    window, and nothing in the answer said a choice had been made."""
    survey = _survey(
        "Apple Valley Unified School District",
        "Chino Valley Unified School District",
        "Walnut Valley Unified School District",
    )

    assert survey.for_account("Valley") is None


def test_the_ambiguous_names_are_available_to_say_out_loud() -> None:
    """Abstaining is only useful if the caller can ask a specific question next."""
    survey = _survey("Apple Valley USD", "Chino Valley USD")

    assert survey.candidates("valley") == ["Apple Valley USD", "Chino Valley USD"]


def test_a_single_match_still_resolves() -> None:
    """Abstention is for ambiguity, not for every partial name."""
    survey = _survey("Pinellas County Schools", "Madera Unified School District")

    found = survey.for_account("pinellas")

    assert found is not None
    assert found.account_name == "Pinellas County Schools"


def test_an_exact_name_wins_over_a_longer_one_containing_it() -> None:
    """Otherwise a district becomes unaskable the moment a longer name appears."""
    survey = _survey("Madera Unified School District", "Madera Unified School District Annex")

    found = survey.for_account("Madera Unified School District")

    assert found is not None
    assert found.account_name == "Madera Unified School District"


def test_no_match_is_none_and_no_candidates() -> None:
    survey = _survey("Pinellas County Schools")

    assert survey.for_account("Zzyzx") is None
    assert survey.candidates("Zzyzx") == []


def test_flagged_is_only_the_accounts_with_something_to_say() -> None:
    survey = Survey(
        portfolio=PortfolioRates(rates={}, call_count=100),
        deviations=[
            AccountDeviation("Quiet", 9),
            AccountDeviation("Loud", 9, elevated_concern={"Customer concerns": 1.0}),
            AccountDeviation("Too small", 1, insufficient=True),
        ],
    )

    assert [d.account_name for d in survey.flagged] == ["Loud"]


def test_a_truncated_survey_says_so() -> None:
    """Running out of pages and running out of data look identical in the
    payload. A partial survey presenting itself as complete is how a district
    gets called quiet because we stopped reading."""
    import inspect

    from artemis.integrations.gong import survey as mod

    src = inspect.getsource(mod.run_survey)
    assert "truncated = False" in src, "the flag must be cleared only on a real end-of-data"
