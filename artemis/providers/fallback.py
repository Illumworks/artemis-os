"""Runtime provider fallback helper.

``complete_with_fallback`` wraps a single LLM completion call with a
transparent fallback to a second provider on transient runtime errors
(rate limits, connection errors, server-side 5xx).

Design constraints:
  - Hard non-retryable errors (4xx except 429) re-raise immediately — they
    indicate a bug in the request, not a transient provider blip.
  - Construction-time errors for the primary (MissingApiKeyError, etc.) fall
    through to the fallback so callers never need a two-phase try/except.
  - lm-studio is never injected anywhere in this module.
  - The function is a plain async def — no class, no state — so it is easy
    to test and import from any of the 5 call sites.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.providers.errors import (
    GeminiRateLimitError,
    MissingApiKeyError,
    MissingCliBinaryError,
    ProviderAPIError,
    UnknownProviderError,
)
from artemis.providers.registry import get_adapter
from artemis.providers.resolver import resolve_adapter_async

logger = logging.getLogger(__name__)

# Status codes that represent transient provider-side problems — safe to retry
# on the fallback provider.  Non-transient 4xx (400, 401, 403, 404, 422, etc.)
# are NOT in this set and will re-raise so the caller can see the real bug.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Construction-time errors that mean "this provider isn't available in this env".
_CONSTRUCTION_ERRORS: tuple[type[Exception], ...] = (
    MissingApiKeyError,
    MissingCliBinaryError,
    UnknownProviderError,
)

# Runtime errors that are network-level transients (not application errors).
_NETWORK_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    socket.gaierror,
    ConnectionError,
)


def _is_retryable(exc: Exception) -> bool:
    """Return True if exc represents a transient error safe to fall through."""
    if isinstance(exc, GeminiRateLimitError):
        return True
    if isinstance(exc, ProviderAPIError):
        return exc.status_code in _RETRYABLE_STATUS_CODES
    if isinstance(exc, _CONSTRUCTION_ERRORS):
        return True
    return bool(isinstance(exc, _NETWORK_ERRORS))


async def complete_with_fallback(
    request: CompletionRequest,
    *,
    primary: str,
    fallback: str = "claude-code",
    session: AsyncSession | None = None,
    feature_tag: str | None = None,
    serving_provider_out: list[str] | None = None,
    **adapter_kwargs: Any,
) -> CompletionResponse:
    """Call ``primary`` provider; on transient error fall through to ``fallback``.

    Parameters
    ----------
    request:
        The CompletionRequest to send.  The same object is forwarded to both
        providers — do not mutate it between attempts.
    primary:
        Provider id to try first (e.g. ``"gemini"``).
    fallback:
        Provider id to try on failure (default ``"claude-code"``).
        Must not be ``"lm-studio"``.
    session:
        Optional AsyncSession forwarded to ``resolve_adapter_async`` so that
        DB-backed routing overrides are honoured.
    feature_tag:
        Optional feature tag for DB-backed override lookup.
    serving_provider_out:
        Optional list; if provided, the id of the provider that actually served
        the request is appended.  Callers use this for accurate cost recording.
        E.g. ``out: list[str] = []; resp = await complete_with_fallback(...,
        serving_provider_out=out); provider_id = out[0]``.
    **adapter_kwargs:
        Forwarded verbatim to the adapter constructor (e.g. ``model``).

    Returns
    -------
    CompletionResponse
        The first successful response from either provider.

    Raises
    ------
    Exception
        Any non-retryable error from the primary provider re-raises immediately.
        If the fallback also fails, its exception propagates.
    ValueError
        If ``fallback`` is ``"lm-studio"`` (hard guard per architecture rules).
    """
    if fallback == "lm-studio":
        raise ValueError(
            "complete_with_fallback: lm-studio must not be used as a fallback provider. "
            "Use 'claude-code' instead."
        )

    # When primary IS the fallback (e.g. agent is already on claude-code),
    # just make one direct call — no retry path needed.
    if primary == fallback:
        adapter = await resolve_adapter_async(
            provider=primary,
            feature_tag=feature_tag,
            session=session,
            **adapter_kwargs,
        )
        response = await adapter.complete(request)
        if serving_provider_out is not None:
            serving_provider_out.append(primary)
        return response

    # ── Primary attempt ───────────────────────────────────────────────────────
    primary_adapter = None
    primary_err: Exception | None = None

    try:
        primary_adapter = get_adapter(primary, **adapter_kwargs)
    except _CONSTRUCTION_ERRORS as exc:
        logger.warning(
            "complete_with_fallback: primary %r unavailable at construction time (%s); "
            "falling through to fallback %r",
            primary,
            exc,
            fallback,
        )
        primary_err = exc

    if primary_adapter is not None:
        try:
            response = await primary_adapter.complete(request)
            if serving_provider_out is not None:
                serving_provider_out.append(primary)
            return response
        except Exception as exc:
            if _is_retryable(exc):
                logger.warning(
                    "complete_with_fallback: primary %r failed with retryable error "
                    "(%s: %s); falling through to fallback %r",
                    primary,
                    type(exc).__name__,
                    exc,
                    fallback,
                )
                primary_err = exc
            else:
                # Non-retryable (e.g. 400 bad request) — re-raise immediately.
                raise

    # ── Fallback attempt ──────────────────────────────────────────────────────
    assert primary_err is not None  # noqa: S101 — logic guard, not user input

    try:
        fallback_adapter = get_adapter(fallback)
    except Exception as fb_exc:
        logger.error(
            "complete_with_fallback: fallback %r also unavailable (%s); "
            "re-raising primary error",
            fallback,
            fb_exc,
        )
        raise primary_err from fb_exc

    # When the request carries a provider-specific model id (e.g. a Gemini model
    # name like "gemini-2.5-flash"), clear it for the fallback call so the
    # fallback adapter uses its own sensible default instead of failing on an
    # unknown model id.
    fallback_request = request
    if request.model is not None and _looks_like_gemini_model(request.model):
        from dataclasses import replace

        fallback_request = replace(request, model=None)

    response = await fallback_adapter.complete(fallback_request)
    if serving_provider_out is not None:
        serving_provider_out.append(fallback)
    return response


def _looks_like_gemini_model(model: str) -> bool:
    """Return True when the model id is clearly a Gemini-specific one.

    Used to strip the model field before forwarding to the fallback adapter so
    the fallback (claude-code) uses its own default instead of failing on an
    unrecognised model id.
    """
    return model.startswith("gemini-") or model.startswith("gemini-flash") or model.startswith("gemini-pro")
