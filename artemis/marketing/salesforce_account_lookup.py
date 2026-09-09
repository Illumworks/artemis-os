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


def _activity_phrase(last_activity: str) -> str:
    """Describe `LastActivityDate` truthfully, including when it has not happened yet.

    **Salesforce's `LastActivityDate` is a DUE date, not a completed one.** It is
    the most recent Event's `ActivityDate` or the most recently closed Task's, and
    an Event counts whether or not it has taken place. So a meeting booked for
    next March makes a contact look "last touched 2027-03-29".

    That is 233 contacts across 184 accounts (0.087% of those with a date). The
    rate understates it badly, because `fetch_account_contacts` sorts
    `LastActivityDate DESC` — future dates sort to the TOP, so on those 184
    accounts a rounding-error field problem is a 100% error on the first rows
    anyone reads. Asked about Pinellas, Callie was handed two of them as rows one
    and two, reported them as past contact, and called the list "warm".

    Filtering them out was the smaller change and would have been worse: it swaps
    one false statement ("no recorded activity") for another and discards a
    booked meeting, which is a thing demand gen actually wants to know. So say
    what it is.

    Evidence that these are calendar entries rather than typos: of the 233 future
    dates, 232 fall Monday-Friday and one on a Saturday. Typed-wrong years land
    uniformly across the week; roughly 67 weekend dates would be expected, which
    puts this about nine standard deviations from noise. Exactly two records are
    genuinely dirty (years 2763 and 2202), and the lone Saturday is one of them.

    Not confirmed by reading the source Activity: `Task`, `Event` and
    `OpenActivity` all return 400 for our run-as user (the same permission gap
    noted in `salesforce_suppression`). This is strong inference plus vendor
    documentation, not direct observation, and it should be revisited if that
    permission ever arrives.
    """
    from datetime import UTC, datetime

    try:
        when = datetime.strptime(last_activity[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        # Unparseable: hand it back verbatim rather than guessing at a tense.
        return f"last activity recorded as {last_activity}"

    if when.date() > datetime.now(UTC).date():
        return (
            f"a meeting is SCHEDULED for {last_activity[:10]} (not yet happened) — "
            "Salesforce cannot tell us when this person was last actually contacted"
        )
    return f"last touched {last_activity[:10]}"


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
            bits.append(_activity_phrase(self.last_activity))
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


#: Statuses that mean "this site is on a licence of some kind". `Child` is the
#: marker proper (5,624 accounts org-wide); the others appear on sites that were
#: set up directly rather than under a parent rollout.
_LICENSED_SITE_STATUSES = frozenset({"Child", "Customer", "Pilot"})


@dataclass
class SiteRoster:
    """The schools under a district, and whether any of them say they are licensed.

    The two counts are deliberately separate. **A district can have 169 child
    accounts and zero licence markers**, which is exactly Pinellas — a customer
    that renewed at $731k last month and whose 169 schools all carry a NULL
    status. Reporting "0 licensed sites" there would be a confident false answer;
    reporting "169 sites" as licensed would be a different one.
    """

    district_name: str
    licensed: list[str] = field(default_factory=list)
    unmarked: list[str] = field(default_factory=list)
    #: Set when the roster query itself failed, so "no sites" is distinguishable
    #: from "could not look".
    unavailable: bool = False

    @property
    def total(self) -> int:
        return len(self.licensed) + len(self.unmarked)

    def one_line(self) -> str:
        """The summary folded into the district brief, whether or not it was asked for."""
        if self.unavailable:
            return "Sites: could not be read from Salesforce (UNKNOWN, not zero)."
        if not self.total:
            return "Sites: no child accounts on this district in Salesforce."
        if self.licensed:
            return (
                f"Sites: {len(self.licensed)} of {self.total} school accounts carry a "
                f"site licence marker."
            )
        return (
            f"Sites: {self.total} school accounts, NONE carrying a site licence marker. "
            "That is the school roster, not a licence list — Salesforce cannot say which "
            "of these sites are licensed."
        )

    def describe(self) -> str:
        """The full roster, for when someone asks for the list itself."""
        if self.unavailable:
            return (
                f"Could not read the site list for {self.district_name} from Salesforce. "
                "That is UNKNOWN, not an empty district."
            )
        if not self.total:
            return (
                f"{self.district_name} has no child accounts in Salesforce. Districts are "
                "often held as a single account, so this does not mean they have one school."
            )

        lines: list[str] = []
        if self.licensed:
            lines.append(f"{self.district_name} — {len(self.licensed)} site(s) marked as licensed:")
            lines.extend(f"  {name}" for name in self.licensed[:60])
            if len(self.licensed) > 60:
                lines.append(f"  …and {len(self.licensed) - 60} more")
        else:
            lines.append(
                f"{self.district_name} — {self.total} school accounts in Salesforce, and NONE "
                "carry a site licence marker. This is the school roster, not a licence list. "
                "Do not tell anyone these sites are licensed, and do not tell them the "
                "district has no licensed sites either: Salesforce simply does not record it "
                "for this district."
            )
            lines.extend(f"  {name}" for name in self.unmarked[:40])
            if len(self.unmarked) > 40:
                lines.append(f"  …and {len(self.unmarked) - 40} more")

        if self.licensed and self.unmarked:
            lines.append(
                f"  ({len(self.unmarked)} further school account(s) carry no marker either way.)"
            )
            # An unmarked school is not an unlicensed one, and the ratio is the tell.
            # Pinellas marks 1 of 169 while renewing at $731,625, so "one licensed
            # site" would be a confident false reading of exactly the data that reads
            # correctly at Ypsilanti's 10 of 17. State it rather than trusting the
            # reader to notice the denominator.
            if len(self.licensed) * 5 < self.total:
                lines.append(
                    f"  CAUTION: only {len(self.licensed)} of {self.total} are marked. This "
                    "district largely does not maintain the site marker, so the list above is "
                    "not the set of licensed schools -- it is the set that happens to be "
                    "tagged. Do not present it as coverage."
                )
        lines.append(
            "  Seat counts are NOT available: of 5,631 site-level accounts org-wide, one "
            "has a licence count on it. Never quote a number of seats or students."
        )
        return "\n".join(lines)


async def fetch_child_sites(client: Any, account_id: str, district_name: str = "") -> SiteRoster:
    """The schools filed under a district, split by whether they claim a licence.

    Josh's second priority, and the answer is partial in a way worth stating: the
    ROSTER is readable on the credential we already have, and the seat COUNTS are
    not. Of 5,631 site-level accounts exactly one carries a licence count, so
    anything that reports seats is reporting a blank field.
    """
    roster = SiteRoster(district_name=district_name)
    if not account_id:
        return roster
    try:
        rows = await client.query(
            "SELECT Id, Name, Customer_Status__c FROM Account "
            f"WHERE ParentId = '{_soql_escape(account_id)}' ORDER BY Name LIMIT 400"
        )
    except Exception:
        # Distinct from an empty district, and the render says so.
        logger.warning("site roster fetch failed for account %s", account_id, exc_info=True)
        roster.unavailable = True
        return roster

    for row in rows:
        name = str(row.get("Name") or "(unnamed)")
        if str(row.get("Customer_Status__c") or "") in _LICENSED_SITE_STATUSES:
            roster.licensed.append(name)
        else:
            roster.unmarked.append(name)
    return roster


@dataclass
class OpportunityHistory:
    """Open and closed deals on one account. Josh's third priority.

    `count_open_opportunities` returned an integer and excluded closed deals
    entirely, so "Pinellas: 5 open opportunities" was the whole of what anyone
    could learn — while the same account carried a $731,625 renewal closed won
    five weeks earlier and another $731,625 renewal open for July 2027. The
    number was true and told nobody anything.
    """

    open_deals: list[dict[str, Any]] = field(default_factory=list)
    won: list[dict[str, Any]] = field(default_factory=list)
    lost: list[dict[str, Any]] = field(default_factory=list)
    unavailable: bool = False

    @staticmethod
    def _money(amount: Any) -> str:
        """Amount is frequently 0 or NULL on real rows; say so rather than print $0."""
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return "amount not recorded"
        if value <= 0:
            return "amount not recorded"
        return f"${value:,.0f}"

    def _line(self, row: dict[str, Any]) -> str:
        return f"  {row.get('CloseDate') or '(no date)'} — {self._money(row.get('Amount'))}"

    def describe(self) -> str:
        if self.unavailable:
            return "Opportunities: could not be read (UNKNOWN, not zero)."
        if not (self.open_deals or self.won or self.lost):
            return "Opportunities: none on this account."

        lines: list[str] = []
        if self.open_deals:
            lines.append(f"Open opportunities ({len(self.open_deals)}):")
            lines.extend(
                f"{self._line(r)} — {r.get('StageName') or 'stage not set'}"
                for r in self.open_deals[:6]
            )
        if self.won:
            lines.append(f"Closed won ({len(self.won)}), most recent first:")
            lines.extend(self._line(r) for r in self.won[:4])
        if self.lost:
            # Named separately because a loss on a current customer is a different
            # fact from a loss on a prospect, and it is the one worth reading.
            lines.append(f"Closed lost ({len(self.lost)}), most recent first:")
            lines.extend(
                f"{self._line(r)} — {r.get('Reason__c') or 'no reason recorded'}"
                for r in self.lost[:4]
            )
        return "\n".join(lines)


async def fetch_opportunity_history(
    client: Any, account_id: str, *, limit: int = 60
) -> OpportunityHistory:
    """Open and closed deals on one account, newest first.

    One query, the same round trip the old count made — it selected `Id` alone and
    returned `len(rows)`. `Reason__c` is included because Salesforce DOES record
    loss reasons on roughly 28,600 closed-lost opportunities, a fact this codebase
    got wrong twice.
    """
    history = OpportunityHistory()
    if not account_id:
        return history
    try:
        rows = await client.query(
            "SELECT Id, Name, StageName, Amount, CloseDate, IsClosed, IsWon, Reason__c "
            f"FROM Opportunity WHERE AccountId = '{_soql_escape(account_id)}' "
            f"ORDER BY CloseDate DESC LIMIT {int(limit)}"
        )
    except Exception:
        logger.warning("opportunity fetch failed for account %s", account_id, exc_info=True)
        history.unavailable = True
        return history

    for row in rows:
        if not row.get("IsClosed"):
            history.open_deals.append(row)
        elif row.get("IsWon"):
            history.won.append(row)
        else:
            history.lost.append(row)
    return history
