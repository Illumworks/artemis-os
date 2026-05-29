"""CC17 — Snapshot extraction reads from tool_invocations (and falls back).

Part D tests: confirms that _build_snapshot prefers the tool_invocations table
(MCP path) and only falls back to message-walking when that table is empty
(anthropic in-process path, CC16 regression).

These are unit tests of _build_snapshot — no DB session required for the
message-walking path.  The MCP path tests use a real DB session via db_session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builder.trajectory_summarizer import AgentRunSnapshot
from artemis.builders.executor import _build_snapshot

# ── Shared helpers ────────────────────────────────────────────────────────────


def _fake_run(run_id: str = "snap-test-run-0001", agent_id: str = "test.agent") -> Any:
    """Return a minimal duck-typed AgentRun substitute."""
    run = MagicMock()
    run.run_id = run_id
    run.id = 1
    run.agent_id = agent_id
    run.status = "completed"
    run.user_message = "test user message"
    run.error = None
    run.started_at = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    run.completed_at = datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
    return run


def _fake_result_with_tools() -> Any:
    """Minimal RunResult with two ToolUseBlock+ToolResultBlock pairs in messages."""
    from artemis.agent.types import Message, ToolResultBlock, ToolUseBlock

    tool_use = ToolUseBlock(id="tu-1", name="news_api.search", input={"query": "test"})
    tool_result = ToolResultBlock(tool_use_id="tu-1", content="headline found", is_error=False)
    assistant_msg = Message(role="assistant", content=[tool_use])
    user_msg = Message(role="user", content=[tool_result])

    result = MagicMock()
    result.messages = [assistant_msg, user_msg]
    return result


def _fake_invocation(tool_name: str, success: bool, result_preview: str = "preview") -> Any:
    """Minimal ToolInvocation-shaped object."""
    inv = MagicMock()
    inv.tool_name = tool_name
    inv.success = success
    inv.result_preview = result_preview
    return inv


# ── Test 4: snapshot reads from tool_invocations when non-empty ──────────────


def test_snapshot_uses_mcp_invocations_when_present() -> None:
    """When mcp_invocations is non-empty, tool_calls comes from it (MCP path)."""
    run = _fake_run(run_id="snap-mcp-run-0001")
    result = MagicMock()
    result.messages = []  # deliberately empty — MCP path has no messages

    invocations = [
        _fake_invocation("signal_queue.write", True, '{"status": "written"}'),
        _fake_invocation("news_api.search", True, "3 articles found"),
    ]

    snapshot = _build_snapshot(run, result, signals_emitted=1, mcp_invocations=invocations)

    assert isinstance(snapshot, AgentRunSnapshot)
    assert len(snapshot.tool_calls) == 2
    assert snapshot.tool_calls[0].name == "signal_queue.write"
    assert snapshot.tool_calls[0].success is True
    assert snapshot.tool_calls[1].name == "news_api.search"
    assert snapshot.signals_emitted == 1


def test_snapshot_tool_calls_ordered_as_provided() -> None:
    """tool_calls preserves the order of mcp_invocations (invoked_at order)."""
    run = _fake_run(run_id="snap-order-run-0001")
    result = MagicMock()
    result.messages = []

    invocations = [
        _fake_invocation("news_api.search", True, "first"),
        _fake_invocation("signal_queue.write", True, "second"),
        _fake_invocation("memory_layer.upsert_last_seen", True, "third"),
    ]

    snapshot = _build_snapshot(run, result, signals_emitted=1, mcp_invocations=invocations)

    names = [tc.name for tc in snapshot.tool_calls]
    assert names == ["news_api.search", "signal_queue.write", "memory_layer.upsert_last_seen"]


def test_snapshot_failed_invocation_reflected() -> None:
    """success=False in a tool_invocations row propagates to the snapshot."""
    run = _fake_run(run_id="snap-fail-run-0001")
    result = MagicMock()
    result.messages = []

    invocations = [
        _fake_invocation("signal_queue.write", False, "PERMISSION_DENIED: ..."),
    ]

    snapshot = _build_snapshot(run, result, signals_emitted=0, mcp_invocations=invocations)

    assert len(snapshot.tool_calls) == 1
    assert snapshot.tool_calls[0].success is False
    assert "PERMISSION_DENIED" in snapshot.tool_calls[0].result_preview


# ── Test 5: fallback to message-walking when tool_invocations is empty ───────


def test_snapshot_falls_back_to_messages_when_invocations_empty() -> None:
    """When mcp_invocations is empty, tool_calls is extracted from result.messages.

    This is the CC16 anthropic in-process path — must remain intact.
    """
    run = _fake_run(run_id="snap-fallback-run-001")
    result = _fake_result_with_tools()

    # Empty invocations list → falls back to message-walking.
    snapshot = _build_snapshot(run, result, signals_emitted=0, mcp_invocations=[])

    assert len(snapshot.tool_calls) == 1
    assert snapshot.tool_calls[0].name == "news_api.search"
    assert snapshot.tool_calls[0].success is True


def test_snapshot_falls_back_when_mcp_invocations_is_none() -> None:
    """None mcp_invocations also triggers the fallback path."""
    run = _fake_run(run_id="snap-none-run-00001")
    result = _fake_result_with_tools()

    snapshot = _build_snapshot(run, result, signals_emitted=0, mcp_invocations=None)

    assert len(snapshot.tool_calls) == 1
    assert snapshot.tool_calls[0].name == "news_api.search"


def test_snapshot_empty_result_messages_and_empty_invocations() -> None:
    """Both paths empty → tool_calls is empty tuple. No crash."""
    run = _fake_run(run_id="snap-empty-run-00001")
    result = MagicMock()
    result.messages = []

    snapshot = _build_snapshot(run, result, signals_emitted=0, mcp_invocations=[])

    assert snapshot.tool_calls == ()


# ── Test 6: existing field shapes preserved (CC16 regression) ────────────────


def test_snapshot_duration_ms_computed() -> None:
    """duration_ms is computed from started_at / completed_at (CC16 regression)."""
    run = _fake_run(run_id="snap-dur-run-00001")
    result = MagicMock()
    result.messages = []

    snapshot = _build_snapshot(run, result, signals_emitted=0, mcp_invocations=[])

    assert snapshot.duration_ms == 5000  # 5 seconds from fake run


def test_snapshot_preserves_metadata_fields() -> None:
    """run_id, run_pk, agent_id, status preserved in snapshot (CC16 regression)."""
    run = _fake_run(run_id="snap-meta-run-00001", agent_id="marketing.scout.regional_news")
    result = MagicMock()
    result.messages = []

    snapshot = _build_snapshot(run, result, signals_emitted=2, mcp_invocations=[])

    assert snapshot.run_id == "snap-meta-run-00001"
    assert snapshot.agent_id == "marketing.scout.regional_news"
    assert snapshot.status == "completed"
    assert snapshot.signals_emitted == 2


# ── DB test: snapshot extraction reads from real DB via tool_invocations ──────


@pytest.mark.asyncio
async def test_snapshot_reads_from_db_tool_invocations(db_session: AsyncSession) -> None:
    """Integration: tool_invocations written to DB are queried by run_agent path.

    Simulates what executor.run_agent does after session.commit():
      1. Insert ToolInvocation rows for a run_id.
      2. Query them back ordered by invoked_at.
      3. Build snapshot — tool_calls reflects DB rows.
    """
    from sqlalchemy import insert

    from artemis.tools.models import ToolInvocation

    run_id = "snap-db-run-00000001"

    # Insert two invocation rows with known timestamps.
    await db_session.execute(
        insert(ToolInvocation).values(
            agent_run_id=run_id,
            pipeline_run_id=None,
            tool_name="news_api.search",
            args_summary='{"query": "test"}',
            result_preview="3 articles",
            success=True,
        )
    )
    await db_session.execute(
        insert(ToolInvocation).values(
            agent_run_id=run_id,
            pipeline_run_id=None,
            tool_name="signal_queue.write",
            args_summary="{}",
            result_preview='{"status": "written"}',
            success=True,
        )
    )
    await db_session.commit()

    # Re-query the rows (mimics executor post-commit query).
    inv_result = await db_session.execute(
        select(ToolInvocation)
        .where(ToolInvocation.agent_run_id == run_id)
        .order_by(ToolInvocation.invoked_at)
    )
    mcp_invocations: list[Any] = list(inv_result.scalars().all())

    run = _fake_run(run_id=run_id)
    result = MagicMock()
    result.messages = []

    snapshot = _build_snapshot(run, result, signals_emitted=1, mcp_invocations=mcp_invocations)

    assert len(snapshot.tool_calls) == 2
    # Both tools present; signal_queue.write appears somewhere.
    names = {tc.name for tc in snapshot.tool_calls}
    assert "signal_queue.write" in names
    assert "news_api.search" in names
    assert snapshot.signals_emitted == 1
