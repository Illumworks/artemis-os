"""Tests for agent_traces recording in artemis.builders.executor (OBS-2).

OBS-1 wired capture_trace() into the conversational path only
(artemis/floating_artemis/chat.py). Pipeline/builder agent runs — everything
that goes through run_agent() — wrote NO agent_traces row at all, on either
provider path. These tests verify run_agent() now writes exactly one row per
run, on both the claude-code (run_with_tools/CC2) and Anthropic (run_turn)
paths, with tools_used populated the same way chat.py's turns are, and on
both success and failure.

Requires ARTEMIS_TEST_DB_URL pointing at a DB migrated to head (agent_traces
predates this change — no migration is added here).
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import CompletionResponse
from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.agent.types import Message, TextBlock, ToolCallRecord, Usage
from artemis.builders import repository as repo
from artemis.builders.executor import run_agent
from artemis.builders.models import AgentTrace
from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

pytestmark = pytest.mark.asyncio

_KNOWN_TOOL = "news_api.search"


def _claude_code_adapter(tmp_path: Path) -> ClaudeCodeAdapter:
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return ClaudeCodeAdapter(binary_path=str(binary))


async def _make_agent(session: AsyncSession, agent_id: str, *, tools: list[str]) -> None:
    async with session.begin():
        await repo.create_agent(
            session,
            agent_id=agent_id,
            name="Trace Test Agent",
            goal="Find signals.",
            system_prompt="You are a scout.",
            tools=tools,
            model="claude-haiku-4-6",
        )


async def _traces_for(session: AsyncSession, run_id: str) -> list[AgentTrace]:
    result = await session.execute(select(AgentTrace).where(AgentTrace.session_id == run_id))
    return list(result.scalars().all())


# ── claude-code (run_with_tools/CC2) path ─────────────────────────────────────


async def test_claude_code_run_writes_trace_with_tool_calls(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """The core OBS-2 case: a claude-code pipeline/scout run that called tools
    (one clean, one that errored) must land both on agent_traces.tools_used,
    exactly like the conversational path already does post-OBS-1."""
    await _make_agent(db_session, "cc-trace-agent", tools=[_KNOWN_TOOL])
    adapter = _claude_code_adapter(tmp_path)
    completion = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text="Wrote 1 signal.")]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=42, output_tokens=17),
        tool_calls=[
            ToolCallRecord(name="list_candidates", is_error=False),
            ToolCallRecord(name="dispatch_research", is_error=True),
        ],
    )
    rwt = AsyncMock(return_value=completion)

    with patch.object(adapter, "run_with_tools", rwt):
        run = await run_agent(session=db_session, agent_id="cc-trace-agent", model_adapter=adapter)
    await db_session.commit()

    assert run.status == "completed"

    traces = await _traces_for(db_session, run.run_id)
    assert len(traces) == 1
    trace = traces[0]
    assert trace.agent_id == "cc-trace-agent"
    assert trace.feature_tag == "agent_run"
    assert trace.provider == "claude-code"
    assert trace.outcome == "success"
    assert trace.tools_used == ["list_candidates", "dispatch_research:error"]
    assert trace.input_tokens == 42
    assert trace.output_tokens == 17
    assert trace.latency_ms is not None and trace.latency_ms >= 0
    assert trace.output_summary is not None and "Wrote 1 signal." in trace.output_summary


async def test_claude_code_run_with_no_tool_calls_writes_empty_list(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A claude-code run that genuinely called no tools records [] — a
    positive "zero calls" signal, not the absence of a row."""
    await _make_agent(db_session, "cc-trace-notools-agent", tools=[_KNOWN_TOOL])
    adapter = _claude_code_adapter(tmp_path)
    completion = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text="Nothing to report.")]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=5, output_tokens=3),
    )
    rwt = AsyncMock(return_value=completion)

    with patch.object(adapter, "run_with_tools", rwt):
        run = await run_agent(
            session=db_session, agent_id="cc-trace-notools-agent", model_adapter=adapter
        )
    await db_session.commit()

    traces = await _traces_for(db_session, run.run_id)
    assert len(traces) == 1
    assert traces[0].tools_used == []


# ── Anthropic (run_turn) path ─────────────────────────────────────────────────


async def test_anthropic_run_writes_trace_from_tool_use_blocks(db_session: AsyncSession) -> None:
    """The Anthropic path's tool calls show up as ToolUseBlocks in
    result.messages (not CompletionResponse.tool_calls) — collect_tools_used
    must scan that source too, unchanged from what chat.py already did."""
    await _make_agent(db_session, "anthropic-trace-agent", tools=[])
    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("call_1", "query_memory", {})],
                stop_reason="tool_use",
            ),
            ScriptedReply(text="Done."),
        ]
    )

    run = await run_agent(
        session=db_session, agent_id="anthropic-trace-agent", model_adapter=adapter
    )
    await db_session.commit()

    assert run.status == "completed"

    traces = await _traces_for(db_session, run.run_id)
    assert len(traces) == 1
    trace = traces[0]
    assert trace.provider == "anthropic"
    assert trace.outcome == "success"
    assert trace.tools_used == ["query_memory"]


# ── Failure path ───────────────────────────────────────────────────────────────


async def test_failed_run_still_writes_error_trace(db_session: AsyncSession) -> None:
    """Lossless recording, mirroring the existing error cost_event: a run
    that raises before producing a RunResult still gets an agent_traces row
    (outcome="error", tools_used=[] since no completion ever happened)."""

    class BoomAdapter:
        async def complete(self, request: Any) -> Any:
            raise RuntimeError("Simulated LLM failure")

        async def run_with_tools(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Simulated LLM failure")

    await _make_agent(db_session, "boom-trace-agent", tools=[])

    run = await run_agent(
        session=db_session, agent_id="boom-trace-agent", model_adapter=BoomAdapter()
    )
    await db_session.commit()

    assert run.status == "failed"

    traces = await _traces_for(db_session, run.run_id)
    assert len(traces) == 1
    trace = traces[0]
    assert trace.outcome == "error"
    assert trace.error is not None and "RuntimeError" in trace.error
    assert trace.tools_used == []
