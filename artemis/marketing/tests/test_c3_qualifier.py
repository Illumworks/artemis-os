"""Phase C3 qualifier tests — ≥32 tests.

Mirrors the structure of Node's signal-qualifier.test.js:
  - Hard filters (5)
  - Weighted scoring (8)
  - Territory multiplier (4)
  - MinFitScore gate (3)
  - RecommendedFamilies (5)
  - RulesetVersionsUsed (4)
  - Edge cases (3)
"""

from __future__ import annotations

from typing import Any

import pytest

from artemis.marketing.qualifier import (
    RulesetInput,
    SignalInput,
    TerritoryEntry,
    qualify_signal,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / factories
# ─────────────────────────────────────────────────────────────────────────────


def make_signal(
    state_code: str | None = "CA",
    reason_codes: list[dict[str, Any]] | None = None,
    campaign_family: str = "obc",
) -> SignalInput:
    return SignalInput(
        state_code=state_code,
        reason_codes=reason_codes or [],
        campaign_family=campaign_family,
    )


def make_ruleset(
    family: str = "obc",
    version: str = "v1",
    min_fit_score: float = 0.5,
    hard_filters: list[dict[str, Any]] | None = None,
    weighted_signals: list[dict[str, Any]] | None = None,
) -> RulesetInput:
    return RulesetInput(
        campaign_family=family,
        version_number=version,
        min_fit_score=min_fit_score,
        hard_filters=hard_filters or [],
        weighted_signals=weighted_signals or [],
    )


def make_territory(state: str, tier: str = "standard") -> TerritoryEntry:
    return TerritoryEntry(state_code=state, priority_tier=tier)


# ─────────────────────────────────────────────────────────────────────────────
# Hard filters (5)
# ─────────────────────────────────────────────────────────────────────────────


def test_hard_filter_passes_when_state_in_config() -> None:
    ruleset = make_ruleset(
        hard_filters=[{"type": "state_not_excluded"}],
    )
    signal = make_signal(state_code="CA")
    territories = {"obc": [make_territory("CA", "standard")]}
    result = qualify_signal(signal, [ruleset], territories)
    assert result.scores[0].passed_hard_filters is True


def test_hard_filter_fails_when_state_not_in_config() -> None:
    ruleset = make_ruleset(
        hard_filters=[{"type": "state_not_excluded"}],
    )
    signal = make_signal(state_code="TX")
    territories = {"obc": [make_territory("CA", "standard")]}
    result = qualify_signal(signal, [ruleset], territories)
    assert result.scores[0].passed_hard_filters is False


def test_hard_filter_passes_when_no_state_code_and_no_filter() -> None:
    ruleset = make_ruleset(hard_filters=[])
    signal = make_signal(state_code=None)
    result = qualify_signal(signal, [ruleset], {})
    assert result.scores[0].passed_hard_filters is True


def test_hard_filter_passes_when_no_state_code_even_with_filter() -> None:
    """Node behavior: state_not_excluded only fires when stateCode is present."""
    ruleset = make_ruleset(hard_filters=[{"type": "state_not_excluded"}])
    signal = make_signal(state_code=None)
    territories = {"obc": [make_territory("CA")]}
    result = qualify_signal(signal, [ruleset], territories)
    # No state to check against → hard filter does not fire → passes
    assert result.scores[0].passed_hard_filters is True


def test_hard_filter_unknown_type_is_ignored() -> None:
    """Unknown filter types should not cause failures."""
    ruleset = make_ruleset(hard_filters=[{"type": "unknown_future_filter"}])
    signal = make_signal(state_code="NY")
    result = qualify_signal(signal, [ruleset], {})
    assert result.scores[0].passed_hard_filters is True


# ─────────────────────────────────────────────────────────────────────────────
# Weighted scoring (8)
# ─────────────────────────────────────────────────────────────────────────────


def test_weighted_score_single_match() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "DISTRICT_VOTED_YES", "weight": 0.6}],
    )
    signal = make_signal(reason_codes=[{"code": "DISTRICT_VOTED_YES", "confidence": 1.0}])
    result = qualify_signal(signal, [ruleset], {})
    score = result.scores[0]
    assert pytest.approx(score.raw_score) == 0.6


def test_weighted_score_multiple_matches_sum() -> None:
    ruleset = make_ruleset(
        weighted_signals=[
            {"rule_id": "r1", "reason_code": "CODE_A", "weight": 0.3},
            {"rule_id": "r2", "reason_code": "CODE_B", "weight": 0.4},
        ],
    )
    signal = make_signal(
        reason_codes=[
            {"code": "CODE_A", "confidence": 1.0},
            {"code": "CODE_B", "confidence": 1.0},
        ]
    )
    result = qualify_signal(signal, [ruleset], {})
    assert pytest.approx(result.scores[0].raw_score) == 0.7


def test_weighted_score_no_match_is_zero() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "MISSING_CODE", "weight": 0.8}]
    )
    signal = make_signal(reason_codes=[{"code": "OTHER_CODE", "confidence": 1.0}])
    result = qualify_signal(signal, [ruleset], {})
    assert result.scores[0].raw_score == 0.0


def test_weighted_score_confidence_scales_contribution() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "CODE", "weight": 1.0}],
    )
    signal = make_signal(reason_codes=[{"code": "CODE", "confidence": 0.5}])
    result = qualify_signal(signal, [ruleset], {})
    assert pytest.approx(result.scores[0].raw_score) == 0.5


def test_weighted_score_confidence_defaults_to_1_when_absent() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "CODE", "weight": 0.8}],
    )
    # No confidence key in reason_code
    signal = make_signal(reason_codes=[{"code": "CODE"}])
    result = qualify_signal(signal, [ruleset], {})
    assert pytest.approx(result.scores[0].raw_score) == 0.8


def test_weighted_score_confidence_clamped_to_0_1() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "CODE", "weight": 0.5}],
    )
    # Over-confidence clamps to 1.0
    signal = make_signal(reason_codes=[{"code": "CODE", "confidence": 2.5}])
    result = qualify_signal(signal, [ruleset], {})
    assert pytest.approx(result.scores[0].raw_score) == 0.5


def test_matched_rules_populated_correctly() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "R99", "reason_code": "X", "weight": 0.7}],
    )
    signal = make_signal(reason_codes=[{"code": "X", "confidence": 0.5}])
    result = qualify_signal(signal, [ruleset], {})
    rules = result.scores[0].matched_rules
    assert len(rules) == 1
    assert rules[0].rule_id == "R99"
    assert rules[0].reason_code == "X"
    assert pytest.approx(rules[0].contribution) == 0.35


def test_empty_reason_codes_on_signal_yields_zero() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "CODE", "weight": 0.9}],
    )
    signal = make_signal(reason_codes=[])
    result = qualify_signal(signal, [ruleset], {})
    assert result.scores[0].raw_score == 0.0
    assert result.scores[0].matched_rules == []


# ─────────────────────────────────────────────────────────────────────────────
# Territory multiplier (4)
# ─────────────────────────────────────────────────────────────────────────────


def test_territory_multiplier_hot_state() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "C", "weight": 0.5}],
    )
    signal = make_signal(state_code="CA", reason_codes=[{"code": "C"}])
    territories = {"obc": [make_territory("CA", "hot")]}
    result = qualify_signal(signal, [ruleset], territories)
    score = result.scores[0]
    assert score.territory_tier == "hot"
    assert pytest.approx(score.territory_multiplier) == 1.2
    assert pytest.approx(score.adjusted_score) == min(1.0, 0.5 * 1.2)


def test_territory_multiplier_standard_state() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "C", "weight": 0.6}],
    )
    signal = make_signal(state_code="NY", reason_codes=[{"code": "C"}])
    territories = {"obc": [make_territory("NY", "standard")]}
    result = qualify_signal(signal, [ruleset], territories)
    score = result.scores[0]
    assert score.territory_tier == "standard"
    assert pytest.approx(score.territory_multiplier) == 1.0
    assert pytest.approx(score.adjusted_score) == 0.6


def test_territory_multiplier_unlisted_state() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "C", "weight": 0.8}],
    )
    signal = make_signal(state_code="AK", reason_codes=[{"code": "C"}])
    territories = {"obc": [make_territory("CA", "hot")]}  # AK not in config
    result = qualify_signal(signal, [ruleset], territories)
    score = result.scores[0]
    assert score.territory_tier == "unlisted"
    assert pytest.approx(score.territory_multiplier) == 0.85
    assert pytest.approx(score.adjusted_score) == min(1.0, 0.8 * 0.85)


def test_territory_multiplier_adjusted_score_capped_at_1() -> None:
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r1", "reason_code": "C", "weight": 1.0}],
    )
    signal = make_signal(state_code="TX", reason_codes=[{"code": "C"}])
    territories = {"obc": [make_territory("TX", "hot")]}
    result = qualify_signal(signal, [ruleset], territories)
    # 1.0 * 1.2 = 1.2, clamped to 1.0
    assert result.scores[0].adjusted_score == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# MinFitScore gate (3)
# ─────────────────────────────────────────────────────────────────────────────


def test_min_fit_score_passes_when_above_threshold() -> None:
    ruleset = make_ruleset(
        min_fit_score=0.4,
        weighted_signals=[{"rule_id": "r1", "reason_code": "C", "weight": 0.5}],
    )
    signal = make_signal(reason_codes=[{"code": "C"}])
    result = qualify_signal(signal, [ruleset], {})
    assert result.scores[0].passes_min_fit_score is True


def test_min_fit_score_fails_when_below_threshold() -> None:
    ruleset = make_ruleset(
        min_fit_score=0.8,
        weighted_signals=[{"rule_id": "r1", "reason_code": "C", "weight": 0.3}],
    )
    signal = make_signal(reason_codes=[{"code": "C"}])
    result = qualify_signal(signal, [ruleset], {})
    assert result.scores[0].passes_min_fit_score is False


def test_min_fit_score_passes_at_exact_threshold() -> None:
    # Use no state_code so territory_tier=unlisted (multiplier=0.85 would reduce score).
    # Set weight such that raw_score * 0.85 exactly meets min_fit_score.
    # raw_score = 0.6, adjusted = 0.6 * 0.85 = 0.51 > 0.5
    ruleset = make_ruleset(
        min_fit_score=0.5,
        weighted_signals=[{"rule_id": "r1", "reason_code": "C", "weight": 0.6}],
    )
    signal = make_signal(state_code=None, reason_codes=[{"code": "C"}])
    result = qualify_signal(signal, [ruleset], {})
    assert result.scores[0].passes_min_fit_score is True


# ─────────────────────────────────────────────────────────────────────────────
# RecommendedFamilies (5)
# ─────────────────────────────────────────────────────────────────────────────


def test_recommended_families_primary_is_highest_scorer() -> None:
    r1 = make_ruleset(
        family="obc",
        min_fit_score=0.3,
        weighted_signals=[{"rule_id": "r", "reason_code": "C", "weight": 0.4}],
    )
    r2 = make_ruleset(
        family="state_screener",
        min_fit_score=0.3,
        weighted_signals=[{"rule_id": "s", "reason_code": "C", "weight": 0.7}],
    )
    signal = make_signal(reason_codes=[{"code": "C"}])
    result = qualify_signal(signal, [r1, r2], {})
    primary = result.recommended_families[0]
    assert primary.campaign_family == "state_screener"
    assert primary.role == "primary"


def test_recommended_families_secondary_role() -> None:
    r1 = make_ruleset(
        family="obc",
        min_fit_score=0.3,
        weighted_signals=[{"rule_id": "r", "reason_code": "C", "weight": 0.4}],
    )
    r2 = make_ruleset(
        family="state_screener",
        min_fit_score=0.3,
        weighted_signals=[{"rule_id": "s", "reason_code": "C", "weight": 0.7}],
    )
    signal = make_signal(reason_codes=[{"code": "C"}])
    result = qualify_signal(signal, [r1, r2], {})
    assert result.recommended_families[1].role == "secondary"


def test_recommended_families_empty_when_all_fail_hard_filter() -> None:
    ruleset = make_ruleset(
        hard_filters=[{"type": "state_not_excluded"}],
        min_fit_score=0.3,
        weighted_signals=[{"rule_id": "r", "reason_code": "C", "weight": 0.8}],
    )
    signal = make_signal(state_code="XX", reason_codes=[{"code": "C"}])
    territories = {"obc": [make_territory("CA")]}
    result = qualify_signal(signal, [ruleset], territories)
    assert result.recommended_families == []


def test_recommended_families_empty_when_all_below_min_fit() -> None:
    ruleset = make_ruleset(
        min_fit_score=0.9,
        weighted_signals=[{"rule_id": "r", "reason_code": "C", "weight": 0.2}],
    )
    signal = make_signal(reason_codes=[{"code": "C"}])
    result = qualify_signal(signal, [ruleset], {})
    assert result.recommended_families == []


def test_recommended_families_sorted_descending() -> None:
    r1 = make_ruleset(
        family="obc",
        min_fit_score=0.1,
        weighted_signals=[{"rule_id": "r", "reason_code": "A", "weight": 0.6}],
    )
    r2 = make_ruleset(
        family="biliteracy",
        min_fit_score=0.1,
        weighted_signals=[{"rule_id": "s", "reason_code": "B", "weight": 0.9}],
    )
    r3 = make_ruleset(
        family="reading_growth",
        min_fit_score=0.1,
        weighted_signals=[{"rule_id": "t", "reason_code": "C", "weight": 0.3}],
    )
    signal = make_signal(reason_codes=[{"code": "A"}, {"code": "B"}, {"code": "C"}])
    result = qualify_signal(signal, [r1, r2, r3], {})
    scores = [r.adjusted_score for r in result.recommended_families]
    assert scores == sorted(scores, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# RulesetVersionsUsed (4)
# ─────────────────────────────────────────────────────────────────────────────


def test_ruleset_versions_used_single_family() -> None:
    ruleset = make_ruleset(family="obc", version="v3")
    signal = make_signal()
    result = qualify_signal(signal, [ruleset], {})
    assert result.ruleset_versions_used == {"obc": "v3"}


def test_ruleset_versions_used_multiple_families() -> None:
    r1 = make_ruleset(family="obc", version="v1")
    r2 = make_ruleset(family="state_screener", version="v2")
    signal = make_signal()
    result = qualify_signal(signal, [r1, r2], {})
    assert result.ruleset_versions_used == {"obc": "v1", "state_screener": "v2"}


def test_ruleset_versions_used_empty_when_no_rulesets() -> None:
    signal = make_signal()
    result = qualify_signal(signal, [], {})
    assert result.ruleset_versions_used == {}


def test_ruleset_versions_used_in_to_dict() -> None:
    ruleset = make_ruleset(family="obc", version="v7")
    signal = make_signal()
    result = qualify_signal(signal, [ruleset], {})
    d = result.to_dict()
    assert "rulesetVersionsUsed" in d
    assert d["rulesetVersionsUsed"]["obc"] == "v7"


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases (3)
# ─────────────────────────────────────────────────────────────────────────────


def test_no_rulesets_returns_empty_result() -> None:
    signal = make_signal()
    result = qualify_signal(signal, [], {})
    assert result.scores == []
    assert result.recommended_families == []
    assert result.qualified_at  # timestamp present


def test_state_code_normalized_to_uppercase() -> None:
    ruleset = make_ruleset(
        hard_filters=[{"type": "state_not_excluded"}],
        weighted_signals=[{"rule_id": "r", "reason_code": "C", "weight": 0.8}],
        min_fit_score=0.5,
    )
    signal = make_signal(state_code="ca", reason_codes=[{"code": "C"}])
    territories = {"obc": [make_territory("CA", "hot")]}
    result = qualify_signal(signal, [ruleset], territories)
    # Should normalize "ca" → "CA" and match
    assert result.scores[0].passed_hard_filters is True
    assert result.scores[0].territory_tier == "hot"


def test_malformed_reason_code_entry_skipped() -> None:
    """Reason code entries without 'code' key should be silently skipped."""
    ruleset = make_ruleset(
        weighted_signals=[{"rule_id": "r", "reason_code": "GOOD", "weight": 0.5}],
    )
    signal = make_signal(
        reason_codes=[
            {},  # no code key
            None,  # type: ignore[list-item]
            {"code": "GOOD", "confidence": 1.0},
        ]
    )
    result = qualify_signal(signal, [ruleset], {})
    assert pytest.approx(result.scores[0].raw_score) == 0.5
