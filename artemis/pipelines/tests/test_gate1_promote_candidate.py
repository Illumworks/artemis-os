"""Integration tests — Gate-1 approval promotes signals to campaign candidate.

Covers:
1. Seeding a pipeline run suspended at gate_1_signals_inbox with N qualified
   signals → approving via the pipeline resume/decision path → asserting that a
   campaign_candidate is created, linked to the run, uninitiated/in_inbox, with
   signals attached + marked approved.

2. content_brief_assembler's list_run_candidates lookup now finds exactly one
   uninitiated candidate (no "found 0" failure).

3. The manual POST /api/signal-queue/{id}/approve path still works unchanged.

4. Idempotency: approving the same run twice does not duplicate the candidate.

No live LLM is needed.  The test DB must be artemis_test_gate1 (or any
ARTEMIS_TEST_DB_URL value that contains "artemis_test").
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignCandidateSignal, SignalQueue
from artemis.marketing.repository import (
    list_run_candidates,
    promote_qualified_signals_for_run,
    promote_signal_to_candidate,
)
from artemis.pipelines import repository as pipeline_repo

pytestmark = pytest.mark.asyncio

# ── TRUNCATE helpers ──────────────────────────────────────────────────────────

_TRUNCATE_MARKETING = text(
    "TRUNCATE campaign_candidate_signals, campaign_candidates, signal_queue, "
    "district_tier_bands, districts RESTART IDENTITY CASCADE"
)
_TRUNCATE_PIPELINES = text(
    "TRUNCATE pipeline_runs, pipelines, approvals RESTART IDENTITY CASCADE"
)


async def _reset(session: AsyncSession) -> None:
    await session.execute(_TRUNCATE_MARKETING)
    await session.execute(_TRUNCATE_PIPELINES)


async def _seed_district(session: AsyncSession) -> int:
    """Insert a district row and return its id."""
    from artemis.marketing.models import District

    district = District(
        nces_id="1234567890",
        name="Test Unified School District",
        state="CA",
        enrollment=10000,
        tier="D1",
        supported=True,
        on_skip_list=False,
        classification_source="manual",
        classified_at=datetime.now(UTC),
    )
    session.add(district)
    await session.flush()
    await session.refresh(district)
    return district.id


# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _seed_pipeline_run(session: AsyncSession) -> Any:
    """Create a minimal pipeline + run suspended at gate_1_signals_inbox."""
    pipeline = await pipeline_repo.create_pipeline(
        session,
        name="Test Marketing Pipeline",
        nodes=[
            {
                "id": "gate_1_signals_inbox",
                "type": "human_gate",
                "config": {"approval_kind": "signal_brief", "approvers": ["t@example.com"]},
                "label": "Gate 1",
            }
        ],
        edges=[],
        status="active",
    )
    run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="awaiting_approval",
        trigger="manual",
        triggered_by="test",
    )
    # Mark gate as suspended in node_states
    await pipeline_repo.update_pipeline_run(
        session,
        run.id,
        node_states={
            "gate_1_signals_inbox": {
                "status": "suspended",
                "started_at": datetime.now(UTC).isoformat(),
                "cost_usd": 0.0,
            }
        },
    )
    await session.flush()
    return run


async def _seed_qualified_signals(
    session: AsyncSession,
    run_id: str,
    count: int = 5,
    campaign_family: str = "marketing",
    resolved_district_id: int | None = None,
) -> list[Any]:
    """Insert `count` qualified signals linked to a pipeline run.

    When resolved_district_id is provided all signals share the same district +
    family, which is the real-pipeline scenario — cluster_or_create_candidate
    will group them all into ONE candidate.  Without a district each signal gets
    its own candidate (not the real-pipeline scenario but still tested separately).
    """
    signals: list[Any] = []
    for i in range(count):
        sig = SignalQueue(
            headline=f"Test signal {i + 1}",
            summary=f"Summary for signal {i + 1}",
            campaign_family=campaign_family,
            signal_status="qualified",
            discovered_by="test",
            pipeline_run_id=run_id,
            resolved_district_id=resolved_district_id,
        )
        session.add(sig)
        signals.append(sig)
    await session.flush()
    for sig in signals:
        await session.refresh(sig)
    return signals


async def _create_approval_row(session: AsyncSession, run_id: str, node_id: str) -> Any:
    """Create the Approval row that the gate executor would normally create."""
    from artemis.marketing.models import Approval

    approval = Approval(
        kind="signal_brief",
        subject_id=f"{run_id}:{node_id}",
        status="pending",
        decision_payload={
            "run_id": run_id,
            "node_id": node_id,
            "pipeline_name": "Test Marketing Pipeline",
            "approvers": ["t@example.com"],
        },
        pipe4_context={
            "pipeline_run_id": run_id,
            "pipeline_name": "Test Marketing Pipeline",
            "node_id": node_id,
            "node_label": "Gate 1",
            "context": {"approval_kind": "signal_brief", "signal_count": 5},
        },
    )
    session.add(approval)
    await session.flush()
    await session.refresh(approval)
    return approval


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_promote_qualified_signals_for_run_creates_candidate(
    db_session: AsyncSession,
) -> None:
    """promote_qualified_signals_for_run creates a campaign candidate from qualified signals."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        run = await _seed_pipeline_run(db_session)
        run_id = run.id
        await _seed_qualified_signals(db_session, run_id, count=5)

    async with db_session.begin():
        results = await promote_qualified_signals_for_run(db_session, run_id)

    assert len(results) == 5, f"Expected 5 promotion results, got {len(results)}"

    # All signals attached to candidates and marked approved
    async with db_session.begin():
        for res in results:
            assert res.candidate is not None

        # Without resolved_district_id each signal gets its own candidate (5 total).
        # list_run_candidates still returns all of them — coverage of the repo function.
        candidates = await list_run_candidates(db_session, run_id, initiated_only=False)
        assert len(candidates) >= 1, (
            f"Expected at least 1 candidate for run {run_id}; got {len(candidates)}"
        )
        # Verify all candidates are uninitiated (in_inbox)
        for cand in candidates:
            assert cand.initiated_at is None
            assert cand.decision_state == "in_inbox"

    # Signals are approved (checked outside the above begin block to avoid nesting)
    async with db_session.begin():
        sigs = (
            await db_session.execute(
                select(SignalQueue).where(SignalQueue.pipeline_run_id == run_id)
            )
        ).scalars().all()
        for sig in sigs:
            assert sig.signal_status == "approved", (
                f"Signal {sig.id} should be approved; got {sig.signal_status}"
            )


async def test_pipeline_gate1_approval_promotes_candidate(
    db_session: AsyncSession,
) -> None:
    """Approving Gate-1 via apply_approval_decision creates a candidate content_brief_assembler can find.

    This is the end-to-end regression test for the bug described in the brief:
    POST /api/pipeline-runs/{run_id}/resume approved Gate-1 but never promoted
    signals, so content_brief_assembler found 0 candidates.

    5 signals all share the same resolved_district_id + campaign_family (the
    real-pipeline scenario), so cluster_or_create_candidate groups them into
    exactly ONE candidate.
    """
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        district_id = await _seed_district(db_session)
        run = await _seed_pipeline_run(db_session)
        run_id = run.id
        await _seed_qualified_signals(db_session, run_id, count=5, resolved_district_id=district_id)
        approval = await _create_approval_row(db_session, run_id, "gate_1_signals_inbox")

    # Simulate what resume_run does: find the pending approval + apply decision.
    from artemis.marketing.routes.approvals import apply_approval_decision

    async with db_session.begin():
        await apply_approval_decision(
            db_session,
            approval=approval,
            decision="approved",
            decided_by="test@example.com",
            decision_payload={
                "decision": "approved",
                "decided_by": "test@example.com",
                "decided_at": datetime.now(UTC).isoformat(),
            },
        )

    # Verify: list_run_candidates now finds exactly one uninitiated candidate
    async with db_session.begin():
        candidates = await list_run_candidates(db_session, run_id, initiated_only=False)

    assert len(candidates) == 1, (
        f"content_brief_assembler would fail: expected 1 candidate for run {run_id}, "
        f"found {len(candidates)}"
    )
    cand = candidates[0]
    assert cand.initiated_at is None, "Candidate should be uninitiated"
    assert cand.decision_state == "in_inbox", (
        f"Candidate decision_state should be in_inbox; got {cand.decision_state}"
    )

    # Verify signals are attached to the candidate
    async with db_session.begin():
        links = (
            await db_session.execute(
                select(CampaignCandidateSignal).where(
                    CampaignCandidateSignal.candidate_id == cand.id
                )
            )
        ).scalars().all()
        assert len(links) >= 1, f"Candidate {cand.id} has no signal links"

    # Verify all signals are marked approved
    async with db_session.begin():
        sigs = (
            await db_session.execute(
                select(SignalQueue).where(SignalQueue.pipeline_run_id == run_id)
            )
        ).scalars().all()
        for sig in sigs:
            assert sig.signal_status == "approved", (
                f"Signal {sig.id} should be approved; got {sig.signal_status}"
            )


async def test_pipeline_gate1_rejection_does_not_promote(
    db_session: AsyncSession,
) -> None:
    """Rejecting Gate-1 does NOT create a candidate (signals stay qualified)."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        run = await _seed_pipeline_run(db_session)
        run_id = run.id
        await _seed_qualified_signals(db_session, run_id, count=3)
        approval = await _create_approval_row(db_session, run_id, "gate_1_signals_inbox")

    from artemis.marketing.routes.approvals import apply_approval_decision

    async with db_session.begin():
        await apply_approval_decision(
            db_session,
            approval=approval,
            decision="rejected",
            decided_by="test@example.com",
            decision_payload={"decision": "rejected", "decided_by": "test@example.com"},
        )

    async with db_session.begin():
        candidates = await list_run_candidates(db_session, run_id, initiated_only=False)

    assert len(candidates) == 0, (
        f"Rejecting Gate-1 should not create candidates; got {len(candidates)}"
    )


async def test_gate1_approval_idempotent(
    db_session: AsyncSession,
) -> None:
    """Calling promote_qualified_signals_for_run twice does not duplicate the candidate."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        district_id = await _seed_district(db_session)
        run = await _seed_pipeline_run(db_session)
        run_id = run.id
        await _seed_qualified_signals(db_session, run_id, count=5, resolved_district_id=district_id)

    async with db_session.begin():
        await promote_qualified_signals_for_run(db_session, run_id)

    # Second call — signals are now 'approved', so they are skipped
    async with db_session.begin():
        results2 = await promote_qualified_signals_for_run(db_session, run_id)

    # The second call finds 0 qualified signals (all already approved)
    assert len(results2) == 0, (
        "Second call should find 0 qualified signals (already approved); "
        f"got {len(results2)}"
    )

    async with db_session.begin():
        candidates = await list_run_candidates(db_session, run_id, initiated_only=False)

    assert len(candidates) >= 1, "Should still have at least one candidate after second call"


async def test_manual_approve_signal_path_still_works(
    db_session: AsyncSession,
) -> None:
    """POST /api/signal-queue/{id}/approve (manual path) still creates a candidate correctly."""
    async with db_session.begin():
        await _reset(db_session)

    # Insert a qualified signal NOT linked to a pipeline run (manual approval scenario)
    async with db_session.begin():
        sig = SignalQueue(
            headline="Manual approval signal",
            summary="A signal approved manually",
            campaign_family="marketing",
            signal_status="qualified",
            discovered_by="test",
        )
        db_session.add(sig)
        await db_session.flush()
        await db_session.refresh(sig)
        signal_id = sig.id

    # Use promote_signal_to_candidate directly (what approve_signal now calls)
    async with db_session.begin():
        reloaded_sig = await db_session.get(SignalQueue, signal_id)
        assert reloaded_sig is not None
        result = await promote_signal_to_candidate(db_session, reloaded_sig)

    assert result.candidate is not None, "Candidate should be created"
    assert result.created is True, "Should be a new candidate"

    async with db_session.begin():
        verified_sig = await db_session.get(SignalQueue, signal_id)
        assert verified_sig is not None
        assert verified_sig.signal_status == "approved", (
            f"Signal should be approved; got {verified_sig.signal_status}"
        )
        cand = await db_session.get(CampaignCandidate, result.candidate.id)
        assert cand is not None
        assert cand.decision_state == "in_inbox"
        assert cand.initiated_at is None


async def test_promote_signal_to_candidate_idempotent_when_already_approved(
    db_session: AsyncSession,
) -> None:
    """promote_signal_to_candidate is a no-op when the signal is already approved."""
    async with db_session.begin():
        await _reset(db_session)

    async with db_session.begin():
        sig = SignalQueue(
            headline="Already approved",
            summary="",
            campaign_family="marketing",
            signal_status="qualified",
            discovered_by="test",
        )
        db_session.add(sig)
        await db_session.flush()
        await db_session.refresh(sig)
        signal_id = sig.id

    # First call: creates candidate + marks approved
    async with db_session.begin():
        sig_v1 = await db_session.get(SignalQueue, signal_id)
        assert sig_v1 is not None
        res1 = await promote_signal_to_candidate(db_session, sig_v1)
        first_candidate_id = res1.candidate.id

    # Second call: signal is now approved → should be a no-op (return same candidate)
    async with db_session.begin():
        sig_v2 = await db_session.get(SignalQueue, signal_id)
        assert sig_v2 is not None
        res2 = await promote_signal_to_candidate(db_session, sig_v2)

    assert res2.candidate.id == first_candidate_id, (
        "Second call should return the same candidate"
    )
    assert res2.created is False, "Should not report a new creation on second call"
