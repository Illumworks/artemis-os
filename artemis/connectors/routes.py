"""Connectors router — /api/connectors + /api/agents/{id}/connectors.

Endpoints:
  GET    /api/connectors              — list (masked credentials)
  POST   /api/connectors/             — create
  GET    /api/connectors/{id}         — detail (decrypted for owner)
  PATCH  /api/connectors/{id}         — update
  DELETE /api/connectors/{id}         — soft delete (status → disabled)
  DELETE /api/connectors/{id}/permanent — hard delete (connector must be disabled)
  POST   /api/connectors/{id}/test    — validate credentials
  POST   /api/agents/{agent_id}/connectors            — link agent ↔ connector
  DELETE /api/agents/{agent_id}/connectors/{conn_id}  — unlink
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.connectors import repository as repo
from artemis.connectors.encryption import mask_credentials
from artemis.connectors.kinds import CONNECTOR_KINDS, KNOWN_KIND_IDS
from artemis.connectors.models import Connector
from artemis.connectors.schemas import (
    AgentConnectorLink,
    AgentConnectorOut,
    ConnectorCreate,
    ConnectorKindOut,
    ConnectorListItem,
    ConnectorOut,
    ConnectorTestResult,
    ConnectorUpdate,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found

logger = logging.getLogger(__name__)

router = APIRouter(tags=["connectors"], dependencies=[Depends(require_token)])
agents_router = APIRouter(tags=["connectors"], dependencies=[Depends(require_token)])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _connector_to_list_item(row: Connector) -> ConnectorListItem:
    meta: dict[str, Any] = row.metadata_ or {}
    return ConnectorListItem(
        id=str(row.id),
        kind=row.kind,
        name=row.name,
        status=row.status,
        owner_user_id=row.owner_user_id,
        last_validated_at=meta.get("last_validated_at"),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _connector_to_detail(row: Connector, *, masked: bool = False) -> ConnectorOut:
    from artemis.connectors.repository import get_decrypted_credentials

    meta: dict[str, Any] = row.metadata_ or {}
    try:
        raw_creds = get_decrypted_credentials(row)
    except Exception:
        raw_creds = {}
    creds = mask_credentials(raw_creds) if masked else raw_creds
    return ConnectorOut(
        id=str(row.id),
        kind=row.kind,
        name=row.name,
        credentials=creds,
        status=row.status,
        owner_user_id=row.owner_user_id,
        last_validated_at=meta.get("last_validated_at"),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ── Test helpers ──────────────────────────────────────────────────────────────


async def _test_connector(kind: str, creds: dict[str, str]) -> ConnectorTestResult:
    """Make a cheap validation call to the external service."""
    try:
        if kind == "openai":
            api_key = creds.get("api_key", "")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if r.status_code == 200:
                return ConnectorTestResult(ok=True, message="OpenAI API key is valid.")
            return ConnectorTestResult(ok=False, message=f"OpenAI returned HTTP {r.status_code}.")

        if kind == "anthropic":
            api_key = creds.get("api_key", "")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
            if r.status_code in (200, 400):  # 400 = valid key, wrong params
                return ConnectorTestResult(ok=True, message="Anthropic API key is valid.")
            return ConnectorTestResult(
                ok=False, message=f"Anthropic returned HTTP {r.status_code}."
            )

        if kind == "starbridge":
            api_key = creds.get("api_key", "")
            api_url = creds.get("api_url", "").rstrip("/")
            if not api_url:
                return ConnectorTestResult(ok=False, message="api_url is required for Starbridge.")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{api_url}/health",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if r.status_code < 400:
                return ConnectorTestResult(ok=True, message="Starbridge endpoint is reachable.")
            return ConnectorTestResult(
                ok=False, message=f"Starbridge returned HTTP {r.status_code}."
            )

        if kind == "tavily":
            api_key = creds.get("api_key", "")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": api_key, "query": "test", "max_results": 1},
                )
            if r.status_code in (200, 400):
                return ConnectorTestResult(ok=True, message="Tavily API key is valid.")
            return ConnectorTestResult(ok=False, message=f"Tavily returned HTTP {r.status_code}.")

        if kind == "gemini":
            api_key = creds.get("api_key", "")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
                )
            if r.status_code == 200:
                return ConnectorTestResult(ok=True, message="Gemini API key is valid.")
            return ConnectorTestResult(ok=False, message=f"Gemini returned HTTP {r.status_code}.")

        if kind == "vista_social":
            # Vista speaks MCP over HTTP, not REST. The handshake is the cheapest
            # honest proof of reachability: it authenticates and returns the
            # server banner without touching any account data.
            from artemis.connectors.mcp_http import (
                McpError,
                McpHttpClient,
                vista_endpoint_url,
            )

            try:
                url = vista_endpoint_url(creds)
            except ValueError as exc:
                return ConnectorTestResult(ok=False, message=str(exc))
            try:
                async with McpHttpClient(url, timeout=20) as mcp:
                    tools = await mcp.list_tools()
            except McpError as exc:
                # exc never carries the URL — see mcp_http module docstring.
                return ConnectorTestResult(ok=False, message=f"Vista Social: {exc.reason}.")
            return ConnectorTestResult(
                ok=True,
                message=f"Vista Social MCP reachable — {len(tools)} tools available.",
            )

        return ConnectorTestResult(ok=True, message=f"No test configured for kind={kind!r}.")

    except httpx.TimeoutException:
        return ConnectorTestResult(ok=False, message="Connection timed out.")
    except Exception as exc:
        return ConnectorTestResult(ok=False, message=f"Test failed: {exc}")


# ── Kind registry ─────────────────────────────────────────────────────────────


@router.get("/api/connectors/kinds", response_model=list[ConnectorKindOut])
async def list_connector_kinds_endpoint() -> list[ConnectorKindOut]:
    """Expose the kind registry so the UI does not keep its own copy.

    ``public/js/features/integrations.js`` used to hardcode this list, which
    meant adding a kind server-side left it invisible in the "Add connector"
    dropdown — the backend accepted a connector nobody could create. One
    registry, served.
    """
    return [
        ConnectorKindOut(
            id=kind.id,
            label=kind.label,
            fields=list(kind.fields),
            secret_fields=list(kind.secret_fields),
            oauth_managed=kind.oauth_managed,
        )
        for kind in CONNECTOR_KINDS.values()
    ]


# ── Connectors CRUD ────────────────────────────────────────────────────────────


@router.get("/api/connectors", response_model=list[ConnectorListItem])
async def list_connectors_endpoint(
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> list[ConnectorListItem]:
    rows = await repo.list_connectors(session, kind=kind, status=status)
    return [_connector_to_list_item(r) for r in rows]


@router.post("/api/connectors/", response_model=ConnectorOut, status_code=201)
async def create_connector_endpoint(
    body: ConnectorCreate,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> ConnectorOut:
    if body.kind not in KNOWN_KIND_IDS:
        raise bad_request(f"Unknown connector kind: {body.kind!r}")
    row = await repo.create_connector(
        session,
        kind=body.kind,
        name=body.name,
        credentials=body.credentials,
    )
    await session.commit()
    return _connector_to_detail(row)


@router.get("/api/connectors/{connector_id}", response_model=ConnectorOut)
async def get_connector_endpoint(
    connector_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> ConnectorOut:
    try:
        row = await repo.get_connector(session, connector_id)
    except ValueError as exc:
        raise not_found(str(exc)) from exc
    return _connector_to_detail(row, masked=False)


@router.patch("/api/connectors/{connector_id}", response_model=ConnectorOut)
async def update_connector_endpoint(
    connector_id: str,
    body: ConnectorUpdate,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> ConnectorOut:
    try:
        row = await repo.update_connector(
            session,
            connector_id,
            name=body.name,
            credentials=body.credentials,
            status=body.status,
        )
    except ValueError as exc:
        raise not_found(str(exc)) from exc
    await session.commit()
    return _connector_to_detail(row)


@router.delete("/api/connectors/{connector_id}", status_code=204)
async def soft_delete_connector_endpoint(
    connector_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> None:
    try:
        await repo.soft_delete_connector(session, connector_id)
    except ValueError as exc:
        raise not_found(str(exc)) from exc
    await session.commit()


@router.delete("/api/connectors/{connector_id}/permanent", status_code=204)
async def permanent_delete_connector_endpoint(
    connector_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> None:
    try:
        await repo.permanent_delete_connector(session, connector_id)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    await session.commit()


@router.post("/api/connectors/{connector_id}/test", response_model=ConnectorTestResult)
async def test_connector_endpoint(
    connector_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> ConnectorTestResult:
    try:
        row = await repo.get_connector(session, connector_id)
    except ValueError as exc:
        raise not_found(str(exc)) from exc
    try:
        creds = repo.get_decrypted_credentials(row)
    except Exception as exc:
        return ConnectorTestResult(ok=False, message=f"Could not decrypt credentials: {exc}")
    result = await _test_connector(row.kind, creds)
    await repo.mark_validated(session, connector_id, success=result.ok)
    await session.commit()
    return result


# ── Agent ↔ Connector linking ──────────────────────────────────────────────────


@agents_router.post(
    "/api/agents/{agent_id}/connectors",
    response_model=AgentConnectorOut,
    status_code=201,
)
async def link_agent_connector_endpoint(
    agent_id: int,
    body: AgentConnectorLink,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> AgentConnectorOut:
    # Verify connector exists
    try:
        await repo.get_connector(session, body.connector_id)
    except ValueError as exc:
        raise not_found(str(exc)) from exc
    row = await repo.link_agent_connector(
        session,
        agent_id=agent_id,
        connector_id=body.connector_id,
        tool_namespace=body.tool_namespace,
    )
    await session.commit()
    return AgentConnectorOut(
        agent_id=row.agent_id,
        connector_id=str(row.connector_id),
        tool_namespace=row.tool_namespace,
    )


@agents_router.delete(
    "/api/agents/{agent_id}/connectors/{connector_id}",
    status_code=204,
)
async def unlink_agent_connector_endpoint(
    agent_id: int,
    connector_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> None:
    await repo.unlink_agent_connector(session, agent_id=agent_id, connector_id=connector_id)
    await session.commit()


@agents_router.get(
    "/api/agents/{agent_id}/connectors",
    response_model=list[AgentConnectorOut],
)
async def list_agent_connectors_endpoint(
    agent_id: int,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> list[AgentConnectorOut]:
    rows = await repo.list_agent_connectors(session, agent_id)
    return [
        AgentConnectorOut(
            agent_id=r.agent_id,
            connector_id=str(r.connector_id),
            tool_namespace=r.tool_namespace,
        )
        for r in rows
    ]
