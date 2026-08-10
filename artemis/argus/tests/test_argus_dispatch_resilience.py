"""Unit tests for Argus dispatch resilience (v3 persistent dispatch).

All tests are UNIT tests — no DB, no env vars required.  DB and Slack layers are
mocked.  Tests cover:

  R1 — dispatch writes a pending row with channel/team/district captured.
  R2 — dispatch fires a background task AFTER inserting the pending row.
  R3 — success marks the row done (status='done', completed_at set).
  R4 — exception increments attempts + sets error; row stays pending.
  R5 — at attempts >= 3 (MAX_ATTEMPTS) the row is marked failed + fallback posted.
  R6 — startup recovery selects pending AND attempts<3, re-fires background tasks.
  R7 — startup recovery does NOT re-fire rows with attempts >= MAX_ATTEMPTS
       (DB query filters them; verified by checking only fresh rows fire).
  R8 — startup recovery is silent/non-blocking when no pending rows exist.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_request_row(
    *,
    id: int = 1,
    district_key: str = "TX-001",
    channel_id: str = "C123",
    team_id: str = "T456",
    signal: dict[str, Any] | None = None,
    triggering_signal_id: str | None = None,
    status: str = "pending",
    attempts: int = 0,
    error: str | None = None,
) -> MagicMock:
    """Build a mock ArgusResearchRequest row."""
    row = MagicMock()
    row.id = id
    row.district_key = district_key
    row.channel_id = channel_id
    row.team_id = team_id
    row.signal = signal
    row.triggering_signal_id = triggering_signal_id
    row.status = status
    row.attempts = attempts
    row.error = error
    return row


def _make_session_ctx(session: AsyncMock) -> AsyncMock:
    """Wrap a mock session in an async context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ── R1: _insert_pending_request creates a row with correct fields ─────────────


@pytest.mark.asyncio
async def test_insert_pending_request_creates_row_with_correct_fields() -> None:
    """_insert_pending_request constructs an ArgusResearchRequest with the right fields."""
    from artemis.floating_artemis.tools.argus_tools import _insert_pending_request

    fake_instance = MagicMock()
    fake_instance.id = 42

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()

    ctx = _make_session_ctx(mock_session)

    with (
        patch("artemis.db.SessionLocal", return_value=ctx),
        patch(
            "artemis.argus.models.ArgusResearchRequest",
            return_value=fake_instance,
        ) as MockModel,
    ):
        # Need to patch where it's imported in the function body
        with patch(
            "artemis.floating_artemis.tools.argus_tools.ArgusResearchRequest",
            MockModel,
        ):
            with patch("artemis.floating_artemis.tools.argus_tools._db") as mock_db_mod:
                mock_db_mod.SessionLocal.return_value = ctx
                row_id = await _insert_pending_request(
                    district_key="TX-001",
                    channel_id="C123",
                    team_id="T456",
                    signal={"headline": "test"},
                    triggering_signal_id="99",
                )

    MockModel.assert_called_once_with(
        district_key="TX-001",
        channel_id="C123",
        team_id="T456",
        signal={"headline": "test"},
        triggering_signal_id="99",
        status="pending",
        attempts=0,
    )
    assert row_id == 42


# ── R2: _dispatch_research fires background task ───────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_fires_task_and_returns_dispatched() -> None:
    """_dispatch_research returns dispatched JSON and fires background task."""
    import json

    from artemis.floating_artemis.tools import argus_tools

    fired: list[str] = []

    async def fake_safe_research_and_post(**kwargs):
        fired.append(kwargs["district_key"])

    # floating_session_id_var is imported lazily inside the function body, so
    # we patch at its source module rather than the argus_tools namespace.
    mock_ctx_var = MagicMock()
    mock_ctx_var.get.return_value = "slack-callie-T456-C123-main"

    with (
        patch(
            "artemis.floating_artemis.context.floating_session_id_var",
            mock_ctx_var,
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=("C123", "T456"),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._insert_pending_request",
            new_callable=AsyncMock,
            return_value=7,
        ) as mock_insert,
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=fake_safe_research_and_post,
        ),
    ):
        result_json = await argus_tools._dispatch_research({"district_key": "TX-001"})

    result = json.loads(result_json)
    assert result["status"] == "dispatched"
    assert result["district"] == "TX-001"

    # Pending row was inserted
    mock_insert.assert_called_once()

    # Give event loop a tick so the created task runs
    await asyncio.sleep(0)
    assert "TX-001" in fired


@pytest.mark.asyncio
async def test_dispatch_no_channel_returns_warning() -> None:
    """When channel resolution fails, _dispatch_research returns a warning payload."""
    import json

    from artemis.floating_artemis.tools import argus_tools

    mock_ctx_var = MagicMock()
    mock_ctx_var.get.return_value = None

    with (
        patch(
            "artemis.floating_artemis.context.floating_session_id_var",
            mock_ctx_var,
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=(None, ""),  # no channel
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._insert_pending_request",
            new_callable=AsyncMock,
        ) as mock_insert,
    ):
        result_json = await argus_tools._dispatch_research({"district_key": "TX-002"})

    result = json.loads(result_json)
    assert result["status"] == "dispatched"
    assert "warning" in result
    # Should NOT have tried to insert a row (no channel = can't persist useful row)
    mock_insert.assert_not_called()


# ── R3: success marks done ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_request_done_sets_status_and_completed_at() -> None:
    """_mark_request_done sets status='done' and completed_at=now on the row."""
    from artemis.floating_artemis.tools.argus_tools import _mark_request_done

    fake_row = MagicMock()
    fake_row.status = "pending"
    fake_row.completed_at = None

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=fake_row)
    mock_session.commit = AsyncMock()

    ctx = _make_session_ctx(mock_session)

    with patch("artemis.floating_artemis.tools.argus_tools._db") as mock_db:
        mock_db.SessionLocal.return_value = ctx
        await _mark_request_done(request_id=1)

    assert fake_row.status == "done"
    assert fake_row.completed_at is not None
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_mark_request_done_noop_for_none_id() -> None:
    """_mark_request_done is a no-op when request_id is None."""
    from artemis.floating_artemis.tools.argus_tools import _mark_request_done

    with patch("artemis.floating_artemis.tools.argus_tools._db") as mock_db:
        await _mark_request_done(request_id=None)
        mock_db.SessionLocal.assert_not_called()


# ── R4: exception increments attempts ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_request_failed_increments_attempts_and_sets_error() -> None:
    """_mark_request_failed increments attempts and sets error; returns False below cap."""
    from artemis.floating_artemis.tools.argus_tools import _mark_request_failed

    fake_row = MagicMock()
    fake_row.attempts = 0
    fake_row.status = "pending"
    fake_row.error = None

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=fake_row)
    mock_session.commit = AsyncMock()

    ctx = _make_session_ctx(mock_session)

    with patch("artemis.floating_artemis.tools.argus_tools._db") as mock_db:
        mock_db.SessionLocal.return_value = ctx
        should_post_fallback = await _mark_request_failed(
            1,
            error="connection reset",
            channel_id="C123",
            team_id="T456",
            district_key="TX-001",
        )

    assert fake_row.attempts == 1
    assert fake_row.error == "connection reset"
    assert fake_row.status == "pending"
    assert should_post_fallback is False
    mock_session.commit.assert_called_once()


# ── R5: at cap → mark failed + post fallback ──────────────────────────────────


@pytest.mark.asyncio
async def test_mark_request_failed_at_cap_marks_failed_and_returns_true() -> None:
    """At MAX_ATTEMPTS, _mark_request_failed marks failed and returns True."""
    from artemis.floating_artemis.tools.argus_tools import _MAX_ATTEMPTS, _mark_request_failed

    fake_row = MagicMock()
    fake_row.attempts = _MAX_ATTEMPTS - 1
    fake_row.status = "pending"
    fake_row.error = None

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=fake_row)
    mock_session.commit = AsyncMock()

    ctx = _make_session_ctx(mock_session)

    with patch("artemis.floating_artemis.tools.argus_tools._db") as mock_db:
        mock_db.SessionLocal.return_value = ctx
        should_post_fallback = await _mark_request_failed(
            1,
            error="max retries exceeded",
            channel_id="C123",
            team_id="T456",
            district_key="TX-001",
        )

    assert fake_row.attempts == _MAX_ATTEMPTS
    assert fake_row.status == "failed"
    assert should_post_fallback is True


@pytest.mark.asyncio
async def test_safe_research_and_post_posts_fallback_at_cap() -> None:
    """_safe_research_and_post calls _post_fallback when at the retry cap."""
    from artemis.floating_artemis.tools.argus_tools import _safe_research_and_post

    fallback_posted: list[str] = []

    async def fake_post_fallback(*, channel_id, team_id, district_key):
        fallback_posted.append(district_key)

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._research_and_post",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM timeout"),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._mark_request_failed",
            new_callable=AsyncMock,
            return_value=True,  # at cap
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._post_fallback",
            side_effect=fake_post_fallback,
        ),
    ):
        await _safe_research_and_post(
            request_id=1,
            channel_id="C123",
            team_id="T456",
            district_key="TX-001",
            triggering_signal_id=None,
            signal=None,
        )

    assert "TX-001" in fallback_posted


@pytest.mark.asyncio
async def test_safe_research_and_post_no_fallback_below_cap() -> None:
    """_safe_research_and_post does NOT call _post_fallback when below the retry cap."""
    from artemis.floating_artemis.tools.argus_tools import _safe_research_and_post

    fallback_posted: list[str] = []

    async def fake_post_fallback(*, channel_id, team_id, district_key):
        fallback_posted.append(district_key)

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._research_and_post",
            new_callable=AsyncMock,
            side_effect=RuntimeError("transient error"),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._mark_request_failed",
            new_callable=AsyncMock,
            return_value=False,  # below cap
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._post_fallback",
            side_effect=fake_post_fallback,
        ),
    ):
        await _safe_research_and_post(
            request_id=1,
            channel_id="C123",
            team_id="T456",
            district_key="TX-001",
            triggering_signal_id=None,
            signal=None,
        )

    assert fallback_posted == []


# ── R6: startup recovery re-fires pending rows ────────────────────────────────


@pytest.mark.asyncio
async def test_startup_recovery_refires_pending_rows() -> None:
    """recover_pending_requests re-fires background tasks for all pending rows."""
    from artemis.floating_artemis.tools import argus_tools

    row1 = _make_request_row(
        id=10, district_key="TX-001", channel_id="C1", team_id="T1", attempts=0
    )
    row2 = _make_request_row(
        id=11, district_key="TX-002", channel_id="C2", team_id="T2", attempts=1
    )

    fired: list[str] = []

    async def fake_safe_research_and_post(**kwargs):
        fired.append(kwargs["district_key"])

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [row1, row2]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    ctx = _make_session_ctx(mock_session)

    # Patch sqlalchemy.select so ArgusResearchRequest (real ORM class) doesn't
    # trigger a DB connection.  We just need the session.execute mock to return rows.
    with (
        patch("artemis.floating_artemis.tools.argus_tools._db") as mock_db,
        patch("artemis.floating_artemis.tools.argus_tools.select", return_value=MagicMock()),
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=fake_safe_research_and_post,
        ),
    ):
        mock_db.SessionLocal.return_value = ctx
        await argus_tools.recover_pending_requests()

    # Drain the created tasks
    await asyncio.sleep(0)

    assert set(fired) == {"TX-001", "TX-002"}
    assert len(fired) == 2


# ── R7: startup recovery only fires rows returned by query ────────────────────


@pytest.mark.asyncio
async def test_startup_recovery_only_fires_rows_returned_by_query() -> None:
    """recover_pending_requests fires exactly the rows the DB query returns.

    The filtering (attempts < MAX_ATTEMPTS) is the DB's job. This test verifies
    the recovery loop doesn't add extra filtering of its own — whatever the query
    returns gets re-fired, no more, no less.
    """
    from artemis.floating_artemis.tools import argus_tools

    # Simulate the DB returning only one row (already filtered by attempts < 3)
    fresh_row = _make_request_row(id=20, district_key="TX-003", attempts=0)

    fired: list[str] = []

    async def fake_safe_research_and_post(**kwargs):
        fired.append(kwargs["district_key"])

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fresh_row]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    ctx = _make_session_ctx(mock_session)

    with (
        patch("artemis.floating_artemis.tools.argus_tools._db") as mock_db,
        patch("artemis.floating_artemis.tools.argus_tools.select", return_value=MagicMock()),
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=fake_safe_research_and_post,
        ),
    ):
        mock_db.SessionLocal.return_value = ctx
        await argus_tools.recover_pending_requests()

    await asyncio.sleep(0)
    assert fired == ["TX-003"]


# ── R8: startup recovery is silent when no pending rows ───────────────────────


@pytest.mark.asyncio
async def test_startup_recovery_silent_with_no_pending_rows() -> None:
    """recover_pending_requests is silent and fires no tasks when there are no pending rows."""
    from artemis.floating_artemis.tools import argus_tools

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    ctx = _make_session_ctx(mock_session)

    tasks_created: list[Any] = []

    async def fake_safe_research_and_post(**kwargs):
        tasks_created.append(kwargs)

    with (
        patch("artemis.floating_artemis.tools.argus_tools._db") as mock_db,
        patch("artemis.floating_artemis.tools.argus_tools.select", return_value=MagicMock()),
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=fake_safe_research_and_post,
        ),
    ):
        mock_db.SessionLocal.return_value = ctx
        await argus_tools.recover_pending_requests()

    await asyncio.sleep(0)
    assert tasks_created == []
