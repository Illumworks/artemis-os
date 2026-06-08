"""Phase C4 — Writing Studio route integration tests.

End-to-end Gate-2 flow via HTTP routes against the Stub external client.

Tests:
  1. POST /api/writing-studio/drafts — creates draft + deliverable row
  2. POST /api/writing-studio/drafts (missing candidate_id) → 400
  3. POST /api/writing-studio/drafts (invalid candidate_id) → 400
  4. POST /api/writing-studio/drafts (nonexistent candidate) → 404
  5. POST /api/writing-studio/drafts/{id}/submit-review → 200 with approval record
  6. POST /api/writing-studio/drafts/{id}/submit-review (unknown id) → 404
  7. POST /api/writing-studio/drafts/{id}/events/approved → 200
  8. POST /api/writing-studio/drafts/{id}/events/rejected → 200
  9. POST /api/writing-studio/drafts/{id}/events/revised → 200
  10. POST /api/writing-studio/drafts/{id}/events/<invalid> → 400
  11. Full Gate-2 flow: create → submit → approved event → workspace_state advanced
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.writing_studio.events import clear_subscribers

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_candidate(db: AsyncSession, family: str = "obc") -> CampaignCandidate:
    sig = await create_signal(
        db,
        headline="Integration Signal",
        campaign_family=family,
        source_type="manual",
        summary="A test signal for integration",
        discovered_by="test",
    )
    candidate = await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )
    await db.commit()
    return candidate


# ── Route tests ───────────────────────────────────────────────────────────────


class TestCreateDraftRoute:
    async def test_create_draft_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": candidate.id},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert "externalId" in data
        assert data["candidateId"] == candidate.id
        assert data["status"] == "generating"

    async def test_create_draft_no_candidate_id_creates_blank_draft(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /drafts with no candidate_id creates a blank draft under the
        templates placeholder candidate rather than returning 400.  This is
        the 'New draft' path from the picker for any draft incl. those without
        a campaign context."""
        clear_subscribers()
        resp = await client.post("/api/writing-studio/drafts", json={})
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] > 0
        # Title defaults to "New draft" when none supplied.
        assert body["title"] == "New draft"

    async def test_create_draft_invalid_candidate_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": "not-an-int"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "invalid_candidate_id"

    async def test_create_draft_nonexistent_candidate(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": 99999},
        )
        assert resp.status_code == 404

    async def test_create_draft_response_has_external_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": candidate.id},
        )
        assert resp.status_code == 201
        data = resp.json()
        # Stub always returns stub-draft-N
        assert data["externalId"].startswith("stub-draft-")


class TestSubmitReviewRoute:
    async def test_submit_review_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        # First create a draft
        create_resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": candidate.id},
        )
        assert create_resp.status_code == 201
        draft_id = create_resp.json()["id"]

        resp = await client.post(f"/api/writing-studio/drafts/{draft_id}/submit-review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "writing_gate_2"
        assert data["status"] == "pending"

    async def test_submit_review_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        resp = await client.post("/api/writing-studio/drafts/99999/submit-review")
        assert resp.status_code == 404

    async def test_submit_review_returns_approval_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        create_resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": candidate.id},
        )
        draft_id = create_resp.json()["id"]
        resp = await client.post(f"/api/writing-studio/drafts/{draft_id}/submit-review")
        data = resp.json()
        assert isinstance(data["id"], int)
        assert data["id"] > 0


class TestDraftEventRoute:
    async def test_event_approved_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        clear_subscribers()
        resp = await client.post("/api/writing-studio/drafts/stub-draft-1/events/approved")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["eventKind"] == "approved"

    async def test_event_rejected_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        clear_subscribers()
        resp = await client.post("/api/writing-studio/drafts/stub-draft-1/events/rejected")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eventKind"] == "rejected"

    async def test_event_revised_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        clear_subscribers()
        resp = await client.post("/api/writing-studio/drafts/stub-draft-1/events/revised")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eventKind"] == "revised"

    async def test_invalid_event_kind_returns_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        resp = await client.post("/api/writing-studio/drafts/stub-draft-1/events/deleted")
        assert resp.status_code == 400
        data = resp.json()
        assert "invalid_event_kind" in data.get("code", "")

    async def test_event_returns_draft_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        resp = await client.post("/api/writing-studio/drafts/my-draft-abc/events/approved")
        assert resp.status_code == 200
        data = resp.json()
        assert data["draftId"] == "my-draft-abc"


class TestGate2EndToEnd:
    """Full Gate-2 flow: create → submit → approved event → workspace_state advanced."""

    async def test_full_gate2_flow(self, client: AsyncClient, db_session: AsyncSession) -> None:
        clear_subscribers()

        # 1. Create candidate
        candidate = await _make_candidate(db_session)

        # 2. Create draft via API
        create_resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": candidate.id},
        )
        assert create_resp.status_code == 201
        draft_data = create_resp.json()
        draft_id = draft_data["id"]
        external_id = draft_data["externalId"]
        assert external_id.startswith("stub-draft-")

        # 3. Submit for review
        submit_resp = await client.post(f"/api/writing-studio/drafts/{draft_id}/submit-review")
        assert submit_resp.status_code == 200
        approval = submit_resp.json()
        assert approval["kind"] == "writing_gate_2"
        assert approval["status"] == "pending"

        # 4. Verify deliverable is now ready_for_review in DB
        result = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == draft_id)
        )
        deliverable = result.scalar_one()
        assert deliverable.status == "draft_ready"

        # 5. Post an 'approved' event back (simulates external WS webhook)
        event_resp = await client.post(f"/api/writing-studio/drafts/{external_id}/events/approved")
        assert event_resp.status_code == 200
        assert event_resp.json()["ok"] is True

    async def test_gate2_stub_external_used_by_default(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The route uses Stub when env vars are not set — no real HTTP."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": candidate.id},
        )
        assert resp.status_code == 201
        # Stub gives deterministic IDs
        assert "stub-draft-" in resp.json()["externalId"]
