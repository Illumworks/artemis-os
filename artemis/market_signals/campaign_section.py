"""The campaign-signals section of Callie's daily #market-signals brief.

Answers one question: *of everything that qualified since yesterday, what would
you actually want to know about?* Not "here is everything" — the individual cards
in `#campaign-signals` already do that, and drowning in them is the problem this
brief exists to solve.

Why this reads `signal_queue` broadly rather than a pipeline run
---------------------------------------------------------------
In the seven days to 2026-08-12, **1** qualified signal was attached to a
`marketing.main` run and **305** were written straight to the queue by the nine
scouts running on their own 24-hour cadence. A section built from a pipeline
run's own output would have been almost always empty. The pipeline is one
contributor to the queue, not the queue.

Idempotency lives in the composer
---------------------------------
Unlike the screentime section, this one marks nothing as reported: `signal_queue`
has no such column and does not need one, because the composer reserves a
once-per-day row in `morning_brief_deliveries` before building. A second run the
same day never reaches this code. The window below is therefore "since roughly
the last brief", not "since the last thing I marked".
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Six is a judgement, not a formula: enough that a real cluster shows up, few
# enough that the section stays readable in a Slack post people skim on a phone.
_MAX_SIGNALS = 6

# Hot first, then standard. 'enrichment' is deliberately excluded — it is
# background context by definition, and it was 11 of 189 qualified signals over
# two days, so including it would spend the cap on the least urgent tier.
_TIERS = ("hot", "standard")


async def build_campaign_section(session: AsyncSession) -> str | None:
    """Top campaign signals since yesterday, or ``None`` if there are none.

    Never raises — returns ``None`` on any failure, per the section contract in
    ``artemis.market_signals.__init__``. One feed's bad day must not cost the
    other two their place in the brief.
    """
    try:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT s.headline,
                           s.campaign_family,
                           s.urgency_tier,
                           s.state,
                           s.source_url,
                           COALESCE(d.name, '') AS district_name
                    FROM signal_queue s
                    LEFT JOIN districts d
                           ON d.id::text = COALESCE(s.resolved_district_id::text,
                                                    s.district_id::text)
                    WHERE s.signal_status = 'qualified'
                      AND s.created_at > now() - interval '26 hours'
                      AND s.urgency_tier = ANY(:tiers)
                    ORDER BY CASE s.urgency_tier WHEN 'hot' THEN 0 ELSE 1 END,
                             s.created_at DESC
                    LIMIT :cap
                    """
                ),
                {"tiers": list(_TIERS), "cap": _MAX_SIGNALS},
            )
        ).all()
    except Exception:
        logger.warning("market_signals: campaign section query failed", exc_info=True)
        return None

    if not rows:
        logger.info("market_signals: no qualified campaign signals in the window")
        return None

    # 26 hours, not 24: a brief that runs a few minutes late must not leave a gap
    # that nothing ever reports. Overlap repeats a signal at worst; a gap loses it.
    lines: list[str] = []
    for row in rows:
        m: dict[str, Any] = dict(row._mapping)
        headline = (m.get("headline") or "").strip()
        if not headline:
            continue
        where = (m.get("district_name") or "").strip() or (m.get("state") or "").strip()
        tier = (m.get("urgency_tier") or "").strip()
        family = (m.get("campaign_family") or "").strip()
        url = (m.get("source_url") or "").strip()

        label = " · ".join(p for p in (where, family.replace("_", " ") if family else "") if p)
        # A WORD, not an emoji. lint_agent_text strips emoji as house style
        # (verified 2026-08-12), so a 🔥 marker would silently vanish from every
        # posted brief and hot signals would be indistinguishable from standard
        # ones -- with the code looking perfectly correct.
        prefix = "*Hot* " if tier == "hot" else ""
        headline_text = f"<{url}|{headline}>" if url else headline
        lines.append(f"- {prefix}{headline_text}" + (f" ({label})" if label else ""))

    if not lines:
        return None

    total = len(lines)
    body = "\n".join(lines)
    if total >= _MAX_SIGNALS:
        body += f"\n_Top {_MAX_SIGNALS} — the rest are in #campaign-signals._"
    return body


__all__ = ["build_campaign_section"]
