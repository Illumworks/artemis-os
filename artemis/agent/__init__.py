"""Agent loop — model interaction, tool use, hooks.

Public API:

    from artemis.agent import (
        AnthropicAdapter, FakeAdapter,
        HookRegistry,
        Message, TextBlock, ToolUseBlock, ToolResultBlock,
        Tool, ToolImpl, ToolRegistry,
        RunResult, Usage,
        run_turn, user_message, assistant_message,
    )

The loop is the only public function. Everything else is types or registries
the loop consumes. Reference implementation: claudeck-artemis/server/agent-loop.js
"""

from artemis.agent.client import (
    AnthropicAdapter,
    CompletionRequest,
    CompletionResponse,
    ModelAdapter,
)
from artemis.agent.hooks import HookCallback, HookEvent, HookRegistry, fire_and_forget
from artemis.agent.loop import assistant_message, run_turn, user_message
from artemis.agent.tools import ToolEntry, ToolRegistry
from artemis.agent.types import (
    Block,
    Message,
    Role,
    RunResult,
    StopReason,
    TextBlock,
    Tool,
    ToolImpl,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

__all__ = [
    # adapters
    "AnthropicAdapter",
    "Block",
    # types
    "CompletionRequest",
    "CompletionResponse",
    "HookCallback",
    "HookEvent",
    # hooks
    "HookRegistry",
    "Message",
    "ModelAdapter",
    "Role",
    "RunResult",
    "StopReason",
    "TextBlock",
    "Tool",
    "ToolEntry",
    "ToolImpl",
    # tools
    "ToolRegistry",
    "ToolResultBlock",
    "ToolUseBlock",
    "Usage",
    "assistant_message",
    "fire_and_forget",
    # loop
    "run_turn",
    "user_message",
]
