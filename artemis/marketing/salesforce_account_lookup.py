"""Look up a district in Salesforce BY NAME, without needing a local record.

The suppression guard (``salesforce_suppression``) answers "may we email this
recipient?" and is keyed on a contact email. That is the right shape for the send
path and the wrong shape for the question demand gen actually asks: *is this
district a customer, and what is the opportunity history?* That is an ACCOUNT
question, and Salesforce can answer it directly.

Until 2026-08-28 it could not be asked. The tool resolved the district against
our own index first and then required contacts with email addresses, so a
district we had never met returned nothing — even when Salesforce held a
complete record. Houston ISD is the case in point: ``Customer_Status__c =
'Customer'`` sat in Salesforce the whole time, behind a lookup that demanded
local contacts we did not have.

**Read-only.** Every call is a SOQL SELECT through
``SalesforceClient.query``, which issues GET requests only.

**Abstains rather than guesses.** A name search returns candidates; attributing
one district's opportunity history to another is the error that matters here, so
several matches are reported as several, never resolved by picking the first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from artemis.config import settings
from artemis.marketing.salesforce_suppression import _soql_escape

logger = logging.getLogger(__name__)

# Salesforce caps SOQL LIKE scans; a district search that returns dozens is not
# a useful answer to a human anyway.
_MAX_CANDIDATES = 8


@dataclass
class AccountMatch:
    """One Salesforce Account, as much as one SOQL round trip can tell us."""

    account_id: str
    name: str
    customer_status: str | None = None
    open_opportunities: int = 0
    is_customer: bool = False

    def describe(self) -> str:
        bits = [self.name]
        if self.customer_status:
            bits.append(f"status: {self.customer_status}")
        elif self.is_customer:
            bits.append("customer")
        else:
            bits.append("no customer status set")
        if self.open_opportunities:
            bits.append(
                f"{self.open_opportunities} open opportunit"
                f"{'y' if self.open_opportunities == 1 else 'ies'}"
            )
        return " — ".join(bits)


@dataclass
class AccountLookup:
    """The outcome of a name search. Exactly one of these three is populated."""

    matched: AccountMatch | None = None
    candidates: list[AccountMatch] = field(default_factory=list)
    error: str = ""

    @property
    def found(self) -> bool:
        return self.matched is not None


def _is_customer(value: Any) -> bool:
    """Interpret the configured customer field.

    Mirrors the suppression guard deliberately: the field is a picklist on this
    org, and the truthy values are configured because the org is shared with
    another product — reading it as a plain boolean would misclassify thousands
    of accounts. See ``settings.salesforce_customer_field``.
    """
    truthy = {
        v.strip().lower()
        for v in (settings.salesforce_customer_truthy_values or "").split(",")
        if v.strip()
    }
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return False
    return text in truthy if truthy else text == "true"


async def find_accounts_by_name(client: Any, district_name: str) -> AccountLookup:
    """Search Salesforce Accounts by name.

    An EXACT name match wins outright even when a broader search would return
    more — "Jefferson County" should resolve to the account literally called
    that, not abstain because a dozen "Jefferson County <something>" accounts
    exist alongside it.
    """
    name = (district_name or "").strip()
    if not name:
        return AccountLookup(error="No district name given.")

    field_name = settings.salesforce_customer_field
    escaped = _soql_escape(name)

    try:
        records = await client.query(
            f"SELECT Id, Name, {field_name} FROM Account "
            f"WHERE Name LIKE '%{escaped}%' ORDER BY Name LIMIT {_MAX_CANDIDATES + 1}"
        )
    except Exception as exc:
        # Fail loud and specific. "Could not reach Salesforce" and "not a
        # customer" must never be confused for one another.
        logger.warning("salesforce account search failed for %r: %s", name, exc)
        return AccountLookup(
            error=f"Salesforce could not be reached ({type(exc).__name__}), so customer "
            "status is UNKNOWN for this district — not confirmed absent."
        )

    matches = [
        AccountMatch(
            account_id=str(r.get("Id") or ""),
            name=str(r.get("Name") or ""),
            customer_status=(str(r[field_name]) if r.get(field_name) else None),
            is_customer=_is_customer(r.get(field_name)),
        )
        for r in records
    ]
    if not matches:
        return AccountLookup()

    exact = [m for m in matches if m.name.strip().lower() == name.lower()]
    if len(exact) == 1:
        return AccountLookup(matched=exact[0])
    if len(matches) == 1:
        return AccountLookup(matched=matches[0])
    return AccountLookup(candidates=matches[:_MAX_CANDIDATES])


async def count_open_opportunities(client: Any, account_id: str) -> int:
    """Open opportunities on one account. Returns 0 on any failure.

    Zero-on-failure is safe HERE and nowhere else in this file: an opportunity
    count is colour, not a gate. The customer-status path above fails loud
    instead, because a wrong answer there decides whether we contact someone.
    """
    if not account_id:
        return 0
    try:
        rows = await client.query(
            "SELECT Id FROM Opportunity WHERE AccountId = "
            f"'{_soql_escape(account_id)}' AND IsClosed = false LIMIT 50"
        )
        return len(rows)
    except Exception:
        logger.debug("open-opportunity count failed for account %s", account_id, exc_info=True)
        return 0


async def lookup_district(client: Any, district_name: str) -> AccountLookup:
    """Full account lookup: find it, then enrich the single match."""
    result = await find_accounts_by_name(client, district_name)
    if result.matched is not None:
        result.matched.open_opportunities = await count_open_opportunities(
            client, result.matched.account_id
        )
    return result


# Fields that answer "is someone already working this person?" without any Gong
# API at all. Gong syncs onto the Contact record, and these were readable the
# whole time -- the conflict guardrail Josh asked for first never needed a new
# integration, only somebody to look.
_CONFLICT_FIELDS = (
    "Id",
    "Name",
    "Title",
    "Email",
    "LastActivityDate",
    "Gong__Actively_Being_in_a_Flow__c",
    "Gong__Current_Flow_Name__c",
    "Gong__Current_Flow_User_Name__c",
    "Gong__Added_to_Flow_Date__c",
)


@dataclass
class AccountContact:
    """One person at a district, with whatever we can say about who is on them."""

    contact_id: str
    name: str
    title: str = ""
    email: str = ""
    last_activity: str = ""
    in_active_flow: bool = False
    flow_name: str = ""
    flow_owner: str = ""
    flow_since: str = ""

    @property
    def conflicted(self) -> bool:
        """Whether someone else is actively working this person right now."""
        return self.in_active_flow

    def describe(self) -> str:
        bits = [self.name]
        if self.title:
            bits.append(self.title)
        if self.conflicted:
            who = self.flow_owner or "a seller"
            flow = f" ({self.flow_name})" if self.flow_name else ""
            bits.append(f"⚠ IN ACTIVE OUTREACH by {who}{flow} since {self.flow_since or 'unknown'}")
        elif self.last_activity:
            bits.append(f"last touched {self.last_activity}")
        else:
            bits.append("no recorded activity")
        return " — ".join(bits)


async def fetch_account_contacts(
    client: Any, account_id: str, *, limit: int = 25
) -> list[AccountContact]:
    """People at an account, and who is already working them.

    Returns [] on any failure rather than raising -- but a caller must not read
    an empty list as "nobody is being worked". Absence of evidence is not
    evidence of absence, and this is the check that stops marketing emailing
    into an active sales conversation.
    """
    if not account_id:
        return []
    try:
        rows = await client.query(
            f"SELECT {', '.join(_CONFLICT_FIELDS)} FROM Contact "
            f"WHERE AccountId = '{_soql_escape(account_id)}' "
            f"ORDER BY LastActivityDate DESC NULLS LAST LIMIT {int(limit)}"
        )
    except Exception:
        logger.warning("contact fetch failed for account %s", account_id, exc_info=True)
        return []

    people: list[AccountContact] = []
    for row in rows:
        people.append(
            AccountContact(
                contact_id=str(row.get("Id") or ""),
                name=str(row.get("Name") or "(unnamed)"),
                title=str(row.get("Title") or ""),
                email=str(row.get("Email") or ""),
                last_activity=str(row.get("LastActivityDate") or ""),
                in_active_flow=bool(row.get("Gong__Actively_Being_in_a_Flow__c")),
                flow_name=str(row.get("Gong__Current_Flow_Name__c") or ""),
                flow_owner=str(row.get("Gong__Current_Flow_User_Name__c") or ""),
                flow_since=str(row.get("Gong__Added_to_Flow_Date__c") or ""),
            )
        )
    return people
