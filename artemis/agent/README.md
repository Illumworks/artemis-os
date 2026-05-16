# `artemis.agent` — agent loop

The Phase F1 skeleton: a clean, narrow loop that talks to Claude, executes tool calls, and surfaces lifecycle hooks. Everything richer (memory injection, skill injection, push notifications, cost recording) lives elsewhere and plugs in via hooks or higher-level orchestration.

## Quickstart

```python
from artemis.agent import (
    AnthropicAdapter, Tool, ToolRegistry,
    run_turn, user_message,
)

# Define a tool.
async def echo_impl(input: dict) -> str:
    return f"echo: {input.get('text', '')}"

tools = ToolRegistry()
tools.register(
    Tool(
        name="echo",
        description="Echo input text back",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    echo_impl,
)

# Run a turn.
result = await run_turn(
    adapter=AnthropicAdapter(),
    messages=[user_message("Use the echo tool to repeat 'hi'.")],
    tools=tools,
    system="You are a helpful assistant.",
)

print(result.messages[-1].content)
print(f"Used {result.usage.input_tokens} in, {result.usage.output_tokens} out")
```

## What's in here

| File | Role |
|------|------|
| `types.py` | `Message`, `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `Tool`, `Usage`, `RunResult` |
| `client.py` | `ModelAdapter` protocol; `AnthropicAdapter` (default); prompt-caching wiring |
| `tools.py` | `ToolRegistry` pairs `Tool` defs with `ToolImpl` callables |
| `hooks.py` | `HookRegistry` for `before_request` / `after_response` / `before_tool` / `after_tool` / `on_message` / `on_done` |
| `loop.py` | `run_turn(...)` — the only public entry point |
| `tests/fake_adapter.py` | `FakeAdapter` + `ScriptedReply` for tests; no Anthropic API calls |

## Design decisions

1. **`ModelAdapter` is the substitutable boundary.** The loop talks to an adapter, not the Anthropic SDK. Tests use `FakeAdapter`; production uses `AnthropicAdapter`. Switching providers later (Vertex AI, Bedrock) means writing a new adapter, not refactoring the loop.

2. **Prompt caching is on by default.** Per the Anthropic-rebuild rule ("prompt caching wired from day one"), `AnthropicAdapter` automatically applies `cache_control: {"type": "ephemeral"}` to the last system block and the tools list. Opt out with `cache_system=False` / `cache_tools=False`.

3. **Tool failures never crash the loop.** An exception in a `ToolImpl` becomes a `ToolResultBlock(is_error=True)`. The model decides whether to retry or abandon. Test: `test_tool_exception_becomes_error_block`.

4. **`max_iterations` is a hard floor.** Default 10. Models can occasionally get into tool-use loops; the cap puts a ceiling on runaway costs. When hit, `stop_reason="max_iterations"`. Test: `test_max_iterations_caps_runaway`.

5. **Parallel tool calls in one assistant message are bundled.** All tool_results from one round go in a single subsequent user message, per the Anthropic protocol. Test: `test_parallel_tool_calls_in_one_response`.

6. **Hooks are observation only.** Hooks cannot mutate the message stream. Mutation goes through explicit middleware in later phases (when we have a clear use case). Exceptions in hook callbacks are caught and logged; the loop continues.

## What this skeleton intentionally does NOT do

These are scoped to later phases — adding them here without a concrete consumer would be premature.

- **Streaming.** `complete()` is non-streaming. Streaming is added when the UI needs token-by-token rendering (Phase E2).
- **Memory injection / skills injection.** The Node `agent-loop.js` weaves these into the loop directly. The Python rebuild keeps the loop narrow and exposes hooks that memory/skills can subscribe to. Wiring happens in Phase F2 (orchestrator) once both keystone and skills exist.
- **DB-side recording (sessions, costs, runs).** Same reasoning — hook subscribers.
- **Push / Telegram notifications.** Same reasoning — hook subscribers.
- **Provider abstraction (Codex / Gemini / OpenRouter).** The Node app has a provider registry. The Python rebuild defaults to Anthropic; multi-provider lands when a real second provider has a real consumer.

## Reference

- Node implementation: `claudeck-artemis/server/agent-loop.js` (457 lines, heavily integrated).
- Phased plan: `claudeck-artemis/decisions/rebuild-phased-plan.md` Phase F1.
