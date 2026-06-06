"""Multi-candidate assembler/executor tests — Worker B.

Tests the new multi-uninitiated-candidate contract in
_execute_campaign_initiation_proposal (agent_executor.py):

  - Zero uninitiated  → status="failed" with an informative error.
  - One uninitiated   → same as today (processes it).
  - Two uninitiated   → picks the most recently created; older stays untouched.
  - One initiated + one uninitiated → ignores the initiated one.
  - Lossless: all candidates that existed at test start still exist after.

Uses the marketing conftest db_session fixture (pointed at artemis_test or
artemis_test_worker_b via ARTEMIS_TEST_DB_URL / ARTEMIS_DB_URL).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.marketing.models import (
    CampaignCandidate,
    CampaignCandidateSignal,
    SignalQueue,
)
from artemis.pipelines import repository as pipeline_repo
from artemis.pipelines.node_executors.agent_executor import (
    _execute_campaign_initiation_proposal,
)

pytestmark = pytest.mark.asyncio

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_VALID_PROPOSAL = json.dumps(
    {
        "name": "Test Campaign",
        "objective": "Reach target districts in Q3.",
        "recommended_deliverable_types": ["outreach_email"],
        "target_scope": {"mode": "states", "states": ["TX"]},
        "rationale": "Strong signal cluster in Texas.",
    }
)


def _fake_adapter() -> FakeAdapter:
    """FakeAdapter that always returns a valid CampaignInitiationProposal."""
    return FakeAdapter([ScriptedReply(text=_VALID_PROPOSAL)])


async def _make_pipeline_run(session: AsyncSession) -> str:
    """Create a minimal pipeline + run; return run.id."""
    pipeline = await pipeline_repo.create_pipeline(
        session,
        name="Multi-Candidate Test Pipeline",
        nodes=[],
        edges=[],
    )
    run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="running",
        trigger="manual",
        triggered_by="test",
    )
    await session.flush()
    return run.id


async def _make_signal(session: AsyncSession, *, run_id: str) -> SignalQueue:
    """Create a minimal qualified signal linked to run_id."""
    sig = SignalQueue(
        headline="Test Signal",
        summary="Signal for multi-candidate test",
        campaign_family="obc",
        signal_status="qualified",
        discovered_by="test",
        pipeline_run_id=run_id,
    )
    session.add(sig)
    await session.flush()
    await session.refresh(sig)
    return sig


async def _make_candidate(
    session: AsyncSession,
    signal: SignalQueue,
    *,
    created_at: datetime | None = None,
    initiated_at: datetime | None = None,
) -> CampaignCandidate:
    """Create a candidate and link it to *signal*.

    ``created_at`` lets callers control relative ordering; Postgres stores
    TIMESTAMPTZ so we pass it via INSERT rather than relying on server_default.
    """
    now = created_at or datetime.now(UTC)
    candidate = CampaignCandidate(
        source_signal_id=signal.id,
        campaign_family=signal.campaign_family,
        stage="human_gate_1",
        decision_state="in_inbox",
        workspace_state="pending_content",
        initiated_at=initiated_at,
    )
    # Override created_at before flush so it is persisted with the row.
    candidate.created_at = now
    session.add(candidate)
    await session.flush()
    link = CampaignCandidateSignal(candidate_id=candidate.id, signal_id=signal.id, is_primary=True)
    session.add(link)
    await session.flush()
    await session.refresh(candidate)
    return candidate


async def _count_candidates(session: AsyncSession) -> int:
    result = await session.execute(select(CampaignCandidate))
    return len(list(result.scalars().all()))


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Zero uninitiated → failed
# ─────────────────────────────────────────────────────────────────────────────


async def test_zero_uninitiated_returns_failed(db_session: AsyncSession) -> None:
    """When no uninitiated candidates exist for the run, the executor returns failed."""
    run_id = await _make_pipeline_run(db_session)
    signal = await _make_signal(db_session, run_id=run_id)
    # Create the candidate but mark it as already initiated.
    await _make_candidate(db_session, signal, initiated_at=datetime.now(UTC))
    await db_session.commit()

    before_count = await _count_candidates(db_session)

    result = await _execute_campaign_initiation_proposal(
        session=db_session,
        run_id=run_id,
        agent_id="marketing.content.campaign_brief_assembler",
        model_adapter=_fake_adapter(),
    )

    assert result["status"] == "failed", result
    assert "uninitiated" in (result.get("error") or "").lower()

    # Lossless: no candidates deleted.
    after_count = await _count_candidates(db_session)
    assert after_count == before_count


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — One uninitiated → processed (unchanged behavior)
# ─────────────────────────────────────────────────────────────────────────────


async def test_one_uninitiated_is_processed(db_session: AsyncSession) -> None:
    """With exactly one uninitiated candidate, the executor processes it — same as today."""
    run_id = await _make_pipeline_run(db_session)
    signal = await _make_signal(db_session, run_id=run_id)
    candidate = await _make_candidate(db_session, signal)
    await db_session.commit()

    before_count = await _count_candidates(db_session)

    result = await _execute_campaign_initiation_proposal(
        session=db_session,
        run_id=run_id,
        agent_id="marketing.content.campaign_brief_assembler",
        model_adapter=_fake_adapter(),
    )

    assert result["status"] == "succeeded", result
    assert result.get("candidate_id") == candidate.id

    # Lossless.
    after_count = await _count_candidates(db_session)
    assert after_count == before_count


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Two uninitiated → newer gets assembled, older stays uninitiated
# ─────────────────────────────────────────────────────────────────────────────


async def test_two_uninitiated_picks_most_recent_and_leaves_older_untouched(
    db_session: AsyncSession,
) -> None:
    """Seed two uninitiated candidates 1h apart; newer one gets assembled."""
    run_id = await _make_pipeline_run(db_session)

    now = datetime.now(UTC)
    older_signal = await _make_signal(db_session, run_id=run_id)
    older = await _make_candidate(db_session, older_signal, created_at=now - timedelta(hours=1))

    newer_signal = await _make_signal(db_session, run_id=run_id)
    newer = await _make_candidate(db_session, newer_signal, created_at=now)

    await db_session.commit()
    before_count = await _count_candidates(db_session)

    result = await _execute_campaign_initiation_proposal(
        session=db_session,
        run_id=run_id,
        agent_id="marketing.content.campaign_brief_assembler",
        model_adapter=_fake_adapter(),
    )

    assert result["status"] == "succeeded", result
    assert result.get("candidate_id") == newer.id, (
        f"Expected newer candidate {newer.id} to be selected, got {result.get('candidate_id')}"
    )

    # Older candidate must still exist and remain uninitiated (no initiated_at set by assembler).
    await db_session.refresh(older)
    assert older.initiated_at is None, "Older uninitiated candidate must not be touched"
    assert older.decision_state == "in_inbox", "Older candidate's decision_state must be unchanged"

    # Lossless: both candidates still exist — none deleted.
    after_count = await _count_candidates(db_session)
    assert after_count == before_count


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — One initiated + one uninitiated → uninitiated processed, initiated untouched
# ─────────────────────────────────────────────────────────────────────────────


async def test_initiated_candidate_ignored_uninitiated_processed(
    db_session: AsyncSession,
) -> None:
    """With one initiated + one uninitiated, only the uninitiated is assembled."""
    run_id = await _make_pipeline_run(db_session)

    initiated_signal = await _make_signal(db_session, run_id=run_id)
    initiated = await _make_candidate(db_session, initiated_signal, initiated_at=datetime.now(UTC))

    uninitiated_signal = await _make_signal(db_session, run_id=run_id)
    uninitiated = await _make_candidate(db_session, uninitiated_signal)

    await db_session.commit()
    before_count = await _count_candidates(db_session)

    result = await _execute_campaign_initiation_proposal(
        session=db_session,
        run_id=run_id,
        agent_id="marketing.content.campaign_brief_assembler",
        model_adapter=_fake_adapter(),
    )

    assert result["status"] == "succeeded", result
    assert result.get("candidate_id") == uninitiated.id

    # Initiated candidate's state must be unchanged.
    await db_session.refresh(initiated)
    assert initiated.initiated_at is not None, "Initiated candidate must stay initiated"

    # Lossless.
    after_count = await _count_candidates(db_session)
    assert after_count == before_count


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Lossless verify across all non-zero scenarios
# ─────────────────────────────────────────────────────────────────────────────


async def test_lossless_no_delete_on_successful_assembly(db_session: AsyncSession) -> None:
    """After a successful assembly run, all seed candidates are still present in the DB."""
    run_id = await _make_pipeline_run(db_session)

    now = datetime.now(UTC)
    s1 = await _make_signal(db_session, run_id=run_id)
    c1 = await _make_candidate(db_session, s1, created_at=now - timedelta(hours=2))

    s2 = await _make_signal(db_session, run_id=run_id)
    c2 = await _make_candidate(db_session, s2, created_at=now - timedelta(hours=1))

    s3 = await _make_signal(db_session, run_id=run_id)
    c3 = await _make_candidate(db_session, s3, created_at=now, initiated_at=now)

    await db_session.commit()
    seed_ids = {c1.id, c2.id, c3.id}

    await _execute_campaign_initiation_proposal(
        session=db_session,
        run_id=run_id,
        agent_id="marketing.content.campaign_brief_assembler",
        model_adapter=_fake_adapter(),
    )

    result = await db_session.execute(
        select(CampaignCandidate).where(CampaignCandidate.id.in_(list(seed_ids)))
    )
    surviving_ids = {c.id for c in result.scalars().all()}
    assert surviving_ids == seed_ids, (
        f"Expected all seeded candidate IDs {seed_ids} to survive; got {surviving_ids}"
    )
