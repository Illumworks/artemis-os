"""SQLAlchemy ORM models for the integrations domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, TIMESTAMP, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class Integration(Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("provider", "workspace_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    bot_user_id: Mapped[str | None] = mapped_column(Text)
    encrypted_credentials: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    connected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="now()"
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="'active'")
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="'{}'::jsonb"
    )


class SlackInboundMessage(Base):
    __tablename__ = "slack_inbound_messages"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    team_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    thread_ts: Mapped[str | None] = mapped_column(Text)
    routed_to_session_id: Mapped[int | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="now()"
    )
