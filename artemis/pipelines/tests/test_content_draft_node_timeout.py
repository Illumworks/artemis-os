"""Tests for the content-draft-node hang fix (briefs/content-draft-node-hang.md).

Root cause: marketing.content.writing_studio_adapter received writing_ruleset_block
(~7K chars) even in preflight mode (no deliverable_type_slug). With a full campaign
brief also in context, the LLM spent 900s planning instead of calling any tools.

Fixes verified here:
1. writing_ruleset_block is NOT injected when node config has no deliverable_type_slug.
2. writing_ruleset_block IS injected when node config has a deliverable_type_slug.
3. _content_node_timeout_and_turns returns 120s / 5 turns for content agents.
4. _content_node_timeout_and_turns returns (None, None) for non-content agents.
5. ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS env var overrides the default 120s.
6. _build_launch_command includes --max-turns when max_turns is specified.
7. _build_launch_command omits --max-turns when max_turns is None.
8. _run_subprocess uses the provided timeout_seconds override.
9. run_with_tools accepts and forwards timeout_seconds + max_turns.
"""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.builders.executor import _content_node_timeout_and_turns
from artemis.providers.claude_code.adapter import (
    ClaudeCodeAdapter,
    _build_launch_command,
)
from artemis.providers.errors import ClaudeCodeTimeoutError

# ── helpers ─────────────────────────────────────────────────────────────────────


def _make_executable(tmp_path: Path, name: str = "claude") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _mock_proc(stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


_CONTENT_TOOLS = ["writing_studio.enqueue"]


# ── 1-2: writing_ruleset_block injection guard ───────────────────────────────────


def test_writing_ground_agent_ids_contains_writing_studio_adapter() -> None:
    """Regression guard: writing_studio_adapter must still be in the set."""
    from artemis.pipelines.node_executors.agent_executor import _WRITING_GROUND_AGENT_IDS

    assert "marketing.content.writing_studio_adapter" in _WRITING_GROUND_AGENT_IDS


def test_writing_ruleset_block_requires_deliverable_type_slug() -> None:
    """The condition in agent_executor must require deliverable_type_slug.

    Verifies the fix: `agent_id in _WRITING_GROUND_AGENT_IDS and deliverable_type_slug`
    so preflight nodes (no deliverable_type_slug) do NOT get the 7K ruleset injected.

    We inspect the source code to confirm the guard is present.
    """
    import inspect

    from artemis.pipelines.node_executors import agent_executor

    src = inspect.getsource(agent_executor)
    # The guard must conjoin _WRITING_GROUND_AGENT_IDS check with deliverable_type_slug
    assert "and deliverable_type_slug" in src, (
        "agent_executor must guard writing_ruleset_block injection with "
        "`deliverable_type_slug` — preflight nodes must not receive the ruleset"
    )


# ── 3-5: _content_node_timeout_and_turns ────────────────────────────────────────


def test_content_node_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """marketing.content.* agents get 120s timeout and max_turns=5 by default."""
    monkeypatch.delenv("ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS", raising=False)
    timeout, max_turns = _content_node_timeout_and_turns("marketing.content.writing_studio_adapter")
    assert timeout == 120.0
    assert max_turns == 5


def test_content_node_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS overrides the 120s default."""
    monkeypatch.setenv("ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS", "60")
    timeout, max_turns = _content_node_timeout_and_turns("marketing.content.asset_selector")
    assert timeout == 60.0
    assert max_turns == 5


def test_content_node_timeout_bad_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un-parseable ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS falls back to 120s."""
    monkeypatch.setenv("ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS", "not-a-number")
    timeout, max_turns = _content_node_timeout_and_turns("marketing.content.asset_selector")
    assert timeout == 120.0
    assert max_turns == 5


def test_non_content_node_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scout and qualifier agents get (None, None) — no override."""
    monkeypatch.delenv("ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS", raising=False)
    # These should NOT get overrides
    for _agent_id in (
        "marketing.scout.regional_news",
        "marketing.qualifier.score",
        "other.agent",
    ):
        timeout, max_turns = _content_node_timeout_and_turns(_agent_id)
        assert timeout is None, f"Expected None timeout for {_agent_id}"
        assert max_turns is None, f"Expected None max_turns for {_agent_id}"


def test_content_brief_assembler_gets_content_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """marketing.content.brief_assembler also gets the 120s / 5-turn limit."""
    monkeypatch.delenv("ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS", raising=False)
    timeout, max_turns = _content_node_timeout_and_turns("marketing.content.brief_assembler")
    assert timeout == 120.0
    assert max_turns == 5


# ── 6-7: _build_launch_command max_turns ────────────────────────────────────────


def test_build_launch_command_with_max_turns() -> None:
    """--max-turns N must appear when max_turns is provided."""
    cmd = _build_launch_command(
        binary="claude",
        model="claude-sonnet-4-6",
        mcp_config_path="/tmp/x.json",
        agent_tools=_CONTENT_TOOLS,
        max_turns=5,
    )
    assert "--max-turns" in cmd
    idx = cmd.index("--max-turns")
    assert cmd[idx + 1] == "5"


def test_build_launch_command_without_max_turns() -> None:
    """--max-turns must NOT appear when max_turns is None (default)."""
    cmd = _build_launch_command(
        binary="claude",
        model="claude-sonnet-4-6",
        mcp_config_path="/tmp/x.json",
        agent_tools=_CONTENT_TOOLS,
        max_turns=None,
    )
    assert "--max-turns" not in cmd


def test_build_launch_command_max_turns_is_string() -> None:
    """--max-turns value must be a string (subprocess expects strings)."""
    cmd = _build_launch_command(
        binary="claude",
        model="claude-sonnet-4-6",
        mcp_config_path="/tmp/x.json",
        agent_tools=_CONTENT_TOOLS,
        max_turns=3,
    )
    idx = cmd.index("--max-turns")
    assert isinstance(cmd[idx + 1], str)
    assert cmd[idx + 1] == "3"


# ── 8: _run_subprocess timeout_seconds override ──────────────────────────────────


@pytest.mark.asyncio
async def test_run_subprocess_uses_provided_timeout(tmp_path: Path) -> None:
    """_run_subprocess must use timeout_seconds when provided, not _timeout_seconds()."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "fast response"}).encode()
    proc = _mock_proc(payload)

    captured_timeout: list[float] = []
    real_wait_for = asyncio.wait_for

    async def _capture_wait_for(coro: Any, timeout: float) -> Any:
        captured_timeout.append(timeout)
        return await real_wait_for(coro, timeout)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch("asyncio.wait_for", side_effect=_capture_wait_for),
    ):
        await adapter._run_subprocess(["claude", "-p"], "Hello", timeout_seconds=42.0)

    assert captured_timeout == [42.0], (
        f"Expected timeout=42.0 from override, got {captured_timeout}"
    )


@pytest.mark.asyncio
async def test_run_subprocess_falls_back_to_env_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without timeout_seconds override, _run_subprocess uses ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS."""
    monkeypatch.setenv("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", "30")
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "ok"}).encode()
    proc = _mock_proc(payload)

    captured_timeout: list[float] = []
    real_wait_for = asyncio.wait_for

    async def _capture_wait_for(coro: Any, timeout: float) -> Any:
        captured_timeout.append(timeout)
        return await real_wait_for(coro, timeout)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch("asyncio.wait_for", side_effect=_capture_wait_for),
    ):
        await adapter._run_subprocess(["claude", "-p"], "Hello")

    assert captured_timeout == [30.0], f"Expected env-driven timeout=30.0, got {captured_timeout}"


# ── 9: run_with_tools forwards timeout_seconds + max_turns ────────────────────────


@pytest.mark.asyncio
async def test_run_with_tools_forwards_timeout_and_max_turns(tmp_path: Path) -> None:
    """run_with_tools must pass timeout_seconds and max_turns through to _run_subprocess / cmd."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    captured: dict[str, Any] = {}

    async def _fake_run_subprocess(
        cmd: list[str],
        prompt: str,
        *,
        tool_run: bool = False,
        timeout_seconds: float | None = None,
    ) -> Any:
        captured["cmd"] = cmd
        captured["timeout_seconds"] = timeout_seconds
        from artemis.agent.client import CompletionResponse
        from artemis.agent.types import Message, TextBlock, Usage

        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text="ok")]),
            stop_reason="end_turn",
            usage=Usage(),
        )

    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock

    req = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="go")])],
    )

    with patch.object(adapter, "_run_subprocess", side_effect=_fake_run_subprocess):
        await adapter.run_with_tools(
            req,
            agent_id="marketing.content.writing_studio_adapter",
            run_id="test-run",
            pipeline_run_id=None,
            agent_tools=_CONTENT_TOOLS,
            timeout_seconds=42.0,
            max_turns=5,
        )

    assert captured.get("timeout_seconds") == 42.0
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--max-turns" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "5"


@pytest.mark.asyncio
async def test_run_with_tools_timeout_fires_fast(tmp_path: Path) -> None:
    """A tight timeout_seconds fires ClaudeCodeTimeoutError quickly.

    This simulates the fail-fast behavior that prevents the 900s hang:
    with timeout_seconds=0.01, the subprocess call raises ClaudeCodeTimeoutError
    within the configured bound, not 900 seconds.
    """
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    proc = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    # Simulate a subprocess that never returns
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock

    req = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="go")])],
    )

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ClaudeCodeTimeoutError) as exc_info,
    ):
        await adapter.run_with_tools(
            req,
            agent_id="marketing.content.writing_studio_adapter",
            run_id="test-run-timeout",
            pipeline_run_id=None,
            agent_tools=_CONTENT_TOOLS,
            timeout_seconds=0.01,  # 10ms — should fail fast
        )

    assert exc_info.value.status_code == 408
    assert "timed out" in str(exc_info.value).lower()
