"""Phase 3 collab WebSocket tests — prosemirror-collab step ordering + flush.

The server is the authoritative step-ordering service: it version-checks,
appends, and broadcasts steps; it is the single (version-gated) writer to
live_content; and it snaps editors to a saved version on rebase.

Two groups:
  A. Protocol (TestClient WS, no DB effect asserted): the collab baseline rides
     in presence.init; steps echo to ALL incl origin and advance the version; a
     stale submission is rejected; a caught-up client's resend is accepted; a
     late joiner replays prior steps; malformed frames are ignored.
  B. DB effect (stub WS + seeded draft): materialize flushes the converged text
     to live_content (asserting the DB row, not just HTTP 200), and Save-version
     broadcasts collab.rebase + re-hydrates the room.

Step payloads are opaque to the server (it never parses ProseMirror), so the
protocol tests use minimal placeholder step dicts.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.testclient import TestClient

import artemis.db as db_module
from artemis.db import attach_pgvector_codec
from artemis.identity.dependencies import RequestIdentity
from artemis.marketing.models import CampaignCandidate, CampaignDeliverable

# A minimal, opaque "step" — the server never parses these.
_STEP: dict[str, Any] = {"stepType": "replace", "from": 1, "to": 1, "slice": {}}


def _make_app() -> FastAPI:
    app = FastAPI()
    from artemis.marketing.writing_studio.collab.routes import router

    app.include_router(router)
    return app


def _fresh() -> None:
    """Clear both singletons (presence connections + step-log rooms)."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager
    from artemis.marketing.writing_studio.collab.rooms import room_registry

    collab_manager._rooms.clear()
    room_registry.clear()


def _connect_url(draft_id: int, who: str) -> str:
    return (
        f"/api/writing-studio/drafts/{draft_id}/collab"
        f"?as_email={who}%40example.com&as_name={who.title()}"
    )


# ===========================================================================
# Group A — protocol (TestClient WS)
# ===========================================================================


def test_presence_init_carries_collab_baseline() -> None:
    """presence.init includes a collab baseline at version 0 with empty steps."""
    _fresh()
    tc = TestClient(_make_app())
    with tc.websocket_connect(_connect_url(3001, "alice")) as ws_a:
        init = ws_a.receive_json()
        assert init["type"] == "presence.init"
        collab = init["collab"]
        assert collab["type"] == "collab.init"
        assert collab["version"] == 0
        assert collab["steps"] == []
        assert collab["currentVersion"] == 0
        assert "doc" in collab


def test_steps_broadcast_to_all_including_origin_and_advance_version() -> None:
    """A's steps@0 are echoed to A (so collab confirms them) and to B; version → 1."""
    _fresh()
    from artemis.marketing.writing_studio.collab.rooms import room_registry

    tc = TestClient(_make_app())
    with tc.websocket_connect(_connect_url(3002, "alice")) as ws_a:
        ws_a.receive_json()  # presence.init (A)
        with tc.websocket_connect(_connect_url(3002, "bob")) as ws_b:
            ws_b.receive_json()  # presence.init (B)
            ws_a.receive_json()  # presence.join (B joined)

            ws_a.send_text(
                json.dumps({"type": "steps", "version": 0, "steps": [_STEP], "clientID": "111"})
            )

            # Origin A receives its own steps echoed (clientID matches → confirm).
            echo_a = ws_a.receive_json()
            assert echo_a["type"] == "collab.steps"
            assert echo_a["version"] == 0
            assert echo_a["steps"][0]["clientID"] == "111"

            # B receives the same broadcast.
            echo_b = ws_b.receive_json()
            assert echo_b["type"] == "collab.steps"
            assert echo_b["steps"][0]["clientID"] == "111"

            assert room_registry.get("3002").version == 1


def test_stale_steps_rejected_with_current_version() -> None:
    """B submitting against an out-of-date version gets collab.reject {version}."""
    _fresh()
    tc = TestClient(_make_app())
    with tc.websocket_connect(_connect_url(3003, "alice")) as ws_a:
        ws_a.receive_json()  # presence.init (A)
        with tc.websocket_connect(_connect_url(3003, "bob")) as ws_b:
            ws_b.receive_json()  # presence.init (B)
            ws_a.receive_json()  # presence.join

            # A commits a step → version 1.
            ws_a.send_text(
                json.dumps({"type": "steps", "version": 0, "steps": [_STEP], "clientID": "AAA"})
            )
            ws_a.receive_json()  # A's echo
            ws_b.receive_json()  # B sees A's step (now at version 1)

            # B submits against the STALE version 0 → rejected.
            ws_b.send_text(
                json.dumps({"type": "steps", "version": 0, "steps": [_STEP], "clientID": "BBB"})
            )
            rej = ws_b.receive_json()
            assert rej["type"] == "collab.reject"
            assert rej["version"] == 1


def test_caught_up_client_resend_is_accepted() -> None:
    """After catching up to the current version, B's resend@1 is accepted."""
    _fresh()
    from artemis.marketing.writing_studio.collab.rooms import room_registry

    tc = TestClient(_make_app())
    with tc.websocket_connect(_connect_url(3004, "alice")) as ws_a:
        ws_a.receive_json()
        with tc.websocket_connect(_connect_url(3004, "bob")) as ws_b:
            ws_b.receive_json()
            ws_a.receive_json()

            ws_a.send_text(
                json.dumps({"type": "steps", "version": 0, "steps": [_STEP], "clientID": "AAA"})
            )
            ws_a.receive_json()  # A echo
            ws_b.receive_json()  # B catches up → now at version 1

            # B resends against the now-current version 1.
            ws_b.send_text(
                json.dumps({"type": "steps", "version": 1, "steps": [_STEP], "clientID": "BBB"})
            )
            echo = ws_b.receive_json()
            assert echo["type"] == "collab.steps"
            assert echo["version"] == 1
            assert room_registry.get("3004").version == 2


def test_late_joiner_replays_prior_steps() -> None:
    """A late joiner's collab baseline includes all steps committed before it joined."""
    _fresh()
    tc = TestClient(_make_app())
    with tc.websocket_connect(_connect_url(3005, "alice")) as ws_a:
        ws_a.receive_json()
        # A commits two steps before anyone else joins.
        ws_a.send_text(
            json.dumps({"type": "steps", "version": 0, "steps": [_STEP, _STEP], "clientID": "AAA"})
        )
        ws_a.receive_json()  # A echo

        with tc.websocket_connect(_connect_url(3005, "carol")) as ws_c:
            init = ws_c.receive_json()
            collab = init["collab"]
            assert collab["version"] == 0
            assert len(collab["steps"]) == 2
            assert collab["currentVersion"] == 2
            assert collab["steps"][0]["clientID"] == "AAA"


def test_malformed_step_frames_ignored() -> None:
    """Missing clientID / empty steps / bad version do not advance or crash."""
    _fresh()
    from artemis.marketing.writing_studio.collab.rooms import room_registry

    tc = TestClient(_make_app())
    with tc.websocket_connect(_connect_url(3006, "alice")) as ws_a:
        ws_a.receive_json()
        # No clientID.
        ws_a.send_text(json.dumps({"type": "steps", "version": 0, "steps": [_STEP]}))
        # Empty steps list.
        ws_a.send_text(json.dumps({"type": "steps", "version": 0, "steps": [], "clientID": "X"}))
        # Non-int version.
        ws_a.send_text(
            json.dumps({"type": "steps", "version": "nope", "steps": [_STEP], "clientID": "X"})
        )
        # A valid frame afterwards proves the room is still alive and at version 0→1.
        ws_a.send_text(
            json.dumps({"type": "steps", "version": 0, "steps": [_STEP], "clientID": "X"})
        )
        echo = ws_a.receive_json()
        assert echo["type"] == "collab.steps"
        assert room_registry.get("3006").version == 1


# ===========================================================================
# Group B — DB effect (stub WS + seeded draft)
# ===========================================================================

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
db_module.engine = _test_engine
db_module.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(bind=_test_engine, expire_on_commit=False, class_=AsyncSession)

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


async def _make_deliverable(session: AsyncSession, *, title: str = "Collab Draft") -> int:
    c = CampaignCandidate(
        campaign_family="test",
        name=title,
        stage="human_gate_1",
        decision_state="approved",
        workspace_state="pending_content",
    )
    session.add(c)
    await session.flush()
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


class _StubWS:
    """Captures broadcast payloads without a real socket."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, evt: dict[str, Any]) -> None:
        self.sent.append(evt)


def _register_stub(room_key: str) -> _StubWS:
    """Insert a stub connection into the presence manager for *room_key*."""
    from artemis.marketing.writing_studio.collab.manager import _Peer, collab_manager

    stub = _StubWS()
    collab_manager._rooms.setdefault(room_key, {})[stub] = _Peer(  # type: ignore[index]
        client_id="stub", identity=RequestIdentity(email="x@y.z", name="X", claims={}, source="t")
    )
    return stub


@pytest.mark.asyncio
async def test_materialize_flushes_converged_text_to_live_content(
    db_session: AsyncSession,
) -> None:
    """A materialize frame persists the converged text to live_content (DB proof)."""
    _fresh()
    from artemis.marketing.writing_studio.collab.rooms import room_registry
    from artemis.marketing.writing_studio.collab.routes import _handle_materialize
    from artemis.marketing.writing_studio.live_content import get_live_text

    draft_id = await _make_deliverable(db_session)
    room_key = str(draft_id)
    stub = _register_stub(room_key)
    room = room_registry.get(room_key)
    room.hydrate("")  # baseline at version 0

    await _handle_materialize(
        room, room_key, draft_id, {"type": "materialize", "version": 0, "text": "converged body"}
    )

    # DB proof: live_content now holds the converged text.
    async with db_module.SessionLocal() as s:
        assert await get_live_text(s, draft_id) == "converged body"

    # Broadcast proof: a collab.flushed carries the bumped CAS counter.
    flushed = [e for e in stub.sent if e["type"] == "collab.flushed"]
    assert len(flushed) == 1
    assert flushed[0]["liveContentVersion"] == 1
    assert room.last_flushed_version == 0


@pytest.mark.asyncio
async def test_materialize_is_idempotent_across_peers(db_session: AsyncSession) -> None:
    """A second materialize at the same already-flushed version is a no-op."""
    _fresh()
    from artemis.marketing.writing_studio.collab.rooms import room_registry
    from artemis.marketing.writing_studio.collab.routes import _handle_materialize

    draft_id = await _make_deliverable(db_session)
    room_key = str(draft_id)
    stub = _register_stub(room_key)
    room = room_registry.get(room_key)
    room.hydrate("")

    msg = {"type": "materialize", "version": 0, "text": "body"}
    await _handle_materialize(room, room_key, draft_id, msg)
    await _handle_materialize(room, room_key, draft_id, msg)  # redundant peer offer

    assert len([e for e in stub.sent if e["type"] == "collab.flushed"]) == 1


@pytest.mark.asyncio
async def test_save_version_broadcasts_rebase_and_rehydrates_room(
    db_session: AsyncSession,
) -> None:
    """PUT {content} (Save-version) snaps live editors via collab.rebase."""
    _fresh()
    from artemis.marketing.writing_studio.collab.rooms import room_registry
    from artemis.marketing.writing_studio.collab.routes import broadcast_version_rebase

    draft_id = await _make_deliverable(db_session)
    room_key = str(draft_id)
    stub = _register_stub(room_key)
    room = room_registry.get(room_key)
    room.hydrate("old baseline")
    room.append_steps([_STEP, _STEP], "AAA")  # advance to version 2
    assert room.version == 2

    await broadcast_version_rebase(draft_id, "saved version body")

    # Room re-hydrated to the saved content at version 0.
    assert room.base_text == "saved version body"
    assert room.version == 0
    # Editors told to snap.
    rebase = [e for e in stub.sent if e["type"] == "collab.rebase"]
    assert len(rebase) == 1
    assert rebase[0]["doc"] == "saved version body"
    assert rebase[0]["version"] == 0


@pytest.mark.asyncio
async def test_put_content_triggers_rebase_broadcast(db_session: AsyncSession) -> None:
    """End-to-end: the HTTP Save-version PUT drives the rebase broadcast."""
    _fresh()
    from httpx import ASGITransport, AsyncClient

    from artemis.main import app
    from artemis.marketing.writing_studio.collab.rooms import room_registry

    draft_id = await _make_deliverable(db_session)
    room_key = str(draft_id)
    stub = _register_stub(room_key)
    room = room_registry.get(room_key)
    room.hydrate("draft text")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/writing-studio/drafts/{draft_id}",
            json={"content": "committed body", "source": "test"},
            headers={"X-Artemis-Token": "test-token"},
        )
    assert resp.status_code == 200

    rebase = [e for e in stub.sent if e["type"] == "collab.rebase"]
    assert len(rebase) == 1
    assert rebase[0]["doc"] == "committed body"
    assert room.base_text == "committed body"
