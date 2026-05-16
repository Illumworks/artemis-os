"""Phase F3 — static asset smoke tests for builder frontend modules.

Verifies that the key JS files wired in F3 are served correctly by the
FastAPI static-files mount.  No browser execution — just confirms the
files exist and get the right content-type.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _is_js(content_type: str) -> bool:
    return "javascript" in content_type or "text/plain" in content_type


async def test_agents_js_200(client: AsyncClient) -> None:
    """GET /js/features/agents.js returns 200 with JavaScript content type."""
    response = await client.get("/js/features/agents.js")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    ct = response.headers.get("content-type", "")
    assert _is_js(ct), f"Expected JS content-type, got: {ct}"


async def test_workflows_js_200(client: AsyncClient) -> None:
    """GET /js/features/workflows.js returns 200 with JavaScript content type."""
    response = await client.get("/js/features/workflows.js")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    ct = response.headers.get("content-type", "")
    assert _is_js(ct), f"Expected JS content-type, got: {ct}"


async def test_agent_modal_js_200(client: AsyncClient) -> None:
    """GET /js/components/agent-modal.js returns 200."""
    response = await client.get("/js/components/agent-modal.js")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


async def test_dag_editor_js_200(client: AsyncClient) -> None:
    """GET /js/features/dag-editor.js returns 200."""
    response = await client.get("/js/features/dag-editor.js")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


async def test_skill_edit_modal_js_200(client: AsyncClient) -> None:
    """GET /js/features/skill-edit-modal.js returns 200."""
    response = await client.get("/js/features/skill-edit-modal.js")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


async def test_api_js_contains_normalise_agents(client: AsyncClient) -> None:
    """api.js contains the F3 adapter helpers confirming the wiring landed."""
    response = await client.get("/js/core/api.js")
    assert response.status_code == 200
    text = response.text
    assert "_normaliseAgent" in text, "F3 agent normaliser missing from api.js"
    assert "_normaliseChain" in text, "F3 chain normaliser missing from api.js"
    assert "_normaliseDag" in text, "F3 dag normaliser missing from api.js"
    assert "_normaliseWorkflow" in text, "F3 workflow normaliser missing from api.js"


async def test_api_js_chains_use_correct_prefix(client: AsyncClient) -> None:
    """api.js fetches chains from /api/agent-chains not /api/agents/chains."""
    response = await client.get("/js/core/api.js")
    assert response.status_code == 200
    text = response.text
    assert '"/api/agent-chains"' in text, (
        "api.js must fetch chains from /api/agent-chains (Python F2a prefix)"
    )
    assert '"/api/agent-dags"' in text, (
        "api.js must fetch DAGs from /api/agent-dags (Python F2a prefix)"
    )


async def test_api_js_run_fallback_message(client: AsyncClient) -> None:
    """api.js includes the graceful F2b fallback message for run endpoints."""
    response = await client.get("/js/core/api.js")
    assert response.status_code == 200
    text = response.text
    assert "Run not yet wired (Phase F2b in progress)" in text, (
        "api.js must include graceful fallback message for 404 run endpoints"
    )
