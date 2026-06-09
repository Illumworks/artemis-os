"""Tests for WritingTrainingCandidate model, repository, and endpoints.

Phase 3 Piece B — writing learning loop.

Test plan:
  1. List/create round-trip via repository.
  2. Decide → reject.
  3. Decide → approve → promote (rule).
  4. Decide → approve → promote (example).
  5. Compose persistence: POST compose → candidate persisted.
  6. Endpoint contract: GET list, POST create, POST decision.
  7. Idempotency / lossless: re-approve returns existing row, no duplicate.
  8. Failure isolation in compose: persist failure → compose still returns 200.

Run:
    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test \
    uv run pytest artemis/writing_rules/tests/test_training_candidates.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.writing_rules import repository as wr_repo
from artemis.writing_rules.models import WritingExample, WritingRule

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_deliverable(db: AsyncSession, family: str = "obc") -> CampaignDeliverable:
    """Create a minimal CampaignDeliverable to use as draft_id FK target."""
    sig = await create_signal(
        db,
        headline="Training candidate test signal",
        campaign_family=family,
        source_type="manual",
        summary="Test signal for training candidates",
        discovered_by="test",
    )
    candidate: CampaignCandidate = await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )
    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="stub-training-test",
        campaign_id=str(candidate.id),
        status="generating",
        deliverable_metadata={"title": "Training Test Draft"},
    )
    db.add(deliverable)
    await db.flush()
    await db.refresh(deliverable)
    await db.commit()
    return deliverable


# ── 1. List / create round-trip ───────────────────────────────────────────────


class TestListCreateRoundTrip:
    async def test_create_three_and_list_proposed(self, db_session: AsyncSession) -> None:
        """Three candidates created → list(status='proposed') returns 3, newest first."""
        for i in range(3):
            await wr_repo.create_training_candidate(
                db_session,
                profile_id=None,
                draft_id=None,
                proposed_text=f"Rule proposal number {i} about voice patterns",
                status="proposed",
            )
        await db_session.commit()

        rows = await wr_repo.list_training_candidates(db_session, status="proposed")
        assert len(rows) == 3
        # Newest first: ids should be in descending order.
        ids = [r.id for r in rows]
        assert ids == sorted(ids, reverse=True)

    async def test_list_approved_returns_zero(self, db_session: AsyncSession) -> None:
        """No approved candidates returns empty list."""
        await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="This is a proposed rule about voice patterns",
            status="proposed",
        )
        await db_session.commit()

        rows = await wr_repo.list_training_candidates(db_session, status="approved")
        assert rows == []

    async def test_no_filter_returns_all(self, db_session: AsyncSession) -> None:
        """list_training_candidates with no status filter returns all."""
        await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="Proposed rule about short sentences",
            status="proposed",
        )
        await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="Another rule about active voice patterns",
            status="approved",
        )
        await db_session.commit()

        rows = await wr_repo.list_training_candidates(db_session)
        assert len(rows) == 2


# ── 2. Decide → reject ────────────────────────────────────────────────────────


class TestDecideReject:
    async def test_reject_sets_decided_at_and_status(self, db_session: AsyncSession) -> None:
        """Rejecting a candidate sets status='rejected' and decided_at."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="A rule about consistent paragraph length in emails",
            status="proposed",
        )
        await db_session.commit()
        assert candidate.decided_at is None

        updated = await wr_repo.decide_training_candidate(
            db_session, candidate.id, status="rejected"
        )
        await db_session.commit()

        assert updated is not None
        assert updated.status == "rejected"
        assert updated.decided_at is not None

    async def test_promote_returns_none_for_rejected_candidate(
        self, db_session: AsyncSession
    ) -> None:
        """promote_training_candidate returns None for a non-approved candidate."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="A rule about consistent paragraph length in emails",
            status="proposed",
        )
        await db_session.commit()

        result = await wr_repo.promote_training_candidate(db_session, candidate)
        assert result is None

    async def test_promote_returns_none_for_proposed_candidate(
        self, db_session: AsyncSession
    ) -> None:
        """promote_training_candidate returns None for a still-proposed candidate."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="A rule about consistent paragraph length in emails",
            status="proposed",
        )
        await db_session.commit()

        # Do NOT call decide — candidate is still proposed.
        result = await wr_repo.promote_training_candidate(db_session, candidate)
        assert result is None


# ── 3. Decide → approve → promote (rule) ─────────────────────────────────────


class TestApprovePromoteRule:
    async def test_approve_promotes_to_writing_rule(self, db_session: AsyncSession) -> None:
        """Approving a candidate_type='rule' creates a WritingRule."""
        long_text = (
            "Always open with the student outcome and how Amira's adaptive learning supports it"
        )
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            candidate_type="rule",
            proposed_text=long_text,
            status="proposed",
        )
        await db_session.commit()

        approved = await wr_repo.decide_training_candidate(
            db_session, candidate.id, status="approved"
        )
        assert approved is not None

        promoted = await wr_repo.promote_training_candidate(db_session, approved)
        await db_session.commit()

        assert promoted is not None
        assert isinstance(promoted, WritingRule)
        assert promoted.body == long_text
        # Title ≤ 72 chars
        assert len(promoted.title) <= 73  # 72 + possible ellipsis
        assert promoted.title == long_text[:72] + "…"
        assert promoted.source_candidate_id == candidate.id
        assert promoted.status == "active"

    async def test_rule_type_maps_to_voice_for_generic_rule(self, db_session: AsyncSession) -> None:
        """candidate_type='rule' maps to rule_type='voice' on the WritingRule."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            candidate_type="rule",
            proposed_text="Keep paragraphs short and tactical for marketing emails",
            status="proposed",
        )
        await db_session.commit()

        await wr_repo.decide_training_candidate(db_session, candidate.id, status="approved")
        promoted = await wr_repo.promote_training_candidate(
            db_session,
            await wr_repo.get_training_candidate(db_session, candidate.id),  # type: ignore[arg-type]
        )
        await db_session.commit()

        assert isinstance(promoted, WritingRule)
        assert promoted.rule_type == "voice"

    async def test_candidate_type_becomes_rule_type_for_non_rule(
        self, db_session: AsyncSession
    ) -> None:
        """candidate_type='cta' → rule_type='cta' on the promoted WritingRule."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            candidate_type="cta",
            proposed_text="Always end with a clear single call-to-action that drives enrollment",
            status="proposed",
        )
        await db_session.commit()

        await wr_repo.decide_training_candidate(db_session, candidate.id, status="approved")
        promoted = await wr_repo.promote_training_candidate(
            db_session,
            await wr_repo.get_training_candidate(db_session, candidate.id),  # type: ignore[arg-type]
        )
        await db_session.commit()

        assert isinstance(promoted, WritingRule)
        assert promoted.rule_type == "cta"


# ── 4. Decide → approve → promote (example) ──────────────────────────────────


class TestApprovePromoteExample:
    async def test_approve_promotes_to_writing_example(self, db_session: AsyncSession) -> None:
        """Approving a candidate_type='example' creates a WritingExample."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            candidate_type="example",
            proposed_text="Amira's reading engine adapts to every student, meeting them where they are.",
            status="proposed",
        )
        await db_session.commit()

        await wr_repo.decide_training_candidate(db_session, candidate.id, status="approved")
        approved = await wr_repo.get_training_candidate(db_session, candidate.id)
        assert approved is not None

        promoted = await wr_repo.promote_training_candidate(db_session, approved)
        await db_session.commit()

        assert promoted is not None
        assert isinstance(promoted, WritingExample)
        assert promoted.body == candidate.proposed_text
        assert promoted.example_type == "learned"
        assert promoted.source_candidate_id == candidate.id


# ── 5. Compose persistence ────────────────────────────────────────────────────


class TestComposePersistence:
    async def test_compose_persists_proposed_learnings(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST compose with AI response containing 'Proposed learning:' persists a candidate."""
        # Create a deliverable to compose against.
        deliverable = await _make_deliverable(db_session)

        compose_response_text = (
            "Here is a revised opening paragraph that leads with student outcomes.\n\n"
            "Proposed learning: keep paragraphs short and tactical."
        )

        # Stub the adapter so no real API call is made.
        # resolve_adapter and run_turn are imported locally inside compose_draft,
        # so we patch at the source module level.
        from artemis.agent.types import Message, TextBlock, Usage

        class _FakeResult:
            messages = [
                Message(
                    role="assistant",
                    content=[TextBlock(text=compose_response_text)],
                )
            ]
            usage = Usage(input_tokens=10, output_tokens=20)

        with (
            patch("artemis.providers.resolver.resolve_adapter") as mock_resolver,
            patch(
                "artemis.agent.run_turn",
                new_callable=AsyncMock,
            ) as mock_run_turn,
        ):
            mock_adapter = AsyncMock()
            mock_resolver.return_value = mock_adapter
            mock_run_turn.return_value = _FakeResult()

            resp = await client.post(
                f"/api/writing-studio/drafts/{deliverable.id}/compose",
                json={"request": "Revise the opening paragraph."},
                headers={"X-Api-Token": "test-token"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "Proposed learning:" not in data["responseText"]
        assert len(data["proposedCandidates"]) == 1
        candidate_payload = data["proposedCandidates"][0]
        # Persisted candidates have id and created_at.
        assert candidate_payload["id"] is not None
        assert candidate_payload["proposed_text"] == "keep paragraphs short and tactical."
        assert candidate_payload["draft_id"] == deliverable.id

        # Verify the DB row was written.
        rows = await wr_repo.list_training_candidates(db_session, status="proposed")
        assert len(rows) == 1
        assert rows[0].proposed_text == "keep paragraphs short and tactical."
        assert rows[0].draft_id == deliverable.id

        messages = await wr_repo.list_thread_messages_for_draft(db_session, deliverable.id)
        assistant_messages = [message for message in messages if message.role == "assistant"]
        assert len(assistant_messages) == 1
        assert "Proposed learning:" not in assistant_messages[0].content


# ── 6. Endpoint contract ──────────────────────────────────────────────────────


class TestEndpointContract:
    async def test_get_training_candidates_returns_array(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /training-candidates returns {"training_candidates": [...]}."""
        await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="Short paragraphs improve scan-ability in marketing emails",
            status="proposed",
        )
        await db_session.commit()

        resp = await client.get(
            "/api/writing-studio/training-candidates",
            headers={"X-Api-Token": "test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "training_candidates" in data
        assert len(data["training_candidates"]) == 1

    async def test_get_training_candidates_status_filter(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /training-candidates?status=proposed returns only proposed."""
        await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="Short paragraphs improve scan-ability in marketing emails",
            status="proposed",
        )
        await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="Active voice keeps the message energetic and clear for readers",
            status="approved",
        )
        await db_session.commit()

        resp = await client.get(
            "/api/writing-studio/training-candidates?status=proposed",
            headers={"X-Api-Token": "test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["training_candidates"]) == 1
        assert data["training_candidates"][0]["status"] == "proposed"

    async def test_post_training_candidate_validates_min_length(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /training-candidates rejects proposedText shorter than 10 chars."""
        resp = await client.post(
            "/api/writing-studio/training-candidates",
            json={"proposedText": "too short"},
            headers={"X-Api-Token": "test-token"},
        )
        assert resp.status_code == 400

    async def test_post_training_candidate_accepts_valid_payload(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /training-candidates returns 201 with persisted candidate."""
        resp = await client.post(
            "/api/writing-studio/training-candidates",
            json={"proposedText": "Use short punchy sentences in all outreach emails."},
            headers={"X-Api-Token": "test-token"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] is not None
        assert data["status"] == "proposed"
        assert data["proposed_text"] == "Use short punchy sentences in all outreach emails."

    async def test_post_decision_approve_promotes(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /training-candidates/{id}/decision with approved promotes the candidate."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            candidate_type="rule",
            proposed_text="Always lead with the student outcome in the opening line of emails",
            status="proposed",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/writing-studio/training-candidates/{candidate.id}/decision",
            json={"status": "approved"},
            headers={"X-Api-Token": "test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["promoted"] is not None
        assert data["promoted"]["kind"] == "rule"
        assert data["promoted"]["id"] is not None

    async def test_post_decision_reject_does_not_promote(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /training-candidates/{id}/decision with rejected → no promoted."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="Use colloquial language that might confuse parents",
            status="proposed",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/writing-studio/training-candidates/{candidate.id}/decision",
            json={"status": "rejected"},
            headers={"X-Api-Token": "test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["promoted"] is None

    async def test_post_decision_invalid_status_400(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /training-candidates/{id}/decision with invalid status → 400."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="Some proposal to validate status logic carefully",
            status="proposed",
        )
        await db_session.commit()

        resp = await client.post(
            f"/api/writing-studio/training-candidates/{candidate.id}/decision",
            json={"status": "pending"},
            headers={"X-Api-Token": "test-token"},
        )
        assert resp.status_code == 400

    async def test_post_decision_missing_candidate_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """POST /training-candidates/{id}/decision with nonexistent id → 404."""
        resp = await client.post(
            "/api/writing-studio/training-candidates/999999/decision",
            json={"status": "approved"},
            headers={"X-Api-Token": "test-token"},
        )
        assert resp.status_code == 404


# ── 7. Idempotency / lossless ─────────────────────────────────────────────────


class TestIdempotency:
    async def test_re_approve_returns_existing_rule(self, db_session: AsyncSession) -> None:
        """Re-approving same candidate (same profile/title) returns the existing rule."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            candidate_type="rule",
            proposed_text="Always open with the student outcome in every marketing email sent",
            status="approved",
        )
        await db_session.commit()

        # First promotion.
        promoted1 = await wr_repo.promote_training_candidate(db_session, candidate)
        await db_session.commit()
        assert isinstance(promoted1, WritingRule)

        # Second promotion — same (profile_id, rule_type, title) → returns existing.
        promoted2 = await wr_repo.promote_training_candidate(db_session, candidate)
        await db_session.commit()
        assert isinstance(promoted2, WritingRule)
        assert promoted1.id == promoted2.id

        # Verify only one rule was created.
        rules = await wr_repo.list_rules(db_session, include_archived=True)
        assert len(rules) == 1

    async def test_rejected_candidate_not_deleted(self, db_session: AsyncSession) -> None:
        """Rejected candidates persist in DB (lossless memory rule)."""
        candidate = await wr_repo.create_training_candidate(
            db_session,
            profile_id=None,
            draft_id=None,
            proposed_text="Some questionable writing advice that should be rejected",
            status="proposed",
        )
        await db_session.commit()

        await wr_repo.decide_training_candidate(db_session, candidate.id, status="rejected")
        await db_session.commit()

        # Row still exists.
        fetched = await wr_repo.get_training_candidate(db_session, candidate.id)
        assert fetched is not None
        assert fetched.status == "rejected"


# ── 8. Failure isolation in compose ──────────────────────────────────────────


class TestComposeFailureIsolation:
    async def test_compose_returns_200_even_if_persist_fails(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """If create_training_candidate raises, compose still returns 200."""
        deliverable = await _make_deliverable(db_session, family="sel")

        compose_response_text = (
            "Here is your revised draft.\n\n"
            "Proposed learning: use active voice throughout all email drafts."
        )

        from artemis.agent.types import Message, TextBlock, Usage

        class _FakeResult:
            messages = [
                Message(
                    role="assistant",
                    content=[TextBlock(text=compose_response_text)],
                )
            ]
            usage = Usage(input_tokens=10, output_tokens=20)

        import logging

        # resolve_adapter and run_turn are imported locally inside compose_draft,
        # so we patch at the source module level.
        with (
            patch("artemis.providers.resolver.resolve_adapter") as mock_resolver,
            patch(
                "artemis.agent.run_turn",
                new_callable=AsyncMock,
            ) as mock_run_turn,
            patch(
                "artemis.writing_rules.repository.create_training_candidate",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB error injected for test"),
            ),
            patch.object(
                logging.getLogger("artemis.marketing.routes.writing_studio"),
                "warning",
            ) as mock_warn,
        ):
            mock_adapter = AsyncMock()
            mock_resolver.return_value = mock_adapter
            mock_run_turn.return_value = _FakeResult()

            resp = await client.post(
                f"/api/writing-studio/drafts/{deliverable.id}/compose",
                json={"request": "Revise the opening."},
                headers={"X-Api-Token": "test-token"},
            )

        # (a) compose returns 200
        assert resp.status_code == 200
        data = resp.json()

        # (b) response still includes in-memory proposedCandidates
        assert len(data["proposedCandidates"]) == 1
        assert (
            "use active voice throughout all email drafts"
            in data["proposedCandidates"][0]["proposed_text"]
        )

        # (c) warning was logged
        assert mock_warn.called
