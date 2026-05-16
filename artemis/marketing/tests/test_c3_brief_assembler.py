"""Phase C3 brief assembler tests — ≥15 tests.

Covers: signal-only, signal+asset, district-unavailable, contact-unavailable,
multi-signal, qualification summary, format_brief_for_writing_studio, edge cases.
"""

from __future__ import annotations

from typing import Any

import pytest

from artemis.marketing.brief_assembler import (
    AssetContext,
    CandidateInput,
    ContactData,
    DistrictData,
    QualificationSummary,
    SignalContext,
    assemble_brief,
    format_brief_for_writing_studio,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def make_candidate(**overrides: Any) -> CandidateInput:
    defaults: dict[str, Any] = {
        "id": 1,
        "campaign_family": "obc",
        "decision_state": "approved",
    }
    return CandidateInput(**{**defaults, **overrides})


def make_signal(**overrides: Any) -> SignalContext:
    defaults: dict[str, Any] = {
        "reason_codes": ["DISTRICT_VOTED_YES"],
        "verbatim_snippet": "District approved a measure.",
        "urgency_tier": "hot",
    }
    return SignalContext(**{**defaults, **overrides})


# ─────────────────────────────────────────────────────────────────────────────
# Core shape tests (signal-only)
# ─────────────────────────────────────────────────────────────────────────────


def test_brief_has_required_keys() -> None:
    candidate = make_candidate()
    brief = assemble_brief(candidate, signals=[make_signal()])
    content = brief.content
    assert "campaignId" in content
    assert "assembledAt" in content
    assert "signal" in content
    assert "campaignType" in content
    assert "districtDataUnavailable" in content
    assert "contactsUnavailable" in content
    assert "audienceTierUnavailable" in content


def test_brief_campaign_id_matches_candidate() -> None:
    candidate = make_candidate(id=42)
    brief = assemble_brief(candidate)
    assert brief.content["campaignId"] == 42
    assert brief.content["sourceCandidateId"] == 42


def test_brief_district_unavailable_when_no_district_data() -> None:
    brief = assemble_brief(make_candidate())
    assert brief.content["districtDataUnavailable"] is True
    assert brief.content["district"] is None


def test_brief_contacts_unavailable_when_no_contact_data() -> None:
    brief = assemble_brief(make_candidate())
    assert brief.content["contactsUnavailable"] is True
    assert brief.content["targetContacts"] == []


def test_brief_audience_tier_always_unavailable() -> None:
    brief = assemble_brief(make_candidate())
    assert brief.content["audienceTierUnavailable"] is True
    assert brief.content["audienceTierDistribution"] is None


def test_brief_reason_codes_from_signal() -> None:
    sig = make_signal(reason_codes=["CODE_A", "CODE_B"])
    brief = assemble_brief(make_candidate(), signals=[sig])
    assert "CODE_A" in brief.content["signal"]["reasonCodesWithEvidence"]
    assert "CODE_B" in brief.content["signal"]["reasonCodesWithEvidence"]


def test_brief_verbatim_evidence_from_signal_snippet() -> None:
    sig = make_signal(verbatim_snippet="The board voted yes.")
    brief = assemble_brief(make_candidate(), signals=[sig])
    assert brief.content["signal"]["verbatimEvidence"] == "The board voted yes."


def test_brief_verbatim_evidence_from_candidate_why() -> None:
    """candidate.why takes priority over signal snippet."""
    candidate = make_candidate(why="Candidate-level evidence.")
    sig = make_signal(verbatim_snippet="Signal snippet.")
    brief = assemble_brief(candidate, signals=[sig])
    assert brief.content["signal"]["verbatimEvidence"] == "Candidate-level evidence."


def test_brief_campaign_type_primary_from_family() -> None:
    candidate = make_candidate(campaign_family="biliteracy")
    brief = assemble_brief(candidate)
    assert brief.content["campaignType"]["primary"] == "biliteracy"


# ─────────────────────────────────────────────────────────────────────────────
# Signal + asset
# ─────────────────────────────────────────────────────────────────────────────


def test_brief_with_linked_asset() -> None:
    asset = AssetContext(
        asset_id=10, asset_type="snippet", summary="A snippet", link_role="primary"
    )
    brief = assemble_brief(make_candidate(), linked_assets=[asset])
    linked = brief.content["linkedAssets"]
    assert len(linked) == 1
    assert linked[0]["assetId"] == 10
    assert linked[0]["assetType"] == "snippet"
    assert linked[0]["linkRole"] == "primary"


def test_brief_no_linked_assets_default_empty() -> None:
    brief = assemble_brief(make_candidate())
    assert brief.content["linkedAssets"] == []


# ─────────────────────────────────────────────────────────────────────────────
# District available
# ─────────────────────────────────────────────────────────────────────────────


def test_brief_district_available_when_provided() -> None:
    district = DistrictData(district_id="D123", district_name="Springfield USD")
    brief = assemble_brief(make_candidate(), district_data=district)
    assert brief.content["districtDataUnavailable"] is False
    assert brief.content["district"]["districtId"] == "D123"


# ─────────────────────────────────────────────────────────────────────────────
# Contacts available
# ─────────────────────────────────────────────────────────────────────────────


def test_brief_contacts_available_when_provided() -> None:
    contacts = ContactData(contacts=[{"name": "Alice", "email": "alice@example.com"}])
    brief = assemble_brief(make_candidate(), contact_data=contacts)
    assert brief.content["contactsUnavailable"] is False
    assert len(brief.content["targetContacts"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Multi-signal
# ─────────────────────────────────────────────────────────────────────────────


def test_brief_multi_signal_merges_reason_codes() -> None:
    s1 = make_signal(reason_codes=["CODE_A"])
    s2 = make_signal(reason_codes=["CODE_B"])
    brief = assemble_brief(make_candidate(), signals=[s1, s2])
    codes = brief.content["signal"]["reasonCodesWithEvidence"]
    assert "CODE_A" in codes
    assert "CODE_B" in codes


# ─────────────────────────────────────────────────────────────────────────────
# Qualification summary
# ─────────────────────────────────────────────────────────────────────────────


def test_brief_includes_qualification_summary() -> None:
    qual = QualificationSummary(
        adjusted_score=0.75,
        recommended_families=[{"campaignFamily": "obc", "role": "primary"}],
        qualified_at="2026-05-16T00:00:00Z",
        ruleset_versions_used={"obc": "v2"},
    )
    brief = assemble_brief(make_candidate(), qualification_summary=qual)
    qs = brief.content["qualificationSummary"]
    assert pytest.approx(qs["adjustedScore"]) == 0.75
    assert qs["rulesetVersionsUsed"]["obc"] == "v2"


# ─────────────────────────────────────────────────────────────────────────────
# format_brief_for_writing_studio
# ─────────────────────────────────────────────────────────────────────────────


def test_format_brief_includes_verbatim_evidence() -> None:
    candidate = make_candidate(why="The district voted yes.")
    brief = assemble_brief(candidate)
    text = format_brief_for_writing_studio(brief)
    assert "The district voted yes." in text


def test_format_brief_includes_unavailability_notices() -> None:
    brief = assemble_brief(make_candidate())
    text = format_brief_for_writing_studio(brief)
    assert "District data not available" in text
    assert "Target contacts not available" in text


def test_format_brief_does_not_emit_null_string() -> None:
    brief = assemble_brief(make_candidate())
    text = format_brief_for_writing_studio(brief)
    assert "null" not in text
    assert "None" not in text


# ─────────────────────────────────────────────────────────────────────────────
# to_dict round-trip
# ─────────────────────────────────────────────────────────────────────────────


def test_brief_to_dict_is_serializable() -> None:
    import json

    brief = assemble_brief(make_candidate(), signals=[make_signal()])
    # Should not raise
    encoded = json.dumps(brief.to_dict())
    decoded = json.loads(encoded)
    assert decoded["source"] == "campaign_candidate"
