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
    CAMPAIGN_DELIVERABLES_PIPELINE_ID,
    MANUAL_TRIGGER_CONFIG,
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


async def _deliverables_row(session: AsyncSession) -> dict[str, Any]:
    return dict(
        (await session.execute(SELECT_PIPELINE, {"id": CAMPAIGN_DELIVERABLES_PIPELINE_ID}))
        .mappings()
        .one()
    )


async def test_seed_loads_idempotently_and_writes_expected_graph(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _reset_and_insert_agents(db_session)
    first = await seed_marketing_pipeline(db_session)
    created_at = (await _pipeline_row(db_session))["created_at"]
    deliverables_created_at = (await _deliverables_row(db_session))["created_at"]
    second = await seed_marketing_pipeline(db_session)
    row = await _pipeline_row(db_session)
    deliverables_row = await _deliverables_row(db_session)
    nodes = row["nodes"]
    edges = row["edges"]
    deliverable_nodes = deliverables_row["nodes"]
    deliverable_edges = deliverables_row["edges"]
    node_ids = {node["id"] for node in nodes}
    nodes_by_id = {node["id"]: node for node in nodes}
    agent_ids = [node["config"]["agent_id"] for node in nodes if node["type"] == "agent_invocation"]
    deliverable_agent_ids = [
        node["config"]["agent_id"]
        for node in deliverable_nodes
        if node["type"] == "agent_invocation"
    ]
    deliverable_ids = {"deliverable_outreach_email"}

    assert (first["inserted"], second["inserted"]) == (2, 0)
    assert row["created_at"] == created_at and row["owner_user_id"] is None
    assert deliverables_row["created_at"] == deliverables_created_at
    assert row["name"] == "Marketing Pipeline"
    assert deliverables_row["name"] == "Marketing Campaign Deliverables"
    assert row["trigger_config"] == TRIGGER_CONFIG
    assert deliverables_row["trigger_config"] == MANUAL_TRIGGER_CONFIG
    # 12/19, down from 14/21: gate_1_signals_inbox and content_brief_assembler
    # were removed on 2026-08-12 (owner decision -- see build_marketing_pipeline's
    # comment). marketing.main now ENDS at qualification.
    assert len(nodes) == 12 and len(edges) == 19
    assert len(deliverable_nodes) == 5 and len(deliverable_edges) == 4
    assert set(agent_ids + deliverable_agent_ids) == set(AGENT_IDS)
    assert {
        "trigger_scheduled": "trigger_scheduled",
    }.items() <= {node["id"]: node["type"] for node in nodes}.items()
    # The removed pair, asserted ABSENT rather than merely dropped from the map
    # above: a blocking human gate reappearing here is the specific regression
    # worth catching. That gate suspended the pipeline per signal, and because
    # its approver emails did not exist it stayed suspended for 57 days and
    # silently blocked every later scheduled run.
    assert "gate_1_signals_inbox" not in node_ids
    assert "content_brief_assembler" not in node_ids
    assert not [node for node in nodes if node["type"] == "human_gate"], (
        "marketing.main must have no human gate -- the human touchpoint is "
        "Callie's daily market-signals brief, and a brief cannot unblock a gate"
    )
    # Qualification is the end of the line: nothing downstream of it.
    assert "qualifier_brief_composer" not in {edge["source_node_id"] for edge in edges}
    assert {
        "trigger_manual": "trigger_manual",
        "gate_2_approval_drawer": "human_gate",
    }.items() <= {node["id"]: node["type"] for node in deliverable_nodes}.items()
    assert deliverable_ids <= {node["id"] for node in deliverable_nodes}
    assert nodes_by_id["qualifier_cross_reference"]["label"] == "Cross-Reference (Phase 1→2→3)"
    deliverables_by_id = {node["id"]: node for node in deliverable_nodes}
    assert {
        deliverables_by_id[node_id]["config"]["deliverable_type_slug"]
        for node_id in deliverable_ids
    } == {
        "outreach_email",
    }
    assert {
        edge["source_node_id"]
        for edge in deliverable_edges
        if edge["target_node_id"] == "gate_2_approval_drawer"
    } == deliverable_ids
    assert all(
        edge["source_node_id"] in node_ids and edge["target_node_id"] in node_ids for edge in edges
    )
    deliverable_node_ids = {node["id"] for node in deliverable_nodes}
    assert all(
        edge["source_node_id"] in deliverable_node_ids
        and edge["target_node_id"] in deliverable_node_ids
        for edge in deliverable_edges
    )
    response = await client.get("/api/pipelines")
    listed = [item for item in response.json() if item["id"] == PIPELINE_ID]
    deliverable_listed = [
        item for item in response.json() if item["id"] == CAMPAIGN_DELIVERABLES_PIPELINE_ID
    ]
    assert response.status_code == 200
    assert listed and listed[0]["status"] == "active"
    assert deliverable_listed and deliverable_listed[0]["status"] == "active"
    assert listed[0]["latestRun"] is None


async def test_missing_agents_error_points_to_m5_seed(db_session: AsyncSession) -> None:
    await _reset_and_insert_agents(db_session, agents=False)
    with pytest.raises(RuntimeError, match="M5 marketing agents"):
        await seed_marketing_pipeline(db_session)
