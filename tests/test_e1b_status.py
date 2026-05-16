"""Phase E1b — /api/_status endpoint tests."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

_MARKETING_OS_SURFACES = {
    "scouts",
    "signal-queue",
    "signal-criteria",
    "campaign-ops",
    "campaign-deliverables",
    "content-assets",
    "approvals",
    "writing-studio",
}

_NODE_ONLY_SURFACES = {
    "sessions",
    # "agents" moved to available_surfaces in Phase F2a
    # "okr-studio" removed from unavailable — Phase H shipped "okr" surface
}


async def test_status_returns_200(client: AsyncClient) -> None:
    """GET /api/_status returns 200."""
    response = await client.get("/api/_status")
    assert response.status_code == 200


async def test_status_json_shape(client: AsyncClient) -> None:
    """Response contains required keys."""
    response = await client.get("/api/_status")
    data = response.json()
    assert "available_surfaces" in data
    assert "unavailable_surfaces" in data
    assert "version" in data
    assert data["available"] is True


async def test_status_marketing_os_surfaces_available(client: AsyncClient) -> None:
    """All marketing-OS surfaces are listed as available."""
    response = await client.get("/api/_status")
    data = response.json()
    available = set(data["available_surfaces"])
    for surface in _MARKETING_OS_SURFACES:
        assert surface in available, f"Expected '{surface}' in available_surfaces"


async def test_status_node_only_surfaces_unavailable(client: AsyncClient) -> None:
    """Node-only surfaces (sessions, agents, okr-studio) are in unavailable_surfaces."""
    response = await client.get("/api/_status")
    data = response.json()
    unavailable = set(data["unavailable_surfaces"])
    for surface in _NODE_ONLY_SURFACES:
        assert surface in unavailable, f"Expected '{surface}' in unavailable_surfaces"


async def test_status_no_overlap(client: AsyncClient) -> None:
    """No surface appears in both available and unavailable lists."""
    response = await client.get("/api/_status")
    data = response.json()
    available = set(data["available_surfaces"])
    unavailable = set(data["unavailable_surfaces"])
    overlap = available & unavailable
    assert not overlap, f"Surfaces in both lists: {overlap}"


async def test_status_no_auth_required(client: AsyncClient) -> None:
    """Endpoint is public — no auth token needed."""
    # If auth were enforced, a bare client (no token) would get 401 or 403.
    response = await client.get("/api/_status")
    assert response.status_code not in (401, 403)
