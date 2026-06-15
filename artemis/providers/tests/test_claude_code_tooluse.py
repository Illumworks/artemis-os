"""Tests for the claude-code tool-use path (stream CC2).

Covers the pure config/command builders and the mocked subprocess launch. No
real ``claude`` binary is invoked — ``asyncio.create_subprocess_exec`` is mocked.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock, Tool
from artemis.floating_artemis.context import floating_session_id_var
from artemis.providers.claude_code.adapter import (
    _DISALLOWED_BUILTINS,
    ClaudeCodeAdapter,
    _build_floating_artemis_mcp_config,
    _build_launch_command,
    _build_mcp_config,
    _mcp_eager_env,
    allowed_tools_for,
)
from artemis.providers.errors import ClaudeCodeTimeoutError, ProviderAPIError
from artemis.tools.mcp_server import mcp_tool_name

# A representative regional_news tool list (dotted artemis names).
_REGIONAL_NEWS_TOOLS = [
    "signal_queue.write",
    "news_api.search",
    "memory_layer.get",
    "territory_config.get_priority_states",
    "reason_codes.lookup",
]


def _make_executable(tmp_path: Path, name: str = "claude") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _request(text: str = "Go scout.", system: str | None = "You are a scout.") -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=text)])],
        system=system,
    )


def _mock_proc(stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ── 1. MCP config generation ────────────────────────────────────────────────────


def test_mcp_config_uses_sys_executable_and_args() -> None:
    cfg = _build_mcp_config(
        agent_id="marketing.scout.regional_news",
        run_id="RUN-1",
        pipeline_run_id="PIPE-7",
    )
    server = cfg["mcpServers"]["artemis"]
    assert server["command"] == sys.executable
    assert server["args"] == [
        "-m",
        "artemis.tools.mcp_server",
        "--agent-id",
        "marketing.scout.regional_news",
        "--run-id",
        "RUN-1",
        "--pipeline-run-id",
        "PIPE-7",
    ]


def test_mcp_config_omits_pipeline_run_id_when_none() -> None:
    cfg = _build_mcp_config(agent_id="a1", run_id="RUN-2", pipeline_run_id=None)
    args = cfg["mcpServers"]["artemis"]["args"]
    assert "--pipeline-run-id" not in args
    assert args[-2:] == ["--run-id", "RUN-2"]


def test_floating_artemis_mcp_config_carries_session_and_tool_names() -> None:
    cfg = _build_floating_artemis_mcp_config(
        session_id="fa-session-1",
        tool_names=["query_memory", "write_memory"],
    )
    server = cfg["mcpServers"]["artemis"]
    assert server["command"] == sys.executable
    assert server["args"] == [
        "-m",
        "artemis.tools.mcp_server",
        "--floating-session-id",
        "fa-session-1",
        "--tool-name",
        "query_memory",
        "--tool-name",
        "write_memory",
    ]


# ── 2. allowed-tools correctness ─────────────────────────────────────────────────


def test_allowed_tools_match_mcp_tool_name() -> None:
    allow = allowed_tools_for(_REGIONAL_NEWS_TOOLS)
    expected = [f"mcp__artemis__{mcp_tool_name(t)}" for t in _REGIONAL_NEWS_TOOLS]
    assert allow == expected
    # Spot-check the headline entries from the brief.
    assert "mcp__artemis__signal_queue_write" in allow
    assert "mcp__artemis__news_api_search" in allow


def test_launch_command_allowed_tools_section() -> None:
    cmd = _build_launch_command(
        binary="claude",
        model="claude-haiku-4-6",
        mcp_config_path="/tmp/x.json",
        agent_tools=_REGIONAL_NEWS_TOOLS,
    )
    start = cmd.index("--allowed-tools") + 1
    end = cmd.index("--disallowed-tools")
    assert cmd[start:end] == [f"mcp__artemis__{mcp_tool_name(t)}" for t in _REGIONAL_NEWS_TOOLS]


# ── 3. built-ins disallowed ──────────────────────────────────────────────────────


def test_launch_command_disallows_builtins() -> None:
    cmd = _build_launch_command(
        binary="claude",
        model="m",
        mcp_config_path="/tmp/x.json",
        agent_tools=_REGIONAL_NEWS_TOOLS,
    )
    start = cmd.index("--disallowed-tools") + 1
    end = cmd.index("--permission-mode")
    assert cmd[start:end] == list(_DISALLOWED_BUILTINS)
    for builtin in ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"):
        assert builtin in cmd


# ── 4. strict + permission-mode + NO max-turns ───────────────────────────────────


def test_launch_command_strict_and_permission_mode_no_max_turns() -> None:
    cmd = _build_launch_command(
        binary="claude",
        model="m",
        mcp_config_path="/tmp/x.json",
        agent_tools=_REGIONAL_NEWS_TOOLS,
    )
    assert "--strict-mcp-config" in cmd
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "default"
    assert "--max-turns" not in cmd
    assert "--max-budget-usd" not in cmd
    # Single JSON result, headless print.
    assert "-p" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--mcp-config") + 1] == "/tmp/x.json"


def test_launch_command_has_no_session_persistence() -> None:
    """--no-session-persistence must be present so per-run sessions are ephemeral.

    Prevents unnecessary session disk writes and removes one startup task that
    competes with MCP handshake timing (contributing to tool deferral).
    """
    cmd = _build_launch_command(
        binary="claude",
        model="m",
        mcp_config_path="/tmp/x.json",
        agent_tools=_REGIONAL_NEWS_TOOLS,
    )
    assert "--no-session-persistence" in cmd


# ── 4a. MCP eager env (anti-deferral) ────────────────────────────────────────────


def test_mcp_eager_env_sets_nonblocking_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """_mcp_eager_env() must inject MCP_CONNECTION_NONBLOCKING=false.

    This forces claude-code's isMcpLadderNonblockingEnabled() to return false,
    so the MCP server handshake completes synchronously before the first LLM
    turn — preventing the deferred-tool-catalog state that causes scouts to
    narrate 'tools not yet connected' and emit 0 signals.
    """
    env = _mcp_eager_env()
    assert env["MCP_CONNECTION_NONBLOCKING"] == "false"
    # Must inherit the parent env so DB creds, PATH, etc. are preserved.
    import os

    assert "PATH" in env or not os.environ.get("PATH")


def test_mcp_eager_env_inherits_parent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_mcp_eager_env() copies the full parent environment."""
    monkeypatch.setenv("ARTEMIS_TEST_SENTINEL", "present")
    env = _mcp_eager_env()
    assert env.get("ARTEMIS_TEST_SENTINEL") == "present"
    assert env["MCP_CONNECTION_NONBLOCKING"] == "false"


@pytest.mark.asyncio
async def test_run_with_tools_passes_mcp_eager_env(tmp_path: Path) -> None:
    """run_with_tools() must pass MCP_CONNECTION_NONBLOCKING=false to the subprocess.

    This is the primary fix for the intermittent 0-signal scout bug where
    claude-code presents MCP tools as deferred rather than eager.
    """
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "ok", "usage": {}}).encode()
    proc = _mock_proc(payload)

    captured_env: dict[str, str] = {}

    async def _capture_exec(*args: object, **kwargs: object) -> object:
        env_arg = kwargs.get("env") or {}
        if isinstance(env_arg, dict):
            captured_env.update(env_arg)
        return proc

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=_capture_exec)):
        await adapter.run_with_tools(
            _request(),
            agent_id="marketing.scout.regional_news",
            run_id="RUN-EAGER",
            pipeline_run_id=None,
            agent_tools=_REGIONAL_NEWS_TOOLS,
        )

    assert captured_env.get("MCP_CONNECTION_NONBLOCKING") == "false", (
        "MCP_CONNECTION_NONBLOCKING=false must be passed to the subprocess env "
        "so MCP tools are loaded synchronously (not deferred)"
    )


# ── 5. subprocess success path + temp-file cleanup ───────────────────────────────


@pytest.mark.asyncio
async def test_run_with_tools_success_returns_completion(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps(
        {"result": "Wrote 3 signals.", "usage": {"input_tokens": 120, "output_tokens": 45}}
    ).encode()
    proc = _mock_proc(payload)

    created_paths: list[str] = []
    real_unlink = Path.unlink

    def _record_unlink(self: Path, **kwargs: object) -> None:
        created_paths.append(str(self))
        real_unlink(self, **kwargs)  # type: ignore[arg-type]

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch.object(Path, "unlink", _record_unlink),
    ):
        resp = await adapter.run_with_tools(
            _request(),
            agent_id="marketing.scout.regional_news",
            run_id="RUN-9",
            pipeline_run_id="PIPE-3",
            agent_tools=_REGIONAL_NEWS_TOOLS,
        )

    assert resp.stop_reason == "end_turn"
    assert isinstance(resp.message.content[0], TextBlock)
    assert resp.message.content[0].text == "Wrote 3 signals."
    assert resp.usage.input_tokens == 120
    assert resp.usage.output_tokens == 45
    # Temp MCP config was created and unlinked.
    assert created_paths, "expected the temp MCP config to be unlinked"
    assert not Path(created_paths[-1]).exists()


# ── 6. failure surfacing + cleanup ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_with_tools_nonzero_exit_raises_and_cleans_up(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    proc = _mock_proc(b"", b"boom", returncode=1)

    unlinked: list[str] = []
    real_unlink = Path.unlink

    def _record_unlink(self: Path, **kwargs: object) -> None:
        unlinked.append(str(self))
        real_unlink(self, **kwargs)  # type: ignore[arg-type]

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch.object(Path, "unlink", _record_unlink),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.run_with_tools(
            _request(),
            agent_id="a1",
            run_id="RUN-10",
            pipeline_run_id=None,
            agent_tools=_REGIONAL_NEWS_TOOLS,
        )

    assert exc_info.value.status_code == 1
    assert unlinked, "temp MCP config must be cleaned up even on failure"
    assert not Path(unlinked[-1]).exists()


@pytest.mark.asyncio
async def test_run_with_tools_timeout_raises_and_cleans_up(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    proc = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

    unlinked: list[str] = []
    real_unlink = Path.unlink

    def _record_unlink(self: Path, **kwargs: object) -> None:
        unlinked.append(str(self))
        real_unlink(self, **kwargs)  # type: ignore[arg-type]

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch.object(Path, "unlink", _record_unlink),
        pytest.raises(ClaudeCodeTimeoutError) as exc_info,
    ):
        await adapter.run_with_tools(
            _request(),
            agent_id="a1",
            run_id="RUN-11",
            pipeline_run_id=None,
            agent_tools=_REGIONAL_NEWS_TOOLS,
        )

    assert exc_info.value.status_code == 408
    assert unlinked and not Path(unlinked[-1]).exists()


@pytest.mark.asyncio
async def test_complete_with_tools_uses_floating_session_context(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(
        cmd: list[str], prompt: str, *, tool_run: bool = False
    ) -> object:
        captured["cmd"] = cmd
        captured["prompt"] = prompt
        from artemis.agent.client import CompletionResponse
        from artemis.agent.types import Usage

        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text="Used floating tool path.")]),
            stop_reason="end_turn",
            usage=Usage(),
        )

    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Search memory for Jon.")])],
        tools=[
            Tool(name="query_memory", description="d", input_schema={}),
            Tool(name="write_memory", description="d", input_schema={}),
        ],
    )

    token = floating_session_id_var.set("fa-session-2")
    try:
        with patch.object(adapter, "_run_subprocess", side_effect=_fake_run_subprocess):
            resp = await adapter.complete(request)
    finally:
        floating_session_id_var.reset(token)

    first_block = resp.message.content[0]
    assert isinstance(first_block, TextBlock)
    assert first_block.text == "Used floating tool path."
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--allowed-tools" in cmd
    start = cmd.index("--allowed-tools") + 1
    end = cmd.index("--disallowed-tools")
    assert cmd[start:end] == ["mcp__artemis__query_memory", "mcp__artemis__write_memory"]


@pytest.mark.asyncio
async def test_timeout_seconds_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from artemis.providers.claude_code.adapter import _timeout_seconds

    monkeypatch.delenv("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", raising=False)
    assert _timeout_seconds() == 900.0
    monkeypatch.setenv("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", "42")
    assert _timeout_seconds() == 42.0
    monkeypatch.setenv("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", "not-a-number")
    assert _timeout_seconds() == 900.0


# ── Multi-account CLAUDE_CONFIG_DIR support ───────────────────────────────────────


# ---------------------------------------------------------------------------
# resolve_claude_config_dir — unit tests (no subprocess)
# ---------------------------------------------------------------------------


def test_resolve_config_dir_exact_match() -> None:
    """Exact agent_id match returns the mapped CLAUDE_CONFIG_DIR."""
    from artemis.providers.claude_code.adapter import resolve_claude_config_dir

    fake_settings = MagicMock()
    fake_settings.claude_agent_accounts = {"marketing.scout.news": "marketing"}
    fake_settings.claude_account_config_dirs = {"marketing": "/Users/artemis/.claude-marketing"}

    with patch("artemis.config.settings", fake_settings):
        result = resolve_claude_config_dir("marketing.scout.news")

    assert result == "/Users/artemis/.claude-marketing"


def test_resolve_config_dir_prefix_match() -> None:
    """Prefix match (e.g. 'marketing.') covers all marketing.* agents."""
    from artemis.providers.claude_code.adapter import resolve_claude_config_dir

    fake_settings = MagicMock()
    fake_settings.claude_agent_accounts = {"marketing.": "marketing"}
    fake_settings.claude_account_config_dirs = {"marketing": "/Users/artemis/.claude-marketing"}

    with patch("artemis.config.settings", fake_settings):
        result = resolve_claude_config_dir("marketing.qualifier.signal_scorer")

    assert result == "/Users/artemis/.claude-marketing"


def test_resolve_config_dir_exact_wins_over_prefix() -> None:
    """When both an exact match and a prefix match exist, exact wins."""
    from artemis.providers.claude_code.adapter import resolve_claude_config_dir

    fake_settings = MagicMock()
    fake_settings.claude_agent_accounts = {
        "marketing.": "marketing",
        "marketing.scout.news": "personal",
    }
    fake_settings.claude_account_config_dirs = {
        "marketing": "/Users/artemis/.claude-marketing",
        "personal": "/Users/artemis/.claude-personal",
    }

    with patch("artemis.config.settings", fake_settings):
        result = resolve_claude_config_dir("marketing.scout.news")

    assert result == "/Users/artemis/.claude-personal"


def test_resolve_config_dir_longest_prefix_wins() -> None:
    """When multiple prefixes match, the longest one wins."""
    from artemis.providers.claude_code.adapter import resolve_claude_config_dir

    fake_settings = MagicMock()
    fake_settings.claude_agent_accounts = {
        "marketing.": "marketing",
        "marketing.scout.": "scout_account",
    }
    fake_settings.claude_account_config_dirs = {
        "marketing": "/Users/artemis/.claude-marketing",
        "scout_account": "/Users/artemis/.claude-scout",
    }

    with patch("artemis.config.settings", fake_settings):
        result = resolve_claude_config_dir("marketing.scout.news")

    assert result == "/Users/artemis/.claude-scout"


def test_resolve_config_dir_unmapped_agent_returns_none() -> None:
    """An agent with no matching entry returns None (ambient login unchanged)."""
    from artemis.providers.claude_code.adapter import resolve_claude_config_dir

    fake_settings = MagicMock()
    fake_settings.claude_agent_accounts = {"marketing.": "marketing"}
    fake_settings.claude_account_config_dirs = {"marketing": "/Users/artemis/.claude-marketing"}

    with patch("artemis.config.settings", fake_settings):
        result = resolve_claude_config_dir("personal.artemis")

    assert result is None


def test_resolve_config_dir_empty_maps_return_none() -> None:
    """Empty config maps → None for any agent (default/safe state)."""
    from artemis.providers.claude_code.adapter import resolve_claude_config_dir

    fake_settings = MagicMock()
    fake_settings.claude_agent_accounts = {}
    fake_settings.claude_account_config_dirs = {}

    with patch("artemis.config.settings", fake_settings):
        result = resolve_claude_config_dir("marketing.scout.news")

    assert result is None


def test_resolve_config_dir_account_missing_from_dirs_returns_none() -> None:
    """Account name mapped but absent from claude_account_config_dirs → None (mis-config guard)."""
    from artemis.providers.claude_code.adapter import resolve_claude_config_dir

    fake_settings = MagicMock()
    fake_settings.claude_agent_accounts = {"marketing.scout.news": "marketing"}
    fake_settings.claude_account_config_dirs = {}  # no entry for "marketing"

    with patch("artemis.config.settings", fake_settings):
        result = resolve_claude_config_dir("marketing.scout.news")

    assert result is None


# ---------------------------------------------------------------------------
# _mcp_eager_env — CLAUDE_CONFIG_DIR injection
# ---------------------------------------------------------------------------


def test_mcp_eager_env_sets_claude_config_dir_when_provided() -> None:
    """_mcp_eager_env(claude_config_dir=...) must add CLAUDE_CONFIG_DIR to env."""
    from artemis.providers.claude_code.adapter import _mcp_eager_env

    env = _mcp_eager_env(claude_config_dir="/Users/artemis/.claude-marketing")
    assert env["CLAUDE_CONFIG_DIR"] == "/Users/artemis/.claude-marketing"
    assert env["MCP_CONNECTION_NONBLOCKING"] == "false"


def test_mcp_eager_env_no_claude_config_dir_when_none() -> None:
    """_mcp_eager_env() with claude_config_dir=None must NOT set CLAUDE_CONFIG_DIR."""
    from artemis.providers.claude_code.adapter import _mcp_eager_env

    env = _mcp_eager_env(claude_config_dir=None)
    assert "CLAUDE_CONFIG_DIR" not in env
    assert env["MCP_CONNECTION_NONBLOCKING"] == "false"


def test_mcp_eager_env_default_has_no_claude_config_dir() -> None:
    """_mcp_eager_env() with no args must NOT set CLAUDE_CONFIG_DIR (backward compat)."""
    from artemis.providers.claude_code.adapter import _mcp_eager_env

    env = _mcp_eager_env()
    assert "CLAUDE_CONFIG_DIR" not in env


# ---------------------------------------------------------------------------
# run_with_tools — subprocess env contains CLAUDE_CONFIG_DIR when mapped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_with_tools_sets_claude_config_dir_for_mapped_agent(tmp_path: Path) -> None:
    """When claude_config_dir is passed to run_with_tools, the subprocess env
    must contain CLAUDE_CONFIG_DIR=<dir>.
    """
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "ok", "usage": {}}).encode()
    proc = _mock_proc(payload)

    captured_env: dict[str, str] = {}

    async def _capture_exec(*args: object, **kwargs: object) -> object:
        env_arg = kwargs.get("env") or {}
        if isinstance(env_arg, dict):
            captured_env.update(env_arg)
        return proc

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=_capture_exec)):
        await adapter.run_with_tools(
            _request(),
            agent_id="marketing.scout.regional_news",
            run_id="RUN-ACCT-1",
            pipeline_run_id=None,
            agent_tools=_REGIONAL_NEWS_TOOLS,
            claude_config_dir="/Users/artemis/.claude-marketing",
        )

    assert captured_env.get("CLAUDE_CONFIG_DIR") == "/Users/artemis/.claude-marketing", (
        "CLAUDE_CONFIG_DIR must be present in subprocess env when claude_config_dir is passed"
    )
    # MCP_CONNECTION_NONBLOCKING must still be set (not regressed)
    assert captured_env.get("MCP_CONNECTION_NONBLOCKING") == "false"


@pytest.mark.asyncio
async def test_run_with_tools_no_claude_config_dir_by_default(tmp_path: Path) -> None:
    """When claude_config_dir is not passed (default None), CLAUDE_CONFIG_DIR must be
    absent from the subprocess env — identical behavior to before this feature.
    """
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "ok", "usage": {}}).encode()
    proc = _mock_proc(payload)

    captured_env: dict[str, str] = {}

    async def _capture_exec(*args: object, **kwargs: object) -> object:
        env_arg = kwargs.get("env") or {}
        if isinstance(env_arg, dict):
            captured_env.update(env_arg)
        return proc

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=_capture_exec)):
        await adapter.run_with_tools(
            _request(),
            agent_id="marketing.scout.regional_news",
            run_id="RUN-ACCT-2",
            pipeline_run_id=None,
            agent_tools=_REGIONAL_NEWS_TOOLS,
            # claude_config_dir omitted intentionally
        )

    assert "CLAUDE_CONFIG_DIR" not in captured_env, (
        "CLAUDE_CONFIG_DIR must NOT appear in subprocess env when claude_config_dir is None"
    )
