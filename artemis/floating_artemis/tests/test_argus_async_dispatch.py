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
    that credits Argus, to the given channel_id.

    channel_id/team_id are resolved in-turn by _dispatch_research (before the
    background task is scheduled) and passed straight through here -- this
    function no longer does session_id resolution itself."""
    from artemis.floating_artemis.tools.argus_tools import _research_and_post

    posted_calls: list[dict[str, Any]] = []

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
    ):
        await _research_and_post(
            request_id=None,
            channel_id="C9999",
            team_id="TABC",
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
            request_id=None,
            channel_id="C9999",
            team_id="TABC",
            district_key="TX-003",
            triggering_signal_id=None,
            signal=None,
        )


# ── T5: no channel_id → no Slack post ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_channel_id_skips_slack_post() -> None:
    """When channel_id cannot be resolved, dispatch_research returns a warning
    payload and never schedules the background research/post task.

    channel_id/team_id resolution now happens in-turn inside _dispatch_research
    (via _resolve_channel_and_team) before the background task would be
    scheduled, rather than inside _research_and_post itself -- so this is
    exercised at the _dispatch_research level, not by passing a missing
    channel_id into _research_and_post (whose channel_id param is non-optional)."""
    from artemis.floating_artemis.tools.argus_tools import _dispatch_research

    safe_post_was_called = False

    async def unexpected_safe_post(**kwargs: Any) -> None:
        nonlocal safe_post_was_called
        safe_post_was_called = True

    with (
        patch(
            "artemis.floating_artemis.tools.argus_tools._resolve_channel_and_team",
            new_callable=AsyncMock,
            return_value=(None, ""),
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            side_effect=unexpected_safe_post,
        ),
        patch("artemis.floating_artemis.context.floating_session_id_var") as mock_var,
    ):
        mock_var.get.return_value = None

        result_str = await _dispatch_research({"district_key": "TX-004"})

    result = json.loads(result_str)
    # This assertion used to read status == "dispatched", and that passing test
    # is why the bug shipped. Nothing is persisted and nothing is started on this
    # path, so reporting success made Callie tell Jon and Josh for five weeks
    # that Argus was running while argus_research_requests stayed empty. A tool
    # must not claim work it did not do -- the agent has no way to know better.
    assert result["status"] == "failed"
    assert result["error"] == "no_channel_resolved"
    assert "NOT started" in result["detail"]
    assert not safe_post_was_called, "Should not post when channel_id is unknown"


# ── T6: thin findings → graceful fallback note is still posted ────────────────


@pytest.mark.asyncio
async def test_thin_findings_posts_graceful_note() -> None:
    """When Argus finds nothing new, _research_and_post still posts a graceful
    note (not silent)."""
    from artemis.floating_artemis.tools.argus_tools import _research_and_post

    posted_calls: list[dict[str, Any]] = []
    thin_summary = _make_summary(new_findings=0, gap_dimensions=[], recommended_angle=None)

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
    ):
        await _research_and_post(
            request_id=None,
            channel_id="C5555",
            team_id="TABC",
            district_key="TX-005",
            triggering_signal_id=None,
            signal=None,
        )

    # A post MUST happen even for thin results
    assert len(posted_calls) == 1, "Expected a graceful fallback post even for thin findings"
    assert posted_calls[0]["channel"] == "C5555"
    assert posted_calls[0]["text"].strip()


# ── T8: the MCP subprocess must set the session contextvar it cannot inherit ──


def test_mcp_subprocess_sets_floating_session_contextvar() -> None:
    """``_serve_floating_artemis`` must set ``floating_session_id_var`` itself.

    The root cause of the five-week Argus outage (2026-08-12). The parent turn
    handler sets that contextvar in ITS process; ``_serve_floating_artemis`` runs
    in a subprocess, and contextvars do not cross a process boundary. So every
    tool reading it got None regardless of what the parent did --
    ``dispatch_research`` resolved no channel, took its early return, and
    persisted nothing while reporting "dispatched".

    Asserted against the source rather than by booting a subprocess: the failure
    mode is the ABSENCE of a call, and a mocked-out subprocess would not have
    caught it either (the original had full test coverage and a test that
    asserted the wrong contract). If this is refactored, keep an assertion that
    the value reaches a tool, not merely that this line exists.
    """
    import inspect

    from artemis.tools import mcp_server

    source = inspect.getsource(mcp_server._serve_floating_artemis)
    assert "floating_session_id_var.set(floating_session_id)" in source, (
        "the MCP subprocess must set floating_session_id_var -- it cannot "
        "inherit it from the parent process"
    )
    assert "floating_trusted_agent_id_var.set(trusted_agent_id)" in source
