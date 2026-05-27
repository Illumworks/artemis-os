"""Routing tests for run_agent's claude-code tool-use branch (stream CC2).

Verifies the ONE routing branch added to ``run_agent``:
  * claude-code provider + tool-using agent  → ``adapter.run_with_tools``
  * anthropic provider + tools               → ``run_turn`` (unchanged)
  * claude-code provider + NO tools          → text path (``run_turn`` → complete)
  * a ``run_with_tools`` failure             → the run row is marked 'failed'

No real ``claude`` binary or LLM is invoked — adapters/methods are mocked.
Requires a running Postgres at ARTEMIS_TEST_DB_URL, migrated to head.
"""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import CompletionResponse
from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.agent.types import Message, TextBlock, Usage
from artemis.builders import repository as repo
from artemis.builders.executor import run_agent
from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

pytestmark = pytest.mark.asyncio

# A known, registered tool so the run_agent tool registry is non-empty.
_KNOWN_TOOL = "news_api.search"


def _claude_code_adapter(tmp_path: Path) -> ClaudeCodeAdapter:
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return ClaudeCodeAdapter(binary_path=str(binary))


def _completion(text: str = "Wrote signals.") -> CompletionResponse:
    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5),
    )


async def _make_agent(session: AsyncSession, agent_id: str, *, tools: list[str]) -> None:
    async with session.begin():
        await repo.create_agent(
            session,
            agent_id=agent_id,
            name="Routing Agent",
            goal="Find signals.",
            system_prompt="You are a scout.",
            tools=tools,
            model="claude-haiku-4-6",
        )


# ── claude-code + tools → run_with_tools ─────────────────────────────────────────


async def test_claude_code_with_tools_uses_run_with_tools(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _make_agent(db_session, "cc-tool-agent", tools=[_KNOWN_TOOL])
    adapter = _claude_code_adapter(tmp_path)
    rwt = AsyncMock(return_value=_completion("Tool run done."))

    with (
        patch.object(adapter, "run_with_tools", rwt),
        patch("artemis.builders.executor.run_turn", new=AsyncMock()) as run_turn_mock,
    ):
        run = await run_agent(session=db_session, agent_id="cc-tool-agent", model_adapter=adapter)
    await db_session.commit()

    assert run.status == "completed"
    rwt.assert_awaited_once()
    run_turn_mock.assert_not_called()
    # Correct interface contract passed through.
    assert rwt.await_args is not None
    kwargs = rwt.await_args.kwargs
    assert kwargs["agent_id"] == "cc-tool-agent"
    assert kwargs["agent_tools"] == [_KNOWN_TOOL]
    assert "run_id" in kwargs and "pipeline_run_id" in kwargs

    ctx = await repo.get_agent_context(db_session, run.run_id, "final_response")
    assert "Tool run done." in ctx.value


# ── anthropic + tools → run_turn (unchanged) ─────────────────────────────────────


async def test_anthropic_with_tools_uses_run_turn(db_session: AsyncSession) -> None:
    await _make_agent(db_session, "anthropic-tool-agent", tools=[_KNOWN_TOOL])
    # A FakeAdapter is NOT a ClaudeCodeAdapter → must go through run_turn.
    adapter = FakeAdapter([ScriptedReply(text="Answered via run_turn.")])

    run = await run_agent(
        session=db_session, agent_id="anthropic-tool-agent", model_adapter=adapter
    )
    await db_session.commit()

    assert run.status == "completed"
    # run_turn drives the adapter's .complete (FakeAdapter records the request).
    assert len(adapter.requests) == 1
    ctx = await repo.get_agent_context(db_session, run.run_id, "final_response")
    assert "Answered via run_turn." in ctx.value


# ── claude-code + NO tools → text path (run_turn → complete) ─────────────────────


async def test_claude_code_without_tools_uses_text_path(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _make_agent(db_session, "cc-notool-agent", tools=[])
    adapter = _claude_code_adapter(tmp_path)
    # If run_with_tools were called, this would raise and fail the run.
    boom = AsyncMock(side_effect=AssertionError("run_with_tools must NOT be called"))
    complete = AsyncMock(return_value=_completion("Plain text answer."))

    with (
        patch.object(adapter, "run_with_tools", boom),
        patch.object(adapter, "complete", complete),
    ):
        run = await run_agent(session=db_session, agent_id="cc-notool-agent", model_adapter=adapter)
    await db_session.commit()

    assert run.status == "completed"
    boom.assert_not_called()
    complete.assert_awaited()  # run_turn called the text completion
    ctx = await repo.get_agent_context(db_session, run.run_id, "final_response")
    assert "Plain text answer." in ctx.value


# ── run_with_tools failure → run marked 'failed' ─────────────────────────────────


async def test_run_with_tools_failure_marks_run_failed(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    from artemis.providers.errors import ProviderAPIError

    await _make_agent(db_session, "cc-fail-agent", tools=[_KNOWN_TOOL])
    adapter = _claude_code_adapter(tmp_path)
    rwt = AsyncMock(side_effect=ProviderAPIError(1, "claude exploded"))

    with patch.object(adapter, "run_with_tools", rwt):
        run = await run_agent(session=db_session, agent_id="cc-fail-agent", model_adapter=adapter)
    await db_session.commit()

    assert run.status == "failed"
    assert run.error is not None
    assert "ProviderAPIError" in run.error
    rwt.assert_awaited_once()
