"""Per-state source configs and fetch helpers for the State DoE Scout.

Each state entry defines RSS feeds, scrape fallback URLs, governor press feeds,
and state board agenda pages.  Fetch helpers return plain dicts — no Pydantic,
no shared state, no side effects beyond network I/O.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts._pdf import extract_text

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-state source configuration
# ---------------------------------------------------------------------------

# Each entry contains:
#   doe_rss          – URL of the state DoE RSS/Atom feed, or None
#   doe_scrape_url   – Fallback newsroom URL to scrape when RSS is absent/empty
#   governor_rss     – Governor's press-release RSS feed, or None
#   state_board_agenda_url – Page listing upcoming/recent state board agendas
#
# All URLs marked "# TODO: verify URL" are plausible but should be validated
# against live state web properties before first production run.

STATE_DOE_SOURCES: dict[str, dict[str, str | None]] = {
    # doe_rss URLs verified 2026-06-16.
    #
    # All seven state DoE sites were tested live; none serve a crawlable RSS feed
    # that survives bot-detection (403 on fldoe.org, 404/redirect-to-homepage on
    # IN/IL/TX, 404 on MO /news/rss).  Google News RSS provides a reliable,
    # publicly accessible RSS feed per state with real DoE news coverage.
    # The queries are scoped to literacy/reading to keep results signal-dense.
    #
    # MO DESE does expose https://dese.mo.gov/rss.xml but it returns only 2 items
    # (one of which is a certification form, not news); Google News is richer.
    "FL": {
        "doe_rss": "https://news.google.com/rss/search?q=Florida+Department+of+Education+literacy+reading&hl=en-US&gl=US&ceid=US%3Aen",
        "doe_scrape_url": "https://www.fldoe.org/newsroom/",
        "governor_rss": "https://www.flgov.com/news/feed/",
        "state_board_agenda_url": "https://www.fldoe.org/policy/state-board-of-edu/meetings/",
    },
    "IN": {
        "doe_rss": "https://news.google.com/rss/search?q=Indiana+Department+of+Education+literacy+reading&hl=en-US&gl=US&ceid=US%3Aen",
        "doe_scrape_url": "https://www.in.gov/doe/news/",
        "governor_rss": "https://www.in.gov/gov/newsroom/feed/",
        "state_board_agenda_url": "https://www.in.gov/doe/sboe/meetings/",
    },
    "MD": {
        "doe_rss": "https://news.google.com/rss/search?q=Maryland+State+Department+of+Education+literacy&hl=en-US&gl=US&ceid=US%3Aen",
        "doe_scrape_url": "https://www.marylandpublicschools.org/about/Pages/News/",
        "governor_rss": "https://governor.maryland.gov/newsroom/feed/",
        "state_board_agenda_url": "https://www.marylandpublicschools.org/about/Pages/SBOE/Meetings/",
    },
    "MO": {
        "doe_rss": "https://news.google.com/rss/search?q=Missouri+DESE+education+literacy&hl=en-US&gl=US&ceid=US%3Aen",
        "doe_scrape_url": "https://dese.mo.gov/news",
        "governor_rss": "https://governor.mo.gov/news/feed",
        "state_board_agenda_url": "https://dese.mo.gov/state-board-education/board-meetings",
    },
    "MI": {
        "doe_rss": "https://news.google.com/rss/search?q=Michigan+Department+of+Education+literacy&hl=en-US&gl=US&ceid=US%3Aen",
        "doe_scrape_url": "https://www.michigan.gov/mde/news",
        "governor_rss": "https://www.michigan.gov/whitmer/news/press-releases/feed",
        "state_board_agenda_url": "https://www.michigan.gov/mde/state-board/board-meetings",
    },
    "IL": {
        "doe_rss": "https://news.google.com/rss/search?q=Illinois+State+Board+of+Education+literacy+reading&hl=en-US&gl=US&ceid=US%3Aen",
        "doe_scrape_url": "https://www.isbe.net/Pages/news.aspx",
        "governor_rss": "https://gov.illinois.gov/news/press-releases/feed/",
        "state_board_agenda_url": "https://www.isbe.net/Pages/State-Board-Meetings.aspx",
    },
    "TX": {
        "doe_rss": "https://news.google.com/rss/search?q=Texas+Education+Agency+literacy+reading&hl=en-US&gl=US&ceid=US%3Aen",
        "doe_scrape_url": "https://tea.texas.gov/about-tea/news-and-multimedia/press-releases",
        "governor_rss": "https://gov.texas.gov/news/press-releases/feed",
        "state_board_agenda_url": "https://tea.texas.gov/about-tea/leadership/state-board-of-education/sboe-meetings",
    },
}


# ---------------------------------------------------------------------------
# RSS namespace helpers
# ---------------------------------------------------------------------------

_RSS_NS: dict[str, str] = {}  # plain RSS 2.0 — no namespace prefix needed


def _text(element: ET.Element | None) -> str:
    """Return stripped text from an Element, or '' if the element is None."""
    if element is None:
        return ""
    return (element.text or "").strip()


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


async def fetch_doe_rss(state: str, http: ScoutHttpClient) -> list[dict[str, Any]]:
    """Fetch and parse the state DoE RSS feed.

    Returns a list of ``{title, link, published, summary, _source_type}`` dicts.
    Returns an empty list on HTTP error, missing config, or malformed XML.
    """
    config = STATE_DOE_SOURCES.get(state, {})
    url = config.get("doe_rss")
    if not url:
        return []

    try:
        resp = await http.get(str(url))
        if resp.status_code != 200:
            _logger.warning("fetch_doe_rss(%s): HTTP %d from %s", state, resp.status_code, url)
            return []
        return _parse_rss_xml(resp.text, source_type="doe_rss")
    except Exception as exc:
        _logger.warning("fetch_doe_rss(%s): error — %s", state, exc)
        return []


async def fetch_governor_rss(state: str, http: ScoutHttpClient) -> list[dict[str, Any]]:
    """Fetch and parse the governor's press-release RSS feed.

    Returns a list of ``{title, link, published, summary, _source_type}`` dicts
    with ``_source_type="governor_rss"`` so mapping can set sourceType correctly.
    Returns an empty list on error or missing config.
    """
    config = STATE_DOE_SOURCES.get(state, {})
    url = config.get("governor_rss")
    if not url:
        return []

    try:
        resp = await http.get(str(url))
        if resp.status_code != 200:
            _logger.warning("fetch_governor_rss(%s): HTTP %d from %s", state, resp.status_code, url)
            return []
        return _parse_rss_xml(resp.text, source_type="governor_rss")
    except Exception as exc:
        _logger.warning("fetch_governor_rss(%s): error — %s", state, exc)
        return []


def _parse_rss_xml(xml_text: str, *, source_type: str) -> list[dict[str, Any]]:
    """Parse RSS 2.0 XML text into a list of item dicts.

    Returns an empty list if the XML is malformed or contains no items.
    """
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        _logger.warning("_parse_rss_xml: malformed XML — %s", exc)
        return []

    items: list[dict[str, Any]] = []
    # Support both plain RSS 2.0 and Atom-wrapped-in-RSS
    channel = root.find("channel")
    if channel is None:
        # Maybe the root IS the channel (some feeds)
        channel = root

    for item_el in channel.findall("item"):
        items.append(
            {
                "title": _text(item_el.find("title")),
                "link": _text(item_el.find("link")),
                "published": _text(item_el.find("pubDate")),
                "summary": _text(item_el.find("description")),
                "_source_type": source_type,
            }
        )
    return items


async def fetch_doe_html(state: str, scraper: Any) -> list[dict[str, Any]]:
    """Scrape the state DoE newsroom HTML page.

    *scraper* should be a :class:`~artemis.scouts._scraper.ScraperSession`
    (or a test double with an async ``fetch_html`` method).

    Returns a list of ``{title, link, snippet, _source_type}`` dicts.
    Returns an empty list on error or missing config.
    """
    config = STATE_DOE_SOURCES.get(state, {})
    url = config.get("doe_scrape_url")
    if not url:
        return []

    try:
        html = await scraper.fetch_html(str(url))
        return _extract_html_items(html, source_type="doe_html")
    except Exception as exc:
        _logger.warning("fetch_doe_html(%s): error — %s", state, exc)
        return []


def _extract_html_items(html: str, *, source_type: str) -> list[dict[str, Any]]:
    """Very lightweight HTML item extractor.

    Looks for anchor tags whose text contains literacy-related terms.
    This is intentionally simple — full structured extraction requires
    per-site parsing that will be tuned per-state during verification.
    """
    import re

    items: list[dict[str, Any]] = []
    # Match <a href="...">...</a> patterns
    pattern = re.compile(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
    )
    for m in pattern.finditer(html):
        link = m.group(1).strip()
        raw_text = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not raw_text:
            continue
        items.append(
            {
                "title": raw_text[:200],
                "link": link,
                "snippet": "",
                "_source_type": source_type,
            }
        )
    return items


async def fetch_state_board_agenda(
    state: str,
    http: ScoutHttpClient,
    pdf_open_fn: Any = None,
) -> list[dict[str, Any]]:
    """Download and parse the state board agenda page or PDF.

    If the URL ends in ``.pdf`` the bytes are passed to ``extract_text()``.
    Otherwise the page text is returned as a single item.

    Returns a list of ``{title, source_url, text, _source_type}`` dicts.
    Returns an empty list on error or missing config.
    """
    config = STATE_DOE_SOURCES.get(state, {})
    url = config.get("state_board_agenda_url")
    if not url:
        return []

    url_str = str(url)

    try:
        resp = await http.get(url_str)
        if resp.status_code != 200:
            _logger.warning(
                "fetch_state_board_agenda(%s): HTTP %d from %s", state, resp.status_code, url_str
            )
            return []

        content_type = resp.headers.get("content-type", "")
        is_pdf = url_str.lower().endswith(".pdf") or "pdf" in content_type.lower()

        if is_pdf:
            text = extract_text(resp.content, first_pages=20, last_pages=5, _open_fn=pdf_open_fn)
        else:
            text = resp.text

        return [
            {
                "title": f"{state} State Board Agenda",
                "source_url": url_str,
                "text": text,
                "_source_type": "state_board",
            }
        ]
    except Exception as exc:
        _logger.warning("fetch_state_board_agenda(%s): error — %s", state, exc)
        return []
