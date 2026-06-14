from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.eval.runner import (
    CorpusFingerprint,
    EvalMetrics,
    QueryRunResult,
    _compute_metrics,
    _same_fingerprint,
)
from artemis.memory.models import MemoryObservation
from artemis.memory.retrieval import search_observations
from artemis.memory.schemas import Scope
from artemis.memory.store import write_observation
from artemis.memory.tests.test_b2_embeddings import MockProvider


def test_compute_metrics_rollup() -> None:
    results = [
        QueryRunResult(
            query_id="a",
            observation_id=1,
            query_kind="keyword",
            query="one",
            hit_rank=1,
            hit_in_top_1=True,
            hit_in_top_3=True,
            hit_in_top_5=True,
            hit_in_top_10=True,
            latency_ms=10.0,
            result_count=3,
            embedding_calls=1,
            embedding_input_tokens=5,
            result_payload_tokens=25,
            top_result_ids=[1, 2, 3],
        ),
        QueryRunResult(
            query_id="b",
            observation_id=2,
            query_kind="semantic",
            query="two",
            hit_rank=4,
            hit_in_top_1=False,
            hit_in_top_3=False,
            hit_in_top_5=True,
            hit_in_top_10=True,
            latency_ms=20.0,
            result_count=5,
            embedding_calls=1,
            embedding_input_tokens=7,
            result_payload_tokens=35,
            top_result_ids=[9, 8, 7, 2],
        ),
    ]

    metrics = _compute_metrics(results)

    assert isinstance(metrics, EvalMetrics)
    assert metrics.query_count == 2
    assert metrics.recall_at_1 == 0.5
    assert metrics.recall_at_3 == 0.5
    assert metrics.recall_at_5 == 1.0
    assert metrics.recall_at_10 == 1.0
    assert metrics.mrr == pytest.approx(0.625)
    assert metrics.latency_p50_ms == 15.0
    assert metrics.latency_p95_ms == 20.0
    assert metrics.mean_embedding_input_tokens == 6.0
    assert metrics.mean_result_payload_tokens == 30.0


def test_same_fingerprint_ignores_capture_timestamp() -> None:
    common = dict(
        db_name="artemis_os",
        observation_count=10,
        active_observation_count=10,
        drawer_count=4,
        raw_input_count=7,
        scope_count=3,
        observation_scope_count=10,
        observation_embedding_count=10,
        entity_count=2,
        relation_count=1,
        raw_input_chain_ok=True,
        raw_input_chain_rows=7,
    )
    first = CorpusFingerprint(
        **common,
        captured_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )
    second = CorpusFingerprint(
        **common,
        captured_at=datetime(2026, 6, 13, 12, 1, tzinfo=UTC),
    )

    assert _same_fingerprint(first, second) is True


async def test_search_observations_record_usage_false_is_read_only(
    db_session: AsyncSession,
) -> None:
    scope = Scope(scope_kind="workspace", scope_id="memory-m2-eval")
    provider = MockProvider()

    async with db_session.begin():
        obs = await write_observation(
            db_session,
            scope,
            "Florida literacy campaign emphasis",
            embedding_provider=provider,
        )

    before = (
        await db_session.execute(select(MemoryObservation).where(MemoryObservation.id == obs.id))
    ).scalar_one()
    before_hit_count = before.hit_count
    before_accessed_at = before.accessed_at

    await search_observations(
        db_session,
        [scope],
        "Florida literacy",
        provider=provider,
        record_usage=False,
    )
    await asyncio.sleep(0.05)
    await db_session.refresh(before)

    assert before.hit_count == before_hit_count
    assert before.accessed_at == before_accessed_at


async def test_search_observations_record_usage_true_still_updates(
    db_session: AsyncSession,
) -> None:
    scope = Scope(scope_kind="workspace", scope_id="memory-m2-eval-write")
    provider = MockProvider()

    async with db_session.begin():
        obs = await write_observation(
            db_session,
            scope,
            "Michigan district literacy push",
            embedding_provider=provider,
        )

    row = (
        await db_session.execute(select(MemoryObservation).where(MemoryObservation.id == obs.id))
    ).scalar_one()
    original_ts = row.accessed_at

    await search_observations(
        db_session,
        [scope],
        "Michigan literacy",
        provider=provider,
        record_usage=True,
    )
    await asyncio.sleep(0.1)
    await db_session.refresh(row)

    assert row.hit_count == 1
    assert row.accessed_at >= original_ts
