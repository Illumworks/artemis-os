"""Cross-source fetch helpers for the Leadership Transition Scout.

Wraps existing board_minutes and state_doe clients to gather raw text, then
filters items to those containing at least one transition keyword.  The news
helper queries newsapi.org when NEWS_API_KEY is set; returns [] gracefully
otherwise.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Coroutine
from typing import Any

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.board_minutes.client import fetch_boarddocs, fetch_granicus
from artemis.scouts.state_doe.sources import fetch_doe_rss

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------

TRANSITION_KEYWORDS: list[str] = [
    "superintendent",
    "interim",
    "principal",
    "search committee",
    "transition",
    "resign",
    "retire",
    "named",
    "appointed",
    "hiring",
    "departure",
    "farewell",
    "welcome",
    "new superintendent",
    "superintendent search",
    "assistant superintendent",
    "curriculum director",
]

# Type aliases for injectable fetcher signatures.
BoarddocsFetcher = Callable[
    [dict[str, Any], ScoutHttpClient],
    Coroutine[Any, Any, list[dict[str, Any]]],
]
DoeFetcher = Callable[
    [str, ScoutHttpClient],
    Coroutine[Any, Any, list[dict[str, Any]]],
]
NewsFetcher = Callable[
    [str, ScoutHttpClient],
    Coroutine[Any, Any, list[dict[str, Any]]],
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _contains_transition_keyword(text: str) -> bool:
    """Return True if *text* (case-insensitive) contains at least one keyword."""
    lower = text.lower()
    return any(kw in lower for kw in TRANSITION_KEYWORDS)


def _filter_to_transition(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only items whose title + text fields contain a transition keyword."""
    filtered: list[dict[str, Any]] = []
    for item in items:
        combined = (
            (item.get("title") or "")
            + " "
            + (item.get("text") or "")
            + " "
            + (item.get("snippet") or "")
            + " "
            + (item.get("summary") or "")
        )
        if _contains_transition_keyword(combined):
            filtered.append(item)
    return filtered


# ---------------------------------------------------------------------------
# Public fetch helpers
# ---------------------------------------------------------------------------


async def gather_board_items(
    district: dict[str, Any],
    http: ScoutHttpClient,
    _boarddocs_fetcher: BoarddocsFetcher | None = None,
) -> list[dict[str, Any]]:
    """Fetch board minutes items from the district, filtered to transition keywords.

    Tries BoardDocs first; falls back to Granicus if ``granicus_url`` is set.
    Returns items tagged with ``source_type="board_minutes"``.
    """
    fetch_bd = _boarddocs_fetcher if _boarddocs_fetcher is not None else fetch_boarddocs

    items: list[dict[str, Any]] = []

    # BoardDocs
    if district.get("boarddocs_url"):
        try:
            raw = await fetch_bd(district, http)
            for item in raw:
                item = dict(item)
                item["source_type"] = "board_minutes"
                items.append(item)
        except Exception as exc:
            _logger.warning(
                "gather_board_items: boarddocs error for %s: %s",
                district.get("district_id"),
                exc,
            )

    # Granicus (production only — injected fetcher covers both for tests)
    if district.get("granicus_url") and _boarddocs_fetcher is None:
        try:
            granicus_raw = await fetch_granicus(district, http)
            for item in granicus_raw:
                item = dict(item)
                item["source_type"] = "board_minutes"
                items.append(item)
        except Exception as exc:
            _logger.warning(
                "gather_board_items: granicus error for %s: %s",
                district.get("district_id"),
                exc,
            )

    return _filter_to_transition(items)


async def gather_doe_items(
    state: str,
    http: ScoutHttpClient,
    _doe_fetcher: DoeFetcher | None = None,
) -> list[dict[str, Any]]:
    """Fetch state DoE items filtered to transition keywords.

    Uses fetch_doe_rss by default.  Items are tagged with
    ``source_type="state_doe"``.
    """
    fetch_doe = _doe_fetcher if _doe_fetcher is not None else fetch_doe_rss

    try:
        raw = await fetch_doe(state, http)
    except Exception as exc:
        _logger.warning("gather_doe_items: error for state %s: %s", state, exc)
        return []

    items: list[dict[str, Any]] = []
    for item in raw:
        item = dict(item)
        item["source_type"] = "state_doe"
        items.append(item)

    return _filter_to_transition(items)


async def gather_news_items(
    district_name: str,
    http: ScoutHttpClient,
    _news_fetcher: NewsFetcher | None = None,
) -> list[dict[str, Any]]:
    """Search newsapi.org for district leadership news.

    Requires NEWS_API_KEY env var.  Returns [] gracefully when the key is
    unset or the request fails.  Items are tagged with
    ``source_type="news_article"``.
    """
    # Allow injection of a custom fetcher for tests.
    if _news_fetcher is not None:
        try:
            return await _news_fetcher(district_name, http)
        except Exception as exc:
            _logger.warning("gather_news_items: injected fetcher error: %s", exc)
            return []

    api_key = os.getenv("NEWS_API_KEY", "")
    if not api_key:
        _logger.debug("gather_news_items: NEWS_API_KEY not set — skipping news fetch.")
        return []

    query = f'"{district_name}" superintendent OR principal OR transition OR resign OR hired'
    try:
        resp = await http.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "apiKey": api_key,
                "pageSize": 20,
                "language": "en",
                "sortBy": "publishedAt",
            },
        )
        data: dict[str, Any] = resp.json()
    except Exception as exc:
        _logger.warning("gather_news_items: request error for %r: %s", district_name, exc)
        return []

    if data.get("status") != "ok":
        _logger.warning(
            "gather_news_items: newsapi returned status=%r for %r",
            data.get("status"),
            district_name,
        )
        return []

    articles: list[dict[str, Any]] = data.get("articles") or []
    items: list[dict[str, Any]] = []
    for article in articles:
        item: dict[str, Any] = {
            "title": article.get("title") or "",
            "text": article.get("content") or article.get("description") or "",
            "snippet": article.get("description") or "",
            "source_url": article.get("url") or "",
            "link": article.get("url") or "",
            "source_type": "news_article",
        }
        items.append(item)

    return _filter_to_transition(items)
