"""Tests for ClaudeCodeAdapter — subprocess-based Claude Code CLI adapter."""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock, ToolCallRecord
from artemis.providers.claude_code.adapter import (
    ClaudeCodeAdapter,
    _flatten_to_prompt,
    _parse_stream_json,
    _strip_mcp_prefix,
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


# ── is_error / empty-result detection ────────────────────────────────────────


async def test_complete_raises_on_is_error_true(tmp_path: Path) -> None:
    """When the CLI JSON payload has is_error=true, complete() must raise ProviderAPIError.

    This is the core of the WS1 fix: a failed CLI run must NOT return a fake
    end_turn response — it must raise so compose_draft can surface the error.
    """
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps(
        {
            "is_error": True,
            "subtype": "error_during_execution",
            "result": "Context window exceeded",
        }
    ).encode()
    proc = _mock_proc(payload)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(_simple_request())

    assert "is_error=true" in exc_info.value.body
    assert "Context window exceeded" in exc_info.value.body


async def test_complete_raises_on_is_error_with_subtype_only(tmp_path: Path) -> None:
    """is_error=true with no result text should still raise (using subtype as error label)."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"is_error": True, "subtype": "rate_limit", "result": ""}).encode()
    proc = _mock_proc(payload)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(_simple_request())

    assert "is_error=true" in exc_info.value.body


async def test_complete_raises_on_empty_result_field(tmp_path: Path) -> None:
    """A JSON payload with is_error=false but empty result must raise ProviderAPIError."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"is_error": False, "result": "", "num_turns": 1}).encode()
    proc = _mock_proc(payload)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(_simple_request())

    assert "empty result" in exc_info.value.body


async def test_complete_happy_path_unchanged(tmp_path: Path) -> None:
    """A normal complete payload (is_error=false, non-empty result) must return end_turn
    with the full text — the happy path must be byte-identical to before the fix."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps(
        {
            "is_error": False,
            "result": "Here is the composed draft copy.",
            "num_turns": 1,
            "duration_ms": 3200,
            "usage": {"input_tokens": 512, "output_tokens": 128},
        }
    ).encode()
    proc = _mock_proc(payload)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter.complete(_simple_request())

    assert response.stop_reason == "end_turn"
    assert len(response.message.content) == 1
    assert isinstance(response.message.content[0], TextBlock)
    assert response.message.content[0].text == "Here is the composed draft copy."
    assert response.usage.input_tokens == 512
    assert response.usage.output_tokens == 128


async def test_run_subprocess_raises_on_is_error_true(tmp_path: Path) -> None:
    """_run_subprocess (tool path) must also raise on is_error=true."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps(
        {"is_error": True, "result": "Tool execution failed", "subtype": "error_during_execution"}
    ).encode()
    proc = _mock_proc(payload)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter._run_subprocess(["fake-cmd"], "prompt", tool_run=False)

    assert "is_error=true" in exc_info.value.body


async def test_run_subprocess_raises_on_empty_result(tmp_path: Path) -> None:
    """_run_subprocess must raise when result is empty (even with zero-exit)."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"is_error": False, "result": "   ", "num_turns": 1}).encode()
    proc = _mock_proc(payload)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter._run_subprocess(["fake-cmd"], "prompt", tool_run=False)

    assert "empty result" in exc_info.value.body


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


# ── OBS-1: _strip_mcp_prefix ──────────────────────────────────────────────────


def test_strip_mcp_prefix_removes_prefix() -> None:
    assert _strip_mcp_prefix("mcp__artemis__dispatch_research") == "dispatch_research"
    assert _strip_mcp_prefix("mcp__artemis__list_candidates") == "list_candidates"


def test_strip_mcp_prefix_passthrough_when_absent() -> None:
    """A name without the prefix (e.g. a native builtin) passes through unchanged."""
    assert _strip_mcp_prefix("Read") == "Read"


# ── OBS-1: _parse_stream_json ─────────────────────────────────────────────────
#
# Line shapes below are lifted verbatim (field names, nesting) from a real
# `claude -p --output-format stream-json --verbose` transcript captured by
# hand on 2026-08-13 (see the OBS-1 brief) — both the success shape (ToolSearch
# then a tool_reference tool_result) and the error shape (Read on a missing
# file, tool_result with `"is_error": true` as a sibling of `content`, not
# nested inside it). Only the fields _parse_stream_json actually reads are
# kept; everything irrelevant to parsing (timestamps, uuids, usage detail) is
# trimmed for readability.


def _assistant_line(*, tool_use_id: str, name: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}
                ],
            },
        }
    )


def _tool_result_line(*, tool_use_id: str, is_error: bool = False, content: str = "ok") -> str:
    block: dict[str, object] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return json.dumps({"type": "user", "message": {"role": "user", "content": [block]}})


def _result_line(*, result: str = "Final answer.", is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success" if not is_error else "error_during_execution",
            "is_error": is_error,
            "result": result,
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
    )


def test_parse_stream_json_two_distinct_tool_use_events_in_order() -> None:
    """Two different tool_use events yield both names, in order (brief's Test 1)."""
    transcript = "\n".join(
        [
            _assistant_line(tool_use_id="t1", name="ToolSearch"),
            _tool_result_line(tool_use_id="t1"),
            _assistant_line(tool_use_id="t2", name="mcp__artemis__dispatch_research"),
            _tool_result_line(tool_use_id="t2"),
            _result_line(),
        ]
    )

    data, tool_calls = _parse_stream_json(transcript)

    assert tool_calls == [
        ToolCallRecord(name="ToolSearch", is_error=False),
        ToolCallRecord(name="dispatch_research", is_error=False),
    ]
    assert data["result"] == "Final answer."


def test_parse_stream_json_dedupes_repeated_tool_use_same_name() -> None:
    """Calling the same tool twice collapses to one entry — first-occurrence
    wins the ordering slot, matching chat.py's existing ToolUseBlock dedup."""
    transcript = "\n".join(
        [
            _assistant_line(tool_use_id="t1", name="mcp__artemis__list_candidates"),
            _tool_result_line(tool_use_id="t1"),
            _assistant_line(tool_use_id="t2", name="mcp__artemis__list_candidates"),
            _tool_result_line(tool_use_id="t2"),
            _result_line(),
        ]
    )

    _, tool_calls = _parse_stream_json(transcript)

    assert tool_calls == [ToolCallRecord(name="list_candidates", is_error=False)]


def test_parse_stream_json_marks_tool_result_error_as_failure() -> None:
    """A tool_result carrying is_error=true is recorded as a failure, not a
    plain success (brief's Test 2) — this is the case that matters most."""
    transcript = "\n".join(
        [
            _assistant_line(tool_use_id="t1", name="Read"),
            _tool_result_line(tool_use_id="t1", is_error=True, content="File does not exist."),
            _result_line(),
        ]
    )

    _, tool_calls = _parse_stream_json(transcript)

    assert tool_calls == [ToolCallRecord(name="Read", is_error=True)]


def test_parse_stream_json_one_failure_marks_deduped_entry_failed() -> None:
    """If a deduped tool name was invoked more than once, any single failure
    marks the whole entry as failed — success on a retry doesn't erase it."""
    transcript = "\n".join(
        [
            _assistant_line(tool_use_id="t1", name="mcp__artemis__dispatch_research"),
            _tool_result_line(tool_use_id="t1", is_error=True),
            _assistant_line(tool_use_id="t2", name="mcp__artemis__dispatch_research"),
            _tool_result_line(tool_use_id="t2", is_error=False),
            _result_line(),
        ]
    )

    _, tool_calls = _parse_stream_json(transcript)

    assert tool_calls == [ToolCallRecord(name="dispatch_research", is_error=True)]


def test_parse_stream_json_strips_mcp_artemis_prefix() -> None:
    """Requirement #3: mcp__artemis__ is stripped so the recorded name matches
    the bare registry name used in the tool registry and in briefs."""
    transcript = "\n".join(
        [
            _assistant_line(tool_use_id="t1", name="mcp__artemis__dispatch_research"),
            _tool_result_line(tool_use_id="t1"),
            _result_line(),
        ]
    )

    _, tool_calls = _parse_stream_json(transcript)

    assert tool_calls == [ToolCallRecord(name="dispatch_research", is_error=False)]
    assert "mcp__artemis__" not in tool_calls[0].name


def test_parse_stream_json_no_tool_calls_yields_empty_list() -> None:
    """A turn with no tool calls yields an empty list, no crash (brief's Test 4)."""
    transcript = _result_line(result="Just a plain answer, no tools needed.")

    data, tool_calls = _parse_stream_json(transcript)

    assert tool_calls == []
    assert data["result"] == "Just a plain answer, no tools needed."


def test_parse_stream_json_skips_malformed_lines_without_raising() -> None:
    """Malformed / partial lines are skipped without failing the turn
    (brief's Test 6) — losing a whole turn to a bad line would be worse."""
    transcript = "\n".join(
        [
            "not json at all",
            '{"incomplete": ',  # truncated mid-object
            "",  # blank line
            "42",  # valid JSON, but not an object
            _assistant_line(tool_use_id="t1", name="mcp__artemis__dispatch_research"),
            _tool_result_line(tool_use_id="t1"),
            _result_line(result="Survived the noise."),
        ]
    )

    data, tool_calls = _parse_stream_json(transcript)

    assert tool_calls == [ToolCallRecord(name="dispatch_research", is_error=False)]
    assert data["result"] == "Survived the noise."


def test_parse_stream_json_raises_when_no_result_event() -> None:
    """No terminal `type: "result"` line at all is a real failure, not noise."""
    transcript = _assistant_line(tool_use_id="t1", name="ToolSearch")

    with pytest.raises(ProviderAPIError):
        _parse_stream_json(transcript)


def test_parse_stream_json_tool_result_without_matching_tool_use_is_ignored() -> None:
    """A tool_result whose tool_use_id was never seen in a tool_use block
    (e.g. the assistant line was itself skipped as malformed) must not crash —
    it's simply unattributable and is dropped."""
    transcript = "\n".join(
        [
            _tool_result_line(tool_use_id="orphan", is_error=True),
            _result_line(),
        ]
    )

    _, tool_calls = _parse_stream_json(transcript)

    assert tool_calls == []


# ── OBS-1: _run_subprocess(parse_mode="stream-json") ──────────────────────────


async def test_run_subprocess_stream_json_populates_tool_calls(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    transcript = "\n".join(
        [
            _assistant_line(tool_use_id="t1", name="mcp__artemis__list_candidates"),
            _tool_result_line(tool_use_id="t1"),
            _result_line(result="Found 3 candidates.", is_error=False),
        ]
    )
    proc = _mock_proc(transcript.encode())

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter._run_subprocess(
            ["fake-cmd"], "prompt", tool_run=True, parse_mode="stream-json"
        )

    assert response.tool_calls == [ToolCallRecord(name="list_candidates", is_error=False)]
    assert isinstance(response.message.content[0], TextBlock)
    assert response.message.content[0].text == "Found 3 candidates."


async def test_run_subprocess_stream_json_text_matches_json_mode_for_same_result(
    tmp_path: Path,
) -> None:
    """Hard constraint: the final assistant text must be byte-identical to
    what plain --output-format json would have produced for the same run.
    Proven directly: feed the SAME terminal payload through both parse modes
    (as the literal --output-format json body vs. the stream-json transcript
    it would be the tail of) and assert equal CompletionResponse text/usage."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    result_line = _result_line(result="Here is the composed draft copy.", is_error=False)

    proc_json = _mock_proc(result_line.encode())
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc_json)):
        json_response = await adapter._run_subprocess(
            ["fake-cmd"], "prompt", tool_run=True, parse_mode="json"
        )

    stream_transcript = "\n".join(
        [
            _assistant_line(tool_use_id="t1", name="mcp__artemis__dispatch_research"),
            _tool_result_line(tool_use_id="t1"),
            result_line,
        ]
    )
    proc_stream = _mock_proc(stream_transcript.encode())
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc_stream)):
        stream_response = await adapter._run_subprocess(
            ["fake-cmd"], "prompt", tool_run=True, parse_mode="stream-json"
        )

    assert isinstance(json_response.message.content[0], TextBlock)
    assert isinstance(stream_response.message.content[0], TextBlock)
    assert json_response.message.content[0].text == stream_response.message.content[0].text
    assert json_response.usage == stream_response.usage
    assert json_response.stop_reason == stream_response.stop_reason
    # Only the stream-json path reports tool calls — proving the extra info
    # rides alongside the text without altering it.
    assert json_response.tool_calls is None
    assert stream_response.tool_calls == [ToolCallRecord(name="dispatch_research")]


async def test_run_subprocess_stream_json_tool_error_recorded_as_failure(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    transcript = "\n".join(
        [
            _assistant_line(tool_use_id="t1", name="Read"),
            _tool_result_line(tool_use_id="t1", is_error=True, content="File does not exist."),
            _result_line(result="It errored — the file does not exist."),
        ]
    )
    proc = _mock_proc(transcript.encode())

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter._run_subprocess(
            ["fake-cmd"], "prompt", tool_run=True, parse_mode="stream-json"
        )

    assert response.tool_calls == [ToolCallRecord(name="Read", is_error=True)]


async def test_run_subprocess_stream_json_no_tool_calls(tmp_path: Path) -> None:
    """A turn with no tool calls yields an empty list (not None, not a crash)
    on the stream-json path — the CLI still resolved zero tools, positively."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    transcript = _result_line(result="No tools needed for that.")
    proc = _mock_proc(transcript.encode())

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter._run_subprocess(
            ["fake-cmd"], "prompt", tool_run=True, parse_mode="stream-json"
        )

    assert response.tool_calls == []


async def test_run_subprocess_default_parse_mode_is_json_unchanged(tmp_path: Path) -> None:
    """parse_mode defaults to "json" — existing callers that never pass it
    (run_with_tools / CC2 pipeline path) are completely unaffected."""
    binary = _make_executable(tmp_path)
    adapter = ClaudeCodeAdapter(binary_path=str(binary))
    payload = json.dumps({"result": "unaffected", "usage": {}}).encode()
    proc = _mock_proc(payload)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        response = await adapter._run_subprocess(["fake-cmd"], "prompt", tool_run=True)

    assert isinstance(response.message.content[0], TextBlock)
    assert response.message.content[0].text == "unaffected"
    assert response.tool_calls is None
