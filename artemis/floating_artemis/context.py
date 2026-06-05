"""Floating Artemis contextvars for provider-specific tool sessions.

Claude Code's MCP path runs tools in a subprocess, so the parent turn handler
must tag the active Floating Artemis session before calling the adapter. The
adapter reads this contextvar to decide which scoped MCP server to launch.
"""

from __future__ import annotations

from contextvars import ContextVar

#: Set to the active floating_artemis_sessions.session_id before calling
#: ClaudeCodeAdapter.complete() with Floating Artemis tools.
floating_session_id_var: ContextVar[str | None] = ContextVar(
    "floating_session_id_var",
    default=None,
)
