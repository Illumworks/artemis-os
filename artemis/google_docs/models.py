"""ORM models for per-user Google credentials."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base
from artemis.google_docs.crypto_types import EncryptedToken


class GoogleCredential(Base):
    """OAuth credential row for one Artemis user + account purpose."""

    __tablename__ = "google_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "purpose", name="uq_google_credentials_user_purpose"),
        Index("idx_google_credentials_connected_email", "connected_email"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False, server_default="personal")
    access_token: Mapped[str] = mapped_column(EncryptedToken, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(EncryptedToken, nullable=True)
    expiry: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
