"""Unit tests for artemis.db.wait_for_db_ready.

These tests are fully offline — no real DB, no real sleeps.  They inject a
fake async ping callable and patch asyncio.sleep so the retry loop runs at
full speed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest  # noqa: E402 — third-party after stdlib (isort: skip)

from artemis.db import wait_for_db_ready

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ping(*, fail_times: int, exc: Exception | None = None) -> AsyncMock:
    """Return an async callable that raises ``exc`` for the first ``fail_times``
    calls, then succeeds (returns None).

    ``exc`` defaults to an OperationalError-shaped OSError so we don't need a
    real SQLAlchemy engine to construct one.
    """
    if exc is None:
        exc = OSError("connection refused")

    call_count = 0

    async def _ping() -> None:
        nonlocal call_count
        call_count += 1
        if call_count <= fail_times:
            raise exc

    return AsyncMock(side_effect=_ping)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_succeeds_immediately_when_db_ready() -> None:
    """Zero failures: should return True on the first attempt without sleeping."""
    ping = _make_ping(fail_times=0)

    with patch("artemis.db.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await wait_for_db_ready(
            max_wait=45.0,
            initial_delay=0.5,
            max_delay=5.0,
            _ping=ping,
        )

    assert result is True
    ping.assert_awaited_once()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_retries_then_succeeds() -> None:
    """Fails K times then succeeds — must return True and have called ping K+1 times."""
    fail_times = 4
    ping = _make_ping(fail_times=fail_times)

    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("artemis.db.asyncio.sleep", side_effect=_fake_sleep):
        result = await wait_for_db_ready(
            max_wait=45.0,
            initial_delay=0.5,
            max_delay=5.0,
            _ping=ping,
        )

    assert result is True
    assert ping.await_count == fail_times + 1
    # Should have slept fail_times times (once per failure, not after success).
    assert len(sleep_calls) == fail_times
    # Back-off: each sleep should be <= the cap and non-decreasing.
    for s in sleep_calls:
        assert s <= 5.0
    for a, b in zip(sleep_calls, sleep_calls[1:], strict=False):
        assert b >= a


@pytest.mark.asyncio
async def test_gives_up_after_bound_and_raises() -> None:
    """Always-failing ping must raise RuntimeError after max_wait, not hang."""
    exc = OSError("connection refused — DB still starting")
    ping = _make_ping(fail_times=9999, exc=exc)

    # Use a very short bound so the test finishes fast.
    max_wait = 2.0
    initial_delay = 0.5

    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("artemis.db.asyncio.sleep", side_effect=_fake_sleep), pytest.raises(
        RuntimeError, match="not ready after"
    ):
        await wait_for_db_ready(
            max_wait=max_wait,
            initial_delay=initial_delay,
            max_delay=5.0,
            _ping=ping,
        )

    # Must have attempted at least once.
    assert ping.await_count >= 1
    # Total elapsed sleep must not exceed max_wait (give 0.1s tolerance for
    # float arithmetic in the loop boundary check).
    assert sum(sleep_calls) <= max_wait + 0.1


@pytest.mark.asyncio
async def test_sqlalchemy_operational_error_is_caught() -> None:
    """OperationalError (SQLAlchemy) is treated as transient and retried."""
    from sqlalchemy.exc import OperationalError

    # Construct a minimal OperationalError without a real engine.
    sa_exc = OperationalError("SELECT 1", {}, Exception("starting up"))
    ping = _make_ping(fail_times=2, exc=sa_exc)

    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("artemis.db.asyncio.sleep", side_effect=_fake_sleep):
        result = await wait_for_db_ready(
            max_wait=45.0,
            initial_delay=0.5,
            max_delay=5.0,
            _ping=ping,
        )

    assert result is True
    assert ping.await_count == 3
