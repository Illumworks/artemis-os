"""Validation tests for Pipelines (PIPE1).

Tests:
- Invalid edges rejected (source/target not in nodes)
- Duplicate node IDs rejected
- Invalid node types rejected
- Invalid status enum rejected
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from artemis.pipelines.schemas import PipelineCreate, PipelineUpdate

_VALID_NODE = {
    "id": "n1",
    "type": "agent_invocation",
    "label": "Node 1",
    "config": {},
    "position": {"x": 0.0, "y": 0.0},
}

_VALID_NODE_2 = {
    "id": "n2",
    "type": "human_gate",
    "label": "Gate",
    "config": {},
    "position": {"x": 100.0, "y": 0.0},
}

_VALID_EDGE = {
    "id": "e1",
    "source_node_id": "n1",
    "target_node_id": "n2",
    "condition": None,
    "data_shape": None,
}


def test_valid_pipeline_create_passes() -> None:
    p = PipelineCreate(
        name="Valid Pipeline",
        nodes=[_VALID_NODE, _VALID_NODE_2],
        edges=[_VALID_EDGE],
    )
    assert p.name == "Valid Pipeline"
    assert len(p.nodes) == 2


def test_edge_with_missing_source_rejected() -> None:
    with pytest.raises(ValidationError, match="source_node_id"):
        PipelineCreate(
            name="Bad Edge",
            nodes=[_VALID_NODE],
            edges=[{"id": "e1", "source_node_id": "missing", "target_node_id": "n1"}],
        )


def test_edge_with_missing_target_rejected() -> None:
    with pytest.raises(ValidationError, match="target_node_id"):
        PipelineCreate(
            name="Bad Edge Target",
            nodes=[_VALID_NODE],
            edges=[{"id": "e1", "source_node_id": "n1", "target_node_id": "does-not-exist"}],
        )


def test_duplicate_node_ids_rejected() -> None:
    dup_node = dict(_VALID_NODE)  # same id "n1"
    with pytest.raises(ValidationError, match="Duplicate node id"):
        PipelineCreate(
            name="Dup Nodes",
            nodes=[_VALID_NODE, dup_node],
            edges=[],
        )


def test_invalid_node_type_rejected() -> None:
    bad_node = {**_VALID_NODE, "type": "not_a_real_type"}
    with pytest.raises(ValidationError, match="Invalid node type"):
        PipelineCreate(
            name="Bad Type",
            nodes=[bad_node],
            edges=[],
        )


def test_invalid_status_rejected() -> None:
    with pytest.raises(ValidationError, match="status must be one of"):
        PipelineCreate(
            name="Bad Status",
            nodes=[],
            edges=[],
            status="running",
        )


def test_pipeline_update_invalid_status_rejected() -> None:
    with pytest.raises(ValidationError, match="status must be one of"):
        PipelineUpdate(status="deleting")


def test_empty_nodes_and_edges_valid() -> None:
    p = PipelineCreate(name="Empty", nodes=[], edges=[])
    assert p.nodes == []
    assert p.edges == []


def test_all_node_types_valid() -> None:
    """Verify all declared node types pass validation."""
    valid_types = [
        "agent_invocation",
        "skill_call",
        "trigger_manual",
        "trigger_scheduled",
        "trigger_webhook",
        "trigger_event",
        "human_gate",
        "conditional",
        "sub_pipeline",
    ]
    nodes = [
        {"id": f"n{i}", "type": t, "label": t, "config": {}, "position": {"x": float(i), "y": 0.0}}
        for i, t in enumerate(valid_types)
    ]
    p = PipelineCreate(name="All Types", nodes=nodes, edges=[])
    assert len(p.nodes) == len(valid_types)
