"""Two lists read from Salesforce's own history, and the traps in building them.

The feature was pitched as "single-threaded deals" -- open opportunities with
exactly one contact. The data killed it: 5,209 of 6,535 open opportunities (80%)
have no OpportunityContactRole at all, so a contact-role count measures how the
record was created, not how the deal is being worked. What survives is narrower
and worse: late-stage deals with ZERO contacts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from artemis.marketing.pipeline_hygiene import (
    LATE_STAGES,
    _days_since,
    find_deals_without_contacts,
    find_stalled_deals,
    is_test_account,
)


class _Client:
    """Records the SOQL it was handed and replays canned rows."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.soql = ""

    async def query(self, soql: str) -> list[dict]:
        self.soql = soql
        return self._rows


def _row(**kw: object) -> dict:
    base = {
        "Id": "006x1",
        "Name": "Opp",
        "StageName": "Proposal Sent",
        "Amount": 1000.0,
        "CloseDate": "2026-12-31",
        "LastModifiedDate": "2026-04-15T00:00:00.000+0000",
        "Owner": {"Name": "Chris Blevins"},
        "Account": {"Name": "Iowa Department of Education"},
    }
    base.update(kw)
    return base


# ── the false positive that would have libelled a real school ────────────────


def test_a_school_with_test_inside_a_word_is_not_a_test_account() -> None:
    """SOQL LIKE '%TEST%' matches "Adams Protestant Reformed Christian School".

    Pro-TEST-ant. Handing sales a list where a real school is written off as a
    test record is worse than handing them no list, because the next real name
    they see is also in doubt.
    """
    assert not is_test_account("Adams Protestant Reformed Christian School")
    assert not is_test_account("Protestant Academy")
    assert not is_test_account("Testa Memorial School District")
    assert not is_test_account("Contested Valley USD")


def test_actual_test_records_are_still_caught() -> None:
    for name in (
        "TEST Account 5",
        "Amira Test District",
        "Amelia Test District (NOT REAL)",
        "Danae Test 10",
        "demo account",
        "Do Not Use - old",
    ):
        assert is_test_account(name), name


def test_an_empty_or_missing_name_is_not_a_test_account() -> None:
    assert not is_test_account(None)
    assert not is_test_account("")


# ── stalled deals ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stalled_deals_exclude_test_records() -> None:
    client = _Client([_row(Account={"Name": "TEST Account 5"}), _row()])

    deals = await find_stalled_deals(client, days=90, limit=10)

    assert [d.account_name for d in deals] == ["Iowa Department of Education"]


@pytest.mark.asyncio
async def test_stalled_query_asks_only_for_open_opportunities() -> None:
    """A closed deal that has not moved in 90 days is not stalled, it is done."""
    client = _Client([])

    await find_stalled_deals(client, days=90, limit=10)

    assert "IsClosed = false" in client.soql
    assert "LAST_N_DAYS:90" in client.soql


@pytest.mark.asyncio
async def test_the_stall_window_is_honoured() -> None:
    client = _Client([])
    await find_stalled_deals(client, days=30, limit=5)
    assert "LAST_N_DAYS:30" in client.soql


def test_days_since_counts_from_the_last_record_change() -> None:
    stamp = (datetime.now(UTC) - timedelta(days=142)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
    assert _days_since(stamp) == 142


# ── late-stage deals with nobody attached ────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_generated_renewals_are_excluded_from_the_no_contact_list() -> None:
    """3,208 of them have no contact because a machine made them.

    Including them buries the ~320 real findings under an artefact of how the
    renewal records are created.
    """
    assert "Renewal Opp Auto-Generated" not in LATE_STAGES
    assert "Renewal Health Assessment" not in LATE_STAGES
    assert "Renewal Created" not in LATE_STAGES

    client = _Client([])
    await find_deals_without_contacts(client, limit=10)

    assert "Renewal Opp Auto-Generated" not in client.soql
    assert "Proposal Sent" in client.soql


@pytest.mark.asyncio
async def test_the_no_contact_list_asks_for_zero_contacts_not_one() -> None:
    """ "Exactly one contact" was the original idea and the data does not support it."""
    client = _Client([])

    await find_deals_without_contacts(client, limit=10)

    assert "NOT IN (SELECT OpportunityId FROM OpportunityContactRole)" in client.soql


@pytest.mark.asyncio
async def test_no_contact_deals_exclude_test_records_too() -> None:
    client = _Client([_row(Account={"Name": "Danae Test 3"}), _row()])

    deals = await find_deals_without_contacts(client, limit=10)

    assert len(deals) == 1
    assert deals[0].account_name == "Iowa Department of Education"


@pytest.mark.asyncio
async def test_a_deal_with_no_amount_still_appears_and_says_so() -> None:
    """Dropping them would hide real late-stage deals for a missing field."""
    client = _Client([_row(Amount=None)])

    deals = await find_deals_without_contacts(client, limit=10)

    assert len(deals) == 1
    assert "no amount" in deals[0].as_line()


@pytest.mark.asyncio
async def test_a_missing_owner_or_account_does_not_crash_the_list() -> None:
    """Nullable columns are NULL on pre-existing rows, forever."""
    client = _Client([_row(Owner=None, Account=None)])

    deals = await find_stalled_deals(client, days=90, limit=10)

    assert deals[0].owner == "(unassigned)"
    assert deals[0].account_name == "(no account)"
