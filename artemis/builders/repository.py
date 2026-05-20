"""Repository helpers for the Builders domain.

All functions are async and accept a SQLAlchemy AsyncSession.
Conventions:
- Raise ValueError for not-found conditions callers should handle as 404.
- Raise ValueError with a "already exists" message for slug conflicts (409).
- No business logic — just DB read/write. Callers own commit/rollback.
- Hard deletes throughout (no tombstone column in the spec).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders.models import (
    Agent,
    AgentChain,
    AgentContext,
    AgentDag,
    AgentRun,
    AgentRunTrajectorySummary,
    AgentSkill,
    Skill,
    Workflow,
    WorkflowRun,
)

# ─────────────────────────────────────────────────────────────────────────────
# Agents
# ─────────────────────────────────────────────────────────────────────────────


async def create_agent(session: AsyncSession, **kwargs: Any) -> Agent:
    agent = Agent(**kwargs)
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    return agent


async def get_agent(session: AsyncSession, agent_id: str) -> Agent:
    result = await session.execute(select(Agent).where(Agent.agent_id == agent_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Agent '{agent_id}' not found")
    return row


async def list_agents(
    session: AsyncSession,
    *,
    limit: int = 50,
    cursor: int | None = None,
) -> list[Agent]:
    q = select(Agent).order_by(Agent.id.desc()).limit(limit)
    if cursor is not None:
        q = q.where(Agent.id < cursor)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_agent(session: AsyncSession, agent_id: str, **kwargs: Any) -> Agent:
    agent = await get_agent(session, agent_id)
    for key, val in kwargs.items():
        if val is not None or key in ("description", "goal", "system_prompt"):
            setattr(agent, key, val)
    agent.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(agent)
    return agent


async def delete_agent(session: AsyncSession, agent_id: str) -> None:
    agent = await get_agent(session, agent_id)
    await session.delete(agent)
    await session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Agent Runs
# ─────────────────────────────────────────────────────────────────────────────


async def create_agent_run(session: AsyncSession, **kwargs: Any) -> AgentRun:
    run = AgentRun(**kwargs)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def get_agent_run(session: AsyncSession, run_id: str) -> AgentRun:
    result = await session.execute(select(AgentRun).where(AgentRun.run_id == run_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"AgentRun '{run_id}' not found")
    return row


async def list_agent_runs(
    session: AsyncSession,
    *,
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    cursor: int | None = None,
    include_ephemeral: bool = False,
) -> list[AgentRun]:
    q = select(AgentRun).order_by(AgentRun.id.desc()).limit(limit)
    if agent_id is not None:
        q = q.where(AgentRun.agent_id == agent_id)
    if status is not None:
        q = q.where(AgentRun.status == status)
    if cursor is not None:
        q = q.where(AgentRun.id < cursor)
    if not include_ephemeral:
        q = q.where(AgentRun.is_ephemeral.is_(False))
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_agent_run_status(
    session: AsyncSession, run_id: str, status: str, error: str | None = None
) -> AgentRun:
    run = await get_agent_run(session, run_id)
    run.status = status
    if error is not None:
        run.error = error
    await session.flush()
    await session.refresh(run)
    return run


async def set_agent_run_completed(
    session: AsyncSession,
    run_id: str,
    *,
    status: str = "completed",
    cost_input_tokens: int = 0,
    cost_output_tokens: int = 0,
    error: str | None = None,
) -> AgentRun:
    run = await get_agent_run(session, run_id)
    run.status = status
    run.completed_at = datetime.now(UTC)
    run.cost_input_tokens = cost_input_tokens
    run.cost_output_tokens = cost_output_tokens
    if error is not None:
        run.error = error
    await session.flush()
    await session.refresh(run)
    return run


# ─────────────────────────────────────────────────────────────────────────────
# Agent Context
# ─────────────────────────────────────────────────────────────────────────────


async def set_agent_context(
    session: AsyncSession, run_id: str, key: str, value: Any
) -> AgentContext:
    """Upsert a context key for an agent run (insert or replace)."""
    result = await session.execute(
        select(AgentContext).where(AgentContext.run_id == run_id, AgentContext.key == key).limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.value = value
        await session.flush()
        await session.refresh(existing)
        return existing
    ctx = AgentContext(run_id=run_id, key=key, value=value)
    session.add(ctx)
    await session.flush()
    await session.refresh(ctx)
    return ctx


async def set_workflow_context(
    session: AsyncSession, workflow_run_id: int, key: str, value: Any
) -> AgentContext:
    """Upsert a context key for a workflow run (insert or replace).

    Stores the row with ``workflow_run_id`` set and ``run_id=NULL``.
    """
    result = await session.execute(
        select(AgentContext)
        .where(
            AgentContext.workflow_run_id == workflow_run_id,
            AgentContext.key == key,
        )
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.value = value
        await session.flush()
        await session.refresh(existing)
        return existing
    ctx = AgentContext(workflow_run_id=workflow_run_id, key=key, value=value)
    session.add(ctx)
    await session.flush()
    await session.refresh(ctx)
    return ctx


async def get_agent_context(session: AsyncSession, run_id: str, key: str) -> AgentContext:
    result = await session.execute(
        select(AgentContext).where(AgentContext.run_id == run_id, AgentContext.key == key).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"AgentContext key '{key}' not found for run '{run_id}'")
    return row


async def get_workflow_context(session: AsyncSession, workflow_run_id: int) -> list[AgentContext]:
    """Return all context entries for a workflow run, ordered by id."""
    result = await session.execute(
        select(AgentContext)
        .where(AgentContext.workflow_run_id == workflow_run_id)
        .order_by(AgentContext.id.asc())
    )
    return list(result.scalars().all())


async def get_all_agent_context_for_run(session: AsyncSession, run_id: str) -> list[AgentContext]:
    result = await session.execute(
        select(AgentContext).where(AgentContext.run_id == run_id).order_by(AgentContext.id.asc())
    )
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# Skills
# ─────────────────────────────────────────────────────────────────────────────


async def create_skill(session: AsyncSession, **kwargs: Any) -> Skill:
    skill = Skill(**kwargs)
    session.add(skill)
    await session.flush()
    await session.refresh(skill)
    return skill


async def get_skill(session: AsyncSession, slug: str) -> Skill:
    result = await session.execute(select(Skill).where(Skill.slug == slug).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Skill '{slug}' not found")
    return row


async def list_skills(
    session: AsyncSession,
    *,
    kind: str | None = None,
    status: str | None = None,
    category: str | None = None,
    limit: int = 50,
    cursor: int | None = None,
) -> list[Skill]:
    q = select(Skill).order_by(Skill.id.desc()).limit(limit)
    if kind is not None:
        q = q.where(Skill.kind == kind)
    if status is not None:
        q = q.where(Skill.status == status)
    if category is not None:
        q = q.where(Skill.category == category)
    if cursor is not None:
        q = q.where(Skill.id < cursor)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_skill(session: AsyncSession, slug: str, **kwargs: Any) -> Skill:
    skill = await get_skill(session, slug)
    for key, val in kwargs.items():
        if val is not None or key in ("description", "instructions", "source_path"):
            setattr(skill, key, val)
    skill.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(skill)
    return skill


async def delete_skill(session: AsyncSession, slug: str) -> None:
    skill = await get_skill(session, slug)
    await session.delete(skill)
    await session.flush()


async def set_skill_status(session: AsyncSession, slug: str, status: str) -> Skill:
    skill = await get_skill(session, slug)
    skill.status = status
    skill.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(skill)
    return skill


async def list_skill_categories(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        select(Skill.category, func.count(Skill.id).label("count"))
        .where(Skill.status != "archived", Skill.category.is_not(None))
        .group_by(Skill.category)
        .order_by(Skill.category.asc())
    )
    return [{"category": category, "count": count} for category, count in result.all()]


# ─────────────────────────────────────────────────────────────────────────────
# Workflows
# ─────────────────────────────────────────────────────────────────────────────


async def create_workflow(session: AsyncSession, **kwargs: Any) -> Workflow:
    wf = Workflow(**kwargs)
    session.add(wf)
    await session.flush()
    await session.refresh(wf)
    return wf


async def get_workflow(session: AsyncSession, workflow_id: str) -> Workflow:
    result = await session.execute(
        select(Workflow).where(Workflow.workflow_id == workflow_id).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Workflow '{workflow_id}' not found")
    return row


async def list_workflows(
    session: AsyncSession,
    *,
    limit: int = 50,
    cursor: int | None = None,
) -> list[Workflow]:
    q = select(Workflow).order_by(Workflow.id.desc()).limit(limit)
    if cursor is not None:
        q = q.where(Workflow.id < cursor)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_workflow(session: AsyncSession, workflow_id: str, **kwargs: Any) -> Workflow:
    wf = await get_workflow(session, workflow_id)
    for key, val in kwargs.items():
        if val is not None or key in ("description",):
            setattr(wf, key, val)
    wf.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(wf)
    return wf


async def delete_workflow(session: AsyncSession, workflow_id: str) -> None:
    wf = await get_workflow(session, workflow_id)
    await session.delete(wf)
    await session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Runs
# ─────────────────────────────────────────────────────────────────────────────


async def get_workflow_run(session: AsyncSession, run_id: str) -> WorkflowRun:
    result = await session.execute(select(WorkflowRun).where(WorkflowRun.run_id == run_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"WorkflowRun '{run_id}' not found")
    return row


async def create_workflow_run(session: AsyncSession, **kwargs: Any) -> WorkflowRun:
    run = WorkflowRun(**kwargs)
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def update_workflow_run_status(
    session: AsyncSession,
    run_id: str,
    status: str,
    *,
    current_step: int | None = None,
    completed_at: datetime | None = None,
    total_cost_usd: float | None = None,
) -> WorkflowRun:
    result = await session.execute(select(WorkflowRun).where(WorkflowRun.run_id == run_id).limit(1))
    run = result.scalar_one_or_none()
    if run is None:
        raise ValueError(f"WorkflowRun '{run_id}' not found")
    run.status = status
    if current_step is not None:
        run.current_step = current_step
    if completed_at is not None:
        run.completed_at = completed_at
    if total_cost_usd is not None:
        run.total_cost_usd = total_cost_usd
    await session.flush()
    await session.refresh(run)
    return run


# ─────────────────────────────────────────────────────────────────────────────
# Agent Chains
# ─────────────────────────────────────────────────────────────────────────────


async def create_agent_chain(session: AsyncSession, **kwargs: Any) -> AgentChain:
    chain = AgentChain(**kwargs)
    session.add(chain)
    await session.flush()
    await session.refresh(chain)
    return chain


async def get_agent_chain(session: AsyncSession, chain_id: str) -> AgentChain:
    result = await session.execute(
        select(AgentChain).where(AgentChain.chain_id == chain_id).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"AgentChain '{chain_id}' not found")
    return row


async def list_agent_chains(
    session: AsyncSession,
    *,
    limit: int = 50,
    cursor: int | None = None,
) -> list[AgentChain]:
    q = select(AgentChain).order_by(AgentChain.id.desc()).limit(limit)
    if cursor is not None:
        q = q.where(AgentChain.id < cursor)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_agent_chain(session: AsyncSession, chain_id: str, **kwargs: Any) -> AgentChain:
    chain = await get_agent_chain(session, chain_id)
    for key, val in kwargs.items():
        if val is not None or key in ("description",):
            setattr(chain, key, val)
    chain.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(chain)
    return chain


async def delete_agent_chain(session: AsyncSession, chain_id: str) -> None:
    chain = await get_agent_chain(session, chain_id)
    await session.delete(chain)
    await session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Agent DAGs
# ─────────────────────────────────────────────────────────────────────────────


async def create_agent_dag(session: AsyncSession, **kwargs: Any) -> AgentDag:
    dag = AgentDag(**kwargs)
    session.add(dag)
    await session.flush()
    await session.refresh(dag)
    return dag


async def get_agent_dag(session: AsyncSession, dag_id: str) -> AgentDag:
    result = await session.execute(select(AgentDag).where(AgentDag.dag_id == dag_id).limit(1))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"AgentDag '{dag_id}' not found")
    return row


async def list_agent_dags(
    session: AsyncSession,
    *,
    limit: int = 50,
    cursor: int | None = None,
) -> list[AgentDag]:
    q = select(AgentDag).order_by(AgentDag.id.desc()).limit(limit)
    if cursor is not None:
        q = q.where(AgentDag.id < cursor)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_agent_dag(session: AsyncSession, dag_id: str, **kwargs: Any) -> AgentDag:
    dag = await get_agent_dag(session, dag_id)
    for key, val in kwargs.items():
        if val is not None or key in ("description",):
            setattr(dag, key, val)
    dag.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(dag)
    return dag


async def delete_agent_dag(session: AsyncSession, dag_id: str) -> None:
    dag = await get_agent_dag(session, dag_id)
    await session.delete(dag)
    await session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Agent Skills (J11)
# ─────────────────────────────────────────────────────────────────────────────


async def list_skills_for_agent(session: AsyncSession, agent_db_id: int) -> list[Skill]:
    """Return all skills assigned to an agent, ordered by slug."""
    result = await session.execute(
        select(Skill)
        .join(AgentSkill, AgentSkill.skill_slug == Skill.slug)
        .where(AgentSkill.agent_id == agent_db_id)
        .order_by(Skill.slug.asc())
    )
    return list(result.scalars().all())


async def assign_skill_to_agent(
    session: AsyncSession, agent_db_id: int, skill_slug: str
) -> AgentSkill:
    """Assign a skill to an agent. Idempotent — returns existing row if already assigned."""
    result = await session.execute(
        select(AgentSkill)
        .where(AgentSkill.agent_id == agent_db_id, AgentSkill.skill_slug == skill_slug)
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    row = AgentSkill(agent_id=agent_db_id, skill_slug=skill_slug)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def unassign_skill_from_agent(
    session: AsyncSession, agent_db_id: int, skill_slug: str
) -> None:
    """Remove a skill assignment. No-op if not assigned."""
    result = await session.execute(
        select(AgentSkill)
        .where(AgentSkill.agent_id == agent_db_id, AgentSkill.skill_slug == skill_slug)
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Run observability helpers (J11)
# ─────────────────────────────────────────────────────────────────────────────


async def list_active_agent_runs(session: AsyncSession) -> list[AgentRun]:
    """Return runs with status 'running' or 'pending', ordered by started_at DESC."""
    result = await session.execute(
        select(AgentRun)
        .where(or_(AgentRun.status == "running", AgentRun.status == "pending"))
        .order_by(AgentRun.started_at.desc())
    )
    return list(result.scalars().all())


async def list_recent_agent_runs(session: AsyncSession, *, limit: int = 50) -> list[AgentRun]:
    """Return the most recent N runs across all agents, ordered by started_at DESC."""
    result = await session.execute(
        select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def search_agent_runs(session: AsyncSession, q: str, *, limit: int = 100) -> list[AgentRun]:
    """Substring match against user_message and error columns, ordered by started_at DESC."""
    pattern = f"%{q}%"
    result = await session.execute(
        select(AgentRun)
        .where(
            or_(
                AgentRun.user_message.ilike(pattern),
                AgentRun.error.ilike(pattern),
            )
        )
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# O2/O3 — Recent runs with trajectory summaries for Agent Card detail
# ─────────────────────────────────────────────────────────────────────────────


async def list_recent_runs_with_trajectory(
    session: AsyncSession, agent_id: str, *, limit: int = 10
) -> list[dict[str, Any]]:
    """Return the last N runs for an agent, with trajectory summary if available.

    Returns a list of plain dicts ready for JSON serialisation:
      {id, run_id, status, duration_s, started_at, trajectory_summary}
    where trajectory_summary is a brief phrase from the most informative
    trajectory column (what_worked / what_stalled / what_was_missing), or None.
    """
    result = await session.execute(
        select(AgentRun)
        .where(AgentRun.agent_id == agent_id, AgentRun.is_ephemeral.is_(False))
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
    )
    runs = list(result.scalars().all())
    if not runs:
        return []

    # Fetch trajectory summaries for these run IDs (integer PKs).
    run_int_ids = [r.id for r in runs]
    traj_result = await session.execute(
        select(AgentRunTrajectorySummary).where(
            AgentRunTrajectorySummary.run_id.in_(run_int_ids)
        )
    )
    traj_by_id: dict[int, AgentRunTrajectorySummary] = {
        t.run_id: t for t in traj_result.scalars().all()
    }

    out = []
    for run in runs:
        traj = traj_by_id.get(run.id)
        summary = (
            traj.what_stalled or traj.what_worked or traj.what_was_missing if traj else None
        )

        duration_s: float | None = None
        if run.completed_at and run.started_at:
            delta = run.completed_at - run.started_at
            duration_s = round(delta.total_seconds(), 1)

        out.append(
            {
                "id": run.id,
                "run_id": run.run_id,
                "status": run.status,
                "duration_s": duration_s,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "trajectory_summary": summary,
            }
        )
    return out
