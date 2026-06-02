"""Per-agent-run context passed to every tool factory within that run."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ToolContext:
    """Immutable per-agent-run context. Lifetime: one agent run."""

    session: AsyncSession
    agent_id: str
    """Dotted agent identifier, e.g. 'marketing.scout.regional_news'."""
    agent_db_id: int
    """Primary key of the Agent row."""
    agent_run_id: str
    """UUID string — for provenance / trace linkage."""
    pipeline_run_id: str | None
    """Set when called from a pipeline executor; None for standalone runs."""
