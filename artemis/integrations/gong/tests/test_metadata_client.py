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

    # The paging moved into `_call_window` when the corpus was cached; the trap is
    # the same one and this follows it rather than assuming where it lives.
    src = inspect.getsource(C._call_window)
    assert 'body["cursor"] = cursor' in src, "cursor must be set at the top level of the body"
    assert '"filter": {"fromDateTime"' in src, "and filter must carry only the date range"


# ── context that arrives without being asked for ─────────────────────────────


@pytest.mark.asyncio
async def test_no_linked_calls_is_not_reported_as_never_contacted(monkeypatch) -> None:
    """2,582 imported calls carry no account link at all.

    "No linked calls" and "no contact" are different claims, and the second one
    would licence a cold opener to a district somebody has already worked.
    """
    from artemis.integrations.gong import client as mod

    async def _none(self, name, *, days=180, limit=5):  # noqa: ANN001, ARG001
        return []

    monkeypatch.setattr(mod.GongMetadataClient, "recent_calls_for_account", _none)

    summary = await mod.recent_contact_summary("Anywhere")

    assert summary is not None
    assert "no linked call" in summary.lower()
    assert "rather than no" in summary.lower()


@pytest.mark.asyncio
async def test_gong_being_down_is_unknown_not_cold(monkeypatch) -> None:
    """An outage must not read as "this district has never been contacted"."""
    from artemis.integrations.gong import client as mod

    async def _boom(self, name, *, days=180, limit=5):  # noqa: ANN001, ARG001
        raise mod.GongUnavailableError("gong down")

    monkeypatch.setattr(mod.GongMetadataClient, "recent_calls_for_account", _boom)

    summary = await mod.recent_contact_summary("Anywhere")

    assert summary is not None
    assert "UNKNOWN" in summary
    assert "do not describe this account as cold" in summary.lower()


@pytest.mark.asyncio
async def test_prior_contact_tells_the_agent_not_to_open_cold(monkeypatch) -> None:
    """The whole point. Callie was drafting cold outreach for Grosse Pointe while
    five calls sat in Gong from 29 April to 11 May, two firing Objections."""
    from artemis.integrations.gong import client as mod

    async def _calls(self, name, *, days=180, limit=5):  # noqa: ANN001, ARG001
        return [
            mod.CallContext(
                call_id="c1",
                started="2026-05-01T10:00:00Z",
                duration_seconds=120,
                title="Call",
                system="Gong Connect",
                account_name="A District",
                account_tier="D3",
                trackers={"Objections (tracker)": 1, "Pricing": 0},
                external_parties=1,
            )
        ]

    monkeypatch.setattr(mod.GongMetadataClient, "recent_calls_for_account", _calls)

    summary = await mod.recent_contact_summary("A District")

    assert summary is not None
    assert "Objections (tracker)" in summary
    assert "Do NOT write to them as a cold account" in summary
    assert "counts, not quotes" in summary, "it must not imply we know what was said"


@pytest.mark.asyncio
async def test_the_summary_carries_no_participant_names(monkeypatch) -> None:
    from artemis.integrations.gong import client as mod

    async def _calls(self, name, *, days=180, limit=5):  # noqa: ANN001, ARG001
        return [
            mod.CallContext(
                call_id="c1",
                started="2026-05-01T10:00:00Z",
                duration_seconds=60,
                title="Call",
                system="Zoom",
                account_name="A District",
                external_parties=2,
                internal_parties=1,
            )
        ]

    monkeypatch.setattr(mod.GongMetadataClient, "recent_calls_for_account", _calls)

    summary = await mod.recent_contact_summary("A District")

    assert summary is not None
    assert "2 external" in summary, "counts are the only party detail that survives"


def test_the_context_is_folded_into_the_pre_outreach_check() -> None:
    """It must arrive without being asked for, or it will not arrive at all.

    A separate tool depends on someone thinking to call it, and nobody thought to
    ask about Grosse Pointe. check_salesforce_activity is already required before
    drafting outreach, which makes it the right carrier for anything that should
    change a recommendation rather than answer a question.
    """
    import inspect

    from artemis.floating_artemis.tools import salesforce_tools

    src = inspect.getsource(salesforce_tools)
    assert "recent_contact_summary" in src
    assert "gong_lines" in src


@pytest.mark.asyncio
async def test_gong_failure_cannot_take_out_the_suppression_check(monkeypatch) -> None:
    """The Salesforce answer carries the do-not-contact check; it must survive.

    This used to slice `inspect.getsource` by character offset around
    "recent_contact_summary" and look for an `except` in the window — so adding a
    comment near the call broke it while the behaviour was untouched. Drive the
    failure instead: make the Gong call raise and assert the Salesforce content
    still comes back.
    """

    async def _boom(*_a: object, **_kw: object) -> str:
        raise RuntimeError("gong down")

    monkeypatch.setattr("artemis.integrations.gong.client.recent_contact_summary", _boom)

    from artemis.floating_artemis.tools.salesforce_tools import _check_salesforce_activity

    out = await _check_salesforce_activity({"district_name": "Pinellas County Schools"})

    assert "Salesforce" in out
    assert "Pinellas" in out
    # And it must not have swallowed the district answer to report a Gong problem.
    assert "gong down" not in out.lower()


# ── the one-line form, for annotating someone else's message ─────────────────


@pytest.mark.asyncio
async def test_the_one_liner_is_silent_when_there_is_nothing_to_say(monkeypatch) -> None:
    """Deliberately the OPPOSITE of the full summary's behaviour.

    Here the line annotates someone else's message, so no calls means no
    annotation. In the full summary the same state must be stated out loud,
    because there a missing Gong section would read as "no prior contact".
    """
    from artemis.integrations.gong import client as mod

    async def _none(self, name, *, days=180, limit=5):  # noqa: ANN001, ARG001
        return []

    monkeypatch.setattr(mod.GongMetadataClient, "recent_calls_for_account", _none)

    assert await mod.one_line_contact("Anywhere") is None


@pytest.mark.asyncio
async def test_the_one_liner_is_silent_when_gong_is_down(monkeypatch) -> None:
    """An outage must not annotate a signal with a guess."""
    from artemis.integrations.gong import client as mod

    async def _boom(self, name, *, days=180, limit=5):  # noqa: ANN001, ARG001
        raise mod.GongUnavailableError("down")

    monkeypatch.setattr(mod.GongMetadataClient, "recent_calls_for_account", _boom)

    assert await mod.one_line_contact("Anywhere") is None


@pytest.mark.asyncio
async def test_the_one_liner_names_the_themes_and_says_not_cold(monkeypatch) -> None:
    from artemis.integrations.gong import client as mod

    async def _calls(self, name, *, days=180, limit=5):  # noqa: ANN001, ARG001
        return [
            mod.CallContext(
                call_id="c1",
                started="2026-05-11T10:00:00Z",
                duration_seconds=120,
                title="Call",
                system="Gong Connect",
                account_name="A District",
                trackers={"Objections (tracker)": 2},
                external_parties=1,
            )
        ]

    monkeypatch.setattr(mod.GongMetadataClient, "recent_calls_for_account", _calls)

    line = await mod.one_line_contact("A District")

    assert line is not None
    assert "2026-05-11" in line
    assert "Objections (tracker)" in line
    assert "Not a cold account" in line


def test_a_signal_push_carries_prior_contact() -> None:
    """ "They posted an RFP" and "they posted an RFP and we spoke to them twice in
    July" call for different next actions, and a Slack message cannot be asked a
    follow-up question."""
    from artemis.marketing.callie_push import _build_push_text

    text = _build_push_text(
        signal_id=1,
        headline="A district posted a screener RFP",
        district_id="A District",
        state="KS",
        campaign_family="obc",
        top_score=0.9,
        reason_codes=[{"code": "PROCUREMENT_LITERACY_RFP"}],
        app_base_url="",
        prior_contact="2 calls in the last 180 days, most recent 2026-07-14. Not a cold account.",
    )

    assert "*Prior contact:*" in text
    assert "Not a cold account" in text


def test_a_signal_push_without_prior_contact_says_nothing_about_it() -> None:
    """No line beats an empty line: absence is not evidence of a cold account."""
    from artemis.marketing.callie_push import _build_push_text

    text = _build_push_text(
        signal_id=1,
        headline="A district posted a screener RFP",
        district_id="A District",
        state="KS",
        campaign_family="obc",
        top_score=0.9,
        reason_codes=[],
        app_base_url="",
    )

    assert "Prior contact" not in text


# ── an empty account name asks about nothing (2026-09-09, live) ─────────────


@pytest.mark.asyncio
async def test_an_empty_account_name_matches_nothing_not_everything(monkeypatch) -> None:
    """`"" in anything` is True, so an empty name matched EVERY call in the
    corpus. A signal card in #campaign-signals told the channel an Indiana policy
    signal with no district attached had "5 calls ... Not a cold account" —
    five unrelated districts' calls, presented as this one's."""
    from artemis.integrations.gong.client import GongMetadataClient

    called = False

    async def _post(self, path, body):  # noqa: ANN001, ARG001
        nonlocal called
        called = True
        return {"calls": [], "records": {}}

    monkeypatch.setattr(GongMetadataClient, "_post", _post)

    client = GongMetadataClient("k", "s")

    assert await client.recent_calls_for_account("") == []
    assert await client.recent_calls_for_account("   ") == []
    assert called is False, "an empty name must not even reach Gong"


@pytest.mark.asyncio
async def test_the_one_liner_is_silent_for_an_empty_name(monkeypatch) -> None:
    from artemis.config import settings
    from artemis.integrations.gong.client import one_line_contact

    monkeypatch.setattr(settings, "gong_access_key", "k", raising=False)
    monkeypatch.setattr(settings, "gong_access_key_secret", "s", raising=False)

    assert await one_line_contact("") is None


def test_the_one_liner_counts_rather_than_reporting_its_fetch_cap() -> None:
    """The identical bug to `recent_contact_summary`, in its sibling, missed when
    that one was fixed. This form prints no per-call list, so the ONLY number it
    shows is the total."""
    import inspect

    from artemis.integrations.gong.client import _COUNT_CAP, one_line_contact

    src = inspect.getsource(one_line_contact)
    assert "limit=_COUNT_CAP" in src, "must not fetch 5 and call it the total"
    assert "_COUNT_CAP" in src and f"{_COUNT_CAP}" != "5"


# ── private calls (2026-09-10) ───────────────────────────────────────────────


def test_a_private_call_is_dropped() -> None:
    """Gong does NOT filter these server-side. A call marked private comes back
    like any other and its transcript stays fetchable; Gong requires connectors
    to drop them client-side and makes it a condition of app approval.

    Zero calls carry the flag today, which is why this never bit and why it
    needed writing before it did."""
    from artemis.integrations.gong.client import drop_private

    raw: list[dict[str, object]] = [
        {"metaData": {"id": "1", "isPrivate": False}},
        {"metaData": {"id": "2", "isPrivate": True}},
        {"metaData": {"id": "3"}},
    ]

    kept = [r["metaData"]["id"] for r in drop_private(raw)]

    assert kept == ["1", "3"], "a private call must not reach anything downstream"


def test_the_flag_survives_onto_the_context() -> None:
    """So any path that somehow bypasses `drop_private` can still see it."""
    from artemis.integrations.gong.client import _to_context

    assert _to_context({"metaData": {"id": "1", "isPrivate": True}}).is_private is True
    assert _to_context({"metaData": {"id": "2"}}).is_private is False


def test_the_log_line_names_no_call() -> None:
    """Which calls somebody marked private is itself something they did not
    offer us."""
    import inspect

    from artemis.integrations.gong import client

    src = inspect.getsource(client.drop_private)
    assert "dropped %d private call" in src
    assert "call_id" not in src.split('"""')[2], "no identifier in the log"


def test_the_window_cache_is_clearable() -> None:
    """A cache with no way to drop it is a debugging problem waiting to happen."""
    from artemis.integrations.gong.client import _window_cache, clear_call_window_cache

    _window_cache[999] = (0.0, [])
    clear_call_window_cache()

    assert _window_cache == {}
