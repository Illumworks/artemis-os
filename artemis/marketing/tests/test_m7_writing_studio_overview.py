"""M7 — Writing Studio overview aggregator + draft CRUD route tests.

Covers:
  GET  /api/writing-studio/overview          — happy path, empty-DB case
  GET  /api/writing-studio/drafts            — happy path, pagination, folder filter
  GET  /api/writing-studio/drafts/{id}       — happy path, not-found
  PUT  /api/writing-studio/drafts/{id}       — rename, content update, folder move,
                                               not-found, bad body
  DELETE /api/writing-studio/drafts/{id}     — soft-archive, row still in DB, not-found

Existing C4 routes are covered by test_c4_route_integration.py — not repeated here.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.writing_studio.events import clear_subscribers
from artemis.writing_rules import repository as wr_repo
from artemis.writing_rules.models import WritingProfile, WritingTrainingCandidate

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_candidate(db: AsyncSession, family: str = "obc") -> CampaignCandidate:
    sig = await create_signal(
        db,
        headline="M7 Test Signal",
        campaign_family=family,
        source_type="manual",
        summary="M7 integration test",
        discovered_by="test",
    )
    candidate = await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )
    await db.commit()
    return candidate


async def _make_deliverable(
    db: AsyncSession,
    candidate_id: int,
    *,
    title: str = "Test Draft",
    folder_id: int | None = None,
    archived: bool = False,
) -> CampaignDeliverable:
    """Create a CampaignDeliverable row for tests.

    Soft-archived rows set metadata.archived = True (not the status column —
    the status CHECK constraint doesn't allow 'archived').
    """
    meta: dict[str, object] = {
        "title": title,
        "externalDraftId": "stub-draft-test",
    }
    if folder_id is not None:
        meta["folder_id"] = folder_id
    if archived:
        meta["archived"] = True
    deliverable = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id="stub-draft-test",
        campaign_id=str(candidate_id),
        status="generating",
        deliverable_metadata=meta,
    )
    db.add(deliverable)
    await db.flush()
    await db.refresh(deliverable)
    await db.commit()
    return deliverable


# ── Overview ──────────────────────────────────────────────────────────────────


class TestOverview:
    async def test_overview_empty_db_returns_200(self, client: AsyncClient) -> None:
        """Empty DB: all keys present, all lists empty."""
        clear_subscribers()
        resp = await client.get("/api/writing-studio/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert "drafts" in body
        assert "folders" in body
        assert "campaigns" in body
        assert "rules" in body
        assert "examples" in body
        assert "sources" in body
        assert "profiles" in body
        assert "activeProfile" in body
        assert "active_profile" in body
        assert "trainingCandidates" in body
        assert "training_candidates" in body
        assert "sync_config" in body
        assert body["trainingCandidates"] == []
        assert body["training_candidates"] == []
        assert body["sync_config"] == {}

    async def test_overview_includes_draft(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A deliverable shows up in overview.drafts."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        await _make_deliverable(db_session, candidate.id, title="Overview Draft")

        resp = await client.get("/api/writing-studio/overview")
        assert resp.status_code == 200
        body = resp.json()
        titles = [d["title"] for d in body["drafts"]]
        assert "Overview Draft" in titles

    async def test_overview_exposes_active_profile_and_training_candidates(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Overview returns the camelCase keys the frontend consumes."""
        clear_subscribers()
        profile = WritingProfile(
            name="District Voice",
            status="active",
            default_model_provider="claude-code",
            default_model_id="claude-opus",
        )
        db_session.add(profile)
        await db_session.flush()
        await db_session.refresh(profile)
        candidate = WritingTrainingCandidate(
            profile_id=profile.id,
            draft_id=None,
            candidate_type="rule",
            proposed_text="Lead with district outcomes.",
            rationale="Observed repeatedly in approved drafts.",
            status="proposed",
        )
        db_session.add(candidate)
        await db_session.commit()

        resp = await client.get("/api/writing-studio/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["activeProfile"]["name"] == "District Voice"
        assert body["active_profile"]["name"] == "District Voice"
        assert body["trainingCandidates"][0]["proposed_text"] == "Lead with district outcomes."
        assert body["training_candidates"][0]["proposed_text"] == "Lead with district outcomes."

    async def test_overview_excludes_archived(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Soft-archived drafts do not appear in overview.drafts."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        await _make_deliverable(db_session, candidate.id, archived=True, title="Gone Draft")

        resp = await client.get("/api/writing-studio/overview")
        assert resp.status_code == 200
        body = resp.json()
        titles = [d["title"] for d in body["drafts"]]
        assert "Gone Draft" not in titles

    async def test_overview_draft_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Each draft row has the fields the frontend reads."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        await _make_deliverable(db_session, candidate.id)

        resp = await client.get("/api/writing-studio/overview")
        assert resp.status_code == 200
        drafts = resp.json()["drafts"]
        assert len(drafts) >= 1
        draft = drafts[0]
        for field in (
            "id",
            "title",
            "status",
            "asset_type",
            "campaign_id",
            "folder_id",
            "folder_name",
            "updated_at",
            "metadata",
        ):
            assert field in draft, f"Missing field: {field}"


# ── Draft list ────────────────────────────────────────────────────────────────


class TestDraftList:
    async def test_list_drafts_empty(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Empty DB (after TRUNCATE): drafts list is empty."""
        resp = await client.get("/api/writing-studio/drafts")
        assert resp.status_code == 200
        body = resp.json()
        assert "drafts" in body
        assert body["drafts"] == []

    async def test_list_drafts_returns_items(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        await _make_deliverable(db_session, candidate.id, title="List Draft")

        resp = await client.get("/api/writing-studio/drafts")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["drafts"]) >= 1

    async def test_list_drafts_excludes_archived(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        await _make_deliverable(db_session, candidate.id, archived=True, title="Hidden")

        resp = await client.get("/api/writing-studio/drafts")
        assert resp.status_code == 200
        titles = [d["title"] for d in resp.json()["drafts"]]
        assert "Hidden" not in titles

    async def test_list_drafts_folder_filter(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        await _make_deliverable(db_session, candidate.id, title="In Folder", folder_id=42)
        await _make_deliverable(db_session, candidate.id, title="No Folder")

        resp = await client.get("/api/writing-studio/drafts?folder_id=42")
        assert resp.status_code == 200
        titles = [d["title"] for d in resp.json()["drafts"]]
        assert "In Folder" in titles
        assert "No Folder" not in titles


# ── Draft detail ──────────────────────────────────────────────────────────────


class TestDraftDetail:
    async def test_get_draft_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, title="Detail Draft")

        resp = await client.get(f"/api/writing-studio/drafts/{deliverable.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == deliverable.id
        assert body["title"] == "Detail Draft"
        assert "versions" in body
        assert "threadMessages" in body
        assert "content" in body
        assert isinstance(body["threadMessages"], list)

    async def test_get_draft_thread_messages_include_text_alias(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Persisted thread messages expose both text and content for frontend compat."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, title="Thread Alias Draft")
        await wr_repo.create_thread_message(
            db_session,
            deliverable.id,
            "assistant",
            "Rendered reply body",
            label="Writing partner",
        )
        await db_session.commit()

        resp = await client.get(f"/api/writing-studio/drafts/{deliverable.id}")
        assert resp.status_code == 200
        message = resp.json()["threadMessages"][0]
        assert message["text"] == "Rendered reply body"
        assert message["content"] == "Rendered reply body"

    async def test_get_draft_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/writing-studio/drafts/999999")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "draft_not_found"

    async def test_get_draft_content_from_versions(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """content field is populated from metadata.versions[0].content."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = CampaignDeliverable(
            candidate_id=candidate.id,
            deliverable_id="stub-v-test",
            campaign_id=str(candidate.id),
            status="generating",
            deliverable_metadata={
                "title": "Versioned Draft",
                "versions": [
                    {"id": "v1", "version_number": 1, "content": "Hello world"},
                ],
            },
        )
        db_session.add(deliverable)
        await db_session.flush()
        await db_session.refresh(deliverable)
        await db_session.commit()

        resp = await client.get(f"/api/writing-studio/drafts/{deliverable.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "Hello world"
        assert len(body["versions"]) == 1


# ── Draft update (PUT) ────────────────────────────────────────────────────────


class TestDraftUpdate:
    async def test_rename_draft(self, client: AsyncClient, db_session: AsyncSession) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, title="Old Title")

        resp = await client.put(
            f"/api/writing-studio/drafts/{deliverable.id}",
            json={"title": "Renamed"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Renamed"

        # Verify persisted
        check = await client.get(f"/api/writing-studio/drafts/{deliverable.id}")
        assert check.json()["title"] == "Renamed"

    async def test_update_draft_not_found(self, client: AsyncClient) -> None:
        resp = await client.put(
            "/api/writing-studio/drafts/999999",
            json={"title": "Ghost"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "draft_not_found"

    async def test_update_draft_empty_title_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id)

        resp = await client.put(
            f"/api/writing-studio/drafts/{deliverable.id}",
            json={"title": ""},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "invalid_title"

    async def test_update_draft_content_creates_version(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id)

        resp = await client.put(
            f"/api/writing-studio/drafts/{deliverable.id}",
            json={"content": "New content body"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "New content body"
        assert len(body["versions"]) == 1
        assert body["versions"][0]["content"] == "New content body"

    async def test_update_draft_content_appends_lossless_version_history(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = CampaignDeliverable(
            candidate_id=candidate.id,
            deliverable_id="stub-existing-version",
            campaign_id=str(candidate.id),
            status="generating",
            deliverable_metadata={
                "title": "Versioned Draft",
                "versions": [
                    {
                        "id": "v1",
                        "version_number": 1,
                        "content": "Original body",
                        "created_at": "2026-06-01T12:00:00+00:00",
                    }
                ],
            },
        )
        db_session.add(deliverable)
        await db_session.flush()
        await db_session.refresh(deliverable)
        await db_session.commit()
        draft_id = deliverable.id

        resp = await client.put(
            f"/api/writing-studio/drafts/{draft_id}",
            json={
                "content": "Updated body",
                "changeNote": "Manual save from Draft Canvas",
                "source": "manual",
                "folderId": 9,
                "campaignId": "cmp-77",
                "audience": "District leaders",
                "channel": "email",
                "metadata": {"brief": "Carry the revised CTA forward."},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "Updated body"
        assert len(body["versions"]) == 2
        assert body["versions"][0]["content"] == "Updated body"
        assert body["versions"][0]["change_note"] == "Manual save from Draft Canvas"
        assert body["versions"][0]["source"] == "manual"
        assert body["versions"][1]["content"] == "Original body"
        assert body["folder_id"] == 9
        assert body["campaign_id"] == "cmp-77"

        db_session.expire_all()
        refreshed = await db_session.get(CampaignDeliverable, draft_id)
        assert refreshed is not None
        assert refreshed.campaign_id == "cmp-77"
        assert isinstance(refreshed.deliverable_metadata, dict)
        assert refreshed.deliverable_metadata["audience"] == "District leaders"
        assert refreshed.deliverable_metadata["channel"] == "email"
        assert refreshed.deliverable_metadata["brief"] == "Carry the revised CTA forward."
        versions = refreshed.deliverable_metadata["versions"]
        assert len(versions) == 2
        assert versions[0]["content"] == "Updated body"
        assert versions[1]["content"] == "Original body"

    async def test_update_draft_folder_move(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id)

        resp = await client.put(
            f"/api/writing-studio/drafts/{deliverable.id}",
            json={"folder_id": 7},
        )
        assert resp.status_code == 200
        assert resp.json()["folder_id"] == 7


# ── Draft delete (soft-archive) ───────────────────────────────────────────────


class TestDraftDelete:
    async def test_delete_draft_soft_archives(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, title="To Delete")

        resp = await client.delete(f"/api/writing-studio/drafts/{deliverable.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["archived"] is True

    async def test_delete_draft_row_still_in_db(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Lossless memory rule: the row must still exist after soft-delete."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id)
        draft_id = deliverable.id

        await client.delete(f"/api/writing-studio/drafts/{draft_id}")

        # Expire the session cache so we read the committed state from DB.
        db_session.expire_all()
        row = await db_session.get(CampaignDeliverable, draft_id)
        assert row is not None
        assert isinstance(row.deliverable_metadata, dict)
        assert row.deliverable_metadata.get("archived") is True

    async def test_delete_draft_excluded_from_overview(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        clear_subscribers()
        candidate = await _make_candidate(db_session)
        deliverable = await _make_deliverable(db_session, candidate.id, title="Will Archive")

        await client.delete(f"/api/writing-studio/drafts/{deliverable.id}")

        overview = await client.get("/api/writing-studio/overview")
        titles = [d["title"] for d in overview.json()["drafts"]]
        assert "Will Archive" not in titles

    async def test_delete_draft_not_found(self, client: AsyncClient) -> None:
        resp = await client.delete("/api/writing-studio/drafts/999999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "draft_not_found"

    async def test_delete_via_c4_create_then_delete(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """End-to-end: C4 create → M7 delete → verify archived in DB."""
        clear_subscribers()
        candidate = await _make_candidate(db_session)

        create_resp = await client.post(
            "/api/writing-studio/drafts",
            json={"candidate_id": candidate.id},
        )
        assert create_resp.status_code == 201
        draft_id = create_resp.json()["id"]

        del_resp = await client.delete(f"/api/writing-studio/drafts/{draft_id}")
        assert del_resp.status_code == 200

        row = await db_session.execute(
            select(CampaignDeliverable).where(CampaignDeliverable.id == draft_id)
        )
        row_obj = row.scalar_one()
        assert isinstance(row_obj.deliverable_metadata, dict)
        assert row_obj.deliverable_metadata.get("archived") is True
