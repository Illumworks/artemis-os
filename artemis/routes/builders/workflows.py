"""Workflows router — /api/workflows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo
from artemis.builders.models import WorkflowRun
from artemis.builders.schemas import WorkflowCreate, WorkflowRead, WorkflowRunRead, WorkflowUpdate
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/workflows",
    tags=["workflows"],
    dependencies=[Depends(require_token)],
)


@router.get("")
@router.get("/")
async def list_workflows(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    workflows = await repo.list_workflows(session, limit=limit, cursor=cursor)
    return {
        "workflows": [WorkflowRead.model_validate(w).model_dump(by_alias=True) for w in workflows]
    }


@router.post("/", status_code=201)
async def create_workflow(
    body: WorkflowCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        await repo.get_workflow(session, body.workflow_id)
        raise conflict(f"Workflow '{body.workflow_id}' already exists", "workflow_exists")
    except ValueError:
        pass
    if not body.steps:
        raise bad_request("steps must not be empty", "steps_required")
    wf = await repo.create_workflow(
        session,
        workflow_id=body.workflow_id,
        name=body.name,
        description=body.description,
        steps=body.steps,
        owner_user_id=body.owner_user_id,
    )
    await session.commit()
    return WorkflowRead.model_validate(wf).model_dump(by_alias=True)


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        wf = await repo.get_workflow(session, workflow_id)
    except ValueError:
        raise not_found(f"Workflow '{workflow_id}' not found", "workflow_not_found")  # noqa: B904
    return WorkflowRead.model_validate(wf).model_dump(by_alias=True)


@router.patch("/{workflow_id}")
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    update_data = body.model_dump(exclude_none=True, by_alias=False)
    if not update_data:
        raise bad_request("No fields to update", "empty_update")
    try:
        wf = await repo.update_workflow(session, workflow_id, **update_data)
    except ValueError:
        raise not_found(f"Workflow '{workflow_id}' not found", "workflow_not_found")  # noqa: B904
    await session.commit()
    return WorkflowRead.model_validate(wf).model_dump(by_alias=True)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.delete_workflow(session, workflow_id)
    except ValueError:
        raise not_found(f"Workflow '{workflow_id}' not found", "workflow_not_found")  # noqa: B904
    await session.commit()


@router.get("/{workflow_id}/runs")
async def list_workflow_runs(
    workflow_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    q = (
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.id.desc())
        .limit(limit)
    )
    if cursor is not None:
        q = q.where(WorkflowRun.id < cursor)
    result = await session.execute(q)
    runs = list(result.scalars().all())
    return {"runs": [WorkflowRunRead.model_validate(r).model_dump(by_alias=True) for r in runs]}


@router.get("/{workflow_id}/runs/latest")
async def get_latest_workflow_run(
    workflow_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    result = await session.execute(
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise not_found("No runs found", "no_runs")
    return WorkflowRunRead.model_validate(run).model_dump(by_alias=True)


@router.get("/{workflow_id}/runs/{run_id}")
async def get_workflow_run(
    workflow_id: str,
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    result = await session.execute(
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id == workflow_id, WorkflowRun.run_id == run_id)
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise not_found("Workflow run not found", "workflow_run_not_found")
    return WorkflowRunRead.model_validate(run).model_dump(by_alias=True)
