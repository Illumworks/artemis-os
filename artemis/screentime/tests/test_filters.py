"""Unit tests for the real-moves filter, normalization, and dedupe (pure, no DB)."""

from __future__ import annotations

from artemis.screentime.filters import (
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
