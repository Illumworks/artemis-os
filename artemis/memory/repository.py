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
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import (
    MemoryConflict,
    MemoryDrawer,
    MemoryEvidence,
    MemoryObservation,
)
from artemis.memory.schemas import Conflict, Observation

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


async def list_drawers(
    session: AsyncSession,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Paginated list of drawers with content_preview.

    Returns (rows, total_count).
    """
    base = select(MemoryDrawer)
    count_base = select(func.count()).select_from(MemoryDrawer)
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
) -> tuple[list[dict[str, Any]], int]:
    """Paginated list of observations with content_preview.

    Returns (rows, total_count).
    """
    base = select(MemoryObservation)
    count_base = select(func.count()).select_from(MemoryObservation)
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
) -> dict[str, Any] | None:
    """Full observation row plus evidence chain with source previews."""
    obs_result = await session.execute(
        select(MemoryObservation).where(MemoryObservation.id == observation_id)
    )
    obs = obs_result.scalar_one_or_none()
    if obs is None:
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
            dr_result = await session.execute(
                select(MemoryDrawer).where(MemoryDrawer.id == ev.source_id)
            )
            dr = dr_result.scalar_one_or_none()
            if dr is not None:
                item["source_preview"] = dr.content[:_PREVIEW_LEN]
        elif ev.source_kind == "observation":
            src_obs_result = await session.execute(
                select(MemoryObservation).where(MemoryObservation.id == ev.source_id)
            )
            src_obs = src_obs_result.scalar_one_or_none()
            if src_obs is not None:
                item["source_preview"] = src_obs.content[:_PREVIEW_LEN]
        evidence.append(item)

    return {
        "observation": Observation.model_validate(obs).model_dump(mode="json"),
        "evidence": evidence,
    }


async def list_scopes(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Distinct scopes with drawer_count and observation_count."""
    # Get drawer counts per (scope_kind, scope_id)
    dr_stmt = select(
        MemoryDrawer.scope_kind,
        MemoryDrawer.scope_id,
        func.count().label("drawer_count"),
    ).group_by(MemoryDrawer.scope_kind, MemoryDrawer.scope_id)
    dr_result = await session.execute(dr_stmt)
    drawer_counts: dict[tuple[str, str], int] = {
        (r.scope_kind, r.scope_id): r.drawer_count for r in dr_result
    }

    # Get observation counts per (scope_kind, scope_id)
    obs_stmt = select(
        MemoryObservation.scope_kind,
        MemoryObservation.scope_id,
        func.count().label("observation_count"),
    ).group_by(MemoryObservation.scope_kind, MemoryObservation.scope_id)
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
) -> dict[str, Any]:
    """Overall memory counts for the dashboard header."""
    total_drawers: int = (
        await session.execute(select(func.count()).select_from(MemoryDrawer))
    ).scalar_one()
    total_observations: int = (
        await session.execute(select(func.count()).select_from(MemoryObservation))
    ).scalar_one()
    total_evidence: int = (
        await session.execute(select(func.count()).select_from(MemoryEvidence))
    ).scalar_one()

    # Distinct scope count
    scope_count_result = await session.execute(
        select(func.count()).select_from(
            select(
                MemoryObservation.scope_kind,
                MemoryObservation.scope_id,
            )
            .union(
                select(
                    MemoryDrawer.scope_kind,
                    MemoryDrawer.scope_id,
                )
            )
            .subquery()
        )
    )
    scope_count: int = scope_count_result.scalar_one()

    # Breakdown by scope_kind (observations only — more meaningful than drawers)
    by_kind_result = await session.execute(
        select(
            MemoryObservation.scope_kind,
            func.count().label("cnt"),
        ).group_by(MemoryObservation.scope_kind)
    )
    by_scope_kind: dict[str, int] = {r.scope_kind: r.cnt for r in by_kind_result}

    return {
        "total_drawers": total_drawers,
        "total_observations": total_observations,
        "total_evidence_links": total_evidence,
        "scope_count": scope_count,
        "by_scope_kind": by_scope_kind,
    }
