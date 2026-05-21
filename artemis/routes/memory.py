"""Memory M2 — HTTP routes for conflict management and observation history.

Mounted under /api/memory. Three endpoints:
  GET  /api/memory/conflicts                     — list unresolved conflicts
  POST /api/memory/conflicts/{id}/resolve        — apply resolution
  GET  /api/memory/observations/{id}/history     — supersession chain
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.memory.repository import (
    get_observation_history,
    list_conflicts_unresolved,
    resolve_conflict,
)
from artemis.memory.schemas import Conflict, ConflictResolveRequest, Observation

router = APIRouter(prefix="/api/memory", tags=["memory"])

_VALID_RESOLUTIONS = frozenset(
    {"a_wins", "b_wins", "both_valid_different_scope", "manual_review_needed"}
)


# ── GET /api/memory/conflicts ─────────────────────────────────────────────────


@router.get("/conflicts", response_model=list[Conflict])
async def list_conflicts(
    scope_id: Annotated[str | None, Query(description="Filter by scope_id")] = None,
    status: Annotated[str | None, Query(description="'unresolved' (default) or 'all'")] = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[Conflict]:
    """List memory conflicts.

    status=unresolved (default) returns only rows with resolution=NULL.
    status=all is not yet implemented (returns unresolved for now).
    """
    return await list_conflicts_unresolved(session, scope_id=scope_id)


# ── POST /api/memory/conflicts/{id}/resolve ───────────────────────────────────


@router.post("/conflicts/{conflict_id}/resolve", response_model=Conflict)
async def resolve_conflict_endpoint(
    conflict_id: int,
    body: ConflictResolveRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Conflict:
    """Resolve a conflict.

    Valid resolutions: a_wins, b_wins, both_valid_different_scope, manual_review_needed.

    a_wins: obs_b.valid_until set to now; obs_b.supersedes = obs_a.id.
    b_wins: obs_a.valid_until set to now; obs_a.supersedes = obs_b.id.
    both_valid_different_scope: no observation change; conflict row closed.
    manual_review_needed: marks for human attention; no observation change.
    """
    if body.resolution not in _VALID_RESOLUTIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Invalid resolution: {body.resolution!r}",
                "code": "invalid_resolution",
                "valid": sorted(_VALID_RESOLUTIONS),
            },
        )
    try:
        async with session.begin():
            conflict = await resolve_conflict(
                session,
                conflict_id=conflict_id,
                resolution=body.resolution,
                reason=body.reason,
                resolver="operator",
            )
    except ValueError as exc:
        raise HTTPException(  # noqa: B904
            status_code=404, detail={"error": str(exc), "code": "not_found"}
        )
    return conflict


# ── GET /api/memory/observations/{id}/history ─────────────────────────────────


@router.get("/observations/{observation_id}/history", response_model=list[Observation])
async def observation_history(
    observation_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[Observation]:
    """Return the supersession chain for an observation.

    result[0] is the requested observation; result[-1] is the oldest ancestor.
    Returns 404 if the observation is not found.
    """
    chain = await get_observation_history(session, observation_id)
    if not chain:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Observation {observation_id} not found", "code": "not_found"},
        )
    return chain


@router.get("/embeddings/status")
async def embeddings_status() -> dict[str, object]:
    """Return embedding job queue status (stub — embedding pipeline not yet implemented)."""
    return {"queued": 0, "processing": 0, "completed_today": 0, "last_error": None}
