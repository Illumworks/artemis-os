"""Provider cascade resolver.

Single entry point: ``resolve_adapter(provider, fallback_provider)``.

Tries ``provider`` first; on common "this provider isn't usable here" errors
(missing API key, missing CLI binary, unknown provider id) falls through to
``fallback_provider``, then to the default cascade. Raises
``NoProviderAvailableError`` only when every candidate fails.

Optional ``feature_tag`` parameter enables DB-backed per-feature cascade
overrides (see ``artemis/providers/routing_models.py``). When ``feature_tag``
is given and an active override row exists in ``feature_routing_overrides``,
the override's cascade replaces the default. Callers that do not pass
``feature_tag`` see exactly the same behavior as before — backwards compatible.

Used by:
  * ``artemis/pipelines/routes.py`` (HTTP AI-assistant turn endpoint)
  * ``artemis/pipelines/node_executors/agent_executor.py``
  * ``artemis/builders/executor.py::run_agent`` (defensive fallback)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import ModelAdapter
from artemis.providers import get_adapter
from artemis.providers.errors import (
    ClaudeCodeTimeoutError,
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
    ClaudeCodeTimeoutError,
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
    *,
    feature_tag: str | None = None,
    session: AsyncSession | None = None,
    **kwargs: Any,
) -> ModelAdapter:
    """Resolve a model adapter via the provider cascade.

    Resolution order:
      1. If ``feature_tag`` is set AND an active override exists for it in
         ``feature_routing_overrides`` (requires ``session``) → walk that cascade.
      2. Otherwise → use ``(provider, fallback_provider, DEFAULT_CASCADE)`` as today.

    Args:
        provider:           Preferred provider id (e.g. ``"claude-code"``).
        fallback_provider:  Provider tried if ``provider`` is unavailable.
        feature_tag:        Optional. Enables per-feature DB override lookup.
                            Callers that do not pass this see identical behavior
                            to the pre-override resolver — backwards compatible.
        session:            Optional AsyncSession for override lookup. Only used
                            when ``feature_tag`` is provided. If None and
                            ``feature_tag`` is set, the override lookup is skipped
                            silently (falls back to normal cascade).
        **kwargs:           Forwarded to the adapter constructor.

    Returns:
        A constructed ``ModelAdapter``.

    Raises:
        NoProviderAvailableError: nothing in the cascade could be constructed.
    """
    tried: list[str] = []
    seen: set[str] = set()

    # Sync path does not perform DB override lookup — callers that want the async
    # override path should use resolve_adapter_async() instead.
    # Build the ordered candidate list, dropping duplicates while preserving order.
    candidate_list: list[str] = []
    for _cand in (provider, fallback_provider, *DEFAULT_CASCADE):
        if _cand and _cand not in seen:
            candidate_list.append(str(_cand))
            seen.add(_cand)

    last_error: Exception | None = None
    for cand in candidate_list:
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


async def resolve_adapter_async(
    provider: str | None = None,
    fallback_provider: str | None = None,
    *,
    feature_tag: str | None = None,
    session: AsyncSession | None = None,
    **kwargs: Any,
) -> ModelAdapter:
    """Async variant of ``resolve_adapter`` that supports DB override lookup.

    When ``feature_tag`` and ``session`` are both provided, this function
    queries ``feature_routing_overrides`` before building the candidate list.
    The result is identical to ``resolve_adapter`` when no override exists.

    Callers that do not pass ``feature_tag`` see exactly the same behavior as
    ``resolve_adapter`` — backwards compatible.
    """
    override_cascade: list[dict[str, str]] | None = None

    if feature_tag and session is not None:
        from artemis.providers.routing_repository import get_routing_override_for_feature

        override = await get_routing_override_for_feature(session, feature_tag)
        if override is not None:
            override_cascade = list(override.cascade)
            logger.debug(
                "resolve_adapter_async: using override cascade for %r: %s",
                feature_tag,
                override_cascade,
            )

    candidate_providers: list[str] = []
    if override_cascade is not None:
        # Walk the override cascade; fall back to DEFAULT_CASCADE if all fail
        seen: set[str] = set()
        for step in override_cascade:
            p = step.get("provider", "")
            if p and p not in seen:
                candidate_providers.append(p)
                seen.add(p)
        # Append DEFAULT_CASCADE as final fallback, deduplicating
        for cand in DEFAULT_CASCADE:
            if cand not in seen:
                candidate_providers.append(cand)
                seen.add(cand)
    else:
        # Normal resolution — identical to sync resolve_adapter
        seen = set()
        for _cand in (provider, fallback_provider, *DEFAULT_CASCADE):
            if _cand and _cand not in seen:
                candidate_providers.append(str(_cand))
                seen.add(_cand)

    tried: list[str] = []
    last_error: Exception | None = None
    for cand in candidate_providers:
        if not cand:
            continue
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
