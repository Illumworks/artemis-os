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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders.models import (
    Agent,
    AgentChain,
    AgentContext,
    AgentDag,
    AgentRun,
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
) -> list[AgentRun]:
    q = select(AgentRun).order_by(AgentRun.id.desc()).limit(limit)
    if agent_id is not None:
        q = q.where(AgentRun.agent_id == agent_id)
    if status is not None:
        q = q.where(AgentRun.status == status)
    if cursor is not None:
        q = q.where(AgentRun.id < cursor)
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
    """Upsert a context key for a run (insert or replace)."""
    # Check existence
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


async def get_agent_context(session: AsyncSession, run_id: str, key: str) -> AgentContext:
    result = await session.execute(
        select(AgentContext).where(AgentContext.run_id == run_id, AgentContext.key == key).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"AgentContext key '{key}' not found for run '{run_id}'")
    return row


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
    limit: int = 50,
    cursor: int | None = None,
) -> list[Skill]:
    q = select(Skill).order_by(Skill.id.desc()).limit(limit)
    if kind is not None:
        q = q.where(Skill.kind == kind)
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
