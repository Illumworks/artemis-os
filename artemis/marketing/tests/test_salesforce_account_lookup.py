"""How a contact line describes itself, and the date field that lies about tense.

`Contact.LastActivityDate` is Salesforce's most recent Event DUE date, not a
record of contact that happened. `fetch_account_contacts` sorts on it descending,
so on the 184 accounts carrying a future one those rows land at the top of what
an agent reads.
"""

from __future__ import annotations

# ── LastActivityDate is a DUE date (2026-09-09) ──────────────────────────────


def test_a_future_activity_date_is_not_described_as_a_past_touch() -> None:
    """Salesforce's `LastActivityDate` counts Events that have not happened yet.
    Callie was handed "last touched 2027-03-29" for Pinellas, read it as a past
    contact in March, and concluded the list was warm."""
    from datetime import UTC, datetime, timedelta

    from artemis.marketing.salesforce_account_lookup import AccountContact

    ahead = (datetime.now(UTC) + timedelta(days=200)).strftime("%Y-%m-%d")
    line = AccountContact(contact_id="1", name="A Person", last_activity=ahead).describe()

    assert "last touched" not in line
    assert "SCHEDULED" in line
    assert ahead in line


def test_a_past_activity_date_still_reads_as_a_touch() -> None:
    """The common case must not get more verbose to fix the rare one."""
    from datetime import UTC, datetime, timedelta

    from artemis.marketing.salesforce_account_lookup import AccountContact

    behind = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
    line = AccountContact(contact_id="1", name="A Person", last_activity=behind).describe()

    assert f"last touched {behind}" in line


def test_today_is_a_touch_not_a_booking() -> None:
    """The boundary: an activity dated today has happened."""
    from datetime import UTC, datetime

    from artemis.marketing.salesforce_account_lookup import AccountContact

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    line = AccountContact(contact_id="1", name="A Person", last_activity=today).describe()

    assert "last touched" in line
    assert "SCHEDULED" not in line


def test_an_unparseable_date_is_handed_back_rather_than_given_a_tense() -> None:
    """Two records really are dirty (years 2763 and 2202). Guessing at a tense
    for something we cannot read is how the original bug worked."""
    from artemis.marketing.salesforce_account_lookup import AccountContact

    line = AccountContact(contact_id="1", name="A Person", last_activity="not-a-date").describe()

    assert "not-a-date" in line
    assert "last touched" not in line
