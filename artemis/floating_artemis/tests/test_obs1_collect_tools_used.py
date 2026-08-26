"""Tests for chat.py's _collect_tools_used (OBS-1).

Pure function, no DB — verifies the merge between the two sources of tool-call
information a turn can carry:

- ``result.messages`` ToolUseBlocks (the Anthropic path — run_turn executes
  tool_use rounds itself, so these land directly in the conversation).
- ``result.metadata["tool_calls"]`` (the claude-code MCP path — Builder /
  Floating Artemis / every Slack-facing agent — the CLI's own internal tool
  loop resolves calls before run_turn ever sees a ToolUseBlock, so this is the
  ONLY place those calls show up; see artemis/agent/loop.py and
  artemis/providers/claude_code/adapter.py::_parse_stream_json).

Both must dedupe by first-occurrence, in encounter order, and a tool that
errored at least once must be recorded as "<name>:error" — see the OBS-1
brief's explicit test list.
"""

from __future__ import annotations

from artemis.agent.types import (
    Message,
    RunResult,
    TextBlock,
    ToolCallRecord,
    ToolUseBlock,
    Usage,
)
from artemis.floating_artemis.chat import _collect_tools_used


def _run_result(
    messages: list[Message], *, tool_calls: list[ToolCallRecord] | None = None
) -> RunResult:
    metadata = {"tool_calls": tool_calls} if tool_calls is not None else {}
    return RunResult(
        messages=messages,
        stop_reason="end_turn",
        usage=Usage(),
        iterations=1,
        metadata=metadata,
    )


def _assistant(*blocks: object) -> Message:
    return Message(role="assistant", content=list(blocks))  # type: ignore[arg-type]


# ── Anthropic path (ToolUseBlock in result.messages) — must stay unchanged ───


def test_anthropic_path_dedupes_tool_use_blocks_in_order() -> None:
    """Brief Test 1 + Test 7: two distinct tool_use events yield both names, in
    order; this is the pre-existing Anthropic-path behavior, unchanged."""
    result = _run_result(
        [
            _assistant(ToolUseBlock(id="1", name="query_memory", input={})),
            _assistant(ToolUseBlock(id="2", name="get_okr", input={})),
        ]
    )

    assert _collect_tools_used(result) == ["query_memory", "get_okr"]


def test_anthropic_path_dedupes_repeated_name() -> None:
    result = _run_result(
        [
            _assistant(ToolUseBlock(id="1", name="query_memory", input={})),
            _assistant(ToolUseBlock(id="2", name="query_memory", input={})),
        ]
    )

    assert _collect_tools_used(result) == ["query_memory"]


def test_anthropic_path_ignores_non_tool_use_blocks_and_user_messages() -> None:
    result = _run_result(
        [
            Message(role="user", content=[TextBlock(text="hi")]),
            _assistant(
                TextBlock(text="thinking..."), ToolUseBlock(id="1", name="get_okr", input={})
            ),
        ]
    )

    assert _collect_tools_used(result) == ["get_okr"]


# ── claude-code path (result.metadata["tool_calls"]) — the OBS-1 fix ────────


def test_claude_code_path_reads_from_metadata_when_no_tool_use_blocks() -> None:
    """The whole point of OBS-1: a claude-code MCP turn never produces a
    ToolUseBlock, so this must come from metadata, not messages."""
    result = _run_result(
        [_assistant(TextBlock(text="Here are the candidates."))],
        tool_calls=[
            ToolCallRecord(name="dispatch_research"),
            ToolCallRecord(name="list_candidates"),
        ],
    )

    assert _collect_tools_used(result) == ["dispatch_research", "list_candidates"]


def test_claude_code_path_records_tool_failure_as_error_suffix() -> None:
    """Brief Test 2: a tool_result carrying an error is recorded as a failure,
    not a plain success — the case that matters most (this is what happened
    in the Argus outage)."""
    result = _run_result(
        [_assistant(TextBlock(text="Something went wrong."))],
        tool_calls=[ToolCallRecord(name="dispatch_research", is_error=True)],
    )

    assert _collect_tools_used(result) == ["dispatch_research:error"]


def test_claude_code_path_one_failure_among_repeats_marks_error() -> None:
    """A deduped name invoked more than once with at least one failure is
    recorded as failed — a later success does not erase an earlier error."""
    result = _run_result(
        [_assistant(TextBlock(text="ok"))],
        tool_calls=[
            ToolCallRecord(name="dispatch_research", is_error=True),
            ToolCallRecord(name="dispatch_research", is_error=False),
        ],
    )

    assert _collect_tools_used(result) == ["dispatch_research:error"]


def test_no_tool_calls_at_all_yields_empty_list() -> None:
    """Brief Test 4: a turn with no tool calls yields an empty list, and does
    not crash on the absent `metadata["tool_calls"]` key."""
    result = _run_result([_assistant(TextBlock(text="Just chatting, no tools."))])

    assert _collect_tools_used(result) == []


def test_empty_tool_calls_list_in_metadata_yields_empty_list() -> None:
    """metadata["tool_calls"] present but empty (positive "zero calls" signal,
    not absence) must still yield []."""
    result = _run_result(
        [_assistant(TextBlock(text="No tools needed."))],
        tool_calls=[],
    )

    assert _collect_tools_used(result) == []


# ── Merge behavior (defensive — real turns only ever populate one source) ───


def test_merges_both_sources_anthropic_first_then_metadata() -> None:
    result = _run_result(
        [_assistant(ToolUseBlock(id="1", name="query_memory", input={}))],
        tool_calls=[ToolCallRecord(name="dispatch_research", is_error=True)],
    )

    assert _collect_tools_used(result) == ["query_memory", "dispatch_research:error"]
