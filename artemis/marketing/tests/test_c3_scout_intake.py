"""Phase C3 scout intake tests — ≥8 tests.

Covers: valid set boundaries, anti-spoof override, raises on invalid input.
"""

from __future__ import annotations

from typing import Any

import pytest

from artemis.marketing.scout_intake import (
    VALID_CAMPAIGN_FAMILIES,
    VALID_SOURCE_TYPES,
    VALID_URGENCY_TIERS,
    NormalizedFinding,
    normalize_intake_payload,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _minimal_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sourceType": "manual",
        "headline": "Test headline",
        "campaignFamily": "obc",
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Anti-spoof override (1)
# ─────────────────────────────────────────────────────────────────────────────


def test_antispoof_discovered_by_always_overridden() -> None:
    """The payload's discoveredBy must always be replaced by scout_type."""
    payload = _minimal_payload(discoveredBy="hacker_bot")
    result = normalize_intake_payload(payload, scout_type="starbridge")
    assert result.discovered_by == "starbridge"


# ─────────────────────────────────────────────────────────────────────────────
# Valid set boundaries
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("source_type", list(VALID_SOURCE_TYPES))
def test_all_valid_source_types_accepted(source_type: str) -> None:
    url = "https://example.com" if source_type != "manual" else None
    payload = _minimal_payload(sourceType=source_type)
    if url:
        payload["sourceUrl"] = url
    result = normalize_intake_payload(payload, scout_type="manual")
    assert result.source_type == source_type


@pytest.mark.parametrize("family", list(VALID_CAMPAIGN_FAMILIES))
def test_all_valid_campaign_families_accepted(family: str) -> None:
    payload = _minimal_payload(campaignFamily=family)
    result = normalize_intake_payload(payload, scout_type="manual")
    assert result.campaign_family == family


@pytest.mark.parametrize("tier", list(VALID_URGENCY_TIERS))
def test_all_valid_urgency_tiers_accepted(tier: str) -> None:
    payload = _minimal_payload(urgencyTier=tier)
    result = normalize_intake_payload(payload, scout_type="manual")
    assert result.urgency_tier == tier


# ─────────────────────────────────────────────────────────────────────────────
# Raises on invalid input
# ─────────────────────────────────────────────────────────────────────────────


def test_raises_on_invalid_source_type() -> None:
    payload = _minimal_payload(sourceType="unknown_type")
    with pytest.raises(ValueError, match="sourceType"):
        normalize_intake_payload(payload, scout_type="manual")


def test_raises_on_missing_headline_and_snippet() -> None:
    payload = _minimal_payload()
    del payload["headline"]
    with pytest.raises(ValueError, match="headline or verbatimSnippet"):
        normalize_intake_payload(payload, scout_type="manual")


def test_raises_on_invalid_campaign_family() -> None:
    payload = _minimal_payload(campaignFamily="not_a_real_family")
    with pytest.raises(ValueError, match="campaignFamily"):
        normalize_intake_payload(payload, scout_type="manual")


def test_raises_on_invalid_state_code() -> None:
    payload = _minimal_payload(stateCode="INVALID")
    with pytest.raises(ValueError, match="stateCode"):
        normalize_intake_payload(payload, scout_type="manual")


def test_raises_on_source_url_missing_for_non_manual() -> None:
    payload = _minimal_payload(sourceType="news_article")
    # no sourceUrl
    with pytest.raises(ValueError, match="sourceUrl"):
        normalize_intake_payload(payload, scout_type="news_article")


def test_raises_on_invalid_http_url() -> None:
    payload = _minimal_payload(sourceType="news_article", sourceUrl="ftp://bad.url")
    with pytest.raises(ValueError, match="sourceUrl must be a valid http"):
        normalize_intake_payload(payload, scout_type="news_article")


def test_raises_on_fit_score_out_of_range() -> None:
    payload = _minimal_payload(fitScore=1.5)
    with pytest.raises(ValueError, match="fitScore"):
        normalize_intake_payload(payload, scout_type="manual")


# ─────────────────────────────────────────────────────────────────────────────
# Normalization correctness
# ─────────────────────────────────────────────────────────────────────────────


def test_state_code_uppercased() -> None:
    payload = _minimal_payload(stateCode="ca")
    result = normalize_intake_payload(payload, scout_type="manual")
    assert result.state_code == "CA"


def test_headline_derived_from_snippet_when_absent() -> None:
    payload = _minimal_payload()
    del payload["headline"]
    payload["verbatimSnippet"] = "The board voted in favour. This is additional context."
    result = normalize_intake_payload(payload, scout_type="manual")
    # Should derive first sentence
    assert "voted" in result.headline


def test_urgency_tier_defaults_to_standard() -> None:
    payload = _minimal_payload()
    result = normalize_intake_payload(payload, scout_type="manual")
    assert result.urgency_tier == "standard"


def test_reason_codes_confidence_clamped() -> None:
    payload = _minimal_payload(reasonCodes=[{"code": "X", "confidence": 5.0}])
    result = normalize_intake_payload(payload, scout_type="manual")
    assert result.reason_codes[0]["confidence"] == 1.0


def test_result_is_normalized_finding_type() -> None:
    payload = _minimal_payload()
    result = normalize_intake_payload(payload, scout_type="manual")
    assert isinstance(result, NormalizedFinding)
