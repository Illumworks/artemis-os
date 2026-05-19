"""ORM model for brief_snapshots."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class BriefSnapshot(Base):
    __tablename__ = "brief_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    brief_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sources_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
