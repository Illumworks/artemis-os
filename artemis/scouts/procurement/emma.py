"""eMMA (eMaryland Marketplace Advantage) adapter for the Procurement Scout.

eMMA at ``emma.maryland.gov`` is Maryland's mandatory state e-procurement portal.
Maryland law requires ALL public agencies — including every K-12 district — to post
solicitations here.  One adapter covers Montgomery County PS, Prince George's PS,
Baltimore County, and every other MD district.

Access status (2026-06-16)
--------------------------
eMMA runs iValua V6, a JavaScript SPA that gates every entry path behind a Google
reCAPTCHA Enterprise "browser check" page.  Any request from a non-JS HTTP client
is redirected to ``/page.aspx/en/bas/browser_check``, which requires reCAPTCHA
resolution before the session is admitted.

The public search URL is::

    https://emma.maryland.gov/page.aspx/en/rfp/request_browse_public

but it is **not reachable** without a valid reCAPTCHA-solved session cookie.

Design choice
-------------
The adapter is **structurally complete** — it constructs the correct search
request and parses the expected HTML response — but until the reCAPTCHA wall is
bypassed (browser automation, a registered vendor session, or a future public API
from DGS Maryland), every live call returns ``[]`` gracefully.

The return contract is identical to the Bonfire adapter::

    {
        "portal_id": "emma_MD",
        "state": "MD",
        "rfp_id": str,
        "title": str,
        "agency": str,
        "posted_date": str,      # ISO YYYY-MM-DD or ""
        "due_date": str,         # ISO YYYY-MM-DD or ""
        "source_url": str,
        "description": str,
        "scope_text": str,       # "" — not available from listing page
        "district_id": str,      # "" — MD-wide; no per-district identifier
    }

Future path
-----------
When reCAPTCHA is solved (e.g. via Playwright cookie injection):
1. POST to ``EMMA_SESSION_URL`` to obtain a valid ASP.NET_SessionId.
2. Pass the session cookie to subsequent GET requests.
3. The HTML result table parser below is designed to handle iValua's rendered output.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

PORTAL_ID = "emma_MD"
STATE = "MD"

# Base URL for the public solicitations search — requires a valid iValua session.
EMMA_BASE_URL = "https://emma.maryland.gov"
EMMA_SEARCH_URL = f"{EMMA_BASE_URL}/page.aspx/en/rfp/request_browse_public"
EMMA_BROWSER_CHECK_URL = f"{EMMA_BASE_URL}/page.aspx/en/bas/browser_check"

# iValua session cookie name
_SESSION_COOKIE_NAME = "ASP.NET_SessionId"

# Browser UA — required; iValua bot-detects python-httpx default.
_EMMA_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Date normalisation helpers
# ---------------------------------------------------------------------------

_MONTH_ABBR: dict[str, str] = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}

# MM/DD/YYYY — typical iValua date format
_SLASH_DATE_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})",
)

# "January 15, 2026" or "Jan 15, 2026"
_LONG_DATE_RE = re.compile(
    r"(\w+)\s+(\d{1,2}),\s*(\d{4})",
    re.IGNORECASE,
)


def _parse_emma_date(raw: str) -> str:
    """Normalise an eMMA date string to ISO YYYY-MM-DD.

    Accepts MM/DD/YYYY or "Month DD, YYYY".  Returns "" on failure.
    """
    raw = raw.strip()
    m = _SLASH_DATE_RE.search(raw)
    if m:
        month, day, year = m.group(1), m.group(2), m.group(3)
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

    m2 = _LONG_DATE_RE.search(raw)
    if m2:
        month_word = m2.group(1).lower()
        month_num = _MONTH_ABBR.get(month_word)
        if month_num:
            day = m2.group(2).zfill(2)
            year = m2.group(3)
            return f"{year}-{month_num}-{day}"

    return ""


# ---------------------------------------------------------------------------
# HTML parser — iValua public solicitation table
#
# iValua renders solicitations as an HTML table.  Each row typically has:
#   Col 0: Solicitation number / RFP ID
#   Col 1: Title / description
#   Col 2: Agency / organisation
#   Col 3: Category
#   Col 4: Open date (posted_date)
#   Col 5: Close date (due_date)
#   Col 6: Status
# Column order may vary — we parse by regex heuristic when the header is absent.
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities from a fragment."""
    cleaned = _TAG_RE.sub(" ", text)
    decoded = html.unescape(cleaned)
    return _WHITESPACE_RE.sub(" ", decoded).strip()


def _extract_href(cell_html: str) -> str:
    """Extract the first href from a cell's HTML fragment."""
    m = re.search(r'href=["\']([^"\']+)["\']', cell_html, re.IGNORECASE)
    if not m:
        return ""
    href = m.group(1)
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return EMMA_BASE_URL + href
    return href


def _parse_emma_html(html_text: str) -> list[dict[str, Any]]:
    """Parse an eMMA solicitations listing page into posting dicts.

    Parameters
    ----------
    html_text:
        Full HTML of the eMMA public solicitations page, already rendered (i.e.
        obtained from a valid session — not the reCAPTCHA page).

    Returns
    -------
    list[dict[str, Any]]
        One posting per table row.  Returns ``[]`` on parse failure or when
        the page is a login/captcha redirect.
    """
    # Detect redirect to browser_check or login — no data available.
    if "browser_check" in html_text or "usr/login" in html_text:
        _logger.info(
            "eMMA: response is a browser_check / login redirect — "
            "reCAPTCHA session required; returning []"
        )
        return []

    # Find all <tr> elements.
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.DOTALL | re.IGNORECASE)
    if not rows:
        _logger.debug("eMMA: no <tr> elements found in HTML — page likely empty or JS-only")
        return []

    postings: list[dict[str, Any]] = []

    for row_html in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL | re.IGNORECASE)
        if len(cells) < 2:
            continue  # header or spacer row

        # Best-effort column extraction — iValua tables vary slightly by config.
        rfp_id = _strip_tags(cells[0])
        title_cell = cells[1] if len(cells) > 1 else cells[0]
        title = _strip_tags(title_cell)
        agency = _strip_tags(cells[2]) if len(cells) > 2 else ""
        posted_date = _parse_emma_date(_strip_tags(cells[4])) if len(cells) > 4 else ""
        due_date = _parse_emma_date(_strip_tags(cells[5])) if len(cells) > 5 else ""

        # Try to extract a detail link from either the RFP ID cell or title cell.
        source_url = _extract_href(cells[0]) or _extract_href(title_cell)

        if not rfp_id or not title:
            continue

        postings.append(
            {
                "portal_id": PORTAL_ID,
                "state": STATE,
                "rfp_id": rfp_id,
                "title": title,
                "agency": agency,
                "posted_date": posted_date,
                "due_date": due_date,
                "source_url": source_url,
                "description": title,  # listing page has no separate description
                "scope_text": "",
                "district_id": "",  # eMMA is state-wide; no per-district id
            }
        )

    return postings


# ---------------------------------------------------------------------------
# Public fetcher
# ---------------------------------------------------------------------------


async def fetch_emma_opportunities(
    http: ScoutHttpClient,
    *,
    session_cookie: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch eMMA solicitations for all Maryland public agencies.

    Parameters
    ----------
    http:
        Shared ``ScoutHttpClient`` instance.
    session_cookie:
        Optional ``ASP.NET_SessionId`` value obtained from a reCAPTCHA-solved
        browser session.  When ``None``, the request will be redirected to the
        browser check page and ``[]`` will be returned.

    Returns
    -------
    list[dict[str, Any]]
        Parsed solicitation postings in the standard tool-shape format.
        Returns ``[]`` on any error or when no valid session is available.

    Notes
    -----
    Access wall: eMMA requires a Google reCAPTCHA Enterprise-solved session.
    Until that is resolved (browser automation or a vendor account), this
    function always returns ``[]``.  It is wired into the live procurement
    tool so that when access is eventually unlocked, it integrates automatically.
    """
    headers: dict[str, str] = {
        "User-Agent": _EMMA_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    if session_cookie:
        headers["Cookie"] = f"{_SESSION_COOKIE_NAME}={session_cookie}"

    try:
        resp = await http.get(EMMA_SEARCH_URL, headers=headers)
    except Exception as exc:
        _logger.warning("eMMA: HTTP error fetching %s: %s", EMMA_SEARCH_URL, exc)
        return []

    if resp.status_code != 200:
        _logger.warning("eMMA: unexpected HTTP %d from %s", resp.status_code, EMMA_SEARCH_URL)
        return []

    try:
        html_text = resp.text
    except Exception as exc:
        _logger.warning("eMMA: failed to read response text: %s", exc)
        return []

    return _parse_emma_html(html_text)
