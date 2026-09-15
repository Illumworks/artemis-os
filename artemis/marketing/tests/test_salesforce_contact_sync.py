"""Salesforce → district_contacts.

`district_contacts` held seven rows for 13,466 districts, six of them test
leftovers, which is why 1,095 signals sat at `unrouted_no_contact`. Salesforce
holds 671,478 contacts that are emailable, not hard-bounced, and mapped to an
NCES district. Every fixture below is shaped like a record that pull returns.
"""

from __future__ import annotations

from typing import Any

import pytest

from artemis.marketing.salesforce_contact_sync import (
    _one_per_district_email,
    _soql,
    sync_district_contacts,
)


class _FakeClient:
    """Stands in for SalesforceClient; records the SOQL it was handed."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.queries: list[str] = []

    async def query_all(self, soql: str, **_: Any) -> list[dict[str, Any]]:
        self.queries.append(soql)
        return self.records


def _contact(sf_id: str, email: str, nces: str, name: str = "A Person") -> dict[str, Any]:
    return {
        "Id": sf_id,
        "Name": name,
        "Title": "Curriculum Director",
        "Email": email,
        "Phone": None,
        "Account": {"NCES_District_ID__c": nces},
    }


# ── The query ────────────────────────────────────────────────────────────────


def test_bounced_and_unmapped_contacts_are_excluded_by_the_query() -> None:
    """Filtered server-side rather than paged across the network and dropped
    here — 1,962 contacts are hard-bounced."""
    soql = _soql(states=None, since=None, limit=None)
    assert "IsEmailBounced = false" in soql
    assert "Email != null" in soql
    assert "Account.NCES_District_ID__c != null" in soql


def test_a_state_filter_scopes_the_pull() -> None:
    assert "Account.BillingState IN ('NM')" in _soql(states=("nm",), since=None, limit=None)


def test_an_incremental_pass_asks_only_for_what_changed() -> None:
    from datetime import UTC, datetime

    soql = _soql(states=None, since=datetime(2026, 9, 1, tzinfo=UTC), limit=None)
    assert "LastModifiedDate >" in soql


def test_the_query_never_writes() -> None:
    """Salesforce is read-only by instruction."""
    soql = _soql(states=None, since=None, limit=None).upper()
    for verb in ("INSERT", "UPDATE", "DELETE", "UPSERT"):
        assert verb not in soql


# ── Duplicate collapse ───────────────────────────────────────────────────────


def test_the_same_person_twice_at_one_district_is_collapsed() -> None:
    """A unique index on (district_id, lower(email)) would abort the load, and
    Salesforce genuinely holds duplicates — 249 of 7,095 New Mexico rows."""
    rows = [
        {"district_id": 1, "email": "a@d.org"},
        {"district_id": 1, "email": "A@D.ORG"},
        {"district_id": 1, "email": "b@d.org"},
        {"district_id": 2, "email": "a@d.org"},
    ]
    kept, collapsed = _one_per_district_email(rows)  # type: ignore[arg-type]
    assert collapsed == 1
    assert len(kept) == 3


def test_the_same_address_at_different_districts_is_kept() -> None:
    """One person can legitimately serve two districts."""
    rows = [{"district_id": 1, "email": "a@d.org"}, {"district_id": 2, "email": "a@d.org"}]
    kept, collapsed = _one_per_district_email(rows)  # type: ignore[arg-type]
    assert collapsed == 0
    assert len(kept) == 2


# ── The sync ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_sync_is_a_dry_run_unless_told_otherwise(db_session: Any) -> None:
    """Loading two-thirds of a million contacts must not happen because someone
    called a function with the arguments they happened to have."""
    client = _FakeClient([_contact("003A", "a@d.org", "9999999")])
    result = await sync_district_contacts(db_session, client=client)
    assert result.dry_run is True


@pytest.mark.asyncio
async def test_an_unmatched_nces_id_is_reported_not_guessed(db_session: Any) -> None:
    """Salesforce carries charters with state-assigned numbers absent from the
    federal district file. 64 of New Mexico's 200 district ids were unmatched."""
    client = _FakeClient([_contact("003A", "a@d.org", "0000000-not-a-district")])
    result = await sync_district_contacts(db_session, client=client)
    assert result.skipped_unmapped == 1
    assert result.inserted == 0
    assert "0000000-not-a-district" in result.unmapped_nces_ids


@pytest.mark.asyncio
async def test_an_undeliverable_address_never_becomes_a_contact(db_session: Any) -> None:
    """Salesforce filters hard bounces; this catches the reserved and malformed
    addresses it happily stores."""
    client = _FakeClient([_contact("003A", "someone@a-district.example", "9999999")])
    result = await sync_district_contacts(db_session, client=client)
    assert result.skipped_undeliverable == 1
    assert result.inserted == 0


@pytest.mark.asyncio
async def test_the_summary_says_what_a_pass_actually_did(db_session: Any) -> None:
    client = _FakeClient([_contact("003A", "bad@x.example", "9999999")])
    result = await sync_district_contacts(db_session, client=client)
    assert "DRY RUN" in result.summary()
    assert "undeliverable" in result.summary()
