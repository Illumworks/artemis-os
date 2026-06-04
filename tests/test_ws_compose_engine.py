"""Tests for Writing Studio compose endpoint and engine (Phase 2 piece ③).

Covers:
  (a) extract_proposed_learnings — parses "Proposed learning: ..." lines correctly.
  (b) build_writing_memory_prompt — injects rule text into systemPrompt; carries
      prior conversation turns.
  (c) POST /api/writing-studio/drafts/{id}/compose — persists user + assistant
      thread messages, returns responseText + proposedCandidates, and passes the
      system prompt containing injected rule text to the adapter.
  (d) GET /api/writing-studio/drafts/{id} — returns real persisted threadMessages
      (not an empty list).

All model calls use FakeAdapter — no real LLM is invoked.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db as db_module
from artemis.db import attach_pgvector_codec

# ---------------------------------------------------------------------------
# Test-DB bootstrap (same pattern as other WS tests)
# ---------------------------------------------------------------------------

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)
_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
db_module.engine = _test_engine
db_module.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE = text(
    """
    TRUNCATE
        writing_draft_thread_messages,
        writing_rules,
        writing_examples,
        writing_profiles,
        campaign_deliverables,
        campaign_candidates
    RESTART IDENTITY CASCADE
    """
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE)
            yield session
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_deliverable(session: AsyncSession, *, title: str = "Test Draft") -> int:
    """Insert a campaign_candidate + campaign_deliverable and return the deliverable id."""
    from artemis.marketing.models import CampaignCandidate, CampaignDeliverable

    c = CampaignCandidate(
        campaign_family="test",
        name=title,
        stage="human_gate_1",
        decision_state="approved",
        workspace_state="pending_content",
    )
    session.add(c)
    await session.flush()
    await session.refresh(c)

    d = CampaignDeliverable(
        candidate_id=c.id,
        status="draft_ready",
        deliverable_metadata={"title": title},
    )
    session.add(d)
    await session.flush()
    await session.refresh(d)
    await session.commit()
    return d.id


async def _make_rule(
    session: AsyncSession,
    *,
    title: str = "Test Rule",
    body: str = "Use clear, concise language.",
    rule_type: str = "voice",
    profile_id: int | None = None,
) -> object:
    from artemis.writing_rules import repository as wr_repo

    rule = await wr_repo.create_rule(
        session,
        title=title,
        body=body,
        rule_type=rule_type,
        profile_id=profile_id,
        status="active",
    )
    await session.commit()  # commit so the compose route can see it via its own session
    return rule


# ---------------------------------------------------------------------------
# (a) extract_proposed_learnings
# ---------------------------------------------------------------------------


def test_extract_proposed_learnings_basic() -> None:
    from artemis.marketing.writing_studio.compose_engine import extract_proposed_learnings

    text = (
        "Here is some output.\nProposed learning: Always lead with the student benefit.\nMore text."
    )
    results = extract_proposed_learnings(text)
    assert results == ["Always lead with the student benefit."]


def test_extract_proposed_learnings_case_insensitive() -> None:
    from artemis.marketing.writing_studio.compose_engine import extract_proposed_learnings

    text = "PROPOSED LEARNING: Avoid jargon in parent-facing copy."
    assert extract_proposed_learnings(text) == ["Avoid jargon in parent-facing copy."]


def test_extract_proposed_learnings_strips_bold_markers() -> None:
    from artemis.marketing.writing_studio.compose_engine import extract_proposed_learnings

    text = "**Proposed learning: Use active voice in all headlines.**"
    results = extract_proposed_learnings(text)
    assert len(results) == 1
    assert results[0] == "Use active voice in all headlines."


def test_extract_proposed_learnings_optional_reusable() -> None:
    from artemis.marketing.writing_studio.compose_engine import extract_proposed_learnings

    text = "Proposed reusable learning: Keep subject lines under 9 words."
    results = extract_proposed_learnings(text)
    assert len(results) == 1
    assert results[0] == "Keep subject lines under 9 words."


def test_extract_proposed_learnings_too_short_ignored() -> None:
    from artemis.marketing.writing_studio.compose_engine import extract_proposed_learnings

    text = "Proposed learning: Short."
    assert extract_proposed_learnings(text) == []


def test_extract_proposed_learnings_none_in_plain_text() -> None:
    from artemis.marketing.writing_studio.compose_engine import extract_proposed_learnings

    text = "Here is some regular output with no proposed learnings at all."
    assert extract_proposed_learnings(text) == []


def test_extract_proposed_learnings_multiple() -> None:
    from artemis.marketing.writing_studio.compose_engine import extract_proposed_learnings

    text = (
        "First point.\n"
        "Proposed learning: Lead with the student outcome.\n"
        "Some more text.\n"
        "Proposed learning: Avoid passive constructions in CTAs.\n"
    )
    results = extract_proposed_learnings(text)
    assert len(results) == 2
    assert "Lead with the student outcome." in results
    assert "Avoid passive constructions in CTAs." in results


# ---------------------------------------------------------------------------
# (b) build_writing_memory_prompt — rule injection + prior turns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_prompt_injects_rules(db_session: AsyncSession) -> None:
    """System prompt must contain the injected rule title and body text."""
    from artemis.marketing.models import CampaignDeliverable
    from artemis.marketing.writing_studio.compose_engine import build_writing_memory_prompt

    rule = await _make_rule(
        db_session,
        title="Lead with outcome",
        body="Always start the copy by naming the student benefit.",
    )

    draft = CampaignDeliverable(
        id=9999,
        status="draft",
        deliverable_metadata={"title": "Test draft"},
    )

    result = build_writing_memory_prompt(
        draft=draft,
        profile=None,
        rules=[rule],
        examples=[],
        request="Please improve this draft.",
    )

    system_prompt: str = result["systemPrompt"]
    assert "Lead with outcome" in system_prompt
    assert "Always start the copy by naming the student benefit." in system_prompt
    # Anti-fabrication guardrail must be present
    assert "fabricat" in system_prompt.lower()


@pytest.mark.asyncio
async def test_build_prompt_carries_prior_turns(db_session: AsyncSession) -> None:
    """priorMessages list must include previously stored user + assistant messages."""
    from artemis.marketing.models import CampaignDeliverable
    from artemis.marketing.writing_studio.compose_engine import build_writing_memory_prompt
    from artemis.writing_rules.models import WritingDraftThreadMessage

    # Create two fake prior messages without going through the DB:
    prior1 = WritingDraftThreadMessage()
    prior1.role = "user"
    prior1.content = "Please make this more engaging."
    prior1.label = "You"
    prior1.created_at = None

    prior2 = WritingDraftThreadMessage()
    prior2.role = "assistant"
    prior2.content = "Here is an improved version..."
    prior2.label = "Artemis"
    prior2.created_at = None

    draft = CampaignDeliverable(
        id=9998,
        status="draft",
        deliverable_metadata={"title": "Test draft"},
    )

    result = build_writing_memory_prompt(
        draft=draft,
        profile=None,
        rules=[],
        examples=[],
        request="Now make it shorter.",
        prior_messages=[prior1, prior2],
    )

    prior_turns: list[dict[str, str]] = result["priorMessages"]
    assert len(prior_turns) == 2
    assert prior_turns[0]["role"] == "user"
    assert "more engaging" in prior_turns[0]["content"]
    assert prior_turns[1]["role"] == "assistant"


def test_build_prompt_no_rules_fallback() -> None:
    """System prompt should include a 'no approved rules' message when rules list is empty."""
    from artemis.marketing.models import CampaignDeliverable
    from artemis.marketing.writing_studio.compose_engine import build_writing_memory_prompt

    draft = CampaignDeliverable(
        id=9997,
        status="draft",
        deliverable_metadata={"title": "Test draft"},
    )
    result = build_writing_memory_prompt(
        draft=draft,
        profile=None,
        rules=[],
        examples=[],
        request="Write something.",
    )
    assert "No approved rules are available" in result["systemPrompt"]


# ---------------------------------------------------------------------------
# (c) POST /api/writing-studio/drafts/{id}/compose — integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_adapter_with_proposed_learning():
    """FakeAdapter that returns a response containing a Proposed learning line."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    return FakeAdapter(
        [
            ScriptedReply(
                text=(
                    "Here is a refined version of your draft that focuses on clarity.\n\n"
                    "The opening now leads with the student outcome rather than the product feature.\n\n"
                    "Proposed learning: Always open with the student's transformation, not the product name."
                ),
                stop_reason="end_turn",
            )
        ]
    )


@pytest.mark.asyncio
async def test_compose_persists_user_and_assistant_messages(
    db_session: AsyncSession,
    fake_adapter_with_proposed_learning,
) -> None:
    """Compose endpoint persists one user message + one assistant message."""
    from artemis.writing_rules import repository as wr_repo

    draft_id = await _make_deliverable(db_session, title="Campaign Draft")

    # Patch resolve_adapter to return our fake adapter
    with patch(
        "artemis.providers.resolver.resolve_adapter",
        return_value=fake_adapter_with_proposed_learning,
    ):
        from httpx import ASGITransport, AsyncClient

        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/writing-studio/drafts/{draft_id}/compose",
                json={"request": "Make it more engaging"},
                headers={"X-Artemis-Token": "test-token"},
            )

    assert resp.status_code == 200, resp.text

    # Re-open a session to verify DB state
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    async with AsyncSession(engine, expire_on_commit=False) as verify_session:
        messages = await wr_repo.list_thread_messages_for_draft(verify_session, draft_id)
    await engine.dispose()

    assert len(messages) == 2
    roles = [m.role for m in messages]
    assert "user" in roles
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_compose_returns_response_text_and_proposed_candidates(
    db_session: AsyncSession,
    fake_adapter_with_proposed_learning,
) -> None:
    """Compose returns responseText and proposedCandidates with extracted learning."""
    draft_id = await _make_deliverable(db_session, title="Campaign Draft")

    with patch(
        "artemis.providers.resolver.resolve_adapter",
        return_value=fake_adapter_with_proposed_learning,
    ):
        from httpx import ASGITransport, AsyncClient

        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/writing-studio/drafts/{draft_id}/compose",
                json={"request": "Make it more engaging"},
                headers={"X-Artemis-Token": "test-token"},
            )

    assert resp.status_code == 200
    data = resp.json()

    assert "responseText" in data
    assert "refined" in data["responseText"]

    assert "proposedCandidates" in data
    assert len(data["proposedCandidates"]) == 1
    assert "student's transformation" in data["proposedCandidates"][0]["proposed_text"]
    # Phase 3 (Piece B): proposed candidates are now PERSISTED to
    # writing_training_candidates (returned with a real id) at status "proposed".
    assert data["proposedCandidates"][0]["status"] == "proposed"
    assert data["proposedCandidates"][0]["draft_id"] == draft_id
    assert data["proposedCandidates"][0]["id"] is not None


@pytest.mark.asyncio
async def test_compose_passes_injected_rules_to_adapter(
    db_session: AsyncSession,
) -> None:
    """The system prompt passed to the adapter must include the injected rule text."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    adapter = FakeAdapter(
        [ScriptedReply(text="Here is the improved draft.", stop_reason="end_turn")]
    )

    await _make_rule(
        db_session,
        title="Outcome-first rule",
        body="Start every email with the student's measurable outcome.",
    )
    draft_id = await _make_deliverable(db_session, title="Rules Test Draft")

    with patch(
        "artemis.providers.resolver.resolve_adapter",
        return_value=adapter,
    ):
        from httpx import ASGITransport, AsyncClient

        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/writing-studio/drafts/{draft_id}/compose",
                json={"request": "Revise this draft"},
                headers={"X-Artemis-Token": "test-token"},
            )

    assert resp.status_code == 200

    # The adapter recorded every CompletionRequest; inspect the first one.
    assert len(adapter.requests) == 1
    req = adapter.requests[0]
    system_prompt: str = req.system or ""
    assert "Outcome-first rule" in system_prompt
    assert "Start every email with the student's measurable outcome." in system_prompt


@pytest.mark.asyncio
async def test_compose_returns_404_for_missing_draft(db_session: AsyncSession) -> None:
    """Compose returns 404 when the draft does not exist."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    adapter = FakeAdapter([ScriptedReply(text="Ok.", stop_reason="end_turn")])

    with patch(
        "artemis.providers.resolver.resolve_adapter",
        return_value=adapter,
    ):
        from httpx import ASGITransport, AsyncClient

        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/writing-studio/drafts/99999/compose",
                json={"request": "Test"},
                headers={"X-Artemis-Token": "test-token"},
            )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# (d) GET /api/writing-studio/drafts/{id} returns real threadMessages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_detail_returns_thread_messages(
    db_session: AsyncSession,
    fake_adapter_with_proposed_learning,
) -> None:
    """After a compose call, GET /drafts/{id} returns the persisted threadMessages."""
    draft_id = await _make_deliverable(db_session, title="Thread Messages Draft")

    # First, call compose to create the messages
    with patch(
        "artemis.providers.resolver.resolve_adapter",
        return_value=fake_adapter_with_proposed_learning,
    ):
        from httpx import ASGITransport, AsyncClient

        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            compose_resp = await ac.post(
                f"/api/writing-studio/drafts/{draft_id}/compose",
                json={"request": "Help me improve this"},
                headers={"X-Artemis-Token": "test-token"},
            )
        assert compose_resp.status_code == 200

        # Now fetch the draft detail — must include the messages
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            detail_resp = await ac.get(
                f"/api/writing-studio/drafts/{draft_id}",
                headers={"X-Artemis-Token": "test-token"},
            )

    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    assert "threadMessages" in detail
    assert len(detail["threadMessages"]) == 2

    roles = {m["role"] for m in detail["threadMessages"]}
    assert "user" in roles
    assert "assistant" in roles
