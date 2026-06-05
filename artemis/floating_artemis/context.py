"""Floating Artemis contextvars for provider-specific tool sessions.

Claude Code's MCP path runs tools in a subprocess, so the parent turn handler
must tag the active Floating Artemis session before calling the adapter. The
adapter reads these contextvars to decide which scoped MCP server to launch and
which model-emitted tool_use block is currently being executed.
"""

from __future__ import annotations

from contextvars import ContextVar

#: Set to the active floating_artemis_sessions.session_id before calling
#: ClaudeCodeAdapter.complete() with Floating Artemis tools.
floating_session_id_var: ContextVar[str | None] = ContextVar(
    "floating_session_id_var",
    default=None,
)

#: Set to the currently executing tool_use id while the agent loop is inside a
#: Floating Artemis tool implementation. Layer-3/4 interceptors use this to
#: persist the model's real tool_use id instead of inventing a new UUID.
floating_tool_use_id_var: ContextVar[str | None] = ContextVar(
    "floating_tool_use_id_var",
    default=None,
)
