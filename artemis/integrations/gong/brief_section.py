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
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from artemis.integrations.gong.baseline import (
    account_deviation,
    portfolio_rates,
)
from artemis.integrations.gong.client import CallContext, GongMetadataClient, _to_context

logger = logging.getLogger(__name__)

#: Window for both the portfolio norm and each district's rate. Long enough that
#: an account can accumulate the three calls it needs to be judged at all, short
#: enough that a district which settled down months ago is not still flagged.
LOOKBACK_DAYS = 120

#: How many of each kind reach the brief. This ranks rather than alarms, so the
#: brief carries the top few and the rest stay queryable.
MAX_PER_KIND = 3

#: Pages of 100 calls. 120 days is roughly eight.
_MAX_PAGES = 10


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
        client = GongMetadataClient(settings.gong_access_key, settings.gong_access_key_secret)
        frm = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
        to = datetime.now(UTC).strftime("%Y-%m-%dT23:59:59Z")

        calls: list[CallContext] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            body: dict[str, object] = {
                "filter": {"fromDateTime": frm, "toDateTime": to},
                "contentSelector": {
                    "context": "Extended",
                    "exposedFields": {"parties": True, "content": {"trackers": True}},
                },
            }
            if cursor:
                body["cursor"] = cursor
            payload = await client._post("/v2/calls/extensive", body)
            calls.extend(_to_context(raw) for raw in payload.get("calls", []))
            cursor = (payload.get("records") or {}).get("cursor")
            if not cursor:
                break

        portfolio = portfolio_rates(calls)
        by_account: dict[str, list[CallContext]] = defaultdict(list)
        for call in calls:
            if call.account_name:
                by_account[call.account_name].append(call)

        deviations = [
            account_deviation(name, account_calls, portfolio)
            for name, account_calls in by_account.items()
        ]
        flagged = [d for d in deviations if d.has_signal]
        if not flagged:
            return None

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
                lines.append(
                    f"• {dev.account_name} — {top[0]} on {top[1]:.0%} of "
                    f"{dev.calls_considered} calls"
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
