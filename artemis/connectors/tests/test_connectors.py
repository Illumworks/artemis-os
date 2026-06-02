"""Tests for the Connectors domain.

Covers:
- Encryption round-trip
- CRUD: create, read, update, soft-delete, permanent-delete
- Agent linking / unlinking
- Runtime resolver: returns credentials, raises ConnectorNotConfigured
- Soft-delete preserves row; permanent-delete requires disabled status
- HTTP routes via ASGI client
"""

from __future__ import annotations

import os
import unittest.mock as mock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure the test encryption key is available before any connector import.
# Must be a valid URL-safe base64-encoded 32-byte Fernet key.
os.environ.setdefault("ARTEMIS_CONNECTOR_KEY", "ANhAReCrCl6MzfiaGMDwgkCZj0rDFY2HottqRIbbd_M=")

from artemis.connectors import repository as repo
from artemis.connectors.encryption import (
    decrypt_credentials,
    encrypt_credentials,
    mask_credentials,
)
from artemis.connectors.resolver import ConnectorNotConfigured, get_credentials_for_tool

# ── Encryption unit tests ─────────────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip() -> None:
    payload = {"api_key": "sk-test-1234", "organization": "org-xyz"}
    blob = encrypt_credentials(payload)
    assert isinstance(blob, str)
    assert "sk-test" not in blob  # not plaintext
    recovered = decrypt_credentials(blob)
    assert recovered == payload


def test_mask_credentials() -> None:
    masked = mask_credentials({"api_key": "sk-real", "url": "https://example.com"})
    assert all(v == "***" for v in masked.values())
    assert set(masked.keys()) == {"api_key", "url"}


def test_encrypt_empty_payload() -> None:
    blob = encrypt_credentials({})
    assert decrypt_credentials(blob) == {}


# ── Repository tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_get_connector(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="anthropic",
        name="Anthropic — prod",
        credentials={"api_key": "sk-ant-test"},
    )
    await db_session.commit()

    fetched = await repo.get_connector(db_session, str(row.id))
    assert fetched.kind == "anthropic"
    assert fetched.name == "Anthropic — prod"
    assert fetched.status == "active"

    creds = repo.get_decrypted_credentials(fetched)
    assert creds["api_key"] == "sk-ant-test"


@pytest.mark.asyncio
async def test_update_connector_credentials(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="openai",
        name="OpenAI",
        credentials={"api_key": "old-key"},
    )
    await db_session.commit()

    updated = await repo.update_connector(
        db_session,
        str(row.id),
        credentials={"api_key": "new-key"},
    )
    await db_session.commit()

    creds = repo.get_decrypted_credentials(updated)
    assert creds["api_key"] == "new-key"


@pytest.mark.asyncio
async def test_soft_delete_preserves_row(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="tavily",
        name="Tavily",
        credentials={"api_key": "tvly-test"},
    )
    await db_session.commit()

    await repo.soft_delete_connector(db_session, str(row.id))
    await db_session.commit()

    # Row still exists
    fetched = await repo.get_connector(db_session, str(row.id))
    assert fetched.status == "disabled"


@pytest.mark.asyncio
async def test_permanent_delete_requires_disabled(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="gemini",
        name="Gemini",
        credentials={"api_key": "gm-test"},
    )
    await db_session.commit()

    # Active connector — permanent delete should fail
    with pytest.raises(ValueError, match="disabled"):
        await repo.permanent_delete_connector(db_session, str(row.id))


@pytest.mark.asyncio
async def test_permanent_delete_after_disable(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="starbridge",
        name="Starbridge",
        credentials={"api_key": "sb-key", "api_url": "https://example.com"},
    )
    await db_session.commit()

    await repo.soft_delete_connector(db_session, str(row.id))
    await db_session.commit()

    await repo.permanent_delete_connector(db_session, str(row.id))
    await db_session.commit()

    with pytest.raises(ValueError):
        await repo.get_connector(db_session, str(row.id))


@pytest.mark.asyncio
async def test_list_connectors_filter_by_kind(db_session: AsyncSession) -> None:
    await repo.create_connector(
        db_session, kind="anthropic", name="A1", credentials={"api_key": "k1"}
    )
    await repo.create_connector(db_session, kind="openai", name="O1", credentials={"api_key": "k2"})
    await db_session.commit()

    anthropic_rows = await repo.list_connectors(db_session, kind="anthropic")
    assert all(r.kind == "anthropic" for r in anthropic_rows)
    assert len(anthropic_rows) >= 1


# ── Agent linking tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_and_unlink_agent_connector(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="starbridge",
        name="Starbridge prod",
        credentials={"api_key": "sb-key", "api_url": "https://api.example.com"},
    )
    await db_session.commit()

    link = await repo.link_agent_connector(
        db_session,
        agent_id=9999,
        connector_id=str(row.id),
        tool_namespace="starbridge",
    )
    await db_session.commit()

    assert link.agent_id == 9999
    assert link.tool_namespace == "starbridge"

    links = await repo.list_agent_connectors(db_session, 9999)
    assert len(links) == 1

    await repo.unlink_agent_connector(db_session, agent_id=9999, connector_id=str(row.id))
    await db_session.commit()

    links_after = await repo.list_agent_connectors(db_session, 9999)
    assert len(links_after) == 0


@pytest.mark.asyncio
async def test_link_idempotent(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="anthropic",
        name="Anthropic",
        credentials={"api_key": "k"},
    )
    await db_session.commit()

    await repo.link_agent_connector(
        db_session, agent_id=1234, connector_id=str(row.id), tool_namespace="anthropic"
    )
    await db_session.commit()
    # Second link is idempotent
    await repo.link_agent_connector(
        db_session, agent_id=1234, connector_id=str(row.id), tool_namespace="anthropic"
    )
    await db_session.commit()

    links = await repo.list_agent_connectors(db_session, 1234)
    assert len(links) == 1


# ── Resolver tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolver_returns_credentials(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="anthropic",
        name="Anthropic",
        credentials={"api_key": "sk-ant-resolved"},
    )
    await db_session.commit()

    await repo.link_agent_connector(
        db_session, agent_id=42, connector_id=str(row.id), tool_namespace="anthropic"
    )
    await db_session.commit()

    creds = await get_credentials_for_tool(db_session, agent_id=42, tool_namespace="anthropic")
    assert creds["api_key"] == "sk-ant-resolved"


@pytest.mark.asyncio
async def test_resolver_raises_when_no_connector(db_session: AsyncSession) -> None:
    with pytest.raises(ConnectorNotConfigured) as exc_info:
        await get_credentials_for_tool(db_session, agent_id=99999, tool_namespace="openai")
    assert "99999" in str(exc_info.value)
    assert "openai" in str(exc_info.value)


@pytest.mark.asyncio
async def test_resolver_raises_when_connector_disabled(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="openai",
        name="OpenAI",
        credentials={"api_key": "sk-disabled"},
    )
    await db_session.commit()
    await repo.link_agent_connector(
        db_session, agent_id=55, connector_id=str(row.id), tool_namespace="openai"
    )
    await repo.soft_delete_connector(db_session, str(row.id))
    await db_session.commit()

    with pytest.raises(ConnectorNotConfigured):
        await get_credentials_for_tool(db_session, agent_id=55, tool_namespace="openai")


# ── HTTP route tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_create_and_list(client: AsyncClient) -> None:
    r = await client.post(
        "/api/connectors/",
        json={"kind": "anthropic", "name": "Anthropic test", "credentials": {"api_key": "k1"}},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "anthropic"
    assert body["credentials"]["api_key"] == "k1"

    r2 = await client.get("/api/connectors")
    assert r2.status_code == 200
    items = r2.json()
    assert any(i["id"] == body["id"] for i in items)
    # Credentials should NOT appear in list items
    assert all("credentials" not in i for i in items)


@pytest.mark.asyncio
async def test_api_update_connector(client: AsyncClient) -> None:
    r = await client.post(
        "/api/connectors/",
        json={"kind": "openai", "name": "OpenAI", "credentials": {"api_key": "old"}},
    )
    assert r.status_code == 201
    conn_id = r.json()["id"]

    r2 = await client.patch(
        f"/api/connectors/{conn_id}",
        json={"credentials": {"api_key": "new"}},
    )
    assert r2.status_code == 200
    assert r2.json()["credentials"]["api_key"] == "new"


@pytest.mark.asyncio
async def test_api_soft_delete(client: AsyncClient) -> None:
    r = await client.post(
        "/api/connectors/",
        json={"kind": "tavily", "name": "Tavily", "credentials": {"api_key": "tv1"}},
    )
    conn_id = r.json()["id"]

    r2 = await client.delete(f"/api/connectors/{conn_id}")
    assert r2.status_code == 204

    # Row still retrievable — status disabled
    r3 = await client.get(f"/api/connectors/{conn_id}")
    assert r3.status_code == 200
    assert r3.json()["status"] == "disabled"


@pytest.mark.asyncio
async def test_api_permanent_delete_requires_disabled(client: AsyncClient) -> None:
    r = await client.post(
        "/api/connectors/",
        json={"kind": "gemini", "name": "Gemini", "credentials": {"api_key": "gm1"}},
    )
    conn_id = r.json()["id"]

    # Active connector — permanent delete should return 400
    r2 = await client.delete(f"/api/connectors/{conn_id}/permanent")
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_api_agent_link_and_unlink(client: AsyncClient) -> None:
    # Create a connector
    r = await client.post(
        "/api/connectors/",
        json={
            "kind": "starbridge",
            "name": "SB prod",
            "credentials": {"api_key": "sb", "api_url": "https://x.com"},
        },
    )
    conn_id = r.json()["id"]

    # Link
    r2 = await client.post(
        "/api/agents/777/connectors",
        json={"connector_id": conn_id, "tool_namespace": "starbridge"},
    )
    assert r2.status_code == 201

    # List
    r3 = await client.get("/api/agents/777/connectors")
    assert r3.status_code == 200
    assert len(r3.json()) == 1

    # Unlink
    r4 = await client.delete(f"/api/agents/777/connectors/{conn_id}")
    assert r4.status_code == 204

    r5 = await client.get("/api/agents/777/connectors")
    assert len(r5.json()) == 0


@pytest.mark.asyncio
async def test_api_unknown_kind_rejected(client: AsyncClient) -> None:
    r = await client.post(
        "/api/connectors/",
        json={"kind": "not_a_real_kind", "name": "Fake", "credentials": {}},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_api_test_endpoint_calls_mock(client: AsyncClient) -> None:
    r = await client.post(
        "/api/connectors/",
        json={"kind": "openai", "name": "OpenAI stub", "credentials": {"api_key": "sk-fake"}},
    )
    conn_id = r.json()["id"]

    # Mock httpx so we don't hit real OpenAI
    with mock.patch("artemis.connectors.routes._test_connector") as mock_test:
        mock_test.return_value = __import__(
            "artemis.connectors.schemas", fromlist=["ConnectorTestResult"]
        ).ConnectorTestResult(ok=True, message="Mocked OK")
        r2 = await client.post(f"/api/connectors/{conn_id}/test")
    assert r2.status_code == 200
    assert r2.json()["ok"] is True


# ── Additional coverage tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_validated_updates_metadata(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="anthropic",
        name="Validated",
        credentials={"api_key": "k"},
    )
    await db_session.commit()

    updated = await repo.mark_validated(db_session, str(row.id), success=True)
    await db_session.commit()
    assert updated.metadata_.get("last_validated_at") is not None
    assert updated.metadata_.get("last_validation_ok") is True


@pytest.mark.asyncio
async def test_mark_validated_failure_sets_needs_reauth(db_session: AsyncSession) -> None:
    row = await repo.create_connector(
        db_session,
        kind="anthropic",
        name="Failing",
        credentials={"api_key": "k"},
    )
    await db_session.commit()

    updated = await repo.mark_validated(db_session, str(row.id), success=False)
    await db_session.commit()
    assert updated.status == "needs_reauth"


@pytest.mark.asyncio
async def test_api_get_connector_detail(client: AsyncClient) -> None:
    r = await client.post(
        "/api/connectors/",
        json={"kind": "tavily", "name": "Tavily detail", "credentials": {"api_key": "tv-detail"}},
    )
    assert r.status_code == 201
    conn_id = r.json()["id"]

    r2 = await client.get(f"/api/connectors/{conn_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == conn_id
    assert body["credentials"]["api_key"] == "tv-detail"


@pytest.mark.asyncio
async def test_api_get_connector_not_found(client: AsyncClient) -> None:
    r = await client.get("/api/connectors/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_patch_connector_not_found(client: AsyncClient) -> None:
    r = await client.patch(
        "/api/connectors/00000000-0000-0000-0000-000000000000",
        json={"name": "New name"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_list_connectors_filter_by_kind(client: AsyncClient) -> None:
    await client.post(
        "/api/connectors/",
        json={"kind": "anthropic", "name": "Anthropic A", "credentials": {"api_key": "a1"}},
    )
    await client.post(
        "/api/connectors/",
        json={"kind": "openai", "name": "OpenAI O", "credentials": {"api_key": "o1"}},
    )
    r = await client.get("/api/connectors?kind=anthropic")
    assert r.status_code == 200
    items = r.json()
    assert all(i["kind"] == "anthropic" for i in items)


@pytest.mark.asyncio
async def test_api_permanent_delete_after_soft_delete(client: AsyncClient) -> None:
    r = await client.post(
        "/api/connectors/",
        json={"kind": "gemini", "name": "Gemini perm", "credentials": {"api_key": "gm2"}},
    )
    conn_id = r.json()["id"]

    # Soft delete first
    r2 = await client.delete(f"/api/connectors/{conn_id}")
    assert r2.status_code == 204

    # Now permanent delete should succeed
    r3 = await client.delete(f"/api/connectors/{conn_id}/permanent")
    assert r3.status_code == 204

    # Fetching should now return 404
    r4 = await client.get(f"/api/connectors/{conn_id}")
    assert r4.status_code == 404


@pytest.mark.asyncio
async def test_api_link_connector_not_found(client: AsyncClient) -> None:
    r = await client.post(
        "/api/agents/888/connectors",
        json={
            "connector_id": "00000000-0000-0000-0000-000000000000",
            "tool_namespace": "starbridge",
        },
    )
    assert r.status_code == 404


def test_decrypt_invalid_blob() -> None:
    from artemis.connectors.encryption import ConnectorEncryptionError

    with pytest.raises(ConnectorEncryptionError):
        decrypt_credentials("not-a-valid-blob===")


@pytest.mark.asyncio
async def test_test_connector_no_kind_returns_ok() -> None:
    from artemis.connectors.routes import _test_connector

    result = await _test_connector("starbridge", {})
    # No api_url → should return failure
    assert result.ok is False
    assert "api_url" in result.message


@pytest.mark.asyncio
async def test_test_connector_timeout() -> None:
    import httpx

    from artemis.connectors.routes import _test_connector

    with mock.patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("timeout")):
        result = await _test_connector("gemini", {"api_key": "fake"})
    assert result.ok is False
    assert "timed out" in result.message
