"""BoardDocs, Granicus, and district-site fetchers for the Board Minutes Scout.

All three fetchers return a list of meeting-item dicts with the shape::

    {
        "title": str,
        "date": str,
        "source_url": str,
        "text": str,
        "speaker_attribution": str | None,
    }

Implementation notes:
- Static HTML pages + PDFs only — no Playwright needed for these sources.
- ScoutHttpClient handles rate-limiting and retry automatically.
- PDF extraction is delegated to artemis.scouts._pdf.extract_text().
- Per-item errors are caught and logged; processing continues.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts._pdf import extract_text

_logger = logging.getLogger(__name__)

# Pattern to pick up speaker attribution names from PDF text.
_SPEAKER_RE = re.compile(
    r"(?:Superintendent|Supt\.|Board Member|Dr\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*"
)

# Heuristic to detect a PDF link from an <a href=...> value.
_PDF_RE = re.compile(r"\.pdf", re.IGNORECASE)


def _extract_speaker(text: str, date: str) -> str | None:
    """Return a formatted speaker attribution string, or None if not found."""
    matches = _SPEAKER_RE.findall(text)
    if not matches:
        return None
    name = matches[0]
    return f"{name}, {date} board meeting"


def _parse_links(html: str, base_url: str) -> list[str]:
    """Extract href values from <a> tags in *html*, resolved against *base_url*.

    Only relative-URL normalisation is performed — no full URL-join library
    required for the simple patterns used by BoardDocs and Granicus.
    """
    hrefs: list[str] = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    resolved: list[str] = []
    for href in hrefs:
        if href.startswith("http://") or href.startswith("https://"):
            resolved.append(href)
        elif href.startswith("/"):
            # Derive scheme + host from base_url.
            match = re.match(r"(https?://[^/]+)", base_url)
            if match:
                resolved.append(match.group(1) + href)
        # Ignore anchors, javascript:, mailto: etc.
    return resolved


async def fetch_boarddocs(
    district: dict[str, Any],
    http: ScoutHttpClient,
    pdf_open_fn: Any = None,
) -> list[dict[str, Any]]:
    """Scrape the BoardDocs public agenda/minutes page for a district.

    Parameters
    ----------
    district:
        District config dict from the watch list.
    http:
        Shared ScoutHttpClient instance.
    pdf_open_fn:
        Injected PDF-open function for tests (forwarded to extract_text).

    Returns
    -------
    list[dict]
        Meeting items, each with title / date / source_url / text /
        speaker_attribution.  Returns ``[]`` on any error.
    """
    url: str | None = district.get("boarddocs_url")
    if not url:
        return []

    try:
        resp = await http.get(url)
        html = resp.text
    except Exception as exc:
        _logger.warning(
            "BoardMinutesScout: BoardDocs GET %s failed: %s",
            url,
            exc,
        )
        return []

    items: list[dict[str, Any]] = []
    links = _parse_links(html, url)

    # Collect PDF links from the page (meeting minutes / agendas).
    pdf_links = [lnk for lnk in links if _PDF_RE.search(lnk)]

    for pdf_url in pdf_links[:10]:  # Limit to first 10 PDF links per district.
        try:
            pdf_resp = await http.get(pdf_url)
            pdf_bytes = pdf_resp.content
            text = extract_text(pdf_bytes, first_pages=20, last_pages=5, _open_fn=pdf_open_fn)
            speaker = _extract_speaker(text, "")
            items.append(
                {
                    "title": _title_from_url(pdf_url),
                    "date": "",
                    "source_url": pdf_url,
                    "text": text,
                    "speaker_attribution": speaker,
                }
            )
        except Exception as exc:
            _logger.warning(
                "BoardMinutesScout: failed to extract PDF %s: %s",
                pdf_url,
                exc,
            )

    # If no PDFs found, emit a single item for the board page itself so the
    # mapper has a chance to find relevant text in the HTML.
    if not items and html.strip():
        items.append(
            {
                "title": f"BoardDocs — {district.get('district_id', '')}",
                "date": "",
                "source_url": url,
                "text": html,
                "speaker_attribution": _extract_speaker(html, ""),
            }
        )

    return items


async def fetch_granicus(
    district: dict[str, Any],
    http: ScoutHttpClient,
    pdf_open_fn: Any = None,
) -> list[dict[str, Any]]:
    """Fetch the Granicus agenda archive page for a district.

    Parameters
    ----------
    district:
        District config dict from the watch list.
    http:
        Shared ScoutHttpClient instance.
    pdf_open_fn:
        Injected PDF-open function for tests.

    Returns
    -------
    list[dict]
        Meeting items.  Returns ``[]`` on any error.
    """
    url: str | None = district.get("granicus_url")
    if not url:
        return []

    try:
        resp = await http.get(url)
        html = resp.text
    except Exception as exc:
        _logger.warning(
            "BoardMinutesScout: Granicus GET %s failed: %s",
            url,
            exc,
        )
        return []

    items: list[dict[str, Any]] = []
    links = _parse_links(html, url)
    pdf_links = [lnk for lnk in links if _PDF_RE.search(lnk)]

    for pdf_url in pdf_links[:10]:
        try:
            pdf_resp = await http.get(pdf_url)
            pdf_bytes = pdf_resp.content
            text = extract_text(pdf_bytes, first_pages=20, last_pages=5, _open_fn=pdf_open_fn)
            speaker = _extract_speaker(text, "")
            items.append(
                {
                    "title": _title_from_url(pdf_url),
                    "date": "",
                    "source_url": pdf_url,
                    "text": text,
                    "speaker_attribution": speaker,
                }
            )
        except Exception as exc:
            _logger.warning(
                "BoardMinutesScout: failed to extract Granicus PDF %s: %s",
                pdf_url,
                exc,
            )

    if not items and html.strip():
        items.append(
            {
                "title": f"Granicus — {district.get('district_id', '')}",
                "date": "",
                "source_url": url,
                "text": html,
                "speaker_attribution": _extract_speaker(html, ""),
            }
        )

    return items


async def fetch_district_site(
    district: dict[str, Any],
    http: ScoutHttpClient,
    pdf_open_fn: Any = None,
) -> list[dict[str, Any]]:
    """Scrape the district's own website agenda/minutes page.

    Parameters
    ----------
    district:
        District config dict from the watch list.
    http:
        Shared ScoutHttpClient instance.
    pdf_open_fn:
        Injected PDF-open function for tests.

    Returns
    -------
    list[dict]
        Meeting items.  Returns ``[]`` on any error.
    """
    url: str | None = district.get("district_site_url")
    if not url:
        return []

    try:
        resp = await http.get(url)
        html = resp.text
    except Exception as exc:
        _logger.warning(
            "BoardMinutesScout: district site GET %s failed: %s",
            url,
            exc,
        )
        return []

    items: list[dict[str, Any]] = []
    links = _parse_links(html, url)
    pdf_links = [lnk for lnk in links if _PDF_RE.search(lnk)]

    for pdf_url in pdf_links[:10]:
        try:
            pdf_resp = await http.get(pdf_url)
            pdf_bytes = pdf_resp.content
            text = extract_text(pdf_bytes, first_pages=20, last_pages=5, _open_fn=pdf_open_fn)
            speaker = _extract_speaker(text, "")
            items.append(
                {
                    "title": _title_from_url(pdf_url),
                    "date": "",
                    "source_url": pdf_url,
                    "text": text,
                    "speaker_attribution": speaker,
                }
            )
        except Exception as exc:
            _logger.warning(
                "BoardMinutesScout: failed to extract district site PDF %s: %s",
                pdf_url,
                exc,
            )

    if not items and html.strip():
        items.append(
            {
                "title": f"District site — {district.get('district_id', '')}",
                "date": "",
                "source_url": url,
                "text": html,
                "speaker_attribution": _extract_speaker(html, ""),
            }
        )

    return items


def _title_from_url(url: str) -> str:
    """Derive a human-readable title from the last path segment of a URL."""
    path = url.rstrip("/").split("/")[-1]
    # Remove extension and decode percent-encoding.
    name = re.sub(r"\.\w+$", "", path)
    name = re.sub(r"%20", " ", name)
    name = re.sub(r"[_-]", " ", name)
    return name.strip() or url
