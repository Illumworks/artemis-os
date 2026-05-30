"""M6 — Tests for memory shell read endpoints.

Tests:
  1. GET /api/memory/drawers returns paginated shape (10 drawers, 2 scopes).
  2. Filter by scope_kind narrows result.
  3. GET /api/memory/observations/{id} returns evidence chain (2 evidence rows).
  4. GET /api/memory/scopes aggregates row counts correctly.
  5. GET /api/memory/stats returns correct totals.

Fixture isolation: TRUNCATE memory tables before each test via _TRUNCATE_SQL.
The conftest for this directory only handles integrations tables; this module
manages its own session/engine using the memory test engine pattern.

Embedding is bypassed via embedding_provider=None (the default) — writes still
go through write_drawer / write_observation, but no embedding model is loaded
and no extra async tasks fire.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builders.models  # noqa: F401 — registers AgentRun etc. on Base.metadata
import artemis.db
import artemis.memory.models  # noqa: F401 — registers memory models on Base.metadata
from artemis.db import attach_pgvector_codec
from artemis.memory.models import MemoryDrawer, MemoryObservation
from artemis.memory.store import link_evidence

pytestmark = pytest.mark.asyncio

# ── Engine / truncate setup ───────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "Set ARTEMIS_DB_URL or ARTEMIS_TEST_DB_URL to a URL containing 'artemis_test'."
    )

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)

# Patch global engine so the FastAPI app's get_session uses the test DB.
artemis.db.engine = _test_engine
import sqlalchemy.ext.asyncio as _sa_async  # noqa: E402

artemis.db.SessionLocal = _sa_async.async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text(
    "TRUNCATE memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    """Per-test session; memory tables truncated before the test."""
    async with AsyncSession(_test_engine, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the FastAPI app via ASGI transport."""
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Helpers ─────────────────────────────────────────────────────────────────
# Use direct ORM inserts to avoid embedding side-effects (no raw_inputs
# SELECT FOR UPDATE, no model load) and keep tests fast + deadlock-free.


import hashlib  # noqa: E402


def _hash(scope_kind: str, scope_id: str, content: str) -> str:
    return hashlib.sha256(f"{scope_kind}:{scope_id}:{content}".encode()).hexdigest()


async def _make_drawer(
    content: str,
    scope_kind: str = "agent",
    scope_id: str = "test.agent",
) -> int:
    """Insert a MemoryDrawer row directly, bypassing embedding.

    Opens its own fresh NullPool session so nested-transaction savepoint
    issues with the per-test mem_session are avoided.
    """
    async with AsyncSession(_test_engine, expire_on_commit=False) as sess, sess.begin():
        stmt = (
            pg_insert(MemoryDrawer)
            .values(
                scope_kind=scope_kind,
                scope_id=scope_id,
                content=content,
                content_hash=_hash(scope_kind, scope_id, content),
                source_kind="test",
            )
            .on_conflict_do_nothing(constraint="uq_drawers_scope_hash")
            .returning(MemoryDrawer.id)
        )
        result = await sess.execute(stmt)
        row = result.fetchone()
        assert row is not None
        return int(row[0])


async def _make_observation(
    content: str,
    scope_kind: str = "agent",
    scope_id: str = "test.agent",
) -> int:
    """Insert a MemoryObservation row directly, bypassing embedding + raw_inputs.

    Opens its own fresh NullPool session.
    """
    async with AsyncSession(_test_engine, expire_on_commit=False) as sess, sess.begin():
        stmt = (
            pg_insert(MemoryObservation)
            .values(
                scope_kind=scope_kind,
                scope_id=scope_id,
                content=content,
                content_hash=_hash(scope_kind, scope_id, content),
                category="discovery",
            )
            .on_conflict_do_nothing(constraint="uq_obs_scope_hash")
            .returning(MemoryObservation.id)
        )
        result = await sess.execute(stmt)
        row = result.fetchone()
        assert row is not None
        return int(row[0])


# ── Test 1: drawers list shape ────────────────────────────────────────────────


async def test_drawers_list_paginated_shape(
    client: AsyncClient,
    mem_session: AsyncSession,
) -> None:
    """10 drawers across 2 scopes → default limit 50, offset 0, total 10."""
    for i in range(7):
        await _make_drawer(
            content=f"agent drawer {i}",
            scope_kind="agent",
            scope_id="marketing.scout",
        )
    for i in range(3):
        await _make_drawer(
            content=f"workspace drawer {i}",
            scope_kind="workspace",
            scope_id="amira",
        )

    resp = await client.get("/api/memory/drawers")
    assert resp.status_code == 200
    data = resp.json()
    assert "drawers" in data
    assert data["total"] == 10
    assert data["offset"] == 0
    assert len(data["drawers"]) == 10
    # Each row has expected fields
    row = data["drawers"][0]
    for field in ("id", "scope_kind", "scope_id", "content_preview", "source", "created_at"):
        assert field in row, f"Missing field {field!r} in drawer row"


# ── Test 2: scope_kind filter narrows result ──────────────────────────────────


async def test_drawers_filter_by_scope_kind(
    client: AsyncClient,
    mem_session: AsyncSession,
) -> None:
    """scope_kind=agent returns only agent-scoped drawers."""
    for i in range(4):
        await _make_drawer(
            content=f"agent drawer {i}",
            scope_kind="agent",
            scope_id="marketing.scout",
        )
    for i in range(3):
        await _make_drawer(
            content=f"workspace drawer {i}",
            scope_kind="workspace",
            scope_id="amira",
        )

    resp = await client.get("/api/memory/drawers?scope_kind=agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert all(r["scope_kind"] == "agent" for r in data["drawers"])

    resp2 = await client.get("/api/memory/drawers?scope_kind=workspace")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 3
    assert all(r["scope_kind"] == "workspace" for r in data2["drawers"])


# ── Test 3: observation detail + evidence chain ───────────────────────────────


async def test_observation_detail_evidence_chain(
    client: AsyncClient,
    mem_session: AsyncSession,
) -> None:
    """Observation with 2 evidence links returns both in detail response."""
    # Create 2 drawers
    dr1_id = await _make_drawer("drawer evidence 1", scope_kind="agent", scope_id="test.agent")
    dr2_id = await _make_drawer("drawer evidence 2", scope_kind="agent", scope_id="test.agent")

    # Create observation
    obs_id = await _make_observation(
        "main observation content", scope_kind="agent", scope_id="test.agent"
    )

    # Link both drawers as evidence using a fresh session
    async with AsyncSession(_test_engine, expire_on_commit=False) as sess, sess.begin():
        await link_evidence(
            sess,
            observation_id=obs_id,
            source_kind="drawer",
            source_id=dr1_id,
        )
        await link_evidence(
            sess,
            observation_id=obs_id,
            source_kind="drawer",
            source_id=dr2_id,
        )

    resp = await client.get(f"/api/memory/observations/{obs_id}")
    assert resp.status_code == 200
    data = resp.json()

    assert "observation" in data
    assert data["observation"]["id"] == obs_id
    assert data["observation"]["content"] == "main observation content"

    assert "evidence" in data
    assert len(data["evidence"]) == 2
    ev_source_ids = {ev["source_id"] for ev in data["evidence"]}
    assert dr1_id in ev_source_ids
    assert dr2_id in ev_source_ids

    # Previews come from drawer content
    for ev in data["evidence"]:
        assert ev["source_preview"] is not None
        assert "drawer evidence" in ev["source_preview"]


async def test_observation_detail_404(
    client: AsyncClient,
    mem_session: AsyncSession,
) -> None:
    """Observation not found → 404."""
    resp = await client.get("/api/memory/observations/99999")
    assert resp.status_code == 404


# ── Test 4: scopes aggregation ────────────────────────────────────────────────


async def test_scopes_aggregation(
    client: AsyncClient,
    mem_session: AsyncSession,
) -> None:
    """3 scopes with varying counts → scope list matches fixture."""
    # scope A: 3 drawers, 2 observations
    for i in range(3):
        await _make_drawer(f"scope-a drawer {i}", scope_kind="agent", scope_id="scope-a")
    for i in range(2):
        await _make_observation(f"scope-a obs {i}", scope_kind="agent", scope_id="scope-a")

    # scope B: 1 drawer, 0 observations
    await _make_drawer("scope-b drawer", scope_kind="workspace", scope_id="scope-b")

    # scope C: 0 drawers, 1 observation
    await _make_observation("scope-c obs", scope_kind="global", scope_id="scope-c")

    resp = await client.get("/api/memory/scopes")
    assert resp.status_code == 200
    scopes = resp.json()
    assert isinstance(scopes, list)

    by_id = {s["scope_id"]: s for s in scopes}

    assert "scope-a" in by_id
    assert by_id["scope-a"]["drawer_count"] == 3
    assert by_id["scope-a"]["observation_count"] == 2

    assert "scope-b" in by_id
    assert by_id["scope-b"]["drawer_count"] == 1
    assert by_id["scope-b"]["observation_count"] == 0

    assert "scope-c" in by_id
    assert by_id["scope-c"]["drawer_count"] == 0
    assert by_id["scope-c"]["observation_count"] == 1


# ── Test 5: stats totals ──────────────────────────────────────────────────────


async def test_stats_totals(
    client: AsyncClient,
    mem_session: AsyncSession,
) -> None:
    """Known fixture counts → stats totals match."""
    dr_id = await _make_drawer("stats drawer 1", scope_kind="agent", scope_id="stats.agent")
    await _make_drawer("stats drawer 2", scope_kind="workspace", scope_id="stats.ws")
    obs_id = await _make_observation("stats obs", scope_kind="agent", scope_id="stats.agent")
    # Link 1 evidence row using a fresh session
    async with AsyncSession(_test_engine, expire_on_commit=False) as sess, sess.begin():
        await link_evidence(
            sess,
            observation_id=obs_id,
            source_kind="drawer",
            source_id=dr_id,
        )

    resp = await client.get("/api/memory/stats")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_drawers"] == 2
    assert data["total_observations"] == 1
    assert data["total_evidence_links"] == 1
    # 3 distinct (scope_kind, scope_id) pairs: agent/stats.agent, workspace/stats.ws, agent/stats.agent
    # — actually 2 distinct pairs for observations, 2 distinct for drawers
    # The union gives 2 rows (agent/stats.agent appears in both)
    assert data["scope_count"] == 2
    assert "by_scope_kind" in data
    assert data["by_scope_kind"].get("agent", 0) == 1
