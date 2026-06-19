"""Tests for Callie proactivity v1 + learn-from-reactions.

Coverage (all unit — no real Slack / LLM calls):
  T1  top-tier signal triggers exactly one proactive push (Slack post dispatched)
  T2  dedup prevents a second push for the same signal_id
  T3  per-day frequency cap holds (cap=1, second call is skipped)
  T4  below-score-gate signal is NOT pushed
  T5  no channel configured → no push
  T6  engagement observation is recorded when Jon acts on a signal (argus-dispatch)
  T7  engagement observations influence get_engagement_weights
  T8  POST /api/signal-queue/{id}/argus-dispatch fires async dispatch, returns 200
  T9  argus-dispatch on a non-hot signal returns 400
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.marketing.models  # noqa: F401 — register models
import artemis.memory.models  # noqa: F401 — register memory models
import artemis.pipelines.models  # noqa: F401 — register pipeline_runs table for FK resolution
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── DB wiring ─────────────────────────────────────────────────────────────────

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test_callie",
)
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
    "TRUNCATE memory_scopes, memory_observations, memory_observation_scopes, "
    "raw_inputs, signal_queue, districts RESTART IDENTITY CASCADE"
)


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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_agent_cfg(token: str = "xoxb-callie-fake") -> MagicMock:
    cfg = MagicMock()
    cfg.access_token = token
    return cfg


def _fake_post_message_calls() -> tuple[list[dict[str, Any]], AsyncMock]:
    calls: list[dict[str, Any]] = []

    async def _post(channel: str, text: str, **_kw: Any) -> dict[str, Any]:
        calls.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "1000000000.000001"}

    mock = AsyncMock(side_effect=_post)
    return calls, mock


# ─────────────────────────────────────────────────────────────────────────────
# T1: top-tier hot signal → exactly one Slack push
# ─────────────────────────────────────────────────────────────────────────────


async def test_top_tier_signal_triggers_one_push(db_session: AsyncSession) -> None:
    """A hot signal above the score gate dispatches exactly one Slack post."""
    from artemis.marketing.callie_push import push_top_tier_signal

    posted, mock_post = _fake_post_message_calls()
    fake_client = MagicMock()
    fake_client.post_message = mock_post

    with (
        patch("artemis.config.settings.callie_proactive_min_score", 0.5),
        patch("artemis.config.settings.callie_proactive_daily_cap", 10),
        patch(
            "artemis.config.settings.callie_proactive_channel",
            "C_MARKETING_TEST",
        ),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=_fake_agent_cfg(),
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
    ):
        result = await push_top_tier_signal(
            db_session,
            signal_id=1001,
            headline="TX district RFP — PROCUREMENT_ELA_ADOPTION",
            district_id="TX-001",
            state="TX",
            campaign_family="obc",
            top_score=0.82,
            reason_codes=[{"code": "PROCUREMENT_ELA_ADOPTION", "confidence": 0.9}],
        )
        await db_session.commit()

    assert result is True, "Expected push to succeed"
    assert len(posted) == 1, f"Expected 1 Slack post, got {len(posted)}"
    assert posted[0]["channel"] == "C_MARKETING_TEST"
    assert "TX district RFP" in posted[0]["text"]
    assert "Argus" in posted[0]["text"] or "dig" in posted[0]["text"].lower(), (
        "Push text should mention Argus / dig deeper offer"
    )


# ─────────────────────────────────────────────────────────────────────────────
# T2: dedup — second call for the same signal is skipped
# ─────────────────────────────────────────────────────────────────────────────


async def test_dedup_prevents_second_push(db_session: AsyncSession) -> None:
    """Second push_top_tier_signal call for same signal_id returns False."""
    from artemis.marketing.callie_push import push_top_tier_signal

    posted, mock_post = _fake_post_message_calls()
    fake_client = MagicMock()
    fake_client.post_message = mock_post

    common_patches = (
        patch("artemis.config.settings.callie_proactive_min_score", 0.5),
        patch("artemis.config.settings.callie_proactive_daily_cap", 10),
        patch("artemis.config.settings.callie_proactive_channel", "C_TEST"),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=_fake_agent_cfg(),
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
    )

    with common_patches[0], common_patches[1], common_patches[2], common_patches[3], common_patches[4]:
        first = await push_top_tier_signal(
            db_session,
            signal_id=2001,
            headline="MO board meeting signal",
            district_id="MO-001",
            state="MO",
            campaign_family="dyslexia",
            top_score=0.75,
            reason_codes=[{"code": "BOARD_MINUTES_ADOPTION", "confidence": 0.8}],
        )
        await db_session.commit()

        second = await push_top_tier_signal(
            db_session,
            signal_id=2001,
            headline="MO board meeting signal",
            district_id="MO-001",
            state="MO",
            campaign_family="dyslexia",
            top_score=0.75,
            reason_codes=[{"code": "BOARD_MINUTES_ADOPTION", "confidence": 0.8}],
        )

    assert first is True, "First push should succeed"
    assert second is False, "Second push for same signal should be deduped"
    assert len(posted) == 1, "Slack post should happen exactly once"


# ─────────────────────────────────────────────────────────────────────────────
# T3: per-day frequency cap
# ─────────────────────────────────────────────────────────────────────────────


async def test_daily_cap_is_respected(db_session: AsyncSession) -> None:
    """With cap=1, the second push on the same day is skipped."""
    from artemis.marketing.callie_push import push_top_tier_signal

    posted, mock_post = _fake_post_message_calls()
    fake_client = MagicMock()
    fake_client.post_message = mock_post

    with (
        patch("artemis.config.settings.callie_proactive_min_score", 0.0),
        patch("artemis.config.settings.callie_proactive_daily_cap", 1),
        patch("artemis.config.settings.callie_proactive_channel", "C_TEST"),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=_fake_agent_cfg(),
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
    ):
        first = await push_top_tier_signal(
            db_session,
            signal_id=3001,
            headline="Signal A",
            district_id="CA-001",
            state="CA",
            campaign_family="obc",
            top_score=0.8,
            reason_codes=[],
        )
        await db_session.commit()

        second = await push_top_tier_signal(
            db_session,
            signal_id=3002,  # different signal — dedup doesn't apply
            headline="Signal B",
            district_id="CA-002",
            state="CA",
            campaign_family="obc",
            top_score=0.8,
            reason_codes=[],
        )

    assert first is True
    assert second is False, "Daily cap=1 should block second push"
    assert len(posted) == 1


# ─────────────────────────────────────────────────────────────────────────────
# T4: below score gate → no push
# ─────────────────────────────────────────────────────────────────────────────


async def test_below_score_gate_skips_push(db_session: AsyncSession) -> None:
    """A hot signal below the score gate is not pushed."""
    from artemis.marketing.callie_push import push_top_tier_signal

    posted, mock_post = _fake_post_message_calls()
    fake_client = MagicMock()
    fake_client.post_message = mock_post

    with (
        patch("artemis.config.settings.callie_proactive_min_score", 0.9),
        patch("artemis.config.settings.callie_proactive_daily_cap", 10),
        patch("artemis.config.settings.callie_proactive_channel", "C_TEST"),
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=_fake_agent_cfg(),
        ),
        patch("artemis.integrations.slack.client.SlackClient", return_value=fake_client),
    ):
        result = await push_top_tier_signal(
            db_session,
            signal_id=4001,
            headline="Below gate signal",
            district_id="TX-999",
            state="TX",
            campaign_family="obc",
            top_score=0.65,  # below gate of 0.9
            reason_codes=[],
        )

    assert result is False
    assert len(posted) == 0


# ─────────────────────────────────────────────────────────────────────────────
# T5: no channel configured → no push
# ─────────────────────────────────────────────────────────────────────────────


async def test_no_channel_skips_push(db_session: AsyncSession) -> None:
    """When no channel is configured, push is skipped gracefully."""
    from artemis.marketing.callie_push import push_top_tier_signal

    with (
        patch("artemis.config.settings.callie_proactive_min_score", 0.5),
        patch("artemis.config.settings.callie_proactive_daily_cap", 10),
        patch("artemis.config.settings.callie_proactive_channel", ""),
        patch("artemis.config.settings.marketing_campaigns_slack_channel", ""),
    ):
        result = await push_top_tier_signal(
            db_session,
            signal_id=5001,
            headline="No channel signal",
            district_id="IL-001",
            state="IL",
            campaign_family="biliteracy",
            top_score=0.88,
            reason_codes=[],
        )

    assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# T6: engagement observation is recorded on argus-dispatch (acted)
# ─────────────────────────────────────────────────────────────────────────────


async def test_engagement_observation_recorded(db_session: AsyncSession) -> None:
    """record_signal_engagement writes a durable observation to agent:callie scope."""
    from artemis.marketing.callie_push import record_signal_engagement

    await record_signal_engagement(
        db_session,
        signal_id=6001,
        outcome="acted",
        reason_codes=["PROCUREMENT_ELA_ADOPTION", "LEADER_TRANSITION_FORMAL"],
        campaign_family="obc",
        district_type="large",
    )
    await db_session.commit()

    # Verify it landed in the DB
    from sqlalchemy import select

    from artemis.memory.models import MemoryObservation

    result = await db_session.execute(
        select(MemoryObservation).where(
            MemoryObservation.scope_kind == "agent",
            MemoryObservation.scope_id == "callie",
            MemoryObservation.content.like("callie_engage:acted:%"),
        )
    )
    obs = result.scalars().all()
    assert len(obs) == 1, f"Expected 1 engagement obs, got {len(obs)}"
    assert "signal=6001" in obs[0].content
    assert "family=obc" in obs[0].content
    assert "PROCUREMENT_ELA_ADOPTION" in obs[0].content


# ─────────────────────────────────────────────────────────────────────────────
# T7: engagement observations influence get_engagement_weights
# ─────────────────────────────────────────────────────────────────────────────


async def test_engagement_weights_favour_acted(db_session: AsyncSession) -> None:
    """get_engagement_weights returns >0.5 for acted attributes, <0.5 for rejected-with-reason."""
    from artemis.marketing.callie_push import get_engagement_weights, record_signal_engagement

    # 3 "acted" on OBC family
    for i in range(3):
        await record_signal_engagement(
            db_session,
            signal_id=7000 + i,
            outcome="acted",
            reason_codes=["PROCUREMENT_ELA_ADOPTION"],
            campaign_family="obc",
            district_type=None,
        )
    # 2 "rejected" (with reason) on biliteracy family
    for i in range(2):
        await record_signal_engagement(
            db_session,
            signal_id=7100 + i,
            outcome="rejected",
            reason_codes=["DISTRICT_DLL_EXPANSION"],
            campaign_family="biliteracy",
            district_type=None,
        )
    await db_session.commit()

    weights = await get_engagement_weights(db_session)

    obc_weight = weights.get("family:obc")
    bili_weight = weights.get("family:biliteracy")
    assert obc_weight is not None, "family:obc should appear in weights"
    assert bili_weight is not None, "family:biliteracy should appear in weights"
    assert obc_weight > bili_weight, (
        f"OBC (all acted) weight {obc_weight:.2f} should exceed "
        f"biliteracy (all rejected) weight {bili_weight:.2f}"
    )
    # Laplace: 3 acted, 0 rejected → (3+1)/(3+0+2) ≈ 0.67
    assert obc_weight > 0.5
    # 0 acted, 2 rejected → (0+1)/(0+2+2) = 0.25
    assert bili_weight < 0.5


# ─────────────────────────────────────────────────────────────────────────────
# T8: dispatch_argus_for_signal route fn returns dispatched payload for hot signal
# ─────────────────────────────────────────────────────────────────────────────


async def test_argus_dispatch_endpoint_hot_signal(db_session: AsyncSession) -> None:
    """dispatch_argus_for_signal on a hot qualified signal returns dispatched payload."""
    from artemis.marketing.models import SignalQueue
    from artemis.marketing.routes.signal_queue import dispatch_argus_for_signal

    # Seed a hot qualified signal
    signal = SignalQueue(
        headline="Hot TX signal for Argus test",
        campaign_family="obc",
        urgency_tier="hot",
        source_type="manual",
        summary="test",
        discovered_by="manual",
        district_id="TX-001",
        state="TX",
        reason_codes=[{"code": "PROCUREMENT_ELA_ADOPTION", "confidence": 0.9}],
        signal_status="qualified",
    )
    db_session.add(signal)
    await db_session.commit()
    await db_session.refresh(signal)
    signal_id = signal.id

    with (
        patch("artemis.config.settings.callie_proactive_channel", "C_TEST"),
        patch("artemis.config.settings.marketing_campaigns_slack_channel", "C_TEST"),
        patch(
            "artemis.floating_artemis.tools.argus_tools._insert_pending_request",
            new_callable=AsyncMock,
            return_value=42,
        ),
        patch(
            "artemis.floating_artemis.tools.argus_tools._safe_research_and_post",
            new_callable=AsyncMock,
        ),
    ):
        result = await dispatch_argus_for_signal(signal_id=signal_id, session=db_session)

    assert result["status"] == "dispatched"
    assert result["signalId"] == signal_id


# ─────────────────────────────────────────────────────────────────────────────
# T9: dispatch_argus_for_signal raises 400 for non-hot signal
# ─────────────────────────────────────────────────────────────────────────────


async def test_argus_dispatch_endpoint_non_hot_returns_400(db_session: AsyncSession) -> None:
    """dispatch_argus_for_signal raises 400/conflict for a standard-tier signal."""
    import fastapi

    from artemis.marketing.models import SignalQueue
    from artemis.marketing.routes.signal_queue import dispatch_argus_for_signal

    signal = SignalQueue(
        headline="Standard TX signal",
        campaign_family="obc",
        urgency_tier="standard",  # not hot
        source_type="manual",
        summary="test",
        discovered_by="manual",
        district_id="TX-002",
        state="TX",
        reason_codes=[],
        signal_status="qualified",
    )
    db_session.add(signal)
    await db_session.commit()
    await db_session.refresh(signal)
    signal_id = signal.id

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await dispatch_argus_for_signal(signal_id=signal_id, session=db_session)

    assert exc_info.value.status_code == 400, (
        f"Expected 400, got {exc_info.value.status_code}"
    )
