"""Pipeline JSON export/import portability tests."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders.models import Agent
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration
from artemis.pipelines import repository as repo

_AGENT_ID = "marketing.scout.starbridge_researcher"

_NODE: dict[str, Any] = {
    "id": "n1",
    "type": "agent_invocation",
    "label": "Scout",
    "config": {
        "agent_id": _AGENT_ID,
        "connector_kind": "starbridge",
    },
    "position": {"x": 0, "y": 0},
}


async def _seed_agent(session: AsyncSession, agent_id: str = _AGENT_ID) -> None:
    session.add(
        Agent(
            agent_id=agent_id,
            name="Starbridge Researcher",
            system_prompt="Find relevant Starbridge signals.",
            tools=["starbridge.search"],
            model="claude-haiku-4-5",
            provider="claude-code",
            fallback_provider="anthropic",
            fallback_model="claude-haiku-4-5",
            memory_policy="persistent",
            permission_mode="auto",
        )
    )


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {str(k) for k in value}
        for nested in value.values():
            found.update(_all_keys(nested))
        return found
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


@pytest.mark.asyncio
async def test_export_contains_agents_connectors_and_no_credentials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    async with db_session.begin():
        await _seed_agent(db_session)
        pipeline = await repo.create_pipeline(
            db_session,
            name="Marketing Pipeline",
            nodes=[{**_NODE, "config": {**_NODE["config"], "api_key": "never-export"}}],
            edges=[],
            metadata_={"token": "never-export", "safe": True},
        )

    response = await client.get(f"/api/pipelines/{pipeline.id}/export")
    assert response.status_code == 200
    bundle = response.json()
    assert bundle["format_version"] == "1"
    assert bundle["pipeline"]["name"] == "Marketing Pipeline"
    assert bundle["agents_required"][0]["agent_id"] == _AGENT_ID
    assert bundle["connectors_required"][0]["kind"] == "starbridge"
    assert not (_all_keys(bundle) & {"api_key", "secret", "token"})


@pytest.mark.asyncio
async def test_import_creates_pipeline_and_missing_agent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    bundle = {
        "format_version": "1",
        "exported_at": "2026-05-22T12:00:00Z",
        "pipeline": {"name": "Imported", "nodes": [_NODE], "edges": [], "status": "active"},
        "agents_required": [
            {
                "agent_id": _AGENT_ID,
                "name": "Starbridge Researcher",
                "tools": ["starbridge.search"],
                "model": "claude-haiku-4-5",
                "provider": "claude-code",
                "memory_policy": "persistent",
                "permission_mode": "auto",
            }
        ],
        "connectors_required": [],
    }
    response = await client.post("/api/pipelines/import", json=bundle)
    assert response.status_code == 201
    data = response.json()
    assert data["agents_created"] == [_AGENT_ID]
    assert data["agents_skipped"] == []

    pipeline = await repo.get_pipeline(db_session, data["pipeline_id"])
    assert pipeline.name == "Imported"
    assert pipeline.nodes == [_NODE]


@pytest.mark.asyncio
async def test_import_skips_existing_agents(client: AsyncClient, db_session: AsyncSession) -> None:
    async with db_session.begin():
        await _seed_agent(db_session, "existing-agent")
    bundle = {
        "format_version": "1",
        "exported_at": "2026-05-22T12:00:00Z",
        "pipeline": {"name": "Imported", "nodes": [], "edges": [], "status": "active"},
        "agents_required": [
            {
                "agent_id": "existing-agent",
                "name": "Should Not Overwrite",
                "tools": [],
                "model": "new-model",
                "provider": "new-provider",
            }
        ],
        "connectors_required": [],
    }
    response = await client.post("/api/pipelines/import", json=bundle)
    assert response.status_code == 201
    assert response.json()["agents_skipped"] == ["existing-agent"]
    agent = await db_session.get(Agent, 1)
    assert agent is not None
    assert agent.name == "Starbridge Researcher"


@pytest.mark.asyncio
async def test_import_missing_connector_pauses_pipeline(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    bundle = {
        "format_version": "1",
        "exported_at": "2026-05-22T12:00:00Z",
        "pipeline": {"name": "Needs Connector", "nodes": [], "edges": [], "status": "active"},
        "agents_required": [],
        "connectors_required": [{"kind": "starbridge", "label": "Starbridge", "fields_needed": []}],
    }
    response = await client.post("/api/pipelines/import", json=bundle)
    assert response.status_code == 201
    data = response.json()
    assert "No starbridge connector configured" in data["import_warnings"][0]
    pipeline = await repo.get_pipeline(db_session, data["pipeline_id"])
    assert pipeline.status == "paused"
    metadata = pipeline.metadata_ or {}
    assert metadata["import_warnings"] == data["import_warnings"]


@pytest.mark.asyncio
async def test_export_import_round_trip_preserves_graph(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    async with db_session.begin():
        await _seed_agent(db_session)
        db_session.add(
            Integration(
                provider="starbridge",
                workspace_id="default",
                encrypted_credentials=encrypt_credentials({"api_key": "local"}),
                status="active",
            )
        )
        pipeline = await repo.create_pipeline(
            db_session, name="Source", nodes=[_NODE], edges=[], status="active"
        )

    exported = (await client.get(f"/api/pipelines/{pipeline.id}/export")).json()
    imported = await client.post("/api/pipelines/import", json=exported)
    assert imported.status_code == 201
    imported_pipeline = await repo.get_pipeline(db_session, imported.json()["pipeline_id"])
    assert imported_pipeline.id != pipeline.id
    assert imported_pipeline.nodes == pipeline.nodes
    assert imported_pipeline.edges == pipeline.edges
    assert imported_pipeline.status == "active"


@pytest.mark.asyncio
async def test_invalid_json_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/api/pipelines/import",
        content="{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_older_format_version_returns_upgrade_error(client: AsyncClient) -> None:
    response = await client.post(
        "/api/pipelines/import",
        json={
            "format_version": "0",
            "exported_at": "2026-05-22T12:00:00Z",
            "pipeline": {"name": "Old", "nodes": [], "edges": []},
        },
    )
    assert response.status_code == 422
    assert "Format upgrade required" in response.text
