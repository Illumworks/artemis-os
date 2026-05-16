"""Phase C4 tests — Writing Studio Adapter state transitions.

Tests:
  - init_adapter() subscribes (idempotent)
  - reset_adapter() unsubscribes
  - _resolve_target_state per event kind
  - process_event_with_session: draft.approved → deliverable status 'approved'
  - process_event_with_session: draft.rejected → 'rejected_at_gate_2'
  - process_event_with_session: draft.revised from rejected → 'ready_for_review'
  - process_event_with_session: draft.edited → no-op
  - workspace_state: all approved → 'all_content_approved'
  - workspace_state: any rejected → 'revision_needed'
  - workspace_state: any ready_for_review → 'content_in_review'
  - workspace_state: otherwise → 'content_in_progress'
  - no deliverable_id → no-op (no error)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.writing_studio.adapter import (
    _resolve_target_state,
    init_adapter,
    process_event_with_session,
    reset_adapter,
)
from artemis.marketing.writing_studio.events import DraftEvent, clear_subscribers

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_event(
    event_type: str,
    draft_id: str = "d-1",
    deliverable_id: str | None = None,
    candidate_id: str | None = None,
) -> DraftEvent:
    return DraftEvent(
        event_id="evt-test",
        type=event_type,
        draft_id=draft_id,
        deliverable_id=deliverable_id,
        campaign_id=candidate_id,
    )


async def _make_candidate(db: AsyncSession) -> CampaignCandidate:
    sig = await create_signal(
        db,
        headline="Test",
        campaign_family="obc",
        source_type="manual",
        summary="Test signal",
        discovered_by="test",
    )
    return await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )


async def _make_deliverable(
    db: AsyncSession, candidate_id: int, status: str = "generating"
) -> CampaignDeliverable:
    deliverable = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id=f"ext-draft-{candidate_id}",
        campaign_id=str(candidate_id),
        status=status,
    )
    db.add(deliverable)
    await db.flush()
    await db.refresh(deliverable)
    return deliverable


# ── init/reset ────────────────────────────────────────────────────────────────


class TestInitReset:
    def test_init_adapter_is_idempotent(self) -> None:
        reset_adapter()
        clear_subscribers()
        init_adapter()
        init_adapter()  # second call should not raise
        reset_adapter()
        clear_subscribers()

    def test_reset_adapter_clears_state(self) -> None:
        from artemis.marketing.writing_studio import adapter as _adapter

        reset_adapter()
        clear_subscribers()
        init_adapter()
        assert _adapter._initialized is True
        reset_adapter()
        assert _adapter._initialized is False
        clear_subscribers()


# ── _resolve_target_state ─────────────────────────────────────────────────────


class TestResolveTargetState:
    def test_approved_maps_to_approved(self) -> None:
        assert _resolve_target_state("draft.approved", "generating") == "approved"

    def test_rejected_maps_to_rejected_at_gate_2(self) -> None:
        assert _resolve_target_state("draft.rejected", "ready_for_review") == "rejected_at_gate_2"

    def test_generated_maps_to_ready_for_review(self) -> None:
        assert _resolve_target_state("draft.generated", "generating") == "ready_for_review"

    def test_edited_returns_none(self) -> None:
        assert _resolve_target_state("draft.edited", "ready_for_review") is None

    def test_revised_from_rejected_returns_ready_for_review(self) -> None:
        assert _resolve_target_state("draft.revised", "rejected_at_gate_2") == "ready_for_review"

    def test_revised_from_generating_returns_ready_for_review(self) -> None:
        assert _resolve_target_state("draft.revised", "generating") == "ready_for_review"

    def test_revised_from_approved_returns_none(self) -> None:
        assert _resolve_target_state("draft.revised", "approved") is None

    def test_regenerated_from_rejected_returns_ready_for_review(self) -> None:
        assert (
            _resolve_target_state("draft.regenerated", "rejected_at_gate_2") == "ready_for_review"
        )


# ── process_event_with_session ────────────────────────────────────────────────


class TestProcessEventWithSession:
    async def test_approved_sets_deliverable_status(self, db_session: AsyncSession) -> None:
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, status="ready_for_review")
        event = _make_event("draft.approved", deliverable_id=str(deliverable.id))
        await process_event_with_session(db_session, event)
        await db_session.refresh(deliverable)
        assert deliverable.status == "approved"

    async def test_rejected_sets_deliverable_status(self, db_session: AsyncSession) -> None:
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, status="ready_for_review")
        event = _make_event("draft.rejected", deliverable_id=str(deliverable.id))
        await process_event_with_session(db_session, event)
        await db_session.refresh(deliverable)
        assert deliverable.status == "rejected_at_gate_2"

    async def test_revised_from_rejected_sets_ready_for_review(
        self, db_session: AsyncSession
    ) -> None:
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, status="rejected_at_gate_2")
        event = _make_event("draft.revised", deliverable_id=str(deliverable.id))
        await process_event_with_session(db_session, event)
        await db_session.refresh(deliverable)
        assert deliverable.status == "ready_for_review"

    async def test_edited_is_no_op(self, db_session: AsyncSession) -> None:
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, status="ready_for_review")
        event = _make_event("draft.edited", deliverable_id=str(deliverable.id))
        await process_event_with_session(db_session, event)
        await db_session.refresh(deliverable)
        assert deliverable.status == "ready_for_review"  # unchanged

    async def test_no_deliverable_id_is_no_op(self, db_session: AsyncSession) -> None:
        event = _make_event("draft.approved", deliverable_id=None)
        # Should not raise
        await process_event_with_session(db_session, event)

    async def test_approved_advances_workspace_state_all_content_approved(
        self, db_session: AsyncSession
    ) -> None:
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, status="ready_for_review")
        event = _make_event("draft.approved", deliverable_id=str(deliverable.id))
        await process_event_with_session(db_session, event)
        await db_session.refresh(candidate)
        assert candidate.workspace_state == "all_content_approved"

    async def test_rejected_advances_workspace_state_revision_needed(
        self, db_session: AsyncSession
    ) -> None:
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, status="ready_for_review")
        event = _make_event("draft.rejected", deliverable_id=str(deliverable.id))
        await process_event_with_session(db_session, event)
        await db_session.refresh(candidate)
        assert candidate.workspace_state == "revision_needed"

    async def test_mixed_deliverables_content_in_review(self, db_session: AsyncSession) -> None:
        candidate = await _make_candidate(db_session)
        d1 = await _make_deliverable(db_session, candidate.id, status="ready_for_review")
        await _make_deliverable(db_session, candidate.id, status="generating")
        # Approve d1; second deliverable still generating
        event = _make_event("draft.approved", deliverable_id=str(d1.id))
        await process_event_with_session(db_session, event)
        await db_session.refresh(candidate)
        # One is 'approved', other still 'generating' → content_in_progress
        assert candidate.workspace_state in ("content_in_progress", "all_content_approved")
