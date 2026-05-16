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
async def test_list_workflows_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    # db_session fixture truncates builders tables before this test
    resp = await client.get("/api/workflows/")
    assert resp.status_code == 200
    assert resp.json()["workflows"] == []


@pytest.mark.asyncio
async def test_create_workflow_http(client: AsyncClient) -> None:
    payload = {
        "workflowId": "http-wf",
        "name": "HTTP Workflow",
        "steps": _STEPS,
    }
    resp = await client.post("/api/workflows/", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["workflowId"] == "http-wf"
    assert data["steps"] == _STEPS


@pytest.mark.asyncio
async def test_create_workflow_empty_steps_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/workflows/", json={"workflowId": "empty-steps", "name": "Bad", "steps": []}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "steps_required"


@pytest.mark.asyncio
async def test_get_workflow_http(client: AsyncClient) -> None:
    await client.post(
        "/api/workflows/",
        json={"workflowId": "get-wf", "name": "Get", "steps": _STEPS},
    )
    resp = await client.get("/api/workflows/get-wf")
    assert resp.status_code == 200
    assert resp.json()["workflowId"] == "get-wf"


@pytest.mark.asyncio
async def test_get_workflow_not_found_http(client: AsyncClient) -> None:
    resp = await client.get("/api/workflows/no-such")
    assert resp.status_code == 404
    assert resp.json()["code"] == "workflow_not_found"


@pytest.mark.asyncio
async def test_create_workflow_duplicate_http(client: AsyncClient) -> None:
    payload = {"workflowId": "dup-wf", "name": "Dup", "steps": _STEPS}
    await client.post("/api/workflows/", json=payload)
    resp = await client.post("/api/workflows/", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_patch_workflow_http(client: AsyncClient) -> None:
    await client.post(
        "/api/workflows/", json={"workflowId": "patch-wf", "name": "Old", "steps": _STEPS}
    )
    resp = await client.patch("/api/workflows/patch-wf", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_workflow_http(client: AsyncClient) -> None:
    await client.post(
        "/api/workflows/", json={"workflowId": "rm-wf", "name": "Remove", "steps": _STEPS}
    )
    resp = await client.delete("/api/workflows/rm-wf")
    assert resp.status_code == 204
    assert (await client.get("/api/workflows/rm-wf")).status_code == 404


@pytest.mark.asyncio
async def test_list_workflow_runs_http(client: AsyncClient) -> None:
    await client.post(
        "/api/workflows/", json={"workflowId": "runs-wf", "name": "Runs", "steps": _STEPS}
    )
    resp = await client.get("/api/workflows/runs-wf/runs")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []
