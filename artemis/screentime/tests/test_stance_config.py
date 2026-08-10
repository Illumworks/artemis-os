"""Unit tests for config-driven stance classification (pure, no DB)."""

from __future__ import annotations

import copy

from artemis.screentime.classifier import classify_by_rules
from artemis.screentime.models import (
    STANCE_FAVORABLE,
    STANCE_NEUTRAL,
    STANCE_UNFAVORABLE,
)
from artemis.screentime.stance_config import DEFAULT_STANCE_RULES

RULES = DEFAULT_STANCE_RULES


def test_restriction_with_carveout_is_favorable():
    text = "Bill limits screen time but exempts evidence-based instructional software."
    assert classify_by_rules(text, RULES) == STANCE_FAVORABLE


def test_blanket_restriction_no_carveout_is_unfavorable():
    text = "Policy imposes a blanket ban to minimize screen time with no exceptions."
    assert classify_by_rules(text, RULES) == STANCE_UNFAVORABLE


def test_restriction_action_no_carveout_is_unfavorable():
    """A plain restrictive ACTION on screen time (no carve-out) → unfavorable.

    These are the real stored bills that v1 mis-tagged neutral because they carry
    no "blanket"-set keyword — only an action verb ("prohibited"/"limiting"/"limited").
    """
    for text in (
        "Screen time prohibited for children in preschool and kindergarten",
        "A bill limiting screen time for prekindergarten through fifth grade",
        "Screen-based instruction limited in kindergarten",
    ):
        assert classify_by_rules(text, RULES) == STANCE_UNFAVORABLE, text


def test_restriction_action_with_carveout_flips_favorable():
    """A restrictive action WITH an evidence-based carve-out → favorable."""
    text = (
        "An act limiting screen time for students, but exempting evidence-based "
        "purpose-built instructional tools."
    )
    assert classify_by_rules(text, RULES) == STANCE_FAVORABLE


def test_standards_framework_is_neutral():
    """A standards/framework act that mentions screen time but takes no restrictive
    action and has no carve-out → neutral (no clear direction)."""
    assert classify_by_rules("Student Screen-Time Standards Act", RULES) == STANCE_NEUTRAL


def test_unrelated_text_is_neutral():
    assert classify_by_rules("A bake sale fundraiser at the school.", RULES) == STANCE_NEUTRAL


def test_cellphone_ban_is_neutral_out_of_lane():
    text = "Cellphone ban: no smartphones bell to bell in schools."
    assert classify_by_rules(text, RULES) == STANCE_NEUTRAL


def test_config_change_flips_classification():
    """Proves tunability: redefining a keyword set flips the stance."""
    text = "Bill restricts screen time with an exemption for approved program tools."
    # Default rules → favorable (has carve-out keyword 'exemption' + 'approved program').
    assert classify_by_rules(text, RULES) == STANCE_FAVORABLE

    # Tuned rules: drop the carve-out keywords so the same text reads as blanket.
    tuned = copy.deepcopy(DEFAULT_STANCE_RULES)
    tuned["favorable_keywords"] = []  # nothing counts as a carve-out anymore
    tuned["unfavorable_keywords"] = tuned["unfavorable_keywords"] + ["restricts screen time"]
    assert classify_by_rules(text, tuned) == STANCE_UNFAVORABLE


def test_tunable_action_keyword_flips_borderline():
    """Adding a restriction-action keyword flips a borderline item to unfavorable.

    A novel restriction phrasing not in the default action set reads neutral
    (anchor present, no action) until the config is tuned — proving the
    favorable/unfavorable mapping is data, not code.
    """
    text = "Bill curtailing screen time in early grades."
    # Default: "curtailing" is not a known action verb → anchor-only → neutral.
    assert classify_by_rules(text, RULES) == STANCE_NEUTRAL
    tuned = copy.deepcopy(DEFAULT_STANCE_RULES)
    tuned["restriction_action_keywords"] = tuned["restriction_action_keywords"] + [
        "curtailing screen"
    ]
    assert classify_by_rules(text, tuned) == STANCE_UNFAVORABLE
