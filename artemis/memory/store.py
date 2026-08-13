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
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.maintenance import KNOWN_CATEGORIES
from artemis.memory.models import (
    MemoryDrawer,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryObservation,
    MemoryObservationScope,
    MemoryScope,
)
from artemis.memory.schemas import (
    Drawer,
    Evidence,
    EvidenceSourceKind,
    Observation,
    Scope,
    Source,
)

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
    raw_payload: dict[str, Any] | None = None,
    raw_source_kind: str = "agent_observation",
    raw_source_id: str | None = None,
    raw_actor: str | None = None,
    additional_scopes: list[Scope] | None = None,
    wing: Literal["working", "durable"] = "durable",
    confidence_origin: str | None = None,
) -> Observation:
    """Write an observation, deduplicating by content hash within the scope.

    Idempotent: if an observation with identical content already exists in this
    scope, the existing observation is returned without modification.

    When raw_payload is provided, a raw_inputs row is written first and the
    resulting raw_input_id is stored on the observation. This is the preferred
    call path — callers that pass raw_payload participate in the M1 lossless
    invariant. Callers that omit it still work (backward compat) but their
    observations have no verbatim source record.

    MW1 additions:
    - additional_scopes: extra scopes written to memory_observation_scopes with
      is_primary=FALSE. The primary scope goes to both the legacy columns and
      the join table with is_primary=TRUE.
    - wing: 'durable' (default) or 'working'. Stored on the observation row.
    - confidence_origin: free-text source label for auditability.

    Raises ValueError if additional_scopes contains the primary scope (which
    would violate the is_primary invariant).

    Embeds content in the same transaction (best-effort; failure is logged and
    the row is queued for backfill — it never blocks the write).
    """
    from artemis.memory.raw_inputs import insert_raw_input

    # Validate category against known-good set from maintenance decay table.
    # Unknown categories are accepted (write remains lossless) but logged so
    # typos and non-standard values surface immediately.
    if category not in KNOWN_CATEGORIES:
        _logger.warning(
            "Observation written with unknown category %r (known: %s). "
            "This will decay at the default 0.95 factor. Did you mean one of the known categories?",
            category,
            sorted(KNOWN_CATEGORIES),
        )

    # Validate: no duplicate of primary scope in additional_scopes
    if additional_scopes:
        primary_key = (scope.scope_kind, scope.scope_id)
        for extra in additional_scopes:
            if (extra.scope_kind, extra.scope_id) == primary_key:
                raise ValueError(
                    f"additional_scopes must not contain the primary scope "
                    f"({scope.scope_kind}:{scope.scope_id})"
                )

    raw_input_id: int | None = None
    if raw_payload is not None:
        raw_row = await insert_raw_input(
            session,
            source_kind=raw_source_kind,
            source_id=raw_source_id,
            actor=raw_actor,
            scope_kind=scope.scope_kind,
            scope_id=scope.scope_id,
            payload=raw_payload,
        )
        raw_input_id = raw_row.id

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
            raw_input_id=raw_input_id,
            wing=wing,
            confidence_origin=confidence_origin,
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

    # Content-hash dedup returned an existing row — and it may be a SUPERSEDED
    # one, which is a data-loss trap that bit three separate times on 2026-08-13.
    #
    # The caller is asserting this content as true right now. Handing back a row
    # that has been retired means the caller's dimension/drawer ends up pointing
    # at a dead end and disappears from retrieval entirely — worse than either
    # keeping the old value or writing a new one, and completely silent.
    #
    # How it happens in practice: Argus's synthesis prompt hard-codes the exact
    # string "Insufficient data from available sources.", so any dimension whose
    # honest answer stays insufficient across a same-day re-research run produces
    # a byte-identical hash and collides with a row that was already superseded
    # by an earlier attempt.
    #
    # Re-asserted content is current content, so reactivate it.
    if row.superseded_by is not None:
        _logger.info(
            "write_observation: reactivating superseded observation %s -- its content "
            "was just re-asserted (content-hash dedup matched a retired row)",
            row.id,
        )
        row.superseded_by = None
        await session.flush()

    obs = Observation.model_validate(row)
    await _embed_and_store(session, "observation", obs.id, content, embedding_provider)

    # MW1: write primary scope to join table
    await add_observation_scope(
        session,
        observation_id=obs.id,
        scope_kind=scope.scope_kind,
        scope_id=scope.scope_id,
        weight=1.0,
        is_primary=True,
    )

    # MW1: write secondary scopes to join table
    if additional_scopes:
        for extra_scope in additional_scopes:
            await _ensure_scope(session, extra_scope)
            await add_observation_scope(
                session,
                observation_id=obs.id,
                scope_kind=extra_scope.scope_kind,
                scope_id=extra_scope.scope_id,
                weight=1.0,
                is_primary=False,
            )

    return obs


async def add_observation_scope(
    session: AsyncSession,
    observation_id: int,
    scope_kind: str,
    scope_id: str,
    weight: float = 1.0,
    is_primary: bool = False,
) -> None:
    """Add a scope entry to the join table for an observation.

    Idempotent: INSERT … ON CONFLICT DO NOTHING. If the (observation_id,
    scope_kind, scope_id) row already exists it is left unchanged.
    """
    stmt = (
        pg_insert(MemoryObservationScope)
        .values(
            observation_id=observation_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            weight=weight,
            is_primary=is_primary,
        )
        .on_conflict_do_nothing()
    )
    await session.execute(stmt)


async def list_scopes_for_observation(
    session: AsyncSession,
    observation_id: int,
) -> list[tuple[str, str, float, bool]]:
    """Return all scopes for an observation as (scope_kind, scope_id, weight, is_primary)."""
    result = await session.execute(
        select(
            MemoryObservationScope.scope_kind,
            MemoryObservationScope.scope_id,
            MemoryObservationScope.weight,
            MemoryObservationScope.is_primary,
        ).where(MemoryObservationScope.observation_id == observation_id)
    )
    return [(row[0], row[1], row[2], row[3]) for row in result.all()]


async def list_observations_for_scope(
    session: AsyncSession,
    scope_kind: str,
    scope_id: str,
    *,
    is_primary: bool | None = None,
) -> list[MemoryObservation]:
    """Return observations belonging to a scope via the join table.

    is_primary filter is optional: None returns all (primary + secondary),
    True returns only primary-scope observations, False only secondary.
    """
    stmt = (
        select(MemoryObservation)
        .join(
            MemoryObservationScope,
            MemoryObservationScope.observation_id == MemoryObservation.id,
        )
        .where(
            MemoryObservationScope.scope_kind == scope_kind,
            MemoryObservationScope.scope_id == scope_id,
        )
    )
    if is_primary is not None:
        stmt = stmt.where(MemoryObservationScope.is_primary == is_primary)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_or_create_scope(
    session: AsyncSession,
    scope_kind: str,
    scope_id: str,
) -> MemoryScope:
    """Fetch or create a memory_scopes row for (scope_kind, scope_id).

    Idempotent: concurrent callers both executing the upsert are safe because
    the underlying INSERT … ON CONFLICT DO NOTHING ignores duplicates. The
    SELECT after the upsert always returns the canonical row.

    M1 uses this to ensure the `agent` scope row exists for each unique
    agent_id before writing an observation.
    """
    stmt = (
        pg_insert(MemoryScope)
        .values(scope_kind=scope_kind, scope_id=scope_id)
        .on_conflict_do_nothing(index_elements=["scope_kind", "scope_id"])
    )
    await session.execute(stmt)
    result = await session.execute(
        select(MemoryScope).where(
            MemoryScope.scope_kind == scope_kind,
            MemoryScope.scope_id == scope_id,
        )
    )
    return result.scalar_one()


async def link_evidence(
    session: AsyncSession,
    observation_id: int,
    source_kind: EvidenceSourceKind,
    source_id: str,
    source_quote: str | None = None,
    weight: float = 1.0,
) -> Evidence:
    """Link a drawer or observation as evidence for an observation.

    Idempotent: if the (observation_id, source_kind, source_id) triple already
    exists, the existing evidence record is returned unchanged.

    source_kind is constrained to EvidenceSourceKind — the union of every
    upstream provenance the memory keystone recognizes (drawer, observation,
    agent_run, signal_queue, definition_proposal, pipeline_run, skill,
    floating_artemis_messages, meeting). Existing callers pass matching
    string literals; mypy will flag drift on any new kind.

    source_id is now TEXT (CC28). Pass string representations of numeric IDs
    (e.g. str(signal_id)) or raw non-numeric identifiers (skill slugs, UUIDs).
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

    Refuses ``old_id == new_id``. Self-supersession is always a caller bug, and
    it is a silent and nasty one: the row stays in the table, reads perfectly
    normal, and simply stops being retrieved — the observation is effectively
    deleted by a function whose entire purpose is that we never delete. Hit on
    2026-08-13 writing a correction into Callie's memory, where
    ``write_observation`` had deduped on content hash and returned the SAME row
    the caller was trying to supersede, so ``old_id`` and ``new_id`` were equal
    and the correction removed itself.
    """
    if old_id == new_id:
        raise ValueError(
            f"refusing to supersede observation {old_id} with itself: that would "
            "remove it from retrieval entirely. Note write_observation dedupes on "
            "content hash and returns the EXISTING row for identical content, "
            "which is the usual way callers end up here."
        )
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
