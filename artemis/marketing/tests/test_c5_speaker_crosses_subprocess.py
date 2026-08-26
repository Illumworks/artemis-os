"""The speaker id must survive the MCP process boundary.

On 2026-08-26 Callie refused Jon's own target-list import with "the system
couldn't identify the current speaker". His Slack id was correct and the event
carried it -- but identity-gated tools bind the speaker as a CLOSURE when the
registry is built, and on the claude-code path the registry is built inside a
SUBPROCESS that inherits nothing from the parent. Every such tool saw None and
failed closed.

That silently disabled four tools, not one: `send_guarded_dm` (CALLIE-1),
`flag_catalog_gap`, `update_asset_summary`, and `import_target_accounts`. Each
reported it as a permissions decision, which is the expensive part -- it sends
people to audit an allowlist that was never wrong.

Same class as the `floating_session_id_var` gap that left Argus never running
for five weeks. These tests exist so the third instance does not happen.
"""

from __future__ import annotations

import inspect

import pytest


def test_the_subprocess_sets_the_speaker_var_itself() -> None:
    """Asserted against source, deliberately.

    The failure mode is the ABSENCE of a call, and a mocked subprocess would not
    catch it -- the Argus version of this bug had full test coverage and a test
    asserting the wrong contract. If this is refactored, keep an assertion that
    the value reaches a TOOL, not merely that this line exists (see the live
    round-trip test below).
    """
    from artemis.tools import mcp_server

    source = inspect.getsource(mcp_server._serve_floating_artemis)
    assert "floating_speaker_id_var.set(speaker_id)" in source, (
        "the MCP subprocess must set floating_speaker_id_var -- it cannot "
        "inherit it from the parent process"
    )


def test_the_spawn_args_carry_the_speaker() -> None:
    from artemis.providers.claude_code.adapter import _build_floating_artemis_mcp_config

    args = _build_floating_artemis_mcp_config(
        session_id="slack-callie-T1-C1-_",
        tool_names=["import_target_accounts"],
        trusted_agent_id="callie",
        speaker_id="U09F3EPJXSQ",
    )["mcpServers"]["artemis"]["args"]

    assert "--speaker-id" in args
    assert args[args.index("--speaker-id") + 1] == "U09F3EPJXSQ"


def test_an_unknown_speaker_is_omitted_rather_than_passed_empty() -> None:
    """No speaker must mean no flag, so the subprocess fails closed on its own."""
    from artemis.providers.claude_code.adapter import _build_floating_artemis_mcp_config

    args = _build_floating_artemis_mcp_config(
        session_id="s", tool_names=[], trusted_agent_id="callie", speaker_id=None
    )["mcpServers"]["artemis"]["args"]

    assert "--speaker-id" not in args


def test_the_parent_turn_sets_the_speaker_var() -> None:
    """Set in the parent too, so the adapter has something to forward."""
    from artemis.floating_artemis import chat

    source = inspect.getsource(chat)
    assert "floating_speaker_id_var.set(speaker_id)" in source


@pytest.mark.asyncio
async def test_the_speaker_reaches_the_tool_through_the_real_build_path() -> None:
    """The round trip that matters: identity in, correct verdict out.

    Uses the same builder the subprocess entrypoint calls, so a regression in
    the wiring between it and `build_authorized_tool_registry` fails here rather
    than in production as an unexplained permissions refusal.
    """
    from artemis.tools.mcp_server import build_floating_artemis_tool_set

    async def verdict(speaker: str | None) -> str:
        tool_set = await build_floating_artemis_tool_set(
            {"import_target_accounts"}, agent_id="callie", speaker_id=speaker
        )
        entry = tool_set.get("import_target_accounts")
        assert entry is not None, "the tool must be exposed on the MCP path at all"
        # No file_id: stops before any download, so this asserts the identity
        # gate alone rather than exercising a real import.
        return await entry[1]({})

    assert "Not permitted" in await verdict(None), "unknown speaker must fail closed"
    assert "Not permitted" in await verdict("U0NOTALLOWED")

    allowed = await verdict("U09F3EPJXSQ")
    assert "Not permitted" not in allowed, (
        "Jon must get past the identity gate -- this is the exact bug of 2026-08-26"
    )
    assert "needs the Slack file_id" in allowed
