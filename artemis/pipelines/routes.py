"""Pipeline routes (PIPE1) — /api/pipelines*.

Endpoints:
  GET    /api/pipelines         — list (latest_run embedded, single LATERAL query)
  GET    /api/pipelines/        — no-slash compat alias
  POST   /api/pipelines/        — create
  GET    /api/pipelines/{id}/export — portable JSON bundle
  POST   /api/pipelines/import  — import portable JSON bundle
  GET    /api/pipelines/{id}    — detail with latest run
  PATCH  /api/pipelines/{id}    — update (full nodes/edges replace)
  DELETE /api/pipelines/{id}    — soft delete (status → archived)
  DELETE /api/pipelines/{id}/permanent — hard delete archived pipeline
  POST   /api/pipelines/{id}/enable  — flip status to active
  POST   /api/pipelines/{id}/disable — flip status to paused
  POST   /api/pipelines/{id}/run     — manual trigger (records intent only — PIPE4 executes)
  GET    /api/pipelines/{id}/runs    — cursor-paginated run history
  POST   /api/pipeline-runs/{run_id}/cancel — mark cancelled
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found, validation_failed
from artemis.pipelines import repository as repo
from artemis.pipelines.schemas import (
    PipelineCreate,
    PipelineExportBundle,
    PipelineImportResult,
    PipelineRunRequest,
    PipelineUpdate,
    pipeline_run_to_schema,
    pipeline_to_schema,
)

router = APIRouter(
    tags=["pipelines"],
    dependencies=[Depends(require_token)],
)

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run_to_dict(run: Any) -> dict[str, Any]:
    return pipeline_run_to_schema(run).model_dump(by_alias=True)


def _pipeline_to_dict(p: Any, latest_run: Any | None = None) -> dict[str, Any]:
    return pipeline_to_schema(p, latest_run).model_dump(by_alias=True)


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/api/pipelines")
@router.get("/api/pipelines/")
async def list_pipelines(
    status: str | None = Query(default=None),
    owner: int | None = Query(default=None),
    has_trigger: bool | None = Query(default=None, alias="hasTrigger"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List pipelines with latest_run embedded (single LATERAL JOIN query)."""
    rows = await repo.list_pipelines(
        session,
        status=status,
        owner_user_id=owner,
        has_trigger=has_trigger,
        limit=limit,
        cursor=cursor,
    )
    return [_pipeline_to_dict(p, run) for p, run in rows]


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/api/pipelines/", status_code=201)
async def create_pipeline(
    body: PipelineCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a new pipeline definition."""
    p = await repo.create_pipeline(
        session,
        name=body.name,
        description=body.description,
        nodes=body.nodes,
        edges=body.edges,
        trigger_config=body.trigger_config,
        status=body.status,
        owner_user_id=body.owner_user_id,
        metadata_=body.metadata,
    )
    await session.commit()
    return _pipeline_to_dict(p)


# ── Detail ────────────────────────────────────────────────────────────────────


@router.get("/api/pipelines/{pipeline_id}/export")
async def export_pipeline(
    pipeline_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a portable, credential-free pipeline JSON bundle."""
    try:
        bundle = await repo.build_export_bundle(
            session,
            pipeline_id,
            exported_from=str(request.base_url).rstrip("/"),
        )
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    return bundle.model_dump(mode="json")


@router.post("/api/pipelines/import", status_code=201)
async def import_pipeline(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Import a portable pipeline bundle, creating missing agents without overwriting."""
    if body.get("format_version") != "1":
        raise validation_failed(
            {"format_version": "Format upgrade required: only format_version '1' is supported"}
        )
    try:
        bundle = PipelineExportBundle.model_validate(body)
        result = await repo.import_bundle(session, bundle)
    except ValidationError as exc:
        raise validation_failed({"bundle": str(exc)}) from exc
    except KeyError as exc:
        raise bad_request(f"Missing pipeline field: {exc}", "invalid_pipeline_import")  # noqa: B904
    except ValueError as exc:
        raise bad_request(str(exc), "invalid_pipeline_import")  # noqa: B904
    await session.commit()
    return PipelineImportResult(**result).model_dump()


@router.get("/api/pipelines/{pipeline_id}")
async def get_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single pipeline with its latest run embedded."""
    try:
        p, run = await repo.get_pipeline_with_latest_run(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    return _pipeline_to_dict(p, run)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/api/pipelines/{pipeline_id}")
async def update_pipeline(
    pipeline_id: str,
    body: PipelineUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Update mutable fields of a pipeline. Nodes/edges are fully replaced when supplied."""
    updates: dict[str, Any] = {}
    for field in [
        "name",
        "description",
        "nodes",
        "edges",
        "trigger_config",
        "status",
        "owner_user_id",
        "metadata",
    ]:
        val = getattr(body, field)
        if val is not None:
            updates[field] = val
    try:
        p = await repo.update_pipeline(session, pipeline_id, **updates)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()
    return _pipeline_to_dict(p)


# ── Soft delete ───────────────────────────────────────────────────────────────


@router.delete("/api/pipelines/{pipeline_id}", status_code=204)
async def delete_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Soft delete: set status=archived. Row never removed from DB."""
    try:
        await repo.archive_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()


@router.delete("/api/pipelines/{pipeline_id}/permanent", status_code=204)
async def permanently_delete_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Hard delete an already archived pipeline and its run history."""
    try:
        await repo.permanently_delete_pipeline(session, pipeline_id)
    except RuntimeError as exc:
        raise conflict(str(exc), "pipeline_must_be_archived")  # noqa: B904
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()


# ── Enable / Disable ──────────────────────────────────────────────────────────


@router.post("/api/pipelines/{pipeline_id}/enable")
async def enable_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Flip pipeline status to active."""
    try:
        p = await repo.update_pipeline(session, pipeline_id, status="active")
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()
    return _pipeline_to_dict(p)


@router.post("/api/pipelines/{pipeline_id}/disable")
async def disable_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Flip pipeline status to paused."""
    try:
        p = await repo.update_pipeline(session, pipeline_id, status="paused")
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    await session.commit()
    return _pipeline_to_dict(p)


# ── Manual trigger ────────────────────────────────────────────────────────────


@router.post("/api/pipelines/{pipeline_id}/run", status_code=202)
async def run_pipeline(
    pipeline_id: str,
    body: PipelineRunRequest | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Manually trigger a pipeline run.

    Records a pipeline_runs row with status=queued. Does NOT execute anything —
    the execution engine wires up in PIPE4.
    """
    body = body or PipelineRunRequest()
    try:
        p = await repo.get_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904

    if p.status == "archived":
        raise bad_request("Cannot run an archived pipeline", "pipeline_archived")

    run = await repo.create_pipeline_run(
        session,
        pipeline_id=pipeline_id,
        status="queued",
        trigger="manual",
        triggered_by=body.triggered_by or "manual",
        metadata_=body.metadata,
    )
    await session.commit()
    return _run_to_dict(run)


# ── Run history ───────────────────────────────────────────────────────────────


@router.get("/api/pipelines/{pipeline_id}/runs")
async def list_runs(
    pipeline_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return run history for a pipeline (cursor-paginated, newest first)."""
    try:
        await repo.get_pipeline(session, pipeline_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_not_found")  # noqa: B904
    runs = await repo.list_pipeline_runs(session, pipeline_id, limit=limit, cursor=cursor)
    return [_run_to_dict(r) for r in runs]


# ── Cancel ────────────────────────────────────────────────────────────────────


@router.post("/api/pipeline-runs/{run_id}/cancel", status_code=200)
async def cancel_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Cancel an in-flight or queued pipeline run."""
    try:
        run = await repo.get_pipeline_run(session, run_id)
    except ValueError as exc:
        raise not_found(str(exc), "pipeline_run_not_found")  # noqa: B904

    if run.status in ("succeeded", "failed", "cancelled"):
        raise bad_request(
            f"Cannot cancel a run with status '{run.status}'",
            "pipeline_run_already_terminal",
        )

    run = await repo.update_pipeline_run(
        session,
        run_id,
        status="cancelled",
        completed_at=datetime.now(UTC),
    )
    await session.commit()
    return _run_to_dict(run)
