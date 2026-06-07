"""Tests for POST /api/campaign-ops/candidates/{id}/writing-handoff.

Covers:
  1. Success path — draft created, linked to candidate, seeded with title/brief/voice/tags.
  2. 404 when candidate_id does not exist.
  3. Second candidate creates a separate draft (no cross-candidate bleed).
  4. assetLabel in body appended to title.
  5. Draft appears in GET /api/writing-studio/overview after creation.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import (
    create_campaign_brief,
    create_campaign_candidate_from_signal,
    create_signal,
)
from artemis.marketing.writing_studio.events import clear_subscribers
from artemis.writing_rules import repository as wr_repo

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_candidate(
    db: AsyncSession,
    family: str = "obc",
    *,
    name: str | None = None,
    objective: str | None = None,
) -> CampaignCandidate:
    sig = await create_signal(
        db,
        headline=f"Handoff Signal for {family}",
        campaign_family=family,
        source_type="manual",
        summary=f"Signal summary for {family}",
        discovered_by="test",
    )
    candidate = await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )
    if name is not None:
        candidate.name = name
    if objective is not None:
        candidate.objective = objective
    await db.commit()
    return candidate


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestWritingHandoffRoute:
    async def test_handoff_creates_draft(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST writing-handoff → 201, returns draft with id, title seeded from campaign name."""
        clear_subscribers()
        candidate = await _make_candidate(
            db_session,
            family="obc",
            name="OBC Fall 2025",
            objective="Drive adoption of OBC in Title I districts",
        )

        resp = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/writing-handoff",
            json={},
        )
        assert resp.status_code == 201
        data = resp.json()

        # id present and > 0
        assert isinstance(data["id"], int)
        assert data["id"] > 0

        # title seeded from campaign name
        assert data["title"] == "OBC Fall 2025"

        # status is "draft_ready" (not "generating" — no auto-compose pipeline)
        assert data["status"] == "draft_ready"

        # candidateId linked correctly
        assert data["candidateId"] == candidate.id

        # briefText includes the objective
        assert data["briefText"] is not None
        assert "Drive adoption of OBC" in data["briefText"]

        # metadata.handoff flag set
        meta = data["metadata"]
        assert meta.get("handoff") is True

    async def test_handoff_404_missing_candidate(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST with a non-existent candidate_id → 404."""
        clear_subscribers()
        resp = await client.post(
            "/api/campaign-ops/candidates/99999/writing-handoff",
            json={},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "campaign_ops_candidate_not_found"

    async def test_handoff_no_bleed_between_candidates(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Two candidates → two separate drafts; each references its own candidate_id."""
        clear_subscribers()
        cand_a = await _make_candidate(db_session, family="obc", name="Campaign A")
        cand_b = await _make_candidate(db_session, family="obc_plus", name="Campaign B")

        resp_a = await client.post(
            f"/api/campaign-ops/candidates/{cand_a.id}/writing-handoff", json={}
        )
        resp_b = await client.post(
            f"/api/campaign-ops/candidates/{cand_b.id}/writing-handoff", json={}
        )

        assert resp_a.status_code == 201
        assert resp_b.status_code == 201

        data_a = resp_a.json()
        data_b = resp_b.json()

        # Two distinct draft IDs
        assert data_a["id"] != data_b["id"]

        # Each draft linked to its own candidate
        assert data_a["candidateId"] == cand_a.id
        assert data_b["candidateId"] == cand_b.id

        # DB confirms two rows
        result = await db_session.execute(
            select(CampaignDeliverable).where(
                CampaignDeliverable.candidate_id.in_([cand_a.id, cand_b.id])
            )
        )
        rows = list(result.scalars())
        assert len(rows) == 2

    async def test_handoff_asset_label_appended_to_title(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """assetLabel in request body is appended to title as '{name} — {label}'."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc", name="Spring Campaign")

        resp = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/writing-handoff",
            json={"assetLabel": "Email Sequence"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Spring Campaign — Email Sequence"

    async def test_handoff_brief_includes_assembled_brief(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """If an assembled Campaign Brief exists, its formatted text appears in briefText."""
        clear_subscribers()
        candidate = await _make_candidate(
            db_session, family="obc", name="Campaign With Brief"
        )
        # Insert an assembled brief with a signal section
        await create_campaign_brief(
            db_session,
            candidate_id=candidate.id,
            content={
                "signal": {"verbatimEvidence": "District saw 30% reading gains"},
                "campaignType": {"primary": "obc"},
            },
            generated_by="test",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/writing-handoff",
            json={},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["briefText"] is not None
        assert "30% reading gains" in data["briefText"]

    async def test_handoff_draft_appears_in_overview(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """After handoff, the draft appears in GET /api/writing-studio/overview."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc", name="Overview Test")

        create_resp = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/writing-handoff", json={}
        )
        assert create_resp.status_code == 201
        draft_id = create_resp.json()["id"]

        overview_resp = await client.get("/api/writing-studio/overview")
        assert overview_resp.status_code == 200
        overview = overview_resp.json()

        draft_ids = [d["id"] for d in overview["drafts"]]
        assert draft_id in draft_ids

    async def test_handoff_folder_linked(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Draft is placed in the campaign's per-candidate folder (folder_id set in metadata)."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc", name="Folder Test Campaign")

        resp = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/writing-handoff", json={}
        )
        assert resp.status_code == 201
        data = resp.json()

        # folder_id present in metadata
        assert data["metadata"].get("folder_id") is not None

        # The folder in DB is keyed on str(candidate_id)
        folder = await wr_repo.get_folder_by_candidate(db_session, candidate.id)
        assert folder is not None
        assert data["metadata"]["folder_id"] == folder.id

    async def test_handoff_tags_include_family(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """metadata.tags contains campaign family."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="obc_plus", name="Tag Test")

        resp = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/writing-handoff", json={}
        )
        assert resp.status_code == 201
        tags = resp.json()["metadata"].get("tags", [])
        assert "obc_plus" in tags
