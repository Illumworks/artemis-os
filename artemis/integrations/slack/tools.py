"""Slack outbound tools for the Floating Artemis tool registry.

Five tools — two read-only (layer 2) and three side-effect (layer 3).
Each implementation resolves the active Slack integration at call time.
"""

from __future__ import annotations

import json
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

def _slack_token(creds: dict[str, Any]) -> str:
    """Return the usable bearer token from a decrypted Slack credential.

    Bot integrations store the token under ``bot_token``; OAuth v2 rows store it
    under ``access_token``.  Accept either — a mixed set of per-agent rows must
    not KeyError.  (This was the bug that surfaced to agents as the opaque
    ``list_slack_channels failed: 'access_token'`` — a KeyError on a bot-token
    row that only carries ``bot_token``.)
    """
    token = creds.get("access_token") or creds.get("bot_token")
    if not token:
        raise RuntimeError("active Slack integration has no access_token or bot_token")
    return str(token)


# ── Implementations ───────────────────────────────────────────────────────────


async def _send_slack_message(inp: dict[str, Any]) -> str:
    channel: str = inp.get("channel", "")
    text: str = inp.get("text", "")
    thread_ts: str | None = inp.get("thread_ts")
    blocks: list[object] | None = inp.get("blocks")
    if not channel or not text:
        return "Error: channel and text are required"
    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.slack.client import SlackClient

        async with _db.SessionLocal() as session:
            integrations = await repo.list_active(session, provider="slack")
        if not integrations:
            return "No active Slack integration found"
        creds = decrypt_credentials(bytes(integrations[0].encrypted_credentials))
        token = _slack_token(creds)
        result = await SlackClient(token).post_message(
            channel, text, thread_ts=thread_ts, blocks=blocks
        )
        return json.dumps(result)
    except Exception as exc:
        return f"send_slack_message failed: {exc}"


async def _send_slack_dm(inp: dict[str, Any]) -> str:
    user: str = inp.get("user", "")
    text: str = inp.get("text", "")
    if not user or not text:
        return "Error: user and text are required"
    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.slack.client import SlackClient

        async with _db.SessionLocal() as session:
            integrations = await repo.list_active(session, provider="slack")
        if not integrations:
            return "No active Slack integration found"
        creds = decrypt_credentials(bytes(integrations[0].encrypted_credentials))
        token = _slack_token(creds)
        result = await SlackClient(token).post_dm(user, text)
        return json.dumps(result)
    except Exception as exc:
        return f"send_slack_dm failed: {exc}"


async def _read_slack_channel(inp: dict[str, Any]) -> str:
    channel: str = inp.get("channel", "")
    limit: int = int(inp.get("limit", 20))
    if not channel:
        return "Error: channel is required"
    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.slack.client import SlackClient

        async with _db.SessionLocal() as session:
            integrations = await repo.list_active(session, provider="slack")
        if not integrations:
            return "No active Slack integration found"
        creds = decrypt_credentials(bytes(integrations[0].encrypted_credentials))
        token = _slack_token(creds)
        messages = await SlackClient(token).get_channel_history(channel, limit=limit)
        return json.dumps(messages)
    except Exception as exc:
        return f"read_slack_channel failed: {exc}"


async def _react_to_slack_message(inp: dict[str, Any]) -> str:
    channel: str = inp.get("channel", "")
    ts: str = inp.get("ts", "")
    emoji: str = inp.get("emoji", "")
    if not channel or not ts or not emoji:
        return "Error: channel, ts, and emoji are required"
    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.slack.client import SlackClient

        async with _db.SessionLocal() as session:
            integrations = await repo.list_active(session, provider="slack")
        if not integrations:
            return "No active Slack integration found"
        creds = decrypt_credentials(bytes(integrations[0].encrypted_credentials))
        token = _slack_token(creds)
        result = await SlackClient(token).add_reaction(channel, ts, emoji)
        return json.dumps(result)
    except Exception as exc:
        return f"react_to_slack_message failed: {exc}"


async def _list_slack_channels(inp: dict[str, Any]) -> str:
    limit: int = int(inp.get("limit", 200))
    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.slack.client import SlackClient

        async with _db.SessionLocal() as session:
            integrations = await repo.list_active(session, provider="slack")
        if not integrations:
            return "No active Slack integration found"
        creds = decrypt_credentials(bytes(integrations[0].encrypted_credentials))
        token = _slack_token(creds)
        channels = await SlackClient(token).list_channels(limit=limit)
        return json.dumps(channels)
    except Exception as exc:
        return f"list_slack_channels failed: {exc}"


# ── Tool definitions ──────────────────────────────────────────────────────────

SEND_SLACK_MESSAGE = Tool(
    name="send_slack_message",
    description="Post a message to a Slack channel or thread. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description": "Channel ID or name (e.g. #general)"},
            "text": {"type": "string"},
            "thread_ts": {"type": "string", "description": "Parent thread ts to reply in-thread"},
            "blocks": {"type": "array", "description": "Optional Block Kit blocks"},
        },
        "required": ["channel", "text"],
    },
)

SEND_SLACK_DM = Tool(
    name="send_slack_dm",
    description="Send a direct message to a Slack user. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "user": {"type": "string", "description": "Slack user ID (e.g. U12345)"},
            "text": {"type": "string"},
        },
        "required": ["user", "text"],
    },
)

READ_SLACK_CHANNEL = Tool(
    name="read_slack_channel",
    description="Read recent messages from a Slack channel. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "channel": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["channel"],
    },
)

REACT_TO_SLACK_MESSAGE = Tool(
    name="react_to_slack_message",
    description="Add an emoji reaction to a Slack message. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "channel": {"type": "string"},
            "ts": {"type": "string", "description": "Message timestamp"},
            "emoji": {"type": "string", "description": "Emoji name without colons (e.g. thumbsup)"},
        },
        "required": ["channel", "ts", "emoji"],
    },
)

LIST_SLACK_CHANNELS = Tool(
    name="list_slack_channels",
    description="List channels in the connected Slack workspace. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 200},
        },
        "required": [],
    },
)


def register_slack_tools(registry: AuthorizedToolRegistry) -> None:
    """Register all Slack outbound tools into the provided registry."""
    registry.register(SEND_SLACK_MESSAGE, _send_slack_message, layer=3)
    registry.register(SEND_SLACK_DM, _send_slack_dm, layer=3)
    registry.register(READ_SLACK_CHANNEL, _read_slack_channel, layer=2)
    registry.register(REACT_TO_SLACK_MESSAGE, _react_to_slack_message, layer=3)
    registry.register(LIST_SLACK_CHANNELS, _list_slack_channels, layer=2)
