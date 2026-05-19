"""Agent Chains router — /api/agent-chains."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo
from artemis.builders.schemas import AgentChainCreate, AgentChainRead, AgentChainUpdate
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/agent-chains",
    tags=["agent-chains"],
    dependencies=[Depends(require_token)],
)


@router.get("")
@router.get("/")
async def list_agent_chains(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    chains = await repo.list_agent_chains(session, limit=limit, cursor=cursor)
    return {"chains": [AgentChainRead.model_validate(c).model_dump(by_alias=True) for c in chains]}


@router.post("/", status_code=201)
async def create_agent_chain(
    body: AgentChainCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        await repo.get_agent_chain(session, body.chain_id)
        raise conflict(f"AgentChain '{body.chain_id}' already exists", "chain_exists")
    except ValueError:
        pass
    chain = await repo.create_agent_chain(
        session,
        chain_id=body.chain_id,
        name=body.name,
        description=body.description,
        steps=body.steps,
        owner_user_id=body.owner_user_id,
    )
    await session.commit()
    return AgentChainRead.model_validate(chain).model_dump(by_alias=True)


@router.get("/{chain_id}")
async def get_agent_chain(
    chain_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        chain = await repo.get_agent_chain(session, chain_id)
    except ValueError:
        raise not_found(f"AgentChain '{chain_id}' not found", "chain_not_found")  # noqa: B904
    return AgentChainRead.model_validate(chain).model_dump(by_alias=True)


@router.patch("/{chain_id}")
async def update_agent_chain(
    chain_id: str,
    body: AgentChainUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    update_data = body.model_dump(exclude_none=True, by_alias=False)
    if not update_data:
        raise bad_request("No fields to update", "empty_update")
    try:
        chain = await repo.update_agent_chain(session, chain_id, **update_data)
    except ValueError:
        raise not_found(f"AgentChain '{chain_id}' not found", "chain_not_found")  # noqa: B904
    await session.commit()
    return AgentChainRead.model_validate(chain).model_dump(by_alias=True)


@router.delete("/{chain_id}", status_code=204)
async def delete_agent_chain(
    chain_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.delete_agent_chain(session, chain_id)
    except ValueError:
        raise not_found(f"AgentChain '{chain_id}' not found", "chain_not_found")  # noqa: B904
    await session.commit()
