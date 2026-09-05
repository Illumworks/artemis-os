"""Bounded questions Callie may ask of the Salesforce pipeline.

**Why a fixed menu and not free SOQL.** Handing an agent a query language is the
fabrication vector: it will happily invent a filter, get a number, and report it
with the same confidence as a real one. Every question here is written in advance
by a human, so a wrong answer is a bug we can find rather than a sentence nobody
can trace.

Three rules hold across all of them.

**Counts come from Salesforce, never from arithmetic in a prompt.** Each figure
below is a `COUNT()` or `SUM()` the database computed.

**Every answer carries the filter that produced it.** A number without its window
and thresholds is a number that can be honestly repeated and still be wrong --
"we win 44%" means nothing without "on deals over $10k, closed in the last two
years, excluding a bulk cleanup".

**A known distortion travels with the data that contains it.** January 2026 holds
38 lost deals worth $46.2M against a monthly baseline of 1-12. They were 314 to
1,182 days old when closed: a cleanup of dead inventory, not defeats. Any caller
reading loss figures is told this in the same breath, because the raw total reads
as a catastrophic quarter and is not one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Deal-size bands for win-rate analysis. Chosen because the pattern breaks at
#: $10k: below it we win 80%, above it 44%, and a single blended number hides
#: the entire finding.
SIZE_BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 10_000, "under $10k"),
    (10_000, 50_000, "$10k-50k"),
    (50_000, 250_000, "$50k-250k"),
    (250_000, 1_000_000, "$250k-1M"),
    (1_000_000, 999_999_999, "$1M+"),
)

#: Repeated verbatim wherever loss figures appear.
JAN_2026_CAVEAT = (
    "Caveat: January 2026 contains 38 lost deals worth $46.2M against a monthly "
    "baseline of 1-12. Those opportunities were 314-1,182 days old when closed -- "
    "a bulk cleanup of dead inventory, not competitive losses. Figures marked "
    "'excluding Jan 2026' have it removed; raw totals do not."
)

#: Said when Salesforce cannot be reached. Never a zero, never an empty list.
UNAVAILABLE = (
    "Salesforce could not be reached, so I have no figures for this. "
    "This is NOT a report of zero -- say the data is unavailable and do not "
    "state or estimate any pipeline number."
)

_JAN_EXCLUSION = " AND (CloseDate < 2026-01-01 OR CloseDate > 2026-01-31)"


class PipelineUnavailableError(Exception):
    """Salesforce did not answer. Distinct from 'the answer is zero'."""


@dataclass
class Answer:
    """One question's result: the numbers, and the filter that produced them."""

    question: str
    filter_description: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def render(self) -> str:
        out = [f"{self.question}", f"Scope: {self.filter_description}", ""]
        for row in self.rows:
            out.append("  " + "  ".join(f"{k}={v}" for k, v in row.items()))
        for caveat in self.caveats:
            out += ["", caveat]
        return "\n".join(out)


async def _count(client: Any, soql: str) -> int:
    rows = await client.query(soql)
    return int(rows[0]["n"]) if rows else 0


async def win_rate_by_size(client: Any, *, days: int = 730) -> Answer:
    """Win rate per deal-size band. The headline rate is carried by small deals."""
    rows: list[dict[str, Any]] = []
    for low, high, label in SIZE_BANDS:
        base = (
            f"IsClosed=true AND CloseDate=LAST_N_DAYS:{days} "
            f"AND Amount>={low} AND Amount<{high}{_JAN_EXCLUSION}"
        )
        won = await _count(
            client, f"SELECT COUNT(Id) n FROM Opportunity WHERE {base} AND IsWon=true"
        )
        lost = await _count(
            client, f"SELECT COUNT(Id) n FROM Opportunity WHERE {base} AND IsWon=false"
        )
        total = won + lost
        rows.append(
            {
                "band": label,
                "won": won,
                "lost": lost,
                # Integer division, not a rounded float: an exact ratio invites
                # being quoted to a precision the sample does not support.
                "win_rate": f"{100 * won // total}%" if total else "no closed deals",
            }
        )
    return Answer(
        question="Win rate by deal size",
        filter_description=(
            f"Opportunities closed in the last {days} days, excluding Jan 2026. "
            "Won and lost counted from Salesforce COUNT()."
        ),
        rows=rows,
        caveats=[JAN_2026_CAVEAT],
    )


async def open_pipeline_by_stage(client: Any, *, limit: int = 20) -> Answer:
    """Every open opportunity, grouped by stage."""
    rows = await client.query(
        "SELECT StageName, COUNT(Id) n, SUM(Amount) amt FROM Opportunity "
        f"WHERE IsClosed=false GROUP BY StageName ORDER BY COUNT(Id) DESC LIMIT {int(limit)}"
    )
    return Answer(
        question="Open pipeline by stage",
        filter_description="All opportunities where IsClosed=false, right now.",
        rows=[
            {
                "stage": r.get("StageName"),
                "count": r.get("n"),
                "amount": f"${(r.get('amt') or 0):,.0f}",
            }
            for r in rows
        ],
        caveats=[
            "Renewal stages dominate this list by volume; read new-business "
            "stages separately rather than as a share of the whole."
        ],
    )


async def big_deals_without_contacts(client: Any, *, min_amount: int = 250_000) -> Answer:
    """Large open deals with nobody recorded against them."""
    total = await _count(
        client, f"SELECT COUNT(Id) n FROM Opportunity WHERE IsClosed=false AND Amount>={min_amount}"
    )
    none_attached = await _count(
        client,
        f"SELECT COUNT(Id) n FROM Opportunity WHERE IsClosed=false AND Amount>={min_amount} "
        "AND Id NOT IN (SELECT OpportunityId FROM OpportunityContactRole)",
    )
    return Answer(
        question=f"Open deals over ${min_amount:,} with no contact attached",
        filter_description=(
            f"Open opportunities with Amount >= {min_amount}, counted against "
            "OpportunityContactRole."
        ),
        rows=[{"open_deals": total, "with_no_contact": none_attached}],
        caveats=[
            "This measures CRM hygiene, not deal quality. 77% of WON deals also "
            "have no contact attached, versus 63% of lost ones, so a missing "
            "contact does not predict a loss and must not be described as one."
        ],
    )


async def loss_reasons(client: Any, *, days: int = 730, limit: int = 12) -> Answer:
    """Why deals were lost, from Salesforce's own field.

    **This function previously reported that no such field existed.** Four
    conventional names were probed -- Loss_Reason__c, Reason_Lost__c,
    Closed_Lost_Reason__c, Loss_Reason_Detail__c -- all absent, and absence of
    those four was recorded as absence of the capability. The field is called
    `Reason__c`, it is populated on roughly 28,600 closed-lost opportunities, and
    it was the single richest thing available while we were telling people it did
    not exist.

    A probe that tests four guesses and reports "NONE" is not a finding. It is
    four guesses.
    """
    rows = await client.query(
        "SELECT Reason__c v, COUNT(Id) n FROM Opportunity "
        f"WHERE IsClosed = true AND IsWon = false AND CloseDate = LAST_N_DAYS:{int(days)} "
        f"GROUP BY Reason__c ORDER BY COUNT(Id) DESC LIMIT {int(limit)}"
    )
    return Answer(
        question="Why we lose deals",
        filter_description=(
            f"Opportunity.Reason__c on deals closed lost in the last {days} days. "
            "Blank on a substantial share of rows, so read the counts as 'of those "
            "with a reason recorded'."
        ),
        rows=[{"reason": r.get("v") or "(not recorded)", "deals": r.get("n")} for r in rows],
        caveats=[
            "'Merged with another Opp' is a bookkeeping outcome, not a loss. Exclude "
            "it before describing why deals are lost.",
            "The field's describe advertises fewer values than the data contains: "
            "several high-volume reasons are marked inactive but still populated. "
            "Read the values from the DATA, never from the picklist definition.",
            JAN_2026_CAVEAT,
        ],
    )


async def stalled_deals(client: Any, *, days: int = 90, limit: int = 15) -> Answer:
    """Open opportunities nobody has touched. Named deals, not just a count."""
    from artemis.marketing.pipeline_hygiene import find_stalled_deals

    deals = await find_stalled_deals(client, days=days, limit=limit)
    return Answer(
        question=f"Open deals with no activity in {days} days",
        filter_description=(
            f"Open opportunities whose LastModifiedDate is older than {days} days, "
            "richest first, test and demo accounts excluded."
        ),
        rows=[
            {
                "account": d.account_name,
                "stage": d.stage,
                "amount": f"${d.amount:,.0f}" if d.amount else "no amount",
                "untouched": f"{d.days_since_touch}d",
                "owner": d.owner,
            }
            for d in deals
        ],
        caveats=[
            "LastModifiedDate means the RECORD has not changed, not that nobody "
            "has spoken to them. Task and Event are not readable on this "
            "connection, so real activity is invisible to us -- do not say a rep "
            "has neglected an account."
        ],
    )


async def deals_missing_contacts(client: Any, *, limit: int = 15) -> Answer:
    """Late-stage open deals with nobody recorded against them, named."""
    from artemis.marketing.pipeline_hygiene import LATE_STAGES, find_deals_without_contacts

    deals = await find_deals_without_contacts(client, limit=limit)
    return Answer(
        question="Late-stage open deals with no contact attached",
        filter_description=(
            "Open opportunities at " + ", ".join(LATE_STAGES) + ", with zero "
            "OpportunityContactRole rows. Auto-generated renewals excluded: they are "
            "empty because a machine created them."
        ),
        rows=[
            {
                "account": d.account_name,
                "stage": d.stage,
                "amount": f"${d.amount:,.0f}" if d.amount else "no amount",
                "closes": d.close_date or "no date",
                "owner": d.owner,
            }
            for d in deals
        ],
        caveats=[
            "CRM hygiene, not deal health. 77% of WON deals also have no contact "
            "attached against 63% of lost ones, so do not present this as deals at risk."
        ],
    )


async def closing_soon(client: Any, *, days: int = 30, limit: int = 20) -> Answer:
    """Open deals with a close date inside the window."""
    rows = await client.query(
        "SELECT Name, StageName, Amount, CloseDate, Owner.Name, Account.Name "
        "FROM Opportunity WHERE IsClosed=false "
        f"AND CloseDate = NEXT_N_DAYS:{int(days)} "
        f"ORDER BY Amount DESC NULLS LAST LIMIT {int(limit)}"
    )
    return Answer(
        question=f"Open deals closing in the next {days} days",
        filter_description=(
            f"Open opportunities with CloseDate inside the next {days} days, richest first."
        ),
        rows=[
            {
                "account": (r.get("Account") or {}).get("Name") or "(no account)",
                "stage": r.get("StageName"),
                "amount": f"${(r.get('Amount') or 0):,.0f}",
                "closes": r.get("CloseDate"),
                "owner": (r.get("Owner") or {}).get("Name") or "(unassigned)",
            }
            for r in rows
        ],
        caveats=[
            "CloseDate is a forecast a rep entered, not a commitment. A date in "
            "the past on an open deal means the forecast slipped, not that it closed."
        ],
    )


#: Returned when the agent judges that no prepared question fits what was asked.
#:
#: The enum on the tool schema pushes the model to pick a valid value, so without
#: an explicit way out it answers the NEAREST question instead of the one asked --
#: and a real number under the wrong framing is worse than a refusal, because it
#: looks like an answer.
NOT_COVERED = (
    "None of the prepared pipeline questions answers that. Say so plainly, name "
    "what you CAN answer, and do not substitute a different figure. Available: "
    "win rate by deal size, open pipeline by stage, stalled deals, late-stage "
    "deals with no contact attached, deals closing soon, and whether Salesforce "
    "records loss reasons (it does not)."
)
