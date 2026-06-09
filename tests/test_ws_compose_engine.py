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
        tag_values,
        tag_dimensions,
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


async def _seed_registry_with_audience_values(session: AsyncSession) -> None:
    from artemis.writing_rules.tag_registry_seed import seed_tag_registry_async

    await seed_tag_registry_async(session)
    await session.commit()


async def _make_active_profile(session: AsyncSession, name: str = "Amira Voice") -> object:
    from artemis.writing_rules import repository as wr_repo

    profile = await wr_repo.create_profile(session, name=name, status="active")
    await session.commit()
    return profile


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


def test_strip_proposed_learning_lines_removes_visible_line_only() -> None:
    from artemis.marketing.writing_studio.compose_engine import strip_proposed_learning_lines

    text = (
        "Here is a tighter opening.\n\n"
        "**Proposed learning: Lead with the outcome before the feature.**\n"
        "Closing note."
    )

    cleaned = strip_proposed_learning_lines(text)

    assert "Proposed learning:" not in cleaned
    assert "Here is a tighter opening." in cleaned
    assert "Closing note." in cleaned


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
    assert "Do NOT emit 'Recommended framing'" in result["systemPrompt"]
    assert "Keep replies tight, natural, and human." in result["systemPrompt"]
    # The directive now appends the deliverable fence rule after the natural-tone
    # sentence — assert the fence rule is present rather than that it's the last line.
    assert "DELIVERABLE FENCE RULE" in result["systemPrompt"]
    assert "artemis-draft" in result["systemPrompt"]


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
    assert "Proposed learning:" not in data["responseText"]

    assert "proposedCandidates" in data
    assert len(data["proposedCandidates"]) == 1
    assert "student's transformation" in data["proposedCandidates"][0]["proposed_text"]
    # Phase 3 (Piece B): proposed candidates are now PERSISTED to
    # writing_training_candidates (returned with a real id) at status "proposed".
    assert data["proposedCandidates"][0]["status"] == "proposed"
    assert data["proposedCandidates"][0]["draft_id"] == draft_id
    assert data["proposedCandidates"][0]["id"] is not None

    from artemis.writing_rules import repository as wr_repo

    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    async with AsyncSession(engine, expire_on_commit=False) as verify_session:
        messages = await wr_repo.list_thread_messages_for_draft(verify_session, draft_id)
    await engine.dispose()

    assistant_messages = [message for message in messages if message.role == "assistant"]
    assert len(assistant_messages) == 1
    assert "Proposed learning:" not in assistant_messages[0].content


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
async def test_compose_uses_resolved_rules_when_draft_has_structured_tags(
    db_session: AsyncSession,
) -> None:
    """Tagged drafts should ground on matching + global rules only."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
    from artemis.marketing.models import CampaignDeliverable

    adapter = FakeAdapter([ScriptedReply(text="Refined.", stop_reason="end_turn")])
    await _seed_registry_with_audience_values(db_session)
    profile = await _make_active_profile(db_session)
    await _make_rule(
        db_session,
        title="Global rule",
        body="Always anchor the opening in the campaign goal.",
        profile_id=profile.id,
    )
    await _make_rule(
        db_session,
        title="Superintendent rule",
        body="Address system-level planning and district leadership tradeoffs.",
        profile_id=profile.id,
    )
    await _make_rule(
        db_session,
        title="Teacher rule",
        body="Focus on classroom routines and teacher prep burden.",
        profile_id=profile.id,
    )

    draft_id = await _make_deliverable(db_session, title="Tagged Rules Draft")
    deliverable = await db_session.get(CampaignDeliverable, draft_id)
    assert deliverable is not None
    deliverable.deliverable_metadata = {
        **(deliverable.deliverable_metadata or {}),
        "structured_tags": {"audience": "superintendent"},
    }
    await db_session.commit()

    from artemis.writing_rules import repository as wr_repo

    global_rule = await wr_repo.get_rule_by_profile_type_title(
        db_session,
        profile_id=profile.id,
        rule_type="voice",
        title="Global rule",
    )
    assert global_rule is not None
    global_rule.tag_scope = {}

    superintendent_rule = await wr_repo.get_rule_by_profile_type_title(
        db_session,
        profile_id=profile.id,
        rule_type="voice",
        title="Superintendent rule",
    )
    assert superintendent_rule is not None
    superintendent_rule.tag_scope = {"audience": ["superintendent"]}

    teacher_rule = await wr_repo.get_rule_by_profile_type_title(
        db_session,
        profile_id=profile.id,
        rule_type="voice",
        title="Teacher rule",
    )
    assert teacher_rule is not None
    teacher_rule.tag_scope = {"audience": ["teacher"]}
    await db_session.commit()

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

    assert resp.status_code == 200, resp.text
    system_prompt: str = adapter.requests[0].system or ""
    assert "Global rule" in system_prompt
    assert "Superintendent rule" in system_prompt
    assert "Teacher rule" not in system_prompt


@pytest.mark.asyncio
async def test_compose_falls_back_to_all_rules_when_draft_is_untagged(
    db_session: AsyncSession,
) -> None:
    """Untagged drafts should preserve the prior all-rules grounding behavior."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
    from artemis.writing_rules import repository as wr_repo

    adapter = FakeAdapter([ScriptedReply(text="Refined.", stop_reason="end_turn")])
    await _seed_registry_with_audience_values(db_session)
    profile = await _make_active_profile(db_session)
    rule_specs = [
        ("Global rule", "Always anchor the opening in the campaign goal.", {}),
        (
            "Superintendent rule",
            "Address system-level planning and district leadership tradeoffs.",
            {"audience": ["superintendent"]},
        ),
        (
            "Teacher rule",
            "Focus on classroom routines and teacher prep burden.",
            {"audience": ["teacher"]},
        ),
    ]
    for title, body, tag_scope in rule_specs:
        await _make_rule(db_session, title=title, body=body, profile_id=profile.id)
        rule = await wr_repo.get_rule_by_profile_type_title(
            db_session,
            profile_id=profile.id,
            rule_type="voice",
            title=title,
        )
        assert rule is not None
        rule.tag_scope = tag_scope
        await db_session.commit()

    draft_id = await _make_deliverable(db_session, title="Untagged Rules Draft")

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

    assert resp.status_code == 200, resp.text
    system_prompt: str = adapter.requests[0].system or ""
    assert "Global rule" in system_prompt
    assert "Superintendent rule" in system_prompt
    assert "Teacher rule" in system_prompt


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


# ---------------------------------------------------------------------------
# (d2) compose endpoint — chatMessage + deliverable in response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compose_returns_chat_message_and_deliverable_when_fence_present(
    db_session: AsyncSession,
) -> None:
    """When the model emits an artemis-draft fence, compose returns chatMessage +
    deliverable and persists the conversational part (no fence) as the thread msg."""
    from unittest.mock import patch

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
    from artemis.db import attach_pgvector_codec
    from artemis.writing_rules import repository as wr_repo

    fenced_reply = (
        "Here's a tighter opening — benefit-led.\n\n"
        "```artemis-draft\n"
        "Every student deserves to read with confidence.\n"
        "```"
    )
    adapter = FakeAdapter([ScriptedReply(text=fenced_reply, stop_reason="end_turn")])

    draft_id = await _make_deliverable(db_session, title="Fence Test Draft")

    with patch("artemis.providers.resolver.resolve_adapter", return_value=adapter):
        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/writing-studio/drafts/{draft_id}/compose",
                json={"request": "Tighten the opening"},
                headers={"X-Artemis-Token": "test-token"},
            )

    assert resp.status_code == 200
    data = resp.json()

    # responseText is backward-compatible: still present, fence-stripped.
    assert "responseText" in data
    # chatMessage = conversational part without the fence block.
    assert "chatMessage" in data
    assert "Here's a tighter opening" in data["chatMessage"]
    assert "artemis-draft" not in data["chatMessage"]
    assert "Every student deserves" not in data["chatMessage"]
    # deliverable = copy inside the fence.
    assert data["deliverable"] == "Every student deserves to read with confidence."

    # The persisted assistant message must store chatMessage (no fence).
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    async with AsyncSession(engine, expire_on_commit=False) as verify_session:
        messages = await wr_repo.list_thread_messages_for_draft(verify_session, draft_id)
    await engine.dispose()

    asst_msgs = [m for m in messages if m.role == "assistant"]
    assert len(asst_msgs) == 1
    assert "artemis-draft" not in asst_msgs[0].content
    assert "Every student deserves" not in asst_msgs[0].content
    assert "Here's a tighter opening" in asst_msgs[0].content


@pytest.mark.asyncio
async def test_compose_returns_null_deliverable_when_no_fence(
    db_session: AsyncSession,
) -> None:
    """When the model emits no fence, deliverable is null and chatMessage = responseText."""
    from unittest.mock import patch

    from httpx import ASGITransport, AsyncClient

    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    no_fence_reply = "The proof pack for the growth stat is reference E003 in the claims register."
    adapter = FakeAdapter([ScriptedReply(text=no_fence_reply, stop_reason="end_turn")])

    draft_id = await _make_deliverable(db_session, title="QA Test Draft")

    with patch("artemis.providers.resolver.resolve_adapter", return_value=adapter):
        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/writing-studio/drafts/{draft_id}/compose",
                json={"request": "What's the proof pack for the growth stat?"},
                headers={"X-Artemis-Token": "test-token"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["deliverable"] is None
    assert data["chatMessage"] == no_fence_reply
    assert data["responseText"] == no_fence_reply


# ---------------------------------------------------------------------------
# (e) parse_draft_fence — unit tests for the fence parser
# ---------------------------------------------------------------------------


def test_parse_draft_fence_with_fence() -> None:
    """When a fence is present, deliverable is the fenced text; chat_message is the rest."""
    from artemis.marketing.writing_studio.compose_engine import parse_draft_fence

    response = (
        "Here's the revised opening — tighter and benefit-led.\n\n"
        "```artemis-draft\n"
        "Amira helps every student read confidently.\n"
        "```"
    )
    chat_message, deliverable = parse_draft_fence(response)
    assert deliverable == "Amira helps every student read confidently."
    assert "Here's the revised opening" in chat_message
    assert "artemis-draft" not in chat_message
    assert "```" not in chat_message


def test_parse_draft_fence_no_fence() -> None:
    """When no fence is present, deliverable is None and chat_message is the full text."""
    from artemis.marketing.writing_studio.compose_engine import parse_draft_fence

    response = "That's a great question! The proof pack for the growth stat is E003."
    chat_message, deliverable = parse_draft_fence(response)
    assert deliverable is None
    assert chat_message == response


def test_parse_draft_fence_malformed_partial_fence() -> None:
    """A partial/unclosed fence should not error — deliverable is None."""
    from artemis.marketing.writing_studio.compose_engine import parse_draft_fence

    response = "Here is some content\n```artemis-draft\nIncomplete fence without closing"
    chat_message, deliverable = parse_draft_fence(response)
    assert deliverable is None
    assert chat_message == response


def test_parse_draft_fence_empty_fence() -> None:
    """An empty fence body is treated as no deliverable."""
    from artemis.marketing.writing_studio.compose_engine import parse_draft_fence

    response = "Hmm, let me think.\n```artemis-draft\n   \n```"
    chat_message, deliverable = parse_draft_fence(response)
    assert deliverable is None
    assert chat_message == response


def test_parse_draft_fence_only_fence_fallback_message() -> None:
    """When the entire response is inside a fence, chat_message gets a fallback."""
    from artemis.marketing.writing_studio.compose_engine import parse_draft_fence

    response = "```artemis-draft\nFull revised body text here.\n```"
    chat_message, deliverable = parse_draft_fence(response)
    assert deliverable == "Full revised body text here."
    assert chat_message  # non-empty fallback
    assert "artemis-draft" not in chat_message


def test_parse_draft_fence_strips_leading_proposed_learning_already_removed() -> None:
    """When called after strip_proposed_learning_lines, fence parsing still works."""
    from artemis.marketing.writing_studio.compose_engine import (
        parse_draft_fence,
        strip_proposed_learning_lines,
    )

    raw = (
        "Here is a tighter version.\n\n"
        "```artemis-draft\n"
        "Improved copy here.\n"
        "```\n\n"
        "Proposed learning: Lead with outcome."
    )
    cleaned = strip_proposed_learning_lines(raw)
    chat_message, deliverable = parse_draft_fence(cleaned)
    assert deliverable == "Improved copy here."
    assert "Proposed learning" not in chat_message
    assert "Here is a tighter version." in chat_message
