"""Async Granola MCP API client.

Wraps the MCP StreamableHTTP endpoint at https://mcp.granola.ai/mcp.
All methods send JSON-RPC 2.0 tools/call requests. Responses may arrive as
JSON or SSE (text/event-stream); both are handled.

Token refresh is automatic: if the stored access_token is within 60 s of
expiry the client exchanges the refresh_token at mcp-auth.granola.ai and
persists the new tokens to the integration row via the passed `token_refresher`
callback.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRANOLA_MCP_URL = "https://mcp.granola.ai/mcp"
GRANOLA_TOKEN_ENDPOINT = "https://mcp-auth.granola.ai/oauth2/token"
GRANOLA_AUTH_ENDPOINT = "https://mcp-auth.granola.ai/oauth2/authorize"
GRANOLA_REGISTER_ENDPOINT = "https://mcp-auth.granola.ai/oauth2/register"
GRANOLA_RESOURCE = "https://mcp.granola.ai/mcp"
GRANOLA_SCOPES = "openid profile email offline_access"

_REFRESH_LEEWAY_S = 60


class GranolaAPIError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Granola MCP {status}: {body[:200]}")
        self.status = status
        self.body = body


@dataclass
class Meeting:
    id: str
    title: str
    date_raw: str
    date_ms: int
    participants: list[str]


def _extract_result_text(result: dict[str, Any] | None) -> str:
    """Pull the concatenated text blocks from a MCP tools/call result."""
    if not result:
        return ""
    items = result.get("content", [])
    if not isinstance(items, list):
        return ""
    return "\n".join(c.get("text", "") for c in items if c.get("type") == "text").strip()


def _parse_meetings(text: str) -> list[Meeting]:
    """Parse <meeting …> XML-attribute tags from Granola list_meetings result text."""
    if not text:
        return []
    meetings: list[Meeting] = []
    tag_re = re.compile(r"<meeting\b([^>]*)>", re.DOTALL)
    attr_re = re.compile(r'\b(\w+)="([^"]*)"')
    for m in tag_re.finditer(text):
        attrs = dict(attr_re.findall(m.group(1)))
        meeting_id = attrs.get("id", "")
        if not meeting_id:
            continue
        title = attrs.get("title", "")
        date_raw = attrs.get("date", "")
        try:
            date_ms = int(time.mktime(time.strptime(date_raw, "%Y-%m-%dT%H:%M:%S")) * 1000)
        except (ValueError, OverflowError):
            date_ms = 0
        pattr = attrs.get("participants", "")
        participants = [s.strip() for s in pattr.split(",") if s.strip()] if pattr else []
        meetings.append(
            Meeting(
                id=meeting_id,
                title=title,
                date_raw=date_raw,
                date_ms=date_ms,
                participants=participants,
            )
        )
    return meetings


class GranolaClient:
    """Thin async httpx wrapper around the Granola MCP API.

    Args:
        access_token: Bearer token for the MCP endpoint.
        refresh_token: Used for auto-refresh. May be empty string for local-state mode.
        client_id: OAuth client_id; required for token refresh.
        client_secret: OAuth client_secret; may be empty (public client).
        expires_at: Unix timestamp (seconds) when the access_token expires.
        on_tokens_refreshed: Async callback(access_token, refresh_token, expires_at)
            called after a successful token refresh so the caller can persist
            the new tokens. If None, refresh is still attempted but not persisted.
    """

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        expires_at: float = 0.0,
        on_tokens_refreshed: Any = None,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._expires_at = expires_at
        self._on_tokens_refreshed = on_tokens_refreshed

    # ── Token management ─────────────────────────────────────────────────────

    async def _ensure_fresh_token(self) -> str:
        """Return a valid access token, refreshing if within the leeway window."""
        if self._expires_at and time.time() < self._expires_at - _REFRESH_LEEWAY_S:
            return self._access_token

        if self._refresh_token and self._client_id:
            refreshed = await self._refresh_token_exchange()
            if refreshed:
                return self._access_token  # updated by _refresh_token_exchange

        return self._access_token

    async def _refresh_token_exchange(self) -> bool:
        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret

        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    GRANOLA_TOKEN_ENDPOINT,
                    data=data,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning("Granola token refresh network error: %s", exc)
            return False

        if not resp.is_success:
            logger.warning("Granola token refresh failed: %s", resp.status_code)
            return False

        body = resp.json()
        new_access = body.get("access_token")
        if not new_access:
            return False

        expires_in = int(body.get("expires_in", 3600))
        new_expires_at = time.time() + expires_in
        new_refresh = body.get("refresh_token") or self._refresh_token

        self._access_token = str(new_access)
        self._refresh_token = str(new_refresh)
        self._expires_at = new_expires_at

        if self._on_tokens_refreshed:
            try:
                await self._on_tokens_refreshed(
                    access_token=self._access_token,
                    refresh_token=self._refresh_token,
                    expires_at=new_expires_at,
                )
            except Exception:
                logger.debug("on_tokens_refreshed callback failed", exc_info=True)

        return True

    # ── MCP transport ────────────────────────────────────────────────────────

    async def _call(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a tools/call JSON-RPC request; return the result dict."""
        token = await self._ensure_fresh_token()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        }

        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                GRANOLA_MCP_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-03-26",
                },
            )

        if not resp.is_success:
            raise GranolaAPIError(resp.status_code, resp.text)

        content_type = resp.headers.get("content-type", "")
        body: dict[str, Any] | None = None

        if "text/event-stream" in content_type:
            # Parse SSE: find the first data: line whose JSON has a result key
            for line in resp.text.splitlines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                try:
                    candidate = json.loads(data_str)
                    if "result" in candidate:
                        body = candidate
                        break
                except json.JSONDecodeError:
                    continue
        else:
            try:
                body = resp.json()
            except Exception as exc:
                raise GranolaAPIError(resp.status_code, f"JSON parse error: {exc}") from exc

        if body is None:
            raise GranolaAPIError(resp.status_code, "Empty or unparseable MCP response")

        if "error" in body:
            err = body["error"]
            msg: str = str(err.get("message", "")) if isinstance(err, dict) else str(err)
            raise GranolaAPIError(0, msg)

        result = body.get("result", {})
        return result if isinstance(result, dict) else {}

    # ── Public API ───────────────────────────────────────────────────────────

    async def get_account_info(self) -> dict[str, Any]:
        """Return account details (email, name, etc.) from the Granola API."""
        result = await self._call("get_account_info")
        text = _extract_result_text(result)
        # Try to parse structured JSON first; fall back to raw text
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"raw": text}
        except (json.JSONDecodeError, ValueError):
            return {"raw": text}

    async def list_meetings(
        self,
        *,
        time_range: str = "last_30_days",
        limit: int | None = None,
    ) -> list[Meeting]:
        """Return a list of meetings for the given time range.

        time_range values accepted by Granola: last_30_days, last_7_days, this_week
        """
        result = await self._call("list_meetings", {"time_range": time_range})
        text = _extract_result_text(result)
        meetings = _parse_meetings(text)
        if limit is not None:
            meetings = meetings[:limit]
        return meetings

    async def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        """Return full meeting detail: title, attendees, transcript, summary."""
        result = await self._call("get_meeting_transcript", {"meeting_id": meeting_id})
        text = _extract_result_text(result)
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"transcript": text}
        except (json.JSONDecodeError, ValueError):
            return {"transcript": text}

    async def list_meeting_folders(self) -> list[dict[str, Any]]:
        """Return user's organized meeting folders."""
        result = await self._call("list_meeting_folders")
        text = _extract_result_text(result)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            return [parsed] if parsed else []
        except (json.JSONDecodeError, ValueError):
            return [{"raw": text}] if text else []

    async def query_meetings(self, query: str) -> str:
        """Semantic search over meetings; returns raw result text with citations."""
        result = await self._call("query_granola_meetings", {"query": query})
        return _extract_result_text(result)
