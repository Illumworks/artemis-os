"""Tests for Floating Artemis chat orchestration with FakeAdapter."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.agent.types import Message, RunResult, TextBlock, ToolUseBlock, Usage
from artemis.floating_artemis.authority import confirmation_store
from artemis.floating_artemis.chat import (
    TurnResult,
    _build_auto_invoke_tool_registry,
    _build_system_prompt,
    _build_tool_registry,
    handle_turn,
    resume_after_confirm,
)
from artemis.floating_artemis.context import floating_session_id_var
from artemis.floating_artemis.intent import IntentKind

pytestmark = pytest.mark.asyncio

# ── System prompt ─────────────────────────────────────────────────────────────


def test_build_system_prompt_contains_persona() -> None:
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
    )
    assert "Artemis" in prompt
    assert "direct" in prompt.lower() or "Direct" in prompt


def test_build_system_prompt_includes_voice_samples() -> None:
    prompt = _build_system_prompt(
        voice_samples=["Already on it.", "Done. You're welcome."],
        page_context=None,
        available_surfaces=[],
    )
    assert "Already on it." in prompt
    assert "Done. You're welcome." in prompt


def test_build_system_prompt_includes_page_context() -> None:
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context="Page: okr (ref: obj-42)",
        available_surfaces=[],
    )
    assert "okr" in prompt
    assert "obj-42" in prompt


def test_build_system_prompt_includes_surfaces() -> None:
    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=["okr", "marketing-os"],
    )
    assert "okr" in prompt
    assert "marketing-os" in prompt


# ── Tool registry ─────────────────────────────────────────────────────────────


def test_build_tool_registry_core_tools_always_present() -> None:
    reg = _build_tool_registry(available_surfaces=set())
    assert "query_memory" in reg
    assert "read_file" in reg
    assert "surface_status" in reg
    assert "health_check" in reg


def test_build_tool_registry_okr_only_when_surface_available() -> None:
    reg_no_okr = _build_tool_registry(available_surfaces=set())
    reg_with_okr = _build_tool_registry(available_surfaces={"okr"})
    assert "list_okr_objectives" not in reg_no_okr
    assert "list_okr_objectives" in reg_with_okr


def test_build_tool_registry_marketing_when_signal_queue_available() -> None:
    reg = _build_tool_registry(available_surfaces={"signal-queue"})
    assert "list_signals" in reg


def test_build_tool_registry_marketing_when_marketing_os_available() -> None:
    reg = _build_tool_registry(available_surfaces={"marketing-os"})
    assert "list_signals" in reg
    assert "get_message_compass" in reg
    assert "search_claims_register" in reg
    assert "get_campaign_performance" in reg
    assert "post_analyst_message" in reg


def test_build_auto_invoke_tool_registry_excludes_confirmation_tools() -> None:
    auth = _build_tool_registry(available_surfaces={"okr"})
    reg = _build_auto_invoke_tool_registry(auth, "fa-auto-1")

    assert "query_memory" in reg
    assert "write_memory" in reg
    assert "propose_agent" not in reg


# ── handle_turn with FakeAdapter ──────────────────────────────────────────────


async def test_handle_turn_simple_reply() -> None:
    adapter = FakeAdapter([ScriptedReply(text="Systems nominal. All good.")])

    with (
        patch(
            "artemis.floating_artemis.chat.select_voice_samples", return_value=["Already on it."]
        ),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages"),
        patch(
            "artemis.floating_artemis.chat.get_status", return_value={"available_surfaces": ["okr"]}
        ),
        patch("artemis.floating_artemis.chat._broadcast"),
    ):
        result = await handle_turn(
            session_id="test-session-1",
            user_text="How are things?",
            adapter=adapter,
        )

    assert isinstance(result, TurnResult)
    assert result.response_text == "Systems nominal. All good."
    assert result.stop_reason == "end_turn"


async def test_handle_turn_system_prompt_includes_voice_samples() -> None:
    """Verify voice samples appear in the system prompt sent to the model."""
    adapter = FakeAdapter([ScriptedReply(text="Done.")])
    captured_requests: list[Any] = []

    original_complete = adapter.complete

    async def capturing_complete(req: Any) -> Any:
        captured_requests.append(req)
        return await original_complete(req)

    samples = ["Already on it.", "Done. You're welcome."]

    with (
        patch.object(adapter, "complete", side_effect=capturing_complete),
        patch("artemis.floating_artemis.chat.select_voice_samples", return_value=samples),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages"),
        patch("artemis.floating_artemis.chat.get_status", return_value={"available_surfaces": []}),
        patch("artemis.floating_artemis.chat._broadcast"),
    ):
        await handle_turn(
            session_id="test-session-2",
            user_text="Status?",
            adapter=adapter,
        )

    assert len(captured_requests) == 1
    system = captured_requests[0].system or ""
    assert "Already on it." in system


async def test_handle_turn_uses_callie_profile_from_session_metadata() -> None:
    adapter = FakeAdapter([ScriptedReply(text="Here's the angle.")])
    captured_requests: list[Any] = []
    session_row = cast(Any, type("Row", (), {"metadata_": {"agent_id": "callie"}})())

    original_complete = adapter.complete

    async def capturing_complete(req: Any) -> Any:
        captured_requests.append(req)
        return await original_complete(req)

    with (
        patch.object(adapter, "complete", side_effect=capturing_complete),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._get_recent_meeting_context", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages"),
        patch("artemis.floating_artemis.chat.get_status", return_value={"available_surfaces": []}),
        patch("artemis.floating_artemis.chat._broadcast"),
        patch("artemis.floating_artemis.chat.inject_memory_context", side_effect=lambda *a: a[0]),
        patch("artemis.floating_artemis.repository.get_session_by_id", return_value=session_row),
    ):
        result = await handle_turn(
            session_id="fa-callie-1",
            user_text="What should marketing do next?",
            adapter=adapter,
        )

    assert result.response_text == "Here's the angle."
    assert len(captured_requests) == 1
    system = captured_requests[0].system or ""
    assert "You are Callie, short for Calliope" in system
    assert "marketing strategist and analyst" in system
    assert "Marketing is Callie's lane" not in system


async def test_handle_turn_intent_shortcut_active_runs() -> None:
    """Observability shortcut avoids LLM call for 'what's running' pattern."""
    adapter = FakeAdapter([])  # No replies — should not be called

    from artemis.floating_artemis.intent import classify_intent, handle_observability_intent

    intent = classify_intent("what's running")
    assert intent.kind == IntentKind.ACTIVE_RUNS

    # Patch get_active_runs at the repository level (where it is imported inside the function)
    with patch("artemis.floating_artemis.repository.get_active_runs", return_value=[]):
        shortcut = await handle_observability_intent(intent)

    assert shortcut is not None
    # Either "Nothing running" or listing format
    assert (
        "running" in shortcut["response"].lower()
        or shortcut["response"] == "Nothing running right now."
    )

    # FakeAdapter should have received 0 requests (no LLM call)
    assert len(adapter.requests) == 0


async def test_handle_turn_layer3_tool_yields() -> None:
    """Layer-3 tool in response causes turn to suspend (tool_pending stop_reason)."""
    # Script: model emits propose_agent tool_use (layer 3)
    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("tuid-layer3", "propose_agent", {"name": "my-new-agent"})],
                stop_reason="tool_use",
            )
        ]
    )

    # Clear any lingering confirmations
    confirmation_store._pending.clear()

    with (
        patch("artemis.floating_artemis.chat.select_voice_samples", return_value=[]),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages"),
        patch("artemis.floating_artemis.chat.get_status", return_value={"available_surfaces": []}),
        patch("artemis.floating_artemis.chat._broadcast"),
    ):
        result = await handle_turn(
            session_id="test-session-layer3",
            user_text="Create a new agent for me",
            adapter=adapter,
        )

    assert result.stop_reason == "tool_pending"
    assert result.pending_tool_use_id is not None


async def test_handle_turn_persists_assistant_tool_use_and_resume_keeps_prior_results() -> None:
    session_id = "fa-mixed-tools"
    proposal_name = "FA Mixed Proposal Agent"
    confirmation_store._pending.clear()

    adapter_first = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[
                    (
                        "fa-qm-1",
                        "query_memory",
                        {"query": "what do we know?", "scope": "global:global"},
                    ),
                    ("fa-pa-1", "propose_agent", {"name": proposal_name}),
                ],
                stop_reason="tool_use",
            )
        ]
    )

    with (
        patch("artemis.floating_artemis.chat.select_voice_samples", return_value=[]),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch(
            "artemis.floating_artemis.chat._persist_messages", new_callable=AsyncMock
        ) as mock_persist,
        patch("artemis.floating_artemis.chat.get_status", return_value={"available_surfaces": []}),
        patch("artemis.floating_artemis.chat.inject_memory_context", side_effect=lambda *a: a[0]),
        patch("artemis.floating_artemis.chat._get_recent_meeting_context", return_value=None),
        patch("artemis.floating_artemis.chat._broadcast"),
        patch("artemis.floating_artemis.chat.write_turn_drawer"),
    ):
        turn1 = await handle_turn(
            session_id=session_id,
            user_text="Search memory, then stage the agent.",
            adapter=adapter_first,
        )

    assert turn1.stop_reason == "tool_pending"
    assert turn1.pending_tool_use_id == "fa-pa-1"

    pending = confirmation_store.get("fa-pa-1")
    assert pending is not None
    assert [block["tool_use_id"] for block in pending.prior_tool_results] == ["fa-qm-1"]
    assert mock_persist.await_args is not None
    persist_kwargs = mock_persist.await_args.kwargs
    assert persist_kwargs["session_id"] == session_id
    assert persist_kwargs["user_text"] == "Search memory, then stage the agent."
    assert [block["id"] for block in persist_kwargs["assistant_content"]] == ["fa-qm-1", "fa-pa-1"]

    adapter_resume = FakeAdapter([ScriptedReply(text="Proposal staged and ready.")])
    history = [
        Message(role="user", content=[TextBlock(text="Search memory, then stage the agent.")]),
        Message(
            role="assistant",
            content=[
                ToolUseBlock(
                    id="fa-qm-1", name="query_memory", input={"query": "what do we know?"}
                ),
                ToolUseBlock(id="fa-pa-1", name="propose_agent", input={"name": proposal_name}),
            ],
        ),
    ]

    with (
        patch(
            "artemis.floating_artemis.tools.builders._propose_agent",
            new=AsyncMock(return_value="Agent proposal saved: proposal_id=77"),
        ),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=history),
        patch(
            "artemis.floating_artemis.chat._persist_messages", new_callable=AsyncMock
        ) as mock_resume_persist,
        patch("artemis.floating_artemis.chat._broadcast"),
        patch("artemis.floating_artemis.chat.write_turn_drawer"),
    ):
        turn2 = await resume_after_confirm(
            session_id=session_id,
            tool_use_id="fa-pa-1",
            decision="run",
            adapter=adapter_resume,
        )

    assert turn2.response_text == "Proposal staged and ready."
    assert mock_resume_persist.await_args is not None
    resume_kwargs = mock_resume_persist.await_args.kwargs
    assert [block["tool_use_id"] for block in resume_kwargs["user_content"]] == [
        "fa-qm-1",
        "fa-pa-1",
    ]
    assert resume_kwargs["assistant_text"] == "Proposal staged and ready."


async def test_handle_turn_claude_code_tool_turn_sets_session_context() -> None:
    from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter.__new__(ClaudeCodeAdapter)
    adapter._binary = "/usr/bin/true"
    adapter._default_model = "claude-sonnet-4-6"

    seen: dict[str, Any] = {}

    async def _fake_run_turn(**kwargs: Any) -> RunResult:
        tools = kwargs["tools"]
        seen["session_id"] = floating_session_id_var.get()
        seen["tool_names"] = [tool.name for tool in tools.specs()]
        return RunResult(
            messages=[
                Message(role="user", content=[TextBlock(text="Search memory.")]),
                Message(role="assistant", content=[TextBlock(text="Used the memory tools.")]),
            ],
            stop_reason="end_turn",
            usage=Usage(),
            iterations=1,
        )

    with (
        patch("artemis.floating_artemis.chat.run_turn", side_effect=_fake_run_turn),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages", return_value=1),
        patch(
            "artemis.floating_artemis.chat.get_status",
            return_value={"available_surfaces": ["okr"]},
        ),
        patch("artemis.floating_artemis.chat._broadcast"),
        patch("artemis.floating_artemis.chat.inject_memory_context", side_effect=lambda *a: a[0]),
        patch("artemis.floating_artemis.chat._get_recent_meeting_context", return_value=None),
        patch("artemis.floating_artemis.chat.write_turn_drawer"),
    ):
        result = await handle_turn(
            session_id="fa-session-tools",
            user_text="Search memory for Jon.",
            adapter=adapter,
        )

    assert result.response_text == "Used the memory tools."
    assert seen["session_id"] == "fa-session-tools"
    assert "query_memory" in seen["tool_names"]
    assert "write_memory" in seen["tool_names"]
    assert "propose_agent" not in seen["tool_names"]


async def test_handle_turn_resume_after_confirm_run() -> None:
    """tool-confirm with decision=run executes the tool and resumes."""
    # First turn: model requests propose_agent (layer 3) → yields
    # After confirm: model gives final reply
    adapter_first = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("tuid-confirm-run", "propose_agent", {"name": "agent-x"})],
                stop_reason="tool_use",
            )
        ]
    )
    confirmation_store._pending.clear()

    with (
        patch("artemis.floating_artemis.chat.select_voice_samples", return_value=[]),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages"),
        patch("artemis.floating_artemis.chat.get_status", return_value={"available_surfaces": []}),
        patch("artemis.floating_artemis.chat._broadcast"),
    ):
        turn1 = await handle_turn(
            session_id="test-confirm-session",
            user_text="Propose agent",
            adapter=adapter_first,
        )

    assert turn1.stop_reason == "tool_pending"
    pending_id = cast(str, turn1.pending_tool_use_id)

    # Now confirm with run
    adapter_resume = FakeAdapter([ScriptedReply(text="Agent proposal submitted.")])

    with (
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages"),
        patch("artemis.floating_artemis.chat._broadcast"),
    ):
        turn2 = await resume_after_confirm(
            session_id="test-confirm-session",
            tool_use_id=pending_id,
            decision="run",
            adapter=adapter_resume,
        )

    assert turn2.response_text == "Agent proposal submitted."
    assert turn2.stop_reason == "end_turn"


async def test_handle_turn_resume_after_confirm_cancel() -> None:
    """tool-confirm with decision=cancel gives cancellation message and resumes."""
    adapter_first = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("tuid-cancel", "propose_agent", {"name": "agent-y"})],
                stop_reason="tool_use",
            )
        ]
    )
    confirmation_store._pending.clear()

    with (
        patch("artemis.floating_artemis.chat.select_voice_samples", return_value=[]),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages"),
        patch("artemis.floating_artemis.chat.get_status", return_value={"available_surfaces": []}),
        patch("artemis.floating_artemis.chat._broadcast"),
    ):
        turn1 = await handle_turn(
            session_id="test-cancel-session",
            user_text="Propose agent Y",
            adapter=adapter_first,
        )

    assert turn1.stop_reason == "tool_pending"
    pending_id = cast(str, turn1.pending_tool_use_id)

    adapter_resume = FakeAdapter([ScriptedReply(text="Cancelled. No agent created.")])

    with (
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
        patch("artemis.floating_artemis.chat._persist_messages"),
        patch("artemis.floating_artemis.chat._broadcast"),
    ):
        turn2 = await resume_after_confirm(
            session_id="test-cancel-session",
            tool_use_id=pending_id,
            decision="cancel",
            adapter=adapter_resume,
        )

    assert turn2.stop_reason == "end_turn"
    # Tool result (user-role message) should appear in the messages passed to resume
    # The first request to the adapter includes: [tool_result_msg] (history is empty in mock)
    req_msgs = adapter_resume.requests[0].messages
    # Find the tool_result block
    tool_result_msg = next((m for m in req_msgs if m.role == "user"), None)
    assert tool_result_msg is not None
    from artemis.agent.types import ToolResultBlock

    tool_blocks = [b for b in tool_result_msg.content if isinstance(b, ToolResultBlock)]
    assert len(tool_blocks) > 0
    assert "cancelled" in tool_blocks[0].content.lower()
