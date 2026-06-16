"""Tests for Codex usage-limit / rate-limit fallback.

Three cases under test:
  (a) turn.failed with a usage-limit message → CodexRateLimitError.
  (b) turn.failed for a non-limit reason → plain ProviderAPIError (NOT retryable).
  (c) complete_with_fallback falls through to claude-code on CodexRateLimitError.

All subprocess calls are mocked — the real codex CLI is never invoked.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agent.client import CompletionRequest, CompletionResponse
from artemis.agent.types import Message, TextBlock, Usage
from artemis.providers.codex.adapter import CodexAdapter, _check_for_rate_limit_failure
from artemis.providers.errors import CodexRateLimitError, ProviderAPIError
from artemis.providers.fallback import _is_retryable, complete_with_fallback

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_executable(tmp_path: Path, name: str = "codex") -> Path:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _simple_request(text: str = "Do the thing") -> CompletionRequest:
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


def _fake_cc_response(text: str = "Hi from claude-code") -> CompletionResponse:
    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason="end_turn",
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


# ── (a) turn.failed with usage-limit message → CodexRateLimitError ────────────


def test_check_raises_codex_rate_limit_on_usage_limit_turn_failed() -> None:
    """turn.failed event with 'usage limit' in nested error.message → CodexRateLimitError."""
    event = json.dumps(
        {
            "type": "turn.failed",
            "error": {"message": "You've hit your usage limit. Please try again at 5:20 PM."},
        }
    )
    with pytest.raises(CodexRateLimitError) as exc_info:
        _check_for_rate_limit_failure(event)
    assert exc_info.value.status_code == 429
    assert isinstance(exc_info.value, ProviderAPIError)


def test_check_raises_codex_rate_limit_on_rate_limit_turn_failed() -> None:
    """turn.failed event with 'rate limit' → CodexRateLimitError."""
    event = json.dumps(
        {
            "type": "turn.failed",
            "error": {"message": "Rate limit exceeded. Try again later."},
        }
    )
    with pytest.raises(CodexRateLimitError):
        _check_for_rate_limit_failure(event)


def test_check_raises_codex_rate_limit_on_try_again_error_event() -> None:
    """Standalone error event with 'try again' phrase → CodexRateLimitError."""
    event = json.dumps({"type": "error", "message": "Too many requests — try again in 60 seconds."})
    with pytest.raises(CodexRateLimitError):
        _check_for_rate_limit_failure(event)


def test_check_raises_codex_rate_limit_case_insensitive() -> None:
    """Detection is case-insensitive ('USAGE LIMIT' still matches)."""
    event = json.dumps({"type": "turn.failed", "error": {"message": "USAGE LIMIT reached."}})
    with pytest.raises(CodexRateLimitError):
        _check_for_rate_limit_failure(event)


@pytest.mark.asyncio
async def test_complete_raises_codex_rate_limit_on_usage_limit_stdout(tmp_path: Path) -> None:
    """complete() raises CodexRateLimitError when stdout contains a usage-limit turn.failed."""
    binary = _make_executable(tmp_path)
    adapter = CodexAdapter(binary_path=str(binary))
    stdout_payload = json.dumps(
        {
            "type": "turn.failed",
            "error": {"message": "You've hit your usage limit. Please try again at 5:20 PM."},
        }
    ).encode()
    proc = _mock_proc(stdout_payload, returncode=0)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(CodexRateLimitError) as exc_info,
    ):
        await adapter.complete(_simple_request())

    assert exc_info.value.status_code == 429
    assert isinstance(exc_info.value, ProviderAPIError)


# ── (b) turn.failed for non-limit reason → plain ProviderAPIError (not retryable) ──


def test_check_raises_plain_provider_error_on_non_limit_turn_failed() -> None:
    """turn.failed with an unrelated message → plain ProviderAPIError, not CodexRateLimitError."""
    event = json.dumps(
        {
            "type": "turn.failed",
            "error": {"message": "Authentication failed: invalid API key."},
        }
    )
    with pytest.raises(ProviderAPIError) as exc_info:
        _check_for_rate_limit_failure(event)
    # Must NOT be the retryable subclass
    assert type(exc_info.value) is ProviderAPIError


def test_check_raises_plain_provider_error_on_task_failure() -> None:
    """turn.failed for a task error (not a limit) → plain ProviderAPIError."""
    event = json.dumps({"type": "turn.failed", "error": {"message": "Sandbox execution error."}})
    with pytest.raises(ProviderAPIError) as exc_info:
        _check_for_rate_limit_failure(event)
    assert type(exc_info.value) is ProviderAPIError


def test_non_limit_provider_error_is_not_retryable() -> None:
    """_is_retryable() returns False for a plain ProviderAPIError with status 1."""
    exc = ProviderAPIError(1, "codex turn.failed: Sandbox execution error.")
    assert not _is_retryable(exc)


def test_codex_rate_limit_error_is_retryable() -> None:
    """_is_retryable() returns True for CodexRateLimitError."""
    exc = CodexRateLimitError(429, "You've hit your usage limit.")
    assert _is_retryable(exc)


@pytest.mark.asyncio
async def test_complete_raises_plain_error_on_non_limit_turn_failed(tmp_path: Path) -> None:
    """complete() raises plain ProviderAPIError (not CodexRateLimitError) on a non-limit failure."""
    binary = _make_executable(tmp_path)
    adapter = CodexAdapter(binary_path=str(binary))
    stdout_payload = json.dumps(
        {"type": "turn.failed", "error": {"message": "Task execution failed."}}
    ).encode()
    proc = _mock_proc(stdout_payload, returncode=0)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(_simple_request())

    assert type(exc_info.value) is ProviderAPIError


# ── (c) complete_with_fallback falls through to claude-code on CodexRateLimitError ──


@pytest.mark.asyncio
async def test_fallback_triggered_on_codex_rate_limit(tmp_path: Path) -> None:
    """complete_with_fallback falls through to claude-code when Codex hits usage limit."""
    binary = _make_executable(tmp_path)
    request = _simple_request()
    claude_response = _fake_cc_response("Hi from claude-code after codex limit")

    usage_limit_stdout = json.dumps(
        {
            "type": "turn.failed",
            "error": {"message": "You've hit your usage limit. Please try again at 5:20 PM."},
        }
    ).encode()
    proc = _mock_proc(usage_limit_stdout, returncode=0)

    mock_codex_adapter = CodexAdapter(binary_path=str(binary))
    mock_cc_adapter = MagicMock()
    mock_cc_adapter.complete = AsyncMock(return_value=claude_response)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch(
            "artemis.providers.fallback.get_adapter",
            side_effect=lambda provider_id, **_kw: (
                mock_codex_adapter if provider_id == "codex" else mock_cc_adapter
            ),
        ),
    ):
        serving: list[str] = []
        result = await complete_with_fallback(
            request,
            primary="codex",
            fallback="claude-code",
            serving_provider_out=serving,
        )

    assert result.message.content[0].text == "Hi from claude-code after codex limit"  # type: ignore[union-attr]
    assert serving == ["claude-code"]


@pytest.mark.asyncio
async def test_no_fallback_on_codex_non_limit_failure(tmp_path: Path) -> None:
    """complete_with_fallback re-raises (does NOT fall through) on a non-limit codex failure."""
    binary = _make_executable(tmp_path)
    request = _simple_request()

    task_failure_stdout = json.dumps(
        {"type": "turn.failed", "error": {"message": "Sandbox execution error."}}
    ).encode()
    proc = _mock_proc(task_failure_stdout, returncode=0)

    mock_codex_adapter = CodexAdapter(binary_path=str(binary))
    mock_cc_adapter = MagicMock()
    mock_cc_adapter.complete = AsyncMock()  # should never be called

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        patch(
            "artemis.providers.fallback.get_adapter",
            side_effect=lambda provider_id, **_kw: (
                mock_codex_adapter if provider_id == "codex" else mock_cc_adapter
            ),
        ),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await complete_with_fallback(
            request,
            primary="codex",
            fallback="claude-code",
        )

    # Must be the plain type — not the retryable subclass
    assert type(exc_info.value) is ProviderAPIError
    # claude-code was never called
    mock_cc_adapter.complete.assert_not_called()


# ── No-op: clean output passes through without raising ────────────────────────


def test_check_does_not_raise_on_normal_output() -> None:
    """_check_for_rate_limit_failure is a no-op when there are no failure events."""
    events = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Hello!"},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            ),
        ]
    )
    # Should not raise
    _check_for_rate_limit_failure(events)
