"""Agent DAGs router — /api/agent-dags."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo
from artemis.builders.schemas import AgentDagCreate, AgentDagRead, AgentDagUpdate
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/agent-dags",
    tags=["agent-dags"],
    dependencies=[Depends(require_token)],
)


@router.get("/")
async def list_agent_dags(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    dags = await repo.list_agent_dags(session, limit=limit, cursor=cursor)
    return {"dags": [AgentDagRead.model_validate(d).model_dump(by_alias=True) for d in dags]}


@router.post("/", status_code=201)
async def create_agent_dag(
    body: AgentDagCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        await repo.get_agent_dag(session, body.dag_id)
        raise conflict(f"AgentDag '{body.dag_id}' already exists", "dag_exists")
    except ValueError:
        pass
    dag = await repo.create_agent_dag(
        session,
        dag_id=body.dag_id,
        name=body.name,
        description=body.description,
        nodes=body.nodes,
        owner_user_id=body.owner_user_id,
    )
    await session.commit()
    return AgentDagRead.model_validate(dag).model_dump(by_alias=True)


@router.get("/{dag_id}")
async def get_agent_dag(
    dag_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        dag = await repo.get_agent_dag(session, dag_id)
    except ValueError:
        raise not_found(f"AgentDag '{dag_id}' not found", "dag_not_found")  # noqa: B904
    return AgentDagRead.model_validate(dag).model_dump(by_alias=True)


@router.patch("/{dag_id}")
async def update_agent_dag(
    dag_id: str,
    body: AgentDagUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    update_data = body.model_dump(exclude_none=True, by_alias=False)
    if not update_data:
        raise bad_request("No fields to update", "empty_update")
    try:
        dag = await repo.update_agent_dag(session, dag_id, **update_data)
    except ValueError:
        raise not_found(f"AgentDag '{dag_id}' not found", "dag_not_found")  # noqa: B904
    await session.commit()
    return AgentDagRead.model_validate(dag).model_dump(by_alias=True)


@router.delete("/{dag_id}", status_code=204)
async def delete_agent_dag(
    dag_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.delete_agent_dag(session, dag_id)
    except ValueError:
        raise not_found(f"AgentDag '{dag_id}' not found", "dag_not_found")  # noqa: B904
    await session.commit()
