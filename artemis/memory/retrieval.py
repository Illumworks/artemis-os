"""Fusion retrieval for memory observations.

Entry point: search_observations()

Retrieval merges four candidate pools — FTS (tsvector), semantic (pgvector cosine),
recency (exponential decay), score (stored field) — weighted by config/memory-retrieval.yaml.
All filtering (scope, superseded_by, validity windows) is applied at the SQL level.
Fusion scoring is done in Python after fetching the candidate rows.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import MemoryObservation
from artemis.memory.schemas import Scope, ScoredObservation

if TYPE_CHECKING:
    from artemis.memory.embeddings import EmbeddingProvider

_logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "memory-retrieval.yaml"


# ── Config ───────────────────────────────────────────────────────────────────


class RetrievalWeights(BaseModel):
    fts: float = 0.30
    semantic: float = 0.40
    recency: float = 0.15
    score: float = 0.15


class RetrievalConfig(BaseModel):
    weights: RetrievalWeights = RetrievalWeights()
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


def _recency_score(created_at: datetime, as_of: datetime, decay_days: float) -> float:
    """Exponential decay: score = exp(-ln(2) * days / half_life).

    At t=0 → 1.0. At t=half_life → 0.5. At t=∞ → 0.
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    delta = as_of - created_at
    days = max(delta.total_seconds() / 86400.0, 0.0)
    return math.exp(-math.log(2) * days / max(decay_days, 1.0))


def _compute_final_score(
    fts_rank: float,
    semantic_sim: float,
    recency: float,
    obs_score: float,
    weights: RetrievalWeights,
) -> float:
    return (
        weights.fts * min(fts_rank, 1.0)
        + weights.semantic * max(0.0, semantic_sim)
        + weights.recency * recency
        + weights.score * max(0.0, obs_score)
    )


# ── SQL helpers ───────────────────────────────────────────────────────────────


def _scope_sql_parts(
    scope_set: list[Scope],
    prefix: str = "",
) -> tuple[str, dict[str, str]]:
    """Return (IN clause fragment, params dict) for scope filtering.

    prefix is prepended to column names, e.g. 'o.' for aliased tables.
    """
    params: dict[str, str] = {}
    tuples: list[str] = []
    for i, s in enumerate(scope_set):
        params[f"_sk_{i}"] = s.scope_kind
        params[f"_si_{i}"] = s.scope_id
        tuples.append(f"(:_sk_{i}, :_si_{i})")
    clause = f"({prefix}scope_kind, {prefix}scope_id) IN ({', '.join(tuples)})"
    return clause, params


def _validity_sql(as_of_param: str = ":_as_of", prefix: str = "") -> str:
    return (
        f"AND ({prefix}valid_from IS NULL OR {prefix}valid_from <= {as_of_param})\n"
        f"    AND ({prefix}valid_until IS NULL OR {prefix}valid_until >= {as_of_param})"
    )


# ── Main retrieval function ───────────────────────────────────────────────────


async def search_observations(
    session: AsyncSession,
    scope_set: list[Scope],
    query: str,
    limit: int = 10,
    as_of: datetime | None = None,
    modes: list[Literal["fts", "semantic", "recency", "score"]] | None = None,
    cfg: RetrievalConfig | None = None,
    provider: "EmbeddingProvider | None" = None,
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
    """
    if not scope_set:
        return []

    cfg = cfg or get_retrieval_config()
    modes = modes or ["fts", "semantic", "recency", "score"]
    _as_of = as_of or datetime.now(timezone.utc)

    scope_clause, scope_params = _scope_sql_parts(scope_set)
    validity = _validity_sql()
    base_params: dict[str, Any] = {**scope_params, "_as_of": _as_of, "_k": cfg.top_k}

    candidate_ids: set[int] = set()
    fts_scores: dict[int, float] = {}
    semantic_scores: dict[int, float] = {}

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
    if "semantic" in modes:
        from artemis.memory.embeddings import get_default_provider

        _provider = provider or get_default_provider()
        try:
            query_vec = await _provider.embed(query)
            vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"
            scope_clause_o, _ = _scope_sql_parts(scope_set, prefix="o.")
            sem_sql = text(f"""
                SELECT o.id,
                       1.0 - (me.embedding <=> :_qvec::vector) AS semantic_sim
                FROM memory_observations o
                JOIN memory_embeddings me
                  ON me.target_table = 'observation' AND me.target_id = o.id
                WHERE {scope_clause_o}
                  AND o.superseded_by IS NULL
                  AND me.model_version = :_model_version
                  AND (o.valid_from IS NULL OR o.valid_from <= :_as_of)
                  AND (o.valid_until IS NULL OR o.valid_until >= :_as_of)
                ORDER BY me.embedding <=> :_qvec::vector
                LIMIT :_k
            """)
            sem_params = {
                **base_params,
                "_qvec": vec_str,
                "_model_version": _provider.model_version,
            }
            result = await session.execute(sem_sql, sem_params)
            for row in result:
                candidate_ids.add(int(row.id))
                semantic_scores[int(row.id)] = float(row.semantic_sim)
        except Exception:
            _logger.warning("Semantic search failed", exc_info=True)

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
        recency = _recency_score(obs.created_at, _as_of, cfg.recency_decay_days)
        fts_r = fts_scores.get(obs_id, 0.0)
        sem_r = semantic_scores.get(obs_id, 0.0)
        final = _compute_final_score(fts_r, sem_r, recency, obs.score, cfg.weights)
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
                final_score=final,
                fts_rank=fts_r,
                semantic_sim=sem_r,
                recency=recency,
            )
        )

    scored.sort(key=lambda x: x.final_score, reverse=True)
    return scored[:limit]
