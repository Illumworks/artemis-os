"""Run, approval-resume, and cancel tests for Automations (OP1).

Tests:
- Manual run with no approval policy → status queued, can dispatch
- Manual run with approval policy → status awaiting_approval, target_run_id IS NULL
- Resume from awaiting_approval → status queued, dispatches
- Cancel an in-flight run → status cancelled
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.automations import repository as repo


@pytest.mark.asyncio
async def test_manual_run_no_approval_creates_queued_run(db_session: AsyncSession) -> None:
    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="No Approval",
            trigger_type="manual",
            target_type="agent",
            target_id="agent-1",
            approval_policy={"required": False},
        )

    async with db_session.begin():
        run = await repo.create_automation_run(
            db_session,
            automation_id=auto.id,
            status="queued",
            trigger="manual",
            triggered_by="user@test.com",
        )

    assert run.status == "queued"
    assert run.target_run_id is None


@pytest.mark.asyncio
async def test_manual_run_with_approval_policy_awaits_approval(db_session: AsyncSession) -> None:
    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="Needs Approval",
            trigger_type="manual",
            target_type="agent",
            target_id="agent-1",
            approval_policy={"required": True, "approver_role": "owner"},
        )
        run = await repo.create_automation_run(
            db_session,
            automation_id=auto.id,
            status="awaiting_approval",
            trigger="manual",
            triggered_by="user@test.com",
        )

    assert run.status == "awaiting_approval"
    assert run.target_run_id is None


@pytest.mark.asyncio
async def test_resume_transitions_to_queued(db_session: AsyncSession) -> None:
    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="Resumable",
            trigger_type="manual",
            target_type="agent",
            target_id="agent-1",
            approval_policy={"required": True},
        )
        run = await repo.create_automation_run(
            db_session,
            automation_id=auto.id,
            status="awaiting_approval",
            trigger="manual",
        )

    async with db_session.begin():
        updated = await repo.update_automation_run(db_session, run.id, status="queued")

    assert updated.status == "queued"


@pytest.mark.asyncio
async def test_cancel_run(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    async with db_session.begin():
        auto = await repo.create_automation(
            db_session,
            name="Cancellable",
            trigger_type="manual",
            target_type="agent",
            target_id="agent-1",
        )
        run = await repo.create_automation_run(
            db_session,
            automation_id=auto.id,
            status="running",
            trigger="manual",
        )

    async with db_session.begin():
        cancelled = await repo.update_automation_run(
            db_session,
            run.id,
            status="cancelled",
            completed_at=datetime.now(UTC),
        )

    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None


@pytest.mark.asyncio
async def test_run_endpoint_approval_policy_no_dispatch(client: AsyncClient) -> None:
    """POST /api/automations/{id}/run with approval policy returns awaiting_approval."""
    import artemis.automations.models  # noqa: F401

    # First create an automation via the API
    create_resp = await client.post(
        "/api/automations/",
        json={
            "name": "Policy Run",
            "triggerType": "manual",
            "targetType": "agent",
            "targetId": "nonexistent-agent",
            "approvalPolicy": {"required": True},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    auto_id = create_resp.json()["id"]

    run_resp = await client.post(f"/api/automations/{auto_id}/run", json={})
    assert run_resp.status_code == 202, run_resp.text
    data = run_resp.json()
    assert data["status"] == "awaiting_approval"
    assert data["targetRunId"] is None


@pytest.mark.asyncio
async def test_resume_endpoint(client: AsyncClient) -> None:
    """POST /api/automation-runs/{id}/resume transitions awaiting_approval → queued."""
    create_resp = await client.post(
        "/api/automations/",
        json={
            "name": "Resume Test",
            "triggerType": "manual",
            "targetType": "agent",
            "targetId": "stub-agent",
            "approvalPolicy": {"required": True},
        },
    )
    auto_id = create_resp.json()["id"]

    run_resp = await client.post(f"/api/automations/{auto_id}/run", json={})
    run_id = run_resp.json()["id"]

    with patch(
        "artemis.automations.routes._dispatch_in_background",
        new_callable=AsyncMock,
    ):
        resume_resp = await client.post(f"/api/automation-runs/{run_id}/resume")
    assert resume_resp.status_code == 202, resume_resp.text
    assert resume_resp.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_cancel_endpoint(client: AsyncClient) -> None:
    """POST /api/automation-runs/{id}/cancel sets status=cancelled."""
    create_resp = await client.post(
        "/api/automations/",
        json={
            "name": "Cancel Test",
            "triggerType": "manual",
            "targetType": "agent",
            "targetId": "stub-agent",
            "approvalPolicy": {"required": True},
        },
    )
    auto_id = create_resp.json()["id"]
    run_resp = await client.post(f"/api/automations/{auto_id}/run", json={})
    run_id = run_resp.json()["id"]

    cancel_resp = await client.post(f"/api/automation-runs/{run_id}/cancel")
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "cancelled"
