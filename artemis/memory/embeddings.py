"""Embedding service for the memory keystone.

Protocol: EmbeddingProvider — async embed / embed_batch + model_version + dims.
Default: MiniLMProvider using sentence-transformers all-MiniLM-L6-v2 (384 dims).
Swap: set ARTEMIS_EMBEDDING_PROVIDER=minilm (only option in V1).

Model is loaded lazily on first embed() call. Thread-safe via asyncio.Lock.
"""

from __future__ import annotations

# HuggingFace `tokenizers` forks worker processes by default and leaks
# multiprocessing semaphores in a long-lived web process; disable before any
# import path can pull tokenizers in (sentence_transformers → transformers →
# tokenizers).
import os

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from artemis.config import settings

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_MODEL_VERSION = "all-MiniLM-L6-v2@1"  # @N reserves slot for re-embed roll
_DIMS = 384


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface for embedding models. Implementations must be thread-safe."""

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def model_version(self) -> str: ...

    @property
    def dims(self) -> int: ...


class MiniLMProvider:
    """sentence-transformers all-MiniLM-L6-v2 — 384-dim, normalized output.

    Model is loaded once on first call (lazy). CPU-only by default; GPU is used
    automatically when sentence-transformers detects CUDA. Encoding is offloaded
    to a thread-pool executor to avoid blocking the async event loop.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def _load(self) -> Any:
        async with self._lock:
            if self._model is None:
                _logger.info("Loading embedding model %s (first call)", _MODEL_NAME)
                loop = asyncio.get_running_loop()
                self._model = await loop.run_in_executor(None, _load_model_sync, _MODEL_NAME)
                _logger.info("Embedding model loaded")
        return self._model

    async def embed(self, text: str) -> list[float]:
        model = await self._load()
        loop = asyncio.get_running_loop()
        result: Any = await loop.run_in_executor(None, _encode_sync, model, text)
        return [float(x) for x in result]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._load()
        loop = asyncio.get_running_loop()
        results: Any = await loop.run_in_executor(None, _encode_batch_sync, model, texts)
        return [[float(x) for x in row] for row in results]

    @property
    def model_version(self) -> str:
        return _MODEL_VERSION

    @property
    def dims(self) -> int:
        return _DIMS


def _load_model_sync(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    # Pin torch to one thread so intra-op workers don't leak in the web process.
    try:
        import torch

        torch.set_num_threads(1)
    except Exception:
        pass

    return SentenceTransformer(model_name)


def _encode_sync(model: Any, text: str) -> Any:
    return model.encode(text, normalize_embeddings=True)


def _encode_batch_sync(model: Any, texts: list[str]) -> Any:
    return model.encode(texts, normalize_embeddings=True)


# Module-level singleton — one provider per process.
_default_provider: EmbeddingProvider | None = None
_provider_lock = asyncio.Lock()


def get_default_provider() -> EmbeddingProvider:
    """Return the configured embedding provider (lazy-initialized singleton).

    Currently only 'minilm' is supported. Set ARTEMIS_EMBEDDING_PROVIDER to
    extend in a future slice.
    """
    global _default_provider
    if _default_provider is not None:
        return _default_provider

    provider_name = getattr(settings, "embedding_provider", "minilm")
    if provider_name == "minilm":
        _default_provider = MiniLMProvider()
    else:
        _logger.warning(
            "Unknown ARTEMIS_EMBEDDING_PROVIDER %r; falling back to minilm", provider_name
        )
        _default_provider = MiniLMProvider()

    return _default_provider
