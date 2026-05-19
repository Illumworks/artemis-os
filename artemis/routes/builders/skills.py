"""Skills router — /api/skills.

Slice D (J11): POST /api/skills/{slug}/assign and /unassign for agent-skill linking.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo
from artemis.builders.schemas import SkillCreate, SkillRead, SkillUpdate
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/skills",
    tags=["skills"],
    dependencies=[Depends(require_token)],
)


@router.get("")
@router.get("/")
async def list_skills(
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    skills = await repo.list_skills(session, kind=kind, limit=limit, cursor=cursor)
    return {"skills": [SkillRead.model_validate(s).model_dump(by_alias=True) for s in skills]}


@router.post("/", status_code=201)
async def create_skill(
    body: SkillCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        await repo.get_skill(session, body.slug)
        raise conflict(f"Skill '{body.slug}' already exists", "skill_exists")
    except ValueError:
        pass
    skill = await repo.create_skill(
        session,
        slug=body.slug,
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        tools=body.tools,
        kind=body.kind,
        source_path=body.source_path,
        owner_user_id=body.owner_user_id,
    )
    await session.commit()
    return SkillRead.model_validate(skill).model_dump(by_alias=True)


@router.get("/{slug}")
async def get_skill(
    slug: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        skill = await repo.get_skill(session, slug)
    except ValueError:
        raise not_found(f"Skill '{slug}' not found", "skill_not_found")  # noqa: B904
    return SkillRead.model_validate(skill).model_dump(by_alias=True)


@router.patch("/{slug}")
async def update_skill(
    slug: str,
    body: SkillUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    update_data = body.model_dump(exclude_none=True, by_alias=False)
    if not update_data:
        raise bad_request("No fields to update", "empty_update")
    try:
        skill = await repo.update_skill(session, slug, **update_data)
    except ValueError:
        raise not_found(f"Skill '{slug}' not found", "skill_not_found")  # noqa: B904
    await session.commit()
    return SkillRead.model_validate(skill).model_dump(by_alias=True)


@router.delete("/{slug}", status_code=204)
async def delete_skill(
    slug: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.delete_skill(session, slug)
    except ValueError:
        raise not_found(f"Skill '{slug}' not found", "skill_not_found")  # noqa: B904
    await session.commit()


# ─── Slice D: Skill assignment ─────────────────────────────────────────────────


@router.post("/{slug}/assign", status_code=200)
async def assign_skill(
    slug: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Assign a skill to an agent. Idempotent — no error if already assigned.

    Body: {"agent_id": <int>}  (the agents.id primary key, not agent_id slug)
    """
    agent_db_id = body.get("agent_id")
    if not isinstance(agent_db_id, int):
        raise bad_request("Field 'agent_id' must be an integer (agents.id PK)", "invalid_agent_id")

    # Verify skill exists
    try:
        await repo.get_skill(session, slug)
    except ValueError:
        raise not_found(f"Skill '{slug}' not found", "skill_not_found")  # noqa: B904

    # Verify agent exists by PK
    from sqlalchemy import select

    from artemis.builders.models import Agent

    result = await session.execute(select(Agent).where(Agent.id == agent_db_id).limit(1))
    if result.scalar_one_or_none() is None:
        raise not_found(f"Agent with id={agent_db_id} not found", "agent_not_found")

    await repo.assign_skill_to_agent(session, agent_db_id, slug)
    await session.commit()
    return {"ok": True, "agentId": agent_db_id, "skillSlug": slug}


@router.post("/{slug}/unassign", status_code=200)
async def unassign_skill(
    slug: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Remove a skill assignment from an agent. No-op if not assigned.

    Body: {"agent_id": <int>}  (the agents.id primary key, not agent_id slug)
    """
    agent_db_id = body.get("agent_id")
    if not isinstance(agent_db_id, int):
        raise bad_request("Field 'agent_id' must be an integer (agents.id PK)", "invalid_agent_id")

    # Verify skill exists
    try:
        await repo.get_skill(session, slug)
    except ValueError:
        raise not_found(f"Skill '{slug}' not found", "skill_not_found")  # noqa: B904

    await repo.unassign_skill_from_agent(session, agent_db_id, slug)
    await session.commit()
    return {"ok": True, "agentId": agent_db_id, "skillSlug": slug}
