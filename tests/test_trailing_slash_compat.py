"""J10 — Trailing-slash compatibility tests (route-introspection based).

Walks ``app.routes`` to find every `/api/<resource>/` list endpoint
(single-segment under ``/api/``, GET method, trailing slash), then asserts
each one also accepts the no-slash form. A new list endpoint added
without an `@router.get("")` alias is caught here automatically — no
hand-maintained list to drift.

Why per-router aliases instead of a global 404-retry middleware: the
earlier middleware approach swallowed handler-emitted 404s (e.g.
``{"code":"agent_not_found"}``). It would re-dispatch the request with a
trailing slash, hit no route, and replace the handler's body with
Starlette's default ``{"detail":"Not Found"}``. Per-router aliases are
targeted and never touch any other 404 path.

Session-scoped loop: the parametrized cases share one asyncio event loop
so the SQLAlchemy connection pool (which caches connections) stays bound
to one loop throughout.
"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

from artemis.main import app

# Matches single-segment list endpoints under /api/: e.g. /api/agents/,
# /api/signal-queue/. Excludes anything with a path parameter or extra
# nesting (e.g. /api/agents/{id}/files).
_LIST_ENDPOINT_RE = re.compile(r"^/api/[A-Za-z][A-Za-z0-9-]*/$")


def _discover_list_endpoint_paths() -> list[str]:
    seen: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path and "GET" in methods and _LIST_ENDPOINT_RE.match(path):
            seen.add(path)
    return sorted(seen)


_LIST_PATHS = _discover_list_endpoint_paths()

# Guardrail: if the introspection heuristic ever silently drops the test
# surface (renamed routes, regex no longer matches), fail loudly rather
# than passing zero parametrized cases.
_EXPECTED_LIST_ENDPOINTS = {
    "/api/agents/",
    "/api/skills/",
    "/api/workflows/",
    "/api/agent-chains/",
    "/api/agent-dags/",
    "/api/agent-runs/",
    "/api/approvals/",
    "/api/signal-queue/",
    "/api/content-assets/",
}


def test_introspection_finds_expected_list_endpoints() -> None:
    missing = _EXPECTED_LIST_ENDPOINTS - set(_LIST_PATHS)
    assert not missing, (
        f"route-introspection missed expected list endpoints: {missing}. "
        "Either the routes were renamed (update _EXPECTED_LIST_ENDPOINTS) or "
        "the regex no longer matches their pattern."
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("slashed_path", _LIST_PATHS)
async def test_slash_parity(client: AsyncClient, slashed_path: str) -> None:
    """No-slash form returns the same status as the trailing-slash form (never 404)."""
    no_slash = slashed_path.rstrip("/")
    r_no = await client.get(no_slash)
    r_yes = await client.get(slashed_path)

    assert r_no.status_code != 404, (
        f'no-slash form {no_slash!r} returned 404 — missing `@router.get("")` '
        f"alias on the handler for {slashed_path!r}"
    )
    assert r_no.status_code == r_yes.status_code, (
        f"{no_slash!r} → {r_no.status_code} but {slashed_path!r} → {r_yes.status_code}"
    )
