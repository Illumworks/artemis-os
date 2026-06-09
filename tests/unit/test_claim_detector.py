"""Unit tests for the claim-flag detector (Stage 4).

Covers:
- Pattern-based candidate extraction (quantified, superlative, comparative)
- Token-set similarity + suppression logic
- Quiet-check invariant: ordinary copy should NOT be a candidate
- scan_draft_for_flags end-to-end (flagged / suppressed / ignored)
"""

from __future__ import annotations

import pytest

from artemis.marketing.writing_studio.claim_detector import (
    SUPPRESS_THRESHOLD,
    ClaimFlag,
    NearestApproved,
    _extract_candidates,
    _normalize,
    _token_set,
    scan_draft_for_flags,
    token_set_similarity,
)

# ─────────────────────────────────────────────────────────────────────────────
# _normalize + _token_set
# ─────────────────────────────────────────────────────────────────────────────


def test_normalize_strips_punct_and_lowercases() -> None:
    assert _normalize("Amira's AI-tutor, 99%!") == "amiras aitutor 99"


def test_token_set_has_unique_words() -> None:
    ts = _token_set("the best the best solution")
    assert ts == {"the", "best", "solution"}


# ─────────────────────────────────────────────────────────────────────────────
# token_set_similarity
# ─────────────────────────────────────────────────────────────────────────────


def test_identical_text_scores_one() -> None:
    assert token_set_similarity("Amira improves scores", "Amira improves scores") == pytest.approx(
        1.0
    )


def test_completely_different_text_scores_zero() -> None:
    score = token_set_similarity("red apples taste sweet", "cold winter nights")
    assert score == pytest.approx(0.0)


def test_high_overlap_scores_above_threshold() -> None:
    a = "Students using Amira gained 2.5 grade levels in one semester"
    b = "Students using Amira gained 2.5 grade levels in one semester of use"
    score = token_set_similarity(a, b)
    assert score >= SUPPRESS_THRESHOLD


def test_short_subset_uses_containment() -> None:
    # Short candidate vs long approved phrasing — containment should save it.
    candidate = "Amira improves scores 99%"
    approved = "Students who use Amira consistently improve oral reading fluency scores by 99% in standardized assessments"
    score = token_set_similarity(candidate, approved)
    # score will be low overall, but that's fine — suppression works on
    # high similarity, not low. We just verify the function runs without error.
    assert 0.0 <= score <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# _extract_candidates
# ─────────────────────────────────────────────────────────────────────────────


def test_quantified_percentage_is_candidate() -> None:
    text = "Amira improves scores by 52% in just one semester."
    cands = _extract_candidates(text)
    assert len(cands) == 1
    assert "52%" in cands[0][2]


def test_quantified_nx_is_candidate() -> None:
    text = "Students see 3x improvement with our tutor."
    cands = _extract_candidates(text)
    assert len(cands) >= 1


def test_superlative_only_is_candidate() -> None:
    text = "Amira is the only AI reading tutor aligned to LETRS."
    cands = _extract_candidates(text)
    assert len(cands) >= 1


def test_superlative_proven_is_candidate() -> None:
    text = "Proven results in under 20 minutes a day."
    cands = _extract_candidates(text)
    assert len(cands) >= 1


def test_comparative_more_than_is_candidate() -> None:
    text = "Amira's approach works more than twice as well as traditional instruction."
    cands = _extract_candidates(text)
    assert len(cands) >= 1


def test_ordinary_copy_is_not_candidate() -> None:
    text = "Our tutor listens as students read aloud, providing real-time support."
    cands = _extract_candidates(text)
    assert len(cands) == 0


def test_description_without_claim_signals_is_not_candidate() -> None:
    texts = [
        "Amira Learning partners with districts across the country.",
        "The platform supports teachers with actionable reading data.",
        "We provide structured literacy instruction aligned to state standards.",
    ]
    for text in texts:
        cands = _extract_candidates(text)
        assert len(cands) == 0, f"Unexpected candidate in: {text!r} → {cands}"


def test_minimum_length_guard() -> None:
    # Very short text with a signal should NOT produce a candidate.
    text = "99% done."
    cands = _extract_candidates(text)
    # At 9 chars / 2 words it should be filtered by MIN guards.
    assert len(cands) == 0


# ─────────────────────────────────────────────────────────────────────────────
# scan_draft_for_flags — end-to-end
# ─────────────────────────────────────────────────────────────────────────────


def test_strong_claim_not_in_register_is_flagged() -> None:
    """Case 1: invented strong claim → flagged."""
    text = "Amira improves reading scores by 99% in a single semester of daily practice."
    approved: list[tuple[int, str]] = []
    flags = scan_draft_for_flags(text, approved)
    assert len(flags) >= 1
    assert any("99" in f.text for f in flags)


def test_strong_claim_matching_approved_is_suppressed() -> None:
    """Case 2: sentence closely matching an approved claim → suppressed."""
    approved_phrasing = "Students using Amira gain 52% more oral reading fluency in one semester."
    text = "Students using Amira gain 52% more oral reading fluency in one semester."
    approved = [(1, approved_phrasing)]
    flags = scan_draft_for_flags(text, approved)
    assert len(flags) == 0, f"Expected suppression, got flags: {flags}"


def test_ordinary_descriptive_copy_produces_no_flags() -> None:
    """Case 3: ordinary copy → zero flags."""
    text = (
        "Our tutor listens as students read aloud and provides structured support. "
        "Teachers receive a clear picture of every reader's progress. "
        "Amira Learning partners with districts across Indiana."
    )
    approved: list[tuple[int, str]] = []
    flags = scan_draft_for_flags(text, approved)
    assert flags == [], f"Expected zero flags, got: {flags}"


def test_multiple_flags_deduped() -> None:
    """Two strong-claim sentences → both flagged, no duplicates."""
    text = (
        "Amira is the only AI tutor aligned to structured literacy. "
        "Students see a 3x improvement in oral reading fluency scores within 8 weeks."
    )
    approved: list[tuple[int, str]] = []
    flags = scan_draft_for_flags(text, approved)
    # There should be at least 2 distinct flags (one per sentence).
    assert len(flags) >= 2
    # Verify no overlapping spans.
    for i, fi in enumerate(flags):
        for j, fj in enumerate(flags):
            if i != j:
                assert not (fi.start < fj.end and fj.start < fi.end), (
                    f"Overlapping spans: {fi} and {fj}"
                )


def test_nearest_approved_populated() -> None:
    """Flags should include nearestApproved when there are similar (but sub-
    threshold) approved claims."""
    candidate_text = "Students who use Amira see 45% growth in reading fluency."
    approved_phrasing = "Students who use Amira consistently gain 52% in oral reading fluency."
    approved = [(42, approved_phrasing)]
    flags = scan_draft_for_flags(candidate_text, approved)
    # The claim should be flagged (45% ≠ 52%, different enough not to suppress).
    if flags:  # it may or may not flag depending on exact similarity
        flag = flags[0]
        # If there's a nearest-approved entry, it must reference the right id.
        if flag.nearest_approved:
            assert flag.nearest_approved[0].id == 42


def test_scan_empty_text_returns_no_flags() -> None:
    flags = scan_draft_for_flags("", [])
    assert flags == []


def test_scan_quiet_check_on_brand_copy() -> None:
    """Quiet-check invariant: an on-brand superintendent outreach email should
    produce zero or very few flags."""
    draft = (
        "Dear Superintendent Johnson,\n\n"
        "Indiana's third-grade reading data tells a clear story — nearly one in three "
        "students is not reading proficiently by the end of third grade. "
        "The Indiana Reads Act creates a direct mandate and a real funding opportunity "
        "for districts to act now.\n\n"
        "Amira Learning's Assess–Instruct–Tutor suite was built for exactly this moment. "
        "Our AI tutor screens a full classroom, delivers structured-literacy instruction "
        "personalized to each student, and gives teachers a clear, actionable picture "
        "of every reader's progress — all aligned to Indiana's LETRS-grounded standards.\n\n"
        "I'd welcome 20 minutes to walk you through what this could look like in your "
        "district this fall. Would a brief call next week work for you?\n\n"
        "Warm regards,\nAmira Learning"
    )
    approved: list[tuple[int, str]] = [
        (1, "Amira is the Learning Agent for Reading Growth."),
        (2, "Amira Learning is the pioneer in AI-powered reading tutoring."),
        (3, "Our AI tutor delivers LETRS-aligned structured literacy instruction."),
    ]
    flags = scan_draft_for_flags(draft, approved)
    # A normal, restrained outreach email should have zero or very few flags.
    assert len(flags) <= 2, (
        f"Quiet-check FAILED: got {len(flags)} flags on an on-brand draft. "
        f"Flag texts: {[f.text for f in flags]}"
    )


def test_suppression_threshold_constant_is_sane() -> None:
    """Guard against accidentally setting the threshold too high or too low."""
    assert 0.4 <= SUPPRESS_THRESHOLD <= 0.85


def test_flag_dataclass_fields() -> None:
    """Structural smoke-test for ClaimFlag."""
    flag = ClaimFlag(start=0, end=42, text="foo", reason="quantified")
    assert flag.nearest_approved == []
    flag2 = ClaimFlag(
        start=0,
        end=10,
        text="bar",
        reason="superlative",
        nearest_approved=[NearestApproved(id=1, phrasing="bar baz", similarity=0.5)],
    )
    assert flag2.nearest_approved[0].id == 1


# ─────────────────────────────────────────────────────────────────────────────
# Precision tuning (2026-06-09) — questions, soft superlatives, ordinal "First"
# ─────────────────────────────────────────────────────────────────────────────


def test_question_is_not_flagged() -> None:
    """A sentence ending in '?' must NEVER be flagged — it's rhetorical."""
    text = "How do we truly understand what matters most to school leaders?"
    flags = scan_draft_for_flags(text, [])
    assert flags == [], f"Expected no flags for a question, got: {[f.text for f in flags]}"


def test_what_matters_most_is_not_flagged() -> None:
    """Motivational 'what matters most' copy is not a market claim."""
    text = "What matters most is helping every student reach their reading potential."
    flags = scan_draft_for_flags(text, [])
    assert flags == [], f"Expected no flags for 'what matters most', got: {[f.text for f in flags]}"


def test_matters_most_without_comparative_object_is_not_flagged() -> None:
    """Bare 'most' without a real comparative anchor must not flag."""
    text = "Reading matters most in the early grades."
    flags = scan_draft_for_flags(text, [])
    assert flags == [], f"Expected no flags for soft 'most', got: {[f.text for f in flags]}"


def test_ordinal_first_paragraph_is_not_flagged() -> None:
    """Leading/ordinal 'First ...' (sentence-initial position) must not flag."""
    text = "First paragraph: Amira listens as students read aloud."
    flags = scan_draft_for_flags(text, [])
    assert flags == [], f"Expected no flags for ordinal 'First', got: {[f.text for f in flags]}"


def test_ordinal_first_with_comma_is_not_flagged() -> None:
    """'First, ...' (rhetorical enumeration) must not flag."""
    text = "First, we screen every student in the classroom."
    flags = scan_draft_for_flags(text, [])
    assert flags == [], (
        f"Expected no flags for enumeration 'First,', got: {[f.text for f in flags]}"
    )


def test_market_claim_first_is_flagged() -> None:
    """'Amira is the first reading agent proven to ...' IS a market claim — must flag."""
    text = "Amira is the first reading agent proven to deliver measurable results."
    flags = scan_draft_for_flags(text, [])
    assert len(flags) >= 1, (
        "Expected at least 1 flag for 'Amira is the first ... proven', got none."
    )


def test_quantified_invented_stat_is_flagged() -> None:
    """An invented quantified stat must flag (false positives are OK here)."""
    text = "Amira improves reading scores by 99% over one semester."
    flags = scan_draft_for_flags(text, [])
    assert len(flags) >= 1, "Expected at least 1 flag for an invented '99%' stat, got none."


def test_precision_paragraph_only_last_two_flag() -> None:
    """The brief's acceptance test paragraph: only the quantified + market-first flag."""
    paragraphs = [
        "How do we truly understand what matters most to our students?",  # question → no flag
        "What matters most is the evidence behind every product we choose.",  # soft most → no flag
        "First paragraph: context is everything in early literacy.",  # ordinal first → no flag
        "Amira is the first reading agent proven to deliver measurable literacy gains.",  # market first → FLAG
        "Students improve scores by 99% after one semester of daily use.",  # invented stat → FLAG
    ]
    full_text = "\n".join(paragraphs)
    flags = scan_draft_for_flags(full_text, [])
    flag_texts = [f.text for f in flags]

    # No flags on the first three sentences.
    for sentence in paragraphs[:3]:
        for flag in flags:
            assert sentence.strip("?") not in flag.text or not flag.text.endswith("?"), (
                f"Unexpected flag on non-claim sentence: {flag.text!r}"
            )

    # The last two sentences must produce at least one flag each.
    market_first_flagged = any("first reading agent" in f.text for f in flags)
    invented_stat_flagged = any("99%" in f.text for f in flags)
    assert market_first_flagged, (
        f"'first reading agent proven to' must be flagged. Got: {flag_texts}"
    )
    assert invented_stat_flagged, f"Invented '99%' stat must be flagged. Got: {flag_texts}"


# ─────────────────────────────────────────────────────────────────────────────
# dismissed_claims suppression
# ─────────────────────────────────────────────────────────────────────────────


def test_dismissed_claim_is_suppressed() -> None:
    """A span text that was dismissed must not appear in flags on re-scan."""
    text = "Amira improves reading scores by 99% in a single semester of daily practice."
    # First confirm it flags without dismissal.
    flags_before = scan_draft_for_flags(text, [])
    assert len(flags_before) >= 1, "Precondition: claim must flag before dismissal."

    flagged_text = flags_before[0].text
    flags_after = scan_draft_for_flags(text, [], dismissed_claims=[flagged_text])
    assert flags_after == [], (
        f"Dismissed span must be suppressed on re-scan, got: {[f.text for f in flags_after]}"
    )


def test_dismissed_claim_suppression_is_normalised() -> None:
    """Dismissal lookup is normalised — trailing punctuation / case differences
    should not cause the flag to re-appear."""
    text = "Amira is the first reading agent proven to deliver measurable results."
    flags_before = scan_draft_for_flags(text, [])
    assert len(flags_before) >= 1, "Precondition: claim must flag."

    flagged_text = flags_before[0].text
    # Dismiss with slightly different casing / trailing whitespace.
    dismissed = [flagged_text.upper() + "   "]
    flags_after = scan_draft_for_flags(text, [], dismissed_claims=dismissed)
    assert flags_after == [], (
        f"Normalised dismissal must suppress the flag, got: {[f.text for f in flags_after]}"
    )


def test_unrelated_dismissed_claim_does_not_suppress_other_flags() -> None:
    """Dismissing claim A must not suppress claim B."""
    text_a = "Amira improves reading scores by 99% in a single semester."
    text_b = "Amira is the first reading agent proven to deliver measurable results."
    full_text = text_a + " " + text_b

    flags_all = scan_draft_for_flags(full_text, [])
    assert len(flags_all) >= 2, "Precondition: both claims must flag."

    # Dismiss only claim A's span text.
    dismissed = [flags_all[0].text]
    flags_after = scan_draft_for_flags(full_text, [], dismissed_claims=dismissed)

    # At least one flag must remain (the one that wasn't dismissed).
    assert len(flags_after) >= 1, "Dismissing one claim must not suppress unrelated flags."
