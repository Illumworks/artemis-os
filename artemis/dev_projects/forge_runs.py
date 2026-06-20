"""Repository helpers for ForgeRun and ForgeRunLog.

Exposes five async functions:

  create_run                    -- open a new build run (status="running")
  append_log                    -- append one event to a run's log (LOSSLESS)
  complete_run                  -- mark a run completed / failed / cancelled
  get_active_run_for_session    -- the most recent running run for a session
  get_run_log                   -- ordered log for a run

IMPORTANT: ForgeRunLog is APPEND-ONLY and is NEVER deleted or pruned (lossless
memory rule). There is intentionally no delete or prune function in this module.

Caller manages the transaction/commit; functions flush but do not commit, matching
the pattern in workspace_memory.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.dev_projects.models import ForgeRun, ForgeRunLog


async def create_run(
    session: AsyncSession,
    *,
    run_id: str,
    dev_session_id: int,
    project_id: int,
) -> ForgeRun:
    """Insert a new ForgeRun with status="running" and return it.

    The caller is responsible for committing (or the outer transaction will
    commit).  flush() assigns the PK without ending the transaction.
    """
    run = ForgeRun(
        run_id=run_id,
        dev_session_id=dev_session_id,
        project_id=project_id,
        status="running",
    )
    session.add(run)
    await session.flush()
    return run


async def append_log(
    session: AsyncSession,
    *,
    run_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
) -> ForgeRunLog:
    """Append one event to the log for *run_id*.

    seq is computed as max(seq)+1 for the run (starting at 0 when the log is
    empty).  The SELECT MAX is issued within the same transaction so sequential
    appends within a single turn are correctly ordered.

    This is the only write path for ForgeRunLog; there is no update or delete.
    """
    result = await session.execute(
        select(func.coalesce(func.max(ForgeRunLog.seq), -1)).where(
            ForgeRunLog.run_id == run_id
        )
    )
    current_max: int = result.scalar_one()
    next_seq = current_max + 1

    entry = ForgeRunLog(
        run_id=run_id,
        seq=next_seq,
        kind=kind,
        payload=payload if payload is not None else {},
    )
    session.add(entry)
    await session.flush()
    return entry


async def complete_run(
    session: AsyncSession,
    *,
    run_id: str,
    status: str = "completed",
    error: str | None = None,
) -> None:
    """Set status and completed_at on an existing ForgeRun.

    *status* should be one of: completed | failed | cancelled.
    *error* is stored verbatim when provided (e.g. on status="failed").

    Flushes but does not commit.
    """
    result = await session.execute(
        select(ForgeRun).where(ForgeRun.run_id == run_id)
    )
    run = result.scalar_one()
    run.status = status
    run.completed_at = datetime.now(UTC)
    if error is not None:
        run.error = error
    await session.flush()


async def get_active_run_for_session(
    session: AsyncSession,
    dev_session_id: int,
) -> ForgeRun | None:
    """Return the most recent running ForgeRun for *dev_session_id*, or None."""
    result = await session.execute(
        select(ForgeRun)
        .where(
            ForgeRun.dev_session_id == dev_session_id,
            ForgeRun.status == "running",
        )
        .order_by(ForgeRun.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_run_log(
    session: AsyncSession,
    run_id: str,
) -> list[ForgeRunLog]:
    """Return all log entries for *run_id* ordered by seq ascending."""
    result = await session.execute(
        select(ForgeRunLog)
        .where(ForgeRunLog.run_id == run_id)
        .order_by(ForgeRunLog.seq.asc())
    )
    return list(result.scalars().all())
