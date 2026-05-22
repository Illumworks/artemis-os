"""Async repository helpers for the Pipelines domain (PIPE1).

Conventions:
- Raise ValueError for not-found conditions (caller maps to 404).
- No business logic — just DB read/write. Callers own commit/rollback.
- Soft delete only: archive() sets status=archived, never deletes.
- Latest-run embedding uses a LATERAL JOIN, not N+1. Mirrors OP1 pattern.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines.models import Pipeline, PipelineRun

# ── Pipeline CRUD ─────────────────────────────────────────────────────────────


async def create_pipeline(session: AsyncSession, **kwargs: Any) -> Pipeline:
    pipeline_id = kwargs.pop("id", None) or str(uuid.uuid4())
    if "metadata" in kwargs:
        kwargs["metadata_"] = kwargs.pop("metadata")
    p = Pipeline(id=pipeline_id, **kwargs)
    session.add(p)
    await session.flush()
    await session.refresh(p)
    return p


async def get_pipeline(session: AsyncSession, pipeline_id: str) -> Pipeline:
    result = await session.execute(select(Pipeline).where(Pipeline.id == pipeline_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Pipeline '{pipeline_id}' not found")
    return row


async def list_pipelines(
    session: AsyncSession,
    *,
    status: str | None = None,
    owner_user_id: int | None = None,
    has_trigger: bool | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> list[tuple[Pipeline, PipelineRun | None]]:
    """List pipelines with latest_run embedded via a LATERAL subquery.

    Returns list of (Pipeline, PipelineRun|None) tuples — single query.
    Excludes archived by default unless status='archived' is requested.
    """
    run_alias = PipelineRun.__table__.alias("latest_run")
    lateral_sq = (
        select(run_alias)
        .where(run_alias.c.pipeline_id == Pipeline.id)
        .order_by(run_alias.c.created_at.desc())
        .limit(1)
        .correlate(Pipeline.__table__)
        .lateral("latest_run")
    )

    q = (
        select(Pipeline, lateral_sq)
        .outerjoin(lateral_sq, text("true"))
        .order_by(Pipeline.created_at.desc())
        .limit(limit)
    )

    q = q.where(Pipeline.status == status) if status else q.where(Pipeline.status != "archived")

    if owner_user_id is not None:
        q = q.where(Pipeline.owner_user_id == owner_user_id)

    if has_trigger is True:
        q = q.where(Pipeline.trigger_config.isnot(None))
    elif has_trigger is False:
        q = q.where(Pipeline.trigger_config.is_(None))

    if cursor:
        q = q.where(Pipeline.created_at < text(f"'{cursor}'::timestamptz"))

    result = await session.execute(q)
    pairs: list[tuple[Pipeline, PipelineRun | None]] = []
    for row in result.all():
        p_obj: Pipeline = row[0]
        # Lateral columns in order: id, pipeline_id, status, trigger,
        # triggered_by, node_states, started_at, completed_at,
        # error_message, metadata, created_at
        run_row_id = row[1]
        if run_row_id is None:
            pairs.append((p_obj, None))
        else:
            run_obj = PipelineRun(
                id=row[1],
                pipeline_id=row[2],
                status=row[3],
                trigger=row[4],
                triggered_by=row[5],
                node_states=row[6],
                started_at=row[7],
                completed_at=row[8],
                error_message=row[9],
                metadata_=row[10],
                created_at=row[11],
            )
            pairs.append((p_obj, run_obj))
    return pairs


async def update_pipeline(session: AsyncSession, pipeline_id: str, **kwargs: Any) -> Pipeline:
    p = await get_pipeline(session, pipeline_id)
    for key, val in kwargs.items():
        col = "metadata_" if key == "metadata" else key
        setattr(p, col, val)
    p.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(p)
    return p


async def archive_pipeline(session: AsyncSession, pipeline_id: str) -> Pipeline:
    """Soft delete: set status=archived. Row stays in table."""
    p = await get_pipeline(session, pipeline_id)
    p.status = "archived"
    p.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(p)
    return p


async def permanently_delete_pipeline(session: AsyncSession, pipeline_id: str) -> None:
    p = await get_pipeline(session, pipeline_id)
    if p.status != "archived":
        raise RuntimeError("Pipeline must be archived before permanent deletion")
    await session.execute(delete(Pipeline).where(Pipeline.id == pipeline_id))
    await session.flush()


async def get_pipeline_with_latest_run(
    session: AsyncSession, pipeline_id: str
) -> tuple[Pipeline, PipelineRun | None]:
    run_alias = PipelineRun.__table__.alias("latest_run_detail")
    lateral_sq = (
        select(run_alias)
        .where(run_alias.c.pipeline_id == Pipeline.id)
        .order_by(run_alias.c.created_at.desc())
        .limit(1)
        .correlate(Pipeline.__table__)
        .lateral("latest_run_detail")
    )
    q = (
        select(Pipeline, lateral_sq)
        .outerjoin(lateral_sq, text("true"))
        .where(Pipeline.id == pipeline_id)
        .limit(1)
    )
    result = await session.execute(q)
    row = result.first()
    if row is None:
        raise ValueError(f"Pipeline '{pipeline_id}' not found")
    p_obj: Pipeline = row[0]
    run_row_id = row[1]
    if run_row_id is None:
        return (p_obj, None)
    run_obj = PipelineRun(
        id=row[1],
        pipeline_id=row[2],
        status=row[3],
        trigger=row[4],
        triggered_by=row[5],
        node_states=row[6],
        started_at=row[7],
        completed_at=row[8],
        error_message=row[9],
        metadata_=row[10],
        created_at=row[11],
    )
    return (p_obj, run_obj)


# ── Pipeline runs ─────────────────────────────────────────────────────────────


async def create_pipeline_run(session: AsyncSession, **kwargs: Any) -> PipelineRun:
    run_id = kwargs.pop("id", None) or str(uuid.uuid4())
    if "metadata" in kwargs:
        kwargs["metadata_"] = kwargs.pop("metadata")
    run = PipelineRun(id=run_id, **kwargs)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def get_pipeline_run(session: AsyncSession, run_id: str) -> PipelineRun:
    result = await session.execute(select(PipelineRun).where(PipelineRun.id == run_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"PipelineRun '{run_id}' not found")
    return row


async def list_pipeline_runs(
    session: AsyncSession,
    pipeline_id: str,
    *,
    limit: int = 30,
    cursor: str | None = None,
) -> list[PipelineRun]:
    q = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.created_at.desc())
        .limit(limit)
    )
    if cursor:
        q = q.where(PipelineRun.created_at < text(f"'{cursor}'::timestamptz"))
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_pipeline_run(session: AsyncSession, run_id: str, **kwargs: Any) -> PipelineRun:
    run = await get_pipeline_run(session, run_id)
    for key, val in kwargs.items():
        col = "metadata_" if key == "metadata" else key
        setattr(run, col, val)
    await session.flush()
    await session.refresh(run)
    return run
