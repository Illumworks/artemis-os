"""Tests for ARGUS-4 -- the Argus-specific board-minutes relevance predicate.

``is_argus_relevant`` replaces a reuse of the board-minutes SCOUT's
``mapping._is_relevant`` inside ``research._fetch_board_minutes`` (see that
module's docstring and ``board_relevance.py``'s module docstring for the
full rationale and live-data validation).

This suite has three parts:
  1. The exact eight false positives measured on Dallas ISD's live agenda
     on 2026-08-13 -- every one of the four trigger patterns that produced
     them (bare "vendor", bare "reading", bare "instructional materials",
     bare "adsy") must now return False.
  2. Realistic true positives -- named literacy-only vendors, diversified
     vendors correctly paired with a literacy context word, literacy
     curriculum/screening/intervention phrases, and the two genuine
     non-procurement hits found on San Diego Unified and Charleston 01
     while validating this predicate against live data.
  3. Deliberately-excluded borderline cases -- a diversified vendor name
     with NO literacy context word must stay False, because (per
     ``board_relevance.py``'s docstring) fetching the BoardDocs body does
     not reliably disambiguate these on the structured AJAX API path.
"""

from __future__ import annotations

import pytest

from artemis.argus.board_relevance import is_argus_relevant

# ---------------------------------------------------------------------------
# 1. The exact Dallas ISD false positives (2026-08-13) -- must all be False
# ---------------------------------------------------------------------------

_DALLAS_FALSE_POSITIVES = [
    (
        "bare 'reading' -- Bible-reading resolution",
        "Consider and Take Possible Action to Approve the Resolution of the Board of "
        "Trustees of the Dallas Independent School District Regarding Period of Prayer "
        "and Reading of the Bible Under Senate Bill 11",
    ),
    (
        "bare 'adsy' -- subject-agnostic missed-instructional-days waiver",
        "Consider and Take Possible Action to Approve the Submission of Waivers to the "
        "Texas Education Agency (TEA) for Missed Instructional Days for Campuses "
        "Participating in Additional Days School Year (ADSY) (No Financial Impact)",
    ),
    (
        "bare 'vendor' -- Human Capital Management teacher pipeline",
        "Consider and Take Possible Action to Authorize, Negotiate, and Enter into "
        "Agreements with Recommended Pool of Vendors for Resident Teacher and Teacher "
        "of Record Pipeline Services for Human Capital Management",
    ),
    (
        "bare 'instructional materials' -- non-literacy dual-credit purchase",
        "Consider and Take Possible Action to Authorize, Negotiate, and Enter into "
        "Cooperative Agreements for the Purchase of Dual Credit Instructional Materials "
        "for Districtwide Use",
    ),
    (
        "bare 'vendor' -- workers' comp health care program",
        "Consider and Take Possible Action to Authorize, Negotiate, and Enter into an "
        "Agreement with Recommended Vendor for Workers' Compensation 504 Health Care "
        "Program Management for Districtwide Use",
    ),
    (
        "bare 'vendor' -- food-service paper products",
        "Consider and Take Possible Action to Authorize, Negotiate, and Enter into "
        "Agreements with Recommended Pool of Vendors for the Purchase of Food Service "
        "Disposable Paper and Plastic Products for Food and Child Nutrition Services",
    ),
    (
        "bare 'vendor' -- elementary school renovation (Hogg)",
        "Consider and Take Possible Action to Negotiate and Enter into an Agreement with "
        "Recommended Vendor for the Renovation of James S. Hogg Elementary School",
    ),
    (
        "bare 'vendor' -- elementary school renovation (Truett)",
        "Consider and Take Possible Action to Negotiate and Enter into an Agreement with "
        "Recommended Vendor for the Renovation of George W. Truett Elementary School",
    ),
]


@pytest.mark.parametrize(
    "label,text", _DALLAS_FALSE_POSITIVES, ids=[lbl for lbl, _ in _DALLAS_FALSE_POSITIVES]
)
def test_dallas_measured_false_positives_are_excluded(label: str, text: str) -> None:
    assert is_argus_relevant(text) is False, f"{label} should not pass is_argus_relevant"


# ---------------------------------------------------------------------------
# 2. Realistic true positives
# ---------------------------------------------------------------------------

_TRUE_POSITIVES = [
    ("literacy-only vendor (Lexia)", "Approve Resolution to Adopt Lexia Core5 Reading"),
    (
        "literacy-only vendor (Wilson Reading)",
        "Approve Contract with Wilson Reading System for Dyslexia Intervention Services",
    ),
    ("literacy-only vendor (DIBELS)", "Approve Renewal of DIBELS Assessment License"),
    (
        "diversified vendor + context (Amplify + ELA)",
        "Authorize Negotiate and Enter into Agreement with Amplify for ELA Curriculum "
        "Districtwide Use",
    ),
    (
        "diversified vendor + context (i-Ready + reading)",
        "Authorize Agreement with Recommended Vendor for i-Ready Reading Diagnostic Assessment",
    ),
    (
        "diversified vendor + context (HMH Into Reading)",
        "Consider and Take Possible Action to Approve HMH Into Reading Adoption",
    ),
    (
        "curriculum adoption + context",
        "Consider and Take Possible Action to Approve Reading Curriculum Adoption for Grades K-5",
    ),
    (
        "TX policy anchor + context (Reading Academies)",
        "Consider Reading Academies Training for K-3 Teachers per House Bill 3",
    ),
    (
        "screening + context (dyslexia)",
        "Consider and Take Possible Action to Approve Dyslexia Screening Program Renewal",
    ),
    (
        "phrase-only, no vendor (structured literacy)",
        "Approve Structured Literacy Professional Development Contract",
    ),
    (
        "San Diego Unified live hit -- dual language / biliteracy",
        "Dual Language Programs and Seal of Biliteracy Pathways",
    ),
    (
        "Charleston 01 live hit -- early literacy monitoring report",
        "Superintendent's Report - Monitoring Report - Early Literacy, Algebra Readiness, "
        "and Math Proficiency/Growth",
    ),
    (
        "Charleston 01 live hit -- early literacy screener named",
        "Monitoring Report: Interim Goal 1.1- African American & Hispanic PK4 Students "
        "Demonstrating Early Literacy Skills (myIGDIs Sound ID/Rhyming)",
    ),
]


@pytest.mark.parametrize("label,text", _TRUE_POSITIVES, ids=[lbl for lbl, _ in _TRUE_POSITIVES])
def test_realistic_true_positives_are_included(label: str, text: str) -> None:
    assert is_argus_relevant(text) is True, f"{label} should pass is_argus_relevant"


# ---------------------------------------------------------------------------
# 3. Deliberately-excluded borderline cases
# ---------------------------------------------------------------------------


def test_diversified_vendor_without_literacy_context_stays_excluded() -> None:
    """Imagine Learning sells math, ELA, and credit-recovery products under one
    brand. Charlotte-Mecklenburg's real agenda had this exact title, and
    fetching its BoardDocs body added zero disambiguating text (see
    board_relevance.py's module docstring) -- so this predicate does not
    guess, and stays False.
    """
    text = "Recommend Approval of Imagine EdgeEX & On-Demand Tutoring Curriculum Platform Contract"
    assert is_argus_relevant(text) is False


def test_diversified_vendor_paired_with_non_literacy_subject_stays_excluded() -> None:
    text = "Approve Renaissance STAR Math Assessment License Renewal"
    assert is_argus_relevant(text) is False


def test_bare_tutoring_without_literacy_context_stays_excluded() -> None:
    """Tutoring alone (e.g. math tutoring, credit-recovery tutoring) is not
    literacy-specific -- only 'reading tutoring' / 'literacy tutoring' or
    tutoring plus a context word should register."""
    text = "Authorize Agreement for On-Demand Tutoring Platform for Secondary Campuses"
    assert is_argus_relevant(text) is False


def test_bare_curriculum_adoption_without_subject_stays_excluded() -> None:
    text = "Consider and Take Possible Action to Approve Science Curriculum Adoption for Grades 6-8"
    assert is_argus_relevant(text) is False


# ── Source attribution must be earned (2026-08-13) ────────────────────────────


def test_a_tool_that_returned_nothing_cannot_be_credited_as_a_source() -> None:
    """Found live: board_minutes timed out, contributed zero items, and synthesis
    still wrote a decision_makers finding attributed to ``Argus/board_minutes``
    naming a specific board member.

    The finding's ``source`` comes straight from the model, and the only check was
    that it starts with "Argus". ``_build_synthesis_prompt`` skips empty tools
    entirely, so the model is never even told a tool came back empty — it can name
    one freely. A sourced-looking claim from an empty source is worse than an
    unsourced one, because it invites someone to trust it.
    """
    from artemis.argus.research import _parse_synthesis_output

    raw = (
        '{"dimension": "decision_makers", "value": "Board member Jane Doe leads curriculum.", '
        '"source": "board_minutes"}'
    )

    findings = _parse_synthesis_output(raw, "11331", contributing_tools={"news_api"})

    assert len(findings) == 1, "the finding is kept — it is the provenance that was false"
    assert findings[0].source == "Argus", "an unearned tool credit must be stripped"
    assert findings[0].raw_notes.get("unsupported_source_claim") == "Argus/board_minutes", (
        "the discrepancy must stay auditable, not be silently rewritten"
    )


def test_a_tool_that_did_contribute_keeps_its_credit() -> None:
    """The guard must not strip legitimate provenance — that is the whole value
    of the source field."""
    from artemis.argus.research import _parse_synthesis_output

    raw = (
        '{"dimension": "procurement_timing", "value": "RFP RR-250363 closes Sept 10.", '
        '"source": "procurement"}'
    )

    findings = _parse_synthesis_output(
        raw, "11331", contributing_tools={"procurement", "news_api"}
    )

    assert findings[0].source == "Argus/procurement"
    assert "unsupported_source_claim" not in findings[0].raw_notes
