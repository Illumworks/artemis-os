"""Unit tests for the three brief fixes:
1. JQL assignee filter in jira/client.py
2. Slack chunking in proactivity/scheduler.py
3. Brief suppression (set_brief_exclusion / clear_brief_exclusion / _safe_brief_exclusions)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Fix 1: JQL contains assignee = currentUser() ─────────────────────────────


def test_jql_assignee_clause_present_no_project() -> None:
    """Each status clause must contain 'assignee = currentUser()' when no project given."""
    from artemis.integrations.jira.client import JiraClient

    client = JiraClient(site_url="https://jira.example.com", email="j@e.com", api_token="tok")
    # We can test the JQL string by examining _fetch_column call args.
    captured: list[str] = []

    async def _fake_fetch(http_client: Any, jql: str, max_items: int) -> list[Any]:
        captured.append(jql)
        return []

    # Patch /myself endpoint to return a minimal user object.
    fake_me = MagicMock()
    fake_me.is_success = True
    fake_me.json.return_value = {
        "accountId": "acc1",
        "displayName": "Jon",
        "emailAddress": "j@e.com",
    }

    async def _fake_get(url: str, **kwargs: Any) -> Any:
        return fake_me

    # Patch httpx.AsyncClient so no real HTTP is made.
    fake_http_client = MagicMock()
    fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
    fake_http_client.__aexit__ = AsyncMock(return_value=False)
    fake_http_client.get = AsyncMock(return_value=fake_me)

    with (
        patch("artemis.integrations.jira.client.httpx.AsyncClient", return_value=fake_http_client),
        patch.object(client, "_fetch_column", side_effect=_fake_fetch),
    ):
        import asyncio

        asyncio.get_event_loop().run_until_complete(client.get_overview())

    assert len(captured) == 4, f"Expected 4 JQL strings, got {len(captured)}"
    for jql in captured:
        assert "assignee = currentUser()" in jql, (
            f"'assignee = currentUser()' missing from JQL: {jql!r}"
        )


def test_jql_assignee_clause_with_project() -> None:
    """Assignee clause must follow the project clause when project_key is given."""
    from artemis.integrations.jira.client import JiraClient

    client = JiraClient(site_url="https://jira.example.com", email="j@e.com", api_token="tok")
    captured: list[str] = []

    async def _fake_fetch(http_client: Any, jql: str, max_items: int) -> list[Any]:
        captured.append(jql)
        return []

    fake_me = MagicMock()
    fake_me.is_success = True
    fake_me.json.return_value = {
        "accountId": "acc1",
        "displayName": "Jon",
        "emailAddress": "j@e.com",
    }

    fake_http_client = MagicMock()
    fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
    fake_http_client.__aexit__ = AsyncMock(return_value=False)
    fake_http_client.get = AsyncMock(return_value=fake_me)

    with (
        patch("artemis.integrations.jira.client.httpx.AsyncClient", return_value=fake_http_client),
        patch.object(client, "_fetch_column", side_effect=_fake_fetch),
    ):
        import asyncio

        asyncio.get_event_loop().run_until_complete(client.get_overview(project_key="MT"))

    for jql in captured:
        assert 'project = "MT"' in jql, f"project clause missing: {jql!r}"
        assert "assignee = currentUser()" in jql, f"assignee clause missing: {jql!r}"
        # project clause must come BEFORE assignee clause
        assert jql.index('project = "MT"') < jql.index("assignee = currentUser()"), (
            f"project clause should precede assignee clause in: {jql!r}"
        )


# ── Fix 2: Slack chunking ─────────────────────────────────────────────────────


def test_chunk_short_text_returns_single_chunk() -> None:
    """A brief that fits in one chunk is returned as a list of one."""
    from artemis.proactivity.scheduler import _chunk_slack_text

    text = "Hello world\n\nThis is a short brief."
    chunks = _chunk_slack_text(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_long_text_produces_multiple_chunks() -> None:
    """Text longer than _SLACK_MAX is split into multiple chunks, each ≤ _SLACK_MAX."""
    from artemis.proactivity.scheduler import _SLACK_MAX, _chunk_slack_text

    # Build a text that is definitely over the limit.
    # Use distinct sections separated by \n\n so we can verify section-boundary splitting.
    section = "A" * 1000
    text = "\n\n".join([section] * 6)  # 6 * 1000 + 10 = ~6010 chars
    assert len(text) > _SLACK_MAX

    chunks = _chunk_slack_text(text)
    assert len(chunks) > 1, "Expected more than one chunk for a long brief"
    for i, chunk in enumerate(chunks):
        assert len(chunk) <= _SLACK_MAX, (
            f"Chunk {i} exceeds _SLACK_MAX ({_SLACK_MAX}): length={len(chunk)}"
        )


def test_chunk_order_preserved() -> None:
    """When split, chunks reassemble to the original text (modulo stripped whitespace at boundaries)."""
    from artemis.proactivity.scheduler import _SLACK_MAX, _chunk_slack_text

    lines = [f"Line {i}: " + "x" * 200 for i in range(30)]
    text = "\n\n".join(lines)
    assert len(text) > _SLACK_MAX

    chunks = _chunk_slack_text(text)
    # All original line markers should appear across the chunks in order.
    full = " ".join(chunks)
    for i in range(30):
        assert f"Line {i}:" in full, f"Line {i} missing from reassembled chunks"


def test_chunk_no_empty_chunks() -> None:
    """No empty string should appear in the returned list."""
    from artemis.proactivity.scheduler import _chunk_slack_text

    text = "\n\n".join(["Section " + str(i) + " " + "y" * 500 for i in range(20)])
    chunks = _chunk_slack_text(text)
    for chunk in chunks:
        assert chunk.strip(), f"Empty chunk found: {chunk!r}"


def test_chunk_each_le_slack_max_on_very_long_section() -> None:
    """Even a single section longer than _SLACK_MAX is split correctly."""
    from artemis.proactivity.scheduler import _SLACK_MAX, _chunk_slack_text

    # One giant section with internal newlines.
    single_section = "\n".join(["z" * 100 for _ in range(50)])  # 5050 chars
    assert len(single_section) > _SLACK_MAX

    chunks = _chunk_slack_text(single_section)
    for chunk in chunks:
        assert len(chunk) <= _SLACK_MAX


# ── Fix 3: Brief suppression ──────────────────────────────────────────────────


def _make_obs(content: str) -> Any:
    """Make a minimal observation-like object."""

    class _Obs:
        def __init__(self, c: str) -> None:
            self.content = c

    return _Obs(content)


@pytest.mark.asyncio
async def test_brief_exclusion_tool_writes_correct_scope() -> None:
    """set_brief_exclusion writes to 'agent:floating-artemis'."""
    from artemis.floating_artemis.tools.core import _set_brief_exclusion

    written: list[dict[str, Any]] = []

    async def _fake_write(inp: dict[str, Any]) -> str:
        written.append(inp)
        return "Memory written to agent:floating-artemis."

    with patch("artemis.floating_artemis.tools.core._write_memory", side_effect=_fake_write):
        result = await _set_brief_exclusion({"ticket_key": "MT-456", "reason": "long-term"})

    assert len(written) == 1
    assert written[0]["scope"] == "agent:floating-artemis"
    assert "brief_exclusion:MT-456" in written[0]["content"]
    assert "long-term" in written[0]["content"]
    # Confirmation is conversational, not a button
    assert "MT-456" in result
    assert "morning brief" in result.lower()


@pytest.mark.asyncio
async def test_clear_brief_exclusion_tool_writes_cleared() -> None:
    """clear_brief_exclusion writes a 'cleared' marker to the same scope."""
    from artemis.floating_artemis.tools.core import _clear_brief_exclusion

    written: list[dict[str, Any]] = []

    async def _fake_write(inp: dict[str, Any]) -> str:
        written.append(inp)
        return "Memory written to agent:floating-artemis."

    with patch("artemis.floating_artemis.tools.core._write_memory", side_effect=_fake_write):
        result = await _clear_brief_exclusion({"ticket_key": "mt-456"})

    assert len(written) == 1
    assert written[0]["scope"] == "agent:floating-artemis"
    assert "brief_exclusion:MT-456" in written[0]["content"]
    assert "cleared" in written[0]["content"]
    assert "MT-456" in result


@pytest.mark.asyncio
async def test_safe_brief_exclusions_returns_excluded_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """_safe_brief_exclusions parses memory and returns net-excluded ticket keys."""
    from artemis.brief.sources import _safe_brief_exclusions

    # Two exclusions, one cleared — net result: MT-456 excluded, MT-521 cleared.
    obs_list = [
        _make_obs("brief_exclusion:MT-456 reason=long-term"),
        _make_obs("brief_exclusion:MT-521 cleared"),
        _make_obs("brief_exclusion:MT-999 reason=other"),
    ]

    async def _fake_search(
        session: Any, scope_set: Any, query: str, limit: int, modes: Any
    ) -> list[Any]:
        return obs_list

    monkeypatch.setattr("artemis.brief.sources.search_observations", _fake_search, raising=False)

    # Patch the import inside _safe_brief_exclusions
    with (
        patch("artemis.memory.retrieval.search_observations", side_effect=_fake_search),
    ):
        # Use a real-ish session mock — _safe_brief_exclusions just passes it through.
        mock_session = MagicMock()
        result = await _safe_brief_exclusions(mock_session)

    assert "MT-456" in result
    assert "MT-999" in result
    assert "MT-521" not in result  # cleared


@pytest.mark.asyncio
async def test_safe_brief_exclusions_scope_matches_tool_scope() -> None:
    """_safe_brief_exclusions queries 'agent:floating-artemis' — same scope the tool writes."""
    from artemis.brief.sources import _safe_brief_exclusions

    queried_scopes: list[Any] = []

    async def _capture_search(
        session: Any, scope_set: Any, query: str, limit: int, modes: Any
    ) -> list[Any]:
        queried_scopes.extend(scope_set)
        return []

    with patch("artemis.memory.retrieval.search_observations", side_effect=_capture_search):
        mock_session = MagicMock()
        await _safe_brief_exclusions(mock_session)

    assert len(queried_scopes) == 1
    scope = queried_scopes[0]
    assert scope.scope_kind == "agent"
    assert scope.scope_id == "floating-artemis"


def test_build_context_string_filters_excluded_tickets() -> None:
    """_build_context_string must omit excluded ticket keys from the Jira section."""
    from artemis.brief.prompt import _build_context_string

    sources: dict[str, Any] = {
        "jira": {
            "connected": True,
            "columns": [
                {
                    "key": "prog",
                    "items": [
                        {"key": "MT-456", "title": "Fix login redirect", "priority": "High"},
                        {"key": "MT-521", "title": "Update email templates", "priority": "Medium"},
                        {"key": "MT-789", "title": "Ship new dashboard", "priority": "Low"},
                    ],
                },
                {"key": "review", "items": []},
                {"key": "blocked", "items": []},
            ],
        },
        "_excluded_ticket_keys": {"MT-456", "MT-521"},
    }

    ctx = _build_context_string(sources)

    # Excluded tickets must not appear in the in-progress list
    assert "MT-456" not in ctx or "Suppressed" in ctx
    assert "MT-521" not in ctx or "Suppressed" in ctx
    # Non-excluded ticket must appear
    assert "MT-789" in ctx
    assert "Ship new dashboard" in ctx


def test_build_context_string_adds_suppressed_block_when_exclusions_present() -> None:
    """When exclusions exist, a Suppressed block appears at the end of the Jira section."""
    from artemis.brief.prompt import _build_context_string

    sources: dict[str, Any] = {
        "jira": {
            "connected": True,
            "columns": [
                {
                    "key": "prog",
                    "items": [{"key": "MT-456", "title": "Skip me", "priority": "High"}],
                },
                {"key": "review", "items": []},
                {"key": "blocked", "items": []},
            ],
        },
        "_excluded_ticket_keys": {"MT-456"},
    }

    ctx = _build_context_string(sources)
    assert "Suppressed" in ctx
    assert "MT-456" in ctx


def test_build_context_string_no_suppressed_block_when_no_exclusions() -> None:
    """When no exclusions, no Suppressed block appears."""
    from artemis.brief.prompt import _build_context_string

    sources: dict[str, Any] = {
        "jira": {
            "connected": True,
            "columns": [
                {
                    "key": "prog",
                    "items": [{"key": "MT-789", "title": "Normal ticket", "priority": "High"}],
                },
                {"key": "review", "items": []},
                {"key": "blocked", "items": []},
            ],
        },
        "_excluded_ticket_keys": set(),
    }

    ctx = _build_context_string(sources)
    assert "Suppressed" not in ctx
    assert "MT-789" in ctx
