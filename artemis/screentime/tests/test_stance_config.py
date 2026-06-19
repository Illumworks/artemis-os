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
