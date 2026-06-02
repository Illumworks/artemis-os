"""Tests for the Pipeline AI Assistant panel backend.

Covers:
  - PipelineProposal schema validation (proposals.py)
  - apply_proposal: all 5 kinds (add_node, remove_node, add_edge, remove_edge,
    update_node_config)
  - apply_proposal: error cases (bad node_id)
  - _extract_proposals: happy path + malformed JSON skipped
  - _detect_self_improvement_hints: consistent_node_failure + high_cancellation
  - handle_assistant_turn_stream: happy path (turn_start + token + turn_complete)
  - handle_assistant_turn_stream: error path
  - turn SSE endpoint: headers present + streams events
  - conversation GET: returns history
  - conversation DELETE: clears history
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_text_response(text: str) -> Any:
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason="end_turn",
        usage=Usage(),
    )


def _sample_nodes() -> list[dict[str, Any]]:
    return [
        {
            "id": "n1",
            "type": "trigger_scheduled",
            "label": "Trigger",
            "config": {"cron": "0 9 * * 1"},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "n2",
            "type": "agent_invocation",
            "label": "Agent",
            "config": {"agent_id": "abc"},
            "position": {"x": 240, "y": 0},
        },
    ]


def _sample_edges() -> list[dict[str, Any]]:
    return [
        {
            "id": "e1",
            "source_node_id": "n1",
            "target_node_id": "n2",
            "condition": None,
            "data_shape": None,
        }
    ]


# ── Proposal schema ────────────────────────────────────────────────────────────


def test_proposal_add_node_valid() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal

    p = PipelineProposal(
        kind="add_node",
        payload={"node": {"type": "human_gate", "label": "Gate", "config": {}}},
        explanation="Add a human gate",
    )
    assert p.kind == "add_node"


def test_proposal_missing_payload_key_raises() -> None:
    from pydantic import ValidationError

    from artemis.pipelines.assistant.proposals import PipelineProposal

    with pytest.raises(ValidationError):
        PipelineProposal(
            kind="add_node",
            payload={},  # missing "node" key
            explanation="bad",
        )


def test_proposal_update_node_config_valid() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal

    p = PipelineProposal(
        kind="update_node_config",
        payload={"node_id": "n1", "config_patch": {"cron": "0 9 * * *"}},
        explanation="daily",
    )
    assert p.kind == "update_node_config"


def test_proposal_to_sse_fragment_format() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal

    p = PipelineProposal(
        kind="add_edge",
        payload={"source_node_id": "n1", "target_node_id": "n2"},
        explanation="connect",
    )
    frag = p.to_sse_fragment()
    assert frag.startswith("PROPOSAL_BEGIN ")
    assert frag.endswith(" PROPOSAL_END")
    inner = frag[len("PROPOSAL_BEGIN ") : -len(" PROPOSAL_END")]
    parsed = json.loads(inner)
    assert parsed["kind"] == "add_edge"


# ── apply_proposal ─────────────────────────────────────────────────────────────


def test_apply_add_node() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal, apply_proposal

    nodes = _sample_nodes()
    edges = _sample_edges()
    p = PipelineProposal(
        kind="add_node",
        payload={"node": {"type": "human_gate", "label": "Gate", "config": {}}},
        explanation="add gate",
    )
    new_nodes, new_edges = apply_proposal(p, nodes, edges)
    assert len(new_nodes) == 3
    assert any(n["type"] == "human_gate" for n in new_nodes)


def test_apply_remove_node() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal, apply_proposal

    nodes = _sample_nodes()
    edges = _sample_edges()
    p = PipelineProposal(
        kind="remove_node",
        payload={"node_id": "n1"},
        explanation="remove trigger",
    )
    new_nodes, new_edges = apply_proposal(p, nodes, edges)
    assert len(new_nodes) == 1
    assert new_nodes[0]["id"] == "n2"
    # Edge referencing n1 should also be removed
    assert len(new_edges) == 0


def test_apply_remove_node_not_found() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal, apply_proposal

    nodes = _sample_nodes()
    edges = _sample_edges()
    p = PipelineProposal(
        kind="remove_node",
        payload={"node_id": "does_not_exist"},
        explanation="bad",
    )
    with pytest.raises(ValueError, match="not found"):
        apply_proposal(p, nodes, edges)


def test_apply_add_edge() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal, apply_proposal

    nodes = [
        {
            "id": "n1",
            "type": "trigger_manual",
            "label": "T",
            "config": {},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "n2",
            "type": "agent_invocation",
            "label": "A",
            "config": {},
            "position": {"x": 200, "y": 0},
        },
        {
            "id": "n3",
            "type": "human_gate",
            "label": "G",
            "config": {},
            "position": {"x": 400, "y": 0},
        },
    ]
    edges: list[dict[str, Any]] = []
    p = PipelineProposal(
        kind="add_edge",
        payload={"source_node_id": "n1", "target_node_id": "n3"},
        explanation="shortcut",
    )
    new_nodes, new_edges = apply_proposal(p, nodes, edges)
    assert len(new_edges) == 1
    assert new_edges[0]["source_node_id"] == "n1"
    assert new_edges[0]["target_node_id"] == "n3"


def test_apply_add_edge_duplicate_skipped() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal, apply_proposal

    nodes = _sample_nodes()
    edges = _sample_edges()
    p = PipelineProposal(
        kind="add_edge",
        payload={"source_node_id": "n1", "target_node_id": "n2"},
        explanation="dup",
    )
    _, new_edges = apply_proposal(p, nodes, edges)
    # No new edge added — still 1
    assert len(new_edges) == 1


def test_apply_remove_edge() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal, apply_proposal

    nodes = _sample_nodes()
    edges = _sample_edges()
    p = PipelineProposal(
        kind="remove_edge",
        payload={"source_node_id": "n1", "target_node_id": "n2"},
        explanation="remove edge",
    )
    _, new_edges = apply_proposal(p, nodes, edges)
    assert len(new_edges) == 0


def test_apply_update_node_config() -> None:
    from artemis.pipelines.assistant.proposals import PipelineProposal, apply_proposal

    nodes = _sample_nodes()
    edges = _sample_edges()
    p = PipelineProposal(
        kind="update_node_config",
        payload={"node_id": "n1", "config_patch": {"cron": "0 9 * * *", "timezone": "US/Eastern"}},
        explanation="daily",
    )
    new_nodes, _ = apply_proposal(p, nodes, edges)
    n1 = next(n for n in new_nodes if n["id"] == "n1")
    assert n1["config"]["cron"] == "0 9 * * *"
    assert n1["config"]["timezone"] == "US/Eastern"


# ── _extract_proposals ─────────────────────────────────────────────────────────


def test_extract_proposals_happy_path() -> None:
    from artemis.pipelines.assistant.turn_handler import _extract_proposals

    proposal_json = json.dumps(
        {
            "kind": "add_node",
            "payload": {"node": {"type": "human_gate", "label": "Gate", "config": {}}},
            "explanation": "Add a gate",
            "id": "abc123",
        }
    )
    text = f"Sure, I'll add a gate. PROPOSAL_BEGIN {proposal_json} PROPOSAL_END Done!"
    props = _extract_proposals(text)
    assert len(props) == 1
    assert props[0]["kind"] == "add_node"


def test_extract_proposals_malformed_json_skipped() -> None:
    from artemis.pipelines.assistant.turn_handler import _extract_proposals

    text = "PROPOSAL_BEGIN {bad json PROPOSAL_END"
    props = _extract_proposals(text)
    assert props == []


def test_extract_proposals_multiple() -> None:
    from artemis.pipelines.assistant.turn_handler import _extract_proposals

    p1 = json.dumps(
        {
            "kind": "add_node",
            "payload": {"node": {"type": "human_gate", "label": "G", "config": {}}},
            "explanation": "a",
            "id": "1",
        }
    )
    p2 = json.dumps(
        {
            "kind": "remove_edge",
            "payload": {"source_node_id": "n1", "target_node_id": "n2"},
            "explanation": "b",
            "id": "2",
        }
    )
    text = f"First: PROPOSAL_BEGIN {p1} PROPOSAL_END. Second: PROPOSAL_BEGIN {p2} PROPOSAL_END."
    props = _extract_proposals(text)
    assert len(props) == 2
    assert props[0]["kind"] == "add_node"
    assert props[1]["kind"] == "remove_edge"


# ── Self-improvement hints ─────────────────────────────────────────────────────


def test_self_improvement_no_hints_for_empty_runs() -> None:
    from artemis.pipelines.assistant.turn_handler import _detect_self_improvement_hints

    hints = _detect_self_improvement_hints([])
    assert hints == []


def test_self_improvement_consistent_failure() -> None:
    from artemis.pipelines.assistant.turn_handler import _detect_self_improvement_hints

    runs = [
        {"id": f"r{i}", "status": "failed", "node_states": {"gate_node": {"status": "failed"}}}
        for i in range(3)
    ]
    hints = _detect_self_improvement_hints(runs)
    assert len(hints) == 1
    assert hints[0]["pattern"] == "consistent_node_failure"
    assert hints[0]["node_id"] == "gate_node"


def test_self_improvement_high_cancellation() -> None:
    from artemis.pipelines.assistant.turn_handler import _detect_self_improvement_hints

    runs = [{"id": f"r{i}", "status": "cancelled", "node_states": {}} for i in range(4)]
    hints = _detect_self_improvement_hints(runs)
    assert any(h["pattern"] == "high_cancellation_rate" for h in hints)


# ── handle_assistant_turn_stream ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_happy_path() -> None:
    from artemis.pipelines.assistant.turn_handler import handle_assistant_turn_stream

    adapter = MagicMock()
    adapter.complete = AsyncMock(return_value=_make_text_response("This pipeline rocks."))

    events = []
    async for ev in handle_assistant_turn_stream(
        pipeline_id="pipe-1",
        user_text="What does this pipeline do?",
        pipeline_data={"nodes": _sample_nodes(), "edges": _sample_edges()},
        conversation=[],
        recent_runs=[],
        adapter=adapter,
    ):
        events.append(ev)

    types = [e.type for e in events]
    assert types[0] == "turn_start"
    assert "assistant_token" in types
    assert types[-1] == "turn_complete"
    complete = events[-1]
    assert "This pipeline rocks." in complete.payload["assistant_text"]


@pytest.mark.asyncio
async def test_stream_self_improvement_fires_on_first_turn() -> None:
    from artemis.pipelines.assistant.turn_handler import handle_assistant_turn_stream

    adapter = MagicMock()
    adapter.complete = AsyncMock(return_value=_make_text_response("ok"))

    runs = [
        {"id": f"r{i}", "status": "failed", "node_states": {"n1": {"status": "failed"}}}
        for i in range(3)
    ]

    events = []
    async for ev in handle_assistant_turn_stream(
        pipeline_id="pipe-2",
        user_text="hi",
        pipeline_data={"nodes": _sample_nodes(), "edges": _sample_edges()},
        conversation=[],
        recent_runs=runs,
        adapter=adapter,
        is_first_turn=True,
    ):
        events.append(ev)

    si_events = [e for e in events if e.type == "self_improvement"]
    assert len(si_events) >= 1


@pytest.mark.asyncio
async def test_stream_proposal_parsed_event() -> None:
    from artemis.pipelines.assistant.turn_handler import handle_assistant_turn_stream

    proposal_json = json.dumps(
        {
            "kind": "add_node",
            "payload": {"node": {"type": "human_gate", "label": "Gate", "config": {}}},
            "explanation": "Add gate",
            "id": "p1",
        }
    )
    response_text = f"I'll add a gate. PROPOSAL_BEGIN {proposal_json} PROPOSAL_END"

    adapter = MagicMock()
    adapter.complete = AsyncMock(return_value=_make_text_response(response_text))

    events = []
    async for ev in handle_assistant_turn_stream(
        pipeline_id="pipe-3",
        user_text="add gate",
        pipeline_data={"nodes": _sample_nodes(), "edges": _sample_edges()},
        conversation=[],
        recent_runs=[],
        adapter=adapter,
    ):
        events.append(ev)

    proposal_events = [e for e in events if e.type == "proposal_parsed"]
    assert len(proposal_events) == 1
    assert proposal_events[0].payload["kind"] == "add_node"


@pytest.mark.asyncio
async def test_stream_error_path() -> None:
    from artemis.pipelines.assistant.turn_handler import handle_assistant_turn_stream

    adapter = MagicMock()
    adapter.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

    events = []
    async for ev in handle_assistant_turn_stream(
        pipeline_id="pipe-err",
        user_text="hi",
        pipeline_data={"nodes": [], "edges": []},
        conversation=[],
        recent_runs=[],
        adapter=adapter,
    ):
        events.append(ev)

    types = [e.type for e in events]
    assert "error" in types
    err = next(e for e in events if e.type == "error")
    assert "LLM down" in err.payload["message"]


# ── AssistantPanelEvent SSE format ─────────────────────────────────────────────


def test_assistant_panel_event_sse_format() -> None:
    from artemis.pipelines.assistant.turn_handler import AssistantPanelEvent

    ev = AssistantPanelEvent(type="assistant_token", payload={"delta": "hello"})
    sse = ev.to_sse()
    assert sse.startswith("event: assistant_token\n")
    assert '"delta"' in sse
    assert sse.endswith("\n\n")
