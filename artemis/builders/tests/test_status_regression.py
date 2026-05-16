"""Status endpoint regression — confirms new surfaces are in available_surfaces."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

_NEW_SURFACES = {"agents", "skills", "workflows", "agent-chains", "agent-dags", "agent-runs"}
_REMOVED_FROM_UNAVAILABLE = {"agents", "skills", "workflows"}


@pytest.mark.asyncio
async def test_status_new_surfaces_available(client: AsyncClient) -> None:
    resp = await client.get("/api/_status")
    assert resp.status_code == 200
    data = resp.json()
    available = set(data["available_surfaces"])
    for surface in _NEW_SURFACES:
        assert surface in available, f"Expected '{surface}' in available_surfaces"


@pytest.mark.asyncio
async def test_status_new_surfaces_not_in_unavailable(client: AsyncClient) -> None:
    resp = await client.get("/api/_status")
    assert resp.status_code == 200
    data = resp.json()
    unavailable = set(data["unavailable_surfaces"])
    for surface in _NEW_SURFACES:
        assert surface not in unavailable, (
            f"'{surface}' should not be in unavailable_surfaces after F2a"
        )


@pytest.mark.asyncio
async def test_status_available_flag(client: AsyncClient) -> None:
    resp = await client.get("/api/_status")
    assert resp.status_code == 200
    assert resp.json()["available"] is True


@pytest.mark.asyncio
async def test_status_existing_marketing_surfaces_still_present(client: AsyncClient) -> None:
    resp = await client.get("/api/_status")
    assert resp.status_code == 200
    available = set(resp.json()["available_surfaces"])
    for surface in {"scouts", "signal-queue", "campaign-ops", "writing-studio"}:
        assert surface in available
