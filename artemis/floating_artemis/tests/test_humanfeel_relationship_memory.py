"""Relationship-memory tests — per-person speaker attribution.

Coverage:
1. write_turn_drawer with speaker writes [SPEAKER] prefix in content.
2. write_turn_drawer with speaker stores speaker_id + speaker_name in source_extra.
3. write_turn_drawer without speaker is byte-for-byte identical to prior behaviour.
4. write_turn_drawer with only speaker_name (no speaker_id) omits the id suffix.
5. DB-backed: drawer written with speaker carries attribution that survives a DB round-trip.
6. inject_memory_context with speaker_name surfaces prior context for that person.
7. inject_memory_context without speaker is unchanged (no extra per-person digest block).
8. Per-person digest dedupes against the general results (same obs not shown twice).
9. Per-person recall failure is silently swallowed (chat never breaks).
10. handle_turn threads speaker_id through to write_turn_drawer.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
import artemis.floating_artemis.models  # noqa: F401
import artemis.memory.models  # noqa: F401
from artemis.db import attach_pgvector_codec
from artemis.floating_artemis.chat import handle_turn
from artemis.floating_artemis.memory import (
    _retrieval_cache,
    inject_memory_context,
    write_turn_drawer,
)
from artemis.memory.schemas import Scope, Source
from artemis.memory.store import write_drawer

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


# ── DB fixture ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
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


def _clear_retrieval_cache() -> None:
    _retrieval_cache.clear()


def _make_scored_obs(content: str, obs_id: int = 1, score: float = 0.8) -> Any:
    obs = MagicMock()
    obs.content = content
    obs.final_score = score
    obs.scope_kind = "agent"
    obs.scope_id = "floating-artemis"
    obs.id = obs_id
    return obs


# ── Unit tests (mocked DB) ──────────────────────────────────────────────────────


async def test_write_turn_drawer_with_speaker_prefixes_content() -> None:
    """Drawer content starts with [SPEAKER] line when speaker is provided."""
    captured: list[str] = []

    async def capture_write_drawer(
        session: Any, scope: Any, content: str, source: Source, **kw: Any
    ) -> Any:
        captured.append(content)
        row = MagicMock()
        row.id = 1
        row.scope_kind = "agent"
        row.scope_id = "floating-artemis"
        row.corpus_kind = None
        row.content = content
        row.content_hash = "abc"
        row.source_kind = source.source_kind
        row.source_id = source.source_id
        row.source_extra = source.source_extra
        row.owner_user_id = None
        from datetime import UTC, datetime

        row.captured_at = datetime.now(UTC)
        return row

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch("artemis.floating_artemis.memory.write_drawer", side_effect=capture_write_drawer),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        await write_turn_drawer(
            user_msg_id=1,
            user_text="Hello Artemis",
            assistant_text="Hello!",
            speaker_name="Alice",
            speaker_id="U01ALICE",
        )

    assert len(captured) == 1
    assert captured[0].startswith("[SPEAKER] Alice (U01ALICE)\n")
    assert "[USER] Hello Artemis" in captured[0]
    assert "[ASSISTANT] Hello!" in captured[0]


async def test_write_turn_drawer_with_speaker_stores_source_extra() -> None:
    """source_extra carries speaker_id and speaker_name when speaker is known."""
    captured_sources: list[Source] = []

    async def capture_write_drawer(
        session: Any, scope: Any, content: str, source: Source, **kw: Any
    ) -> Any:
        captured_sources.append(source)
        row = MagicMock()
        row.id = 1
        row.scope_kind = "agent"
        row.scope_id = "floating-artemis"
        row.corpus_kind = None
        row.content = content
        row.content_hash = "abc"
        row.source_kind = source.source_kind
        row.source_id = source.source_id
        row.source_extra = source.source_extra
        row.owner_user_id = None
        from datetime import UTC, datetime

        row.captured_at = datetime.now(UTC)
        return row

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch("artemis.floating_artemis.memory.write_drawer", side_effect=capture_write_drawer),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        await write_turn_drawer(
            user_msg_id=2,
            user_text="What's the OKR status?",
            assistant_text="All green.",
            speaker_name="Bob",
            speaker_id="U01BOB",
        )

    assert len(captured_sources) == 1
    assert captured_sources[0].source_extra is not None
    assert captured_sources[0].source_extra["speaker_id"] == "U01BOB"
    assert captured_sources[0].source_extra["speaker_name"] == "Bob"


async def test_write_turn_drawer_without_speaker_unchanged() -> None:
    """Drawer content without speaker is byte-for-byte identical to prior behaviour."""
    captured: list[tuple[str, Any]] = []

    async def capture_write_drawer(
        session: Any, scope: Any, content: str, source: Source, **kw: Any
    ) -> Any:
        captured.append((content, source.source_extra))
        row = MagicMock()
        row.id = 1
        row.scope_kind = "agent"
        row.scope_id = "floating-artemis"
        row.corpus_kind = None
        row.content = content
        row.content_hash = "abc"
        row.source_kind = source.source_kind
        row.source_id = source.source_id
        row.source_extra = source.source_extra
        row.owner_user_id = None
        from datetime import UTC, datetime

        row.captured_at = datetime.now(UTC)
        return row

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch("artemis.floating_artemis.memory.write_drawer", side_effect=capture_write_drawer),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        await write_turn_drawer(
            user_msg_id=3,
            user_text="Is the server OK?",
            assistant_text="Yes.",
        )

    assert len(captured) == 1
    content, source_extra = captured[0]
    # Exact same format as before
    assert content == "[USER] Is the server OK?\n[ASSISTANT] Yes."
    # No source_extra added
    assert source_extra is None


async def test_write_turn_drawer_speaker_name_only_no_id_suffix() -> None:
    """When only speaker_name is provided (no speaker_id), no parens suffix is added."""
    captured: list[str] = []

    async def capture_write_drawer(
        session: Any, scope: Any, content: str, source: Source, **kw: Any
    ) -> Any:
        captured.append(content)
        row = MagicMock()
        row.id = 1
        row.scope_kind = "agent"
        row.scope_id = "floating-artemis"
        row.corpus_kind = None
        row.content = content
        row.content_hash = "abc"
        row.source_kind = source.source_kind
        row.source_id = source.source_id
        row.source_extra = source.source_extra
        row.owner_user_id = None
        from datetime import UTC, datetime

        row.captured_at = datetime.now(UTC)
        return row

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch("artemis.floating_artemis.memory.write_drawer", side_effect=capture_write_drawer),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        await write_turn_drawer(
            user_msg_id=4,
            user_text="Hello",
            assistant_text="Hi!",
            speaker_name="Carol",
        )

    assert len(captured) == 1
    assert captured[0].startswith("[SPEAKER] Carol\n")
    assert "(" not in captured[0].split("\n")[0]  # no parens on the SPEAKER line


# ── DB-backed tests ─────────────────────────────────────────────────────────────


async def test_db_drawer_with_speaker_attribution_survives_round_trip(
    db_session: AsyncSession,
) -> None:
    """Drawer written with speaker attribution can be read back with full content + source_extra."""
    from artemis.memory.models import MemoryDrawer

    scope = Scope(scope_kind="agent", scope_id="floating-artemis")
    content = "[SPEAKER] Dave (U01DAVE)\n[USER] What campaigns are live?\n[ASSISTANT] Two."
    source = Source(
        source_kind="floating_artemis_message",
        source_id="9999",
        source_extra={"speaker_id": "U01DAVE", "speaker_name": "Dave"},
    )

    async with db_session.begin_nested():
        drawer = await write_drawer(db_session, scope=scope, content=content, source=source)

    # Re-read from DB
    from sqlalchemy import select

    result = await db_session.execute(select(MemoryDrawer).where(MemoryDrawer.id == drawer.id))
    row = result.scalar_one()

    assert "[SPEAKER] Dave (U01DAVE)" in row.content
    assert "[USER] What campaigns are live?" in row.content
    assert row.source_extra is not None
    assert row.source_extra["speaker_id"] == "U01DAVE"
    assert row.source_extra["speaker_name"] == "Dave"


async def test_write_turn_drawer_db_round_trip_with_speaker(db_session: AsyncSession) -> None:
    """End-to-end: write_turn_drawer with speaker → drawer in DB with attribution."""
    from sqlalchemy import select

    from artemis.memory.models import MemoryDrawer

    await write_turn_drawer(
        user_msg_id=12345,
        user_text="What is the budget?",
        assistant_text="$50k.",
        speaker_name="Eve",
        speaker_id="U01EVE",
    )

    result = await db_session.execute(select(MemoryDrawer).where(MemoryDrawer.source_id == "12345"))
    row = result.scalar_one_or_none()
    assert row is not None
    assert "[SPEAKER] Eve (U01EVE)" in row.content
    assert row.source_extra is not None
    assert row.source_extra["speaker_name"] == "Eve"


# ── inject_memory_context tests ─────────────────────────────────────────────────


async def test_inject_memory_context_with_speaker_surfaces_prior_context() -> None:
    """When speaker_name is known, relevant observations for that person are surfaced."""
    speaker_obs = _make_scored_obs("[SPEAKER] Frank (U01F)\n[USER] ...", obs_id=10, score=0.95)
    general_obs = _make_scored_obs("Campaign budget is $100k", obs_id=20, score=0.85)

    call_queries: list[str] = []

    async def fake_search(session: Any, scope_set: Any, query: str, limit: int) -> list[Any]:
        call_queries.append(query)
        if "Frank" in query and limit == 3:
            return [speaker_obs]
        return [general_obs]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    _clear_retrieval_cache()
    with (
        patch("artemis.floating_artemis.memory.search_observations", side_effect=fake_search),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        result_prompt = await inject_memory_context(
            "Base prompt.",
            "Tell me the latest",
            [],
            "session-humanfeel-1",
            speaker_name="Frank",
        )

    # Both the general and per-person blocks should appear
    assert "## Recent memory" in result_prompt
    assert "What I know about Frank" in result_prompt
    # Speaker-attributed obs should be in the per-person block
    assert "[SPEAKER] Frank" in result_prompt


async def test_inject_memory_context_without_speaker_unchanged() -> None:
    """Without speaker_name the prompt has no per-person digest block."""
    obs = _make_scored_obs("General observation", obs_id=1)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    _clear_retrieval_cache()
    with (
        patch(
            "artemis.floating_artemis.memory.search_observations",
            new_callable=AsyncMock,
            return_value=[obs],
        ),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        result_prompt = await inject_memory_context(
            "Base prompt.",
            "What is happening?",
            [],
            "session-humanfeel-2",
        )

    assert "What I know about" not in result_prompt
    assert "## Recent memory" in result_prompt


async def test_inject_memory_context_person_digest_dedupes_against_general() -> None:
    """Per-person digest only shows observations NOT already in the general results."""
    shared_obs = _make_scored_obs("Both query and person match", obs_id=99, score=0.9)

    async def fake_search(session: Any, scope_set: Any, query: str, limit: int) -> list[Any]:
        # Same obs returned for both the general query and the per-person query
        return [shared_obs]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_id=mock_session)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    _clear_retrieval_cache()
    with (
        patch("artemis.floating_artemis.memory.search_observations", side_effect=fake_search),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        result_prompt = await inject_memory_context(
            "Base.",
            "What do I know?",
            [],
            "session-humanfeel-dedup",
            speaker_name="Grace",
        )

    # Content should appear only once (deduped)
    assert result_prompt.count("Both query and person match") == 1


async def test_inject_memory_context_person_lookup_failure_swallowed() -> None:
    """A crash in the per-person lookup must never break the overall injection."""
    general_obs = _make_scored_obs("General content", obs_id=5, score=0.8)
    call_count = [0]

    async def fake_search(session: Any, scope_set: Any, query: str, limit: int) -> list[Any]:
        call_count[0] += 1
        if limit == 3:
            raise RuntimeError("DB timeout simulated")
        return [general_obs]

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    _clear_retrieval_cache()
    with (
        patch("artemis.floating_artemis.memory.search_observations", side_effect=fake_search),
        patch("artemis.db.SessionLocal", return_value=mock_session),
    ):
        result_prompt = await inject_memory_context(
            "Base.",
            "Any question",
            [],
            "session-humanfeel-failure",
            speaker_name="Henry",
        )

    # General memory should still be injected despite per-person failure
    assert "General content" in result_prompt
    # No per-person digest (the lookup failed)
    assert "What I know about Henry" not in result_prompt


# ── handle_turn integration ─────────────────────────────────────────────────────

_STANDARD_PATCHES = dict(
    get_status=patch(
        "artemis.floating_artemis.chat.get_status",
        return_value={"available_surfaces": []},
    ),
    broadcast=patch("artemis.floating_artemis.chat._broadcast"),
    load_history=patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
    get_page_ctx=patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
    persist=patch(
        "artemis.floating_artemis.chat._persist_messages",
        return_value=42,
    ),
    meeting_ctx=patch(
        "artemis.floating_artemis.chat._get_recent_meeting_context", return_value=None
    ),
)


async def test_handle_turn_threads_speaker_id_to_write_turn_drawer() -> None:
    """handle_turn passes speaker_id + speaker_name to write_turn_drawer."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    adapter = FakeAdapter([ScriptedReply(text="Got it.")])
    drawer_calls: list[dict[str, Any]] = []

    async def fake_write_turn_drawer(
        user_msg_id: int,
        user_text: str,
        assistant_text: str,
        *,
        agent_id: str = "artemis",
        speaker_name: str | None = None,
        speaker_id: str | None = None,
    ) -> None:
        drawer_calls.append(
            {
                "speaker_name": speaker_name,
                "speaker_id": speaker_id,
            }
        )

    _clear_retrieval_cache()
    with (
        _STANDARD_PATCHES["get_status"],
        _STANDARD_PATCHES["broadcast"],
        _STANDARD_PATCHES["load_history"],
        _STANDARD_PATCHES["get_page_ctx"],
        _STANDARD_PATCHES["persist"],
        _STANDARD_PATCHES["meeting_ctx"],
        patch(
            "artemis.floating_artemis.chat.write_turn_drawer",
            side_effect=fake_write_turn_drawer,
        ),
        patch(
            "artemis.floating_artemis.chat.inject_memory_context",
            side_effect=lambda prompt, *a, **kw: prompt,
        ),
    ):
        result = await handle_turn(
            session_id="test-humanfeel-thread",
            user_text="Hi there",
            adapter=adapter,
            speaker_name="Irene",
            speaker_id="U01IRENE",
        )

    assert result.response_text == "Got it."
    assert len(drawer_calls) == 1
    assert drawer_calls[0]["speaker_name"] == "Irene"
    assert drawer_calls[0]["speaker_id"] == "U01IRENE"


async def test_handle_turn_threads_speaker_to_inject_memory_context() -> None:
    """handle_turn passes speaker_name + speaker_id to inject_memory_context."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    adapter = FakeAdapter([ScriptedReply(text="Understood.")])
    inject_calls: list[dict[str, Any]] = []

    async def fake_inject(
        prompt: str,
        user_msg: str,
        history: list[Any],
        session_id: str,
        *,
        agent_id: str = "artemis",
        speaker_name: str | None = None,
        speaker_id: str | None = None,
    ) -> str:
        inject_calls.append({"speaker_name": speaker_name, "speaker_id": speaker_id})
        return prompt

    _clear_retrieval_cache()
    with (
        _STANDARD_PATCHES["get_status"],
        _STANDARD_PATCHES["broadcast"],
        _STANDARD_PATCHES["load_history"],
        _STANDARD_PATCHES["get_page_ctx"],
        _STANDARD_PATCHES["persist"],
        _STANDARD_PATCHES["meeting_ctx"],
        patch(
            "artemis.floating_artemis.chat.inject_memory_context",
            side_effect=fake_inject,
        ),
        patch(
            "artemis.floating_artemis.chat.write_turn_drawer",
            new_callable=AsyncMock,
        ),
    ):
        await handle_turn(
            session_id="test-humanfeel-inject",
            user_text="Anything",
            adapter=adapter,
            speaker_name="Jim",
            speaker_id="U01JIM",
        )

    assert len(inject_calls) == 1
    assert inject_calls[0]["speaker_name"] == "Jim"
    assert inject_calls[0]["speaker_id"] == "U01JIM"
