"""CI2 — initiation proposal + pipeline pause tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.marketing.brief_assembler import (
    build_campaign_initiation_context,
    propose_campaign_initiation,
)
from artemis.marketing.initiation_schemas import CampaignInitiationProposal, TargetScope
from artemis.marketing.models import District, SignalQueue
from artemis.marketing.repository import (
    cluster_or_create_candidate,
    create_campaign_brief,
    create_content_asset,
    create_signal,
    get_candidate,
    initiate_campaign,
    link_content_asset_to_candidate,
)
from artemis.pipelines import repository as pipeline_repo
from artemis.pipelines.node_executors.agent_executor import _deliverable_enabled_for_run


async def _make_district(
    session: AsyncSession,
    *,
    name: str = "Fort Bend ISD",
    state: str = "TX",
) -> District:
    district = District(
        name=name,
        state=state,
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
    summary: str = "District signal summary",
    pipeline_run_id: str | None = None,
) -> SignalQueue:
    return await create_signal(
        session,
        headline=headline,
        campaign_family="obc",
        source_type="manual",
        summary=summary,
        urgency_tier="standard",
        discovered_by="manual",
        reason_codes=["DISTRICT_SIGNAL"],
        resolved_district_id=district_id,
        state="TX",
        pipeline_run_id=pipeline_run_id,
    )


@pytest.mark.asyncio
async def test_campaign_initiation_proposal_accepts_valid_payload() -> None:
    proposal = CampaignInitiationProposal.validate_with_active_slugs(
        {
            "name": "Texas Outreach",
            "objective": "Launch a follow-up district outreach sequence.",
            "recommended_deliverable_types": ["outreach_email"],
            "target_scope": {"mode": "states", "states": ["TX"]},
            "rationale": "The cluster is strongest in Texas districts.",
        },
        ["outreach_email"],
    )
    assert proposal.recommended_deliverable_types == ["outreach_email"]


@pytest.mark.asyncio
async def test_campaign_initiation_proposal_rejects_inactive_deliverable_slug() -> None:
    with pytest.raises(ValidationError, match="Active: outreach_email"):
        CampaignInitiationProposal.validate_with_active_slugs(
            {
                "name": "Texas Outreach",
                "objective": "Launch a follow-up district outreach sequence.",
                "recommended_deliverable_types": ["social"],
                "target_scope": {"mode": "states", "states": ["TX"]},
            },
            ["outreach_email"],
        )


def test_campaign_initiation_proposal_rejects_invalid_target_scope() -> None:
    with pytest.raises(ValidationError, match="Unknown state code\\(s\\): XX"):
        CampaignInitiationProposal.validate_with_active_slugs(
            {
                "name": "Texas Outreach",
                "objective": "Launch a follow-up district outreach sequence.",
                "recommended_deliverable_types": ["outreach_email"],
                "target_scope": {"mode": "states", "states": ["XX"]},
            },
            ["outreach_email"],
        )


@pytest.mark.asyncio
async def test_brief_assembler_propose_mode_reads_full_cluster_and_does_not_auto_initiate(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        district = await _make_district(db_session)
        first = await _make_signal(
            db_session,
            headline="Superintendent transition",
            summary="The superintendent announced a literacy push.",
            district_id=district.id,
        )
        candidate = await cluster_or_create_candidate(db_session, first)
        second = await _make_signal(
            db_session,
            headline="Board vote for multilingual materials",
            summary="The board approved multilingual reading materials.",
            district_id=district.id,
        )
        await cluster_or_create_candidate(db_session, second)

        adapter = FakeAdapter(
            [
                ScriptedReply(
                    text=json.dumps(
                        {
                            "name": "Fort Bend Literacy Follow-Up",
                            "objective": "Build a timely outreach campaign from the corroborating district signals.",
                            "recommended_deliverable_types": ["outreach_email"],
                            "target_scope": {"mode": "states", "states": ["TX"]},
                            "rationale": "The superintendent and board signals reinforce the same district opportunity.",
                        }
                    )
                )
            ]
        )

        result = await propose_campaign_initiation(
            db_session,
            candidate.id,
            model_adapter=adapter,
        )

    refreshed = await get_candidate(db_session, candidate.id)
    assert result.proposal is not None
    assert len(result.context["signals"]) == 2
    persisted = refreshed.initiation_proposal_json
    assert isinstance(persisted, dict)
    assert persisted["name"] == "Fort Bend Literacy Follow-Up"
    assert refreshed.initiated_at is None


@pytest.mark.asyncio
async def test_predecessor_grounding_includes_name_objective_and_collateral(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        district = await _make_district(db_session)
        first = await _make_signal(
            db_session,
            headline="Prior campaign trigger",
            district_id=district.id,
        )
        predecessor = await cluster_or_create_candidate(db_session, first)
        await initiate_campaign(
            db_session,
            predecessor.id,
            name="Spring Outreach",
            objective="Re-engage Fort Bend literacy leaders.",
            owner_user_id=7,
            target_scope=TargetScope.model_validate({"mode": "states", "states": ["TX"]}),
            deliverable_type_slugs=["outreach_email"],
            initiated_by=42,
        )
        await create_campaign_brief(
            db_session,
            predecessor.id,
            {"summary": "Prior brief context"},
            generated_by="test",
        )
        asset = await create_content_asset(
            db_session,
            asset_type="snippet",
            summary="Prior collateral summary",
            metadata={"campaign_family": "obc"},
            status="approved",
        )
        await link_content_asset_to_candidate(db_session, predecessor.id, asset.id, "reference")

        second = await _make_signal(
            db_session,
            headline="Fresh corroboration",
            district_id=district.id,
        )
        successor = await cluster_or_create_candidate(db_session, second)
        context = await build_campaign_initiation_context(db_session, successor.id)

    assert context["predecessor"] is not None
    assert context["predecessor"]["name"] == "Spring Outreach"
    assert context["predecessor"]["objective"] == "Re-engage Fort Bend literacy leaders."
    assert context["predecessor"]["linked_assets"][0]["summary"] == "Prior collateral summary"


@pytest.mark.asyncio
async def test_old_shape_retry_path_leaves_no_silent_empty_proposal(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        district = await _make_district(db_session)
        signal = await _make_signal(
            db_session,
            headline="Retry path trigger",
            district_id=district.id,
        )
        candidate = await cluster_or_create_candidate(db_session, signal)
        adapter = FakeAdapter(
            [
                ScriptedReply(
                    text=json.dumps(
                        {
                            "objective": "Missing the required name field.",
                            "recommended_deliverable_types": ["outreach_email"],
                            "target_scope": {"mode": "states", "states": ["TX"]},
                        }
                    )
                ),
                ScriptedReply(
                    text=json.dumps(
                        {
                            "objective": "Still missing the required name field.",
                            "recommended_deliverable_types": ["outreach_email"],
                            "target_scope": {"mode": "states", "states": ["TX"]},
                        }
                    )
                ),
            ]
        )

        result = await propose_campaign_initiation(
            db_session,
            candidate.id,
            model_adapter=adapter,
        )

    refreshed = await get_candidate(db_session, candidate.id)
    assert result.proposal is None
    assert len(adapter.requests) == 2
    assert refreshed.initiation_proposal_json is None


@pytest.mark.asyncio
async def test_confirmed_mix_only_enables_confirmed_deliverable_types(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        pipeline = await pipeline_repo.create_pipeline(
            db_session,
            name="CI2 Test Pipeline",
            nodes=[],
            edges=[],
        )
        run = await pipeline_repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline.id,
            status="queued",
            trigger="manual",
            triggered_by="test",
        )
        district = await _make_district(db_session)
        signal = await _make_signal(
            db_session,
            headline="Confirmed mix trigger",
            district_id=district.id,
            pipeline_run_id=run.id,
        )
        candidate = await cluster_or_create_candidate(db_session, signal)
        await initiate_campaign(
            db_session,
            candidate.id,
            name="Confirmed outreach",
            objective="Confirm a single deliverable mix.",
            owner_user_id=7,
            target_scope={"mode": "states", "states": ["TX"]},
            deliverable_type_slugs=["outreach_email"],
            initiated_by=42,
        )

    assert await _deliverable_enabled_for_run(db_session, run.id, "outreach_email") is True
    assert await _deliverable_enabled_for_run(db_session, run.id, "social") is False
