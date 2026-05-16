"""Tests for WSEvent Pydantic schemas and event builder helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from artemis.ws.events import (
    WSEvent,
    agent_completed_event,
    agent_failed_event,
    agent_message_event,
    agent_started_event,
    tool_completed_event,
    tool_started_event,
    workflow_completed_event,
    workflow_failed_event,
    workflow_started_event,
    workflow_step_completed_event,
)

# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------


def test_wsevent_round_trip() -> None:
    """WSEvent can be constructed and serialised without loss."""
    now = datetime.now(UTC)
    evt = WSEvent(
        type="agent_run.started",
        run_id="run-abc",
        timestamp=now,
        payload={"agent_id": "my-agent"},
    )
    d = evt.to_dict()
    assert d["type"] == "agent_run.started"
    assert d["run_id"] == "run-abc"
    assert d["payload"]["agent_id"] == "my-agent"
    # timestamp should be an ISO string in JSON mode
    assert isinstance(d["timestamp"], str)


def test_wsevent_default_timestamp() -> None:
    """WSEvent populates timestamp automatically if not provided."""
    evt = WSEvent(type="agent_run.completed", run_id="r1", payload={})
    assert evt.timestamp is not None


def test_wsevent_rejects_unknown_type() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WSEvent.model_validate({"type": "bogus.event", "run_id": "r", "payload": {}})


def test_wsevent_empty_payload_ok() -> None:
    evt = WSEvent(type="agent_run.failed", run_id="r1")
    assert evt.payload == {}


# ---------------------------------------------------------------------------
# Agent run builders
# ---------------------------------------------------------------------------


def test_agent_started_event() -> None:
    evt = agent_started_event("run-1", "my-agent", "hello")
    assert evt.type == "agent_run.started"
    assert evt.run_id == "run-1"
    assert evt.payload["agent_id"] == "my-agent"
    assert evt.payload["user_message"] == "hello"


def test_agent_message_event() -> None:
    content = [{"type": "text", "text": "hi"}]
    evt = agent_message_event("run-1", "assistant", content)
    assert evt.type == "agent_run.message"
    assert evt.payload["role"] == "assistant"
    assert evt.payload["content"] == content


def test_tool_started_event() -> None:
    evt = tool_started_event("run-1", "bash", {"cmd": "ls"}, "tu-1")
    assert evt.type == "agent_run.tool_started"
    assert evt.payload["name"] == "bash"
    assert evt.payload["input"] == {"cmd": "ls"}
    assert evt.payload["tool_use_id"] == "tu-1"


def test_tool_completed_event() -> None:
    evt = tool_completed_event("run-1", "bash", {"cmd": "ls"}, "tu-1", "file.txt\n", False, 42)
    assert evt.type == "agent_run.tool_completed"
    assert evt.payload["result"] == "file.txt\n"
    assert evt.payload["is_error"] is False
    assert evt.payload["elapsed_ms"] == 42


def test_agent_completed_event() -> None:
    evt = agent_completed_event("run-1", "end_turn", 100, 50)
    assert evt.type == "agent_run.completed"
    assert evt.payload["stop_reason"] == "end_turn"
    assert evt.payload["input_tokens"] == 100
    assert evt.payload["output_tokens"] == 50


def test_agent_failed_event() -> None:
    evt = agent_failed_event("run-1", "RuntimeError: boom")
    assert evt.type == "agent_run.failed"
    assert "RuntimeError" in evt.payload["error"]


# ---------------------------------------------------------------------------
# Workflow run builders
# ---------------------------------------------------------------------------


def test_workflow_started_event() -> None:
    evt = workflow_started_event("wf-run-1", "my-workflow")
    assert evt.type == "workflow_run.started"
    assert evt.payload["workflow_id"] == "my-workflow"


def test_workflow_step_completed_event() -> None:
    evt = workflow_step_completed_event("wf-run-1", 0, 3, "step output")
    assert evt.type == "workflow_run.step_completed"
    assert evt.payload["step_index"] == 0
    assert evt.payload["step_count"] == 3
    assert evt.payload["response_text"] == "step output"


def test_workflow_completed_event() -> None:
    evt = workflow_completed_event("wf-run-1", 0.0025)
    assert evt.type == "workflow_run.completed"
    assert evt.payload["total_cost_usd"] == pytest.approx(0.0025)


def test_workflow_failed_event() -> None:
    evt = workflow_failed_event("wf-run-1", 2, "ValueError: bad input")
    assert evt.type == "workflow_run.failed"
    assert evt.payload["step_index"] == 2
    assert "ValueError" in evt.payload["error"]


# ---------------------------------------------------------------------------
# to_dict serialisation
# ---------------------------------------------------------------------------


def test_to_dict_is_json_serialisable() -> None:
    """to_dict output must be JSON-serialisable (no datetime objects)."""
    import json

    evt = agent_started_event("r1", "a1", "hello")
    d = evt.to_dict()
    # Should not raise
    json.dumps(d)


def test_to_dict_payload_preserved() -> None:
    evt = tool_completed_event("r1", "bash", {"x": 1}, "tu", "out", True, 99)
    d = evt.to_dict()
    assert d["payload"]["is_error"] is True
    assert d["payload"]["elapsed_ms"] == 99
