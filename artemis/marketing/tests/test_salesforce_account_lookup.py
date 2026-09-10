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


# ── site roster (Josh's item 2) ──────────────────────────────────────────────


def test_an_unmarked_roster_is_not_reported_as_unlicensed() -> None:
    """Pinellas has 169 school accounts, one marker, and renewed at $731,625 five
    weeks ago. "No licensed sites" would be a confident false answer."""
    from artemis.marketing.salesforce_account_lookup import SiteRoster

    roster = SiteRoster(district_name="D", unmarked=[f"School {i}" for i in range(169)])

    line = roster.one_line()
    assert "NONE carrying a site licence marker" in line
    assert "not a licence list" in line
    assert "cannot say which" in line


def test_a_mostly_unmarked_roster_warns_about_its_own_denominator() -> None:
    """1 of 169 marked and 10 of 17 marked are the same data structure and mean
    opposite things. The list alone does not carry that."""
    from artemis.marketing.salesforce_account_lookup import SiteRoster

    sparse = SiteRoster("D", licensed=["One"], unmarked=[f"S{i}" for i in range(168)])
    dense = SiteRoster(
        "D", licensed=[f"L{i}" for i in range(10)], unmarked=[f"S{i}" for i in range(7)]
    )

    assert "CAUTION" in sparse.describe()
    assert "CAUTION" not in dense.describe()


def test_seat_counts_are_never_offered() -> None:
    """Of 5,631 site-level accounts, one carries a licence count. Anything that
    reports seats is reporting a blank field."""
    from artemis.marketing.salesforce_account_lookup import SiteRoster

    out = SiteRoster("D", licensed=["A School"]).describe()

    assert "Seat counts are NOT available" in out


def test_a_failed_roster_lookup_is_unknown_not_empty() -> None:
    from artemis.marketing.salesforce_account_lookup import SiteRoster

    roster = SiteRoster(district_name="D", unavailable=True)

    assert "UNKNOWN" in roster.describe()
    assert "could not be read" in roster.one_line()


def test_no_children_does_not_imply_one_school() -> None:
    """Districts are frequently held as a single account."""
    from artemis.marketing.salesforce_account_lookup import SiteRoster

    assert "does not mean they have one school" in SiteRoster(district_name="D").describe()


# ── opportunity history (Josh's item 3) ─────────────────────────────────────


def _opp(close: str, amount: float | None, *, closed: bool, won: bool, reason: str = "") -> dict:
    return {
        "CloseDate": close,
        "Amount": amount,
        "IsClosed": closed,
        "IsWon": won,
        "StageName": "Renewal",
        "Reason__c": reason,
    }


def test_the_history_separates_open_won_and_lost() -> None:
    """ "5 open opportunities" was true for Pinellas and hid a $731,625 renewal
    closed won five weeks earlier."""
    from artemis.marketing.salesforce_account_lookup import OpportunityHistory

    h = OpportunityHistory(
        open_deals=[_opp("2027-07-31", 731625.0, closed=False, won=False)],
        won=[_opp("2026-08-06", 731625.0, closed=True, won=True)],
        lost=[
            _opp("2025-09-02", 14010.0, closed=True, won=False, reason="Budget Constraints/Price")
        ],
    )
    out = h.describe()

    assert "$731,625" in out
    assert "Closed won (1)" in out
    assert "Closed lost (1)" in out
    assert "Budget Constraints/Price" in out, "Salesforce DOES record loss reasons"


def test_a_zero_amount_is_not_printed_as_a_dollar_zero() -> None:
    """Amount is unpopulated on many real rows. "$0" reads as a free deal."""
    from artemis.marketing.salesforce_account_lookup import OpportunityHistory

    h = OpportunityHistory(won=[_opp("2025-12-04", 0.0, closed=True, won=True)])

    assert "amount not recorded" in h.describe()
    assert "$0" not in h.describe()


def test_a_failed_opportunity_lookup_is_unknown_not_none() -> None:
    from artemis.marketing.salesforce_account_lookup import OpportunityHistory

    assert "UNKNOWN" in OpportunityHistory(unavailable=True).describe()


# ── rep notes and who we met (Josh, 2026-09-10) ──────────────────────────────


def test_a_deal_line_carries_the_reps_own_note() -> None:
    """Josh asked directly whether Callie could see these fields. She could not,
    so a $260k loss reading "Insufficient Access to Key Decision Makers" gave her
    no way to learn that a previous seller had met the district in person and the
    handoff to the current one went cold. Different problem, different re-entry."""
    from artemis.marketing.salesforce_account_lookup import OpportunityHistory

    h = OpportunityHistory(
        lost=[
            {
                "CloseDate": "2026-08-21",
                "Amount": 260000.0,
                "IsClosed": True,
                "IsWon": False,
                "Reason__c": "Insufficient Access to Key Decision Makers",
                "Description": "Meeting went great! Interested in the bilingual program.",
                "_contacts": ["Rebecca Kundert (Decision Maker)", "Amanda Gartzke (Gatekeeper)"],
            }
        ]
    )
    out = h.describe()

    assert "Meeting went great" in out
    assert "Rebecca Kundert (Decision Maker)" in out
    # The contradiction has to be visible in one place to be noticed at all.
    assert "Insufficient Access to Key Decision Makers" in out


def test_a_deal_with_no_note_stays_short() -> None:
    """Most rows have neither, and they must not gain blank lines."""
    from artemis.marketing.salesforce_account_lookup import OpportunityHistory

    out = OpportunityHistory(
        won=[{"CloseDate": "2026-08-06", "Amount": 731625.0, "IsClosed": True, "IsWon": True}]
    ).describe()

    assert "note:" not in out
    assert "we met:" not in out


def test_a_contact_line_shows_the_email_we_already_hold() -> None:
    """The field was fetched, stored on the object, and never rendered — so Callie
    read a list with no addresses in it and told Josh, repeatedly, to source them
    from ZoomInfo. 89% of Salesforce contacts have one; 92.7% on customer
    accounts. The work was already done."""
    from artemis.marketing.salesforce_account_lookup import AccountContact

    line = AccountContact(
        contact_id="1",
        name="Rebecca Kundert",
        title="Executive Director of C+I",
        email="kundert@madison.k12.wi.us",
        last_activity="2026-03-19",
    ).describe()

    assert "kundert@madison.k12.wi.us" in line
    assert "Rebecca Kundert" in line


def test_a_contact_in_active_outreach_still_shows_the_warning_first() -> None:
    """Showing the address must not bury the reason not to use it."""
    from artemis.marketing.salesforce_account_lookup import AccountContact

    line = AccountContact(
        contact_id="1",
        name="A Person",
        email="a@b.k12.us",
        in_active_flow=True,
        flow_owner="Natasha",
    ).describe()

    assert "IN ACTIVE OUTREACH" in line
    assert line.index("a@b.k12.us") < line.index("IN ACTIVE OUTREACH")


def test_a_contact_with_no_email_says_nothing_extra() -> None:
    from artemis.marketing.salesforce_account_lookup import AccountContact

    line = AccountContact(contact_id="1", name="A Person", title="Principal").describe()

    assert line.count("—") == 2, "no empty email segment"
