"""ORM models for the routing control surface.

Two tables:
  feature_routing_overrides  — per-feature cascade overrides
  routing_changes_log        — lossless audit of every routing change
  app_settings               — key/value store (hosts default_cascade config)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from artemis.db import Base


class FeatureRoutingOverride(Base):
    """Per-feature cascade override.

    One row per feature_tag. Re-applying same feature_tag updates rather than
    creates duplicates (endpoint uses ON CONFLICT DO UPDATE). Setting
    ``active=False`` is the "delete" operation — lossless; the row is kept.
    """

    __tablename__ = "feature_routing_overrides"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feature_tag: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    cascade: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="operator")


class RoutingChangeLog(Base):
    """Append-only audit log for every routing change.

    Rows are never deleted. Override "delete" (active=False) writes a log entry
    here just like any other change. ``scope`` distinguishes the type of change:
    'feature' | 'default_cascade' | 'agent'.
    """

    __tablename__ = "routing_changes_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    changed_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="operator")
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppSettings(Base):
    """Key/value settings store.

    Used to persist the default provider cascade at runtime so changes
    take effect without a restart. Key 'default_cascade' stores a JSON
    list of provider strings.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
