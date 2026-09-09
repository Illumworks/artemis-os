"""One pass over the call window, shared by everything that needs the picture.

**Why this is not two paging loops.** The brief section reads 120 days of calls
to rank districts; a tool answering "what is going on with Pinellas" needs the
same corpus, because `recent_calls_for_account` filters client-side and pages the
whole thing regardless. Two copies of that loop means two copies of the cursor
trap it documents -- the cursor belongs at the TOP LEVEL of the body, and inside
`filter` it is accepted, ignored, and returns page one forever with no error.
That bug cost a day and reported "no calls" for a district with five.

**Truncation is reported, not swallowed.** Running out of pages and running out
of data look identical in the payload. A partial survey that presents itself as
complete is how a district gets described as quiet because we stopped reading.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from artemis.integrations.gong.baseline import (
    AccountDeviation,
    PortfolioRates,
    account_deviation,
    portfolio_rates,
)
from artemis.integrations.gong.client import CallContext, GongMetadataClient, _to_context

logger = logging.getLogger(__name__)

#: Window for both the portfolio norm and each district's rate. Long enough that
#: an account can accumulate the three calls it needs to be judged at all, short
#: enough that a district which settled down months ago is not still flagged.
LOOKBACK_DAYS = 120

#: Pages of 100 calls. 120 days is roughly eight.
_MAX_PAGES = 10


@dataclass(frozen=True)
class Survey:
    """Every account in the window, judged against the portfolio."""

    portfolio: PortfolioRates
    deviations: list[AccountDeviation] = field(default_factory=list)
    #: True when the pager gave up before the data ran out. Anything derived from
    #: a truncated survey is a floor, not a count.
    truncated: bool = False

    @property
    def flagged(self) -> list[AccountDeviation]:
        return [d for d in self.deviations if d.has_signal]

    def for_account(self, name: str) -> AccountDeviation | None:
        """The one district this name means, or ``None`` if that is not decidable.

        ``None`` covers two different situations and the caller has to tell them
        apart with ``candidates``: nothing matched, or several did.

        **It does not pick a winner among several.** An earlier version returned
        the match with the most calls, and "Valley" confidently returned Walnut
        Valley Unified while three other Valley districts sat in the same window
        — an answer about a district nobody asked about, indistinguishable from a
        correct one. A unique match in an incomplete lookup is not a correct
        match; abstaining costs one clarifying question, and guessing costs
        someone acting on another district's calls.
        """
        wanted = name.strip().lower()
        if not wanted:
            return None
        hits = [d for d in self.deviations if wanted in d.account_name.lower()]
        # An exact name always wins: "Madera Unified School District" should not
        # be ambiguous merely because a longer name contains it.
        for dev in hits:
            if dev.account_name.lower() == wanted:
                return dev
        return hits[0] if len(hits) == 1 else None

    def candidates(self, name: str) -> list[str]:
        """Every account the name could have meant. For saying so out loud."""
        wanted = name.strip().lower()
        return sorted(d.account_name for d in self.deviations if wanted in d.account_name.lower())


async def run_survey(*, days: int = LOOKBACK_DAYS) -> Survey:
    """One live pass over the window. Raises if Gong cannot be reached.

    Callers that must not fail -- the brief section -- catch. Callers that would
    otherwise report a confident "nothing found" should let it propagate and say
    Gong was unreachable, which is a different answer.
    """
    from artemis.config import settings

    client = GongMetadataClient(settings.gong_access_key, settings.gong_access_key_secret)
    frm = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    to = datetime.now(UTC).strftime("%Y-%m-%dT23:59:59Z")

    calls: list[CallContext] = []
    cursor: str | None = None
    truncated = True
    for _ in range(_MAX_PAGES):
        body: dict[str, object] = {
            "filter": {"fromDateTime": frm, "toDateTime": to},
            "contentSelector": {
                "context": "Extended",
                "exposedFields": {"parties": True, "content": {"trackers": True}},
            },
        }
        if cursor:
            # TOP LEVEL. Inside `filter` this is ignored silently.
            body["cursor"] = cursor
        payload = await client._post("/v2/calls/extensive", body)
        calls.extend(_to_context(raw) for raw in payload.get("calls", []))
        cursor = (payload.get("records") or {}).get("cursor")
        if not cursor:
            truncated = False
            break
    if truncated:
        logger.warning(
            "gong survey: stopped after %d pages with more data waiting; results are partial",
            _MAX_PAGES,
        )

    portfolio = portfolio_rates(calls)
    by_account: dict[str, list[CallContext]] = defaultdict(list)
    for call in calls:
        if call.account_name:
            by_account[call.account_name].append(call)

    return Survey(
        portfolio=portfolio,
        deviations=[
            account_deviation(name, account_calls, portfolio)
            for name, account_calls in by_account.items()
        ],
        truncated=truncated,
    )
