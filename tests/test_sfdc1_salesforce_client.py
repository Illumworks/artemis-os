"""Tests for SFDC-1's read-only Salesforce client.

Coverage:
  - SalesforceClient is structurally read-only: its public method set is
    exactly {describe_sobject, query} -- no create/update/delete/patch.
  - fetch_access_token: happy path, HTTP-error response, network error,
    2xx-but-missing-fields response.
  - describe_sobject / query: happy path + non-2xx raises SalesforceAPIError.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from artemis.integrations.salesforce.client import (
    SalesforceAPIError,
    SalesforceAuthError,
    SalesforceClient,
    fetch_access_token,
)


def _mock_response(status: int, body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.json.return_value = body
    resp.text = json.dumps(body) if not isinstance(body, str) else body
    return resp


def _mock_http_client(method_name: str, resp: MagicMock) -> AsyncMock:
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    setattr(mock_http, method_name, AsyncMock(return_value=resp))
    return mock_http


# ── Structural read-only guarantee ────────────────────────────────────────────


def test_client_public_surface_is_exactly_describe_and_query() -> None:
    """The whole point of SFDC-1: this client must be structurally incapable
    of writing to Salesforce. No create/update/delete/patch method may ever
    exist on this class -- assert the public method set directly rather than
    checking for the absence of a few guessed names."""
    public_methods = {
        name
        for name in dir(SalesforceClient)
        if not name.startswith("_") and callable(getattr(SalesforceClient, name))
    }
    assert public_methods == {"describe_sobject", "query"}


def test_client_has_no_write_verb_methods() -> None:
    for verb in ("create", "update", "delete", "patch", "post", "put", "upsert", "write"):
        assert not hasattr(SalesforceClient, verb), f"SalesforceClient must not have .{verb}()"


# ── fetch_access_token ────────────────────────────────────────────────────────


async def test_fetch_access_token_happy_path() -> None:
    resp = _mock_response(
        200, {"access_token": "tok-123", "instance_url": "https://na1.salesforce.com"}
    )
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("post", resp)
        token = await fetch_access_token(
            login_url="https://login.salesforce.com",
            client_id="cid",
            client_secret="csecret",
        )
    assert token.access_token == "tok-123"
    assert token.instance_url == "https://na1.salesforce.com"


async def test_fetch_access_token_rejected_raises_auth_error() -> None:
    resp = _mock_response(400, {"error": "invalid_client_id"})
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("post", resp)
        with pytest.raises(SalesforceAuthError):
            await fetch_access_token(
                login_url="https://login.salesforce.com",
                client_id="bad",
                client_secret="bad",
            )


async def test_fetch_access_token_network_error_raises_auth_error() -> None:
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = mock_http
        with pytest.raises(SalesforceAuthError):
            await fetch_access_token(
                login_url="https://login.salesforce.com",
                client_id="cid",
                client_secret="csecret",
            )


async def test_fetch_access_token_missing_fields_raises_auth_error() -> None:
    # 2xx but the body is missing access_token/instance_url -- must not
    # silently return a half-populated token.
    resp = _mock_response(200, {"token_type": "Bearer"})
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("post", resp)
        with pytest.raises(SalesforceAuthError):
            await fetch_access_token(
                login_url="https://login.salesforce.com",
                client_id="cid",
                client_secret="csecret",
            )


# ── describe_sobject / query ──────────────────────────────────────────────────


async def test_describe_sobject_happy_path() -> None:
    resp = _mock_response(200, {"fields": [{"name": "Type", "label": "Account Type"}]})
    client = SalesforceClient("https://na1.salesforce.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        result = await client.describe_sobject("Account")
    assert result["fields"][0]["name"] == "Type"


async def test_describe_sobject_error_raises_api_error() -> None:
    resp = _mock_response(404, {"message": "not found"})
    client = SalesforceClient("https://na1.salesforce.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        with pytest.raises(SalesforceAPIError):
            await client.describe_sobject("NotARealObject")


async def test_query_happy_path_returns_records() -> None:
    resp = _mock_response(200, {"records": [{"Id": "001x", "Name": "Acme"}], "totalSize": 1})
    client = SalesforceClient("https://na1.salesforce.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        records = await client.query("SELECT Id, Name FROM Account LIMIT 1")
    assert records == [{"Id": "001x", "Name": "Acme"}]


async def test_query_error_raises_api_error() -> None:
    resp = _mock_response(400, {"message": "malformed query"})
    client = SalesforceClient("https://na1.salesforce.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        with pytest.raises(SalesforceAPIError):
            await client.query("SELECT")


async def test_query_missing_records_key_returns_empty_list() -> None:
    resp = _mock_response(200, {"totalSize": 0})
    client = SalesforceClient("https://na1.salesforce.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http_client("get", resp)
        records = await client.query("SELECT Id FROM Account WHERE Id = 'nope'")
    assert records == []
