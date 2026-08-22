"""The per-state fan-out stamped answers with the state it ASKED about.

Google News does not honour per-state scoping. Every case below is a REAL row
from the live run on 2026-08-21, taken from ``screentime_signals`` as stored:
of 23 rows, at least 9 were filed under a state the article was not about.

Why this mattered more than ordinary noise: per-state counts are what tell us
whether we are blind somewhere. Mis-attribution inflates exactly the number used
to conclude we have coverage — Georgia looked like it had 12 signals when several
were about Washington and Florida.
"""

from __future__ import annotations

import pytest

from artemis.screentime.national_news import NATIONAL, item_to_finding, resolve_state


@pytest.mark.parametrize(
    ("headline", "query_state", "expected", "confidence"),
    [
        # Correctly attributed — the article names the state we searched for.
        (
            "Amira Learning Selected As Georgia's Only State-Approved Free "
            "Universal Reading Screener",
            "GA",
            "GA",
            "confirmed",
        ),
        (
            "Meet Amira, an AI reading tutor alarming some parents and school "
            "leaders in New Mexico",
            "NM",
            "NM",
            "confirmed",
        ),
        # Was filed under NM. It is a Texas story.
        (
            "Texas Education Agency Renews Amira Learning as Trusted Reading "
            "Assessment for Texas First Graders",
            "NM",
            "TX",
            "reattributed",
        ),
        # Was filed under GA. Bellevue is in Washington, but the text never says
        # so — national is the honest answer, and better than a wrong state.
        (
            "BSD Launches i-Ready Assessment and Personalized Instruction for "
            "K-8 - Bellevue School District",
            "GA",
            NATIONAL,
            "national",
        ),
        # Was filed under GA. Hillsborough County is in Florida; again unnamed.
        (
            "Popular school program i-Ready, used in Hillsborough County, faces "
            "lawsuit over student data",
            "GA",
            NATIONAL,
            "national",
        ),
        # Vendor funding news with no geography at all.
        (
            "Brisk Teaching Raises $6.9M in Funding for AI Education Tool",
            "GA",
            NATIONAL,
            "national",
        ),
        # The ", Ga." place-suffix form is unambiguous and must resolve.
        ("School board in Marietta, Ga. limits classroom AI use", "GA", "GA", "confirmed"),
    ],
)
def test_resolve_state_on_real_rows(
    headline: str, query_state: str, expected: str, confidence: str
) -> None:
    state, conf = resolve_state(headline, query_state)
    assert state == expected
    assert conf == confidence


def test_bare_two_letter_abbreviations_are_not_matched() -> None:
    """ "IN", "OR", "OK", "ME", "HI", "DE" are ordinary words.

    Matching them bare would be worse than the bug it replaces: every sentence
    containing "in" would resolve to Indiana.
    """
    for text in (
        "Districts weigh in on the new reading rules",
        "Teachers say the tool is OK for older students",
        "Parents ask whether the app is safe or harmful",
    ):
        state, conf = resolve_state(text, "GA")
        assert state == NATIONAL, f"{text!r} resolved to {state}"
        assert conf == "national"


def test_several_states_named_and_none_ours_is_ambiguous() -> None:
    state, conf = resolve_state(
        "Amira is required in New Mexico and Idaho and authorized in Texas", "GA"
    )
    assert state == NATIONAL
    assert conf == "ambiguous"


def test_unknown_query_state_degrades_to_national_not_to_a_wrong_state() -> None:
    state, conf = resolve_state("A story with no geography", "ZZ")
    assert state == NATIONAL


def test_finding_records_both_the_query_and_the_verdict() -> None:
    """Provenance for auditing: a run whose rows are mostly reattributed means
    the per-state queries are not scoping, which is a scout bug, not noise."""
    finding = item_to_finding(
        {"title": "Texas Education Agency renews Amira Learning", "summary": "", "link": "x"},
        "NM",
    )
    assert finding is not None
    assert finding["state"] == "TX"
    assert finding["metadata"]["state"] == "TX"
    assert finding["metadata"]["query_state"] == "NM"
    assert finding["metadata"]["state_confidence"] == "reattributed"
