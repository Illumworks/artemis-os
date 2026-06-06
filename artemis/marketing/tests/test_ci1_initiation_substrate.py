"""CI1 — initiation substrate + deterministic grouping tests."""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.initiation_schemas import TargetScope
from artemis.marketing.models import DeliverableType, District, SignalQueue
from artemis.marketing.repository import (
    cluster_or_create_candidate,
    create_signal,
    get_candidate_signals,
    get_cluster_window_days,
    initiate_campaign,
    list_deliverable_types,
)


async def _make_district(session: AsyncSession, *, name: str = "Fort Bend ISD") -> District:
    district = District(
        name=name,
        state="TX",
        enrollment=20000,
        tier="D2",
        supported=True,
        on_skip_list=False,
        classification_source="manual",
    )
    session.add(district)
    await session.flush()
    await session.refresh(district)
    return district


async def _make_signal(
    session: AsyncSession,
    *,
    headline: str,
    district_id: int | None,
    campaign_family: str = "obc",
) -> SignalQueue:
    return await create_signal(
        session,
        headline=headline,
        campaign_family=campaign_family,
        source_type="manual",
        summary="",
        urgency_tier="standard",
        discovered_by="manual",
        reason_codes=[],
        resolved_district_id=district_id,
        state="TX",
    )


@pytest.mark.asyncio
async def test_deliverable_type_seed_and_registry_listing(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        select(DeliverableType).order_by(DeliverableType.display_order)
    )
    seeded = list(result.scalars().all())
    assert [row.slug for row in seeded] == [
        "outreach_email",
        "social",
        "long_form",
        "landing_page",
    ]
    assert [row.active for row in seeded] == [True, False, False, False]
    assert [row.default_enabled for row in seeded] == [True, False, False, False]

    active_only = await list_deliverable_types(db_session, active_only=True)
    all_types = await list_deliverable_types(db_session, active_only=False)
    assert len(active_only) == 1
    assert active_only[0].slug == "outreach_email"
    assert len(all_types) == 4
    assert await get_cluster_window_days(db_session) == 90


@pytest.mark.asyncio
async def test_cluster_or_create_creates_first_candidate(db_session: AsyncSession) -> None:
    async with db_session.begin():
        district = await _make_district(db_session)
        signal = await _make_signal(
            db_session,
            headline="Superintendent transition",
            district_id=district.id,
        )
        candidate = await cluster_or_create_candidate(db_session, signal)
        links = await get_candidate_signals(db_session, candidate.id)

    assert candidate.source_signal_id == signal.id
    assert candidate.campaign_family == "obc"
    assert candidate.predecessor_id is None
    assert len(links) == 1
    assert links[0].signal_id == signal.id
    assert links[0].is_primary is True


@pytest.mark.asyncio
async def test_cluster_or_create_attaches_second_signal_to_open_candidate(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        district = await _make_district(db_session)
        first = await _make_signal(
            db_session,
            headline="Program launch",
            district_id=district.id,
        )
        candidate = await cluster_or_create_candidate(db_session, first)
        second = await _make_signal(
            db_session,
            headline="Board vote",
            district_id=district.id,
        )
        same_candidate = await cluster_or_create_candidate(db_session, second)
        links = await get_candidate_signals(db_session, candidate.id)

    assert same_candidate.id == candidate.id
    assert [link.signal_id for link in links] == [first.id, second.id]
    assert [link.is_primary for link in links] == [True, False]


@pytest.mark.asyncio
async def test_cluster_or_create_creates_fresh_candidate_with_lineage_after_initiation(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        district = await _make_district(db_session)
        first = await _make_signal(
            db_session,
            headline="Initial corroboration",
            district_id=district.id,
        )
        original = await cluster_or_create_candidate(db_session, first)
        await initiate_campaign(
            db_session,
            original.id,
            name="Fort Bend OBC Outreach",
            objective="Open the new campaign",
            owner_user_id=7,
            target_scope={"mode": "states", "states": ["TX"]},
            deliverable_type_slugs=["outreach_email"],
            initiated_by=42,
        )

        second = await _make_signal(
            db_session,
            headline="Fresh corroboration after initiation",
            district_id=district.id,
        )
        successor = await cluster_or_create_candidate(db_session, second)
        links = await get_candidate_signals(db_session, successor.id)

    assert successor.id != original.id
    assert successor.predecessor_id == original.id
    assert successor.source_signal_id == second.id
    assert len(links) == 1
    assert links[0].signal_id == second.id
    assert links[0].is_primary is True


@pytest.mark.asyncio
async def test_null_resolved_district_always_creates_standalone_candidate(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        first = await _make_signal(
            db_session,
            headline="Statewide trend 1",
            district_id=None,
        )
        second = await _make_signal(
            db_session,
            headline="Statewide trend 2",
            district_id=None,
        )
        first_candidate = await cluster_or_create_candidate(db_session, first)
        second_candidate = await cluster_or_create_candidate(db_session, second)

    assert first_candidate.id != second_candidate.id
    assert first_candidate.predecessor_id is None
    assert second_candidate.predecessor_id is None


@pytest.mark.asyncio
async def test_initiate_campaign_sets_fields_and_enforces_slug_and_idempotency(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        district = await _make_district(db_session)
        signal = await _make_signal(
            db_session,
            headline="Candidate for initiation",
            district_id=district.id,
        )
        candidate = await cluster_or_create_candidate(db_session, signal)
        initiated = await initiate_campaign(
            db_session,
            candidate.id,
            name="Texas OBC Summer Push",
            objective="Reach every D1-D3 district in Texas",
            owner_user_id=99,
            target_scope={"mode": "district_tier", "tiers": ["D1", "D2", "D3"]},
            deliverable_type_slugs=["outreach_email"],
            initiated_by=501,
        )

        assert initiated.name == "Texas OBC Summer Push"
        assert initiated.objective == "Reach every D1-D3 district in Texas"
        assert initiated.owner_user_id == 99
        # exclude_none=True storage: only non-null fields are persisted
        assert initiated.target_scope_json == {
            "mode": "district_tier",
            "tiers": ["D1", "D2", "D3"],
        }
        assert initiated.deliverable_types_json == ["outreach_email"]
        assert initiated.initiated_by == 501
        assert initiated.initiated_at is not None
        assert initiated.initiated_at.tzinfo is UTC

        invalid_signal = await _make_signal(
            db_session,
            headline="Invalid deliverable candidate",
            district_id=district.id,
            campaign_family="biliteracy",
        )
        invalid_candidate = await cluster_or_create_candidate(db_session, invalid_signal)
        with pytest.raises(ValueError, match="Invalid: social"):
            await initiate_campaign(
                db_session,
                invalid_candidate.id,
                name="Invalid deliverable test",
                objective="Should fail",
                owner_user_id=99,
                target_scope={"mode": "states", "states": ["TX"]},
                deliverable_type_slugs=["social"],
                initiated_by=501,
            )

        with pytest.raises(ValueError, match="already initiated"):
            await initiate_campaign(
                db_session,
                candidate.id,
                name="Second initiation",
                objective="Should fail",
                owner_user_id=99,
                target_scope={"mode": "states", "states": ["TX"]},
                deliverable_type_slugs=["outreach_email"],
                initiated_by=501,
            )


@pytest.mark.asyncio
async def test_initiate_campaign_rejects_rejected_candidates(db_session: AsyncSession) -> None:
    async with db_session.begin():
        district = await _make_district(db_session)
        signal = await _make_signal(
            db_session,
            headline="Rejected candidate",
            district_id=district.id,
        )
        candidate = await cluster_or_create_candidate(db_session, signal)
        candidate.decision_state = "rejected"

        with pytest.raises(ValueError, match="is rejected and cannot be initiated"):
            await initiate_campaign(
                db_session,
                candidate.id,
                name="Rejected candidate test",
                objective="Should fail",
                owner_user_id=99,
                target_scope={"mode": "states", "states": ["TX"]},
                deliverable_type_slugs=["outreach_email"],
                initiated_by=501,
            )


def test_target_scope_accepts_each_valid_mode() -> None:
    assert TargetScope.model_validate({"mode": "all_districts"}).mode == "all_districts"
    assert TargetScope.model_validate({"mode": "states", "states": ["fl", "TX"]}).states == [
        "FL",
        "TX",
    ]
    assert TargetScope.model_validate(
        {"mode": "district_tier", "tiers": ["d1", "D2", "D3"]}
    ).tiers == ["D1", "D2", "D3"]
    assert TargetScope.model_validate(
        {"mode": "named_districts", "district_ids": [1, 2, 3]}
    ).district_ids == [1, 2, 3]


def test_target_scope_rejects_invalid_state_and_tier_with_self_teaching_messages() -> None:
    with pytest.raises(ValidationError, match="Unknown state code\\(s\\): XX"):
        TargetScope.model_validate({"mode": "states", "states": ["XX"]})
    with pytest.raises(ValidationError, match="Invalid tier\\(s\\): D9"):
        TargetScope.model_validate({"mode": "district_tier", "tiers": ["D9"]})
