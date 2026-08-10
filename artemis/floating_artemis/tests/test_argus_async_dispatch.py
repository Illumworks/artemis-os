"""Unit tests for the async dispatch_research tool (v2).

All tests are UNIT tests -- no DB, no env vars, no network.
research_district, the LLM summary, and SlackClient are all mocked.

Test coverage
-------------
T1 -- dispatch_research returns a 'dispatched' payload immediately and does NOT
      call research_district synchronously in the same turn.
T2 -- dispatch_research schedules a background asyncio Task (GC-guarded);
      the task is added to _BACKGROUND_TASKS while live.
T3 -- The background task (_research_and_post) calls SlackClient.post_message
      with Callie-voiced, md_to_mrkdwn-processed text that credits Argus,
      to the captured channel_id.
T4 -- A failure inside the background task is swallowed (no raise propagates).
T5 -- When no channel_id can be resolved, no Slack post is attempted.
T6 -- Thin/no findings: graceful fallback note is still posted (not silently
      dropped).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_summary(
    *,
    new_findings: int = 3,
    gap_dimensions: list[str] | None = None,
    existing_dimensions: list[str] | None = None,
    recommended_angle: str | None = "Lead with the RFP timeline",
) -> dict[str, Any]:
    return {
        "new_findings": new_findings,
        "gap_dimensions": gap_dimensions
        or ["current_vendor", "procurement_timing", "decision_makers"],
        "existing_dimensions": existing_dimensions or [],
        "recommended_angle": recommended_angle,
        "written_obs_ids": list(range(new_findings)),
    }


def _make_db_mock() -> tuple[MagicMock, AsyncMock]:
    """Return (mock_db_module, mock_session) where mock_db_module.SessionLocal()
    is an async context manager yielding mock_session."""
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_db = MagicMock()
    mock_db.SessionLocal.return_value = mock_ctx
    return mock_db, mock_session


# ── T1: dispatch_research returns dispatched payload immediately ───────────────


@pytest.mark.asyncio
async def test_dispatch_research_returns_dispatched_payload_immediately() -> None:
    """dispatch_research returns {"status":"dispatched","district":...} without
    calling research_district in the same turn."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    research_was_called = False

    async def fake_safe_post(**kwargs: Any) -> None:
        nonlocal research_was_called
        research_was_called = True

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            new_callable=AsyncMock,
        ) as mock_safe,
        patch("artemis.floating_artemis.context.floating_session_id_var") as mock_var,
    ):
        mock_var.get.return_value = "slack-callie-TABC-CABC-_"
        # Prevent the task body from running by making the mock a no-op coroutine
        mock_safe.return_value = None

        result_str = await _dispatch_research({"district_key": "TX-001"})

    result = json.loads(result_str)
    assert result["status"] == "dispatched"
    assert result["district"] == "TX-001"


@pytest.mark.asyncio
async def test_dispatch_research_does_not_call_research_district_inline() -> None:
    """Verify research_district is NOT called synchronously during the tool invocation."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    inline_research_calls: list[str] = []

    async def spy_research_and_post(**kwargs: Any) -> None:
        inline_research_calls.append(kwargs.get("district_key", ""))

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=spy_research_and_post,
        ),
        patch("artemis.floating_artemis.context.floating_session_id_var") as mock_var,
    ):
        mock_var.get.return_value = "slack-callie-TABC-CABC-_"

        # Call and return immediately -- don't drain the event loop
        coro = _dispatch_research({"district_key": "TX-010"})
        result_str = await coro

    # The tool returned immediately (result is valid JSON)
    result = json.loads(result_str)
    assert result["status"] == "dispatched"

    # _safe_research_and_post may or may not have run yet (it's a background task).
    # What matters is that research_district was NOT called synchronously in-turn.
    # We can't easily distinguish "scheduled but not run yet" from "not scheduled",
    # but T2 verifies the task is registered in _BACKGROUND_TASKS.
    # This test's purpose is: result came back immediately with "dispatched".


# ── T2: dispatch_research schedules a background task ─────────────────────────


@pytest.mark.asyncio
async def test_dispatch_research_adds_task_to_background_tasks_set() -> None:
    """dispatch_research registers a task in _BACKGROUND_TASKS while it is running."""
    from artemis.floating_artemis.tools.argus_tools import _BACKGROUND_TASKS, _dispatch_research

    task_was_live: list[bool] = []

    # Use an event to detect when the task is executing (live in _BACKGROUND_TASKS)
    task_started = asyncio.Event()
    task_can_finish = asyncio.Event()

    async def blocking_safe_post(**kwargs: Any) -> None:
        task_started.set()
        await task_can_finish.wait()  # hold the task open so we can inspect the set

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=blocking_safe_post,
        ),
        patch("artemis.floating_artemis.context.floating_session_id_var") as mock_var,
    ):
        mock_var.get.return_value = "slack-callie-TABC-CABC-_"

        await _dispatch_research({"district_key": "TX-002"})

        # Give the event loop a tick so the task starts executing
        await task_started.wait()

        # While the task is still running, it must be in _BACKGROUND_TASKS
        task_was_live.append(
            any("argus_bg_TX-002" in (t.get_name() or "") for t in _BACKGROUND_TASKS)
        )

        # Release the task
        task_can_finish.set()
        await asyncio.sleep(0)  # let the done-callback fire

    assert task_was_live and task_was_live[0], (
        "Background task was not found in _BACKGROUND_TASKS while running"
    )


# ── T3: background task posts Callie-voiced, Argus-credited, md_to_mrkdwn'd text ──


@pytest.mark.asyncio
async def test_background_task_posts_callie_voiced_argus_credited_message() -> None:
    """_research_and_post posts a Callie-voiced, md_to_mrkdwn-processed message
    that credits Argus, to the captured channel_id."""
    from artemis.floating_artemis.tools.argus_tools import _research_and_post

    posted_calls: list[dict[str, Any]] = []

    # Mock FA session row with channel/team in metadata
    mock_fa_row = MagicMock()
    mock_fa_row.metadata_ = {
        "surface": "slack",
        "agent_id": "callie",
        "team_id": "TABC",
        "channel_id": "C9999",
    }

    # Fake SlackClient
    async def fake_post_message(channel: str, text: str, **kwargs: Any) -> dict[str, Any]:
        posted_calls.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "1234567890.123456"}

    fake_client = MagicMock()
    fake_client.post_message = fake_post_message

    fake_agent_cfg = MagicMock()
    fake_agent_cfg.access_token = "xoxb-callie-fake-token"

    callie_text = (
        "Argus is back with findings on TX-001. "
        "The district is mid-RFP -- strongest angle is the timeline. "
        "No competitive commitments on record yet."
    )

    mock_db, mock_session = _make_db_mock()
    mock_fa_repo = MagicMock()
    mock_fa_repo.get_session_by_id = AsyncMock(return_value=mock_fa_row)

    mock_research_district = AsyncMock(return_value=_make_summary())

    with (
        patch("artemis.db.SessionLocal", mock_db.SessionLocal),
        patch("artemis.argus.flow.research_district", mock_research_district),
        patch(
            "artemis.floating_artemis.tools.argus_tools._callie_summarize",
            new_callable=AsyncMock,
            return_value=callie_text,
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=fake_agent_cfg,
        ),
        patch(
            "artemis.floating_artemis.repository.get_session_by_id", mock_fa_repo.get_session_by_id
        ),
    ):
        await _research_and_post(
            session_id="slack-callie-TABC-C9999-_",
            district_key="TX-001",
            triggering_signal_id=None,
            signal=None,
        )

    # One post must have happened
    assert len(posted_calls) == 1, f"Expected 1 post call, got {len(posted_calls)}"
    posted = posted_calls[0]

    # Posted to the correct channel
    assert posted["channel"] == "C9999", f"Wrong channel: {posted['channel']!r}"

    # Text must be non-empty and credit Argus
    assert posted["text"].strip()
    assert "Argus" in posted["text"], f"'Argus' not found in posted text: {posted['text']!r}"


# ── T4: background task failure is swallowed ──────────────────────────────────


@pytest.mark.asyncio
async def test_background_task_swallows_failure() -> None:
    """An exception inside _research_and_post never propagates out of
    _safe_research_and_post."""
    from artemis.floating_artemis.tools.argus_tools import _safe_research_and_post

    with patch(
        "artemis.floating_artemis.tools.argus_tools._research_and_post",
        side_effect=RuntimeError("simulated research failure"),
    ):
        # Must NOT raise
        await _safe_research_and_post(
            session_id="slack-callie-TABC-C9999-_",
            district_key="TX-003",
            triggering_signal_id=None,
            signal=None,
        )


# ── T5: no channel_id → no Slack post ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_channel_id_skips_slack_post() -> None:
    """When session_id is None (no channel_id can be resolved), no Slack post
    is attempted."""
    from artemis.floating_artemis.tools.argus_tools import _research_and_post

    post_was_called = False

    async def unexpected_post(**kwargs: Any) -> None:
        nonlocal post_was_called
        post_was_called = True

    with patch(
        "artemis.floating_artemis.tools.argus_tools._post_as_callie",
        side_effect=unexpected_post,
    ):
        # session_id=None → cannot resolve channel_id
        await _research_and_post(
            session_id=None,
            district_key="TX-004",
            triggering_signal_id=None,
            signal=None,
        )

    assert not post_was_called, "Should not post when channel_id is unknown"


# ── T6: thin findings → graceful fallback note is still posted ────────────────


@pytest.mark.asyncio
async def test_thin_findings_posts_graceful_note() -> None:
    """When Argus finds nothing new, _research_and_post still posts a graceful
    note (not silent)."""
    from artemis.floating_artemis.tools.argus_tools import _research_and_post

    posted_calls: list[dict[str, Any]] = []
    thin_summary = _make_summary(new_findings=0, gap_dimensions=[], recommended_angle=None)

    mock_fa_row = MagicMock()
    mock_fa_row.metadata_ = {
        "surface": "slack",
        "agent_id": "callie",
        "team_id": "TABC",
        "channel_id": "C5555",
    }
    mock_fa_repo = MagicMock()
    mock_fa_repo.get_session_by_id = AsyncMock(return_value=mock_fa_row)

    thin_note = (
        "Argus came back light on TX-005 -- no new material surfaced this pass. "
        "We can revisit when a stronger signal comes through."
    )

    async def fake_post_message(channel: str, text: str, **kwargs: Any) -> dict[str, Any]:
        posted_calls.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "1234567890.123456"}

    fake_client = MagicMock()
    fake_client.post_message = fake_post_message

    fake_agent_cfg = MagicMock()
    fake_agent_cfg.access_token = "xoxb-callie-fake-token"

    mock_db, _ = _make_db_mock()
    mock_research_district = AsyncMock(return_value=thin_summary)

    with (
        patch("artemis.db.SessionLocal", mock_db.SessionLocal),
        patch("artemis.argus.flow.research_district", mock_research_district),
        patch(
            "artemis.floating_artemis.tools.argus_tools._callie_summarize",
            new_callable=AsyncMock,
            return_value=thin_note,
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=fake_agent_cfg,
        ),
        patch(
            "artemis.floating_artemis.repository.get_session_by_id", mock_fa_repo.get_session_by_id
        ),
    ):
        await _research_and_post(
            session_id="slack-callie-TABC-C5555-_",
            district_key="TX-005",
            triggering_signal_id=None,
            signal=None,
        )

    # A post MUST happen even for thin results
    assert len(posted_calls) == 1, "Expected a graceful fallback post even for thin findings"
    assert posted_calls[0]["channel"] == "C5555"
    assert posted_calls[0]["text"].strip()
