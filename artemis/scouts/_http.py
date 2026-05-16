"""Shared async HTTP client for all Artemis scout workers.

All external HTTP in scouts should go through ``ScoutHttpClient``.  It provides:

- Per-client rate limiting (token bucket via asyncio.Lock)
- Automatic retry with configurable back-off on transient failures
  (5xx, 429, and network-level errors)
- Context-manager lifecycle around the inner ``httpx.AsyncClient``
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import httpx

_logger = logging.getLogger(__name__)

_DEFAULT_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
# Seconds to wait before each successive retry (3 attempts total by default).
_DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 5.0, 30.0)


class _RateLimiter:
    """Enforce a minimum inter-request interval via a simple token-bucket."""

    def __init__(self, calls_per_second: float) -> None:
        if calls_per_second <= 0:
            raise ValueError("calls_per_second must be positive")
        self._interval = 1.0 / calls_per_second
        # Initialise far enough in the past that the first call never waits.
        self._last: float = -self._interval
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            gap = self._interval - (now - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = asyncio.get_running_loop().time()


class ScoutHttpClient:
    """``httpx.AsyncClient`` wrapper with rate limiting and retry.

    Parameters
    ----------
    base_url:
        Optional URL prefix prepended to every request path.
    headers:
        Default headers merged into every request.
    timeout:
        Per-request timeout in seconds (default 30).
    rate_limit:
        Maximum requests per second (default 5).  Set to 1.0 for the
        LegiScan free tier, for example.
    backoff:
        Sequence of sleep durations (seconds) between retries.  Length
        determines maximum retry count.
    retry_statuses:
        HTTP status codes that trigger a retry.
    _inner:
        Inject a pre-built ``httpx.AsyncClient`` — intended for tests only.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        rate_limit: float = 5.0,
        backoff: Sequence[float] = _DEFAULT_BACKOFF,
        retry_statuses: frozenset[int] = _DEFAULT_RETRY_STATUSES,
        _inner: httpx.AsyncClient | None = None,
    ) -> None:
        self._client: httpx.AsyncClient = _inner or httpx.AsyncClient(
            base_url=base_url,
            headers=headers or {},
            timeout=timeout,
        )
        self._limiter = _RateLimiter(rate_limit)
        self._backoff = tuple(backoff)
        self._retry_statuses = retry_statuses

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._send("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self._send("POST", url, **kwargs)

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        await self._limiter.wait()
        last_exc: Exception | None = None
        last_resp: httpx.Response | None = None

        # One initial attempt + len(backoff) retries.
        delays: tuple[float | None, ...] = (*self._backoff, None)
        for attempt, delay in enumerate(delays, start=1):
            try:
                resp = await self._client.request(method, url, **kwargs)
                last_resp = resp
                if resp.status_code not in self._retry_statuses:
                    return resp
                _logger.warning(
                    "%s %s → HTTP %d (attempt %d/%d); will retry",
                    method,
                    url,
                    resp.status_code,
                    attempt,
                    len(delays),
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as exc:
                _logger.warning(
                    "%s %s → %s (attempt %d/%d); will retry",
                    method,
                    url,
                    type(exc).__name__,
                    attempt,
                    len(delays),
                )
                last_exc = exc
                last_resp = None

            if delay is not None:
                await asyncio.sleep(delay)

        if last_exc is not None:
            raise last_exc
        assert last_resp is not None  # exhausted retries on a retryable HTTP status
        return last_resp

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ScoutHttpClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
