from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import Approval, CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal
from artemis.marketing.state_machine import DeliverableState
from artemis.pipelines import repository as pipeline_repo
from artemis.pipelines.executor import PipelineExecutor

pytestmark = pytest.mark.asyncio

_PIPELINE_TRUNCATE = text("TRUNCATE pipeline_runs, pipelines RESTART IDENTITY CASCADE")


def _node(node_id: str, node_type: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": node_id,
        "config": config or {},
        "position": {"x": 0.0, "y": 0.0},
    }


def _edge(src: str, tgt: str) -> dict[str, Any]:
    return {
        "id": f"edge_{src}_{tgt}",
        "source_node_id": src,
        "target_node_id": tgt,
        "condition": None,
        "data_shape": None,
    }


def _mock_agent_result(agent_id: str = "mock.post.gate") -> dict[str, Any]:
    return {
        "status": "succeeded",
        "output_summary": f"Mocked agent '{agent_id}' completed",
        "cost_usd": 0.001,
        "agent_run_id": f"mock-run-{agent_id.replace('.', '-')}",
    }


async def _reset_pipeline_tables(session: AsyncSession) -> None:
    await session.execute(_PIPELINE_TRUNCATE)
    await session.commit()


async def _seed_gate_2_review_run(
    session: AsyncSession,
) -> tuple[CampaignCandidate, CampaignDeliverable, str, int]:
    await _reset_pipeline_tables(session)

    signal = await create_signal(
        session,
        headline="District literacy shift",
        campaign_family="outreach_email",
        source_type="manual",
        summary="Signal summary",
        discovered_by="test",
        state="TX",
        reason_codes=[{"code": "literacy_shift"}],
    )
    candidate = await create_campaign_candidate_from_signal(
        session,
        signal_id=signal.id,
        ruleset_version_tag="v1",
    )
    candidate.name = "Fort Bend Follow-Up"
    candidate.workspace_state = "content_in_review"

    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="stub-draft-1",
        campaign_id=str(candidate.id),
        status=DeliverableState.draft_ready.value,
        deliverable_metadata={
            "externalTitle": "Outreach Email Draft",
            "deliverableTypeSlug": "outreach_email",
            "versions": [
                {
                    "id": "v1",
                    "version_number": 1,
                    "content": "Draft intro for Fort Bend ISD families and district leaders.",
                }
            ],
        },
    )
    session.add(deliverable)
    await session.flush()

    await session.execute(
        text(
            "INSERT INTO agents (agent_id, name, tools, model, provider) "
            "VALUES ('mock.post.gate', 'Mock Post Gate', '[]'::jsonb, "
            "'claude-haiku-4-5', 'claude-code') "
            "ON CONFLICT (agent_id) DO NOTHING"
        )
    )

    pipeline = await pipeline_repo.create_pipeline(
        session,
        name="Gate 2 Review Test",
        nodes=[
            _node("trigger", "trigger_manual"),
            _node(
                "gate_2_approval_drawer",
                "human_gate",
                {
                    "approval_kind": "content_draft",
                    "approvers": ["reviewer@example.com"],
                    "timeout_hours": 72,
                },
            ),
            _node("after_gate", "agent_invocation", {"agent_id": "mock.post.gate"}),
        ],
        edges=[
            _edge("trigger", "gate_2_approval_drawer"),
            _edge("gate_2_approval_drawer", "after_gate"),
        ],
    )
    run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="queued",
        trigger="manual",
        triggered_by="test",
        target_candidate_id=candidate.id,
    )
    await session.commit()

    with (
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        async with session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(session)

    approval = (
        await session.execute(
            text(
                "SELECT id FROM approvals WHERE kind = 'content_draft' AND subject_id = :subject_id"
            ),
            {"subject_id": f"{run.id}:gate_2_approval_drawer"},
        )
    ).scalar_one()
    await session.commit()
    return candidate, deliverable, run.id, int(approval)


async def _refresh_entities(
    session: AsyncSession,
    candidate_id: int,
    deliverable_id: int,
    approval_id: int,
    run_id: str,
) -> tuple[CampaignCandidate, CampaignDeliverable, Approval, Any]:
    session.expire_all()
    candidate = await session.get(CampaignCandidate, candidate_id)
    deliverable = await session.get(CampaignDeliverable, deliverable_id)
    approval = await session.get(Approval, approval_id)
    run = await pipeline_repo.get_pipeline_run(session, run_id)
    assert candidate is not None
    assert deliverable is not None
    assert approval is not None
    await session.refresh(candidate)
    await session.refresh(deliverable)
    await session.refresh(approval)
    return candidate, deliverable, approval, run


async def test_gate_2_suspension_creates_reviewable_content_draft_approval(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, deliverable, run_id, approval_id = await _seed_gate_2_review_run(db_session)

    response = await client.get("/api/approvals/?status=pending&kind=content_draft")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    approval = body[0]
    assert approval["id"] == approval_id
    assert approval["subjectId"] == f"{run_id}:gate_2_approval_drawer"
    ctx = approval["pipe4Context"]["context"]
    assert ctx["candidate_id"] is not None
    assert ctx["deliverable_ids"] == [deliverable.id]
    assert ctx["deliverables"][0]["draftPreview"].startswith("Draft intro for Fort Bend")


async def test_decide_approved_transitions_deliverable_and_resume_flow(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    candidate, deliverable, run_id, approval_id = await _seed_gate_2_review_run(db_session)

    with patch("artemis.pipelines.routes._dispatch_execution", return_value=None):
        response = await client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approved", "reviewer": "jon@amiralearning.com"},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "approved"
    assert payload["resume"]["resumed"] is True
    assert payload["resume"]["pipelineDecision"] == "approved"

    candidate, deliverable, approval, run = await _refresh_entities(
        db_session,
        candidate.id,
        deliverable.id,
        approval_id,
        run_id,
    )
    assert approval.status == "approved"
    assert deliverable.status == DeliverableState.approved.value
    assert candidate.workspace_state == "all_content_approved"
    assert run.status == "running"
    assert run.node_states["gate_2_approval_drawer"]["decision"] == "approved"

    with patch(
        "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
        new=AsyncMock(return_value=_mock_agent_result()),
    ):
        await db_session.commit()
        executor = PipelineExecutor(run_id)
        await executor.run(db_session)
        await db_session.commit()

    _, _, _, final_run = await _refresh_entities(
        db_session,
        candidate.id,
        deliverable.id,
        approval_id,
        run_id,
    )
    assert final_run.status == "succeeded"
    assert final_run.node_states["after_gate"]["status"] == "succeeded"


async def test_decide_rejected_transitions_deliverable_and_workspace_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    candidate, deliverable, run_id, approval_id = await _seed_gate_2_review_run(db_session)

    with patch("artemis.pipelines.routes._dispatch_execution", return_value=None):
        response = await client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "rejected", "reviewer": "jon@amiralearning.com"},
        )
    assert response.status_code == 200, response.text

    candidate, deliverable, approval, run = await _refresh_entities(
        db_session,
        candidate.id,
        deliverable.id,
        approval_id,
        run_id,
    )
    assert approval.status == "rejected"
    assert deliverable.status == DeliverableState.rejected.value
    assert candidate.workspace_state == "revision_needed"
    assert run.status == "running"

    await db_session.commit()
    executor = PipelineExecutor(run_id)
    await executor.run(db_session)
    await db_session.commit()

    _, _, _, final_run = await _refresh_entities(
        db_session,
        candidate.id,
        deliverable.id,
        approval_id,
        run_id,
    )
    assert final_run.status == "failed"
    assert "rejected" in (final_run.error_message or "")


async def test_revision_requested_marks_revised_and_holds_via_rejected_gate_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    candidate, deliverable, run_id, approval_id = await _seed_gate_2_review_run(db_session)

    with patch("artemis.pipelines.routes._dispatch_execution", return_value=None):
        response = await client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "revision_requested", "reviewer": "jon@amiralearning.com"},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "revision_requested"
    assert payload["resume"]["pipelineDecision"] == "rejected"

    candidate, deliverable, approval, run = await _refresh_entities(
        db_session,
        candidate.id,
        deliverable.id,
        approval_id,
        run_id,
    )
    assert approval.status == "revision_requested"
    assert deliverable.status == DeliverableState.revised.value
    assert candidate.workspace_state == "revision_needed"
    assert run.status == "running"

    await db_session.commit()
    executor = PipelineExecutor(run_id)
    await executor.run(db_session)
    await db_session.commit()

    _, _, _, final_run = await _refresh_entities(
        db_session,
        candidate.id,
        deliverable.id,
        approval_id,
        run_id,
    )
    assert final_run.status == "failed"


async def test_already_decided_returns_4xx(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, _, _, approval_id = await _seed_gate_2_review_run(db_session)

    with patch("artemis.pipelines.routes._dispatch_execution", return_value=None):
        first = await client.post(
            f"/api/approvals/{approval_id}/decision",
            json={"decision": "approved", "reviewer": "jon@amiralearning.com"},
        )
    assert first.status_code == 200

    second = await client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "approved", "reviewer": "jon@amiralearning.com"},
    )
    assert second.status_code == 400
    assert second.json()["code"] == "approval_not_pending"


async def test_invalid_decision_returns_4xx(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, _, _, approval_id = await _seed_gate_2_review_run(db_session)

    response = await client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"decision": "ship_it", "reviewer": "jon@amiralearning.com"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "approval_invalid_decision"
