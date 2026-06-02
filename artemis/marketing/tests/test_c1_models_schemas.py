"""Phase C1 — unit tests for Pydantic schemas (no DB required).

Tests that each schema validates sample payloads from the Node reference
and that the camelCase/snake_case aliases work correctly.

These tests do NOT need a database — they're pure Pydantic validation.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from artemis.marketing.schemas import (
    ApprovalCreate,
    ApprovalDecide,
    ApprovalRead,
    CampaignBriefCreate,
    CampaignBriefRead,
    CampaignCandidateCreate,
    CampaignCandidateRead,
    CampaignDeliverableCreate,
    CampaignDeliverableRead,
    ContentAssetCreate,
    ContentAssetLinkCreate,
    ContentAssetRead,
    RulesetCreate,
    RulesetRead,
    ScoutRunCreate,
    ScoutRunRead,
    ScoutRunUpdate,
    SignalCreate,
    SignalQualificationUpdate,
    SignalRead,
    SignalStatusUpdate,
    TerritoryConfigRead,
)

# ── Sample payloads lifted from marketing-ops-v1/schemas/signal.md ────────────

SAMPLE_SIGNAL_PAYLOAD = {
    "headline": "Pinellas County Schools posts RFP with efficacy language",
    "campaignFamily": "obc",
    "sourceType": "procurement_portal",
    "sourceUrl": "https://procurement.pinellas.k12.fl.us/rfp/2026-042",
    "sourceId": "sig_2026_05_07_pinellas_rfp_001",
    "summary": "District seeks Reading Intervention solution with measurable student growth.",
    "urgencyTier": "hot",
    "discoveredBy": "procurement_scout",
    "districtId": "FL_pinellas",
    "state": "FL",
    "reasonCodes": [
        {"code": "RFP_LITERACY_POSTED", "evidence_quote": "...", "confidence": 0.95},
        {"code": "RFP_EFFICACY_LANGUAGE", "evidence_quote": "...", "confidence": 0.90},
    ],
    "provenance": {
        "embedding_hash": "a4c8b2e1",
        "near_duplicates_checked": [],
        "material_change_reasoning": None,
    },
}

SAMPLE_RULESET_PAYLOAD = {
    "family": "obc",
    "versionTag": "v1",
    "hardFilters": [
        {"type": "state_not_excluded", "description": "State is not on the OBC exclusion list"}
    ],
    "weightedSignals": [
        {"rule_id": "ws_obc_1", "reason_code": "accountability_funding", "weight": 0.40}
    ],
    "qualitativeRubrics": [
        {
            "rule_id": "qr_obc_1",
            "description": "Evidence of vendor-accountability culture",
            "weight": 0.20,
        }
    ],
    "state": "active",
}


# ── SignalCreate ───────────────────────────────────────────────────────────────


class TestSignalCreate:
    def test_validates_sample_payload(self) -> None:
        sig = SignalCreate.model_validate(SAMPLE_SIGNAL_PAYLOAD)
        assert sig.headline == "Pinellas County Schools posts RFP with efficacy language"
        assert sig.campaign_family == "obc"
        assert sig.urgency_tier == "hot"
        assert len(sig.reason_codes) == 2

    def test_defaults(self) -> None:
        sig = SignalCreate.model_validate(
            {"headline": "Test signal", "campaignFamily": "reading_growth"}
        )
        assert sig.source_type == "manual"
        assert sig.urgency_tier == "standard"
        assert sig.discovered_by == "manual"
        assert sig.reason_codes == []

    def test_snake_case_also_accepted(self) -> None:
        sig = SignalCreate(
            headline="Test",
            campaign_family="biliteracy",
            urgency_tier="hot",
        )
        assert sig.campaign_family == "biliteracy"

    def test_missing_required_fields_raises(self) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            SignalCreate(headline="Missing family")  # type: ignore[call-arg]


# ── SignalStatusUpdate ─────────────────────────────────────────────────────────


class TestSignalStatusUpdate:
    def test_approve(self) -> None:
        u = SignalStatusUpdate.model_validate({"signalStatus": "approved"})
        assert u.signal_status == "approved"

    def test_reject_with_reason(self) -> None:
        u = SignalStatusUpdate.model_validate(
            {"signalStatus": "rejected", "rejectedReason": "Low confidence"}
        )
        assert u.rejected_reason == "Low confidence"

    def test_snooze(self) -> None:
        from datetime import datetime

        until = datetime(2026, 6, 1, tzinfo=UTC)
        u = SignalStatusUpdate.model_validate({"signalStatus": "snoozed", "snoozedUntil": until})
        assert u.snoozed_until == until


# ── SignalQualificationUpdate ──────────────────────────────────────────────────


class TestSignalQualificationUpdate:
    def test_valid(self) -> None:
        u = SignalQualificationUpdate.model_validate(
            {"qualificationJson": {"scores": [0.9], "passed": True}}
        )
        assert u.qualification_json["passed"] is True


# ── ScoutRunCreate ─────────────────────────────────────────────────────────────


class TestScoutRunCreate:
    def test_valid(self) -> None:
        r = ScoutRunCreate.model_validate(
            {"id": "scout_run_20260516_procurement_abc12345", "scoutType": "procurement_scout"}
        )
        assert r.scout_type == "procurement_scout"
        assert r.status == "pending"
        assert r.created_signal_ids == []

    def test_with_dry_run_summary(self) -> None:
        r = ScoutRunCreate.model_validate(
            {
                "id": "scout_run_20260516_procurement_abc12345",
                "scoutType": "procurement_scout",
                "dryRunSummary": {"checked": 10, "would_create": 2},
            }
        )
        assert r.dry_run_summary is not None
        assert r.dry_run_summary["checked"] == 10


class TestScoutRunUpdate:
    def test_partial_update(self) -> None:
        u = ScoutRunUpdate.model_validate({"status": "committed", "createdSignalIds": ["sig_001"]})
        assert u.status == "committed"
        assert u.created_signal_ids == ["sig_001"]
        assert u.errors is None  # Not provided → None


# ── CampaignCandidateCreate ───────────────────────────────────────────────────


class TestCampaignCandidateCreate:
    def test_defaults(self) -> None:
        c = CampaignCandidateCreate.model_validate({"campaignFamily": "obc"})
        assert c.stage == "human_gate_1"
        assert c.decision_state == "pending_review"
        assert c.workspace_state == "created"

    def test_with_signal_and_ruleset(self) -> None:
        c = CampaignCandidateCreate.model_validate(
            {
                "campaignFamily": "obc",
                "sourceSignalId": 42,
                "rulesetVersionAtQualification": "v1",
                "metricsJson": {"fit_score": 0.82},
            }
        )
        assert c.source_signal_id == 42
        assert c.ruleset_version_at_qualification == "v1"


# ── CampaignBriefCreate ───────────────────────────────────────────────────────


class TestCampaignBriefCreate:
    def test_valid(self) -> None:
        b = CampaignBriefCreate.model_validate(
            {"candidateId": 1, "content": {"title": "OBC Brief"}}
        )
        assert b.candidate_id == 1
        assert b.content["title"] == "OBC Brief"

    def test_empty_content_default(self) -> None:
        b = CampaignBriefCreate.model_validate({"candidateId": 1})
        assert b.content == {}


# ── ContentAssetCreate ────────────────────────────────────────────────────────


class TestContentAssetCreate:
    def test_valid(self) -> None:
        a = ContentAssetCreate.model_validate({"assetType": "email_draft"})
        assert a.asset_type == "email_draft"
        assert a.status == "draft"
        assert a.asset_metadata == {}

    def test_with_summary(self) -> None:
        a = ContentAssetCreate.model_validate(
            {"assetType": "playbook", "summary": "OBC Playbook v1"}
        )
        assert a.summary == "OBC Playbook v1"


# ── ContentAssetLinkCreate ────────────────────────────────────────────────────


class TestContentAssetLinkCreate:
    def test_valid(self) -> None:
        link = ContentAssetLinkCreate.model_validate(
            {"candidateId": 1, "assetId": 2, "linkRole": "primary_draft"}
        )
        assert link.candidate_id == 1
        assert link.asset_id == 2
        assert link.link_role == "primary_draft"

    def test_no_role(self) -> None:
        link = ContentAssetLinkCreate.model_validate({"candidateId": 1, "assetId": 2})
        assert link.link_role is None


# ── CampaignDeliverableCreate ─────────────────────────────────────────────────


class TestCampaignDeliverableCreate:
    def test_valid(self) -> None:
        d = CampaignDeliverableCreate.model_validate(
            {"candidateId": 1, "deliverableId": "draft_001", "campaignId": "florida-obc"}
        )
        assert d.candidate_id == 1
        assert d.status == "generating"


# ── RulesetCreate ─────────────────────────────────────────────────────────────


class TestRulesetCreate:
    def test_from_sample(self) -> None:
        r = RulesetCreate.model_validate(SAMPLE_RULESET_PAYLOAD)
        assert r.family == "obc"
        assert r.version_tag == "v1"
        assert len(r.hard_filters) == 1
        assert r.weighted_signals[0]["weight"] == 0.40

    def test_defaults(self) -> None:
        r = RulesetCreate.model_validate({"family": "biliteracy", "versionTag": "v1"})
        assert r.state == "draft"
        assert r.hard_filters == []


# ── TerritoryConfigRead ───────────────────────────────────────────────────────


class TestTerritoryConfigRead:
    def test_valid(self) -> None:
        from datetime import datetime

        t = TerritoryConfigRead.model_validate(
            {
                "id": 1,
                "family": "obc",
                "hotStates": ["FL", "IN", "IL"],
                "standardStates": ["TX", "OH"],
                "unlistedMultiplier": 0.85,
                "createdAt": datetime(2026, 5, 1, tzinfo=UTC),
                "updatedAt": datetime(2026, 5, 16, tzinfo=UTC),
            }
        )
        assert t.hot_states == ["FL", "IN", "IL"]
        assert t.unlisted_multiplier == 0.85


# ── ApprovalCreate / Decide ───────────────────────────────────────────────────


class TestApprovalSchemas:
    def test_create_valid(self) -> None:
        a = ApprovalCreate.model_validate({"kind": "signal_approval", "subjectId": "42"})
        assert a.kind == "signal_approval"
        assert a.subject_id == "42"

    def test_decide_approved(self) -> None:
        d = ApprovalDecide.model_validate(
            {"status": "approved", "decidedBy": "josh@amiralearning.com"}
        )
        assert d.status == "approved"
        assert d.decided_by == "josh@amiralearning.com"

    def test_decide_rejected_with_payload(self) -> None:
        d = ApprovalDecide.model_validate(
            {
                "status": "rejected",
                "decidedBy": "josh@amiralearning.com",
                "decisionPayload": {"reason": "insufficient confidence"},
            }
        )
        assert d.decision_payload is not None
        assert d.decision_payload["reason"] == "insufficient confidence"


# ── Read schema from_attributes ───────────────────────────────────────────────


class TestReadSchemasFromAttributes:
    """Verify that Read schemas work with from_attributes=True (for ORM rows)."""

    def test_signal_read_from_dict(self) -> None:
        from datetime import datetime

        data = {
            "id": 1,
            "source_type": "procurement_portal",
            "source_url": "https://example.com/rfp",
            "source_id": None,
            "headline": "RFP posted",
            "summary": "...",
            "campaign_family": "obc",
            "urgency_tier": "hot",
            "discovered_by": "procurement_scout",
            "district_id": "FL_pinellas",
            "state": "FL",
            "reason_codes": [],
            "provenance": None,
            "qualification_json": None,
            "signal_status": "pending_qualification",
            "snoozed_until": None,
            "rejected_reason": None,
            "owner_user_id": None,
            "created_at": datetime(2026, 5, 7, tzinfo=UTC),
            "updated_at": datetime(2026, 5, 7, tzinfo=UTC),
        }
        s = SignalRead.model_validate(data)
        assert s.id == 1
        assert s.campaign_family == "obc"

    def test_scout_run_read_from_dict(self) -> None:
        from datetime import datetime

        data = {
            "id": "scout_run_20260516_procurement_abc12345",
            "scout_type": "procurement_scout",
            "status": "committed",
            "dry_run_summary": None,
            "created_signal_ids": ["sig_001"],
            "errors": [],
            "started_at": datetime(2026, 5, 16, tzinfo=UTC),
            "completed_at": datetime(2026, 5, 16, 0, 5, tzinfo=UTC),
        }
        r = ScoutRunRead.model_validate(data)
        assert r.id == "scout_run_20260516_procurement_abc12345"
        assert r.status == "committed"

    def test_ruleset_read_from_dict(self) -> None:
        from datetime import datetime

        data = {
            "id": 1,
            "family": "obc",
            "version_tag": "v1",
            "hard_filters": [],
            "weighted_signals": [],
            "qualitative_rubrics": [],
            "state": "active",
            "created_at": datetime(2026, 5, 1, tzinfo=UTC),
        }
        r = RulesetRead.model_validate(data)
        assert r.version_tag == "v1"
        assert r.state == "active"

    def test_campaign_brief_read(self) -> None:
        from datetime import datetime

        data = {
            "id": 1,
            "candidate_id": 42,
            "content": {"title": "OBC Brief"},
            "generated_at": datetime(2026, 5, 16, tzinfo=UTC),
            "generated_by": "brief_assembler",
        }
        b = CampaignBriefRead.model_validate(data)
        assert b.content["title"] == "OBC Brief"

    def test_approval_read(self) -> None:
        from datetime import datetime

        data = {
            "id": 5,
            "kind": "signal_approval",
            "subject_id": "42",
            "status": "approved",
            "decided_by": "josh@amiralearning.com",
            "decided_at": datetime(2026, 5, 16, tzinfo=UTC),
            "decision_payload": None,
            "created_at": datetime(2026, 5, 16, tzinfo=UTC),
        }
        a = ApprovalRead.model_validate(data)
        assert a.status == "approved"

    def test_content_asset_read(self) -> None:
        from datetime import datetime

        # ORM attribute is 'asset_metadata' (maps to DB column 'metadata')
        data = {
            "id": 10,
            "asset_type": "email_draft",
            "status": "draft",
            "summary": None,
            "asset_metadata": {},
            "owner_user_id": None,
            "created_at": datetime(2026, 5, 16, tzinfo=UTC),
            "updated_at": datetime(2026, 5, 16, tzinfo=UTC),
        }
        a = ContentAssetRead.model_validate(data)
        assert a.asset_type == "email_draft"

    def test_campaign_deliverable_read(self) -> None:
        from datetime import datetime

        # ORM attribute is 'deliverable_metadata' (maps to DB column 'metadata')
        data = {
            "id": 3,
            "candidate_id": 1,
            "deliverable_id": "draft_001",
            "campaign_id": "florida-obc",
            "status": "generating",
            "deliverable_metadata": {},
            "created_at": datetime(2026, 5, 16, tzinfo=UTC),
            "updated_at": datetime(2026, 5, 16, tzinfo=UTC),
        }
        d = CampaignDeliverableRead.model_validate(data)
        assert d.deliverable_id == "draft_001"

    def test_candidate_read_from_dict(self) -> None:
        from datetime import datetime

        data = {
            "id": 7,
            "campaign_family": "state_screener",
            "source_signal_id": 3,
            "stage": "human_gate_1",
            "decision_state": "approved",
            "workspace_state": "created",
            "ruleset_version_at_qualification": "v1",
            "metrics_json": {"fit_score": 0.75},
            "deliverables": None,
            "owner_user_id": None,
            "created_at": datetime(2026, 5, 16, tzinfo=UTC),
            "updated_at": datetime(2026, 5, 16, tzinfo=UTC),
        }
        c = CampaignCandidateRead.model_validate(data)
        assert c.campaign_family == "state_screener"
        assert c.metrics_json is not None
        assert c.metrics_json["fit_score"] == 0.75
