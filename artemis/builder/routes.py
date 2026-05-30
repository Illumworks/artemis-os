"""HTTP routes for the Builder API (O1).

Endpoints:
  GET    /api/builder/sessions              — list user's builder sessions
  POST   /api/builder/sessions              — start a new session
  GET    /api/builder/sessions/{id}         — session detail + conversation
  POST   /api/builder/sessions/{id}/messages — send user message, get builder response
  DELETE /api/builder/sessions/{id}         — abandon session

  POST   /api/builder/sessions/{id}/test-run   — sandboxed trial run (Decision 2 Option A)
  GET    /api/builder/sessions/{id}/test-run/{run_id} — poll test run result

  GET    /api/builder/proposals             — list pending proposals
  GET    /api/builder/proposals/{id}        — one proposal + diff
  POST   /api/builder/proposals/{id}/approve — commit to real definition tables
  POST   /api/builder/proposals/{id}/reject  — decline

  GET    /api/agents/{agent_id}/builder-context — recent runs + trajectory summaries + proposals
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
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

_PROVIDER_CASCADE = ("claude-code", "codex", "lm-studio", "anthropic")


def _resolve_builder_adapter() -> Any:
    """Walk provider cascade and return the first available adapter.

    Chain: claude-code → codex → lm-studio → anthropic
    Raises HTTPException(503) if nothing is available.
    """
    from artemis.providers import get_adapter
    from artemis.providers.errors import MissingApiKeyError, UnknownProviderError

    for candidate in _PROVIDER_CASCADE:
        try:
            return get_adapter(candidate)
        except (MissingApiKeyError, UnknownProviderError):
            continue
        except Exception:
            continue
    raise bad_request(
        "No LLM provider is available. Add an API key in Integrations.",
        "no_provider",
    )


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
    return {"sessions": [BuilderSessionRead.model_validate(r).model_dump() for r in rows]}


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

    adapter = _resolve_builder_adapter()

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


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(
    session_id: int,
    body: BuilderMessageCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> StreamingResponse:
    """Streaming SSE endpoint for Builder messages (O4).

    Returns a text/event-stream response that emits turn_start, tool_call,
    tool_result, assistant_token, proposal_staged, heartbeat, turn_complete,
    and error events.  The existing synchronous POST /messages endpoint is
    unaffected and remains the default for CLI/non-browser callers.
    """
    from artemis.builder.agent_builder import BuilderEvent, handle_turn_stream
    from artemis.builder.repository import get_builder_session

    try:
        sess_row = await get_builder_session(session, session_id)
    except ValueError:
        raise not_found(f"BuilderSession {session_id} not found", "builder_session_not_found")  # noqa: B904

    if sess_row.status != "active":
        raise bad_request(
            f"BuilderSession {session_id} is not active (status={sess_row.status!r})",
            "builder_session_not_active",
        )

    adapter = _resolve_builder_adapter()

    async def _event_stream() -> AsyncIterator[str]:
        """Emit SSE events; heartbeat every 15 s during slow turns."""
        import datetime

        q: asyncio.Queue[BuilderEvent | None] = asyncio.Queue()

        async def _drain() -> None:
            try:
                async for ev in handle_turn_stream(
                    builder_session_id=session_id,
                    user_text=body.content,
                    adapter=adapter,
                    db_session=session,
                ):
                    await q.put(ev)
            finally:
                await q.put(None)  # sentinel

        task = asyncio.ensure_future(_drain())
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield BuilderEvent(
                        "heartbeat", {"ts": datetime.datetime.now(datetime.UTC).isoformat()}
                    ).to_sse()
                    continue
                if ev is None:
                    break
                yield ev.to_sse()
                if ev.type in ("turn_complete", "error"):
                    break
        except asyncio.CancelledError:
            logger.info("builder SSE cancelled for session %s (client disconnect)", session_id)
            task.cancel()
        finally:
            if not task.done():
                task.cancel()

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )


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


# ── Test-run (Decision 2 — Option A whitelist) ────────────────────────────────


class TestRunRequest(BaseModel):
    """Body for POST /api/builder/sessions/{id}/test-run."""

    prompt: str
    allow_writes: bool = False


@router.post("/sessions/{session_id}/test-run", status_code=202)
async def create_test_run(
    session_id: int,
    body: TestRunRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Run a sandboxed trial of the session's current draft definition.

    Decision 2 Option A: tools are filtered through _TEST_RUN_SAFE_TOOLS.
    Only read-only tools are permitted. Write tools appear in tools_skipped.
    Total tool calls are capped at 20.

    allow_writes=True bypasses the whitelist — use only for final pre-deployment
    testing and only with explicit user confirmation in the UI.
    """
    from artemis.builder.engine import sandbox_run as test_run  # API name kept as test-run in HTTP
    from artemis.builder.repository import get_builder_session

    # Verify session + get current draft
    try:
        sess_row = await get_builder_session(session, session_id)
    except ValueError:
        raise not_found(  # noqa: B904
            f"BuilderSession {session_id} not found", "builder_session_not_found"
        )

    if sess_row.status != "active":
        raise bad_request(
            f"BuilderSession {session_id} is not active (status={sess_row.status!r})",
            "builder_session_not_active",
        )

    draft = sess_row.draft or {}
    if not draft:
        raise bad_request(
            "No draft definition in this session yet — build a definition first.",
            "no_draft",
        )

    adapter = _resolve_builder_adapter()

    try:
        result = await test_run(
            draft,
            body.prompt,
            adapter=adapter,
            allow_writes=body.allow_writes,
        )
    except Exception as exc:
        logger.exception("test_run failed for session %s", session_id)
        raise bad_request(str(exc), "test_run_failed")  # noqa: B904

    return {
        "session_id": session_id,
        "status": "completed",
        **result,
    }


@router.get("/sessions/{session_id}/test-run/{run_id}")
async def get_test_run(
    session_id: int,
    run_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Placeholder poll endpoint — test-run is synchronous in v1.

    The POST endpoint returns the full result synchronously (no background job).
    This endpoint exists to fulfil the interface spec and will be upgraded to
    a streaming/polling pattern if test runs become long-running.
    """
    return {
        "session_id": session_id,
        "run_id": run_id,
        "status": "not_applicable",
        "note": "test-run is synchronous in v1; results are in the POST response.",
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
    return {"proposals": [DefinitionProposalRead.model_validate(r).model_dump() for r in rows]}


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
    from artemis.builder.memory_carryover import write_proposal_approval_observation
    from artemis.builder.repository import get_definition_proposal

    try:
        proposal = await get_definition_proposal(session, proposal_id)
    except ValueError:
        raise not_found(f"Proposal {proposal_id} not found", "proposal_not_found")  # noqa: B904

    # Capture proposal fields before commit (session still valid pre-commit)
    _kind = proposal.kind
    _target_id = proposal.target_id
    _proposed_definition = dict(proposal.proposed_definition or {})
    _proposed_by = proposal.proposed_by
    _citations = dict(proposal.citations) if proposal.citations else None
    _builder_session_id = proposal.builder_session_id

    try:
        result = await commit(proposal_id, db_session=session)
    except ValueError as exc:
        raise bad_request(str(exc), "proposal_cannot_commit")  # noqa: B904

    # already_approved is idempotent — no memory write needed for a no-op
    if result.get("status") == "already_approved":
        await session.commit()
        return result

    await session.commit()

    # Resolve target slug (agent_id string or skill slug) for the memory scope id.
    # For agent: look up agent_id string from agents.id; for skill: look up slug.
    target_slug: str = (
        _proposed_definition.get("agent_id") or _proposed_definition.get("slug") or ""
    )
    if _kind == "agent" and _target_id is not None:
        try:
            from sqlalchemy import select as _sa_select

            from artemis.builders.models import Agent as _Agent

            _r = await session.execute(
                _sa_select(_Agent.agent_id).where(_Agent.id == _target_id).limit(1)
            )
            _slug = _r.scalar_one_or_none()
            if _slug:
                target_slug = _slug
        except Exception:
            pass
    elif _kind == "skill" and _target_id is not None:
        try:
            from sqlalchemy import select as _sa_select

            from artemis.builders.models import Skill as _Skill

            _r = await session.execute(
                _sa_select(_Skill.slug).where(_Skill.id == _target_id).limit(1)
            )
            _slug = _r.scalar_one_or_none()
            if _slug:
                target_slug = _slug
        except Exception:
            pass
    # Fallback: use slug from commit result
    if not target_slug:
        target_slug = result.get("agent_id") or result.get("slug") or str(_target_id or proposal_id)

    await write_proposal_approval_observation(
        proposal_id=proposal_id,
        kind=_kind,
        target_id=_target_id,
        target_slug=target_slug,
        proposed_definition=_proposed_definition,
        proposed_by=_proposed_by,
        citations=_citations,
        builder_session_id=_builder_session_id,
    )

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


@router.get("/inbox")
async def get_builder_inbox(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Proposals Inbox — cross-agent discovery surface (J6a).

    Returns three lists:
      agents_with_pending_proposals — agents that have >= 1 pending proposal,
        with proposal_ids so the caller can inline-approve/reject the first one.
      agents_with_new_summaries — agents with trajectory summaries newer than
        last_reviewed_at (or last_reviewed_at IS NULL).  Agents already in the
        proposals list are excluded to avoid double-listing.
      skills_with_pending_proposals — skills with >= 1 pending proposal.
    """
    from sqlalchemy import text as sa_text

    # ── agents with pending proposals ─────────────────────────────────────────
    proposals_q = sa_text(
        """
        SELECT
            a.agent_id,
            a.name,
            COUNT(dp.id)               AS pending_count,
            MAX(dp.created_at)         AS last_proposal_at,
            MAX(ats.generated_at)      AS last_summary_at,
            a.last_reviewed_at,
            ARRAY_AGG(dp.id ORDER BY dp.created_at ASC) AS proposal_ids
        FROM agents a
        JOIN definition_proposals dp
            ON dp.target_id = CAST(a.id AS integer)
           AND dp.kind = 'agent'
           AND dp.status = 'pending'
        LEFT JOIN agent_runs ar ON ar.agent_id = a.agent_id
        LEFT JOIN agent_run_trajectory_summaries ats ON ats.run_id = ar.id
        GROUP BY a.agent_id, a.name, a.last_reviewed_at
        ORDER BY last_proposal_at DESC
        """
    )
    proposals_result = await session.execute(proposals_q)
    proposals_rows = proposals_result.fetchall()

    agents_with_pending_proposals = [
        {
            "agent_id": row.agent_id,
            "name": row.name,
            "kind": "agent",
            "pending_count": row.pending_count,
            "last_proposal_at": row.last_proposal_at.isoformat() if row.last_proposal_at else None,
            "last_summary_at": row.last_summary_at.isoformat() if row.last_summary_at else None,
            "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
            "proposal_ids": list(row.proposal_ids) if row.proposal_ids else [],
        }
        for row in proposals_rows
    ]

    # Set of agent_ids already captured above — exclude from summaries list.
    proposal_agent_ids = {r["agent_id"] for r in agents_with_pending_proposals}

    # ── agents with new summaries (no pending proposals) ─────────────────────
    summaries_q = sa_text(
        """
        SELECT
            a.agent_id,
            a.name,
            COUNT(ats.run_id)          AS new_summary_count,
            MAX(ats.generated_at)      AS last_summary_at,
            a.last_reviewed_at
        FROM agents a
        JOIN agent_runs ar ON ar.agent_id = a.agent_id
        JOIN agent_run_trajectory_summaries ats ON ats.run_id = ar.id
        WHERE (
            a.last_reviewed_at IS NULL
            OR ats.generated_at > a.last_reviewed_at
        )
        GROUP BY a.agent_id, a.name, a.last_reviewed_at
        ORDER BY last_summary_at DESC
        """
    )
    summaries_result = await session.execute(summaries_q)
    summaries_rows = summaries_result.fetchall()

    agents_with_new_summaries = [
        {
            "agent_id": row.agent_id,
            "name": row.name,
            "new_summary_count": row.new_summary_count,
            "last_summary_at": row.last_summary_at.isoformat() if row.last_summary_at else None,
            "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
        }
        for row in summaries_rows
        if row.agent_id not in proposal_agent_ids
    ]

    # ── skills with pending proposals ─────────────────────────────────────────
    skills_q = sa_text(
        """
        SELECT
            s.slug                     AS skill_id,
            s.name,
            COUNT(dp.id)               AS pending_count,
            MAX(dp.created_at)         AS last_proposal_at,
            ARRAY_AGG(dp.id ORDER BY dp.created_at ASC) AS proposal_ids
        FROM skills s
        JOIN definition_proposals dp
            ON dp.target_id = CAST(s.id AS integer)
           AND dp.kind = 'skill'
           AND dp.status = 'pending'
        GROUP BY s.slug, s.name
        ORDER BY last_proposal_at DESC
        """
    )
    skills_result = await session.execute(skills_q)
    skills_rows = skills_result.fetchall()

    skills_with_pending_proposals = [
        {
            "skill_id": row.skill_id,
            "name": row.name,
            "pending_count": row.pending_count,
            "last_proposal_at": row.last_proposal_at.isoformat() if row.last_proposal_at else None,
            "proposal_ids": list(row.proposal_ids) if row.proposal_ids else [],
        }
        for row in skills_rows
    ]

    return {
        "agents_with_pending_proposals": agents_with_pending_proposals,
        "agents_with_new_summaries": agents_with_new_summaries,
        "skills_with_pending_proposals": skills_with_pending_proposals,
    }


@agents_subresource_router.post("/{agent_id}/mark-reviewed")
async def mark_agent_reviewed(
    agent_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Mark an agent as reviewed — stamps last_reviewed_at = now().

    Called by the Proposals Inbox when an operator clicks "Review with Builder"
    so the agent drops out of the new-summaries list after the next reload.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    from artemis.builders.models import Agent

    # Confirm agent exists.
    result = await session.execute(sa_select(Agent).where(Agent.agent_id == agent_id).limit(1))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise not_found(f"Agent '{agent_id}' not found", "agent_not_found")

    now = await session.scalar(sa_select(sa_func.now()))
    await session.execute(
        sa_update(Agent).where(Agent.agent_id == agent_id).values(last_reviewed_at=now)
    )
    await session.commit()

    return {
        "status": "ok",
        "agent_id": agent_id,
        "last_reviewed_at": now.isoformat() if now else None,
    }


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
    result = await session.execute(sa_select(Agent).where(Agent.agent_id == agent_id).limit(1))
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
    trajectory_summaries = [TrajectorySummaryRead.model_validate(r).model_dump() for r in traj_rows]

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
