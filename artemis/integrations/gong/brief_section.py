"""The districts talking about Amira, for the daily market-signals brief.

Market signals is a pulse: what is going on with Amira, AI in schools, screen
time in schools. Topic-shaped, not audience-shaped. Two district-level things
belong in it anyway, because both are the market talking about us: **a customer
having a problem**, and **a district saying something good**.

The second half is the one nothing currently surfaces. `Amira Solving Problems`
fires on 83% of calls at Chino Valley and 67% at Pinellas, and outside those
calls nobody hears it. Those are case-study leads sitting in call metadata.

**Notification, never content.** This reports that a district's calls are
unusually concerned or unusually positive, and how often a tracker fired. It
never reports what anyone said, because it never reads it: trackers are counts
and the client cannot fetch a transcript.

**District scope, never rep.** Aggregated by account. CLAUDE.md rule 4.
"""

from __future__ import annotations

import logging

from artemis.integrations.gong.baseline import AccountDeviation
from artemis.integrations.gong.snapshots import (
    TrendVerdict,
    previous_readings,
    trend_for,
    write_snapshots,
)
from artemis.integrations.gong.survey import LOOKBACK_DAYS, run_survey

logger = logging.getLogger(__name__)

#: How many of each kind reach the brief. This ranks rather than alarms, so the
#: brief carries the top few and the rest stay queryable.
MAX_PER_KIND = 3


async def _trends(flagged: list[AccountDeviation]) -> dict[str, TrendVerdict]:
    """Each flagged district against its own past, or ``{}`` when that is unavailable.

    Runs in its own session rather than the composer's, deliberately. A failed
    statement poisons the surrounding transaction, and after this section returns
    the composer still has to resolve mentions and claim today's slot -- so a
    dead query here would take down the whole brief, which is exactly what the
    section contract exists to prevent.
    """
    from artemis.db import SessionLocal

    try:
        async with SessionLocal() as session:
            previous = await previous_readings(session)
    except Exception:
        logger.warning("gong trends: could not read prior readings", exc_info=True)
        return {}
    return {dev.account_name: trend_for(dev, previous.get(dev.account_name)) for dev in flagged}


async def _record(flagged: list[AccountDeviation]) -> None:
    """Store today's reading, so tomorrow's run has something to compare against.

    Same separate session, same reason. Idempotent through the content hash, so
    a second run on the same day with the same rates writes nothing.
    """
    from artemis.db import SessionLocal

    try:
        async with SessionLocal() as session:
            recorded = await write_snapshots(session, flagged)
            await session.commit()
        # "Recorded", not "wrote": a second run on the same day with the same
        # rates hits the content hash and inserts nothing, and a log line
        # claiming otherwise is the small version of a tool reporting work it
        # did not do.
        logger.info("gong snapshots: today's reading recorded for %d districts", recorded)
    except Exception:
        logger.warning("gong snapshots: could not store today's readings", exc_info=True)


async def build_gong_section(session: object = None) -> str | None:
    """Return the brief section, or None when there is nothing worth saying.

    Follows the section contract in `artemis.market_signals`: returns a body or
    None, and never raises into the composer -- one dead feed must not take down
    the brief.
    """
    from artemis.config import settings

    if not settings.gong_access_key or not settings.gong_access_key_secret:
        return None

    try:
        survey = await run_survey(days=LOOKBACK_DAYS)
        portfolio = survey.portfolio
        flagged = survey.flagged
        if not flagged:
            return None

        # Trends on the concern half only. That is where direction changes what
        # anyone does -- a district getting worse wants a call this week, while a
        # case-study lead is just as good whether enthusiasm rose or held.
        trends = await _trends(flagged)

        concern = sorted(
            (d for d in flagged if d.elevated_concern),
            key=lambda d: (-max(d.elevated_concern.values()), -d.calls_considered),
        )[:MAX_PER_KIND]
        positive = sorted(
            (d for d in flagged if d.elevated_advocacy),
            key=lambda d: (-max(d.elevated_advocacy.values()), -d.calls_considered),
        )[:MAX_PER_KIND]

        lines: list[str] = []
        if concern:
            lines.append("*Districts raising more than usual:*")
            for dev in concern:
                top = max(dev.elevated_concern.items(), key=lambda kv: kv[1])
                verdict = trends.get(dev.account_name)
                lines.append(
                    f"• {dev.account_name} — {top[0]} on {top[1]:.0%} of "
                    f"{dev.calls_considered} calls"
                    f"{verdict.brief_clause() if verdict else ''}"
                )
        if positive:
            if lines:
                lines.append("")
            lines.append("*Districts sounding positive (possible case studies):*")
            for dev in positive:
                top = max(dev.elevated_advocacy.items(), key=lambda kv: kv[1])
                lines.append(
                    f"• {dev.account_name} — {top[0]} on {top[1]:.0%} of "
                    f"{dev.calls_considered} calls"
                )

        await _record(flagged)

        lines.append("")
        lines.append(
            f"_Call trackers over {LOOKBACK_DAYS} days, compared to the rate across "
            f"all {portfolio.call_count} linked calls. These are counts of what a "
            "call touched on, never what anyone said._"
        )
        return "\n".join(lines)

    except Exception:
        # The contract: never raise into the composer.
        logger.warning("gong brief section failed (non-fatal)", exc_info=True)
        return None
