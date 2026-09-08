"""Gong metadata client: what it reads, and what it structurally cannot.

Jon's rule is that call content is never stored. The strongest form of that rule
is a client with no transcript method at all, so most of this file is about the
absence rather than the presence.
"""

from __future__ import annotations

import pytest

from artemis.integrations.gong.client import (
    CallContext,
    GongMetadataClient,
    GongUnavailableError,
    _to_context,
)

# ── the rule, enforced by construction ───────────────────────────────────────


def test_the_client_has_no_way_to_fetch_a_transcript() -> None:
    """Not a flag, not a guarded path, not a method that raises. Absent.

    A prompt can be ignored and a flag can be flipped by someone in a hurry. An
    absent method has to be written, which is a decision someone makes on purpose
    rather than by accident.
    """
    surface = {name for name in dir(GongMetadataClient) if not name.startswith("__")}

    for forbidden in ("transcript", "get_transcript", "fetch_transcript", "calls_transcript"):
        assert forbidden not in surface

    assert not any("transcript" in name.lower() for name in surface)


def test_the_module_never_references_the_transcript_endpoint() -> None:
    """The path itself should not appear, so nobody can 'just add' a caller."""
    import inspect

    from artemis.integrations.gong import client

    assert "/v2/calls/transcript" not in inspect.getsource(client)


def test_participant_names_are_not_carried_forward() -> None:
    """Names and emails are IN the payload and are deliberately dropped.

    Who attended is a fact about people, not about the district, and the scope is
    the district.
    """
    ctx = _to_context(
        {
            "metaData": {"id": "c1", "title": "Call", "started": "2026-05-11T10:00:00Z"},
            "parties": [
                {"affiliation": "External", "name": "A Person", "emailAddress": "a@x.org"},
                {"affiliation": "Internal", "name": "A Rep", "emailAddress": "b@amira.com"},
            ],
        }
    )

    blob = repr(ctx)
    assert "A Person" not in blob
    assert "a@x.org" not in blob
    assert ctx.external_parties == 1
    assert ctx.internal_parties == 1


# ── the three-valued affiliation ─────────────────────────────────────────────


def test_unknown_affiliation_is_counted_separately_not_as_external() -> None:
    """Three values, not two, and Unknown is the largest group on imported calls.

    Treating "not Internal" as "the customer" is wrong on real data.
    """
    ctx = _to_context(
        {
            "metaData": {"id": "c2"},
            "parties": [
                {"affiliation": "Unknown"},
                {"affiliation": "Unknown"},
                {"affiliation": "External"},
                {"affiliation": "Internal"},
            ],
        }
    )

    assert (ctx.internal_parties, ctx.external_parties, ctx.unknown_parties) == (1, 1, 2)


def test_a_missing_affiliation_counts_as_unknown_not_external() -> None:
    ctx = _to_context({"metaData": {"id": "c3"}, "parties": [{}, {}]})
    assert ctx.unknown_parties == 2
    assert ctx.external_parties == 0


# ── trackers are counts, and most of them are zero ───────────────────────────


def test_only_trackers_that_fired_are_reported() -> None:
    """All 26 come back on every call; 26 zeroes is not a finding."""
    ctx = _to_context(
        {
            "metaData": {"id": "c4"},
            "content": {
                "trackers": [
                    {"name": "Customer concerns", "count": 3},
                    {"name": "Pricing", "count": 0},
                    {"name": "Objections (tracker)", "count": 1},
                ]
            },
        }
    )

    assert ctx.fired_trackers == {"Customer concerns": 3, "Objections (tracker)": 1}


# ── Salesforce context arrives inline ────────────────────────────────────────


def test_account_and_tier_are_read_from_the_call_itself() -> None:
    """District_Marketing_Tier__c on the call means no second Salesforce query."""
    ctx = _to_context(
        {
            "metaData": {"id": "c5"},
            "context": [
                {
                    "objects": [
                        {
                            "objectType": "Account",
                            "fields": [
                                {"name": "Name", "value": "A District"},
                                {"name": "District_Marketing_Tier__c", "value": "D1"},
                            ],
                        },
                        {
                            "objectType": "Opportunity",
                            "fields": [
                                {"name": "StageName", "value": "Active Discussion"},
                                {
                                    "name": "DashboardsGSP__Days_Since_Last_Stage_Change__c",
                                    "value": 40,
                                },
                            ],
                        },
                    ]
                }
            ],
        }
    )

    assert ctx.account_name == "A District"
    assert ctx.account_tier == "D1"
    assert ctx.opportunity_stage == "Active Discussion"
    assert ctx.days_since_stage_change == 40


def test_a_call_with_no_salesforce_context_still_parses() -> None:
    """2,582 imported calls carry no account link at all."""
    ctx = _to_context({"metaData": {"id": "c6", "title": "Imported"}})

    assert ctx.account_name is None
    assert isinstance(ctx, CallContext)


# ── unavailable is never "no calls" ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_credentials_raise_rather_than_return_empty() -> None:
    """An empty list here would read as "this district has been quiet"."""
    client = GongMetadataClient("", "")

    with pytest.raises(GongUnavailableError) as excinfo:
        await client.recent_calls_for_account("Anywhere")

    assert "NOT a report of zero" in str(excinfo.value)


def test_pagination_uses_a_top_level_cursor() -> None:
    """Inside `filter` the cursor is accepted, ignored, and returns page one forever.

    Searching 180 days without paging looked at the first 100 calls only. Grosse
    Pointe returned zero linked calls that way and three once paged, so the bug
    read as a quiet account.
    """
    import inspect

    from artemis.integrations.gong.client import GongMetadataClient as C

    src = inspect.getsource(C.recent_calls_for_account)
    assert 'body["cursor"] = cursor' in src, "cursor must be set at the top level of the body"
    assert '"filter": {"fromDateTime"' in src, "and filter must carry only the date range"
