"""ws-campaign-folders — Writing Studio campaign folder grouping tests.

Covers:
  - create_draft_from_candidate sets campaign_id = campaign_family (not str(candidate_id))
  - create_draft_from_candidate creates a WritingFolder keyed on campaign_family
  - create_draft_from_candidate stores folder_id + folder_name in metadata
  - second draft for same family reuses the existing folder (get-or-create is idempotent)
  - overview endpoint: draft has non-null folder_id → not orphaned in "All drafts"
  - overview endpoint: campaign filter uses campaign_family as id
  - list endpoint: campaign_id filter matches on family string
  - backfill_campaign_folders: fixes existing rows that have numeric campaign_id
  - backfill_campaign_folders: idempotent (second run does nothing)
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.writing_rules.models  # noqa: F401  — ensures writing_folders is in metadata
from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import (
    create_campaign_candidate_from_signal,
    create_signal,
)
from artemis.marketing.writing_studio.events import clear_subscribers
from artemis.marketing.writing_studio.external import StubWritingStudio
from artemis.marketing.writing_studio.invoke import (
    BackfillResult,
    backfill_campaign_folders,
    create_draft_from_candidate,
)
from artemis.writing_rules.models import WritingFolder
from artemis.writing_rules.repository import get_folder_by_campaign

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession) -> None:
    """Truncate writing_folders in addition to the standard marketing tables.

    The marketing conftest truncates campaign_deliverables etc., but not
    writing_folders.  We need a clean slate for folder get-or-create logic.
    """
    await db_session.execute(
        text(
            "TRUNCATE writing_sources, writing_examples, writing_rules, "
            "writing_folders, writing_profiles RESTART IDENTITY CASCADE"
        )
    )
    await db_session.commit()


async def _make_candidate(
    db: AsyncSession, family: str = "obc", name: str | None = None
) -> CampaignCandidate:
    sig = await create_signal(
        db,
        headline="WS Folder Test Signal",
        campaign_family=family,
        source_type="manual",
        summary="folder test",
        discovered_by="test",
    )
    candidate = await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )
    if name is not None:
        candidate.name = name
        await db.flush()
    await db.commit()
    return candidate


# ── create_draft_from_candidate: campaign_id and folder ──────────────────────


class TestCreateDraftCampaignFolder:
    async def test_campaign_id_is_family_not_candidate_id(self, db_session: AsyncSession) -> None:
        """campaign_id must be campaign_family, not str(candidate_id)."""
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session, family="obc")

        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)

        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == draft.id)
        )
        deliverable = result.scalar_one()
        assert deliverable.campaign_id == "obc"
        assert deliverable.campaign_id != str(candidate.id)

    async def test_folder_created_for_campaign_family(self, db_session: AsyncSession) -> None:
        """A WritingFolder keyed on campaign_family must be created."""
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session, family="refresh")

        await create_draft_from_candidate(db_session, candidate.id, ws=stub)

        folder = await get_folder_by_campaign(db_session, "refresh")
        assert folder is not None
        assert folder.campaign_id == "refresh"

    async def test_metadata_folder_id_is_set(self, db_session: AsyncSession) -> None:
        """deliverable_metadata.folder_id must be a non-null integer."""
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session, family="obc")

        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)

        db_session.expire_all()
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == draft.id)
        )
        deliverable = result.scalar_one()
        meta = deliverable.deliverable_metadata or {}
        assert isinstance(meta.get("folder_id"), int)

    async def test_metadata_folder_name_is_set(self, db_session: AsyncSession) -> None:
        """deliverable_metadata.folder_name must be set."""
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session, family="obc", name="OBC Campaign")

        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)

        db_session.expire_all()
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == draft.id)
        )
        deliverable = result.scalar_one()
        meta = deliverable.deliverable_metadata or {}
        assert meta.get("folder_name")

    async def test_second_draft_same_family_reuses_folder(self, db_session: AsyncSession) -> None:
        """Two drafts from the same family share one folder (get-or-create idempotent)."""
        clear_subscribers()
        stub = StubWritingStudio()
        candidate = await _make_candidate(db_session, family="obc")

        draft1 = await create_draft_from_candidate(db_session, candidate.id, ws=stub)
        draft2 = await create_draft_from_candidate(db_session, candidate.id, ws=stub)

        db_session.expire_all()
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id.in_([draft1.id, draft2.id]))
        )
        deliverables = list(result.scalars())
        folder_ids = {(d.deliverable_metadata or {}).get("folder_id") for d in deliverables}
        # Both drafts must point to the same folder.
        assert len(folder_ids) == 1
        assert None not in folder_ids

        # Only one folder exists for this family.
        folder_rows = await db_session.execute(
            select(WritingFolder).where(WritingFolder.campaign_id == "obc")
        )
        assert len(list(folder_rows.scalars())) == 1

    async def test_drafts_from_different_families_get_different_folders(
        self, db_session: AsyncSession
    ) -> None:
        """Two drafts from different families get different folders."""
        clear_subscribers()
        stub = StubWritingStudio()
        c1 = await _make_candidate(db_session, family="obc")
        c2 = await _make_candidate(db_session, family="refresh")

        d1 = await create_draft_from_candidate(db_session, c1.id, ws=stub)
        d2 = await create_draft_from_candidate(db_session, c2.id, ws=stub)

        db_session.expire_all()
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id.in_([d1.id, d2.id]))
        )
        deliverables = list(result.scalars())
        folder_ids = {(d.deliverable_metadata or {}).get("folder_id") for d in deliverables}
        assert len(folder_ids) == 2
        assert None not in folder_ids


# ── Overview endpoint: folder grouping ───────────────────────────────────────


class TestOverviewFolderGrouping:
    async def test_draft_has_non_null_folder_id_in_overview(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """After creation the draft in the overview has a non-null folder_id."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc")
        stub = StubWritingStudio()
        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)

        resp = await client.get("/api/writing-studio/overview")
        assert resp.status_code == 200
        overview = resp.json()

        matching = [d for d in overview["drafts"] if d["id"] == draft.id]
        assert len(matching) == 1
        assert matching[0]["folder_id"] is not None

    async def test_overview_folders_includes_campaign_folder(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Overview.folders contains the campaign folder created for the draft."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc")
        stub = StubWritingStudio()
        await create_draft_from_candidate(db_session, candidate.id, ws=stub)

        resp = await client.get("/api/writing-studio/overview")
        assert resp.status_code == 200
        overview = resp.json()

        campaign_ids = {f.get("campaign_id") for f in overview["folders"]}
        assert "obc" in campaign_ids

    async def test_overview_campaigns_uses_family_as_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Each campaign entry in overview.campaigns has id = campaign_family string."""
        clear_subscribers()
        await _make_candidate(db_session, family="obc")
        await _make_candidate(db_session, family="refresh")

        resp = await client.get("/api/writing-studio/overview")
        assert resp.status_code == 200
        campaigns: list[dict[str, Any]] = resp.json()["campaigns"]
        ids = {c["id"] for c in campaigns}
        assert "obc" in ids
        assert "refresh" in ids
        # Numeric candidate ids must NOT be used as campaign ids.
        for c in campaigns:
            assert not str(c["id"]).isdigit(), (
                f"campaign.id should be campaign_family string, got numeric: {c['id']!r}"
            )


# ── /drafts filter by campaign_id ────────────────────────────────────────────


class TestDraftListCampaignFilter:
    async def test_campaign_id_filter_matches_family_string(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """?campaign_id=obc should return drafts whose campaign_id == 'obc'."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc")
        stub = StubWritingStudio()
        draft = await create_draft_from_candidate(db_session, candidate.id, ws=stub)

        resp = await client.get("/api/writing-studio/drafts?campaign_id=obc")
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()["drafts"]]
        assert draft.id in ids

    async def test_campaign_id_filter_excludes_other_families(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """?campaign_id=obc should not return drafts from 'refresh'."""
        clear_subscribers()
        c_obc = await _make_candidate(db_session, family="obc")
        c_ref = await _make_candidate(db_session, family="refresh")
        stub = StubWritingStudio()
        d_obc = await create_draft_from_candidate(db_session, c_obc.id, ws=stub)
        d_ref = await create_draft_from_candidate(db_session, c_ref.id, ws=stub)

        resp = await client.get("/api/writing-studio/drafts?campaign_id=obc")
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()["drafts"]]
        assert d_obc.id in ids
        assert d_ref.id not in ids


# ── Backfill ──────────────────────────────────────────────────────────────────


class TestBackfillCampaignFolders:
    async def test_backfill_fixes_numeric_campaign_id(self, db_session: AsyncSession) -> None:
        """Existing rows with campaign_id = str(candidate_id) are updated."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc")

        # Simulate an old-style row: campaign_id = str(candidate.id), no folder_id.
        old_row = CampaignDeliverable(
            candidate_id=candidate.id,
            deliverable_id="stub-old-1",
            campaign_id=str(candidate.id),  # old numeric form
            status="generating",
            deliverable_metadata={},
        )
        db_session.add(old_row)
        await db_session.flush()
        await db_session.commit()

        old_row_id = old_row.id  # capture before expire_all

        result = await backfill_campaign_folders(db_session)
        await db_session.commit()

        assert isinstance(result, BackfillResult)
        assert result.rows_updated >= 1

        db_session.expire_all()
        refreshed = await db_session.get(CampaignDeliverable, old_row_id)
        assert refreshed is not None
        assert refreshed.campaign_id == "obc"
        meta = refreshed.deliverable_metadata or {}
        assert isinstance(meta.get("folder_id"), int)

    async def test_backfill_idempotent(self, db_session: AsyncSession) -> None:
        """Running backfill twice leaves rows correctly set and does not create extra folders."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc")

        old_row = CampaignDeliverable(
            candidate_id=candidate.id,
            deliverable_id="stub-idem-1",
            campaign_id=str(candidate.id),
            status="generating",
            deliverable_metadata={},
        )
        db_session.add(old_row)
        await db_session.flush()
        await db_session.commit()

        # First run.
        r1 = await backfill_campaign_folders(db_session)
        await db_session.commit()

        # Second run — nothing should change.
        r2 = await backfill_campaign_folders(db_session)
        await db_session.commit()

        assert r1.rows_updated >= 1
        assert r2.rows_updated == 0  # already correct

        # Still exactly one folder for "obc".
        folder_result = await db_session.execute(
            select(WritingFolder).where(WritingFolder.campaign_id == "obc")
        )
        assert len(list(folder_result.scalars())) == 1

    async def test_backfill_already_correct_row_not_updated(self, db_session: AsyncSession) -> None:
        """A row already correctly set (family string + folder_id) is untouched."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc")
        # Create the folder first.
        from artemis.writing_rules.repository import get_or_create_folder_by_campaign

        folder = await get_or_create_folder_by_campaign(db_session, "obc", name="OBC")
        await db_session.commit()

        # Insert a row that already has the correct campaign_id and folder_id.
        good_row = CampaignDeliverable(
            candidate_id=candidate.id,
            deliverable_id="stub-correct-1",
            campaign_id="obc",
            status="generating",
            deliverable_metadata={"folder_id": folder.id, "folder_name": "OBC"},
        )
        db_session.add(good_row)
        await db_session.flush()
        await db_session.commit()

        result = await backfill_campaign_folders(db_session)
        assert result.rows_updated == 0
