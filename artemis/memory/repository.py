"""Memory M2 — Repository functions for conflict management.

LOSSLESS: resolving a conflict does NOT delete observations. It sets
valid_until on the losing observation and supersedes on the winner.
The conflict row is updated with resolution, reason, resolved_at, resolved_by.

Public API:
  list_conflicts_unresolved(session, scope_id=None) -> list[Conflict]
  resolve_conflict(session, conflict_id, resolution, reason, resolver) -> Conflict
  get_observation_history(session, observation_id) -> list[Observation]
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import MemoryConflict, MemoryObservation
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

    result = await session.execute(
        select(MemoryConflict).where(MemoryConflict.id == conflict_id)
    )
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
