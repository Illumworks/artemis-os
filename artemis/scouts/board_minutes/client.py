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

BoardDocs API notes:
  BoardDocs is a Lotus Notes / Domino-based SPA.  The public-facing URL
  (``/Board.nsf/Public``) returns a JavaScript shell with no meeting content.
  Real agenda content is served through three undocumented AJAX endpoints:

  1. ``POST /Board.nsf/BD-GetMeetingsList?open&<rand>``
     Body: ``current_committee_id=<id>``
     Returns: JSON list of meeting objects with keys ``unique``, ``name``,
     ``numberdate`` (YYYYMMDD), ``unid``.

  2. ``POST /Board.nsf/BD-GetAgenda?open&<rand>``
     Body: ``id=<meeting_unique>&current_committee_id=<comm_id>``
     Returns: HTML fragment containing ``<li>`` elements, each with a
     ``<span class="title">`` carrying the agenda item title.

  Committee IDs are scraped from the ``/Board.nsf/Public`` SPA shell by
  matching ``committeeid="<id>"`` attributes (only ids that look like
  real governing-board committees are selected — Policies and Leasing
  sub-committees are filtered by name).

  These endpoints are public and require no authentication for boards
  configured as publicly visible.  They were identified by inspecting the
  BoardDocs JavaScript files (``meetings.js``, ``agenda.js``).
"""

from __future__ import annotations

import json
import logging
import random
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

# Committee names to skip — Policies and subsidiary committees are not
# governing-board meeting records we want to scan.
_SKIP_COMMITTEE_NAMES_RE = re.compile(
    r"polic|leasing|corporation|foundation|charter|auxiliary",
    re.IGNORECASE,
)

# Limit how many recent meetings we fetch agenda items for per district.
_MAX_MEETINGS_PER_DISTRICT = 5

# BoardDocs sits behind CloudFront which blocks the default python-httpx UA.
# We identify as a generic browser to avoid 403 Forbidden.  No credentials
# are passed and only publicly-visible board data is requested.
_BOARDDOCS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_BOARDDOCS_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
)

# HTML entity substitutions for common board-document titles.
_HTML_ENTITIES = re.compile(r"&#(\d+);|&amp;|&lt;|&gt;|&quot;|&apos;|&#x([0-9a-fA-F]+);")


def _decode_html_entities(text: str) -> str:
    """Decode common HTML character references to plain text."""

    def _replace(m: re.Match[str]) -> str:
        if m.group(1):
            return chr(int(m.group(1)))
        if m.group(2):
            return chr(int(m.group(2), 16))
        mapping = {
            "&amp;": "&",
            "&lt;": "<",
            "&gt;": ">",
            "&quot;": '"',
            "&apos;": "'",
        }
        return mapping.get(m.group(0), m.group(0))

    return _HTML_ENTITIES.sub(_replace, text)


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


# ---------------------------------------------------------------------------
# BoardDocs structured API helpers
# ---------------------------------------------------------------------------


def _boarddocs_base(url: str) -> str:
    """Return the ``/Board.nsf`` base URL from a BoardDocs public URL.

    ``https://go.boarddocs.com/fl/pcsfl/Board.nsf/Public``
    → ``https://go.boarddocs.com/fl/pcsfl/Board.nsf``
    """
    # Strip any trailing path component after Board.nsf
    match = re.match(r"(https?://[^/]+/[a-z]{2}/[^/]+/Board\.nsf)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    # Fallback: strip last segment
    return url.rstrip("/").rsplit("/", 1)[0]


def _parse_committee_ids(html: str) -> list[tuple[str, str]]:
    """Extract ``(committee_id, label)`` pairs from the BoardDocs SPA shell HTML.

    Returns pairs in document order, skipping Policies / subsidiary committees.
    """
    raw = re.findall(
        r'committeeid="([^"]+)"[^>]+aria-label="([^"]+)"',
        html,
        re.IGNORECASE,
    )
    # Deduplicate while preserving order (each id may appear twice in the HTML).
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for cid, label in raw:
        if cid in seen:
            continue
        seen.add(cid)
        if not _SKIP_COMMITTEE_NAMES_RE.search(label):
            result.append((cid, label))
    return result


def _parse_agenda_items(
    html: str,
    date_str: str,
    meeting_name: str,
    base_url: str,
) -> list[dict[str, Any]]:
    """Parse agenda item ``<li>`` elements from a ``BD-GetAgenda`` HTML fragment.

    Each item carries a ``<span class="title">`` with the human-readable title
    and a ``unique`` attribute used to build a shareable URL.

    Parameters
    ----------
    html:
        The raw HTML fragment returned by ``BD-GetAgenda``.
    date_str:
        ISO date string (YYYY-MM-DD) for the meeting.
    meeting_name:
        Human-readable meeting name (e.g. "Regular School Board Meeting").
    base_url:
        The ``/Board.nsf`` base URL used to construct ``goto`` URLs.

    Returns
    -------
    list[dict]
        One dict per agenda item, in agenda order.  Empty if no items found.
    """
    items: list[dict[str, Any]] = []

    # Match <li> agenda items — they carry unique="..." and contain a
    # <span class="title"> child.
    li_pattern = re.compile(
        r'<li[^>]+class="[^"]*\bitem\b[^"]*"[^>]+unique="([^"]+)"[^>]*>'
        r".*?"
        r'<span class="title">([^<]+)</span>',
        re.IGNORECASE | re.DOTALL,
    )
    for m in li_pattern.finditer(html):
        item_unique = m.group(1)
        raw_title = _decode_html_entities(m.group(2).strip())
        # Build a stable permalink for this agenda item.
        goto_url = f"{base_url}/goto?open&id={item_unique}"
        items.append(
            {
                "title": f"{meeting_name} — {raw_title}",
                "date": date_str,
                "source_url": goto_url,
                "text": raw_title,
                "speaker_attribution": None,
            }
        )
    return items


def _numberdate_to_iso(numberdate: str) -> str:
    """Convert BoardDocs ``numberdate`` (YYYYMMDD) to ISO ``YYYY-MM-DD``."""
    nd = numberdate.strip()
    if len(nd) == 8 and nd.isdigit():
        return f"{nd[:4]}-{nd[4:6]}-{nd[6:]}"
    return nd


# ---------------------------------------------------------------------------
# BoardDocs deep-fetch via structured API
# ---------------------------------------------------------------------------


async def _fetch_boarddocs_api_items(
    base_url: str,
    committee_id: str,
    http: ScoutHttpClient,
) -> list[dict[str, Any]]:
    """Fetch agenda items via the BoardDocs AJAX API for one committee.

    Makes at most ``1 + _MAX_MEETINGS_PER_DISTRICT`` HTTP requests:
    one for the meeting list, then one per recent meeting for its agenda.

    Parameters
    ----------
    base_url:
        The ``/Board.nsf`` base URL (e.g.
        ``https://go.boarddocs.com/fl/pcsfl/Board.nsf``).
    committee_id:
        The BoardDocs committee identifier extracted from the SPA HTML.
    http:
        Shared ``ScoutHttpClient``.

    Returns
    -------
    list[dict]
        Agenda items across all fetched meetings, empty on failure.
    """
    rand = random.random()  # noqa: S311 — not security-sensitive

    # ------------------------------------------------------------------
    # Step 1: get meeting list JSON
    # ------------------------------------------------------------------
    meetings_url = f"{base_url}/BD-GetMeetingsList?open&{rand}"
    try:
        resp = await http.post(
            meetings_url,
            content=f"current_committee_id={committee_id}",
            headers={
                "User-Agent": _BOARDDOCS_UA,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": f"{base_url}/Public",
                "Origin": "https://go.boarddocs.com",
            },
        )
    except Exception as exc:
        _logger.warning("BoardDocs BD-GetMeetingsList %s failed: %s", meetings_url, exc)
        return []

    if resp.status_code != 200 or not resp.text:
        _logger.debug(
            "BoardDocs BD-GetMeetingsList %s returned %d / empty",
            meetings_url,
            resp.status_code,
        )
        return []

    try:
        meetings_json: list[dict[str, Any]] = json.loads(resp.text)
    except json.JSONDecodeError:
        _logger.debug("BoardDocs BD-GetMeetingsList response is not JSON: %r", resp.text[:200])
        return []

    if not isinstance(meetings_json, list) or not meetings_json:
        return []

    # ------------------------------------------------------------------
    # Step 2: fetch agenda HTML for each recent meeting
    # ------------------------------------------------------------------
    items: list[dict[str, Any]] = []
    recent = [m for m in meetings_json if isinstance(m, dict) and m.get("unique")]
    for meeting in recent[:_MAX_MEETINGS_PER_DISTRICT]:
        meeting_unique: str = meeting["unique"]
        meeting_name: str = meeting.get("name", "Board Meeting")
        date_iso = _numberdate_to_iso(meeting.get("numberdate", ""))

        rand2 = random.random()  # noqa: S311
        agenda_url = f"{base_url}/BD-GetAgenda?open&{rand2}"
        try:
            agenda_resp = await http.post(
                agenda_url,
                data={"id": meeting_unique, "current_committee_id": committee_id},
                headers={
                    "User-Agent": _BOARDDOCS_UA,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "text/html, */*; q=0.01",
                    "Referer": f"{base_url}/Public",
                    "Origin": "https://go.boarddocs.com",
                },
            )
        except Exception as exc:
            _logger.warning(
                "BoardDocs BD-GetAgenda %s (meeting %s) failed: %s",
                agenda_url,
                meeting_unique,
                exc,
            )
            continue

        agenda_html = agenda_resp.text
        if not agenda_html or "Error:" in agenda_html or "No Access" in agenda_html:
            _logger.debug(
                "BoardDocs BD-GetAgenda meeting %s: empty or access-denied response",
                meeting_unique,
            )
            continue

        agenda_items = _parse_agenda_items(agenda_html, date_iso, meeting_name, base_url)
        _logger.debug(
            "BoardDocs meeting %s (%s): %d agenda items parsed",
            meeting_unique,
            date_iso,
            len(agenda_items),
        )
        items.extend(agenda_items)

    return items


async def fetch_boarddocs(
    district: dict[str, Any],
    http: ScoutHttpClient,
    pdf_open_fn: Any = None,
) -> list[dict[str, Any]]:
    """Scrape the BoardDocs public agenda/minutes page for a district.

    Attempts the structured BoardDocs AJAX API first (committee list →
    meeting list → agenda items).  Falls back to scanning the SPA HTML
    for PDF links if the API path yields nothing.

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

    # ------------------------------------------------------------------
    # Fetch the SPA shell — needed both for committee-id scraping and
    # as a fallback HTML/PDF scan path.
    #
    # BoardDocs sits behind CloudFront; the default python-httpx UA is
    # blocked with HTTP 403.  We send a generic browser UA and Accept
    # header to get the SPA HTML reliably.
    # ------------------------------------------------------------------
    try:
        resp = await http.get(
            url,
            headers={
                "User-Agent": _BOARDDOCS_UA,
                "Accept": _BOARDDOCS_ACCEPT,
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        html = resp.text
    except Exception as exc:
        _logger.warning(
            "BoardMinutesScout: BoardDocs GET %s failed: %s",
            url,
            exc,
        )
        return []

    # ------------------------------------------------------------------
    # Primary path: BoardDocs AJAX API
    # ------------------------------------------------------------------
    base_url = _boarddocs_base(url)
    committees = _parse_committee_ids(html)
    if committees:
        all_items: list[dict[str, Any]] = []
        for comm_id, comm_label in committees:
            _logger.debug(
                "BoardDocs %s: fetching agenda for committee %s (%s)",
                district.get("district_id"),
                comm_id,
                comm_label,
            )
            comm_items = await _fetch_boarddocs_api_items(base_url, comm_id, http)
            all_items.extend(comm_items)
        if all_items:
            _logger.info(
                "BoardDocs %s: API path returned %d agenda items across %d committee(s)",
                district.get("district_id"),
                len(all_items),
                len(committees),
            )
            return all_items
        _logger.debug(
            "BoardDocs %s: API path returned 0 items; falling back to HTML/PDF scan",
            district.get("district_id"),
        )

    # ------------------------------------------------------------------
    # Fallback path: scan the SPA HTML for PDF links (older boards or
    # boards without publicly-visible agendas).
    # ------------------------------------------------------------------
    items: list[dict[str, Any]] = []
    links = _parse_links(html, url)
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
