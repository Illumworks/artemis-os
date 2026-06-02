"""Pipeline archive, restore, and permanent-delete tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo


@pytest.mark.asyncio
async def test_permanent_delete_rejects_active_pipeline(client: AsyncClient) -> None:
    created = await client.post(
        "/api/pipelines/", json={"name": "Active", "nodes": [], "edges": []}
    )
    pipeline_id = created.json()["id"]

    response = await client.delete(f"/api/pipelines/{pipeline_id}/permanent")

    assert response.status_code == 409
    assert response.json()["code"] == "pipeline_must_be_archived"


@pytest.mark.asyncio
async def test_permanent_delete_removes_archived_pipeline_and_runs(client: AsyncClient) -> None:
    created = await client.post(
        "/api/pipelines/", json={"name": "Archived", "nodes": [], "edges": []}
    )
    pipeline_id = created.json()["id"]
    run = await client.post(f"/api/pipelines/{pipeline_id}/run", json={})
    assert run.status_code == 202
    assert (await client.delete(f"/api/pipelines/{pipeline_id}")).status_code == 204

    response = await client.delete(f"/api/pipelines/{pipeline_id}/permanent")

    assert response.status_code == 204
    assert (await client.get(f"/api/pipelines/{pipeline_id}")).status_code == 404
    assert (await client.get(f"/api/pipelines/{pipeline_id}/runs")).status_code == 404


@pytest.mark.asyncio
async def test_permanent_delete_missing_pipeline_returns_404(client: AsyncClient) -> None:
    response = await client.delete("/api/pipelines/missing/permanent")

    assert response.status_code == 404
    assert response.json()["code"] == "pipeline_not_found"


@pytest.mark.asyncio
async def test_restore_archived_pipeline_via_patch(db_session: AsyncSession) -> None:
    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, name="Restore me", nodes=[], edges=[])
        await repo.archive_pipeline(db_session, pipeline.id)

    async with db_session.begin():
        restored = await repo.update_pipeline(db_session, pipeline.id, status="active")

    assert restored.status == "active"
    rows = await repo.list_pipelines(db_session)
    assert [p.id for p, _ in rows] == [pipeline.id]
