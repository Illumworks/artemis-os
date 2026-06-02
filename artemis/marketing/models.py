"""SQLAlchemy 2.x async ORM models for the Marketing OS domain.

Tables (all Postgres, all TIMESTAMPTZ timestamps, all JSONB blobs):
  signal_queue          — inbound market signals from scouts and manual intake
  scout_runs            — scout execution audit log (id is structured TEXT)
  campaign_candidates   — qualified signals promoted to campaign workspace
  campaign_briefs       — append-only assembled briefs for candidates
  content_assets        — reusable content pieces (drafts, docs, snippets)
  content_asset_links   — M2M: candidates ↔ content_assets with a role label
  campaign_deliverables — Writing Studio draft refs tied to a candidate
  rulesets              — versioned qualification logic per campaign family
  territory_config      — one-row-per-family hot/standard state routing config
  districts             — first-class district entity with tier/support flags
  district_tier_bands   — global editable district size bands
  approvals             — generic approval gate log (signals, writing gates, …)

Intentional improvements over the Node SQLite schema:
- TIMESTAMPTZ instead of INTEGER unix-seconds or TEXT datetime()
- JSONB instead of TEXT for all JSON columns
- BIGSERIAL PKs (except scout_runs which uses a structured TEXT key)
- owner_user_id BIGINT NULL on user-facing tables for multi-user readiness
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from artemis.db import Base

# ---------------------------------------------------------------------------
# Canonical signal lifecycle states (H1 — source of truth for tool schemas)
#
# These are the valid values for signal_queue.signal_status. The authoritative
# enum definition lives in artemis/marketing/state_machine.py (SignalState).
# This tuple is the documentation anchor; do NOT add states here without
# adding them to SignalState first.
#
# NOTE: held_pending_corroboration and suppressed_deprioritized have been
# observed in production rows (see hallucination-audit-2026-05-29.md) but are
# NOT valid enum members — H2 resolves the drift. Do NOT accept them in
# tool schemas until H2 completes.
# ---------------------------------------------------------------------------
CANONICAL_SIGNAL_STATES: tuple[str, ...] = (
    "pending_qualification",  # initial state written by scouts
    "qualified",  # passed qualification; promoted to campaign workspace
    "rejected_hard_filter",  # terminal — definitively not relevant
    "suppressed_stale",  # terminal — stale / superseded signal
    "approved",  # Gate-1 human approved (written to signal_status pending m3b cleanup)
    "rejected_at_gate_1",  # Gate-1 human rejected
    "snoozed",  # snooze-and-revisit
    "archived",  # terminal — manually archived
)


class SignalReasonCode(Base):
    """Registry of canonical signal reason codes (Josh spec v1).

    Append-only: hard deletes are blocked by a DB trigger.
    Soft-delete via is_active = False.
    """

    __tablename__ = "signal_reason_codes"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_scout_looks_for: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_urgency: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_scouts: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    campaign_families: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class SignalQueue(Base):
    """Inbound market signal — emitted by scouts or entered manually.

    reason_codes and provenance store the full structured JSON from the
    signal schema (see marketing-ops-v1/schemas/signal.md).
    """

    __tablename__ = "signal_queue"
    __table_args__ = (
        Index("idx_signal_queue_status_tier", "signal_status", "urgency_tier"),
        Index("idx_signal_queue_family_status", "campaign_family", "signal_status"),
        Index("idx_signal_queue_district", "district_id"),
        Index("idx_signal_queue_resolved_district", "resolved_district_id"),
        Index("idx_signal_queue_pipeline_run", "pipeline_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_run_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("pipeline_runs.id", name="fk_signal_queue_pipeline_run", ondelete="SET NULL"),
        nullable=True,
    )
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    campaign_family: Mapped[str] = mapped_column(Text, nullable=False)
    urgency_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default="standard")
    discovered_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    district_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DIST3: additive resolved FK — district_id (text) preserved for provenance.
    resolved_district_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("districts.id", name="fk_signal_queue_resolved_district", ondelete="SET NULL"),
        nullable=True,
    )
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_codes: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    provenance: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    qualification_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    signal_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending_qualification",
        server_default="pending_qualification",
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    candidates: Mapped[list[CampaignCandidate]] = relationship(
        "CampaignCandidate",
        back_populates="source_signal",
        lazy="noload",
    )
    candidate_signals: Mapped[list[CampaignCandidateSignal]] = relationship(
        "CampaignCandidateSignal",
        back_populates="signal",
        cascade="all, delete-orphan",
        lazy="noload",
    )


class ScoutRun(Base):
    """Scout execution audit log.

    id format: scout_run_YYYYMMDD_<type>_<uuid8>
    status enum: pending | dry_run_passed | committed | failed
    """

    __tablename__ = "scout_runs"
    __table_args__ = (Index("idx_scout_runs_type_started", "scout_type", "started_at"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scout_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    dry_run_summary: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_signal_ids: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    errors: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class CampaignCandidate(Base):
    """A signal that has been approved and promoted to the campaign workspace.

    deliverables is a legacy JSONB column kept for backwards compatibility
    with the Node app's campaign_candidates.deliverables_json column.
    """

    __tablename__ = "campaign_candidates"
    __table_args__ = (
        Index("idx_campaign_candidates_decision_state", "decision_state"),
        Index("idx_campaign_candidates_family", "campaign_family"),
        Index("idx_campaign_candidates_updated", "updated_at"),
        Index("idx_campaign_candidates_source_signal", "source_signal_id"),
        Index("idx_campaign_candidates_predecessor", "predecessor_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_signal_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("signal_queue.id", name="fk_candidates_signal", ondelete="SET NULL"),
        nullable=True,
    )
    campaign_family: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default="human_gate_1")
    decision_state: Mapped[str] = mapped_column(
        Text, nullable=False, default="created", server_default="created"
    )
    workspace_state: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending_content", server_default="pending_content"
    )
    ruleset_version_at_qualification: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    deliverables: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiation_proposal_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    target_scope_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    deliverable_types_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    initiated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    initiated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    predecessor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "campaign_candidates.id", name="fk_campaign_candidates_predecessor", ondelete="SET NULL"
        ),
        nullable=True,
    )
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    source_signal: Mapped[SignalQueue | None] = relationship(
        "SignalQueue",
        back_populates="candidates",
        lazy="noload",
    )
    candidate_signals: Mapped[list[CampaignCandidateSignal]] = relationship(
        "CampaignCandidateSignal",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    predecessor: Mapped[CampaignCandidate | None] = relationship(
        "CampaignCandidate",
        remote_side="CampaignCandidate.id",
        back_populates="successors",
        lazy="noload",
    )
    successors: Mapped[list[CampaignCandidate]] = relationship(
        "CampaignCandidate",
        back_populates="predecessor",
        lazy="noload",
    )
    briefs: Mapped[list[CampaignBrief]] = relationship(
        "CampaignBrief",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    asset_links: Mapped[list[ContentAssetLink]] = relationship(
        "ContentAssetLink",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    deliverable_rows: Mapped[list[CampaignDeliverable]] = relationship(
        "CampaignDeliverable",
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="noload",
    )


class DeliverableType(Base):
    """Registry of campaign deliverable types."""

    __tablename__ = "deliverable_types"
    __table_args__ = (Index("idx_deliverable_types_active_order", "active", "display_order"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    default_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class CampaignCandidateSignal(Base):
    """Many-to-many join: campaign_candidates ↔ signal_queue."""

    __tablename__ = "campaign_candidate_signals"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "signal_id", name="uq_campaign_candidate_signals_candidate_signal"
        ),
        Index("idx_campaign_candidate_signals_candidate", "candidate_id"),
        Index("idx_campaign_candidate_signals_signal", "signal_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "campaign_candidates.id",
            name="fk_campaign_candidate_signals_candidate",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    signal_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "signal_queue.id", name="fk_campaign_candidate_signals_signal", ondelete="CASCADE"
        ),
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    attached_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    candidate: Mapped[CampaignCandidate] = relationship(
        "CampaignCandidate",
        back_populates="candidate_signals",
        lazy="noload",
    )
    signal: Mapped[SignalQueue] = relationship(
        "SignalQueue",
        back_populates="candidate_signals",
        lazy="noload",
    )


class CampaignBrief(Base):
    """Assembled brief for a campaign candidate.

    Append-only: re-assembly creates a new row. Retrieve the latest via
    ORDER BY generated_at DESC LIMIT 1.
    """

    __tablename__ = "campaign_briefs"
    __table_args__ = (Index("idx_campaign_briefs_candidate", "candidate_id", "generated_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("campaign_candidates.id", name="fk_briefs_candidate", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'{}'")
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    generated_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    candidate: Mapped[CampaignCandidate] = relationship(
        "CampaignCandidate",
        back_populates="briefs",
        lazy="noload",
    )


class ContentAsset(Base):
    """A reusable content piece — draft, doc, snippet, asset bundle."""

    __tablename__ = "content_assets"
    __table_args__ = (
        Index("idx_content_assets_status", "status"),
        Index("idx_content_assets_type", "asset_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Note: 'metadata' is reserved by SQLAlchemy's Declarative API;
    # column is named asset_metadata in the ORM, maps to 'metadata' in the DB.
    asset_metadata: Mapped[Any] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="'{}'"
    )
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    links: Mapped[list[ContentAssetLink]] = relationship(
        "ContentAssetLink",
        back_populates="asset",
        cascade="all, delete-orphan",
        lazy="noload",
    )


class ContentAssetLink(Base):
    """Many-to-many link: campaign_candidates ↔ content_assets with a role label."""

    __tablename__ = "content_asset_links"
    __table_args__ = (
        UniqueConstraint("candidate_id", "asset_id", name="uq_content_asset_links_candidate_asset"),
        Index("idx_content_asset_links_candidate", "candidate_id"),
        Index("idx_content_asset_links_asset", "asset_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("campaign_candidates.id", name="fk_asset_links_candidate", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("content_assets.id", name="fk_asset_links_asset", ondelete="CASCADE"),
        nullable=False,
    )
    link_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    candidate: Mapped[CampaignCandidate] = relationship(
        "CampaignCandidate",
        back_populates="asset_links",
        lazy="noload",
    )
    asset: Mapped[ContentAsset] = relationship(
        "ContentAsset",
        back_populates="links",
        lazy="noload",
    )


class CampaignDeliverable(Base):
    """Writing Studio draft reference tied to a campaign candidate."""

    __tablename__ = "campaign_deliverables"
    __table_args__ = (
        Index("idx_campaign_deliverables_candidate", "candidate_id"),
        Index("idx_campaign_deliverables_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("campaign_candidates.id", name="fk_deliverables_candidate", ondelete="CASCADE"),
        nullable=False,
    )
    deliverable_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="generating")
    # 'metadata' is reserved by SQLAlchemy's Declarative API; alias to DB column name.
    deliverable_metadata: Mapped[Any] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="'{}'"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    candidate: Mapped[CampaignCandidate] = relationship(
        "CampaignCandidate",
        back_populates="deliverable_rows",
        lazy="noload",
    )


class Ruleset(Base):
    """Versioned qualification logic for a campaign family.

    state enum: draft | active | archived
    Only one row per family should be active at a time (enforced by repository logic).
    """

    __tablename__ = "rulesets"
    __table_args__ = (
        UniqueConstraint("family", "version_tag", name="uq_rulesets_family_version"),
        Index("idx_rulesets_family_state", "family", "state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    version_tag: Mapped[str] = mapped_column(Text, nullable=False)
    hard_filters: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    weighted_signals: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    qualitative_rubrics: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class TerritoryConfig(Base):
    """One-row-per-family routing config for hot vs. standard state targeting.

    The Node schema stored one row per (family, state_code) in signal_territory_config.
    The brief spec calls for a single row per family with hot_states / standard_states
    as JSONB arrays. This is the Python improvement.
    """

    __tablename__ = "territory_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    family: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    hot_states: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    standard_states: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    unlisted_multiplier: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.85")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class DistrictTierBand(Base):
    """Global editable enrollment bands for district tier classification."""

    __tablename__ = "district_tier_bands"
    __table_args__ = (
        CheckConstraint("tier IN ('D1', 'D2', 'D3', 'D4')", name="ck_district_tier_bands_tier"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tier: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    min_enrollment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_enrollment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class District(Base):
    """First-class district entity with lossless support and tier state."""

    __tablename__ = "districts"
    __table_args__ = (
        CheckConstraint(
            "tier IS NULL OR tier IN ('D1', 'D2', 'D3', 'D4')",
            name="ck_districts_tier",
        ),
        CheckConstraint(
            "classification_source IN ('nces', 'manual', 'unresolved')",
            name="ck_districts_classification_source",
        ),
        Index("idx_districts_state", "state"),
        Index("idx_districts_tier", "tier"),
        Index("idx_districts_supported", "supported"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    nces_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    enrollment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    supported: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    on_skip_list: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    classification_source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="unresolved"
    )
    classified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class Approval(Base):
    """Generic approval gate record.

    kind enum examples: signal_approval | writing_gate_2 | ruleset_activation
    status enum: pending | approved | rejected
    """

    __tablename__ = "approvals"
    __table_args__ = (
        Index("idx_approvals_status_created", "status", "created_at"),
        Index("idx_approvals_kind_subject", "kind", "subject_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    decided_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    decision_payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    # PIPE4 gate rendering context — populated by human_gate_executor at gate-fire time.
    # NULL for non-PIPE4 approvals; UI detects absence and falls back to existing path.
    pipe4_context: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class QualifierRuleApplication(Base):
    """Audit log for every qualifier rule application.

    Written atomically with any priority/status change.
    Never deleted — append-only by contract.
    """

    __tablename__ = "qualifier_rule_applications"
    __table_args__ = (Index("idx_qra_signal_applied", "signal_id", "applied_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("signal_queue.id", name="fk_qra_signal", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False)  # skip | suppress | boost
    applied_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    from_priority: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_priority: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SkippedSignal(Base):
    """Hard-skip visibility log.

    Signals killed by apply_hard_skips are logged here for ops review.
    Never deleted — append-only.
    """

    __tablename__ = "skipped_signals"
    __table_args__ = (Index("idx_skipped_signals_district_created", "district_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("signal_queue.id", name="fk_skipped_signal", ondelete="CASCADE"),
        nullable=False,
    )
    district_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class DistrictDataMeta(Base):
    """Singleton metadata row stamped after each NCES district bulk load.

    Only one logical row exists (upserted on each load).  If the table is
    empty the endpoint/UI show an honest "no data loaded" state.
    """

    __tablename__ = "district_data_meta"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    school_year: Mapped[str] = mapped_column(Text, nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class MarketingClusteringConfig(Base):
    """Singleton config row for deterministic candidate clustering."""

    __tablename__ = "marketing_clustering_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cluster_window_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignStateTransition(Base):
    """Append-only audit log for every campaign lifecycle state transition.

    One row per transition. Never deleted or updated — append-only by contract.
    Indexed on (entity_type, entity_id, transitioned_at) for per-entity history.
    """

    __tablename__ = "campaign_state_transitions"
    __table_args__ = (
        Index(
            "idx_cst_entity_type_id_at",
            "entity_type",
            "entity_id",
            "transitioned_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # signal | brief | workspace | deliverable
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_state: Mapped[str] = mapped_column(Text, nullable=False)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    transitioned_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class DistrictContact(Base):
    """District-side recipient for outbound campaign sends.

    Lossless: never hard-deleted — only deactivated via active=False.
    source enum: 'manual' | 'salesforce' (Salesforce sync seam, not built yet).
    """

    __tablename__ = "district_contacts"
    __table_args__ = (
        CheckConstraint("source IN ('manual','salesforce')", name="ck_district_contacts_source"),
        Index("idx_district_contacts_district_active", "district_id", "active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    district_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("districts.id", name="fk_district_contacts_district", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
