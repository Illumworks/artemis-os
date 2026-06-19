"""Gmail read tools for Floating Artemis."""

from __future__ import annotations

import json
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry


async def _resolve_gmail_client() -> Any | None:
    """Return a GmailClient wired with a persist callback, or None if not connected.

    BUG FIX: the previous implementation captured the session from the
    ``async with _db.SessionLocal()`` block and called ``await _session.commit()``
    inside the callback.  By the time the callback fires the ``async with`` block
    has already exited and the session is closed/expired, so the commit either
    raises (silently swallowed) or is a no-op — the refreshed token was never
    written back.

    Fix: open a FRESH session inside the callback using upsert_google_credential
    (the same path the proactive refresh scheduler uses) so the write is
    independent of the credential-loading session lifetime.
    """
    import logging as _logging
    from datetime import UTC, datetime

    import artemis.db as _db
    from artemis.google_docs.repository import get_google_credential, upsert_google_credential
    from artemis.google_integration import (
        google_has_any_scope,
        resolve_google_oauth_client_config,
    )
    from artemis.integrations.gmail.client import GmailClient

    _logger = _logging.getLogger(__name__)

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

        # Snapshot the fields we need for the callback — do NOT capture the
        # session or ORM object (both will be expired once the block exits).
        _user_id = credential.user_id
        _scope = credential.scope
        _connected_email = credential.connected_email
        _refresh_token_snapshot = credential.refresh_token
        _expires_at = credential.expiry.timestamp()
        _access_token = credential.access_token
        _refresh_token = credential.refresh_token or ""
        _client_id = config.client_id
        _client_secret = config.client_secret

    async def _on_tokens_refreshed(
        new_access_token: str, new_refresh_token: str, new_expires_at: float
    ) -> None:
        """Persist refreshed Gmail tokens via a fresh DB session.

        This callback is invoked from inside GmailClient._refresh() AFTER the
        token exchange succeeds.  We open a new session here because the session
        used to load the credential has already been closed.
        """
        try:
            async with _db.SessionLocal() as _session:
                await upsert_google_credential(
                    _session,
                    user_id=_user_id,
                    purpose="personal",
                    access_token=new_access_token,
                    refresh_token=new_refresh_token or _refresh_token_snapshot,
                    expiry=datetime.fromtimestamp(new_expires_at, tz=UTC),
                    scope=_scope,
                    connected_email=_connected_email,
                )
                await _session.commit()
        except Exception:
            _logger.warning("Gmail on_tokens_refreshed: failed to persist tokens", exc_info=True)

    return GmailClient(
        access_token=_access_token,
        refresh_token=_refresh_token,
        client_id=_client_id,
        client_secret=_client_secret,
        expires_at=_expires_at,
        on_tokens_refreshed=_on_tokens_refreshed,
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
