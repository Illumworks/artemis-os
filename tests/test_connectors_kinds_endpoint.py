"""The connector kind registry must be served, not duplicated in the frontend.

`public/js/features/integrations.js` used to hardcode its own copy of the kind
list. Adding a kind server-side therefore left it invisible in the "Add
connector" dropdown: the API would accept the new kind, but no one could create
one through the UI. These tests pin the endpoint that removes the duplication —
including that every registry kind reaches it, so the next kind added does not
need this lesson learned again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from artemis.config import settings
from artemis.connectors.kinds import API_MANAGED_KIND_IDS, CONNECTOR_KINDS

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "token", None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_kinds_endpoint_returns_whole_registry(client: AsyncClient) -> None:
    res = await client.get("/api/connectors/kinds")
    assert res.status_code == 200
    returned = {k["id"] for k in res.json()}
    assert returned == set(CONNECTOR_KINDS)


async def test_vista_social_is_present_and_asks_for_the_link(client: AsyncClient) -> None:
    """Vista hands out a link, not a bare key — the form must ask for that."""
    res = await client.get("/api/connectors/kinds")
    vista = next(k for k in res.json() if k["id"] == "vista_social")
    assert vista["label"] == "Vista Social"
    assert vista["fields"] == ["mcp_url"]
    assert vista["oauth_managed"] is False


async def test_oauth_managed_kinds_are_flagged(client: AsyncClient) -> None:
    """The UI hides these from the create form; the flag is how it knows."""
    payload = {k["id"]: k for k in (await client.get("/api/connectors/kinds")).json()}
    assert payload["slack"]["oauth_managed"] is True
    assert payload["google_calendar"]["oauth_managed"] is True
    for kind_id in API_MANAGED_KIND_IDS:
        assert payload[kind_id]["oauth_managed"] is False


async def test_every_creatable_kind_declares_fields(client: AsyncClient) -> None:
    """A creatable kind with no fields renders an empty form — always a bug."""
    for kind in (await client.get("/api/connectors/kinds")).json():
        if not kind["oauth_managed"]:
            assert kind["fields"], f"{kind['id']} is creatable but declares no fields"


async def test_kinds_route_is_not_shadowed_by_the_detail_route(client: AsyncClient) -> None:
    """`/kinds` must not be parsed as a connector id by `/api/connectors/{id}`."""
    res = await client.get("/api/connectors/kinds")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


async def test_kinds_endpoint_never_returns_credentials(client: AsyncClient) -> None:
    """It advertises field *names*; values live only in the encrypted store."""
    body = (await client.get("/api/connectors/kinds")).text
    assert "credentials" not in body
    assert "blob" not in body


async def test_secret_fields_are_declared_for_every_credential(client: AsyncClient) -> None:
    """Sensitivity is declared, not inferred from the field name.

    The modal used to pick `type=password` by testing whether the field name
    contained "key" or "secret". Vista's field is `mcp_url` — a URL with the
    API key inside it — so name-sniffing would have rendered a live credential
    as visible text.
    """
    payload = {k["id"]: k for k in (await client.get("/api/connectors/kinds")).json()}
    assert payload["vista_social"]["secret_fields"] == ["mcp_url"]
    # A non-secret field stays visible: Starbridge's api_url is just an address.
    assert payload["starbridge"]["secret_fields"] == ["api_key"]
    assert "api_url" not in payload["starbridge"]["secret_fields"]


async def test_secret_fields_are_a_subset_of_fields(client: AsyncClient) -> None:
    for kind in (await client.get("/api/connectors/kinds")).json():
        assert set(kind["secret_fields"]) <= set(kind["fields"]), kind["id"]
