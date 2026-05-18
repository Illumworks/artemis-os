"""Jira tools for Floating Artemis.

Seven tools split by authority layer:
  Layer 1 (read): search_jira, get_jira_issue, list_jira_assignable_users
  Layer 3 (write): add_jira_comment, transition_jira_issue, assign_jira_issue, create_jira_issue

[surface:jira-board] — all tools in this module require the jira-board surface.
"""

from __future__ import annotations

import json
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_SURFACE = "[surface:jira-board]"


# ── Implementations ───────────────────────────────────────────────────────────


async def _search_jira(inp: dict[str, Any]) -> str:
    query = str(inp.get("query") or "")
    limit = int(inp.get("limit") or 20)
    if not query:
        return "Error: query is required"
    try:
        import artemis.db as _db
        from artemis.integrations.config_resolver import resolve_jira_config
        from artemis.integrations.jira.client import JiraClient

        async with _db.SessionLocal() as session:
            cfg = await resolve_jira_config(session)
        results = await JiraClient(cfg.site_url, cfg.email, cfg.api_token).search_issues(
            query, max_results=limit
        )
        if not results:
            return f"No Jira issues found for query: {query!r}"
        lines = [f"{r['key']}: [{r['status']}] {r['summary']}" for r in results]
        return "\n".join(lines)
    except Exception as exc:
        return f"search_jira failed: {exc}"


async def _get_jira_issue(inp: dict[str, Any]) -> str:
    key = str(inp.get("key") or "")
    if not key:
        return "Error: key is required"
    try:
        import artemis.db as _db
        from artemis.integrations.config_resolver import resolve_jira_config
        from artemis.integrations.jira.client import JiraClient

        async with _db.SessionLocal() as session:
            cfg = await resolve_jira_config(session)
        issue = await JiraClient(cfg.site_url, cfg.email, cfg.api_token).get_issue(key)
        summary = {
            "key": issue["key"],
            "title": issue["title"],
            "status": issue["status"],
            "priority": issue["priority"],
            "assignee": issue["assignee"],
            "description": issue["description"][:500] if issue.get("description") else "",
            "url": issue["url"],
            "commentCount": len(issue.get("comments") or []),
        }
        return json.dumps(summary)
    except Exception as exc:
        return f"get_jira_issue failed: {exc}"


async def _list_jira_assignable_users(inp: dict[str, Any]) -> str:
    project = str(inp.get("project") or "")
    try:
        import artemis.db as _db
        from artemis.integrations.config_resolver import resolve_jira_config
        from artemis.integrations.jira.client import JiraClient

        async with _db.SessionLocal() as session:
            cfg = await resolve_jira_config(session)
        key = project or cfg.project_key
        if not key:
            return "Error: project key required — pass project param or set projectKey in config"
        users = await JiraClient(cfg.site_url, cfg.email, cfg.api_token).get_assignable_users(key)
        if cfg.team_members:
            member_set = set(cfg.team_members)
            users = [u for u in users if u["accountId"] in member_set]
        if not users:
            return "No assignable users found"
        lines = [f"{u['accountId']}: {u['displayName']}" for u in users]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_jira_assignable_users failed: {exc}"


async def _add_jira_comment(inp: dict[str, Any]) -> str:
    key = str(inp.get("key") or "")
    text = str(inp.get("text") or "")
    if not key or not text:
        return "Error: key and text are required"
    try:
        import artemis.db as _db
        from artemis.integrations.config_resolver import resolve_jira_config
        from artemis.integrations.jira.client import JiraClient

        async with _db.SessionLocal() as session:
            cfg = await resolve_jira_config(session)
        result = await JiraClient(cfg.site_url, cfg.email, cfg.api_token).add_comment(key, text)
        return json.dumps(result)
    except Exception as exc:
        return f"add_jira_comment failed: {exc}"


async def _transition_jira_issue(inp: dict[str, Any]) -> str:
    key = str(inp.get("key") or "")
    transition_id = str(inp.get("transition_id") or "")
    if not key or not transition_id:
        return "Error: key and transition_id are required"
    try:
        import artemis.db as _db
        from artemis.integrations.config_resolver import resolve_jira_config
        from artemis.integrations.jira.client import JiraClient

        async with _db.SessionLocal() as session:
            cfg = await resolve_jira_config(session)
        await JiraClient(cfg.site_url, cfg.email, cfg.api_token).transition_issue(
            key, transition_id
        )
        return f"Issue {key} transitioned (transition_id={transition_id})."
    except Exception as exc:
        return f"transition_jira_issue failed: {exc}"


async def _assign_jira_issue(inp: dict[str, Any]) -> str:
    key = str(inp.get("key") or "")
    account_id = inp.get("account_id")
    if not key:
        return "Error: key is required"
    try:
        import artemis.db as _db
        from artemis.integrations.config_resolver import resolve_jira_config
        from artemis.integrations.jira.client import JiraClient

        async with _db.SessionLocal() as session:
            cfg = await resolve_jira_config(session)
        await JiraClient(cfg.site_url, cfg.email, cfg.api_token).set_assignee(
            key, str(account_id) if account_id else None
        )
        verb = f"assigned to {account_id}" if account_id else "unassigned"
        return f"Issue {key} {verb}."
    except Exception as exc:
        return f"assign_jira_issue failed: {exc}"


async def _create_jira_issue(inp: dict[str, Any]) -> str:
    summary = str(inp.get("summary") or "")
    if not summary:
        return "Error: summary is required"
    try:
        import artemis.db as _db
        from artemis.integrations.config_resolver import resolve_jira_config
        from artemis.integrations.jira.client import JiraClient

        async with _db.SessionLocal() as session:
            cfg = await resolve_jira_config(session)
        project_key = str(inp.get("project_key") or cfg.project_key or "")
        labels_raw = inp.get("labels")
        result = await JiraClient(cfg.site_url, cfg.email, cfg.api_token).create_issue(
            project_key=project_key,
            summary=summary,
            description=str(inp.get("description") or ""),
            assignee_account_id=inp.get("assignee_account_id") or None,
            priority_name=str(inp.get("priority_name") or "Medium"),
            labels=labels_raw if isinstance(labels_raw, list) else [],
            issue_type_name=str(inp.get("issue_type_name") or "Task"),
        )
        return json.dumps(result)
    except Exception as exc:
        return f"create_jira_issue failed: {exc}"


# ── Tool definitions ──────────────────────────────────────────────────────────


def register_jira_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(
        Tool(
            name="search_jira",
            description=f"Search Jira issues by text query. Returns key, status, and summary. {_SURFACE}",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
                "required": ["query"],
            },
        ),
        _search_jira,
        layer=1,
    )
    registry.register(
        Tool(
            name="get_jira_issue",
            description=f"Get full details for a Jira issue by key (e.g. ENG-123). {_SURFACE}",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Jira issue key"},
                },
                "required": ["key"],
            },
        ),
        _get_jira_issue,
        layer=1,
    )
    registry.register(
        Tool(
            name="list_jira_assignable_users",
            description=f"List users assignable to issues in a Jira project. {_SURFACE}",
            input_schema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project key (falls back to configured default)",
                    },
                },
            },
        ),
        _list_jira_assignable_users,
        layer=1,
    )
    registry.register(
        Tool(
            name="add_jira_comment",
            description=f"Post a comment on a Jira issue. {_SURFACE}",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Jira issue key"},
                    "text": {"type": "string", "description": "Comment body"},
                },
                "required": ["key", "text"],
            },
        ),
        _add_jira_comment,
        layer=3,
    )
    registry.register(
        Tool(
            name="transition_jira_issue",
            description=f"Move a Jira issue to a new status via a transition ID. {_SURFACE}",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Jira issue key"},
                    "transition_id": {
                        "type": "string",
                        "description": "Transition ID from the issue's transitions list",
                    },
                },
                "required": ["key", "transition_id"],
            },
        ),
        _transition_jira_issue,
        layer=3,
    )
    registry.register(
        Tool(
            name="assign_jira_issue",
            description=f"Assign (or unassign) a Jira issue. Pass account_id=null to unassign. {_SURFACE}",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Jira issue key"},
                    "account_id": {
                        "type": ["string", "null"],
                        "description": "Assignee accountId, or null to unassign",
                    },
                },
                "required": ["key"],
            },
        ),
        _assign_jira_issue,
        layer=3,
    )
    registry.register(
        Tool(
            name="create_jira_issue",
            description=f"Create a new Jira issue. {_SURFACE}",
            input_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Issue summary / title"},
                    "project_key": {
                        "type": "string",
                        "description": "Project key (falls back to configured default)",
                    },
                    "description": {"type": "string", "description": "Issue description (plain text)"},
                    "assignee_account_id": {
                        "type": "string",
                        "description": "Assignee accountId",
                    },
                    "priority_name": {
                        "type": "string",
                        "description": "Priority name (default: Medium)",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Labels to attach",
                    },
                    "issue_type_name": {
                        "type": "string",
                        "description": "Issue type (default: Task)",
                    },
                },
                "required": ["summary"],
            },
        ),
        _create_jira_issue,
        layer=3,
    )
