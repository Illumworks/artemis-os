"""CC8 — Pipeline run-lock tests.

Tests:
1. Manual trigger while a run is queued/running → 409 Conflict, only 1 run exists.
2. After run reaches terminal state → new trigger succeeds (lock released).
3. awaiting_approval counts as in-flight → 409.
4. Scheduler trigger while in-flight → logs warning + skips, no new run created.
5. Different pipelines do NOT block each other (lock is per-pipeline).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo

pytestmark = pytest.mark.asyncio

_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
_IN_FLIGHT_STATUSES = ("queued", "running", "awaiting_approval")


# ── 1. Manual trigger while queued/running → 409 ─────────────────────────────


async def test_manual_trigger_while_in_flight_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /run while a run is already queued → 409; DB still has only one run."""
    # Create pipeline
    resp = await client.post(
        "/api/pipelines/",
        json={"name": "Lock Test", "nodes": [], "edges": []},
    )
    assert resp.status_code == 201
    pipeline_id = resp.json()["id"]

    # Seed an in-flight run directly via DB so we don't need a real executor
    async with db_session.begin():
        existing = await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline_id,
            status="queued",
            trigger="manual",
            triggered_by="test",
        )

    # Attempt a second manual trigger
    with patch("artemis.pipelines.routes._execute_pipeline_run", new=AsyncMock()):
        resp2 = await client.post(f"/api/pipelines/{pipeline_id}/run", json={})

    assert resp2.status_code == 409, resp2.text
    body = resp2.json()
    assert body.get("error") == "pipeline_run_in_flight"
    assert body.get("in_flight_run_id") == existing.id

    # Confirm only the original run exists
    async with db_session.begin():
        runs = await repo.list_pipeline_runs(db_session, pipeline_id)
    assert len(runs) == 1
    assert runs[0].id == existing.id


# ── 2. Lock released after terminal state ─────────────────────────────────────


@pytest.mark.parametrize("terminal_status", list(_TERMINAL_STATUSES))
async def test_trigger_succeeds_after_terminal_state(
    terminal_status: str,
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When the existing run is in a terminal state, a new trigger is allowed."""
    resp = await client.post(
        "/api/pipelines/",
        json={"name": f"Terminal-{terminal_status}", "nodes": [], "edges": []},
    )
    pipeline_id = resp.json()["id"]

    # Seed a run that has already finished
    async with db_session.begin():
        await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline_id,
            status=terminal_status,
            trigger="manual",
            triggered_by="test",
        )

    with patch("artemis.pipelines.routes._execute_pipeline_run", new=AsyncMock()):
        resp2 = await client.post(f"/api/pipelines/{pipeline_id}/run", json={})

    assert resp2.status_code == 202, resp2.text
    assert resp2.json()["status"] == "queued"


# ── 3. awaiting_approval counts as in-flight ──────────────────────────────────


async def test_awaiting_approval_blocks_new_trigger(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A gate-suspended (awaiting_approval) run blocks new manual triggers."""
    resp = await client.post(
        "/api/pipelines/",
        json={"name": "Gate Suspended Lock Test", "nodes": [], "edges": []},
    )
    pipeline_id = resp.json()["id"]

    async with db_session.begin():
        suspended_run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline_id,
            status="awaiting_approval",
            trigger="manual",
            triggered_by="test",
        )

    with patch("artemis.pipelines.routes._execute_pipeline_run", new=AsyncMock()):
        resp2 = await client.post(f"/api/pipelines/{pipeline_id}/run", json={})

    assert resp2.status_code == 409, resp2.text
    body = resp2.json()
    assert body.get("error") == "pipeline_run_in_flight"
    assert body.get("in_flight_run_id") == suspended_run.id


# ── 4. Scheduler trigger while in-flight → log + skip ────────────────────────


async def test_scheduler_skips_when_in_flight(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """_fire_scheduled_pipeline logs a warning and creates no new run when locked."""
    from artemis.pipelines.scheduler import _fire_scheduled_pipeline

    async with db_session.begin():
        pipeline = await repo.create_pipeline(
            db_session,
            name="Cron Lock Test",
            nodes=[
                {
                    "id": "t",
                    "type": "trigger_scheduled",
                    "label": "t",
                    "config": {},
                    "position": {"x": 0.0, "y": 0.0},
                }
            ],
            edges=[],
            status="active",
        )
        await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline.id,
            status="running",
            trigger="scheduled",
            triggered_by="scheduler",
        )

    with (
        caplog.at_level(logging.WARNING, logger="artemis.pipelines.scheduler"),
        patch("artemis.pipelines.executor.PipelineExecutor") as mock_executor,
    ):
        mock_executor.return_value.run = AsyncMock()
        await _fire_scheduled_pipeline(pipeline.id)

    # No new run should have been created
    async with db_session.begin():
        runs = await repo.list_pipeline_runs(db_session, pipeline.id)
    assert len(runs) == 1, "Scheduler must not create a new run while one is in-flight"

    assert any(
        "in-flight" in rec.message or "skipping" in rec.message.lower() for rec in caplog.records
    ), "Expected a warning log about skipping the in-flight run"


# ── 5. Different pipelines do not block each other ────────────────────────────


async def test_lock_is_per_pipeline(client: AsyncClient, db_session: AsyncSession) -> None:
    """An in-flight run on pipeline A must not block a trigger on pipeline B."""
    resp_a = await client.post(
        "/api/pipelines/",
        json={"name": "Pipeline A", "nodes": [], "edges": []},
    )
    pipeline_a_id = resp_a.json()["id"]

    resp_b = await client.post(
        "/api/pipelines/",
        json={"name": "Pipeline B", "nodes": [], "edges": []},
    )
    pipeline_b_id = resp_b.json()["id"]

    # Seed an in-flight run on pipeline A
    async with db_session.begin():
        await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline_a_id,
            status="running",
            trigger="manual",
            triggered_by="test",
        )

    # Triggering pipeline B must succeed
    with patch("artemis.pipelines.routes._execute_pipeline_run", new=AsyncMock()):
        resp = await client.post(f"/api/pipelines/{pipeline_b_id}/run", json={})

    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "queued"
