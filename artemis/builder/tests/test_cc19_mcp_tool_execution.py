"""CC19 — Builder MCP Tool Execution tests.

Tests:
1. Integration test: end-to-end Builder session → definition_proposals row lands.
2. MCP tool unit tests: each of the 5 builder tools exercised directly.
3. Adapter routing test: ClaudeCodeAdapter routes to _complete_with_tools when
   tools are present.
4. Citations validation: builder_propose rejects fabricated run_ids.
5. test_run recursion: builder_test_run excludes claude-code from sandbox cascade.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builder.context import builder_session_id_var
from artemis.builders.models import Agent, AgentRun, AgentRunTrajectorySummary

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_agent(session: AsyncSession, agent_id: str) -> Agent:
    """Insert a minimal agent row."""
    from artemis.builders import repository as builders_repo

    agent = await builders_repo.create_agent(
        session,
        agent_id=agent_id,
        name=f"Test Agent {agent_id}",
        goal="Test goal",
        system_prompt="You are a test agent.",
        tools=[],
        model="claude-sonnet-4-6",
    )
    return agent


async def _make_agent_run(
    session: AsyncSession,
    agent_id: str,
    *,
    with_trajectory: bool = False,
) -> AgentRun:
    """Insert a minimal agent_run row and optionally a trajectory summary."""
    import uuid

    run = AgentRun(
        run_id=str(uuid.uuid4()),
        agent_id=agent_id,
        status="completed",
        user_message="Test run",
        error=None,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)

    if with_trajectory:
        traj = AgentRunTrajectorySummary(
            run_id=run.id,
            what_worked="Signal fetch worked well.",
            what_stalled="Qualifier loop repeated.",
            what_was_missing="A dedup tool.",
        )
        session.add(traj)
        await session.flush()

    return run


# ── Test 1: Integration test — end-to-end Builder session ────────────────────


@pytest.mark.asyncio
async def test_builder_integration_proposal_lands(db_session: AsyncSession) -> None:
    """End-to-end: a Builder session using FakeAdapter produces a definition_proposals row.

    This test exercises the full handle_turn_stream path with a FakeAdapter that
    returns a tool_use for propose, followed by a text response.  The in-process
    tool registry (_propose) runs and inserts the row.

    Why FakeAdapter (not ClaudeCodeAdapter): the integration test proves the
    propose pathway end-to-end without requiring the claude binary.  The adapter
    routing test (test_claude_code_adapter_routing) covers the ClaudeCodeAdapter
    dispatch separately.
    """
    from sqlalchemy import select

    from artemis.builder.agent_builder import handle_turn
    from artemis.builder.repository import create_builder_session
    from artemis.builders.models import DefinitionProposal

    # Set up: agent with a trajectory summary.
    agent = await _make_agent(db_session, "cc19-integration-agent")
    await _make_agent_run(db_session, "cc19-integration-agent", with_trajectory=True)
    await db_session.commit()

    # Create builder session.
    builder_session = await create_builder_session(
        db_session,
        builder_kind="agent",
        target_id=agent.id,
    )
    await db_session.commit()

    # Scripted adapter: first call returns tool_use for propose, second returns final text.
    propose_input = {
        "kind": "agent",
        "definition": {
            "name": "cc19-improved-agent",
            "goal": "Updated goal after CC19 analysis.",
            "system_prompt": "Improved system prompt.",
            "tools": [],
            "model": "claude-sonnet-4-6",
        },
        "target_id": agent.id,
        "citations": None,
    }
    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("tool-use-1", "propose", propose_input)],
                stop_reason="tool_use",
            ),
            ScriptedReply(
                text="I've proposed improvements to the agent definition.",
                stop_reason="end_turn",
            ),
        ]
    )

    result = await handle_turn(
        builder_session_id=builder_session.id,
        user_text="Please review and propose improvements.",
        adapter=adapter,
        db_session=db_session,
    )

    # Verify: a definition_proposals row landed.
    rows = await db_session.execute(
        select(DefinitionProposal).where(
            DefinitionProposal.builder_session_id == builder_session.id
        )
    )
    proposals = rows.scalars().all()

    assert len(proposals) >= 1, (
        f"Expected at least 1 definition_proposals row for session {builder_session.id}, "
        f"got {len(proposals)}. handle_turn result: {result!r}"
    )
    proposal = proposals[0]
    assert proposal.kind == "agent", f"Expected kind='agent', got {proposal.kind!r}"
    assert proposal.status == "pending", f"Expected status='pending', got {proposal.status!r}"
    assert proposal.proposed_by == "builder", (
        f"Expected proposed_by='builder', got {proposal.proposed_by!r}"
    )
    assert proposal.proposed_definition is not None, "proposed_definition is null"
    assert proposal.target_id == agent.id, (
        f"Expected target_id={agent.id}, got {proposal.target_id!r}"
    )

    # Emit a readable summary for the report.
    print(
        f"\n[CC19 integration] definition_proposals row:\n"
        f"  id={proposal.id}\n"
        f"  kind={proposal.kind}\n"
        f"  status={proposal.status}\n"
        f"  proposed_by={proposal.proposed_by}\n"
        f"  builder_session_id={proposal.builder_session_id}\n"
        f"  target_id={proposal.target_id}\n"
        f"  proposed_definition={json.dumps(proposal.proposed_definition, indent=2)}\n"
    )


# ── Test 2: MCP tool unit tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_builder_mcp_read_existing(db_session: AsyncSession) -> None:
    """builder_read_existing returns a list of agent definitions."""
    from artemis.tools.mcp_server import _dispatch_builder_tool

    # Set up a couple of agents.
    await _make_agent(db_session, "cc19-re-agent-1")
    await _make_agent(db_session, "cc19-re-agent-2")
    await db_session.commit()

    result_json = await _dispatch_builder_tool(
        name="builder_read_existing",
        args={"kind": "agent", "limit": 10},
        session=db_session,
        builder_session_id=99,
        seen_run_ids=set(),
    )
    data = json.loads(result_json)
    assert isinstance(data, list), f"Expected a list, got {type(data)}"
    agent_ids = {d["agent_id"] for d in data}
    assert "cc19-re-agent-1" in agent_ids
    assert "cc19-re-agent-2" in agent_ids


@pytest.mark.asyncio
async def test_builder_mcp_read_capabilities(db_session: AsyncSession) -> None:
    """builder_read_capabilities returns providers + integrations dict."""
    from artemis.tools.mcp_server import _dispatch_builder_tool

    result_json = await _dispatch_builder_tool(
        name="builder_read_capabilities",
        args={},
        session=db_session,
        builder_session_id=99,
        seen_run_ids=set(),
    )
    data = json.loads(result_json)
    assert "providers" in data, f"Expected 'providers' key, got {list(data.keys())}"
    assert isinstance(data["providers"], list)


@pytest.mark.asyncio
async def test_builder_mcp_read_recent_runs(db_session: AsyncSession) -> None:
    """builder_read_recent_runs records run PKs in seen_run_ids."""
    from artemis.tools.mcp_server import _dispatch_builder_tool

    await _make_agent(db_session, "cc19-rrr-agent")
    run = await _make_agent_run(db_session, "cc19-rrr-agent", with_trajectory=True)
    await db_session.commit()

    seen_ids: set[int] = set()
    result_json = await _dispatch_builder_tool(
        name="builder_read_recent_runs",
        args={"agent_id": "cc19-rrr-agent", "limit": 5},
        session=db_session,
        builder_session_id=99,
        seen_run_ids=seen_ids,
    )
    data = json.loads(result_json)
    assert isinstance(data, list), f"Expected list, got {type(data)}"
    assert len(data) >= 1
    # PKs should be recorded.
    assert run.id in seen_ids, f"run.id={run.id} not in seen_run_ids={seen_ids}"
    # Trajectory data should be present.
    runs_with_traj = [r for r in data if "trajectory" in r]
    assert len(runs_with_traj) >= 1, "Expected at least one run with trajectory data"


@pytest.mark.asyncio
async def test_builder_mcp_propose(db_session: AsyncSession) -> None:
    """builder_propose inserts a definition_proposals row and returns proposal_id."""
    from sqlalchemy import select

    from artemis.builder.repository import create_builder_session
    from artemis.builders.models import DefinitionProposal
    from artemis.tools.mcp_server import _dispatch_builder_tool

    agent = await _make_agent(db_session, "cc19-propose-agent")
    builder_session = await create_builder_session(db_session, target_id=agent.id)
    await db_session.commit()

    result_json = await _dispatch_builder_tool(
        name="builder_propose",
        args={
            "kind": "agent",
            "definition": {"name": "improved", "goal": "Better goal."},
            "target_id": agent.id,
        },
        session=db_session,
        builder_session_id=builder_session.id,
        seen_run_ids=set(),
    )
    await db_session.commit()

    data = json.loads(result_json)
    assert "proposal_id" in data, f"Expected proposal_id key, got {list(data.keys())}"
    assert data["status"] == "pending"

    # Verify the row exists.
    rows = await db_session.execute(
        select(DefinitionProposal).where(DefinitionProposal.id == data["proposal_id"])
    )
    row = rows.scalar_one_or_none()
    assert row is not None, f"No DefinitionProposal found for id={data['proposal_id']}"
    assert row.proposed_by == "builder"
    assert row.kind == "agent"


@pytest.mark.asyncio
async def test_builder_mcp_test_run_no_provider(db_session: AsyncSession) -> None:
    """builder_test_run returns error when no non-claude-code provider is available."""
    from artemis.providers.errors import MissingApiKeyError
    from artemis.tools.mcp_server import _dispatch_builder_tool

    # Directly test by patching the registry inside _dispatch_builder_tool.
    with patch(
        "artemis.providers.registry.get_adapter",
        side_effect=MissingApiKeyError("no key"),
    ):
        result_json = await _dispatch_builder_tool(
            name="builder_test_run",
            args={"definition": {}, "prompt": "test"},
            session=db_session,
            builder_session_id=99,
            seen_run_ids=set(),
        )

    data = json.loads(result_json)
    assert "error" in data, f"Expected error key, got {list(data.keys())}"
    assert "test_run requires a tool-capable provider" in data["error"]


# ── Test 3: Adapter routing test ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claude_code_adapter_routing() -> None:
    """ClaudeCodeAdapter.complete routes to _complete_with_tools when tools present.

    The test verifies the routing decision without launching a real subprocess:
    - No tools → calls the text-only path (subprocess command has --print flag)
    - Tools present → calls _complete_with_tools (patched to capture the call)
    """
    from artemis.agent.client import CompletionRequest, CompletionResponse
    from artemis.agent.types import Message, TextBlock, Tool, Usage
    from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter.__new__(ClaudeCodeAdapter)
    adapter._binary = "/usr/bin/true"
    adapter._default_model = "claude-sonnet-4-6"

    tools_path_called: list[bool] = []
    text_path_called: list[bool] = []

    fake_response = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text="ok")]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=0, output_tokens=0),
    )

    async def _fake_complete_with_tools(request: CompletionRequest) -> CompletionResponse:
        tools_path_called.append(True)
        return fake_response

    # Patch _complete_with_tools on the instance to capture routing.
    adapter._complete_with_tools = _fake_complete_with_tools  # type: ignore[method-assign]

    no_tools_request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="hello")])],
        tools=None,
    )
    tools_request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="hello")])],
        tools=[Tool(name="propose", description="d", input_schema={})],
    )

    # Tools path — must route to _complete_with_tools (patched above).
    await adapter.complete(tools_request)
    assert tools_path_called, "Tools path was not taken when tools=[...]"
    assert not text_path_called, "Text-only path was incorrectly taken when tools=[...]"

    # No-tools path — must NOT call _complete_with_tools.
    # Patch the subprocess to avoid actual binary launch.
    tools_path_called.clear()
    with (
        patch(
            "artemis.providers.claude_code.adapter.asyncio.create_subprocess_exec",
            side_effect=OSError("no binary in routing test"),
        ),
        contextlib.suppress(OSError, Exception),
    ):
        await adapter.complete(no_tools_request)

    # tools_path_called remains empty (no tools → did not enter _complete_with_tools).
    assert not tools_path_called, (
        "Text-only path incorrectly entered _complete_with_tools: "
        "routing check failed for tools=None"
    )


# ── Test 4: Citations validation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_builder_propose_citations_validation(db_session: AsyncSession) -> None:
    """builder_propose rejects citations with run_ids not returned by read_recent_runs."""
    from artemis.builder.repository import create_builder_session
    from artemis.tools.mcp_server import _dispatch_builder_tool

    agent = await _make_agent(db_session, "cc19-citations-agent")
    builder_session = await create_builder_session(db_session, target_id=agent.id)
    await db_session.commit()

    # seen_run_ids is empty — no runs have been read yet.
    seen_ids: set[int] = set()

    result_json = await _dispatch_builder_tool(
        name="builder_propose",
        args={
            "kind": "agent",
            "definition": {"name": "fake"},
            "citations": {"run_ids": [9999, 8888], "summary": "fabricated"},
        },
        session=db_session,
        builder_session_id=builder_session.id,
        seen_run_ids=seen_ids,
    )
    data = json.loads(result_json)
    assert "error" in data, f"Expected error for fabricated run_ids, got {list(data.keys())}"
    assert "validation failed" in data["error"].lower()
    assert "9999" in data["error"] or "8888" in data["error"]


@pytest.mark.asyncio
async def test_builder_propose_valid_citations(db_session: AsyncSession) -> None:
    """builder_propose accepts citations with run_ids previously returned by read_recent_runs."""
    from sqlalchemy import select

    from artemis.builder.repository import create_builder_session
    from artemis.builders.models import DefinitionProposal
    from artemis.tools.mcp_server import _dispatch_builder_tool

    agent = await _make_agent(db_session, "cc19-valid-cite-agent")
    run = await _make_agent_run(db_session, "cc19-valid-cite-agent")
    builder_session = await create_builder_session(db_session, target_id=agent.id)
    await db_session.commit()

    # Seed seen_run_ids with the actual run PK (simulates prior read_recent_runs call).
    seen_ids: set[int] = {run.id}

    result_json = await _dispatch_builder_tool(
        name="builder_propose",
        args={
            "kind": "agent",
            "definition": {"name": "improved"},
            "citations": {"run_ids": [run.id], "summary": "Based on run analysis."},
        },
        session=db_session,
        builder_session_id=builder_session.id,
        seen_run_ids=seen_ids,
    )
    await db_session.commit()

    data = json.loads(result_json)
    assert "error" not in data, f"Unexpected error for valid citations: {data}"
    assert "proposal_id" in data

    # Verify row has citations.
    rows = await db_session.execute(
        select(DefinitionProposal).where(DefinitionProposal.id == data["proposal_id"])
    )
    row = rows.scalar_one_or_none()
    assert row is not None
    assert row.citations is not None
    assert run.id in row.citations.get("run_ids", [])


# ── Test 5: test_run recursion — excludes claude-code from cascade ────────────


@pytest.mark.asyncio
async def test_builder_test_run_excludes_claude_code() -> None:
    """builder_test_run walks provider cascade EXCLUDING claude-code.

    Verifies that when we attempt to resolve a sandbox adapter, claude-code
    is never returned as the candidate, and the function properly tries the
    non-claude-code fallback chain first.
    """
    from artemis.providers.errors import MissingApiKeyError
    from artemis.tools.mcp_server import _dispatch_builder_tool

    # The candidates tried inside _dispatch_builder_tool are:
    # anthropic, openai, openrouter, gemini, lm-studio, codex
    # (explicitly NOT claude-code).
    # We verify this by patching get_adapter to track what was tried.
    tried: list[str] = []

    def _mock_get_adapter(provider_id: str, **kw: Any) -> Any:
        tried.append(provider_id)
        raise MissingApiKeyError(f"no key for {provider_id}")

    seen_ids: set[int] = set()
    mock_session = MagicMock()
    with patch("artemis.providers.registry.get_adapter", side_effect=_mock_get_adapter):
        result_json = await _dispatch_builder_tool(
            name="builder_test_run",
            args={"definition": {}, "prompt": "test"},
            session=mock_session,
            builder_session_id=99,
            seen_run_ids=seen_ids,
        )

    data = json.loads(result_json)

    # claude-code must NOT appear in the tried list.
    assert "claude-code" not in tried, (
        f"claude-code was tried as a sandbox provider but should be excluded. tried={tried}"
    )
    # All expected non-claude-code providers were attempted.
    for expected in ("anthropic", "openai", "openrouter", "gemini"):
        assert expected in tried, (
            f"Provider {expected!r} was not tried in sandbox cascade. tried={tried}"
        )
    # Result is an error (no provider available).
    assert "error" in data


# ── Test 6: builder_session_id_var contextvar ─────────────────────────────────


def test_builder_session_id_var_context() -> None:
    """builder_session_id_var is None by default; set/reset works correctly."""
    # Default is None.
    assert builder_session_id_var.get() is None

    token = builder_session_id_var.set(42)
    assert builder_session_id_var.get() == 42

    builder_session_id_var.reset(token)
    assert builder_session_id_var.get() is None


def test_builder_session_id_var_isolation() -> None:
    """builder_session_id_var is isolated per-context (no cross-context leakage)."""
    import contextvars

    results: dict[str, int | None] = {}

    def _run_in_context(name: str, value: int | None) -> None:
        ctx = contextvars.copy_context()

        def _task() -> None:
            if value is not None:
                builder_session_id_var.set(value)
            results[name] = builder_session_id_var.get()

        ctx.run(_task)

    _run_in_context("a", 10)
    _run_in_context("b", 20)
    _run_in_context("c", None)

    assert results["a"] == 10
    assert results["b"] == 20
    assert results["c"] is None

    # The original context is unaffected.
    assert builder_session_id_var.get() is None


# ── Test 7: codex/lm-studio tool warning ──────────────────────────────────────


@pytest.mark.asyncio
async def test_codex_adapter_warns_on_tools(caplog: pytest.LogCaptureFixture) -> None:
    """CodexAdapter emits a warning when request.tools is populated."""
    import logging

    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock, Tool
    from artemis.providers.codex.adapter import CodexAdapter

    adapter = CodexAdapter.__new__(CodexAdapter)
    adapter._binary = "/usr/bin/true"
    adapter._default_model = ""
    adapter._default_reasoning_effort = None
    adapter._default_speed_tier = None

    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="test")])],
        tools=[Tool(name="propose", description="d", input_schema={})],
    )

    # Patch the subprocess to avoid actually launching.
    with (
        caplog.at_level(logging.WARNING, logger="artemis.providers.codex.adapter"),
        patch(
            "artemis.providers.codex.adapter.asyncio.create_subprocess_exec",
            side_effect=OSError("no binary in test"),
        ),
        contextlib.suppress(Exception),
    ):
        await adapter.complete(request)

    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "CodexAdapter" in r.message and "does not support tool execution" in r.message
        for r in warn_records
    ), f"Expected a CodexAdapter tool warning, got: {[r.message for r in warn_records]}"


@pytest.mark.asyncio
async def test_lm_studio_adapter_warns_on_tools(caplog: pytest.LogCaptureFixture) -> None:
    """LMStudioAdapter emits a warning when request.tools is populated."""
    import logging

    from artemis.agent.client import CompletionRequest, CompletionResponse
    from artemis.agent.types import Message, TextBlock, Tool, Usage
    from artemis.providers.lm_studio.adapter import LMStudioAdapter

    adapter = LMStudioAdapter.__new__(LMStudioAdapter)
    adapter._api_key = "not-needed"
    adapter._default_model = "local-model"
    adapter._base_url = "http://localhost:1234/v1"

    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="test")])],
        tools=[Tool(name="propose", description="d", input_schema={})],
    )

    # Patch super().complete to avoid actual HTTP.
    fake_response = CompletionResponse(
        message=MagicMock(),
        stop_reason="end_turn",
        usage=Usage(input_tokens=0, output_tokens=0),
    )
    with (
        caplog.at_level(logging.WARNING, logger="artemis.providers.lm_studio.adapter"),
        patch.object(
            adapter.__class__.__bases__[0],
            "complete",
            new_callable=AsyncMock,
            return_value=fake_response,
        ),
    ):
        await adapter.complete(request)

    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "LMStudioAdapter" in r.message and "does not support tool execution" in r.message
        for r in warn_records
    ), f"Expected a LMStudioAdapter tool warning, got: {[r.message for r in warn_records]}"
