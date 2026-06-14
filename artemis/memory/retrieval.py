"""Fusion retrieval for memory observations.

Entry point: search_observations()

Retrieval merges four candidate pools — FTS (tsvector), semantic (pgvector cosine),
recency (exponential decay), score (stored field) — weighted by config/memory-retrieval.yaml.
All filtering (scope, superseded_by, validity windows) is applied at the SQL level.
Fusion scoring is done in Python after fetching the candidate rows.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import artemis.db as _db
from artemis.memory.models import MemoryObservation
from artemis.memory.schemas import Scope, ScoredObservation

if TYPE_CHECKING:
    from artemis.memory.embeddings import EmbeddingProvider

_logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "memory-retrieval.yaml"
_BACKGROUND_USAGE_TASKS: set[asyncio.Task[None]] = set()


# ── Config ───────────────────────────────────────────────────────────────────


class RetrievalWeights(BaseModel):
    fts: float = 0.30
    semantic: float = 0.40
    recency: float = 0.15
    score: float = 0.15
    graph_proximity: float = 0.12  # B4: graph entity expansion channel


class ScoreFeatureWeights(BaseModel):
    """Sub-weights for decomposing the stored `score` channel.

    The four components together determine the contribution of the `score`
    channel in fusion. They do NOT need to sum to 1.0 — each is applied
    independently and the result is multiplied by RetrievalWeights.score.
    """

    relevance: float = 0.40  # obs.score (decayed stored value)
    hits: float = 0.15  # normalized hit_count (min(1, count/10))
    quality: float = 0.35  # source_quality
    confirmed: float = 0.10  # 1.0 if user_confirmed else 0.0


class RetrievalConfig(BaseModel):
    weights: RetrievalWeights = RetrievalWeights()
    score_features: ScoreFeatureWeights = ScoreFeatureWeights()
    top_k: int = 50
    recency_decay_days: float = 30.0


def load_retrieval_config() -> RetrievalConfig:
    """Load config/memory-retrieval.yaml; fall back to defaults if absent."""
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open() as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        return RetrievalConfig(**data)
    return RetrievalConfig()


# Module-level cached config (reloaded only on explicit call).
_cached_config: RetrievalConfig | None = None


def get_retrieval_config() -> RetrievalConfig:
    global _cached_config
    if _cached_config is None:
        _cached_config = load_retrieval_config()
    return _cached_config


# ── Scoring helpers ───────────────────────────────────────────────────────────


def _recency_score(
    created_at: datetime,
    as_of: datetime,
    decay_days: float,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> float:
    """Exponential decay: score = exp(-ln(2) * days / half_life).

    M2: uses valid_from as the "birth" timestamp when set (preferred over created_at).
    An observation that is currently valid (valid_until IS NULL or in the future)
    anchors at valid_from; one that has expired anchors at valid_until so it
    naturally decays toward 0 after expiry.

    At t=0 → 1.0. At t=half_life → 0.5. At t=∞ → 0.
    """
    # M2: pick the best anchor timestamp
    anchor = valid_from if valid_from is not None else created_at
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)

    # If valid_until is in the past, decay from valid_until instead (expired claims decay faster)
    if valid_until is not None:
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=UTC)
        if valid_until < as_of:
            anchor = valid_until

    delta = as_of - anchor
    days = max(delta.total_seconds() / 86400.0, 0.0)
    return math.exp(-math.log(2) * days / max(decay_days, 1.0))


def _composite_score(
    obs_score: float,
    hit_count: int,
    source_quality: float,
    user_confirmed: bool,
    sf: ScoreFeatureWeights,
) -> float:
    """Decompose the stored score channel into its four sub-components."""
    hits_norm = min(1.0, hit_count / 10.0)
    confirmed = 1.0 if user_confirmed else 0.0
    return (
        sf.relevance * max(0.0, obs_score)
        + sf.hits * hits_norm
        + sf.quality * max(0.0, min(1.0, source_quality))
        + sf.confirmed * confirmed
    )


def _compute_final_score(
    fts_rank: float,
    semantic_sim: float,
    recency: float,
    obs_score: float,
    weights: RetrievalWeights,
    *,
    hit_count: int = 0,
    source_quality: float = 0.0,
    user_confirmed: bool = False,
    score_features: ScoreFeatureWeights | None = None,
    graph_proximity: float = 0.0,
    confidence: float = 0.5,
    evidence_count: int = 1,
) -> float:
    """M2: final score is the fusion weighted sum, then multiplied by confidence,
    then boosted by log-scale evidence_count.

    score *= confidence
    score *= 1 + log10(evidence_count)

    A 0.6-confidence observation ranks below a 0.9-confidence one with
    otherwise equal components. Three corroborating sources doesn't 3× the
    score — log10 compresses it — but decisively beats one source.
    """
    sf = score_features or ScoreFeatureWeights()
    score_contrib = _composite_score(obs_score, hit_count, source_quality, user_confirmed, sf)
    base = (
        weights.fts * min(fts_rank, 1.0)
        + weights.semantic * max(0.0, semantic_sim)
        + weights.recency * recency
        + weights.score * score_contrib
        + weights.graph_proximity * max(0.0, min(1.0, graph_proximity))
    )
    # M2 multipliers
    clamped_confidence = max(0.0, min(1.0, confidence))
    evidence_boost = 1.0 + math.log10(max(1, evidence_count))
    return base * clamped_confidence * evidence_boost


# ── SQL helpers ───────────────────────────────────────────────────────────────


def _scope_sql_parts(
    scope_set: list[Scope],
    obs_alias: str = "",
) -> tuple[str, dict[str, str]]:
    """Return (EXISTS subquery fragment, params dict) for scope filtering via join table.

    Filters observations by matching any row in memory_observation_scopes for the
    requested (scope_kind, scope_id) pairs — regardless of is_primary. This supports
    MW1 multi-scope: an observation can be found via its primary scope OR any additional
    scopes it was written with.

    obs_alias is prepended to 'id' when the observations table is aliased, e.g. 'o.'.
    """
    params: dict[str, str] = {}
    tuples: list[str] = []
    for i, s in enumerate(scope_set):
        params[f"_sk_{i}"] = s.scope_kind
        params[f"_si_{i}"] = s.scope_id
        tuples.append(f"(:_sk_{i}, :_si_{i})")
    obs_id_col = f"{obs_alias}id" if obs_alias else "id"
    clause = (
        f"EXISTS ("
        f"SELECT 1 FROM memory_observation_scopes mos "
        f"WHERE mos.observation_id = {obs_id_col} "
        f"AND (mos.scope_kind, mos.scope_id) IN ({', '.join(tuples)})"
        f")"
    )
    return clause, params


def _validity_sql(as_of_param: str = ":_as_of", prefix: str = "") -> str:
    return (
        f"AND ({prefix}valid_from IS NULL OR {prefix}valid_from <= {as_of_param})\n"
        f"    AND ({prefix}valid_until IS NULL OR {prefix}valid_until >= {as_of_param})"
    )


async def _record_observation_usage(
    observation_ids: list[int],
    accessed_at: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Best-effort retrieval feedback write.

    Uses its own session so the hot retrieval path can return immediately and
    remains isolated from any caller-managed transaction.
    """
    if not observation_ids:
        return

    timestamp = accessed_at or datetime.now(UTC)
    factory = session_factory or _db.SessionLocal
    async with factory() as session:
        try:
            await session.execute(
                update(MemoryObservation)
                .where(MemoryObservation.id.in_(observation_ids))
                .values(
                    hit_count=MemoryObservation.hit_count + 1,
                    accessed_at=timestamp,
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
            _logger.warning(
                "Observation usage update failed for %d results",
                len(observation_ids),
                exc_info=True,
            )


def _schedule_observation_usage_update(
    observation_ids: list[int],
    accessed_at: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Launch the usage write without awaiting it.

    A strong reference prevents the task from being garbage-collected before
    completion on long-lived event loops.
    """
    if not observation_ids:
        return
    try:
        task = asyncio.create_task(
            _record_observation_usage(
                observation_ids,
                accessed_at,
                session_factory=session_factory,
            )
        )
    except RuntimeError:
        _logger.debug("Skipping observation usage update: no running event loop")
        return
    _BACKGROUND_USAGE_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_USAGE_TASKS.discard)


# ── Main retrieval function ───────────────────────────────────────────────────


async def search_observations(
    session: AsyncSession,
    scope_set: list[Scope],
    query: str,
    limit: int = 10,
    as_of: datetime | None = None,
    modes: list[Literal["fts", "semantic", "recency", "score", "graph_expand"]] | None = None,
    cfg: RetrievalConfig | None = None,
    provider: EmbeddingProvider | None = None,
    *,
    record_usage: bool = True,
) -> list[ScoredObservation]:
    """Fusion search across active (non-superseded) observations in scope_set.

    Merges FTS, semantic, recency, and score candidate pools weighted by cfg.
    Returns up to `limit` ScoredObservation, sorted by final_score descending.

    Args:
        session: active async session (caller manages transaction if needed)
        scope_set: observations are filtered to these (scope_kind, scope_id) pairs
        query: natural language query string
        limit: max results returned
        as_of: validity window anchor; defaults to now()
        modes: which retrieval channels to use; defaults to all four
        cfg: retrieval weights config; defaults to config/memory-retrieval.yaml
        provider: embedding provider; defaults to get_default_provider()
        record_usage: when False, skip the async hit_count/accessed_at side-effect.
    """
    if not scope_set:
        return []

    cfg = cfg or get_retrieval_config()
    modes = modes or ["fts", "semantic", "recency", "score", "graph_expand"]
    _as_of = as_of or datetime.now(UTC)

    scope_clause, scope_params = _scope_sql_parts(scope_set)
    validity = _validity_sql()
    base_params: dict[str, Any] = {**scope_params, "_as_of": _as_of, "_k": cfg.top_k}

    candidate_ids: set[int] = set()
    fts_scores: dict[int, float] = {}
    semantic_scores: dict[int, float] = {}
    graph_scores: dict[int, float] = {}  # obs_id → graph_proximity (0.0/0.5/1.0)

    # ── FTS candidates ────────────────────────────────────────────────────────
    if "fts" in modes and query.strip():
        fts_sql = text(f"""
            SELECT id, ts_rank(content_fts, plainto_tsquery('english', :_query)) AS fts_rank
            FROM memory_observations
            WHERE {scope_clause}
              AND superseded_by IS NULL
              AND content_fts @@ plainto_tsquery('english', :_query)
              {validity}
            ORDER BY fts_rank DESC
            LIMIT :_k
        """)
        try:
            result = await session.execute(fts_sql, {**base_params, "_query": query})
            for row in result:
                candidate_ids.add(int(row.id))
                fts_scores[int(row.id)] = float(row.fts_rank)
        except Exception:
            _logger.warning("FTS search failed", exc_info=True)

    # ── Semantic candidates ───────────────────────────────────────────────────
    if "semantic" in modes and query.strip():
        from artemis.memory.embeddings import get_default_provider

        _provider = provider or get_default_provider()
        try:
            query_vec = await _provider.embed(query)
            # asyncpg has the pgvector codec registered (see artemis.db) and
            # encodes Python lists/ndarrays straight to vector binary format.
            # Pre-serializing to a "[0.1,0.2,…]" text blob trips the codec
            # ("could not convert string to float") and quietly drops every
            # semantic candidate — pass the list through unchanged instead.
            scope_clause_o, _ = _scope_sql_parts(scope_set, obs_alias="o.")
            sem_sql = text(f"""
                SELECT o.id,
                       1.0 - (me.embedding <=> CAST(:_qvec AS vector)) AS semantic_sim
                FROM memory_observations o
                JOIN memory_embeddings me
                  ON me.target_table = 'observation' AND me.target_id = o.id
                WHERE {scope_clause_o}
                  AND o.superseded_by IS NULL
                  AND me.model_version = :_model_version
                  AND (o.valid_from IS NULL OR o.valid_from <= :_as_of)
                  AND (o.valid_until IS NULL OR o.valid_until >= :_as_of)
                ORDER BY me.embedding <=> CAST(:_qvec AS vector)
                LIMIT :_k
            """)
            sem_params = {
                **base_params,
                "_qvec": query_vec,
                "_model_version": _provider.model_version,
            }
            result = await session.execute(sem_sql, sem_params)
            for row in result:
                candidate_ids.add(int(row.id))
                semantic_scores[int(row.id)] = float(row.semantic_sim)
        except Exception:
            _logger.warning("Semantic search failed", exc_info=True)

    # ── Graph expansion candidates ────────────────────────────────────────────
    if "graph_expand" in modes and query.strip():
        try:
            from artemis.memory.graph import (
                find_entities_in_text,
                get_neighbor_entity_ids,
                get_observation_ids_for_entities,
            )

            direct_entities = await find_entities_in_text(session, scope_set, query)
            if direct_entities:
                direct_ids = [e.id for e in direct_entities]
                direct_obs = await get_observation_ids_for_entities(session, direct_ids)
                for obs_id in direct_obs:
                    candidate_ids.add(obs_id)
                    graph_scores[obs_id] = max(graph_scores.get(obs_id, 0.0), 1.0)

                neighbor_ids = await get_neighbor_entity_ids(session, direct_ids)
                if neighbor_ids:
                    neighbor_obs = await get_observation_ids_for_entities(session, neighbor_ids)
                    for obs_id in neighbor_obs:
                        candidate_ids.add(obs_id)
                        if obs_id not in graph_scores:
                            graph_scores[obs_id] = 0.5
        except Exception:
            _logger.warning("Graph expansion failed", exc_info=True)

    # ── Recency + score candidates (always include for fusion stability) ───────
    if "recency" in modes or "score" in modes:
        recency_sql = text(f"""
            SELECT id FROM memory_observations
            WHERE {scope_clause}
              AND superseded_by IS NULL
              {validity}
            ORDER BY created_at DESC
            LIMIT :_k
        """)
        try:
            result = await session.execute(recency_sql, base_params)
            for row in result:
                candidate_ids.add(int(row.id))
        except Exception:
            _logger.warning("Recency candidate fetch failed", exc_info=True)

    if not candidate_ids:
        return []

    # ── Fetch full rows ───────────────────────────────────────────────────────
    obs_result = await session.execute(
        select(MemoryObservation).where(MemoryObservation.id.in_(list(candidate_ids)))
    )
    observations = {row.id: row for row in obs_result.scalars()}

    # ── Fusion scoring ────────────────────────────────────────────────────────
    scored: list[ScoredObservation] = []
    for obs_id, obs in observations.items():
        # M2: use valid_from/valid_until for recency anchor
        obs_confidence: float = getattr(obs, "confidence", 0.5)
        obs_evidence_count: int = getattr(obs, "evidence_count", 1)
        recency = _recency_score(
            obs.created_at,
            _as_of,
            cfg.recency_decay_days,
            valid_from=obs.valid_from,
            valid_until=obs.valid_until,
        )
        fts_r = fts_scores.get(obs_id, 0.0)
        sem_r = semantic_scores.get(obs_id, 0.0)
        graph_prox = graph_scores.get(obs_id, 0.0)
        final = _compute_final_score(
            fts_r,
            sem_r,
            recency,
            obs.score,
            cfg.weights,
            hit_count=obs.hit_count,
            source_quality=obs.source_quality,
            user_confirmed=obs.user_confirmed,
            score_features=cfg.score_features,
            graph_proximity=graph_prox,
            confidence=obs_confidence,
            evidence_count=obs_evidence_count,
        )
        scored.append(
            ScoredObservation(
                id=obs.id,
                scope_kind=obs.scope_kind,
                scope_id=obs.scope_id,
                category=obs.category,
                content=obs.content,
                score=obs.score,
                hit_count=obs.hit_count,
                source_quality=obs.source_quality,
                user_confirmed=obs.user_confirmed,
                valid_from=obs.valid_from,
                valid_until=obs.valid_until,
                superseded_by=obs.superseded_by,
                owner_user_id=obs.owner_user_id,
                created_at=obs.created_at,
                accessed_at=obs.accessed_at,
                confidence=obs_confidence,
                supersedes=getattr(obs, "supersedes", None),
                evidence_count=obs_evidence_count,
                final_score=final,
                fts_rank=fts_r,
                semantic_sim=sem_r,
                recency=recency,
                graph_proximity=graph_prox,
            )
        )

    scored.sort(key=lambda x: x.final_score, reverse=True)
    results = scored[:limit]
    session_factory: async_sessionmaker[AsyncSession] | None = None
    if session.bind is not None:
        session_factory = async_sessionmaker(
            bind=session.bind,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    if record_usage:
        _schedule_observation_usage_update(
            [obs.id for obs in results],
            _as_of,
            session_factory=session_factory,
        )
    return results
