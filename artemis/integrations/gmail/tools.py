"""Gmail read tools for Floating Artemis."""

from __future__ import annotations

import json
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry


async def _resolve_gmail_client() -> Any | None:
    import artemis.db as _db
    from artemis.google_docs.repository import get_google_credential
    from artemis.google_integration import (
        google_has_any_scope,
        resolve_google_oauth_client_config,
    )
    from artemis.integrations.gmail.client import GmailClient

    async with _db.SessionLocal() as session:
        credential = await get_google_credential(session, user_id=1, purpose="personal")
        if credential is None:
            return None
        if not google_has_any_scope(
            credential.scope,
            "https://www.googleapis.com/auth/gmail.readonly",
        ):
            return None
        config = await resolve_google_oauth_client_config(session)
        return GmailClient(
            access_token=credential.access_token,
            refresh_token=credential.refresh_token or "",
            client_id=config.client_id,
            client_secret=config.client_secret,
        )


async def _list_recent_gmail_messages(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 10))
    query = inp.get("query")
    try:
        client = await _resolve_gmail_client()
        if client is None:
            return "No active Gmail read credential found"
        messages = await client.list_recent_messages(max_results=limit, query=query)
        return json.dumps(messages)
    except Exception as exc:
        return f"list_recent_gmail_messages failed: {exc}"


async def _get_gmail_thread(inp: dict[str, Any]) -> str:
    thread_id = str(inp.get("thread_id", "")).strip()
    if not thread_id:
        return "Error: thread_id is required"
    try:
        client = await _resolve_gmail_client()
        if client is None:
            return "No active Gmail read credential found"
        thread = await client.get_thread(thread_id)
        return json.dumps(thread)
    except Exception as exc:
        return f"get_gmail_thread failed: {exc}"


LIST_RECENT_GMAIL_MESSAGES = Tool(
    name="list_recent_gmail_messages",
    description="List recent Gmail messages from the personal account. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 10},
            "query": {"type": "string", "description": "Optional Gmail search query"},
        },
        "required": [],
    },
)

GET_GMAIL_THREAD = Tool(
    name="get_gmail_thread",
    description="Fetch one Gmail thread by id from the personal account. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "thread_id": {"type": "string", "description": "Gmail thread id"},
        },
        "required": ["thread_id"],
    },
)


def register_gmail_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(LIST_RECENT_GMAIL_MESSAGES, _list_recent_gmail_messages, layer=2)
    registry.register(GET_GMAIL_THREAD, _get_gmail_thread, layer=2)
