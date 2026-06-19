"""DB-backed tests: store/dedupe, purge isolation, rollup, retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from artemis.screentime.classifier import Classification
from artemis.screentime.filters import CandidateSignal
from artemis.screentime.models import (
    STANCE_FAVORABLE,
    STANCE_NEUTRAL,
    STANCE_UNFAVORABLE,
)
from artemis.screentime.repository import (
    expire_old_signals,
    purge_screentime_data,
    recompute_state_stance,
    signal_count,
    state_stance_count,
    store_signal,
    upsert_signal_classification,
)
from artemis.screentime.stance_config import DEFAULT_STANCE_RULES

pytestmark = pytest.mark.asyncio


def _cand(title: str, state: str = "TN", stance_url: str = "http://u") -> CandidateSignal:
    return CandidateSignal(
        state=state,
        title=title,
        summary="summary",
        source_type="legislative",
        source_url=stance_url,
        status="passed",
    )


def _cls(stance: str) -> Classification:
    return Classification(stance=stance, amira_angle="angle", served_by="codex")


async def test_store_signal_dedupes_on_content_hash(db_session):
    c = _cand("HB 1 limits screen time")
    assert await store_signal(db_session, c, _cls(STANCE_FAVORABLE)) is True
    # Same content_hash → second store is a no-op.
    assert await store_signal(db_session, c, _cls(STANCE_FAVORABLE)) is False
    await db_session.flush()
    assert await signal_count(db_session) == 1


async def test_recompute_state_stance_rollup(db_session):
    await store_signal(db_session, _cand("a", "TN", "http://a"), _cls(STANCE_FAVORABLE))
    await store_signal(db_session, _cand("b", "TN", "http://b"), _cls(STANCE_FAVORABLE))
    await store_signal(db_session, _cand("c", "TN", "http://c"), _cls(STANCE_UNFAVORABLE))
    await store_signal(db_session, _cand("d", "CA", "http://d"), _cls(STANCE_UNFAVORABLE))
    await db_session.flush()

    written = await recompute_state_stance(db_session, DEFAULT_STANCE_RULES)
    assert written == 2

    rows = dict(
        (r[0], (r[1], r[2]))
        for r in (
            await db_session.execute(
                text("SELECT state, stance, signal_count FROM screentime_state_stance")
            )
        ).all()
    )
    assert rows["TN"][0] == STANCE_FAVORABLE  # 2 fav > 1 unf
    assert rows["TN"][1] == 3
    assert rows["CA"][0] == STANCE_UNFAVORABLE


async def test_config_flip_on_rerun(db_session):
    """Re-applying a new classification flips a stored signal's stance + rollup."""
    c = _cand("HB 7 restricts screen time", "TX", "http://tx7")
    await store_signal(db_session, c, _cls(STANCE_FAVORABLE))
    await db_session.flush()
    await recompute_state_stance(db_session, DEFAULT_STANCE_RULES)

    before = (
        await db_session.execute(
            text("SELECT stance FROM screentime_state_stance WHERE state='TX'")
        )
    ).scalar_one()
    assert before == STANCE_FAVORABLE

    # Tuned config re-run: same signal reclassified unfavorable.
    await upsert_signal_classification(db_session, c.content_hash, _cls(STANCE_UNFAVORABLE))
    await db_session.flush()
    await recompute_state_stance(db_session, DEFAULT_STANCE_RULES)

    after = (
        await db_session.execute(
            text("SELECT stance FROM screentime_state_stance WHERE state='TX'")
        )
    ).scalar_one()
    assert after == STANCE_UNFAVORABLE


async def test_expire_old_signals(db_session):
    await store_signal(db_session, _cand("recent", "TN", "http://r"), _cls(STANCE_NEUTRAL))
    await store_signal(db_session, _cand("old", "TN", "http://o"), _cls(STANCE_NEUTRAL))
    await db_session.flush()
    # Backdate the "old" one beyond retention.
    await db_session.execute(
        text(
            "UPDATE screentime_signals SET discovered_at = :ts WHERE title='old'"
        ),
        {"ts": datetime.now(UTC) - timedelta(days=90)},
    )
    await db_session.flush()

    deleted = await expire_old_signals(db_session, retention_days=60)
    assert deleted == 1
    await db_session.flush()
    assert await signal_count(db_session) == 1


async def test_expire_zero_keeps_all(db_session):
    await store_signal(db_session, _cand("keep", "TN", "http://k"), _cls(STANCE_NEUTRAL))
    await db_session.flush()
    assert await expire_old_signals(db_session, retention_days=0) == 0


async def test_purge_truncates_only_screentime_tables(db_session):
    # Seed all three screentime tables.
    await store_signal(db_session, _cand("x", "TN", "http://x"), _cls(STANCE_FAVORABLE))
    await db_session.flush()
    await recompute_state_stance(db_session, DEFAULT_STANCE_RULES)
    await db_session.execute(
        text(
            "INSERT INTO screentime_stance_config (name, rules) VALUES ('default', '{}'::jsonb) "
            "ON CONFLICT (name) DO NOTHING"
        )
    )
    await db_session.flush()
    assert await signal_count(db_session) == 1
    assert await state_stance_count(db_session) == 1

    # Count a non-screentime table before purge — must be unchanged after.
    pipelines_before = (
        await db_session.execute(text("SELECT count(*) FROM pipelines"))
    ).scalar_one()

    result = await purge_screentime_data(db_session)
    await db_session.flush()

    assert "screentime_signals" in result["truncated"]
    assert await signal_count(db_session) == 0
    assert await state_stance_count(db_session) == 0
    cfg = (
        await db_session.execute(text("SELECT count(*) FROM screentime_stance_config"))
    ).scalar_one()
    assert cfg == 0

    pipelines_after = (
        await db_session.execute(text("SELECT count(*) FROM pipelines"))
    ).scalar_one()
    assert pipelines_after == pipelines_before  # untouched
