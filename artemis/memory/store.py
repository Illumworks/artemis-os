"""Memory keystone write + read API.

LOSSLESS RULE (load-bearing):
  Drawers and observations are NEVER deleted from the database.
  Observations are removed from active retrieval only by setting
  superseded_by — not by DELETE. There is no public delete_drawer
  or delete_observation function. Any code that calls DELETE on these
  tables without an explicit architectural justification is a bug.

All functions accept an AsyncSession. Transaction management is the
caller's responsibility:

    async with session.begin():
        drawer = await write_drawer(session, scope, content, source)
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import (
    MemoryDrawer,
    MemoryEvidence,
    MemoryObservation,
    MemoryScope,
)
from artemis.memory.schemas import Drawer, Evidence, Observation, Scope, Source


# ── Internals ────────────────────────────────────────────────────────────────


def _content_hash(scope_kind: str, scope_id: str, content: str) -> str:
    """sha256(scope_kind:scope_id:content) — same formula as the Node reference."""
    raw = f"{scope_kind}:{scope_id}:{content}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def _ensure_scope(session: AsyncSession, scope: Scope) -> None:
    """Upsert scope catalog entry. No-op if already present."""
    stmt = (
        pg_insert(MemoryScope)
        .values(scope_kind=scope.scope_kind, scope_id=scope.scope_id)
        .on_conflict_do_nothing(index_elements=["scope_kind", "scope_id"])
    )
    await session.execute(stmt)


# ── Public write API ─────────────────────────────────────────────────────────


async def write_drawer(
    session: AsyncSession,
    scope: Scope,
    content: str,
    source: Source,
    corpus_kind: str | None = None,
    owner_user_id: int | None = None,
) -> Drawer:
    """Write a drawer, deduplicating by content hash within the scope.

    Idempotent: if a drawer with identical content already exists in this scope,
    the existing drawer is returned without modification.
    """
    content_hash = _content_hash(scope.scope_kind, scope.scope_id, content)
    await _ensure_scope(session, scope)
    stmt = (
        pg_insert(MemoryDrawer)
        .values(
            scope_kind=scope.scope_kind,
            scope_id=scope.scope_id,
            corpus_kind=corpus_kind,
            content=content,
            content_hash=content_hash,
            source_kind=source.source_kind,
            source_id=source.source_id,
            source_extra=source.source_extra,
            owner_user_id=owner_user_id,
        )
        .on_conflict_do_nothing(constraint="uq_drawers_scope_hash")
    )
    await session.execute(stmt)
    result = await session.execute(
        select(MemoryDrawer).where(
            MemoryDrawer.scope_kind == scope.scope_kind,
            MemoryDrawer.scope_id == scope.scope_id,
            MemoryDrawer.content_hash == content_hash,
        )
    )
    row = result.scalar_one()
    return Drawer.model_validate(row)


async def write_observation(
    session: AsyncSession,
    scope: Scope,
    content: str,
    category: str = "discovery",
    source_quality: float = 0.5,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    owner_user_id: int | None = None,
) -> Observation:
    """Write an observation, deduplicating by content hash within the scope.

    Idempotent: if an observation with identical content already exists in this
    scope, the existing observation is returned without modification.
    """
    content_hash = _content_hash(scope.scope_kind, scope.scope_id, content)
    await _ensure_scope(session, scope)
    stmt = (
        pg_insert(MemoryObservation)
        .values(
            scope_kind=scope.scope_kind,
            scope_id=scope.scope_id,
            category=category,
            content=content,
            content_hash=content_hash,
            source_quality=source_quality,
            valid_from=valid_from,
            valid_until=valid_until,
            owner_user_id=owner_user_id,
        )
        .on_conflict_do_nothing(constraint="uq_obs_scope_hash")
    )
    await session.execute(stmt)
    result = await session.execute(
        select(MemoryObservation).where(
            MemoryObservation.scope_kind == scope.scope_kind,
            MemoryObservation.scope_id == scope.scope_id,
            MemoryObservation.content_hash == content_hash,
        )
    )
    row = result.scalar_one()
    return Observation.model_validate(row)


async def link_evidence(
    session: AsyncSession,
    observation_id: int,
    source_kind: Literal["drawer", "observation"],
    source_id: int,
    source_quote: str | None = None,
    weight: float = 1.0,
) -> Evidence:
    """Link a drawer or observation as evidence for an observation.

    Idempotent: if the (observation_id, source_kind, source_id) triple already
    exists, the existing evidence record is returned unchanged.
    """
    stmt = (
        pg_insert(MemoryEvidence)
        .values(
            observation_id=observation_id,
            source_kind=source_kind,
            source_id=source_id,
            source_quote=source_quote,
            weight=weight,
        )
        .on_conflict_do_nothing(constraint="uq_evidence_obs_source")
    )
    await session.execute(stmt)
    result = await session.execute(
        select(MemoryEvidence).where(
            MemoryEvidence.observation_id == observation_id,
            MemoryEvidence.source_kind == source_kind,
            MemoryEvidence.source_id == source_id,
        )
    )
    row = result.scalar_one()
    return Evidence.model_validate(row)


async def supersede_observation(
    session: AsyncSession,
    old_id: int,
    new_id: int,
) -> None:
    """Mark old_id as superseded by new_id.

    Only acts when old_id is not already superseded (mirrors Node's conditional
    UPDATE … WHERE superseded_by IS NULL). The old observation remains in the
    database — it just falls out of active retrieval.
    """
    stmt = (
        update(MemoryObservation)
        .where(
            MemoryObservation.id == old_id,
            MemoryObservation.superseded_by.is_(None),  # type: ignore[attr-defined]
        )
        .values(superseded_by=new_id)
    )
    await session.execute(stmt)


# ── Public read API ──────────────────────────────────────────────────────────


async def get_drawer(session: AsyncSession, drawer_id: int) -> Drawer | None:
    result = await session.execute(
        select(MemoryDrawer).where(MemoryDrawer.id == drawer_id)
    )
    row = result.scalar_one_or_none()
    return Drawer.model_validate(row) if row is not None else None


async def get_observation(
    session: AsyncSession, observation_id: int
) -> Observation | None:
    result = await session.execute(
        select(MemoryObservation).where(MemoryObservation.id == observation_id)
    )
    row = result.scalar_one_or_none()
    return Observation.model_validate(row) if row is not None else None


async def list_evidence_for_observation(
    session: AsyncSession, observation_id: int
) -> list[Evidence]:
    result = await session.execute(
        select(MemoryEvidence)
        .where(MemoryEvidence.observation_id == observation_id)
        .order_by(MemoryEvidence.weight.desc(), MemoryEvidence.id.asc())
    )
    return [Evidence.model_validate(row) for row in result.scalars()]
