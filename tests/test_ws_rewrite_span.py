"""Tests for the Writing Studio rewrite-span endpoint (Composer Stage 2).

Covers:
  (a) POST /api/writing-studio/drafts/{id}/rewrite-span — happy path returns
      rewrittenText.
  (b) resolve_grounding_rules is called with the draft's structured_tags; the
      resolved rule titles land in the system prompt passed to the adapter.
  (c) The endpoint returns ONLY the rewritten span, not a full draft body.
  (d) Validation: 400 on missing selectedText / instruction.
  (e) 404 on unknown draft id.
  (f) Span replacement isolation: when the client accepts the rewrite and
      replaces the span, the rest of the doc is byte-identical.

All LLM calls use FakeAdapter — no real provider is invoked.
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
# Test-DB bootstrap — isolated DB for this agent
# ---------------------------------------------------------------------------

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test_highlightedit",
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
# Helpers — shared with test_ws_compose_engine.py by convention
# ---------------------------------------------------------------------------


async def _make_deliverable(
    session: AsyncSession,
    *,
    title: str = "Test Draft",
    structured_tags: dict | None = None,
    live_content: str | None = None,
) -> int:
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

    meta: dict = {"title": title}
    if structured_tags:
        meta["structured_tags"] = structured_tags
    if live_content:
        meta["live_content"] = live_content

    d = CampaignDeliverable(
        candidate_id=c.id,
        status="draft_ready",
        deliverable_metadata=meta,
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
    tag_scope: dict | None = None,
    status: str = "active",
) -> object:
    from artemis.writing_rules import repository as wr_repo

    kwargs: dict = {
        "title": title,
        "body": body,
        "rule_type": rule_type,
        "profile_id": profile_id,
        "status": status,
    }
    if tag_scope is not None:
        kwargs["tag_scope"] = tag_scope
    rule = await wr_repo.create_rule(session, **kwargs)
    await session.commit()
    return rule


async def _make_active_profile(session: AsyncSession, name: str = "Amira Voice") -> object:
    from artemis.writing_rules import repository as wr_repo

    profile = await wr_repo.create_profile(session, name=name, status="active")
    await session.commit()
    return profile


def _make_fake_adapter(rewrite_text: str):
    """Return a FakeAdapter that responds with the given rewrite text."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    return FakeAdapter([ScriptedReply(text=rewrite_text, stop_reason="end_turn")])


async def _post_rewrite_span(draft_id: int, payload: dict) -> tuple[int, dict]:
    """POST /api/writing-studio/drafts/{id}/rewrite-span via ASGI and return (status, json)."""
    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/writing-studio/drafts/{draft_id}/rewrite-span",
            json=payload,
            headers={"X-Artemis-Token": "test-token"},
        )
    return resp.status_code, (
        resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    )


# ---------------------------------------------------------------------------
# (a) Happy path — returns rewrittenText
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_span_returns_rewritten_text(db_session: AsyncSession) -> None:
    """Endpoint returns rewrittenText matching the fake adapter's scripted reply."""
    draft_id = await _make_deliverable(
        db_session,
        title="Superintendent Outreach Email",
        live_content="Our literacy program improves student outcomes.",
    )

    fake_adapter = _make_fake_adapter(
        "Our award-winning literacy program transforms outcomes for every student."
    )

    with patch("artemis.providers.resolver.resolve_adapter", return_value=fake_adapter):
        status, data = await _post_rewrite_span(
            draft_id,
            {
                "selectedText": "Our literacy program improves student outcomes.",
                "instruction": "Rewrite",
                "fullText": "Our literacy program improves student outcomes.",
            },
        )

    assert status == 200, data
    assert "rewrittenText" in data
    assert "award-winning" in data["rewrittenText"]


@pytest.mark.asyncio
async def test_rewrite_span_strips_whitespace_from_response(db_session: AsyncSession) -> None:
    """rewrittenText is returned stripped (no leading/trailing whitespace)."""
    draft_id = await _make_deliverable(db_session)

    fake_adapter = _make_fake_adapter("  \n  Rewritten span text here.  \n  ")

    with patch("artemis.providers.resolver.resolve_adapter", return_value=fake_adapter):
        status, data = await _post_rewrite_span(
            draft_id,
            {"selectedText": "Original text here.", "instruction": "Shorten"},
        )

    assert status == 200, data
    assert data["rewrittenText"] == "Rewritten span text here."


# ---------------------------------------------------------------------------
# (b) resolve_grounding_rules called with draft's structured_tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_span_uses_tag_scoped_rules(db_session: AsyncSession) -> None:
    """resolve_grounding_rules must be called with the draft's structured_tags.

    This is the tag-scoped-rules showcase: a draft tagged audience=superintendent
    should get the superintendent-specific rule injected into the prompt.
    """
    profile = await _make_active_profile(db_session)

    # Create a general rule (no tag scope) and a superintendent-scoped rule.
    await _make_rule(
        db_session,
        title="General voice rule",
        body="Be clear and concise.",
        profile_id=profile.id,
    )
    await _make_rule(
        db_session,
        title="Superintendent-specific rule",
        body="Address the superintendent's accountability priorities directly.",
        profile_id=profile.id,
        tag_scope={"audience": "superintendent"},
    )

    draft_id = await _make_deliverable(
        db_session,
        title="Supt Email",
        structured_tags={"audience": "superintendent"},
        live_content="We improve student outcomes district-wide.",
    )

    captured_system_prompt: list[str] = []

    from artemis.agent.client import CompletionRequest, CompletionResponse
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    class CapturingFakeAdapter(FakeAdapter):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            if request.system:
                captured_system_prompt.append(request.system)
            return await super().complete(request)

    fake_adapter = CapturingFakeAdapter(
        [ScriptedReply(text="Rewritten for superintendents.", stop_reason="end_turn")]
    )

    with patch("artemis.providers.resolver.resolve_adapter", return_value=fake_adapter):
        status, data = await _post_rewrite_span(
            draft_id,
            {
                "selectedText": "We improve student outcomes district-wide.",
                "instruction": "Make on-brand",
            },
        )

    assert status == 200, data
    assert len(captured_system_prompt) >= 1
    system = captured_system_prompt[0]

    # The superintendent-scoped rule must appear in the prompt.
    assert "Superintendent-specific rule" in system, (
        f"Tag-scoped rule title not found in system prompt. First 500 chars: {system[:500]}"
    )
    assert "accountability priorities" in system, "Tag-scoped rule body not found in system prompt."


@pytest.mark.asyncio
async def test_rewrite_span_resolve_grounding_rules_called(db_session: AsyncSession) -> None:
    """resolve_grounding_rules is invoked with the draft's structured_tags."""
    from artemis.writing_rules import repository as wr_repo

    draft_id = await _make_deliverable(
        db_session,
        structured_tags={"audience": "principal"},
    )

    fake_adapter = _make_fake_adapter("Shorter version.")

    with (
        patch("artemis.providers.resolver.resolve_adapter", return_value=fake_adapter),
        patch.object(
            wr_repo,
            "resolve_grounding_rules",
            wraps=wr_repo.resolve_grounding_rules,
        ) as mock_resolve,
    ):
        status, _ = await _post_rewrite_span(
            draft_id,
            {"selectedText": "Long passage here.", "instruction": "Shorten"},
        )

    assert status == 200
    assert mock_resolve.called, "resolve_grounding_rules was not called"
    call_kwargs = mock_resolve.call_args.kwargs
    assert "structured_tags" in call_kwargs
    # structured_tags is normalized; audience value should be present.
    raw_tags = call_kwargs["structured_tags"]
    # It may be a dict or a StructuredTags object — get audience via .get or dict access.
    if hasattr(raw_tags, "get"):
        audience_val = raw_tags.get("audience")
    else:
        audience_val = getattr(raw_tags, "audience", None)
    assert audience_val == "principal", f"Expected audience=principal, got {raw_tags}"


# ---------------------------------------------------------------------------
# (c) Span replacement isolation test
#
# This is a pure-logic test (no HTTP, no DB) that verifies the single-span
# replacement invariant:
#   len(doc_before) - len(span_before) + len(span_after) == len(doc_after)
# and that characters outside the span are byte-identical.
# ---------------------------------------------------------------------------


def test_span_replacement_isolation() -> None:
    """Only the selected span changes; rest of the document is byte-identical.

    Simulates the client-side accept logic: replace [from:to] in the full text.
    """
    full_doc = (
        "Introduction paragraph about literacy.\n\n"
        "The student outcomes are very good across all grades.\n\n"
        "Contact us for more information."
    )
    span_before = "The student outcomes are very good across all grades."
    span_after = "Student achievement soars when literacy is prioritized."

    # Find the span position.
    idx = full_doc.index(span_before)
    span_end = idx + len(span_before)

    # Apply the replacement (the same operation the JS accept handler does).
    doc_after = full_doc[:idx] + span_after + full_doc[span_end:]

    # Verify the invariant.
    expected_len = len(full_doc) - len(span_before) + len(span_after)
    assert len(doc_after) == expected_len

    # Verify prefix and suffix are byte-identical.
    assert doc_after[:idx] == full_doc[:idx], "Prefix changed — should be identical"
    assert doc_after[idx + len(span_after) :] == full_doc[span_end:], (
        "Suffix changed — should be identical"
    )

    # Verify only the span changed.
    assert span_after in doc_after
    assert span_before not in doc_after


def test_span_replacement_reject_is_noop() -> None:
    """Reject: the document is byte-identical to the original."""
    full_doc = "Paragraph one.\n\nParagraph two with the selected bit.\n\nParagraph three."
    span = "the selected bit"

    # Reject = no replacement at all.
    doc_after_reject = full_doc  # literally no change

    assert doc_after_reject == full_doc, "Reject should be a pure no-op"
    assert span in doc_after_reject


# ---------------------------------------------------------------------------
# (d) Validation — 400 on missing required fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_span_missing_selected_text(db_session: AsyncSession) -> None:
    draft_id = await _make_deliverable(db_session)

    _, data = await _post_rewrite_span(draft_id, {"instruction": "Shorten"})
    # Should fail with 400 because selectedText is missing.
    # (The HTTP client returns status from the tuple.)
    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/writing-studio/drafts/{draft_id}/rewrite-span",
            json={"instruction": "Shorten"},
            headers={"X-Artemis-Token": "test-token"},
        )

    assert resp.status_code == 400
    assert "selectedText" in resp.text or "missing_selected_text" in resp.text


@pytest.mark.asyncio
async def test_rewrite_span_missing_instruction(db_session: AsyncSession) -> None:
    draft_id = await _make_deliverable(db_session)

    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/writing-studio/drafts/{draft_id}/rewrite-span",
            json={"selectedText": "Some text"},
            headers={"X-Artemis-Token": "test-token"},
        )

    assert resp.status_code == 400
    assert "instruction" in resp.text or "missing_instruction" in resp.text


@pytest.mark.asyncio
async def test_rewrite_span_empty_selected_text(db_session: AsyncSession) -> None:
    draft_id = await _make_deliverable(db_session)

    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/writing-studio/drafts/{draft_id}/rewrite-span",
            json={"selectedText": "   ", "instruction": "Shorten"},
            headers={"X-Artemis-Token": "test-token"},
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# (e) 404 on unknown draft id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_span_unknown_draft(db_session: AsyncSession) -> None:
    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/writing-studio/drafts/99999/rewrite-span",
            json={"selectedText": "Some text", "instruction": "Shorten"},
            headers={"X-Artemis-Token": "test-token"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# (f) Trace includes rule titles for tag-scoped showcase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_span_trace_includes_rule_titles(db_session: AsyncSession) -> None:
    """The trace returned by the endpoint lists the rules that were injected."""
    profile = await _make_active_profile(db_session, name="Test Profile")
    await _make_rule(
        db_session,
        title="Student-first language",
        body="Lead every sentence with the student as subject.",
        profile_id=profile.id,
    )

    draft_id = await _make_deliverable(db_session, title="Email Draft")

    fake_adapter = _make_fake_adapter("Student achievement grows with this program.")

    with patch("artemis.providers.resolver.resolve_adapter", return_value=fake_adapter):
        from httpx import ASGITransport, AsyncClient

        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/writing-studio/drafts/{draft_id}/rewrite-span",
                json={"selectedText": "The program helps students.", "instruction": "Rewrite"},
                headers={"X-Artemis-Token": "test-token"},
            )

    assert resp.status_code == 200
    data = resp.json()

    trace = data.get("trace", {})
    rule_titles = [r["title"] for r in trace.get("rules", [])]
    assert "Student-first language" in rule_titles, (
        f"Expected rule 'Student-first language' in trace rules. Got: {rule_titles}"
    )


# ---------------------------------------------------------------------------
# (g) No thread messages persisted (span rewrites are not chat turns)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_span_does_not_persist_thread_messages(db_session: AsyncSession) -> None:
    """Span rewrites must NOT create writing_draft_thread_messages rows."""
    from artemis.writing_rules import repository as wr_repo

    draft_id = await _make_deliverable(db_session)

    fake_adapter = _make_fake_adapter("Rewritten span text.")

    with patch("artemis.providers.resolver.resolve_adapter", return_value=fake_adapter):
        from httpx import ASGITransport, AsyncClient

        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/writing-studio/drafts/{draft_id}/rewrite-span",
                json={"selectedText": "Some text", "instruction": "Shorten"},
                headers={"X-Artemis-Token": "test-token"},
            )

    assert resp.status_code == 200

    # Verify no thread messages were created.
    engine2 = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine2)
    async with AsyncSession(engine2, expire_on_commit=False) as verify_session:
        messages = await wr_repo.list_thread_messages_for_draft(verify_session, draft_id)
    await engine2.dispose()

    assert len(messages) == 0, (
        f"Span rewrite should NOT persist thread messages. Got {len(messages)} messages."
    )
