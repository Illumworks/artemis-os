"""Tests for Callie's ON-DEMAND Screen-Time & AI-policy report tool.

Distinct from ``tests/test_screentime_callie_report.py`` (Brief 2's auto-digest
to #policy-watch, which stays untouched here). Coverage:

  T1  build_report/format_report_text compose a national summary + notable
      moves + stance/status counts + the Amira carve-out angle, all sourced
  T2  get_screentime_report is registered for callie only (not other agents)
  T3  the report tool never writes: screentime_signals/state_stance row
      counts are unchanged after a report call, and no Slack client is ever
      constructed
  T4  a "not relevant" reaction WITH a reason down-weights that signal-shape
      via the SAME callie_push engagement ledger, and a subsequent report
      deprioritizes matching (non-big) notable moves
  T5  a "not relevant" reaction with NO reason is refused: nothing recorded,
      weights unchanged (mirrors callie_push's reason-less-reject rule)
  T6  a silent ignore (no call at all) leaves weights untouched
  T7  the tool-registry wrapper functions (_get_screentime_report /
      _record_screentime_feedback) work end-to-end through SessionLocal
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
import artemis.memory.models  # noqa: F401 — register memory models
import artemis.screentime.models  # noqa: F401 — register screentime models
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── DB wiring (own, dedicated test DB — avoids cross-agent contention) ──────

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test_screentime_report",
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
    "raw_inputs, screentime_signals, screentime_state_stance RESTART IDENTITY CASCADE"
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


async def _insert_signal(
    session: AsyncSession,
    *,
    state: str,
    title: str,
    status: str = "passed",
    stance: str = "unfavorable",
    is_real_move: bool = True,
    source_url: str | None = "https://legislature.example/bill/1",
    amira_angle: str | None = None,
    district_name: str | None = None,
    content_hash: str | None = None,
) -> int:
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


async def _table_count(session: AsyncSession, table: str) -> int:
    result = await session.execute(text(f"SELECT count(*) FROM {table}"))
    return int(result.scalar_one())


# ─────────────────────────────────────────────────────────────────────────────
# T1: report composition — national summary + notable moves + sources + angle
# ─────────────────────────────────────────────────────────────────────────────


async def test_report_composes_national_summary_and_notable_moves(
    db_session: AsyncSession,
) -> None:
    from artemis.screentime.callie_report import build_report, format_report_text

    await _insert_signal(
        db_session,
        state="CA",
        title="CA AB-123 blanket restriction",
        status="passed",
        stance="unfavorable",
        source_url="https://leginfo.example/CA/AB123",
    )
    await _insert_signal(
        db_session,
        state="TN",
        title="TN evidence-based carve-out",
        status="passed",
        stance="favorable",
        source_url="https://leginfo.example/TN/SB456",
        amira_angle="Carve-out for evidence-based tools — Amira qualifies.",
    )
    await _insert_signal(
        db_session,
        state="OH",
        title="OH press chatter",
        is_real_move=False,  # excluded from the report entirely
        content_hash="hash::OH::chatter",
    )
    await db_session.commit()

    data = await build_report(db_session)

    assert data["total_real_moves"] == 2, "Only real moves counted (chatter excluded)"
    assert data["states_covered"] == 2
    assert data["stance_counts"].get("favorable") == 1
    assert data["stance_counts"].get("unfavorable") == 1
    assert data["status_counts"].get("passed") == 2

    notable_titles = {m.title for m in data["notable_moves"]}
    assert "CA AB-123 blanket restriction" in notable_titles
    assert "TN evidence-based carve-out" in notable_titles
    assert "OH press chatter" not in notable_titles

    assert data["amira_carveout_angles"] == [
        "Carve-out for evidence-based tools — Amira qualifies."
    ]

    text_report = format_report_text(data)
    # Sourced — every notable move must carry its actual URL, not a bare headline.
    assert "https://leginfo.example/CA/AB123" in text_report
    assert "https://leginfo.example/TN/SB456" in text_report
    assert "2 real move(s) across 2 state(s)" in text_report
    assert "Amira carve-out angle" in text_report


async def test_report_notable_limit_respected(db_session: AsyncSession) -> None:
    from artemis.screentime.callie_report import build_report

    for i in range(5):
        await _insert_signal(
            db_session,
            state=f"S{i}",
            title=f"Move {i}",
            status="proposed",
            stance="neutral",
            content_hash=f"hash::S{i}::{i}",
        )
    await db_session.commit()

    data = await build_report(db_session, notable_limit=3)
    assert len(data["notable_moves"]) == 3
    assert data["total_real_moves"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# T2: registered for callie only
# ─────────────────────────────────────────────────────────────────────────────


def test_tool_registered_for_callie_only() -> None:
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    callie_registry = build_authorized_tool_registry(
        {"marketing-os", "signal-queue"}, agent_id="callie"
    )
    assert "get_screentime_report" in callie_registry
    assert "record_screentime_feedback" in callie_registry
    assert callie_registry.is_auto_invoke("get_screentime_report"), "read-only tool must auto-invoke"

    for other_agent in ("artemis", "kai", "ares"):
        other_registry = build_authorized_tool_registry(
            {"marketing-os", "signal-queue"}, agent_id=other_agent
        )
        assert "get_screentime_report" not in other_registry, f"{other_agent} must not see the tool"
        assert "record_screentime_feedback" not in other_registry


# ─────────────────────────────────────────────────────────────────────────────
# T3: read-only — no row mutation, no Slack client construction
# ─────────────────────────────────────────────────────────────────────────────


async def test_report_tool_never_writes_or_posts(db_session: AsyncSession, monkeypatch) -> None:
    from artemis.floating_artemis.tools.screentime_tools import _get_screentime_report

    await _insert_signal(db_session, state="CA", title="CA blanket restriction")
    await db_session.commit()

    signals_before = await _table_count(db_session, "screentime_signals")
    rollup_before = await _table_count(db_session, "screentime_state_stance")

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("get_screentime_report must never touch Slack")

    monkeypatch.setattr("artemis.integrations.slack.client.SlackClient", _boom)

    result = await _get_screentime_report({})
    assert "CA blanket restriction" in result

    async with artemis.db.SessionLocal() as verify_session:
        signals_after = await _table_count(verify_session, "screentime_signals")
        rollup_after = await _table_count(verify_session, "screentime_state_stance")

    assert signals_after == signals_before
    assert rollup_after == rollup_before


# ─────────────────────────────────────────────────────────────────────────────
# T4: deny-with-reason down-weights via the SAME callie_push ledger
# ─────────────────────────────────────────────────────────────────────────────


async def test_deny_with_reason_downweights_via_callie_push_ledger(
    db_session: AsyncSession,
) -> None:
    from artemis.marketing.callie_push import get_engagement_weights
    from artemis.screentime.callie_report import build_report, record_feedback

    noisy_id = await _insert_signal(
        db_session,
        state="MT",
        title="MT routine guidance memo",
        status="guidance",
        stance="neutral",
        content_hash="hash::MT::guidance",
    )
    await db_session.commit()

    weights_before = await get_engagement_weights(db_session)
    assert "code:STATE_MT" not in weights_before

    msg = await record_feedback(
        db_session,
        signal_id=noisy_id,
        not_relevant=True,
        reason="Not useful — this is routine agency guidance, not a real policy move.",
    )
    assert "down-weighted" in msg

    weights_after = await get_engagement_weights(db_session)
    # Same ledger callie_push reads — a rejected observation drives the
    # Laplace-smoothed weight below the neutral 0.5.
    assert weights_after["code:STATE_MT"] < 0.5
    assert weights_after["code:STATUS_GUIDANCE"] < 0.5
    assert weights_after["code:STANCE_NEUTRAL"] < 0.5

    # A second, similar (non-big-move) MT/guidance/neutral signal should now be
    # deprioritized out of "notable moves" by the learned suppression.
    await _insert_signal(
        db_session,
        state="MT",
        title="MT another routine guidance memo",
        status="guidance",
        stance="neutral",
        content_hash="hash::MT::guidance2",
    )
    await db_session.commit()

    data = await build_report(db_session)
    notable_states = [m.state for m in data["notable_moves"]]
    assert "MT" not in notable_states, "Learned-noisy shape should be suppressed from notable moves"
    # But it's still counted in the national totals — suppression only affects
    # the notable-moves list, not the counts.
    assert data["total_real_moves"] == 2


async def test_deny_without_reason_is_refused_and_records_nothing(
    db_session: AsyncSession,
) -> None:
    from artemis.marketing.callie_push import get_engagement_weights
    from artemis.screentime.callie_report import record_feedback

    sid = await _insert_signal(
        db_session,
        state="WY",
        title="WY minor item",
        status="proposed",
        stance="neutral",
        content_hash="hash::WY::minor",
    )
    await db_session.commit()

    msg = await record_feedback(db_session, signal_id=sid, not_relevant=True, reason="   ")
    assert "needs a reason" in msg

    weights = await get_engagement_weights(db_session)
    assert "code:STATE_WY" not in weights, "reason-less reject must not be recorded"


# ─────────────────────────────────────────────────────────────────────────────
# T5: silent ignore — no call at all — leaves weights untouched
# ─────────────────────────────────────────────────────────────────────────────


async def test_silent_ignore_changes_nothing(db_session: AsyncSession) -> None:
    from artemis.marketing.callie_push import get_engagement_weights

    await _insert_signal(
        db_session,
        state="NV",
        title="NV item nobody reacted to",
        content_hash="hash::NV::ignored",
    )
    await db_session.commit()

    # No record_feedback call whatsoever — simulating a teammate who reads the
    # report and just moves on.
    weights = await get_engagement_weights(db_session)
    assert "code:STATE_NV" not in weights


# ─────────────────────────────────────────────────────────────────────────────
# T6: tool-registry wrapper end-to-end (I/O plumbing through SessionLocal)
# ─────────────────────────────────────────────────────────────────────────────


async def test_record_screentime_feedback_tool_end_to_end(db_session: AsyncSession) -> None:
    from artemis.floating_artemis.tools.screentime_tools import (
        _get_screentime_report,
        _record_screentime_feedback,
    )

    sid = await _insert_signal(
        db_session,
        state="FL",
        title="FL passed blanket restriction",
        status="passed",
        stance="unfavorable",
        source_url="https://leginfo.example/FL/HB1",
    )
    await db_session.commit()

    report = await _get_screentime_report({"notable_limit": 5})
    assert "FL passed blanket restriction" in report
    assert "https://leginfo.example/FL/HB1" in report

    denied = await _record_screentime_feedback(
        {"signal_id": sid, "not_relevant": True, "reason": "duplicate of a bill we already track"}
    )
    assert "down-weighted" in denied

    missing = await _record_screentime_feedback({"signal_id": 999999, "not_relevant": True, "reason": "x"})
    assert "No screen-time signal found" in missing

    no_reason = await _record_screentime_feedback({"signal_id": sid, "not_relevant": True})
    assert "needs a reason" in no_reason
