"""Tests for the backfill coroutine.

Uses the shared db_session / _engine from conftest for simplicity.
Each test truncates the DB via db_session fixture.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.backfill import backfill_embeddings
from artemis.memory.models import MemoryEmbedding
from artemis.memory.schemas import Scope, Source
from artemis.memory.store import write_drawer, write_observation
from artemis.memory.tests.conftest import _engine
from artemis.memory.tests.test_b2_embeddings import MockProvider

_SCOPE = Scope(scope_kind="global", scope_id="backfill-test")
_SOURCE = Source(source_kind="test")


async def test_backfill_embeds_missing_drawers(db_session: AsyncSession) -> None:
    """Drawers written without a provider get picked up by backfill."""
    # Write without embedding (no provider)
    async with db_session.begin():
        d1 = await write_drawer(db_session, _SCOPE, "drawer needs backfill", _SOURCE)
        d2 = await write_drawer(db_session, _SCOPE, "another drawer for backfill", _SOURCE)

    provider = MockProvider()
    n = await backfill_embeddings(_engine, batch_size=10, provider=provider)
    assert n >= 2

    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "drawer",
            MemoryEmbedding.target_id.in_([d1.id, d2.id]),
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 2


async def test_backfill_embeds_missing_observations(db_session: AsyncSession) -> None:
    async with db_session.begin():
        o1 = await write_observation(db_session, _SCOPE, "obs needs backfill")
        o2 = await write_observation(db_session, _SCOPE, "another obs for backfill")

    provider = MockProvider()
    n = await backfill_embeddings(_engine, batch_size=10, provider=provider)
    assert n >= 2

    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "observation",
            MemoryEmbedding.target_id.in_([o1.id, o2.id]),
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 2


async def test_backfill_is_idempotent(db_session: AsyncSession) -> None:
    """Running backfill twice does not create duplicate embeddings."""
    async with db_session.begin():
        await write_drawer(db_session, _SCOPE, "idempotent backfill", _SOURCE)

    provider = MockProvider()
    n1 = await backfill_embeddings(_engine, batch_size=10, provider=provider)
    n2 = await backfill_embeddings(_engine, batch_size=10, provider=provider)

    # Second run should find nothing to backfill
    assert n1 >= 1
    assert n2 == 0

    result = await db_session.execute(
        select(MemoryEmbedding).where(MemoryEmbedding.target_table == "drawer")
    )
    rows = result.scalars().all()
    model_versions = {r.model_version for r in rows}
    # All rows for same model version should be unique per target_id
    pairs = [(r.target_id, r.model_version) for r in rows]
    assert len(pairs) == len(set(pairs))


async def test_backfill_skips_already_embedded_rows(db_session: AsyncSession) -> None:
    """Rows that already have an embedding are skipped."""
    provider = MockProvider()
    async with db_session.begin():
        # Write with embedding (already embedded)
        d_with = await write_drawer(
            db_session, _SCOPE, "already embedded", _SOURCE, embedding_provider=provider
        )
        # Write without embedding
        d_without = await write_drawer(db_session, _SCOPE, "needs embedding", _SOURCE)

    n = await backfill_embeddings(_engine, batch_size=10, provider=provider)
    # Only the un-embedded drawer should be processed
    assert n == 1


async def test_backfill_graceful_with_failing_provider(db_session: AsyncSession) -> None:
    """A failing provider does not crash backfill; it logs and exits gracefully."""
    async with db_session.begin():
        await write_drawer(db_session, _SCOPE, "fail backfill", _SOURCE)

    failing_provider = MockProvider(fail=True)
    # Should not raise
    n = await backfill_embeddings(_engine, batch_size=10, provider=failing_provider)
    assert n == 0
