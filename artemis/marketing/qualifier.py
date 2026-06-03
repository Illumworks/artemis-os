"""Deterministic fit-scoring engine for signals against campaign rulesets.

Pure function: no DB, no LLM, no external calls. Callers pre-load all data
from the DB and pass it in so this module is trivially unit-testable.

Port of Node's server/signal-qualifier.js (134 lines).

Algorithm:
  Phase 1 — Hard filter check (territory presence)
  Phase 2 — Weighted signal match (reason_codes × ruleset weights)
  Phase 3 — Territory multiplier (hot 1.2 / standard 1.0 / unlisted 0.85)
  Phase 4 — Route: passes_min_fit_score, recommended_families[]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_TERRITORY_MULTIPLIERS: dict[str, float] = {
    "hot": 1.2,
    "standard": 1.0,
    "unlisted": 0.85,
}


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MatchedRule:
    rule_id: str | None
    reason_code: str
    weight: float
    confidence: float
    contribution: float


@dataclass
class FamilyScore:
    campaign_family: str
    ruleset_version: str
    passed_hard_filters: bool
    raw_score: float
    territory_tier: str
    territory_multiplier: float
    adjusted_score: float
    passes_min_fit_score: bool
    min_fit_score: float
    matched_rules: list[MatchedRule] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaignFamily": self.campaign_family,
            "rulesetVersion": self.ruleset_version,
            "passedHardFilters": self.passed_hard_filters,
            "rawScore": self.raw_score,
            "territoryTier": self.territory_tier,
            "territoryMultiplier": self.territory_multiplier,
            "adjustedScore": self.adjusted_score,
            "passesMinFitScore": self.passes_min_fit_score,
            "minFitScore": self.min_fit_score,
            "matchedRules": [
                {
                    "ruleId": r.rule_id,
                    "reasonCode": r.reason_code,
                    "weight": r.weight,
                    "confidence": r.confidence,
                    "contribution": r.contribution,
                }
                for r in self.matched_rules
            ],
        }


@dataclass
class RecommendedFamily:
    campaign_family: str
    role: str  # "primary" | "secondary"
    adjusted_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaignFamily": self.campaign_family,
            "role": self.role,
            "adjustedScore": self.adjusted_score,
        }


@dataclass
class QualificationResult:
    qualified_at: str
    ruleset_versions_used: dict[str, str]
    scores: list[FamilyScore]
    recommended_families: list[RecommendedFamily]

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualifiedAt": self.qualified_at,
            "rulesetVersionsUsed": self.ruleset_versions_used,
            "scores": [s.to_dict() for s in self.scores],
            "recommendedFamilies": [r.to_dict() for r in self.recommended_families],
        }


# ─────────────────────────────────────────────────────────────────────────────
# DIST4 — District tier soft-flag annotation
# ─────────────────────────────────────────────────────────────────────────────


def annotate_district_tier(
    qual_dict: dict[str, Any],
    *,
    district_id: int | None,
    district_name: str | None,
    district_state: str | None,
    district_tier: str | None,
    district_enrollment: int | None,
    district_supported: bool | None,
    district_on_skip_list: bool | None = None,
) -> dict[str, Any]:
    """Annotate a qualification dict with district tier metadata (DIST4).

    Pure: no DB, no I/O.  Callers pre-load the district row and pass fields.

    Soft-flag only: sets ``tier_flag="unsupported_tier"`` when
    ``district_supported=False``, else ``tier_flag=None``.  The signal is
    NEVER dropped, rejected, or auto-skipped — the flag is metadata for the
    human reviewer and for UI filtering.

    When ``district_id`` is None (unresolved signal), the dict gains a
    ``districtContext`` key with ``resolved=False`` and no fabricated data.

    Returns a new dict (does not mutate the input).
    """
    result = dict(qual_dict)

    if district_id is None:
        result["districtContext"] = {"resolved": False}
        return result

    tier_flag: str | None = None
    if district_supported is False:
        tier_flag = "unsupported_tier"

    result["districtContext"] = {
        "resolved": True,
        "districtId": district_id,
        "districtName": district_name,
        "districtState": district_state,
        "districtTier": district_tier,
        "districtEnrollment": district_enrollment,
        "districtSupported": district_supported,
        "onSkipList": district_on_skip_list,
        "tierFlag": tier_flag,
    }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Input types (thin wrappers around ORM / dict data)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SignalInput:
    """Caller-constructed signal data extracted from the ORM model.

    reason_codes is a list of dicts with 'code' and optional 'confidence'.
    state_code is the 2-letter uppercase state code or None.
    """

    state_code: str | None
    reason_codes: list[dict[str, Any]]
    campaign_family: str | None = None
    urgency_tier: str | None = None


@dataclass
class RulesetInput:
    """Caller-constructed ruleset data extracted from the ORM model."""

    campaign_family: str
    version_number: str  # version_tag stored here (e.g. "v1", "v2")
    min_fit_score: float = 0.5
    hard_filters: list[dict[str, Any]] = field(default_factory=list)
    weighted_signals: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TerritoryEntry:
    """A single state's territory classification for a family."""

    state_code: str
    priority_tier: str  # "hot" | "standard"


# ─────────────────────────────────────────────────────────────────────────────
# Core function
# ─────────────────────────────────────────────────────────────────────────────


def qualify_signal(
    signal: SignalInput,
    active_rulesets: list[RulesetInput],
    territories_by_family: dict[str, list[TerritoryEntry]],
) -> QualificationResult:
    """Score a signal deterministically against all active rulesets.

    Pure: no DB, no LLM, no I/O.

    Args:
        signal: Signal data including state_code and reason_codes.
        active_rulesets: One entry per campaign family (active only).
        territories_by_family: Maps campaign_family → list of TerritoryEntry.

    Returns:
        QualificationResult with per-family scores and recommended families.
    """
    # Normalize state code
    state_code: str | None = str(signal.state_code).upper().strip() if signal.state_code else None

    # Build reason-code confidence map
    reason_map: dict[str, float] = {}
    for rc in signal.reason_codes or []:
        if not rc or not rc.get("code"):
            continue
        code = str(rc["code"])
        raw_conf = rc.get("confidence")
        if isinstance(raw_conf, (int, float)):
            confidence = min(1.0, max(0.0, float(raw_conf)))
        else:
            confidence = 1.0
        reason_map[code] = confidence

    scores: list[FamilyScore] = []
    ruleset_versions_used: dict[str, str] = {}

    for ruleset in active_rulesets:
        family = ruleset.campaign_family
        version_tag = ruleset.version_number
        ruleset_versions_used[family] = version_tag

        territories = territories_by_family.get(family, [])

        # Phase 1: Hard filter check (territory presence)
        passed_hard_filters = True
        for hf in ruleset.hard_filters:
            if hf.get("type") == "state_not_excluded" and state_code:
                in_config = any(t.state_code == state_code for t in territories)
                if not in_config:
                    passed_hard_filters = False

        # Phase 2: Weighted signal match
        matched_rules: list[MatchedRule] = []
        raw_score = 0.0

        for ws in ruleset.weighted_signals:
            reason_code = ws.get("reason_code")
            if reason_code and reason_code in reason_map:
                confidence = reason_map[reason_code]
                weight = float(ws.get("weight", 0))
                contribution = weight * confidence
                raw_score += contribution
                matched_rules.append(
                    MatchedRule(
                        rule_id=ws.get("rule_id"),
                        reason_code=reason_code,
                        weight=weight,
                        confidence=confidence,
                        contribution=contribution,
                    )
                )

        # Phase 3: Territory multiplier
        territory_tier = "unlisted"
        if state_code:
            for t in territories:
                if t.state_code == state_code:
                    territory_tier = t.priority_tier or "standard"
                    break

        multiplier = _TERRITORY_MULTIPLIERS.get(territory_tier, 1.0)
        adjusted_score = min(1.0, raw_score * multiplier)

        # Phase 4: Min fit score gate
        min_fit_score = ruleset.min_fit_score
        passes_min_fit_score = adjusted_score >= min_fit_score

        scores.append(
            FamilyScore(
                campaign_family=family,
                ruleset_version=version_tag,
                passed_hard_filters=passed_hard_filters,
                raw_score=raw_score,
                territory_tier=territory_tier,
                territory_multiplier=multiplier,
                adjusted_score=adjusted_score,
                passes_min_fit_score=passes_min_fit_score,
                min_fit_score=min_fit_score,
                matched_rules=matched_rules,
            )
        )

    # Rank: qualifying families (pass hard filters AND min_fit_score) desc
    qualifying = sorted(
        [s for s in scores if s.passed_hard_filters and s.passes_min_fit_score],
        key=lambda s: s.adjusted_score,
        reverse=True,
    )
    recommended_families = [
        RecommendedFamily(
            campaign_family=s.campaign_family,
            role="primary" if i == 0 else "secondary",
            adjusted_score=s.adjusted_score,
        )
        for i, s in enumerate(qualifying)
    ]

    return QualificationResult(
        qualified_at=datetime.now(UTC).isoformat(),
        ruleset_versions_used=ruleset_versions_used,
        scores=scores,
        recommended_families=recommended_families,
    )
