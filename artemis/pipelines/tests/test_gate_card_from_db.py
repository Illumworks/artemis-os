"""CC5 — Gate-1 approval card reads qualified signals from the DB.

These tests cover ``_build_pipe4_context`` (the async, DB-reading variant) in
``artemis.pipelines.node_executors.human_gate_executor``. The contract: for a
signal-family gate (``approval_kind`` in the signal family), the context is
built from ``signal_queue`` rows committed for the run (signal_status=qualified),
not from pipeline ``node_states``.

The conftest TRUNCATEs ``pipeline_runs ... CASCADE``, which also clears
``signal_queue`` via its FK. We import ``artemis.marketing.models`` here so the
SignalQueue/Approval tables are registered on Base.metadata (conftest does not).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.marketing.models  # noqa: F401 — register SignalQueue/Approval on Base.metadata
from artemis.marketing.models import SignalQueue
from artemis.pipelines import repository as repo
from artemis.pipelines.node_executors.human_gate_executor import _build_pipe4_context

pytestmark = pytest.mark.asyncio


async def _seed_run(session: AsyncSession) -> str:
    """Create a minimal pipeline + run; return the run id (satisfies the FK)."""
    pipeline = await repo.create_pipeline(
        session,
        name="CC5 Test Pipeline",
        nodes=[
            {
                "id": "trigger",
                "type": "trigger_manual",
                "label": "trigger",
                "config": {},
                "position": {"x": 0.0, "y": 0.0},
            }
        ],
        edges=[],
    )
    run = await repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="running",
        trigger="manual",
        triggered_by="test",
    )
    return run.id


def _signal(
    run_id: str,
    *,
    headline: str,
    reason_codes: list[Any],
    district_id: str | None,
    state: str | None,
    brief: dict[str, Any] | None,
    signal_status: str = "qualified",
) -> SignalQueue:
    return SignalQueue(
        source_type="scout",
        pipeline_run_id=run_id,
        headline=headline,
        summary="",
        campaign_family="marketing",
        urgency_tier="standard",
        discovered_by="test",
        district_id=district_id,
        state=state,
        reason_codes=reason_codes,
        qualification_json={"brief": brief} if brief is not None else None,
        signal_status=signal_status,
    )


async def test_signal_gate_context_populated_from_db(db_session: AsyncSession) -> None:
    """N qualified signals → signal_count==N, real brief_preview, codes, districts."""
    run_id = await _seed_run(db_session)

    db_session.add_all(
        [
            _signal(
                run_id,
                headline="Signal A",
                reason_codes=[{"code": "budget_cut"}, "literacy_mandate"],
                district_id="TX-Austin",
                state="TX",
                brief={
                    "preview": "Austin ISD cut reading budget 12%.",
                    "body": "Full brief body for Austin ISD...",
                    "evidence_quote": "We are reducing the literacy budget.",
                },
            ),
            _signal(
                run_id,
                headline="Signal B",
                reason_codes=[{"code": "new_superintendent"}],
                district_id=None,
                state="CA",  # district_id null → falls back to state
                brief={
                    "preview": "Fresno names new superintendent.",
                    "body": "Body B...",
                    "evidence_quote": None,
                },
            ),
            # A non-qualified signal for the SAME run must be ignored.
            _signal(
                run_id,
                headline="Pending signal",
                reason_codes=[{"code": "should_not_appear"}],
                district_id="ZZ-Nowhere",
                state="ZZ",
                brief={"preview": "Should not appear", "body": "x"},
                signal_status="pending_qualification",
            ),
        ]
    )
    await db_session.flush()

    ctx = await _build_pipe4_context("signal_brief", {}, session=db_session, run_id=run_id)

    assert ctx["signal_count"] == 2
    assert ctx["brief_preview"] == "Austin ISD cut reading budget 12%."
    assert ctx["reason_codes"] == sorted({"budget_cut", "literacy_mandate", "new_superintendent"})
    assert ctx["districts"] == sorted({"TX-Austin", "CA"})
    # Evidence quote is the first available across rows.
    assert ctx["evidence_quote"] == "We are reducing the literacy budget."
    # The non-qualified signal's code must NOT leak in.
    assert "should_not_appear" not in ctx["reason_codes"]


async def test_signal_gate_brief_preview_falls_back_to_body(db_session: AsyncSession) -> None:
    """When the top brief has no preview, the body is used (truncated)."""
    run_id = await _seed_run(db_session)
    long_body = "x" * 600
    db_session.add(
        _signal(
            run_id,
            headline="No preview signal",
            reason_codes=["code_a"],
            district_id="OR-Portland",
            state="OR",
            brief={"body": long_body},
        )
    )
    await db_session.flush()

    ctx = await _build_pipe4_context("signal_brief", {}, session=db_session, run_id=run_id)

    assert ctx["signal_count"] == 1
    assert ctx["brief_preview"]
    assert len(ctx["brief_preview"]) == 400  # truncated to _PREVIEW_MAX


async def test_signal_gate_empty_run_yields_clean_empty_ctx(db_session: AsyncSession) -> None:
    """A run with 0 qualified signals → clean empty ctx, no error."""
    run_id = await _seed_run(db_session)

    ctx = await _build_pipe4_context("signal_brief", {}, session=db_session, run_id=run_id)

    assert ctx["signal_count"] == 0
    assert ctx["reason_codes"] == []
    assert ctx["districts"] == []
    assert ctx["evidence_quote"] is None
    assert ctx["brief_preview"] is None


async def test_signal_gate_ctx_has_ui_contract_keys(db_session: AsyncSession) -> None:
    """Returned ctx exposes exactly the keys the UI card reads (plus carriers)."""
    run_id = await _seed_run(db_session)
    db_session.add(
        _signal(
            run_id,
            headline="Signal",
            reason_codes=["code_a"],
            district_id="NY-Buffalo",
            state="NY",
            brief={"preview": "preview text", "body": "body"},
        )
    )
    await db_session.flush()

    ctx = await _build_pipe4_context("signal_brief", {}, session=db_session, run_id=run_id)

    for key in ("signal_count", "districts", "reason_codes", "evidence_quote", "brief_preview"):
        assert key in ctx, f"UI-contract key {key!r} missing from ctx"
    # Existing keys preserved so nothing else downstream breaks.
    assert ctx["approval_kind"] == "signal_brief"
    assert "draft_summary" in ctx


async def test_content_gate_falls_back_to_node_states(db_session: AsyncSession) -> None:
    """A non-signal gate kind uses the legacy node_states path, ignoring the DB."""
    run_id = await _seed_run(db_session)
    # Even with a qualified signal in the DB, a content_draft gate must NOT read it.
    db_session.add(
        _signal(
            run_id,
            headline="DB signal",
            reason_codes=["db_code"],
            district_id="DB-District",
            state="DB",
            brief={"preview": "db preview", "body": "x"},
        )
    )
    await db_session.flush()

    node_states = {
        "draft_node": {"draft": {"summary": "A drafted content summary."}},
    }
    ctx = await _build_pipe4_context(
        "content_draft", node_states, session=db_session, run_id=run_id
    )

    assert ctx["draft_summary"] == "A drafted content summary."
    assert ctx["signal_count"] == 0  # DB signal ignored for content gate
    assert "db_code" not in ctx["reason_codes"]
