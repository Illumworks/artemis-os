"""Agents router — /api/agents."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo
from artemis.builders.schemas import AgentCreate, AgentRead, AgentUpdate
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/agents",
    tags=["agents"],
    dependencies=[Depends(require_token)],
)


@router.get("")
@router.get("/")
async def list_agents(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    agents = await repo.list_agents(session, limit=limit, cursor=cursor)
    return {"agents": [AgentRead.model_validate(a).model_dump(by_alias=True) for a in agents]}


@router.post("/", status_code=201)
async def create_agent(
    body: AgentCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        await repo.get_agent(session, body.agent_id)
        raise conflict(f"Agent '{body.agent_id}' already exists", "agent_exists")
    except ValueError:
        pass
    agent = await repo.create_agent(
        session,
        agent_id=body.agent_id,
        name=body.name,
        description=body.description,
        goal=body.goal,
        system_prompt=body.system_prompt,
        tools=body.tools,
        model=body.model,
        provider=body.provider,
        max_iterations=body.max_iterations,
        owner_user_id=body.owner_user_id,
    )
    await session.commit()
    return AgentRead.model_validate(agent).model_dump(by_alias=True)


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        agent = await repo.get_agent(session, agent_id)
    except ValueError:
        raise not_found(f"Agent '{agent_id}' not found", "agent_not_found")  # noqa: B904
    return AgentRead.model_validate(agent).model_dump(by_alias=True)


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    update_data = body.model_dump(exclude_none=True, by_alias=False)
    if not update_data:
        raise bad_request("No fields to update", "empty_update")
    try:
        agent = await repo.update_agent(session, agent_id, **update_data)
    except ValueError:
        raise not_found(f"Agent '{agent_id}' not found", "agent_not_found")  # noqa: B904
    await session.commit()
    return AgentRead.model_validate(agent).model_dump(by_alias=True)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.delete_agent(session, agent_id)
    except ValueError:
        raise not_found(f"Agent '{agent_id}' not found", "agent_not_found")  # noqa: B904
    await session.commit()


@router.get("/{agent_id}/runs")
async def list_agent_runs(
    agent_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    from artemis.builders.schemas import AgentRunRead

    runs = await repo.list_agent_runs(
        session, agent_id=agent_id, status=status, limit=limit, cursor=cursor
    )
    return {"runs": [AgentRunRead.model_validate(r).model_dump(by_alias=True) for r in runs]}
