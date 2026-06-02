"""Pipeline AI Assistant — Proposal schema + apply logic.

A Proposal describes a single structural change to a pipeline that the AI
has suggested. Proposals are delivered in the SSE stream as PROPOSAL_BEGIN
... PROPOSAL_END tokens so the frontend can parse them independently of the
surrounding prose.

Proposal kinds (v1):
  add_node          — add a new node at a suggested position
  remove_node       — remove an existing node (and its edges)
  add_edge          — add a directed edge between two existing nodes
  remove_edge       — remove an edge by source+target pair
  update_node_config — patch specific keys in a node's config dict

See also: artemis/builder/agent_builder.py — parallel O1 proposal pattern.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ── Proposal kinds ──────────────────────────────────────────────────────────────

ProposalKind = Literal[
    "add_node",
    "remove_node",
    "add_edge",
    "remove_edge",
    "update_node_config",
]


class PipelineProposal(BaseModel):
    """A single AI-suggested change to a pipeline's nodes/edges/configs.

    Delivered embedded in SSE stream as:
      PROPOSAL_BEGIN <json> PROPOSAL_END

    Frontend parses this out, renders ghost state, and offers Accept/Reject.
    On Accept the frontend calls PATCH /api/pipelines/{id} with the updated
    nodes/edges.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    kind: ProposalKind
    payload: dict[str, Any]
    explanation: str

    @model_validator(mode="after")
    def _check_payload_keys(self) -> PipelineProposal:
        required: dict[ProposalKind, list[str]] = {
            "add_node": ["node"],
            "remove_node": ["node_id"],
            "add_edge": ["source_node_id", "target_node_id"],
            "remove_edge": ["source_node_id", "target_node_id"],
            "update_node_config": ["node_id", "config_patch"],
        }
        missing = [k for k in required[self.kind] if k not in self.payload]
        if missing:
            raise ValueError(f"Proposal kind={self.kind!r} missing payload keys: {missing}")
        return self

    def to_sse_fragment(self) -> str:
        """Wrap the proposal JSON in sentinel tokens for frontend parsing."""
        import json

        return f"PROPOSAL_BEGIN {json.dumps(self.model_dump())} PROPOSAL_END"


# ── Apply logic ────────────────────────────────────────────────────────────────


def apply_proposal(
    proposal: PipelineProposal,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply a proposal to nodes+edges and return the updated lists.

    This is the same transformation the frontend applies on Accept. Having it
    server-side lets the backend PATCH endpoint do the authoritative write when
    the frontend posts an accept action.

    Raises ValueError if the proposal refers to nodes/edges that don't exist.
    """
    nodes = [dict(n) for n in nodes]
    edges = [dict(e) for e in edges]
    node_index = {n["id"]: i for i, n in enumerate(nodes)}

    if proposal.kind == "add_node":
        new_node = dict(proposal.payload["node"])
        if "id" not in new_node:
            new_node["id"] = f"node_{uuid.uuid4().hex[:8]}"
        if "position" not in new_node:
            new_node["position"] = {"x": 400, "y": 200}
        nodes.append(new_node)

    elif proposal.kind == "remove_node":
        nid = proposal.payload["node_id"]
        if nid not in node_index:
            raise ValueError(f"Node '{nid}' not found — cannot remove")
        nodes = [n for n in nodes if n["id"] != nid]
        edges = [
            e for e in edges if e.get("source_node_id") != nid and e.get("target_node_id") != nid
        ]

    elif proposal.kind == "add_edge":
        src = proposal.payload["source_node_id"]
        tgt = proposal.payload["target_node_id"]
        if src not in node_index:
            raise ValueError(f"Source node '{src}' not found — cannot add edge")
        if tgt not in node_index:
            raise ValueError(f"Target node '{tgt}' not found — cannot add edge")
        # Avoid duplicates
        dup = any(e.get("source_node_id") == src and e.get("target_node_id") == tgt for e in edges)
        if not dup:
            edges.append(
                {
                    "id": f"edge_{uuid.uuid4().hex[:8]}",
                    "source_node_id": src,
                    "target_node_id": tgt,
                    "condition": proposal.payload.get("condition"),
                    "data_shape": None,
                }
            )

    elif proposal.kind == "remove_edge":
        src = proposal.payload["source_node_id"]
        tgt = proposal.payload["target_node_id"]
        edges = [
            e
            for e in edges
            if not (e.get("source_node_id") == src and e.get("target_node_id") == tgt)
        ]

    elif proposal.kind == "update_node_config":
        nid = proposal.payload["node_id"]
        if nid not in node_index:
            raise ValueError(f"Node '{nid}' not found — cannot update config")
        idx = node_index[nid]
        config = dict(nodes[idx].get("config") or {})
        config.update(proposal.payload["config_patch"])
        nodes[idx] = {**nodes[idx], "config": config}

    return nodes, edges
