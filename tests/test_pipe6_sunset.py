"""PIPE6 sunset coverage for Workflows + Automations."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from httpx import AsyncClient

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/0053_pipe6_workflows_automations_migrate.py"
NAVIGATION_JS = ROOT / "public/js/core/navigation.js"
OPERATIONS_JS = ROOT / "public/js/features/operations-shell.js"
SITE_MAP = ROOT / "docs/SITE-MAP.md"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("pipe6_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0053_chains_after_0052() -> None:
    module = _load_migration_module()
    assert module.revision == "0053"
    assert module.down_revision == "0052"


def test_workflow_steps_transform_to_pipeline_nodes_and_edges() -> None:
    module = _load_migration_module()
    steps = [{"label": "noop", "prompt": "say hello"}, {"prompt": "say goodbye"}]

    nodes = module._build_nodes(steps)
    edges = module._build_edges(steps)

    assert nodes[0]["type"] == "trigger_manual"
    assert nodes[1]["type"] == "skill_call"
    assert nodes[1]["config"]["prompt"] == "say hello"
    assert nodes[2]["config"]["legacy_workflow_step"] == {"prompt": "say goodbye"}
    assert edges == [
        {
            "id": "edge_manual_trigger_to_workflow_step_1",
            "source_node_id": "manual_trigger",
            "target_node_id": "workflow_step_1",
            "condition": None,
            "data_shape": None,
        },
        {
            "id": "edge_workflow_step_1_to_workflow_step_2",
            "source_node_id": "workflow_step_1",
            "target_node_id": "workflow_step_2",
            "condition": None,
            "data_shape": None,
        },
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_automations_routes_return_410(client: AsyncClient) -> None:
    response = await client.get("/api/automations")
    body = response.json()
    assert response.status_code == 410
    assert body["error"] == "automations_deprecated"
    assert body["redirect_to"] == "/api/pipelines"


@pytest.mark.asyncio(loop_scope="session")
async def test_workflows_routes_return_410(client: AsyncClient) -> None:
    response = await client.get("/api/workflows")
    body = response.json()
    assert response.status_code == 410
    assert body["error"] == "workflows_deprecated"
    assert body["redirect_to"] == "/api/pipelines"


@pytest.mark.asyncio(loop_scope="session")
async def test_workflow_run_route_returns_410(client: AsyncClient) -> None:
    response = await client.post("/api/workflows/codex-smoke-workflow/run")
    body = response.json()
    assert response.status_code == 410
    assert body["error"] == "workflows_deprecated"
    assert body["redirect_to"] == "/api/pipelines"


def test_operations_sidebar_nav_excludes_deprecated_surfaces() -> None:
    src = NAVIGATION_JS.read_text()
    assert 'id: "workflows"' not in src
    assert 'label: "Workflows"' not in src
    assert 'id: "automations"' not in src
    assert 'label: "Automations"' not in src


def test_operations_overview_no_longer_links_deprecated_surfaces() -> None:
    src = OPERATIONS_JS.read_text()
    assert 'renderOpsButton("Open Workflows"' not in src
    assert 'renderOpsButton("Open Automations"' not in src
    assert 'case "workflows":' not in src
    assert 'case "automations":' not in src


def test_site_map_records_pipe6_deprecated_surfaces() -> None:
    src = SITE_MAP.read_text()
    operations_section = src.split("### Operations", 1)[1].split("### Marketing", 1)[0]
    assert "| **Automations** |" not in operations_section
    assert "| **Workflows** |" not in operations_section
    assert "## Deprecated surfaces (sunset in PIPE6, 2026-05-30)" in src
