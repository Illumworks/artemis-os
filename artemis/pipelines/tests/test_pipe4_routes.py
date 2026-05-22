"""PIPE4 route tests — resume + Slack callback endpoints.

Tests:
1. POST /api/pipeline-runs/{id}/resume — approved path
2. POST /api/pipeline-runs/{id}/resume — rejected path
3. POST /api/pipeline-runs/{id}/resume — non-awaiting run rejected
4. POST /api/slack/pipeline-approval-callback — valid approve action
5. POST /api/slack/pipeline-approval-callback — invalid payload ignored
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo

pytestmark = pytest.mark.asyncio

TRUNCATE = text(
    "TRUNCATE pipeline_runs, pipelines, approvals, agent_context, "
    "agent_run_trajectory_summaries, definition_proposals, "
    "agent_runs, agent_skills, agents RESTART IDENTITY CASCADE"
)


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
        "id": f"e_{src}_{tgt}",
        "source_node_id": src,
        "target_node_id": tgt,
        "condition": None,
        "data_shape": None,
    }


async def _setup_suspended_gate(
    db_session: AsyncSession,
    run_id_out: list[str],
) -> str:
    """Helper: create a pipeline run with a suspended gate."""
    async with db_session.begin():
        await db_session.execute(TRUNCATE)

    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "gate1",
            "human_gate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["approver@example.com"],
                "timeout_hours": 72,
            },
        ),
    ]
    edges = [_edge("trigger", "gate1")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(
            db_session, name="Resume Test", nodes=nodes, edges=edges
        )
        run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline.id,
            status="awaiting_approval",
            trigger="manual",
            triggered_by="test",
        )
        ns = {
            "trigger": {"status": "succeeded", "cost_usd": 0.0, "output_summary": "done"},
            "gate1": {"status": "suspended", "cost_usd": 0.0, "output_summary": "pending"},
        }
        await repo.update_pipeline_run(db_session, run.id, node_states=ns)

    run_id_out.append(run.id)
    return run.id


# ── Resume endpoint tests ─────────────────────────────────────────────────────


async def test_resume_approved_triggers_background_execution(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    run_id_holder: list[str] = []
    run_id = await _setup_suspended_gate(db_session, run_id_holder)

    with patch("artemis.pipelines.routes._execute_pipeline_run", new=AsyncMock()):
        resp = await client.post(
            f"/api/pipeline-runs/{run_id}/resume",
            json={"node_id": "gate1", "decision": "approved", "actor": "approver@example.com"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] in ("running", "queued")


async def test_resume_rejected_allowed(client: AsyncClient, db_session: AsyncSession) -> None:
    run_id_holder: list[str] = []
    run_id = await _setup_suspended_gate(db_session, run_id_holder)

    with patch("artemis.pipelines.routes._execute_pipeline_run", new=AsyncMock()):
        resp = await client.post(
            f"/api/pipeline-runs/{run_id}/resume",
            json={"node_id": "gate1", "decision": "rejected", "actor": "approver@example.com"},
        )

    assert resp.status_code == 200, resp.text


async def test_resume_non_awaiting_run_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    async with db_session.begin():
        await db_session.execute(TRUNCATE)

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, name="Already Done", nodes=[], edges=[])
        run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline.id,
            status="succeeded",
            trigger="manual",
            triggered_by="test",
        )

    resp = await client.post(
        f"/api/pipeline-runs/{run.id}/resume",
        json={"node_id": "gate1", "decision": "approved", "actor": "test@example.com"},
    )
    assert resp.status_code == 400


async def test_resume_invalid_decision_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    run_id_holder: list[str] = []
    run_id = await _setup_suspended_gate(db_session, run_id_holder)

    resp = await client.post(
        f"/api/pipeline-runs/{run_id}/resume",
        json={"node_id": "gate1", "decision": "maybe", "actor": "test@example.com"},
    )
    assert resp.status_code == 400


# ── Slack callback tests ──────────────────────────────────────────────────────


async def test_slack_callback_valid_approve(client: AsyncClient, db_session: AsyncSession) -> None:
    run_id_holder: list[str] = []
    run_id = await _setup_suspended_gate(db_session, run_id_holder)

    payload = {
        "actions": [
            {
                "action_id": "pipeline_approval_approve",
                "value": f"{run_id}:gate1:approved",
            }
        ],
        "user": {"id": "USLACK123", "name": "approver"},
    }

    with patch("artemis.pipelines.routes._execute_pipeline_run", new=AsyncMock()):
        resp = await client.post(
            "/api/slack/pipeline-approval-callback",
            data={"payload": json.dumps(payload)},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert resp.status_code == 200
    assert resp.json().get("ok") is True


async def test_slack_callback_empty_payload_ok(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/slack/pipeline-approval-callback",
        data={},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200


async def test_slack_callback_unrelated_action_ignored(client: AsyncClient) -> None:
    payload = {
        "actions": [{"action_id": "some_other_app_action", "value": "whatever"}],
        "user": {"id": "U123"},
    }
    resp = await client.post(
        "/api/slack/pipeline-approval-callback",
        data={"payload": json.dumps(payload)},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True
