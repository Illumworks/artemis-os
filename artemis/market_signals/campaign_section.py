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

# What kind of signal is worth a slot, most-buying-intent first.
#
# Ranking by urgency and district size alone produced six superintendent hires
# and dropped IXL's Missouri DOE approval off the end. The reason is volume:
# LEADER_TRANSITION_FORMAL was 86 of ~200 qualified signals over three days,
# while PROCUREMENT_LITERACY_RFP was 2 and VENDOR_APPROVED_LIST was 7. Sorting
# by recency inside a tier hands the whole brief to the commonest story type,
# and the rarest codes are the ones with money attached.
_CODE_PRIORITY: dict[str, int] = {
    "PROCUREMENT_LITERACY_RFP": 0,  # someone is buying, with a deadline
    "VENDOR_APPROVED_LIST": 1,  # a competitor just got approved somewhere
    "TX_HB1416_WAIVER": 2,
    "POLICY_LIT_MANDATE": 3,  # a legal requirement creates a shopping list
    "FUNDING_LITERACY_GRANT": 4,  # money arriving
    "DISTRICT_PROFICIENCY_GAP": 5,
    "DISTRICT_MTSS_STRAIN": 5,
    "DISTRICT_STRATEGIC_LITERACY": 6,
    "DISTRICT_DLL_EXPANSION": 6,
    "LEADER_TRANSITION_FORMAL": 7,  # useful, but 86 of them is not a brief
}
_DEFAULT_CODE_PRIORITY = 6

# At most this many of any single reason code. Without a cap, the highest-volume
# code fills the section even after priority ordering — and "three superintendents
# changed" is one fact, not three.
_MAX_PER_CODE = 2


def _primary_code(reason_codes: Any) -> str:
    """The first reason code on a signal, or ``""``.

    ``reason_codes`` is JSONB shaped like ``[{"code": "POLICY_LIT_MANDATE"}]``.
    Tolerates a bare list of strings and anything unexpected, because this only
    drives presentation order — a signal with an unreadable code should sort mid
    pack, not vanish or crash the brief.
    """
    if isinstance(reason_codes, str):
        return reason_codes.strip()
    if isinstance(reason_codes, list) and reason_codes:
        first = reason_codes[0]
        if isinstance(first, dict):
            return str(first.get("code") or "").strip()
        return str(first).strip()
    return ""


def _code_priority(reason_codes: Any) -> int:
    return _CODE_PRIORITY.get(_primary_code(reason_codes), _DEFAULT_CODE_PRIORITY)


def _tier_rank(row: dict[str, Any]) -> int:
    """D1 first, then D2/D3, then everything else.

    D1-D3 is Josh's own definition of a target account ("the d1-d3 accounts",
    2026-08-12). Unresolved districts and D4 sort last rather than being dropped:
    a state-level mandate has no district at all and is often the most valuable
    line in the brief.
    """
    return {"D1": 0, "D2": 1, "D3": 2}.get((row.get("district_tier") or "").strip(), 3)


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
                           COALESCE(d.name, '') AS district_name,
                           COALESCE(d.tier, '') AS district_tier,
                           s.reason_codes
                    FROM signal_queue s
                    LEFT JOIN districts d
                           ON d.id::text = COALESCE(s.resolved_district_id::text,
                                                    s.district_id::text)
                    WHERE s.signal_status = 'qualified'
                      AND s.created_at > now() - interval '26 hours'
                      AND s.urgency_tier = ANY(:tiers)
                    ORDER BY CASE s.urgency_tier WHEN 'hot' THEN 0 ELSE 1 END,
                             -- District size decides ties, and almost everything
                             -- ties: 6 of 6 signals in the first real window were
                             -- 'hot', so ordering by urgency alone collapsed to
                             -- newest-first. That put Whitnall (D4, 2,364
                             -- students) above Chicago (D1, 324,130) and pushed
                             -- IXL's Missouri DOE approval -- an actual
                             -- competitor event -- to last place.
                             -- D1-D3 is Josh's own definition of a target
                             -- account ("the d1-d3 accounts", 2026-08-12), so
                             -- unresolved and D4 sort last rather than being
                             -- excluded: a big story in a district we cannot
                             -- resolve is still worth a line.
                             CASE COALESCE(d.tier, '')
                                  WHEN 'D1' THEN 0
                                  WHEN 'D2' THEN 1
                                  WHEN 'D3' THEN 2
                                  ELSE 3
                             END,
                             s.created_at DESC
                    LIMIT :cap
                    """
                ),
                {"tiers": list(_TIERS), "cap": _MAX_SIGNALS * 8},
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
    #
    # Selection happens here rather than in SQL because it needs two passes over a
    # wider pool: order by what kind of signal it is, then cap each kind so the
    # commonest one cannot fill the brief.
    candidates = [dict(row._mapping) for row in rows]
    candidates.sort(key=lambda m: (_code_priority(m.get("reason_codes")), _tier_rank(m)))

    seen_per_code: dict[str, int] = {}
    lines: list[str] = []
    for m in candidates:
        if len(lines) >= _MAX_SIGNALS:
            break
        code = _primary_code(m.get("reason_codes"))
        if seen_per_code.get(code, 0) >= _MAX_PER_CODE:
            continue

        headline = (m.get("headline") or "").strip()
        if not headline:
            continue
        seen_per_code[code] = seen_per_code.get(code, 0) + 1
        where = (m.get("district_name") or "").strip() or (m.get("state") or "").strip()
        tier = (m.get("urgency_tier") or "").strip()
        family = (m.get("campaign_family") or "").strip()
        url = (m.get("source_url") or "").strip()

        # Show the tier when we know it: it is how Josh sizes an account, and it
        # tells the reader at a glance whether a headline is about a system of
        # 300,000 students or of 2,000.
        dtier = (m.get("district_tier") or "").strip()
        where_label = f"{where} ({dtier})" if where and dtier else where
        label = " · ".join(
            p for p in (where_label, family.replace("_", " ") if family else "") if p
        )
        # A WORD, not an emoji. lint_agent_text strips emoji as house style
        # (verified 2026-08-12), so a 🔥 marker would silently vanish from every
        # posted brief and hot signals would be indistinguishable from standard
        # ones -- with the code looking perfectly correct.
        prefix = "*Hot* " if tier == "hot" else ""
        headline_text = f"<{url}|{headline}>" if url else headline
        lines.append(f"- {prefix}{headline_text}" + (f" [{label}]" if label else ""))

    if not lines:
        return None

    total = len(lines)
    body = "\n".join(lines)
    if total >= _MAX_SIGNALS:
        body += f"\n_Top {_MAX_SIGNALS} by buying intent, the rest are in #campaign-signals._"
    return body


__all__ = ["build_campaign_section"]
