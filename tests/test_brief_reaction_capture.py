"""Tests for brief-reaction capture — the CAPTURE half of the morning-brief loop.

Covers:
  - persist_brief_manifest + _load_recent_manifest round-trip, incl. age gating.
  - capture_brief_reactions_from_message:
      * no manifest          → returns [] WITHOUT calling the LLM.
      * canned classify       → records engage/mute under canonical labels,
                                ignores labels not in the manifest.
      * empty classify result → records nothing.

Uses a real Postgres session against the dedicated artemis_test DB so the full
write_observation / content-hash / dedup path is exercised (mirrors
tests/test_callie_engagement_learning.py).

Run:
    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test \\
    ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test \\
    uv run pytest tests/test_brief_reaction_capture.py -q
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.memory.models  # noqa: F401 — register ORM models
from artemis.config import settings
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── Test DB wiring (mirrors test_callie_engagement_learning.py) ─────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)

_TRUNCATE_SQL = text(
    "TRUNCATE memory_observations, memory_observation_scopes, memory_scopes "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Isolated session: truncates memory tables before each test."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


# ── Fixtures / helpers ───────────────────────────────────────────────────────

_BRIEF = {
    "summary": "Two things to watch today.",
    "top_priorities": [
        {"item": "Ship the login redirect fix", "rationale": "blocks beta"},
        {"item": "Review the Q3 OKR draft", "rationale": None},
    ],
    "waiting_on_you": [
        {"who": "Alice", "context": "needs PR review"},
    ],
    "okr_at_risk": "Product Adoption KR at 34% — stalled",
}


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.message = _FakeMessage(text)


def _fake_complete(json_payload: object) -> object:
    """Build a fake complete_with_fallback returning a canned JSON string."""
    raw = json.dumps(json_payload)

    async def _inner(*_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse(raw)

    return _inner


async def _read_reaction_weights(session: AsyncSession) -> dict[str, float]:
    """Read back recorded reactions as the brief generator would see them."""
    from artemis.proactivity.brief_reactions import read_engagement_weights

    return await read_engagement_weights(session)


# ── Manifest round-trip + age gating ────────────────────────────────────────


async def test_persist_and_load_manifest_round_trip(db_session: AsyncSession) -> None:
    """A persisted manifest loads back with all three item types."""
    from artemis.proactivity.brief_reaction_capture import (
        _load_recent_manifest,
        persist_brief_manifest,
    )

    await persist_brief_manifest(db_session, _BRIEF)
    await db_session.commit()

    items = await _load_recent_manifest(db_session)
    assert items is not None
    by_type = {(it["type"], it["label"]) for it in items}
    assert ("priority", "Ship the login redirect fix") in by_type
    assert ("priority", "Review the Q3 OKR draft") in by_type
    assert ("waiting_on", "Alice") in by_type
    assert ("okr", "Product Adoption KR at 34% — stalled") in by_type
    assert len(items) == 4


async def test_load_manifest_age_gating(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest older than max_age_hours returns None."""
    import artemis.proactivity.brief_reaction_capture as cap
    from artemis.memory.schemas import SourceQualityHint
    from artemis.memory.store import write_observation

    # Write a manifest stamped 48h ago (default max_age is 36h).
    old_iso = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    items = [{"type": "priority", "label": "Old item"}]
    content = f"{cap._MANIFEST_PREFIX}{old_iso}:" + json.dumps(items)
    await write_observation(
        db_session,
        scope=cap._scope(),
        content=content,
        category="convention",
        source_quality=SourceQualityHint.user,
        raw_source_kind="brief_manifest",
        raw_actor="artemis-proactivity",
    )
    await db_session.commit()

    # Default 36h window → too old → None.
    assert await cap._load_recent_manifest(db_session) is None
    # Wider window → found.
    found = await cap._load_recent_manifest(db_session, max_age_hours=72)
    assert found == items


async def test_persist_empty_brief_writes_nothing(db_session: AsyncSession) -> None:
    """A brief with no items writes no manifest, and load returns None."""
    from artemis.proactivity.brief_reaction_capture import (
        _load_recent_manifest,
        persist_brief_manifest,
    )

    await persist_brief_manifest(db_session, {"summary": "nothing", "okr_at_risk": None})
    await db_session.commit()
    assert await _load_recent_manifest(db_session) is None


# ── Capture from a reply ─────────────────────────────────────────────────────


async def test_capture_no_manifest_returns_empty_without_llm(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no manifest, capture returns [] and never calls the LLM."""
    import artemis.providers.fallback as fb
    from artemis.proactivity.brief_reaction_capture import (
        capture_brief_reactions_from_message,
    )

    called = {"hit": False}

    async def _boom(*_a: object, **_k: object) -> object:
        called["hit"] = True
        raise AssertionError("LLM must not be called when there is no manifest")

    monkeypatch.setattr(fb, "complete_with_fallback", _boom)

    result = await capture_brief_reactions_from_message(db_session, "engage with the login fix")
    assert result == []
    assert called["hit"] is False


async def test_capture_records_engage_and_mute_under_canonical_labels(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canned classify → records engage/mute; a non-manifest label is ignored."""
    import artemis.providers.fallback as fb
    from artemis.proactivity.brief_reaction_capture import (
        capture_brief_reactions_from_message,
        persist_brief_manifest,
    )
    from artemis.proactivity.brief_reactions import make_item_key

    await persist_brief_manifest(db_session, _BRIEF)
    await db_session.commit()

    # The model: engage one priority (lowercased to test case-insensitive match),
    # mute the waiting_on item, and reference a label NOT in the manifest.
    payload = [
        {"label": "ship the login redirect fix", "reaction": "engage"},
        {"label": "Alice", "reaction": "mute"},
        {"label": "Some random thing Jon invented", "reaction": "engage"},
    ]
    monkeypatch.setattr(fb, "complete_with_fallback", _fake_complete(payload))

    recorded = await capture_brief_reactions_from_message(
        db_session, "Yes focus on the login fix, and drop Alice for now."
    )

    # Returned under CANONICAL labels (exact brief text), non-manifest label dropped.
    assert ("priority", "Ship the login redirect fix", "engage") in recorded
    assert ("waiting_on", "Alice", "mute") in recorded
    assert len(recorded) == 2

    # And the observations are actually persisted with the right weights.
    weights = await _read_reaction_weights(db_session)
    assert weights.get(make_item_key("priority", "Ship the login redirect fix")) == 1.5  # engage
    assert weights.get(make_item_key("waiting_on", "Alice")) == 0.0  # mute


async def test_capture_empty_classify_records_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty classify result records nothing and returns []."""
    import artemis.providers.fallback as fb
    from artemis.proactivity.brief_reaction_capture import (
        capture_brief_reactions_from_message,
        persist_brief_manifest,
    )

    await persist_brief_manifest(db_session, _BRIEF)
    await db_session.commit()

    monkeypatch.setattr(fb, "complete_with_fallback", _fake_complete([]))

    recorded = await capture_brief_reactions_from_message(db_session, "Thanks, looks good.")
    assert recorded == []
    weights = await _read_reaction_weights(db_session)
    assert weights == {}
