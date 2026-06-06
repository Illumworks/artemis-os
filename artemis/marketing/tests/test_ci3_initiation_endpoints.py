"""CI3 — campaign initiation endpoint tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import CampaignDeliverable, District, SignalQueue
from artemis.marketing.repository import (
    cluster_or_create_candidate,
    create_campaign_brief,
    create_content_asset,
    create_signal,
    initiate_campaign,
    link_content_asset_to_candidate,
    save_initiation_proposal,
)
from artemis.pipelines import repository as pipeline_repo
from artemis.pipelines.seeds.marketing_pipeline import AGENT_IDS, seed_marketing_pipeline


async def _make_district(
    session: AsyncSession,
    *,
    name: str = "Fort Bend ISD",
    state: str = "TX",
    on_skip_list: bool = False,
) -> District:
    district = District(
        name=name,
        state=state,
        enrollment=20000,
        tier="D2",
        supported=True,
        on_skip_list=on_skip_list,
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
    pipeline_run_id: str | None = None,
    campaign_family: str = "obc",
) -> SignalQueue:
    signal = await create_signal(
        session,
        headline=headline,
        campaign_family=campaign_family,
        source_type="manual",
        summary=f"Summary for {headline}",
        urgency_tier="standard",
        discovered_by="manual",
        reason_codes=["DISTRICT_SIGNAL"],
        resolved_district_id=district_id,
        state="TX",
        pipeline_run_id=pipeline_run_id,
    )
    return signal


async def _make_gate_run(db_session: AsyncSession) -> str:
    await db_session.execute(
        text(
            "INSERT INTO agents (agent_id, name, tools, model, provider) "
            "VALUES (:agent_id, :agent_id, '[]'::jsonb, 'claude-haiku-4-5', 'claude-code') "
            "ON CONFLICT (agent_id) DO NOTHING"
        ),
        [{"agent_id": agent_id} for agent_id in AGENT_IDS],
    )
    await db_session.commit()
    await seed_marketing_pipeline(db_session)
    pipeline = await pipeline_repo.create_pipeline(
        db_session,
        name="CI3 Test Pipeline",
        nodes=[
            {
                "id": "gate_campaign_initiation",
                "type": "human_gate",
                "label": "Campaign Initiation Confirm",
                "config": {
                    "approval_kind": "campaign_initiation",
                    "approvers": ["josh@amiralearning.com"],
                    "timeout_hours": 72,
                },
                "position": {"x": 0.0, "y": 0.0},
            }
        ],
        edges=[],
    )
    run = await pipeline_repo.create_pipeline_run(
        db_session,
        pipeline_id=pipeline.id,
        status="awaiting_approval",
        trigger="manual",
        triggered_by="test",
    )
    await pipeline_repo.update_pipeline_run(
        db_session,
        run.id,
        node_states={
            "gate_campaign_initiation": {
                "status": "suspended",
                "cost_usd": 0.0,
                "output_summary": "Waiting for initiation confirmation",
            }
        },
    )
    return run.id


async def _seed_initiation_candidate(
    db_session: AsyncSession,
    *,
    district_name: str = "Fort Bend ISD",
    district_state: str = "TX",
    resolved_district: bool = True,
    run_id: str | None = None,
) -> int:
    district = await _make_district(db_session, name=district_name, state=district_state)
    first_signal = await _make_signal(
        db_session,
        headline="Superintendent transition",
        district_id=district.id if resolved_district else None,
        pipeline_run_id=run_id,
    )
    predecessor = await cluster_or_create_candidate(db_session, first_signal)
    await initiate_campaign(
        db_session,
        predecessor.id,
        name="Prior Fort Bend Outreach",
        objective="Continue the prior district outreach effort.",
        owner_user_id=7,
        target_scope={"mode": "states", "states": [district_state]},
        deliverable_type_slugs=["outreach_email"],
        initiated_by=7,
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
    deliverable = CampaignDeliverable(
        candidate_id=predecessor.id,
        deliverable_id="draft-1",
        campaign_id="campaign-1",
        status="generating",
        deliverable_metadata={"title": "Prior email draft"},
    )
    db_session.add(deliverable)
    await db_session.flush()

    second_signal = await _make_signal(
        db_session,
        headline="Board vote",
        district_id=district.id if resolved_district else None,
        pipeline_run_id=run_id,
    )
    successor = await cluster_or_create_candidate(db_session, second_signal)
    third_signal = await _make_signal(
        db_session,
        headline="Community concern",
        district_id=district.id if resolved_district else None,
        pipeline_run_id=run_id,
    )
    await cluster_or_create_candidate(db_session, third_signal)

    await save_initiation_proposal(
        db_session,
        successor.id,
        {
            "name": "Fort Bend Follow-Up",
            "objective": "Build the next district campaign from the signal cluster.",
            "recommended_deliverable_types": ["outreach_email"],
            "target_scope": {"mode": "states", "states": [district_state]},
            "rationale": "The cluster is strongest in the same district and family.",
        },
    )
    await create_campaign_brief(
        db_session,
        successor.id,
        {"summary": "Successor brief context"},
        generated_by="test",
    )

    return successor.id


@pytest.mark.asyncio
async def test_list_campaigns_returns_real_candidates_with_cluster_counts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    run_id = await _make_gate_run(db_session)
    candidate_id = await _seed_initiation_candidate(db_session, run_id=run_id)
    await db_session.commit()

    resp = await client.get("/api/marketing/campaigns")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    campaigns = data["campaigns"]
    assert len(campaigns) == 2

    initiated = next(item for item in campaigns if item["initiatedAt"] is not None)
    proposed = next(item for item in campaigns if item["initiatedAt"] is None)

    assert initiated["name"] == "Prior Fort Bend Outreach"
    assert initiated["family"] == "obc"
    assert initiated["signalClusterCount"] == 1
    assert proposed["id"] == candidate_id
    assert proposed["name"] == "Fort Bend Follow-Up"
    assert proposed["family"] == "obc"
    assert proposed["signalClusterCount"] == 2
    assert proposed["objective"] == "Build the next district campaign from the signal cluster."
    assert proposed["state"]
    assert proposed["primarySignalState"] == "TX"


@pytest.mark.asyncio
async def test_get_initiation_proposal_returns_proposal_cluster_registry_district_and_lineage(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    run_id = await _make_gate_run(db_session)
    candidate_id = await _seed_initiation_candidate(db_session, run_id=run_id)
    await db_session.commit()

    resp = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["candidateId"] == candidate_id
    assert data["initiatedAt"] is None
    assert data["proposal"]["name"] == "Fort Bend Follow-Up"
    assert len(data["signalCluster"]) == 2
    assert data["signalCluster"][0]["isPrimary"] is True
    assert data["districtContext"]["resolved"] is True
    assert data["districtContext"]["state"] == "TX"
    assert data["districtContext"]["enrollment"] == 20000
    assert data["districtContext"]["onSkipList"] is False
    assert data["districtContext"]["defaultTargetScope"] == {"base": "states", "states": ["TX"]}
    assert data["metricsJson"] == {}
    assert data["targetScopeCounts"]["byState"]["TX"] >= 1
    assert data["selectedTargetScopeCount"] >= 1
    assert data["signalCluster"][0]["whyFlagged"] == "Summary for Board vote"
    assert any(
        row["slug"] == "outreach_email" and row["active"] for row in data["deliverableRegistry"]
    )
    assert any(row["slug"] == "social" and not row["active"] for row in data["deliverableRegistry"])
    assert len(data["lineage"]) == 1
    assert data["lineage"][0]["latestBriefSummary"] == "Prior brief context"
    assert data["lineage"][0]["drafts"][0]["deliverable_id"] == "draft-1"
    assert data["lineage"][0]["linkedAssets"][0]["summary"] == "Prior collateral summary"


@pytest.mark.asyncio
async def test_post_initiate_creates_deliverables_run_for_candidate(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    run_id = await _make_gate_run(db_session)
    candidate_id = await _seed_initiation_candidate(db_session, run_id=run_id)
    await db_session.commit()

    payload = {
        "name": "Fort Bend Follow-Up",
        "objective": "Build the next district campaign from the signal cluster.",
        "owner_user_id": 7,
        "deliverable_type_slugs": ["outreach_email"],
        "target_scope": {"mode": "states", "states": ["TX"]},
    }

    with patch(
        "artemis.marketing.routes.initiation._dispatch_execution", new=MagicMock()
    ) as dispatch_mock:
        resp = await client.post(f"/api/marketing/campaigns/{candidate_id}/initiate", json=payload)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "Fort Bend Follow-Up"
    dispatch_mock.assert_called_once_with(data["deliverableRunId"])
    run = await pipeline_repo.get_pipeline_run(db_session, data["deliverableRunId"])
    assert run.pipeline_id == "marketing.campaign_deliverables"
    assert run.target_candidate_id == candidate_id

    reget = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert reget.status_code == 200
    assert reget.json()["initiatedAt"] is not None


@pytest.mark.asyncio
async def test_post_initiate_requires_skip_list_acknowledgment(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    run_id = await _make_gate_run(db_session)
    district = await _make_district(
        db_session,
        name="Skip List ISD",
        state="TX",
        on_skip_list=True,
    )
    first_signal = await _make_signal(
        db_session,
        headline="Skip list superintendent transition",
        district_id=district.id,
        pipeline_run_id=run_id,
    )
    candidate = await cluster_or_create_candidate(db_session, first_signal)
    await save_initiation_proposal(
        db_session,
        candidate.id,
        {
            "name": "Skip List Follow-Up",
            "objective": "Proceed intentionally despite the district flag.",
            "recommended_deliverable_types": ["outreach_email"],
            "target_scope": {"mode": "states", "states": ["TX"]},
            "rationale": "Operator review still wants the option available.",
        },
    )
    await create_campaign_brief(
        db_session,
        candidate.id,
        {"summary": "Skip-list brief context"},
        generated_by="test",
    )
    await db_session.commit()

    payload = {
        "name": "Skip List Follow-Up",
        "objective": "Proceed intentionally despite the district flag.",
        "owner_user_id": 7,
        "deliverable_type_slugs": ["outreach_email"],
        "target_scope": {"mode": "states", "states": ["TX"]},
    }
    resp = await client.post(f"/api/marketing/campaigns/{candidate.id}/initiate", json=payload)
    assert resp.status_code == 422
    assert "skip_list_acknowledged" in resp.text

    with patch("artemis.marketing.routes.initiation._dispatch_execution", new=MagicMock()):
        acked = await client.post(
            f"/api/marketing/campaigns/{candidate.id}/initiate",
            json={**payload, "skip_list_acknowledged": True},
        )
    assert acked.status_code == 200


@pytest.mark.asyncio
async def test_post_initiate_rejects_inactive_deliverable_slug(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    run_id = await _make_gate_run(db_session)
    candidate_id = await _seed_initiation_candidate(db_session, run_id=run_id)
    await db_session.commit()

    resp = await client.post(
        f"/api/marketing/campaigns/{candidate_id}/initiate",
        json={
            "name": "Fort Bend Follow-Up",
            "objective": "Build the next district campaign from the signal cluster.",
            "owner_user_id": 7,
            "deliverable_type_slugs": ["social"],
            "target_scope": {"mode": "states", "states": ["TX"]},
        },
    )
    assert resp.status_code == 422
    assert "Invalid: social" in resp.text or "active deliverable type slugs" in resp.text


@pytest.mark.asyncio
async def test_post_initiate_rejects_already_initiated_candidate(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    run_id = await _make_gate_run(db_session)
    candidate_id = await _seed_initiation_candidate(db_session, run_id=run_id)
    await db_session.commit()

    payload = {
        "name": "Fort Bend Follow-Up",
        "objective": "Build the next district campaign from the signal cluster.",
        "owner_user_id": 7,
        "deliverable_type_slugs": ["outreach_email"],
        "target_scope": {"mode": "states", "states": ["TX"]},
    }

    with patch("artemis.marketing.routes.initiation._dispatch_execution", new=MagicMock()):
        first = await client.post(f"/api/marketing/campaigns/{candidate_id}/initiate", json=payload)
    assert first.status_code == 200

    second = await client.post(f"/api/marketing/campaigns/{candidate_id}/initiate", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_post_initiate_rejects_missing_campaign_brief(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    run_id = await _make_gate_run(db_session)
    district = await _make_district(db_session)
    signal = await _make_signal(
        db_session,
        headline="Briefless candidate",
        district_id=district.id,
        pipeline_run_id=run_id,
    )
    candidate = await cluster_or_create_candidate(db_session, signal)
    await save_initiation_proposal(
        db_session,
        candidate.id,
        {
            "name": "Briefless Follow-Up",
            "objective": "Should be blocked before deliverables dispatch.",
            "recommended_deliverable_types": ["outreach_email"],
            "target_scope": {"mode": "states", "states": ["TX"]},
            "rationale": "No brief exists yet.",
        },
    )
    await db_session.commit()

    resp = await client.post(
        f"/api/marketing/campaigns/{candidate.id}/initiate",
        json={
            "name": "Briefless Follow-Up",
            "objective": "Should be blocked before deliverables dispatch.",
            "owner_user_id": 7,
            "deliverable_type_slugs": ["outreach_email"],
            "target_scope": {"mode": "states", "states": ["TX"]},
        },
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == "campaign_brief_missing"


@pytest.mark.asyncio
async def test_unresolved_district_candidate_defaults_to_all_districts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    run_id = await _make_gate_run(db_session)
    candidate_id = await _seed_initiation_candidate(
        db_session,
        district_name="Unresolved",
        district_state="TX",
        resolved_district=False,
        run_id=run_id,
    )
    await db_session.commit()

    resp = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-proposal")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["districtContext"]["resolved"] is False
    # Signal has state=TX → unresolved district defaults to all TX districts (not all 1903)
    assert data["districtContext"]["label"] == "All TX districts"
    assert data["districtContext"]["defaultTargetScope"] == {"base": "states", "states": ["TX"]}
