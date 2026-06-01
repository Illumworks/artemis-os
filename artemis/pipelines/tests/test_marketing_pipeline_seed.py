from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.builders.models  # noqa: F401
import artemis.pipelines.models  # noqa: F401
from artemis.pipelines.seeds.marketing_pipeline import (
    AGENT_IDS,
    PIPELINE_ID,
    TRIGGER_CONFIG,
    seed_marketing_pipeline,
)

pytestmark = pytest.mark.asyncio
TRUNCATE = text(
    "TRUNCATE pipeline_runs, pipelines, agent_context, agent_run_trajectory_summaries, "
    "definition_proposals, agent_runs, agent_skills, agents RESTART IDENTITY CASCADE"
)
SELECT_PIPELINE = text("SELECT * FROM pipelines WHERE id = :id")


async def _reset_and_insert_agents(session: AsyncSession, *, agents: bool = True) -> None:
    await session.execute(TRUNCATE)
    if agents:
        await session.execute(
            text(
                "INSERT INTO agents (agent_id, name, tools, model, provider) "
                "VALUES (:agent_id, :agent_id, '[]'::jsonb, 'claude-haiku-4-5', 'claude-code')"
            ),
            [{"agent_id": agent_id} for agent_id in AGENT_IDS],
        )
    await session.commit()


async def _pipeline_row(session: AsyncSession) -> dict[str, Any]:
    return dict((await session.execute(SELECT_PIPELINE, {"id": PIPELINE_ID})).mappings().one())


async def test_seed_loads_idempotently_and_writes_expected_graph(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _reset_and_insert_agents(db_session)
    first = await seed_marketing_pipeline(db_session)
    created_at = (await _pipeline_row(db_session))["created_at"]
    second = await seed_marketing_pipeline(db_session)
    row = await _pipeline_row(db_session)
    nodes = row["nodes"]
    edges = row["edges"]
    node_ids = {node["id"] for node in nodes}
    nodes_by_id = {node["id"]: node for node in nodes}
    agent_ids = [node["config"]["agent_id"] for node in nodes if node["type"] == "agent_invocation"]
    deliverable_ids = {"deliverable_outreach_email"}

    assert (first["inserted"], second["inserted"]) == (1, 0)
    assert row["created_at"] == created_at and row["owner_user_id"] is None
    assert row["name"] == "Marketing Pipeline"
    assert row["trigger_config"] == TRIGGER_CONFIG
    assert len(nodes) == 19 and len(edges) == 26
    assert set(agent_ids) == set(AGENT_IDS)
    assert {
        "trigger_scheduled": "trigger_scheduled",
        "gate_1_signals_inbox": "human_gate",
        "gate_campaign_initiation": "human_gate",
        "gate_2_approval_drawer": "human_gate",
    }.items() <= {node["id"]: node["type"] for node in nodes}.items()
    assert deliverable_ids <= node_ids
    assert nodes_by_id["qualifier_cross_reference"]["label"] == "Cross-Reference (Phase 1→2→3)"
    assert {
        nodes_by_id[node_id]["config"]["deliverable_type_slug"] for node_id in deliverable_ids
    } == {
        "outreach_email",
    }
    assert [
        edge["target_node_id"]
        for edge in edges
        if edge["source_node_id"] == "content_writing_studio_adapter"
    ] == ["deliverable_outreach_email"]
    assert {
        edge["source_node_id"]
        for edge in edges
        if edge["target_node_id"] == "gate_2_approval_drawer"
    } == deliverable_ids
    assert all(
        edge["source_node_id"] in node_ids and edge["target_node_id"] in node_ids for edge in edges
    )
    response = await client.get("/api/pipelines")
    listed = [item for item in response.json() if item["id"] == PIPELINE_ID]
    assert response.status_code == 200
    assert listed and listed[0]["status"] == "active"
    assert listed[0]["latestRun"] is None


async def test_missing_agents_error_points_to_m5_seed(db_session: AsyncSession) -> None:
    await _reset_and_insert_agents(db_session, agents=False)
    with pytest.raises(RuntimeError, match="M5 marketing agents"):
        await seed_marketing_pipeline(db_session)
