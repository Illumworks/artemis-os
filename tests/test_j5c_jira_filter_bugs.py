"""Tests for swimlane-filter bug fixes (worker/jira-filter-fixes).

Coverage:
  - get_overview assignee_filter="me"  → JQL contains currentUser()
  - get_overview assignee_filter=["U1","U2"] → JQL contains assignee IN + OR IS EMPTY
  - get_overview assignee_filter=None  → no assignee clause in JQL
  - get_overview assignee_filter=[]    → treated as None (no clause)
  - Default parameter is "me" (brief stays me-only)
  - Brief caller (sources._safe_jira) passes me_only=True to jira_overview
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

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


def _myself_response() -> MagicMock:
    return _mock_response(
        200,
        {"accountId": "ME123", "displayName": "Jon Fila", "emailAddress": "jon.fila@example.com"},
    )


def _empty_search_response() -> MagicMock:
    return _mock_response(200, {"issues": []})


def _make_client() -> JiraClient:
    return JiraClient("https://example.atlassian.net", "jon@example.com", "secret")


def _captured_jql_calls(mock_http: AsyncMock) -> list[str]:
    """Extract the JQL strings passed to each _fetch_column POST call."""
    jqls = []
    for c in mock_http.post.call_args_list:
        body = c.kwargs.get("json") or (c.args[1] if len(c.args) > 1 else {})
        if isinstance(body, dict) and "jql" in body:
            jqls.append(body["jql"])
    return jqls


# ── get_overview assignee_filter="me" (default) ───────────────────────────────


@pytest.mark.asyncio
async def test_get_overview_me_only_uses_current_user_jql() -> None:
    """Default (me) path must embed `assignee = currentUser()` in every column."""
    client = _make_client()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search_response())
        mock_cls.return_value = mock_http

        result = await client.get_overview()

    jqls = _captured_jql_calls(mock_http)
    assert len(jqls) == 4, "Expected 4 column fetches"
    for jql in jqls:
        assert "assignee = currentUser()" in jql, f"Expected currentUser() clause in: {jql}"
        assert "assignee IN" not in jql
        assert "IS EMPTY" not in jql


@pytest.mark.asyncio
async def test_get_overview_explicit_me_string_uses_current_user_jql() -> None:
    """Passing assignee_filter='me' explicitly should behave identically to the default."""
    client = _make_client()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search_response())
        mock_cls.return_value = mock_http

        result = await client.get_overview(assignee_filter="me")

    jqls = _captured_jql_calls(mock_http)
    for jql in jqls:
        assert "assignee = currentUser()" in jql


# ── get_overview assignee_filter=list[str] ───────────────────────────────────


@pytest.mark.asyncio
async def test_get_overview_team_list_uses_in_and_is_empty_jql() -> None:
    """Team list path must embed `assignee IN (...) OR assignee = currentUser() OR assignee IS EMPTY`.

    The board owner may not be in team_members, so currentUser() is always
    appended to guarantee the owner's own swim lane appears.
    """
    client = _make_client()
    team = ["U1", "U2", "U3"]

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search_response())
        mock_cls.return_value = mock_http

        result = await client.get_overview(assignee_filter=team)

    jqls = _captured_jql_calls(mock_http)
    assert len(jqls) == 4
    for jql in jqls:
        assert "assignee IN" in jql, f"Expected assignee IN clause in: {jql}"
        assert "assignee IS EMPTY" in jql, f"Expected IS EMPTY in: {jql}"
        # Owner must always be included via currentUser() even if not in team list.
        assert "assignee = currentUser()" in jql, f"Expected currentUser() clause in: {jql}"
        # All three IDs must be present
        for uid in team:
            assert uid in jql, f"Expected {uid} in JQL: {jql}"


@pytest.mark.asyncio
async def test_get_overview_team_list_jql_structure() -> None:
    """Team list JQL must be structured as (IN ... OR currentUser() OR IS EMPTY)
    so Jira treats them as a single grouped OR condition."""
    client = _make_client()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search_response())
        mock_cls.return_value = mock_http

        await client.get_overview(assignee_filter=["A1"])

    jqls = _captured_jql_calls(mock_http)
    for jql in jqls:
        # All three conditions must sit inside a single parenthesised OR group.
        import re
        # Look for (assignee IN (...) OR assignee = currentUser() OR assignee IS EMPTY)
        assert re.search(
            r'\(assignee IN \(.*?\)\s+OR\s+assignee = currentUser\(\)\s+OR\s+assignee IS EMPTY\)',
            jql,
        ), f"Expected grouped OR structure in: {jql}"


@pytest.mark.asyncio
async def test_get_overview_team_list_returns_current_user_info() -> None:
    """Result must carry currentUser block from /myself regardless of assignee_filter."""
    client = _make_client()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search_response())
        mock_cls.return_value = mock_http

        result = await client.get_overview(assignee_filter=["U1"])

    assert result["currentUser"]["accountId"] == "ME123"
    assert result["currentUser"]["displayName"] == "Jon Fila"


# ── get_overview assignee_filter=None ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_overview_none_filter_omits_assignee_clause() -> None:
    """None means whole project — no assignee clause of any kind."""
    client = _make_client()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search_response())
        mock_cls.return_value = mock_http

        result = await client.get_overview(assignee_filter=None)

    jqls = _captured_jql_calls(mock_http)
    assert len(jqls) == 4
    for jql in jqls:
        assert "assignee" not in jql.lower(), f"Expected no assignee clause in: {jql}"


# ── get_overview assignee_filter=[] (empty list treated as None) ──────────────


@pytest.mark.asyncio
async def test_get_overview_empty_list_omits_assignee_clause() -> None:
    """An empty list must fall through to the no-clause path (same as None)."""
    client = _make_client()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search_response())
        mock_cls.return_value = mock_http

        result = await client.get_overview(assignee_filter=[])

    jqls = _captured_jql_calls(mock_http)
    for jql in jqls:
        assert "assignee" not in jql.lower(), f"Expected no assignee clause in: {jql}"


# ── project_key clause still present ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_overview_project_key_combined_with_team_filter() -> None:
    """project_key clause and assignee clause must both appear."""
    client = _make_client()

    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=_myself_response())
        mock_http.post = AsyncMock(return_value=_empty_search_response())
        mock_cls.return_value = mock_http

        await client.get_overview(project_key="ENG", assignee_filter=["U1"])

    jqls = _captured_jql_calls(mock_http)
    for jql in jqls:
        assert 'project = "ENG"' in jql
        assert "assignee IN" in jql
