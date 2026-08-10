"""Memory HTTP routes.

Mounted under /api/memory.

M3 (identity-aware scope enforcement):
  All read endpoints derive the caller's identity via CF Access / dev shim,
  resolve their scope allowance via ``allowed_scopes_for_email``, and constrain
  every query to that allowance.  Caller-supplied filters outside the allowance
  are silently ignored (not widened).  ``/observations/{id}`` outside the
  caller's allowance → 404 (not 403; we do not reveal existence).

Conflict management (M2):
  GET  /api/memory/conflicts                     — list unresolved conflicts
  POST /api/memory/conflicts/{id}/resolve        — apply resolution
  GET  /api/memory/observations/{id}/history     — supersession chain

Shell UI read layer (M6) — all read-only:
  GET  /api/memory/drawers                       — paginated drawer list
  GET  /api/memory/observations                  — paginated observation list
  GET  /api/memory/observations/{id}             — detail + evidence chain
  GET  /api/memory/scopes                        — distinct scopes with row counts
  GET  /api/memory/stats                         — dashboard totals
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    # Annotation-only: `from __future__ import annotations` keeps this out of
    # the runtime import graph, but the name must still resolve for type
    # checkers (and for anything calling typing.get_type_hints()).
    from artemis.identity.models import User

from artemis.db import get_session
from artemis.identity.dependencies import resolve_request_identity
from artemis.identity.scope_policy import allowed_scopes_for_email
from artemis.marketing.routes._auth import require_owner, require_token
from artemis.memory.maintenance import run_maintenance
from artemis.memory.repository import (
    get_memory_stats,
    get_observation_detail,
    get_observation_history,
    list_conflicts_unresolved,
    list_drawers,
    list_observations,
    list_scopes,
    resolve_conflict,
)
from artemis.memory.schemas import Conflict, ConflictResolveRequest, Observation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])

_VALID_RESOLUTIONS = frozenset(
    {"a_wins", "b_wins", "both_valid_different_scope", "manual_review_needed"}
)


# ── Identity + allowance dependency ──────────────────────────────────────────


async def _get_allowance(
    request: Request,
    session: AsyncSession,
) -> Any:
    """Resolve the caller's scope allowance from their identity.

    M3 fail-closed: any error → deny (return a denied allowance, not all scopes).
    This prevents any auth/DB error from accidentally widening access.
    """
    from artemis.identity.scope_policy import allowance_denied

    try:
        identity = await resolve_request_identity(request)
        user = await _get_user_for_identity(session, identity.email, identity.name)
        return allowed_scopes_for_email(identity.email, user.id)
    except HTTPException:
        raise
    except Exception:
        logger.exception("_get_allowance: unexpected error resolving identity — denying")
        return allowance_denied()


async def _get_user_for_identity(session: AsyncSession, email: str, name: str | None) -> User:
    from artemis.identity.repository import get_or_create_user

    user = await get_or_create_user(session, email, name)
    await session.commit()
    await session.refresh(user)
    return user


# ── GET /api/memory/conflicts ─────────────────────────────────────────────────


@router.get(
    "/conflicts",
    response_model=list[Conflict],
    dependencies=[Depends(require_token), Depends(require_owner)],
)
async def list_conflicts(
    scope_id: Annotated[str | None, Query(description="Filter by scope_id")] = None,
    status: Annotated[str | None, Query(description="'unresolved' (default) or 'all'")] = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[Conflict]:
    """List memory conflicts. Owner-only — conflicts may span personal/agent scopes.

    status=unresolved (default) returns only rows with resolution=NULL.
    status=all is not yet implemented (returns unresolved for now).
    """
    return await list_conflicts_unresolved(session, scope_id=scope_id)


# ── POST /api/memory/conflicts/{id}/resolve ───────────────────────────────────


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=Conflict,
    dependencies=[Depends(require_token), Depends(require_owner)],
)
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


@router.get(
    "/observations/{observation_id}/history",
    response_model=list[Observation],
    dependencies=[Depends(require_token), Depends(require_owner)],
)
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


@router.post("/maintain", dependencies=[Depends(require_token), Depends(require_owner)])
async def maintain_memory_endpoint(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, int]:
    """Run one maintenance pass and return updated row counts per category. Owner-only."""
    async with session.begin():
        return await run_maintenance(session)


# ── M6 Shell read endpoints (all require token + M3 scope enforcement) ────────


@router.get("/drawers", dependencies=[Depends(require_token)])
async def list_drawers_endpoint(
    request: Request,
    scope_kind: Annotated[str | None, Query(description="Filter by scope_kind")] = None,
    scope_id: Annotated[str | None, Query(description="Filter by scope_id")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Paginated list of memory drawers.

    Optional filters: scope_kind, scope_id.
    Default limit 50, max 200.

    M3: Results are constrained to the caller's allowed scopes.
    """
    allowance = await _get_allowance(request, session)
    rows, total = await list_drawers(
        session,
        scope_kind=scope_kind,
        scope_id=scope_id,
        limit=limit,
        offset=offset,
        allowance=allowance,
    )
    return {"drawers": rows, "total": total, "offset": offset}


@router.get("/observations", dependencies=[Depends(require_token)])
async def list_observations_endpoint(
    request: Request,
    scope_kind: Annotated[str | None, Query(description="Filter by scope_kind")] = None,
    scope_id: Annotated[str | None, Query(description="Filter by scope_id")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Paginated list of memory observations.

    Optional filters: scope_kind, scope_id.
    Default limit 50, max 200.

    M3: Results are constrained to the caller's allowed scopes.
    Caller-supplied scope filters outside their allowance are silently ignored
    (the server returns only what the caller is permitted to see).
    """
    allowance = await _get_allowance(request, session)
    rows, total = await list_observations(
        session,
        scope_kind=scope_kind,
        scope_id=scope_id,
        limit=limit,
        offset=offset,
        allowance=allowance,
    )
    return {"observations": rows, "total": total, "offset": offset}


@router.get("/observations/{observation_id}", dependencies=[Depends(require_token)])
async def get_observation_detail_endpoint(
    observation_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Observation detail with evidence chain.

    Returns the full observation row and all evidence links with source previews.
    Returns 404 if the observation is not found.

    M3: Returns 404 (not 403) if the observation exists but is outside the
    caller's allowed scopes.  We do not reveal the existence of out-of-scope
    observations.

    Note: this route is registered before /observations/{id}/history so the
    path parameter does not shadow the 'history' literal — FastAPI resolves
    exact-match path segments first.
    """
    allowance = await _get_allowance(request, session)
    detail = await get_observation_detail(session, observation_id, allowance=allowance)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Observation {observation_id} not found", "code": "not_found"},
        )
    return detail


@router.get("/scopes", dependencies=[Depends(require_token)])
async def list_scopes_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List distinct scopes with drawer and observation counts.

    Used to populate the scope-filter UI in the memory shell.

    M3: Only scopes the caller is permitted to see are returned.
    """
    allowance = await _get_allowance(request, session)
    return await list_scopes(session, allowance=allowance)


@router.get("/stats", dependencies=[Depends(require_token)])
async def memory_stats_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Overall memory counts for the dashboard header, scoped to caller's allowance.

    Returns total_drawers, total_observations, total_evidence_links,
    scope_count, and a by_scope_kind breakdown.

    M3: aggregates are constrained to the scopes the caller may read, so
    non-owner users cannot infer the existence of personal/agent:artemis scopes.
    """
    allowance = await _get_allowance(request, session)
    return await get_memory_stats(session, allowance=allowance)
