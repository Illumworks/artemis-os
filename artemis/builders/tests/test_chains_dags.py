"""Tests for /api/agent-chains, /api/agent-dags endpoints and repository helpers."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo

_CHAIN_STEPS = [{"agent_id": "agent-a"}, {"agent_id": "agent-b"}]
_DAG_NODES = [
    {"id": "n1", "agent_id": "agent-a"},
    {"id": "n2", "agent_id": "agent-b", "depends_on": ["n1"]},
]


# ─────────────────────────────────────────────────────────────────────────────
# AgentChain repository tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_chain(db_session: AsyncSession) -> None:
    async with db_session.begin():
        chain = await repo.create_agent_chain(
            db_session,
            chain_id="my-chain",
            name="My Chain",
            description="A test chain",
            steps=_CHAIN_STEPS,
        )
    assert chain.chain_id == "my-chain"
    fetched = await repo.get_agent_chain(db_session, "my-chain")
    assert fetched.steps == _CHAIN_STEPS


@pytest.mark.asyncio
async def test_list_chains(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent_chain(db_session, chain_id="c1", name="Chain 1", steps=[])
        await repo.create_agent_chain(db_session, chain_id="c2", name="Chain 2", steps=[])
    chains = await repo.list_agent_chains(db_session)
    assert len(chains) == 2


@pytest.mark.asyncio
async def test_update_chain(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent_chain(db_session, chain_id="upd-chain", name="Old", steps=[])
    async with db_session.begin():
        chain = await repo.update_agent_chain(db_session, "upd-chain", name="New")
    assert chain.name == "New"


@pytest.mark.asyncio
async def test_delete_chain(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent_chain(db_session, chain_id="del-chain", name="Del", steps=[])
    async with db_session.begin():
        await repo.delete_agent_chain(db_session, "del-chain")
    with pytest.raises(ValueError, match="not found"):
        await repo.get_agent_chain(db_session, "del-chain")


# ─────────────────────────────────────────────────────────────────────────────
# AgentDag repository tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_dag(db_session: AsyncSession) -> None:
    async with db_session.begin():
        dag = await repo.create_agent_dag(
            db_session,
            dag_id="my-dag",
            name="My DAG",
            nodes=_DAG_NODES,
        )
    assert dag.dag_id == "my-dag"
    fetched = await repo.get_agent_dag(db_session, "my-dag")
    assert fetched.nodes == _DAG_NODES


@pytest.mark.asyncio
async def test_list_dags(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent_dag(db_session, dag_id="d1", name="DAG 1", nodes=[])
        await repo.create_agent_dag(db_session, dag_id="d2", name="DAG 2", nodes=[])
    dags = await repo.list_agent_dags(db_session)
    assert len(dags) == 2


@pytest.mark.asyncio
async def test_update_dag(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent_dag(db_session, dag_id="upd-dag", name="Old", nodes=[])
    async with db_session.begin():
        dag = await repo.update_agent_dag(db_session, "upd-dag", name="New")
    assert dag.name == "New"


@pytest.mark.asyncio
async def test_delete_dag(db_session: AsyncSession) -> None:
    async with db_session.begin():
        await repo.create_agent_dag(db_session, dag_id="del-dag", name="Del", nodes=[])
    async with db_session.begin():
        await repo.delete_agent_dag(db_session, "del-dag")
    with pytest.raises(ValueError, match="not found"):
        await repo.get_agent_dag(db_session, "del-dag")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP tests — Agent Chains
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_chains_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/agent-chains/")
    assert resp.status_code == 200
    assert resp.json()["chains"] == []


@pytest.mark.asyncio
async def test_create_chain_http(client: AsyncClient) -> None:
    payload = {"chainId": "http-chain", "name": "HTTP Chain", "steps": _CHAIN_STEPS}
    resp = await client.post("/api/agent-chains/", json=payload)
    assert resp.status_code == 201
    assert resp.json()["chainId"] == "http-chain"


@pytest.mark.asyncio
async def test_get_chain_not_found_http(client: AsyncClient) -> None:
    resp = await client.get("/api/agent-chains/no-such")
    assert resp.status_code == 404
    assert resp.json()["code"] == "chain_not_found"


@pytest.mark.asyncio
async def test_create_chain_duplicate_http(client: AsyncClient) -> None:
    payload = {"chainId": "dup-chain", "name": "Dup", "steps": []}
    await client.post("/api/agent-chains/", json=payload)
    resp = await client.post("/api/agent-chains/", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_patch_chain_http(client: AsyncClient) -> None:
    await client.post("/api/agent-chains/", json={"chainId": "patch-chain", "name": "Old"})
    resp = await client.patch("/api/agent-chains/patch-chain", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_chain_http(client: AsyncClient) -> None:
    await client.post("/api/agent-chains/", json={"chainId": "rm-chain", "name": "Rm"})
    resp = await client.delete("/api/agent-chains/rm-chain")
    assert resp.status_code == 204


# ─────────────────────────────────────────────────────────────────────────────
# HTTP tests — Agent DAGs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_dags_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/agent-dags/")
    assert resp.status_code == 200
    assert resp.json()["dags"] == []


@pytest.mark.asyncio
async def test_create_dag_http(client: AsyncClient) -> None:
    payload = {"dagId": "http-dag", "name": "HTTP DAG", "nodes": _DAG_NODES}
    resp = await client.post("/api/agent-dags/", json=payload)
    assert resp.status_code == 201
    assert resp.json()["dagId"] == "http-dag"


@pytest.mark.asyncio
async def test_get_dag_not_found_http(client: AsyncClient) -> None:
    resp = await client.get("/api/agent-dags/no-such")
    assert resp.status_code == 404
    assert resp.json()["code"] == "dag_not_found"


@pytest.mark.asyncio
async def test_create_dag_duplicate_http(client: AsyncClient) -> None:
    payload = {"dagId": "dup-dag", "name": "Dup", "nodes": []}
    await client.post("/api/agent-dags/", json=payload)
    resp = await client.post("/api/agent-dags/", json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_patch_dag_http(client: AsyncClient) -> None:
    await client.post("/api/agent-dags/", json={"dagId": "patch-dag", "name": "Old"})
    resp = await client.patch("/api/agent-dags/patch-dag", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_dag_http(client: AsyncClient) -> None:
    await client.post("/api/agent-dags/", json={"dagId": "rm-dag", "name": "Rm"})
    resp = await client.delete("/api/agent-dags/rm-dag")
    assert resp.status_code == 204
