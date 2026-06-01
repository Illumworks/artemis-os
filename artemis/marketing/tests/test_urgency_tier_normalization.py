"""#81 — urgency-tier taxonomy reconciliation.

Canonical tiers (Josh's spec §2 default urgencies + §4 suppress/boost ladder):
hot, standard, enrichment. The legacy 'low' slug — which lived in scout
intake, the signal_queue.write tool enum, and the Pydantic Literal but never
in the spec — normalizes to 'enrichment' through josh_spec.normalize_urgency_tier,
and scout intake stores the canonical slug.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from artemis.marketing.josh_spec import (
    CANONICAL_URGENCY_TIERS,
    normalize_urgency_tier,
)
from artemis.marketing.scout_intake import normalize_intake_payload
from artemis.marketing.scout_schemas import ScoutEmittedSignal


def test_canonical_set() -> None:
    assert CANONICAL_URGENCY_TIERS == ("hot", "standard", "enrichment")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # canonical slugs pass through
        ("hot", "hot"),
        ("standard", "standard"),
        ("enrichment", "enrichment"),
        # legacy alias -> canonical
        ("low", "enrichment"),
        # case / whitespace insensitive
        ("  HOT  ", "hot"),
        ("Enrichment", "enrichment"),
        ("LOW", "enrichment"),
        # unrecognized
        ("extreme", None),
        ("medium", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_urgency_tier(value: str | None, expected: str | None) -> None:
    assert normalize_urgency_tier(value) == expected


def _payload(tier: str) -> dict[str, object]:
    return {
        "sourceType": "news_article",
        "headline": "District literacy update",
        "campaignFamily": "general_growth",
        "urgencyTier": tier,
        "reasonCodes": [{"code": "PROGRAM_LAUNCH", "confidence": 0.8}],
        "evidence": "Board press release.",
        "sourceUrl": "https://example.org/x",
    }


def test_intake_stores_canonical_for_each_tier() -> None:
    for tier in CANONICAL_URGENCY_TIERS:
        result = normalize_intake_payload(_payload(tier), scout_type="news_article")
        assert result.urgency_tier == tier


def test_intake_normalizes_legacy_low_to_enrichment() -> None:
    """A scout still emitting the legacy 'low' slug stores 'enrichment'."""
    result = normalize_intake_payload(_payload("low"), scout_type="news_article")
    assert result.urgency_tier == "enrichment"


def test_intake_rejects_unknown_tier() -> None:
    with pytest.raises(ValueError, match="urgencyTier must be one of"):
        normalize_intake_payload(_payload("extreme"), scout_type="news_article")


def test_pydantic_accepts_canonical_tiers() -> None:
    for tier in CANONICAL_URGENCY_TIERS:
        parsed = ScoutEmittedSignal.model_validate(_payload(tier))
        assert parsed.urgency_tier == tier


def test_pydantic_normalizes_low_to_enrichment() -> None:
    """The legacy 'low' slug is mapped to 'enrichment' before the Literal check."""
    parsed = ScoutEmittedSignal.model_validate(_payload("low"))
    assert parsed.urgency_tier == "enrichment"


def test_pydantic_rejects_unknown_tier() -> None:
    with pytest.raises(ValidationError):
        ScoutEmittedSignal.model_validate(_payload("extreme"))
