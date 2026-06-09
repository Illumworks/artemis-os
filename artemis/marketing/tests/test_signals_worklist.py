from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignCandidateSignal, District, SignalQueue

pytestmark = pytest.mark.asyncio

_TRUNCATE = text(
    "TRUNCATE signal_worklist_overrides, campaign_candidate_signals, campaign_candidates, "
    "approvals, signal_queue, district_tier_bands, districts RESTART IDENTITY CASCADE"
)


async def _reset(session: AsyncSession) -> None:
    await session.execute(_TRUNCATE)
    await session.commit()


async def _seed_district(session: AsyncSession, *, name: str, state: str, nces: str) -> District:
    district = District(
        nces_id=nces,
        name=name,
        state=state,
        enrollment=12000,
        tier="D1",
        supported=True,
        on_skip_list=False,
        classification_source="manual",
        classified_at=datetime.now(UTC),
    )
    session.add(district)
    await session.flush()
    await session.refresh(district)
    return district


async def _seed_signal(
    session: AsyncSession,
    *,
    headline: str,
    district: District,
    status: str = "qualified",
    family: str = "obc",
    urgency: str = "hot",
) -> SignalQueue:
    signal = SignalQueue(
        headline=headline,
        summary=f"{headline} summary",
        campaign_family=family,
        urgency_tier=urgency,
        discovered_by="test",
        district_id=district.name,
        resolved_district_id=district.id,
        state=district.state,
        reason_codes=[{"code": "district_opportunity"}],
        signal_status=status,
    )
    session.add(signal)
    await session.flush()
    await session.refresh(signal)
    return signal


async def test_worklist_merge_groups_cards_losslessly(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _reset(db_session)
    async with db_session.begin():
        fort_worth = await _seed_district(
            db_session, name="Fort Worth ISD", state="TX", nces="111111111111"
        )
        redlands = await _seed_district(
            db_session, name="Redlands USD", state="CA", nces="222222222222"
        )
        sig_a = await _seed_signal(
            db_session, headline="Fort Worth board move", district=fort_worth
        )
        sig_b = await _seed_signal(
            db_session, headline="Redlands strategic plan", district=redlands
        )

    response = await client.post(
        "/api/signal-queue/worklist/merge",
        json={"signalIds": [sig_b.id], "targetClusterKey": f"{fort_worth.id}|obc"},
    )
    assert response.status_code == 200, response.text

    worklist = await client.get("/api/signal-queue/worklist")
    assert worklist.status_code == 200, worklist.text
    cards = worklist.json()["cards"]
    assert len(cards) == 1
    assert cards[0]["signalCount"] == 2
    assert set(cards[0]["signalIds"]) == {sig_a.id, sig_b.id}

    browse_all = await client.get("/api/signal-queue")
    assert browse_all.status_code == 200, browse_all.text
    returned_ids = {row["id"] for row in browse_all.json()["signals"]}
    assert {sig_a.id, sig_b.id}.issubset(returned_ids)


async def test_worklist_remove_hides_card_but_signal_stays_browseable(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _reset(db_session)
    async with db_session.begin():
        district = await _seed_district(
            db_session, name="Klein ISD", state="TX", nces="333333333333"
        )
        signal = await _seed_signal(
            db_session, headline="Klein superintendent op-ed", district=district
        )

    response = await client.post(
        "/api/signal-queue/worklist/remove",
        json={"signalId": signal.id},
    )
    assert response.status_code == 200, response.text

    worklist = await client.get("/api/signal-queue/worklist")
    assert worklist.status_code == 200, worklist.text
    assert worklist.json()["cards"] == []

    browse_all = await client.get("/api/signal-queue")
    assert browse_all.status_code == 200, browse_all.text
    rows = browse_all.json()["signals"]
    assert any(row["id"] == signal.id for row in rows)


async def test_worklist_promote_creates_one_candidate_for_cluster(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _reset(db_session)
    async with db_session.begin():
        district = await _seed_district(
            db_session, name="Fort Bend ISD", state="TX", nces="444444444444"
        )
        sig_a = await _seed_signal(db_session, headline="Fort Bend board note", district=district)
        sig_b = await _seed_signal(db_session, headline="Fort Bend RFP", district=district)
    sig_a_id = sig_a.id
    sig_b_id = sig_b.id

    response = await client.post(
        "/api/signal-queue/clusters/promote",
        json={"signalIds": [sig_a_id, sig_b_id], "title": "Fort Bend ISD"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["candidateId"] is not None
    assert set(payload["linkedSignalIds"]) == {sig_a_id, sig_b_id}

    links = (
        (
            await db_session.execute(
                select(CampaignCandidateSignal).where(
                    CampaignCandidateSignal.candidate_id == payload["candidateId"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert {link.signal_id for link in links} == {sig_a_id, sig_b_id}

    db_session.expire_all()
    refreshed_a = await db_session.get(SignalQueue, sig_a_id)
    refreshed_b = await db_session.get(SignalQueue, sig_b_id)
    assert refreshed_a is not None and refreshed_a.signal_status == "approved"
    assert refreshed_b is not None and refreshed_b.signal_status == "approved"


async def test_freeform_cluster_rejects_mixed_campaign_families(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _reset(db_session)
    async with db_session.begin():
        district = await _seed_district(
            db_session, name="Humble ISD", state="TX", nces="666666666666"
        )
        sig_a = await _seed_signal(
            db_session, headline="Humble board note", district=district, family="obc"
        )
        sig_b = await _seed_signal(
            db_session, headline="Humble biliteracy note", district=district, family="biliteracy"
        )

    response = await client.post(
        "/api/signal-queue/clusters/promote",
        json={"signalIds": [sig_a.id, sig_b.id], "title": "Mixed families"},
    )
    assert response.status_code == 400, response.text
    assert "campaign_family" in response.text


async def test_browse_all_surfaces_campaign_trace_for_converted_signals(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _reset(db_session)
    async with db_session.begin():
        district = await _seed_district(
            db_session, name="Chicago Public Schools", state="IL", nces="555555555555"
        )
        sig_a = await _seed_signal(db_session, headline="CPS literacy push", district=district)
        sig_b = await _seed_signal(db_session, headline="CPS board urgency", district=district)

    promote = await client.post(
        "/api/signal-queue/worklist/promote",
        json={"signalIds": [sig_a.id, sig_b.id], "title": "CPS literacy push"},
    )
    assert promote.status_code == 200, promote.text
    candidate_id = promote.json()["candidateId"]

    browse_all = await client.get("/api/signal-queue?status=approved")
    assert browse_all.status_code == 200, browse_all.text
    rows = browse_all.json()["signals"]
    approved = [row for row in rows if row["id"] in {sig_a.id, sig_b.id}]
    assert len(approved) == 2
    assert all(row["campaignCandidateId"] == candidate_id for row in approved)
