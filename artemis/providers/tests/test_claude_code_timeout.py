"""Tests for CC15 — env-configurable Claude CLI timeout.

Design choice: _timeout_seconds() reads os.environ on every call, so
monkeypatch.setenv takes effect without reloading the module.  The three
tests below are config tests — no real subprocess is launched.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.providers.claude_code.adapter import ClaudeCodeAdapter, _timeout_seconds

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_executable(tmp_path: Path, name: str = "claude") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _simple_request(text: str = "Hello") -> CompletionRequest:
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


# ── CC15 tests ────────────────────────────────────────────────────────────────


def test_default_timeout_is_900(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the env var unset, _timeout_seconds() returns 900.0."""
    monkeypatch.delenv("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", raising=False)
    assert _timeout_seconds() == 900.0


def test_env_override_changes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS=120 yields 120.0."""
    monkeypatch.setenv("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", "120")
    assert _timeout_seconds() == 120.0


@pytest.mark.asyncio
async def test_no_timeout_error_when_subprocess_finishes_in_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS=900, a fast subprocess does NOT raise.

    Regression: the timeout must still be applied as a wall-clock bound, so
    a subprocess that returns promptly should complete normally.
    """
    monkeypatch.setenv("ARTEMIS_CLAUDE_CODE_TIMEOUT_SECONDS", "900")
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))

    payload = json.dumps({"result": "done quickly"}).encode()
    proc = _mock_proc(payload)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter.complete(_simple_request())

    # Must not raise ClaudeCodeTimeoutError; result should be parsed correctly.
    block = response.message.content[0]
    assert isinstance(block, TextBlock)
    assert block.text == "done quickly"
