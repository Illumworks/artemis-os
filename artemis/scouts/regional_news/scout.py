"""RegionalNewsScout — discovers district board minutes, regional news, and state DoE
announcements across a configurable list of watch districts.

Data sources per district:
1. newsapi.org articles (filtered to literacy keywords)
2. BoardDocs board minutes (via board_minutes.client.fetch_boarddocs)
3. State DoE RSS press items (via state_doe.sources.fetch_doe_rss)

Per-district exceptions are caught and logged; collection continues for remaining
districts. Results are deduplicated by (district_id, source_url).
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import BaseScout, ScoutConfig
from artemis.scouts.regional_news.client import (
    fetch_district_board_items,
    fetch_doe_press_items,
    fetch_news_articles,
)
from artemis.scouts.regional_news.mapping import (
    article_to_finding,
    board_item_to_finding,
    doe_item_to_finding,
)

_logger = logging.getLogger(__name__)

_DEFAULT_WATCH_DISTRICTS: list[dict[str, Any]] = [
    {
        "district_id": "FL_pinellas",
        "state": "FL",
        "district_name": "Pinellas County Schools",
        "boarddocs_url": "https://go.boarddocs.com/fl/pinellas/Board.nsf/Public",
    },
    {
        "district_id": "FL_duval",
        "state": "FL",
        "district_name": "Duval County Public Schools",
        "boarddocs_url": "https://go.boarddocs.com/fl/duval/Board.nsf/Public",
    },
    {
        "district_id": "TX_dallas",
        "state": "TX",
        "district_name": "Dallas ISD",
        "boarddocs_url": "https://go.boarddocs.com/tx/dallasisd/Board.nsf/Public",
    },
    {
        "district_id": "IN_msd_pike",
        "state": "IN",
        "district_name": "MSD of Pike Township",
        "boarddocs_url": "https://go.boarddocs.com/in/msdpike/Board.nsf/Public",
    },
    {
        "district_id": "MD_baltimore_city",
        "state": "MD",
        "district_name": "Baltimore City Public Schools",
        "boarddocs_url": None,
    },
]


class RegionalNewsScout(BaseScout):
    """Discovers district board minutes, regional news, and state DoE announcements.

    Parameters
    ----------
    config:
        Standard scout runtime config.
    watch_districts:
        List of district dicts to monitor.  Defaults to the five Amira-priority
        districts defined in ``_DEFAULT_WATCH_DISTRICTS``.
    news_api_key:
        newsapi.org API key.  Defaults to the ``NEWS_API_KEY`` environment
        variable.  When empty, news article fetching is skipped gracefully.
    query_topics:
        Override the OR-group of topic terms ANDed with each district name in
        the newsapi query (default: literacy + screen-time/AI-in-schools —
        see ``regional_news.client.TOPIC_KEYWORDS``).
    news_domains:
        Optional newsapi ``domains`` filter (e.g.
        ``regional_news.client.NEWS_OUTLET_DOMAINS``) to prioritize the major
        ed-policy outlets. ``None`` (default) leaves news search unrestricted.
    _http_client:
        Inject a pre-built ``ScoutHttpClient`` — intended for tests only.
    _news_fetcher:
        Inject a replacement for ``fetch_news_articles`` — intended for tests only.
    _board_fetcher:
        Inject a replacement for ``fetch_district_board_items`` — intended for tests.
    _doe_fetcher:
        Inject a replacement for ``fetch_doe_press_items`` — intended for tests.
    """

    scout_type: ClassVar[str] = "regional_news_scout"

    def __init__(
        self,
        config: ScoutConfig | None = None,
        *,
        watch_districts: list[dict[str, Any]] | None = None,
        news_api_key: str = "",
        query_topics: list[str] | None = None,
        news_domains: list[str] | None = None,
        _http_client: ScoutHttpClient | None = None,
        _news_fetcher: Any = None,
        _board_fetcher: Any = None,
        _doe_fetcher: Any = None,
    ) -> None:
        super().__init__(config)
        self._watch_districts: list[dict[str, Any]] = (
            watch_districts if watch_districts is not None else list(_DEFAULT_WATCH_DISTRICTS)
        )
        self._news_api_key: str = news_api_key or os.getenv("NEWS_API_KEY", "")
        self._query_topics: list[str] | None = query_topics
        self._news_domains: list[str] | None = news_domains
        self._http: ScoutHttpClient = _http_client or ScoutHttpClient()
        # Injected fetchers (fall back to real implementations)
        self._news_fetcher: Any = _news_fetcher or fetch_news_articles
        self._board_fetcher: Any = _board_fetcher or fetch_district_board_items
        self._doe_fetcher: Any = _doe_fetcher or fetch_doe_press_items

    async def _gather_findings(self) -> list[dict[str, Any]]:
        """Collect findings from all watch districts.

        Per-district exceptions are caught and logged; collection continues.
        Results are deduplicated by (district_id, source_url).
        """
        seen: set[tuple[str, str]] = set()
        findings: list[dict[str, Any]] = []

        for district in self._watch_districts:
            district_id: str = district.get("district_id", "unknown")
            try:
                district_findings = await self._gather_district(district)
                for finding in district_findings:
                    meta: dict[str, Any] = finding.get("metadata") or {}
                    source_url: str = meta.get("source_url") or ""
                    key = (district_id, source_url)
                    if key not in seen:
                        seen.add(key)
                        findings.append(finding)
            except Exception as exc:
                _logger.warning(
                    "RegionalNewsScout: error processing district %s — skipping: %s",
                    district_id,
                    exc,
                )

        return findings

    async def _gather_district(
        self,
        district: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Collect findings for a single district from all three sources."""
        findings: list[dict[str, Any]] = []

        # 1. News articles
        district_name: str = district.get("district_name") or district.get("district_id", "")
        news_kwargs: dict[str, Any] = {"api_key": self._news_api_key}
        # Only forwarded when explicitly set — keeps the call signature
        # backward-compatible with injected test fetchers that don't accept
        # query_topics/domains.
        if self._query_topics is not None:
            news_kwargs["query_topics"] = self._query_topics
        if self._news_domains is not None:
            news_kwargs["domains"] = self._news_domains
        articles = await self._news_fetcher(
            district_name,
            self._http,
            **news_kwargs,
        )
        for article in articles:
            finding = article_to_finding(article, district)
            if finding is not None:
                findings.append(finding)

        # 2. Board minutes items
        board_items = await self._board_fetcher(district, self._http)
        for item in board_items:
            finding = board_item_to_finding(item, district)
            if finding is not None:
                findings.append(finding)

        # 3. State DoE press items
        state: str = district.get("state", "")
        doe_items = await self._doe_fetcher(state, self._http)
        for item in doe_items:
            finding = doe_item_to_finding(item, district)
            if finding is not None:
                findings.append(finding)

        return findings
