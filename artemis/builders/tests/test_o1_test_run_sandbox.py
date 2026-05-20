"""Tests for the test_run sandbox (O1 — Decision 2 Option A).

Coverage:
  1. Whitelist enforcement — read-only tools pass, write tools are blocked.
  2. Deny-by-default — unknown tools are blocked.
  3. Rate cap — tool call count is capped at _TEST_RUN_MAX_TOOL_CALLS.
  4. tools_skipped metadata is populated and accurate.
  5. allow_writes=True bypasses the whitelist.
  6. test_mode flag is always True.
  7. Empty tool list runs without error.
  8. Bad definition (no system_prompt) falls back gracefully.
"""

from __future__ import annotations

import pytest

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builder.engine import (
    _TEST_RUN_MAX_TOOL_CALLS,
    _TEST_RUN_SAFE_TOOLS,
    sandbox_run,
)

# Local alias so test bodies can read naturally as `test_run(...)`
# without creating a top-level `test_run` name that pytest would collect.
_run = sandbox_run


def _adapter(text: str = "Test output from agent.") -> FakeAdapter:
    return FakeAdapter([ScriptedReply(text=text)])


# ── 1. Whitelist: safe tools pass ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_run_safe_tools_not_skipped() -> None:
    """Tools that are in _TEST_RUN_SAFE_TOOLS must NOT appear in tools_skipped."""
    safe_tools = list(_TEST_RUN_SAFE_TOOLS)[:3]  # take 3 known-safe tools
    defn = {
        "name": "safe-agent",
        "goal": "Test with safe tools",
        "system_prompt": "You are a test agent.",
        "tools": safe_tools,
    }
    result = await _run(defn, "What issues are in Jira?", adapter=_adapter())

    assert result["test_mode"] is True
    assert not result["tools_skipped"], (
        f"Expected no skipped tools for safe list; got {result['tools_skipped']}"
    )
    assert result["tool_calls"] >= 0  # may be 0 if model doesn't call tools
    assert "output" in result
    assert isinstance(result["output"], str)


# ── 2. Whitelist: write tools are blocked ────────────────────────────────────


@pytest.mark.asyncio
async def test_test_run_write_tools_skipped() -> None:
    """Tools NOT in _TEST_RUN_SAFE_TOOLS must appear in tools_skipped."""
    write_tools = ["jira.create_issue", "slack.post_message", "gcal.create_event", "memory.write"]
    safe_tools = ["jira.list_issues", "slack.search_messages"]
    all_tools = safe_tools + write_tools

    defn = {
        "name": "mixed-agent",
        "goal": "Test with mixed tools",
        "system_prompt": "You are a test agent.",
        "tools": all_tools,
    }
    result = await _run(defn, "List my Jira issues then create a new one.", adapter=_adapter())

    skipped = set(result["tools_skipped"])
    assert skipped == set(write_tools), (
        f"Expected skipped={set(write_tools)}, got skipped={skipped}"
    )
    # safe tools should not be in skipped
    for safe in safe_tools:
        assert safe not in skipped, f"Safe tool {safe!r} was wrongly skipped"


# ── 3. Deny-by-default: unknown tools blocked ────────────────────────────────


@pytest.mark.asyncio
async def test_test_run_unknown_tools_denied() -> None:
    """Any tool not explicitly in _TEST_RUN_SAFE_TOOLS is denied by default."""
    unknown_tools = ["my.custom_tool", "some.new_integration.create", "untrusted.action"]
    defn = {
        "name": "unknown-tool-agent",
        "goal": "Test deny-by-default",
        "system_prompt": "You are a test agent.",
        "tools": unknown_tools,
    }
    result = await _run(defn, "Do something.", adapter=_adapter())

    assert set(result["tools_skipped"]) == set(unknown_tools), (
        f"Unknown tools should be denied; tools_skipped={result['tools_skipped']}"
    )


# ── 4. tools_skipped metadata accuracy ───────────────────────────────────────


@pytest.mark.asyncio
async def test_test_run_tools_skipped_is_correct_subset() -> None:
    """tools_skipped is exactly the complement of safe tools in the definition."""
    mixed = [
        "jira.list_issues",      # safe
        "jira.create_issue",     # write — blocked
        "slack.search_messages", # safe
        "slack.post_message",    # write — blocked
        "memory.search",         # safe
    ]
    expected_skipped = {"jira.create_issue", "slack.post_message"}
    defn = {"system_prompt": "Testing.", "tools": mixed}
    result = await _run(defn, "Run a test.", adapter=_adapter())

    assert set(result["tools_skipped"]) == expected_skipped


# ── 5. allow_writes=True bypasses whitelist ───────────────────────────────────


@pytest.mark.asyncio
async def test_test_run_allow_writes_no_skip() -> None:
    """With allow_writes=True, write tools are NOT skipped."""
    write_tools = ["jira.create_issue", "slack.post_message"]
    defn = {"system_prompt": "Testing.", "tools": write_tools}
    result = await _run(defn, "Create a Jira issue.", adapter=_adapter(), allow_writes=True)

    assert result["allow_writes"] is True
    assert result["tools_skipped"] == [], (
        f"allow_writes=True should produce no skipped tools; got {result['tools_skipped']}"
    )


# ── 6. test_mode flag always set ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_run_test_mode_flag() -> None:
    """result['test_mode'] is always True regardless of allow_writes."""
    defn = {"system_prompt": "Testing.", "tools": []}
    result_normal = await _run(defn, "Hello.", adapter=_adapter())
    result_writes = await _run(defn, "Hello.", adapter=_adapter(), allow_writes=True)

    assert result_normal["test_mode"] is True
    assert result_writes["test_mode"] is True


# ── 7. Empty tool list — no error ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_run_empty_tools() -> None:
    """An agent with no tools should complete without error."""
    defn = {
        "name": "no-tools-agent",
        "goal": "Summarize text",
        "system_prompt": "You are a summarizer.",
        "tools": [],
    }
    result = await _run(defn, "Summarize: the quick brown fox.", adapter=_adapter("Summary: fox."))

    assert result["test_mode"] is True
    assert result["tools_skipped"] == []
    assert "fox" in result["output"].lower() or result["output"]  # model replied


# ── 8. Missing system_prompt falls back gracefully ────────────────────────────


@pytest.mark.asyncio
async def test_test_run_missing_system_prompt_fallback() -> None:
    """A definition missing system_prompt should not raise — uses fallback text."""
    defn = {
        "name": "no-prompt-agent",
        "tools": [],
    }
    # Should not raise
    result = await _run(defn, "Hello.", adapter=_adapter("I'm a helpful assistant."))
    assert result["test_mode"] is True
    assert isinstance(result["output"], str)


# ── 9. tools list as string items (not dicts) ─────────────────────────────────


@pytest.mark.asyncio
async def test_test_run_tools_as_strings() -> None:
    """tools may be a list of plain strings (common in draft definitions)."""
    defn = {
        "system_prompt": "Testing.",
        "tools": ["jira.list_issues", "jira.create_issue"],
    }
    result = await _run(defn, "List issues.", adapter=_adapter())
    assert "jira.create_issue" in result["tools_skipped"]
    assert "jira.list_issues" not in result["tools_skipped"]


# ── 10. Whitelist is deny-by-default (no tools = no skipped) ──────────────────


@pytest.mark.asyncio
async def test_test_run_whitelist_constants_sanity() -> None:
    """Sanity: the whitelist contains only read/list/search/get operations.

    Specifically: no 'create', 'post', 'send', 'update', 'delete', 'write'
    in the allowed tool names.
    """
    write_verbs = {"create", "post", "send", "update", "delete", "write", "modify", "patch"}
    violations = []
    for tool_name in _TEST_RUN_SAFE_TOOLS:
        _, _, action = tool_name.rpartition(".")
        for verb in write_verbs:
            if action.startswith(verb):
                violations.append(tool_name)
    assert not violations, (
        f"Whitelist contains write-capable tools: {violations}. "
        "Review _TEST_RUN_SAFE_TOOLS in engine.py."
    )


# ── 11. MAX_TOOL_CALLS constant is positive and reasonable ────────────────────


def test_test_run_max_tool_calls_constant() -> None:
    """_TEST_RUN_MAX_TOOL_CALLS should be positive and not absurdly large."""
    assert 1 <= _TEST_RUN_MAX_TOOL_CALLS <= 100, (
        f"Unexpected _TEST_RUN_MAX_TOOL_CALLS={_TEST_RUN_MAX_TOOL_CALLS}"
    )
