"""J6a — Proposals Inbox route tests.

Tests:
1. GET /api/builder/inbox returns correct JSON shape (empty state).
2. agents_with_pending_proposals aggregates correctly across two agents.
3. agents_with_new_summaries filters by last_reviewed_at (NULL = always include).
4. POST /api/agents/{id}/mark-reviewed stamps last_reviewed_at and returns ok.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func as sa_func
from sqlalchemy import select as sa_select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders.models import Agent, AgentRun, AgentRunTrajectorySummary, DefinitionProposal

# ── Fixture helpers ────────────────────────────────────────────────────────────


async def _make_agent(
    session: AsyncSession,
    agent_id: str = "inbox-agent",
    name: str = "Inbox Agent",
) -> Agent:
    """Insert a minimal agent row and return it (flushed, id available)."""
    agent = Agent(
        agent_id=agent_id,
        name=name,
        goal="test",
        system_prompt=None,
        tools=[],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    return agent


async def _make_run(
    session: AsyncSession,
    agent_id: str,
) -> AgentRun:
    """Insert a minimal completed agent_run and return it."""
    run = AgentRun(
        run_id=str(uuid.uuid4()),
        agent_id=agent_id,
        status="completed",
        user_message="run",
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def _make_trajectory_summary(
    session: AsyncSession,
    run_pk: int,
) -> AgentRunTrajectorySummary:
    """Insert a trajectory summary for a run."""
    summary = AgentRunTrajectorySummary(
        run_id=run_pk,
        what_worked="ok",
        what_stalled=None,
        what_was_missing=None,
    )
    session.add(summary)
    await session.flush()
    return summary


async def _make_proposal(
    session: AsyncSession,
    target_id: int,
    kind: str = "agent",
    status: str = "pending",
) -> DefinitionProposal:
    """Insert a minimal definition proposal."""
    proposal = DefinitionProposal(
        kind=kind,
        target_id=target_id,
        proposed_by="builder",
        proposed_definition={"name": "Draft"},
        status=status,
    )
    session.add(proposal)
    await session.flush()
    await session.refresh(proposal)
    return proposal


# ── Test 1: Empty state returns correct shape ──────────────────────────────────


@pytest.mark.asyncio
async def test_inbox_empty_shape(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/builder/inbox with no data returns three empty lists."""
    response = await client.get("/api/builder/inbox")
    assert response.status_code == 200
    body = response.json()
    assert "agents_with_pending_proposals" in body
    assert "agents_with_new_summaries" in body
    assert "skills_with_pending_proposals" in body
    assert body["agents_with_pending_proposals"] == []
    assert body["agents_with_new_summaries"] == []
    assert body["skills_with_pending_proposals"] == []


# ── Test 2: agents_with_pending_proposals aggregates across multiple agents ────


@pytest.mark.asyncio
async def test_inbox_pending_proposals_aggregation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Two agents each with proposals appear in agents_with_pending_proposals."""
    await db_session.commit()  # flush fixture truncation

    agent_a = await _make_agent(db_session, "inbox-agent-a", "Agent A")
    agent_b = await _make_agent(db_session, "inbox-agent-b", "Agent B")
    await db_session.commit()

    # 2 proposals for agent_a, 1 for agent_b
    await _make_proposal(db_session, agent_a.id, "agent", "pending")
    await _make_proposal(db_session, agent_a.id, "agent", "pending")
    await _make_proposal(db_session, agent_b.id, "agent", "pending")
    await db_session.commit()

    response = await client.get("/api/builder/inbox")
    assert response.status_code == 200
    body = response.json()

    pending = body["agents_with_pending_proposals"]
    ids_to_row = {r["agent_id"]: r for r in pending}

    assert "inbox-agent-a" in ids_to_row, f"Expected inbox-agent-a in {ids_to_row}"
    assert "inbox-agent-b" in ids_to_row, f"Expected inbox-agent-b in {ids_to_row}"

    row_a = ids_to_row["inbox-agent-a"]
    assert row_a["pending_count"] == 2
    assert len(row_a["proposal_ids"]) == 2
    assert row_a["kind"] == "agent"

    row_b = ids_to_row["inbox-agent-b"]
    assert row_b["pending_count"] == 1
    assert len(row_b["proposal_ids"]) == 1


# ── Test 3: agents_with_new_summaries respects last_reviewed_at ───────────────


@pytest.mark.asyncio
async def test_inbox_new_summaries_null_reviewed_at(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Agent with last_reviewed_at=NULL and a trajectory summary appears in new_summaries."""
    await db_session.commit()

    await _make_agent(db_session, "inbox-summary-agent", "Summary Agent")
    await db_session.commit()

    run = await _make_run(db_session, "inbox-summary-agent")
    await db_session.commit()

    await _make_trajectory_summary(db_session, run.id)
    await db_session.commit()

    response = await client.get("/api/builder/inbox")
    assert response.status_code == 200
    body = response.json()

    summaries = body["agents_with_new_summaries"]
    agent_ids = [s["agent_id"] for s in summaries]
    assert "inbox-summary-agent" in agent_ids

    row = next(s for s in summaries if s["agent_id"] == "inbox-summary-agent")
    assert row["new_summary_count"] >= 1
    assert row["last_reviewed_at"] is None


@pytest.mark.asyncio
async def test_inbox_new_summaries_excluded_when_reviewed(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Agent marked as reviewed does not appear in new_summaries."""
    await db_session.commit()

    await _make_agent(db_session, "inbox-reviewed-agent", "Reviewed Agent")
    await db_session.commit()

    run = await _make_run(db_session, "inbox-reviewed-agent")
    await db_session.commit()

    await _make_trajectory_summary(db_session, run.id)
    await db_session.commit()

    # Stamp last_reviewed_at = now() so the summary is "already seen".
    now = await db_session.scalar(sa_select(sa_func.now()))
    await db_session.execute(
        sa_update(Agent)
        .where(Agent.agent_id == "inbox-reviewed-agent")
        .values(last_reviewed_at=now)
    )
    await db_session.commit()

    response = await client.get("/api/builder/inbox")
    assert response.status_code == 200
    body = response.json()

    agent_ids = [s["agent_id"] for s in body["agents_with_new_summaries"]]
    assert "inbox-reviewed-agent" not in agent_ids


# ── Test 4: POST /agents/{id}/mark-reviewed updates column ────────────────────


@pytest.mark.asyncio
async def test_mark_agent_reviewed(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /api/agents/{agent_id}/mark-reviewed stamps last_reviewed_at."""
    await db_session.commit()

    agent = await _make_agent(db_session, "inbox-mark-reviewed", "Mark Reviewed Agent")
    assert agent.last_reviewed_at is None
    await db_session.commit()

    response = await client.post("/api/agents/inbox-mark-reviewed/mark-reviewed")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["agent_id"] == "inbox-mark-reviewed"
    assert body["last_reviewed_at"] is not None

    # Verify DB was updated.
    await db_session.refresh(agent)
    assert agent.last_reviewed_at is not None


@pytest.mark.asyncio
async def test_mark_agent_reviewed_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /api/agents/{id}/mark-reviewed returns 404 for unknown agent."""
    response = await client.post("/api/agents/nonexistent-agent-zyx/mark-reviewed")
    assert response.status_code == 404
