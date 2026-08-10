from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from artemis.integrations.gcal.types import Calendar, Event, EventDateTime

logger = logging.getLogger(__name__)

_GCAL_BASE = "https://www.googleapis.com/calendar/v3"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _extract_google_error(resp: httpx.Response) -> str:
    """Return a short diagnostic string from a Google error response.

    Google token-endpoint errors carry a JSON body like::

        {"error": "invalid_client", "error_description": "The OAuth client was not found."}

    Returns the ``error`` field (and description when present), falling back to
    the raw text so callers always get something diagnosable in the logs.
    """
    try:
        body = resp.json()
        code = str(body.get("error") or "")
        desc = str(body.get("error_description") or "")
        if code and desc:
            return f"{code}: {desc}"
        return code or resp.text[:200]
    except Exception:
        return resp.text[:200]


class GCalAPIError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Google Calendar API error {status}: {body}")
        self.status = status
        self.body = body


class GCalAuthDeadError(Exception):
    """Raised when the refresh token has been revoked and reauth is required.

    This is a hard stop — retrying with the same tokens will not help.
    The integration must be reconnected by the user.
    """


class GCalClient:
    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        expires_at: float = 0.0,
        on_tokens_refreshed: Any = None,
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
            GCalAuthDeadError: if Google returns 400 invalid_grant (revoked token).
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
            # generic error; surface it as GCalAuthDeadError so callers can
            # distinguish it from transient failures.
            _google_error = _extract_google_error(resp)
            logger.error(
                "GCal refresh token rejected (400 invalid_grant) — reauth required. "
                "google_error=%r",
                _google_error,
            )
            raise GCalAuthDeadError("Google refresh token revoked; reauth required")

        if not resp.is_success:
            # Capture Google's JSON error body before raising — a 401 means
            # invalid_client (wrong client_id/secret), a 5xx is transient.
            # Without this the logs show an opaque httpx.HTTPStatusError with
            # no indication of *why* Google rejected the request.
            _google_error = _extract_google_error(resp)
            logger.error(
                "GCal token refresh failed: status=%d google_error=%r body=%r",
                resp.status_code,
                _google_error,
                resp.text[:500],
            )
            resp.raise_for_status()

        body = resp.json()
        new_access_token: str = body["access_token"]
        expires_in: int = int(body.get("expires_in", 3600))
        new_refresh_token: str = body.get("refresh_token") or self._refresh_token
        new_expires_at: float = time.time() + expires_in

        self._access_token = new_access_token
        self._refresh_token = new_refresh_token
        self._expires_at = new_expires_at

        if self._on_tokens_refreshed is not None:
            try:
                await self._on_tokens_refreshed(
                    access_token=self._access_token,
                    refresh_token=self._refresh_token,
                    expires_at=new_expires_at,
                )
            except Exception:
                logger.debug("GCal on_tokens_refreshed callback failed", exc_info=True)

    async def _get(self, path: str, **params: object) -> dict[str, object]:
        url = f"{_GCAL_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        query_params: dict[str, str | int | float | bool | None] = {
            k: v  # type: ignore[misc]
            for k, v in params.items()
            if v is not None
        }
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(
                url,
                headers=headers,
                params=query_params,
            )
            if resp.status_code == 401:
                await self._refresh()
                headers = {"Authorization": f"Bearer {self._access_token}"}
                resp = await http.get(
                    url,
                    headers=headers,
                    params=query_params,
                )
        if not resp.is_success:
            raise GCalAPIError(resp.status_code, resp.text)
        result: dict[str, object] = resp.json()
        return result

    async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        url = f"{_GCAL_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(url, headers=headers, json=body)
            if resp.status_code == 401:
                await self._refresh()
                headers = {"Authorization": f"Bearer {self._access_token}"}
                resp = await http.post(url, headers=headers, json=body)
        if not resp.is_success:
            raise GCalAPIError(resp.status_code, resp.text)
        result: dict[str, object] = resp.json()
        return result

    async def _put(self, path: str, body: dict[str, object]) -> dict[str, object]:
        url = f"{_GCAL_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.put(url, headers=headers, json=body)
            if resp.status_code == 401:
                await self._refresh()
                headers = {"Authorization": f"Bearer {self._access_token}"}
                resp = await http.put(url, headers=headers, json=body)
        if not resp.is_success:
            raise GCalAPIError(resp.status_code, resp.text)
        result: dict[str, object] = resp.json()
        return result

    async def _delete(self, path: str) -> None:
        url = f"{_GCAL_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.delete(url, headers=headers)
            if resp.status_code == 401:
                await self._refresh()
                headers = {"Authorization": f"Bearer {self._access_token}"}
                resp = await http.delete(url, headers=headers)
        if not resp.is_success and resp.status_code != 204:
            raise GCalAPIError(resp.status_code, resp.text)

    async def list_calendars(self) -> list[Calendar]:
        data = await self._get("/users/me/calendarList")
        items: list[dict[str, object]] = data.get("items", [])  # type: ignore[assignment]
        return [Calendar.model_validate(item) for item in items]

    async def list_events(
        self,
        calendar_id: str,
        time_min: str,
        time_max: str,
        max_results: int = 50,
    ) -> list[Event]:
        data = await self._get(
            f"/calendars/{calendar_id}/events",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        items: list[dict[str, object]] = data.get("items", [])  # type: ignore[assignment]
        return [Event.model_validate(item) for item in items]

    async def get_event(self, calendar_id: str, event_id: str) -> Event:
        data = await self._get(f"/calendars/{calendar_id}/events/{event_id}")
        return Event.model_validate(data)

    async def query_freebusy(
        self,
        time_min: str,
        time_max: str,
        calendar_ids: list[str],
    ) -> dict[str, object]:
        """Query free/busy for one or more calendars (emails or calendar IDs).

        Returns the raw Google freeBusy response. The shape is::

            {
              "calendars": {
                "alice@org.com": {"busy": [{"start": "...", "end": "..."}]},
                "bob@org.com":   {"errors": [{"domain": "global",
                                              "reason": "notFound"}]}
              }
            }

        IMPORTANT — visibility caveat: Google only returns busy intervals for a
        calendar the authed account is allowed to see. Inside a Google Workspace
        org, free/busy is usually visible org-wide by default, so coworkers'
        busy times come back. For any calendar the account can't read, Google
        returns an ``errors`` array under that calendar id instead of ``busy``.
        Callers MUST treat a calendar present in ``errors`` (or absent entirely)
        as "availability unknown" and degrade gracefully — never as "free".
        """
        body: dict[str, object] = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": cid} for cid in calendar_ids],
        }
        return await self._post("/freeBusy", body)

    async def create_event(
        self,
        calendar_id: str,
        summary: str,
        start: EventDateTime,
        end: EventDateTime,
        attendees: list[str] | None = None,
        description: str | None = None,
    ) -> Event:
        body: dict[str, object] = {
            "summary": summary,
            "start": start.model_dump(by_alias=True, exclude_none=True),
            "end": end.model_dump(by_alias=True, exclude_none=True),
        }
        if description:
            body["description"] = description
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]
        data = await self._post(f"/calendars/{calendar_id}/events", body)
        return Event.model_validate(data)

    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        summary: str | None = None,
        start: EventDateTime | None = None,
        end: EventDateTime | None = None,
        attendees: list[str] | None = None,
        description: str | None = None,
    ) -> Event:
        current = await self.get_event(calendar_id, event_id)
        body: dict[str, object] = current.model_dump(by_alias=True, exclude_none=True)
        if summary is not None:
            body["summary"] = summary
        if description is not None:
            body["description"] = description
        if start is not None:
            body["start"] = start.model_dump(by_alias=True, exclude_none=True)
        if end is not None:
            body["end"] = end.model_dump(by_alias=True, exclude_none=True)
        if attendees is not None:
            body["attendees"] = [{"email": e} for e in attendees]
        data = await self._put(f"/calendars/{calendar_id}/events/{event_id}", body)
        return Event.model_validate(data)

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        await self._delete(f"/calendars/{calendar_id}/events/{event_id}")
