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

Embedding is best-effort: if the provider fails (or is unavailable),
the row is written without an embedding and picked up by backfill later.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import (
    MemoryDrawer,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryObservation,
    MemoryScope,
)
from artemis.memory.schemas import Drawer, Evidence, Observation, Scope, Source

if TYPE_CHECKING:
    from artemis.memory.embeddings import EmbeddingProvider

_logger = logging.getLogger(__name__)


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


async def _embed_and_store(
    session: AsyncSession,
    target_table: Literal["drawer", "observation"],
    target_id: int,
    content: str,
    provider: EmbeddingProvider | None = None,
) -> None:
    """Embed content and persist to memory_embeddings. Best-effort: never raises.

    Uses a SAVEPOINT so a DB failure on the embedding write cannot corrupt the
    outer transaction that wrote the primary row.
    """
    from artemis.memory.embeddings import get_default_provider

    try:
        _provider = provider or get_default_provider()
        vector = await _provider.embed(content)
        async with session.begin_nested():
            await upsert_embedding(
                session, target_table, target_id, _provider.model_version, vector
            )
    except Exception:
        _logger.warning(
            "Embedding failed for %s id=%d; row will be backfilled",
            target_table,
            target_id,
            exc_info=True,
        )


# ── Public write API ─────────────────────────────────────────────────────────


async def upsert_embedding(
    session: AsyncSession,
    target_table: Literal["drawer", "observation"],
    target_id: int,
    model_version: str,
    vector: list[float],
) -> None:
    """Insert or replace the embedding for a drawer or observation.

    Idempotent on (target_table, target_id, model_version). An existing vector
    is overwritten — used by both the write path and the backfill coroutine.
    """
    stmt = (
        pg_insert(MemoryEmbedding)
        .values(
            target_table=target_table,
            target_id=target_id,
            model_version=model_version,
            embedding=vector,
        )
        .on_conflict_do_update(
            constraint="uq_embeddings_target_model",
            set_={"embedding": vector},
        )
    )
    await session.execute(stmt)


async def write_drawer(
    session: AsyncSession,
    scope: Scope,
    content: str,
    source: Source,
    corpus_kind: str | None = None,
    owner_user_id: int | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> Drawer:
    """Write a drawer, deduplicating by content hash within the scope.

    Idempotent: if a drawer with identical content already exists in this scope,
    the existing drawer is returned without modification.

    Embeds content in the same transaction (best-effort; failure is logged and
    the row is queued for backfill — it never blocks the write).
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
    drawer = Drawer.model_validate(row)
    await _embed_and_store(session, "drawer", drawer.id, content, embedding_provider)

    # Notify incremental consolidator (synchronous — never blocks the write)
    try:
        from artemis.memory.incremental_consolidator import get_incremental_consolidator

        get_incremental_consolidator().notify_drawer_written(
            scope, category=corpus_kind or "discovery"
        )
    except Exception:
        _logger.debug("Incremental consolidator notification failed", exc_info=True)

    return drawer


async def write_observation(
    session: AsyncSession,
    scope: Scope,
    content: str,
    category: str = "discovery",
    source_quality: float = 0.5,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    owner_user_id: int | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> Observation:
    """Write an observation, deduplicating by content hash within the scope.

    Idempotent: if an observation with identical content already exists in this
    scope, the existing observation is returned without modification.

    Embeds content in the same transaction (best-effort; failure is logged and
    the row is queued for backfill — it never blocks the write).
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
    obs = Observation.model_validate(row)
    await _embed_and_store(session, "observation", obs.id, content, embedding_provider)
    return obs


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
            MemoryObservation.superseded_by.is_(None),
        )
        .values(superseded_by=new_id)
    )
    await session.execute(stmt)


# ── Public read API ──────────────────────────────────────────────────────────


async def get_drawer(session: AsyncSession, drawer_id: int) -> Drawer | None:
    result = await session.execute(select(MemoryDrawer).where(MemoryDrawer.id == drawer_id))
    row = result.scalar_one_or_none()
    return Drawer.model_validate(row) if row is not None else None


async def get_observation(session: AsyncSession, observation_id: int) -> Observation | None:
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
