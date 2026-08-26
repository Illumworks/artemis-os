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

#: SECURITY (M3): the TRUSTED agent_id for the active turn — derived by the turn
#: handler from the live caller's verified identity (session_ctx.agent_id), NOT
#: from persisted session metadata. The claude-code adapter forwards this into the
#: MCP subprocess so memory-scope gating there binds to the live caller, closing
#: the agent_id-spoof / cross-session read hole (a non-owner running a turn on an
#: owner session must NOT get owner memory). When None, the subprocess falls back
#: to persisted metadata (Slack path server-authors it; tests).
floating_trusted_agent_id_var: ContextVar[str | None] = ContextVar(
    "floating_trusted_agent_id_var",
    default=None,
)

#: SECURITY: the Slack user id of the person speaking this turn, forwarded into
#: the MCP subprocess so IDENTITY-GATED tools can see who is asking.
#:
#: Without it every such tool fails closed on the claude-code path, because the
#: subprocess builds its own registry and a closure bound in the parent process
#: cannot cross the fork. Discovered 2026-08-26 when Callie refused Jon's own
#: target-list import with "couldn't identify the current speaker" -- and the
#: same gap silently disabled `send_guarded_dm` (CALLIE-1), `flag_catalog_gap`
#: and `update_asset_summary`, all of which had been failing closed for every
#: requester on this path.
#:
#: This is the same class of bug as the `floating_session_id_var` gap that left
#: Argus never running for five weeks: a value set in the parent, read as None in
#: the child, with the failure looking like a permissions decision rather than a
#: plumbing one. It must be SET in the subprocess entrypoint, not merely set in
#: the parent.
floating_speaker_id_var: ContextVar[str | None] = ContextVar(
    "floating_speaker_id_var",
    default=None,
)
