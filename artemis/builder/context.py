"""Builder-scoped context variables (CC19).

Provides the ``builder_session_id_var`` context variable used to pass the
active Builder session ID into the Artemis MCP server during a
claude-code-with-tools run.

Usage (in agent_builder.handle_turn_stream):

    from artemis.builder.context import builder_session_id_var

    token = builder_session_id_var.set(builder_session_id)
    try:
        response = await adapter.complete(request)
    finally:
        builder_session_id_var.reset(token)

The MCP server reads this variable at tool-call time to scope the five
Builder MCP tools (builder_read_existing, builder_read_capabilities,
builder_read_recent_runs, builder_propose, builder_test_run) to the
correct session.
"""

from __future__ import annotations

from contextvars import ContextVar

#: Set to the active BuilderSession.id before calling ClaudeCodeAdapter.complete()
#: with Builder tools. The MCP server reads this at tool-call time.
builder_session_id_var: ContextVar[int | None] = ContextVar("builder_session_id_var", default=None)
