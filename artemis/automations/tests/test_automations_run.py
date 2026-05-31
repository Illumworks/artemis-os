"""Run, approval-resume, and cancel tests for Automations (OP1).

Tests:
- Manual run with no approval policy → status queued, can dispatch
- Manual run with approval policy → status awaiting_approval, target_run_id IS NULL
- Resume from awaiting_approval → status queued, dispatches
- Cancel an in-flight run → status cancelled
"""

from __future__ import annotations

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
async def test_automation_http_surface_deprecated(client: AsyncClient) -> None:
    """Legacy automation HTTP routes return PIPE6 410 after sunset."""
    for method, path in [
        ("post", "/api/automations/"),
        ("post", "/api/automations/legacy/run"),
        ("post", "/api/automation-runs/legacy/resume"),
        ("post", "/api/automation-runs/legacy/cancel"),
    ]:
        resp = await client.request(method.upper(), path, json={})
        assert resp.status_code == 410
        body = resp.json()
        assert body["error"] == "automations_deprecated"
        assert body["redirect_to"] == "/api/pipelines"
