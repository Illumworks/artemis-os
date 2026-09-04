"""Rendering the people at a district, conflicts first.

Josh's first ask was the decision-makers — superintendent, chief academic
officer, curriculum leads — and his first guardrail was never emailing someone a
seller is already working. Both are answered from Salesforce, so both come back
in one brief.

Ordering is the design, not cosmetics: a contact in active outreach changes what
happens next, and burying that under a roster of twenty-five names is how it gets
missed.
"""

from __future__ import annotations

import pytest

from artemis.floating_artemis.tools.salesforce_tools import _render_people
from artemis.marketing.salesforce_account_lookup import AccountContact


class FakeClient:
    def __init__(self, rows: list[dict] | None = None, boom: bool = False) -> None:
        self.rows = rows or []
        self.boom = boom

    async def query(self, _soql: str) -> list[dict]:
        if self.boom:
            raise RuntimeError("no access")
        return self.rows


def _row(name: str, title: str = "", *, flow: str = "", rep: str = "", last: str = "") -> dict:
    return {
        "Id": f"003{name[:3]}",
        "Name": name,
        "Title": title,
        "Email": f"{name.split()[0].lower()}@d.org",
        "LastActivityDate": last,
        "Gong__Actively_Being_in_a_Flow__c": bool(flow),
        "Gong__Current_Flow_Name__c": flow,
        "Gong__Current_Flow_User_Name__c": rep,
        "Gong__Added_to_Flow_Date__c": "2026-08-13" if flow else None,
    }


@pytest.mark.asyncio
async def test_a_contact_in_active_outreach_leads_and_says_do_not_send() -> None:
    client = FakeClient(
        [
            _row("Judith White", "Chief Academic Officer", last="2026-05-05"),
            _row("Mike Miles", "Superintendent", flow="Texas/HB 1416", rep="Ann-Marie Meyn"),
        ]
    )

    out = await _render_people(client, "001X")

    assert "DO NOT SEND" in out
    assert out.index("DO NOT SEND") < out.index("Judith White"), "the conflict must lead"
    assert "Ann-Marie Meyn" in out
    assert "Texas/HB 1416" in out


@pytest.mark.asyncio
async def test_titled_contacts_come_before_untitled_ones() -> None:
    """A title is what makes someone a decision-maker rather than a name."""
    client = FakeClient(
        [
            _row("LaQuitta Reed"),
            _row("Judith White", "Chief Academic Officer"),
            _row("Arielle Stone"),
        ]
    )

    out = await _render_people(client, "001X")

    assert out.index("Judith White") < out.index("without a title")
    assert "LaQuitta Reed" in out and "Arielle Stone" in out


@pytest.mark.asyncio
async def test_the_count_of_conflicts_is_stated_up_front() -> None:
    client = FakeClient(
        [
            _row("A One", "Director", flow="F1", rep="Rep One"),
            _row("B Two", "Director", flow="F2", rep="Rep Two"),
            _row("C Three", "Director"),
        ]
    )

    out = await _render_people(client, "001X")

    assert "2 contact(s)" in out
    assert "3 total, 2 in active outreach" in out


@pytest.mark.asyncio
async def test_no_contacts_is_reported_as_unknown_not_as_clear() -> None:
    """The failure that matters: an empty result read as "nobody is being worked"."""
    out = await _render_people(FakeClient([]), "001X")

    assert "not a clean bill of health" in out
    assert "UNKNOWN" in out


@pytest.mark.asyncio
async def test_a_query_failure_is_also_reported_as_unknown() -> None:
    out = await _render_people(FakeClient(boom=True), "001X")

    assert "UNKNOWN" in out


def test_describe_states_who_is_working_the_contact() -> None:
    person = AccountContact(
        contact_id="003X",
        name="Mike Miles",
        title="Superintendent",
        in_active_flow=True,
        flow_name="Texas/HB 1416",
        flow_owner="Ann-Marie Meyn",
        flow_since="2026-08-13",
    )

    text = person.describe()

    assert person.conflicted
    assert "IN ACTIVE OUTREACH by Ann-Marie Meyn" in text
    assert "2026-08-13" in text


def test_a_contact_with_no_activity_says_so_rather_than_looking_clear() -> None:
    person = AccountContact(contact_id="003X", name="Jane Doe")
    assert "no recorded activity" in person.describe()
