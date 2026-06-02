"""H1 — Self-teaching tool error messages.

Tests that the ToolRegistry's validate_input() method produces self-teaching
error messages, and that errors flow back to the LLM via ToolResultBlock.

Tests 1–6 are pure unit tests (no DB). Test 7 requires a live Postgres test
database; it is marked `integration` and skipped by default.

Run with:
    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test \\
    uv run pytest artemis/agent/tests/test_h1_self_teaching_errors.py -v
"""

from __future__ import annotations

from typing import Any

from artemis.agent.loop import run_turn, user_message
from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.agent.tools import ToolRegistry, _enum_list
from artemis.agent.types import Tool, ToolResultBlock

# NOTE: async tests are individually marked @pytest.mark.asyncio rather than
# using a module-level pytestmark, because this file also contains sync tests.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _registry_with_tool(name: str, schema: dict[str, Any]) -> tuple[ToolRegistry, list[str]]:
    """Build a registry with a single tool whose impl records calls and returns 'ok'."""
    registry = ToolRegistry()
    calls: list[str] = []

    async def _impl(inp: dict[str, Any]) -> str:
        calls.append("called")
        return "ok"

    registry.register(Tool(name=name, description="test tool", input_schema=schema), _impl)
    return registry, calls


def _make_exc(
    schema: dict[str, Any],
    instance: Any,
    validator: str,
    validator_value: Any,
    message: str,
    path: list[Any] | None = None,
) -> Any:
    """Build a minimal jsonschema-like ValidationError stub for unit testing the formatter."""
    import collections

    class _FakeExc:
        def __init__(self) -> None:
            self.schema = schema
            self.instance = instance
            self.validator = validator
            self.validator_value = validator_value
            self.message = message
            self.absolute_path = collections.deque(path or [])

    return _FakeExc()


# ---------------------------------------------------------------------------
# Test 1 — Enum violation produces enumerated error
# ---------------------------------------------------------------------------


async def test_enum_violation_enumerates_valid_values() -> None:
    """H1 test 1: enum violation yields all valid values in the error message."""
    registry, calls = _registry_with_tool(
        "test.color",
        {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["red", "green", "blue"]},
            },
        },
    )

    error = registry.validate_input("test.color", {"color": "purple"})

    assert error is not None
    assert "Valid values are:" in error
    assert "red" in error
    assert "green" in error
    assert "blue" in error
    assert "purple" in error  # rejected value must be named
    assert len(error) <= 300
    assert calls == []  # impl not called


async def test_enum_violation_via_loop_returns_error_block() -> None:
    """H1 test 1b: enum error flows back to LLM as ToolResultBlock(is_error=True)."""
    registry, _calls = _registry_with_tool(
        "test.state",
        {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["a", "b", "c"]},
            },
            "required": ["state"],
        },
    )

    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("call_1", "test.state", {"state": "invalid"})], stop_reason="tool_use"
            ),
            ScriptedReply(text="I see the valid values"),
        ]
    )
    result = await run_turn(
        adapter=adapter,
        messages=[user_message("call with bad state")],
        tools=registry,
    )

    tool_result_msg = result.messages[2]
    assert tool_result_msg.role == "user"
    block = tool_result_msg.content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.is_error is True
    assert "Valid values are:" in block.content
    assert "a, b, c" in block.content


# ---------------------------------------------------------------------------
# Test 2 — Type mismatch produces typed error
# ---------------------------------------------------------------------------


async def test_type_mismatch_names_expected_and_actual_types() -> None:
    """H1 test 2: type mismatch error names expected and actual types."""
    registry, calls = _registry_with_tool(
        "test.count",
        {
            "type": "object",
            "properties": {
                "n": {"type": "integer"},
            },
        },
    )

    error = registry.validate_input("test.count", {"n": "forty-two"})

    assert error is not None
    assert "integer" in error
    assert "str" in error  # actual type
    assert len(error) <= 300
    assert calls == []


# ---------------------------------------------------------------------------
# Test 3 — Missing required parameter produces named error
# ---------------------------------------------------------------------------


async def test_missing_required_field_names_the_field() -> None:
    """H1 test 3: missing required parameter error names the missing field."""
    registry, calls = _registry_with_tool(
        "test.create",
        {
            "type": "object",
            "required": ["agent_id"],
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent identifier.",
                },
            },
        },
    )

    error = registry.validate_input("test.create", {})

    assert error is not None
    assert "agent_id" in error
    assert "Missing" in error
    assert len(error) <= 300
    assert calls == []


# ---------------------------------------------------------------------------
# Test 4 — Constraint violation reports the constraint
# ---------------------------------------------------------------------------


async def test_constraint_violation_names_constraint_and_limit() -> None:
    """H1 test 4: constraint violation names the constraint and limit."""
    registry, calls = _registry_with_tool(
        "test.score",
        {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "maximum": 10},
            },
        },
    )

    error = registry.validate_input("test.score", {"score": 20})

    assert error is not None
    assert "maximum" in error
    assert "10" in error
    assert "20" in error  # the actual value
    assert len(error) <= 300
    assert calls == []


# ---------------------------------------------------------------------------
# Test 5 — Additional properties violation lists allowed keys
# ---------------------------------------------------------------------------


async def test_additional_properties_lists_allowed_keys() -> None:
    """H1 test 5: additionalProperties error lists the allowed parameters."""
    registry, calls = _registry_with_tool(
        "test.lookup",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "integer"},
                "format": {"type": "string"},
            },
        },
    )

    error = registry.validate_input("test.lookup", {"id": 1, "unknown_key": "oops"})

    assert error is not None
    assert "id" in error
    assert "format" in error
    # Should mention the offending key or list of allowed keys
    assert len(error) <= 300
    assert calls == []


# ---------------------------------------------------------------------------
# Test 6 — End-to-end recovery: agent sees error, retries with correct value
# ---------------------------------------------------------------------------


async def test_e2e_recovery_agent_corrects_after_seeing_enum_error() -> None:
    """H1 test 6: agent gets enum error on first call, then succeeds on second call.

    This proves the self-teaching round-trip works: the LLM sees the valid values
    in the ToolResultBlock and can self-correct on the next turn.
    """
    registry, calls = _registry_with_tool(
        "signal_queue.update_status_mock",
        {
            "type": "object",
            "required": ["signalId", "newStatus"],
            "properties": {
                "signalId": {"type": "integer"},
                "newStatus": {
                    "type": "string",
                    "enum": [
                        "pending_qualification",
                        "qualified",
                        "suppressed_stale",
                        "rejected_hard_filter",
                    ],
                },
            },
        },
    )

    # Turn 1: agent hallucinate "pending_human_review" → gets enum error
    # Turn 2: agent retries with "qualified" → succeeds
    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[
                    (
                        "c1",
                        "signal_queue.update_status_mock",
                        {"signalId": 42, "newStatus": "pending_human_review"},
                    )
                ],
                stop_reason="tool_use",
            ),
            ScriptedReply(
                tool_calls=[
                    (
                        "c2",
                        "signal_queue.update_status_mock",
                        {"signalId": 42, "newStatus": "qualified"},
                    )
                ],
                stop_reason="tool_use",
            ),
            ScriptedReply(text="Signal successfully qualified."),
        ]
    )

    result = await run_turn(
        adapter=adapter,
        messages=[user_message("qualify signal 42")],
        tools=registry,
    )

    # user + assistant(bad call) + user(error) + assistant(good call) + user(ok) + assistant(done)
    assert len(result.messages) == 6
    assert result.stop_reason == "end_turn"

    # First tool result must be an error with enum values
    first_tool_result_msg = result.messages[2]
    first_block = first_tool_result_msg.content[0]
    assert isinstance(first_block, ToolResultBlock)
    assert first_block.is_error is True
    assert "Valid values are:" in first_block.content
    assert "qualified" in first_block.content

    # Second tool result must succeed
    second_tool_result_msg = result.messages[4]
    second_block = second_tool_result_msg.content[0]
    assert isinstance(second_block, ToolResultBlock)
    assert second_block.is_error is False
    assert second_block.content == "ok"

    # The impl was called exactly once (only for the valid call)
    assert calls == ["called"]


# ---------------------------------------------------------------------------
# Test 7 — Valid inputs are unaffected (regression: existing tool calls succeed)
# ---------------------------------------------------------------------------


async def test_valid_inputs_pass_through_unchanged() -> None:
    """H1 test 7 (unit portion): valid inputs produce None validation error.

    Spot-checks 5 representative valid inputs across different tool schemas
    to confirm the H1 changes do NOT affect the success path.
    """
    # Representative sample of real tool schemas + valid inputs
    cases: list[tuple[dict[str, Any], dict[str, Any]]] = [
        # signal_queue.write – valid sourceType enum
        (
            {
                "type": "object",
                "required": [
                    "sourceType",
                    "headline",
                    "campaignFamily",
                    "urgencyTier",
                    "reasonCodes",
                    "evidence",
                ],
                "properties": {
                    "sourceType": {
                        "type": "string",
                        "enum": [
                            "manual",
                            "starbridge",
                            "news_article",
                            "board_minutes",
                            "state_doe",
                            "linkedin_post",
                        ],
                    },
                    "headline": {"type": "string"},
                    "campaignFamily": {"type": "string"},
                    "urgencyTier": {"type": "string", "enum": ["hot", "standard", "low"]},
                    "reasonCodes": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "evidence": {"type": "string"},
                },
            },
            {
                "sourceType": "news_article",
                "headline": "District approves new reading program",
                "campaignFamily": "literacy",
                "urgencyTier": "standard",
                "reasonCodes": ["RC-001"],
                "evidence": "https://example.com/article",
            },
        ),
        # signal_queue.update_status – valid newStatus enum
        (
            {
                "type": "object",
                "required": ["signalId", "newStatus"],
                "properties": {
                    "signalId": {"type": "integer"},
                    "newStatus": {
                        "type": "string",
                        "enum": [
                            "pending_qualification",
                            "qualified",
                            "rejected_hard_filter",
                            "suppressed_stale",
                            "approved",
                            "rejected_at_gate_1",
                            "snoozed",
                            "archived",
                        ],
                    },
                    "reason": {"type": "string"},
                },
            },
            {"signalId": 123, "newStatus": "qualified"},
        ),
        # signal_queue.get – valid signalId
        (
            {
                "type": "object",
                "required": ["signalId"],
                "properties": {"signalId": {"type": "integer"}},
            },
            {"signalId": 42},
        ),
        # echo tool (minimal schema) – any object passes
        (
            {"type": "object", "properties": {"text": {"type": "string"}}},
            {"text": "hello"},
        ),
        # Tool with no additionalProperties restriction – extra keys allowed
        (
            {"type": "object", "properties": {"x": {"type": "integer"}}},
            {"x": 1, "extra": "ignored"},
        ),
    ]

    for i, (schema, valid_input) in enumerate(cases):
        registry = ToolRegistry()

        async def _ok(inp: dict[str, Any]) -> str:
            return "ok"

        registry.register(Tool(name=f"test.case{i}", description="test", input_schema=schema), _ok)
        error = registry.validate_input(f"test.case{i}", valid_input)
        assert error is None, (
            f"Case {i}: valid input {valid_input!r} should not fail validation, but got: {error!r}"
        )


# ---------------------------------------------------------------------------
# Test: truncation helper keeps messages under 300 chars
# ---------------------------------------------------------------------------


def test_enum_list_truncates_long_list() -> None:
    """Long enum lists are truncated to first 10 + 'and N more'."""
    many = [f"val_{i}" for i in range(20)]
    result = _enum_list(many)
    assert "and 10 more" in result
    assert len(result) < 200


def test_message_under_300_chars_for_large_enum() -> None:
    """Even with 50 enum values the self-teaching message stays under 300 chars."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": [f"state_{i}" for i in range(50)]},
        },
    }
    registry, _ = _registry_with_tool("test.big", schema)
    error = registry.validate_input("test.big", {"state": "invalid"})
    assert error is not None
    assert len(error) <= 300
