"""ORM model for persisting Argus research dispatch requests.

argus_research_requests tracks every ``dispatch_research`` call so that a
process restart -- or, since ARGUS-1, the ordinary death of the per-turn MCP
subprocess ``dispatch_research`` runs in -- can recover and complete the work.

Lifecycle:
    pending  → enqueued, not yet claimed by the app-process claimer
    running  → claimed (``claimed_at`` set); a ``running`` row older than
               ``settings.argus_claim_stale_minutes`` is presumed orphaned by a
               crash mid-research and becomes re-claimable (back to ``pending``
               semantics for claim purposes, but the status value itself stays
               ``running`` until the reclaim's atomic UPDATE moves it)
    done     → research + Slack post completed successfully
    failed   → attempts >= 3 (hard cap), fallback Slack message posted

See ``artemis/floating_artemis/tools/argus_tools.py`` (``_claim_next_request``,
``run_claim_tick``) for the claimer. ``recover_pending_requests`` (app startup)
is a backstop that runs one claim pass immediately rather than waiting for the
first scheduled tick -- it is not a separate mechanism from the claimer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class ArgusResearchRequest(Base):
    """Persistent record for a single Argus dispatch invocation."""

    __tablename__ = "argus_research_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    district_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Resolved at dispatch time from the session context.
    channel_id: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # Optional triggering context.
    signal: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    triggering_signal_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="pending",
        # valid values: 'pending' | 'running' | 'done' | 'failed'
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # ARGUS-1: set (to now()) by the atomic claim UPDATE when a row moves to
    # 'running'. Never cleared afterward (on release-to-pending, on done, or on
    # failed) -- it is left as an audit trail of "when was this row last
    # claimed", and correctness never depends on it being null for a
    # non-'running' row: the claim query's WHERE clause only ever inspects
    # claimed_at for rows that are CURRENTLY status='running'.
    claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
