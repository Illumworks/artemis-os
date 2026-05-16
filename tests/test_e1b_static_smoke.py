"""Phase E1b — static asset smoke tests for E1b-added frontend files."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_index_html_200(client: AsyncClient) -> None:
    """GET /index.html returns 200 with HTML content type."""
    response = await client.get("/index.html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_status_js_200(client: AsyncClient) -> None:
    """GET /js/core/status.js returns 200 with JavaScript content type."""
    response = await client.get("/js/core/status.js")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "javascript" in content_type or "text/plain" in content_type, (
        f"Expected JS content-type, got: {content_type}"
    )


async def test_api_js_200(client: AsyncClient) -> None:
    """GET /js/core/api.js returns 200 with JavaScript content type."""
    response = await client.get("/js/core/api.js")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "javascript" in content_type or "text/plain" in content_type, (
        f"Expected JS content-type, got: {content_type}"
    )
