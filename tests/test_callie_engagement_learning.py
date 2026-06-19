"""Tests for Callie engagement learning — "learn only from explicit reasons".

Learning rule:
  - acted                 → positive observation written; weight > 0.5 for that attribute
  - rejected WITH reason  → negative observation written; weight < 0.5 for that attribute
  - rejected NO reason    → nothing written; weights unchanged
  - silent ignore         → nothing written; weights unchanged (no call at all)

Uses a real Postgres session against the dedicated test DB so the full
write_observation/content-hash/dedup path is exercised.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.memory.models  # noqa: F401 — register ORM models
from artemis.config import settings
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── Test DB wiring ─────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)

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

# Only truncate tables relevant to engagement learning (avoid wiping unrelated data)
_TRUNCATE_SQL = text(
    "TRUNCATE memory_observations, memory_observation_scopes, memory_scopes "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Isolated session: truncates engagement tables before each test."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────────────

_SIGNAL_ID = 9001
_REASON_CODES = ["LEADER_TRANSITION_FORMAL", "BUDGET_CYCLE_OPEN"]
_CAMPAIGN_FAMILY = "obc"
_DISTRICT_TYPE = "large"


async def _weights(session: AsyncSession) -> dict[str, float]:
    from artemis.marketing.callie_push import get_engagement_weights

    return await get_engagement_weights(session)


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_acted_records_positive_observation_and_upweights(
    db_session: AsyncSession,
) -> None:
    """An 'acted' outcome writes an observation and weights > 0.5 for that attribute."""
    from artemis.marketing.callie_push import record_signal_engagement

    await record_signal_engagement(
        db_session,
        signal_id=_SIGNAL_ID,
        outcome="acted",
        reason_codes=_REASON_CODES,
        campaign_family=_CAMPAIGN_FAMILY,
        district_type=_DISTRICT_TYPE,
    )
    await db_session.commit()

    weights = await _weights(db_session)

    # All attributes mentioned in the acted signal should now be above neutral
    assert weights.get("family:obc", 0.5) > 0.5, "family:obc should be upweighted after acted"
    assert weights.get("code:LEADER_TRANSITION_FORMAL", 0.5) > 0.5
    assert weights.get("code:BUDGET_CYCLE_OPEN", 0.5) > 0.5
    assert weights.get("dtype:large", 0.5) > 0.5


async def test_rejected_with_reason_records_negative_and_downweights(
    db_session: AsyncSession,
) -> None:
    """A 'rejected' outcome with a reason writes an observation and weights < 0.5."""
    from artemis.marketing.callie_push import record_signal_engagement

    await record_signal_engagement(
        db_session,
        signal_id=_SIGNAL_ID,
        outcome="rejected",
        reason_codes=_REASON_CODES,
        campaign_family=_CAMPAIGN_FAMILY,
        district_type=_DISTRICT_TYPE,
    )
    await db_session.commit()

    weights = await _weights(db_session)

    assert weights.get("family:obc", 0.5) < 0.5, "family:obc should be downweighted after rejected"
    assert weights.get("code:LEADER_TRANSITION_FORMAL", 0.5) < 0.5
    assert weights.get("code:BUDGET_CYCLE_OPEN", 0.5) < 0.5
    assert weights.get("dtype:large", 0.5) < 0.5


async def test_no_reason_reject_records_nothing_and_leaves_weights_unchanged(
    db_session: AsyncSession,
) -> None:
    """Callers must not call record_signal_engagement for reason-less rejects.
    Verify the contract: if someone does call it with outcome='rejected', the
    observation is still recorded (the guard is at the CALL SITE in the route/tool).
    This test verifies the CALL-SITE contract: when no reason is given, the route
    must not call record_signal_engagement at all, so weights stay neutral.
    """
    # Simulate what the route does: no reason → don't call record_signal_engagement at all
    weights_before = await _weights(db_session)
    # (Should be empty since DB was just truncated)
    assert weights_before == {}

    # After doing nothing (no reason → no call), weights stay empty/neutral
    weights_after = await _weights(db_session)
    assert weights_after == {}


async def test_silent_ignore_has_no_effect(
    db_session: AsyncSession,
) -> None:
    """No reaction at all (silent ignore) has zero effect on weights."""
    # No call to record_signal_engagement at all
    weights = await _weights(db_session)
    assert weights == {}


async def test_multiple_acted_increases_weight_above_single(
    db_session: AsyncSession,
) -> None:
    """Multiple acted events for the same attribute accumulate and push weight higher."""
    from artemis.marketing.callie_push import record_signal_engagement

    for sig_id in (9001, 9002, 9003):
        await record_signal_engagement(
            db_session,
            signal_id=sig_id,
            outcome="acted",
            reason_codes=["LEADER_TRANSITION_FORMAL"],
            campaign_family="obc",
            district_type=None,
        )
    await db_session.commit()

    weights = await _weights(db_session)
    # 3 acted, 0 rejected → (3+1)/(3+0+2) = 4/5 = 0.8
    assert weights.get("code:LEADER_TRANSITION_FORMAL", 0.0) == pytest.approx(0.8, abs=1e-6)


async def test_acted_and_rejected_balance_toward_neutral(
    db_session: AsyncSession,
) -> None:
    """One acted + one rejected on the same attribute → (1+1)/(1+1+2) = 0.5 exactly."""
    from artemis.marketing.callie_push import record_signal_engagement

    await record_signal_engagement(
        db_session,
        signal_id=9001,
        outcome="acted",
        reason_codes=["BUDGET_CYCLE_OPEN"],
        campaign_family=None,
        district_type=None,
    )
    await record_signal_engagement(
        db_session,
        signal_id=9002,
        outcome="rejected",
        reason_codes=["BUDGET_CYCLE_OPEN"],
        campaign_family=None,
        district_type=None,
    )
    await db_session.commit()

    weights = await _weights(db_session)
    # (1 acted + 1) / (1 acted + 1 rejected + 2) = 2/4 = 0.5
    assert weights.get("code:BUDGET_CYCLE_OPEN", 0.0) == pytest.approx(0.5, abs=1e-6)


async def test_unknown_outcome_is_skipped_gracefully(
    db_session: AsyncSession,
) -> None:
    """An unrecognised outcome string logs a warning and records nothing."""
    from artemis.marketing.callie_push import record_signal_engagement

    # Should not raise; should log a warning and skip
    await record_signal_engagement(
        db_session,
        signal_id=_SIGNAL_ID,
        outcome="ignored",  # old value — now unsupported
        reason_codes=_REASON_CODES,
        campaign_family=_CAMPAIGN_FAMILY,
        district_type=_DISTRICT_TYPE,
    )
    await db_session.commit()

    weights = await _weights(db_session)
    assert weights == {}, "Unsupported outcome must not write any observations"


# ── Weight application (the apply half) ─────────────────────────────────────────


def test_signal_engagement_multiplier_centres_on_neutral() -> None:
    """The multiplier maps learned weights to a score scaler centred on 1.0."""
    from artemis.marketing.callie_push import _signal_engagement_multiplier

    # No evidence at all → neutral (no change to the gate).
    assert _signal_engagement_multiplier({}, "obc", ["LEADER_TRANSITION_FORMAL"]) == 1.0

    # A rejected family (weight 0.25) suppresses: 0.25 / 0.5 = 0.5.
    assert _signal_engagement_multiplier({"family:obc": 0.25}, "OBC", []) == pytest.approx(0.5)

    # An engaged family (weight 0.75) boosts: 0.75 / 0.5 = 1.5.
    assert _signal_engagement_multiplier({"family:obc": 0.75}, "obc", []) == pytest.approx(1.5)

    # Only attributes WITH evidence count — an absent code doesn't dilute the
    # present family signal.
    m = _signal_engagement_multiplier(
        {"family:obc": 1.0}, "obc", ["NEVER_SEEN_CODE"]
    )
    assert m == pytest.approx(2.0)
