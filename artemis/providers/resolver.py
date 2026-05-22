"""Provider cascade resolver.

Single entry point: ``resolve_adapter(provider, fallback_provider)``.

Tries ``provider`` first; on common "this provider isn't usable here" errors
(missing API key, missing CLI binary, unknown provider id) falls through to
``fallback_provider``, then to the default cascade. Raises
``NoProviderAvailableError`` only when every candidate fails.

Used by:
  * ``artemis/pipelines/routes.py`` (HTTP AI-assistant turn endpoint)
  * ``artemis/pipelines/node_executors/agent_executor.py``
  * ``artemis/builders/executor.py::run_agent`` (defensive fallback)
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.client import ModelAdapter
from artemis.providers import get_adapter
from artemis.providers.errors import (
    MissingApiKeyError,
    MissingCliBinaryError,
    UnknownProviderError,
)

logger = logging.getLogger(__name__)

# Fallback cascade if neither the caller's provider nor its declared fallback
# is available. Ordered cheapest-/most-likely-available first.
DEFAULT_CASCADE: tuple[str, ...] = ("claude-code", "codex", "lm-studio", "anthropic")

# Errors that mean "this provider can't be constructed in this env" — safe to
# fall through. Anything else (e.g., a programming error) propagates.
_FALLTHROUGH_ERRORS: tuple[type[Exception], ...] = (
    MissingApiKeyError,
    MissingCliBinaryError,
    UnknownProviderError,
)


class NoProviderAvailableError(RuntimeError):
    """Raised when neither the requested provider, the declared fallback, nor
    any default-cascade provider can be constructed."""


def resolve_adapter(
    provider: str | None = None,
    fallback_provider: str | None = None,
    **kwargs: Any,
) -> ModelAdapter:
    """Resolve a model adapter via the provider cascade.

    Args:
        provider:           Preferred provider id (e.g. ``"claude-code"``).
        fallback_provider:  Provider tried if ``provider`` is unavailable.
        **kwargs:           Forwarded to the adapter constructor.

    Returns:
        A constructed ``ModelAdapter``.

    Raises:
        NoProviderAvailableError: nothing in the cascade could be constructed.
    """
    tried: list[str] = []
    seen: set[str] = set()

    # Build the ordered candidate list, dropping duplicates while preserving order.
    candidates: list[str] = []
    for cand in (provider, fallback_provider, *DEFAULT_CASCADE):
        if cand and cand not in seen:
            candidates.append(cand)
            seen.add(cand)

    last_error: Exception | None = None
    for cand in candidates:
        try:
            adapter = get_adapter(cand, **kwargs)
        except _FALLTHROUGH_ERRORS as exc:
            logger.debug("Provider %r unavailable: %s", cand, exc)
            tried.append(cand)
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001 — best-effort cascade
            logger.warning("Provider %r raised unexpected error: %s", cand, exc)
            tried.append(cand)
            last_error = exc
            continue
        return adapter

    raise NoProviderAvailableError(
        f"No LLM provider available. Tried: {', '.join(tried) or '(none)'}. "
        f"Last error: {last_error!r}"
    )
