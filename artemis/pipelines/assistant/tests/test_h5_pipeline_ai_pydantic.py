"""H5 — Pipeline AI Panel Pydantic + proposal validation tests.

Test plan:
8.  Valid proposal passes Pydantic.
9.  Invalid node_mods.action rejected.
10. Malformed JSON in proposal block triggers strip + warning log.
11. No PROPOSAL block in response → text returned as-is.
"""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from artemis.pipelines.assistant.schemas import (
    PipelineEdgeMod,
    PipelineNodeMod,
    PipelineProposal,
)
from artemis.pipelines.assistant.turn_handler import _extract_and_validate_proposal

# ── Fixture payloads ──────────────────────────────────────────────────────────

_VALID_PROPOSAL_DICT = {
    "summary": "Add error-handling node after agent_invocation_1",
    "node_mods": [
        {
            "action": "add",
            "node_id": "error_handler_1",
            "node_type": "conditional",
            "config": {"condition": "status == 'failed'"},
        }
    ],
    "edge_mods": [
        {
            "action": "add",
            "from_node": "agent_invocation_1",
            "to_node": "error_handler_1",
            "condition": "on_error",
        }
    ],
    "rationale": "Node 'agent_invocation_1' has failed in all recent runs.",
    "confidence": "high",
}


# ── Test 8: Valid proposal passes Pydantic ────────────────────────────────────


def test_valid_proposal_passes_pydantic() -> None:
    """PipelineProposal.model_validate accepts a canonical valid proposal."""
    proposal = PipelineProposal.model_validate(_VALID_PROPOSAL_DICT)
    assert proposal.summary == "Add error-handling node after agent_invocation_1"
    assert len(proposal.node_mods) == 1
    assert proposal.node_mods[0].action == "add"
    assert proposal.node_mods[0].node_id == "error_handler_1"
    assert len(proposal.edge_mods) == 1
    assert proposal.edge_mods[0].from_node == "agent_invocation_1"
    assert proposal.confidence == "high"


def test_valid_proposal_passes_model_validate_json() -> None:
    """PipelineProposal.model_validate_json also accepts a canonical proposal."""
    raw = json.dumps(_VALID_PROPOSAL_DICT)
    proposal = PipelineProposal.model_validate_json(raw)
    assert proposal.rationale is not None
    assert "agent_invocation_1" in proposal.rationale


def test_valid_proposal_empty_mods() -> None:
    """PipelineProposal allows empty node_mods and edge_mods lists."""
    proposal = PipelineProposal(summary="Just a conversational hint")
    assert proposal.node_mods == []
    assert proposal.edge_mods == []
    assert proposal.confidence == "medium"  # default


# ── Test 9: Invalid node_mods.action rejected ─────────────────────────────────


def test_invalid_node_mod_action_rejected() -> None:
    """PipelineNodeMod rejects action values not in Literal['add', 'update', 'remove']."""
    with pytest.raises(ValidationError) as exc_info:
        PipelineNodeMod(
            action="extreme",
            node_id="node_1",
        )
    assert "action" in str(exc_info.value).lower() or "extreme" in str(exc_info.value)


def test_invalid_edge_mod_action_rejected() -> None:
    """PipelineEdgeMod rejects action values not in Literal['add', 'remove']."""
    with pytest.raises(ValidationError) as exc_info:
        PipelineEdgeMod(
            action="update",
            from_node="a",
            to_node="b",
        )
    assert "action" in str(exc_info.value).lower() or "update" in str(exc_info.value)


def test_extra_field_rejected_on_proposal() -> None:
    """PipelineProposal rejects extra fields (extra='forbid')."""
    with pytest.raises(ValidationError) as exc_info:
        PipelineProposal.model_validate(
            {**_VALID_PROPOSAL_DICT, "hallucinated_field": "sneaky extra"}
        )
    assert (
        "hallucinated_field" in str(exc_info.value).lower()
        or "extra" in str(exc_info.value).lower()
    )


def test_empty_summary_rejected() -> None:
    """PipelineProposal rejects empty summary (min_length=1)."""
    with pytest.raises(ValidationError):
        PipelineProposal(summary="")


# ── Test 10: Malformed JSON triggers strip + log ──────────────────────────────


def test_malformed_proposal_block_stripped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed JSON in PROPOSAL_BEGIN...PROPOSAL_END is stripped from text.

    The conversational text is returned without the block.  A warning is logged.
    No exception bubbles to the caller.
    """
    assistant_text = (
        "Here is my analysis of the pipeline.\n"
        "PROPOSAL_BEGIN\n"
        '{"action": "extreme", "hallucinated_key": true, "no_summary": true}\n'
        "PROPOSAL_END\n"
        "Let me know if you want more details."
    )

    with caplog.at_level(logging.WARNING, logger="artemis.pipelines.assistant.turn_handler"):
        cleaned_text, proposal = _extract_and_validate_proposal(assistant_text)

    # Proposal is None — validation failed
    assert proposal is None

    # PROPOSAL block stripped from text
    assert "PROPOSAL_BEGIN" not in cleaned_text
    assert "PROPOSAL_END" not in cleaned_text

    # Conversational text preserved
    assert "Here is my analysis of the pipeline." in cleaned_text
    assert "Let me know if you want more details." in cleaned_text

    # Warning was logged
    assert any(
        "validation" in rec.message.lower() or "proposal" in rec.message.lower()
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
    ), f"Expected warning log, got: {[r.message for r in caplog.records]}"


def test_malformed_json_syntax_stripped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """JSON syntax error in PROPOSAL_BEGIN...PROPOSAL_END is also handled."""
    assistant_text = (
        "Some conversational text.\n"
        "PROPOSAL_BEGIN\n"
        "{not valid json at all!!!\n"
        "PROPOSAL_END\n"
        "End of response."
    )

    with caplog.at_level(logging.WARNING, logger="artemis.pipelines.assistant.turn_handler"):
        cleaned_text, proposal = _extract_and_validate_proposal(assistant_text)

    assert proposal is None
    assert "PROPOSAL_BEGIN" not in cleaned_text
    assert "Some conversational text." in cleaned_text
    assert "End of response." in cleaned_text


# ── Test 11: No PROPOSAL block → text returned as-is ─────────────────────────


def test_no_proposal_block_text_returned_as_is() -> None:
    """When no PROPOSAL_BEGIN...PROPOSAL_END block exists, text is returned unchanged."""
    assistant_text = (
        "This is a purely conversational response explaining the pipeline. "
        "No structural changes are needed at this time."
    )

    cleaned_text, proposal = _extract_and_validate_proposal(assistant_text)

    assert proposal is None
    assert cleaned_text == assistant_text  # unchanged


def test_no_proposal_block_empty_text() -> None:
    """Empty assistant text returns empty text and None proposal."""
    cleaned_text, proposal = _extract_and_validate_proposal("")
    assert proposal is None
    assert cleaned_text == ""


# ── Test 12: Valid proposal block extracted successfully ──────────────────────


def test_valid_proposal_block_extracted() -> None:
    """Valid PROPOSAL_BEGIN...PROPOSAL_END block is extracted and returned as dict."""
    proposal_json = json.dumps(_VALID_PROPOSAL_DICT)
    assistant_text = (
        "I've analyzed the pipeline runs.\n"
        f"PROPOSAL_BEGIN\n{proposal_json}\nPROPOSAL_END\n"
        "Please review and accept if this looks right."
    )

    cleaned_text, proposal = _extract_and_validate_proposal(assistant_text)

    assert proposal is not None
    assert proposal["summary"] == "Add error-handling node after agent_invocation_1"
    assert len(proposal["node_mods"]) == 1
    assert proposal["confidence"] == "high"

    # Proposal block stripped from text
    assert "PROPOSAL_BEGIN" not in cleaned_text
    assert "PROPOSAL_END" not in cleaned_text
    assert "I've analyzed the pipeline runs." in cleaned_text
