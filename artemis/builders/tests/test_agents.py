"""Tests for /api/agents endpoints and Agent repository helpers."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo

PROVIDER_FIELDS = {
    "provider": "claude-code",
    "model": "sonnet",
    "fallbackProvider": "codex",
    "fallbackModel": "gpt-5.4",
}

# ─────────────────────────────────────────────────────────────────────────────
# Repository round-trip tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_agent(db_session: AsyncSession) -> None:
    async with db_session.begin():
        agent = await repo.create_agent(
            db_session,
            agent_id="test-agent",
            name="Test Agent",
            description="A test agent",
            goal="Do testing",
            system_prompt="You are a tester",
            tools=["bash", "read"],
            model="claude-sonnet-4-6",
            provider="anthropic",
            max_iterations=5,
        )
    assert agent.id is not None
    assert agent.agent_id == "test-agent"

    fetched = await repo.get_agent(db_session, "test-agent")
    assert fetched.name == "Test Agent"
    assert fetched.tools == ["bash", "read"]
    assert fetched.max_iterations == 5


@pytest.mark.asyncio
async def test_list_agents(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(db_session, agent_id="a1", name="Agent One")
        await repo.create_agent(db_session, agent_id="a2", name="Agent Two")
    agents = await repo.list_agents(db_session, limit=10)
    assert len(agents) == 2


@pytest.mark.asyncio
async def test_update_agent(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(db_session, agent_id="upd-agent", name="Old Name")
    async with db_session.begin():
        updated = await repo.update_agent(db_session, "upd-agent", name="New Name")
    assert updated.name == "New Name"


@pytest.mark.asyncio
async def test_update_agent_metadata_does_not_change_agent_id(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(
            db_session,
            agent_id="folder-agent",
            name="Folder Agent",
            metadata={"display_folder": "Old"},
        )
    async with db_session.begin():
        updated = await repo.update_agent(
            db_session, "folder-agent", metadata={"display_folder": "Top Picks"}
        )
    assert updated.agent_id == "folder-agent"
    assert updated.metadata_ == {"display_folder": "Top Picks"}


@pytest.mark.asyncio
async def test_delete_agent(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent(db_session, agent_id="del-agent", name="Delete Me")
    async with db_session.begin():
        await repo.delete_agent(db_session, "del-agent")
    with pytest.raises(ValueError, match="not found"):
        await repo.get_agent(db_session, "del-agent")


@pytest.mark.asyncio
async def test_get_agent_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not found"):
        await repo.get_agent(db_session, "nonexistent")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP endpoint tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_agents_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/agents/")
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data
    assert data["agents"] == []


@pytest.mark.asyncio
async def test_create_agent_http(client: AsyncClient) -> None:
    payload = {
        "agentId": "http-agent",
        "name": "HTTP Agent",
        "description": "Created via HTTP",
        "tools": ["bash"],
        "maxIterations": 8,
        **PROVIDER_FIELDS,
    }
    resp = await client.post("/api/agents/", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["agentId"] == "http-agent"
    assert data["name"] == "HTTP Agent"
    assert data["maxIterations"] == 8


@pytest.mark.asyncio
async def test_get_agent_http(client: AsyncClient) -> None:
    await client.post(
        "/api/agents/",
        json={"agentId": "get-test-agent", "name": "Get Test", **PROVIDER_FIELDS},
    )
    resp = await client.get("/api/agents/get-test-agent")
    assert resp.status_code == 200
    assert resp.json()["agentId"] == "get-test-agent"


@pytest.mark.asyncio
async def test_get_agent_not_found_http(client: AsyncClient) -> None:
    resp = await client.get("/api/agents/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["code"] == "agent_not_found"


@pytest.mark.asyncio
async def test_create_agent_duplicate_http(client: AsyncClient) -> None:
    payload = {"agentId": "dup-agent", "name": "Dup", **PROVIDER_FIELDS}
    await client.post("/api/agents/", json=payload)
    resp = await client.post("/api/agents/", json=payload)
    assert resp.status_code == 409
    assert resp.json()["code"] == "agent_exists"


@pytest.mark.asyncio
async def test_patch_agent_http(client: AsyncClient) -> None:
    await client.post(
        "/api/agents/", json={"agentId": "patch-me", "name": "Old", **PROVIDER_FIELDS}
    )
    resp = await client.patch("/api/agents/patch-me", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_patch_agent_metadata_http_keeps_agent_id(client: AsyncClient) -> None:
    await client.post(
        "/api/agents/", json={"agentId": "folder-http", "name": "Folder", **PROVIDER_FIELDS}
    )
    resp = await client.patch(
        "/api/agents/folder-http", json={"metadata": {"display_folder": "Favorites"}}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agentId"] == "folder-http"
    assert data["metadata"] == {"display_folder": "Favorites"}


@pytest.mark.asyncio
async def test_patch_agent_not_found_http(client: AsyncClient) -> None:
    resp = await client.patch("/api/agents/no-such", json={"name": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_agent_http(client: AsyncClient) -> None:
    await client.post(
        "/api/agents/", json={"agentId": "rm-agent", "name": "Remove", **PROVIDER_FIELDS}
    )
    resp = await client.delete("/api/agents/rm-agent")
    assert resp.status_code == 204
    resp2 = await client.get("/api/agents/rm-agent")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_agent_not_found_http(client: AsyncClient) -> None:
    resp = await client.delete("/api/agents/ghost")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_agent_runs_for_agent(client: AsyncClient) -> None:
    await client.post(
        "/api/agents/", json={"agentId": "run-agent", "name": "Runner", **PROVIDER_FIELDS}
    )
    resp = await client.get("/api/agents/run-agent/runs")
    assert resp.status_code == 200
    assert resp.json()["runs"] == []
