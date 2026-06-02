"""Tests for /api/workflows endpoints and Workflow repository helpers."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo

_STEPS = [{"name": "Step 1", "prompt": "Do something"}]


# ─────────────────────────────────────────────────────────────────────────────
# Repository tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_workflow(db_session: AsyncSession) -> None:
    async with db_session.begin():
        wf = await repo.create_workflow(
            db_session,
            workflow_id="my-workflow",
            name="My Workflow",
            description="A test workflow",
            steps=_STEPS,
        )
    assert wf.workflow_id == "my-workflow"
    fetched = await repo.get_workflow(db_session, "my-workflow")
    assert fetched.name == "My Workflow"
    assert fetched.steps == _STEPS


@pytest.mark.asyncio
async def test_list_workflows(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_workflow(db_session, workflow_id="wf1", name="WF One", steps=_STEPS)
        await repo.create_workflow(db_session, workflow_id="wf2", name="WF Two", steps=_STEPS)
    wfs = await repo.list_workflows(db_session)
    assert len(wfs) == 2


@pytest.mark.asyncio
async def test_update_workflow(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_workflow(db_session, workflow_id="upd-wf", name="Old", steps=_STEPS)
    async with db_session.begin():
        wf = await repo.update_workflow(db_session, "upd-wf", name="New")
    assert wf.name == "New"


@pytest.mark.asyncio
async def test_delete_workflow(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_workflow(db_session, workflow_id="del-wf", name="Del", steps=_STEPS)
    async with db_session.begin():
        await repo.delete_workflow(db_session, "del-wf")
    with pytest.raises(ValueError, match="not found"):
        await repo.get_workflow(db_session, "del-wf")


@pytest.mark.asyncio
async def test_workflow_run_lifecycle(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_workflow(db_session, workflow_id="run-wf", name="Run WF", steps=_STEPS)
    run_id = str(uuid.uuid4())
    async with db_session.begin():
        run = await repo.create_workflow_run(db_session, run_id=run_id, workflow_id="run-wf")
    assert run.status == "queued"

    async with db_session.begin():
        from datetime import UTC, datetime

        updated = await repo.update_workflow_run_status(
            db_session,
            run_id,
            "completed",
            current_step=2,
            completed_at=datetime.now(UTC),
            total_cost_usd=0.005,
        )
    assert updated.status == "completed"
    assert updated.current_step == 2
    assert updated.total_cost_usd == pytest.approx(0.005, rel=1e-3)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_http_surface_deprecated(client: AsyncClient) -> None:
    for method, path in [
        ("get", "/api/workflows/"),
        ("post", "/api/workflows/"),
        ("get", "/api/workflows/get-wf"),
        ("patch", "/api/workflows/patch-wf"),
        ("delete", "/api/workflows/rm-wf"),
        ("get", "/api/workflows/runs-wf/runs"),
        ("get", "/api/workflows/runs-wf/runs/latest"),
    ]:
        resp = await client.request(
            method.upper(), path, json={} if method in {"post", "patch"} else None
        )
        assert resp.status_code == 410
        body = resp.json()
        assert body["error"] == "workflows_deprecated"
        assert body["redirect_to"] == "/api/pipelines"
