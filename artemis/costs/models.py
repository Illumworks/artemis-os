"""ORM model for the cost_events table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class CostEvent(Base):
    """Append-only cost ledger. One row per LLM call.

    Lossless invariant: rows are NEVER deleted. Errored calls write a row with
    is_error=True. Rate snapshot columns are frozen at write time so historical
    cost math doesn't drift when pricing.py updates.
    """

    __tablename__ = "cost_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # When + identity
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    # Provider attribution
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # Allowed: 'anthropic' | 'openai' | 'gemini' | 'claude-code' | 'codex' | 'lm-studio'
    model: Mapped[str] = mapped_column(Text, nullable=False)
    provider_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Allowed: 'cli' | 'api'

    # Feature attribution
    feature_tag: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Allowed: 'agent_run', 'floating_artemis', 'workflow', 'pipeline',
    # 'marketing_scout', 'marketing_brief', 'meeting_summary', 'memory_consolidation',
    # 'memory_graph_extraction', 'trajectory_summary', 'signal_qualifier',
    # 'background', 'unknown'

    source_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'agent_run' | 'fa_message' | 'workflow_run' | 'tool_invocation'
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FK-ish reference to the source row; not enforced at DB level.

    agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    workflow_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    # Campaign attribution — tagged at campaign-tied call sites (brief assembly,
    # content drafting, sends). Historical rows are NULL ("unattributed"). The
    # per-campaign rollup endpoint filters WHERE campaign_candidate_id = :id.
    campaign_candidate_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    # Token counts
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cache_creation_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    cache_read_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )

    # Rate snapshot — frozen at call time; never recomputed from PRICING
    input_rate_per_million: Mapped[float] = mapped_column(Float, nullable=False)
    output_rate_per_million: Mapped[float] = mapped_column(Float, nullable=False)
    cache_write_rate_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    cache_read_rate_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )

    # Computed cost — frozen at write time
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")

    # Optional context
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    error_kind: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_cost_events_created_feature", "created_at", "feature_tag"),
        Index("idx_cost_events_provider_model", "provider", "model"),
    )
