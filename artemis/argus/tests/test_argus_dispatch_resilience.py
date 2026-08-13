"""Unit tests for Argus dispatch resilience (v3 persistent dispatch → v4 claimed dispatch).

All tests are UNIT tests — no DB, no env vars required.  DB and Slack layers are
mocked.  Tests cover:

  R1 — dispatch writes a pending row with channel/team/district captured.
  R2 — dispatch does NOT fire a background task; it only inserts the pending
       row and returns a "queued" payload (ARGUS-1 -- see
       artemis/floating_artemis/tools/argus_tools.py's module docstring "v4").
  R3 — success marks the row done (status='done', completed_at set).
  R4 — exception increments attempts + sets error; row released to 'pending'
       so the claimer's next tick retries it immediately (ARGUS-1).
  R5 — at attempts >= 3 (MAX_ATTEMPTS) the row is marked failed + fallback posted.
  R6 — recover_pending_requests (ARGUS-1) is now a thin wrapper that runs one
       claim tick immediately; it no longer queries the table or fires
       background tasks itself. The real "claims a pending row and cannot
       double-run one the claimer already holds" proof needs a real
       Postgres connection (SKIP LOCKED semantics can't be meaningfully
       mocked) -- see artemis/floating_artemis/tests/test_argus_async_dispatch.py.

R6/R7/R8 here used to assert recover_pending_requests queried
argus_research_requests directly and re-fired loop.create_task(...) per row
-- that mechanism doesn't exist anymore; it's one call to run_claim_tick.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


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


# ── R2: _dispatch_research enqueues only -- no background task (ARGUS-1) ──────


@pytest.mark.asyncio
async def test_dispatch_enqueues_only_and_returns_queued() -> None:
    """_dispatch_research inserts the pending row and returns 'queued' -- it
    does NOT create a task and does NOT run research itself (ARGUS-1)."""
    import json

    from artemis.floating_artemis.tools import argus_tools

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
            "artemis.floating_artemis.tools.argus_tools._resolve_latest_qualified_signal",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._insert_pending_request",
            new_callable=AsyncMock,
            return_value=7,
        ) as mock_insert,
    ):
        result_json = await argus_tools._dispatch_research({"district_key": "TX-001"})

    result = json.loads(result_json)
    assert result["status"] == "queued"
    assert result["district"] == "TX-001"
    assert "detail" in result

    # Pending row was inserted -- and that is the ONLY effect of this call.
    # No task is created here at all (contrast with the old v2/v3 behavior,
    # which this test used to prove by sleep(0)-draining a background task);
    # test_argus_async_dispatch.py's rewritten dispatch tests assert directly
    # that no research-running call happens as a result of this function.
    mock_insert.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_no_channel_returns_failed_not_queued() -> None:
    """When channel resolution fails, _dispatch_research returns status='failed'
    -- never 'queued' or 'dispatched' (nothing was persisted or started)."""
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
    assert result["status"] == "failed"
    assert result["error"] == "no_channel_resolved"
    assert "NOT queued" in result["detail"]
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


# ── R6: recover_pending_requests delegates to run_claim_tick (ARGUS-1) ────────


@pytest.mark.asyncio
async def test_startup_recovery_delegates_to_run_claim_tick() -> None:
    """recover_pending_requests calls run_claim_tick and nothing else.

    ARGUS-1: this used to query argus_research_requests directly and re-fire
    a background task per row -- a second, independent mechanism from the
    interval scheduler that happened to be safe only because it ran once,
    before that scheduler existed to race it. It is now a one-line delegation
    to the SAME entry point the scheduler uses (including that entry point's
    own skip-if-already-running guard), which is what makes "cannot double-run
    a row the claimer already holds" true by construction. See that entry
    point's own claim-atomicity proof (real Postgres, SKIP LOCKED) in
    artemis/floating_artemis/tests/test_argus_async_dispatch.py -- it can't be
    meaningfully asserted with mocks the way this test asserts delegation.
    """
    from artemis.floating_artemis.tools import argus_tools

    with patch(
        "artemis.floating_artemis.tools.argus_tools.run_claim_tick",
        new_callable=AsyncMock,
    ) as mock_tick:
        await argus_tools.recover_pending_requests()

    mock_tick.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_startup_recovery_swallows_claim_tick_failure() -> None:
    """A failure inside run_claim_tick must not propagate out of
    recover_pending_requests -- it's awaited via asyncio.create_task from
    main.py's lifespan and must never block or crash startup."""
    from artemis.floating_artemis.tools import argus_tools

    with patch(
        "artemis.floating_artemis.tools.argus_tools.run_claim_tick",
        new_callable=AsyncMock,
        side_effect=RuntimeError("DB unreachable"),
    ):
        await argus_tools.recover_pending_requests()  # must not raise
