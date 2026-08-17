"""Tests for SFDC-1's resolve_salesforce_config.

Coverage:
  - DB-stored config wins over env.
  - env fallback when nothing is stored.
  - login_url defaults to Salesforce production when unset anywhere.
  - MissingProviderConfigError when client_id/client_secret are absent from
    both sources.

Mirrors test_resolve_jira_config_from_env / test_resolve_jira_config_raises_when_missing
in test_j5_jira_integration.py: mock artemis.integrations.repository.get_provider_config
directly rather than requiring a live DB, since resolve_salesforce_config's own
logic (merge DB-then-env, raise when both are empty) is what's under test here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from artemis.integrations.config_resolver import (
    MissingProviderConfigError,
    resolve_salesforce_config,
)

pytestmark = pytest.mark.asyncio


async def test_resolve_salesforce_config_from_db() -> None:
    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "client_id": "db-client-id",
            "client_secret": "db-client-secret",
            "login_url": "https://mydomain.my.salesforce.com",
        }
        cfg = await resolve_salesforce_config(mock_session)

    assert cfg.client_id == "db-client-id"
    assert cfg.client_secret == "db-client-secret"
    assert cfg.login_url == "https://mydomain.my.salesforce.com"


async def test_resolve_salesforce_config_login_url_defaults_to_production() -> None:
    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"client_id": "cid", "client_secret": "csecret"}
        cfg = await resolve_salesforce_config(mock_session)

    assert cfg.login_url == "https://login.salesforce.com"


async def test_resolve_salesforce_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "env-client-secret")

    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {}
        cfg = await resolve_salesforce_config(mock_session)

    assert cfg.client_id == "env-client-id"
    assert cfg.client_secret == "env-client-secret"
    assert cfg.login_url == "https://login.salesforce.com"


async def test_resolve_salesforce_config_raises_when_missing() -> None:
    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {}
        with pytest.raises(MissingProviderConfigError) as exc_info:
            await resolve_salesforce_config(mock_session)
    assert "client_id" in exc_info.value.missing_fields
    assert "client_secret" in exc_info.value.missing_fields
