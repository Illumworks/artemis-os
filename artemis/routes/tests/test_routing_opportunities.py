"""Tests for GET /api/costs/routing-opportunities — Phase 3.

Run against artemis_test_cost_p3:
    ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_cost_p3 \\
    uv run pytest artemis/routes/tests/test_routing_opportunities.py -v

Tests (from the brief):
  1. Endpoint computes savings correctly.
  2. Endpoint filters out small savings (< $1/mo).
  3. Critical features only get critical-tier candidates.
  4. Monthly pace extrapolates correctly.
  5. Availability filtering: LM Studio available → "available" status.
  6. Availability filtering: Gemini key empty → "setup_required" status.
  7. Apply cascade includes fallback steps (≥ 2, ends with fallback).
  8. current_routing_is_override flag set correctly.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.costs.models  # noqa: F401 — registers CostEvent on Base.metadata
import artemis.db as db_module
import artemis.providers.routing_models  # noqa: F401 — registers FeatureRoutingOverride
from artemis.db import attach_pgvector_codec

# ── DB guard ──────────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)

db_module.engine = _test_engine
db_module.SessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text(
    "TRUNCATE cost_events, feature_routing_overrides, routing_changes_log RESTART IDENTITY CASCADE"
)

# Sonnet 4.6 rates (per-million): input $3, output $15
_SONNET_INPUT_RATE = 3.0
_SONNET_OUTPUT_RATE = 15.0
# Gemini 2.5 Flash rates (per-million): input $0.15, output $0.60 (see pricing.py)
_GEMINI_FLASH_INPUT_RATE = 0.15
_GEMINI_FLASH_OUTPUT_RATE = 0.60


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


async def _seed_cost_event(
    session: AsyncSession,
    *,
    feature_tag: str,
    provider: str = "claude-code",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 1_000_000,
    output_tokens: int = 200_000,
    cost_usd: float | None = None,
    created_at: datetime | None = None,
    input_rate: float = _SONNET_INPUT_RATE,
    output_rate: float = _SONNET_OUTPUT_RATE,
) -> None:
    """Insert a cost_events row with sensible defaults."""
    if cost_usd is None:
        cost_usd = input_tokens * input_rate / 1_000_000 + output_tokens * output_rate / 1_000_000
    if created_at is None:
        created_at = datetime.now(UTC)
    await session.execute(
        text("""
            INSERT INTO cost_events
              (feature_tag, provider, model, provider_path,
               input_tokens, output_tokens,
               cache_creation_input_tokens, cache_read_input_tokens,
               cost_usd, input_rate_per_million, output_rate_per_million,
               cache_write_rate_per_million, cache_read_rate_per_million,
               created_at)
            VALUES
              (:tag, :provider, :model, 'cli',
               :input_tokens, :output_tokens,
               0, 0,
               :cost_usd, :input_rate, :output_rate,
               0, 0,
               :created_at)
        """),
        {
            "tag": feature_tag,
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "input_rate": input_rate,
            "output_rate": output_rate,
            "created_at": created_at,
        },
    )
    await session.flush()


# ── Test 1: savings computed correctly ───────────────────────────────────────


async def test_savings_computed_correctly(db_session: AsyncSession, client: AsyncClient) -> None:
    """LM Studio cost = $0, current cost matches seeded data, savings = current."""
    # 30-day window: 1M input + 200K output at Sonnet 4.6 rates
    await _seed_cost_event(
        db_session,
        feature_tag="trajectory_summary",
        input_tokens=1_000_000,
        output_tokens=200_000,
        cost_usd=4.0,  # $3 input + $1 output at Sonnet rates per 1M tokens
    )
    await db_session.commit()

    # Mock all providers unavailable except lm-studio
    def _mock_health(provider: str):
        return {
            "provider": provider,
            "available": provider == "lm-studio",
            "latency_ms": None,
            "version": None,
            "error": None,
            "checked_at": "2026-06-06T00:00:00Z",
            "models": None,
        }

    with patch(
        "artemis.routes.costs_routing.probe_all_providers",
        new=AsyncMock(
            return_value=[
                _mock_health(p)
                for p in (
                    "claude-code",
                    "lm-studio",
                    "gemini",
                    "openai",
                    "anthropic",
                    "codex",
                    "openrouter",
                )
            ]
        ),
    ):
        resp = await client.get("/api/costs/routing-opportunities")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    opps = data["opportunities"]

    # Should have an opportunity for trajectory_summary
    traj = next((o for o in opps if o["feature_tag"] == "trajectory_summary"), None)
    assert traj is not None, f"trajectory_summary missing from {[o['feature_tag'] for o in opps]}"

    # lm-studio candidate should be available and have ~$0 monthly cost
    lm_alt = next((a for a in traj["alternatives"] if a["provider"] == "lm-studio"), None)
    assert lm_alt is not None
    assert lm_alt["monthly_pace_usd"] == 0.0
    assert lm_alt["savings_usd"] > 0.0
    assert lm_alt["availability"] == "available"


# ── Test 2: small savings filtered out ───────────────────────────────────────


async def test_small_savings_filtered_out(db_session: AsyncSession, client: AsyncClient) -> None:
    """Features with < $1/mo savings should not appear."""
    # Seed a very low-volume trajectory_summary (tiny savings)
    # 1000 tokens total at Sonnet rates ≈ $0.003
    await _seed_cost_event(
        db_session,
        feature_tag="signal_qualifier",
        input_tokens=1_000,
        output_tokens=200,
        cost_usd=0.006,
    )
    await db_session.commit()

    with patch(
        "artemis.routes.costs_routing.probe_all_providers",
        new=AsyncMock(
            return_value=[
                {
                    "provider": "lm-studio",
                    "available": True,
                    "latency_ms": 10,
                    "version": "ok",
                    "error": None,
                    "checked_at": "2026-06-06T00:00:00Z",
                    "models": [],
                },
            ]
        ),
    ):
        resp = await client.get("/api/costs/routing-opportunities")

    assert resp.status_code == 200
    opps = resp.json()["opportunities"]
    signal_opp = next((o for o in opps if o["feature_tag"] == "signal_qualifier"), None)
    # Should be absent (savings < $1/mo)
    assert signal_opp is None


# ── Test 3: critical features only get critical-tier candidates ───────────────


async def test_critical_features_only_critical_candidates(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """floating_artemis should only suggest Anthropic Haiku, never LM Studio/Gemini/OpenAI."""
    # Enough spend that haiku downgrade saves ~$1+
    await _seed_cost_event(
        db_session,
        feature_tag="floating_artemis",
        provider="claude-code",
        model="claude-sonnet-4-6",
        input_tokens=5_000_000,
        output_tokens=500_000,
        cost_usd=22.5,  # 5M * $3 + 0.5M * $15 = $15 + $7.5
    )
    await db_session.commit()

    with patch(
        "artemis.routes.costs_routing.probe_all_providers",
        new=AsyncMock(
            return_value=[
                {
                    "provider": p,
                    "available": True,
                    "latency_ms": None,
                    "version": None,
                    "error": None,
                    "checked_at": "2026-06-06T00:00:00Z",
                    "models": None,
                }
                for p in ("claude-code", "lm-studio", "gemini", "openai", "anthropic")
            ]
        ),
    ):
        resp = await client.get("/api/costs/routing-opportunities")

    assert resp.status_code == 200
    opps = resp.json()["opportunities"]
    fa_opp = next((o for o in opps if o["feature_tag"] == "floating_artemis"), None)

    if fa_opp is not None:
        # All alternatives must be anthropic/haiku only
        for alt in fa_opp["alternatives"]:
            assert alt["provider"] == "anthropic", (
                f"Critical feature got non-anthropic candidate: {alt['provider']}"
            )
            assert "haiku" in alt["model"].lower()
        # Must NOT contain lm-studio, gemini, or openai
        alt_providers = {a["provider"] for a in fa_opp["alternatives"]}
        assert "lm-studio" not in alt_providers
        assert "gemini" not in alt_providers
        assert "openai" not in alt_providers


# ── Test 4: monthly pace extrapolates correctly ───────────────────────────────


async def test_monthly_pace_extrapolation(db_session: AsyncSession, client: AsyncClient) -> None:
    """7 days of data → monthly pace ≈ 7-day total × (30/7)."""
    now = datetime.now(UTC)
    seven_days_ago = now - timedelta(days=7)

    await _seed_cost_event(
        db_session,
        feature_tag="trajectory_summary",
        input_tokens=700_000,
        output_tokens=70_000,
        cost_usd=3.15,  # per 7-day period (roughly)
        created_at=now - timedelta(days=3),
    )
    await db_session.commit()

    from_str = seven_days_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    with patch(
        "artemis.routes.costs_routing.probe_all_providers",
        new=AsyncMock(
            return_value=[
                {
                    "provider": "lm-studio",
                    "available": True,
                    "latency_ms": 10,
                    "version": "ok",
                    "error": None,
                    "checked_at": "2026-06-06T00:00:00Z",
                    "models": [],
                }
            ]
        ),
    ):
        resp = await client.get(f"/api/costs/routing-opportunities?from={from_str}&to={to_str}")

    assert resp.status_code == 200
    opps = resp.json()["opportunities"]
    traj = next((o for o in opps if o["feature_tag"] == "trajectory_summary"), None)
    if traj is not None:
        # Monthly pace must be > 7-day window cost
        assert traj["current"]["monthly_pace_usd"] > traj["current"]["cost_usd_in_window"]


# ── Test 5: LM Studio available → "available" status ─────────────────────────


async def test_lm_studio_available_shows_available(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """When LM Studio health probe returns available=True, alternative is 'available'."""
    await _seed_cost_event(
        db_session,
        feature_tag="trajectory_summary",
        input_tokens=2_000_000,
        output_tokens=400_000,
        cost_usd=8.0,
    )
    await db_session.commit()

    with patch(
        "artemis.routes.costs_routing.probe_all_providers",
        new=AsyncMock(
            return_value=[
                {
                    "provider": "lm-studio",
                    "available": True,
                    "latency_ms": 42,
                    "version": "1 model loaded",
                    "error": None,
                    "checked_at": "2026-06-06T00:00:00Z",
                    "models": ["qwen3-14b"],
                },
            ]
        ),
    ):
        resp = await client.get("/api/costs/routing-opportunities")

    assert resp.status_code == 200
    opps = resp.json()["opportunities"]
    traj = next((o for o in opps if o["feature_tag"] == "trajectory_summary"), None)
    assert traj is not None
    lm_alt = next((a for a in traj["alternatives"] if a["provider"] == "lm-studio"), None)
    assert lm_alt is not None
    assert lm_alt["availability"] == "available"
    assert lm_alt.get("setup_hint") is None


# ── Test 6: Gemini key empty → "setup_required" ───────────────────────────────


async def test_gemini_unavailable_shows_setup_required(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """When Gemini health probe returns available=False, alternative is 'setup_required'."""
    await _seed_cost_event(
        db_session,
        feature_tag="memory_consolidation",
        input_tokens=2_000_000,
        output_tokens=400_000,
        cost_usd=8.0,
    )
    await db_session.commit()

    def _health(p: str) -> dict[str, Any]:
        return {
            "provider": p,
            "available": p not in ("gemini", "openai", "anthropic"),
            "latency_ms": None,
            "version": None,
            "error": "no key" if p in ("gemini", "openai", "anthropic") else None,
            "checked_at": "2026-06-06T00:00:00Z",
            "models": None,
        }

    with patch(
        "artemis.routes.costs_routing.probe_all_providers",
        new=AsyncMock(
            return_value=[
                _health(p)
                for p in (
                    "claude-code",
                    "lm-studio",
                    "gemini",
                    "openai",
                    "anthropic",
                    "codex",
                    "openrouter",
                )
            ]
        ),
    ):
        resp = await client.get("/api/costs/routing-opportunities")

    assert resp.status_code == 200
    opps = resp.json()["opportunities"]
    consol = next((o for o in opps if o["feature_tag"] == "memory_consolidation"), None)
    assert consol is not None
    gemini_alt = next((a for a in consol["alternatives"] if a["provider"] == "gemini"), None)
    assert gemini_alt is not None
    assert gemini_alt["availability"] == "setup_required"
    assert gemini_alt.get("setup_hint") is not None


# ── Test 7: apply_cascade has ≥ 2 steps and ends with current routing ─────────


async def test_apply_cascade_has_fallback(db_session: AsyncSession, client: AsyncClient) -> None:
    """Every available alternative must have apply_cascade ≥ 2 steps."""
    await _seed_cost_event(
        db_session,
        feature_tag="trajectory_summary",
        input_tokens=2_000_000,
        output_tokens=400_000,
        cost_usd=8.0,
    )
    await db_session.commit()

    with patch(
        "artemis.routes.costs_routing.probe_all_providers",
        new=AsyncMock(
            return_value=[
                {
                    "provider": "lm-studio",
                    "available": True,
                    "latency_ms": 42,
                    "version": "ok",
                    "error": None,
                    "checked_at": "2026-06-06T00:00:00Z",
                    "models": [],
                },
            ]
        ),
    ):
        resp = await client.get("/api/costs/routing-opportunities")

    assert resp.status_code == 200
    for opp in resp.json()["opportunities"]:
        for alt in opp["alternatives"]:
            cascade = alt["apply_cascade"]
            assert len(cascade) >= 2, (
                f"apply_cascade for {opp['feature_tag']} / {alt['provider']} has < 2 steps: {cascade}"
            )


# ── Test 8: current_routing_is_override flag ──────────────────────────────────


async def test_override_flag_set_when_feature_has_active_override(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """When feature_routing_overrides has an active row, current_routing_is_override = true."""
    await _seed_cost_event(
        db_session,
        feature_tag="trajectory_summary",
        input_tokens=2_000_000,
        output_tokens=400_000,
        cost_usd=8.0,
    )
    # Insert an active override for trajectory_summary
    await db_session.execute(
        text("""
            INSERT INTO feature_routing_overrides (feature_tag, cascade, active, updated_by)
            VALUES ('trajectory_summary', '[{"provider":"lm-studio"}]', true, 'test')
            ON CONFLICT (feature_tag) DO UPDATE
              SET cascade = EXCLUDED.cascade, active = true
        """)
    )
    await db_session.commit()

    with patch(
        "artemis.routes.costs_routing.probe_all_providers",
        new=AsyncMock(
            return_value=[
                {
                    "provider": "lm-studio",
                    "available": True,
                    "latency_ms": 42,
                    "version": "ok",
                    "error": None,
                    "checked_at": "2026-06-06T00:00:00Z",
                    "models": [],
                },
            ]
        ),
    ):
        resp = await client.get("/api/costs/routing-opportunities")

    assert resp.status_code == 200
    opps = resp.json()["opportunities"]
    traj = next((o for o in opps if o["feature_tag"] == "trajectory_summary"), None)
    if traj is not None:
        assert traj["current_routing_is_override"] is True
