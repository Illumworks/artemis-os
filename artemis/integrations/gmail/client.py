"""Minimal Gmail REST client."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from email.message import EmailMessage
from typing import Any

import httpx

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ParamScalar = str | int | float | bool | None
_QueryParams = Mapping[str, _ParamScalar | Sequence[_ParamScalar]]


class GmailAPIError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Gmail API error {status}: {body}")
        self.status = status
        self.body = body


class GmailClient:
    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret

    async def _refresh(self) -> None:
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
        resp.raise_for_status()
        self._access_token = str(resp.json()["access_token"])

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
