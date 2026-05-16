"""Background embedding backfill for the memory keystone.

Finds drawers and observations that lack an embedding for the current model
version and embeds them in batches. Idempotent: running twice is safe.

CLI: uv run python -m artemis.memory.backfill
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from artemis.memory.models import MemoryDrawer, MemoryEmbedding, MemoryObservation
from artemis.memory.store import upsert_embedding

if TYPE_CHECKING:
    from artemis.memory.embeddings import EmbeddingProvider

_logger = logging.getLogger(__name__)

_TARGETS: list[
    tuple[Literal["drawer", "observation"], type[MemoryDrawer] | type[MemoryObservation]]
] = [
    ("drawer", MemoryDrawer),
    ("observation", MemoryObservation),
]


async def backfill_embeddings(
    engine: AsyncEngine,
    batch_size: int = 50,
    provider: EmbeddingProvider | None = None,
) -> int:
    """Embed all rows that are missing embeddings for the current model version.

    Args:
        engine: async engine; each batch runs in its own transaction.
        batch_size: rows to embed per batch (limits memory use during encoding).
        provider: embedding provider; defaults to get_default_provider().

    Returns:
        Total number of embeddings written.
    """
    from artemis.memory.embeddings import get_default_provider

    _provider = provider or get_default_provider()
    model_version = _provider.model_version
    total = 0

    for target_label, model_cls in _TARGETS:
        _logger.info("Backfilling %ss for model %s", target_label, model_version)
        total += await _backfill_table(
            engine, target_label, model_cls, model_version, _provider, batch_size
        )

    _logger.info("Backfill complete: %d embeddings written", total)
    return total


async def _backfill_table(
    engine: AsyncEngine,
    target_label: Literal["drawer", "observation"],
    model_cls: type[MemoryDrawer] | type[MemoryObservation],
    model_version: str,
    provider: EmbeddingProvider,
    batch_size: int,
) -> int:
    count = 0
    while True:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            # Find rows without a matching embedding
            existing_subq = (
                select(MemoryEmbedding.target_id)
                .where(
                    MemoryEmbedding.target_table == target_label,
                    MemoryEmbedding.model_version == model_version,
                )
                .scalar_subquery()
            )
            rows_result = await session.execute(
                select(model_cls.id, model_cls.content)
                .where(model_cls.id.notin_(existing_subq))
                .limit(batch_size)
            )
            rows = rows_result.all()

        if not rows:
            break

        ids = [int(r[0]) for r in rows]
        texts = [str(r[1]) for r in rows]

        try:
            vectors = await provider.embed_batch(texts)
        except Exception:
            _logger.warning(
                "embed_batch failed for %s ids %s; skipping batch", target_label, ids, exc_info=True
            )
            # Skip the whole batch to avoid an infinite loop on persistent failure.
            break

        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            for row_id, vector in zip(ids, vectors, strict=True):
                await upsert_embedding(session, target_label, row_id, model_version, vector)

        count += len(rows)
        _logger.debug("Backfilled %d %ss (running total %d)", len(rows), target_label, count)

        if len(rows) < batch_size:
            break

        await asyncio.sleep(0)  # yield between batches

    return count


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    async def _main() -> None:
        from artemis.db import engine

        n = await backfill_embeddings(engine)
        print(f"Done. {n} embeddings written.")

    asyncio.run(_main())
