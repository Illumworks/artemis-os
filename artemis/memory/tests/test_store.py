"""Tests for memory keystone storage + write path (Slice B1).

Coverage targets:
  - Drawer + observation + evidence happy path (write, read, link)
  - Unique constraint enforcement (same content_hash within scope → idempotent)
  - Supersession sets superseded_by, queryable, old row preserved (lossless)
  - Evidence chains traversable from observation → drawer
  - Lossless invariant: no public API deletes drawers or observations
  - Scope round-trip for all six scope kinds
  - owner_user_id round-trip
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.schemas import Drawer, Observation, Scope, Source
from artemis.memory.store import (
    get_drawer,
    get_observation,
    link_evidence,
    list_evidence_for_observation,
    supersede_observation,
    write_drawer,
    write_observation,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

_DEFAULT_SCOPE = Scope(scope_kind="workspace", scope_id="test-default")
_DEFAULT_SOURCE = Source(source_kind="user", source_id="u1")


async def _drawer(
    session: AsyncSession,
    content: str = "drawer content",
    scope: Scope = _DEFAULT_SCOPE,
    source: Source = _DEFAULT_SOURCE,
    corpus_kind: str | None = None,
    owner_user_id: int | None = None,
) -> Drawer:
    async with session.begin():
        return await write_drawer(
            session,
            scope,
            content,
            source,
            corpus_kind=corpus_kind,
            owner_user_id=owner_user_id,
        )


async def _observation(
    session: AsyncSession,
    content: str = "observation content",
    scope: Scope = _DEFAULT_SCOPE,
    category: str = "discovery",
    source_quality: float = 0.5,
    owner_user_id: int | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> Observation:
    async with session.begin():
        return await write_observation(
            session,
            scope,
            content,
            category=category,
            source_quality=source_quality,
            owner_user_id=owner_user_id,
            valid_from=valid_from,
            valid_until=valid_until,
        )


# ── Drawer tests ─────────────────────────────────────────────────────────────


async def test_write_drawer_returns_drawer_with_id(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session)
    assert drawer.id > 0


async def test_write_drawer_content_stored(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session, content="specific content")
    assert drawer.content == "specific content"


async def test_write_drawer_content_hash_computed(db_session: AsyncSession) -> None:
    import hashlib

    content = "hash me"
    scope = _DEFAULT_SCOPE
    drawer = await _drawer(db_session, content=content, scope=scope)
    expected = hashlib.sha256(
        f"{scope.scope_kind}:{scope.scope_id}:{content}".encode()
    ).hexdigest()
    assert drawer.content_hash == expected


async def test_write_drawer_source_stored(db_session: AsyncSession) -> None:
    source = Source(source_kind="document", source_id="doc-99", source_extra={"url": "http://example.com"})
    drawer = await _drawer(db_session, source=source)
    assert drawer.source_kind == "document"
    assert drawer.source_id == "doc-99"
    assert drawer.source_extra == {"url": "http://example.com"}


async def test_write_drawer_corpus_kind_stored(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session, corpus_kind="voice_rule")
    assert drawer.corpus_kind == "voice_rule"


async def test_write_drawer_corpus_kind_defaults_none(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session)
    assert drawer.corpus_kind is None


async def test_write_drawer_owner_user_id_stored(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session, owner_user_id=42)
    assert drawer.owner_user_id == 42


async def test_write_drawer_owner_user_id_defaults_none(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session)
    assert drawer.owner_user_id is None


async def test_write_drawer_captured_at_set(db_session: AsyncSession) -> None:
    before = datetime.now(timezone.utc)
    drawer = await _drawer(db_session)
    assert drawer.captured_at >= before


async def test_write_drawer_duplicate_returns_existing(db_session: AsyncSession) -> None:
    """Same content in same scope is idempotent — returns the original row."""
    d1 = await _drawer(db_session, content="same content")
    d2 = await _drawer(db_session, content="same content")
    assert d1.id == d2.id
    assert d1.content_hash == d2.content_hash


async def test_write_drawer_different_content_different_rows(db_session: AsyncSession) -> None:
    d1 = await _drawer(db_session, content="alpha")
    d2 = await _drawer(db_session, content="beta")
    assert d1.id != d2.id


async def test_get_drawer_returns_drawer(db_session: AsyncSession) -> None:
    written = await _drawer(db_session)
    async with db_session.begin():
        fetched = await get_drawer(db_session, written.id)
    assert fetched is not None
    assert fetched.id == written.id
    assert fetched.content == written.content


async def test_get_drawer_missing_returns_none(db_session: AsyncSession) -> None:
    async with db_session.begin():
        result = await get_drawer(db_session, 999_999)
    assert result is None


# ── Observation tests ─────────────────────────────────────────────────────────


async def test_write_observation_returns_observation_with_id(db_session: AsyncSession) -> None:
    obs = await _observation(db_session)
    assert obs.id > 0


async def test_write_observation_content_stored(db_session: AsyncSession) -> None:
    obs = await _observation(db_session, content="insight A")
    assert obs.content == "insight A"


async def test_write_observation_category_default(db_session: AsyncSession) -> None:
    obs = await _observation(db_session)
    assert obs.category == "discovery"


async def test_write_observation_category_custom(db_session: AsyncSession) -> None:
    obs = await _observation(db_session, category="voice")
    assert obs.category == "voice"


async def test_write_observation_source_quality_stored(db_session: AsyncSession) -> None:
    obs = await _observation(db_session, source_quality=0.9)
    assert obs.source_quality == pytest.approx(0.9)


async def test_write_observation_source_quality_default(db_session: AsyncSession) -> None:
    obs = await _observation(db_session)
    assert obs.source_quality == pytest.approx(0.5)


async def test_write_observation_score_defaults_to_one(db_session: AsyncSession) -> None:
    obs = await _observation(db_session)
    assert obs.score == pytest.approx(1.0)


async def test_write_observation_valid_from_until(db_session: AsyncSession) -> None:
    vf = datetime(2026, 1, 1, tzinfo=timezone.utc)
    vu = datetime(2026, 12, 31, tzinfo=timezone.utc)
    obs = await _observation(db_session, valid_from=vf, valid_until=vu)
    assert obs.valid_from is not None
    assert obs.valid_until is not None
    assert obs.valid_from.year == 2026
    assert obs.valid_until.month == 12


async def test_write_observation_valid_windows_default_none(db_session: AsyncSession) -> None:
    obs = await _observation(db_session)
    assert obs.valid_from is None
    assert obs.valid_until is None


async def test_write_observation_duplicate_returns_existing(db_session: AsyncSession) -> None:
    """Same content in same scope deduplicates — returns original row."""
    o1 = await _observation(db_session, content="same insight")
    o2 = await _observation(db_session, content="same insight")
    assert o1.id == o2.id


async def test_write_observation_not_superseded_on_create(db_session: AsyncSession) -> None:
    obs = await _observation(db_session)
    assert obs.superseded_by is None


async def test_get_observation_returns_observation(db_session: AsyncSession) -> None:
    written = await _observation(db_session)
    async with db_session.begin():
        fetched = await get_observation(db_session, written.id)
    assert fetched is not None
    assert fetched.id == written.id


async def test_get_observation_missing_returns_none(db_session: AsyncSession) -> None:
    async with db_session.begin():
        result = await get_observation(db_session, 999_999)
    assert result is None


# ── Evidence tests ────────────────────────────────────────────────────────────


async def test_link_evidence_drawer(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session)
    obs = await _observation(db_session)
    async with db_session.begin():
        ev = await link_evidence(db_session, obs.id, "drawer", drawer.id, weight=0.8)
    assert ev.id > 0
    assert ev.observation_id == obs.id
    assert ev.source_kind == "drawer"
    assert ev.source_id == drawer.id
    assert ev.weight == pytest.approx(0.8)


async def test_link_evidence_observation(db_session: AsyncSession) -> None:
    src_obs = await _observation(db_session, content="source obs")
    target_obs = await _observation(db_session, content="target obs")
    async with db_session.begin():
        ev = await link_evidence(db_session, target_obs.id, "observation", src_obs.id)
    assert ev.source_kind == "observation"
    assert ev.source_id == src_obs.id


async def test_link_evidence_source_quote_stored(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session)
    obs = await _observation(db_session)
    async with db_session.begin():
        ev = await link_evidence(
            db_session, obs.id, "drawer", drawer.id, source_quote="exact phrase"
        )
    assert ev.source_quote == "exact phrase"


async def test_link_evidence_idempotent(db_session: AsyncSession) -> None:
    """Duplicate link is silently ignored; original record returned."""
    drawer = await _drawer(db_session)
    obs = await _observation(db_session)
    async with db_session.begin():
        ev1 = await link_evidence(db_session, obs.id, "drawer", drawer.id)
    async with db_session.begin():
        ev2 = await link_evidence(db_session, obs.id, "drawer", drawer.id)
    assert ev1.id == ev2.id


async def test_list_evidence_for_observation(db_session: AsyncSession) -> None:
    d1 = await _drawer(db_session, content="drawer one")
    d2 = await _drawer(db_session, content="drawer two")
    obs = await _observation(db_session)
    async with db_session.begin():
        await link_evidence(db_session, obs.id, "drawer", d1.id, weight=0.9)
        await link_evidence(db_session, obs.id, "drawer", d2.id, weight=0.5)
    async with db_session.begin():
        evs = await list_evidence_for_observation(db_session, obs.id)
    assert len(evs) == 2
    # Ordered by weight DESC
    assert evs[0].weight > evs[1].weight


async def test_list_evidence_empty_for_new_observation(db_session: AsyncSession) -> None:
    obs = await _observation(db_session)
    async with db_session.begin():
        evs = await list_evidence_for_observation(db_session, obs.id)
    assert evs == []


async def test_evidence_chain_traversal_obs_to_drawer(db_session: AsyncSession) -> None:
    """Evidence is traversable from observation back to source drawer."""
    drawer = await _drawer(db_session, content="source text")
    obs = await _observation(db_session)
    async with db_session.begin():
        await link_evidence(
            db_session, obs.id, "drawer", drawer.id, source_quote="source text"
        )
        evs = await list_evidence_for_observation(db_session, obs.id)
        assert len(evs) == 1
        fetched_drawer = await get_drawer(db_session, evs[0].source_id)
    assert fetched_drawer is not None
    assert fetched_drawer.content == "source text"


# ── Supersession tests ────────────────────────────────────────────────────────


async def test_supersede_observation_sets_field(db_session: AsyncSession) -> None:
    old = await _observation(db_session, content="old insight")
    new = await _observation(db_session, content="refined insight")
    async with db_session.begin():
        await supersede_observation(db_session, old.id, new.id)
        refreshed = await get_observation(db_session, old.id)
    assert refreshed is not None
    assert refreshed.superseded_by == new.id


async def test_supersede_observation_old_row_still_queryable(db_session: AsyncSession) -> None:
    """Lossless: superseded observation is still in the DB and readable."""
    old = await _observation(db_session, content="stale insight")
    new = await _observation(db_session, content="current insight")
    async with db_session.begin():
        await supersede_observation(db_session, old.id, new.id)
    async with db_session.begin():
        still_there = await get_observation(db_session, old.id)
    assert still_there is not None
    assert still_there.content == "stale insight"


async def test_supersede_observation_no_double_supersession(db_session: AsyncSession) -> None:
    """Once superseded, further supersede_observation calls do not change superseded_by."""
    o1 = await _observation(db_session, content="v1")
    o2 = await _observation(db_session, content="v2")
    o3 = await _observation(db_session, content="v3")
    async with db_session.begin():
        await supersede_observation(db_session, o1.id, o2.id)
    async with db_session.begin():
        # Second call should be a no-op since o1 is already superseded
        await supersede_observation(db_session, o1.id, o3.id)
    async with db_session.begin():
        o1_state = await get_observation(db_session, o1.id)
    assert o1_state is not None
    # Still points to o2, not o3
    assert o1_state.superseded_by == o2.id


# ── owner_user_id round-trip tests ────────────────────────────────────────────


async def test_owner_user_id_drawer_set_and_read(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session, owner_user_id=7)
    async with db_session.begin():
        fetched = await get_drawer(db_session, drawer.id)
    assert fetched is not None
    assert fetched.owner_user_id == 7


async def test_owner_user_id_observation_set_and_read(db_session: AsyncSession) -> None:
    obs = await _observation(db_session, owner_user_id=7)
    async with db_session.begin():
        fetched = await get_observation(db_session, obs.id)
    assert fetched is not None
    assert fetched.owner_user_id == 7


async def test_owner_user_id_drawer_default_none(db_session: AsyncSession) -> None:
    drawer = await _drawer(db_session)
    async with db_session.begin():
        fetched = await get_drawer(db_session, drawer.id)
    assert fetched is not None
    assert fetched.owner_user_id is None


async def test_owner_user_id_observation_default_none(db_session: AsyncSession) -> None:
    obs = await _observation(db_session)
    async with db_session.begin():
        fetched = await get_observation(db_session, obs.id)
    assert fetched is not None
    assert fetched.owner_user_id is None


# ── Scope kind round-trip tests ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "scope_kind,scope_id",
    [
        ("project", "/Users/jon/Desktop/artemis-os"),
        ("workspace", "default"),
        ("brand", "artemis-marketing"),
        ("agent", "bug-hunter"),
        ("skill", "resume-session"),
        ("global", "global"),
    ],
)
async def test_scope_kind_drawer_round_trip(
    db_session: AsyncSession, scope_kind: str, scope_id: str
) -> None:
    scope = Scope(scope_kind=scope_kind, scope_id=scope_id)  # type: ignore[arg-type]
    source = Source(source_kind="test")
    async with db_session.begin():
        drawer = await write_drawer(db_session, scope, "scoped content", source)
    assert drawer.scope_kind == scope_kind
    assert drawer.scope_id == scope_id


@pytest.mark.parametrize(
    "scope_kind,scope_id",
    [
        ("project", "/Users/jon/Desktop/artemis-os"),
        ("workspace", "default"),
        ("brand", "artemis-marketing"),
        ("agent", "bug-hunter"),
        ("skill", "resume-session"),
        ("global", "global"),
    ],
)
async def test_scope_kind_observation_round_trip(
    db_session: AsyncSession, scope_kind: str, scope_id: str
) -> None:
    scope = Scope(scope_kind=scope_kind, scope_id=scope_id)  # type: ignore[arg-type]
    async with db_session.begin():
        obs = await write_observation(db_session, scope, "scoped observation")
    assert obs.scope_kind == scope_kind
    assert obs.scope_id == scope_id


# ── Lossless rule — no public delete API ─────────────────────────────────────


def test_no_delete_drawer_in_public_api() -> None:
    """The store module must not expose any delete_drawer function."""
    import artemis.memory.store as store_module

    public_names = [n for n in dir(store_module) if not n.startswith("_")]
    delete_names = [n for n in public_names if "delete" in n.lower() and "drawer" in n.lower()]
    assert delete_names == [], f"Found unexpected delete functions: {delete_names}"


def test_no_delete_observation_in_public_api() -> None:
    """The store module must not expose any delete_observation function."""
    import artemis.memory.store as store_module

    public_names = [n for n in dir(store_module) if not n.startswith("_")]
    delete_names = [n for n in public_names if "delete" in n.lower() and "observation" in n.lower()]
    assert delete_names == [], f"Found unexpected delete functions: {delete_names}"


def test_store_public_api_is_complete() -> None:
    """All declared public functions in store.py are async coroutines."""
    import artemis.memory.store as store_module

    public_fns = [
        getattr(store_module, n)
        for n in dir(store_module)
        if not n.startswith("_") and callable(getattr(store_module, n))
        and inspect.iscoroutinefunction(getattr(store_module, n))
    ]
    # At minimum the seven specified functions must be present
    fn_names = {f.__name__ for f in public_fns}
    required = {
        "write_drawer",
        "write_observation",
        "link_evidence",
        "supersede_observation",
        "get_drawer",
        "get_observation",
        "list_evidence_for_observation",
    }
    assert required <= fn_names
