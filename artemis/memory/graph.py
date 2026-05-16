"""Graph layer DB helpers for memory_entities, memory_relations, and friends.

Public API:
    upsert_entity        — create or update an entity, returns EntityRead
    record_alias         — add an alias surface form, idempotent
    record_mention       — link entity to an observation/drawer, idempotent
    upsert_relation      — create or update a directed entity relation
    list_entities_for_scope  — list entities in a scope, optionally by kind
    get_entity_neighborhood  — entity + relations within N hops
    find_entities_in_text    — text-scan for entity name/alias slugs

Predicate vocabulary:
    VALID_PREDICATES — server-side allowlist. upsert_relation rejects any
    predicate outside this set, logging the attempt to memory_relation_rejections.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import (
    MemoryEntity,
    MemoryEntityAlias,
    MemoryEntityMention,
    MemoryRelation,
    MemoryRelationRejection,
)
from artemis.memory.schemas import EntityNeighborhood, EntityRead, RelationRead, Scope

_logger = logging.getLogger(__name__)

# ── Vocabulary ────────────────────────────────────────────────────────────────

VALID_ENTITY_KINDS: frozenset[str] = frozenset(
    ["person", "project", "brand", "campaign", "post", "channel", "other"]
)

VALID_PREDICATES: frozenset[str] = frozenset(
    [
        "works_on",
        "owns",
        "publishes_to",
        "belongs_to",
        "posted_on",
        "runs_campaign",
        "authored_by",
        "mentioned_with",
        "related_to",
    ]
)

# ── Slug helper ───────────────────────────────────────────────────────────────


def _to_slug(text_: str) -> str:
    """Normalize text to a lowercase underscore slug for slug-based lookups."""
    return re.sub(r"[^a-z0-9]+", "_", text_.lower()).strip("_")


# ── Entity helpers ────────────────────────────────────────────────────────────


async def upsert_entity(
    session: AsyncSession,
    *,
    kind: str,
    name: str,
    scope_kind: str,
    scope_id: str,
    confidence: float = 0.9,
    attributes: dict[str, Any] | None = None,
) -> EntityRead:
    """Create or bump a named entity.

    If (scope_kind, scope_id, entity_kind, name_slug) already exists, increments
    mention_count and updates last_seen_at. Returns the persisted row.

    Raises ValueError for unknown entity_kind values.
    """
    if kind not in VALID_ENTITY_KINDS:
        raise ValueError(f"Unknown entity_kind {kind!r}. Valid: {sorted(VALID_ENTITY_KINDS)}")
    slug = _to_slug(name)
    stmt = (
        pg_insert(MemoryEntity)
        .values(
            entity_kind=kind,
            canonical_name=name,
            name_slug=slug,
            scope_kind=scope_kind,
            scope_id=scope_id,
            attributes=attributes,
            confidence=confidence,
        )
        .on_conflict_do_update(
            constraint="uq_entities_scope_kind_slug",
            set_={
                "mention_count": MemoryEntity.mention_count + 1,
                "last_seen_at": text("now()"),
                "confidence": confidence,
            },
        )
    )
    await session.execute(stmt)
    result = await session.execute(
        select(MemoryEntity).where(
            MemoryEntity.scope_kind == scope_kind,
            MemoryEntity.scope_id == scope_id,
            MemoryEntity.entity_kind == kind,
            MemoryEntity.name_slug == slug,
        )
    )
    row = result.scalar_one()
    return EntityRead.model_validate(row)


async def record_alias(
    session: AsyncSession,
    entity_id: int,
    alias: str,
) -> None:
    """Add an alias surface form for an entity. Silently no-ops on duplicate."""
    if not alias:
        return
    slug = _to_slug(alias)
    stmt = (
        pg_insert(MemoryEntityAlias)
        .values(entity_id=entity_id, alias=alias, alias_slug=slug)
        .on_conflict_do_nothing(constraint="uq_aliases_entity_slug")
    )
    await session.execute(stmt)


async def record_mention(
    session: AsyncSession,
    *,
    entity_id: int,
    source_kind: str,
    source_id: int,
    mention_quote: str | None = None,
    weight: float = 1.0,
) -> None:
    """Link an entity to the observation/drawer that mentioned it. Idempotent."""
    stmt = (
        pg_insert(MemoryEntityMention)
        .values(
            entity_id=entity_id,
            source_kind=source_kind,
            source_id=source_id,
            mention_quote=mention_quote,
            weight=weight,
        )
        .on_conflict_do_nothing(constraint="uq_mentions_entity_source")
    )
    await session.execute(stmt)


# ── Relation helpers ──────────────────────────────────────────────────────────


async def upsert_relation(
    session: AsyncSession,
    *,
    subject_id: int,
    predicate: str,
    object_id: int,
    evidence_observation_id: int | None = None,
    confidence: float = 0.9,
) -> RelationRead | None:
    """Insert or update a directed relation. Returns None if predicate is rejected.

    Unknown predicates are logged to memory_relation_rejections and None is returned.
    Valid relations update last_seen_at and confidence on conflict.
    """
    if predicate not in VALID_PREDICATES:
        _logger.warning(
            "Rejected unknown predicate %r for (%s → %s)", predicate, subject_id, object_id
        )
        rejection = MemoryRelationRejection(
            subject_id=subject_id, predicate=predicate, object_id=object_id
        )
        session.add(rejection)
        await session.flush()
        return None

    stmt = (
        pg_insert(MemoryRelation)
        .values(
            subject_id=subject_id,
            predicate=predicate,
            object_id=object_id,
            evidence_observation_id=evidence_observation_id,
            confidence=confidence,
        )
        .on_conflict_do_update(
            constraint="uq_relations_triple",
            set_={
                "last_seen_at": text("now()"),
                "confidence": confidence,
                "evidence_observation_id": evidence_observation_id,
            },
        )
    )
    await session.execute(stmt)
    result = await session.execute(
        select(MemoryRelation).where(
            MemoryRelation.subject_id == subject_id,
            MemoryRelation.predicate == predicate,
            MemoryRelation.object_id == object_id,
        )
    )
    row = result.scalar_one()
    return RelationRead.model_validate(row)


# ── Query helpers ─────────────────────────────────────────────────────────────


async def list_entities_for_scope(
    session: AsyncSession,
    scope_kind: str,
    scope_id: str,
    *,
    kind: str | None = None,
    limit: int = 50,
) -> list[EntityRead]:
    """List entities in a scope, ordered by mention_count descending."""
    query = select(MemoryEntity).where(
        MemoryEntity.scope_kind == scope_kind,
        MemoryEntity.scope_id == scope_id,
        MemoryEntity.superseded_by.is_(None),
    )
    if kind is not None:
        query = query.where(MemoryEntity.entity_kind == kind)
    query = query.order_by(
        MemoryEntity.mention_count.desc(), MemoryEntity.last_seen_at.desc()
    ).limit(limit)
    result = await session.execute(query)
    return [EntityRead.model_validate(row) for row in result.scalars()]


async def get_entity_neighborhood(
    session: AsyncSession,
    entity_id: int,
    hops: int = 1,
) -> EntityNeighborhood | None:
    """Return an entity and all its relations within `hops` hops.

    Uses a simple BFS expansion. hop cap defaults to 1; max supported is 2.
    Relations are deduplicated by id.
    """
    hops = max(1, min(hops, 2))

    result = await session.execute(select(MemoryEntity).where(MemoryEntity.id == entity_id))
    entity_row = result.scalar_one_or_none()
    if entity_row is None:
        return None

    entity = EntityRead.model_validate(entity_row)
    visited: set[int] = {entity_id}
    frontier: list[int] = [entity_id]
    all_relations: dict[int, RelationRead] = {}

    for _ in range(hops):
        if not frontier:
            break
        next_frontier: list[int] = []
        rel_sql = text("""
            SELECT r.id, r.subject_id, r.predicate, r.object_id,
                   r.evidence_observation_id, r.weight, r.confidence,
                   r.first_seen_at, r.last_seen_at, r.superseded_by,
                   es.canonical_name AS subject_name, es.entity_kind AS subject_kind,
                   eo.canonical_name AS object_name, eo.entity_kind AS object_kind
            FROM memory_relations r
            JOIN memory_entities es ON es.id = r.subject_id
            JOIN memory_entities eo ON eo.id = r.object_id
            WHERE (r.subject_id = ANY(:ids) OR r.object_id = ANY(:ids))
              AND r.superseded_by IS NULL
        """)
        rows = await session.execute(rel_sql, {"ids": list(frontier)})
        for row in rows.mappings():
            rel_id = int(row["id"])
            if rel_id not in all_relations:
                all_relations[rel_id] = RelationRead(
                    id=rel_id,
                    subject_id=int(row["subject_id"]),
                    predicate=str(row["predicate"]),
                    object_id=int(row["object_id"]),
                    evidence_observation_id=row["evidence_observation_id"],
                    weight=float(row["weight"]),
                    confidence=float(row["confidence"]),
                    first_seen_at=row["first_seen_at"],
                    last_seen_at=row["last_seen_at"],
                    superseded_by=row["superseded_by"],
                    subject_name=str(row["subject_name"]),
                    subject_kind=str(row["subject_kind"]),
                    object_name=str(row["object_name"]),
                    object_kind=str(row["object_kind"]),
                )
            # Expand frontier with the other end of the relation
            for neighbor_id in (int(row["subject_id"]), int(row["object_id"])):
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    next_frontier.append(neighbor_id)
        frontier = next_frontier

    return EntityNeighborhood(entity=entity, relations=list(all_relations.values()))


async def find_entities_in_text(
    session: AsyncSession,
    scope_set: list[Scope],
    query_text: str,
    min_token_length: int = 3,
) -> list[EntityRead]:
    """Scan query_text for tokens that match entity name_slug or alias_slug.

    Tokenizes by splitting on non-alphanumeric characters, then looks up each
    slug against memory_entities.name_slug and memory_entity_aliases.alias_slug.
    Returns at most one row per entity (deduped by id).
    """
    if not scope_set or not query_text.strip():
        return []

    tokens = re.split(r"[^a-z0-9]+", query_text.lower())
    slugs = list({t for t in tokens if len(t) >= min_token_length})
    if not slugs:
        return []

    # Build scope IN clause
    scope_pairs: list[str] = []
    params: dict[str, str] = {}
    for i, s in enumerate(scope_set):
        params[f"_sk_{i}"] = s.scope_kind
        params[f"_si_{i}"] = s.scope_id
        scope_pairs.append(f"(:_sk_{i}, :_si_{i})")
    scope_in = f"({', '.join(scope_pairs)})"

    params["_slugs"] = slugs  # type: ignore[assignment]

    sql = text(f"""
        SELECT DISTINCT e.id, e.entity_kind, e.canonical_name, e.name_slug,
               e.scope_kind, e.scope_id, e.attributes,
               e.first_seen_at, e.last_seen_at, e.mention_count,
               e.confidence, e.superseded_by
        FROM memory_entities e
        WHERE (e.scope_kind, e.scope_id) IN {scope_in}
          AND e.superseded_by IS NULL
          AND e.name_slug = ANY(:_slugs)
        UNION
        SELECT DISTINCT e.id, e.entity_kind, e.canonical_name, e.name_slug,
               e.scope_kind, e.scope_id, e.attributes,
               e.first_seen_at, e.last_seen_at, e.mention_count,
               e.confidence, e.superseded_by
        FROM memory_entities e
        JOIN memory_entity_aliases a ON a.entity_id = e.id
        WHERE (e.scope_kind, e.scope_id) IN {scope_in}
          AND e.superseded_by IS NULL
          AND a.alias_slug = ANY(:_slugs)
    """)
    result = await session.execute(sql, params)
    entities: dict[int, EntityRead] = {}
    for row in result.mappings():
        eid = int(row["id"])
        if eid not in entities:
            entities[eid] = EntityRead(
                id=eid,
                entity_kind=str(row["entity_kind"]),
                canonical_name=str(row["canonical_name"]),
                name_slug=str(row["name_slug"]),
                scope_kind=str(row["scope_kind"]),
                scope_id=str(row["scope_id"]),
                attributes=row["attributes"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
                mention_count=int(row["mention_count"]),
                confidence=float(row["confidence"]),
                superseded_by=row["superseded_by"],
            )
    return list(entities.values())


async def get_observation_ids_for_entities(
    session: AsyncSession,
    entity_ids: list[int],
) -> dict[int, float]:
    """Return {observation_id: max_weight} for entity mentions of type 'observation'."""
    if not entity_ids:
        return {}
    sql = text("""
        SELECT source_id, MAX(weight) AS weight
        FROM memory_entity_mentions
        WHERE source_kind = 'observation'
          AND entity_id = ANY(:ids)
        GROUP BY source_id
    """)
    result = await session.execute(sql, {"ids": entity_ids})
    return {int(row.source_id): float(row.weight) for row in result}


async def get_neighbor_entity_ids(
    session: AsyncSession,
    entity_ids: list[int],
) -> list[int]:
    """Return entity ids reachable in 1 hop from entity_ids (excluding originals)."""
    if not entity_ids:
        return []
    sql = text("""
        SELECT DISTINCT
            CASE WHEN subject_id = ANY(:ids) THEN object_id
                 ELSE subject_id END AS neighbor_id
        FROM memory_relations
        WHERE (subject_id = ANY(:ids) OR object_id = ANY(:ids))
          AND superseded_by IS NULL
    """)
    result = await session.execute(sql, {"ids": entity_ids})
    origin_set = set(entity_ids)
    return [int(row.neighbor_id) for row in result if int(row.neighbor_id) not in origin_set]
