"""Tests for the agent loop — happy path, tool use, errors, hooks, limits."""

from __future__ import annotations

from typing import Any

import pytest

from artemis.agent import (
    HookRegistry,
    TextBlock,
    Tool,
    ToolCallRecord,
    ToolRegistry,
    ToolResultBlock,
    ToolUseBlock,
    run_turn,
    user_message,
)
from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

pytestmark = pytest.mark.asyncio


# ───── happy path ─────────────────────────────────────────────────────────


async def test_single_turn_no_tools() -> None:
    adapter = FakeAdapter([ScriptedReply(text="hello")])

    result = await run_turn(
        adapter=adapter,
        messages=[user_message("hi")],
    )

    assert result.stop_reason == "end_turn"
    assert result.iterations == 1
    assert len(result.messages) == 2  # user + assistant
    assert result.messages[-1].role == "assistant"
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50


async def test_messages_input_not_mutated() -> None:
    adapter = FakeAdapter([ScriptedReply(text="ok")])
    initial = [user_message("hi")]

    await run_turn(adapter=adapter, messages=initial)

    assert len(initial) == 1, "input messages list must not be mutated"


# ───── tool use ───────────────────────────────────────────────────────────


def _registry_with_echo() -> ToolRegistry:
    registry = ToolRegistry()

    async def echo_impl(input: dict[str, Any]) -> str:
        return f"echo: {input.get('text', '')}"

    registry.register(
        Tool(
            name="echo",
            description="Echo input text",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        ),
        echo_impl,
    )
    return registry


async def test_tool_use_completes_in_one_round() -> None:
    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("call_1", "echo", {"text": "hi"})],
                stop_reason="tool_use",
            ),
            ScriptedReply(text="done"),
        ]
    )

    result = await run_turn(
        adapter=adapter,
        messages=[user_message("use echo")],
        tools=_registry_with_echo(),
    )

    # user + assistant(tool_use) + user(tool_result) + assistant(end)
    assert len(result.messages) == 4
    assert result.iterations == 2
    assert result.stop_reason == "end_turn"

    tool_result_msg = result.messages[2]
    assert tool_result_msg.role == "user"
    assert len(tool_result_msg.content) == 1
    block = tool_result_msg.content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.is_error is False
    assert block.content == "echo: hi"
    assert block.tool_use_id == "call_1"


async def test_unknown_tool_returns_error_block_does_not_crash() -> None:
    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("call_x", "no_such_tool", {})],
                stop_reason="tool_use",
            ),
            ScriptedReply(text="apologies"),
        ]
    )

    result = await run_turn(
        adapter=adapter,
        messages=[user_message("use bogus")],
        tools=ToolRegistry(),  # empty registry
    )

    block = result.messages[2].content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.is_error is True
    assert "not registered" in block.content


async def test_tool_exception_becomes_error_block() -> None:
    registry = ToolRegistry()

    async def bad_impl(input: dict[str, Any]) -> str:
        raise RuntimeError("kaboom")

    registry.register(
        Tool(name="bad", description="raises", input_schema={"type": "object"}),
        bad_impl,
    )
    adapter = FakeAdapter(
        [
            ScriptedReply(tool_calls=[("call_1", "bad", {})], stop_reason="tool_use"),
            ScriptedReply(text="ok"),
        ]
    )

    result = await run_turn(
        adapter=adapter,
        messages=[user_message("use bad")],
        tools=registry,
    )

    block = result.messages[2].content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.is_error is True
    assert "RuntimeError" in block.content
    assert "kaboom" in block.content


async def test_parallel_tool_calls_in_one_response() -> None:
    """Anthropic models may emit several tool_use blocks in one assistant
    message; the loop must execute all of them in one bundle and reply with
    a single user message containing all the tool_results."""
    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[
                    ("call_a", "echo", {"text": "first"}),
                    ("call_b", "echo", {"text": "second"}),
                ],
                stop_reason="tool_use",
            ),
            ScriptedReply(text="combined"),
        ]
    )

    result = await run_turn(
        adapter=adapter,
        messages=[user_message("run two echoes")],
        tools=_registry_with_echo(),
    )

    tool_result_msg = result.messages[2]
    assert len(tool_result_msg.content) == 2
    blocks = tool_result_msg.content
    assert all(isinstance(b, ToolResultBlock) for b in blocks)
    contents = [b.content for b in blocks if isinstance(b, ToolResultBlock)]
    assert contents == ["echo: first", "echo: second"]


# ───── limits ─────────────────────────────────────────────────────────────


async def test_max_iterations_caps_runaway() -> None:
    # Model insists on calling the tool forever; loop must stop at the cap.
    replies = [
        ScriptedReply(
            tool_calls=[(f"call_{i}", "echo", {"text": str(i)})],
            stop_reason="tool_use",
        )
        for i in range(20)
    ]
    adapter = FakeAdapter(replies)

    result = await run_turn(
        adapter=adapter,
        messages=[user_message("loop")],
        tools=_registry_with_echo(),
        max_iterations=3,
    )

    assert result.stop_reason == "max_iterations"
    assert result.iterations == 3


async def test_usage_accumulates_across_iterations() -> None:
    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("c1", "echo", {"text": "x"})],
                stop_reason="tool_use",
                input_tokens=100,
                output_tokens=20,
            ),
            ScriptedReply(text="done", input_tokens=120, output_tokens=10),
        ]
    )

    result = await run_turn(
        adapter=adapter,
        messages=[user_message("hi")],
        tools=_registry_with_echo(),
    )

    assert result.usage.input_tokens == 220
    assert result.usage.output_tokens == 30


# ───── hooks ──────────────────────────────────────────────────────────────


async def test_hooks_fire_in_expected_order() -> None:
    fired: list[str] = []

    hooks = HookRegistry()
    for event in (
        "before_request",
        "after_response",
        "before_tool",
        "after_tool",
        "on_message",
        "on_done",
    ):

        async def _h(_payload: object, _e: str = event) -> None:
            fired.append(_e)

        hooks.on(event, _h)

    adapter = FakeAdapter(
        [
            ScriptedReply(tool_calls=[("c1", "echo", {"text": "x"})], stop_reason="tool_use"),
            ScriptedReply(text="done"),
        ]
    )
    await run_turn(
        adapter=adapter,
        messages=[user_message("hi")],
        tools=_registry_with_echo(),
        hooks=hooks,
    )

    # Sequence per iteration: before_request, after_response, on_message,
    # then (if tool_use) before_tool, after_tool, on_message.
    # Final iteration ends with on_done.
    assert fired.count("before_request") == 2
    assert fired.count("after_response") == 2
    assert fired.count("before_tool") == 1
    assert fired.count("after_tool") == 1
    assert fired.count("on_done") == 1
    assert fired.index("before_tool") < fired.index("after_tool")
    assert fired.index("on_done") == len(fired) - 1


async def test_hook_exception_does_not_break_loop() -> None:
    hooks = HookRegistry()

    async def bad_hook(_payload: object) -> None:
        raise RuntimeError("hook explosion")

    hooks.on("after_response", bad_hook)

    adapter = FakeAdapter([ScriptedReply(text="ok")])
    result = await run_turn(
        adapter=adapter,
        messages=[user_message("hi")],
        hooks=hooks,
    )

    assert result.stop_reason == "end_turn"


# ───── prompt-caching wiring ──────────────────────────────────────────────
#
# (Caching is applied inside AnthropicAdapter, not the loop. We verify the
# adapter's caching here so the contract is locked.)


async def test_anthropic_adapter_applies_cache_control_to_last_system_block() -> None:
    from artemis.agent.client import AnthropicAdapter

    blocks = AnthropicAdapter._build_system("hello system", cache=True)
    assert blocks is not None
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}


async def test_anthropic_adapter_can_disable_caching() -> None:
    from artemis.agent.client import AnthropicAdapter

    blocks = AnthropicAdapter._build_system("hello", cache=False)
    assert blocks is not None
    assert "cache_control" not in blocks[-1]


async def test_anthropic_adapter_caches_last_tool() -> None:
    from artemis.agent.client import AnthropicAdapter

    tools = [
        Tool(name="a", description="A", input_schema={"type": "object"}),
        Tool(name="b", description="B", input_schema={"type": "object"}),
    ]
    api_tools = AnthropicAdapter._build_tools(tools, cache=True)
    assert api_tools is not None
    assert "cache_control" not in api_tools[0]
    assert api_tools[1]["cache_control"] == {"type": "ephemeral"}


# ───── tools registry ─────────────────────────────────────────────────────


async def test_tool_registry_disallows_duplicate_registration() -> None:
    registry = ToolRegistry()
    t = Tool(name="dup", description="x", input_schema={"type": "object"})

    async def _impl(_: dict[str, Any]) -> str:
        return "ok"

    registry.register(t, _impl)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(t, _impl)


async def test_tool_registry_membership() -> None:
    registry = _registry_with_echo()
    assert "echo" in registry
    assert "missing" not in registry
    assert len(registry) == 1
    specs = registry.specs()
    assert specs[0].name == "echo"


# ───── content shape ──────────────────────────────────────────────────────


async def test_tool_use_block_serializes_to_api_shape() -> None:
    block = ToolUseBlock(id="x", name="t", input={"k": 1})
    assert block.to_api() == {
        "type": "tool_use",
        "id": "x",
        "name": "t",
        "input": {"k": 1},
    }


async def test_tool_result_block_serializes_to_api_shape() -> None:
    block = ToolResultBlock(tool_use_id="x", content="result", is_error=False)
    assert block.to_api() == {
        "type": "tool_result",
        "tool_use_id": "x",
        "content": "result",
        "is_error": False,
    }


# ───── OBS-1: CompletionResponse.tool_calls threaded into RunResult.metadata ──


async def test_response_tool_calls_land_in_run_result_metadata() -> None:
    """A provider with its own internal tool loop (ClaudeCodeAdapter's MCP
    path) reports calls via CompletionResponse.tool_calls, not ToolUseBlocks —
    run_turn must surface those on RunResult.metadata["tool_calls"] so callers
    that never see a ToolUseBlock (because none was ever produced) still learn
    what ran."""
    adapter = FakeAdapter(
        [
            ScriptedReply(
                text="done",
                response_tool_calls=[
                    ToolCallRecord(name="dispatch_research"),
                    ToolCallRecord(name="list_candidates", is_error=True),
                ],
            )
        ]
    )

    result = await run_turn(adapter=adapter, messages=[user_message("hi")])

    assert result.metadata["tool_calls"] == [
        ToolCallRecord(name="dispatch_research"),
        ToolCallRecord(name="list_candidates", is_error=True),
    ]
    # And, since this path never produces a ToolUseBlock, the message content
    # is text-only — the metadata channel is the ONLY place this shows up.
    assert len(result.messages) == 2
    assert isinstance(result.messages[-1].content[0], TextBlock)


async def test_no_response_tool_calls_leaves_metadata_empty() -> None:
    """When the adapter never sets tool_calls (e.g. the Anthropic path, or a
    claude-code turn with genuinely no tool use), metadata must not gain a
    spurious "tool_calls" key — RunResult.metadata stays {}."""
    adapter = FakeAdapter([ScriptedReply(text="hello")])

    result = await run_turn(adapter=adapter, messages=[user_message("hi")])

    assert result.metadata == {}
