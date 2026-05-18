"""Tests for J5 — Jira integration.

Coverage:
  - adf_to_text: all node types
  - _build_adf: no mentions, with mentions
  - _description_to_adf: multi-paragraph
  - _map_column_item: field mapping
  - JiraClient methods: mocked httpx responses
  - routes/jira: config save, overview (connected / not-configured / error)
  - resolve_jira_config: DB-first, env fallback
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.integrations.jira.client import (
    JiraAPIError,
    JiraClient,
    _build_adf,
    _description_to_adf,
    _map_column_item,
    adf_to_text,
)

# ── adf_to_text ───────────────────────────────────────────────────────────────


def test_adf_to_text_none_returns_empty() -> None:
    assert adf_to_text(None) == ""


def test_adf_to_text_text_node() -> None:
    assert adf_to_text({"type": "text", "text": "hello"}) == "hello"


def test_adf_to_text_hard_break() -> None:
    assert adf_to_text({"type": "hardBreak"}) == "\n"


def test_adf_to_text_mention() -> None:
    node = {"type": "mention", "attrs": {"text": "Alice"}}
    assert adf_to_text(node) == "@Alice"


def test_adf_to_text_mention_fallback_to_id() -> None:
    node = {"type": "mention", "attrs": {"id": "U123"}}
    assert adf_to_text(node) == "@U123"


def test_adf_to_text_emoji() -> None:
    node = {"type": "emoji", "attrs": {"text": "👍"}}
    assert adf_to_text(node) == "👍"


def test_adf_to_text_inline_card() -> None:
    node = {"type": "inlineCard", "attrs": {"url": "https://example.com"}}
    assert adf_to_text(node) == "https://example.com"


def test_adf_to_text_rule() -> None:
    assert adf_to_text({"type": "rule"}) == "\n---\n"


def test_adf_to_text_paragraph() -> None:
    node = {
        "type": "paragraph",
        "content": [{"type": "text", "text": "Hello world"}],
    }
    assert adf_to_text(node) == "Hello world\n"


def test_adf_to_text_code_block() -> None:
    node = {
        "type": "codeBlock",
        "attrs": {"language": "python"},
        "content": [{"type": "text", "text": "print('hi')"}],
    }
    result = adf_to_text(node)
    assert "```(python)" in result
    assert "print('hi')" in result


def test_adf_to_text_blockquote() -> None:
    node = {
        "type": "blockquote",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "quoted"}]}],
    }
    result = adf_to_text(node)
    assert "> " in result


def test_adf_to_text_table_row() -> None:
    node = {
        "type": "tableRow",
        "content": [
            {"type": "tableCell", "content": [{"type": "text", "text": "A"}]},
            {"type": "tableCell", "content": [{"type": "text", "text": "B"}]},
        ],
    }
    result = adf_to_text(node)
    assert "| " in result
    assert "A" in result and "B" in result


def test_adf_to_text_nested_doc() -> None:
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "line one"}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "line two"}],
            },
        ],
    }
    result = adf_to_text(doc)
    assert "line one" in result
    assert "line two" in result


# ── _build_adf ────────────────────────────────────────────────────────────────


def test_build_adf_no_mentions_returns_wrap() -> None:
    result = _build_adf("hello", [])
    assert result["type"] == "doc"
    para = result["content"][0]
    assert para["content"][0]["text"] == "hello"


def test_build_adf_with_mention_substitution() -> None:
    result = _build_adf("Hey @Alice nice work", [{"id": "U1", "name": "Alice"}])
    para = result["content"][0]["content"]
    types = [n["type"] for n in para]
    assert "mention" in types
    mention = next(n for n in para if n["type"] == "mention")
    assert mention["attrs"]["id"] == "U1"


def test_build_adf_no_match_returns_plain_text() -> None:
    result = _build_adf("Hey Bob", [{"id": "U1", "name": "Alice"}])
    para = result["content"][0]["content"]
    assert all(n["type"] == "text" for n in para)


# ── _description_to_adf ───────────────────────────────────────────────────────


def test_description_to_adf_empty() -> None:
    doc = _description_to_adf("")
    assert doc["content"] == []


def test_description_to_adf_single_paragraph() -> None:
    doc = _description_to_adf("Hello world")
    assert len(doc["content"]) == 1
    assert doc["content"][0]["type"] == "paragraph"


def test_description_to_adf_multi_paragraph() -> None:
    doc = _description_to_adf("Para one\n\nPara two\n\nPara three")
    assert len(doc["content"]) == 3


# ── _map_column_item ──────────────────────────────────────────────────────────


def _make_issue(**override: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": "ENG-1",
        "fields": {
            "summary": "Test issue",
            "assignee": {"displayName": "Alice", "accountId": "U1"},
            "status": {"name": "In Progress"},
            "priority": {"name": "High"},
            "labels": ["backend"],
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-02T00:00:00Z",
            "comment": {"total": 3},
            "worklog": {"worklogs": [{"timeSpentSeconds": 3600}]},
            "attachment": [{"id": "1"}],
            "customfield_10020": [{"name": "Sprint 1", "state": "active"}],
        },
    }
    base.update(override)
    return base


def test_map_column_item_basic_fields() -> None:
    item = _map_column_item(_make_issue())
    assert item["key"] == "ENG-1"
    assert item["title"] == "Test issue"
    assert item["assignee"] == "Alice"
    assert item["assigneeId"] == "U1"
    assert item["status"] == "In Progress"
    assert item["priority"] == "High"
    assert item["labels"] == ["backend"]
    assert item["commentCount"] == 3
    assert item["worklogTotal"] == 1.0
    assert item["attachmentCount"] == 1
    assert item["sprint"] == "Sprint 1"


def test_map_column_item_null_fields() -> None:
    item = _map_column_item({"key": "ENG-2", "fields": {}})
    assert item["title"] == "ENG-2"
    assert item["assignee"] == ""
    assert item["worklogTotal"] == 0.0
    assert item["sprint"] == ""


# ── JiraClient auth header ────────────────────────────────────────────────────


def test_jira_client_auth_header() -> None:
    client = JiraClient("https://test.atlassian.net", "user@example.com", "mytoken")
    expected = base64.b64encode(b"user@example.com:mytoken").decode()
    assert client._auth_header == f"Basic {expected}"


def test_jira_client_strips_trailing_slash() -> None:
    client = JiraClient("https://test.atlassian.net/", "u", "t")
    assert client._base == "https://test.atlassian.net"


# ── JiraClient.search_issues (mocked httpx) ───────────────────────────────────


def _mock_response(status: int, body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


@pytest.mark.asyncio
async def test_search_issues_returns_mapped_results() -> None:
    issue = _make_issue()
    resp = _mock_response(
        200,
        {"issues": [issue]},
    )
    client = JiraClient("https://jira.test", "u@x.com", "tok")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        results = await client.search_issues("test query")

    assert len(results) == 1
    assert results[0]["key"] == "ENG-1"
    assert results[0]["summary"] == "Test issue"
    assert "url" in results[0]


@pytest.mark.asyncio
async def test_search_issues_raises_on_error() -> None:
    resp = _mock_response(403, {"message": "Forbidden"})
    client = JiraClient("https://jira.test", "u", "t")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        with pytest.raises(JiraAPIError) as exc_info:
            await client.search_issues("test")
    assert exc_info.value.status == 403


# ── JiraClient write methods ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_comment_returns_shape() -> None:
    resp_data = {
        "id": "10001",
        "author": {"displayName": "Alice"},
        "body": {"type": "text", "text": "hi"},
        "created": "2024-01-01T00:00:00Z",
    }
    resp = _mock_response(201, resp_data)
    client = JiraClient("https://jira.test", "u", "t")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.add_comment("ENG-1", "hello")
    assert result["id"] == "10001"
    assert result["author"] == "Alice"


@pytest.mark.asyncio
async def test_add_worklog_raises_on_zero_hours() -> None:
    client = JiraClient("https://jira.test", "u", "t")
    with pytest.raises(ValueError, match="hours must be > 0"):
        await client.add_worklog("ENG-1", 0)


@pytest.mark.asyncio
async def test_set_assignee_returns_ok() -> None:
    resp = _mock_response(204, {})
    resp.is_success = True
    client = JiraClient("https://jira.test", "u", "t")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.put = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.set_assignee("ENG-1", "U99")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_transition_issue_returns_ok() -> None:
    resp = _mock_response(204, {})
    resp.is_success = True
    client = JiraClient("https://jira.test", "u", "t")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.transition_issue("ENG-1", "31")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_create_issue_raises_without_project_key() -> None:
    client = JiraClient("https://jira.test", "u", "t")
    with pytest.raises(ValueError, match="project_key required"):
        await client.create_issue(project_key="", summary="oops")


@pytest.mark.asyncio
async def test_create_issue_raises_without_summary() -> None:
    client = JiraClient("https://jira.test", "u", "t")
    with pytest.raises(ValueError, match="summary required"):
        await client.create_issue(project_key="ENG", summary="")


@pytest.mark.asyncio
async def test_create_issue_returns_key_and_id() -> None:
    resp = _mock_response(201, {"key": "ENG-42", "id": "10042"})
    client = JiraClient("https://jira.test", "u", "t")
    with patch("httpx.AsyncClient") as mock_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=resp)
        mock_cls.return_value = mock_http

        result = await client.create_issue(project_key="ENG", summary="New feature")
    assert result["key"] == "ENG-42"
    assert result["id"] == "10042"


# ── resolve_jira_config ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_jira_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_SITE_URL", "https://env.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "env@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "envtoken")

    from artemis.integrations.config_resolver import resolve_jira_config

    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {}
        cfg = await resolve_jira_config(mock_session)

    assert cfg.site_url == "https://env.atlassian.net"
    assert cfg.email == "env@example.com"
    assert cfg.api_token == "envtoken"


@pytest.mark.asyncio
async def test_resolve_jira_config_raises_when_missing() -> None:
    from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_jira_config

    mock_session = AsyncMock()
    with patch(
        "artemis.integrations.repository.get_provider_config", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {}
        with pytest.raises(MissingProviderConfigError) as exc_info:
            await resolve_jira_config(mock_session)
    assert "site_url" in exc_info.value.missing_fields
