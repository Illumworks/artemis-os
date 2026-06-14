"""Tests for the memory provenance inspector — MemoryReadEvent wiring.

Coverage:
- MemoryObservationDigest schema construction.
- MemoryReadEvent schema construction (event literal, fields).
- _emit_memory_read_event builds digests from ScoredObservation results.
- _emit_memory_read_event truncates text at 200 chars.
- _emit_memory_read_event emits with empty list when retrieval returns nothing.
- _emit_memory_read_event still emits when search_observations raises.
- memory_read_cache: put / get / clear round-trip.
- Backfill route returns 204 when no event cached.
- Backfill route returns cached MemoryReadEvent as JSON.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from artemis.floating_artemis import memory_read_cache as cache
from artemis.floating_artemis.schemas import MemoryObservationDigest, MemoryReadEvent

pytestmark = pytest.mark.asyncio


# ── Schema construction ───────────────────────────────────────────────────────


def test_memory_observation_digest_defaults() -> None:
    d = MemoryObservationDigest(
        id=1,
        drawer="global:global",
        text="Some observation text.",
        score=0.75,
        sources=["global"],
    )
    assert d.why is None
    assert d.drawer == "global:global"
    assert d.score == 0.75


def test_memory_read_event_literal() -> None:
    event = MemoryReadEvent(
        session_id="sess-1",
        turn_id="2026-05-17T00:00:00+00:00",
        observations=[],
    )
    assert event.event == "floating_artemis.memory_read"
    assert event.observations == []


def test_memory_read_event_with_observations() -> None:
    obs = MemoryObservationDigest(
        id=42,
        drawer="agent:fa",
        text="She prefers concise answers.",
        score=0.82,
        sources=["agent"],
    )
    event = MemoryReadEvent(
        session_id="sess-x",
        turn_id="ts-1",
        observations=[obs],
    )
    assert len(event.observations) == 1
    assert event.observations[0].id == 42


# ── memory_read_cache ─────────────────────────────────────────────────────────


def test_cache_put_get_round_trip() -> None:
    event = MemoryReadEvent(
        session_id="cache-test-1",
        turn_id="ts",
        observations=[],
    )
    cache.put(event)
    result = cache.get("cache-test-1")
    assert result is event


def test_cache_get_missing_returns_none() -> None:
    assert cache.get("nonexistent-session-xyz") is None


def test_cache_clear_removes_entry() -> None:
    event = MemoryReadEvent(session_id="cache-test-2", turn_id="ts", observations=[])
    cache.put(event)
    assert cache.get("cache-test-2") is not None
    cache.clear("cache-test-2")
    assert cache.get("cache-test-2") is None


# ── _emit_memory_read_event ───────────────────────────────────────────────────


def _make_scored_obs(id_: int, content: str, score: float = 0.5) -> Any:
    """Build a minimal ScoredObservation-like mock."""
    obs = MagicMock()
    obs.id = id_
    obs.scope_kind = "global"
    obs.scope_id = "global"
    obs.content = content
    obs.final_score = score
    return obs


async def test_emit_memory_read_event_broadcasts_digested_observations() -> None:
    from artemis.floating_artemis.chat import _emit_memory_read_event

    obs = _make_scored_obs(1, "Operator prefers dark mode.", score=0.9)

    broadcast_calls: list[Any] = []

    async def fake_broadcast(session_id: str, event: dict[str, Any]) -> None:
        broadcast_calls.append(event)

    with (
        patch("artemis.floating_artemis.chat._broadcast", side_effect=fake_broadcast),
        patch("artemis.floating_artemis.chat.cache_put"),
        patch(
            "artemis.memory.retrieval.search_observations",
            new_callable=AsyncMock,
            return_value=[obs],
        ),
        patch("artemis.db.SessionLocal") as mock_session_cls,
    ):
        # Patch the async context manager
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # M3: _emit_memory_read_event now requires agent_id; without it (or with an
        # unknown/None agent_id) the scope allowance is DENIED and observations return
        # empty (fail-closed).  Pass agent_id="artemis" so global:global is permitted
        # and the digest/score assertions exercise the actual path.
        await _emit_memory_read_event(
            "sess-emit-1",
            {"query": "preferences", "scope": "global:global", "limit": 10},
            agent_id="artemis",
        )

    assert len(broadcast_calls) == 1
    payload = broadcast_calls[0]
    assert payload["event"] == "floating_artemis.memory_read"
    assert payload["session_id"] == "sess-emit-1"
    assert len(payload["observations"]) == 1
    assert payload["observations"][0]["drawer"] == "global:global"
    assert payload["observations"][0]["score"] == 0.9


async def test_emit_memory_read_event_truncates_text_to_200_chars() -> None:
    from artemis.floating_artemis.chat import _emit_memory_read_event

    long_content = "A" * 300
    obs = _make_scored_obs(2, long_content, score=0.5)

    broadcast_calls: list[Any] = []

    async def fake_broadcast(session_id: str, event: dict[str, Any]) -> None:
        broadcast_calls.append(event)

    with (
        patch("artemis.floating_artemis.chat._broadcast", side_effect=fake_broadcast),
        patch("artemis.floating_artemis.chat.cache_put"),
        patch(
            "artemis.memory.retrieval.search_observations",
            new_callable=AsyncMock,
            return_value=[obs],
        ),
        patch("artemis.db.SessionLocal") as mock_session_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # M3: pass agent_id="artemis" so global:global is permitted and the
        # truncation logic is exercised.  Without agent_id the scope is denied
        # (fail-closed) and observations would be empty, masking the truncation test.
        await _emit_memory_read_event(
            "sess-trunc",
            {"query": "q", "scope": "global:global", "limit": 5},
            agent_id="artemis",
        )

    assert len(broadcast_calls) == 1
    text = broadcast_calls[0]["observations"][0]["text"]
    assert len(text) == 200
    assert text == "A" * 200


async def test_emit_memory_read_event_empty_list_when_no_results() -> None:
    from artemis.floating_artemis.chat import _emit_memory_read_event

    broadcast_calls: list[Any] = []

    async def fake_broadcast(session_id: str, event: dict[str, Any]) -> None:
        broadcast_calls.append(event)

    with (
        patch("artemis.floating_artemis.chat._broadcast", side_effect=fake_broadcast),
        patch("artemis.floating_artemis.chat.cache_put"),
        patch(
            "artemis.memory.retrieval.search_observations",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("artemis.db.SessionLocal") as mock_session_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _emit_memory_read_event("sess-empty", {"query": "nothing", "scope": "global:global"})

    assert len(broadcast_calls) == 1
    assert broadcast_calls[0]["observations"] == []
    assert broadcast_calls[0]["session_id"] == "sess-empty"


async def test_emit_memory_read_event_still_emits_on_db_failure() -> None:
    """Even if search_observations raises, we still emit an empty event."""
    from artemis.floating_artemis.chat import _emit_memory_read_event

    broadcast_calls: list[Any] = []

    async def fake_broadcast(session_id: str, event: dict[str, Any]) -> None:
        broadcast_calls.append(event)

    with (
        patch("artemis.floating_artemis.chat._broadcast", side_effect=fake_broadcast),
        patch("artemis.floating_artemis.chat.cache_put"),
        patch("artemis.db.SessionLocal") as mock_session_cls,
    ):
        # Simulate a DB connection failure
        mock_session_cls.side_effect = RuntimeError("DB unavailable")

        await _emit_memory_read_event("sess-fail", {"query": "q", "scope": "global:global"})

    assert len(broadcast_calls) == 1
    assert broadcast_calls[0]["observations"] == []
    assert broadcast_calls[0]["event"] == "floating_artemis.memory_read"


# ── Backfill route ────────────────────────────────────────────────────────────


async def _make_client() -> AsyncClient:
    from artemis.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_backfill_route_returns_204_when_no_event() -> None:
    cache.clear("no-event-session")
    with patch("artemis.routes.floating_artemis.memory_cache_get", return_value=None):
        async with await _make_client() as client:
            resp = await client.get(
                "/api/floating-artemis/sessions/no-event-session/memory-reads/latest"
            )
    assert resp.status_code == 204


async def test_backfill_route_returns_cached_event() -> None:
    event = MemoryReadEvent(
        session_id="backfill-sess-1",
        turn_id="2026-05-17T10:00:00+00:00",
        observations=[
            MemoryObservationDigest(
                id=99,
                drawer="global:global",
                text="A cached observation.",
                score=0.65,
                sources=["global"],
            )
        ],
    )
    with patch("artemis.routes.floating_artemis.memory_cache_get", return_value=event):
        async with await _make_client() as client:
            resp = await client.get(
                "/api/floating-artemis/sessions/backfill-sess-1/memory-reads/latest"
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["event"] == "floating_artemis.memory_read"
    assert data["session_id"] == "backfill-sess-1"
    assert len(data["observations"]) == 1
    assert data["observations"][0]["id"] == 99
    assert data["observations"][0]["text"] == "A cached observation."
