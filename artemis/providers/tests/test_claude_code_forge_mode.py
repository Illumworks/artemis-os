"""Tests for the Forge-mode path in the claude-code adapter.

Covers:
- ``_build_forge_command`` argv shape (unit-level, no subprocess).
- ``_complete_with_tools`` branches on ``forge_project_path_var``.
- The contextvar guard tolerates Forge-only (no builder/floating var needed).
- cwd threading into ``_run_subprocess``.
- Non-Forge (MCP) path is unaffected when the var is unset.

No real ``claude`` binary is invoked — ``asyncio.create_subprocess_exec`` is
mocked, same pattern as ``test_claude_code_tooluse.py``.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, Tool, Usage
from artemis.dev_projects.context import forge_project_path_var
from artemis.floating_artemis.context import floating_session_id_var
from artemis.providers.claude_code.adapter import (
    _FORGE_DISALLOWED,
    _FORGE_READONLY_ALLOWED,
    ClaudeCodeAdapter,
    _build_forge_command,
)
from artemis.providers.errors import ProviderAPIError

_PROJECT_PATH = "/Users/artemis/Artemis/artemis-os"
_MODEL = "claude-sonnet-4-6"


def _make_executable(tmp_path: Path, name: str = "claude") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _request_with_tools(text: str = "Read the adapter file.") -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=text)])],
        tools=[Tool(name="Read", description="Read a file", input_schema={})],
    )


def _mock_proc(stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ── 1. _build_forge_command argv shape ──────────────────────────────────────


def test_forge_command_contains_add_dir() -> None:
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    assert "--add-dir" in cmd
    assert cmd[cmd.index("--add-dir") + 1] == _PROJECT_PATH


def test_forge_command_permission_mode_bypass() -> None:
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"


def test_forge_command_allowed_tools_readonly() -> None:
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    assert "--allowed-tools" in cmd
    start = cmd.index("--allowed-tools") + 1
    end = cmd.index("--disallowed-tools")
    allowed_in_cmd = cmd[start:end]
    for tool in _FORGE_READONLY_ALLOWED:
        assert tool in allowed_in_cmd, f"{tool} missing from --allowed-tools section"
    assert "Read" in allowed_in_cmd
    assert "Glob" in allowed_in_cmd
    assert "Grep" in allowed_in_cmd


def test_forge_command_disallowed_tools() -> None:
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    assert "--disallowed-tools" in cmd
    start = cmd.index("--disallowed-tools") + 1
    disallowed_in_cmd = cmd[start:]
    for tool in _FORGE_DISALLOWED:
        assert tool in disallowed_in_cmd, f"{tool} missing from --disallowed-tools section"
    assert "Bash" in disallowed_in_cmd
    assert "Write" in disallowed_in_cmd
    assert "Edit" in disallowed_in_cmd


def test_forge_command_no_mcp_config() -> None:
    """Forge mode must NOT include --mcp-config or --strict-mcp-config."""
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    assert "--mcp-config" not in cmd
    assert "--strict-mcp-config" not in cmd


def test_forge_command_headless_print_json() -> None:
    """The command must use -p and --output-format json."""
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    assert "-p" in cmd
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"


def test_forge_command_model_is_passed() -> None:
    cmd = _build_forge_command(
        binary="claude", model="claude-haiku-4-6", project_path=_PROJECT_PATH
    )
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-6"


def test_forge_command_returns_list() -> None:
    """_build_forge_command returns a plain list, suitable for create_subprocess_exec."""
    cmd = _build_forge_command(binary="claude", model=_MODEL, project_path=_PROJECT_PATH)
    assert isinstance(cmd, list)
    assert cmd[0] == "claude"


# ── 2. _complete_with_tools branches on forge_project_path_var ──────────────


@pytest.mark.asyncio
async def test_complete_with_tools_forge_mode_uses_forge_argv(tmp_path: Path) -> None:
    """When forge_project_path_var is set, _complete_with_tools must build a Forge
    argv (--add-dir, bypassPermissions, Read/Glob/Grep) and NOT an MCP argv.
    """
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
        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text="Forge result.")]),
            stop_reason="end_turn",
            usage=Usage(),
        )

    token = forge_project_path_var.set(_PROJECT_PATH)
    try:
        with patch.object(adapter, "_run_subprocess", side_effect=_fake_run_subprocess):
            resp = await adapter.complete(_request_with_tools())
    finally:
        forge_project_path_var.reset(token)

    first_block = resp.message.content[0]
    assert isinstance(first_block, TextBlock)
    assert first_block.text == "Forge result."

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--add-dir" in cmd
    assert cmd[cmd.index("--add-dir") + 1] == _PROJECT_PATH
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--mcp-config" not in cmd
    assert "--strict-mcp-config" not in cmd
    # cwd must be threaded in
    assert captured["project_path"] == _PROJECT_PATH


@pytest.mark.asyncio
async def test_complete_with_tools_no_forge_var_uses_mcp_argv(tmp_path: Path) -> None:
    """When forge_project_path_var is NOT set (None), the MCP argv path must be
    taken.  The Forge flags must be absent.
    """
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
        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text="MCP result.")]),
            stop_reason="end_turn",
            usage=Usage(),
        )

    # Set floating session var so the guard passes (forge var remains None).
    token = floating_session_id_var.set("fa-session-forge-test")
    try:
        with patch.object(adapter, "_run_subprocess", side_effect=_fake_run_subprocess):
            resp = await adapter.complete(_request_with_tools())
    finally:
        floating_session_id_var.reset(token)

    first_block = resp.message.content[0]
    assert isinstance(first_block, TextBlock)
    assert first_block.text == "MCP result."

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    # Must be MCP path: --mcp-config present, no Forge flags.
    assert "--mcp-config" in cmd
    assert "--add-dir" not in cmd
    assert "bypassPermissions" not in cmd
    # project_path must be None (cwd not set on MCP path)
    assert captured["project_path"] is None


# ── 3. Contextvar guard tolerates forge-only (no builder/floating needed) ───


@pytest.mark.asyncio
async def test_guard_does_not_raise_when_only_forge_var_set(tmp_path: Path) -> None:
    """The ProviderAPIError guard must NOT fire when only forge_project_path_var
    is set (builder_session_id_var and floating_session_id_var both None).
    """
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    async def _fake_run_subprocess(
        cmd: list[str],
        prompt: str,
        *,
        tool_run: bool = False,
        project_path: str | None = None,
        **_kwargs: object,
    ) -> CompletionResponse:
        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text="ok")]),
            stop_reason="end_turn",
            usage=Usage(),
        )

    token = forge_project_path_var.set(_PROJECT_PATH)
    try:
        with patch.object(adapter, "_run_subprocess", side_effect=_fake_run_subprocess):
            # Must not raise ProviderAPIError "no tool-session contextvar is set".
            resp = await adapter.complete(_request_with_tools())
    finally:
        forge_project_path_var.reset(token)

    first_block = resp.message.content[0]
    assert isinstance(first_block, TextBlock)
    assert first_block.text == "ok"


@pytest.mark.asyncio
async def test_guard_raises_when_all_vars_unset(tmp_path: Path) -> None:
    """With all three contextvars None, _complete_with_tools must still raise."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    with pytest.raises(ProviderAPIError, match="no tool-session contextvar is set"):
        await adapter.complete(_request_with_tools())


# ── 4. cwd is threaded into create_subprocess_exec for Forge mode ───────────


@pytest.mark.asyncio
async def test_run_subprocess_passes_cwd_in_forge_mode(tmp_path: Path) -> None:
    """_run_subprocess must pass cwd=project_path to create_subprocess_exec
    when project_path is provided.
    """
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "read file ok", "usage": {}}).encode()
    proc = _mock_proc(payload)

    captured_kwargs: dict[str, object] = {}

    async def _capture_exec(*args: object, **kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        return proc

    forge_cmd = _build_forge_command(binary=str(binary), model=_MODEL, project_path=_PROJECT_PATH)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=_capture_exec)):
        await adapter._run_subprocess(
            forge_cmd,
            "Read artemis/main.py",
            tool_run=True,
            project_path=_PROJECT_PATH,
        )

    assert captured_kwargs.get("cwd") == _PROJECT_PATH, (
        "cwd must be set to project_path in the Forge subprocess call"
    )


@pytest.mark.asyncio
async def test_run_subprocess_no_cwd_when_project_path_none(tmp_path: Path) -> None:
    """When project_path is None (standard MCP path), cwd must NOT be passed
    to create_subprocess_exec — existing behavior is preserved byte-for-byte.
    """
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "mcp ok", "usage": {}}).encode()
    proc = _mock_proc(payload)

    captured_kwargs: dict[str, object] = {}

    async def _capture_exec(*args: object, **kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        return proc

    # Minimal MCP-style cmd (not validated by the test, just needs to run).
    dummy_cmd = [str(binary), "-p", "--output-format", "json", "--model", _MODEL]

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=_capture_exec)):
        await adapter._run_subprocess(dummy_cmd, "hello", tool_run=True, project_path=None)

    assert "cwd" not in captured_kwargs, (
        "cwd must NOT be passed to create_subprocess_exec on the standard MCP path"
    )
