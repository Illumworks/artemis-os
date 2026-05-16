"""News API fetcher + per-district adapters for the Regional News Scout.

Three async fetch functions:
- fetch_news_articles  — newsapi.org queries for district education news
- fetch_district_board_items — board minutes items filtered to literacy keywords
- fetch_doe_press_items — state DoE RSS items filtered to literacy keywords

All functions return [] gracefully on error or missing config.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.board_minutes.client import fetch_boarddocs
from artemis.scouts.state_doe.sources import fetch_doe_rss

_logger = logging.getLogger(__name__)

_NEWSAPI_URL = "https://newsapi.org/v2/everything"

LITERACY_KEYWORDS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "biliteracy",
    "obc",
    "outcomes-based",
    "curriculum",
    "assessment",
    "tutoring",
    "superintendent",
    "rfp",
    "esser",
    "board vote",
    "board approved",
]


def _contains_literacy_keyword(text: str) -> bool:
    """Return True if *text* contains at least one literacy keyword."""
    lower = text.lower()
    return any(kw in lower for kw in LITERACY_KEYWORDS)


async def fetch_news_articles(
    district_name: str,
    http: ScoutHttpClient,
    *,
    api_key: str = "",
) -> list[dict[str, Any]]:
    """Search newsapi.org for district education news.

    Returns [] gracefully when api_key is empty or on any error.

    Each returned article dict has keys:
    title, description, url, published_at, source_name, content
    """
    if not api_key:
        _logger.debug("fetch_news_articles: NEWS_API_KEY is not set — returning []")
        return []

    query = (
        f'"{district_name}" AND '
        "(literacy OR reading OR dyslexia OR curriculum OR superintendent OR RFP)"
    )
    params: dict[str, str] = {
        "q": query,
        "sortBy": "publishedAt",
        "language": "en",
        "apiKey": api_key,
    }

    try:
        resp = await http.get(_NEWSAPI_URL, params=params)
        if resp.status_code != 200:
            _logger.warning(
                "fetch_news_articles(%s): HTTP %d from newsapi.org",
                district_name,
                resp.status_code,
            )
            return []

        data: dict[str, Any] = resp.json()
        raw_articles: list[dict[str, Any]] = data.get("articles") or []

        articles: list[dict[str, Any]] = []
        for raw in raw_articles:
            title: str = raw.get("title") or ""
            description: str = raw.get("description") or ""
            if not _contains_literacy_keyword(f"{title} {description}"):
                continue
            source: dict[str, Any] = raw.get("source") or {}
            articles.append(
                {
                    "title": title,
                    "description": description,
                    "url": raw.get("url") or "",
                    "published_at": raw.get("publishedAt") or "",
                    "source_name": source.get("name") or "",
                    "content": raw.get("content") or "",
                }
            )
        return articles

    except Exception as exc:
        _logger.warning("fetch_news_articles(%s): error — %s", district_name, exc)
        return []


async def fetch_district_board_items(
    district: dict[str, Any],
    http: ScoutHttpClient,
    pdf_open_fn: Any = None,
) -> list[dict[str, Any]]:
    """Fetch board minutes items for the district, filtered to literacy keywords.

    Delegates to fetch_boarddocs from board_minutes.client.
    Each returned item: {title, text, source_url, date}
    """
    try:
        raw_items = await fetch_boarddocs(district, http, pdf_open_fn=pdf_open_fn)
    except Exception as exc:
        _logger.warning(
            "fetch_district_board_items(%s): error — %s",
            district.get("district_id", "unknown"),
            exc,
        )
        return []

    filtered: list[dict[str, Any]] = []
    for item in raw_items:
        combined = f"{item.get('title', '')} {item.get('text', '')}"
        if _contains_literacy_keyword(combined):
            filtered.append(
                {
                    "title": item.get("title", ""),
                    "text": item.get("text", ""),
                    "source_url": item.get("source_url", ""),
                    "date": item.get("date", ""),
                }
            )
    return filtered


async def fetch_doe_press_items(
    state: str,
    http: ScoutHttpClient,
) -> list[dict[str, Any]]:
    """Fetch state DoE news items, filtered to literacy keywords.

    Delegates to fetch_doe_rss from state_doe.sources.
    Each returned item: {title, summary, link, published}
    """
    try:
        raw_items = await fetch_doe_rss(state, http)
    except Exception as exc:
        _logger.warning("fetch_doe_press_items(%s): error — %s", state, exc)
        return []

    filtered: list[dict[str, Any]] = []
    for item in raw_items:
        combined = f"{item.get('title', '')} {item.get('summary', '')}"
        if _contains_literacy_keyword(combined):
            filtered.append(
                {
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "link": item.get("link", ""),
                    "published": item.get("published", ""),
                }
            )
    return filtered
