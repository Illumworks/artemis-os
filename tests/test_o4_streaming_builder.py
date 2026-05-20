"""Tests for O4 — Streaming SSE for Builder message endpoint.

Covers:
  - handle_turn_stream: happy multi-tool-call turn
  - handle_turn_stream: mid-turn cancellation
  - handle_turn_stream: error path (adapter raises)
  - Sync wrapper (handle_turn) produces identical final state as draining stream
  - SSE response headers: Cache-Control + X-Accel-Buffering
  - BuilderEvent.to_sse() wire format
  - heartbeat logic (event shape)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_text_response(text: str, stop_reason: str = "end_turn") -> Any:
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason=stop_reason,
        usage=Usage(),
    )


def _make_tool_response(tool_id: str, tool_name: str, inputs: dict[str, Any]) -> Any:
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, ToolUseBlock, Usage

    return CompletionResponse(
        message=Message(
            role="assistant", content=[ToolUseBlock(id=tool_id, name=tool_name, input=inputs)]
        ),
        stop_reason="tool_use",
        usage=Usage(),
    )


def _fake_session_row(draft: dict | None = None) -> Any:
    row = MagicMock()
    row.conversation = []
    row.draft = draft
    row.target_id = None
    row.status = "active"
    return row


def _mock_registry() -> Any:
    mock_reg = MagicMock()
    mock_reg.specs.return_value = []
    mock_reg.get.return_value = None
    return mock_reg


# patch targets: functions are imported with `from x import y` inside the
# generator body, so we must patch in the source module.
_REPO = "artemis.builder.repository"
_BUILDER = "artemis.builder.agent_builder"


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_happy_turn_no_tools() -> None:
    """handle_turn_stream yields turn_start + assistant_token + turn_complete."""
    from artemis.builder.agent_builder import handle_turn_stream

    adapter = MagicMock()
    adapter.complete = AsyncMock(return_value=_make_text_response("Hello world"))

    session_row = _fake_session_row()
    mock_db = AsyncMock()

    with (
        patch(f"{_REPO}.get_builder_session", new_callable=AsyncMock, return_value=session_row),
        patch(f"{_REPO}.append_builder_message", new_callable=AsyncMock),
        patch(f"{_BUILDER}.build_tool_registry", return_value=_mock_registry()),
    ):
        events = []
        async for ev in handle_turn_stream(
            builder_session_id=1,
            user_text="Build me an agent",
            adapter=adapter,
            db_session=mock_db,
        ):
            events.append(ev)

    types = [e.type for e in events]
    assert types[0] == "turn_start"
    assert "assistant_token" in types
    assert types[-1] == "turn_complete"

    complete_ev = events[-1]
    assert complete_ev.payload["assistant_text"] == "Hello world"
    assert complete_ev.payload["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_stream_tool_call_sequence() -> None:
    """handle_turn_stream yields tool_call + tool_result before turn_complete."""
    from artemis.builder.agent_builder import handle_turn_stream

    adapter = MagicMock()
    adapter.complete = AsyncMock(
        side_effect=[
            _make_tool_response("tc1", "read_existing", {"kind": "agent"}),
            _make_text_response("I found 0 existing agents."),
        ]
    )

    session_row = _fake_session_row()
    mock_db = AsyncMock()

    entry = MagicMock()
    entry.impl = AsyncMock(return_value=json.dumps([]))
    reg = MagicMock()
    reg.specs.return_value = []
    reg.get.return_value = entry

    with (
        patch(f"{_REPO}.get_builder_session", new_callable=AsyncMock, return_value=session_row),
        patch(f"{_REPO}.append_builder_message", new_callable=AsyncMock),
        patch(f"{_BUILDER}.build_tool_registry", return_value=reg),
    ):
        events = []
        async for ev in handle_turn_stream(
            builder_session_id=2,
            user_text="Show me existing agents",
            adapter=adapter,
            db_session=mock_db,
        ):
            events.append(ev)

    types = [e.type for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert types[-1] == "turn_complete"

    tc = next(e for e in events if e.type == "tool_call")
    assert tc.payload["tool_name"] == "read_existing"
    tr = next(e for e in events if e.type == "tool_result")
    assert tr.payload["tool_call_id"] == "tc1"
    assert tr.payload["ok"] is True


@pytest.mark.asyncio
async def test_stream_cancellation_stops_cleanly() -> None:
    """Closing the generator mid-turn does not crash; partial text is safe to persist."""
    from artemis.builder.agent_builder import handle_turn_stream

    async def _slow_complete(req: Any) -> Any:
        await asyncio.sleep(100)
        return _make_text_response("never")

    adapter = MagicMock()
    adapter.complete = _slow_complete

    session_row = _fake_session_row()
    mock_db = AsyncMock()

    with (
        patch(f"{_REPO}.get_builder_session", new_callable=AsyncMock, return_value=session_row),
        patch(f"{_REPO}.append_builder_message", new_callable=AsyncMock),
        patch(f"{_BUILDER}.build_tool_registry", return_value=_mock_registry()),
    ):
        gen = handle_turn_stream(
            builder_session_id=3,
            user_text="test cancel",
            adapter=adapter,
            db_session=mock_db,
        )

        async def _consume_and_cancel() -> None:
            async for ev in gen:
                if ev.type == "turn_start":
                    break
            await gen.aclose()

        await asyncio.wait_for(_consume_and_cancel(), timeout=5)
    # No crash — reaching here is a pass


@pytest.mark.asyncio
async def test_stream_error_path() -> None:
    """Adapter exception yields an error event and terminates cleanly."""
    from artemis.builder.agent_builder import handle_turn_stream

    adapter = MagicMock()
    adapter.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    session_row = _fake_session_row()
    mock_db = AsyncMock()

    with (
        patch(f"{_REPO}.get_builder_session", new_callable=AsyncMock, return_value=session_row),
        patch(f"{_REPO}.append_builder_message", new_callable=AsyncMock),
        patch(f"{_BUILDER}.build_tool_registry", return_value=_mock_registry()),
    ):
        events = []
        async for ev in handle_turn_stream(
            builder_session_id=4,
            user_text="oops",
            adapter=adapter,
            db_session=mock_db,
        ):
            events.append(ev)

    types = [e.type for e in events]
    assert "error" in types
    err_ev = next(e for e in events if e.type == "error")
    assert "LLM unavailable" in err_ev.payload["message"]


@pytest.mark.asyncio
async def test_handle_turn_wrapper_matches_stream() -> None:
    """handle_turn (sync wrapper) produces same final dict as draining stream."""
    from artemis.builder.agent_builder import handle_turn, handle_turn_stream

    text = "Wrapper test response"

    session_row = _fake_session_row(draft={"name": "my-agent"})
    mock_db = AsyncMock()

    adapter = MagicMock()
    adapter.complete = AsyncMock(return_value=_make_text_response(text))

    # Drain stream manually
    stream_final = None
    with (
        patch(f"{_REPO}.get_builder_session", new_callable=AsyncMock, return_value=session_row),
        patch(f"{_REPO}.append_builder_message", new_callable=AsyncMock),
        patch(f"{_BUILDER}.build_tool_registry", return_value=_mock_registry()),
    ):
        async for ev in handle_turn_stream(
            builder_session_id=5,
            user_text="test wrap",
            adapter=adapter,
            db_session=mock_db,
        ):
            if ev.type == "turn_complete":
                stream_final = ev.payload

    # Via thin wrapper
    adapter2 = MagicMock()
    adapter2.complete = AsyncMock(return_value=_make_text_response(text))
    mock_db2 = AsyncMock()
    session_row2 = _fake_session_row(draft={"name": "my-agent"})

    sync_result = None
    with (
        patch(f"{_REPO}.get_builder_session", new_callable=AsyncMock, return_value=session_row2),
        patch(f"{_REPO}.append_builder_message", new_callable=AsyncMock),
        patch(f"{_BUILDER}.build_tool_registry", return_value=_mock_registry()),
    ):
        sync_result = await handle_turn(
            builder_session_id=6,
            user_text="test wrap",
            adapter=adapter2,
            db_session=mock_db2,
        )

    assert stream_final is not None
    assert sync_result is not None
    assert stream_final["assistant_text"] == sync_result["assistant_text"] == text
    assert stream_final["stop_reason"] == sync_result["stop_reason"] == "end_turn"


def test_heartbeat_event_shape() -> None:
    """Heartbeat BuilderEvent serialises correctly as SSE."""
    from artemis.builder.agent_builder import BuilderEvent

    hb = BuilderEvent(type="heartbeat", payload={"ts": "2026-05-20T00:00:00+00:00"})
    sse = hb.to_sse()
    assert sse.startswith("event: heartbeat\n")
    assert '"ts"' in sse
    assert sse.endswith("\n\n")


def test_sse_response_headers() -> None:
    """send_message_stream source includes required SSE headers."""
    import inspect

    from artemis.builder import routes

    src = inspect.getsource(routes.send_message_stream)
    assert "Cache-Control" in src
    assert "no-cache" in src
    assert "X-Accel-Buffering" in src


def test_builder_event_to_sse_format() -> None:
    """BuilderEvent.to_sse() emits valid SSE wire format."""
    from artemis.builder.agent_builder import BuilderEvent

    ev = BuilderEvent(
        type="tool_call",
        payload={"tool_call_id": "x1", "tool_name": "read_existing", "inputs": {}},
    )
    sse = ev.to_sse()
    lines = sse.split("\n")
    assert lines[0] == "event: tool_call"
    assert lines[1].startswith("data: ")
    parsed = json.loads(lines[1][6:])
    assert parsed["tool_name"] == "read_existing"
    assert sse.endswith("\n\n")
