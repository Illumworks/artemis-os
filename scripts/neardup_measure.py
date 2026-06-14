"""M1b/M1c measurement driver — supersession-aware + series-aware retrieval eval.

Runs the *same* QA set against any database with the *same* retrieval code so a
before/after comparison is apples-to-apples.  Two layers of hit credit:

1. Supersession-aware: a hit is credited when the target observation OR the
   active observation that (transitively) superseded it appears in the top-k.
   Without this, collapsing a clone that happened to be a QA target would look
   like a regression even though the canonical now carries the answer.

2. Series-aware (M1c, default ON): a hit is also credited when any returned
   result shares the target's ``_series_key``.  This prevents the harness from
   penalising "current momentum" queries whose ground-truth ID was frozen on an
   older snapshot while the system correctly returns a newer one.

Usage:
    uv run python scripts/neardup_measure.py --db-url <url> --label before
    uv run python scripts/neardup_measure.py --db-url <url> --label after --config confirmed_bias
    uv run python scripts/neardup_measure.py --db-url <url> --label strict --no-series-aware
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from artemis.db import attach_pgvector_codec
from artemis.memory.models import MemoryObservation
from artemis.memory.retrieval import (
    RetrievalConfig,
    RetrievalWeights,
    ScoreFeatureWeights,
    _series_key,
    get_retrieval_config,
    search_observations,
)
from artemis.memory.schemas import Scope

_QA_PATH = (
    Path.home() / ".artemis" / "memory-eval" / "artemis_os" / "qa_sets" / "memory-m2-live.json"
)


def _confirmed_bias(base: RetrievalConfig) -> RetrievalConfig:
    """The 'confirmed_bias' candidate from the M2 sweep (runner._candidate_configs)."""
    return RetrievalConfig(
        weights=RetrievalWeights(
            fts=0.25,
            semantic=0.35,
            recency=0.20,
            score=0.20,
            graph_proximity=base.weights.graph_proximity,
        ),
        score_features=ScoreFeatureWeights(relevance=0.25, hits=0.10, quality=0.25, confirmed=0.40),
        top_k=base.top_k,
        recency_decay_days=base.recency_decay_days,
        series_collapse=base.series_collapse,
    )


async def _build_supersession_head(session: AsyncSession) -> dict[int, int]:
    """Map every observation id → the active head of its supersession chain."""
    rows = list(
        (await session.execute(select(MemoryObservation.id, MemoryObservation.superseded_by))).all()
    )
    parent = {int(i): (int(s) if s is not None else None) for i, s in rows}

    def head(oid: int) -> int:
        seen: set[int] = set()
        cur = oid
        while parent.get(cur) is not None and cur not in seen:
            seen.add(cur)
            cur = parent[cur]  # type: ignore[assignment]
        return cur

    return {oid: head(oid) for oid in parent}


async def _load_content_map(session: AsyncSession) -> dict[int, str]:
    """Map observation id → content (for series-key lookup)."""
    rows = list(
        (await session.execute(select(MemoryObservation.id, MemoryObservation.content))).all()
    )
    return {int(row[0]): str(row[1]) for row in rows}


async def run(
    db_url: str,
    label: str,
    config_name: str,
    *,
    series_aware: bool = True,
    top_k_override: int | None = None,
    series_collapse_override: bool | None = None,
) -> None:
    qa = json.loads(_QA_PATH.read_text())
    queries = qa["queries"]

    engine = create_async_engine(db_url, pool_pre_ping=True)
    attach_pgvector_codec(engine)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    base = get_retrieval_config()
    cfg = _confirmed_bias(base) if config_name == "confirmed_bias" else base
    if top_k_override is not None:
        cfg = cfg.model_copy(update={"top_k": top_k_override})
    if series_collapse_override is not None:
        cfg = cfg.model_copy(update={"series_collapse": series_collapse_override})

    ranks: list[int | None] = []
    latencies: list[float] = []
    miss_ids: list[int] = []

    async with session_factory() as session:
        head = await _build_supersession_head(session)
        content_map = await _load_content_map(session) if series_aware else {}

        for q in queries:
            scope_set = [Scope(**s) for s in q["scope_set"]]
            target_obs_id = q["observation_id"]
            target_head = head.get(target_obs_id, target_obs_id)
            target_series: tuple[str, str] | None = None
            if series_aware:
                target_content = content_map.get(target_obs_id)
                if target_content is None:
                    # target may be superseded — look up via head
                    target_content = content_map.get(target_head)
                if target_content is not None:
                    target_series = _series_key(target_content)

            started = time.perf_counter()
            hits = await search_observations(
                session, scope_set, q["query"], limit=10, cfg=cfg, record_usage=False
            )
            latencies.append((time.perf_counter() - started) * 1000.0)

            rank: int | None = None
            for i, h in enumerate(hits, start=1):
                result_head = head.get(h.id, h.id)
                if result_head == target_head:
                    rank = i
                    break
                if series_aware and target_series is not None:
                    result_key = _series_key(h.content)
                    if result_key == target_series:
                        rank = i
                        break
            ranks.append(rank)
            if rank is None:
                miss_ids.append(target_obs_id)

    await engine.dispose()

    n = len(ranks)

    def rec(k: int) -> float:
        return round(sum(1 for r in ranks if r is not None and r <= k) / n, 4)

    mrr = round(sum(1.0 / r for r in ranks if r) / n, 4)
    series_flag = "series-aware" if series_aware else "strict"
    print(
        f"[{label}/{config_name}/{series_flag}] n={n} R@1={rec(1)} R@3={rec(3)} R@5={rec(5)} "
        f"R@10={rec(10)} MRR={mrr} p50={round(median(latencies), 1)}ms "
        f"top_k={cfg.top_k} collapse={cfg.series_collapse}"
    )
    print(f"  miss target ids: {sorted(miss_ids)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db-url", required=True)
    p.add_argument("--label", default="run")
    p.add_argument("--config", default="baseline", choices=["baseline", "confirmed_bias"])
    p.add_argument(
        "--no-series-aware",
        action="store_true",
        help="Use strict (exact-id) hit scoring — pre-M1c behaviour.",
    )
    p.add_argument(
        "--top-k", type=int, default=None, help="Override top_k (e.g. 50 to simulate pre-M1c)."
    )
    p.add_argument(
        "--no-collapse",
        action="store_true",
        help="Force series_collapse=False regardless of config.",
    )
    args = p.parse_args()
    asyncio.run(
        run(
            args.db_url,
            args.label,
            args.config,
            series_aware=not args.no_series_aware,
            top_k_override=args.top_k,
            series_collapse_override=False if args.no_collapse else None,
        )
    )


if __name__ == "__main__":
    main()
