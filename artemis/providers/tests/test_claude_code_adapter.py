"""Tests for ClaudeCodeAdapter — subprocess-based Claude Code CLI adapter."""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.providers.claude_code.adapter import (
    ClaudeCodeAdapter,
    _flatten_to_prompt,
    _timeout_seconds,
)
from artemis.providers.errors import (
    ClaudeCodeTimeoutError,
    MissingCliBinaryError,
    ProviderAPIError,
)

pytestmark = pytest.mark.asyncio


def _make_executable(tmp_path: Path, name: str = "claude") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _simple_request(text: str = "Hello") -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=text)])],
    )


# ── construction ──────────────────────────────────────────────────────────────


def test_raises_missing_cli_binary_error_when_binary_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    with (
        patch("artemis.providers.claude_code.adapter.find_cli_binary", return_value=None),
        pytest.raises(MissingCliBinaryError) as exc_info,
    ):
        ClaudeCodeAdapter()
    assert exc_info.value.provider == "claude-code"
    assert exc_info.value.binary_name == "claude"


def test_accepts_explicit_binary_path(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    assert adapter._binary == str(binary)


def test_uses_find_cli_binary_when_no_path_given(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    with patch("artemis.providers.claude_code.adapter.find_cli_binary", return_value=str(binary)):
        adapter = ClaudeCodeAdapter()
    assert adapter._binary == str(binary)


# ── complete() ────────────────────────────────────────────────────────────────


def _mock_proc(stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


async def test_complete_parses_json_result(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "Hello back!"}).encode()
    proc = _mock_proc(payload)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter.complete(_simple_request())

    assert len(response.message.content) == 1
    assert isinstance(response.message.content[0], TextBlock)
    assert response.message.content[0].text == "Hello back!"
    assert response.stop_reason == "end_turn"


async def test_complete_parses_json_result_with_usage(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps(
        {
            "result": "answer",
            "usage": {"input_tokens": 42, "output_tokens": 17},
        }
    ).encode()
    proc = _mock_proc(payload)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter.complete(_simple_request())

    assert response.usage.input_tokens == 42
    assert response.usage.output_tokens == 17


async def test_complete_raises_on_nonzero_exit(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    proc = _mock_proc(b"", b"something went wrong", returncode=1)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(_simple_request())
    assert exc_info.value.status_code == 1


async def test_complete_raises_on_timeout(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    proc = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ClaudeCodeTimeoutError) as exc_info,
    ):
        await adapter.complete(_simple_request())
    assert exc_info.value.status_code == 408
    assert _timeout_seconds() == 900.0
    assert "timed out after 900s" in exc_info.value.body


async def test_complete_raises_on_non_json_output(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    proc = _mock_proc(b"not-json-at-all")

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError),
    ):
        await adapter.complete(_simple_request())


async def test_complete_raises_on_empty_output(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    proc = _mock_proc(b"")

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError),
    ):
        await adapter.complete(_simple_request())


# ── _flatten_to_prompt ────────────────────────────────────────────────────────


def test_flatten_includes_system() -> None:
    req = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="hi")])],
        system="You are helpful.",
    )
    result = _flatten_to_prompt(req)
    assert result.startswith("System: You are helpful.")


def test_flatten_includes_messages() -> None:
    req = CompletionRequest(
        messages=[
            Message(role="user", content=[TextBlock(text="Hello")]),
            Message(role="assistant", content=[TextBlock(text="Hi there")]),
        ],
    )
    result = _flatten_to_prompt(req)
    assert "Human: Hello" in result
    assert "Assistant: Hi there" in result
