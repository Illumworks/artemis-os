"""Tests for the P2 OKR stage-via-breadcrumb brief (subscription-path apply).

Root cause this closes: Artemis runs on the claude-code subscription adapter,
whose MCP subprocess strips layer>2 tools AND has an in-memory confirmation_store
that cannot reach the main process. So the layer-3 update_okr_kr(s) confirm flow
can never complete a Friday check-in there. The fix stages updates on the
DB-backed breadcrumb (layer-1 stage_okr_updates) and applies them server-side in
route_inbound on the operator's explicit "go".

Covers:
  S1. stage_okr_updates writes staged_updates to the live breadcrumb and writes
      ZERO KR rows.
  S2. stage_okr_updates drops unknown-KR and empty-basis items.
  S3. stage_okr_updates with no live breadcrumb stages nothing.
  T1. served floating tool set INCLUDES stage_okr_updates (layer 1) and EXCLUDES
      the layer-3 write tools (proves reachability on the subscription path).
  R1. route_inbound + "go" applies all staged updates (KR rows + activity),
      clears staged_updates, and completes the breadcrumb.
  R2. route_inbound + "no" clears staged_updates with zero KR writes.
  R3. route_inbound + unrelated reply (NEITHER) leaves staged_updates intact and
      runs a normal turn.
  R4. No double-apply: a second "go" after the staged set is cleared does nothing.
  E1. End-to-end: seed breadcrumb -> stage via tool -> "go" via route -> KR rows
      changed (and only then).

All DB-backed tests use the isolated artemis_test DB via the OKR conftest
pattern (NullPool, per-test TRUNCATE). Slack + LLM calls are fully mocked.
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
# Module-level setup — rebind the global engine/SessionLocal to the test DB so
# production code paths (the tool + route_inbound) hit the isolated test DB.
# ---------------------------------------------------------------------------

_SPEAKER_ID = "U_JON_OKR_STAGE"
_TEAM_ID = "T_TEST_STAGE"
_CHANNEL_ID = "D_STAGE_DM"
_AGENT_ID = "artemis"

_db_url = _os.environ.get("ARTEMIS_TEST_DB_URL") or _os.environ.get(
    "ARTEMIS_DB_URL", "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test"
)

# Guard — only proceed if the test DB URL is safe.
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
    """Per-test session with a fresh NullPool engine. Truncates OKR + breadcrumb tables."""
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


async def _make_objective_and_kr(session: AsyncSession, kr_count: int = 1) -> list[int]:
    """Create one objective with kr_count key results (prog=10). Returns KR IDs."""
    from artemis.okr import repository as repo

    obj = await repo.create_objective(session, title="Test Obj", cycle="Q2-2026")
    await session.flush()
    kr_ids: list[int] = []
    for i in range(kr_count):
        kr = await repo.create_key_result(
            session,
            objective_id=obj.id,
            title=f"Test KR {i + 1}",
            status="ontrack",
            prog=10,
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
    """Insert a live breadcrumb for recipient_id. Returns its id."""
    from artemis.proactivity import repository as prepo

    snapshot = [
        {"kr_id": kid, "kr_title": f"KR {kid}", "objective_title": "Obj", "prog": 10}
        for kid in (kr_ids or [])
    ]
    crumb = await prepo.create_okr_checkin_breadcrumb(
        session,
        recipient_id=recipient_id,
        kr_snapshot=snapshot,
        proposal_text="Friday check-in",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    await session.commit()
    return crumb.id


async def _fresh_kr_prog(kr_id: int) -> int | None:
    """Read a KR's prog through a brand-new session (no identity-map staleness)."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        from artemis.okr import repository as repo

        async with AsyncSession(engine, expire_on_commit=False) as session:
            kr = await repo.get_key_result(session, kr_id)
            return kr.prog if kr is not None else None
    finally:
        await engine.dispose()


async def _fresh_breadcrumb(crumb_id: int) -> Any:
    """Read a breadcrumb row fresh. Returns a detached object (or None)."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        from artemis.proactivity.models import OkrCheckinBreadcrumb

        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await session.get(OkrCheckinBreadcrumb, crumb_id)
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


def _make_event_data(text_body: str, user: str = _SPEAKER_ID) -> dict[str, Any]:
    return {
        "team_id": _TEAM_ID,
        "channel": _CHANNEL_ID,
        "user": user,
        "text": text_body,
        "ts": "999.000",
        "thread_ts": None,
    }


async def _run_route_inbound(
    text_body: str,
    verdict: str,
    handle_mock: AsyncMock | None = None,
) -> list[str]:
    """Drive route_inbound against the real test DB with Slack + session deps mocked.

    SessionLocal is NOT mocked — the staged-updates gate and apply read/write the
    real test DB. Pass handle_mock to assert on the normal-turn fall-through.
    Returns the texts posted to Slack.
    """
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
# S1-S3 — stage_okr_updates behaviour
# ---------------------------------------------------------------------------


async def test_stage_writes_breadcrumb_and_no_kr_rows(db_session: AsyncSession) -> None:
    """stage_okr_updates records staged_updates on the breadcrumb and writes ZERO KR rows."""
    from artemis.floating_artemis.tools.okr import _stage_okr_updates

    kr_ids = await _make_objective_and_kr(db_session, kr_count=2)
    kr1, kr2 = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    result = await _stage_okr_updates(
        {
            "speaker_id": _SPEAKER_ID,
            "updates": [
                {"kr_id": kr1, "progress": 78, "basis": "launched 2 pilots"},
                {"kr_id": kr2, "progress": 62, "basis": "churn dropped"},
            ],
        }
    )

    assert "Staged 2" in result, f"Expected 'Staged 2' in result, got: {result!r}"
    assert "go" in result, f"Expected the staged result to ask for 'go', got: {result!r}"

    # Breadcrumb now carries the staged list.
    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None
    assert crumb.staged_updates is not None, "staged_updates must be written"
    staged = list(crumb.staged_updates)
    assert len(staged) == 2
    assert {s["kr_id"] for s in staged} == {kr1, kr2}
    assert all(isinstance(s["progress"], int) for s in staged), "progress must be stored as int"

    # ZERO KR rows written — both still at the seeded prog.
    assert await _fresh_kr_prog(kr1) == 10
    assert await _fresh_kr_prog(kr2) == 10
    assert await _fresh_activity_count() == 0


async def test_stage_drops_unknown_kr_and_empty_basis(db_session: AsyncSession) -> None:
    """Unknown-KR items and empty-basis items are dropped; valid items are staged."""
    from artemis.floating_artemis.tools.okr import _stage_okr_updates

    kr_ids = await _make_objective_and_kr(db_session, kr_count=1)
    (kr1,) = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    result = await _stage_okr_updates(
        {
            "speaker_id": _SPEAKER_ID,
            "updates": [
                {"kr_id": kr1, "progress": 80, "basis": "shipped feature X"},  # valid
                {"kr_id": 999999, "progress": 50, "basis": "real basis"},  # unknown KR
                {"kr_id": kr1, "progress": 90, "basis": "   "},  # empty basis
            ],
        }
    )

    assert "Staged 1" in result, f"Expected only 1 staged, got: {result!r}"
    assert "Dropped" in result, f"Expected a Dropped note, got: {result!r}"

    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None and crumb.staged_updates is not None
    staged = list(crumb.staged_updates)
    assert len(staged) == 1
    assert staged[0]["kr_id"] == kr1
    assert staged[0]["progress"] == 80


async def test_stage_no_live_breadcrumb_stages_nothing(db_session: AsyncSession) -> None:
    """With no live breadcrumb, stage_okr_updates stages nothing and says so."""
    from artemis.floating_artemis.tools.okr import _stage_okr_updates

    kr_ids = await _make_objective_and_kr(db_session, kr_count=1)
    (kr1,) = kr_ids
    # No breadcrumb seeded.

    result = await _stage_okr_updates(
        {
            "speaker_id": _SPEAKER_ID,
            "updates": [{"kr_id": kr1, "progress": 80, "basis": "shipped X"}],
        }
    )

    assert "No live OKR check-in" in result, f"Expected no-live-breadcrumb message, got: {result!r}"
    assert await _fresh_kr_prog(kr1) == 10
    assert await _fresh_activity_count() == 0


# ---------------------------------------------------------------------------
# T1 — served tool set reachability on the subscription path
# ---------------------------------------------------------------------------


async def test_served_tool_set_includes_stage_excludes_layer3() -> None:
    """build_floating_artemis_tool_set serves stage_okr_updates (layer 1) and NOT layer-3 tools."""
    from artemis.tools.mcp_server import build_floating_artemis_tool_set, mcp_tool_name

    with patch(
        "artemis.tools.mcp_server.get_status",
        new_callable=AsyncMock,
        return_value={"available_surfaces": ["okr"]},
    ):
        tool_set = await build_floating_artemis_tool_set()

    keys = set(tool_set.keys())
    assert mcp_tool_name("stage_okr_updates") in keys, (
        "stage_okr_updates (layer 1) must be served on the subscription path"
    )
    assert mcp_tool_name("update_okr_kr") not in keys, "layer-3 update_okr_kr must be stripped"
    assert mcp_tool_name("update_okr_krs") not in keys, "layer-3 update_okr_krs must be stripped"


def test_stage_okr_updates_registered_layer1() -> None:
    """stage_okr_updates must register at layer 1 (auto-invoke, no confirmation gate)."""
    from artemis.floating_artemis.authority import AuthorizedToolRegistry
    from artemis.floating_artemis.tools.okr import register_okr_tools

    registry = AuthorizedToolRegistry()
    register_okr_tools(registry)

    entry = registry.get("stage_okr_updates")
    assert entry is not None, "stage_okr_updates must be registered"
    assert entry.layer == 1, f"Expected layer=1, got layer={entry.layer}"
    # Layer-3 write tools must remain registered (web/intercepting path keeps them).
    assert registry.get("update_okr_krs") is not None
    assert registry.get("update_okr_kr") is not None


# ---------------------------------------------------------------------------
# R1-R4 — route_inbound apply-on-"go"
# ---------------------------------------------------------------------------


async def test_route_go_applies_clears_and_completes(db_session: AsyncSession) -> None:
    """'go' applies staged updates (KR rows + activity), clears staged, completes breadcrumb."""
    kr_ids = await _make_objective_and_kr(db_session, kr_count=3)
    kr1, kr2, kr3 = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    # Stage directly on the breadcrumb (simulates a prior stage_okr_updates call).
    from artemis.proactivity import repository as prepo

    await prepo.set_staged_updates(
        db_session,
        crumb_id,
        [
            {"kr_id": kr1, "progress": 78, "basis": "launched 2 pilots"},
            {"kr_id": kr2, "progress": 62, "basis": "churn dropped"},
            {"kr_id": kr3, "progress": 70, "basis": "12 content pieces"},
        ],
    )
    await db_session.commit()

    posted = await _run_route_inbound("go", verdict="YES")

    # A result message is posted naming the applied KRs.
    assert len(posted) == 1, f"Expected one posted result, got {posted!r}"
    assert "Done" in posted[0]

    # KR rows updated.
    assert await _fresh_kr_prog(kr1) == 78
    assert await _fresh_kr_prog(kr2) == 62
    assert await _fresh_kr_prog(kr3) == 70

    # Activity logged (one per KR, citing approval).
    assert await _fresh_activity_count() == 3

    # Staged cleared + breadcrumb completed.
    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None
    assert not crumb.staged_updates, "staged_updates must be cleared after apply"
    assert crumb.completed_at is not None, "breadcrumb must be completed after apply"


async def test_route_no_clears_with_zero_writes(db_session: AsyncSession) -> None:
    """'no' clears staged_updates and writes zero KR rows."""
    kr_ids = await _make_objective_and_kr(db_session, kr_count=2)
    kr1, kr2 = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    from artemis.proactivity import repository as prepo

    await prepo.set_staged_updates(
        db_session,
        crumb_id,
        [
            {"kr_id": kr1, "progress": 78, "basis": "launched 2 pilots"},
            {"kr_id": kr2, "progress": 62, "basis": "churn dropped"},
        ],
    )
    await db_session.commit()

    posted = await _run_route_inbound("no", verdict="NO")

    assert len(posted) == 1
    assert "Cleared" in posted[0] or "nothing changed" in posted[0]

    # Zero KR writes.
    assert await _fresh_kr_prog(kr1) == 10
    assert await _fresh_kr_prog(kr2) == 10
    assert await _fresh_activity_count() == 0

    # Staged cleared.
    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None and not crumb.staged_updates


async def test_route_unrelated_keeps_staged_and_runs_turn(db_session: AsyncSession) -> None:
    """An unrelated reply (NEITHER) leaves staged_updates intact and runs a normal turn."""
    kr_ids = await _make_objective_and_kr(db_session, kr_count=1)
    (kr1,) = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    from artemis.proactivity import repository as prepo

    staged = [{"kr_id": kr1, "progress": 78, "basis": "launched 2 pilots"}]
    await prepo.set_staged_updates(db_session, crumb_id, staged)
    await db_session.commit()

    result = MagicMock()
    result.response_text = "Sure, here's the weather."
    result.pending_tool_use_id = None
    mock_handle = AsyncMock(return_value=result)
    posted = await _run_route_inbound("what's the weather?", "NEITHER", handle_mock=mock_handle)

    # Normal turn ran; a reply was posted.
    assert mock_handle.await_count == 1, "handle_turn must run on a NEITHER reply"
    assert len(posted) == 1

    # KR untouched; staged intact.
    assert await _fresh_kr_prog(kr1) == 10
    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None and crumb.staged_updates, "staged must remain on NEITHER"
    assert list(crumb.staged_updates)[0]["kr_id"] == kr1
    assert crumb.completed_at is None


async def test_route_no_double_apply_on_second_go(db_session: AsyncSession) -> None:
    """A second 'go' after the staged set is cleared applies nothing further."""
    kr_ids = await _make_objective_and_kr(db_session, kr_count=1)
    (kr1,) = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    from artemis.proactivity import repository as prepo

    await prepo.set_staged_updates(
        db_session, crumb_id, [{"kr_id": kr1, "progress": 78, "basis": "launched 2 pilots"}]
    )
    await db_session.commit()

    # First "go" applies.
    await _run_route_inbound("go", verdict="YES")
    assert await _fresh_kr_prog(kr1) == 78
    assert await _fresh_activity_count() == 1

    # Second "go" — staged is cleared AND breadcrumb completed, so the gate is inert.
    posted = await _run_route_inbound("go", verdict="YES")
    assert await _fresh_kr_prog(kr1) == 78, "prog must not change on a second go"
    assert await _fresh_activity_count() == 1, "no second activity entry (no double-apply)"
    # The completed breadcrumb is no longer live, so the staged gate does not fire;
    # the turn falls through to a normal handle_turn reply.
    assert len(posted) == 1


# ---------------------------------------------------------------------------
# E1 — end-to-end: stage via the tool, then apply via route "go"
# ---------------------------------------------------------------------------


async def test_e2e_stage_then_go_changes_kr(db_session: AsyncSession) -> None:
    """Seed breadcrumb -> stage via stage_okr_updates -> 'go' via route -> KR rows change."""
    from artemis.floating_artemis.tools.okr import _stage_okr_updates

    kr_ids = await _make_objective_and_kr(db_session, kr_count=3)
    kr1, kr2, kr3 = kr_ids
    crumb_id = await _seed_breadcrumb(db_session, kr_ids=kr_ids)

    # Stage through the real tool (writes the breadcrumb, no KR rows).
    stage_result = await _stage_okr_updates(
        {
            "speaker_id": _SPEAKER_ID,
            "updates": [
                {"kr_id": kr1, "progress": 78, "basis": "launched 2 pilots this week"},
                {"kr_id": kr2, "progress": 62, "basis": "churn dropped to 6.5%"},
                {"kr_id": kr3, "progress": 70, "basis": "12 content pieces"},
            ],
        }
    )
    assert "Staged 3" in stage_result
    assert await _fresh_kr_prog(kr1) == 10, "stage must not write KR rows"

    # Apply via the route's "go".
    posted = await _run_route_inbound("go", verdict="YES")
    assert len(posted) == 1 and "Done" in posted[0]

    assert await _fresh_kr_prog(kr1) == 78
    assert await _fresh_kr_prog(kr2) == 62
    assert await _fresh_kr_prog(kr3) == 70
    assert await _fresh_activity_count() == 3

    crumb = await _fresh_breadcrumb(crumb_id)
    assert crumb is not None and not crumb.staged_updates and crumb.completed_at is not None
