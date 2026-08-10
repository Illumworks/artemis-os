"""Tests for Forge write mode in the claude-code adapter (Forge Phase 3 chunk 3.3).

Covers:
- ``_build_forge_command(..., write_mode=True)`` argv shape: Write/Edit/Bash in
  --allowed-tools; NOT in --disallowed-tools; --add-dir and bypassPermissions
  still present; no --mcp-config.
- ``_build_forge_command(..., write_mode=False)`` (and default, i.e. omitted) is
  byte-identical to the read-only argv produced by the existing constants.
- With ``forge_project_path_var`` set and ``forge_write_mode_var=True``, the
  adapter builds the write argv (asserted via _run_subprocess capture, no real
  subprocess launched).

No real ``claude`` binary is invoked.  Pattern mirrors ``test_claude_code_forge_mode.py``.
"""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, Tool, Usage
from artemis.dev_projects.context import forge_project_path_var, forge_write_mode_var
from artemis.providers.claude_code.adapter import (
    _FORGE_DISALLOWED,
    _FORGE_READONLY_ALLOWED,
    _FORGE_WRITE_DISALLOWED,
    ClaudeCodeAdapter,
    _build_forge_command,
)

_PROJECT_PATH = "/Users/artemis/Artemis/artemis-os"
_MODEL = "claude-sonnet-4-6"


def _make_executable(tmp_path: Path, name: str = "claude") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _request_with_tools(text: str = "Edit the adapter.") -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=text)])],
        tools=[Tool(name="Edit", description="Edit a file", input_schema={})],
    )


def _fake_response(text: str = "Write result.") -> CompletionResponse:
    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason="end_turn",
        usage=Usage(),
    )


# ── 1. write_mode=True argv shape ───────────────────────────────────────────


def test_write_mode_allowed_tools_contains_write_edit_bash() -> None:
    cmd = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=True
    )
    assert "--allowed-tools" in cmd
    start = cmd.index("--allowed-tools") + 1
    end = cmd.index("--disallowed-tools")
    allowed_in_cmd = cmd[start:end]
    for tool in ("Write", "Edit", "Bash"):
        assert tool in allowed_in_cmd, f"{tool} missing from --allowed-tools in write mode"


def test_write_mode_allowed_tools_still_has_readonly_tools() -> None:
    cmd = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=True
    )
    start = cmd.index("--allowed-tools") + 1
    end = cmd.index("--disallowed-tools")
    allowed_in_cmd = cmd[start:end]
    for tool in _FORGE_READONLY_ALLOWED:
        assert tool in allowed_in_cmd, f"{tool} missing from write-mode --allowed-tools"


def test_write_mode_disallowed_does_not_contain_write_edit_bash() -> None:
    cmd = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=True
    )
    start = cmd.index("--disallowed-tools") + 1
    disallowed_in_cmd = cmd[start:]
    for tool in ("Write", "Edit", "Bash"):
        assert tool not in disallowed_in_cmd, (
            f"{tool} must NOT appear in --disallowed-tools in write mode"
        )


def test_write_mode_disallowed_contains_web_tools() -> None:
    cmd = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=True
    )
    start = cmd.index("--disallowed-tools") + 1
    disallowed_in_cmd = cmd[start:]
    for tool in _FORGE_WRITE_DISALLOWED:
        assert tool in disallowed_in_cmd, f"{tool} missing from write-mode --disallowed-tools"


def test_write_mode_has_add_dir() -> None:
    cmd = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=True
    )
    assert "--add-dir" in cmd
    assert cmd[cmd.index("--add-dir") + 1] == _PROJECT_PATH


def test_write_mode_has_bypass_permissions() -> None:
    cmd = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=True
    )
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"


def test_write_mode_no_mcp_config() -> None:
    cmd = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=True
    )
    assert "--mcp-config" not in cmd
    assert "--strict-mcp-config" not in cmd


def test_write_mode_headless_print_json() -> None:
    cmd = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=True
    )
    assert "-p" in cmd
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"


# ── 2. write_mode=False and default are byte-identical to read-only argv ────


def test_readonly_default_identical_to_explicit_false() -> None:
    """write_mode omitted must produce the same argv as write_mode=False."""
    cmd_default = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    cmd_false = _build_forge_command(
        binary="claude", model=_MODEL, project_path=_PROJECT_PATH, write_mode=False
    )
    assert cmd_default == cmd_false


def test_readonly_default_allowed_tools_are_read_glob_grep() -> None:
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    start = cmd.index("--allowed-tools") + 1
    end = cmd.index("--disallowed-tools")
    allowed_in_cmd = cmd[start:end]
    assert set(allowed_in_cmd) == set(_FORGE_READONLY_ALLOWED), (
        "Default read-only allowed-tools must be exactly Read/Glob/Grep"
    )


def test_readonly_default_disallowed_contains_bash_write_edit() -> None:
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    start = cmd.index("--disallowed-tools") + 1
    disallowed_in_cmd = cmd[start:]
    for tool in ("Bash", "Write", "Edit"):
        assert tool in disallowed_in_cmd, (
            f"{tool} must be in --disallowed-tools in read-only (default) mode"
        )


def test_readonly_default_full_disallowed_matches_constant() -> None:
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    start = cmd.index("--disallowed-tools") + 1
    disallowed_in_cmd = cmd[start:]
    for tool in _FORGE_DISALLOWED:
        assert tool in disallowed_in_cmd, (
            f"{tool} from _FORGE_DISALLOWED missing in default read-only argv"
        )


# ── 3. _complete_with_tools uses write argv when forge_write_mode_var=True ──


@pytest.mark.asyncio
async def test_complete_with_tools_write_mode_builds_write_argv(tmp_path: Path) -> None:
    """forge_project_path_var set + forge_write_mode_var=True => write argv."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(
        cmd: list[str],
        prompt: str,
        *,
        tool_run: bool = False,
        project_path: str | None = None,
        **_kwargs: object,
    ) -> CompletionResponse:
        captured["cmd"] = cmd
        captured["project_path"] = project_path
        return _fake_response()

    path_token = forge_project_path_var.set(_PROJECT_PATH)
    write_token = forge_write_mode_var.set(True)
    try:
        with patch.object(adapter, "_run_subprocess", side_effect=_fake_run_subprocess):
            resp = await adapter.complete(_request_with_tools())
    finally:
        forge_write_mode_var.reset(write_token)
        forge_project_path_var.reset(path_token)

    first_block = resp.message.content[0]
    assert isinstance(first_block, TextBlock)
    assert first_block.text == "Write result."

    cmd = captured["cmd"]
    assert isinstance(cmd, list)

    # Verify write-mode tool shape.
    assert "--add-dir" in cmd
    assert cmd[cmd.index("--add-dir") + 1] == _PROJECT_PATH
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--mcp-config" not in cmd

    start_allowed = cmd.index("--allowed-tools") + 1
    end_allowed = cmd.index("--disallowed-tools")
    allowed_in_cmd = cmd[start_allowed:end_allowed]
    for tool in ("Write", "Edit", "Bash"):
        assert tool in allowed_in_cmd, f"{tool} must be in --allowed-tools for write mode"

    start_dis = cmd.index("--disallowed-tools") + 1
    disallowed_in_cmd = cmd[start_dis:]
    for tool in ("Write", "Edit", "Bash"):
        assert tool not in disallowed_in_cmd, (
            f"{tool} must NOT be in --disallowed-tools for write mode"
        )

    assert captured["project_path"] == _PROJECT_PATH


@pytest.mark.asyncio
async def test_complete_with_tools_write_mode_false_builds_readonly_argv(tmp_path: Path) -> None:
    """forge_project_path_var set + forge_write_mode_var=False => read-only argv."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(
        cmd: list[str],
        prompt: str,
        *,
        tool_run: bool = False,
        project_path: str | None = None,
        **_kwargs: object,
    ) -> CompletionResponse:
        captured["cmd"] = cmd
        return _fake_response("Readonly result.")

    path_token = forge_project_path_var.set(_PROJECT_PATH)
    # forge_write_mode_var defaults to False; set it explicitly for clarity.
    write_token = forge_write_mode_var.set(False)
    try:
        with patch.object(adapter, "_run_subprocess", side_effect=_fake_run_subprocess):
            await adapter.complete(_request_with_tools())
    finally:
        forge_write_mode_var.reset(write_token)
        forge_project_path_var.reset(path_token)

    cmd = captured["cmd"]
    assert isinstance(cmd, list)

    start_allowed = cmd.index("--allowed-tools") + 1
    end_allowed = cmd.index("--disallowed-tools")
    allowed_in_cmd = cmd[start_allowed:end_allowed]
    for tool in ("Write", "Edit", "Bash"):
        assert tool not in allowed_in_cmd, (
            f"{tool} must NOT be in --allowed-tools for read-only mode"
        )

    start_dis = cmd.index("--disallowed-tools") + 1
    disallowed_in_cmd = cmd[start_dis:]
    for tool in ("Bash", "Write", "Edit"):
        assert tool in disallowed_in_cmd, f"{tool} must be in --disallowed-tools for read-only mode"


@pytest.mark.asyncio
async def test_complete_with_tools_write_mode_var_unset_defaults_readonly(
    tmp_path: Path,
) -> None:
    """When forge_write_mode_var is at its default (False), read-only argv is built."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(
        cmd: list[str],
        prompt: str,
        *,
        tool_run: bool = False,
        project_path: str | None = None,
        **_kwargs: object,
    ) -> CompletionResponse:
        captured["cmd"] = cmd
        return _fake_response("Default readonly result.")

    # Only set forge path; write mode var stays at its default (False).
    path_token = forge_project_path_var.set(_PROJECT_PATH)
    try:
        with patch.object(adapter, "_run_subprocess", side_effect=_fake_run_subprocess):
            await adapter.complete(_request_with_tools())
    finally:
        forge_project_path_var.reset(path_token)

    cmd = captured["cmd"]
    assert isinstance(cmd, list)

    start_allowed = cmd.index("--allowed-tools") + 1
    end_allowed = cmd.index("--disallowed-tools")
    allowed_in_cmd = cmd[start_allowed:end_allowed]
    # Write/Edit/Bash must NOT appear in the allowed list.
    for tool in ("Write", "Edit", "Bash"):
        assert tool not in allowed_in_cmd, (
            f"{tool} must not be allowed when write mode var is at default"
        )

    # argv must be identical to the explicit write_mode=False build.
    expected = _build_forge_command(
        binary=str(binary),
        model=adapter._default_model,
        project_path=_PROJECT_PATH,
        write_mode=False,
    )
    assert cmd == expected, "Default path must be byte-identical to write_mode=False argv"
