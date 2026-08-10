"""Tests for Screen-Time Watch Brief 2 — Callie reports to #policy-watch.

Coverage (all unit — no real Slack / LLM calls):
  T1  channel unset → post_screentime_digest posts nothing, returns 0 (dormant)
  T2  seeded real moves + test channel → one digest posted, source-linked,
      returns the count; re-run posts nothing (dedup via the marker)
  T3  non-real-move signals are excluded from the digest
  T4  is_big_move: large-state passed unfavorable = big; minor signal = not big
  T5  is_big_move: passed favorable carve-out (any state) = big when enabled
  T6  maybe_alert_big_move posts an alert for a big move + marks it reported,
      so it is excluded from the next digest (single report across modes)
  T7  maybe_alert_big_move on a minor signal posts nothing
  T8  maybe_alert_big_move with channel unset posts nothing (dormant)

The digest composition is forced down the deterministic fallback path (provider
import patched to fail) so tests never hit a real LLM but still assert the EFFECT
(a posted, source-linked digest + dedup).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.memory.models  # noqa: F401 — register memory models
import artemis.screentime.models  # noqa: F401 — register screentime models
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── DB wiring (own test DB, mirrors test_callie_proactivity_v1) ──────────────

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test_screentime",
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
    "raw_inputs, screentime_signals RESTART IDENTITY CASCADE"
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

    return calls, AsyncMock(side_effect=_post)


async def _insert_signal(
    session: AsyncSession,
    *,
    state: str,
    title: str,
    status: str = "passed",
    stance: str = "unfavorable",
    is_real_move: bool = True,
    source_url: str | None = "https://legislature.example/bill/1",
    amira_angle: str | None = "Blanket restriction, no carve-out for Amira.",
    district_name: str | None = None,
    content_hash: str | None = None,
) -> int:
    """Insert a screentime signal row, return its id."""
    from artemis.screentime.models import ScreentimeSignal

    sig = ScreentimeSignal(
        state=state,
        level="state",
        district_name=district_name,
        title=title,
        summary="Summary text.",
        status=status,
        stance=stance,
        amira_angle=amira_angle,
        source_url=source_url,
        source_type="legislative",
        published_at=datetime.now(UTC),
        is_real_move=is_real_move,
        content_hash=content_hash or f"hash::{state}::{title}",
    )
    session.add(sig)
    await session.flush()
    return sig.id


def _patch_slack(calls_mock: AsyncMock) -> tuple:
    """Patch Callie's Slack resolution + SlackClient to capture posts, and force
    the digest down the deterministic fallback (no real LLM) by making
    ``complete_with_fallback`` raise — composition then uses the source-linked
    deterministic builder, so tests assert the EFFECT without an LLM call.
    """
    fake_client = MagicMock()
    fake_client.post_message = calls_mock
    return (
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=_fake_agent_cfg(),
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient",
            return_value=fake_client,
        ),
        patch(
            "artemis.providers.fallback.complete_with_fallback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("no LLM in tests"),
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# T1: channel unset → no post, returns 0 (dormant)
# ─────────────────────────────────────────────────────────────────────────────


async def test_digest_dormant_when_channel_unset(db_session: AsyncSession) -> None:
    from artemis.screentime.reporting import post_screentime_digest

    await _insert_signal(db_session, state="CA", title="CA blanket restriction")
    await db_session.commit()

    posted, mock_post = _fake_post_message_calls()
    p_resolve, p_client, p_llm = _patch_slack(mock_post)
    with patch("artemis.config.settings.screentime_report_channel", ""), p_resolve, p_client, p_llm:
        count = await post_screentime_digest(db_session)

    assert count == 0
    assert posted == [], "No Slack post should happen when channel is unset"


# ─────────────────────────────────────────────────────────────────────────────
# T2: digest posts once, source-linked, and dedups on re-run
# ─────────────────────────────────────────────────────────────────────────────


async def test_digest_posts_once_and_dedups(db_session: AsyncSession) -> None:
    from artemis.screentime.reporting import post_screentime_digest

    await _insert_signal(
        db_session,
        state="CA",
        title="CA AB-123 screen-time restriction",
        source_url="https://leginfo.example/CA/AB123",
    )
    await _insert_signal(
        db_session,
        state="TN",
        title="TN evidence-based carve-out",
        stance="favorable",
        source_url="https://leginfo.example/TN/SB456",
        amira_angle="Carve-out for evidence-based tools — Amira qualifies.",
    )
    await db_session.commit()

    posted, mock_post = _fake_post_message_calls()
    p_resolve, p_client, p_llm = _patch_slack(mock_post)
    with (
        patch("artemis.config.settings.screentime_report_channel", "C_POLICY_WATCH"),
        p_resolve,
        p_client,
        p_llm,
    ):
        count = await post_screentime_digest(db_session)
        await db_session.commit()

        assert count == 2, f"Expected 2 signals reported, got {count}"
        assert len(posted) == 1, "Digest is a single message"
        assert posted[0]["channel"] == "C_POLICY_WATCH"
        body = posted[0]["text"]
        # Source-linked (mrkdwn link with the actual bill URL), not bare headline
        assert "https://leginfo.example/CA/AB123" in body
        assert "https://leginfo.example/TN/SB456" in body

        # Re-run: dedup via the marker → nothing posted
        count2 = await post_screentime_digest(db_session)
        await db_session.commit()

    assert count2 == 0, "Re-run must post nothing (dedup marker)"
    assert len(posted) == 1, "No second Slack post on re-run"


# ─────────────────────────────────────────────────────────────────────────────
# T3: non-real-move signals excluded
# ─────────────────────────────────────────────────────────────────────────────


async def test_digest_excludes_non_real_moves(db_session: AsyncSession) -> None:
    from artemis.screentime.reporting import post_screentime_digest

    await _insert_signal(
        db_session,
        state="OH",
        title="OH real legislative move",
        is_real_move=True,
    )
    await _insert_signal(
        db_session,
        state="OH",
        title="OH press chatter headline",
        is_real_move=False,
        content_hash="hash::OH::chatter",
    )
    await db_session.commit()

    posted, mock_post = _fake_post_message_calls()
    p_resolve, p_client, p_llm = _patch_slack(mock_post)
    with (
        patch("artemis.config.settings.screentime_report_channel", "C_POLICY_WATCH"),
        p_resolve,
        p_client,
        p_llm,
    ):
        count = await post_screentime_digest(db_session)
        await db_session.commit()

    assert count == 1, "Only the real move should be reported"
    assert "OH press chatter" not in posted[0]["text"]
    assert "OH real legislative move" in posted[0]["text"]


# ─────────────────────────────────────────────────────────────────────────────
# T4 / T5: is_big_move threshold (pure)
# ─────────────────────────────────────────────────────────────────────────────


def _make_signal(**kw: Any) -> Any:
    from artemis.screentime.models import ScreentimeSignal

    base: dict[str, Any] = {
        "state": "CA",
        "level": "state",
        "title": "t",
        "status": "passed",
        "stance": "unfavorable",
        "is_real_move": True,
        "content_hash": "h",
    }
    base.update(kw)
    return ScreentimeSignal(**base)


def test_is_big_move_large_state_blanket_restriction() -> None:
    from artemis.screentime.reporting import is_big_move

    with (
        patch("artemis.config.settings.screentime_bigmove_states", "CA,TX,FL"),
        patch("artemis.config.settings.screentime_bigmove_statuses", "passed,amended"),
        patch("artemis.config.settings.screentime_bigmove_favorable_alert", True),
    ):
        # Large state + passed + unfavorable = BIG
        assert is_big_move(_make_signal(state="CA", status="passed", stance="unfavorable"))
        # Small state, same shape = NOT big
        assert not is_big_move(_make_signal(state="MT", status="passed", stance="unfavorable"))
        # Large state but only proposed = NOT big (hasn't landed)
        assert not is_big_move(_make_signal(state="CA", status="proposed", stance="unfavorable"))
        # Large state, passed, but neutral = NOT big
        assert not is_big_move(_make_signal(state="CA", status="passed", stance="neutral"))


def test_is_big_move_favorable_carveout_any_state() -> None:
    from artemis.screentime.reporting import is_big_move

    with (
        patch("artemis.config.settings.screentime_bigmove_states", "CA,TX"),
        patch("artemis.config.settings.screentime_bigmove_statuses", "passed,amended"),
        patch("artemis.config.settings.screentime_bigmove_favorable_alert", True),
    ):
        # Favorable carve-out in a SMALL state still fires (any state)
        assert is_big_move(_make_signal(state="TN", status="passed", stance="favorable"))

    # With favorable alerting disabled, the favorable carve-out does NOT fire
    with (
        patch("artemis.config.settings.screentime_bigmove_states", "CA,TX"),
        patch("artemis.config.settings.screentime_bigmove_statuses", "passed,amended"),
        patch("artemis.config.settings.screentime_bigmove_favorable_alert", False),
    ):
        assert not is_big_move(_make_signal(state="TN", status="passed", stance="favorable"))


# ─────────────────────────────────────────────────────────────────────────────
# T6: big-move alert posts + marks reported (excluded from next digest)
# ─────────────────────────────────────────────────────────────────────────────


async def test_big_move_alert_posts_and_blocks_digest(db_session: AsyncSession) -> None:
    from artemis.screentime.models import ScreentimeSignal
    from artemis.screentime.reporting import maybe_alert_big_move, post_screentime_digest

    sid = await _insert_signal(
        db_session,
        state="TX",
        title="TX passed blanket restriction",
        status="passed",
        stance="unfavorable",
        source_url="https://leginfo.example/TX/HB1",
    )
    await db_session.commit()
    signal = await db_session.get(ScreentimeSignal, sid)

    posted, mock_post = _fake_post_message_calls()
    p_resolve, p_client, p_llm = _patch_slack(mock_post)
    with (
        patch("artemis.config.settings.screentime_report_channel", "C_POLICY_WATCH"),
        patch("artemis.config.settings.screentime_bigmove_states", "CA,TX,FL"),
        patch("artemis.config.settings.screentime_bigmove_statuses", "passed,amended"),
        patch("artemis.config.settings.screentime_bigmove_favorable_alert", True),
        p_resolve,
        p_client,
        p_llm,
    ):
        alerted = await maybe_alert_big_move(db_session, signal)
        await db_session.commit()

        assert alerted is True
        assert len(posted) == 1
        assert "https://leginfo.example/TX/HB1" in posted[0]["text"]

        # The same signal must NOT reappear in the digest (single report)
        count = await post_screentime_digest(db_session)
        await db_session.commit()

    assert count == 0, "Already-alerted signal must be excluded from the digest"
    assert len(posted) == 1, "No extra post from the digest"


# ─────────────────────────────────────────────────────────────────────────────
# T7: minor signal → no alert
# ─────────────────────────────────────────────────────────────────────────────


async def test_minor_signal_no_alert(db_session: AsyncSession) -> None:
    from artemis.screentime.models import ScreentimeSignal
    from artemis.screentime.reporting import maybe_alert_big_move

    sid = await _insert_signal(
        db_session,
        state="MT",  # small state
        title="MT proposed minor bill",
        status="proposed",
        stance="unfavorable",
    )
    await db_session.commit()
    signal = await db_session.get(ScreentimeSignal, sid)

    posted, mock_post = _fake_post_message_calls()
    p_resolve, p_client, p_llm = _patch_slack(mock_post)
    with (
        patch("artemis.config.settings.screentime_report_channel", "C_POLICY_WATCH"),
        patch("artemis.config.settings.screentime_bigmove_states", "CA,TX,FL"),
        patch("artemis.config.settings.screentime_bigmove_statuses", "passed,amended"),
        patch("artemis.config.settings.screentime_bigmove_favorable_alert", True),
        p_resolve,
        p_client,
        p_llm,
    ):
        alerted = await maybe_alert_big_move(db_session, signal)
        await db_session.commit()

    assert alerted is False
    assert posted == []


# ─────────────────────────────────────────────────────────────────────────────
# T8: big-move alert dormant when channel unset
# ─────────────────────────────────────────────────────────────────────────────


async def test_big_move_alert_dormant_when_channel_unset(db_session: AsyncSession) -> None:
    from artemis.screentime.models import ScreentimeSignal
    from artemis.screentime.reporting import maybe_alert_big_move

    sid = await _insert_signal(db_session, state="CA", title="CA passed blanket restriction")
    await db_session.commit()
    signal = await db_session.get(ScreentimeSignal, sid)

    posted, mock_post = _fake_post_message_calls()
    p_resolve, p_client, p_llm = _patch_slack(mock_post)
    with patch("artemis.config.settings.screentime_report_channel", ""), p_resolve, p_client, p_llm:
        alerted = await maybe_alert_big_move(db_session, signal)

    assert alerted is False
    assert posted == []
