"""J10 — Trailing-slash compatibility tests.

Verifies that all 8 list endpoints return the same HTTP status code for both
the no-slash form (/api/agents) and the canonical trailing-slash form
(/api/agents/).  The actual status code may be 200 or 422 (if required query
params are absent) but must NEVER be 404 — that would indicate the
TrailingSlashCompatMiddleware is not firing.

Session-scoped loop: the parametrized cases share a single asyncio event loop
so that the SQLAlchemy connection pool (which caches connections) stays bound
to one loop throughout.  Function-scoped loops cause "Future attached to a
different loop" errors when pool connections are handed between cases.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio(loop_scope="session")

# (no-slash path, trailing-slash path)
_ENDPOINT_PAIRS = [
    ("/api/agents", "/api/agents/"),
    ("/api/skills", "/api/skills/"),
    ("/api/workflows", "/api/workflows/"),
    ("/api/agent-chains", "/api/agent-chains/"),
    ("/api/agent-dags", "/api/agent-dags/"),
    ("/api/approvals?status=pending", "/api/approvals/?status=pending"),
    ("/api/signal-queue?status=in_inbox", "/api/signal-queue/?status=in_inbox"),
    ("/api/content-assets?status=draft", "/api/content-assets/?status=draft"),
]


@pytest.mark.parametrize("no_slash,with_slash", _ENDPOINT_PAIRS)
async def test_slash_parity(client: AsyncClient, no_slash: str, with_slash: str) -> None:
    """No-slash and trailing-slash forms return identical status codes (never 404)."""
    r_no = await client.get(no_slash)
    r_yes = await client.get(with_slash)

    assert r_no.status_code != 404, (
        f"no-slash form {no_slash!r} returned 404 — middleware not working"
    )
    assert r_no.status_code == r_yes.status_code, (
        f"{no_slash!r} → {r_no.status_code} but {with_slash!r} → {r_yes.status_code}"
    )
