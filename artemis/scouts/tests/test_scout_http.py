"""Tests for artemis.scouts._http — shared scout HTTP client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from artemis.scouts._http import ScoutHttpClient, _RateLimiter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resp(status: int) -> httpx.Response:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    return r


def _client(
    *responses: httpx.Response | Exception,
    rate_limit: float = 100.0,
    backoff: tuple[float, ...] = (0.0, 0.0),
) -> ScoutHttpClient:
    inner = AsyncMock(spec=httpx.AsyncClient)
    side: list[httpx.Response | Exception] = list(responses)
    inner.request = AsyncMock(side_effect=side)
    return ScoutHttpClient(_inner=inner, rate_limit=rate_limit, backoff=backoff)


# ---------------------------------------------------------------------------
# Basic success paths
# ---------------------------------------------------------------------------


async def test_get_200_returns_immediately() -> None:
    """200 is returned on first attempt with no sleep."""
    c = _client(_resp(200))
    with patch("asyncio.sleep") as mock_sleep:
        resp = await c.get("http://x/ok")
    assert resp.status_code == 200
    mock_sleep.assert_not_called()


async def test_404_not_retried() -> None:
    """404 is not in the retry set; returned immediately."""
    c = _client(_resp(404))
    resp = await c.get("http://x/notfound")
    assert resp.status_code == 404
    # inner.request called once only
    c._client.request.assert_called_once()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Retry on HTTP status codes
# ---------------------------------------------------------------------------


async def test_500_retries_then_200() -> None:
    """500 on first attempt → retry → 200 returned."""
    c = _client(_resp(500), _resp(200))
    with patch("asyncio.sleep"):
        resp = await c.get("http://x/flaky")
    assert resp.status_code == 200
    assert c._client.request.call_count == 2  # type: ignore[attr-defined]


async def test_429_retries_then_200() -> None:
    """429 rate-limited response triggers retry."""
    c = _client(_resp(429), _resp(200))
    with patch("asyncio.sleep"):
        resp = await c.post("http://x/post")
    assert resp.status_code == 200


async def test_exhaust_retries_returns_last_response() -> None:
    """All retries exhausted on persistent 5xx → last response returned."""
    c = _client(_resp(503), _resp(503), _resp(503), backoff=(0.0, 0.0))
    with patch("asyncio.sleep"):
        resp = await c.get("http://x/down")
    assert resp.status_code == 503
    assert c._client.request.call_count == 3  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Retry on network errors
# ---------------------------------------------------------------------------


async def test_connect_error_retries_then_succeeds() -> None:
    """ConnectError on first attempt → retry → success."""
    c = _client(httpx.ConnectError("refused"), _resp(200))
    with patch("asyncio.sleep"):
        resp = await c.get("http://x/net")
    assert resp.status_code == 200


async def test_exhaust_retries_raises_last_network_exc() -> None:
    """All retries fail with network errors → raises."""
    c = _client(
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        backoff=(0.0, 0.0),
    )
    with patch("asyncio.sleep"), pytest.raises(httpx.ConnectError):
        await c.get("http://x/always-down")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


async def test_rate_limiter_sleeps_when_called_too_soon() -> None:
    """Second call within the interval triggers asyncio.sleep."""
    limiter = _RateLimiter(calls_per_second=1.0)  # 1 s interval

    slept: list[float] = []

    async def fake_sleep(t: float) -> None:
        slept.append(t)

    # Simulate: first call at t=0 (no wait), second call at t=0.1 (must wait ~0.9s)
    times = iter([0.0, 0.0, 0.1, 1.0])

    with (
        patch("asyncio.get_running_loop") as mock_loop,
        patch("asyncio.sleep", side_effect=fake_sleep),
    ):
        mock_loop.return_value.time.side_effect = times
        await limiter.wait()  # first call — no sleep
        await limiter.wait()  # second call — should sleep

    assert len(slept) == 1
    assert slept[0] == pytest.approx(0.9, abs=0.01)
