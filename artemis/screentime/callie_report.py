"""Callie's ON-DEMAND Screen-Time & AI-policy report — read-only, ask-and-answer.

This is a DIFFERENT consumer of ``screentime_signals`` than
``artemis/screentime/reporting.py`` (Brief 2's auto-digest to #policy-watch).
That module pushes a weekly digest / immediate big-move alert to a channel on
its own schedule -- it stays exactly as-is, dormant unless
``screentime_report_channel`` is set, and this module never touches it.

This module instead backs a Slack **tool call**: when a teammate (owner or
not -- e.g. Amy, the COO) asks Callie something like "what's the latest on
screen-time / AI policy", she calls ``get_screentime_report`` (registered in
``artemis/floating_artemis/tools/screentime_tools.py``, Callie-only) and
answers in her own voice using the data this module returns. There is no
scheduler, no cron, no automatic Slack post here -- it only runs when invoked.

Read-only
=========
Every function in this module only SELECTs from ``screentime_signals`` /
``screentime_state_stance``. ``record_feedback`` is the one exception -- it
is an explicit, teammate-directed write to the *engagement ledger* (a memory
observation), not to any screentime table, and only fires on an explicit
"not relevant" reaction with a reason (see below).

Reaction-learning hook (reuse, not a parallel system)
======================================================
Callie already learns from explicit reactions to marketing signals via
``artemis.marketing.callie_push.record_signal_engagement`` /
``get_engagement_weights`` (see that module's docstring: "intentionally
simple so it can be mirrored ... without diverging into two different
systems"). We mirror it here rather than build a second ledger:

  * An explicit "not relevant, because <reason>" on a screen-time item calls
    ``record_feedback(..., not_relevant=True, reason=...)``, which calls
    ``record_signal_engagement(outcome="rejected", ...)`` with the SAME
    function marketing uses -- just keyed on this signal's own attributes
    (state / status / stance) under ``campaign_family="screentime"`` so it
    can never collide with a marketing campaign_family value.
  * A silent ignore calls nothing at all -- Callie's tool description tells
    her never to call ``record_screentime_feedback`` without an explicit
    reaction, exactly like the marketing rule ("silent ignores ... must not
    be recorded").
  * ``get_engagement_weights`` (also reused, unmodified) is read back by
    ``build_report`` to deprioritize signal-shapes a teammate has taught us
    are noise -- big moves (see ``reporting.is_big_move``) are never
    suppressed, only routine notable-move ranking is affected.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.screentime.models import (
    STANCE_FAVORABLE,
    STANCE_UNFAVORABLE,
    ScreentimeSignal,
    ScreentimeStateStance,
)
from artemis.screentime.reporting import is_big_move

_log = logging.getLogger(__name__)

# campaign_family bucket used for screentime feedback observations -- kept
# distinct from any marketing campaign_family value so weights never mix
# screen-time signal-shape learning with campaign-signal learning.
_FEEDBACK_FAMILY = "screentime"

_NOTABLE_LIMIT_DEFAULT = 10

# Weight below which a NON-big-move is deprioritized out of "notable moves".
# On callie_push's Laplace-smoothed 0..1 scale, 0.5 is neutral; below this
# floor means teammates have explicitly told us this shape is not relevant
# more often than not.
_SUPPRESS_BELOW = 0.35


# ── Attribute keys (mirrors callie_push._engagement_obs_content's shape) ─────


def _reason_codes(signal: ScreentimeSignal) -> list[str]:
    """This signal's engagement-learning attribute codes: state/status/stance."""
    return [
        f"STATE_{(signal.state or '').strip().upper()}",
        f"STATUS_{(signal.status or '').strip().upper()}",
        f"STANCE_{(signal.stance or '').strip().upper()}",
    ]


def _move_weight(signal: ScreentimeSignal, weights: dict[str, float]) -> float:
    """Average learned weight across this signal's attribute keys.

    No recorded evidence for any of its attributes -> neutral 0.5 (never
    suppressed). Mirrors callie_push._signal_engagement_multiplier's "only
    present attributes count" rule.
    """
    keys = [f"code:{c}" for c in _reason_codes(signal)]
    present = [weights[k] for k in keys if k in weights]
    if not present:
        return 0.5
    return sum(present) / len(present)


# ── Read-only queries ─────────────────────────────────────────────────────────


async def _stance_counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(ScreentimeSignal.stance, func.count())
            .where(ScreentimeSignal.is_real_move.is_(True))
            .group_by(ScreentimeSignal.stance)
        )
    ).all()
    return {str(stance): int(n) for stance, n in rows}


async def _status_counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(ScreentimeSignal.status, func.count())
            .where(ScreentimeSignal.is_real_move.is_(True))
            .group_by(ScreentimeSignal.status)
        )
    ).all()
    return {str(status): int(n) for status, n in rows}


async def _state_rollup_counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(ScreentimeStateStance.stance, func.count()).group_by(
                ScreentimeStateStance.stance
            )
        )
    ).all()
    return {str(stance): int(n) for stance, n in rows}


async def _states_covered(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count(func.distinct(ScreentimeSignal.state))).where(
                    ScreentimeSignal.is_real_move.is_(True)
                )
            )
        ).scalar_one()
    )


async def _all_real_moves(session: AsyncSession) -> list[ScreentimeSignal]:
    return list(
        (
            await session.execute(
                select(ScreentimeSignal)
                .where(ScreentimeSignal.is_real_move.is_(True))
                .order_by(ScreentimeSignal.discovered_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def _notable_moves(
    session: AsyncSession, weights: dict[str, float], *, limit: int
) -> list[ScreentimeSignal]:
    """Big moves first (reuses reporting.is_big_move -- same bar Callie's
    auto-digest uses for "must react now"), then newest-first, with routine
    (non-big) moves the reaction-learning loop has taught us are noise for
    this audience dropped below the suppression floor.
    """
    moves = await _all_real_moves(session)
    big = [s for s in moves if is_big_move(s)]
    big_ids = {s.id for s in big}
    rest = [s for s in moves if s.id not in big_ids]
    kept_rest = [s for s in rest if _move_weight(s, weights) >= _SUPPRESS_BELOW]
    return (big + kept_rest)[:limit]


# ── Composition (deterministic -- Callie voices the final reply herself) ────


def _stance_emoji(stance: str) -> str:
    s = (stance or "").strip().lower()
    if s == STANCE_FAVORABLE:
        return "🟢"
    if s == STANCE_UNFAVORABLE:
        return "🔴"
    return "⚪"


def _source_link(signal: ScreentimeSignal) -> str:
    if signal.source_url:
        label = signal.title.strip() or "source"
        if len(label) > 80:
            label = label[:77] + "..."
        return f"<{signal.source_url}|{label}>"
    return signal.title.strip() or "(no source link)"


def _move_line(signal: ScreentimeSignal) -> str:
    where = signal.state + (f"/{signal.district_name}" if signal.district_name else "")
    angle = f" — {signal.amira_angle}" if signal.amira_angle else ""
    return (
        f"{_stance_emoji(signal.stance)} *{where}* ({signal.status}) "
        f"[id={signal.id}]: {_source_link(signal)}{angle}"
    )


def format_report_text(data: dict[str, Any]) -> str:
    """Deterministic, source-linked text Callie can present (or re-voice)."""
    lines: list[str] = []
    total = data["total_real_moves"]
    states = data["states_covered"]
    lines.append(
        f"*Screen-Time & AI-Policy Watch* — {total} real move(s) across {states} state(s)."
    )

    sc = data["stance_counts"]
    lines.append(
        "By stance: "
        f"{sc.get('favorable', 0)} favorable, "
        f"{sc.get('unfavorable', 0)} unfavorable, "
        f"{sc.get('neutral', 0)} neutral."
    )
    if data["status_counts"]:
        status_bits = ", ".join(f"{k}={v}" for k, v in sorted(data["status_counts"].items()))
        lines.append(f"By status: {status_bits}.")
    if data["state_rollup_counts"]:
        rollup_bits = ", ".join(f"{k}={v}" for k, v in sorted(data["state_rollup_counts"].items()))
        lines.append(f"State stance rollup: {rollup_bits}.")

    notable = data["notable_moves"]
    if notable:
        lines.append("\n*Notable moves:*")
        for m in notable:
            lines.append(_move_line(m))
    else:
        lines.append("\nNo notable real moves on file yet.")

    angles = data["amira_carveout_angles"]
    if angles:
        lines.append("\n*Amira carve-out angle:*")
        for a in angles:
            lines.append(f"- {a}")

    return "\n".join(lines)


# ── Public entry points ──────────────────────────────────────────────────────


async def build_report(
    session: AsyncSession, *, notable_limit: int = _NOTABLE_LIMIT_DEFAULT
) -> dict[str, Any]:
    """Gather + compose the on-demand screen-time/AI-policy report. READ-ONLY.

    Never raises -- any query failure degrades to an empty/neutral section so a
    Slack ask never errors out; the caller (the tool wrapper) still wraps this
    for belt-and-suspenders safety.
    """
    from artemis.marketing.callie_push import get_engagement_weights

    stance_counts = await _stance_counts(session)
    status_counts = await _status_counts(session)
    state_rollup_counts = await _state_rollup_counts(session)
    states = await _states_covered(session)
    total = sum(stance_counts.values())

    try:
        weights = await get_engagement_weights(session)
    except Exception:
        _log.warning(
            "screentime_report: engagement weights unavailable, using neutral", exc_info=True
        )
        weights = {}

    notable = await _notable_moves(session, weights, limit=notable_limit)

    angles = sorted(
        {
            (s.amira_angle or "").strip()
            for s in notable
            if (s.stance or "").lower() == STANCE_FAVORABLE and s.amira_angle
        }
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_real_moves": total,
        "states_covered": states,
        "stance_counts": stance_counts,
        "status_counts": status_counts,
        "state_rollup_counts": state_rollup_counts,
        "notable_moves": notable,
        "amira_carveout_angles": angles,
    }


async def record_feedback(
    session: AsyncSession,
    *,
    signal_id: int,
    not_relevant: bool,
    reason: str | None,
) -> str:
    """Explicit reaction-learning hook -- reuses callie_push.record_signal_engagement
    verbatim (same function marketing signals use). Returns a short confirmation
    string for the tool to hand back to the model.

    Rule (mirrors callie_push exactly): a "not relevant" reaction with NO reason
    is refused (not recorded) -- only reasoned rejects teach the filter. Callers
    (Callie's tool contract) must never invoke this for a silent ignore at all.
    """
    from artemis.marketing.callie_push import record_signal_engagement

    signal = await session.get(ScreentimeSignal, signal_id)
    if signal is None:
        return f"No screen-time signal found with id={signal_id}; nothing recorded."

    if not_relevant:
        reason_text = (reason or "").strip()
        if not reason_text:
            return (
                "A 'not relevant' reaction needs a reason to teach the filter -- "
                "silent ignores are never recorded, by design. Nothing changed."
            )
        outcome = "rejected"
    else:
        outcome = "acted"

    await record_signal_engagement(
        session,
        signal_id=signal.id,
        outcome=outcome,
        reason_codes=_reason_codes(signal),
        campaign_family=_FEEDBACK_FAMILY,
        district_type=signal.level,
    )
    await session.commit()

    verb = "down-weighted" if outcome == "rejected" else "up-weighted"
    return (
        f"Got it -- {verb} {signal.state}/{signal.status}/{signal.stance} "
        f"screen-time moves like signal {signal.id} for future reports."
    )


__all__ = ["build_report", "format_report_text", "record_feedback"]
