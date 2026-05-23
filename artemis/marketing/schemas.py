"""Pydantic 2.x DTOs for the Marketing OS domain.

Shapes mirror the JSON the Node/Express API returns so the frontend port
(Phase E) requires no payload changes. Field names are camelCase to match
the existing Node API responses; aliases defined with `alias=` where needed.

Classes follow the Read / Write (Create) / Update pattern:
  - <Model>Create — inbound, all required fields, no server-set fields
  - <Model>Read   — outbound, includes server-set fields (id, created_at, …)
  - <Model>Update — inbound PATCH, all fields optional

The Node app normalizes rows to camelCase in JS (e.g. normalizeSignalRow).
We replicate that contract here using model_config with populate_by_name so
tests can pass either style.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Signal Queue
# ─────────────────────────────────────────────────────────────────────────────


class SignalCreate(_Base):
    """Inbound payload for a new market signal (manual intake or scout emit)."""

    headline: str
    campaign_family: str = Field(..., alias="campaignFamily")
    source_type: str = Field(default="manual", alias="sourceType")
    source_url: str | None = Field(default=None, alias="sourceUrl")
    source_id: str | None = Field(default=None, alias="sourceId")
    pipeline_run_id: str | None = Field(default=None, alias="pipelineRunId")
    summary: str = ""
    urgency_tier: str = Field(default="standard", alias="urgencyTier")
    discovered_by: str = Field(default="manual", alias="discoveredBy")
    district_id: str | None = Field(default=None, alias="districtId")
    state: str | None = None
    reason_codes: list[Any] = Field(default_factory=list, alias="reasonCodes")
    provenance: dict[str, Any] | None = None
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class SignalRead(_Base):
    """Outbound representation of a signal_queue row."""

    id: int
    source_type: str = Field(alias="sourceType")
    source_url: str | None = Field(default=None, alias="sourceUrl")
    source_id: str | None = Field(default=None, alias="sourceId")
    pipeline_run_id: str | None = Field(default=None, alias="pipelineRunId")
    pipeline_run: dict[str, Any] | None = Field(default=None, alias="pipelineRun")
    approval: dict[str, Any] | None = None
    headline: str
    summary: str
    campaign_family: str = Field(alias="campaignFamily")
    urgency_tier: str = Field(alias="urgencyTier")
    discovered_by: str = Field(alias="discoveredBy")
    district_id: str | None = Field(default=None, alias="districtId")
    state: str | None = None
    reason_codes: list[Any] = Field(alias="reasonCodes")
    provenance: dict[str, Any] | None = None
    qualification_json: dict[str, Any] | None = Field(default=None, alias="qualificationJson")
    signal_status: str = Field(alias="signalStatus")
    snoozed_until: datetime | None = Field(default=None, alias="snoozedUntil")
    rejected_reason: str | None = Field(default=None, alias="rejectedReason")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class SignalStatusUpdate(_Base):
    """Payload for status transitions (approve / reject / snooze / ask)."""

    signal_status: str = Field(alias="signalStatus")
    rejected_reason: str | None = Field(default=None, alias="rejectedReason")
    snoozed_until: datetime | None = Field(default=None, alias="snoozedUntil")


class SignalQualificationUpdate(_Base):
    qualification_json: dict[str, Any] = Field(alias="qualificationJson")


# ─────────────────────────────────────────────────────────────────────────────
# Scout Runs
# ─────────────────────────────────────────────────────────────────────────────


class ScoutRunCreate(_Base):
    id: str
    scout_type: str = Field(alias="scoutType")
    status: str = "pending"
    dry_run_summary: dict[str, Any] | None = Field(default=None, alias="dryRunSummary")
    created_signal_ids: list[str] = Field(default_factory=list, alias="createdSignalIds")
    errors: list[Any] = Field(default_factory=list)


class ScoutRunRead(_Base):
    id: str
    scout_type: str = Field(alias="scoutType")
    status: str
    dry_run_summary: dict[str, Any] | None = Field(default=None, alias="dryRunSummary")
    created_signal_ids: list[str] = Field(alias="createdSignalIds")
    errors: list[Any]
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")


class ScoutRunUpdate(_Base):
    status: str | None = None
    dry_run_summary: dict[str, Any] | None = Field(default=None, alias="dryRunSummary")
    created_signal_ids: list[str] | None = Field(default=None, alias="createdSignalIds")
    errors: list[Any] | None = None
    completed_at: datetime | None = Field(default=None, alias="completedAt")


# ─────────────────────────────────────────────────────────────────────────────
# Campaign Candidates
# ─────────────────────────────────────────────────────────────────────────────


class CampaignCandidateCreate(_Base):
    campaign_family: str = Field(alias="campaignFamily")
    source_signal_id: int | None = Field(default=None, alias="sourceSignalId")
    stage: str = "human_gate_1"
    decision_state: str = Field(default="pending_review", alias="decisionState")
    workspace_state: str = Field(default="created", alias="workspaceState")
    ruleset_version_at_qualification: str | None = Field(
        default=None, alias="rulesetVersionAtQualification"
    )
    metrics_json: dict[str, Any] | None = Field(default=None, alias="metricsJson")
    deliverables: list[Any] | None = None
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class CampaignCandidateRead(_Base):
    id: int
    campaign_family: str = Field(alias="campaignFamily")
    source_signal_id: int | None = Field(default=None, alias="sourceSignalId")
    stage: str
    decision_state: str = Field(alias="decisionState")
    workspace_state: str = Field(alias="workspaceState")
    ruleset_version_at_qualification: str | None = Field(
        default=None, alias="rulesetVersionAtQualification"
    )
    metrics_json: dict[str, Any] | None = Field(default=None, alias="metricsJson")
    deliverables: list[Any] | None = None
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


# ─────────────────────────────────────────────────────────────────────────────
# Campaign Briefs
# ─────────────────────────────────────────────────────────────────────────────


class CampaignBriefCreate(_Base):
    candidate_id: int = Field(alias="candidateId")
    content: dict[str, Any] = Field(default_factory=dict)
    generated_by: str | None = Field(default=None, alias="generatedBy")


class CampaignBriefRead(_Base):
    id: int
    candidate_id: int = Field(alias="candidateId")
    content: dict[str, Any]
    generated_at: datetime = Field(alias="generatedAt")
    generated_by: str | None = Field(default=None, alias="generatedBy")


# ─────────────────────────────────────────────────────────────────────────────
# Content Assets
# ─────────────────────────────────────────────────────────────────────────────


class ContentAssetCreate(_Base):
    asset_type: str = Field(alias="assetType")
    status: str = "draft"
    summary: str | None = None
    asset_metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class ContentAssetRead(_Base):
    id: int
    asset_type: str = Field(alias="assetType")
    status: str
    summary: str | None = None
    asset_metadata: dict[str, Any] = Field(alias="metadata")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ContentAssetUpdate(_Base):
    status: str | None = None
    summary: str | None = None
    asset_metadata: dict[str, Any] | None = Field(default=None, alias="metadata")


# ─────────────────────────────────────────────────────────────────────────────
# Content Asset Links
# ─────────────────────────────────────────────────────────────────────────────


class ContentAssetLinkCreate(_Base):
    candidate_id: int = Field(alias="candidateId")
    asset_id: int = Field(alias="assetId")
    link_role: str | None = Field(default=None, alias="linkRole")


class ContentAssetLinkRead(_Base):
    id: int
    candidate_id: int = Field(alias="candidateId")
    asset_id: int = Field(alias="assetId")
    link_role: str | None = Field(default=None, alias="linkRole")
    created_at: datetime = Field(alias="createdAt")


# ─────────────────────────────────────────────────────────────────────────────
# Campaign Deliverables
# ─────────────────────────────────────────────────────────────────────────────


class CampaignDeliverableCreate(_Base):
    candidate_id: int = Field(alias="candidateId")
    deliverable_id: str | None = Field(default=None, alias="deliverableId")
    campaign_id: str | None = Field(default=None, alias="campaignId")
    status: str = "generating"
    deliverable_metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CampaignDeliverableRead(_Base):
    id: int
    candidate_id: int = Field(alias="candidateId")
    deliverable_id: str | None = Field(default=None, alias="deliverableId")
    campaign_id: str | None = Field(default=None, alias="campaignId")
    status: str
    deliverable_metadata: dict[str, Any] = Field(alias="metadata")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


# ─────────────────────────────────────────────────────────────────────────────
# Rulesets
# ─────────────────────────────────────────────────────────────────────────────


class RulesetCreate(_Base):
    family: str
    version_tag: str = Field(alias="versionTag")
    hard_filters: list[Any] = Field(default_factory=list, alias="hardFilters")
    weighted_signals: list[Any] = Field(default_factory=list, alias="weightedSignals")
    qualitative_rubrics: list[Any] = Field(default_factory=list, alias="qualitativeRubrics")
    state: str = "draft"


class RulesetRead(_Base):
    id: int
    family: str
    version_tag: str = Field(alias="versionTag")
    hard_filters: list[Any] = Field(alias="hardFilters")
    weighted_signals: list[Any] = Field(alias="weightedSignals")
    qualitative_rubrics: list[Any] = Field(alias="qualitativeRubrics")
    state: str
    created_at: datetime = Field(alias="createdAt")


# ─────────────────────────────────────────────────────────────────────────────
# Territory Config
# ─────────────────────────────────────────────────────────────────────────────


class TerritoryConfigRead(_Base):
    id: int
    family: str
    hot_states: list[str] = Field(alias="hotStates")
    standard_states: list[str] = Field(alias="standardStates")
    unlisted_multiplier: float = Field(alias="unlistedMultiplier")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


# ─────────────────────────────────────────────────────────────────────────────
# Approvals
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalCreate(_Base):
    kind: str
    subject_id: str = Field(alias="subjectId")
    decision_payload: dict[str, Any] | None = Field(default=None, alias="decisionPayload")


class ApprovalRead(_Base):
    id: int
    kind: str
    subject_id: str = Field(alias="subjectId")
    status: str
    decided_by: str | None = Field(default=None, alias="decidedBy")
    decided_at: datetime | None = Field(default=None, alias="decidedAt")
    decision_payload: dict[str, Any] | None = Field(default=None, alias="decisionPayload")
    created_at: datetime = Field(alias="createdAt")


class ApprovalDecide(_Base):
    status: str  # approved | rejected
    decided_by: str = Field(alias="decidedBy")
    decision_payload: dict[str, Any] | None = Field(default=None, alias="decisionPayload")
