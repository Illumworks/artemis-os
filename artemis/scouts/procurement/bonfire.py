"""Bonfire (Euna) RSS adapter for the Procurement Scout.

Bonfire exposes a public RSS 2.0 feed per organisation at::

    https://<org-slug>.bonfirehub.com/opportunities/rss

No authentication is required.  Each ``<item>`` in the feed represents one
open procurement opportunity.

This module provides:

- ``BONFIRE_REGISTRY`` — district → Bonfire slug config (config-driven; add
  a new district by adding an entry here).
- ``fetch_bonfire_opportunities(slug, http)`` — fetches + parses the RSS feed
  for one slug and returns a list of posting dicts in the standard
  ``fetch_portal_postings`` shape.
- ``fetch_all_bonfire_opportunities(http)`` — iterates all registry entries
  and returns the combined list.

Return contract mirrors ``portals.py``::

    {
        "portal_id": str,   # e.g. "bonfire_dallasisd"
        "state": str,       # e.g. "TX"
        "rfp_id": str,      # opportunity numeric ID extracted from the URL
        "title": str,
        "agency": str,      # district name
        "posted_date": str, # ISO YYYY-MM-DD or "" if not parseable
        "due_date": str,    # ISO YYYY-MM-DD or "" if embedded date unparseable
        "source_url": str,  # canonical opportunity URL
        "description": str,
        "scope_text": str,  # always "" (not available in the RSS)
        "district_id": str, # machine district identifier for districtId override
    }
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Browser-ish User-Agent — Bonfire/Cloudflare occasionally blocks the default
# python-httpx agent.
# ---------------------------------------------------------------------------

_BONFIRE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# District → Bonfire slug registry
#
# Structure per entry:
#   slug      — the subdomain used in <slug>.bonfirehub.com
#   district  — machine identifier (maps to districtId in findings)
#   state     — two-letter state code
#   name      — human-readable district name
#
# Adding a new district: add one entry here.  The rest of the adapter picks
# it up automatically.
# ---------------------------------------------------------------------------

BONFIRE_REGISTRY: list[dict[str, str]] = [
    {
        "slug": "dallasisd",
        "district": "dallas_isd",
        "state": "TX",
        "name": "Dallas ISD",
    },
    {
        "slug": "fortbendisd",
        "district": "fort_bend_isd",
        "state": "TX",
        "name": "Fort Bend ISD",
    },
    {
        "slug": "cps",
        "district": "chicago_ps",
        "state": "IL",
        "name": "Chicago Public Schools",
    },
    {
        "slug": "u-46",
        "district": "u46",
        "state": "IL",
        "name": "School District U-46 (Elgin)",
    },
    {
        "slug": "austinisd",
        "district": "austin_isd",
        "state": "TX",
        "name": "Austin ISD",
    },
    # Katy ISD: "katyisd" slug does not resolve; marked inactive.
    # Verify via https://katyisd.bonfirehub.com before re-enabling.
    # {
    #     "slug": "katyisd",
    #     "district": "katy_isd",
    #     "state": "TX",
    #     "name": "Katy ISD",
    # },
]

# ---------------------------------------------------------------------------
# RSS URL pattern
# ---------------------------------------------------------------------------

_RSS_URL_TEMPLATE = "https://{slug}.bonfirehub.com/opportunities/rss"

# ---------------------------------------------------------------------------
# Deadline extraction — Bonfire embeds the close date in the description as
# "Project closes MMM DD, YYYY HH:MM AM/PM TZ." or similar.
# ---------------------------------------------------------------------------

_CLOSES_RE = re.compile(
    r"Project closes\s+(\w+ \d{1,2},\s*\d{4})",
    re.IGNORECASE,
)

# Month abbreviation → number for the simple parser.
_MONTH_ABBR: dict[str, str] = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}

_CLOSES_DETAIL_RE = re.compile(
    r"(\w{3})\s+(\d{1,2}),\s*(\d{4})",
    re.IGNORECASE,
)


def _extract_due_date(description: str) -> str:
    """Extract an ISO YYYY-MM-DD due date from a Bonfire description.

    Bonfire embeds the closing date as "Project closes Jun 22, 2026 …".
    Returns "" when no match is found or the date cannot be parsed.
    """
    m = _CLOSES_RE.search(description)
    if not m:
        return ""
    raw = m.group(1)
    dm = _CLOSES_DETAIL_RE.search(raw)
    if not dm:
        return ""
    month_key = dm.group(1).lower()[:3]
    month_num = _MONTH_ABBR.get(month_key)
    if not month_num:
        return ""
    day = dm.group(2).zfill(2)
    year = dm.group(3)
    return f"{year}-{month_num}-{day}"


def _extract_rfp_id(link: str) -> str:
    """Extract the numeric opportunity ID from a Bonfire opportunity URL.

    ``https://dallasisd.bonfirehub.com/opportunities/233109`` → ``"233109"``

    Falls back to the full URL when the pattern is not matched.
    """
    m = re.search(r"/opportunities/(\d+)", link)
    return m.group(1) if m else link


def _rfc2822_to_iso(pub_date: str) -> str:
    """Convert an RFC 2822 pubDate string to ISO YYYY-MM-DD.

    Returns "" on parse failure.
    """
    try:
        dt = parsedate_to_datetime(pub_date.strip())
        return dt.date().isoformat()
    except Exception:
        return ""


def _parse_rss(
    slug: str,
    state: str,
    district: str,
    district_name: str,
    xml_text: str,
) -> list[dict[str, Any]]:
    """Parse a Bonfire RSS 2.0 feed into posting dicts.

    Parameters
    ----------
    slug:
        Bonfire organisation slug (for the portal_id key).
    state:
        Two-letter state code.
    district:
        Machine district identifier (e.g. ``"dallas_isd"``).
    district_name:
        Human-readable district name.
    xml_text:
        Raw XML feed text.

    Returns
    -------
    list[dict[str, Any]]
        One posting dict per ``<item>`` element, in feed order.
        Returns ``[]`` on any parse error.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        _logger.warning("Bonfire %s: RSS parse error: %s", slug, exc)
        return []

    channel = root.find("channel")
    if channel is None:
        _logger.warning("Bonfire %s: no <channel> element in feed", slug)
        return []

    postings: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        link_el = item.find("link")
        pub_el = item.find("pubDate")

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        description = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        link = link_el.text.strip() if link_el is not None and link_el.text else ""
        pub_date_raw = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

        rfp_id = _extract_rfp_id(link)
        posted_date = _rfc2822_to_iso(pub_date_raw)
        due_date = _extract_due_date(description)

        postings.append(
            {
                "portal_id": f"bonfire_{slug}",
                "state": state,
                "rfp_id": rfp_id,
                "title": title,
                "agency": district_name,
                "posted_date": posted_date,
                "due_date": due_date,
                "source_url": link,
                "description": description,
                "scope_text": "",
                # Extra field: used by posting_to_finding for a real districtId
                # rather than the generic STATE_<X> fallback.
                "district_id": district,
            }
        )

    return postings


async def fetch_bonfire_opportunities(
    entry: dict[str, str],
    http: ScoutHttpClient,
) -> list[dict[str, Any]]:
    """Fetch and parse the Bonfire RSS feed for one registry entry.

    Parameters
    ----------
    entry:
        A single entry from ``BONFIRE_REGISTRY`` (slug, district, state, name).
    http:
        Shared ``ScoutHttpClient`` instance.

    Returns
    -------
    list[dict[str, Any]]
        All parsed postings from the feed.  Returns ``[]`` on any error —
        never raises into the caller.
    """
    slug = entry["slug"]
    state = entry["state"]
    district = entry["district"]
    district_name = entry["name"]
    url = _RSS_URL_TEMPLATE.format(slug=slug)

    try:
        resp = await http.get(url, headers={"User-Agent": _BONFIRE_UA})
    except Exception as exc:
        _logger.warning("Bonfire %s: HTTP error fetching %s: %s", slug, url, exc)
        return []

    if resp.status_code != 200:
        _logger.warning(
            "Bonfire %s: unexpected HTTP %d from %s",
            slug,
            resp.status_code,
            url,
        )
        return []

    try:
        xml_text = resp.text
    except Exception as exc:
        _logger.warning("Bonfire %s: failed to read response text: %s", slug, exc)
        return []

    return _parse_rss(slug, state, district, district_name, xml_text)


async def fetch_all_bonfire_opportunities(
    http: ScoutHttpClient,
) -> list[dict[str, Any]]:
    """Fetch Bonfire opportunities for all registry entries.

    Per-slug errors are caught and logged; collection continues.

    Parameters
    ----------
    http:
        Shared ``ScoutHttpClient`` instance.

    Returns
    -------
    list[dict[str, Any]]
        Combined posting list across all Bonfire districts.
    """
    all_postings: list[dict[str, Any]] = []
    for entry in BONFIRE_REGISTRY:
        try:
            postings = await fetch_bonfire_opportunities(entry, http)
        except Exception as exc:
            _logger.warning(
                "Bonfire: unhandled error for slug %s — skipping: %s",
                entry.get("slug", "?"),
                exc,
            )
            continue
        all_postings.extend(postings)
    return all_postings
