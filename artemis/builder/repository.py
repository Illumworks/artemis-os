"""DB repository helpers for the Builder domain (O1).

All functions are async and accept a SQLAlchemy AsyncSession.
Conventions match artemis/builders/repository.py:
  - Raise ValueError for not-found (callers convert to 404).
  - No business logic — pure DB read/write.
  - Callers own commit/rollback.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders.models import (
    AgentRun,
    AgentRunTrajectorySummary,
    BuilderSession,
    DefinitionProposal,
)

# ── BuilderSession ─────────────────────────────────────────────────────────────


async def create_builder_session(
    session: AsyncSession,
    *,
    builder_kind: str = "agent",
    target_id: int | None = None,
    user_id: str | None = None,
) -> BuilderSession:
    row = BuilderSession(
        builder_kind=builder_kind,
        target_id=target_id,
        user_id=user_id,
        status="active",
        conversation=[],
        draft=None,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_builder_session(session: AsyncSession, session_id: int) -> BuilderSession:
    result = await session.execute(
        select(BuilderSession).where(BuilderSession.id == session_id).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"BuilderSession {session_id!r} not found")
    return row


async def list_builder_sessions(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[BuilderSession]:
    stmt = select(BuilderSession).order_by(BuilderSession.updated_at.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(BuilderSession.user_id == user_id)
    if status is not None:
        stmt = stmt.where(BuilderSession.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_builder_session(
    session: AsyncSession,
    session_id: int,
    **kwargs: Any,
) -> BuilderSession:
    row = await get_builder_session(session, session_id)
    for key, value in kwargs.items():
        setattr(row, key, value)
    row.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    return row


async def append_builder_message(
    session: AsyncSession,
    session_id: int,
    role: str,
    content: str,
) -> BuilderSession:
    """Append a message to the session's conversation JSONB array."""
    row = await get_builder_session(session, session_id)
    conversation: list[dict[str, Any]] = list(row.conversation or [])
    conversation.append({"role": role, "content": content})
    row.conversation = conversation
    row.updated_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    return row


async def abandon_builder_session(session: AsyncSession, session_id: int) -> BuilderSession:
    return await update_builder_session(session, session_id, status="abandoned")


# ── DefinitionProposal ────────────────────────────────────────────────────────


async def create_definition_proposal(
    session: AsyncSession,
    *,
    builder_session_id: int | None = None,
    kind: str,
    target_id: int | None = None,
    proposed_by: str,
    proposed_definition: dict[str, Any],
    citations: dict[str, Any] | None = None,
) -> DefinitionProposal:
    row = DefinitionProposal(
        builder_session_id=builder_session_id,
        kind=kind,
        target_id=target_id,
        proposed_by=proposed_by,
        proposed_definition=proposed_definition,
        citations=citations,
        status="pending",
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_definition_proposal(session: AsyncSession, proposal_id: int) -> DefinitionProposal:
    result = await session.execute(
        select(DefinitionProposal).where(DefinitionProposal.id == proposal_id).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"DefinitionProposal {proposal_id!r} not found")
    return row


async def list_definition_proposals(
    session: AsyncSession,
    *,
    status: str | None = "pending",
    kind: str | None = None,
    limit: int = 50,
) -> list[DefinitionProposal]:
    stmt = select(DefinitionProposal).order_by(DefinitionProposal.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(DefinitionProposal.status == status)
    if kind is not None:
        stmt = stmt.where(DefinitionProposal.kind == kind)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def approve_proposal(session: AsyncSession, proposal_id: int) -> DefinitionProposal:
    row = await get_definition_proposal(session, proposal_id)
    if row.status != "pending":
        raise ValueError(f"Proposal {proposal_id} is not pending (status={row.status!r})")
    row.status = "approved"
    await session.flush()
    await session.refresh(row)
    return row


async def reject_proposal(
    session: AsyncSession,
    proposal_id: int,
    *,
    rejection_reason: str | None = None,
) -> DefinitionProposal:
    """Flip a pending proposal to ``rejected``.

    CC22: optional ``rejection_reason`` is captured alongside ``rejected_at``
    (always set on flip).  Backward-compat: callers that don't pass a reason
    still reject the proposal cleanly; rejection_reason just stays NULL.
    """
    row = await get_definition_proposal(session, proposal_id)
    if row.status != "pending":
        raise ValueError(f"Proposal {proposal_id} is not pending (status={row.status!r})")
    row.status = "rejected"
    row.rejection_reason = rejection_reason
    row.rejected_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(row)
    return row


async def supersede_pending_proposals_for_target(
    session: AsyncSession,
    kind: str,
    target_id: int,
    except_proposal_id: int,
) -> None:
    """Mark all other pending proposals for the same target as superseded."""
    result = await session.execute(
        select(DefinitionProposal).where(
            DefinitionProposal.kind == kind,
            DefinitionProposal.target_id == target_id,
            DefinitionProposal.status == "pending",
            DefinitionProposal.id != except_proposal_id,
        )
    )
    for row in result.scalars().all():
        row.status = "superseded"
    await session.flush()


# ── AgentRunTrajectorySummary ─────────────────────────────────────────────────


async def create_trajectory_summary(
    session: AsyncSession,
    *,
    run_id: int,
    what_worked: str | None = None,
    what_stalled: str | None = None,
    what_was_missing: str | None = None,
) -> AgentRunTrajectorySummary:
    row = AgentRunTrajectorySummary(
        run_id=run_id,
        what_worked=what_worked,
        what_stalled=what_stalled,
        what_was_missing=what_was_missing,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_trajectory_summaries_for_agent(
    session: AsyncSession,
    agent_id: str,
    limit: int = 10,
) -> list[AgentRunTrajectorySummary]:
    """Fetch trajectory summaries for the most recent runs of an agent."""
    stmt = (
        select(AgentRunTrajectorySummary)
        .join(AgentRun, AgentRun.id == AgentRunTrajectorySummary.run_id)
        .where(AgentRun.agent_id == agent_id)
        .order_by(AgentRunTrajectorySummary.generated_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_trajectory_summary(
    session: AsyncSession, run_id: int
) -> AgentRunTrajectorySummary | None:
    result = await session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run_id).limit(1)
    )
    return result.scalar_one_or_none()
