"""Async Slack Web API client.

Wraps the subset of Slack API methods needed for outbound Artemis tools.
All HTTP is done via httpx.AsyncClient; a single Retry-After 429 is honoured.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

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
        # Slack's Web API requires application/x-www-form-urlencoded for many
        # read-style endpoints (e.g. conversations.info, users.info). JSON
        # bodies silently fail with "missing required field" / invalid_arguments
        # on those methods. Form-encoding works universally; non-scalar values
        # (lists/dicts like ``blocks``) must be JSON-stringified per Slack docs.
        form: dict[str, str] = {}
        for k, v in kwargs.items():
            if isinstance(v, (list, dict)):
                form[k] = json.dumps(v)
            elif isinstance(v, bool):
                form[k] = "true" if v else "false"
            else:
                form[k] = str(v)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        url = f"{_SLACK_API_BASE}/{method}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, data=form)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "1"))
                await asyncio.sleep(retry_after)
                resp = await client.post(url, headers=headers, data=form)
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
        unfurl_links: bool | None = None,
        unfurl_media: bool | None = None,
    ) -> dict[str, object]:
        """Post a message. ``unfurl_*`` default to None = leave Slack's default.

        Pass ``unfurl_links=False`` for any message carrying several links to
        an aggregator. Google News RSS links are redirect URLs, so Slack
        unfurled each one into an identical "Comprehensive up-to-date news
        coverage..." card -- three of them stacked under one brief, which was
        most of what made it look broken.
        """
        kwargs: dict[str, object] = {"channel": channel, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        if blocks is not None:
            kwargs["blocks"] = blocks
        if unfurl_links is not None:
            kwargs["unfurl_links"] = unfurl_links
        if unfurl_media is not None:
            kwargs["unfurl_media"] = unfurl_media
        return await self._post("chat.postMessage", **kwargs)

    async def post_dm(
        self, user: str, text: str, blocks: Sequence[object] | None = None
    ) -> dict[str, object]:
        open_resp = await self._post("conversations.open", users=user)
        channel_raw = open_resp["channel"]
        channel_id = str(channel_raw["id"]) if isinstance(channel_raw, dict) else str(channel_raw)
        kwargs: dict[str, object] = {"channel": channel_id, "text": text}
        if blocks is not None:
            kwargs["blocks"] = blocks
        return await self._post("chat.postMessage", **kwargs)

    async def views_open(self, trigger_id: str, view: dict[str, object]) -> dict[str, object]:
        """Open a modal (``views.open``) in response to a ``trigger_id``.

        ``trigger_id`` is single-use and expires quickly (Slack's ~3-second
        interactivity window) -- callers must call this immediately after
        receiving the block_actions payload that carried it, not after any
        slower work (e.g. persistence).
        """
        return await self._post("views.open", trigger_id=trigger_id, view=view)

    async def get_conversation_members(self, channel: str, limit: int = 50) -> list[str]:
        """Slack user ids in a conversation, via ``conversations.members``.

        Used to tell an agent WHO IS IN THE ROOM. Callie was asked to "tell Josh
        the top signals" while Josh Mukai was a member of that very channel, and
        answered by fuzzy-matching the name against the whole company directory
        -- offering "Josh Smith (0.9 confidence)" (2026-08-12). The participants
        were knowable the entire time.

        First page only, capped: this exists to name a handful of people in a
        working channel, not to enumerate a 500-member one. Returns an empty
        list on any failure -- the caller degrades to no participant context
        rather than failing the turn.
        """
        try:
            data = await self._post("conversations.members", channel=channel, limit=limit)
        except SlackAPIError:
            return []
        members = data.get("members")
        return [str(m) for m in members] if isinstance(members, list) else []

    async def get_channel_history(self, channel: str, limit: int = 20) -> list[dict[str, object]]:
        data = await self._post("conversations.history", channel=channel, limit=limit)
        messages: list[dict[str, object]] = data.get("messages", [])  # type: ignore[assignment]
        return messages

    async def add_reaction(self, channel: str, ts: str, emoji: str) -> dict[str, object]:
        return await self._post("reactions.add", channel=channel, timestamp=ts, name=emoji)

    async def get_permalink(self, channel: str, message_ts: str) -> str:
        """Call ``chat.getPermalink`` -- a link to the MESSAGE, never its files.

        Used by the crisis-content image-link flow (CCA10) to point Jen's
        doc at a Slack message that carries an attachment, without ever
        needing ``files:read`` or fetching a file's ``url_private`` -- this
        method touches no file content at all, only the message's own
        location. Raises ``SlackAPIError`` if Slack reports an error (e.g.
        ``message_not_found``) or if a 200 response is missing
        ``permalink`` despite ``ok: true``.
        """
        data = await self._post("chat.getPermalink", channel=channel, message_ts=message_ts)
        permalink = data.get("permalink")
        if not isinstance(permalink, str) or not permalink:
            raise SlackAPIError("chat.getPermalink", "response missing 'permalink'")
        return permalink

    async def list_channels(self, limit: int = 200) -> list[dict[str, object]]:
        data = await self._post(
            "conversations.list",
            types="public_channel,private_channel",
            limit=limit,
        )
        channels: list[dict[str, object]] = data.get("channels", [])  # type: ignore[assignment]
        return channels

    async def lookup_user_by_email(self, email: str) -> str | None:
        """Resolve a Slack user ID from an email via ``users.lookupByEmail``.

        This is the canonical email→user resolution and works regardless of
        workspace size. The older ``list_users`` path only fetched the first
        ``users.list`` page (no pagination), so members past the first page were
        silently missed. Returns ``None`` when Slack reports ``users_not_found``.
        """
        try:
            data = await self._post("users.lookupByEmail", email=email)
        except SlackAPIError as exc:
            if "users_not_found" in str(exc):
                return None
            raise
        user = data.get("user")
        if isinstance(user, dict):
            user_id = user.get("id")
            return str(user_id) if user_id else None
        return None

    async def lookup_user_email(self, user_id: str) -> str | None:
        """Resolve a Slack user ID to their email via ``users.info``.

        The reverse of ``lookup_user_by_email``, and the authoritative answer
        to "who is this id" -- Slack owns its own user records, so this is
        immune to drift in our ``directory_people`` cache.

        Added after a production incident (2026-08-12): crisis-content
        authorization resolved identity from ``directory_people`` alone, every
        approver in that table had ``slack_user_id = NULL``, so every lookup
        missed and NOBODY could approve anything. Nothing errored; the pipeline
        simply refused every click.

        Requires ``users:read.email``. Returns ``None`` when the user is
        unknown or has no visible email (external Slack Connect users often
        expose one, but do not rely on it).
        """
        if not user_id:
            return None
        try:
            data = await self._post("users.info", user=user_id)
        except SlackAPIError as exc:
            if "user_not_found" in str(exc):
                return None
            raise
        user = data.get("user")
        if not isinstance(user, dict):
            return None
        profile = user.get("profile")
        email = profile.get("email") if isinstance(profile, dict) else None
        return str(email) if email else None

    # ── User-token methods (require user OAuth token, not bot token) ─────────

    async def search_messages(
        self,
        query: str,
        *,
        count: int = 20,
        sort: str = "timestamp",
        sort_dir: str = "desc",
    ) -> list[dict[str, object]]:
        """Call ``search.messages`` (requires user token with ``search:read``).

        Returns the list of message hit dicts from the ``messages.matches``
        field.  Each hit has: ``type``, ``channel``, ``user``, ``username``,
        ``ts``, ``text``, ``permalink``, ``iid``.
        """
        data = await self._post(
            "search.messages",
            query=query,
            count=count,
            sort=sort,
            sort_dir=sort_dir,
        )
        messages_block = data.get("messages") or {}
        matches: list[dict[str, object]] = []
        if isinstance(messages_block, dict):
            raw = messages_block.get("matches") or []
            if isinstance(raw, list):
                matches = raw
        return matches

    async def get_conversation_replies(
        self,
        channel: str,
        thread_ts: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Call ``conversations.replies`` to fetch all messages in a thread.

        Returns the list of message dicts (oldest first).  The first element
        is the parent message; subsequent elements are replies.
        """
        data = await self._post(
            "conversations.replies",
            channel=channel,
            ts=thread_ts,
            limit=limit,
        )
        messages: list[dict[str, object]] = data.get("messages", [])  # type: ignore[assignment]
        return messages

    async def get_im_history(
        self,
        channel: str,
        *,
        oldest: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Call ``conversations.history`` for a DM channel.

        ``oldest`` is a Slack timestamp string (e.g. "1700000000.000000");
        only messages AFTER that ts are returned.
        """
        kwargs: dict[str, object] = {"channel": channel, "limit": limit}
        if oldest is not None:
            kwargs["oldest"] = oldest
        data = await self._post("conversations.history", **kwargs)
        messages: list[dict[str, object]] = data.get("messages", [])  # type: ignore[assignment]
        return messages

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
