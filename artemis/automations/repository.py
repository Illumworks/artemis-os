"""Async repository helpers for the Automations domain (OP1).

Conventions:
- Raise ValueError for not-found conditions (caller maps to 404).
- No business logic — just DB read/write. Callers own commit/rollback.
- Soft delete only: archive() sets status=archived + archived_at, never deletes.
- Latest-run embedding uses a single LEFT JOIN, not N+1.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.automations.models import Automation, AutomationRun

# ── Automations CRUD ──────────────────────────────────────────────────────────


async def create_automation(session: AsyncSession, **kwargs: Any) -> Automation:
    automation_id = kwargs.pop("id", None) or str(uuid.uuid4())
    auto = Automation(id=automation_id, **kwargs)
    session.add(auto)
    await session.flush()
    await session.refresh(auto)
    return auto


async def get_automation(session: AsyncSession, automation_id: str) -> Automation:
    result = await session.execute(
        select(Automation).where(Automation.id == automation_id).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Automation '{automation_id}' not found")
    return row


async def list_automations(
    session: AsyncSession,
    *,
    status: str | None = None,
    owner_user_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> list[tuple[Automation, AutomationRun | None]]:
    """List automations with latest_run embedded via a lateral subquery.

    Returns list of (Automation, AutomationRun|None) tuples — single query
    (automation + correlated latest-run join).

    Excludes archived by default unless status='archived' is requested.
    """
    # Lateral subquery: correlates on automations.id, returns one AutomationRun row
    run_alias = AutomationRun.__table__.alias("latest_run")
    lateral_sq = (
        select(run_alias)
        .where(run_alias.c.automation_id == Automation.id)
        .order_by(run_alias.c.created_at.desc())
        .limit(1)
        .correlate(Automation.__table__)
        .lateral("latest_run")
    )

    # Select Automation ORM + all columns of the lateral alias
    q = (
        select(Automation, lateral_sq)
        .outerjoin(lateral_sq, text("true"))
        .order_by(Automation.created_at.desc())
        .limit(limit)
    )

    q = q.where(Automation.status == status) if status else q.where(Automation.status != "archived")

    if owner_user_id:
        q = q.where(Automation.owner_user_id == owner_user_id)

    if cursor:
        q = q.where(Automation.created_at < text(f"'{cursor}'::timestamptz"))

    result = await session.execute(q)
    pairs: list[tuple[Automation, AutomationRun | None]] = []
    for row in result.all():
        auto_obj: Automation = row[0]
        # The remaining columns come from the lateral subquery as raw scalars.
        # run_alias columns in order: id, automation_id, status, trigger,
        # triggered_by, started_at, completed_at, target_run_id,
        # error_message, metadata, created_at
        run_row_id = row[1]  # lateral first col = id
        if run_row_id is None:
            pairs.append((auto_obj, None))
        else:
            # Reconstruct a transient AutomationRun for the caller.
            run_obj = AutomationRun(
                id=row[1],
                automation_id=row[2],
                status=row[3],
                trigger=row[4],
                triggered_by=row[5],
                started_at=row[6],
                completed_at=row[7],
                target_run_id=row[8],
                error_message=row[9],
                metadata_=row[10],
                created_at=row[11],
            )
            pairs.append((auto_obj, run_obj))
    return pairs


async def update_automation(session: AsyncSession, automation_id: str, **kwargs: Any) -> Automation:
    auto = await get_automation(session, automation_id)
    for key, val in kwargs.items():
        col = "metadata_" if key == "metadata" else key
        setattr(auto, col, val)
    auto.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(auto)
    return auto


async def archive_automation(session: AsyncSession, automation_id: str) -> Automation:
    """Soft delete: set status=archived, archived_at=now. Row stays in table."""
    auto = await get_automation(session, automation_id)
    auto.status = "archived"
    auto.archived_at = datetime.now(UTC)
    auto.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(auto)
    return auto


async def get_automation_with_latest_run(
    session: AsyncSession, automation_id: str
) -> tuple[Automation, AutomationRun | None]:
    lateral_sq = (
        select(AutomationRun)
        .where(AutomationRun.automation_id == Automation.id)
        .order_by(AutomationRun.created_at.desc())
        .limit(1)
        .correlate(Automation)
        .lateral("latest_run")
    )
    q = (
        select(Automation, lateral_sq)
        .outerjoin(lateral_sq, text("true"))
        .where(Automation.id == automation_id)
        .limit(1)
    )
    result = await session.execute(q)
    row = result.first()
    if row is None:
        raise ValueError(f"Automation '{automation_id}' not found")
    return (row[0], row[1])


# ── Automation runs ───────────────────────────────────────────────────────────


async def create_automation_run(session: AsyncSession, **kwargs: Any) -> AutomationRun:
    run_id = kwargs.pop("id", None) or str(uuid.uuid4())
    if "metadata" in kwargs:
        kwargs["metadata_"] = kwargs.pop("metadata")
    run = AutomationRun(id=run_id, **kwargs)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def get_automation_run(session: AsyncSession, run_id: str) -> AutomationRun:
    result = await session.execute(select(AutomationRun).where(AutomationRun.id == run_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"AutomationRun '{run_id}' not found")
    return row


async def list_automation_runs(
    session: AsyncSession,
    automation_id: str,
    *,
    limit: int = 30,
    cursor: str | None = None,
) -> list[AutomationRun]:
    q = (
        select(AutomationRun)
        .where(AutomationRun.automation_id == automation_id)
        .order_by(AutomationRun.created_at.desc())
        .limit(limit)
    )
    if cursor:
        q = q.where(AutomationRun.created_at < text(f"'{cursor}'::timestamptz"))
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_automation_run(session: AsyncSession, run_id: str, **kwargs: Any) -> AutomationRun:
    run = await get_automation_run(session, run_id)
    for key, val in kwargs.items():
        col = "metadata_" if key == "metadata" else key
        setattr(run, col, val)
    await session.flush()
    await session.refresh(run)
    return run
