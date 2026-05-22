"""SQLAlchemy ORM models for the Connectors domain.

Tables:
  connectors       — per-source credential sets (API keys etc.)
  agent_connectors — many-to-many join: agents ↔ connectors
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class Connector(Base):
    """A configured credential set for one external source."""

    __tablename__ = "connectors"
    __table_args__ = (
        Index("idx_connectors_kind", "kind"),
        Index("idx_connectors_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Encrypted credentials stored as a base64 Fernet blob inside JSONB.
    # Schema: {"blob": "<base64-fernet-ciphertext>"}
    credentials: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'{}'::jsonb")
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="'active'"
    )  # active | disabled | needs_reauth
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Free-form metadata: OAuth expiry, last_validated_at, etc.
    metadata_: Mapped[Any] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class AgentConnector(Base):
    """Many-to-many join: agents ↔ connectors, scoped by tool namespace."""

    __tablename__ = "agent_connectors"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "connector_id",
            "tool_namespace",
            name="uq_agent_connectors_triple",
        ),
        Index("idx_agent_connectors_agent_id", "agent_id"),
        Index("idx_agent_connectors_connector_id", "connector_id"),
    )

    agent_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    connector_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, nullable=False)
    tool_namespace: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
