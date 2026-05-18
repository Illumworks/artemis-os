"""Raw inputs: append-only verbatim capture with SHA-256 hash chain.

Design invariant — lossless by structural guarantee:
  Every memory-write source lands here first. Derived tables FK back via
  raw_input_id. Even if all derived tables are corrupted or truncated,
  the raw source is reconstructable from this table and the cold archive.

Canonical insert path: call insert_raw_input() inside an active transaction.
The function serializes concurrent writes via SELECT FOR UPDATE on the last row.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Index, Text, select
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base
from artemis.memory.hashchain import canonical_form, compute_this_hash, payload_sha256

_logger = logging.getLogger(__name__)


class RawInput(Base):
    """Verbatim, append-only, hash-chained record of every memory write source.

    payload is NULLed on archiving; payload_hash is preserved so rehydration
    can verify integrity. The row itself is never deleted — it remains as a
    placeholder that keeps the hash chain continuous.
    """

    __tablename__ = "raw_inputs"
    __table_args__ = (
        Index("ix_raw_inputs_scope", "scope_kind", "scope_id", "created_at"),
        Index("ix_raw_inputs_source", "source_kind", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_kind: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    this_hash: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


async def insert_raw_input(
    session: AsyncSession,
    *,
    source_kind: str,
    source_id: str | None = None,
    actor: str | None = None,
    scope_kind: str,
    scope_id: str,
    payload: dict[str, Any],
    created_at: datetime | None = None,
) -> RawInput:
    """Append a raw input row, computing and linking the hash chain.

    Must be called inside an active transaction (session.begin() context).
    Serializes concurrent writes by locking the current last row.

    created_at defaults to now(UTC); callers may provide an explicit value
    (e.g. for seeding historical data in tests).
    """
    ts = created_at if created_at is not None else datetime.now(UTC)

    # Lock the current tail row to serialize concurrent inserts.
    # The FOR UPDATE ensures no other writer can sneak between our read
    # of prev_hash and our insert.
    tail_result = await session.execute(
        select(RawInput).order_by(RawInput.id.desc()).limit(1).with_for_update()
    )
    tail = tail_result.scalar_one_or_none()
    prev_hash: str | None = tail.this_hash if tail is not None else None

    canon = canonical_form(
        source_kind=source_kind,
        source_id=source_id,
        actor=actor,
        scope_kind=scope_kind,
        scope_id=scope_id,
        payload=payload,
        created_at=ts,
        prev_hash=prev_hash,
    )
    this_hash = compute_this_hash(canon)
    p_hash = payload_sha256(payload)

    row = RawInput(
        created_at=ts,
        source_kind=source_kind,
        source_id=source_id,
        actor=actor,
        scope_kind=scope_kind,
        scope_id=scope_id,
        payload=payload,
        payload_hash=p_hash,
        prev_hash=prev_hash,
        this_hash=this_hash,
    )
    session.add(row)
    await session.flush()  # populate row.id without committing
    return row
