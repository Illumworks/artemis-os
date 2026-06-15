"""Memory M2 — Repository functions for conflict management.

LOSSLESS: resolving a conflict does NOT delete observations. It sets
valid_until on the losing observation and supersedes on the winner.
The conflict row is updated with resolution, reason, resolved_at, resolved_by.

Public API:
  list_conflicts_unresolved(session, scope_id=None) -> list[Conflict]
  resolve_conflict(session, conflict_id, resolution, reason, resolver) -> Conflict
  get_observation_history(session, observation_id) -> list[Observation]

M6 shell read API:
  list_drawers(session, ...) -> tuple[list[dict], int]
  list_observations(session, ...) -> tuple[list[dict], int]
  get_observation_detail(session, observation_id) -> dict | None
  list_scopes(session) -> list[dict]
  get_memory_stats(session) -> dict
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import (
    MemoryConflict,
    MemoryDrawer,
    MemoryEvidence,
    MemoryObservation,
)
from artemis.memory.schemas import Conflict, Observation

if TYPE_CHECKING:
    from artemis.identity.scope_policy import ScopeAllowance

_VALID_RESOLUTIONS = frozenset(
    {"a_wins", "b_wins", "both_valid_different_scope", "manual_review_needed", "auto"}
)


async def list_conflicts_unresolved(
    session: AsyncSession,
    scope_id: str | None = None,
) -> list[Conflict]:
    """Return unresolved conflict rows, optionally filtered by scope_id."""
    stmt = select(MemoryConflict).where(MemoryConflict.resolution.is_(None))
    if scope_id is not None:
        stmt = stmt.where(MemoryConflict.scope_id == scope_id)
    stmt = stmt.order_by(MemoryConflict.detected_at.desc())
    result = await session.execute(stmt)
    return [Conflict.model_validate(row) for row in result.scalars()]


async def resolve_conflict(
    session: AsyncSession,
    conflict_id: int,
    resolution: str,
    reason: str | None,
    resolver: str,
) -> Conflict:
    """Apply a resolution to a conflict row and update the losing observation.

    a_wins          → set obs_b.valid_until=now, obs_b.supersedes=obs_a.id
    b_wins          → set obs_a.valid_until=now, obs_a.supersedes=obs_b.id
    both_valid_*    → no observation change; just close the conflict row
    manual_review_needed → same as both_valid; marks for human attention
    auto            → internal path; caller must have already handled obs updates

    Raises ValueError on unknown resolution or missing conflict.
    """
    if resolution not in _VALID_RESOLUTIONS:
        raise ValueError(f"Unknown resolution: {resolution!r}")

    result = await session.execute(select(MemoryConflict).where(MemoryConflict.id == conflict_id))
    conflict_row = result.scalar_one_or_none()
    if conflict_row is None:
        raise ValueError(f"Conflict {conflict_id} not found")

    now = datetime.now(UTC)

    if resolution == "a_wins":
        await session.execute(
            update(MemoryObservation)
            .where(
                MemoryObservation.id == conflict_row.observation_b_id,
                MemoryObservation.valid_until.is_(None),
            )
            .values(
                valid_until=now,
                supersedes=conflict_row.observation_a_id,
            )
        )
    elif resolution == "b_wins":
        await session.execute(
            update(MemoryObservation)
            .where(
                MemoryObservation.id == conflict_row.observation_a_id,
                MemoryObservation.valid_until.is_(None),
            )
            .values(
                valid_until=now,
                supersedes=conflict_row.observation_b_id,
            )
        )
    # both_valid_different_scope and manual_review_needed: no obs change

    # Update conflict row
    await session.execute(
        update(MemoryConflict)
        .where(MemoryConflict.id == conflict_id)
        .values(
            resolution=resolution,
            resolution_reason=reason,
            resolved_at=now,
            resolved_by=resolver,
        )
    )
    await session.flush()

    refreshed = await session.execute(
        select(MemoryConflict).where(MemoryConflict.id == conflict_id)
    )
    return Conflict.model_validate(refreshed.scalar_one())


async def get_observation_history(
    session: AsyncSession,
    observation_id: int,
) -> list[Observation]:
    """Return the supersession chain starting from observation_id.

    Walks backward via the `supersedes` FK: result[0] is the requested
    observation, result[-1] is the oldest ancestor with supersedes=NULL.
    Returns [] if the observation is not found.
    """
    chain: list[Observation] = []
    current_id: int | None = observation_id

    visited: set[int] = set()
    while current_id is not None:
        if current_id in visited:
            break  # cycle guard
        visited.add(current_id)

        result = await session.execute(
            select(MemoryObservation).where(MemoryObservation.id == current_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            break

        chain.append(Observation.model_validate(row))
        current_id = row.supersedes

    return chain


# ── M6 Shell read helpers ──────────────────────────────────────────────────────

_PREVIEW_LEN = 200


def _allowance_clauses(allowance: "ScopeAllowance | None", model: Any) -> list[Any]:
    """Return SQLAlchemy WHERE expressions that enforce *allowance* on *model*.

    Returns an empty list when allowance is None (no enforcement — internal paths).
    Returns [False] (always-false) when allowance.denied is True.
    Returns a list of OR-able clauses otherwise.

    Callers must wrap the result with ``or_(*clauses)`` and add it to the query.

    This is imported lazily to avoid a circular-import at module load time; the
    identity.scope_policy module imports from memory.schemas only (safe).
    """
    if allowance is None:
        return []  # no enforcement for internal callers

    from sqlalchemy import false as sa_false

    sk = model.scope_kind
    sid = model.scope_id

    if allowance.denied:
        return [sa_false()]

    if allowance.allow_all:
        return []  # unrestricted

    clauses: list[Any] = []

    # personal:<user_id> — only their own
    if allowance.personal_user_id is not None:
        from sqlalchemy import and_
        clauses.append(
            and_(sk == "personal", sid == str(allowance.personal_user_id))
        )

    # agent:<id> for each permitted agent
    for agent_id in sorted(allowance.allowed_agent_ids):
        from sqlalchemy import and_
        clauses.append(and_(sk == "agent", sid == agent_id))

    # blanket scope-kind access
    for scope_kind_val in sorted(allowance.allowed_scope_kinds):
        clauses.append(sk == scope_kind_val)

    if not clauses:
        # allowance is non-deny, non-all, but has no permitted scopes — deny
        from sqlalchemy import false as sa_false
        return [sa_false()]

    return clauses


async def list_drawers(
    session: AsyncSession,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    allowance: "ScopeAllowance | None" = None,
) -> tuple[list[dict[str, Any]], int]:
    """Paginated list of drawers with content_preview.

    Returns (rows, total_count).

    When *allowance* is provided, only drawers in permitted scopes are returned.
    Caller-supplied scope_kind/scope_id filters are intersected with the
    allowance — they can never widen beyond what the allowance permits.
    """
    # M3: validate caller-supplied filter against allowance BEFORE applying it.
    # If the requested scope is outside the allowance, silently drop the filter
    # (return the caller's permitted scopes only — don't widen, don't error).
    if allowance is not None and not allowance.allow_all and not allowance.denied:
        if scope_kind is not None and scope_id is not None:
            if not allowance.permits(scope_kind, scope_id):
                scope_kind = None
                scope_id = None
        elif scope_kind is not None and scope_id is None:
            # Can't trivially validate kind-only without knowing all ids;
            # apply the SQL allowance filter which will restrict naturally.
            pass

    base = select(MemoryDrawer)
    count_base = select(func.count()).select_from(MemoryDrawer)

    # Apply allowance SQL enforcement FIRST (broadest constraint).
    acl_clauses = _allowance_clauses(allowance, MemoryDrawer)
    if acl_clauses:
        acl_filter = or_(*acl_clauses)
        base = base.where(acl_filter)
        count_base = count_base.where(acl_filter)

    if scope_kind is not None:
        base = base.where(MemoryDrawer.scope_kind == scope_kind)
        count_base = count_base.where(MemoryDrawer.scope_kind == scope_kind)
    if scope_id is not None:
        base = base.where(MemoryDrawer.scope_id == scope_id)
        count_base = count_base.where(MemoryDrawer.scope_id == scope_id)

    total_result = await session.execute(count_base)
    total: int = total_result.scalar_one()

    stmt = base.order_by(MemoryDrawer.captured_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    rows = [
        {
            "id": r.id,
            "scope_kind": r.scope_kind,
            "scope_id": r.scope_id,
            "content_preview": r.content[:_PREVIEW_LEN],
            "source": r.source_kind,
            "created_at": r.captured_at.isoformat(),
        }
        for r in result.scalars()
    ]
    return rows, total


async def list_observations(
    session: AsyncSession,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    allowance: "ScopeAllowance | None" = None,
) -> tuple[list[dict[str, Any]], int]:
    """Paginated list of observations with content_preview.

    Returns (rows, total_count).

    When *allowance* is provided, only observations in permitted scopes are
    returned.  Caller-supplied filters are intersected with the allowance.
    """
    # M3: validate caller-supplied filter against allowance.
    if allowance is not None and not allowance.allow_all and not allowance.denied:
        if scope_kind is not None and scope_id is not None:
            if not allowance.permits(scope_kind, scope_id):
                scope_kind = None
                scope_id = None

    base = select(MemoryObservation)
    count_base = select(func.count()).select_from(MemoryObservation)

    # Apply allowance SQL enforcement FIRST.
    acl_clauses = _allowance_clauses(allowance, MemoryObservation)
    if acl_clauses:
        acl_filter = or_(*acl_clauses)
        base = base.where(acl_filter)
        count_base = count_base.where(acl_filter)

    if scope_kind is not None:
        base = base.where(MemoryObservation.scope_kind == scope_kind)
        count_base = count_base.where(MemoryObservation.scope_kind == scope_kind)
    if scope_id is not None:
        base = base.where(MemoryObservation.scope_id == scope_id)
        count_base = count_base.where(MemoryObservation.scope_id == scope_id)

    total_result = await session.execute(count_base)
    total: int = total_result.scalar_one()

    stmt = base.order_by(MemoryObservation.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    rows = [
        {
            "id": r.id,
            "scope_kind": r.scope_kind,
            "scope_id": r.scope_id,
            "content_preview": r.content[:_PREVIEW_LEN],
            "superseded_by": r.superseded_by,
            "created_at": r.created_at.isoformat(),
        }
        for r in result.scalars()
    ]
    return rows, total


async def get_observation_detail(
    session: AsyncSession,
    observation_id: int,
    *,
    allowance: "ScopeAllowance | None" = None,
) -> dict[str, Any] | None:
    """Full observation row plus evidence chain with source previews.

    M3: When *allowance* is provided, returns None if the observation is outside
    the caller's permitted scopes.  The caller should translate None → 404
    (not 403; we do NOT reveal existence of out-of-scope observations).
    """
    obs_result = await session.execute(
        select(MemoryObservation).where(MemoryObservation.id == observation_id)
    )
    obs = obs_result.scalar_one_or_none()
    if obs is None:
        return None

    # M3 access check — 404 if outside allowance (don't reveal existence).
    if allowance is not None and not allowance.permits(obs.scope_kind, obs.scope_id):
        return None

    # Fetch evidence rows
    ev_result = await session.execute(
        select(MemoryEvidence)
        .where(MemoryEvidence.observation_id == observation_id)
        .order_by(MemoryEvidence.weight.desc())
    )
    evidence_rows = ev_result.scalars().all()

    evidence: list[dict[str, Any]] = []
    for ev in evidence_rows:
        item: dict[str, Any] = {
            "id": ev.id,
            "source_kind": ev.source_kind,
            "source_id": ev.source_id,
            "weight": ev.weight,
            "source_preview": None,
            "created_at": ev.created_at.isoformat(),
        }
        # Best-effort source preview
        if ev.source_quote:
            item["source_preview"] = ev.source_quote[:_PREVIEW_LEN]
        elif ev.source_kind == "drawer":
            # CC28: ev.source_id is TEXT; numeric IDs need int() for BigInt PK lookup
            try:
                dr_result = await session.execute(
                    select(MemoryDrawer).where(MemoryDrawer.id == int(ev.source_id))
                )
                dr = dr_result.scalar_one_or_none()
                if dr is not None:
                    item["source_preview"] = dr.content[:_PREVIEW_LEN]
            except (ValueError, TypeError):
                pass  # non-numeric source_id; no preview available
        elif ev.source_kind == "observation":
            try:
                src_obs_result = await session.execute(
                    select(MemoryObservation).where(MemoryObservation.id == int(ev.source_id))
                )
                src_obs = src_obs_result.scalar_one_or_none()
                if src_obs is not None:
                    item["source_preview"] = src_obs.content[:_PREVIEW_LEN]
            except (ValueError, TypeError):
                pass  # non-numeric source_id; no preview available
        evidence.append(item)

    # The stored memory_observations.evidence_count column is only bumped by
    # the consolidator's corroboration path; raw link_evidence calls leave it
    # at the server default (1). Recompute from the actual evidence rows here
    # so the detail endpoint never reports fewer links than it actually shows.
    obs_payload = Observation.model_validate(obs).model_dump(mode="json")
    obs_payload["evidence_count"] = len(evidence_rows)

    return {
        "observation": obs_payload,
        "evidence": evidence,
    }


async def list_scopes(
    session: AsyncSession,
    *,
    allowance: "ScopeAllowance | None" = None,
) -> list[dict[str, Any]]:
    """Distinct scopes with drawer_count and observation_count.

    M3: When *allowance* is provided, only scopes the caller is permitted to
    see are returned.  Denied callers receive an empty list.
    """
    # Get drawer counts per (scope_kind, scope_id)
    dr_stmt = select(
        MemoryDrawer.scope_kind,
        MemoryDrawer.scope_id,
        func.count().label("drawer_count"),
    )
    # Apply allowance filter at SQL level for drawers
    acl_clauses_dr = _allowance_clauses(allowance, MemoryDrawer)
    if acl_clauses_dr:
        dr_stmt = dr_stmt.where(or_(*acl_clauses_dr))
    dr_stmt = dr_stmt.group_by(MemoryDrawer.scope_kind, MemoryDrawer.scope_id)
    dr_result = await session.execute(dr_stmt)
    drawer_counts: dict[tuple[str, str], int] = {
        (r.scope_kind, r.scope_id): r.drawer_count for r in dr_result
    }

    # Get observation counts per (scope_kind, scope_id)
    obs_stmt = select(
        MemoryObservation.scope_kind,
        MemoryObservation.scope_id,
        func.count().label("observation_count"),
    )
    # Apply allowance filter at SQL level for observations
    acl_clauses_obs = _allowance_clauses(allowance, MemoryObservation)
    if acl_clauses_obs:
        obs_stmt = obs_stmt.where(or_(*acl_clauses_obs))
    obs_stmt = obs_stmt.group_by(MemoryObservation.scope_kind, MemoryObservation.scope_id)
    obs_result = await session.execute(obs_stmt)
    obs_counts: dict[tuple[str, str], int] = {
        (r.scope_kind, r.scope_id): r.observation_count for r in obs_result
    }

    # Union of all scope keys
    all_keys = set(drawer_counts.keys()) | set(obs_counts.keys())
    rows = [
        {
            "scope_kind": sk,
            "scope_id": sid,
            "drawer_count": drawer_counts.get((sk, sid), 0),
            "observation_count": obs_counts.get((sk, sid), 0),
        }
        for sk, sid in sorted(all_keys)
    ]
    return rows


async def get_memory_stats(
    session: AsyncSession,
    allowance: "ScopeAllowance | None" = None,
) -> dict[str, Any]:
    """Overall memory counts for the dashboard header, optionally scoped.

    When *allowance* is provided, all aggregates are filtered to the scopes
    the caller may read (M3).  This prevents non-owner users from inferring
    the existence of personal:* or agent:artemis data via count leakage.
    """
    drawer_base = select(func.count()).select_from(MemoryDrawer)
    obs_base = select(func.count()).select_from(MemoryObservation)

    # Apply allowance filtering
    drawer_acl = _allowance_clauses(allowance, MemoryDrawer)
    obs_acl = _allowance_clauses(allowance, MemoryObservation)
    if drawer_acl:
        from sqlalchemy import or_ as _or_
        drawer_base = drawer_base.where(_or_(*drawer_acl) if len(drawer_acl) > 1 else drawer_acl[0])
    if obs_acl:
        from sqlalchemy import or_ as _or_
        obs_base = obs_base.where(_or_(*obs_acl) if len(obs_acl) > 1 else obs_acl[0])

    total_drawers: int = (await session.execute(drawer_base)).scalar_one()
    total_observations: int = (await session.execute(obs_base)).scalar_one()
    total_evidence: int = (
        await session.execute(select(func.count()).select_from(MemoryEvidence))
    ).scalar_one()

    # Distinct scope count — apply allowance on both sub-selects
    obs_scope_q = select(
        MemoryObservation.scope_kind,
        MemoryObservation.scope_id,
    )
    drawer_scope_q = select(
        MemoryDrawer.scope_kind,
        MemoryDrawer.scope_id,
    )
    if obs_acl:
        from sqlalchemy import or_ as _or_
        obs_scope_q = obs_scope_q.where(_or_(*obs_acl) if len(obs_acl) > 1 else obs_acl[0])
    if drawer_acl:
        from sqlalchemy import or_ as _or_
        drawer_scope_q = drawer_scope_q.where(_or_(*drawer_acl) if len(drawer_acl) > 1 else drawer_acl[0])
    scope_count_result = await session.execute(
        select(func.count()).select_from(obs_scope_q.union(drawer_scope_q).subquery())
    )
    scope_count: int = scope_count_result.scalar_one()

    # Breakdown by scope_kind (observations only — more meaningful than drawers)
    by_kind_q = select(
        MemoryObservation.scope_kind,
        func.count().label("cnt"),
    ).group_by(MemoryObservation.scope_kind)
    if obs_acl:
        from sqlalchemy import or_ as _or_
        by_kind_q = by_kind_q.where(_or_(*obs_acl) if len(obs_acl) > 1 else obs_acl[0])
    by_kind_result = await session.execute(by_kind_q)
    by_scope_kind: dict[str, int] = {r.scope_kind: r.cnt for r in by_kind_result}

    return {
        "total_drawers": total_drawers,
        "total_observations": total_observations,
        "total_evidence_links": total_evidence,
        "scope_count": scope_count,
        "by_scope_kind": by_scope_kind,
    }
