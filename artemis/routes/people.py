"""People search router — /api/people.

Merges contacts from Google People API and Slack workspace members into a
single deduped list keyed on email (case-insensitive).

GET /api/people/search?q=<prefix>&limit=20
  Response: list[PersonResult]

If neither integration is connected → 200 with [].
If one is connected → results from that provider only.
In-memory TTL cache (60 s) keyed on (q.lower(), limit) reduces API hammering
during debounced typing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.gcal.people_client import PeopleClient
from artemis.integrations.slack.client import SlackClient
from artemis.marketing.routes._auth import require_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/people",
    tags=["people"],
    dependencies=[Depends(require_token)],
)

# ---------------------------------------------------------------------------
# Simple in-memory TTL cache
# ---------------------------------------------------------------------------

_CACHE_TTL = 60.0  # seconds

_cache: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}


def _cache_get(key: tuple[str, int]) -> list[dict[str, Any]] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


def _cache_set(key: tuple[str, int], value: list[dict[str, Any]]) -> None:
    _cache[key] = (time.monotonic(), value)


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------


async def _fetch_gcal_people(
    creds: dict[str, object],
    query: str,
    limit: int,
    integration_id: int | None = None,
) -> list[dict[str, Any]]:
    """Call People API and return normalised results.

    When integration_id is provided an on_tokens_refreshed callback is wired up
    so any on-the-fly token refresh is persisted back to the DB.
    """

    async def _on_tokens_refreshed(
        access_token: str, refresh_token: str, expires_at: float
    ) -> None:
        if integration_id is None:
            return
        import artemis.db as _db
        from artemis.integrations import repository as repo

        new_creds = dict(creds)
        new_creds["access_token"] = access_token
        new_creds["refresh_token"] = refresh_token
        new_creds["expires_at"] = expires_at
        async with _db.SessionLocal() as _session:
            try:
                await repo.persist_refreshed_credentials(
                    _session,
                    integration_id=integration_id,
                    new_creds=new_creds,
                )
                await _session.commit()
            except Exception:
                logger.debug(
                    "people: persist_refreshed_credentials failed for integration_id=%d",
                    integration_id,
                    exc_info=True,
                )

    client = PeopleClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
        expires_at=float(str(creds.get("expires_at") or 0)),
        on_tokens_refreshed=_on_tokens_refreshed if integration_id is not None else None,
    )
    try:
        contacts = await client.search_contacts(query, limit=limit)
    except Exception as exc:
        logger.warning("People API error: %s", exc)
        return []

    return [
        {
            "name": c.get("name") or c.get("email", ""),
            "email": (c.get("email") or "").lower(),
            "source": "gcal",
            "avatarUrl": c.get("avatarUrl"),
        }
        for c in contacts
        if c.get("email")
    ]


async def _fetch_slack_people(
    token: str,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Call Slack users.list, filter by query, and return normalised results."""
    client = SlackClient(token)
    try:
        members = await client.list_users(query=query, limit=limit)
    except Exception as exc:
        logger.warning("Slack users.list error: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for m in members:
        profile: dict[str, object] = m.get("profile", {})  # type: ignore[assignment]
        email = str(profile.get("email", "")).strip().lower()
        if not email:
            continue
        display_name = str(profile.get("display_name") or m.get("real_name", "")).strip()
        real_name = str(m.get("real_name", "")).strip()
        name = display_name or real_name or email
        avatar_url: str | None = str(profile.get("image_48", "")) or None
        results.append(
            {
                "name": name,
                "email": email,
                "source": "slack",
                "avatarUrl": avatar_url if avatar_url else None,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Merge + dedupe
# ---------------------------------------------------------------------------


def _merge(
    gcal_results: list[dict[str, Any]],
    slack_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two lists, deduplicating on email (case-insensitive).

    When the same email appears in both providers:
    - source becomes "both"
    - name is taken from whichever entry is richer (longer)
    - avatarUrl prefers Google (usually higher res)
    """
    merged: dict[str, dict[str, Any]] = {}

    for person in gcal_results:
        email = person["email"].lower()
        merged[email] = person

    for person in slack_results:
        email = person["email"].lower()
        if email in merged:
            existing = merged[email]
            # Prefer longer name
            if len(person.get("name", "")) > len(existing.get("name", "")):
                existing["name"] = person["name"]
            # Avatar: keep gcal if present, else use slack
            if not existing.get("avatarUrl") and person.get("avatarUrl"):
                existing["avatarUrl"] = person["avatarUrl"]
            existing["source"] = "both"
        else:
            merged[email] = person

    return list(merged.values())


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/search")
async def search_people(
    q: str = Query(default="", description="Name or email prefix to search"),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Search connected contacts across Google People API and Slack.

    Returns a merged, deduped list sorted by name.  If no providers are
    connected, returns an empty list (never an error).
    """
    q_stripped = q.strip()
    if not q_stripped:
        return []

    cache_key = (q_stripped.lower(), limit)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Resolve connected integrations
    gcal_rows = await repo.list_active(session, provider="gcal")
    slack_rows = await repo.list_active(session, provider="slack")

    gcal_creds: dict[str, object] | None = None
    gcal_integration_id: int | None = None
    slack_token: str | None = None

    if gcal_rows:
        try:
            gcal_creds = decrypt_credentials(bytes(gcal_rows[0].encrypted_credentials))
            gcal_integration_id = gcal_rows[0].id
        except Exception:
            gcal_creds = None

    if slack_rows:
        try:
            raw = decrypt_credentials(bytes(slack_rows[0].encrypted_credentials))
            slack_token = str(raw.get("access_token", "")) or None
        except Exception:
            slack_token = None

    if not gcal_creds and not slack_token:
        return []

    # Fan out both API calls with 500 ms budget each
    async def _gcal_task() -> list[dict[str, Any]]:
        if not gcal_creds:
            return []
        try:
            return await asyncio.wait_for(
                _fetch_gcal_people(gcal_creds, q_stripped, limit, gcal_integration_id),
                timeout=0.5,
            )
        except TimeoutError:
            logger.warning("People API timed out for q=%r", q_stripped)
            return []

    async def _slack_task() -> list[dict[str, Any]]:
        if not slack_token:
            return []
        try:
            return await asyncio.wait_for(
                _fetch_slack_people(slack_token, q_stripped, limit),
                timeout=0.5,
            )
        except TimeoutError:
            logger.warning("Slack users.list timed out for q=%r", q_stripped)
            return []

    gcal_results, slack_results = await asyncio.gather(_gcal_task(), _slack_task())

    merged = _merge(gcal_results, slack_results)
    # Sort by name for stable ordering
    merged.sort(key=lambda p: (p.get("name") or "").lower())
    # Honour limit after merge
    merged = merged[:limit]

    _cache_set(cache_key, merged)
    return merged
