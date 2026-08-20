"""Tests for SFDC-1 -- Salesforce read-and-suppress.

Coverage (brief's required test list, verbatim):
  - A customer account -> skip_reason='existing_customer', nothing queued.
  - An open opportunity -> skip_reason='open_opportunity', nothing queued.
  - A contact emailed inside the window -> skip_reason='recent_sales_contact'.
  - The same contact emailed outside the window -> queued normally.
  - Salesforce unreachable -> skipped, not queued, skip_reason='salesforce_unavailable'.
  - A clean prospect with a real email -> queued, exactly as today.
  - Contact enrichment never overwrites a real email with a null.

Also: the describe-based fail-closed guard for our own assumed customer
field name, and the existing no_contacts_on_file path is untouched by any
of this (SFDC-1 must not weaken it).

Salesforce itself is never actually called -- artemis.marketing.
salesforce_suppression._get_client is monkeypatched to return a scripted
stub, so these tests exercise the REAL check_suppression /
check_suppression_for_recipients / enqueue_send_for_deliverable code paths
against fake data, per the brief's explicit instruction to build against a
fake credential/fake Salesforce rather than a live connection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.marketing.salesforce_suppression as suppression_mod
from artemis.config import settings
from artemis.marketing.contacts import create_contact, upsert_salesforce_contact
from artemis.marketing.models import CampaignCandidate, CampaignDeliverable, District
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.salesforce_suppression import (
    SKIP_EXISTING_CUSTOMER,
    SKIP_OPEN_OPPORTUNITY,
    SKIP_RECENT_SALES_CONTACT,
    SKIP_SALESFORCE_UNAVAILABLE,
    check_suppression,
)
from artemis.marketing.sends import enqueue_send_for_deliverable
from artemis.marketing.state_machine import DeliverableState

pytestmark = pytest.mark.asyncio


# ── Stub Salesforce client ────────────────────────────────────────────────────


class _StubSalesforceClient:
    """Scripted stand-in for SalesforceClient -- no real Salesforce call ever
    happens. describe_sobject and query route on the sobject name embedded in
    the SOQL/describe call, exactly like the real REST API, but from fixed
    canned data.
    """

    def __init__(
        self,
        *,
        account_fields: list[dict[str, Any]] | None = None,
        contact_records: list[dict[str, Any]] | None = None,
        account_records: list[dict[str, Any]] | None = None,
        opportunity_records: list[dict[str, Any]] | None = None,
        task_records: list[dict[str, Any]] | None = None,
    ) -> None:
        self.account_fields = (
            account_fields
            if account_fields is not None
            else [{"name": settings.salesforce_customer_field, "label": "Is Customer"}]
        )
        self.contact_records = contact_records if contact_records is not None else []
        self.account_records = account_records if account_records is not None else []
        self.opportunity_records = opportunity_records if opportunity_records is not None else []
        self.task_records = task_records if task_records is not None else []
        self.queries_seen: list[str] = []

    async def describe_sobject(self, sobject: str) -> dict[str, Any]:
        assert sobject == "Account"
        return {"fields": self.account_fields}

    async def query(self, soql: str) -> list[dict[str, Any]]:
        self.queries_seen.append(soql)
        if "FROM Contact" in soql:
            return self.contact_records
        if "FROM Account" in soql:
            return self.account_records
        if "FROM Opportunity" in soql:
            return self.opportunity_records
        if "FROM Task" in soql:
            return self.task_records
        raise AssertionError(f"unexpected SOQL in stub: {soql!r}")


def _customer_value() -> object:
    """A value the CONFIGURED customer field actually treats as 'is a customer'.

    These tests follow `settings.salesforce_customer_field` for the field NAME,
    so they must follow it for the VALUE too. The live org's field
    (Customer_Status__c) is a picklist, so a hardcoded boolean True stopped
    counting the moment the default moved off the boolean Is_Customer__c guess.
    """
    truthy = [v.strip() for v in settings.salesforce_customer_truthy_values.split(",") if v.strip()]
    return truthy[0] if truthy else True


def _non_customer_value() -> object:
    """A value the configured field treats as NOT a customer."""
    truthy = [v.strip() for v in settings.salesforce_customer_truthy_values.split(",") if v.strip()]
    return "Prospect" if truthy else False


def _patch_client(monkeypatch: pytest.MonkeyPatch, stub: _StubSalesforceClient) -> None:
    async def _fake_get_client(session: AsyncSession) -> _StubSalesforceClient:  # noqa: ARG001
        return stub

    monkeypatch.setattr(suppression_mod, "_get_client", _fake_get_client)


def _patch_client_raises(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    async def _fake_get_client(session: AsyncSession) -> Any:  # noqa: ARG001
        raise exc

    monkeypatch.setattr(suppression_mod, "_get_client", _fake_get_client)


# ── Helpers (district/candidate/deliverable seeding) ──────────────────────────


async def _make_district(session: AsyncSession, *, name: str = "Test District") -> District:
    d = District(name=name, state="TX", tier="D1", supported=True)
    session.add(d)
    await session.flush()
    return d


async def _make_candidate_for_district(
    session: AsyncSession, district_id: int
) -> CampaignCandidate:
    signal = await create_signal(
        session,
        headline="Test signal",
        campaign_family="outreach_email",
        source_type="manual",
        summary="Test",
        discovered_by="test",
        state="TX",
        reason_codes=[],
    )
    candidate = await create_campaign_candidate_from_signal(
        session, signal_id=signal.id, ruleset_version_tag="v1"
    )
    candidate.target_scope_json = {"mode": "named_districts", "district_ids": [district_id]}
    await session.flush()
    return candidate


async def _make_approved_deliverable(
    session: AsyncSession, candidate_id: int
) -> CampaignDeliverable:
    d = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id="sfdc1-draft-1",
        campaign_id=str(candidate_id),
        status=DeliverableState.approved.value,
        deliverable_metadata={
            "externalTitle": "Test Deliverable",
            "deliverableTypeSlug": "outreach_email",
            "versions": [{"id": "v1", "version_number": 1, "content": "Draft content here."}],
        },
    )
    session.add(d)
    await session.flush()
    return d


# ── check_suppression: unit-level ─────────────────────────────────────────────


async def test_check_suppression_existing_customer(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    district = await _make_district(db_session, name="Customer District")
    await db_session.commit()

    stub = _StubSalesforceClient(
        contact_records=[{"Id": "003x", "AccountId": "001x", "Name": "Alice", "Email": "a@ex.com"}],
        account_records=[{"Id": "001x", settings.salesforce_customer_field: _customer_value()}],
    )
    _patch_client(monkeypatch, stub)

    result = await check_suppression(
        db_session, district_id=district.id, email="a@ex.com", enrich=False
    )
    assert result.suppressed is True
    assert result.skip_reason == SKIP_EXISTING_CUSTOMER


async def test_check_suppression_open_opportunity(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    district = await _make_district(db_session, name="Opp District")
    await db_session.commit()

    stub = _StubSalesforceClient(
        contact_records=[{"Id": "003x", "AccountId": "001x", "Name": "Bob", "Email": "b@ex.com"}],
        account_records=[{"Id": "001x", settings.salesforce_customer_field: _non_customer_value()}],
        opportunity_records=[{"Id": "006x"}],
    )
    _patch_client(monkeypatch, stub)

    result = await check_suppression(
        db_session, district_id=district.id, email="b@ex.com", enrich=False
    )
    assert result.suppressed is True
    assert result.skip_reason == SKIP_OPEN_OPPORTUNITY


async def test_check_suppression_recent_sales_contact_inside_window(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    district = await _make_district(db_session, name="Recent Contact District")
    await db_session.commit()

    recent = datetime.now(UTC) - timedelta(days=5)
    stub = _StubSalesforceClient(
        contact_records=[{"Id": "003x", "AccountId": None, "Name": "Carol", "Email": "c@ex.com"}],
        task_records=[{"Id": "00Tx", "CreatedDate": recent.strftime("%Y-%m-%dT%H:%M:%SZ")}],
    )
    _patch_client(monkeypatch, stub)

    result = await check_suppression(
        db_session, district_id=district.id, email="c@ex.com", enrich=False
    )
    assert result.suppressed is True
    assert result.skip_reason == SKIP_RECENT_SALES_CONTACT


async def test_check_suppression_contact_emailed_outside_window_not_suppressed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as the inside-window test, but the stub's Task query
    represents what Salesforce itself would return for a WHERE CreatedDate >=
    <window_start> filter: an email from 200 days ago (older than the default
    90-day window) does not match that filter, so Salesforce would return no
    rows at all -- exactly what an empty task_records list represents here."""
    district = await _make_district(db_session, name="Old Contact District")
    await db_session.commit()

    stub = _StubSalesforceClient(
        contact_records=[{"Id": "003x", "AccountId": None, "Name": "Dana", "Email": "d@ex.com"}],
        task_records=[],  # nothing inside the window -- Salesforce's own filter already excluded it
    )
    _patch_client(monkeypatch, stub)

    result = await check_suppression(
        db_session, district_id=district.id, email="d@ex.com", enrich=False
    )
    assert result.suppressed is False
    assert result.skip_reason is None


async def test_check_suppression_task_query_date_bound_reflects_configured_window(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directly verifies the WINDOW is real, not decorative: the Task SOQL's
    CreatedDate lower bound must be computed from
    settings.salesforce_recent_contact_window_days (default 90), since that
    field-filtering is what makes 'inside/outside the window' actually mean
    something -- Salesforce itself does the date comparison, so the only
    thing our code can get wrong is the bound it sends."""
    district = await _make_district(db_session, name="Window Bound District")
    await db_session.commit()

    stub = _StubSalesforceClient(
        contact_records=[{"Id": "003x", "AccountId": None, "Name": "Eve", "Email": "eve@ex.com"}],
        task_records=[],
    )
    _patch_client(monkeypatch, stub)

    # -2s slack on `before` to absorb the microsecond truncation the SOQL
    # literal's "%Y-%m-%dT%H:%M:%SZ" format applies (whole seconds only).
    before = (
        datetime.now(UTC)
        - timedelta(days=settings.salesforce_recent_contact_window_days)
        - timedelta(seconds=2)
    )
    await check_suppression(db_session, district_id=district.id, email="eve@ex.com", enrich=False)
    after = datetime.now(UTC) - timedelta(days=settings.salesforce_recent_contact_window_days)

    task_queries = [q for q in stub.queries_seen if "FROM Task" in q]
    assert len(task_queries) == 1
    # Extract the CreatedDate literal and confirm it falls within [before, after]
    # (a couple of seconds of test-runtime slack either side).
    marker = "CreatedDate >= "
    idx = task_queries[0].index(marker) + len(marker)
    date_literal = task_queries[0][idx : idx + 20]
    parsed = datetime.strptime(date_literal, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert before <= parsed <= after


async def test_check_suppression_clean_prospect_not_found_in_salesforce(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    district = await _make_district(db_session, name="Prospect District")
    await db_session.commit()

    stub = _StubSalesforceClient(contact_records=[])  # no Contact matches this email
    _patch_client(monkeypatch, stub)

    result = await check_suppression(
        db_session, district_id=district.id, email="prospect@newdistrict.org", enrich=False
    )
    assert result.suppressed is False
    assert result.skip_reason is None


async def test_check_suppression_unavailable_on_client_error(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    district = await _make_district(db_session, name="Down District")
    await db_session.commit()

    from artemis.integrations.salesforce.client import SalesforceAuthError

    _patch_client_raises(monkeypatch, SalesforceAuthError("token exchange rejected"))

    result = await check_suppression(
        db_session, district_id=district.id, email="e@ex.com", enrich=False
    )
    assert result.suppressed is True
    assert result.skip_reason == SKIP_SALESFORCE_UNAVAILABLE


async def test_check_suppression_unavailable_when_customer_field_missing_from_describe(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The single most important behaviour in the brief: if our assumed
    customer field name is wrong, this must fail CLOSED -- never silently
    answer 'not a customer'."""
    district = await _make_district(db_session, name="Wrong Field District")
    await db_session.commit()

    stub = _StubSalesforceClient(account_fields=[{"name": "Type", "label": "Account Type"}])
    _patch_client(monkeypatch, stub)

    result = await check_suppression(
        db_session, district_id=district.id, email="f@ex.com", enrich=False
    )
    assert result.suppressed is True
    assert result.skip_reason == SKIP_SALESFORCE_UNAVAILABLE
    assert settings.salesforce_customer_field in result.detail


# ── enqueue_send_for_deliverable: wired end-to-end ────────────────────────────


async def test_enqueue_skips_existing_customer(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    district = await _make_district(db_session, name="E2E Customer District")
    await db_session.flush()
    await create_contact(
        db_session, district_id=district.id, name="Alice", email="alice@customer.org"
    )
    candidate = await _make_candidate_for_district(db_session, district.id)
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    stub = _StubSalesforceClient(
        contact_records=[
            {"Id": "003x", "AccountId": "001x", "Name": "Alice", "Email": "alice@customer.org"}
        ],
        account_records=[{"Id": "001x", settings.salesforce_customer_field: _customer_value()}],
    )
    _patch_client(monkeypatch, stub)

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable, actor="test"
    )
    await db_session.flush()

    assert send.status == "skipped"
    assert send.skip_reason == SKIP_EXISTING_CUSTOMER
    assert send.recipients == []

    await db_session.refresh(deliverable)
    assert deliverable.status == DeliverableState.approved.value  # NOT transitioned


async def test_enqueue_skips_salesforce_unavailable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Salesforce unreachable -> skipped, not queued. This is the most
    important test in the suite: a wrong fail-open here emails a real client."""
    district = await _make_district(db_session, name="E2E Down District")
    await db_session.flush()
    await create_contact(db_session, district_id=district.id, name="Bob", email="bob@ex.com")
    candidate = await _make_candidate_for_district(db_session, district.id)
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    from artemis.integrations.config_resolver import MissingProviderConfigError

    _patch_client_raises(
        monkeypatch, MissingProviderConfigError("salesforce", ["client_id", "client_secret"])
    )

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable, actor="test"
    )
    await db_session.flush()

    assert send.status == "skipped"
    assert send.skip_reason == SKIP_SALESFORCE_UNAVAILABLE
    assert send.recipients == []

    await db_session.refresh(deliverable)
    assert deliverable.status == DeliverableState.approved.value


async def test_enqueue_queues_clean_prospect_exactly_as_today(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    district = await _make_district(db_session, name="E2E Prospect District")
    await db_session.flush()
    await create_contact(
        db_session, district_id=district.id, name="Carol", email="carol@newprospect.org"
    )
    candidate = await _make_candidate_for_district(db_session, district.id)
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    stub = _StubSalesforceClient(contact_records=[])  # unknown to Salesforce
    _patch_client(monkeypatch, stub)

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable, actor="test"
    )
    await db_session.flush()

    assert send.status == "queued"
    assert send.skip_reason is None
    assert len(send.recipients) == 1
    assert send.recipients[0]["email"] == "carol@newprospect.org"

    await db_session.refresh(deliverable)
    assert deliverable.status == DeliverableState.queued_for_send.value


async def test_enqueue_no_contacts_path_unaffected_by_sfdc1(db_session: AsyncSession) -> None:
    """SFDC-1 must not touch the existing no_contacts_on_file behaviour --
    the suppression check only runs when recipients_snapshot is non-empty."""
    candidate = await _make_candidate_for_district(db_session, district_id=999_999_999)
    deliverable = await _make_approved_deliverable(db_session, candidate.id)
    await db_session.commit()

    send = await enqueue_send_for_deliverable(
        db_session, candidate=candidate, deliverable=deliverable
    )
    await db_session.flush()

    assert send.status == "skipped"
    assert send.skip_reason == "no_contacts_on_file"


# ── Contact enrichment: never overwrite a real email with a null ─────────────


async def test_enrichment_never_overwrites_email_or_clears_populated_fields(
    db_session: AsyncSession,
) -> None:
    district = await _make_district(db_session, name="Enrichment District")
    await db_session.flush()
    existing = await create_contact(
        db_session,
        district_id=district.id,
        name="Grace Hopper",
        email="grace@district.org",
        title="Director of Curriculum",
        phone="555-1234",
    )
    await db_session.commit()

    contact, created = await upsert_salesforce_contact(
        db_session,
        district_id=district.id,
        email="grace@district.org",
        name="Grace Hopper",
        title=None,  # Salesforce has nothing here -- must NOT clear the existing title
        phone=None,  # same for phone
        external_id="003SFtest",
    )

    assert created is False
    assert contact.id == existing.id
    assert contact.email == "grace@district.org"  # never touched
    assert contact.title == "Director of Curriculum"  # NOT cleared
    assert contact.phone == "555-1234"  # NOT cleared
    assert contact.external_id == "003SFtest"  # newly set, since it WAS provided


async def test_enrichment_creates_new_row_when_no_match(db_session: AsyncSession) -> None:
    district = await _make_district(db_session, name="New Salesforce Contact District")
    await db_session.commit()

    contact, created = await upsert_salesforce_contact(
        db_session,
        district_id=district.id,
        email="new.person@district.org",
        name="New Person",
        title="Superintendent",
        phone="555-9999",
        external_id="003SFnew",
    )

    assert created is True
    assert contact.source == "salesforce"
    assert contact.email == "new.person@district.org"
    assert contact.active is True
