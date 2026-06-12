"""Phase 0 collab WebSocket tests.

Covers:
  (a) Two simultaneous connections — room count reaches 2; drops to 1 on disconnect.
  (b) Dev-override query params yield distinct identities in the roster.
  (c) Auth rejection when CF Access is enabled but no JWT header is present.

All tests are hermetic — no real CF Access calls, no DB access.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from starlette.testclient import TestClient

from artemis.marketing.writing_studio.collab.routes import router

# ---------------------------------------------------------------------------
# Minimal app fixture — just the collab router, no other middleware
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# (a) Room count and disconnect
# ---------------------------------------------------------------------------


def test_two_connections_room_count() -> None:
    """After two clients connect to the same draft, room_count == 2."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1001

    with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab") as ws1:
        ws1.receive_json()  # consume presence.init for ws1
        assert collab_manager.room_count(str(draft_id)) == 1

        with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab") as ws2:
            assert collab_manager.room_count(str(draft_id)) == 2

            # Phase 1: ws1 gets presence.join; ws2 gets presence.init (with ws1 as peer).
            join_evt = ws1.receive_json()
            assert join_evt["type"] == "presence.join"
            init_evt = ws2.receive_json()
            assert init_evt["type"] == "presence.init"
            assert len(init_evt["peers"]) == 1

            ws2.close()

        # After second disconnect, count drops to 1.
        assert collab_manager.room_count(str(draft_id)) == 1
        ws1.close()

    assert collab_manager.room_count(str(draft_id)) == 0


def test_disconnect_clears_room() -> None:
    """After all clients disconnect, the room is removed entirely."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1002

    with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab"):
        assert collab_manager.room_count(str(draft_id)) == 1

    assert collab_manager.room_count(str(draft_id)) == 0
    assert str(draft_id) not in collab_manager._rooms


# ---------------------------------------------------------------------------
# (b) Dev-override: distinct identities in the roster
# ---------------------------------------------------------------------------


def test_dev_override_distinct_identities() -> None:
    """?as_email= overrides yield two distinct identities in the roster."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1003

    with (
        tc.websocket_connect(
            f"/api/writing-studio/drafts/{draft_id}/collab?as_email=alice%40example.com&as_name=Alice"
        ),
        tc.websocket_connect(
            f"/api/writing-studio/drafts/{draft_id}/collab?as_email=bob%40example.com&as_name=Bob"
        ),
    ):
        roster = collab_manager.roster(str(draft_id))
        assert len(roster) == 2
        emails = {r.email for r in roster}
        assert "alice@example.com" in emails
        assert "bob@example.com" in emails


def test_dev_override_name_stored() -> None:
    """?as_name= is reflected in the roster identity."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1004

    with tc.websocket_connect(
        f"/api/writing-studio/drafts/{draft_id}/collab?as_email=carol%40example.com&as_name=Carol"
    ):
        roster = collab_manager.roster(str(draft_id))
        assert len(roster) == 1
        assert roster[0].name == "Carol"
        assert roster[0].email == "carol@example.com"


# ---------------------------------------------------------------------------
# (c) Auth rejection: CF Access enabled, no JWT → close 4401
# ---------------------------------------------------------------------------


def test_auth_rejection_no_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    """When CF Access is enabled and no JWT header is present, the connection
    is closed with code 4401."""
    import artemis.config as _cfg

    monkeypatch.setattr(_cfg.settings, "cf_access_enabled", True)

    app = _make_app()
    tc = TestClient(app)

    draft_id = 1005

    # pytest.raises and websocket_connect cannot be merged into a single with-statement
    # because the exception is raised by __enter__ of the inner context manager.
    with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117
        with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab"):
            pass  # should not reach here

    assert exc_info.value.code == 4401


# ---------------------------------------------------------------------------
# (d) Presence event received on connect (single client baseline)
# ---------------------------------------------------------------------------


def test_presence_event_on_connect() -> None:
    """First client receives a presence.init event with empty peers on connect (Phase 1 protocol)."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1006

    with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab") as ws:
        evt = ws.receive_json()
        # Phase 1 replaced collab.presence count with the full roster protocol.
        assert evt["type"] == "presence.init"
        assert evt["peers"] == []
        assert "you" in evt
