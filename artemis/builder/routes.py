"""HTTP routes for the Builder API (O1).

Endpoints:
  GET    /api/builder/sessions              — list user's builder sessions
  POST   /api/builder/sessions              — start a new session
  GET    /api/builder/sessions/{id}         — session detail + conversation
  POST   /api/builder/sessions/{id}/messages — send user message, get builder response
  DELETE /api/builder/sessions/{id}         — abandon session

  POST   /api/builder/sessions/{id}/test-run   — STUB (pending Decision 2)
  GET    /api/builder/sessions/{id}/test-run/{run_id} — STUB (pending Decision 2)

  GET    /api/builder/proposals             — list pending proposals
  GET    /api/builder/proposals/{id}        — one proposal + diff
  POST   /api/builder/proposals/{id}/approve — commit to real definition tables
  POST   /api/builder/proposals/{id}/reject  — decline

  GET    /api/agents/{agent_id}/builder-context — recent runs + trajectory summaries + proposals
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builder.schemas import (
    AgentBuilderContext,
    BuilderMessageCreate,
    BuilderMessageResponse,
    BuilderSessionCreate,
    BuilderSessionRead,
    DefinitionProposalRead,
    TrajectorySummaryRead,
)
from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/builder",
    tags=["builder"],
    dependencies=[Depends(require_token)],
)

# Separate router for the agents subresource (no /api/builder prefix)
agents_subresource_router = APIRouter(
    prefix="/api/agents",
    tags=["agents", "builder"],
    dependencies=[Depends(require_token)],
)


# ── Sessions ───────────────────────────────────────────────────────────────────


@router.get("/sessions")
async def list_sessions(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """List in-flight builder sessions."""
    from artemis.builder.repository import list_builder_sessions

    rows = await list_builder_sessions(session, status=status, limit=limit)
    return {
        "sessions": [BuilderSessionRead.model_validate(r).model_dump() for r in rows]
    }


@router.post("/sessions", status_code=201)
async def create_session(
    body: BuilderSessionCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Start a new builder session."""
    from artemis.builder.repository import create_builder_session

    row = await create_builder_session(
        session,
        builder_kind=body.builder_kind,
        target_id=body.target_id,
        user_id=body.user_id,
    )
    await session.commit()
    return BuilderSessionRead.model_validate(row).model_dump()


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Session detail including full conversation history."""
    from artemis.builder.repository import get_builder_session

    try:
        row = await get_builder_session(session, session_id)
    except ValueError:
        raise not_found(f"BuilderSession {session_id} not found", "builder_session_not_found")  # noqa: B904
    return BuilderSessionRead.model_validate(row).model_dump()


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    body: BuilderMessageCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Send a user message and get the builder's response.

    Runs the Agent-Builder's F1 loop with the message, persists both turns,
    and returns the assistant text + current draft.
    """
    from artemis.builder.agent_builder import handle_turn
    from artemis.builder.repository import get_builder_session
    from artemis.providers import get_adapter
    from artemis.providers.errors import MissingApiKeyError, UnknownProviderError

    # Verify session exists
    try:
        sess_row = await get_builder_session(session, session_id)
    except ValueError:
        raise not_found(f"BuilderSession {session_id} not found", "builder_session_not_found")  # noqa: B904

    if sess_row.status != "active":
        raise bad_request(
            f"BuilderSession {session_id} is not active (status={sess_row.status!r})",
            "builder_session_not_active",
        )

    # Resolve adapter
    try:
        adapter = get_adapter("anthropic")
    except (MissingApiKeyError, UnknownProviderError):
        try:
            adapter = get_adapter("claude-code")
        except Exception:
            raise bad_request(  # noqa: B904
                "No LLM provider is available. Add an API key in Integrations.",
                "no_provider",
            )

    result = await handle_turn(
        builder_session_id=session_id,
        user_text=body.content,
        adapter=adapter,
        db_session=session,
    )
    await session.commit()

    return BuilderMessageResponse(
        session_id=session_id,
        assistant_text=result["assistant_text"],
        draft=result.get("draft"),
        stop_reason=result["stop_reason"],
    ).model_dump()


@router.delete("/sessions/{session_id}", status_code=204)
async def abandon_session(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    """Abandon a builder session."""
    from artemis.builder.repository import abandon_builder_session

    try:
        await abandon_builder_session(session, session_id)
    except ValueError:
        raise not_found(f"BuilderSession {session_id} not found", "builder_session_not_found")  # noqa: B904
    await session.commit()


# ── Test-run (stub — pending Decision 2) ─────────────────────────────────────


@router.post("/sessions/{session_id}/test-run", status_code=202)
async def create_test_run(
    session_id: int,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """STUB — test-run sandbox safety is pending Lead Decision 2.

    Returns 202 Accepted with a not_implemented marker so the frontend
    can detect the pending state.
    """
    logger.info(
        "test-run requested for session %s — pending Decision 2 (sandbox safety)", session_id
    )
    return {
        "status": "not_implemented",
        "reason": "test_run sandbox is pending Lead Decision 2 (safety review)",
        "session_id": session_id,
    }


@router.get("/sessions/{session_id}/test-run/{run_id}")
async def get_test_run(
    session_id: int,
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """STUB — pending Decision 2."""
    return {
        "status": "not_implemented",
        "reason": "test_run sandbox is pending Lead Decision 2 (safety review)",
        "session_id": session_id,
        "run_id": run_id,
    }


# ── Proposals ─────────────────────────────────────────────────────────────────


@router.get("/proposals")
async def list_proposals(
    status: str | None = Query(default="pending"),
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """List proposals (default: pending only)."""
    from artemis.builder.repository import list_definition_proposals

    rows = await list_definition_proposals(session, status=status, kind=kind, limit=limit)
    return {
        "proposals": [DefinitionProposalRead.model_validate(r).model_dump() for r in rows]
    }


@router.get("/proposals/{proposal_id}")
async def get_proposal(
    proposal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """One proposal detail."""
    from artemis.builder.repository import get_definition_proposal

    try:
        row = await get_definition_proposal(session, proposal_id)
    except ValueError:
        raise not_found(f"Proposal {proposal_id} not found", "proposal_not_found")  # noqa: B904
    return DefinitionProposalRead.model_validate(row).model_dump()


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Commit the proposal to the real definition tables."""
    from artemis.builder.engine import commit
    from artemis.builder.repository import get_definition_proposal

    try:
        await get_definition_proposal(session, proposal_id)
    except ValueError:
        raise not_found(f"Proposal {proposal_id} not found", "proposal_not_found")  # noqa: B904

    try:
        result = await commit(proposal_id, db_session=session)
    except ValueError as exc:
        raise bad_request(str(exc), "proposal_cannot_commit")  # noqa: B904

    await session.commit()
    return result


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal_route(
    proposal_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Decline a proposal."""
    from artemis.builder.repository import get_definition_proposal, reject_proposal

    try:
        await get_definition_proposal(session, proposal_id)
    except ValueError:
        raise not_found(f"Proposal {proposal_id} not found", "proposal_not_found")  # noqa: B904

    try:
        row = await reject_proposal(session, proposal_id)
    except ValueError as exc:
        raise bad_request(str(exc), "proposal_cannot_reject")  # noqa: B904

    await session.commit()
    return DefinitionProposalRead.model_validate(row).model_dump()


# ── Builder context (agents subresource) ─────────────────────────────────────


@agents_subresource_router.get("/{agent_id}/builder-context")
async def get_agent_builder_context(
    agent_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """What the builder sees when you open an existing agent.

    Returns: recent runs, trajectory summaries, and pending proposals
    targeting this agent.
    """
    from sqlalchemy import select as sa_select

    from artemis.builder.repository import get_trajectory_summaries_for_agent
    from artemis.builders.models import Agent, AgentRun
    from artemis.builders.schemas import AgentRunRead

    # Resolve the agent
    result = await session.execute(
        sa_select(Agent).where(Agent.agent_id == agent_id).limit(1)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise not_found(f"Agent '{agent_id}' not found", "agent_not_found")

    # Recent runs (last 10)
    runs_result = await session.execute(
        sa_select(AgentRun)
        .where(AgentRun.agent_id == agent_id)
        .order_by(AgentRun.started_at.desc())
        .limit(10)
    )
    recent_runs = [
        AgentRunRead.model_validate(r).model_dump(by_alias=True)
        for r in runs_result.scalars().all()
    ]

    # Trajectory summaries
    traj_rows = await get_trajectory_summaries_for_agent(session, agent_id, limit=10)
    trajectory_summaries = [
        TrajectorySummaryRead.model_validate(r).model_dump() for r in traj_rows
    ]

    # Pending proposals targeting this agent
    from artemis.builders.models import DefinitionProposal

    proposals_result = await session.execute(
        sa_select(DefinitionProposal).where(
            DefinitionProposal.kind == "agent",
            DefinitionProposal.target_id == agent.id,
            DefinitionProposal.status == "pending",
        )
    )
    pending_proposals = [
        DefinitionProposalRead.model_validate(r).model_dump()
        for r in proposals_result.scalars().all()
    ]

    ctx = AgentBuilderContext(
        agent_id=agent_id,
        recent_runs=recent_runs,
        trajectory_summaries=trajectory_summaries,
        pending_proposals=pending_proposals,
    )
    return ctx.model_dump()
