from __future__ import annotations

import httpx

from artemis.integrations.gcal.types import Calendar, Event, EventDateTime

_GCAL_BASE = "https://www.googleapis.com/calendar/v3"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GCalAPIError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Google Calendar API error {status}: {body}")
        self.status = status
        self.body = body


class GCalClient:
    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret

    @property
    def access_token(self) -> str:
        return self._access_token

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
        self._access_token = resp.json()["access_token"]

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
