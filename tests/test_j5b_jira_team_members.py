"""Tests for J5b — Jira team-members filter.

Coverage:
  - JiraClient.get_assignable_users: no filter, with filter, pagination >200, member absent from org
  - emailAddress field included in returned shape
  - GET /api/jira/team-members route
  - PUT /api/jira/team-members route: happy path, unknown member rejected, empty list clears
  - GET /api/jira/assignable-users applies team filter automatically
  - list_jira_assignable_users FA tool applies filter
  - upsert_provider_config preserves empty list
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.integrations.jira.client import JiraAPIError, JiraClient

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_response(status: int, body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _make_user(account_id: str, display_name: str, email: str = "") -> dict[str, Any]:
    return {
        "accountId": account_id,
        "displayName": display_name,
        "emailAddress": email,
        "avatarUrls": {"48x48": f"https://avatar/{account_id}", "32x32": ""},
    }


# ── JiraClient.get_assignable_users ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_assignable_users_no_filter_returns_all() -> None:
    users = [_make_user("U1", "Alice"), _make_user("U2", "Bob")]
    resp = _mock_response(200, users)
    client = JiraClient("https://test.atlassian.net", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.get_assignable_users("ENG")

    assert len(result) == 2
    account_ids = {u["accountId"] for u in result}
    assert account_ids == {"U1", "U2"}


@pytest.mark.asyncio
async def test_get_assignable_users_with_filter_returns_subset() -> None:
    users = [_make_user("U1", "Alice"), _make_user("U2", "Bob"), _make_user("U3", "Carol")]
    resp = _mock_response(200, users)
    client = JiraClient("https://test.atlassian.net", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.get_assignable_users("ENG", team_filter=["U1", "U3"])

    assert len(result) == 2
    account_ids = {u["accountId"] for u in result}
    assert account_ids == {"U1", "U3"}


@pytest.mark.asyncio
async def test_get_assignable_users_member_not_in_org_skipped_gracefully() -> None:
    """Member in saved team that no longer exists in org is silently skipped."""
    users = [_make_user("U1", "Alice"), _make_user("U2", "Bob")]
    resp = _mock_response(200, users)
    client = JiraClient("https://test.atlassian.net", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.get_assignable_users("ENG", team_filter=["U1", "GHOST_ID"])

    assert len(result) == 1
    assert result[0]["accountId"] == "U1"


@pytest.mark.asyncio
async def test_get_assignable_users_empty_filter_returns_all() -> None:
    users = [_make_user("U1", "Alice"), _make_user("U2", "Bob")]
    resp = _mock_response(200, users)
    client = JiraClient("https://test.atlassian.net", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.get_assignable_users("ENG", team_filter=[])

    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_assignable_users_includes_email_address() -> None:
    users = [_make_user("U1", "Alice", email="alice@example.com")]
    resp = _mock_response(200, users)
    client = JiraClient("https://test.atlassian.net", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.get_assignable_users("ENG")

    assert result[0]["emailAddress"] == "alice@example.com"


@pytest.mark.asyncio
async def test_get_assignable_users_pagination() -> None:
    """When org has >200 users, all pages are fetched and filter applied."""
    page1 = [_make_user(f"U{i}", f"User{i}") for i in range(200)]
    page2 = [_make_user("U200", "User200"), _make_user("U201", "User201")]
    responses = [_mock_response(200, page1), _mock_response(200, page2)]
    response_iter = iter(responses)

    client = JiraClient("https://test.atlassian.net", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=lambda *a, **kw: next(response_iter))
        mock_cls.return_value = mock_http

        result = await client.get_assignable_users("ENG")

    assert len(result) == 202


@pytest.mark.asyncio
async def test_get_assignable_users_pagination_with_filter() -> None:
    """Filter applies after all pages collected."""
    page1 = [_make_user(f"U{i}", f"User{i}") for i in range(200)]
    page2 = [_make_user("U200", "Marketing1"), _make_user("U201", "Marketing2")]
    responses = [_mock_response(200, page1), _mock_response(200, page2)]
    response_iter = iter(responses)

    client = JiraClient("https://test.atlassian.net", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(side_effect=lambda *a, **kw: next(response_iter))
        mock_cls.return_value = mock_http

        result = await client.get_assignable_users("ENG", team_filter=["U200", "U201"])

    assert len(result) == 2
    account_ids = {u["accountId"] for u in result}
    assert account_ids == {"U200", "U201"}


@pytest.mark.asyncio
async def test_get_assignable_users_api_error_raises() -> None:
    resp = _mock_response(403, {"error": "Forbidden"})
    client = JiraClient("https://test.atlassian.net", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        with pytest.raises(JiraAPIError) as exc_info:
            await client.get_assignable_users("ENG")

    assert exc_info.value.status == 403


# ── upsert_provider_config: empty list preservation ───────────────────────────


@pytest.mark.asyncio
async def test_upsert_provider_config_preserves_empty_list() -> None:
    """team_members=[] must overwrite an existing non-empty list (clear the team)."""
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import encrypt_credentials

    existing_encrypted = encrypt_credentials(
        {"site_url": "https://x.atlassian.net", "team_members": ["U1", "U2"]}
    )

    mock_existing_row = MagicMock()
    mock_existing_row.encrypted_payload = existing_encrypted

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = mock_existing_row
    mock_session.execute.return_value = scalar_result

    captured: dict[str, Any] = {}

    with patch("artemis.integrations.crypto.encrypt_credentials") as mock_enc:
        mock_enc.side_effect = lambda d: (captured.update(d), encrypt_credentials(d))[1]
        await repo.upsert_provider_config(mock_session, "jira", {"team_members": []})

    assert "team_members" in captured
    assert captured["team_members"] == []


# ── GET /api/jira/team-members ────────────────────────────────────────────────


def _make_jira_raw_config(
    *,
    team_members: list[str] | None = None,
    has_credentials: bool = True,
) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if has_credentials:
        cfg.update(
            {
                "site_url": "https://test.atlassian.net",
                "email": "u@x.com",
                "api_token": "tok",
                "project_key": "ENG",
            }
        )
    if team_members is not None:
        cfg["team_members"] = team_members
    return cfg


@pytest.mark.asyncio
async def test_get_team_members_returns_saved_and_all() -> None:
    raw = _make_jira_raw_config(team_members=["U1", "U2"])
    all_users = [_make_user("U1", "Alice"), _make_user("U2", "Bob"), _make_user("U3", "Carol")]
    users_resp = _mock_response(200, all_users)

    with (
        patch(
            "artemis.integrations.repository.get_provider_config",
            new_callable=AsyncMock,
            return_value=raw,
        ),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=users_resp)
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_get_team_members

        mock_session = AsyncMock()
        result = await jira_get_team_members(_=None, session=mock_session)

    assert result["saved"] == ["U1", "U2"]
    assert len(result["all_assignable"]) == 3


@pytest.mark.asyncio
async def test_get_team_members_no_credentials_returns_empty() -> None:
    raw = _make_jira_raw_config(has_credentials=False, team_members=[])

    with patch(
        "artemis.integrations.repository.get_provider_config",
        new_callable=AsyncMock,
        return_value=raw,
    ):
        from artemis.routes.jira import jira_get_team_members

        mock_session = AsyncMock()
        result = await jira_get_team_members(_=None, session=mock_session)

    assert result["saved"] == []
    assert result["all_assignable"] == []


@pytest.mark.asyncio
async def test_get_team_members_no_project_key_returns_empty_all() -> None:
    raw = {"site_url": "https://x.atlassian.net", "email": "u@x.com", "api_token": "tok"}

    with patch(
        "artemis.integrations.repository.get_provider_config",
        new_callable=AsyncMock,
        return_value=raw,
    ):
        from artemis.routes.jira import jira_get_team_members

        mock_session = AsyncMock()
        result = await jira_get_team_members(_=None, session=mock_session)

    assert result["all_assignable"] == []


# ── PUT /api/jira/team-members ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_team_members_happy_path() -> None:
    raw = _make_jira_raw_config(team_members=[])
    all_users = [_make_user("U1", "Alice"), _make_user("U2", "Bob")]
    users_resp = _mock_response(200, all_users)

    with (
        patch(
            "artemis.integrations.repository.get_provider_config",
            new_callable=AsyncMock,
            return_value=raw,
        ),
        patch(
            "artemis.integrations.repository.upsert_provider_config", new_callable=AsyncMock
        ) as mock_upsert,
        patch("httpx.AsyncClient") as mock_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=users_resp)
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_put_team_members

        mock_session = AsyncMock()
        result = await jira_put_team_members(
            body={"members": ["U1", "U2"]}, _=None, session=mock_session
        )

    assert result["ok"] is True
    assert result["saved"] == ["U1", "U2"]
    mock_upsert.assert_called_once()
    # upsert_provider_config called positionally: (session, provider, payload_dict)
    assert mock_upsert.call_args.args[2]["team_members"] == ["U1", "U2"]


@pytest.mark.asyncio
async def test_put_team_members_empty_list_clears() -> None:
    raw = _make_jira_raw_config(team_members=["U1"])

    with (
        patch(
            "artemis.integrations.repository.get_provider_config",
            new_callable=AsyncMock,
            return_value=raw,
        ),
        patch(
            "artemis.integrations.repository.upsert_provider_config", new_callable=AsyncMock
        ) as mock_upsert,
    ):
        from artemis.routes.jira import jira_put_team_members

        mock_session = AsyncMock()
        result = await jira_put_team_members(body={"members": []}, _=None, session=mock_session)

    assert result["ok"] is True
    assert result["saved"] == []
    mock_upsert.assert_called_once()


@pytest.mark.asyncio
async def test_put_team_members_invalid_body_raises_400() -> None:
    from fastapi import HTTPException

    from artemis.routes.jira import jira_put_team_members

    mock_session = AsyncMock()
    with pytest.raises(HTTPException) as exc_info:
        await jira_put_team_members(body={"members": "not-a-list"}, _=None, session=mock_session)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_put_team_members_unknown_id_raises_422() -> None:
    from fastapi import HTTPException

    raw = _make_jira_raw_config(team_members=[])
    all_users = [_make_user("U1", "Alice")]
    users_resp = _mock_response(200, all_users)

    with (
        patch(
            "artemis.integrations.repository.get_provider_config",
            new_callable=AsyncMock,
            return_value=raw,
        ),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=users_resp)
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_put_team_members

        mock_session = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await jira_put_team_members(
                body={"members": ["U1", "GHOST_ID"]}, _=None, session=mock_session
            )

    assert exc_info.value.status_code == 422
    assert "GHOST_ID" in exc_info.value.detail


# ── GET /api/jira/assignable-users applies saved team filter ──────────────────


@pytest.mark.asyncio
async def test_assignable_users_route_applies_team_filter() -> None:
    """assignable-users endpoint filters by saved team_members automatically."""
    all_users_api = [
        _make_user("U1", "Alice"),
        _make_user("U2", "Bob"),
        _make_user("U3", "Carol"),
    ]
    users_resp = _mock_response(200, all_users_api)

    with (
        patch(
            "artemis.routes.jira.resolve_jira_config",
            new_callable=AsyncMock,
        ) as mock_resolve,
        patch("httpx.AsyncClient") as mock_cls,
    ):
        from artemis.integrations.config_resolver import JiraConfig

        mock_resolve.return_value = JiraConfig(
            site_url="https://test.atlassian.net",
            email="u@x.com",
            api_token="tok",
            project_key="ENG",
            max_items_per_column=20,
            team_members=("U1", "U3"),
        )
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=users_resp)
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_assignable_users

        mock_session = AsyncMock()
        result = await jira_assignable_users(project="ENG", _=None, session=mock_session)

    assert len(result) == 2
    account_ids = {u["accountId"] for u in result}
    assert account_ids == {"U1", "U3"}


@pytest.mark.asyncio
async def test_assignable_users_route_no_filter_returns_all() -> None:
    all_users_api = [_make_user("U1", "Alice"), _make_user("U2", "Bob")]
    users_resp = _mock_response(200, all_users_api)

    with (
        patch(
            "artemis.routes.jira.resolve_jira_config",
            new_callable=AsyncMock,
        ) as mock_resolve,
        patch("httpx.AsyncClient") as mock_cls,
    ):
        from artemis.integrations.config_resolver import JiraConfig

        mock_resolve.return_value = JiraConfig(
            site_url="https://test.atlassian.net",
            email="u@x.com",
            api_token="tok",
            project_key="ENG",
            max_items_per_column=20,
            team_members=(),
        )
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=users_resp)
        mock_cls.return_value = mock_http

        from artemis.routes.jira import jira_assignable_users

        mock_session = AsyncMock()
        result = await jira_assignable_users(project="ENG", _=None, session=mock_session)

    assert len(result) == 2


# ── FA tool: list_jira_assignable_users applies filter ────────────────────────


@pytest.mark.asyncio
async def test_fa_tool_list_assignable_users_applies_filter() -> None:
    all_users_api = [
        _make_user("U1", "Alice"),
        _make_user("U2", "Bob"),
        _make_user("U3", "Carol"),
    ]
    users_resp = _mock_response(200, all_users_api)

    with (
        patch("artemis.db.SessionLocal") as mock_session_cls,
        patch(
            "artemis.integrations.config_resolver.resolve_jira_config",
            new_callable=AsyncMock,
        ) as mock_resolve,
        patch("httpx.AsyncClient") as mock_http_cls,
    ):
        from artemis.integrations.config_resolver import JiraConfig

        mock_resolve.return_value = JiraConfig(
            site_url="https://test.atlassian.net",
            email="u@x.com",
            api_token="tok",
            project_key="ENG",
            max_items_per_column=20,
            team_members=("U1", "U2"),
        )
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session_ctx

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=users_resp)
        mock_http_cls.return_value = mock_http

        from artemis.floating_artemis.tools.jira_tools import _list_jira_assignable_users

        result = await _list_jira_assignable_users({"project": "ENG"})

    assert "Alice" in result
    assert "Bob" in result
    assert "Carol" not in result
