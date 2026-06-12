"""Phase 2 soft-lock tests — compare-and-set on live_content.

Ship gate: assert the DB state, not just the HTTP status.  The core proof is
that a stale write rejected with 409 leaves the other writer's content intact.

Covers:
  1. PUT {liveContent:"A", baseVersion:0} → 200; GET detail shows liveContent=="A",
     liveContentVersion==1.
  2. PUT {liveContent:"B-stale", baseVersion:0} → 409 with code stale_live_content;
     GET detail still shows liveContent=="A" and liveContentVersion==1 (clobber
     was prevented — this is the DB-state proof).
  3. PUT {liveContent:"A2", baseVersion:1} → 200; version → 2.
  4. Save-version branch: PUT {content:"v1 body"} clears live_content AND bumps
     liveContentVersion.
  5. Backward-compat: PUT {liveContent:"C"} with NO baseVersion → 200; version
     increments (blind write still allowed).

All tests use the shared conftest test DB (ARTEMIS_TEST_DB_URL / artemis_test).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# DB override — use the agent-specific test DB from the env (set by conftest)
# ---------------------------------------------------------------------------
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db as db_module
from artemis.db import attach_pgvector_codec
from artemis.marketing.models import CampaignCandidate, CampaignDeliverable

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
        writing_draft_thread_messages,
        writing_rules,
        writing_examples,
        writing_profiles,
        campaign_deliverables,
        campaign_candidates
    RESTART IDENTITY CASCADE
    """
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


async def _make_deliverable(session: AsyncSession, *, title: str = "Soft-lock Draft") -> int:
    """Insert a candidate + deliverable; return the deliverable id."""
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


async def _put_draft(draft_id: int, payload: dict) -> tuple[int, dict]:
    """PUT /api/writing-studio/drafts/{id} via ASGI and return (status, json)."""
    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/writing-studio/drafts/{draft_id}",
            json=payload,
            headers={"X-Artemis-Token": "test-token"},
        )
    body = (
        resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    )
    return resp.status_code, body


async def _get_draft(draft_id: int) -> tuple[int, dict]:
    """GET /api/writing-studio/drafts/{id} via ASGI and return (status, json)."""
    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/writing-studio/drafts/{draft_id}",
            headers={"X-Artemis-Token": "test-token"},
        )
    body = (
        resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    )
    return resp.status_code, body


# ---------------------------------------------------------------------------
# Test 1: First autosave — sets liveContent, bumps version to 1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autosave_sets_live_content_and_bumps_version(db_session: AsyncSession) -> None:
    """PUT {liveContent:'A', baseVersion:0} → 200; detail shows liveContent=='A', version==1."""
    draft_id = await _make_deliverable(db_session)

    status, body = await _put_draft(draft_id, {"liveContent": "A", "baseVersion": 0})
    assert status == 200, f"Expected 200, got {status}: {body}"
    assert body["liveContent"] == "A"
    assert body["liveContentVersion"] == 1

    # Verify via GET (round-trip through the serializer).
    get_status, detail = await _get_draft(draft_id)
    assert get_status == 200
    assert detail["liveContent"] == "A"
    assert detail["liveContentVersion"] == 1


# ---------------------------------------------------------------------------
# Test 2: Stale write rejected; DB state proves clobber was prevented (core proof)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_autosave_rejected_and_db_intact(db_session: AsyncSession) -> None:
    """PUT with stale baseVersion → 409; GET still shows the other writer's content."""
    draft_id = await _make_deliverable(db_session)

    # Writer A saves first (version 0 → 1).
    status_a, body_a = await _put_draft(draft_id, {"liveContent": "A", "baseVersion": 0})
    assert status_a == 200, f"Writer A first save failed: {body_a}"
    assert body_a["liveContentVersion"] == 1

    # Writer B tries to save with the old baseVersion (0) — stale write.
    status_b, body_b = await _put_draft(draft_id, {"liveContent": "B-stale", "baseVersion": 0})
    assert status_b == 409, f"Expected 409 conflict, got {status_b}: {body_b}"
    assert body_b.get("code") == "stale_live_content", f"Wrong code: {body_b}"
    assert "error" in body_b
    # Response also carries the current server content for recovery.
    assert body_b.get("currentVersion") == 1
    assert body_b.get("liveContent") == "A"

    # THE CORE PROOF: DB state is unchanged — Writer A's content survived.
    get_status, detail = await _get_draft(draft_id)
    assert get_status == 200
    assert detail["liveContent"] == "A", (
        f"CLOBBER DETECTED: expected liveContent=='A' but got {detail.get('liveContent')!r}"
    )
    assert detail["liveContentVersion"] == 1, (
        f"CLOBBER DETECTED: expected liveContentVersion==1 but got {detail.get('liveContentVersion')}"
    )


# ---------------------------------------------------------------------------
# Test 3: Writer B retries with the correct baseVersion (1 → 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_with_correct_base_version_succeeds(db_session: AsyncSession) -> None:
    """After fetching the current version, Writer B can save with baseVersion==1 → 200."""
    draft_id = await _make_deliverable(db_session)

    # Writer A saves: version 0 → 1.
    s1, _ = await _put_draft(draft_id, {"liveContent": "A", "baseVersion": 0})
    assert s1 == 200

    # Writer B retries with the correct base (1 → 2).
    s2, body2 = await _put_draft(draft_id, {"liveContent": "A2", "baseVersion": 1})
    assert s2 == 200, f"Expected 200 on correct-base retry, got {s2}: {body2}"
    assert body2["liveContent"] == "A2"
    assert body2["liveContentVersion"] == 2

    _, detail = await _get_draft(draft_id)
    assert detail["liveContent"] == "A2"
    assert detail["liveContentVersion"] == 2


# ---------------------------------------------------------------------------
# Test 4: Save-version clears live_content AND bumps liveContentVersion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_version_clears_live_content_and_bumps_version(db_session: AsyncSession) -> None:
    """PUT {content:'v1 body'} clears live_content and increments liveContentVersion."""
    draft_id = await _make_deliverable(db_session)

    # Establish a live_content first so we can confirm it gets cleared.
    s1, b1 = await _put_draft(draft_id, {"liveContent": "Draft text", "baseVersion": 0})
    assert s1 == 200
    version_before = b1["liveContentVersion"]

    # Now save an explicit version — this is the "Save version" path.
    s2, b2 = await _put_draft(draft_id, {"content": "v1 body", "source": "test"})
    assert s2 == 200, f"Save-version PUT failed: {b2}"
    # live_content should be cleared.
    assert b2["liveContent"] is None
    # Version counter should have been bumped so any in-flight autosave is rejected.
    assert b2["liveContentVersion"] > version_before, (
        f"Expected liveContentVersion > {version_before}, got {b2.get('liveContentVersion')}"
    )

    # Confirm via GET.
    _, detail = await _get_draft(draft_id)
    assert detail["liveContent"] is None
    assert detail["liveContentVersion"] > version_before


# ---------------------------------------------------------------------------
# Test 5: Backward-compat — omitting baseVersion allows blind write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autosave_without_base_version_is_allowed(db_session: AsyncSession) -> None:
    """PUT {liveContent:'C'} with no baseVersion → 200; blind write still works."""
    draft_id = await _make_deliverable(db_session)

    # First save (with version).
    s1, b1 = await _put_draft(draft_id, {"liveContent": "A", "baseVersion": 0})
    assert s1 == 200
    assert b1["liveContentVersion"] == 1

    # Second save without baseVersion — should be allowed (backward-compatible).
    s2, b2 = await _put_draft(draft_id, {"liveContent": "C"})
    assert s2 == 200, f"Expected 200 on blind write, got {s2}: {b2}"
    assert b2["liveContent"] == "C"
    # Version should have incremented even on a blind write.
    assert b2["liveContentVersion"] == 2

    _, detail = await _get_draft(draft_id)
    assert detail["liveContent"] == "C"
    assert detail["liveContentVersion"] == 2
