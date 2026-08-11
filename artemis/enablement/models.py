"""ORM model for enablement_assets — Kai's read-only knowledge store.

One row per enablement asset (doc, image, video, etc.).  The primary unique
key is ``drive_file_id`` — every row represents a single Drive asset.  When a
sheet row has no drive_file_id a stable slug is synthesised from asset_name
and stored in that column instead (prefix ``slug:``) so upserts remain
idempotent across repeated syncs.

Embedding: reuses the same all-MiniLM-L6-v2 / 384-dim vector type used by the
memory keystone (``artemis.memory.models.Vector``).  The embedding is computed
from ``title + summary + tags + transcript_text`` at sync time.

source_scope values:
  "enablement"  — internal enablement content (default).
  "shared"      — cross-team shared content that other agents may surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base
from artemis.memory.models import Vector


class EnablementAsset(Base):
    """One row per enablement asset — keyed by drive_file_id for idempotent upsert."""

    __tablename__ = "enablement_assets"
    __table_args__ = (
        UniqueConstraint("drive_file_id", name="uq_enablement_assets_drive_file_id"),
        Index("idx_enablement_assets_type", "type"),
        Index("idx_enablement_assets_status", "status"),
        Index("idx_enablement_assets_source_scope", "source_scope"),
        Index("idx_enablement_assets_updated_at", "updated_at"),
        Index("idx_enablement_assets_source_sheet", "source_sheet"),
        Index("idx_enablement_assets_summary_status", "summary_status"),
        Index("idx_enablement_assets_format", "format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # --- identity / sheet mirror columns ---
    drive_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    asset_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "doc" | "image" | "video" | free-form string from sheet
    type: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored as a Postgres text array (null-safe; empty array if not present)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full transcript text for video search (nullable — absent for non-video assets)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "enablement" (default) or "shared" for cross-team content
    source_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="'enablement'")

    # --- multi-link surfacing (0098) ---
    # List of structured link objects:
    #   {role, label, url, visibility: "customer"|"internal", on_request: bool, make_copy: bool}
    # Kai's surfacing rules operate on these explicit flags (customer-default,
    # editable/internal on explicit request, make-a-copy reminder).
    links: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # Indexable body folded into the embedding + keyword search (script-doc text,
    # slide text, full Google-Doc text, walkthrough notes). Separate from summary.
    searchable_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provenance (the idempotency anchor remains drive_file_id).
    source_sheet: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_row: Mapped[str | None] = mapped_column(Text, nullable=True)
    # View-only decks/handouts the CSM must copy before editing.
    requires_copy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # --- AI-drafted enrichment + human review (0105) ---
    # NULL | "ai_draft" | "enablement_verified" | "needs_revision".
    # An "ai_draft" summary is NOT catalog fact: Kai must caveat it. This field
    # is what keeps the speed of AI-written summaries from recreating the
    # 2026-08-10 problem of confident unverified claims.
    summary_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_reviewed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_reviewed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Reviewer's note when sending a draft back; feeds the regeneration prompt.
    summary_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_generated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # --- facets that produced visible misses in #enablement-library (0105) ---
    # Sara asked for a Google Slides deck and got a PDF: format was never captured.
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "Reading Risk report: K-8 or PK-8?" was unanswerable with no grade metadata.
    grade_range: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- embedding (384-dim, all-MiniLM-L6-v2, same as memory keystone) ---
    embedding: Mapped[Any] = mapped_column(Vector(384), nullable=True)

    # --- extra metadata (any additional sheet columns captured as JSON) ---
    extra: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # --- timestamps ---
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
