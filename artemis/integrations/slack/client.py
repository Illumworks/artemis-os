"""Async Slack Web API client.

Wraps the subset of Slack API methods needed for outbound Artemis tools.
All HTTP is done via httpx.AsyncClient; a single Retry-After 429 is honoured.
"""

from __future__ import annotations

import asyncio

import httpx

_SLACK_API_BASE = "https://slack.com/api"


class SlackAPIError(Exception):
    def __init__(self, method: str, error: str) -> None:
        super().__init__(f"Slack {method} failed: {error}")
        self.method = method
        self.error = error


class SlackClient:
    def __init__(self, token: str) -> None:
        self._token = token

    async def _post(self, method: str, **kwargs: object) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {self._token}"}
        url = f"{_SLACK_API_BASE}/{method}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, json=kwargs)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "1"))
                await asyncio.sleep(retry_after)
                resp = await client.post(url, headers=headers, json=kwargs)
            resp.raise_for_status()
        data: dict[str, object] = resp.json()
        if not data.get("ok"):
            raise SlackAPIError(method, str(data.get("error", "unknown")))
        return data

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list[object] | None = None,
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {"channel": channel, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        if blocks is not None:
            kwargs["blocks"] = blocks
        return await self._post("chat.postMessage", **kwargs)

    async def post_dm(self, user: str, text: str) -> dict[str, object]:
        open_resp = await self._post("conversations.open", users=user)
        channel_raw = open_resp["channel"]
        channel_id = str(channel_raw["id"]) if isinstance(channel_raw, dict) else str(channel_raw)
        return await self._post("chat.postMessage", channel=channel_id, text=text)

    async def get_channel_history(self, channel: str, limit: int = 20) -> list[dict[str, object]]:
        data = await self._post("conversations.history", channel=channel, limit=limit)
        messages: list[dict[str, object]] = data.get("messages", [])  # type: ignore[assignment]
        return messages

    async def add_reaction(self, channel: str, ts: str, emoji: str) -> dict[str, object]:
        return await self._post("reactions.add", channel=channel, timestamp=ts, name=emoji)

    async def list_channels(self, limit: int = 200) -> list[dict[str, object]]:
        data = await self._post(
            "conversations.list",
            types="public_channel,private_channel",
            limit=limit,
        )
        channels: list[dict[str, object]] = data.get("channels", [])  # type: ignore[assignment]
        return channels

    async def list_users(
        self, query: str | None = None, limit: int = 200
    ) -> list[dict[str, object]]:
        """Return workspace members, optionally pre-filtered by name/email prefix.

        Slack's users.list does not support server-side search, so we fetch up
        to ``limit`` members and filter client-side.  Bots and deleted accounts
        are excluded.
        """
        data = await self._post("users.list", limit=limit)
        members: list[dict[str, object]] = data.get("members", [])  # type: ignore[assignment]

        # Strip bots and deleted accounts up-front.
        members = [
            m
            for m in members
            if not m.get("is_bot") and not m.get("deleted") and m.get("id") != "USLACKBOT"
        ]

        if not query:
            return members

        q = query.lower()
        filtered: list[dict[str, object]] = []
        for m in members:
            profile: dict[str, object] = m.get("profile", {})  # type: ignore[assignment]
            real_name = str(m.get("real_name", "")).lower()
            display_name = str(profile.get("display_name", "")).lower()
            email = str(profile.get("email", "")).lower()
            if q in real_name or q in display_name or q in email:
                filtered.append(m)
        return filtered
