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
    Float,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from artemis.db import Base


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
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    campaign_family: Mapped[str] = mapped_column(Text, nullable=False)
    urgency_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default="standard")
    discovered_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    district_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_codes: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    provenance: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    qualification_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    signal_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="in_inbox")
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
        Text, nullable=False, server_default="pending_review"
    )
    workspace_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="created")
    ruleset_version_at_qualification: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    deliverables: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
