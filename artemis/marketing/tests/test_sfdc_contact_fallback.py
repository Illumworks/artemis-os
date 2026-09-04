"""Answering "is sales already working this person?" without the Task object.

The run-as user has no read access to Task, so the recent-contact check 400s for
every contact that exists in Salesforce and fails closed. Safe, but it blocked
the send path outright and told everyone to go and ask Neil for a permission.

The same question is answerable from the Contact record, which IS readable:
Gong syncs its flow state onto Contact, so "a seller is working them right now"
and "when were they last touched" were available the whole time. Task adds WHAT
a touch was; this answers WHETHER, which is what the guardrail is for.

Fail-closed is preserved where it matters: if the fallback itself cannot be read,
the caller still reports UNVERIFIED rather than clear.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from artemis.marketing.salesforce_suppression import (
    SKIP_RECENT_SALES_CONTACT,
    _recent_contact_from_contact_record,
)


class FakeClient:
    def __init__(self, rows: list[dict] | None = None, boom: bool = False) -> None:
        self.rows = rows
        self.boom = boom

    async def query(self, _soql: str) -> list[dict]:
        if self.boom:
            raise RuntimeError("no access")
        return self.rows or []


def _contact(**kw: object) -> dict:
    base = {
        "Id": "003X",
        "LastActivityDate": None,
        "Gong__Actively_Being_in_a_Flow__c": False,
        "Gong__Current_Flow_Name__c": None,
        "Gong__Current_Flow_User_Name__c": None,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_an_active_gong_sequence_suppresses_and_names_the_seller() -> None:
    """Josh's guardrail, verbatim: do not send into an open sales conversation."""
    client = FakeClient(
        [
            _contact(
                **{
                    "Gong__Actively_Being_in_a_Flow__c": True,
                    "Gong__Current_Flow_Name__c": "Texas/HB 1416",
                    "Gong__Current_Flow_User_Name__c": "Ann-Marie Meyn",
                }
            )
        ]
    )

    result = await _recent_contact_from_contact_record(client, "003X", 30)

    assert result is not None
    assert result.suppressed
    assert result.skip_reason == SKIP_RECENT_SALES_CONTACT
    assert "Ann-Marie Meyn" in result.detail
    assert "Texas/HB 1416" in result.detail


@pytest.mark.asyncio
async def test_activity_inside_the_window_suppresses() -> None:
    recent = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d")
    client = FakeClient([_contact(LastActivityDate=recent)])

    result = await _recent_contact_from_contact_record(client, "003X", 30)

    assert result is not None
    assert result.suppressed
    assert recent in result.detail


@pytest.mark.asyncio
async def test_activity_outside_the_window_does_not_suppress() -> None:
    old = (datetime.now(UTC) - timedelta(days=200)).strftime("%Y-%m-%d")
    client = FakeClient([_contact(LastActivityDate=old)])

    result = await _recent_contact_from_contact_record(client, "003X", 30)

    assert result is not None
    assert not result.suppressed


@pytest.mark.asyncio
async def test_a_clear_result_still_says_what_it_could_not_see() -> None:
    """The answer is real but partial, and must not be passed off as complete."""
    client = FakeClient([_contact()])

    result = await _recent_contact_from_contact_record(client, "003X", 30)

    assert result is not None
    assert not result.suppressed
    assert "Task detail is not readable" in result.detail


@pytest.mark.asyncio
async def test_an_unparseable_date_does_not_crash_or_falsely_suppress() -> None:
    client = FakeClient([_contact(LastActivityDate="not-a-date")])

    result = await _recent_contact_from_contact_record(client, "003X", 30)

    assert result is not None
    assert not result.suppressed


@pytest.mark.asyncio
async def test_an_unreadable_fallback_returns_none_so_the_caller_fails_closed() -> None:
    """None means "I could not look", which must never read as "nobody is there"."""
    assert await _recent_contact_from_contact_record(FakeClient(boom=True), "003X", 30) is None
    assert await _recent_contact_from_contact_record(FakeClient([]), "003X", 30) is None
