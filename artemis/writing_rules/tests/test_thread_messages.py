"""Unit tests for WritingDraftThreadMessage model + repository helpers.

Verifies:
  - create_thread_message persists a row and returns it with a PK + created_at.
  - list_thread_messages_for_draft returns messages in chronological / id order.
  - FK integrity: inserting a message for a non-existent draft_id raises.
  - Multiple drafts don't bleed into each other's thread.
  - Optional JSONB columns (trace, engine, prompt, attachments) round-trip.

Run:
    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test \
    uv run pytest artemis/writing_rules/tests/test_thread_messages.py -q
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.writing_rules.repository import (
    create_thread_message,
    list_thread_messages_for_draft,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_deliverable(db: AsyncSession, family: str = "obc") -> CampaignDeliverable:
    """Create a minimal CampaignDeliverable to use as draft_id FK target."""
    sig = await create_signal(
        db,
        headline="Thread test signal",
        campaign_family=family,
        source_type="manual",
        summary="Thread test",
        discovered_by="test",
    )
    candidate: CampaignCandidate = await create_campaign_candidate_from_signal(
        db, signal_id=sig.id, ruleset_version_tag="v1"
    )
    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="stub-thread-test",
        campaign_id=str(candidate.id),
        status="generating",
        deliverable_metadata={"title": "Thread Test Draft"},
    )
    db.add(deliverable)
    await db.flush()
    await db.refresh(deliverable)
    await db.commit()
    return deliverable


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCreateThreadMessage:
    async def test_create_user_message_returns_row(self, db_session: AsyncSession) -> None:
        """create_thread_message returns a persisted row with PK and created_at."""
        deliverable = await _make_deliverable(db_session)
        msg = await create_thread_message(
            db_session,
            draft_id=deliverable.id,
            role="user",
            content="Help me tighten this paragraph.",
        )
        await db_session.commit()

        assert msg.id is not None
        assert msg.id > 0
        assert msg.draft_id == deliverable.id
        assert msg.role == "user"
        assert msg.content == "Help me tighten this paragraph."
        assert msg.created_at is not None
        assert msg.label is None
        assert msg.attachments is None
        assert msg.trace is None
        assert msg.engine is None
        assert msg.prompt is None

    async def test_create_assistant_message_with_all_fields(self, db_session: AsyncSession) -> None:
        """Assistant messages can carry label, trace, engine, and prompt JSONB."""
        deliverable = await _make_deliverable(db_session)
        trace_payload = {"rules": [{"id": 1, "title": "Be concise"}], "draft": {"id": 42}}
        engine_payload = {
            "providerId": "claude-code",
            "modelId": "",
            "resolvedModelId": "claude-opus-4-5",
            "sessionId": "abc123",
        }
        prompt_payload = {
            "systemPrompt": "You are Artemis Writing Studio…",
            "userPrompt": "Help me tighten this paragraph.",
        }

        msg = await create_thread_message(
            db_session,
            draft_id=deliverable.id,
            role="assistant",
            content="Here is a tightened version…",
            label="Artemis",
            trace=trace_payload,
            engine=engine_payload,
            prompt=prompt_payload,
        )
        await db_session.commit()

        assert msg.role == "assistant"
        assert msg.label == "Artemis"
        assert msg.content == "Here is a tightened version…"
        assert msg.trace == trace_payload
        assert msg.engine == engine_payload
        assert msg.prompt == prompt_payload
        assert msg.attachments is None

    async def test_create_user_message_with_attachments(self, db_session: AsyncSession) -> None:
        """User messages can carry an attachments JSONB array."""
        deliverable = await _make_deliverable(db_session)
        attachments_payload = [{"name": "brand_guide.pdf", "type": "file", "text": "…excerpt…"}]

        msg = await create_thread_message(
            db_session,
            draft_id=deliverable.id,
            role="user",
            content="See the attached brand guide.",
            label="System",
            attachments=attachments_payload,
        )
        await db_session.commit()

        assert msg.attachments == attachments_payload

    async def test_fk_violation_raises_on_nonexistent_draft(self, db_session: AsyncSession) -> None:
        """Inserting a message for a non-existent draft_id raises an IntegrityError."""
        with pytest.raises(IntegrityError):
            await create_thread_message(
                db_session,
                draft_id=999_999_999,
                role="user",
                content="This should fail.",
            )
            await db_session.flush()


class TestListThreadMessagesForDraft:
    async def test_empty_draft_returns_empty_list(self, db_session: AsyncSession) -> None:
        """A draft with no messages returns an empty list."""
        deliverable = await _make_deliverable(db_session)
        messages = await list_thread_messages_for_draft(db_session, deliverable.id)
        assert messages == []

    async def test_returns_messages_in_chronological_order(self, db_session: AsyncSession) -> None:
        """Messages are returned ordered by created_at ASC, id ASC (Node mirror)."""
        deliverable = await _make_deliverable(db_session)

        m1 = await create_thread_message(
            db_session, draft_id=deliverable.id, role="user", content="First"
        )
        m2 = await create_thread_message(
            db_session, draft_id=deliverable.id, role="assistant", content="Second"
        )
        m3 = await create_thread_message(
            db_session, draft_id=deliverable.id, role="user", content="Third"
        )
        await db_session.commit()

        messages = await list_thread_messages_for_draft(db_session, deliverable.id)
        assert len(messages) == 3
        assert [m.id for m in messages] == [m1.id, m2.id, m3.id]
        assert [m.content for m in messages] == ["First", "Second", "Third"]

    async def test_two_drafts_do_not_bleed_into_each_other(self, db_session: AsyncSession) -> None:
        """Messages from one draft do not appear when listing another draft's thread."""
        d1 = await _make_deliverable(db_session, family="obc")
        d2 = await _make_deliverable(db_session, family="sel")

        await create_thread_message(
            db_session, draft_id=d1.id, role="user", content="Draft 1 message"
        )
        await create_thread_message(
            db_session, draft_id=d2.id, role="user", content="Draft 2 message"
        )
        await db_session.commit()

        d1_messages = await list_thread_messages_for_draft(db_session, d1.id)
        d2_messages = await list_thread_messages_for_draft(db_session, d2.id)

        assert len(d1_messages) == 1
        assert d1_messages[0].content == "Draft 1 message"
        assert len(d2_messages) == 1
        assert d2_messages[0].content == "Draft 2 message"

    async def test_nonexistent_draft_returns_empty_list(self, db_session: AsyncSession) -> None:
        """Querying a draft that has no rows returns [] (no FK check on reads)."""
        messages = await list_thread_messages_for_draft(db_session, 999_999_999)
        assert messages == []
