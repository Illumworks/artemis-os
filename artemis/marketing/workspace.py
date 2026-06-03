"""Workspace state path-walking helpers.

Shared by approvals.py (Gate-2 decisions), the deliverable-run agent
executor (FIX115 — content nodes), and the writing_studio adapter event
handler. The workspace state machine only permits single-step transitions,
so anything that wants to "catch up" workspace_state from where it is now to
where deliverable progress says it should be must walk the legal path
rather than attempt an illegal one-hop jump (which raises IllegalTransition
out of transition()).
"""

from __future__ import annotations

import logging
from collections import deque

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.state_machine import (
    WORKSPACE_TRANSITIONS,
    DeliverableState,
    IllegalTransition,
    WorkspaceState,
    transition,
)

logger = logging.getLogger(__name__)


# FIX115: deliverable-run nodes that mark concrete workspace progress.
# Keyed by pipeline node id; value is the workspace state the candidate
# should have reached by the time the node succeeds. Idempotent — re-runs
# no-op via walk_workspace_state_to.
NODE_WORKSPACE_TARGETS: dict[str, WorkspaceState] = {
    "content_asset_selector": WorkspaceState.in_content_preparation,
    "content_writing_studio_adapter": WorkspaceState.sent_to_writing_studio,
}


def workspace_path(
    from_state: WorkspaceState, to_state: WorkspaceState
) -> list[WorkspaceState] | None:
    """Shortest legal WORKSPACE_TRANSITIONS path from *from_state* to *to_state*.

    Returns the list of intermediate+final states to step through (exclusive
    of from_state, inclusive of to_state), [] when already at to_state, or
    None when to_state is unreachable (e.g. behind a terminal state).
    """
    if from_state == to_state:
        return []
    queue: deque[tuple[WorkspaceState, list[WorkspaceState]]] = deque([(from_state, [])])
    seen: set[WorkspaceState] = {from_state}
    while queue:
        current, path = queue.popleft()
        for nxt in WORKSPACE_TRANSITIONS.get(current, set()):
            if nxt in seen:
                continue
            next_path = [*path, nxt]
            if nxt == to_state:
                return next_path
            seen.add(nxt)
            queue.append((nxt, next_path))
    return None


async def walk_workspace_state_to(
    session: AsyncSession,
    candidate_id: int,
    target: WorkspaceState,
    *,
    actor: str | None = None,
    reason: str | None = None,
) -> None:
    """Walk a candidate's workspace_state along the legal path to *target*.

    Idempotent: no-op when already at target. When the target is unreachable
    (legacy/unknown current state, or a terminal state already reached),
    falls back to a direct transition so transition() raises a clear
    IllegalTransition rather than silently desyncing.
    """
    candidate = await session.get(CampaignCandidate, candidate_id)
    if candidate is None or candidate.workspace_state == target.value:
        return
    try:
        current = WorkspaceState(candidate.workspace_state)
    except ValueError:
        current = None
    path = workspace_path(current, target) if current is not None else None
    steps = path if path else [target]
    for step in steps:
        await transition(
            session,
            "workspace",
            candidate_id,
            step,
            actor=actor,
            reason=reason,
        )


async def recompute_workspace_state_from_deliverables(
    session: AsyncSession,
    candidate_id: int,
    *,
    actor: str | None = None,
    reason: str | None = None,
) -> None:
    """Recompute target workspace_state from deliverable statuses and walk to it.

    Rules:
      - Any deliverable rejected → revision_needed
      - All deliverables approved → all_content_approved
      - Any draft_ready → content_in_review
      - Otherwise → in_content_preparation

    No-op when there are no deliverables yet (callers wanting a forward
    push without deliverables should use walk_workspace_state_to directly).
    """
    result = await session.execute(
        select(CampaignDeliverable)
        .where(CampaignDeliverable.candidate_id == candidate_id)
        .order_by(CampaignDeliverable.id)
    )
    deliverables = list(result.scalars().all())
    if not deliverables:
        return

    statuses = [d.status for d in deliverables]
    if any(s == DeliverableState.rejected for s in statuses):
        target = WorkspaceState.revision_needed
    elif all(s == DeliverableState.approved for s in statuses):
        target = WorkspaceState.all_content_approved
    elif any(s == DeliverableState.draft_ready for s in statuses):
        target = WorkspaceState.content_in_review
    else:
        target = WorkspaceState.in_content_preparation

    await walk_workspace_state_to(session, candidate_id, target, actor=actor, reason=reason)


async def advance_workspace_for_node(
    session: AsyncSession,
    candidate_id: int,
    node_id: str,
    *,
    actor: str | None = None,
) -> None:
    """FIX115 hook: advance a candidate's workspace_state after a content node.

    Walks to the node's declared target (when present in NODE_WORKSPACE_TARGETS)
    and then recomputes from any deliverables that exist — so a deliverable_X
    node that lands its draft at draft_ready ends the run at content_in_review,
    not pending_content. All steps are idempotent; an unreachable target is
    logged but never raised — the run should not fail because workspace status
    desynced.
    """
    target = NODE_WORKSPACE_TARGETS.get(node_id)
    reason = f"node:{node_id}" if node_id else None
    if target is not None:
        try:
            await walk_workspace_state_to(session, candidate_id, target, actor=actor, reason=reason)
        except IllegalTransition as exc:
            logger.warning(
                "[workspace] node-target advance skipped for candidate=%s node=%s: %s",
                candidate_id,
                node_id,
                exc,
            )
    try:
        await recompute_workspace_state_from_deliverables(
            session, candidate_id, actor=actor, reason=reason
        )
    except IllegalTransition as exc:
        logger.warning(
            "[workspace] deliverable recompute skipped for candidate=%s node=%s: %s",
            candidate_id,
            node_id,
            exc,
        )
