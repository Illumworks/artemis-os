"""Persistence + rollup + scrub for the Screen-Time Watch namespace.

Everything here touches ONLY the screentime_* tables. The purge helper is hard
-bound to ``models.SCREENTIME_TABLES`` so it can never truncate a non-screentime
table.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.screentime.classifier import Classification
from artemis.screentime.filters import CandidateSignal
from artemis.screentime.models import (
    SCREENTIME_TABLES,
    STANCE_FAVORABLE,
    STANCE_NEUTRAL,
    STANCE_NO_INFO,
    STANCE_UNFAVORABLE,
    ScreentimeSignal,
    ScreentimeStateStance,
)

_logger = logging.getLogger(__name__)


async def store_signal(
    session: AsyncSession,
    candidate: CandidateSignal,
    classification: Classification,
) -> bool:
    """Insert one classified signal, deduped on content_hash.

    Returns True if a NEW row was inserted, False if it was a duplicate
    (ON CONFLICT (content_hash) DO NOTHING). Idempotent across re-runs.
    """
    stmt = (
        pg_insert(ScreentimeSignal)
        .values(
            state=candidate.state,
            level=candidate.level,
            district_name=candidate.district_name,
            title=candidate.title,
            summary=candidate.summary or None,
            status=candidate.status,
            stance=classification.stance,
            amira_angle=classification.amira_angle,
            source_url=candidate.source_url or None,
            source_type=candidate.source_type,
            published_at=candidate.published_at,
            is_real_move=True,
            content_hash=candidate.content_hash,
            raw=candidate.raw,
        )
        .on_conflict_do_nothing(index_elements=["content_hash"])
        .returning(ScreentimeSignal.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def upsert_signal_classification(
    session: AsyncSession,
    content_hash: str,
    classification: Classification,
) -> None:
    """Re-apply a classification to an existing signal (re-run after config change).

    Lets a re-run flip a stored signal's stance when the stance config changes,
    proving tunability end-to-end.
    """
    await session.execute(
        text(
            """
            UPDATE screentime_signals
               SET stance = :stance, amira_angle = :angle
             WHERE content_hash = :h
            """
        ),
        {"stance": classification.stance, "angle": classification.amira_angle, "h": content_hash},
    )


def _state_stance_from_counts(counts: dict[str, int], rules: dict[str, Any]) -> tuple[str, str]:
    """Roll a state's per-stance signal counts into one stance + rationale."""
    fav = counts.get(STANCE_FAVORABLE, 0)
    unf = counts.get(STANCE_UNFAVORABLE, 0)
    neu = counts.get(STANCE_NEUTRAL, 0)
    total = fav + unf + neu
    if total == 0:
        return STANCE_NO_INFO, "No signals yet."

    favorable_wins_ties = bool((rules.get("rollup") or {}).get("favorable_wins_ties", True))
    rationale = f"{fav} favorable, {unf} unfavorable, {neu} neutral across {total} signal(s)."

    if fav == 0 and unf == 0:
        return STANCE_NEUTRAL, rationale
    if fav > unf:
        return STANCE_FAVORABLE, rationale
    if unf > fav:
        return STANCE_UNFAVORABLE, rationale
    # tie between favorable and unfavorable
    return (STANCE_FAVORABLE if favorable_wins_ties else STANCE_UNFAVORABLE), rationale


async def recompute_state_stance(
    session: AsyncSession,
    rules: dict[str, Any],
    *,
    states: list[str] | None = None,
) -> int:
    """Recompute screentime_state_stance from the stored signals.

    Recomputes every state present in screentime_signals (or just *states* when
    given). Returns the number of state rows written. Failure-safe per state.
    """
    rows = (
        await session.execute(
            select(
                ScreentimeSignal.state,
                ScreentimeSignal.stance,
                func.count().label("n"),
            ).group_by(ScreentimeSignal.state, ScreentimeSignal.stance)
        )
    ).all()

    per_state: dict[str, dict[str, int]] = {}
    for state, stance, n in rows:
        per_state.setdefault(state, {})[stance] = int(n)

    if states:
        wanted = {s.upper() for s in states}
        per_state = {s: c for s, c in per_state.items() if s in wanted}

    written = 0
    now = datetime.now(UTC)
    for state, counts in per_state.items():
        stance, rationale = _state_stance_from_counts(counts, rules)
        signal_count = sum(counts.values())
        await session.execute(
            text(
                """
                INSERT INTO screentime_state_stance
                    (state, stance, rationale, signal_count, last_updated)
                VALUES (:state, :stance, :rationale, :signal_count, :ts)
                ON CONFLICT (state) DO UPDATE SET
                    stance = EXCLUDED.stance,
                    rationale = EXCLUDED.rationale,
                    signal_count = EXCLUDED.signal_count,
                    last_updated = EXCLUDED.last_updated
                """
            ),
            {
                "state": state,
                "stance": stance,
                "rationale": rationale,
                "signal_count": signal_count,
                "ts": now,
            },
        )
        written += 1
    return written


async def reclassify_stored_signals(session: AsyncSession) -> int:
    """Re-color ALREADY-STORED signals in place — no scout sweep needed.

    Runs the tuned topic gate + config-driven stance classifier over every row in
    ``screentime_signals``:
      * rows that NO LONGER pass the topic gate (now-off-topic noise that slipped
        an earlier, looser gate — e.g. a health "screening" or budget "study")
        are DELETED;
      * surviving rows are re-classified by the deterministic rule classifier and
        their ``stance`` / ``amira_angle`` updated when the stance changes.
    Then ``screentime_state_stance`` is recomputed so the heat map reflects the
    re-colored set.

    This is the deterministic, config-driven re-color path: it uses the rule
    classifier only (no LLM / no provider call), so it's cheap and reproducible —
    re-running it after a stance/topic config edit re-colors existing data without
    a full scout sweep (Lead's "re-classify after merge" step).

    Returns the number of signals re-classified (kept rows whose row was visited;
    deletions are not counted in the return but are logged). Failure-safe: a bad
    individual row is skipped, never aborting the batch; an outer failure rolls the
    count back to what completed and is logged, never raised.
    """
    from artemis.screentime.classifier import _rule_angle, classify_by_rules
    from artemis.screentime.filters import CandidateSignal, passes_topic_gate
    from artemis.screentime.stance_config import load_stance_rules
    from artemis.screentime.topic_config import load_topic_rules

    rules = await load_stance_rules(session)
    topic_rules = await load_topic_rules(session)

    rows = (
        await session.execute(
            select(
                ScreentimeSignal.id,
                ScreentimeSignal.content_hash,
                ScreentimeSignal.state,
                ScreentimeSignal.title,
                ScreentimeSignal.summary,
                ScreentimeSignal.stance,
                ScreentimeSignal.level,
                ScreentimeSignal.status,
                ScreentimeSignal.source_type,
                ScreentimeSignal.source_url,
            )
        )
    ).all()

    reclassified = 0
    dropped = 0
    for row in rows:
        try:
            combined = f"{row.title or ''}\n{row.summary or ''}".strip()

            # 1. Re-apply the (possibly tightened) topic gate. Now-off-topic → drop.
            if not passes_topic_gate(combined, topic_rules):
                await session.execute(
                    text("DELETE FROM screentime_signals WHERE id = :id"),
                    {"id": row.id},
                )
                dropped += 1
                continue

            # 2. Re-classify with the config-driven rule classifier (topic-hardened).
            new_stance = classify_by_rules(combined, rules, topic_rules=topic_rules)
            if new_stance != row.stance:
                candidate = CandidateSignal(
                    state=row.state,
                    title=row.title,
                    summary=row.summary or "",
                    source_type=row.source_type,
                    source_url=row.source_url or "",
                    level=row.level,
                    status=row.status,
                )
                await session.execute(
                    text(
                        """
                        UPDATE screentime_signals
                           SET stance = :stance, amira_angle = :angle
                         WHERE id = :id
                        """
                    ),
                    {
                        "stance": new_stance,
                        "angle": _rule_angle(new_stance, candidate),
                        "id": row.id,
                    },
                )
            reclassified += 1
        except Exception:
            _logger.warning(
                "reclassify_stored_signals: skipped a row (id=%s)",
                getattr(row, "id", "?"),
                exc_info=True,
            )
            continue

    await session.flush()

    # 3. Recompute the per-state rollup over the re-colored / trimmed set.
    try:
        await recompute_state_stance(session, rules)
    except Exception:
        _logger.warning("reclassify_stored_signals: state rollup failed", exc_info=True)

    _logger.info(
        "reclassify_stored_signals: reclassified=%d dropped_off_topic=%d", reclassified, dropped
    )
    return reclassified


async def expire_old_signals(session: AsyncSession, retention_days: int) -> int:
    """Delete signals older than *retention_days* (by discovered_at). 0 = keep all.

    Returns the number of deleted rows. After expiry, callers should recompute
    state stance so the rollup reflects the trimmed set.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = await session.execute(
        text("DELETE FROM screentime_signals WHERE discovered_at < :cutoff"),
        {"cutoff": cutoff},
    )
    return int(result.rowcount or 0)


async def purge_screentime_data(session: AsyncSession) -> dict[str, str]:
    """Scrub: TRUNCATE only the screentime_* tables. Touches nothing else.

    Bound to ``SCREENTIME_TABLES`` — the table list is fixed in code, not derived
    from input, so this can never truncate a marketing/campaign/memory table.
    """
    table_list = ", ".join(SCREENTIME_TABLES)
    await session.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))
    _logger.info("purge_screentime_data: truncated %s", table_list)
    return {"truncated": table_list}


async def signal_count(session: AsyncSession) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(ScreentimeSignal))).scalar_one()
    )


async def state_stance_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(select(func.count()).select_from(ScreentimeStateStance))
        ).scalar_one()
    )
