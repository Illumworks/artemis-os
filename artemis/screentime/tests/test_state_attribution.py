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


# ── outlet names carry geography, and sometimes lie about it ──────────────────
# Both cases below are real rows from the first full national run (2026-08-21),
# found by reading the output rather than by a test failing.


def test_camelcase_outlet_domain_resolves() -> None:
    """A word boundary cannot see "WyomingNews.com". A negative lookahead can.

    Google News puts the outlet in the title, so the domain is often the only
    geography present. This row was stored as national.
    """
    state, conf = resolve_state(
        "Sheridan County school district talks AI in education - WyomingNews.com", "GA"
    )
    assert state == "WY"
    assert conf == "reattributed"


def test_lookahead_is_case_sensitive_even_though_the_name_is_not() -> None:
    """Regression guard for a bug inside the fix.

    With ``re.IGNORECASE`` applied to the whole pattern, the class ``[a-z]``
    also matches ``A-Z``, so ``(?![a-z])`` rejected the capital N in
    "WyomingNews" and silently restored the behaviour it was added to fix. The
    flag is scoped inline instead.
    """
    # lowercase state name still resolves
    assert resolve_state("texas education agency renews amira learning", "NM")[0] == "TX"
    # a longer lowercase word starting with a state name must NOT resolve
    assert resolve_state("a wyomingite complains about school", "GA")[0] == NATIONAL
    # "Indianapolis" is not Indiana for our purposes
    assert resolve_state("Indianapolis students post reading gains", "GA")[0] == NATIONAL


def test_national_outlet_named_after_a_state_does_not_hijack_attribution() -> None:
    """Every Washington Post story was becoming a Washington-state signal."""
    state, _ = resolve_state(
        "Obama and Trump agree! Switching colleges can improve your life - The Washington Post",
        "GA",
    )
    assert state == NATIONAL


def test_outlet_named_after_the_state_it_covers_is_still_evidence() -> None:
    """The guard must stay narrow: Texas Tribune really is Texas."""
    assert resolve_state("Tribune convenes Texas educators for event", "GA")[0] == "TX"
