"""C3c tests — Callie history handoff.

Coverage:
1. _scope_for_agent — callie maps to agent:callie, artemis/others stay on agent:floating-artemis.
2. _extract_text_from_content — various content shapes.
3. _build_observation_content — role/text/timestamp formatting.
4. ingest_session_messages — DB-backed: messages → observations in agent:callie scope.
5. mark_handoff_complete — DB-backed: metadata updated correctly.
6. run_handoff — skips when already done; processes when pending.
7. Original session/messages untouched after handoff.
8. Callie retrieval surfaces the ingested content.
9. write_turn_drawer agent_id routing — callie gets agent:callie scope.
10. inject_memory_context agent_id routing — callie retrieves from agent:callie scope.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
import artemis.floating_artemis.models  # noqa: F401
import artemis.memory.models  # noqa: F401
from artemis.db import attach_pgvector_codec
from artemis.floating_artemis.callie_history_handoff import (
    _CALLIE_SCOPE,
    RETIRED_SESSION_ID,
    _build_observation_content,
    _extract_text_from_content,
    ingest_session_messages,
    mark_handoff_complete,
    run_handoff,
)
from artemis.floating_artemis.memory import (
    _scope_for_agent,
    inject_memory_context,
    write_turn_drawer,
)
from artemis.floating_artemis.models import FloatingArtemisMessage, FloatingArtemisSession
from artemis.memory.models import MemoryObservation
from artemis.memory.schemas import Scope

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)

_TRUNCATE_SQL = text(
    """
    TRUNCATE
        floating_artemis_page_context,
        floating_artemis_messages,
        floating_artemis_sessions,
        memory_conflicts,
        memory_relation_rejections,
        memory_relations,
        memory_entity_mentions,
        memory_entity_aliases,
        memory_entities,
        memory_observation_scopes,
        memory_embeddings,
        memory_evidence,
        memory_observations,
        memory_drawers,
        memory_scopes,
        raw_inputs
    RESTART IDENTITY CASCADE
    """
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    # Override the module-level SessionLocal so run_handoff uses the test DB
    db_module.engine = engine
    db_module.SessionLocal = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


# ── Helper ─────────────────────────────────────────────────────────────────────


async def _seed_retired_session(
    session: AsyncSession,
    *,
    session_id: str = RETIRED_SESSION_ID,
    messages: list[dict[str, Any]] | None = None,
    handoff_pending: bool = True,
) -> FloatingArtemisSession:
    """Create the retired session + optional messages in the test DB."""
    fa_session = FloatingArtemisSession(
        session_id=session_id,
        metadata_={
            "retired_history_owner": "callie",
            "callie_handoff_pending": handoff_pending,
            "surface": "slack",
        },
    )
    session.add(fa_session)
    await session.flush()

    if messages is not None:
        for msg_data in messages:
            msg = FloatingArtemisMessage(
                session_id=session_id,
                role=msg_data.get("role", "user"),
                content=msg_data.get("content", []),
            )
            session.add(msg)
    await session.flush()
    return fa_session


# ── Unit tests (no DB) ─────────────────────────────────────────────────────────


def test_scope_for_agent_callie_returns_callie_scope() -> None:
    scope = _scope_for_agent("callie")
    assert scope.scope_kind == "agent"
    assert scope.scope_id == "callie"


def test_scope_for_agent_artemis_returns_floating_artemis() -> None:
    scope = _scope_for_agent("artemis")
    assert scope.scope_kind == "agent"
    assert scope.scope_id == "floating-artemis"


def test_scope_for_agent_empty_returns_floating_artemis() -> None:
    scope = _scope_for_agent("")
    assert scope.scope_id == "floating-artemis"


def test_scope_for_agent_unknown_returns_that_scope() -> None:
    # Any non-artemis non-empty agent gets its own scope (future-proof)
    scope = _scope_for_agent("my-agent")
    assert scope.scope_id == "my-agent"


def test_extract_text_string_content() -> None:
    assert _extract_text_from_content("hello world") == "hello world"


def test_extract_text_block_list() -> None:
    content = [{"type": "text", "text": "Signal score is 0.9."}]
    assert _extract_text_from_content(content) == "Signal score is 0.9."


def test_extract_text_multi_block() -> None:
    content = [
        {"type": "text", "text": "Part one."},
        {"type": "text", "text": "Part two."},
    ]
    result = _extract_text_from_content(content)
    assert result is not None
    assert "Part one." in result
    assert "Part two." in result


def test_extract_text_tool_only_returns_none() -> None:
    content = [{"type": "tool_use", "id": "tu1", "name": "list_signals", "input": {}}]
    assert _extract_text_from_content(content) is None


def test_extract_text_empty_string_returns_none() -> None:
    assert _extract_text_from_content("   ") is None


def test_extract_text_empty_list_returns_none() -> None:
    assert _extract_text_from_content([]) is None


def test_build_observation_content_user_role() -> None:
    result = _build_observation_content(role="user", text="What is the HB27 angle?")
    assert "[USER]" in result
    assert "HB27" in result


def test_build_observation_content_assistant_role() -> None:
    result = _build_observation_content(role="assistant", text="The angle is proof-backed.")
    assert "[ASSISTANT]" in result
    assert "proof-backed" in result


def test_build_observation_content_with_timestamp() -> None:
    ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    result = _build_observation_content(role="user", text="Test.", created_at=ts)
    assert "2026-06-01" in result
    assert "[USER]" in result


# ── DB-backed tests ────────────────────────────────────────────────────────────


async def test_ingest_session_messages_writes_to_callie_scope(
    db_session: AsyncSession,
) -> None:
    """ingest_session_messages writes observations into agent:callie scope."""
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "What's the HB27 signal strength?"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "HB27 has a score of 0.87. Worth pursuing."}],
        },
    ]
    async with db_session.begin():
        await _seed_retired_session(db_session, messages=messages)

    written = await ingest_session_messages(db_session, fa_session_id=RETIRED_SESSION_ID)

    assert written == 2

    # Confirm observations are in agent:callie scope
    from sqlalchemy import select

    result = await db_session.execute(
        select(MemoryObservation).where(
            MemoryObservation.scope_kind == "agent",
            MemoryObservation.scope_id == "callie",
        )
    )
    obs_rows = list(result.scalars().all())
    assert len(obs_rows) == 2
    contents = {r.content for r in obs_rows}
    assert any("HB27" in c for c in contents)
    assert any("score of 0.87" in c for c in contents)


async def test_ingest_session_messages_skips_tool_only_messages(
    db_session: AsyncSession,
) -> None:
    """Messages with no usable text (tool-only) are skipped."""
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "Run the analysis."}],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu1", "name": "list_signals", "input": {}}],
        },
    ]
    async with db_session.begin():
        await _seed_retired_session(db_session, messages=messages)

    written = await ingest_session_messages(db_session, fa_session_id=RETIRED_SESSION_ID)

    assert written == 1  # only the user message


async def test_ingest_session_messages_empty_session_returns_zero(
    db_session: AsyncSession,
) -> None:
    """Empty session → 0 observations written."""
    async with db_session.begin():
        await _seed_retired_session(db_session, messages=[])

    written = await ingest_session_messages(db_session, fa_session_id=RETIRED_SESSION_ID)
    assert written == 0


async def test_ingest_session_messages_nonexistent_session_returns_zero(
    db_session: AsyncSession,
) -> None:
    """Session not found → 0 written, no exception."""
    written = await ingest_session_messages(
        db_session, fa_session_id="slack-nonexistent-session-_"
    )
    assert written == 0


async def test_mark_handoff_complete_sets_flag(db_session: AsyncSession) -> None:
    """mark_handoff_complete clears callie_handoff_pending and sets handed_to_callie_at."""
    async with db_session.begin():
        await _seed_retired_session(db_session, handoff_pending=True)

    ts = datetime(2026, 6, 10, 9, 0, 0, tzinfo=UTC)
    result = await mark_handoff_complete(
        db_session, fa_session_id=RETIRED_SESSION_ID, timestamp=ts
    )
    assert result is True

    from sqlalchemy import select

    row = (
        await db_session.execute(
            select(FloatingArtemisSession).where(
                FloatingArtemisSession.session_id == RETIRED_SESSION_ID
            )
        )
    ).scalar_one()
    meta = row.metadata_ or {}
    assert meta.get("callie_handoff_pending") is False
    assert meta.get("handed_to_callie_at") == ts.isoformat()
    # Existing keys preserved
    assert meta.get("retired_history_owner") == "callie"
    assert meta.get("surface") == "slack"


async def test_mark_handoff_complete_missing_session_returns_false(
    db_session: AsyncSession,
) -> None:
    """Missing session → returns False, no exception."""
    result = await mark_handoff_complete(
        db_session, fa_session_id="slack-does-not-exist-_"
    )
    assert result is False


async def test_original_session_messages_still_exist_after_handoff(
    db_session: AsyncSession,
) -> None:
    """The original session and its messages are never deleted during handoff."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Check the campaign."}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Campaign 18 is active."}]},
    ]
    async with db_session.begin():
        await _seed_retired_session(db_session, messages=messages)

    async with db_session.begin():
        await ingest_session_messages(db_session, fa_session_id=RETIRED_SESSION_ID)
        await mark_handoff_complete(db_session, fa_session_id=RETIRED_SESSION_ID)

    from sqlalchemy import select

    # Session still exists
    session_row = (
        await db_session.execute(
            select(FloatingArtemisSession).where(
                FloatingArtemisSession.session_id == RETIRED_SESSION_ID
            )
        )
    ).scalar_one_or_none()
    assert session_row is not None

    # Messages still exist
    msgs = list(
        (
            await db_session.execute(
                select(FloatingArtemisMessage).where(
                    FloatingArtemisMessage.session_id == RETIRED_SESSION_ID
                )
            )
        ).scalars().all()
    )
    assert len(msgs) == 2


async def test_callie_retrieval_surfaces_ingested_content(
    db_session: AsyncSession,
) -> None:
    """After handoff, search_observations on agent:callie scope returns the content."""
    from artemis.memory.retrieval import search_observations

    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "HB27 signal leads with a Tier 2 proof-backed claim."}
            ],
        },
    ]
    async with db_session.begin():
        await _seed_retired_session(db_session, messages=messages)

    async with db_session.begin():
        await ingest_session_messages(db_session, fa_session_id=RETIRED_SESSION_ID)

    # Query Callie's scope for the ingested content
    results = await search_observations(
        db_session,
        scope_set=[_CALLIE_SCOPE],
        query="HB27 proof claim",
        modes=["fts", "recency", "score"],  # skip semantic (no embedding provider in test)
    )
    assert len(results) > 0
    contents = [r.content for r in results]
    assert any("HB27" in c for c in contents)


async def test_run_handoff_skips_when_already_complete(
    db_session: AsyncSession,
) -> None:
    """run_handoff skips when callie_handoff_pending is already false."""
    async with db_session.begin():
        await _seed_retired_session(db_session, handoff_pending=False)

    result = await run_handoff(fa_session_id=RETIRED_SESSION_ID)

    assert result["skipped"] is True
    assert result["observations_written"] == 0
    assert result["error"] is None


async def test_run_handoff_processes_pending_session(
    db_session: AsyncSession,
) -> None:
    """run_handoff ingests messages and marks complete when callie_handoff_pending=true."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Marketing status?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Three active campaigns."}]},
    ]
    async with db_session.begin():
        await _seed_retired_session(db_session, messages=messages, handoff_pending=True)

    result = await run_handoff(fa_session_id=RETIRED_SESSION_ID)

    assert result["skipped"] is False
    assert result["observations_written"] == 2
    assert result["handoff_marked"] is True
    assert result["error"] is None


async def test_run_handoff_force_reruns_completed_handoff(
    db_session: AsyncSession,
) -> None:
    """run_handoff with force=True re-runs even when already complete."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Force re-ingest check."}]},
    ]
    async with db_session.begin():
        await _seed_retired_session(db_session, messages=messages, handoff_pending=False)

    result = await run_handoff(fa_session_id=RETIRED_SESSION_ID, force=True)

    assert result["skipped"] is False
    # write_observation is idempotent so written count may be 0 or 1 depending on
    # whether the observation already exists; either way no error
    assert result["error"] is None


# ── Agent-aware memory routing tests ──────────────────────────────────────────


async def test_write_turn_drawer_callie_uses_callie_scope() -> None:
    """write_turn_drawer with agent_id='callie' writes to agent:callie scope."""
    captured_scopes: list[Scope] = []

    async def fake_write_drawer(
        session: Any, scope: Scope, content: str, source: Any, **kw: Any
    ) -> Any:
        captured_scopes.append(scope)
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        row = MagicMock()
        row.id = 1
        row.scope_kind = scope.scope_kind
        row.scope_id = scope.scope_id
        row.corpus_kind = None
        row.content = content
        row.content_hash = "abc"
        row.source_kind = source.source_kind
        row.source_id = source.source_id
        row.source_extra = None
        row.owner_user_id = None
        row.captured_at = datetime.now(UTC)
        return row

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch("artemis.floating_artemis.memory.write_drawer", side_effect=fake_write_drawer),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        await write_turn_drawer(
            user_msg_id=1,
            user_text="Campaign angle?",
            assistant_text="Proof-backed Tier 2.",
            agent_id="callie",
        )

    assert len(captured_scopes) == 1
    assert captured_scopes[0].scope_kind == "agent"
    assert captured_scopes[0].scope_id == "callie"


async def test_write_turn_drawer_artemis_uses_floating_artemis_scope() -> None:
    """write_turn_drawer with agent_id='artemis' uses legacy agent:floating-artemis scope."""
    captured_scopes: list[Scope] = []

    async def fake_write_drawer(
        session: Any, scope: Scope, content: str, source: Any, **kw: Any
    ) -> Any:
        captured_scopes.append(scope)
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        row = MagicMock()
        row.id = 1
        row.scope_kind = scope.scope_kind
        row.scope_id = scope.scope_id
        row.corpus_kind = None
        row.content = content
        row.content_hash = "abc"
        row.source_kind = source.source_kind
        row.source_id = source.source_id
        row.source_extra = None
        row.owner_user_id = None
        row.captured_at = datetime.now(UTC)
        return row

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch("artemis.floating_artemis.memory.write_drawer", side_effect=fake_write_drawer),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        await write_turn_drawer(
            user_msg_id=2,
            user_text="What is my OKR status?",
            assistant_text="All on track.",
            agent_id="artemis",
        )

    assert len(captured_scopes) == 1
    assert captured_scopes[0].scope_id == "floating-artemis"


async def test_inject_memory_context_callie_queries_callie_scope() -> None:
    """inject_memory_context with agent_id='callie' queries agent:callie scope."""
    captured_scope_sets: list[list[Scope]] = []

    async def fake_search(
        session: Any, scope_set: list[Scope], query: str, limit: int
    ) -> list[Any]:
        captured_scope_sets.append(list(scope_set))
        return []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("artemis.floating_artemis.memory.search_observations", side_effect=fake_search),
        patch("artemis.db.SessionLocal", return_value=mock_session),
        # Clear the per-session cache so this is a fresh retrieval
        patch("artemis.floating_artemis.memory._retrieval_cache", {}),
    ):
        await inject_memory_context(
            "System prompt.",
            "What's the HB27 angle?",
            [],
            "slack-callie-test-session",
            agent_id="callie",
        )

    assert len(captured_scope_sets) == 1
    scope_ids = [s.scope_id for s in captured_scope_sets[0]]
    assert "callie" in scope_ids
    assert "floating-artemis" not in scope_ids


async def test_inject_memory_context_artemis_queries_floating_artemis_scope() -> None:
    """inject_memory_context with agent_id='artemis' queries agent:floating-artemis scope."""
    captured_scope_sets: list[list[Scope]] = []

    async def fake_search(
        session: Any, scope_set: list[Scope], query: str, limit: int
    ) -> list[Any]:
        captured_scope_sets.append(list(scope_set))
        return []

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("artemis.floating_artemis.memory.search_observations", side_effect=fake_search),
        patch("artemis.db.SessionLocal", return_value=mock_session),
        patch("artemis.floating_artemis.memory._retrieval_cache", {}),
    ):
        await inject_memory_context(
            "System prompt.",
            "What is my OKR?",
            [],
            "slack-artemis-test-session-2",
            agent_id="artemis",
        )

    assert len(captured_scope_sets) == 1
    scope_ids = [s.scope_id for s in captured_scope_sets[0]]
    assert "floating-artemis" in scope_ids
    assert "callie" not in scope_ids
