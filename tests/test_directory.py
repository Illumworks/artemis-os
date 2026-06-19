"""Tests for the name→email directory resolver + Slack sync.

Resolver tests seed DirectoryPerson rows directly in the test DB; sync tests
monkeypatch the Slack roster source so no live Slack call is made.

DB: uses ARTEMIS_TEST_DB_URL (set by conftest) which must be at head (0100+).
"""

from __future__ import annotations

import os as _os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
from artemis.db import attach_pgvector_codec
from artemis.directory.models import DirectoryPerson
from artemis.directory.resolver import resolve_one, resolve_people
from artemis.directory.sync import sync_directory_from_slack

pytestmark = pytest.mark.asyncio

_db_url = _os.environ.get("ARTEMIS_TEST_DB_URL") or _os.environ.get(
    "ARTEMIS_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)

if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session that clears directory_people before/after each test."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await session.execute(text("DELETE FROM directory_people"))
            await session.commit()
            yield session
            await session.execute(text("DELETE FROM directory_people"))
            await session.commit()
    finally:
        await engine.dispose()


def _person(
    *,
    email: str,
    full_name: str,
    first_name: str | None = None,
    last_name: str | None = None,
    is_active: bool = True,
) -> DirectoryPerson:
    return DirectoryPerson(
        email=email.lower(),
        full_name=full_name,
        display_name=None,
        first_name=first_name,
        last_name=last_name,
        slack_user_id=None,
        source="slack",
        is_active=is_active,
    )


async def _seed(session: AsyncSession, people: list[DirectoryPerson]) -> None:
    for p in people:
        session.add(p)
    await session.commit()


# ── Resolver tests ──────────────────────────────────────────────────────────


async def test_exact_email(db_session: AsyncSession) -> None:
    await _seed(db_session, [_person(email="angela@amiralearning.com", full_name="Angela Smith",
                                     first_name="Angela", last_name="Smith")])
    assert await resolve_one("Angela@amiralearning.com", db_session) == "angela@amiralearning.com"
    matches = await resolve_people("angela@amiralearning.com", db_session)
    assert matches[0].confidence == 1.0


async def test_exact_full_name(db_session: AsyncSession) -> None:
    await _seed(db_session, [_person(email="greg@istation.com", full_name="Greg Shrader",
                                     first_name="Greg", last_name="Shrader")])
    assert await resolve_one("Greg Shrader", db_session) == "greg@istation.com"


async def test_first_last(db_session: AsyncSession) -> None:
    await _seed(db_session, [
        _person(email="kristen.j@amiralearning.com", full_name="Kristen Jones",
                first_name="Kristen", last_name="Jones"),
        _person(email="bob@amiralearning.com", full_name="Bob Lee",
                first_name="Bob", last_name="Lee"),
    ])
    assert await resolve_one("Kristen Jones", db_session) == "kristen.j@amiralearning.com"


async def test_first_initial_single_resolves(db_session: AsyncSession) -> None:
    """'Julie K' → single Julie K... resolves."""
    await _seed(db_session, [
        _person(email="julie.k@amiralearning.com", full_name="Julie Kim",
                first_name="Julie", last_name="Kim"),
        _person(email="mark@amiralearning.com", full_name="Mark Stone",
                first_name="Mark", last_name="Stone"),
    ])
    matches = await resolve_people("Julie K", db_session)
    assert matches[0].email == "julie.k@amiralearning.com"
    assert matches[0].confidence == pytest.approx(0.90)
    assert await resolve_one("Julie K", db_session) == "julie.k@amiralearning.com"


async def test_first_initial_ambiguous_returns_none(db_session: AsyncSession) -> None:
    """Two Julie K... people → ambiguous, resolve_one returns None."""
    await _seed(db_session, [
        _person(email="julie.kim@amiralearning.com", full_name="Julie Kim",
                first_name="Julie", last_name="Kim"),
        _person(email="julie.knox@amiralearning.com", full_name="Julie Knox",
                first_name="Julie", last_name="Knox"),
    ])
    matches = await resolve_people("Julie K", db_session)
    # Both demoted to ambiguous tier.
    assert all(m.reason == "ambiguous" for m in matches)
    assert all(m.confidence < 0.90 for m in matches)
    assert await resolve_one("Julie K", db_session) is None


async def test_first_name_only_ambiguous(db_session: AsyncSession) -> None:
    await _seed(db_session, [
        _person(email="julie.a@amiralearning.com", full_name="Julie Adams",
                first_name="Julie", last_name="Adams"),
        _person(email="julie.b@amiralearning.com", full_name="Julie Brown",
                first_name="Julie", last_name="Brown"),
    ])
    matches = await resolve_people("Julie", db_session)
    assert len(matches) == 2
    assert all(m.reason == "ambiguous" for m in matches)
    assert await resolve_one("Julie", db_session) is None


async def test_fuzzy_match(db_session: AsyncSession) -> None:
    await _seed(db_session, [_person(email="kristen@amiralearning.com", full_name="Kristen Jameson",
                                     first_name="Kristen", last_name="Jameson")])
    matches = await resolve_people("Kristin Jameson", db_session)  # misspelled first name
    assert matches
    assert matches[0].email == "kristen@amiralearning.com"
    assert matches[0].reason == "fuzzy"


async def test_unknown_returns_none(db_session: AsyncSession) -> None:
    await _seed(db_session, [_person(email="greg@istation.com", full_name="Greg Shrader",
                                     first_name="Greg", last_name="Shrader")])
    assert await resolve_one("Zxqwerty Nobody", db_session) is None
    assert await resolve_people("Zxqwerty Nobody", db_session) == []


async def test_inactive_excluded(db_session: AsyncSession) -> None:
    await _seed(db_session, [_person(email="gone@amiralearning.com", full_name="Gone Person",
                                     first_name="Gone", last_name="Person", is_active=False)])
    assert await resolve_people("Gone Person", db_session) == []


# ── Sync tests ────────────────────────────────────────────────────────────────


class _FakeSlackClient:
    def __init__(self, members: list[dict]) -> None:
        self._members = members

    async def list_users(self, limit: int = 200) -> list[dict]:
        return self._members


def _members_v1() -> list[dict]:
    return [
        {"id": "U1", "real_name": "Angela Smith",
         "profile": {"email": "Angela@amiralearning.com", "display_name": "Ang"}},
        {"id": "U2", "real_name": "Greg Shrader",
         "profile": {"email": "greg@istation.com"}},
        {"id": "U3", "real_name": "Botty", "is_bot": True,
         "profile": {"email": "bot@amiralearning.com"}},
        {"id": "U4", "real_name": "No Email Person", "profile": {}},
    ]


async def test_sync_upserts(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_loader() -> _FakeSlackClient:
        return _FakeSlackClient(_members_v1())

    monkeypatch.setattr(
        "artemis.directory.sync._load_working_slack_client", _fake_loader
    )

    count = await sync_directory_from_slack(db_session)
    # Bot + no-email rows skipped → 2 upserted.
    assert count == 2

    rows = (await db_session.execute(text("SELECT email, first_name, last_name FROM directory_people ORDER BY email"))).all()
    emails = {r[0] for r in rows}
    assert emails == {"angela@amiralearning.com", "greg@istation.com"}
    # Name split + lowercased email.
    angela = next(r for r in rows if r[0] == "angela@amiralearning.com")
    assert angela[1] == "Angela" and angela[2] == "Smith"

    # resolve against the synced rows.
    assert await resolve_one("Greg Shrader", db_session) == "greg@istation.com"


async def test_sync_rerun_no_dupes(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_loader() -> _FakeSlackClient:
        return _FakeSlackClient(_members_v1())

    monkeypatch.setattr(
        "artemis.directory.sync._load_working_slack_client", _fake_loader
    )

    await sync_directory_from_slack(db_session)
    await sync_directory_from_slack(db_session)

    total = (await db_session.execute(text("SELECT COUNT(*) FROM directory_people"))).scalar_one()
    assert total == 2  # no duplicates on re-run
