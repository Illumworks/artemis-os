"""Tests for GET /api/costs/summary — Phase 2 visibility dashboard.

Run against artemis_test_cost_p2 (already migrated):
    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_cost_p2 \\
    uv run pytest artemis/routes/tests/test_costs_summary.py -v

Tests:
  1. Summary aggregates correctly — 10 rows, 3 feature_tags, 2 models.
  2. Prior window aligns to same duration.
  3. Cache savings computed correctly.
  4. Filter by feature_tag narrows response.
  5. Filter by provider narrows response.
  6. today block uses today UTC start.
  7. top_calls returns top 20 by cost desc.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.costs.models  # noqa: F401 — registers CostEvent on Base.metadata
import artemis.db as db_module
from artemis.db import attach_pgvector_codec

# ── DB guard ──────────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)

# Redirect the app's DB engine so FastAPI endpoint uses the test DB.
db_module.engine = _test_engine
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

db_module.SessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text("TRUNCATE cost_events RESTART IDENTITY CASCADE")


# ── Fixtures ──────────────────────────────────────────────────────────────────


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


# ── Seed helpers ──────────────────────────────────────────────────────────────

_INSERT = text("""
    INSERT INTO cost_events (
        created_at, provider, model, provider_path, feature_tag,
        input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens,
        input_rate_per_million, output_rate_per_million,
        cache_write_rate_per_million, cache_read_rate_per_million,
        cost_usd
    ) VALUES (
        :created_at, :provider, :model, :provider_path, :feature_tag,
        :input_tokens, :output_tokens, :cache_creation_input_tokens, :cache_read_input_tokens,
        :input_rate_per_million, :output_rate_per_million,
        :cache_write_rate_per_million, :cache_read_rate_per_million,
        :cost_usd
    )
""")


def _row(
    *,
    created_at: datetime,
    feature_tag: str = "agent_run",
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    provider_path: str = "api",
    input_tokens: int = 100_000,
    output_tokens: int = 10_000,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    input_rate_per_million: float = 3.0,
    output_rate_per_million: float = 15.0,
    cache_write_rate_per_million: float = 3.75,
    cache_read_rate_per_million: float = 0.30,
    cost_usd: float | None = None,
) -> dict:
    if cost_usd is None:
        cost_usd = (
            input_tokens * input_rate_per_million / 1_000_000
            + output_tokens * output_rate_per_million / 1_000_000
        )
    return {
        "created_at": created_at,
        "provider": provider,
        "model": model,
        "provider_path": provider_path,
        "feature_tag": feature_tag,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "input_rate_per_million": input_rate_per_million,
        "output_rate_per_million": output_rate_per_million,
        "cache_write_rate_per_million": cache_write_rate_per_million,
        "cache_read_rate_per_million": cache_read_rate_per_million,
        "cost_usd": cost_usd,
    }


# ── Test 1: aggregates correctly ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_aggregates_correctly(db_session: AsyncSession, client: AsyncClient) -> None:
    """10 rows across 3 feature_tags and 2 models; totals must match SUM."""
    base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    rows = (
        [
            _row(
                created_at=base + timedelta(hours=i),
                feature_tag="agent_run",
                model="claude-sonnet-4-6",
                cost_usd=1.00,
            )
            for i in range(4)
        ]
        + [
            _row(
                created_at=base + timedelta(hours=i + 4),
                feature_tag="floating_artemis",
                model="claude-sonnet-4-6",
                cost_usd=2.00,
            )
            for i in range(3)
        ]
        + [
            _row(
                created_at=base + timedelta(hours=i + 7),
                feature_tag="memory_consolidation",
                model="claude-haiku-4-5",
                provider="anthropic",
                cost_usd=0.50,
            )
            for i in range(3)
        ]
    )
    async with db_session.begin():
        for r in rows:
            await db_session.execute(_INSERT, r)

    resp = await client.get(
        "/api/costs/summary",
        params={"from": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    expected_total = 4 * 1.00 + 3 * 2.00 + 3 * 0.50
    assert abs(data["totals"]["cost_usd"] - expected_total) < 0.001
    assert data["totals"]["calls"] == 10

    feature_tags = {r["feature_tag"] for r in data["by_feature"]}
    assert {"agent_run", "floating_artemis", "memory_consolidation"} == feature_tags

    models = {r["model"] for r in data["by_model"]}
    assert {"claude-sonnet-4-6", "claude-haiku-4-5"} == models

    # Shares must sum to ~1.0
    total_share = sum(r["share"] for r in data["by_feature"])
    assert abs(total_share - 1.0) < 0.01


# ── Test 2: prior window aligns to same duration ──────────────────────────────


@pytest.mark.asyncio
async def test_prior_window_aligns(db_session: AsyncSession, client: AsyncClient) -> None:
    """Prior window is exactly the same duration, ending at the start of the main window."""
    # Seed 5 rows in the main window (June 1–6) and 3 in the prior window (May 27–June 1)
    june_base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    may_base = datetime(2026, 5, 27, 0, 0, 0, tzinfo=UTC)

    main_rows = [_row(created_at=june_base + timedelta(hours=i), cost_usd=5.0) for i in range(5)]
    prior_rows = [_row(created_at=may_base + timedelta(hours=i), cost_usd=10.0) for i in range(3)]
    async with db_session.begin():
        for r in main_rows + prior_rows:
            await db_session.execute(_INSERT, r)

    # Window: June 1–6 (5 days = 120 hours); prior window should be May 27–June 1
    resp = await client.get(
        "/api/costs/summary",
        params={"from": "2026-06-01T00:00:00Z", "to": "2026-06-06T00:00:00Z"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["window"]["from"] == "2026-06-01T00:00:00Z"
    assert data["window"]["to"] == "2026-06-06T00:00:00Z"
    assert data["prior_window"]["from"] == "2026-05-27T00:00:00Z"
    assert data["prior_window"]["to"] == "2026-06-01T00:00:00Z"

    assert abs(data["totals"]["cost_usd"] - 5 * 5.0) < 0.001
    assert abs(data["prior_totals"]["cost_usd"] - 3 * 10.0) < 0.001


# ── Test 3: cache savings computed correctly ──────────────────────────────────


@pytest.mark.asyncio
async def test_cache_savings_math(db_session: AsyncSession, client: AsyncClient) -> None:
    """cache_savings_usd = SUM(cache_read_tokens*(input_rate - cache_read_rate))/1_000_000."""
    base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    # 2 rows: each has 500_000 cache_read_input_tokens
    # savings per row = 500_000 * (3.0 - 0.30) / 1_000_000 = 1.35
    cache_rows = [
        _row(
            created_at=base + timedelta(hours=i),
            cache_read_input_tokens=500_000,
            input_rate_per_million=3.0,
            cache_read_rate_per_million=0.30,
        )
        for i in range(2)
    ]
    async with db_session.begin():
        for r in cache_rows:
            await db_session.execute(_INSERT, r)

    resp = await client.get(
        "/api/costs/summary",
        params={"from": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z"},
    )
    assert resp.status_code == 200
    data = resp.json()

    expected_savings = 2 * (500_000 * (3.0 - 0.30) / 1_000_000)
    assert abs(data["totals"]["cache_savings_usd"] - expected_savings) < 0.001
    assert data["totals"]["cache_read_tokens"] == 1_000_000


# ── Test 4: filter by feature_tag ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_by_feature_tag(db_session: AsyncSession, client: AsyncClient) -> None:
    base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    rows = [
        _row(created_at=base, feature_tag="agent_run", cost_usd=5.0),
        _row(created_at=base + timedelta(hours=1), feature_tag="floating_artemis", cost_usd=10.0),
    ]
    async with db_session.begin():
        for r in rows:
            await db_session.execute(_INSERT, r)

    resp = await client.get(
        "/api/costs/summary",
        params={
            "from": "2026-06-01T00:00:00Z",
            "to": "2026-06-02T00:00:00Z",
            "feature_tag": "agent_run",
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert abs(data["totals"]["cost_usd"] - 5.0) < 0.001
    assert data["totals"]["calls"] == 1
    assert all(r["feature_tag"] == "agent_run" for r in data["by_feature"])


# ── Test 5: filter by provider ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_by_provider(db_session: AsyncSession, client: AsyncClient) -> None:
    base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    rows = [
        _row(created_at=base, provider="anthropic", cost_usd=5.0),
        _row(
            created_at=base + timedelta(hours=1),
            provider="openai",
            model="gpt-4o",
            cost_usd=3.0,
            input_rate_per_million=2.5,
            output_rate_per_million=10.0,
            cache_write_rate_per_million=0.0,
            cache_read_rate_per_million=0.0,
        ),
    ]
    async with db_session.begin():
        for r in rows:
            await db_session.execute(_INSERT, r)

    resp = await client.get(
        "/api/costs/summary",
        params={
            "from": "2026-06-01T00:00:00Z",
            "to": "2026-06-02T00:00:00Z",
            "provider": "anthropic",
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert abs(data["totals"]["cost_usd"] - 5.0) < 0.001
    assert all(r["provider"] == "anthropic" for r in data["by_model"])


# ── Test 6: today block uses today UTC start ──────────────────────────────────


@pytest.mark.asyncio
async def test_today_block_today_only(db_session: AsyncSession, client: AsyncClient) -> None:
    """today.cost_usd must include only events on or after today UTC midnight."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today_start - timedelta(hours=1)

    rows = [
        _row(created_at=today_start + timedelta(minutes=30), cost_usd=7.0),
        _row(created_at=yesterday, cost_usd=99.0),  # must NOT appear in today
    ]
    async with db_session.begin():
        for r in rows:
            await db_session.execute(_INSERT, r)

    # Use current month-to-date default window (omit from/to)
    resp = await client.get("/api/costs/summary")
    assert resp.status_code == 200
    data = resp.json()

    # today.cost_usd must be exactly 7.0 (yesterday row excluded)
    assert abs(data["today"]["cost_usd"] - 7.0) < 0.001


# ── Test 7: top_calls returns top 20 by cost desc ────────────────────────────


@pytest.mark.asyncio
async def test_top_calls_top_20_descending(db_session: AsyncSession, client: AsyncClient) -> None:
    base = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    # Seed 30 rows with distinct costs ($1.00 to $30.00)
    rows = [_row(created_at=base + timedelta(minutes=i), cost_usd=float(i + 1)) for i in range(30)]
    async with db_session.begin():
        for r in rows:
            await db_session.execute(_INSERT, r)

    resp = await client.get(
        "/api/costs/summary",
        params={"from": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z"},
    )
    assert resp.status_code == 200
    data = resp.json()

    top_calls = data["top_calls"]
    assert len(top_calls) == 20

    # Must be ordered descending by cost_usd
    costs = [r["cost_usd"] for r in top_calls]
    assert costs == sorted(costs, reverse=True)

    # Top row must be $30.00 (the most expensive)
    assert abs(top_calls[0]["cost_usd"] - 30.0) < 0.001
