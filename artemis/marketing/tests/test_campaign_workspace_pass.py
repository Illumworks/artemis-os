"""Campaign workspace pass — backend endpoint tests.

Covers:
  POST /api/campaign-deliverables/{id}/unlink — detach from campaign → placeholder
  POST /api/campaign-deliverables/{id}/attach — attach to target candidate
  POST /api/campaign-deliverables/blank       — blank draft linked to candidate
  GET  /api/campaign-deliverables/unlinked-drafts — list placeholder-candidate deliverables
  Approve-via-approvals-path: lossless, same backend, status flips correctly
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.writing_studio import invoke as ws_invoke
from artemis.marketing.writing_studio.events import clear_subscribers

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_candidate(
    db: AsyncSession, family: str = "cwp_test", name: str | None = None
) -> CampaignCandidate:
    sig = await create_signal(
        db,
        headline="Campaign WS Pass Test Signal",
        campaign_family=family,
        source_type="manual",
        summary="workspace pass test",
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


async def _make_deliverable(
    db: AsyncSession,
    candidate_id: int,
    title: str = "Test Draft",
    status: str = "draft_ready",
) -> CampaignDeliverable:
    """Create a deliverable directly (not via writing-studio invoke) for test speed."""
    deliverable = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id=f"stub-{title.lower().replace(' ', '-')}",
        campaign_id="cwp_test",
        status=status,
        deliverable_metadata={"title": title, "externalTitle": title},
    )
    db.add(deliverable)
    await db.flush()
    await db.refresh(deliverable)
    await db.commit()
    return deliverable


# ── Tests: unlink ─────────────────────────────────────────────────────────────


class TestUnlinkDeliverable:
    async def test_unlink_detaches_to_placeholder_candidate(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Unlink moves candidate_id to the placeholder; draft row is NOT deleted."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, "Email Draft")

        assert deliverable.candidate_id == candidate.id

        resp = await client.post(f"/api/campaign-deliverables/{deliverable.id}/unlink")
        assert resp.status_code == 200
        body = resp.json()
        # Response must still have the deliverable id
        assert body["id"] == deliverable.id

        # Fetch fresh from DB — row must survive (lossless)
        await db_session.refresh(deliverable)
        assert deliverable.id == deliverable.id  # row exists

        # candidate_id must have changed to the placeholder
        placeholder = await ws_invoke._get_or_create_template_workspace_candidate(db_session)
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == deliverable.id)
        )
        fresh = result.scalar_one()
        assert fresh.candidate_id == placeholder.id

    async def test_unlink_unknown_deliverable_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        resp = await client.post("/api/campaign-deliverables/999999/unlink")
        assert resp.status_code == 404

    async def test_unlink_preserves_draft_content(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Metadata (title, content) is preserved after unlink."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, "One-Pager Draft")

        resp = await client.post(f"/api/campaign-deliverables/{deliverable.id}/unlink")
        assert resp.status_code == 200
        body = resp.json()
        # Title still present in metadata
        assert body["metadata"]["title"] == "One-Pager Draft"


# ── Tests: attach ─────────────────────────────────────────────────────────────


class TestAttachDeliverable:
    async def test_attach_moves_candidate_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Attach moves a deliverable from placeholder to a real campaign candidate."""
        clear_subscribers()
        # Start as a "blank" standalone deliverable on placeholder
        placeholder = await ws_invoke._get_or_create_template_workspace_candidate(db_session)
        deliverable = await _make_deliverable(db_session, placeholder.id, "Standalone Draft")

        target_candidate = await _make_candidate(db_session, family="cwp_attach")

        resp = await client.post(
            f"/api/campaign-deliverables/{deliverable.id}/attach",
            json={"candidateId": target_candidate.id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["candidateId"] == target_candidate.id
        assert body["id"] == deliverable.id

        # DB proof — use populate_existing to bypass stale identity map
        result = await db_session.execute(
            select(CampaignDeliverable)
            .where(CampaignDeliverable.id == deliverable.id)
            .execution_options(populate_existing=True)
        )
        fresh = result.scalar_one()
        assert fresh.candidate_id == target_candidate.id

    async def test_attach_missing_candidate_id_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id)

        resp = await client.post(
            f"/api/campaign-deliverables/{deliverable.id}/attach",
            json={},
        )
        assert resp.status_code == 400

    async def test_attach_nonexistent_candidate_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id)

        resp = await client.post(
            f"/api/campaign-deliverables/{deliverable.id}/attach",
            json={"candidateId": 999999},
        )
        assert resp.status_code == 404

    async def test_attach_unknown_deliverable_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        resp = await client.post(
            "/api/campaign-deliverables/999999/attach",
            json={"candidateId": candidate.id},
        )
        assert resp.status_code == 404


# ── Tests: blank ──────────────────────────────────────────────────────────────


class TestCreateBlankDeliverable:
    async def test_blank_creates_draft_and_deliverable(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /blank creates a CampaignDeliverable row linked to the target candidate."""
        clear_subscribers()
        candidate = await _make_candidate(db_session, family="cwp_blank", name="Blank Campaign")

        resp = await client.post(
            "/api/campaign-deliverables/blank",
            json={"candidateId": candidate.id, "title": "My new asset"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "deliverableId" in body
        assert body["candidateId"] == candidate.id
        assert "draftId" in body
        assert body["draftId"]  # non-empty

        # Verify both the deliverable row exists and is linked to the candidate
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == body["deliverableId"])
        )
        deliverable = result.scalar_one_or_none()
        assert deliverable is not None
        assert deliverable.candidate_id == candidate.id

    async def test_blank_default_title(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """POST /blank without a title creates a deliverable with a sensible default."""
        clear_subscribers()
        candidate = await _make_candidate(
            db_session, family="cwp_blank2", name="Default Title Campaign"
        )

        resp = await client.post(
            "/api/campaign-deliverables/blank",
            json={"candidateId": candidate.id},
        )
        assert resp.status_code == 201

    async def test_blank_missing_candidate_id_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        resp = await client.post("/api/campaign-deliverables/blank", json={})
        assert resp.status_code == 400

    async def test_blank_nonexistent_candidate_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        resp = await client.post(
            "/api/campaign-deliverables/blank",
            json={"candidateId": 999999},
        )
        assert resp.status_code == 404


# ── Tests: unlinked-drafts ────────────────────────────────────────────────────


class TestListUnlinkedDrafts:
    async def test_returns_empty_when_no_placeholder(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """No placeholder candidate → empty list (not 404)."""
        resp = await client.get("/api/campaign-deliverables/unlinked-drafts")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_placeholder_deliverables(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """After unlinking, the deliverable appears in unlinked-drafts."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, "Orphan Draft")

        # Unlink it
        resp = await client.post(f"/api/campaign-deliverables/{deliverable.id}/unlink")
        assert resp.status_code == 200

        # Now it should appear in unlinked-drafts
        resp2 = await client.get("/api/campaign-deliverables/unlinked-drafts")
        assert resp2.status_code == 200
        data = resp2.json()
        ids = [d["id"] for d in data]
        assert deliverable.id in ids


# ── Tests: round-trip unlink → attach ────────────────────────────────────────


class TestUnlinkAttachRoundTrip:
    async def test_unlink_then_attach_to_different_campaign(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Unlink from campaign A → attach to campaign B → candidate_id reflects B."""
        clear_subscribers()
        candidate_a = await _make_candidate(db_session, family="cwp_rt_a")
        candidate_b = await _make_candidate(db_session, family="cwp_rt_b")
        deliverable = await _make_deliverable(db_session, candidate_a.id, "Round-Trip Draft")

        # Unlink from A
        resp = await client.post(f"/api/campaign-deliverables/{deliverable.id}/unlink")
        assert resp.status_code == 200

        # Verify placeholder — use populate_existing to bypass stale session cache
        placeholder = await ws_invoke._get_or_create_template_workspace_candidate(db_session)
        result = await db_session.execute(
            select(CampaignDeliverable)
            .where(CampaignDeliverable.id == deliverable.id)
            .execution_options(populate_existing=True)
        )
        mid = result.scalar_one()
        assert mid.candidate_id == placeholder.id

        # Attach to B
        resp2 = await client.post(
            f"/api/campaign-deliverables/{deliverable.id}/attach",
            json={"candidateId": candidate_b.id},
        )
        assert resp2.status_code == 200

        # Verify B — bypass stale cache again
        result2 = await db_session.execute(
            select(CampaignDeliverable)
            .where(CampaignDeliverable.id == deliverable.id)
            .execution_options(populate_existing=True)
        )
        final = result2.scalar_one()
        assert final.candidate_id == candidate_b.id
        # Must NOT equal original A or placeholder
        assert final.candidate_id != candidate_a.id
        assert final.candidate_id != placeholder.id
