"""Looking a district up in Salesforce by name.

Josh asked on 2026-08-28 whether Callie could see opportunity history and
customer status. The answer was no — not because Salesforce lacked the data, but
because the lookup resolved against OUR index first and then required local
contacts. Houston ISD was sitting in Salesforce as `Customer` with 10 open
opportunities the entire time.

The account question belongs to Salesforce. These tests pin that it is asked
directly, that ambiguity abstains, and that an unreachable Salesforce never
reads as "not a customer".
"""

from __future__ import annotations

import re

import pytest

from artemis.marketing.salesforce_account_lookup import (
    _is_customer,
    count_open_opportunities,
    find_accounts_by_name,
    lookup_district,
)


class FakeClient:
    """Records the SOQL it is given, so injection and shape can both be asserted."""

    def __init__(self, rows: list[dict] | None = None, opps: int = 0, boom: bool = False):
        self.rows = rows or []
        self.opps = opps
        self.boom = boom
        self.queries: list[str] = []

    async def query(self, soql: str) -> list[dict]:
        self.queries.append(soql)
        if self.boom:
            raise RuntimeError("connection reset")
        if "FROM Opportunity" in soql:
            return [{"Id": f"006{i}"} for i in range(self.opps)]
        return self.rows


def _acct(name: str, status: str | None = None, ident: str = "001X") -> dict:
    return {"Id": ident, "Name": name, "Customer_Status__c": status}


@pytest.mark.asyncio
async def test_a_single_account_resolves_with_its_status() -> None:
    client = FakeClient([_acct("Houston Independent School District", "Customer")], opps=10)

    result = await lookup_district(client, "Houston Independent School District")

    assert result.found
    assert result.matched is not None
    assert result.matched.is_customer
    assert result.matched.customer_status == "Customer"
    assert result.matched.open_opportunities == 10
    assert "Customer" in result.matched.describe()


@pytest.mark.asyncio
async def test_an_exact_name_wins_over_broader_matches() -> None:
    """ "Jefferson County" must resolve to the account called exactly that."""
    client = FakeClient(
        [
            _acct("Jefferson County Adult High School", None, "001A"),
            _acct("Jefferson County", "Customer", "001B"),
            _acct("Jefferson County Alternative School", None, "001C"),
        ]
    )

    result = await find_accounts_by_name(client, "Jefferson County")

    assert result.found
    assert result.matched is not None and result.matched.account_id == "001B"


@pytest.mark.asyncio
async def test_several_accounts_abstain_and_are_all_reported() -> None:
    """Attributing one district's opportunity history to another is the real error."""
    client = FakeClient(
        [
            _acct("Dallas Independent School District", "Customer", "001A"),
            _acct("DISD - Dallas Independent School District", None, "001B"),
        ]
    )

    result = await find_accounts_by_name(client, "Dallas ISD")

    assert not result.found
    assert len(result.candidates) == 2


@pytest.mark.asyncio
async def test_no_account_is_a_real_absence_not_an_error() -> None:
    result = await find_accounts_by_name(FakeClient([]), "Zzyzx Unified")

    assert not result.found
    assert result.candidates == []
    assert result.error == ""


@pytest.mark.asyncio
async def test_an_unreachable_salesforce_is_unknown_never_not_a_customer() -> None:
    """The distinction that decides whether we contact someone."""
    result = await find_accounts_by_name(FakeClient(boom=True), "Houston ISD")

    assert not result.found
    assert "UNKNOWN" in result.error
    assert "not confirmed absent" in result.error


@pytest.mark.asyncio
async def test_a_name_with_an_apostrophe_is_escaped() -> None:
    """Prince George's would otherwise terminate the SOQL string literal."""
    client = FakeClient([_acct("Prince George's County Public Schools", "Pilot")])

    await find_accounts_by_name(client, "Prince George's")

    soql = client.queries[0]
    assert "\\'" in soql, "the apostrophe must be escaped"
    # Count only UNESCAPED quotes — an escaped one is a literal character, not a
    # delimiter, so a naive count is odd here and proves nothing.
    unescaped = re.findall(r"(?<!\\)'", soql)
    assert len(unescaped) % 2 == 0, f"delimiters unbalanced: {soql}"


@pytest.mark.asyncio
async def test_an_opportunity_count_failure_degrades_to_zero() -> None:
    """Colour, not a gate — unlike customer status, which fails loud."""
    assert await count_open_opportunities(FakeClient(boom=True), "001X") == 0
    assert await count_open_opportunities(FakeClient(), "") == 0


# ── The picklist trap ────────────────────────────────────────────────────────


def test_customer_status_is_read_as_a_picklist_not_a_boolean() -> None:
    """The configured field is a picklist on this org, shared with another product.

    Reading it as a plain boolean would misclassify thousands of accounts — the
    reason the truthy values are configured at all.
    """
    assert _is_customer("Customer")
    assert _is_customer("Pilot")
    assert _is_customer("parent")
    assert not _is_customer("Loss")
    assert not _is_customer("Prospect")
    assert not _is_customer(None)
    assert not _is_customer("")
