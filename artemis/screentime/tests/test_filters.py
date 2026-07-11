"""Unit tests for the real-moves filter, normalization, and dedupe (pure, no DB)."""

from __future__ import annotations

from artemis.screentime.filters import (
    STATUS_VETOED_FAILED,
    CandidateSignal,
    compute_content_hash,
    dedupe,
    is_real_move,
    is_screentime_relevant,
    normalize_finding,
)
from artemis.screentime.stance_config import DEFAULT_STANCE_RULES

RULES = DEFAULT_STANCE_RULES


def _cand(**kw):
    base = dict(
        state="TN",
        title="t",
        summary="s",
        source_type="legislative",
        source_url="http://x",
        status="proposed",
    )
    base.update(kw)
    return CandidateSignal(**base)


def test_real_move_kept_for_bill_with_screentime_topic():
    c = _cand(
        title="HB 123 introduced to limit instructional screen time",
        summary="Bill limits screen time but exempts evidence-based tools.",
        status="proposed",
    )
    assert is_real_move(c, RULES) is True


def test_generic_headline_dropped():
    # status 'news' is not a real move even if topical.
    c = _cand(
        title="What to know about screen time in schools",
        summary="An explainer on screen time limits.",
        status="news",
    )
    assert is_real_move(c, RULES) is False


def test_opinion_piece_dropped_even_if_action_words():
    c = _cand(
        title="Opinion: lawmakers should restrict screen time",
        summary="An editorial about a proposed bill to limit screen time.",
        status="proposed",
    )
    assert is_real_move(c, RULES) is False


def test_cellphone_ban_dropped_out_of_lane():
    c = _cand(
        title="HB 9 passed: cellphone ban bell to bell",
        summary="Bans personal cell phones in school all day.",
        status="passed",
    )
    # No instructional-screen-time hook → not relevant → not a real move.
    assert is_screentime_relevant(c.text, RULES) is False
    assert is_real_move(c, RULES) is False


def test_guidance_status_is_real_move():
    c = _cand(
        title="State DoE guidance on digital learning device time",
        summary="Department guidance recommending limits on device time.",
        source_type="state_doe",
        status="guidance",
    )
    assert is_real_move(c, RULES) is True


def test_normalize_legislative_finding():
    finding = {
        "sourceType": "legiscan",
        "discoveredBy": "legislative_scout",
        "districtId": "STATE_TN",
        "evidence": "An act to limit screen time with an exemption for educational software.",
        "metadata": {"state": "TN", "status_code": 4, "url": "http://leg/tn/hb1"},
    }
    c = normalize_finding(finding)
    assert c is not None
    assert c.state == "TN"
    assert c.source_type == "legislative"
    assert c.status == "passed"  # status_code 4
    assert c.source_url == "http://leg/tn/hb1"


def test_normalize_legislative_vetoed_status_code_is_not_passed():
    # Regression: status_code 5 (VETOED per artemis/scouts/legislative/client.py)
    # used to fall into the `>= 4` bucket and be misread as "passed".
    finding = {
        "sourceType": "legiscan",
        "districtId": "STATE_TN",
        "evidence": "An act to limit screen time.",
        "metadata": {"state": "TN", "status_code": 5, "url": "http://leg/tn/hb2"},
    }
    c = normalize_finding(finding)
    assert c is not None
    assert c.status == STATUS_VETOED_FAILED
    assert c.status != "passed"


def test_normalize_legislative_failed_status_code_is_not_passed():
    # status_code 6 == FAILED — same non-enacted bucket as vetoed.
    finding = {
        "sourceType": "legiscan",
        "districtId": "STATE_TN",
        "evidence": "An act to limit screen time.",
        "metadata": {"state": "TN", "status_code": 6, "url": "http://leg/tn/hb3"},
    }
    c = normalize_finding(finding)
    assert c is not None
    assert c.status == STATUS_VETOED_FAILED


def test_vetoed_failed_bill_is_not_a_real_move():
    # A vetoed/failed bill never became law — it must not count as a "real
    # move" (and therefore never as a "big move" either, since big-move
    # eligibility is a subset of real moves).
    c = _cand(
        title="HB 2 vetoed: screen time limit bill",
        summary="Governor vetoed the instructional screen-time limit bill.",
        status=STATUS_VETOED_FAILED,
    )
    assert is_real_move(c, RULES) is False


def test_normalize_drops_stateless_finding():
    assert normalize_finding({"sourceType": "newsapi", "metadata": {}}) is None


def test_dedupe_collapses_same_content_hash():
    a = _cand(title="Same", source_url="http://u", source_type="legislative")
    b = _cand(title="Same", source_url="http://u", source_type="legislative")
    c = _cand(title="Different", source_url="http://u", source_type="legislative")
    out = dedupe([a, b, c])
    assert len(out) == 2


def test_content_hash_is_stable_and_case_insensitive():
    h1 = compute_content_hash("legislative", "http://U", "Title")
    h2 = compute_content_hash("legislative", "http://u", "title")
    assert h1 == h2
