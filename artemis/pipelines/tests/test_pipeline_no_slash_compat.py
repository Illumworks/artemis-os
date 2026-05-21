"""No-slash compatibility tests for Pipelines (PIPE1).

Both /api/pipelines and /api/pipelines/ must return 200 (J10 invariant).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_no_slash(client: AsyncClient) -> None:
    """GET /api/pipelines (no trailing slash) returns 200."""
    resp = await client.get("/api/pipelines")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_with_slash(client: AsyncClient) -> None:
    """GET /api/pipelines/ (trailing slash) returns 200."""
    resp = await client.get("/api/pipelines/")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)
