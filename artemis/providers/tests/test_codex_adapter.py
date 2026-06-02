"""Tests for CodexAdapter — subprocess-based Codex CLI adapter."""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.providers.codex.adapter import CodexAdapter, _parse_ndjson_output
from artemis.providers.errors import MissingCliBinaryError, ProviderAPIError

pytestmark = pytest.mark.asyncio


def _make_executable(tmp_path: Path, name: str = "codex") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _simple_request(text: str = "Write hello world") -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=text)])],
    )


def _mock_proc(stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ── construction ──────────────────────────────────────────────────────────────


def test_raises_missing_cli_binary_error_when_binary_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_BIN", raising=False)
    with (
        patch("artemis.providers.codex.adapter.find_cli_binary", return_value=None),
        pytest.raises(MissingCliBinaryError) as exc_info,
    ):
        CodexAdapter()
    assert exc_info.value.provider == "codex"
    assert exc_info.value.binary_name == "codex"


def test_accepts_explicit_binary_path(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = CodexAdapter(binary_path=str(binary))
    assert adapter._binary == str(binary)


# ── complete() ────────────────────────────────────────────────────────────────


async def test_complete_parses_single_json_line(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = CodexAdapter(binary_path=str(binary))
    line = json.dumps({"type": "result", "result": "print('hello')"})
    proc = _mock_proc(line.encode())

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter.complete(_simple_request())

    block = response.message.content[0]
    assert isinstance(block, TextBlock)
    assert block.text == "print('hello')"
    assert response.stop_reason == "end_turn"


async def test_complete_forwards_effort_and_speed_flags(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = CodexAdapter(binary_path=str(binary))
    line = json.dumps({"type": "result", "result": "ok"})
    proc = _mock_proc(line.encode())
    req = _simple_request("Hi")
    req.model = "gpt-5.5"
    req.reasoning_effort = "xhigh"
    req.speed_tier = "fast"

    create = AsyncMock(return_value=proc)
    with patch("asyncio.create_subprocess_exec", new=create):
        await adapter.complete(req)

    assert create.await_args is not None
    args = create.await_args.args
    pairs = list(zip(args, args[1:], strict=False))
    assert ("-c", 'model_reasoning_effort="xhigh"') in pairs
    assert ("-c", "service_tier=fast") in pairs


async def test_complete_raises_on_nonzero_exit(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = CodexAdapter(binary_path=str(binary))
    proc = _mock_proc(b"", b"fatal error", returncode=2)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(_simple_request())
    assert exc_info.value.status_code == 2


async def test_complete_raises_on_timeout(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = CodexAdapter(binary_path=str(binary))
    proc = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(_simple_request())
    assert exc_info.value.status_code == 408


async def test_complete_raises_on_empty_output(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = CodexAdapter(binary_path=str(binary))
    proc = _mock_proc(b"")

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError),
    ):
        await adapter.complete(_simple_request())


# ── _parse_ndjson_output ─────────────────────────────────────────────────────


def test_parse_ndjson_concatenates_multiple_result_lines() -> None:
    lines = "\n".join(
        [
            json.dumps({"type": "result", "result": "Part 1"}),
            json.dumps({"type": "result", "result": "Part 2"}),
        ]
    )
    text, usage = _parse_ndjson_output(lines)
    assert "Part 1" in text
    assert "Part 2" in text
    assert usage.input_tokens == 0


def test_parse_ndjson_reads_usage_from_last_usage_object() -> None:
    lines = "\n".join(
        [
            json.dumps({"type": "result", "result": "text"}),
            json.dumps({"usage": {"input_tokens": 100, "output_tokens": 50}}),
        ]
    )
    _, usage = _parse_ndjson_output(lines)
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50


def test_parse_ndjson_falls_back_to_raw_when_no_result_objects() -> None:
    raw = "some plain text output"
    text, _ = _parse_ndjson_output(raw)
    assert text == raw


def test_parse_ndjson_skips_invalid_json_lines() -> None:
    lines = "not-json\n" + json.dumps({"type": "result", "result": "ok"})
    text, _ = _parse_ndjson_output(lines)
    assert text == "ok"
