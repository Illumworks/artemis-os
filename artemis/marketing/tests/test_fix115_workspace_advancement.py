"""FIX115 — deliverable-run workspace_state advancement.

Covers the shared workspace.py helpers and the advance_workspace_for_node
hook that the agent_executor calls. The goal is that a candidate whose
deliverable run reaches Gate-2 reports `content_in_review`, not
`pending_content` (which was the bug Jon hit on candidates 5/7/8 in the
live DB on 2026-06-01).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignDeliverable
from artemis.marketing.repository import (
    create_campaign_candidate_from_signal,
    create_signal,
)
from artemis.marketing.state_machine import (
    DeliverableState,
    IllegalTransition,
    WorkspaceState,
)
from artemis.marketing.workspace import (
    advance_workspace_for_node,
    recompute_workspace_state_from_deliverables,
    walk_workspace_state_to,
    workspace_path,
)

# ── workspace_path (pure unit) ───────────────────────────────────────────────


class TestWorkspacePath:
    def test_path_pending_to_content_in_review(self) -> None:
        path = workspace_path(WorkspaceState.pending_content, WorkspaceState.content_in_review)
        assert path == [
            WorkspaceState.in_content_preparation,
            WorkspaceState.sent_to_writing_studio,
            WorkspaceState.content_in_review,
        ]

    def test_path_pending_to_in_content_preparation(self) -> None:
        path = workspace_path(WorkspaceState.pending_content, WorkspaceState.in_content_preparation)
        assert path == [WorkspaceState.in_content_preparation]

    def test_path_same_state_empty(self) -> None:
        assert workspace_path(WorkspaceState.pending_content, WorkspaceState.pending_content) == []

    def test_unreachable_returns_none(self) -> None:
        # all_content_approved is terminal — nothing leads back from it.
        assert (
            workspace_path(WorkspaceState.all_content_approved, WorkspaceState.pending_content)
            is None
        )


# ── helpers ──────────────────────────────────────────────────────────────────


async def _make_candidate(db: AsyncSession) -> int:
    sig = await create_signal(
        db,
        headline="FIX115 test signal",
        campaign_family="outreach_email",
        source_type="manual",
        summary="signal for workspace advancement",
        discovered_by="test",
    )
    candidate = await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )
    return candidate.id


async def _add_deliverable(db: AsyncSession, candidate_id: int, status: str) -> CampaignDeliverable:
    deliverable = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id=f"draft-{candidate_id}-{status}",
        campaign_id=str(candidate_id),
        status=status,
    )
    db.add(deliverable)
    await db.flush()
    await db.refresh(deliverable)
    return deliverable


async def _workspace_state(db: AsyncSession, candidate_id: int) -> str:
    from artemis.marketing.models import CampaignCandidate

    candidate = await db.get(CampaignCandidate, candidate_id)
    assert candidate is not None
    return candidate.workspace_state


# ── walk_workspace_state_to ──────────────────────────────────────────────────


class TestWalkWorkspaceStateTo:
    async def test_walks_full_legal_path(self, db_session: AsyncSession) -> None:
        cid = await _make_candidate(db_session)
        assert await _workspace_state(db_session, cid) == WorkspaceState.pending_content.value

        await walk_workspace_state_to(
            db_session, cid, WorkspaceState.content_in_review, actor="test"
        )
        assert await _workspace_state(db_session, cid) == WorkspaceState.content_in_review.value

    async def test_no_op_when_already_at_target(self, db_session: AsyncSession) -> None:
        cid = await _make_candidate(db_session)
        await walk_workspace_state_to(
            db_session, cid, WorkspaceState.in_content_preparation, actor="test"
        )
        # Second call is idempotent — no exception, state unchanged.
        await walk_workspace_state_to(
            db_session, cid, WorkspaceState.in_content_preparation, actor="test"
        )
        assert (
            await _workspace_state(db_session, cid) == WorkspaceState.in_content_preparation.value
        )

    async def test_unreachable_target_raises_illegal_transition(
        self, db_session: AsyncSession
    ) -> None:
        cid = await _make_candidate(db_session)
        # Drive into all_content_approved (terminal).
        await walk_workspace_state_to(
            db_session, cid, WorkspaceState.all_content_approved, actor="test"
        )
        # Now try to walk back to pending_content — unreachable from terminal.
        with pytest.raises(IllegalTransition):
            await walk_workspace_state_to(
                db_session, cid, WorkspaceState.pending_content, actor="test"
            )


# ── recompute_workspace_state_from_deliverables ──────────────────────────────


class TestRecomputeFromDeliverables:
    async def test_draft_ready_walks_from_pending_to_content_in_review(
        self, db_session: AsyncSession
    ) -> None:
        cid = await _make_candidate(db_session)
        await _add_deliverable(db_session, cid, DeliverableState.draft_ready.value)

        await recompute_workspace_state_from_deliverables(db_session, cid, actor="test")
        assert await _workspace_state(db_session, cid) == WorkspaceState.content_in_review.value

    async def test_generating_walks_to_in_content_preparation(
        self, db_session: AsyncSession
    ) -> None:
        cid = await _make_candidate(db_session)
        await _add_deliverable(db_session, cid, DeliverableState.generating.value)

        await recompute_workspace_state_from_deliverables(db_session, cid, actor="test")
        assert (
            await _workspace_state(db_session, cid) == WorkspaceState.in_content_preparation.value
        )

    async def test_no_deliverables_is_noop(self, db_session: AsyncSession) -> None:
        cid = await _make_candidate(db_session)
        await recompute_workspace_state_from_deliverables(db_session, cid, actor="test")
        assert await _workspace_state(db_session, cid) == WorkspaceState.pending_content.value


# ── advance_workspace_for_node (FIX115 deliverable-run hook) ─────────────────


class TestAdvanceWorkspaceForNode:
    async def test_content_asset_selector_advances_to_in_content_preparation(
        self, db_session: AsyncSession
    ) -> None:
        cid = await _make_candidate(db_session)

        await advance_workspace_for_node(
            db_session, cid, "content_asset_selector", actor="deliverable_run"
        )
        assert (
            await _workspace_state(db_session, cid) == WorkspaceState.in_content_preparation.value
        )

    async def test_writing_studio_adapter_advances_to_sent_to_writing_studio(
        self, db_session: AsyncSession
    ) -> None:
        cid = await _make_candidate(db_session)
        # Cross the prep step first (asset selector node).
        await advance_workspace_for_node(
            db_session, cid, "content_asset_selector", actor="deliverable_run"
        )

        await advance_workspace_for_node(
            db_session, cid, "content_writing_studio_adapter", actor="deliverable_run"
        )
        assert (
            await _workspace_state(db_session, cid) == WorkspaceState.sent_to_writing_studio.value
        )

    async def test_deliverable_node_with_draft_ready_lands_at_content_in_review(
        self, db_session: AsyncSession
    ) -> None:
        """The FIX115 bug, recreated: a candidate whose deliverable_X node finishes
        with the deliverable at draft_ready should reach content_in_review."""
        cid = await _make_candidate(db_session)
        await _add_deliverable(db_session, cid, DeliverableState.draft_ready.value)

        # deliverable_X nodes don't appear in NODE_WORKSPACE_TARGETS — the
        # recompute step is what walks workspace forward for them.
        await advance_workspace_for_node(
            db_session, cid, "deliverable_outreach_email", actor="deliverable_run"
        )
        assert await _workspace_state(db_session, cid) == WorkspaceState.content_in_review.value

    async def test_full_sequence_pending_to_content_in_review(
        self, db_session: AsyncSession
    ) -> None:
        """End-to-end sequence: replays the deliverable pipeline's node order
        and asserts the candidate ends at content_in_review, not pending_content."""
        cid = await _make_candidate(db_session)

        # Stage 1: content_asset_selector runs.
        await advance_workspace_for_node(
            db_session, cid, "content_asset_selector", actor="deliverable_run"
        )
        assert (
            await _workspace_state(db_session, cid) == WorkspaceState.in_content_preparation.value
        )

        # Stage 2: content_writing_studio_adapter (orchestrator) runs.
        await advance_workspace_for_node(
            db_session, cid, "content_writing_studio_adapter", actor="deliverable_run"
        )
        assert (
            await _workspace_state(db_session, cid) == WorkspaceState.sent_to_writing_studio.value
        )

        # Stage 3: deliverable_outreach_email writes draft, lands draft_ready.
        await _add_deliverable(db_session, cid, DeliverableState.draft_ready.value)
        await advance_workspace_for_node(
            db_session, cid, "deliverable_outreach_email", actor="deliverable_run"
        )
        assert await _workspace_state(db_session, cid) == WorkspaceState.content_in_review.value

    async def test_idempotent_rerun(self, db_session: AsyncSession) -> None:
        """Resuming/re-running a deliverable run must not crash on already-advanced state."""
        cid = await _make_candidate(db_session)
        await _add_deliverable(db_session, cid, DeliverableState.draft_ready.value)

        for _ in range(3):
            await advance_workspace_for_node(
                db_session, cid, "content_asset_selector", actor="deliverable_run"
            )
            await advance_workspace_for_node(
                db_session, cid, "content_writing_studio_adapter", actor="deliverable_run"
            )
            await advance_workspace_for_node(
                db_session, cid, "deliverable_outreach_email", actor="deliverable_run"
            )

        assert await _workspace_state(db_session, cid) == WorkspaceState.content_in_review.value

    async def test_unknown_node_id_only_runs_recompute(self, db_session: AsyncSession) -> None:
        """A node not in NODE_WORKSPACE_TARGETS still triggers the deliverable
        recompute — that's what catches deliverable_X nodes."""
        cid = await _make_candidate(db_session)
        await _add_deliverable(db_session, cid, DeliverableState.generating.value)

        await advance_workspace_for_node(db_session, cid, "some_unrelated_node", actor="test")
        assert (
            await _workspace_state(db_session, cid) == WorkspaceState.in_content_preparation.value
        )

    async def test_terminal_state_swallows_illegal_transition(
        self, db_session: AsyncSession
    ) -> None:
        """Once a campaign is at all_content_approved, replay attempts must not
        raise — workspace sync is advisory and never fails the run."""
        cid = await _make_candidate(db_session)
        await walk_workspace_state_to(
            db_session, cid, WorkspaceState.all_content_approved, actor="test"
        )

        # No exception should escape.
        await advance_workspace_for_node(
            db_session, cid, "content_asset_selector", actor="deliverable_run"
        )
        assert await _workspace_state(db_session, cid) == WorkspaceState.all_content_approved.value
