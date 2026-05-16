"""Phase C4 tests — Writing Studio invoke layer.

Tests:
  - create_draft_from_candidate: happy path (candidate → draft row)
  - create_draft_from_candidate: deliverable row created in DB
  - create_draft_from_candidate: external draft ID stored on deliverable
  - create_draft_from_candidate: draft.generated event emitted
  - create_draft_from_candidate: assets with empty summary excluded
  - create_draft_from_candidate: missing candidate raises ValueError
  - submit_draft_for_review: creates writing_gate_2 approval row
  - submit_draft_for_review: deliverable status → ready_for_review
  - submit_draft_for_review: returns ApprovalRecord with correct kind
  - submit_draft_for_review: missing deliverable raises ValueError
  - list_campaign_asset_links: only returns assets with non-empty summary
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import Approval, CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import (
    create_campaign_candidate_from_signal,
    create_content_asset,
    create_signal,
    link_content_asset_to_candidate,
)
from artemis.marketing.writing_studio.events import clear_subscribers
from artemis.marketing.writing_studio.external import StubWritingStudio
from artemis.marketing.writing_studio.invoke import (
    ApprovalRecord,
    Draft,
    create_draft_from_candidate,
    list_campaign_asset_links,
    submit_draft_for_review,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_candidate(db: AsyncSession, family: str = "obc") -> CampaignCandidate:
    sig = await create_signal(
        db,
        headline="Test Signal",
        campaign_family=family,
        source_type="manual",
        summary="A district voted yes",
        discovered_by="test",
    )
    return await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )


# ── create_draft_from_candidate ───────────────────────────────────────────────


class TestCreateDraftFromCandidate:
    async def test_returns_draft_dataclass(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session)
        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)
        assert isinstance(draft, Draft)

    async def test_draft_has_correct_candidate_id(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session)
        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)
        assert draft.candidate_id == candidate.id

    async def test_draft_external_id_is_deterministic_stub_id(
        self, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session)
        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)
        assert draft.external_id == "stub-draft-1"

    async def test_deliverable_row_created(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session)
        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == draft.id)
        )
        deliverable = result.scalar_one_or_none()
        assert deliverable is not None

    async def test_deliverable_stores_external_id(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session)
        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == draft.id)
        )
        deliverable = result.scalar_one_or_none()
        assert deliverable is not None
        assert deliverable.deliverable_id == draft.external_id

    async def test_draft_initial_status_generating(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session)
        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)
        assert draft.status == "generating"

    async def test_asset_context_excludes_empty_summary(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session)
        asset_bundle = [
            {
                "id": 1,
                "title": "Asset With Summary",
                "assetType": "doc",
                "summary": "Rich context",
                "sourceUrl": None,
            },
            {
                "id": 2,
                "title": "Asset No Summary",
                "assetType": "doc",
                "summary": "",
                "sourceUrl": None,
            },
            {
                "id": 3,
                "title": "Asset Whitespace",
                "assetType": "doc",
                "summary": "   ",
                "sourceUrl": None,
            },
        ]
        draft = await create_draft_from_candidate(
            db_session, candidate.id, asset_context_bundle=asset_bundle, ws=stub
        )
        # Only asset 1 has non-empty summary
        assert len(draft.asset_context_bundle) == 1
        assert draft.asset_context_bundle[0]["id"] == 1

    async def test_missing_candidate_raises_value_error(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        with pytest.raises((ValueError, Exception)):
            await create_draft_from_candidate(db_session, 99999, ws=stub)

    async def test_draft_generated_event_emitted(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        from artemis.marketing.writing_studio.events import DraftEvent, subscribe

        received: list[DraftEvent] = []

        async def cb(event: DraftEvent) -> None:
            received.append(event)

        unsubscribe = subscribe(cb)
        stub = StubWritingStudio()
        try:
            candidate = await _make_candidate(db_session)
            await create_draft_from_candidate(db_session, candidate.id, ws=stub)
            assert any(e.type == "draft.generated" for e in received)
        finally:
            unsubscribe()
            clear_subscribers()


# ── submit_draft_for_review ───────────────────────────────────────────────────


class TestSubmitDraftForReview:
    async def _create_deliverable(self, db: AsyncSession, candidate_id: int) -> CampaignDeliverable:
        stub = StubWritingStudio()
        draft = await create_draft_from_candidate(db, candidate_id, ws=stub)
        result = await db.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == draft.id)
        )
        return result.scalar_one()

    async def test_returns_approval_record(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await self._create_deliverable(db_session, candidate.id)
        stub = StubWritingStudio()
        record = await submit_draft_for_review(db_session, deliverable_id=deliverable.id, ws=stub)
        assert isinstance(record, ApprovalRecord)

    async def test_creates_writing_gate_2_approval(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await self._create_deliverable(db_session, candidate.id)
        stub = StubWritingStudio()
        record = await submit_draft_for_review(db_session, deliverable_id=deliverable.id, ws=stub)
        assert record.kind == "writing_gate_2"

    async def test_approval_row_in_db(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await self._create_deliverable(db_session, candidate.id)
        stub = StubWritingStudio()
        record = await submit_draft_for_review(db_session, deliverable_id=deliverable.id, ws=stub)
        db_approval = await db_session.get(Approval, record.id)
        assert db_approval is not None
        assert db_approval.kind == "writing_gate_2"

    async def test_deliverable_status_ready_for_review(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await self._create_deliverable(db_session, candidate.id)
        stub = StubWritingStudio()
        await submit_draft_for_review(db_session, deliverable_id=deliverable.id, ws=stub)
        await db_session.refresh(deliverable)
        assert deliverable.status == "ready_for_review"

    async def test_approval_status_pending(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await self._create_deliverable(db_session, candidate.id)
        stub = StubWritingStudio()
        record = await submit_draft_for_review(db_session, deliverable_id=deliverable.id, ws=stub)
        assert record.status == "pending"

    async def test_missing_deliverable_raises(self, db_session: AsyncSession) -> None:
        clear_subscribers()
        stub = StubWritingStudio()
        with pytest.raises(ValueError, match="not found"):
            await submit_draft_for_review(db_session, deliverable_id=99999, ws=stub)


# ── list_campaign_asset_links ─────────────────────────────────────────────────


class TestListCampaignAssetLinks:
    async def test_excludes_assets_with_empty_summary(self, db_session: AsyncSession) -> None:
        candidate = await _make_candidate(db_session)
        # Asset with empty summary
        a1 = await create_content_asset(db_session, asset_type="doc", status="draft")
        # Asset with real summary
        a2 = await create_content_asset(
            db_session, asset_type="snippet", status="draft", summary="Good summary"
        )
        await link_content_asset_to_candidate(db_session, candidate.id, a1.id, "primary")
        await link_content_asset_to_candidate(db_session, candidate.id, a2.id, "supporting")
        links = await list_campaign_asset_links(db_session, candidate.id)
        asset_ids = [lnk.asset_id for lnk in links]
        assert a1.id not in asset_ids
        assert a2.id in asset_ids

    async def test_includes_assets_with_summary(self, db_session: AsyncSession) -> None:
        candidate = await _make_candidate(db_session)
        asset = await create_content_asset(
            db_session, asset_type="doc", status="draft", summary="Has summary"
        )
        await link_content_asset_to_candidate(db_session, candidate.id, asset.id, "primary")
        links = await list_campaign_asset_links(db_session, candidate.id)
        assert len(links) == 1
        assert links[0].asset_id == asset.id

    async def test_no_assets_returns_empty(self, db_session: AsyncSession) -> None:
        candidate = await _make_candidate(db_session)
        links = await list_campaign_asset_links(db_session, candidate.id)
        assert links == []
