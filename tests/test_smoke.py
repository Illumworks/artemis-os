"""Phase A smoke tests — process is alive, routing works, root responds.

readyz is intentionally excluded until B1 schema lands and we have a real
database to probe; testing it now would just verify Postgres is up, which is
infrastructure rather than code under test.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_root(client: AsyncClient) -> None:
    # Phase E1: GET / now serves the static frontend (index.html) via
    # StaticFiles(html=True). The JSON root handler was removed when
    # StaticFiles was mounted. The shell should return 200 with text/html.
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
