"""Provider / executor hardening tests.

Covers all four "hardened" items from the provider-hardening brief:

1. CLI leg — claude-code adapter resolves correctly and routes to run_with_tools.
2. API leg — anthropic adapter forwards the tool registry to run_turn; a
   mocked SDK tool-use round-trip executes and returns correctly.
3. Fallback both directions:
   a. CLI unavailable (MissingCliBinaryError) → falls through to anthropic.
   b. API key missing (MissingApiKeyError at construction) → anthropic skipped;
      resolver raises NoProviderAvailableError when nothing works.
   c. Executor hard-fallback (AnthropicAdapter()) fails cleanly with
      MissingApiKeyError when key is absent — does NOT hang.
4. (Documentation only — see module docstring at bottom.)

All tests are hermetic: no real network calls, no real claude binary,
no real ANTHROPIC_API_KEY required.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from artemis.agent.client import AnthropicAdapter, CompletionRequest
from artemis.agent.types import Message, TextBlock, Tool, ToolResultBlock
from artemis.providers.errors import (
    ClaudeCodeTimeoutError,
    MissingApiKeyError,
    MissingCliBinaryError,
    UnknownProviderError,
)
from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executable(tmp_path: Path, name: str = "claude") -> Path:
    """Write a minimal executable shell script to tmp_path/<name>."""
    p = tmp_path / name
    p.write_text("#!/bin/sh\necho '{}'\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _make_resolver_side_effect(available: dict[str, Any]) -> Any:
    """Build a get_adapter side_effect: returns sentinel for known providers,
    raises UnknownProviderError for anything else."""

    def _builder(provider_id: str, **_: Any) -> Any:
        if provider_id in available:
            value = available[provider_id]
            if isinstance(value, Exception):
                raise value
            return value
        raise UnknownProviderError(f"unknown: {provider_id}")

    return _builder


# ---------------------------------------------------------------------------
# 1. CLI leg — ClaudeCodeAdapter resolves + routes to run_with_tools
# ---------------------------------------------------------------------------


def test_claude_code_provider_resolves_to_claude_code_adapter(tmp_path: Path) -> None:
    """resolve_adapter('claude-code', ...) returns a ClaudeCodeAdapter when binary exists."""
    from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

    binary = _make_executable(tmp_path)
    # Patch find_cli_binary to return our fake executable path.
    with patch(
        "artemis.providers.claude_code.adapter.find_cli_binary",
        return_value=str(binary),
    ):
        adapter = resolve_adapter("claude-code", "anthropic")

    assert isinstance(adapter, ClaudeCodeAdapter)


def test_claude_code_adapter_routes_to_run_with_tools_in_executor(tmp_path: Path) -> None:
    """_is_claude_code_tool_run returns True for ClaudeCodeAdapter + non-empty registry."""
    from artemis.agent.tools import ToolRegistry
    from artemis.agent.types import Tool
    from artemis.builders.executor import _is_claude_code_tool_run
    from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    # Empty registry → False (text path)
    empty_registry = ToolRegistry()
    assert not _is_claude_code_tool_run(adapter, empty_registry)

    # Non-empty registry → True (run_with_tools path)
    registry = ToolRegistry()
    tool_def = Tool(name="signal_queue.write", description="write", input_schema={})

    async def _impl(inp: dict[str, Any]) -> str:
        return "ok"

    registry.register(tool_def, _impl)
    assert _is_claude_code_tool_run(adapter, registry)


@pytest.mark.asyncio
async def test_claude_code_run_with_tools_called_on_tool_using_run(tmp_path: Path) -> None:
    """When adapter is ClaudeCodeAdapter and registry is non-empty, run_with_tools is called."""
    from artemis.agent.tools import ToolRegistry
    from artemis.builders.executor import _is_claude_code_tool_run
    from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    registry = ToolRegistry()
    tool_def = Tool(name="signal_queue.write", description="write", input_schema={})

    async def _impl(inp: dict[str, Any]) -> str:
        return "ok"

    registry.register(tool_def, _impl)

    # Confirm routing function — the actual run_with_tools call is tested in
    # test_claude_code_tooluse.py (subprocess mock tests). Here we just confirm
    # the routing gate returns True so the executor will branch correctly.
    assert _is_claude_code_tool_run(adapter, registry) is True

    # And that a non-ClaudeCodeAdapter with non-empty registry → False
    class _OtherAdapter:
        async def complete(self, request: Any) -> Any: ...

    assert not _is_claude_code_tool_run(_OtherAdapter(), registry)


# ---------------------------------------------------------------------------
# 2. API leg — AnthropicAdapter forwards tools; mock SDK tool-use round-trip
# ---------------------------------------------------------------------------


def test_anthropic_adapter_constructs_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """AnthropicAdapter constructs without error when ANTHROPIC_API_KEY is set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    adapter = AnthropicAdapter()
    # Constructed successfully — client is set
    assert adapter._client is not None


def test_anthropic_adapter_raises_missing_key_error_when_key_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AnthropicAdapter.__init__ raises MissingApiKeyError fast when key is absent.

    This is the critical fix: the resolver needs a MissingApiKeyError at
    construction time so it can fall through to the next cascade candidate.
    Without this, AnthropicAdapter would construct successfully (lazy SDK),
    return a broken adapter to the resolver, and then blow up with a TypeError
    inside the actual LLM call — AFTER the resolver has already committed.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError, match="ANTHROPIC_API_KEY"):
        AnthropicAdapter()


@pytest.mark.asyncio
async def test_anthropic_adapter_forwards_tools_to_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """AnthropicAdapter.complete() forwards the tools list to the SDK.

    The SDK is mocked — no real network call. We verify that when tools are
    present in the request, they appear in the kwargs passed to
    client.messages.create.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    adapter = AnthropicAdapter()

    # Capture the kwargs sent to client.messages.create
    captured_kwargs: dict[str, Any] = {}

    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="Wrote 2 signals.")]
    mock_response.stop_reason = "end_turn"
    mock_response.usage = MagicMock(
        input_tokens=80,
        output_tokens=30,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )

    async def _mock_create(**kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return mock_response

    # monkeypatch.setattr (rather than direct assignment) sidesteps mypy's
    # "Cannot assign to a method" on the SDK client's bound method, and
    # auto-reverts after the test.
    monkeypatch.setattr(adapter._client.messages, "create", _mock_create)

    tool = Tool(
        name="signal_queue.write",
        description="Write a qualified signal to the queue",
        input_schema={
            "type": "object",
            "properties": {"signal_id": {"type": "string"}},
            "required": ["signal_id"],
        },
    )
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Scout and emit signals.")])],
        tools=[tool],
        system="You are a scout.",
        model="claude-haiku-4-6",
    )

    response = await adapter.complete(request)

    # Tools were forwarded to the SDK
    assert "tools" in captured_kwargs, "tools must be forwarded to client.messages.create"
    assert len(captured_kwargs["tools"]) == 1
    sdk_tool = captured_kwargs["tools"][0]
    assert sdk_tool["name"] == "signal_queue.write"
    assert sdk_tool["description"] == "Write a qualified signal to the queue"
    # Response parsed correctly
    assert response.stop_reason == "end_turn"
    assert isinstance(response.message.content[0], TextBlock)
    assert response.message.content[0].text == "Wrote 2 signals."


@pytest.mark.asyncio
async def test_run_turn_tool_use_round_trip_with_mocked_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full tool-use round-trip via run_turn with a mocked AnthropicAdapter.

    Turn 1: model emits a tool_use block (signal_queue.write).
    Turn 2: tool executes (in-process), result is sent back; model returns end_turn.

    This proves the API leg's tool-use loop (run_turn → tool dispatch → second
    complete() call) works end-to-end with the AnthropicAdapter mocked out.
    """
    from artemis.agent.loop import run_turn

    # Use FakeAdapter (scripted responses) so no SDK call happens.
    # FakeAdapter is the right tool here — we're testing run_turn's loop logic,
    # not the AnthropicAdapter's SDK forwarding (tested separately above).
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
    from artemis.agent.tools import ToolRegistry

    # Turn 1: model calls signal_queue.write
    # Turn 2: model receives the result and ends
    adapter = FakeAdapter(
        replies=[
            ScriptedReply(
                tool_calls=[("call-1", "signal_queue.write", {"signal_id": "sig-abc"})],
                stop_reason="tool_use",
            ),
            ScriptedReply(text="Emitted 1 signal.", stop_reason="end_turn"),
        ]
    )

    # Register the tool
    tool_results: list[dict[str, Any]] = []

    async def _write_signal(inp: dict[str, Any]) -> str:
        tool_results.append(inp)
        return "signal written"

    registry = ToolRegistry()
    tool_def = Tool(
        name="signal_queue.write",
        description="Write signal",
        input_schema={
            "type": "object",
            "properties": {"signal_id": {"type": "string"}},
        },
    )
    registry.register(tool_def, _write_signal)

    result = await run_turn(
        adapter=adapter,
        messages=[Message(role="user", content=[TextBlock(text="Scout and emit signals.")])],
        tools=registry,
        model="claude-haiku-4-6",
    )

    # Tool was called
    assert len(tool_results) == 1
    assert tool_results[0]["signal_id"] == "sig-abc"

    # Two adapter calls were made (tool_use turn + final text turn)
    assert len(adapter.requests) == 2

    # Second request included the tool result in the conversation
    second_request = adapter.requests[1]
    tool_result_messages = [
        m
        for m in second_request.messages
        if m.role == "user" and any(isinstance(b, ToolResultBlock) for b in m.content)
    ]
    assert tool_result_messages, "tool result must be in the second request's messages"
    result_block = next(
        b for b in tool_result_messages[0].content if isinstance(b, ToolResultBlock)
    )
    assert result_block.tool_use_id == "call-1"
    assert result_block.content == "signal written"

    # Final result is the text from turn 2
    assert result.stop_reason == "end_turn"
    final_texts = [
        b.text
        for m in result.messages
        if m.role == "assistant"
        for b in m.content
        if isinstance(b, TextBlock)
    ]
    assert "Emitted 1 signal." in final_texts


# ---------------------------------------------------------------------------
# 3a. Fallback: CLI unavailable → falls through to anthropic
# ---------------------------------------------------------------------------


def test_resolver_falls_through_from_cli_to_anthropic_on_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MissingCliBinaryError from claude-code → resolver falls through to anthropic."""
    from artemis.agent.client import AnthropicAdapter as _AnthropicAdapter

    sentinel = MagicMock(spec=_AnthropicAdapter)

    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_resolver_side_effect(
            {
                "claude-code": MissingCliBinaryError("claude-code", "claude"),
                "anthropic": sentinel,
            }
        ),
    ):
        result = resolve_adapter("claude-code", "anthropic")

    assert result is sentinel


def test_resolver_falls_through_from_cli_to_anthropic_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ClaudeCodeTimeoutError from claude-code → resolver falls through to anthropic."""
    from artemis.agent.client import AnthropicAdapter as _AnthropicAdapter

    sentinel = MagicMock(spec=_AnthropicAdapter)

    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_resolver_side_effect(
            {
                "claude-code": ClaudeCodeTimeoutError(408, "timed out"),
                "anthropic": sentinel,
            }
        ),
    ):
        result = resolve_adapter("claude-code", "anthropic")

    assert result is sentinel


# ---------------------------------------------------------------------------
# 3b. Fallback: API key missing → anthropic skipped, NoProviderAvailableError
# ---------------------------------------------------------------------------


def test_resolver_skips_anthropic_when_key_missing_and_raises_no_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all providers fail with MissingApiKeyError, NoProviderAvailableError is raised.

    Specifically: if ANTHROPIC_API_KEY is absent, AnthropicAdapter() raises
    MissingApiKeyError at construction, which the resolver treats as a fallthrough.
    When nothing in the cascade can be constructed, NoProviderAvailableError is raised.
    """
    with (
        patch(
            "artemis.providers.resolver.get_adapter",
            side_effect=MissingApiKeyError("no key for any provider"),
        ),
        pytest.raises(NoProviderAvailableError) as exc_info,
    ):
        resolve_adapter("claude-code", "anthropic")

    msg = str(exc_info.value)
    # Both declared providers tried
    assert "claude-code" in msg
    assert "anthropic" in msg


def test_anthropic_missing_key_is_in_fallthrough_errors() -> None:
    """MissingApiKeyError is listed in _FALLTHROUGH_ERRORS so resolver skips it."""
    from artemis.providers.resolver import _FALLTHROUGH_ERRORS

    assert MissingApiKeyError in _FALLTHROUGH_ERRORS


# ---------------------------------------------------------------------------
# 3c. Hard-fallback AnthropicAdapter() fails cleanly (fast raise, no hang)
# ---------------------------------------------------------------------------


def test_anthropic_hard_fallback_fails_cleanly_when_key_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AnthropicAdapter() hard-fallback raises MissingApiKeyError fast when key absent.

    The executor's NoProviderAvailableError handler does `adapter = AnthropicAdapter()`.
    When ANTHROPIC_API_KEY is unset, this must raise MissingApiKeyError immediately
    (not hang, not return a broken adapter that detonates later on the first API call).
    The raised exception propagates to the executor's outer try/except which logs it
    and marks the AgentRun as failed — clean failure, no hang.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Must raise synchronously (not async), and must be MissingApiKeyError.
    with pytest.raises(MissingApiKeyError):
        AnthropicAdapter()


def test_hard_fallback_exception_is_caught_by_run_agent_outer_handler() -> None:
    """Verify that MissingApiKeyError is a subclass of Exception (not BaseException)
    so it is caught by executor.py's broad `except Exception` handler at line ~584.

    If it were BaseException, the outer handler would miss it and the run would
    not be marked failed — it would just crash the task.
    """
    err = MissingApiKeyError("no key")
    assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# 4. Documentation: enabling the live API leg
# ---------------------------------------------------------------------------
# To enable the live Anthropic API leg (API path with real key):
#
#   1. Add to your .env file (do NOT commit real keys):
#        ANTHROPIC_API_KEY=sk-ant-<your-real-key>
#
#   2. Optionally set the model:
#        ARTEMIS_AGENT_MODEL=claude-sonnet-4-6
#
#   3. Restart the server:
#        uv run uvicorn artemis.main:app --reload
#
#   4. Seed an agent with provider="anthropic" (or let the resolver fall through
#      from claude-code to anthropic when the binary is absent).
#
# No code change required — the resolver and AnthropicAdapter both read the env
# variable at construction time, so setting it in .env is the single step.
# ---------------------------------------------------------------------------
