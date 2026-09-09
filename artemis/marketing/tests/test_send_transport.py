"""The seam where a send would leave the building, and what it may claim.

`mark_send_sent` wrote `status='sent'` with a log line reading "NO REAL EMAIL"
for the system's whole life. Zero sends exist, so nothing was mis-delivered —
but `sent` is what every other surface reads, and none of them read the note
underneath admitting the truth.
"""

from __future__ import annotations

import pytest

from artemis.marketing.transport import (
    DryRunTransport,
    NullTransport,
    OutboundMessage,
    TransportResult,
    resolve_transport,
)


def _msg(n: int = 3) -> OutboundMessage:
    return OutboundMessage(
        recipients=[f"person{i}@district.k12.us" for i in range(n)],
        subject="A subject",
        body="A body",
        deliverable_id=1,
    )


# ── what a result is allowed to claim ────────────────────────────────────────


def test_only_a_delivered_result_may_be_called_sent() -> None:
    assert TransportResult("x", delivered=True, detail="").status == "sent"
    assert TransportResult("x", delivered=False, detail="").status == "simulated"


def test_simulated_is_not_failed() -> None:
    """Nothing went wrong and nothing went out. Those are different states and
    collapsing them would make a quiet rehearsal look like an incident."""
    assert TransportResult("dry_run", delivered=False, detail="").status != "failed"


# ── the transports ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_dry_run_delivers_nothing_and_says_what_it_would_have_sent() -> None:
    """The artefact nobody has ever seen: the actual recipient list and copy."""
    result = await DryRunTransport().send(_msg(3))

    assert result.delivered is False
    assert result.status == "simulated"
    assert result.rendered["recipient_count"] == 3
    assert result.rendered["subject"] == "A subject"
    assert len(result.rendered["recipients"]) == 3


@pytest.mark.asyncio
async def test_no_configured_provider_is_distinct_from_a_rehearsal() -> None:
    """ "Nobody has chosen an ESP" and "we deliberately rehearsed" read very
    differently in a log six months from now."""
    result = await NullTransport().send(_msg())

    assert result.delivered is False
    assert result.transport == "none"
    assert "No email provider is configured" in result.detail


# ── resolution never falls through to sending ────────────────────────────────


def test_the_default_is_a_dry_run() -> None:
    assert resolve_transport().name == "dry_run"


def test_an_unknown_transport_name_resolves_to_no_send() -> None:
    """A typo in configuration must not reach a provider, and must not take the
    pipeline down either."""
    assert resolve_transport("brevoo").name == "none"
    assert resolve_transport("").name == "dry_run"


def test_no_real_provider_is_wired_yet() -> None:
    """Deliberate. HubSpot terminates around October 2026 and its replacement is
    an open business decision; choosing one here would make it in a commit.

    When one is added this test should be updated in the same change, so nobody
    can wire a provider without reading why there wasn't one.
    """
    from artemis.marketing.transport import _TRANSPORTS

    assert set(_TRANSPORTS) == {"dry_run", "none"}


@pytest.mark.asyncio
async def test_a_send_the_transport_did_not_deliver_is_not_recorded_as_sent(db_session) -> None:
    """The whole point, asserted against the database."""
    from artemis.marketing.models import CampaignDeliverable, CampaignSend
    from artemis.marketing.sends import mark_send_sent

    from artemis.marketing.repository import (
        create_campaign_candidate_from_signal,
        create_signal,
    )

    signal = await create_signal(
        db_session,
        headline="Seed for a send",
        campaign_family="test_family",
        source_type="manual",
        summary="seeded",
        discovered_by="manual",
    )
    candidate = await create_campaign_candidate_from_signal(
        db_session, signal_id=signal.id, ruleset_version_tag="v1"
    )
    deliverable = CampaignDeliverable(candidate_id=candidate.id, status="queued_for_send")
    db_session.add(deliverable)
    await db_session.flush()

    send = CampaignSend(
        candidate_id=candidate.id,
        deliverable_id=deliverable.id,
        status="queued",
        recipients=["a@b.k12.us"],
        transport="stub",
    )
    db_session.add(send)
    await db_session.flush()

    updated = await mark_send_sent(db_session, send_id=send.id, actor="tester")

    assert updated.status == "simulated", "a dry run must never read as sent"
    assert updated.sent_at is None, "nothing was sent, so there is no sent_at"
    assert updated.transport_log["delivered"] is False
    assert "rendered" in updated.transport_log, "a dry run records what it would have sent"

    # And the deliverable must not claim it either.
    await db_session.refresh(deliverable)
    assert deliverable.status == "queued_for_send"
