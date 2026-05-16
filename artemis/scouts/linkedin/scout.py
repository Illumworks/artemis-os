"""LinkedInObserverScout — D10 real implementation.

Mode B (post → signal) — watches a bounded list of education leader profiles
and emits signals when posts match campaign themes.

Mode A (follower digest) is DISABLED in v1.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from artemis.scouts._linkedin_scraper import LinkedInScraperClient
from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.linkedin.mapping import _week_key, post_to_finding
from artemis.scouts.linkedin.watch_list import _DEFAULT_WATCH_PROFILES

_logger = logging.getLogger(__name__)


class LinkedInObserverScout(BaseScout):
    """Observes LinkedIn posts by district leaders for campaign signals.

    Mode B only. Watches a bounded list of education leader profiles and
    emits a finding whenever a post matches campaign theme keywords.

    Parameters
    ----------
    config:
        Standard scout runtime config.
    watch_profiles:
        Override the default watch list.  Each dict must have keys:
        profile_id, district_id, state, role, name.
    _linkedin_client:
        Inject a pre-built :class:`LinkedInScraperClient` — intended for
        tests only.  Production instances use :meth:`LinkedInScraperClient.from_env`.
    """

    scout_type: ClassVar[str] = "linkedin_observer"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        watch_profiles: list[dict[str, Any]] | None = None,
        _linkedin_client: LinkedInScraperClient | None = None,
    ) -> None:
        super().__init__(config)
        self._watch_profiles: list[dict[str, Any]] = watch_profiles or list(_DEFAULT_WATCH_PROFILES)
        self._linkedin: LinkedInScraperClient = _linkedin_client or LinkedInScraperClient.from_env()

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect LinkedIn post findings across all watch-list profiles.

        Returns an empty list immediately when the LinkedIn scraper API key
        is not set.  Per-profile exceptions are caught and logged; collection
        continues for remaining profiles.

        # Mode A disabled in v1 — see LinkedInScraperClient.fetch_company_followers
        """
        if not self._linkedin._api_key:
            _logger.warning(
                "LinkedInObserverScout: LINKEDIN_SCRAPER_API_KEY is not set — returning empty findings."
            )
            return []

        all_findings: list[dict[str, Any]] = []
        # Final dedup set across all profiles: (profile_id, post_id)
        seen_post_ids: set[tuple[str, str]] = set()

        for profile in self._watch_profiles:
            profile_id: str = profile["profile_id"]
            try:
                posts = await self._linkedin.fetch_posts(profile_id)
            except Exception as exc:
                _logger.warning(
                    "LinkedInObserverScout: failed to fetch posts for %s — skipping: %s",
                    profile_id,
                    exc,
                )
                continue

            # Dedup within this profile by (profile_id, week_key).
            seen_weeks: set[tuple[str, str]] = set()

            for post in posts:
                finding = post_to_finding(post, profile)
                if finding is None:
                    continue

                # Per-profile week-level dedup.
                # Fallback: use post_id when posted_at is absent/unparseable.
                wk = _week_key(post.get("posted_at", ""))
                week_dedup_key = (profile_id, wk) if wk else (profile_id, post.get("post_id", ""))

                if week_dedup_key in seen_weeks:
                    continue
                seen_weeks.add(week_dedup_key)

                # Cross-profile final safety dedup by (profile_id, post_id).
                post_dedup_key = (profile_id, post.get("post_id", ""))
                if post_dedup_key in seen_post_ids:
                    continue
                seen_post_ids.add(post_dedup_key)

                all_findings.append(finding)

        return all_findings
