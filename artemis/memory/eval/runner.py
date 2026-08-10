"""Memory M2 retrieval-eval + tuning harness.

Local artifact persistence is intentionally file-based under ~/.artemis so the
live corpus stays read-only. The harness reads the real memory DB, generates a
repeatable QA set, evaluates search_observations() without recording usage, and
optionally restores a source backup into an artemis_test database for 10x
scale duplication.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import re
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from artemis.agent.client import CompletionRequest, ModelAdapter
from artemis.agent.types import Message, TextBlock
from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.memory.embeddings import EmbeddingProvider, get_default_provider
from artemis.memory.graph import _to_slug
from artemis.memory.hashchain import verify_chain
from artemis.memory.models import (
    MemoryEmbedding,
    MemoryEntity,
    MemoryEntityAlias,
    MemoryEntityMention,
    MemoryObservation,
    MemoryObservationScope,
    MemoryRelation,
    MemoryScope,
)
from artemis.memory.retrieval import (
    RetrievalConfig,
    RetrievalWeights,
    ScoreFeatureWeights,
    _series_key,
    get_retrieval_config,
    search_observations,
)
from artemis.memory.schemas import Scope
from artemis.memory.store import _content_hash, upsert_embedding
from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter
from scripts.memory_backup import run_backup
from scripts.memory_restore import run_restore

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "with",
    }
)


class CorpusFingerprint(BaseModel):
    db_name: str
    observation_count: int
    active_observation_count: int
    drawer_count: int
    raw_input_count: int
    scope_count: int
    observation_scope_count: int
    observation_embedding_count: int
    entity_count: int
    relation_count: int
    raw_input_chain_ok: bool
    raw_input_chain_rows: int
    captured_at: datetime


class EvalQuery(BaseModel):
    query_id: str
    observation_id: int
    query_kind: Literal["keyword", "semantic", "recency", "graph"]
    query: str
    scope_set: list[Scope]
    category: str
    snippet: str
    entity_names: list[str] = Field(default_factory=list)
    created_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class QueryGenerationMeta(BaseModel):
    provider: str | None = None
    model: str | None = None
    used_llm: bool
    input_tokens: int = 0
    output_tokens: int = 0


class EvalQuerySet(BaseModel):
    name: str
    seed: int
    qa_size: int
    fingerprint: CorpusFingerprint
    generation: QueryGenerationMeta
    queries: list[EvalQuery]
    created_at: datetime


class QueryRunResult(BaseModel):
    query_id: str
    observation_id: int
    query_kind: str
    query: str
    hit_rank: int | None
    hit_in_top_1: bool
    hit_in_top_3: bool
    hit_in_top_5: bool
    hit_in_top_10: bool
    latency_ms: float
    result_count: int
    embedding_calls: int
    embedding_input_tokens: int
    result_payload_tokens: int
    top_result_ids: list[int]
    miss_top_snippets: list[str] = Field(default_factory=list)


class EvalMetrics(BaseModel):
    query_count: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    latency_p50_ms: float
    latency_p95_ms: float
    mean_embedding_calls: float
    mean_embedding_input_tokens: float
    mean_result_payload_tokens: float
    p95_result_payload_tokens: float
    token_estimator: str


class EvalRunReport(BaseModel):
    report_type: Literal["baseline", "tuned", "scale"]
    label: str
    db_name: str
    qa_set_name: str
    fingerprint_before: CorpusFingerprint
    fingerprint_after: CorpusFingerprint
    retrieval_config: RetrievalConfig
    metrics: EvalMetrics
    query_results: list[QueryRunResult]
    created_at: datetime
    notes: list[str] = Field(default_factory=list)


class SweepCandidateResult(BaseModel):
    name: str
    config: RetrievalConfig
    metrics: EvalMetrics


class SweepReport(BaseModel):
    report_type: Literal["sweep"] = "sweep"
    label: str
    db_name: str
    qa_set_name: str
    baseline_config: RetrievalConfig
    baseline_metrics: EvalMetrics
    candidates: list[SweepCandidateResult]
    recommended: SweepCandidateResult | None = None
    created_at: datetime
    notes: list[str] = Field(default_factory=list)


class FullRunReport(BaseModel):
    report_type: Literal["full"] = "full"
    db_name: str
    qa_set_path: str
    baseline_report_path: str
    sweep_report_path: str
    scale_report_path: str | None = None
    created_at: datetime


class _ObservationCandidate(BaseModel):
    id: int
    scope_kind: str
    scope_id: str
    category: str
    content: str
    created_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    entity_names: list[str] = Field(default_factory=list)


class _EmbeddingCallStats(BaseModel):
    calls: int = 0
    input_tokens: int = 0


def _estimate_tokens(text_value: str) -> int:
    collapsed = re.sub(r"\s+", " ", text_value).strip()
    if not collapsed:
        return 0
    return max(1, math.ceil(len(collapsed) / 4))


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    index = max(0, min(len(ordered) - 1, math.ceil((percentile / 100.0) * len(ordered)) - 1))
    return round(ordered[index], 3)


def _snippet(text_value: str, *, max_chars: int = 160) -> str:
    collapsed = re.sub(r"\s+", " ", text_value).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"


def _artifact_root(db_url: str) -> Path:
    db_name = _database_name(db_url)
    root = settings.memory_eval_dir / db_name
    root.mkdir(parents=True, exist_ok=True)
    (root / "qa_sets").mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    return root


def _database_name(db_url: str) -> str:
    clean = re.sub(r"^postgresql\+[^:]+://", "postgresql://", db_url)
    parsed = urlparse(clean)
    return (parsed.path or "/artemis_os").lstrip("/") or "artemis_os"


def _qa_set_path(root: Path, name: str) -> Path:
    return root / "qa_sets" / f"{name}.json"


def _report_path(root: Path, label: str, report_type: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-") or "memory-m2"
    return root / "reports" / f"{stamp}-{safe_label}-{report_type}.json"


def _response_text(response: Any) -> str:
    parts = [block.text for block in response.message.content if isinstance(block, TextBlock)]
    return "\n".join(parts).strip()


def _extract_json_block(raw_text: str) -> Any:
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", stripped, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


class CountingEmbeddingProvider:
    def __init__(self, inner: EmbeddingProvider) -> None:
        self._inner = inner
        self.stats = _EmbeddingCallStats()

    async def embed(self, text_value: str) -> list[float]:
        self.stats.calls += 1
        self.stats.input_tokens += _estimate_tokens(text_value)
        return await self._inner.embed(text_value)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.stats.calls += len(texts)
        self.stats.input_tokens += sum(_estimate_tokens(item) for item in texts)
        return await self._inner.embed_batch(texts)

    @property
    def model_version(self) -> str:
        return self._inner.model_version

    @property
    def dims(self) -> int:
        return self._inner.dims


async def _open_session_factory(
    db_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    attach_pgvector_codec(engine)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _capture_fingerprint(session: AsyncSession, db_name: str) -> CorpusFingerprint:
    tables = {
        "observation_count": "memory_observations",
        "drawer_count": "memory_drawers",
        "raw_input_count": "raw_inputs",
        "scope_count": "memory_scopes",
        "observation_scope_count": "memory_observation_scopes",
        "entity_count": "memory_entities",
        "relation_count": "memory_relations",
    }
    counts: dict[str, int] = {}
    for key, table_name in tables.items():
        result = await session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        counts[key] = int(result.scalar_one())
    embedding_result = await session.execute(
        text("SELECT COUNT(*) FROM memory_embeddings WHERE target_table = 'observation'")
    )
    chain = await verify_chain(session)
    active_result = await session.execute(
        text("SELECT COUNT(*) FROM memory_observations WHERE superseded_by IS NULL")
    )
    return CorpusFingerprint(
        db_name=db_name,
        observation_count=counts["observation_count"],
        active_observation_count=int(active_result.scalar_one()),
        drawer_count=counts["drawer_count"],
        raw_input_count=counts["raw_input_count"],
        scope_count=counts["scope_count"],
        observation_scope_count=counts["observation_scope_count"],
        observation_embedding_count=int(embedding_result.scalar_one()),
        entity_count=counts["entity_count"],
        relation_count=counts["relation_count"],
        raw_input_chain_ok=chain.ok,
        raw_input_chain_rows=chain.row_count,
        captured_at=datetime.now(UTC),
    )


async def _load_scope_map(session: AsyncSession) -> dict[int, list[Scope]]:
    result = await session.execute(
        text(
            "SELECT observation_id, scope_kind, scope_id "
            "FROM memory_observation_scopes "
            "ORDER BY observation_id, is_primary DESC, scope_kind, scope_id"
        )
    )
    grouped: dict[int, list[Scope]] = defaultdict(list)
    for row in result.mappings():
        grouped[int(row["observation_id"])].append(
            Scope(scope_kind=str(row["scope_kind"]), scope_id=str(row["scope_id"]))
        )
    return grouped


async def _load_entity_names_by_observation(session: AsyncSession) -> dict[int, list[str]]:
    result = await session.execute(
        text(
            """
            SELECT m.source_id AS observation_id, e.canonical_name
            FROM memory_entity_mentions m
            JOIN memory_entities e ON e.id = m.entity_id
            WHERE m.source_kind = 'observation'
            ORDER BY m.source_id, e.canonical_name
            """
        )
    )
    grouped: dict[int, list[str]] = defaultdict(list)
    for row in result.mappings():
        grouped[int(row["observation_id"])].append(str(row["canonical_name"]))
    return grouped


async def _load_generation_candidates(
    session: AsyncSession,
) -> tuple[list[_ObservationCandidate], dict[int, list[Scope]]]:
    scope_map = await _load_scope_map(session)
    entity_names = await _load_entity_names_by_observation(session)
    result = await session.execute(
        select(MemoryObservation)
        .where(MemoryObservation.superseded_by.is_(None))
        .order_by(MemoryObservation.created_at.desc(), MemoryObservation.id.desc())
    )
    rows = list(result.scalars())
    candidates: list[_ObservationCandidate] = []
    for row in rows:
        candidates.append(
            _ObservationCandidate(
                id=row.id,
                scope_kind=row.scope_kind,
                scope_id=row.scope_id,
                category=row.category,
                content=row.content,
                created_at=row.created_at,
                valid_from=row.valid_from,
                valid_until=row.valid_until,
                entity_names=entity_names.get(row.id, []),
            )
        )
    return candidates, scope_map


def _choose_observations_for_eval(
    candidates: list[_ObservationCandidate],
    total: int,
    seed: int,
) -> list[tuple[str, _ObservationCandidate]]:
    rng = random.Random(seed)
    ordered = list(candidates)
    rng.shuffle(ordered)

    graph_pool = [item for item in ordered if item.entity_names]
    recency_pool = sorted(
        ordered,
        key=lambda item: (
            0 if item.valid_from or item.valid_until else 1,
            -int(item.created_at.timestamp()),
        ),
    )
    keyword_pool = sorted(ordered, key=lambda item: (-len(item.content), item.id))
    semantic_pool = list(ordered)

    graph_target = min(max(2, total // 10), len(graph_pool))
    recency_target = max(4, total // 4)
    semantic_target = max(8, total // 3)
    keyword_target = total - graph_target - recency_target - semantic_target
    if keyword_target < 0:
        keyword_target = 0

    chosen_ids: set[int] = set()
    chosen: list[tuple[str, _ObservationCandidate]] = []

    def _take(
        label: str,
        pool: Iterable[_ObservationCandidate],
        wanted: int,
    ) -> None:
        for item in pool:
            if len([kind for kind, _ in chosen if kind == label]) >= wanted:
                break
            if item.id in chosen_ids:
                continue
            chosen_ids.add(item.id)
            chosen.append((label, item))

    _take("graph", graph_pool, graph_target)
    _take("recency", recency_pool, recency_target)
    _take("semantic", semantic_pool, semantic_target)
    _take("keyword", keyword_pool, keyword_target)

    if len(chosen) < total:
        for item in ordered:
            if item.id in chosen_ids:
                continue
            label = "semantic" if len(chosen) % 2 == 0 else "keyword"
            chosen_ids.add(item.id)
            chosen.append((label, item))
            if len(chosen) >= total:
                break

    return chosen[:total]


def _fallback_query(kind: str, item: _ObservationCandidate) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", item.content)
    keywords = [word for word in words if word.lower() not in _STOPWORDS]
    shortlist = keywords[:6] or words[:6] or [item.category]
    if kind == "keyword":
        return " ".join(shortlist[:5])
    if kind == "recency":
        return "latest " + " ".join(shortlist[:4])
    if kind == "graph" and item.entity_names:
        if len(item.entity_names) >= 2:
            return f"how are {item.entity_names[0]} and {item.entity_names[1]} connected"
        return f"what involves {item.entity_names[0]}"
    return "what does memory say about " + " ".join(shortlist[:4])


async def _generate_queries_with_llm(
    adapter: ModelAdapter,
    items: list[tuple[str, _ObservationCandidate]],
    *,
    provider_name: str,
    model_name: str | None,
) -> tuple[list[EvalQuery], QueryGenerationMeta]:
    batches: list[list[tuple[str, _ObservationCandidate]]] = []
    batch_size = 8
    for idx in range(0, len(items), batch_size):
        batches.append(items[idx : idx + batch_size])

    queries: list[EvalQuery] = []
    usage_in = 0
    usage_out = 0

    for batch in batches:
        serialized_items: list[dict[str, Any]] = []
        for kind, item in batch:
            serialized_items.append(
                {
                    "query_id": f"{kind}-{item.id}",
                    "query_kind": kind,
                    "observation_id": item.id,
                    "category": item.category,
                    "created_at": item.created_at.isoformat(),
                    "valid_from": item.valid_from.isoformat() if item.valid_from else None,
                    "valid_until": item.valid_until.isoformat() if item.valid_until else None,
                    "entity_names": item.entity_names,
                    "content": item.content,
                }
            )

        prompt = (
            "Create one natural-language retrieval query for each observation.\n"
            "Output JSON only: a list of objects with keys query_id and query.\n"
            "Rules:\n"
            "- The correct answer should be that exact observation.\n"
            "- keyword: use distinctive exact nouns/phrases from the observation.\n"
            "- semantic: paraphrase heavily; avoid rare exact phrases when possible.\n"
            "- recency: make freshness matter with words like latest/current/recent.\n"
            "- graph: ask through named entities or their relationship.\n"
            "- Keep each query under 16 words.\n"
            "- Do not include ids, dates unless needed for meaning, or quote the whole text.\n\n"
            f"Items:\n{json.dumps(serialized_items, ensure_ascii=False, indent=2)}"
        )
        response = await adapter.complete(
            CompletionRequest(
                messages=[Message(role="user", content=[TextBlock(text=prompt)])],
                system="You generate concise retrieval-eval queries and return strict JSON.",
                model=model_name,
                max_tokens=1800,
            )
        )
        raw = _response_text(response)
        usage_in += response.usage.input_tokens
        usage_out += response.usage.output_tokens
        try:
            parsed = _extract_json_block(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse query-generation JSON: {exc}") from exc
        lookup = {str(item["query_id"]): item["query"] for item in parsed}
        for kind, item in batch:
            query_id = f"{kind}-{item.id}"
            query_text = str(lookup.get(query_id, "")).strip() or _fallback_query(kind, item)
            queries.append(
                EvalQuery(
                    query_id=query_id,
                    observation_id=item.id,
                    query_kind=kind,
                    query=query_text,
                    scope_set=[],
                    category=item.category,
                    snippet=_snippet(item.content),
                    entity_names=item.entity_names,
                    created_at=item.created_at,
                    valid_from=item.valid_from,
                    valid_until=item.valid_until,
                )
            )

    return queries, QueryGenerationMeta(
        provider=provider_name,
        model=model_name,
        used_llm=True,
        input_tokens=usage_in,
        output_tokens=usage_out,
    )


async def build_or_load_qa_set(
    session: AsyncSession,
    *,
    db_url: str,
    name: str,
    qa_size: int,
    seed: int,
    generator_provider: str,
    generator_model: str | None,
    force_regenerate: bool,
) -> tuple[EvalQuerySet, Path]:
    root = _artifact_root(db_url)
    path = _qa_set_path(root, name)
    fingerprint = await _capture_fingerprint(session, _database_name(db_url))

    if path.exists() and not force_regenerate:
        loaded = EvalQuerySet.model_validate_json(path.read_text())
        if (
            loaded.fingerprint.observation_count == fingerprint.observation_count
            and loaded.fingerprint.active_observation_count == fingerprint.active_observation_count
            and loaded.fingerprint.raw_input_count == fingerprint.raw_input_count
        ):
            return loaded, path
        logger.info("QA set %s is stale relative to current corpus; regenerating", name)

    candidates, scope_map = await _load_generation_candidates(session)
    picks = _choose_observations_for_eval(candidates, qa_size, seed)

    used_llm = False
    generation_meta = QueryGenerationMeta(used_llm=False)
    queries: list[EvalQuery]
    try:
        adapter = resolve_adapter(provider=generator_provider)
        queries, generation_meta = await _generate_queries_with_llm(
            adapter,
            picks,
            provider_name=generator_provider,
            model_name=generator_model,
        )
        used_llm = True
    except (NoProviderAvailableError, RuntimeError) as exc:
        logger.warning("Query generation fell back to heuristics: %s", exc)
        queries = []
        for kind, item in picks:
            queries.append(
                EvalQuery(
                    query_id=f"{kind}-{item.id}",
                    observation_id=item.id,
                    query_kind=kind,
                    query=_fallback_query(kind, item),
                    scope_set=[],
                    category=item.category,
                    snippet=_snippet(item.content),
                    entity_names=item.entity_names,
                    created_at=item.created_at,
                    valid_from=item.valid_from,
                    valid_until=item.valid_until,
                )
            )

    for query in queries:
        query.scope_set = scope_map.get(
            query.observation_id,
            [
                Scope(
                    scope_kind=next(
                        item.scope_kind for _, item in picks if item.id == query.observation_id
                    ),
                    scope_id=next(
                        item.scope_id for _, item in picks if item.id == query.observation_id
                    ),
                )
            ],
        )

    qa_set = EvalQuerySet(
        name=name,
        seed=seed,
        qa_size=len(queries),
        fingerprint=fingerprint,
        generation=(
            generation_meta
            if used_llm
            else QueryGenerationMeta(provider=None, model=None, used_llm=False)
        ),
        queries=queries,
        created_at=datetime.now(UTC),
    )
    path.write_text(qa_set.model_dump_json(indent=2))
    return qa_set, path


def _serialize_payload_tokens(results: Sequence[Any]) -> int:
    parts = []
    for item in results:
        parts.append(f"[{item.scope_kind}:{item.scope_id}] {item.category}: {item.content}")
    return _estimate_tokens("\n".join(parts))


def _vector_to_list(value: Any) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    return [float(item) for item in value]


def _same_fingerprint(left: CorpusFingerprint, right: CorpusFingerprint) -> bool:
    return left.model_dump(exclude={"captured_at"}) == right.model_dump(exclude={"captured_at"})


def _compute_metrics(results: Sequence[QueryRunResult]) -> EvalMetrics:
    count = len(results)
    if count == 0:
        return EvalMetrics(
            query_count=0,
            recall_at_1=0.0,
            recall_at_3=0.0,
            recall_at_5=0.0,
            recall_at_10=0.0,
            mrr=0.0,
            latency_p50_ms=0.0,
            latency_p95_ms=0.0,
            mean_embedding_calls=0.0,
            mean_embedding_input_tokens=0.0,
            mean_result_payload_tokens=0.0,
            p95_result_payload_tokens=0.0,
            token_estimator="chars_div_4_approx",
        )

    latencies = [item.latency_ms for item in results]
    embedding_calls = [item.embedding_calls for item in results]
    embedding_tokens = [item.embedding_input_tokens for item in results]
    payload_tokens = [item.result_payload_tokens for item in results]

    def _ratio(attr: str) -> float:
        return round(sum(1 for item in results if getattr(item, attr)) / count, 4)

    mrr = 0.0
    for item in results:
        if item.hit_rank is not None and item.hit_rank > 0:
            mrr += 1.0 / item.hit_rank
    mrr = round(mrr / count, 4)

    return EvalMetrics(
        query_count=count,
        recall_at_1=_ratio("hit_in_top_1"),
        recall_at_3=_ratio("hit_in_top_3"),
        recall_at_5=_ratio("hit_in_top_5"),
        recall_at_10=_ratio("hit_in_top_10"),
        mrr=mrr,
        latency_p50_ms=round(median(latencies), 3),
        latency_p95_ms=_percentile(latencies, 95),
        mean_embedding_calls=round(sum(embedding_calls) / count, 3),
        mean_embedding_input_tokens=round(sum(embedding_tokens) / count, 3),
        mean_result_payload_tokens=round(sum(payload_tokens) / count, 3),
        p95_result_payload_tokens=_percentile(payload_tokens, 95),
        token_estimator="chars_div_4_approx",
    )


async def evaluate_qa_set(
    session: AsyncSession,
    *,
    db_url: str,
    qa_set: EvalQuerySet,
    cfg: RetrievalConfig,
    label: str,
    report_type: Literal["baseline", "tuned", "scale"],
    series_aware_credit: bool = True,
) -> EvalRunReport:
    """Evaluate ``qa_set`` against ``search_observations`` with ``cfg``.

    Args:
        series_aware_credit: when True (default), a query is considered a hit
            when the returned result is the exact target observation OR any
            returned result shares the target's ``_series_key`` — so the harness
            doesn't penalise the live time-series queries when the QA was frozen
            against an older snapshot.  When False, only an exact id match counts
            (pre-M1c strict behaviour).
    """
    db_name = _database_name(db_url)
    fingerprint_before = await _capture_fingerprint(session, db_name)
    base_provider = get_default_provider()
    results: list[QueryRunResult] = []

    # Pre-load target snippets for series-key lookup (avoid re-querying)
    target_content_cache: dict[int, str | None] = {}

    async def _get_target_content(obs_id: int) -> str | None:
        if obs_id not in target_content_cache:
            row = await session.get(MemoryObservation, obs_id)
            target_content_cache[obs_id] = row.content if row is not None else None
        return target_content_cache[obs_id]

    for query in qa_set.queries:
        counting_provider = CountingEmbeddingProvider(base_provider)
        started = time.perf_counter()
        hits = await search_observations(
            session,
            query.scope_set,
            query.query,
            limit=10,
            cfg=cfg,
            provider=counting_provider,
            record_usage=False,
        )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        hit_rank: int | None = None
        top_ids = [item.id for item in hits]

        target_series_key: tuple[str, str] | None = None
        if series_aware_credit:
            target_content = await _get_target_content(query.observation_id)
            if target_content is not None:
                target_series_key = _series_key(target_content)

        for index, item in enumerate(hits, start=1):
            if item.id == query.observation_id:
                hit_rank = index
                break
            if series_aware_credit and target_series_key is not None:
                result_key = _series_key(item.content)
                if result_key == target_series_key:
                    hit_rank = index
                    break
        miss_snippets: list[str] = []
        if hit_rank is None:
            miss_snippets = [_snippet(item.content, max_chars=96) for item in hits[:3]]
        results.append(
            QueryRunResult(
                query_id=query.query_id,
                observation_id=query.observation_id,
                query_kind=query.query_kind,
                query=query.query,
                hit_rank=hit_rank,
                hit_in_top_1=hit_rank == 1,
                hit_in_top_3=hit_rank is not None and hit_rank <= 3,
                hit_in_top_5=hit_rank is not None and hit_rank <= 5,
                hit_in_top_10=hit_rank is not None and hit_rank <= 10,
                latency_ms=latency_ms,
                result_count=len(hits),
                embedding_calls=counting_provider.stats.calls,
                embedding_input_tokens=counting_provider.stats.input_tokens,
                result_payload_tokens=_serialize_payload_tokens(hits),
                top_result_ids=top_ids,
                miss_top_snippets=miss_snippets,
            )
        )

    fingerprint_after = await _capture_fingerprint(session, db_name)
    notes: list[str] = []
    if not _same_fingerprint(fingerprint_before, fingerprint_after):
        notes.append("Corpus fingerprint changed during eval; read-only invariant violated.")

    return EvalRunReport(
        report_type=report_type,
        label=label,
        db_name=db_name,
        qa_set_name=qa_set.name,
        fingerprint_before=fingerprint_before,
        fingerprint_after=fingerprint_after,
        retrieval_config=cfg,
        metrics=_compute_metrics(results),
        query_results=results,
        created_at=datetime.now(UTC),
        notes=notes,
    )


def _candidate_configs(base: RetrievalConfig) -> list[tuple[str, RetrievalConfig]]:
    base_weights = base.weights
    base_score = base.score_features

    def _cfg(
        *,
        fts: float,
        semantic: float,
        recency: float,
        score: float,
        graph: float | None = None,
        relevance: float | None = None,
        hits: float | None = None,
        quality: float | None = None,
        confirmed: float | None = None,
    ) -> RetrievalConfig:
        return RetrievalConfig(
            weights=RetrievalWeights(
                fts=fts,
                semantic=semantic,
                recency=recency,
                score=score,
                graph_proximity=graph if graph is not None else base_weights.graph_proximity,
            ),
            score_features=ScoreFeatureWeights(
                relevance=relevance if relevance is not None else base_score.relevance,
                hits=hits if hits is not None else base_score.hits,
                quality=quality if quality is not None else base_score.quality,
                confirmed=confirmed if confirmed is not None else base_score.confirmed,
            ),
            top_k=base.top_k,
            recency_decay_days=base.recency_decay_days,
        )

    return [
        ("baseline", base),
        ("lexical_focus", _cfg(fts=0.40, semantic=0.30, recency=0.15, score=0.15)),
        ("semantic_focus", _cfg(fts=0.20, semantic=0.50, recency=0.15, score=0.15)),
        ("balanced", _cfg(fts=0.25, semantic=0.25, recency=0.25, score=0.25)),
        ("recency_focus", _cfg(fts=0.25, semantic=0.25, recency=0.30, score=0.20)),
        ("score_focus", _cfg(fts=0.20, semantic=0.30, recency=0.15, score=0.35)),
        (
            "quality_bias",
            _cfg(
                fts=0.25,
                semantic=0.35,
                recency=0.20,
                score=0.20,
                relevance=0.25,
                hits=0.10,
                quality=0.55,
                confirmed=0.10,
            ),
        ),
        (
            "confirmed_bias",
            _cfg(
                fts=0.25,
                semantic=0.35,
                recency=0.20,
                score=0.20,
                relevance=0.25,
                hits=0.10,
                quality=0.25,
                confirmed=0.40,
            ),
        ),
        (
            "graph_heavier",
            _cfg(fts=0.25, semantic=0.35, recency=0.20, score=0.20, graph=0.18),
        ),
    ]


def _non_regressing(candidate: EvalMetrics, baseline: EvalMetrics) -> bool:
    return candidate.latency_p95_ms <= max(
        baseline.latency_p95_ms * 1.05, baseline.latency_p95_ms + 2.0
    ) and candidate.mean_result_payload_tokens <= max(
        baseline.mean_result_payload_tokens * 1.05,
        baseline.mean_result_payload_tokens + 20.0,
    )


def _choose_recommendation(
    baseline_metrics: EvalMetrics,
    candidates: list[SweepCandidateResult],
) -> SweepCandidateResult | None:
    viable = [
        item
        for item in candidates
        if item.name != "baseline" and _non_regressing(item.metrics, baseline_metrics)
    ]
    if not viable:
        return None
    viable.sort(
        key=lambda item: (
            item.metrics.recall_at_5,
            item.metrics.recall_at_10,
            item.metrics.mrr,
            -item.metrics.latency_p95_ms,
        ),
        reverse=True,
    )
    best = viable[0]
    baseline_tuple = (
        baseline_metrics.recall_at_5,
        baseline_metrics.recall_at_10,
        baseline_metrics.mrr,
    )
    best_tuple = (best.metrics.recall_at_5, best.metrics.recall_at_10, best.metrics.mrr)
    if best_tuple <= baseline_tuple:
        return None
    return best


async def run_sweep(
    session: AsyncSession,
    *,
    db_url: str,
    qa_set: EvalQuerySet,
    label: str,
) -> SweepReport:
    base_cfg = get_retrieval_config()
    baseline_report = await evaluate_qa_set(
        session,
        db_url=db_url,
        qa_set=qa_set,
        cfg=base_cfg,
        label=label,
        report_type="baseline",
    )
    candidates: list[SweepCandidateResult] = []
    for name, cfg in _candidate_configs(base_cfg):
        report = await evaluate_qa_set(
            session,
            db_url=db_url,
            qa_set=qa_set,
            cfg=cfg,
            label=f"{label}-{name}",
            report_type="tuned",
        )
        candidates.append(SweepCandidateResult(name=name, config=cfg, metrics=report.metrics))
    recommended = _choose_recommendation(baseline_report.metrics, candidates)
    notes: list[str] = []
    if recommended is None:
        notes.append("No candidate improved recall without latency/payload regression.")
    return SweepReport(
        label=label,
        db_name=_database_name(db_url),
        qa_set_name=qa_set.name,
        baseline_config=base_cfg,
        baseline_metrics=baseline_report.metrics,
        candidates=candidates,
        recommended=recommended,
        created_at=datetime.now(UTC),
        notes=notes,
    )


async def _duplicate_for_scale(target_session: AsyncSession, factor: int) -> dict[str, int]:
    original_scopes: list[MemoryScope] = list(
        (
            await target_session.execute(
                select(MemoryScope).order_by(MemoryScope.scope_kind, MemoryScope.scope_id)
            )
        ).scalars()
    )
    original_observations: list[MemoryObservation] = list(
        (
            await target_session.execute(select(MemoryObservation).order_by(MemoryObservation.id))
        ).scalars()
    )
    scope_rows: list[MemoryObservationScope] = list(
        (
            await target_session.execute(
                select(MemoryObservationScope).order_by(MemoryObservationScope.observation_id)
            )
        ).scalars()
    )
    observation_scopes_by_id: dict[int, list[MemoryObservationScope]] = defaultdict(list)
    for row in scope_rows:
        observation_scopes_by_id[row.observation_id].append(row)

    original_embeddings: list[MemoryEmbedding] = list(
        (
            await target_session.execute(
                select(MemoryEmbedding)
                .where(MemoryEmbedding.target_table == "observation")
                .order_by(MemoryEmbedding.target_id)
            )
        ).scalars()
    )
    embedding_by_obs: dict[int, list[MemoryEmbedding]] = defaultdict(list)
    for embedding_row in original_embeddings:
        embedding_by_obs[embedding_row.target_id].append(embedding_row)

    original_entities: list[MemoryEntity] = list(
        (await target_session.execute(select(MemoryEntity).order_by(MemoryEntity.id))).scalars()
    )
    original_aliases: list[MemoryEntityAlias] = list(
        (
            await target_session.execute(select(MemoryEntityAlias).order_by(MemoryEntityAlias.id))
        ).scalars()
    )
    original_mentions: list[MemoryEntityMention] = list(
        (
            await target_session.execute(
                select(MemoryEntityMention)
                .where(MemoryEntityMention.source_kind == "observation")
                .order_by(MemoryEntityMention.id)
            )
        ).scalars()
    )
    original_relations: list[MemoryRelation] = list(
        (await target_session.execute(select(MemoryRelation).order_by(MemoryRelation.id))).scalars()
    )

    clones_created = 0
    entity_clones_created = 0

    for replica in range(1, factor):
        suffix = f"__m2x{replica}"
        for scope in original_scopes:
            target_session.add(
                MemoryScope(
                    scope_kind=scope.scope_kind,
                    scope_id=f"{scope.scope_id}{suffix}",
                    display_name=scope.display_name,
                    parent_scope_kind=scope.parent_scope_kind,
                    parent_scope_id=(
                        f"{scope.parent_scope_id}{suffix}" if scope.parent_scope_id else None
                    ),
                    created_at=scope.created_at,
                )
            )
        await target_session.flush()

        obs_id_map: dict[int, int] = {}
        pending_obs: list[tuple[MemoryObservation, MemoryObservation]] = []
        for original in original_observations:
            clone = MemoryObservation(
                scope_kind=original.scope_kind,
                scope_id=f"{original.scope_id}{suffix}",
                category=original.category,
                content=original.content,
                content_hash=_content_hash(
                    original.scope_kind,
                    f"{original.scope_id}{suffix}",
                    original.content,
                ),
                score=original.score,
                hit_count=original.hit_count,
                source_quality=original.source_quality,
                user_confirmed=original.user_confirmed,
                valid_from=original.valid_from,
                valid_until=original.valid_until,
                superseded_by=None,
                owner_user_id=original.owner_user_id,
                created_at=original.created_at,
                accessed_at=original.accessed_at,
                raw_input_id=original.raw_input_id,
                confidence=original.confidence,
                supersedes=None,
                evidence_count=original.evidence_count,
                graph_status=original.graph_status,
                graph_attempt_count=original.graph_attempt_count,
                graph_last_attempt_at=original.graph_last_attempt_at,
                wing=original.wing,
                confidence_origin=original.confidence_origin,
            )
            target_session.add(clone)
            pending_obs.append((original, clone))
        await target_session.flush()
        for original, clone in pending_obs:
            obs_id_map[original.id] = clone.id
        for original, clone in pending_obs:
            clone.superseded_by = (
                obs_id_map[original.superseded_by] if original.superseded_by is not None else None
            )
            clone.supersedes = (
                obs_id_map[original.supersedes] if original.supersedes is not None else None
            )
        await target_session.flush()

        for original, clone in pending_obs:
            for scope_row in observation_scopes_by_id.get(original.id, []):
                target_session.add(
                    MemoryObservationScope(
                        observation_id=clone.id,
                        scope_kind=scope_row.scope_kind,
                        scope_id=f"{scope_row.scope_id}{suffix}",
                        weight=scope_row.weight,
                        is_primary=scope_row.is_primary,
                        created_at=scope_row.created_at,
                    )
                )
            for embedding in embedding_by_obs.get(original.id, []):
                await upsert_embedding(
                    target_session,
                    "observation",
                    clone.id,
                    embedding.model_version,
                    _vector_to_list(embedding.embedding),
                )
        clones_created += len(pending_obs)

        entity_id_map: dict[int, int] = {}
        pending_entities: list[tuple[MemoryEntity, MemoryEntity]] = []
        for original_entity in original_entities:
            entity_clone = MemoryEntity(
                entity_kind=original_entity.entity_kind,
                canonical_name=original_entity.canonical_name,
                name_slug=_to_slug(original_entity.canonical_name),
                scope_kind=original_entity.scope_kind,
                scope_id=f"{original_entity.scope_id}{suffix}",
                attributes=original_entity.attributes,
                first_seen_at=original_entity.first_seen_at,
                last_seen_at=original_entity.last_seen_at,
                mention_count=original_entity.mention_count,
                confidence=original_entity.confidence,
                superseded_by=None,
                valid_from=original_entity.valid_from,
                valid_until=original_entity.valid_until,
                entity_evidence_count=original_entity.entity_evidence_count,
                entity_supersedes=None,
            )
            target_session.add(entity_clone)
            pending_entities.append((original_entity, entity_clone))
        await target_session.flush()
        for original_entity, entity_clone in pending_entities:
            entity_id_map[original_entity.id] = entity_clone.id
        for original_entity, entity_clone in pending_entities:
            entity_clone.superseded_by = (
                entity_id_map[original_entity.superseded_by]
                if original_entity.superseded_by is not None
                else None
            )
            entity_clone.entity_supersedes = (
                entity_id_map[original_entity.entity_supersedes]
                if original_entity.entity_supersedes is not None
                else None
            )
        await target_session.flush()

        for alias in original_aliases:
            mapped_entity_id = entity_id_map.get(alias.entity_id)
            if mapped_entity_id is None:
                continue
            target_session.add(
                MemoryEntityAlias(
                    entity_id=mapped_entity_id,
                    alias=alias.alias,
                    alias_slug=alias.alias_slug,
                    created_at=alias.created_at,
                )
            )
        for mention in original_mentions:
            mapped_entity_id = entity_id_map.get(mention.entity_id)
            mapped_obs_id = obs_id_map.get(mention.source_id)
            if mapped_entity_id is None or mapped_obs_id is None:
                continue
            target_session.add(
                MemoryEntityMention(
                    entity_id=mapped_entity_id,
                    source_kind="observation",
                    source_id=mapped_obs_id,
                    mention_quote=mention.mention_quote,
                    weight=mention.weight,
                    created_at=mention.created_at,
                )
            )
        for relation in original_relations:
            subject_id = entity_id_map.get(relation.subject_id)
            object_id = entity_id_map.get(relation.object_id)
            if subject_id is None or object_id is None:
                continue
            target_session.add(
                MemoryRelation(
                    subject_id=subject_id,
                    predicate=relation.predicate,
                    object_id=object_id,
                    evidence_observation_id=(
                        obs_id_map[relation.evidence_observation_id]
                        if relation.evidence_observation_id is not None
                        else None
                    ),
                    weight=relation.weight,
                    confidence=relation.confidence,
                    first_seen_at=relation.first_seen_at,
                    last_seen_at=relation.last_seen_at,
                    superseded_by=None,
                )
            )
        entity_clones_created += len(pending_entities)
        await target_session.flush()

    await target_session.commit()
    return {
        "observation_clones_created": clones_created,
        "entity_clones_created": entity_clones_created,
    }


async def prepare_scaled_database(
    *,
    source_db_url: str,
    target_db_url: str,
    factor: int,
) -> str:
    target_name = _database_name(target_db_url)
    if "artemis_test" not in target_name:
        raise RuntimeError(f"Scale target DB must contain 'artemis_test'; got {target_name!r}")
    with tempfile.TemporaryDirectory(prefix="artemis-memory-m2-") as tmp_dir:
        backup_path = run_backup(backup_dir=Path(tmp_dir), keep_days=9999, db_url=source_db_url)
        run_restore(
            backup_path=backup_path,
            target_dbname=target_name,
            db_url=target_db_url,
            drop_before_restore=True,
        )
    engine, factory = await _open_session_factory(target_db_url)
    try:
        async with factory() as session:
            await _duplicate_for_scale(session, factor)
    finally:
        await engine.dispose()
    return target_name


def _print_eval_summary(report: EvalRunReport) -> None:
    metrics = report.metrics
    print(
        f"{report.report_type.upper()} {report.db_name} "
        f"R@1={metrics.recall_at_1:.3f} R@3={metrics.recall_at_3:.3f} "
        f"R@5={metrics.recall_at_5:.3f} R@10={metrics.recall_at_10:.3f} "
        f"MRR={metrics.mrr:.3f} p50={metrics.latency_p50_ms:.1f}ms "
        f"p95={metrics.latency_p95_ms:.1f}ms payload≈{metrics.mean_result_payload_tokens:.1f} tok"
    )


def _print_sweep_summary(report: SweepReport) -> None:
    print(
        f"SWEEP {report.db_name} baseline R@5={report.baseline_metrics.recall_at_5:.3f} "
        f"MRR={report.baseline_metrics.mrr:.3f}"
    )
    if report.recommended is None:
        print("No non-regressing candidate beat the baseline.")
        return
    best = report.recommended
    print(
        f"Recommended: {best.name} R@5={best.metrics.recall_at_5:.3f} "
        f"MRR={best.metrics.mrr:.3f} p95={best.metrics.latency_p95_ms:.1f}ms"
    )


async def _run_async(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db_url = args.db_url or settings.db_url
    qa_name = args.qa_set_name or "memory-m2-live"
    engine, factory = await _open_session_factory(db_url)
    try:
        async with factory() as session:
            qa_set, qa_path = await build_or_load_qa_set(
                session,
                db_url=db_url,
                name=qa_name,
                qa_size=args.qa_size,
                seed=args.seed,
                generator_provider=args.generator_provider,
                generator_model=args.generator_model,
                force_regenerate=args.regenerate_qa,
            )
            root = _artifact_root(db_url)
            if args.mode == "generate":
                print(f"QA set written: {qa_path}")
                return 0

            baseline = await evaluate_qa_set(
                session,
                db_url=db_url,
                qa_set=qa_set,
                cfg=get_retrieval_config(),
                label=args.label,
                report_type="baseline",
            )
            baseline_path = _report_path(root, args.label, "baseline")
            baseline_path.write_text(baseline.model_dump_json(indent=2))
            _print_eval_summary(baseline)
            if args.mode == "baseline":
                print(f"Report: {baseline_path}")
                return 0

            sweep = await run_sweep(
                session,
                db_url=db_url,
                qa_set=qa_set,
                label=args.label,
            )
            sweep_path = _report_path(root, args.label, "sweep")
            sweep_path.write_text(sweep.model_dump_json(indent=2))
            _print_sweep_summary(sweep)
            if args.mode == "sweep":
                print(f"Report: {sweep_path}")
                return 0

        scale_path_str: str | None = None
        if args.mode in {"scale", "full"}:
            target_db_url = args.target_db_url or re.sub(
                r"/([^/?]+)$",
                "/artemis_test_memory_m2",
                db_url,
            )
            await prepare_scaled_database(
                source_db_url=db_url,
                target_db_url=target_db_url,
                factor=args.scale_factor,
            )
            scale_engine, scale_factory = await _open_session_factory(target_db_url)
            try:
                async with scale_factory() as scale_session:
                    scale_report = await evaluate_qa_set(
                        scale_session,
                        db_url=target_db_url,
                        qa_set=qa_set,
                        cfg=get_retrieval_config(),
                        label=f"{args.label}-scale",
                        report_type="scale",
                    )
                    scale_report.notes.append(
                        f"Scaled corpus duplicated to ~{args.scale_factor}x using cloned scopes "
                        "(observations/observation_embeddings/entities/relations)."
                    )
                    scale_root = _artifact_root(target_db_url)
                    scale_path = _report_path(scale_root, args.label, "scale")
                    scale_path.write_text(scale_report.model_dump_json(indent=2))
                    scale_path_str = str(scale_path)
                    _print_eval_summary(scale_report)
            finally:
                await scale_engine.dispose()
            if args.mode == "scale":
                print(f"Scale report: {scale_path_str}")
                return 0

        full = FullRunReport(
            db_name=_database_name(db_url),
            qa_set_path=str(qa_path),
            baseline_report_path=str(baseline_path),
            sweep_report_path=str(sweep_path),
            scale_report_path=scale_path_str,
            created_at=datetime.now(UTC),
        )
        full_path = _report_path(root, args.label, "full")
        full_path.write_text(full.model_dump_json(indent=2))
        print(f"Full report: {full_path}")
        return 0
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Memory M2 retrieval eval / tuning harness.")
    parser.add_argument(
        "--mode",
        choices=["generate", "baseline", "sweep", "scale", "full"],
        default="full",
    )
    parser.add_argument("--db-url", default=None, help="Override ARTEMIS_DB_URL.")
    parser.add_argument("--target-db-url", default=None, help="Scale-test target database URL.")
    parser.add_argument("--qa-set-name", default="memory-m2-live", help="Persisted QA set name.")
    parser.add_argument("--qa-size", type=int, default=36, help="Number of QA queries to build.")
    parser.add_argument("--seed", type=int, default=20260613, help="Deterministic sampling seed.")
    parser.add_argument(
        "--generator-provider",
        default="claude-code",
        help="Provider used for synthetic query generation.",
    )
    parser.add_argument(
        "--generator-model",
        default=None,
        help="Optional model override for synthetic query generation.",
    )
    parser.add_argument(
        "--regenerate-qa",
        action="store_true",
        help="Force QA-set regeneration even if a matching one exists locally.",
    )
    parser.add_argument("--scale-factor", type=int, default=10, help="Target corpus multiplier.")
    parser.add_argument("--label", default="memory-m2", help="Report label stem.")
    return parser


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_async(args)))
