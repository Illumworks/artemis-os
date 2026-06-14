"""M1b measurement driver — supersession-aware retrieval eval, fixed QA set.

Runs the *same* QA set against any database with the *same* retrieval code so a
before/after near-duplicate-consolidation comparison is apples-to-apples. The one
addition over the M2 harness's evaluate_qa_set: a hit is credited when the target
observation OR the active observation that (transitively) superseded it appears in
the top-k. Without this, collapsing a clone that happened to be a QA target would
look like a regression even though the canonical now carries the answer.

Usage:
    uv run python scripts/neardup_measure.py --db-url <url> --label before
    uv run python scripts/neardup_measure.py --db-url <url> --label after --config confirmed_bias
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
    get_retrieval_config,
    search_observations,
)
from artemis.memory.schemas import Scope

_QA_PATH = Path.home() / ".artemis" / "memory-eval" / "artemis_os" / "qa_sets" / "memory-m2-live.json"


def _confirmed_bias(base: RetrievalConfig) -> RetrievalConfig:
    """The 'confirmed_bias' candidate from the M2 sweep (runner._candidate_configs)."""
    return RetrievalConfig(
        weights=RetrievalWeights(fts=0.25, semantic=0.35, recency=0.20, score=0.20,
                                 graph_proximity=base.weights.graph_proximity),
        score_features=ScoreFeatureWeights(relevance=0.25, hits=0.10, quality=0.25, confirmed=0.40),
        top_k=base.top_k,
        recency_decay_days=base.recency_decay_days,
    )


async def _build_supersession_head(session: AsyncSession) -> dict[int, int]:
    """Map every observation id → the active head of its supersession chain."""
    rows = list((await session.execute(
        select(MemoryObservation.id, MemoryObservation.superseded_by)
    )).all())
    parent = {int(i): (int(s) if s is not None else None) for i, s in rows}

    def head(oid: int) -> int:
        seen: set[int] = set()
        cur = oid
        while parent.get(cur) is not None and cur not in seen:
            seen.add(cur)
            cur = parent[cur]  # type: ignore[assignment]
        return cur

    return {oid: head(oid) for oid in parent}


async def run(db_url: str, label: str, config_name: str) -> None:
    qa = json.loads(_QA_PATH.read_text())
    queries = qa["queries"]

    engine = create_async_engine(db_url, pool_pre_ping=True)
    attach_pgvector_codec(engine)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    base = get_retrieval_config()
    cfg = _confirmed_bias(base) if config_name == "confirmed_bias" else base

    ranks: list[int | None] = []
    latencies: list[float] = []
    miss_ids: list[int] = []

    async with Session() as session:
        head = await _build_supersession_head(session)
        for q in queries:
            scope_set = [Scope(**s) for s in q["scope_set"]]
            target_head = head.get(q["observation_id"], q["observation_id"])
            started = time.perf_counter()
            hits = await search_observations(
                session, scope_set, q["query"], limit=10, cfg=cfg, record_usage=False
            )
            latencies.append((time.perf_counter() - started) * 1000.0)
            rank: int | None = None
            for i, h in enumerate(hits, start=1):
                if head.get(h.id, h.id) == target_head:
                    rank = i
                    break
            ranks.append(rank)
            if rank is None:
                miss_ids.append(q["observation_id"])

    await engine.dispose()

    n = len(ranks)
    def rec(k: int) -> float:
        return round(sum(1 for r in ranks if r is not None and r <= k) / n, 4)
    mrr = round(sum(1.0 / r for r in ranks if r) / n, 4)
    print(f"[{label}/{config_name}] n={n} R@1={rec(1)} R@3={rec(3)} R@5={rec(5)} "
          f"R@10={rec(10)} MRR={mrr} p50={round(median(latencies),1)}ms")
    print(f"  miss target ids: {sorted(miss_ids)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db-url", required=True)
    p.add_argument("--label", default="run")
    p.add_argument("--config", default="baseline", choices=["baseline", "confirmed_bias"])
    args = p.parse_args()
    asyncio.run(run(args.db_url, args.label, args.config))


if __name__ == "__main__":
    main()
