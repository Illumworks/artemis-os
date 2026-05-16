"""Thin client for a third-party LinkedIn public-data scraping service.

Wraps Proxycurl / Phantombuster-style APIs behind a stable interface so the
D10 LinkedIn Observer can swap providers without touching scout logic.

Auth: ``LINKEDIN_SCRAPER_API_KEY`` env var.  When unset, all methods return
empty results gracefully — no exception is raised and a warning is logged.

Usage::

    client = LinkedInScraperClient.from_env()
    posts = await client.fetch_posts("linkedin_url_or_id", since=datetime(...))
    delta = await client.check_profile_delta("linkedin_url_or_id")
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

# Base URL for the scraper service.  In production this points to Proxycurl or
# equivalent.  Tests inject a mock client so the URL is never actually hit.
_DEFAULT_BASE_URL = "https://nubela.co/proxycurl/api"  # TODO: confirm vendor

# ---------------------------------------------------------------------------
# Data types (plain dicts — no Pydantic to keep the module lightweight)
# ---------------------------------------------------------------------------
# Post dict keys: profile_id, post_id, text, posted_at (ISO str), url, is_authored
# ProfileChange dict keys: profile_id, field_changed, old_value, new_value, detected_at


# ---------------------------------------------------------------------------
# LinkedInScraperClient
# ---------------------------------------------------------------------------


class LinkedInScraperClient:
    """Async client for fetching public LinkedIn data via a scraper service.

    Parameters
    ----------
    api_key:
        Third-party scraper service API key.  When empty, all methods are
        no-ops and return empty results.
    http:
        Injected :class:`ScoutHttpClient`.  Defaults to a new client pointed
        at ``base_url``.
    base_url:
        Override the service base URL — **tests only**.
    """

    def __init__(
        self,
        api_key: str = "",
        *,
        http: ScoutHttpClient | None = None,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = http or ScoutHttpClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            rate_limit=2.0,  # conservative: avoid LinkedIn enforcement
        )

    @classmethod
    def from_env(cls) -> LinkedInScraperClient:
        """Build a client from the ``LINKEDIN_SCRAPER_API_KEY`` environment variable."""
        return cls(api_key=os.getenv("LINKEDIN_SCRAPER_API_KEY", ""))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_posts(
        self,
        profile_id: str,
        *,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent public posts for *profile_id*.

        Parameters
        ----------
        profile_id:
            LinkedIn profile URL or numeric ID.
        since:
            Only return posts published after this datetime (UTC).
            When ``None``, returns the most recent page of posts.

        Returns
        -------
        list[dict]
            Each item: ``{profile_id, post_id, text, posted_at, url, is_authored}``.
            Returns ``[]`` when the API key is unset or the request fails.
        """
        if not self._api_key:
            _logger.warning(
                "LinkedInScraperClient: LINKEDIN_SCRAPER_API_KEY unset — skipping fetch_posts."
            )
            return []

        params: dict[str, str] = {"linkedin_profile_url": profile_id}
        if since is not None:
            params["since"] = since.isoformat()

        try:
            resp = await self._http.get("/v2/linkedin/profile/posts", params=params)
            resp.raise_for_status()
            raw: dict[str, Any] = resp.json()
            return self._parse_posts(profile_id, raw)
        except Exception as exc:
            _logger.warning("LinkedInScraperClient.fetch_posts(%s) failed: %s", profile_id, exc)
            return []

    async def check_profile_delta(
        self,
        profile_id: str,
    ) -> dict[str, Any] | None:
        """Check whether a profile's headline / employer changed since last poll.

        Returns a delta dict if a change is detected, ``None`` otherwise.
        Dict shape: ``{profile_id, field_changed, old_value, new_value, detected_at}``.
        Returns ``None`` when the API key is unset or the request fails.
        """
        if not self._api_key:
            _logger.warning(
                "LinkedInScraperClient: LINKEDIN_SCRAPER_API_KEY unset — skipping check_profile_delta."
            )
            return None

        try:
            resp = await self._http.get(
                "/v2/linkedin/profile",
                params={"linkedin_profile_url": profile_id},
            )
            resp.raise_for_status()
            raw: dict[str, Any] = resp.json()
            return self._detect_delta(profile_id, raw)
        except Exception as exc:
            _logger.warning(
                "LinkedInScraperClient.check_profile_delta(%s) failed: %s", profile_id, exc
            )
            return None

    # ------------------------------------------------------------------
    # Mode A (DISABLED in v1)
    # ------------------------------------------------------------------

    async def fetch_company_followers(self, company_id: str) -> list[dict[str, Any]]:
        """Mode A — follower digest. NO-OP in v1.

        Mode A is disabled — no Contact team consumer for the digest.
        When the Contact team ships, give this method a real body.
        """
        _logger.info(
            "LinkedInScraperClient.fetch_company_followers: Mode A disabled in v1; skipping."
        )
        return []

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_posts(profile_id: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize a Proxycurl-style posts response to our dict shape."""
        posts: list[dict[str, Any]] = []
        for item in raw.get("posts", []) or []:
            posts.append(
                {
                    "profile_id": profile_id,
                    "post_id": str(item.get("urn") or item.get("id") or ""),
                    "text": str(item.get("text") or ""),
                    "posted_at": str(item.get("posted_at") or ""),
                    "url": str(item.get("url") or ""),
                    "is_authored": bool(item.get("is_authored", True)),
                }
            )
        return posts

    @staticmethod
    def _detect_delta(profile_id: str, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Return a delta dict if the profile shows a headline/company change.

        For V1 this is a lightweight heuristic — full change detection requires
        persisting the previous state. TODO: persist last-seen headline in DB.
        """
        # Proxycurl returns current_company / headline in the profile object.
        current_company = str(
            raw.get("experiences", [{}])[0].get("company", "") if raw.get("experiences") else ""
        )
        headline = str(raw.get("headline") or "")
        if not current_company and not headline:
            return None
        # V1 stub: always return None (no baseline to diff against).
        # TODO: store baseline in DB and compare.
        return None
