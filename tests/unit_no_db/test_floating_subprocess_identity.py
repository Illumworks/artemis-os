"""SECURITY (M3): the claude-code subprocess must gate memory scope to the TRUSTED
agent_id (the live caller's identity forwarded by the parent), never to persisted
session metadata a non-owner could influence.

Regression anchor: a non-owner could PATCH a session's agent_id to "artemis" (or run
a turn on an owner's session) and the subprocess, re-reading persisted metadata, would
serve owner-private memory. The parent now forwards the trusted agent_id as --agent-id.
"""

from __future__ import annotations

from artemis.providers.claude_code.adapter import _build_floating_artemis_mcp_config


def _args(cfg: dict) -> list[str]:
    return cfg["mcpServers"]["artemis"]["args"]


def test_trusted_agent_id_forwarded_as_cli_arg() -> None:
    args = _args(
        _build_floating_artemis_mcp_config(
            session_id="s1", tool_names=["query_memory"], trusted_agent_id="callie"
        )
    )
    assert "--agent-id" in args
    assert args[args.index("--agent-id") + 1] == "callie"
    # session id + tool allowlist still present
    assert args[args.index("--floating-session-id") + 1] == "s1"
    assert "--tool-name" in args


def test_no_agent_id_flag_when_absent() -> None:
    """Legacy/test callers (no trusted id) omit the flag; subprocess falls back to
    persisted metadata (Slack path server-authors it)."""
    args = _args(_build_floating_artemis_mcp_config(session_id="s1", tool_names=["query_memory"]))
    assert "--agent-id" not in args


def test_empty_trusted_agent_id_omits_flag() -> None:
    args = _args(
        _build_floating_artemis_mcp_config(session_id="s1", tool_names=[], trusted_agent_id="")
    )
    assert "--agent-id" not in args
