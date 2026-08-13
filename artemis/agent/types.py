"""Message, tool, and event types for the agent loop.

Types are deliberately Pydantic-light: the agent loop is hot-path code and we
avoid validating message blocks on every turn. Pydantic is reserved for
config / external API surfaces.

Reference: claudeck-artemis/server/agent-loop.js — the Node implementation
threads conversations as plain dicts. The Python rebuild keeps the same shape
but with typed dataclasses for IDE support.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "system"]


@dataclass(slots=True)
class TextBlock:
    text: str
    type: Literal["text"] = "text"

    def to_api(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass(slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"

    def to_api(self) -> dict[str, Any]:
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass(slots=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"

    def to_api(self) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
            "is_error": self.is_error,
        }


Block = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(slots=True)
class ToolCallRecord:
    """One tool invocation observed on a provider path with its OWN internal
    tool loop (currently: ``ClaudeCodeAdapter``'s MCP tool path, parsed from
    ``--output-format stream-json``).

    This is deliberately NOT a ``Block`` / part of ``Message.content``. On
    that path the CLI subprocess resolves every tool call itself and returns
    only a final text result, so no ``ToolUseBlock`` ever appears in the
    response message for callers to scan (OBS-1). ``ToolCallRecord`` is the
    side-channel that carries the same information — which tools ran, and
    whether each one errored — for callers (``run_turn`` / ``chat.py``) that
    need it for trace/observability purposes without changing the message
    content contract.

    ``name`` has any ``mcp__artemis__`` MCP-server prefix already stripped,
    so it matches the bare tool name used in the registry and in briefs.
    """

    name: str
    is_error: bool = False


@dataclass(slots=True)
class Message:
    role: Role
    content: list[Block]

    def to_api(self) -> dict[str, Any]:
        return {"role": self.role, "content": [b.to_api() for b in self.content]}


@dataclass(slots=True)
class Tool:
    """Tool definition the model sees in its tools list."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


#: Async tool implementation. Receives parsed input, returns string output.
#:
#: Raising any exception is caught by the loop and surfaced as a tool_result
#: with is_error=True. The exception message becomes the tool result content.
ToolImpl = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens


StopReason = Literal["end_turn", "max_tokens", "stop_sequence", "tool_use", "max_iterations"]


@dataclass(slots=True)
class RunResult:
    messages: list[Message]
    """Full conversation including the new turns produced by run_turn."""
    stop_reason: StopReason
    usage: Usage
    iterations: int = 0
    """Number of model calls performed in this turn (1 + tool-use rounds)."""
    metadata: dict[str, Any] = field(default_factory=dict)
