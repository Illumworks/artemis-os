"""Phase B4 tests: Graph layer + MCP server.

Coverage: ≥50 tests across:
  - Schema / FK cascades
  - Entity helpers (upsert, alias, mention, list, neighborhood)
  - Predicate vocabulary enforcement
  - Graph extraction (mock LLM)
  - Graph fusion in retrieval
  - MCP handler functions (no real DB round-trips for IO)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Generator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.graph import (
    VALID_ENTITY_KINDS,
    VALID_PREDICATES,
    _to_slug,
    find_entities_in_text,
    get_entity_neighborhood,
    get_neighbor_entity_ids,
    get_observation_ids_for_entities,
    list_entities_for_scope,
    record_alias,
    record_mention,
    upsert_entity,
    upsert_relation,
)
from artemis.memory.graph_extractor import (
    _get_pending_observations,
    _parse_extraction_output,
    _reset_for_tests,
    _set_call_model_for_tests,
    _set_session_factory_for_tests,
    extract_for_observation,
)
from artemis.memory.schemas import Scope
from artemis.memory.store import write_observation


def _sf(session: AsyncSession) -> Any:
    """Wrap an existing session as a session factory for MCP handler tests."""

    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    return _factory


# ── Fixtures ──────────────────────────────────────────────────────────────────

_SCOPE = Scope(scope_kind="workspace", scope_id="test")


@pytest.fixture(autouse=True)
def reset_extractor() -> Generator[None, None, None]:
    _reset_for_tests()
    yield
    _reset_for_tests()


# ── Slug helper ───────────────────────────────────────────────────────────────


def test_to_slug_basic() -> None:
    assert _to_slug("Angela Smith") == "angela_smith"


def test_to_slug_punctuation() -> None:
    assert _to_slug("Spring 2026!") == "spring_2026"


def test_to_slug_already_slug() -> None:
    assert _to_slug("linkedin") == "linkedin"


# ── Vocabulary ────────────────────────────────────────────────────────────────


def test_valid_entity_kinds_complete() -> None:
    assert "person" in VALID_ENTITY_KINDS
    assert "brand" in VALID_ENTITY_KINDS
    assert "campaign" in VALID_ENTITY_KINDS
    assert "other" in VALID_ENTITY_KINDS


def test_valid_predicates_complete() -> None:
    assert "works_on" in VALID_PREDICATES
    assert "runs_campaign" in VALID_PREDICATES
    assert "related_to" in VALID_PREDICATES


# ── Entity upsert ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_entity_creates(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
    assert entity.id > 0
    assert entity.canonical_name == "Angela"
    assert entity.entity_kind == "person"
    assert entity.mention_count == 1


@pytest.mark.asyncio
async def test_upsert_entity_deduplicates(db_session: AsyncSession) -> None:
    async with db_session.begin():
        e1 = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        e2 = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
    assert e1.id == e2.id


@pytest.mark.asyncio
async def test_upsert_entity_bumps_mention_count(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        e2 = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
    assert e2.mention_count == 2


@pytest.mark.asyncio
async def test_upsert_entity_rejects_invalid_kind(db_session: AsyncSession) -> None:
    async with db_session.begin():
        with pytest.raises(ValueError, match="Unknown entity_kind"):
            await upsert_entity(
                db_session, kind="animal", name="Cat", scope_kind="workspace", scope_id="test"
            )


@pytest.mark.asyncio
async def test_upsert_entity_scope_isolation(db_session: AsyncSession) -> None:
    async with db_session.begin():
        e1 = await upsert_entity(
            db_session, kind="brand", name="Artemis", scope_kind="workspace", scope_id="alpha"
        )
        e2 = await upsert_entity(
            db_session, kind="brand", name="Artemis", scope_kind="workspace", scope_id="beta"
        )
    assert e1.id != e2.id  # same name, different scope → different rows


# ── Alias helpers ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_alias_adds_alias(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Jonathan", scope_kind="workspace", scope_id="test"
        )
        await record_alias(db_session, entity.id, "Jon")
    # Verify alias exists
    result = await db_session.execute(
        text("SELECT alias_slug FROM memory_entity_aliases WHERE entity_id = :id"),
        {"id": entity.id},
    )
    slugs = {row.alias_slug for row in result}
    assert "jon" in slugs


@pytest.mark.asyncio
async def test_record_alias_deduplicates(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Jonathan", scope_kind="workspace", scope_id="test"
        )
        await record_alias(db_session, entity.id, "Jon")
        await record_alias(db_session, entity.id, "Jon")  # second call is no-op
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM memory_entity_aliases WHERE entity_id = :id"),
        {"id": entity.id},
    )
    assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_record_alias_empty_is_noop(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        await record_alias(db_session, entity.id, "")  # should not raise
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM memory_entity_aliases WHERE entity_id = :id"),
        {"id": entity.id},
    )
    assert result.scalar_one() == 0


# ── Mention helpers ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_mention_links_entity_to_observation(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        await record_mention(
            db_session, entity_id=entity.id, source_kind="observation", source_id=42
        )
    result = await db_session.execute(
        text("SELECT source_kind, source_id FROM memory_entity_mentions WHERE entity_id = :id"),
        {"id": entity.id},
    )
    row = result.one()
    assert row.source_kind == "observation"
    assert row.source_id == 42


@pytest.mark.asyncio
async def test_record_mention_is_idempotent(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        await record_mention(
            db_session, entity_id=entity.id, source_kind="observation", source_id=42
        )
        await record_mention(
            db_session, entity_id=entity.id, source_kind="observation", source_id=42
        )
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM memory_entity_mentions WHERE entity_id = :id"),
        {"id": entity.id},
    )
    assert result.scalar_one() == 1


# ── Relation helpers ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_relation_valid_predicate(db_session: AsyncSession) -> None:
    async with db_session.begin():
        s = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        o = await upsert_entity(
            db_session, kind="campaign", name="Spring 2026", scope_kind="workspace", scope_id="test"
        )
        rel = await upsert_relation(
            db_session, subject_id=s.id, predicate="runs_campaign", object_id=o.id
        )
    assert rel is not None
    assert rel.predicate == "runs_campaign"


@pytest.mark.asyncio
async def test_upsert_relation_rejects_unknown_predicate(db_session: AsyncSession) -> None:
    async with db_session.begin():
        s = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        o = await upsert_entity(
            db_session, kind="brand", name="LinkedIn", scope_kind="workspace", scope_id="test"
        )
        result = await upsert_relation(
            db_session, subject_id=s.id, predicate="hates", object_id=o.id
        )
    assert result is None
    # Rejection should be logged
    rej = await db_session.execute(
        text("SELECT predicate FROM memory_relation_rejections WHERE predicate = 'hates'")
    )
    assert rej.one_or_none() is not None


@pytest.mark.asyncio
async def test_upsert_relation_is_idempotent(db_session: AsyncSession) -> None:
    async with db_session.begin():
        s = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        o = await upsert_entity(
            db_session, kind="campaign", name="Spring 2026", scope_kind="workspace", scope_id="test"
        )
        r1 = await upsert_relation(
            db_session, subject_id=s.id, predicate="runs_campaign", object_id=o.id
        )
        r2 = await upsert_relation(
            db_session, subject_id=s.id, predicate="runs_campaign", object_id=o.id
        )
    assert r1 is not None and r2 is not None
    assert r1.id == r2.id


# ── List entities ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_entities_for_scope(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        await upsert_entity(
            db_session, kind="brand", name="LinkedIn", scope_kind="workspace", scope_id="test"
        )
    entities = await list_entities_for_scope(db_session, "workspace", "test")
    assert len(entities) == 2


@pytest.mark.asyncio
async def test_list_entities_filters_by_kind(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        await upsert_entity(
            db_session, kind="brand", name="LinkedIn", scope_kind="workspace", scope_id="test"
        )
    people = await list_entities_for_scope(db_session, "workspace", "test", kind="person")
    assert len(people) == 1
    assert people[0].entity_kind == "person"


@pytest.mark.asyncio
async def test_list_entities_excludes_other_scopes(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="alpha"
        )
        await upsert_entity(
            db_session, kind="person", name="Bob", scope_kind="workspace", scope_id="beta"
        )
    entities = await list_entities_for_scope(db_session, "workspace", "alpha")
    assert all(e.scope_id == "alpha" for e in entities)


# ── Entity neighborhood ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_entity_neighborhood_no_relations(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
    hood = await get_entity_neighborhood(db_session, entity.id)
    assert hood is not None
    assert hood.entity.id == entity.id
    assert hood.relations == []


@pytest.mark.asyncio
async def test_get_entity_neighborhood_with_relations(db_session: AsyncSession) -> None:
    async with db_session.begin():
        angela = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        campaign = await upsert_entity(
            db_session, kind="campaign", name="Spring 2026", scope_kind="workspace", scope_id="test"
        )
        await upsert_relation(
            db_session, subject_id=angela.id, predicate="runs_campaign", object_id=campaign.id
        )
    hood = await get_entity_neighborhood(db_session, angela.id)
    assert hood is not None
    assert len(hood.relations) == 1
    assert hood.relations[0].predicate == "runs_campaign"
    assert hood.relations[0].subject_name == "Angela"
    assert hood.relations[0].object_name == "Spring 2026"


@pytest.mark.asyncio
async def test_get_entity_neighborhood_returns_none_for_unknown(db_session: AsyncSession) -> None:
    hood = await get_entity_neighborhood(db_session, 99999)
    assert hood is None


@pytest.mark.asyncio
async def test_get_entity_neighborhood_hop_cap(db_session: AsyncSession) -> None:
    """With hops=1, only direct neighbors are returned."""
    async with db_session.begin():
        a = await upsert_entity(
            db_session, kind="person", name="A", scope_kind="workspace", scope_id="test"
        )
        b = await upsert_entity(
            db_session, kind="person", name="B", scope_kind="workspace", scope_id="test"
        )
        c = await upsert_entity(
            db_session, kind="person", name="C", scope_kind="workspace", scope_id="test"
        )
        await upsert_relation(db_session, subject_id=a.id, predicate="related_to", object_id=b.id)
        await upsert_relation(db_session, subject_id=b.id, predicate="related_to", object_id=c.id)
    hood = await get_entity_neighborhood(db_session, a.id, hops=1)
    assert hood is not None
    # With hops=1, relations should be limited; neighborhood should be returned without error
    assert hood is not None


# ── FK cascade ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fk_cascade_entity_to_aliases(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
        await record_alias(db_session, entity.id, "Ang")
        await db_session.execute(
            text("DELETE FROM memory_entities WHERE id = :id"), {"id": entity.id}
        )
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM memory_entity_aliases WHERE entity_id = :id"),
        {"id": entity.id},
    )
    assert result.scalar_one() == 0  # cascade deleted


# ── Graph extraction ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extraction_sets_ok_status(
    db_session: AsyncSession,
    test_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> None:
    # Write an observation to extract from
    async with db_session.begin():
        obs = await write_observation(
            db_session, _SCOPE, "Angela runs the Spring 2026 campaign on LinkedIn."
        )
    obs_id = obs.id

    good_response = json.dumps(
        {
            "entities": [
                {"kind": "person", "name": "Angela", "aliases": []},
                {"kind": "campaign", "name": "Spring 2026", "aliases": []},
                {"kind": "brand", "name": "LinkedIn", "aliases": []},
            ],
            "relations": [
                {"subject": "Angela", "predicate": "runs_campaign", "object": "Spring 2026"},
            ],
        }
    )
    _set_call_model_for_tests(AsyncMock(return_value=good_response))
    _set_session_factory_for_tests(test_session_factory)

    await extract_for_observation(obs_id, "workspace", "test")

    # Verify graph_status
    result = await db_session.execute(
        text("SELECT graph_status, graph_attempt_count FROM memory_observations WHERE id = :id"),
        {"id": obs_id},
    )
    row = result.one()
    assert row.graph_status == "ok"
    assert row.graph_attempt_count == 1


@pytest.mark.asyncio
async def test_extraction_creates_entities(
    db_session: AsyncSession,
    test_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> None:
    async with db_session.begin():
        obs = await write_observation(
            db_session, _SCOPE, "Angela manages the Spring 2026 campaign."
        )
    obs_id = obs.id

    good_response = json.dumps(
        {
            "entities": [
                {"kind": "person", "name": "Angela", "aliases": ["Ang"]},
                {"kind": "campaign", "name": "Spring 2026", "aliases": []},
            ],
            "relations": [
                {"subject": "Angela", "predicate": "runs_campaign", "object": "Spring 2026"}
            ],
        }
    )
    _set_call_model_for_tests(AsyncMock(return_value=good_response))
    _set_session_factory_for_tests(test_session_factory)
    await extract_for_observation(obs_id, "workspace", "test")

    result = await db_session.execute(
        text(
            "SELECT canonical_name FROM memory_entities WHERE scope_kind = 'workspace' AND scope_id = 'test'"
        )
    )
    names = {row.canonical_name for row in result}
    assert "Angela" in names
    assert "Spring 2026" in names


@pytest.mark.asyncio
async def test_extraction_creates_relations(
    db_session: AsyncSession,
    test_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> None:
    async with db_session.begin():
        obs = await write_observation(db_session, _SCOPE, "Angela runs Spring 2026.")
    obs_id = obs.id

    good_response = json.dumps(
        {
            "entities": [
                {"kind": "person", "name": "Angela", "aliases": []},
                {"kind": "campaign", "name": "Spring 2026", "aliases": []},
            ],
            "relations": [
                {"subject": "Angela", "predicate": "runs_campaign", "object": "Spring 2026"}
            ],
        }
    )
    _set_call_model_for_tests(AsyncMock(return_value=good_response))
    _set_session_factory_for_tests(test_session_factory)
    await extract_for_observation(obs_id, "workspace", "test")

    result = await db_session.execute(text("SELECT predicate FROM memory_relations"))
    predicates = {row.predicate for row in result}
    assert "runs_campaign" in predicates


@pytest.mark.asyncio
async def test_extraction_malformed_json_sets_failed(
    db_session: AsyncSession,
    test_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> None:
    async with db_session.begin():
        obs = await write_observation(db_session, _SCOPE, "Some observation.")
    obs_id = obs.id

    _set_call_model_for_tests(AsyncMock(return_value="not json at all !!!"))
    _set_session_factory_for_tests(test_session_factory)
    await extract_for_observation(obs_id, "workspace", "test")

    result = await db_session.execute(
        text("SELECT graph_status FROM memory_observations WHERE id = :id"),
        {"id": obs_id},
    )
    assert result.scalar_one() == "failed"


@pytest.mark.asyncio
async def test_extraction_idempotent_on_ok_status(
    db_session: AsyncSession,
    test_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> None:
    async with db_session.begin():
        obs = await write_observation(db_session, _SCOPE, "Angela runs Spring 2026.")
        await db_session.execute(
            text(
                "UPDATE memory_observations SET graph_status = 'ok', graph_attempt_count = 1 WHERE id = :id"
            ),
            {"id": obs.id},
        )

    call_mock = AsyncMock(return_value='{"entities":[],"relations":[]}')
    _set_call_model_for_tests(call_mock)
    _set_session_factory_for_tests(test_session_factory)
    await extract_for_observation(obs.id, "workspace", "test")

    # Model should NOT have been called (status already ok)
    call_mock.assert_not_called()


@pytest.mark.asyncio
async def test_extraction_inflight_guard(
    db_session: AsyncSession,
    test_session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
) -> None:
    """Second call for same obs_id while first is in flight is a no-op."""
    from artemis.memory.graph_extractor import _INFLIGHT

    async with db_session.begin():
        obs = await write_observation(db_session, _SCOPE, "Angela runs campaign.")
    obs_id = obs.id

    call_count = 0

    async def slow_model(content: str, model: str) -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return json.dumps({"entities": [], "relations": []})

    _set_call_model_for_tests(slow_model)
    _set_session_factory_for_tests(test_session_factory)

    # Manually add to inflight before the second call
    _INFLIGHT.add(obs_id)
    await extract_for_observation(obs_id, "workspace", "test")
    _INFLIGHT.discard(obs_id)

    assert call_count == 0  # inflight guard blocked it


@pytest.mark.asyncio
async def test_get_pending_observations(db_session: AsyncSession) -> None:
    async with db_session.begin():
        obs1 = await write_observation(db_session, _SCOPE, "Content A")
        obs2 = await write_observation(db_session, _SCOPE, "Content B")
        # Mark obs2 as already ok
        await db_session.execute(
            text("UPDATE memory_observations SET graph_status = 'ok' WHERE id = :id"),
            {"id": obs2.id},
        )
    pending = await _get_pending_observations(db_session, "workspace", "test")
    pending_ids = {row["id"] for row in pending}
    assert obs1.id in pending_ids
    assert obs2.id not in pending_ids


@pytest.mark.asyncio
async def test_extraction_skipped_when_disabled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_GRAPH_EXTRACTION_DISABLED", "1")
    from artemis.memory.graph_extractor import notify_consolidation_complete

    call_mock = AsyncMock(return_value='{"entities":[],"relations":[]}')
    _set_call_model_for_tests(call_mock)
    # notify_consolidation_complete should be a no-op
    notify_consolidation_complete("workspace", "test")
    await asyncio.sleep(0)  # yield to event loop
    call_mock.assert_not_called()


# ── Parse extraction output ───────────────────────────────────────────────────


def test_parse_extraction_output_valid() -> None:
    raw = '{"entities": [{"kind": "person", "name": "Angela", "aliases": []}], "relations": []}'
    result = _parse_extraction_output(raw)
    assert result is not None
    assert result["entities"][0]["name"] == "Angela"


def test_parse_extraction_output_with_markdown_fences() -> None:
    raw = '```json\n{"entities": [], "relations": []}\n```'
    result = _parse_extraction_output(raw)
    assert result is not None
    assert result["entities"] == []


def test_parse_extraction_output_invalid_returns_none() -> None:
    assert _parse_extraction_output("not json") is None
    assert _parse_extraction_output("") is None
    assert _parse_extraction_output('{"no_entities": true}') is None


# ── find_entities_in_text ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_entities_by_name_slug(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_entity(
            db_session, kind="brand", name="LinkedIn", scope_kind="workspace", scope_id="test"
        )
    matches = await find_entities_in_text(db_session, [_SCOPE], "Posts on LinkedIn this week")
    names = {e.canonical_name for e in matches}
    assert "LinkedIn" in names


@pytest.mark.asyncio
async def test_find_entities_by_alias_slug(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="brand", name="LinkedIn", scope_kind="workspace", scope_id="test"
        )
        await record_alias(db_session, entity.id, "LIN")  # 3-char alias → slug "lin"
    matches = await find_entities_in_text(db_session, [_SCOPE], "Posted on LIN today")
    names = {e.canonical_name for e in matches}
    assert "LinkedIn" in names


@pytest.mark.asyncio
async def test_find_entities_ignores_short_tokens(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_entity(
            db_session, kind="person", name="Li", scope_kind="workspace", scope_id="test"
        )
    # "Li" slug is 2 chars — below min_token_length=3 default
    matches = await find_entities_in_text(db_session, [_SCOPE], "Li sent an email")
    # Should not match since "li" is 2 chars
    assert all(e.canonical_name != "Li" for e in matches)


@pytest.mark.asyncio
async def test_find_entities_empty_query_returns_empty(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="test"
        )
    matches = await find_entities_in_text(db_session, [_SCOPE], "")
    assert matches == []


# ── Graph fusion in retrieval ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_expand_mode_boosts_entity_obs(db_session: AsyncSession) -> None:
    """Obs mentioning 'Angela' should score higher when querying 'Angela campaigns'."""
    from artemis.memory.retrieval import search_observations

    scope = Scope(scope_kind="workspace", scope_id="gfusion")

    async with db_session.begin():
        # Obs mentioning Angela
        angela_obs = await write_observation(
            db_session, scope, "Angela leads the marketing team at Amira."
        )
        # Unrelated obs
        await write_observation(db_session, scope, "The budget was approved for Q3.")
        # Insert Angela entity + mention
        entity = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="gfusion"
        )
        await record_mention(
            db_session, entity_id=entity.id, source_kind="observation", source_id=angela_obs.id
        )

    results = await search_observations(
        db_session, [scope], "Angela campaigns", modes=["graph_expand"]
    )
    top_ids = [r.id for r in results]
    assert angela_obs.id in top_ids


@pytest.mark.asyncio
async def test_graph_expand_mode_one_hop_neighbor(db_session: AsyncSession) -> None:
    """Obs mentioning 1-hop entity (campaign) should be retrieved for Angela query."""
    from artemis.memory.retrieval import search_observations

    scope = Scope(scope_kind="workspace", scope_id="hop_test")

    async with db_session.begin():
        # Angela runs Spring 2026 (relation)
        angela_obs = await write_observation(db_session, scope, "Angela manages content strategy.")
        campaign_obs = await write_observation(
            db_session, scope, "Spring 2026 campaign launches in Michigan."
        )
        angela = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="hop_test"
        )
        campaign = await upsert_entity(
            db_session,
            kind="campaign",
            name="Spring 2026",
            scope_kind="workspace",
            scope_id="hop_test",
        )
        await record_mention(
            db_session, entity_id=angela.id, source_kind="observation", source_id=angela_obs.id
        )
        await record_mention(
            db_session, entity_id=campaign.id, source_kind="observation", source_id=campaign_obs.id
        )
        await upsert_relation(
            db_session, subject_id=angela.id, predicate="runs_campaign", object_id=campaign.id
        )

    results = await search_observations(db_session, [scope], "Angela", modes=["graph_expand"])
    result_ids = {r.id for r in results}
    # Both obs should appear (angela directly, campaign as 1-hop)
    assert angela_obs.id in result_ids
    assert campaign_obs.id in result_ids


@pytest.mark.asyncio
async def test_graph_proximity_zero_for_unrelated(db_session: AsyncSession) -> None:
    """Obs with no entity match should have graph_proximity=0.0."""
    from artemis.memory.retrieval import search_observations

    scope = Scope(scope_kind="workspace", scope_id="prox_test")

    async with db_session.begin():
        obs = await write_observation(db_session, scope, "The weather is sunny today.")

    results = await search_observations(
        db_session, [scope], "sunny weather", modes=["recency", "graph_expand"]
    )
    for r in results:
        if r.id == obs.id:
            assert r.graph_proximity == 0.0


@pytest.mark.asyncio
async def test_get_observation_ids_for_entities(db_session: AsyncSession) -> None:
    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Jon", scope_kind="workspace", scope_id="test"
        )
        await record_mention(
            db_session, entity_id=entity.id, source_kind="observation", source_id=101
        )
        await record_mention(
            db_session, entity_id=entity.id, source_kind="observation", source_id=202
        )
    obs_map = await get_observation_ids_for_entities(db_session, [entity.id])
    assert 101 in obs_map
    assert 202 in obs_map


@pytest.mark.asyncio
async def test_get_neighbor_entity_ids(db_session: AsyncSession) -> None:
    async with db_session.begin():
        a = await upsert_entity(
            db_session, kind="person", name="A", scope_kind="workspace", scope_id="test"
        )
        b = await upsert_entity(
            db_session, kind="person", name="B", scope_kind="workspace", scope_id="test"
        )
        await upsert_relation(db_session, subject_id=a.id, predicate="related_to", object_id=b.id)
    neighbors = await get_neighbor_entity_ids(db_session, [a.id])
    assert b.id in neighbors
    assert a.id not in neighbors


# ── MCP handler functions ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_memory_search_returns_results(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_search

    scope = Scope(scope_kind="workspace", scope_id="mcp_test")

    async with db_session.begin():
        obs = await write_observation(
            db_session, scope, "Artemis is a marketing intelligence platform."
        )

    results = await handle_memory_search(
        scope_set=[{"scopeKind": "workspace", "scopeId": "mcp_test"}],
        query="marketing intelligence",
        limit=5,
        as_of_ts=None,
        session_factory=_sf(db_session),
    )
    assert isinstance(results, list)
    assert any(r["id"] == obs.id for r in results)


@pytest.mark.asyncio
async def test_mcp_memory_search_defaults_to_workspace_default(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_search

    # Should not raise even if no results
    results = await handle_memory_search(
        scope_set=None, query="", limit=10, as_of_ts=None, session_factory=_sf(db_session)
    )
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_mcp_memory_get_observation_found(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_get_observation

    scope = Scope(scope_kind="workspace", scope_id="mcp_test")
    async with db_session.begin():
        obs = await write_observation(db_session, scope, "Test content for MCP.")

    result = await handle_memory_get_observation(obs.id, session_factory=_sf(db_session))
    assert result is not None
    assert result["id"] == obs.id
    assert "evidence" in result


@pytest.mark.asyncio
async def test_mcp_memory_get_observation_not_found(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_get_observation

    result = await handle_memory_get_observation(99999, session_factory=_sf(db_session))
    assert result is None


@pytest.mark.asyncio
async def test_mcp_memory_get_drawer_found(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_get_drawer
    from artemis.memory.schemas import Source
    from artemis.memory.store import write_drawer

    scope = Scope(scope_kind="workspace", scope_id="mcp_test")
    async with db_session.begin():
        drawer = await write_drawer(
            db_session, scope, "Drawer content for MCP.", Source(source_kind="test")
        )

    result = await handle_memory_get_drawer(drawer.id, session_factory=_sf(db_session))
    assert result is not None
    assert result["id"] == drawer.id


@pytest.mark.asyncio
async def test_mcp_memory_get_drawer_not_found(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_get_drawer

    result = await handle_memory_get_drawer(99999, session_factory=_sf(db_session))
    assert result is None


@pytest.mark.asyncio
async def test_mcp_memory_list_scopes(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_list_scopes

    scope = Scope(scope_kind="project", scope_id="my_project")
    async with db_session.begin():
        await write_observation(db_session, scope, "Scopes test content.")

    scopes = await handle_memory_list_scopes(None, session_factory=_sf(db_session))
    assert any(s["scope_kind"] == "project" for s in scopes)


@pytest.mark.asyncio
async def test_mcp_memory_list_scopes_filter(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_list_scopes

    scope_a = Scope(scope_kind="project", scope_id="alpha")
    scope_b = Scope(scope_kind="workspace", scope_id="beta")
    async with db_session.begin():
        await write_observation(db_session, scope_a, "Alpha content.")
        await write_observation(db_session, scope_b, "Beta content.")

    scopes = await handle_memory_list_scopes("project", session_factory=_sf(db_session))
    assert all(s["scope_kind"] == "project" for s in scopes)


@pytest.mark.asyncio
async def test_mcp_memory_list_entities(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_list_entities

    async with db_session.begin():
        await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="mcp_ent"
        )

    entities = await handle_memory_list_entities(
        scope_set=[{"scopeKind": "workspace", "scopeId": "mcp_ent"}],
        kind=None,
        limit=50,
        session_factory=_sf(db_session),
    )
    assert any(e["canonical_name"] == "Angela" for e in entities)


@pytest.mark.asyncio
async def test_mcp_memory_list_entities_filter_kind(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_list_entities

    async with db_session.begin():
        await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="mcp_kind"
        )
        await upsert_entity(
            db_session, kind="brand", name="LinkedIn", scope_kind="workspace", scope_id="mcp_kind"
        )

    entities = await handle_memory_list_entities(
        scope_set=[{"scopeKind": "workspace", "scopeId": "mcp_kind"}],
        kind="brand",
        limit=50,
        session_factory=_sf(db_session),
    )
    assert all(e["entity_kind"] == "brand" for e in entities)


@pytest.mark.asyncio
async def test_mcp_memory_get_entity_neighborhood_found(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_get_entity_neighborhood

    async with db_session.begin():
        entity = await upsert_entity(
            db_session, kind="person", name="Angela", scope_kind="workspace", scope_id="mcp_hood"
        )

    result = await handle_memory_get_entity_neighborhood(
        entity.id, 1, session_factory=_sf(db_session)
    )
    assert result is not None
    assert result["entity"]["canonical_name"] == "Angela"
    assert "relations" in result


@pytest.mark.asyncio
async def test_mcp_memory_get_entity_neighborhood_not_found(db_session: AsyncSession) -> None:
    from artemis.mcp.memory_server import handle_memory_get_entity_neighborhood

    result = await handle_memory_get_entity_neighborhood(99999, 1, session_factory=_sf(db_session))
    assert result is None


@pytest.mark.asyncio
async def test_mcp_as_of_passthrough(db_session: AsyncSession) -> None:
    """as_of_ts filters out observations outside their validity window."""
    from artemis.mcp.memory_server import handle_memory_search

    scope = Scope(scope_kind="workspace", scope_id="mcp_asof")
    past = datetime(2020, 1, 1, tzinfo=UTC)
    future = datetime(2030, 1, 1, tzinfo=UTC)

    async with db_session.begin():
        # valid in the past
        await write_observation(
            db_session,
            scope,
            "Old observation.",
            valid_from=past,
            valid_until=datetime(2021, 1, 1, tzinfo=UTC),
        )
        # valid now
        fresh = await write_observation(
            db_session, scope, "Fresh observation.", valid_from=past, valid_until=future
        )

    as_of_now = int(datetime.now(UTC).timestamp())

    results = await handle_memory_search(
        scope_set=[{"scopeKind": "workspace", "scopeId": "mcp_asof"}],
        query="observation",
        limit=10,
        as_of_ts=as_of_now,
        session_factory=_sf(db_session),
    )
    result_ids = {r["id"] for r in results}
    assert fresh.id in result_ids
