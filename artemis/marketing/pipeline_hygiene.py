"""Two lists Josh can work, read from Salesforce's own history.

Both answer questions the CRM already holds and nobody asks it:

**Stalled deals.** An open opportunity nobody has touched in months. Salesforce
will not say so on its own -- nothing turns red, the deal simply sits in a stage
forever and quietly inflates the forecast. There are 138 of these at 90 days
today, and the top of the list is state-level: Iowa DOE at $2.0M and New Mexico
DOE at $1.0M, both untouched since mid-April.

**Late-stage deals with nobody attached.** Not "single-threaded" -- that was the
intended feature and the data does not support it. 5,209 of 6,535 open
opportunities (80%) have no ``OpportunityContactRole`` at all, overwhelmingly the
auto-generated renewals, so a contact-role COUNT cannot measure buying-committee
depth; the field is empty far too often to mean anything. What survives is the
narrower and more alarming slice: deals at Proposal Sent, Commit, Active
Discussion or At Risk with **zero** contacts recorded. Roughly 320 of those --
someone is negotiating and the CRM cannot say with whom.

Read-only. Every call is a SOQL SELECT through ``SalesforceClient.query``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Stages where "no contact attached" is a real finding rather than an artefact.
#: The auto-generated renewal stages are excluded deliberately: 3,208 of them have
#: no contact because a machine created them, which says nothing about the deal.
LATE_STAGES: tuple[str, ...] = (
    "Proposal Sent",
    "Active Discussion",
    "Commit",
    "At Risk",
    "SAL and Demo",
    "Early Start",
    "Pilot",
)

#: Test and demo accounts, matched on a WHOLE WORD.
#:
#: SOQL ``LIKE '%TEST%'`` matches "Adams Protestant Reformed Christian School" --
#: Pro-TEST-ant. A list handed to sales with a real school libelled as a test
#: record is worse than no list, so the match is anchored to word boundaries and
#: applied in Python, where a real regex is available.
_TEST_NAME_RE = re.compile(r"(?<![a-z])(test|demo|dummy|sample|do not use|not real)(?![a-z])", re.I)


def is_test_account(name: str | None) -> bool:
    """True for obvious test/demo records, without eating real school names."""
    return bool(_TEST_NAME_RE.search(name or ""))


@dataclass
class StalledDeal:
    opportunity_id: str
    name: str
    account_name: str
    stage: str
    amount: float | None
    owner: str
    days_since_touch: int
    close_date: str | None

    def as_line(self) -> str:
        money = f"${self.amount:,.0f}" if self.amount else "no amount"
        return (
            f"{self.account_name} — {self.stage} — {money} — "
            f"untouched {self.days_since_touch}d — {self.owner}"
        )


@dataclass
class UnattachedDeal:
    opportunity_id: str
    name: str
    account_name: str
    stage: str
    amount: float | None
    owner: str
    close_date: str | None

    def as_line(self) -> str:
        money = f"${self.amount:,.0f}" if self.amount else "no amount"
        return f"{self.account_name} — {self.stage} — {money} — closes {self.close_date} — {self.owner}"


def _account_name(row: dict[str, Any]) -> str:
    account = row.get("Account") or {}
    return str(account.get("Name") or "(no account)")


def _owner_name(row: dict[str, Any]) -> str:
    owner = row.get("Owner") or {}
    return str(owner.get("Name") or "(unassigned)")


def _days_since(iso_timestamp: str, *, now: Any = None) -> int:
    from datetime import UTC, datetime

    now = now or datetime.now(UTC)
    touched = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return int((now - touched).days)


async def find_stalled_deals(client: Any, *, days: int = 90, limit: int = 50) -> list[StalledDeal]:
    """Open opportunities with no modification in `days`, richest first.

    `LastModifiedDate` is the honest available proxy. It is not "nobody has
    spoken to them" -- Task and Event are not readable on this connection, so
    real activity is invisible -- it is "the record itself has not changed",
    which for a deal in an active stage is already a problem worth a look.
    """
    soql = (
        "SELECT Id, Name, StageName, Amount, CloseDate, LastModifiedDate, "
        "Owner.Name, Account.Name FROM Opportunity "
        f"WHERE IsClosed = false AND LastModifiedDate < LAST_N_DAYS:{int(days)} "
        f"ORDER BY Amount DESC NULLS LAST LIMIT {int(limit) * 2}"
    )
    rows = await client.query(soql)

    deals: list[StalledDeal] = []
    for row in rows:
        account_name = _account_name(row)
        if is_test_account(account_name) or is_test_account(row.get("Name")):
            continue
        deals.append(
            StalledDeal(
                opportunity_id=str(row["Id"]),
                name=str(row.get("Name") or ""),
                account_name=account_name,
                stage=str(row.get("StageName") or ""),
                amount=row.get("Amount"),
                owner=_owner_name(row),
                days_since_touch=_days_since(str(row["LastModifiedDate"])),
                close_date=(row.get("CloseDate") or None),
            )
        )
        if len(deals) >= limit:
            break
    return deals


async def find_deals_without_contacts(client: Any, *, limit: int = 50) -> list[UnattachedDeal]:
    """Late-stage open opportunities with no contact role attached at all.

    Restricted to `LATE_STAGES` on purpose. Across all open stages this returns
    5,209 rows and is noise; the auto-generated renewals dominate it and their
    emptiness is an artefact of how they were created, not a signal.
    """
    stages = ", ".join(f"'{s}'" for s in LATE_STAGES)
    soql = (
        "SELECT Id, Name, StageName, Amount, CloseDate, Owner.Name, Account.Name "
        "FROM Opportunity WHERE IsClosed = false "
        f"AND StageName IN ({stages}) "
        "AND Id NOT IN (SELECT OpportunityId FROM OpportunityContactRole) "
        f"ORDER BY Amount DESC NULLS LAST LIMIT {int(limit) * 2}"
    )
    rows = await client.query(soql)

    deals: list[UnattachedDeal] = []
    for row in rows:
        account_name = _account_name(row)
        if is_test_account(account_name) or is_test_account(row.get("Name")):
            continue
        deals.append(
            UnattachedDeal(
                opportunity_id=str(row["Id"]),
                name=str(row.get("Name") or ""),
                account_name=account_name,
                stage=str(row.get("StageName") or ""),
                amount=row.get("Amount"),
                owner=_owner_name(row),
                close_date=(row.get("CloseDate") or None),
            )
        )
        if len(deals) >= limit:
            break
    return deals
