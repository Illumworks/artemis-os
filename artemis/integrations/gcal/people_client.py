"""Google People API client — read-only contacts/connections.

Reuses the same token-refresh pattern as GCalClient (same OAuth credentials).
Scope required: https://www.googleapis.com/auth/contacts.readonly
"""

from __future__ import annotations

import httpx

from artemis.integrations.gcal.client import GCalAPIError

_PEOPLE_BASE = "https://people.googleapis.com/v1"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Fields requested from the People API — kept minimal for speed.
_PERSON_FIELDS = "names,emailAddresses,photos"


class PeopleClient:
    """Thin async wrapper around people.googleapis.com."""

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
        url = f"{_PEOPLE_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        query_params: dict[str, str | int | float | bool | None] = {
            k: v  # type: ignore[misc]
            for k, v in params.items()
            if v is not None
        }
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(url, headers=headers, params=query_params)
            if resp.status_code == 401:
                await self._refresh()
                headers = {"Authorization": f"Bearer {self._access_token}"}
                resp = await http.get(url, headers=headers, params=query_params)
        if not resp.is_success:
            raise GCalAPIError(resp.status_code, resp.text)
        result: dict[str, object] = resp.json()
        return result

    async def search_contacts(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, str | None]]:
        """Search the authenticated user's contacts by name or email prefix.

        Returns a list of dicts with keys: name, email, avatarUrl.
        Entries without any email address are skipped.
        """
        if not query:
            return []

        # people.searchContacts supports free-text search across all fields.
        try:
            data = await self._get(
                "/people:searchContacts",
                query=query,
                readMask=_PERSON_FIELDS,
                pageSize=limit,
            )
        except GCalAPIError as exc:
            # 403 = contacts scope not granted; treat as empty rather than crash.
            if exc.status in (403, 404):
                return []
            raise

        results_raw: list[dict[str, object]] = data.get("results", [])  # type: ignore[assignment]
        people: list[dict[str, str | None]] = []

        for entry in results_raw:
            person: dict[str, object] = entry.get("person", {})  # type: ignore[assignment]

            # Extract primary email
            email_addrs: list[dict[str, object]] = person.get("emailAddresses", [])  # type: ignore[assignment]
            primary_email = next(
                (
                    str(e.get("value", ""))
                    for e in email_addrs
                    if e.get("metadata", {}).get("primary")  # type: ignore[attr-defined]
                ),
                next((str(e.get("value", "")) for e in email_addrs), None),
            )
            if not primary_email:
                continue

            # Extract display name
            names: list[dict[str, object]] = person.get("names", [])  # type: ignore[assignment]
            display_name = next(
                (
                    str(n.get("displayName", ""))
                    for n in names
                    if n.get("metadata", {}).get("primary")  # type: ignore[attr-defined]
                ),
                next((str(n.get("displayName", "")) for n in names), None),
            )

            # Extract avatar URL (first photo)
            photos: list[dict[str, object]] = person.get("photos", [])  # type: ignore[assignment]
            avatar_url: str | None = next(
                (str(p.get("url", "")) for p in photos if p.get("url")),
                None,
            )

            people.append(
                {
                    "name": display_name or primary_email,
                    "email": primary_email,
                    "avatarUrl": avatar_url,
                }
            )

        return people
