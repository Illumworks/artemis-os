"""Tests for the Screen-Time Watch page backend (artemis/routes/screentime.py).

Coverage (observe the EFFECT against the screentime test DB):
  T1  /state-stance returns ALL 51 entries (50 states + DC); empty DB → every
      entry no_info (honest gray), never a gap.
  T2  seeded stance rows surface with their stance/rationale/count; un-seeded
      states still fall back to no_info.
  T3  /signals lists rows, newest first, with the full row shape + total.
  T4  /signals filters (state, stance, status, q free-text) AND-combine and
      return the correct subset + total.
  T5  /signals pagination (limit/offset) returns the right page + stable total.
  T6  /purge empties the screentime_* tables (owner-gated; dev mode = owner).

DB wiring mirrors tests/test_screentime_callie_report.py: own NullPool engine
bound to artemis_test_screentime, TRUNCATE the screentime_* tables per test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
import artemis.screentime.models  # noqa: F401 — register screentime models on Base.metadata
from artemis.db import attach_pgvector_codec
from artemis.screentime.models import SCREENTIME_TABLES, ScreentimeSignal, ScreentimeStateStance

pytestmark = pytest.mark.asyncio

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test_screentime",
)
_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    bind=_test_engine, expire_on_commit=False, class_=AsyncSession
)

_TRUNCATE_SQL = text(f"TRUNCATE TABLE {', '.join(SCREENTIME_TABLES)} RESTART IDENTITY CASCADE")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _insert_signal(
    session: AsyncSession,
    *,
    state: str,
    title: str,
    status: str = "passed",
    stance: str = "unfavorable",
    source_type: str = "legislative",
    source_url: str | None = "https://legislature.example/bill/1",
    summary: str | None = "A summary.",
    amira_angle: str | None = "Blanket restriction, no carve-out.",
    level: str = "state",
    district_name: str | None = None,
    content_hash: str | None = None,
    discovered_at: datetime | None = None,
) -> int:
    sig = ScreentimeSignal(
        state=state,
        level=level,
        district_name=district_name,
        title=title,
        summary=summary,
        status=status,
        stance=stance,
        amira_angle=amira_angle,
        source_url=source_url,
        source_type=source_type,
        is_real_move=True,
        content_hash=content_hash or f"hash-{state}-{title}",
        discovered_at=discovered_at or datetime.now(UTC),
    )
    session.add(sig)
    await session.commit()
    await session.refresh(sig)
    return sig.id


async def _insert_state_stance(
    session: AsyncSession,
    *,
    state: str,
    stance: str,
    rationale: str,
    signal_count: int,
) -> None:
    session.add(
        ScreentimeStateStance(
            state=state,
            stance=stance,
            rationale=rationale,
            signal_count=signal_count,
            last_updated=datetime.now(UTC),
        )
    )
    await session.commit()


# ── T1: state-stance renders all 50 states + DC, empty = all no_info ────────────


async def test_state_stance_all_states_no_info_when_empty(db_session, client) -> None:
    res = await client.get("/api/screentime/state-stance")
    assert res.status_code == 200
    body = res.json()
    assert body["total_states"] == 51  # 50 + DC
    assert len(body["states"]) == 51
    codes = {s["state"] for s in body["states"]}
    assert "CA" in codes and "DC" in codes and "WY" in codes
    # Empty DB → every state honest no_info, none missing.
    assert all(s["stance"] == "no_info" for s in body["states"])
    assert all(s["signal_count"] == 0 for s in body["states"])
    assert body["counts"]["no_info"] == 51


# ── T2: seeded stance rows surface; un-seeded fall back to no_info ──────────────


async def test_state_stance_reflects_seeded_rows(db_session, client) -> None:
    await _insert_state_stance(
        db_session, state="TN", stance="favorable", rationale="Carve-out for Amira.", signal_count=3
    )
    await _insert_state_stance(
        db_session, state="CA", stance="unfavorable", rationale="Blanket ban.", signal_count=2
    )

    res = await client.get("/api/screentime/state-stance")
    body = res.json()
    by_state = {s["state"]: s for s in body["states"]}

    assert by_state["TN"]["stance"] == "favorable"
    assert by_state["TN"]["signal_count"] == 3
    assert "Carve-out" in by_state["TN"]["rationale"]
    assert by_state["CA"]["stance"] == "unfavorable"
    # An un-seeded state is still present, honest no_info.
    assert by_state["TX"]["stance"] == "no_info"
    assert body["counts"]["favorable"] == 1
    assert body["counts"]["unfavorable"] == 1
    assert body["counts"]["no_info"] == 49


# ── T3: signals list shape + newest-first ───────────────────────────────────────


async def test_signals_list_shape_and_order(db_session, client) -> None:
    now = datetime.now(UTC)
    await _insert_signal(
        db_session, state="TN", title="Older bill", content_hash="h-old",
        discovered_at=now - timedelta(days=2),
    )
    await _insert_signal(
        db_session, state="CA", title="Newer bill", content_hash="h-new",
        discovered_at=now,
    )

    res = await client.get("/api/screentime/signals")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert len(body["signals"]) == 2
    # Newest first.
    assert body["signals"][0]["title"] == "Newer bill"
    row = body["signals"][0]
    for key in (
        "id", "title", "state", "status", "stance", "source_type",
        "source_url", "amira_angle", "discovered_at",
    ):
        assert key in row


# ── T4: filters AND-combine ─────────────────────────────────────────────────────


async def test_signals_filters(db_session, client) -> None:
    await _insert_signal(
        db_session, state="TN", title="TN favorable carve-out", stance="favorable",
        status="passed", summary="exemption for evidence-based tools", content_hash="h1",
    )
    await _insert_signal(
        db_session, state="CA", title="CA blanket ban", stance="unfavorable",
        status="passed", summary="blanket restriction on screens",
        amira_angle="Restricts broadly.", content_hash="h2",
    )
    await _insert_signal(
        db_session, state="TN", title="TN proposed neutral", stance="neutral",
        status="proposed", summary="study committee", content_hash="h3",
    )

    # state filter
    res = await client.get("/api/screentime/signals", params={"state": "tn"})
    assert {s["title"] for s in res.json()["signals"]} == {"TN favorable carve-out", "TN proposed neutral"}
    assert res.json()["total"] == 2

    # state + stance AND
    res = await client.get("/api/screentime/signals", params={"state": "TN", "stance": "favorable"})
    body = res.json()
    assert body["total"] == 1
    assert body["signals"][0]["title"] == "TN favorable carve-out"

    # status filter
    res = await client.get("/api/screentime/signals", params={"status": "proposed"})
    assert res.json()["total"] == 1

    # free-text search hits summary
    res = await client.get("/api/screentime/signals", params={"q": "carve-out"})
    titles = {s["title"] for s in res.json()["signals"]}
    assert "TN favorable carve-out" in titles
    assert "CA blanket ban" not in titles


# ── T5: pagination ──────────────────────────────────────────────────────────────


async def test_signals_pagination(db_session, client) -> None:
    for i in range(5):
        await _insert_signal(
            db_session, state="NY", title=f"bill {i}", content_hash=f"hp{i}",
            discovered_at=datetime.now(UTC) - timedelta(minutes=i),
        )

    res = await client.get("/api/screentime/signals", params={"limit": 2, "offset": 0})
    body = res.json()
    assert body["total"] == 5
    assert len(body["signals"]) == 2
    assert body["signals"][0]["title"] == "bill 0"  # newest

    res2 = await client.get("/api/screentime/signals", params={"limit": 2, "offset": 2})
    body2 = res2.json()
    assert body2["total"] == 5
    assert len(body2["signals"]) == 2
    assert body2["signals"][0]["title"] == "bill 2"


# ── T6: purge empties the tables (owner-gated; dev mode = owner) ─────────────────


async def test_purge_empties_tables(db_session, client) -> None:
    await _insert_signal(db_session, state="TX", title="to be purged", content_hash="hpurge")
    await _insert_state_stance(
        db_session, state="TX", stance="unfavorable", rationale="x", signal_count=1
    )

    # sanity: present before
    before = await client.get("/api/screentime/signals")
    assert before.json()["total"] == 1

    res = await client.post("/api/screentime/purge")
    assert res.status_code == 200
    assert res.json()["ok"] is True

    after = await client.get("/api/screentime/signals")
    assert after.json()["total"] == 0
    # state-stance rollup rows gone too → all no_info again
    stance = await client.get("/api/screentime/state-stance")
    assert all(s["stance"] == "no_info" for s in stance.json()["states"])
