"""Smoke tests for static asset serving via FastAPI StaticFiles (Phase E1)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_index_html(client: AsyncClient) -> None:
    """GET /index.html returns 200 with text/html content type."""
    response = await client.get("/index.html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_nested_css(client: AsyncClient) -> None:
    """GET /css/core/components.css returns 200."""
    response = await client.get("/css/core/components.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_healthz_regression(client: AsyncClient) -> None:
    """GET /healthz still returns ok:true after StaticFiles mount."""
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_nonexistent_returns_404(client: AsyncClient) -> None:
    """GET /nonexistent returns 404."""
    response = await client.get("/nonexistent-file-that-does-not-exist.xyz")
    assert response.status_code == 404
