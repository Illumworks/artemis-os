"""Integration tests for F2b execution endpoints.

Tests the 4 POST /run endpoints via ASGI transport (no real server, no real
Anthropic API). The executors are monkey-patched to inject FakeAdapter so
the test is end-to-end from HTTP through to DB.

Pattern: create the resource via the CRUD endpoint, then hit the /run endpoint.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_adapter(text: str = "ok") -> FakeAdapter:
    """An adapter that always returns *text*."""
    return FakeAdapter([ScriptedReply(text=text)])


def _multi_fake(*texts: str) -> FakeAdapter:
    return FakeAdapter([ScriptedReply(text=t) for t in texts])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_client(client: AsyncClient, db_session: AsyncSession) -> AsyncClient:
    """Client with common agents pre-seeded in the DB."""
    async with db_session.begin():
        for aid in ["route-agent-1", "route-agent-2", "route-agent-3"]:
            await repo.create_agent(
                db_session,
                agent_id=aid,
                name=f"Route Agent {aid}",
                goal="Execute me",
                model="claude-sonnet-4-6",
                tools=[],
            )
    return client


# ---------------------------------------------------------------------------
# Agent run endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_run_endpoint_happy_path(seeded_client: AsyncClient) -> None:
    """POST /api/agents/{id}/run returns a completed AgentRun JSON."""
    adapter = _fake_adapter("agent says hello")

    with patch("artemis.builders.executor.AnthropicAdapter", return_value=adapter):
        resp = await seeded_client.post(
            "/api/agents/route-agent-1/run",
            json={"userMessage": "Do something"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["agentId"] == "route-agent-1"
    assert "runId" in data


@pytest.mark.asyncio
async def test_agent_run_endpoint_with_shared_context(seeded_client: AsyncClient) -> None:
    """POST /run with sharedContext passes it through to the executor."""
    adapter = _fake_adapter("used context")

    with patch("artemis.builders.executor.AnthropicAdapter", return_value=adapter):
        resp = await seeded_client.post(
            "/api/agents/route-agent-2/run",
            json={"sharedContext": {"key": "value"}},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_agent_run_endpoint_unknown_agent(seeded_client: AsyncClient) -> None:
    """POST /run on unknown agent returns 404."""
    resp = await seeded_client.post("/api/agents/does-not-exist/run", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Workflow run endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_run_endpoint_happy_path(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /api/workflows/{id}/run returns a completed WorkflowRun JSON."""
    async with db_session.begin():
        await repo.create_workflow(
            db_session,
            workflow_id="route-wf-1",
            name="Route WF 1",
            steps=[{"name": "step1", "prompt": "Do it"}],
        )

    adapter = _fake_adapter("workflow step done")
    with patch("artemis.builders.workflow_executor.AnthropicAdapter", return_value=adapter):
        resp = await client.post(
            "/api/workflows/route-wf-1/run",
            json={"initialMessage": "go"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["workflowId"] == "route-wf-1"
    assert "runId" in data


@pytest.mark.asyncio
async def test_workflow_run_endpoint_unknown(client: AsyncClient) -> None:
    """POST /run on unknown workflow returns 404."""
    resp = await client.post("/api/workflows/no-such-wf/run", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Chain run endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_run_endpoint_happy_path(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /api/agent-chains/{id}/run returns list of AgentRun rows."""
    async with db_session.begin():
        await repo.create_agent(
            db_session,
            agent_id="chain-route-a1",
            name="Chain Route Agent 1",
            goal="run",
            model="claude-sonnet-4-6",
            tools=[],
        )
        await repo.create_agent_chain(
            db_session,
            chain_id="route-chain-1",
            name="Route Chain 1",
            steps=[{"agent_id": "chain-route-a1"}],
        )

    adapter = _fake_adapter("chain step done")
    with patch("artemis.builders.executor.AnthropicAdapter", return_value=adapter):
        resp = await client.post(
            "/api/agent-chains/route-chain-1/run",
            json={"initialMessage": "start chain"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "runs" in data
    assert len(data["runs"]) == 1
    assert data["runs"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_chain_run_endpoint_unknown(client: AsyncClient) -> None:
    """POST /run on unknown chain returns 404."""
    resp = await client.post("/api/agent-chains/no-such-chain/run", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DAG run endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dag_run_endpoint_happy_path(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /api/agent-dags/{id}/run returns dict of node_id → AgentRun."""
    async with db_session.begin():
        await repo.create_agent(
            db_session,
            agent_id="dag-route-n1",
            name="DAG Route Node 1",
            goal="execute",
            model="claude-sonnet-4-6",
            tools=[],
        )
        await repo.create_agent_dag(
            db_session,
            dag_id="route-dag-1",
            name="Route DAG 1",
            nodes=[{"id": "N1", "agent_id": "dag-route-n1"}],
        )

    adapter = _fake_adapter("dag node done")
    with patch("artemis.builders.executor.AnthropicAdapter", return_value=adapter):
        resp = await client.post(
            "/api/agent-dags/route-dag-1/run",
            json={"initialInputs": {"N1": "my input"}},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "N1" in data["results"]
    assert data["results"]["N1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_dag_run_endpoint_unknown(client: AsyncClient) -> None:
    """POST /run on unknown DAG returns 404."""
    resp = await client.post("/api/agent-dags/no-such-dag/run", json={})
    assert resp.status_code == 404
