"""Agent Runs router — /api/agent-runs (read-only; creation is F2b)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo
from artemis.builders.schemas import AgentContextRead, AgentRunRead
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import not_found

router = APIRouter(
    prefix="/api/agent-runs",
    tags=["agent-runs"],
    dependencies=[Depends(require_token)],
)


@router.get("")
@router.get("/")
async def list_agent_runs(
    agent_id: str | None = Query(default=None, alias="agentId"),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    include_ephemeral: bool = Query(default=False, alias="includeEphemeral"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    runs = await repo.list_agent_runs(
        session,
        agent_id=agent_id,
        status=status,
        limit=limit,
        cursor=cursor,
        include_ephemeral=include_ephemeral,
    )
    return {"runs": [AgentRunRead.model_validate(r).model_dump(by_alias=True) for r in runs]}


@router.get("/{run_id}")
async def get_agent_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        run = await repo.get_agent_run(session, run_id)
    except ValueError:
        raise not_found(f"AgentRun '{run_id}' not found", "agent_run_not_found")  # noqa: B904
    return AgentRunRead.model_validate(run).model_dump(by_alias=True)


@router.get("/{run_id}/context")
async def get_agent_run_context(
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    # Verify the run exists first
    try:
        await repo.get_agent_run(session, run_id)
    except ValueError:
        raise not_found(f"AgentRun '{run_id}' not found", "agent_run_not_found")  # noqa: B904
    ctx = await repo.get_all_agent_context_for_run(session, run_id)
    return {"context": [AgentContextRead.model_validate(c).model_dump(by_alias=True) for c in ctx]}
