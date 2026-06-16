"""Minimal Gmail REST client."""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from email.message import EmailMessage
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ParamScalar = str | int | float | bool | None
_QueryParams = Mapping[str, _ParamScalar | Sequence[_ParamScalar]]
_OnTokensRefreshed = Callable[[str, str, float], Coroutine[Any, Any, None]]


class GmailAPIError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Gmail API error {status}: {body}")
        self.status = status
        self.body = body


class GmailAuthDeadError(Exception):
    """Raised when the Gmail refresh token has been revoked and reauth is required.

    This is a hard stop — retrying with the same tokens will not help.
    The user must reconnect their Google account.
    """


class GmailClient:
    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        expires_at: float = 0.0,
        on_tokens_refreshed: _OnTokensRefreshed | None = None,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._expires_at = expires_at
        self._on_tokens_refreshed = on_tokens_refreshed

    @property
    def access_token(self) -> str:
        return self._access_token

    async def _refresh(self) -> None:
        """Exchange the refresh token for a new access token.

        Raises:
            GmailAuthDeadError: if Google returns 400 invalid_grant (revoked token).
            httpx.HTTPStatusError: for other non-success responses.
        """
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )

        if resp.status_code == 400:
            # Google returns 400 + {"error":"invalid_grant"} when the refresh
            # token has been revoked or the account password changed.
            # This is a permanent failure — do not raise_for_status into a
            # generic error; surface it as GmailAuthDeadError so callers can
            # distinguish it from transient failures.
            logger.error(
                "Gmail refresh token rejected (400 invalid_grant) — reauth required. "
                "body=[REDACTED]"
            )
            raise GmailAuthDeadError("Google refresh token revoked; reauth required")

        resp.raise_for_status()

        body = resp.json()
        new_access_token: str = str(body["access_token"])
        expires_in: int = int(body.get("expires_in", 3600))
        new_refresh_token: str = str(body.get("refresh_token") or self._refresh_token)
        new_expires_at: float = time.time() + expires_in

        self._access_token = new_access_token
        self._refresh_token = new_refresh_token
        self._expires_at = new_expires_at

        if self._on_tokens_refreshed is not None:
            try:
                await self._on_tokens_refreshed(
                    self._access_token,
                    self._refresh_token,
                    new_expires_at,
                )
            except Exception:
                logger.debug("Gmail on_tokens_refreshed callback failed", exc_info=True)

    async def _get(
        self,
        path: str,
        *,
        params: _QueryParams | None = None,
    ) -> dict[str, Any]:
        url = f"{_GMAIL_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(url, headers=headers, params=params)
            if resp.status_code == 401 and self._refresh_token:
                await self._refresh()
                headers = {"Authorization": f"Bearer {self._access_token}"}
                resp = await http.get(url, headers=headers, params=params)
        if not resp.is_success:
            raise GmailAPIError(resp.status_code, resp.text)
        payload: dict[str, Any] = resp.json()
        return payload

    async def _post(
        self,
        path: str,
        *,
        json: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        url = f"{_GMAIL_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(url, headers=headers, json=json)
            if resp.status_code == 401 and self._refresh_token:
                await self._refresh()
                headers = {"Authorization": f"Bearer {self._access_token}"}
                resp = await http.post(url, headers=headers, json=json)
        if not resp.is_success:
            raise GmailAPIError(resp.status_code, resp.text)
        payload: dict[str, Any] = resp.json()
        return payload

    async def list_recent_messages(
        self,
        *,
        max_results: int = 10,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = await self._get(
            "/messages",
            params={
                "maxResults": max_results,
                "q": query,
            },
        )
        items = payload.get("messages") or []
        if not isinstance(items, list):
            return []
        messages: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            message_id = str(item.get("id") or "")
            if not message_id:
                continue
            detail = await self._get(
                f"/messages/{message_id}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["Subject", "From", "Date"],
                },
            )
            messages.append(_message_summary(detail))
        return messages

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        payload = await self._get(
            f"/threads/{thread_id}",
            params={
                "format": "metadata",
                "metadataHeaders": ["Subject", "From", "Date"],
            },
        )
        raw_messages = payload.get("messages") or []
        messages = [_message_summary(item) for item in raw_messages if isinstance(item, dict)]
        return {
            "threadId": str(payload.get("id") or thread_id),
            "historyId": payload.get("historyId"),
            "messages": messages,
        }

    async def send_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> dict[str, Any]:
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)

        payload: dict[str, object] = {
            "raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        }
        if thread_id:
            payload["threadId"] = thread_id

        resp = await self._post("/messages/send", json=payload)
        return {
            "id": str(resp.get("id") or ""),
            "threadId": str(resp.get("threadId") or thread_id or ""),
            "labelIds": resp.get("labelIds") or [],
        }


def _message_summary(payload: dict[str, Any]) -> dict[str, Any]:
    headers = payload.get("payload", {}).get("headers", [])
    header_map: dict[str, str] = {}
    if isinstance(headers, list):
        for entry in headers:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").lower()
            value = str(entry.get("value") or "")
            if name:
                header_map[name] = value
    return {
        "id": str(payload.get("id") or ""),
        "threadId": str(payload.get("threadId") or ""),
        "snippet": str(payload.get("snippet") or ""),
        "subject": header_map.get("subject", ""),
        "from": header_map.get("from", ""),
        "date": header_map.get("date", ""),
        "internalDate": str(payload.get("internalDate") or ""),
    }
