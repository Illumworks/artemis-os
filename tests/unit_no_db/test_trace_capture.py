"""Unit tests for the P6 execution-trace capture foundation.

All tests are pure-unit (no live DB required).  Sessions are mocked.

Tests
-----
1. record_trace builds the row with correct fields (including truncation).
2. capture_trace failure is swallowed — never raises into the caller.
3. get_recent_traces returns an ordered list; query filters agent_id.
4. get_recent_traces returns [] on DB error (fail-safe).
5. start_timer / elapsed_ms return plausible values.
6. Long input_summary and error are truncated to 500 / 2000 chars.
7. tools_used defaults to [] when None is passed.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.trace.capture import (
    _safe_record_trace,
    elapsed_ms,
    get_recent_traces,
    record_trace,
    start_timer,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _make_session(row: Any = None) -> AsyncMock:
    """Build a minimal mock AsyncSession."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    # refresh sets id=1 on whatever was add()ed
    async def _refresh(obj: Any, *a: Any, **kw: Any) -> None:
        obj.id = 1

    session.refresh = _refresh

    # execute returns scalars().all() == [row] or []
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[row] if row is not None else [])
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    session.execute = AsyncMock(return_value=result_mock)
    return session


# ─── Test 1: record_trace builds correct row ─────────────────────────────────


@pytest.mark.asyncio
async def test_record_trace_fields() -> None:
    """record_trace inserts an AgentTrace with the correct field values."""
    session = _make_session()

    row = await record_trace(
        session,
        agent_id="artemis",
        feature_tag="floating_artemis",
        session_id="slack-123",
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_summary="What is on my calendar today?",
        tools_used=["query_memory", "get_gcal_events"],
        output_summary="You have 3 meetings.",
        outcome="success",
        latency_ms=420,
        input_tokens=1234,
        output_tokens=56,
        owner_user_id=42,
    )

    # session.add must have been called with the row
    session.add.assert_called_once()
    added = session.add.call_args[0][0]

    assert added.agent_id == "artemis"
    assert added.feature_tag == "floating_artemis"
    assert added.session_id == "slack-123"
    assert added.provider == "anthropic"
    assert added.model == "claude-sonnet-4-6"
    assert added.input_summary == "What is on my calendar today?"
    assert added.tools_used == ["query_memory", "get_gcal_events"]
    assert added.output_summary == "You have 3 meetings."
    assert added.outcome == "success"
    assert added.latency_ms == 420
    assert added.input_tokens == 1234
    assert added.output_tokens == 56
    assert added.owner_user_id == 42
    assert added.error is None

    # flush was called (caller owns commit)
    session.flush.assert_awaited_once()


# ─── Test 2: capture_trace failure is swallowed ──────────────────────────────


@pytest.mark.asyncio
async def test_capture_trace_failure_does_not_raise() -> None:
    """_safe_record_trace never raises even when the inner write explodes."""
    # Patch _do_record_trace to raise
    with patch(
        "artemis.trace.capture._do_record_trace",
        new=AsyncMock(side_effect=RuntimeError("DB is on fire")),
    ):
        # This must complete without raising
        await _safe_record_trace(
            agent_id="artemis",
            feature_tag="floating_artemis",
            session_id=None,
            provider=None,
            model=None,
            input_summary=None,
            tools_used=[],
            output_summary=None,
            outcome="success",
            error=None,
            latency_ms=None,
            input_tokens=None,
            output_tokens=None,
            owner_user_id=None,
        )
    # If we reach here the exception was swallowed — test passes.


# ─── Test 3: get_recent_traces returns ordered results ───────────────────────


@pytest.mark.asyncio
async def test_get_recent_traces_returns_rows() -> None:
    """get_recent_traces returns the rows from the DB query."""
    fake_row = MagicMock()
    fake_row.agent_id = "artemis"
    fake_row.outcome = "success"

    session = _make_session(row=fake_row)

    rows = await get_recent_traces(session, "artemis", limit=10)
    assert rows == [fake_row]
    session.execute.assert_awaited_once()


# ─── Test 4: get_recent_traces returns [] on DB error ────────────────────────


@pytest.mark.asyncio
async def test_get_recent_traces_fail_safe() -> None:
    """get_recent_traces returns [] when the DB query raises (never raises itself)."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=Exception("connection refused"))

    rows = await get_recent_traces(session, "artemis")
    assert rows == []


# ─── Test 5: timing helpers return plausible values ──────────────────────────


def test_timer_helpers() -> None:
    """start_timer / elapsed_ms return non-negative integer milliseconds."""
    t = start_timer()
    ms = elapsed_ms(t)
    assert isinstance(ms, int)
    assert ms >= 0


# ─── Test 6: long input_summary and error are truncated ──────────────────────


@pytest.mark.asyncio
async def test_truncation_of_long_fields() -> None:
    """input_summary is capped at 500 chars; error is capped at 2000 chars."""
    long_input = "x" * 1000
    long_error = "e" * 3000
    session = _make_session()

    await record_trace(
        session,
        agent_id="artemis",
        feature_tag="floating_artemis",
        input_summary=long_input,
        error=long_error,
        outcome="error",
    )

    added = session.add.call_args[0][0]
    assert len(added.input_summary) == 500
    assert len(added.error) == 2000


# ─── Test 7: tools_used defaults to [] ───────────────────────────────────────


@pytest.mark.asyncio
async def test_tools_used_defaults_to_empty_list() -> None:
    """When tools_used=None is passed, the row stores an empty list."""
    session = _make_session()

    await record_trace(
        session,
        agent_id="artemis",
        feature_tag="floating_artemis",
        tools_used=None,
        outcome="success",
    )

    added = session.add.call_args[0][0]
    assert added.tools_used == []


# ─── Test 8: outcome="error" path stores the error string ────────────────────


@pytest.mark.asyncio
async def test_error_outcome_stores_error_string() -> None:
    """outcome='error' rows have a non-None error field."""
    session = _make_session()

    await record_trace(
        session,
        agent_id="callie",
        feature_tag="agent_run",
        outcome="error",
        error="TimeoutError: upstream timed out",
    )

    added = session.add.call_args[0][0]
    assert added.outcome == "error"
    assert added.error == "TimeoutError: upstream timed out"
