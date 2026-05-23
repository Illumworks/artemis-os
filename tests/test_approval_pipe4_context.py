"""Unit tests for approval PIPE4 context — Lane B of PIPE4-aftermath briefs.

Tests:
1. _build_pipe4_context: builds correct context from node_states with signals
2. _build_pipe4_context: builds empty context when no signals in node_states
3. _build_pipe4_context: extracts brief_preview from node_states brief_data
4. _build_pipe4_context: extracts draft_summary from node_states draft_data
5. Approvals route _serialize exposes pipe4Context field when populated
6. Approvals route _serialize returns pipe4Context=None for non-PIPE4 approvals

All tests are pure unit tests — no database required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

# ── Helper ────────────────────────────────────────────────────────────────────


def _make_approval_mock(**kwargs: Any) -> MagicMock:
    """Build a minimal Approval mock with sensible defaults."""
    a = MagicMock()
    a.id = kwargs.get("id", 1)
    a.kind = kwargs.get("kind", "signal_brief")
    a.subject_id = kwargs.get("subject_id", "run-abc:node-gate-1")
    a.status = kwargs.get("status", "pending")
    a.decided_by = kwargs.get("decided_by")
    a.decided_at = kwargs.get("decided_at")
    a.decision_payload = kwargs.get("decision_payload")
    a.pipe4_context = kwargs.get("pipe4_context")
    a.created_at = kwargs.get("created_at", datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC))
    return a


# ── 1. _build_pipe4_context: populates signal fields ─────────────────────────


def test_build_pipe4_context_with_signals() -> None:
    """Qualified signals populate signal_count, reason_codes, districts, evidence."""
    from artemis.pipelines.node_executors.human_gate_executor import _build_pipe4_context

    node_states: dict[str, Any] = {
        "qualifier-node": {
            "status": "succeeded",
            "qualified_signals": [
                {
                    "reason_codes": [{"code": "HIGH_ENGAGEMENT"}, {"code": "POLICY_SIGNAL"}],
                    "geography": {"district": "Michigan-17"},
                    "source": {"verbatim_snippet": "Test evidence from Michigan district."},
                },
                {
                    "reason_codes": [{"code": "HIGH_ENGAGEMENT"}],
                    "geography": {"district": "Florida-3"},
                    "source": {},
                },
            ],
        }
    }

    ctx = _build_pipe4_context("signal_brief", node_states)

    assert ctx["signal_count"] == 2
    assert "HIGH_ENGAGEMENT" in ctx["reason_codes"]
    assert "POLICY_SIGNAL" in ctx["reason_codes"]
    assert "Michigan-17" in ctx["districts"]
    assert "Florida-3" in ctx["districts"]
    assert ctx["evidence_quote"] is not None
    assert "Michigan" in ctx["evidence_quote"]
    assert ctx["approval_kind"] == "signal_brief"


# ── 2. _build_pipe4_context: empty context when no signals ────────────────────


def test_build_pipe4_context_empty_when_no_signals() -> None:
    """No qualified_signals in node_states → zero counts, all fields None."""
    from artemis.pipelines.node_executors.human_gate_executor import _build_pipe4_context

    node_states: dict[str, Any] = {
        "some-other-node": {"status": "succeeded", "output_summary": "done"},
    }

    ctx = _build_pipe4_context("signal_brief", node_states)

    assert ctx["signal_count"] == 0
    assert ctx["reason_codes"] == []
    assert ctx["districts"] == []
    assert ctx["evidence_quote"] is None
    assert ctx["brief_preview"] is None
    assert ctx["draft_summary"] is None


# ── 3. _build_pipe4_context: extracts brief_preview ──────────────────────────


def test_build_pipe4_context_extracts_brief_preview() -> None:
    """brief_data.preview in node_state is captured as brief_preview."""
    from artemis.pipelines.node_executors.human_gate_executor import _build_pipe4_context

    node_states: dict[str, Any] = {
        "brief-composer": {
            "status": "succeeded",
            "brief_data": {"preview": "This is the brief preview text.", "full": "..."},
        },
    }

    ctx = _build_pipe4_context("signal_brief", node_states)

    assert ctx["brief_preview"] == "This is the brief preview text."


# ── 4. _build_pipe4_context: extracts draft_summary ──────────────────────────


def test_build_pipe4_context_extracts_draft_summary() -> None:
    """draft_data.summary in node_state is captured as draft_summary."""
    from artemis.pipelines.node_executors.human_gate_executor import _build_pipe4_context

    node_states: dict[str, Any] = {
        "writing-node": {
            "status": "succeeded",
            "draft_data": {"summary": "Draft summary here.", "content": "..."},
        },
    }

    ctx = _build_pipe4_context("content_draft", node_states)

    assert ctx["draft_summary"] == "Draft summary here."


# ── 5. Approvals route _serialize exposes pipe4Context ───────────────────────


def test_serialize_includes_pipe4_context_when_populated() -> None:
    """_serialize returns pipe4Context dict when approval.pipe4_context is set."""
    from artemis.marketing.routes.approvals import _serialize

    p4_data = {
        "pipeline_run_id": "run-xyz",
        "pipeline_name": "Marketing Pipeline",
        "node_id": "gate-1",
        "node_label": "Signal Review Gate",
        "context": {
            "signal_count": 3,
            "reason_codes": ["HIGH_ENGAGEMENT"],
            "districts": ["MI-7"],
        },
    }
    approval = _make_approval_mock(pipe4_context=p4_data)

    result = _serialize(approval)

    assert result["pipe4Context"] == p4_data
    assert result["pipe4Context"]["pipeline_run_id"] == "run-xyz"


# ── 6. Approvals route _serialize: pipe4Context null for non-PIPE4 ────────────


def test_serialize_pipe4_context_null_for_non_pipe4_approval() -> None:
    """_serialize returns pipe4Context=None for non-PIPE4 approvals."""
    from artemis.marketing.routes.approvals import _serialize

    approval = _make_approval_mock(pipe4_context=None)

    result = _serialize(approval)

    assert result["pipe4Context"] is None
    # Non-PIPE4 legacy fields still present and unaffected
    assert result["id"] == approval.id
    assert result["kind"] == approval.kind
    assert result["status"] == approval.status
    assert result["targetType"] is None
