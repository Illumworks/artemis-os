"""Tests for Phase B2: embedding service, upsert_embedding, embed-on-write paths.

Tests that require a DB use the db_session fixture from conftest.
Tests that require sentence-transformers to load the actual model are marked
with @pytest.mark.slow to allow skipping in fast mode (model download is ~90MB).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.embeddings import (
    EmbeddingProvider,
    MiniLMProvider,
    _DIMS,
    _MODEL_VERSION,
    get_default_provider,
)
from artemis.memory.models import MemoryEmbedding
from artemis.memory.schemas import Scope, Source
from artemis.memory.store import upsert_embedding, write_drawer, write_observation

# ── EmbeddingProvider protocol ────────────────────────────────────────────────


def test_embedding_provider_is_protocol() -> None:
    import inspect

    assert inspect.isclass(EmbeddingProvider)


def test_miniml_provider_implements_protocol() -> None:
    provider = MiniLMProvider()
    assert isinstance(provider, EmbeddingProvider)


def test_miniml_provider_model_version_format() -> None:
    provider = MiniLMProvider()
    assert "@" in provider.model_version
    assert provider.model_version == _MODEL_VERSION


def test_miniml_provider_dims() -> None:
    provider = MiniLMProvider()
    assert provider.dims == _DIMS
    assert provider.dims == 384


def test_get_default_provider_returns_embedding_provider() -> None:
    prov = get_default_provider()
    assert isinstance(prov, EmbeddingProvider)


def test_get_default_provider_is_singleton() -> None:
    p1 = get_default_provider()
    p2 = get_default_provider()
    assert p1 is p2


# ── Mock provider for DB tests ────────────────────────────────────────────────


class MockProvider:
    """Deterministic mock provider — no model download required."""

    def __init__(self, dims: int = 384, fail: bool = False) -> None:
        self._dims = dims
        self._fail = fail
        self._model_version = "mock@1"

    async def embed(self, text: str) -> list[float]:
        if self._fail:
            raise RuntimeError("mock embedding failure")
        # Deterministic: hash of text normalised to unit sphere
        import hashlib

        digest = hashlib.sha256(text.encode()).digest()
        raw = [float(b) / 255.0 for b in digest[: self._dims]]
        norm = sum(x**2 for x in raw) ** 0.5 or 1.0
        return [x / norm for x in raw]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def dims(self) -> int:
        return self._dims


def _mock_provider(fail: bool = False) -> MockProvider:
    return MockProvider(dims=_DIMS, fail=fail)


# ── upsert_embedding ─────────────────────────────────────────────────────────


async def test_upsert_embedding_inserts_row(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_embedding(db_session, "observation", 999, "test@1", [0.1] * 384)
    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "observation",
            MemoryEmbedding.target_id == 999,
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.model_version == "test@1"


async def test_upsert_embedding_is_idempotent(db_session: AsyncSession) -> None:
    vec1 = [0.1] * 384
    vec2 = [0.9] * 384
    async with db_session.begin():
        await upsert_embedding(db_session, "drawer", 1, "test@1", vec1)
    async with db_session.begin():
        await upsert_embedding(db_session, "drawer", 1, "test@1", vec2)
    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "drawer",
            MemoryEmbedding.target_id == 1,
            MemoryEmbedding.model_version == "test@1",
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1  # updated in place, not duplicated


async def test_upsert_embedding_different_models_are_distinct(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_embedding(db_session, "observation", 1, "modelA@1", [0.1] * 384)
        await upsert_embedding(db_session, "observation", 1, "modelB@1", [0.2] * 384)
    result = await db_session.execute(
        select(MemoryEmbedding).where(MemoryEmbedding.target_id == 1)
    )
    rows = result.scalars().all()
    assert len(rows) == 2


# ── embed-on-write ────────────────────────────────────────────────────────────


_SCOPE = Scope(scope_kind="workspace", scope_id="b2-test")
_SOURCE = Source(source_kind="test")


async def test_write_drawer_stores_embedding(db_session: AsyncSession) -> None:
    provider = _mock_provider()
    async with db_session.begin():
        drawer = await write_drawer(
            db_session, _SCOPE, "embed test content", _SOURCE, embedding_provider=provider
        )
    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "drawer",
            MemoryEmbedding.target_id == drawer.id,
        )
    )
    emb = result.scalar_one_or_none()
    assert emb is not None
    assert emb.model_version == provider.model_version


async def test_write_observation_stores_embedding(db_session: AsyncSession) -> None:
    provider = _mock_provider()
    async with db_session.begin():
        obs = await write_observation(
            db_session, _SCOPE, "embed test observation", embedding_provider=provider
        )
    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "observation",
            MemoryEmbedding.target_id == obs.id,
        )
    )
    emb = result.scalar_one_or_none()
    assert emb is not None
    assert emb.model_version == provider.model_version


async def test_write_drawer_embedding_failure_does_not_block_write(
    db_session: AsyncSession,
) -> None:
    """If embedding fails, the drawer row is still written (lossless)."""
    provider = _mock_provider(fail=True)
    async with db_session.begin():
        drawer = await write_drawer(
            db_session, _SCOPE, "fail-embed content", _SOURCE, embedding_provider=provider
        )
    assert drawer.id > 0
    # No embedding row expected
    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "drawer",
            MemoryEmbedding.target_id == drawer.id,
        )
    )
    assert result.scalar_one_or_none() is None


async def test_write_observation_embedding_failure_does_not_block_write(
    db_session: AsyncSession,
) -> None:
    provider = _mock_provider(fail=True)
    async with db_session.begin():
        obs = await write_observation(
            db_session, _SCOPE, "fail-embed obs", embedding_provider=provider
        )
    assert obs.id > 0
    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "observation",
            MemoryEmbedding.target_id == obs.id,
        )
    )
    assert result.scalar_one_or_none() is None


async def test_write_drawer_duplicate_does_not_create_duplicate_embedding(
    db_session: AsyncSession,
) -> None:
    """Same content written twice → one drawer, one embedding row."""
    provider = _mock_provider()
    async with db_session.begin():
        d1 = await write_drawer(
            db_session, _SCOPE, "shared content", _SOURCE, embedding_provider=provider
        )
    async with db_session.begin():
        d2 = await write_drawer(
            db_session, _SCOPE, "shared content", _SOURCE, embedding_provider=provider
        )
    assert d1.id == d2.id
    result = await db_session.execute(
        select(MemoryEmbedding).where(
            MemoryEmbedding.target_table == "drawer",
            MemoryEmbedding.target_id == d1.id,
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
