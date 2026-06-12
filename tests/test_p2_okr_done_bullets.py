"""Tests for the P2 OKR done_bullets brief.

Verifies that on the "go" apply path, a concise accomplishment bullet is
appended (losslessly) to each applied KR's done_bullets JSONB list.

Covers:
  DB1. append_done_bullet helper appends to an existing list (lossless).
  DB2. append_done_bullet on an empty list starts a new list.
  DB3. append_done_bullet with a blank text is a no-op.
  DB4. append_done_bullet on a non-existent KR returns None gracefully.

  S1. stage_okr_updates with a bullet field persists it in staged_updates.
  S2. stage_okr_updates without a bullet still persists the entry (bullet absent).

  R1. route_inbound "go" with staged bullet → done_bullets gains the bullet AND
      existing bullets are preserved (3 existing → 4 after).
  R2. route_inbound "go" WITHOUT staged bullet → falls back to trimmed basis as
      the bullet; done_bullets is still updated (non-empty).
  R3. route_inbound "no" → done_bullets unchanged (zero writes).
  R4. prog write + activity log still happen (no regression).

All DB-backed tests use the isolated artemis_test DB (NullPool, per-test TRUNCATE).
Slack + LLM calls are fully mocked.
"""

from __future__ import annotations

import os as _os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.okr.models  # noqa: F401 — registers OKR models on Base.metadata
import artemis.proactivity.models  # noqa: F401 — registers breadcrumb model
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Module-level DB setup — rebind global engine/SessionLocal to the test DB
# ---------------------------------------------------------------------------

_SPEAKER_ID = "U_JON_OKR_BULLETS"
_TEAM_ID = "T_TEST_BULLETS"
_CHANNEL_ID = "D_BULLETS_DM"
_AGENT_ID = "artemis"

_db_url = _os.environ.get("ARTEMIS_TEST_DB_URL") or _os.environ.get(
    "ARTEMIS_DB_URL", "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test"
)

if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "okr_checkin_breadcrumbs, "
    "okr_update_previews, "
    "okr_next_up, "
    "okr_activity, "
    "okr_key_results, "
    "okr_objectives "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session. Truncates OKR + breadcrumb tables."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _make_objective_and_kr(
    session: AsyncSession,
    kr_count: int = 1,
    initial_done_bullets: list[str] | None = None,
) -> list[int]:
    """Create one objective with kr_count key results (prog=10). Returns KR IDs.

    All KRs get the same initial_done_bullets list (default: empty list).
    """
    from artemis.okr import repository as repo

    obj = await repo.create_objective(session, title="Test Obj Bullets", cycle="Q2-2026")
    await session.flush()
    kr_ids: list[int] = []
    bullets = initial_done_bullets if initial_done_bullets is not None else []
    for i in range(kr_count):
        kr = await repo.create_key_result(
            session,
            objective_id=obj.id,
            title=f"Test KR Bullets {i + 1}",
            status="ontrack",
            prog=10,
            done_bullets=list(bullets),
        )
        await session.flush()
        kr_ids.append(kr.id)
    await session.commit()
    return kr_ids


async def _seed_breadcrumb(
    session: AsyncSession,
    recipient_id: str = _SPEAKER_ID,
    kr_ids: list[int] | None = None,
) -> int:
    """Insert a live breadcrumb. Returns its id."""
    from artemis.proactivity import repository as prepo

    snapshot = [
        {"kr_id": kid, "kr_title": f"KR {kid}", "objective_title": "Obj", "prog": 10}
        for kid in (kr_ids or [])
    ]
    crumb = await prepo.create_okr_checkin_breadcrumb(
        session,
        recipient_id=recipient_id,
        kr_snapshot=snapshot,
        proposal_text="Friday check-in bullets",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await session.commit()
    return crumb.id


async def _fresh_kr(kr_id: int) -> Any:
    """Read a KR row through a brand-new session (no identity-map staleness)."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        from artemis.okr import repository as repo

        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await repo.get_key_result(session, kr_id)
    finally:
        await engine.dispose()


async def _fresh_activity_count() -> int:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        from artemis.okr import repository as repo

        async with AsyncSession(engine, expire_on_commit=False) as session:
            return len(await repo.list_activity(session, limit=500))
    finally:
        await engine.dispose()


async def _fresh_breadcrumb(crumb_id: int) -> Any:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        from artemis.proactivity.models import OkrCheckinBreadcrumb

        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await session.get(OkrCheckinBreadcrumb, crumb_id)
    finally:
        await engine.dispose()


def _make_event_data(text_body: str, user: str = _SPEAKER_ID) -> dict[str, Any]:
    return {
        "team_id": _TEAM_ID,
        "channel": _CHANNEL_ID,
        "user": user,
        "text": text_body,
        "ts": "111.000",
        "thread_ts": None,
    }


async def _run_route_inbound(
    text_body: str,
    verdict: str,
    handle_mock: AsyncMock | None = None,
) -> list[str]:
    """Drive route_inbound with Slack + session deps mocked. Returns posted texts."""
    from artemis.routes.integrations_slack_events import route_inbound

    posted: list[str] = []

    async def _fake_post(*, channel: str, text: str, thread_ts: str | None = None) -> None:
        posted.append(text)

    mock_client = MagicMock()
    mock_client.post_message = _fake_post

    agent_cfg = MagicMock()
    agent_cfg.access_token = "xoxb-test"

    if handle_mock is None:
        handle_result = MagicMock()
        handle_result.response_text = "Normal reply."
        handle_result.pending_tool_use_id = None
        handle_mock = AsyncMock(return_value=handle_result)

    async def _classifier(_t: str) -> str:
        return verdict

    with (
        patch(
            "artemis.integrations.repository.get_slack_user",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            new_callable=AsyncMock,
            side_effect=ValueError("not found"),
        ),
        patch("artemis.floating_artemis.repository.create_session", new_callable=AsyncMock),
        patch("artemis.floating_artemis.chat.handle_turn", handle_mock),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=agent_cfg,
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=mock_client),
    ):
        await route_inbound(
            _make_event_data(text_body),
            agent_id=_AGENT_ID,
            confirm_classifier=_classifier,
        )

    return posted


# ---------------------------------------------------------------------------
# DB1-DB4 — append_done_bullet helper
# ---------------------------------------------------------------------------


async def test_append_done_bullet_extends_existing_list(db_session: AsyncSession) -> None:
    """append_done_bullet appends to an existing list without clobbering."""
    from artemis.okr import repository as repo

    existing = ["May: shipped onboarding", "June: reduced churn", "July: launched new tier"]
    kr_ids = await _make_objective_and_kr(db_session, kr_count=1, initial_done_bullets=existing)
    (kr_id,) = kr_ids

    result = await repo.append_done_bullet(db_session, kr_id, "August: brand hub live on server")
    await db_session.commit()

    assert result is not None
    kr = await _fresh_kr(kr_id)
    assert kr is not None
    bullets = list(kr.done_bullets)
    assert len(bullets) == 4, (
        f"Expected 4 bullets (3 existing + 1 new), got {len(bullets)}: {bullets}"
    )
    assert bullets[:3] == existing, "First three bullets must be the originals"
    assert bullets[3] == "August: brand hub live on server"


async def test_append_done_bullet_on_empty_list(db_session: AsyncSession) -> None:
    """append_done_bullet on a KR with empty done_bullets starts a fresh list."""
    from artemis.okr import repository as repo

    kr_ids = await _make_objective_and_kr(db_session, kr_count=1, initial_done_bullets=[])
    (kr_id,) = kr_ids

    await repo.append_done_bullet(db_session, kr_id, "First bullet ever")
    await db_session.commit()

    kr = await _fresh_kr(kr_id)
    assert kr is not None
    bullets = list(kr.done_bullets)
    assert bullets == ["First bullet ever"], f"Expected one bullet, got {bullets!r}"


async def test_append_done_bullet_blank_text_noop(db_session: AsyncSession) -> None:
    """append_done_bullet with blank/empty text is a no-op, returns None."""
    from artemis.okr import repository as repo

    kr_ids = await _make_objective_and_kr(
        db_session, kr_count=1, initial_done_bullets=["existing bullet"]
    )
    (kr_id,) = kr_ids

    result = await repo.append_done_bullet(db_session, kr_id, "   ")
    await db_session.commit()

    assert result is None
    kr = await _fresh_kr(kr_id)
    assert kr is not None
    assert list(kr.done_bullets) == ["existing bullet"], "No-op must not change bullets"


async def test_append_done_bullet_nonexistent_kr_returns_none(db_session: AsyncSession) -> None:
    """append_done_bullet on a non-existent KR returns None gracefully."""
    from artemis.okr import repository as repo

    result = await repo.append_done_bullet(db_session, 999999, "should not land anywhere")
    assert result is None


# ---------------------------------------------------------------------------
# S1-S2 — stage_okr_updates with/without bullet
# ---------------------------------------------------------------------------


async def test_stage_with_bullet_persists_bullet_in_breadcrumb(db_session: AsyncSession) -> None:
    """stage_okr_updates with a bullet field persists it in staged_updates JSONB."""
    from artemis.floating_artemis.tools.okr import _stage_okr_updates

    kr_ids = await _make_objective_and_kr(db_session, kr_count=2)
    kr1, kr2 = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    result = await _stage_okr_updates(
        {
            "speaker_id": _SPEAKER_ID,
            "updates": [
                {
                    "kr_id": kr1,
                    "progress": 78,
                    "basis": "brand hub is live on our server with all guidelines centralized",
                    "bullet": "Brand hub live on server with centralized guidelines",
                },
                {
                    "kr_id": kr2,
                    "progress": 62,
                    "basis": "churn dropped from 8% to 6.5% this quarter",
                    # No bullet — will use basis fallback on apply
                },
            ],
        }
    )

    assert "Staged 2" in result, f"Expected 'Staged 2', got: {result!r}"

    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None and crumb.staged_updates is not None
    staged = list(crumb.staged_updates)
    assert len(staged) == 2

    # KR1 must carry the bullet
    kr1_entry = next(s for s in staged if s["kr_id"] == kr1)
    assert "bullet" in kr1_entry, "bullet field must be persisted in staged_updates"
    assert kr1_entry["bullet"] == "Brand hub live on server with centralized guidelines"

    # KR2 must NOT carry bullet (not provided)
    kr2_entry = next(s for s in staged if s["kr_id"] == kr2)
    assert "bullet" not in kr2_entry or not kr2_entry.get("bullet"), (
        "bullet must be absent when not provided"
    )

    # ZERO KR rows written
    kr = await _fresh_kr(kr1)
    assert kr is not None and kr.prog == 10, "stage must not write KR rows"


async def test_stage_without_bullet_omits_bullet_key(db_session: AsyncSession) -> None:
    """stage_okr_updates without bullet does not inject a bullet key."""
    from artemis.floating_artemis.tools.okr import _stage_okr_updates

    kr_ids = await _make_objective_and_kr(db_session, kr_count=1)
    (kr1,) = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    await _stage_okr_updates(
        {
            "speaker_id": _SPEAKER_ID,
            "updates": [{"kr_id": kr1, "progress": 50, "basis": "shipped the new feature"}],
        }
    )

    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None and crumb.staged_updates
    entry = list(crumb.staged_updates)[0]
    assert "basis" in entry
    assert entry.get("bullet", "") == "", "bullet must be absent or empty when not provided"


# ---------------------------------------------------------------------------
# R1-R4 — route_inbound done_bullets apply path
# ---------------------------------------------------------------------------


async def test_route_go_with_bullet_appends_to_existing_bullets(db_session: AsyncSession) -> None:
    """'go' with a staged bullet → done_bullets gains bullet; 3 existing → 4 after."""
    existing = [
        "May: shipped new onboarding",
        "June: churn to 6.5%",
        "July: content hub launched",
    ]
    kr_ids = await _make_objective_and_kr(db_session, kr_count=1, initial_done_bullets=existing)
    (kr1,) = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    from artemis.proactivity import repository as prepo

    await prepo.set_staged_updates(
        db_session,
        crumb_id,
        [
            {
                "kr_id": kr1,
                "progress": 85,
                "basis": "brand hub now live on server with all brand guidelines",
                "bullet": "Brand hub live on server with centralized brand guidelines",
            }
        ],
    )
    await db_session.commit()

    posted = await _run_route_inbound("go", verdict="YES")
    assert len(posted) == 1 and "Done" in posted[0]

    # prog updated
    kr = await _fresh_kr(kr1)
    assert kr is not None
    assert kr.prog == 85, f"prog must be 85, got {kr.prog}"

    # done_bullets: 3 existing + 1 new = 4 total
    bullets = list(kr.done_bullets)
    assert len(bullets) == 4, (
        f"Expected 4 bullets (3 existing + 1 new), got {len(bullets)}: {bullets}"
    )
    assert bullets[:3] == existing, "First 3 bullets must be the originals (lossless)"
    assert bullets[3] == "Brand hub live on server with centralized brand guidelines"

    # activity logged
    assert await _fresh_activity_count() == 1


async def test_route_go_without_bullet_falls_back_to_trimmed_basis(
    db_session: AsyncSession,
) -> None:
    """'go' with NO staged bullet → basis is trimmed and used as the bullet; non-empty."""
    kr_ids = await _make_objective_and_kr(db_session, kr_count=1, initial_done_bullets=[])
    (kr1,) = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    from artemis.proactivity import repository as prepo

    basis_text = "shipped the onboarding redesign and pilot contracts signed"
    await prepo.set_staged_updates(
        db_session,
        crumb_id,
        [{"kr_id": kr1, "progress": 60, "basis": basis_text}],
    )
    await db_session.commit()

    posted = await _run_route_inbound("go", verdict="YES")
    assert len(posted) == 1 and "Done" in posted[0]

    kr = await _fresh_kr(kr1)
    assert kr is not None
    assert kr.prog == 60

    bullets = list(kr.done_bullets)
    assert len(bullets) == 1, f"Expected 1 bullet, got {len(bullets)}: {bullets}"
    # Fallback must be non-empty and grounded in the basis
    assert bullets[0], "Bullet must be non-empty"
    assert len(bullets[0]) <= 200, "Fallback bullet must be trimmed to at most 200 chars"
    # Confirm the bullet is derived from basis (some substring present)
    assert any(word in bullets[0] for word in basis_text.split()[:4]), (
        f"Fallback bullet {bullets[0]!r} must contain words from basis {basis_text!r}"
    )


async def test_route_no_leaves_done_bullets_unchanged(db_session: AsyncSession) -> None:
    """'no' → done_bullets unchanged (zero writes)."""
    existing = ["old bullet one", "old bullet two"]
    kr_ids = await _make_objective_and_kr(db_session, kr_count=1, initial_done_bullets=existing)
    (kr1,) = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    from artemis.proactivity import repository as prepo

    await prepo.set_staged_updates(
        db_session,
        crumb_id,
        [
            {
                "kr_id": kr1,
                "progress": 99,
                "basis": "some great work done",
                "bullet": "Great work done this week",
            }
        ],
    )
    await db_session.commit()

    posted = await _run_route_inbound("no", verdict="NO")
    assert len(posted) == 1
    assert "Cleared" in posted[0] or "nothing changed" in posted[0]

    kr = await _fresh_kr(kr1)
    assert kr is not None
    assert kr.prog == 10, "prog must be unchanged on 'no'"
    assert list(kr.done_bullets) == existing, "done_bullets must be unchanged on 'no'"
    assert await _fresh_activity_count() == 0


async def test_route_go_prog_and_activity_still_write(db_session: AsyncSession) -> None:
    """Regression: prog write and activity log still happen alongside done_bullets update."""
    kr_ids = await _make_objective_and_kr(db_session, kr_count=2, initial_done_bullets=[])
    kr1, kr2 = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    from artemis.proactivity import repository as prepo

    await prepo.set_staged_updates(
        db_session,
        crumb_id,
        [
            {
                "kr_id": kr1,
                "progress": 78,
                "basis": "launched 2 pilots this week",
                "bullet": "Launched 2 pilots",
            },
            {
                "kr_id": kr2,
                "progress": 62,
                "basis": "churn dropped to 6.5%",
                "bullet": "Churn reduced to 6.5%",
            },
        ],
    )
    await db_session.commit()

    posted = await _run_route_inbound("go", verdict="YES")
    assert len(posted) == 1 and "Done" in posted[0]

    # Prog updated for both
    kr1_row = await _fresh_kr(kr1)
    kr2_row = await _fresh_kr(kr2)
    assert kr1_row is not None and kr1_row.prog == 78
    assert kr2_row is not None and kr2_row.prog == 62

    # Activity logged (2 entries)
    assert await _fresh_activity_count() == 2

    # done_bullets updated for both
    assert list(kr1_row.done_bullets) == ["Launched 2 pilots"]
    assert list(kr2_row.done_bullets) == ["Churn reduced to 6.5%"]

    # Breadcrumb completed + cleared
    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None
    assert not crumb.staged_updates
    assert crumb.completed_at is not None
