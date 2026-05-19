"""Agents router — /api/agents.

Slice A (J11): subresource routes for instruction file, supporting files, skills.
Slice B (J11): run-observability aliases (active, recent, search, run by ID, run context).
Slice C (J11): enriched detail on GET /api/agents/{agent_id} (instructionFileExists,
               supportingFileCount, linkedSkills).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo
from artemis.builders.schemas import (
    AgentCreate,
    AgentRead,
    AgentRunRead,
    AgentUpdate,
    SkillRead,
)
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found

router = APIRouter(
    prefix="/api/agents",
    tags=["agents"],
    dependencies=[Depends(require_token)],
)

# Base directory for per-agent files. Override via ARTEMIS_AGENTS_DIR env var for tests.
_AGENTS_BASE = Path(os.environ.get("ARTEMIS_AGENTS_DIR", str(Path.home() / ".artemis" / "agents")))


def _agent_dir(agent_db_id: int) -> Path:
    return _AGENTS_BASE / str(agent_db_id)


def _instruction_path(agent_db_id: int) -> Path:
    return _agent_dir(agent_db_id) / "instruction.md"


def _files_dir(agent_db_id: int) -> Path:
    return _agent_dir(agent_db_id) / "files"


# ─────────────────────────────────────────────────────────────────────────────
# IMPORTANT: Static sub-paths (/runs/active, /runs/recent, /runs/search,
# /runs/{run_id}, /context/{run_id}) must be registered BEFORE the dynamic
# /{agent_id} pattern, otherwise FastAPI will match "runs" as an agent_id.
# ─────────────────────────────────────────────────────────────────────────────


# ─── Slice B: Run-observability aliases ───────────────────────────────────────


@router.get("/runs/active")
async def get_active_runs(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Active runs — status running or pending, ordered by started_at DESC."""
    runs = await repo.list_active_agent_runs(session)
    return {"runs": [AgentRunRead.model_validate(r).model_dump(by_alias=True) for r in runs]}


@router.get("/runs/recent")
async def get_recent_runs(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Recent runs across all agents, ordered by started_at DESC."""
    runs = await repo.list_recent_agent_runs(session, limit=limit)
    return {"runs": [AgentRunRead.model_validate(r).model_dump(by_alias=True) for r in runs]}


@router.get("/runs/search")
async def search_runs(
    q: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Substring search across user_message and error, capped at 100 results."""
    runs = await repo.search_agent_runs(session, q)
    return {"runs": [AgentRunRead.model_validate(r).model_dump(by_alias=True) for r in runs]}


@router.get("/runs/{run_id}")
async def get_run_by_id(
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Alias for GET /api/agent-runs/{run_id}."""
    try:
        run = await repo.get_agent_run(session, run_id)
    except ValueError:
        raise not_found(f"AgentRun '{run_id}' not found", "agent_run_not_found")  # noqa: B904
    return AgentRunRead.model_validate(run).model_dump(by_alias=True)


@router.get("/context/{run_id}")
async def get_run_context(
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Alias for GET /api/agent-runs/{run_id}/context."""
    from artemis.builders.schemas import AgentContextRead

    try:
        await repo.get_agent_run(session, run_id)
    except ValueError:
        raise not_found(f"AgentRun '{run_id}' not found", "agent_run_not_found")  # noqa: B904
    ctx = await repo.get_all_agent_context_for_run(session, run_id)
    return {"context": [AgentContextRead.model_validate(c).model_dump(by_alias=True) for c in ctx]}


# ─── CRUD ─────────────────────────────────────────────────────────────────────


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
        fallback_provider=body.fallback_provider,
        fallback_model=body.fallback_model,
        memory_policy=body.memory_policy,
        permission_mode=body.permission_mode,
        output_contract=body.output_contract,
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

    base = AgentRead.model_validate(agent).model_dump(by_alias=True)

    # Enrichment: file-system fields
    instr_path = _instruction_path(agent.id)
    files_dir = _files_dir(agent.id)

    instruction_file_exists = instr_path.exists()
    supporting_file_count = (
        sum(1 for f in files_dir.iterdir() if f.is_file()) if files_dir.exists() else 0
    )

    # Enrichment: linked skills from join table
    skills = await repo.list_skills_for_agent(session, agent.id)
    linked_skills = [{"slug": s.slug, "name": s.name} for s in skills]

    base["instructionFileExists"] = instruction_file_exists
    base["supportingFileCount"] = supporting_file_count
    base["linkedSkills"] = linked_skills
    return base


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
    runs = await repo.list_agent_runs(
        session, agent_id=agent_id, status=status, limit=limit, cursor=cursor
    )
    return {"runs": [AgentRunRead.model_validate(r).model_dump(by_alias=True) for r in runs]}


# ─── Slice A: Subresource routes ──────────────────────────────────────────────


async def _get_agent_or_404(session: AsyncSession, agent_id: str) -> Any:
    """Fetch agent by agent_id string, raising 404 if not found."""
    try:
        return await repo.get_agent(session, agent_id)
    except ValueError:
        raise not_found(f"Agent '{agent_id}' not found", "agent_not_found")  # noqa: B904


@router.get("/{agent_id}/instruction")
async def get_instruction(
    agent_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the agent's instruction.md file content."""
    agent = await _get_agent_or_404(session, agent_id)
    path = _instruction_path(agent.id)
    if path.exists():
        return {"exists": True, "content": path.read_text(encoding="utf-8")}
    return {"exists": False, "content": ""}


@router.put("/{agent_id}/instruction")
async def put_instruction(
    agent_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Write the agent's instruction.md file."""
    agent = await _get_agent_or_404(session, agent_id)
    content = body.get("content", "")
    if not isinstance(content, str):
        raise bad_request("Field 'content' must be a string", "invalid_content")
    path = _instruction_path(agent.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True}


@router.delete("/{agent_id}/instruction", status_code=204)
async def delete_instruction(
    agent_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Delete the agent's instruction.md file (no-op if not present)."""
    agent = await _get_agent_or_404(session, agent_id)
    path = _instruction_path(agent.id)
    if path.exists():
        path.unlink()


@router.get("/{agent_id}/files")
async def list_files(
    agent_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """List supporting files in the agent's files/ directory."""
    agent = await _get_agent_or_404(session, agent_id)
    files_dir = _files_dir(agent.id)
    if not files_dir.exists():
        return {"files": []}
    entries = []
    for f in sorted(files_dir.iterdir()):
        if f.is_file():
            stat = f.stat()
            entries.append(
                {
                    "name": f.name,
                    "size": stat.st_size,
                    "modifiedAt": stat.st_mtime,
                }
            )
    return {"files": entries}


@router.get("/{agent_id}/skills")
async def list_agent_skills(
    agent_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """List skills assigned to this agent."""
    agent = await _get_agent_or_404(session, agent_id)
    skills = await repo.list_skills_for_agent(session, agent.id)
    return {"skills": [SkillRead.model_validate(s).model_dump(by_alias=True) for s in skills]}
