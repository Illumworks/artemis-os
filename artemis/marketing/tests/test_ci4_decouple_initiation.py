"""CI4 — decoupled campaign initiation tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.marketing.models import District, SignalQueue
from artemis.marketing.repository import (
    cluster_or_create_candidate,
    create_campaign_brief,
    create_signal,
    get_candidate,
    initiate_campaign,
    save_initiation_proposal,
)
from artemis.pipelines import repository as pipeline_repo
from artemis.pipelines.models import PipelineRun
from artemis.pipelines.node_executors.agent_executor import (
    _deliverable_enabled_for_run,
    _resolve_candidate_for_run,
    execute_agent_node,
)
from artemis.pipelines.seeds.marketing_pipeline import (
    AGENT_IDS,
    CAMPAIGN_DELIVERABLES_PIPELINE_ID,
    seed_marketing_pipeline,
)

pytestmark = pytest.mark.asyncio


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


async def _make_pipeline_run(session: AsyncSession, *, suffix: str) -> str:
    pipeline = await pipeline_repo.create_pipeline(
        session,
        name=f"Discovery {suffix}",
        nodes=[],
        edges=[],
    )
    run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="succeeded",
        trigger="manual",
        triggered_by="test",
    )
    return run.id


async def _seed_marketing_pipelines(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO agents (agent_id, name, tools, model, provider) "
            "VALUES (:agent_id, :agent_id, '[]'::jsonb, 'claude-haiku-4-5', 'claude-code') "
            "ON CONFLICT (agent_id) DO NOTHING"
        ),
        [{"agent_id": agent_id} for agent_id in AGENT_IDS],
    )
    await session.commit()
    await seed_marketing_pipeline(session)


async def _make_signal(
    session: AsyncSession,
    *,
    headline: str,
    district_id: int,
    pipeline_run_id: str | None = None,
) -> SignalQueue:
    return await create_signal(
        session,
        headline=headline,
        campaign_family="obc",
        source_type="manual",
        summary=f"Summary for {headline}",
        urgency_tier="standard",
        discovered_by="manual",
        reason_codes=["DISTRICT_SIGNAL"],
        resolved_district_id=district_id,
        state="TX",
        pipeline_run_id=pipeline_run_id,
    )


async def _seed_candidate(
    session: AsyncSession,
    *,
    pipeline_run_ids: list[str | None],
    with_proposal: bool = True,
    with_brief: bool = True,
) -> int:
    district = await _make_district(session)
    candidate_id: int | None = None
    for index, run_id in enumerate(pipeline_run_ids, start=1):
        signal = await _make_signal(
            session,
            headline=f"Signal {index}",
            district_id=district.id,
            pipeline_run_id=run_id,
        )
        candidate = await cluster_or_create_candidate(session, signal)
        candidate_id = candidate.id
    assert candidate_id is not None
    if with_proposal:
        await save_initiation_proposal(
            session,
            candidate_id,
            {
                "name": "Fort Bend Follow-Up",
                "objective": "Build the next district campaign from the signal cluster.",
                "recommended_deliverable_types": ["outreach_email"],
                "target_scope": {"mode": "states", "states": ["TX"]},
                "rationale": "Two corroborating district signals support one campaign.",
            },
        )
    if with_brief:
        await create_campaign_brief(
            session,
            candidate_id,
            {"summary": "Seeded candidate brief"},
            generated_by="test",
        )
    await session.commit()
    return candidate_id


def _payload() -> dict[str, object]:
    return {
        "name": "Fort Bend Follow-Up",
        "objective": "Build the next district campaign from the signal cluster.",
        "owner_user_id": 7,
        "deliverable_type_slugs": ["outreach_email"],
        "target_scope": {"mode": "states", "states": ["TX"]},
    }


async def test_confirm_with_no_paused_gate_succeeds_and_creates_deliverables_run(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_marketing_pipelines(db_session)
    candidate_id = await _seed_candidate(db_session, pipeline_run_ids=[None], with_proposal=True)

    with patch("artemis.marketing.routes.initiation._dispatch_execution", new=MagicMock()):
        response = await client.post(
            f"/api/marketing/campaigns/{candidate_id}/initiate",
            json=_payload(),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    candidate = await get_candidate(db_session, candidate_id)
    run = await pipeline_repo.get_pipeline_run(db_session, body["deliverableRunId"])
    assert candidate.initiated_at is not None
    assert run.pipeline_id == CAMPAIGN_DELIVERABLES_PIPELINE_ID
    assert run.target_candidate_id == candidate_id


async def test_cross_run_candidate_confirms_without_resuming_any_discovery_run(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_marketing_pipelines(db_session)
    first_run = await _make_pipeline_run(db_session, suffix="A")
    second_run = await _make_pipeline_run(db_session, suffix="B")
    candidate_id = await _seed_candidate(
        db_session,
        pipeline_run_ids=[first_run, second_run],
        with_proposal=True,
    )

    with patch("artemis.marketing.routes.initiation._dispatch_execution", new=MagicMock()):
        response = await client.post(
            f"/api/marketing/campaigns/{candidate_id}/initiate",
            json=_payload(),
        )

    assert response.status_code == 200, response.text
    run = await pipeline_repo.get_pipeline_run(db_session, response.json()["deliverableRunId"])
    assert run.target_candidate_id == candidate_id


async def test_deliverable_node_resolves_candidate_via_target_candidate_id(
    db_session: AsyncSession,
) -> None:
    candidate_id = await _seed_candidate(db_session, pipeline_run_ids=[None], with_proposal=True)
    await initiate_campaign(
        db_session,
        candidate_id,
        name="Fort Bend Follow-Up",
        objective="Build the next district campaign from the signal cluster.",
        owner_user_id=7,
        target_scope={"mode": "states", "states": ["TX"]},
        deliverable_type_slugs=["outreach_email"],
        initiated_by=7,
    )
    run = await pipeline_repo.create_pipeline_run(
        db_session,
        pipeline_id=CAMPAIGN_DELIVERABLES_PIPELINE_ID,
        status="queued",
        trigger="manual",
        triggered_by="test",
        target_candidate_id=candidate_id,
    )
    await db_session.commit()

    resolved = await _resolve_candidate_for_run(db_session, run.id, initiated_only=True)
    assert resolved is not None and resolved.id == candidate_id
    assert await _deliverable_enabled_for_run(db_session, run.id, "outreach_email") is True
    assert await _deliverable_enabled_for_run(db_session, run.id, "social") is False


async def test_writing_studio_adapter_fails_fast_without_target_candidate_brief(
    db_session: AsyncSession,
) -> None:
    candidate_id = await _seed_candidate(
        db_session,
        pipeline_run_ids=[None],
        with_proposal=True,
        with_brief=False,
    )
    await initiate_campaign(
        db_session,
        candidate_id,
        name="Fort Bend Follow-Up",
        objective="Build the next district campaign from the signal cluster.",
        owner_user_id=7,
        target_scope={"mode": "states", "states": ["TX"]},
        deliverable_type_slugs=["outreach_email"],
        initiated_by=7,
    )
    run = await pipeline_repo.create_pipeline_run(
        db_session,
        pipeline_id=CAMPAIGN_DELIVERABLES_PIPELINE_ID,
        status="queued",
        trigger="manual",
        triggered_by="test",
        target_candidate_id=candidate_id,
    )
    await db_session.commit()

    result = await execute_agent_node(
        node={
            "id": "content_writing_studio_adapter",
            "type": "agent_invocation",
            "config": {"agent_id": "marketing.content.writing_studio_adapter", "mode": "manual"},
        },
        node_states={},
        session=db_session,
        run_id=run.id,
    )

    assert result["status"] == "failed"
    assert "has no campaign brief" in (result.get("error") or "")


async def test_second_confirm_is_clean_409_and_does_not_create_second_run(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_marketing_pipelines(db_session)
    candidate_id = await _seed_candidate(db_session, pipeline_run_ids=[None], with_proposal=True)

    with patch("artemis.marketing.routes.initiation._dispatch_execution", new=MagicMock()):
        first = await client.post(
            f"/api/marketing/campaigns/{candidate_id}/initiate", json=_payload()
        )
    assert first.status_code == 200, first.text

    second = await client.post(f"/api/marketing/campaigns/{candidate_id}/initiate", json=_payload())
    assert second.status_code == 409, second.text

    runs = (
        (
            await db_session.execute(
                select(PipelineRun).where(PipelineRun.target_candidate_id == candidate_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 1


async def test_lazy_proposal_generation_persists_when_context_is_requested(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    candidate_id = await _seed_candidate(db_session, pipeline_run_ids=[None], with_proposal=False)
    adapter = FakeAdapter(
        [
            ScriptedReply(
                text=json.dumps(
                    {
                        "name": "Fort Bend Follow-Up",
                        "objective": "Build the next district campaign from the signal cluster.",
                        "recommended_deliverable_types": ["outreach_email"],
                        "target_scope": {"mode": "states", "states": ["TX"]},
                        "rationale": "Generated lazily for a cross-run candidate.",
                    }
                )
            )
        ]
    )

    with patch("artemis.providers.resolver.resolve_adapter", return_value=adapter):
        response = await client.get(f"/api/marketing/campaigns/{candidate_id}/initiation-context")

    assert response.status_code == 200, response.text
    body = response.json()
    refreshed = await get_candidate(db_session, candidate_id)
    assert body["proposal"]["name"] == "Fort Bend Follow-Up"
    assert isinstance(refreshed.initiation_proposal_json, dict)
    assert refreshed.initiation_proposal_json["name"] == "Fort Bend Follow-Up"


async def test_legacy_signal_join_fallback_still_resolves_candidate_for_deliverables(
    db_session: AsyncSession,
) -> None:
    run_id = await _make_pipeline_run(db_session, suffix="fallback")
    candidate_id = await _seed_candidate(db_session, pipeline_run_ids=[run_id], with_proposal=True)
    await initiate_campaign(
        db_session,
        candidate_id,
        name="Fort Bend Follow-Up",
        objective="Build the next district campaign from the signal cluster.",
        owner_user_id=7,
        target_scope={"mode": "states", "states": ["TX"]},
        deliverable_type_slugs=["outreach_email"],
        initiated_by=7,
    )
    await db_session.commit()

    resolved = await _resolve_candidate_for_run(db_session, run_id, initiated_only=True)
    assert resolved is not None and resolved.id == candidate_id
    assert await _deliverable_enabled_for_run(db_session, run_id, "outreach_email") is True
