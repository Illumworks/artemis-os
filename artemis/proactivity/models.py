"""ORM models for proactive scheduled deliveries and commitments."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from artemis.db import Base


class RadarSurfacedItem(Base):
    """Dedup + dismiss ledger for awaiting-reply radar items.

    Each row = one Slack mention or Gmail thread Artemis has surfaced to Jon.
    Rows are never deleted (lossless); dismissed_at != NULL stops re-nagging.
    """

    __tablename__ = "radar_surfaced_items"
    __table_args__ = (
        UniqueConstraint("item_type", "item_key", name="uq_radar_surfaced_item_type_key"),
        Index("idx_radar_surfaced_items_active", "item_type", "dismissed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # "slack_mention" | "gmail_thread"
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Stable opaque dedup key.
    # Slack: "<channel_id>:<thread_ts>"   Gmail: "<threadId>"
    item_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Short human-readable label for audit (never drives logic).
    label: Mapped[str] = mapped_column(Text, nullable=False)
    # Permalink / web link surfaced in nudge text.
    permalink: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_surfaced_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
    last_surfaced_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
    # NULL = active (keep nagging on cadence).  Set = Jon said "drop it".
    dismissed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class MorningBriefDelivery(Base):
    """Once-per-day delivery reservation + outcome for the morning brief."""

    __tablename__ = "morning_brief_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "delivery_kind",
            "provider",
            "recipient_id",
            "delivery_date",
            name="uq_morning_brief_delivery_once_per_day",
        ),
        CheckConstraint(
            "status IN ('reserved', 'sent', 'failed')",
            name="ck_morning_brief_deliveries_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_kind: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_id: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="reserved")
    snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("brief_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reserved_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )


class OkrCheckinBreadcrumb(Base):
    """Breadcrumb left when a Friday OKR check-in is posted.

    Exists for the TTL window (expires end of following Monday) so that
    handle_turn can detect a live check-in and inject OKR-reconcile context
    into the next DM turn, mapping Jon's word-dump to specific KRs and
    proposing update_okr_kr via the layer-3 confirm path.

    Lossless invariant: rows are never deleted. Expiry is via expires_at + completed_at.
    """

    __tablename__ = "okr_checkin_breadcrumbs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Slack user-ID of the check-in recipient (used to scope injection).
    recipient_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Serialised snapshot: list[{kr_id, kr_title, objective_title, prog, target_text}]
    kr_snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    # The full check-in text as delivered (for reference / audit).
    proposal_text: Mapped[str] = mapped_column(Text, nullable=False)
    # TTL: expires end of the following Monday so the window covers the whole weekend.
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Staged KR updates awaiting operator 'go'. List of {kr_id, progress, basis}.
    # Null / empty when nothing is staged. Written by stage_okr_updates (layer-1 tool);
    # applied server-side in route_inbound on explicit 'go'; cleared on apply or 'no'.
    staged_updates: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # Set when reconcile completes (layer-3 applied or declined), superseding the crumb.
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )


class Commitment(Base):
    """Lifecycle table for proactive follow-up commitments.

    Commitments are not pure memory observations because they need explicit
    state transitions (active -> snoozed -> done), dedupe, and notification
    timestamps. A mirrored memory observation is written separately so named
    agents can still recall commitments conversationally.
    """

    __tablename__ = "commitments"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "text",
            name="uq_commitments_source_text",
        ),
        CheckConstraint(
            "status IN ('proposed', 'active', 'snoozed', 'done', 'dismissed')",
            name="ck_commitments_status",
        ),
        Index("idx_commitments_status_due", "status", "due"),
        Index("idx_commitments_owner", "owner_user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    due: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False, server_default="personal_ops")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    snoozed_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )


class CommitmentDecision(Base):
    """Append-only learning-signal table for commitment approve/dismiss decisions.

    Every time the owner approves or dismisses a proposed commitment, one row is
    written here with a feature snapshot.  Rows are never deleted (lossless /
    audit invariant).  The features JSONB is the labeled dataset for Phase 2-3
    learning (shrink the proposed list, rank by predicted approval).

    Invariant: decision is immutable after insert — do NOT update rows.
    """

    __tablename__ = "commitment_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve', 'dismiss')",
            name="ck_commitment_decisions_decision",
        ),
        Index("idx_commitment_decisions_commitment_id", "commitment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    commitment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commitments.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'approve' → status transitions to 'active'; 'dismiss' → 'dismissed'
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    # Feature snapshot for future learning:
    # {owner_is_owner, had_due, sensitivity, source_type, source_id, meeting_title}
    features: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    decided_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )


class ProposedAction(Base):
    """Lifecycle table for the propose→confirm agency-writes gate.

    Every side-effecting action Artemis wants to take on Jon's behalf is
    inserted here in ``proposed`` state.  Jon's reply (yes/no) in the DM
    transitions it to ``approved``/``rejected``; only an ``approved`` row can
    reach ``executed`` or ``failed``.  Rows are never deleted (lossless /
    audit invariant).

    Safety invariants enforced in application code:
    - No execution path exists without a prior approved transition.
    - A proposal executes at most once (double-yes is a no-op).
    - The payload approved is the payload executed (immutable after insert).
    - Every state transition is appended to the JSONB ``audit`` list.
    """

    __tablename__ = "proposed_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ("
            "'calendar.create',"
            "'calendar.update',"
            "'calendar.respond',"
            "'slack.send',"
            "'jira.create',"
            "'gmail.send'"
            ")",
            name="ck_proposed_actions_action_type",
        ),
        CheckConstraint(
            "status IN ('proposed','approved','rejected','executed','failed','expired')",
            name="ck_proposed_actions_status",
        ),
        Index("idx_proposed_actions_target_status", "target_user_id", "status"),
        Index("idx_proposed_actions_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    preview: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="proposed")
    requested_by: Mapped[str] = mapped_column(Text, nullable=False)
    target_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    executed_result: Mapped[Any] = mapped_column(JSONB, nullable=True)
    audit: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
