"""OpenGov Procurement adapter for the Procurement Scout.

OpenGov Procurement is a SaaS eProcurement platform used by a growing number
of K-12 districts in our territory.  Confirmed districts:

- **Pinellas County School District (FL)** — slug ``pcsb``
- **Katy ISD (TX, piloting)** — slug ``katyisd`` (verify before enabling)

Public portal: ``https://procurement.opengov.com/portal/<slug>``
Embed iframe:  ``https://procurement.opengov.com/portal/embed/<slug>/project-list``

Access status (2026-06-16)
--------------------------
OpenGov procurement data is **NOT publicly accessible** via programmatic means:

1. **Portal HTML pages** (``procurement.opengov.com/portal/<slug>/*``) return
   HTTP 403 Forbidden for unauthenticated programmatic requests.  Individual
   project pages are Google-indexed because the search crawler has privileged
   access, but they are not reachable by a plain HTTP client without auth.

2. **Developer API** (``api.procurement.opengov.com/gateway/datasets/v1/``)
   requires an **API key** (``x-api-key`` header or HTTP Basic auth with
   email:key).  Source: official API documentation at
   ``https://opengov-procurement.redoc.ly/`` and confirmed in live probing
   (all unauthenticated requests to the gateway return 401 "Unauthorized,
   invalid or missing API token").

3. **Embed portal** (``/portal/embed/<slug>/project-list``) returns 403 to
   non-browser HTTP clients.

To obtain an API key: register a (free) vendor account at
``https://procurement.opengov.com/login``, then generate an API key from
Account Settings → API Keys on the OpenGov Developer Portal at
``https://developer.opengov.com/docs/app-management/api-key``.  The vendor
account registration itself is free — OpenGov charges agencies, not vendors.

Design choice
-------------
The adapter is **structurally complete** — it constructs the correct gateway
request and parses the expected JSON response shape — but until an API key is
provided (via ``OPENGOV_API_KEY`` in the environment), every live call returns
``[]`` gracefully.  The injection hook signature is:

    fetch_opengov_opportunities(entry, http, api_key="...")

Once ``OPENGOV_API_KEY`` is set, no code changes are required.

Return contract (mirrors Bonfire/eMMA/ESBD adapters)::

    {
        "portal_id": str,     # e.g. "opengov_pcsb"
        "state":     str,     # e.g. "FL"
        "rfp_id":    str,     # solicitation number / project ID
        "title":     str,
        "agency":    str,     # district name
        "posted_date": str,   # ISO YYYY-MM-DD or ""
        "due_date":  str,     # ISO YYYY-MM-DD or ""
        "source_url": str,    # https://procurement.opengov.com/portal/<slug>/projects/<id>
        "description": str,
        "scope_text": str,    # "" — not available from listing
        "district_id": str,   # machine district identifier
    }

API response shape (``datasets/v1/solicitations``)::

    {
        "data": [
            {
                "id": "12345",
                "name": "K-12 Literacy Curriculum",
                "number": "RFP-2026-001",
                "status": "open",
                "published_at": "2026-06-01T00:00:00Z",
                "response_due_at": "2026-07-15T17:00:00Z",
                "organization": {
                    "name": "Pinellas County School District",
                    "slug": "pcsb"
                },
                "description": "...",
                ...
            }
        ],
        "meta": {
            "total_count": 42,
            "page": 1,
            "per_page": 25
        }
    }
"""

from __future__ import annotations

import logging
import re
from typing import Any

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

PORTAL_BASE_URL = "https://procurement.opengov.com"

# Datasets API endpoint — requires x-api-key authentication.
# Confirmed gated: all unauthenticated requests return 401.
OPENGOV_API_BASE = "https://api.procurement.opengov.com/gateway/datasets/v1"
OPENGOV_SOLICITATIONS_URL = f"{OPENGOV_API_BASE}/solicitations"

# Browser-ish UA for any portal-page fallback paths.
_OPENGOV_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# District → OpenGov slug registry
#
# Structure per entry:
#   slug      — OpenGov portal slug (e.g. "pcsb")
#   district  — machine identifier (maps to districtId in findings)
#   state     — two-letter state code
#   name      — human-readable district name
#
# Adding a new district: add one entry here. Everything else picks it up.
# ---------------------------------------------------------------------------

OPENGOV_REGISTRY: list[dict[str, str]] = [
    {
        "slug": "pcsb",
        "district": "pinellas_county_schools",
        "state": "FL",
        "name": "Pinellas County School District",
    },
    # Katy ISD is piloting OpenGov — verify slug before enabling.
    # {
    #     "slug": "katyisd",
    #     "district": "katy_isd",
    #     "state": "TX",
    #     "name": "Katy ISD",
    # },
]

# ---------------------------------------------------------------------------
# ISO date parsing helpers
#
# The OpenGov API returns ISO 8601 timestamps (e.g. "2026-06-01T00:00:00Z").
# We extract just the date part (YYYY-MM-DD).
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _parse_opengov_date(raw: str | None) -> str:
    """Extract ISO YYYY-MM-DD from an OpenGov timestamp string.

    Accepts "2026-06-01T17:00:00Z", "2026-06-01", or other ISO-like strings.
    Returns "" on failure or when ``raw`` is ``None``/empty.
    """
    if not raw:
        return ""
    m = _ISO_DATE_RE.search(raw.strip())
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# JSON response parser
# ---------------------------------------------------------------------------


def _parse_opengov_json(
    slug: str,
    state: str,
    district: str,
    district_name: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Parse an OpenGov datasets/v1/solicitations JSON response into posting dicts.

    Parameters
    ----------
    slug:
        OpenGov portal slug (e.g. ``"pcsb"``).
    state:
        Two-letter state code.
    district:
        Machine district identifier.
    district_name:
        Human-readable district name.
    payload:
        Parsed JSON from the solicitations endpoint.

    Returns
    -------
    list[dict[str, Any]]
        One posting dict per solicitation in the response.
        Returns ``[]`` on any parse error.
    """
    data = payload.get("data")
    if not isinstance(data, list):
        _logger.debug("OpenGov %s: JSON payload has no 'data' list — empty or wrong shape", slug)
        return []

    postings: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        project_id = str(item.get("id", "")).strip()
        number = str(item.get("number", "")).strip()
        rfp_id = number or project_id
        if not rfp_id:
            continue

        title = str(item.get("name", "")).strip()
        description = str(item.get("description", title)).strip()
        posted_date = _parse_opengov_date(str(item.get("published_at", "")))
        due_date = _parse_opengov_date(str(item.get("response_due_at", "")))

        source_url = (
            f"{PORTAL_BASE_URL}/portal/{slug}/projects/{project_id}"
            if project_id
            else f"{PORTAL_BASE_URL}/portal/{slug}"
        )

        postings.append(
            {
                "portal_id": f"opengov_{slug}",
                "state": state,
                "rfp_id": rfp_id,
                "title": title,
                "agency": district_name,
                "posted_date": posted_date,
                "due_date": due_date,
                "source_url": source_url,
                "description": description,
                "scope_text": "",
                "district_id": district,
            }
        )

    return postings


# ---------------------------------------------------------------------------
# Public fetcher
# ---------------------------------------------------------------------------


async def fetch_opengov_opportunities(
    entry: dict[str, str],
    http: ScoutHttpClient,
    *,
    api_key: str | None = None,
    page: int = 1,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    """Fetch OpenGov procurement solicitations for one registry entry.

    Parameters
    ----------
    entry:
        A single entry from ``OPENGOV_REGISTRY`` (slug, district, state, name).
    http:
        Shared ``ScoutHttpClient`` instance.
    api_key:
        OpenGov API key (from ``x-api-key`` header).  When ``None``, the
        request returns HTTP 401 and the adapter returns ``[]`` gracefully.
        Set ``OPENGOV_API_KEY`` in the environment and pass it here; the tool
        layer reads it from ``os.getenv("OPENGOV_API_KEY")``.
    page:
        Page number (1-based).
    per_page:
        Records per page (max 100 per OpenGov docs).

    Returns
    -------
    list[dict[str, Any]]
        All parsed solicitation postings for this district.
        Returns ``[]`` on any error — never raises into the caller.

    Notes
    -----
    Access wall: OpenGov requires an API key obtained from a (free) vendor
    account on the OpenGov platform.  Until a key is provided, this function
    always returns ``[]``.  The adapter is wired into the live procurement
    tool so that when a key is eventually provided, it integrates automatically.

    How to unlock: register a free vendor account at
    ``https://procurement.opengov.com/login``, then generate an API key at
    ``https://developer.opengov.com/docs/app-management/api-key``.
    """
    slug = entry["slug"]
    state = entry["state"]
    district = entry["district"]
    district_name = entry["name"]

    if not api_key:
        _logger.info(
            "OpenGov %s: no API key provided (OPENGOV_API_KEY unset) — "
            "returning []; register a free vendor account at "
            "https://procurement.opengov.com/login to unlock",
            slug,
        )
        return []

    headers: dict[str, str] = {
        "User-Agent": _OPENGOV_UA,
        "Accept": "application/json",
        "x-api-key": api_key,
    }

    params: dict[str, Any] = {
        "portal_slug": slug,
        "status": "open",
        "page": page,
        "per_page": per_page,
    }

    try:
        resp = await http.get(OPENGOV_SOLICITATIONS_URL, headers=headers, params=params)
    except Exception as exc:
        _logger.warning("OpenGov %s: HTTP error fetching solicitations: %s", slug, exc)
        return []

    if resp.status_code == 401:
        _logger.warning(
            "OpenGov %s: HTTP 401 Unauthorized — API key is invalid or missing",
            slug,
        )
        return []

    if resp.status_code != 200:
        _logger.warning(
            "OpenGov %s: unexpected HTTP %d from %s",
            slug,
            resp.status_code,
            OPENGOV_SOLICITATIONS_URL,
        )
        return []

    try:
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type.lower():
            _logger.warning(
                "OpenGov %s: response is not JSON (content-type: %s) — "
                "possible auth redirect; returning []",
                slug,
                content_type,
            )
            return []
        payload = resp.json()
    except Exception as exc:
        _logger.warning("OpenGov %s: failed to parse JSON response: %s", slug, exc)
        return []

    if not isinstance(payload, dict):
        _logger.warning(
            "OpenGov %s: unexpected payload type %s",
            slug,
            type(payload).__name__,
        )
        return []

    return _parse_opengov_json(slug, state, district, district_name, payload)


async def fetch_all_opengov_opportunities(
    http: ScoutHttpClient,
    *,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch OpenGov solicitations for all registry entries.

    Per-entry errors are caught and logged; collection continues.

    Parameters
    ----------
    http:
        Shared ``ScoutHttpClient`` instance.
    api_key:
        OpenGov API key.  When ``None``, all entries return ``[]``.

    Returns
    -------
    list[dict[str, Any]]
        Combined posting list across all OpenGov districts.
    """
    all_postings: list[dict[str, Any]] = []
    for entry in OPENGOV_REGISTRY:
        try:
            postings = await fetch_opengov_opportunities(entry, http, api_key=api_key)
        except Exception as exc:
            _logger.warning(
                "OpenGov: unhandled error for slug %s — skipping: %s",
                entry.get("slug", "?"),
                exc,
            )
            continue
        all_postings.extend(postings)
    return all_postings
