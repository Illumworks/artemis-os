"""Tests for agent_runs endpoints and repository helpers."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo


async def _make_run(session: AsyncSession, *, agent_id: str = "run-agent") -> str:
    """Create an agent row + run row, return run_id."""
    try:
        await repo.get_agent(session, agent_id)
    except ValueError:
        async with session.begin():
            await repo.create_agent(session, agent_id=agent_id, name="Run Agent")
    run_id = str(uuid.uuid4())
    async with session.begin():
        await repo.create_agent_run(session, run_id=run_id, agent_id=agent_id)
    return run_id


# ─────────────────────────────────────────────────────────────────────────────
# Repository tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_agent_run(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(db_session, agent_id="ar-agent", name="AR Agent")
    run_id = str(uuid.uuid4())
    async with db_session.begin():
        run = await repo.create_agent_run(db_session, run_id=run_id, agent_id="ar-agent")
    assert run.run_id == run_id
    assert run.status == "queued"

    fetched = await repo.get_agent_run(db_session, run_id)
    assert fetched.agent_id == "ar-agent"


@pytest.mark.asyncio
async def test_update_run_status(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(db_session, agent_id="status-agent", name="SA")
    run_id = str(uuid.uuid4())
    async with db_session.begin():
        await repo.create_agent_run(db_session, run_id=run_id, agent_id="status-agent")
    async with db_session.begin():
        run = await repo.update_agent_run_status(db_session, run_id, "running")
    assert run.status == "running"


@pytest.mark.asyncio
async def test_set_agent_run_completed(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(db_session, agent_id="done-agent", name="Done")
    run_id = str(uuid.uuid4())
    async with db_session.begin():
        await repo.create_agent_run(db_session, run_id=run_id, agent_id="done-agent")
    async with db_session.begin():
        run = await repo.set_agent_run_completed(
            db_session,
            run_id,
            status="completed",
            cost_input_tokens=100,
            cost_output_tokens=200,
        )
    assert run.status == "completed"
    assert run.completed_at is not None
    assert run.cost_input_tokens == 100


@pytest.mark.asyncio
async def test_agent_context_upsert(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(db_session, agent_id="ctx-agent", name="Ctx")
    run_id = str(uuid.uuid4())
    async with db_session.begin():
        await repo.create_agent_run(db_session, run_id=run_id, agent_id="ctx-agent")
    async with db_session.begin():
        ctx = await repo.set_agent_context(db_session, run_id, "output", {"result": 42})
    assert ctx.value == {"result": 42}

    # Upsert same key
    async with db_session.begin():
        ctx2 = await repo.set_agent_context(db_session, run_id, "output", {"result": 99})
    assert ctx2.value == {"result": 99}


@pytest.mark.asyncio
async def test_get_all_agent_context(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(db_session, agent_id="allctx-agent", name="Ctx")
    run_id = str(uuid.uuid4())
    async with db_session.begin():
        await repo.create_agent_run(db_session, run_id=run_id, agent_id="allctx-agent")
    async with db_session.begin():
        await repo.set_agent_context(db_session, run_id, "k1", "v1")
        await repo.set_agent_context(db_session, run_id, "k2", "v2")
    items = await repo.get_all_agent_context_for_run(db_session, run_id)
    assert len(items) == 2


# ─────────────────────────────────────────────────────────────────────────────
# HTTP tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_agent_runs_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    # db_session fixture truncates builders tables before this test
    resp = await client.get("/api/agent-runs/")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


@pytest.mark.asyncio
async def test_get_agent_run_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/agent-runs/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["code"] == "agent_run_not_found"


@pytest.mark.asyncio
async def test_get_agent_run_context_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/agent-runs/no-run/context")
    assert resp.status_code == 404
