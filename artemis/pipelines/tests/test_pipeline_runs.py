"""Pipeline run tests (PIPE1).

Tests:
- Manual /run creates a pipeline_runs row in queued status (no execution)
- Cancel transitions run to cancelled
- Cancelling a terminal run is rejected
- Run on archived pipeline is rejected
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo


@pytest.mark.asyncio
async def test_manual_run_creates_queued_run(db_session: AsyncSession) -> None:
    async with db_session.begin():
        p = await repo.create_pipeline(db_session, name="Runnable", nodes=[], edges=[])
        run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=p.id,
            status="queued",
            trigger="manual",
            triggered_by="user@test.com",
        )
    assert run.status == "queued"
    assert run.pipeline_id == p.id
    assert run.trigger == "manual"


@pytest.mark.asyncio
async def test_cancel_run_transitions_to_cancelled(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    async with db_session.begin():
        p = await repo.create_pipeline(db_session, name="Cancellable", nodes=[], edges=[])
        run = await repo.create_pipeline_run(
            db_session, pipeline_id=p.id, status="running", trigger="manual"
        )

    async with db_session.begin():
        cancelled = await repo.update_pipeline_run(
            db_session,
            run.id,
            status="cancelled",
            completed_at=datetime.now(UTC),
        )

    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None


@pytest.mark.asyncio
async def test_run_endpoint_creates_queued_row(client: AsyncClient) -> None:
    """POST /api/pipelines/{id}/run returns 202 and status=queued."""
    create_resp = await client.post(
        "/api/pipelines/",
        json={"name": "API Run Test", "nodes": [], "edges": []},
    )
    assert create_resp.status_code == 201, create_resp.text
    pipeline_id = create_resp.json()["id"]

    run_resp = await client.post(f"/api/pipelines/{pipeline_id}/run", json={})
    assert run_resp.status_code == 202, run_resp.text
    data = run_resp.json()
    assert data["status"] == "queued"
    assert data["pipelineId"] == pipeline_id


@pytest.mark.asyncio
async def test_run_on_archived_pipeline_rejected(client: AsyncClient) -> None:
    """POST /api/pipelines/{id}/run on archived pipeline returns 400."""
    create_resp = await client.post(
        "/api/pipelines/",
        json={"name": "Archive Me", "nodes": [], "edges": []},
    )
    pipeline_id = create_resp.json()["id"]
    await client.delete(f"/api/pipelines/{pipeline_id}")

    run_resp = await client.post(f"/api/pipelines/{pipeline_id}/run", json={})
    assert run_resp.status_code == 400, run_resp.text


@pytest.mark.asyncio
async def test_cancel_endpoint(client: AsyncClient) -> None:
    """POST /api/pipeline-runs/{id}/cancel sets status=cancelled."""
    create_resp = await client.post(
        "/api/pipelines/",
        json={"name": "Cancel Test", "nodes": [], "edges": []},
    )
    pipeline_id = create_resp.json()["id"]

    run_resp = await client.post(f"/api/pipelines/{pipeline_id}/run", json={})
    run_id = run_resp.json()["id"]

    cancel_resp = await client.post(f"/api/pipeline-runs/{run_id}/cancel")
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_terminal_run_rejected(client: AsyncClient) -> None:
    """Cancelling an already-cancelled run returns 400."""
    create_resp = await client.post(
        "/api/pipelines/",
        json={"name": "Double Cancel", "nodes": [], "edges": []},
    )
    pipeline_id = create_resp.json()["id"]
    run_resp = await client.post(f"/api/pipelines/{pipeline_id}/run", json={})
    run_id = run_resp.json()["id"]

    await client.post(f"/api/pipeline-runs/{run_id}/cancel")
    second_cancel = await client.post(f"/api/pipeline-runs/{run_id}/cancel")
    assert second_cancel.status_code == 400
