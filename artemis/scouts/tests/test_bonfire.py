"""Tests for the Bonfire RSS adapter.

All HTTP is mocked — no live network calls are made.

Coverage:
- bonfire.py: _extract_due_date, _extract_rfp_id, _rfc2822_to_iso, _parse_rss
- bonfire.py: fetch_bonfire_opportunities (HTTP error paths + success)
- bonfire.py: fetch_all_bonfire_opportunities (multi-entry, error isolation)
- bonfire.py: BONFIRE_REGISTRY structure
- portals.py: PORTAL_REGISTRY Bonfire entries + rss dispatch via fetch_portal_postings
- mapping.py: district_id override for Bonfire postings
"""

from __future__ import annotations

import pathlib
import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.procurement.bonfire import (
    BONFIRE_REGISTRY,
    _extract_due_date,
    _extract_rfp_id,
    _parse_rss,
    _rfc2822_to_iso,
    fetch_all_bonfire_opportunities,
    fetch_bonfire_opportunities,
)
from artemis.scouts.procurement.mapping import posting_to_finding
from artemis.scouts.procurement.portals import PORTAL_REGISTRY, fetch_portal_postings

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
_SAMPLE_XML = (_FIXTURES_DIR / "bonfire_sample.xml").read_text()

_DALLAS_ENTRY = next(e for e in BONFIRE_REGISTRY if e["slug"] == "dallasisd")

_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Open Public Opportunities</title>
  </channel>
</rss>
"""

_MALFORMED_XML = "this is not xml <<<<"


def _make_http_mock(
    response_text: str = "",
    status_code: int = 200,
    raise_error: Exception | None = None,
) -> ScoutHttpClient:
    """Return a ScoutHttpClient with a mocked inner httpx.AsyncClient."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = response_text

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    if raise_error is not None:
        inner.request.side_effect = raise_error
    else:
        inner.request.return_value = mock_resp

    return ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))


# ---------------------------------------------------------------------------
# _extract_due_date
# ---------------------------------------------------------------------------


def test_extract_due_date_standard_format() -> None:
    """Standard 'Project closes MMM DD, YYYY' extracts an ISO date."""
    desc = "Some text. Project closes Jul 15, 2026 2:00 PM CDT."
    assert _extract_due_date(desc) == "2026-07-15"


def test_extract_due_date_case_insensitive() -> None:
    """Match is case-insensitive."""
    desc = "project closes aug 01, 2026 10:00 AM CDT."
    assert _extract_due_date(desc) == "2026-08-01"


def test_extract_due_date_no_match_returns_empty() -> None:
    """Description with no closing date returns ''."""
    assert _extract_due_date("This RFP has no closing date information.") == ""


def test_extract_due_date_unknown_month_returns_empty() -> None:
    """An unrecognised month abbreviation returns ''."""
    assert _extract_due_date("Project closes Xyz 01, 2026 10:00 AM CDT.") == ""


def test_extract_due_date_single_digit_day() -> None:
    """Single-digit day is zero-padded correctly."""
    assert _extract_due_date("Project closes Jun 5, 2026 2:00 PM CDT.") == "2026-06-05"


def test_extract_due_date_all_months() -> None:
    """All twelve month abbreviations are mapped correctly."""
    months = [
        ("Jan", "01"),
        ("Feb", "02"),
        ("Mar", "03"),
        ("Apr", "04"),
        ("May", "05"),
        ("Jun", "06"),
        ("Jul", "07"),
        ("Aug", "08"),
        ("Sep", "09"),
        ("Oct", "10"),
        ("Nov", "11"),
        ("Dec", "12"),
    ]
    for abbr, num in months:
        desc = f"Project closes {abbr} 10, 2026 2:00 PM CDT."
        assert _extract_due_date(desc) == f"2026-{num}-10", f"Failed for month: {abbr}"


# ---------------------------------------------------------------------------
# _extract_rfp_id
# ---------------------------------------------------------------------------


def test_extract_rfp_id_standard_url() -> None:
    """Numeric ID is extracted from the URL path."""
    assert _extract_rfp_id("https://dallasisd.bonfirehub.com/opportunities/233109") == "233109"


def test_extract_rfp_id_fallback_to_full_url() -> None:
    """When no /opportunities/<id> pattern, returns the full URL."""
    url = "https://dallasisd.bonfirehub.com/portal/"
    assert _extract_rfp_id(url) == url


def test_extract_rfp_id_empty_string() -> None:
    """Empty string returns empty string."""
    assert _extract_rfp_id("") == ""


# ---------------------------------------------------------------------------
# _rfc2822_to_iso
# ---------------------------------------------------------------------------


def test_rfc2822_to_iso_standard() -> None:
    """Standard RFC 2822 date converts to ISO."""
    assert _rfc2822_to_iso("Mon, 09 Jun 2026 08:00:00 -0500") == "2026-06-09"


def test_rfc2822_to_iso_with_leading_space() -> None:
    """Leading/trailing whitespace is stripped before parsing."""
    assert _rfc2822_to_iso(" Sun, 07 Jun 2026 08:00:00 -0500") == "2026-06-07"


def test_rfc2822_to_iso_invalid_returns_empty() -> None:
    """Unparseable string returns ''."""
    assert _rfc2822_to_iso("not a date") == ""


def test_rfc2822_to_iso_empty_returns_empty() -> None:
    """Empty string returns ''."""
    assert _rfc2822_to_iso("") == ""


# ---------------------------------------------------------------------------
# _parse_rss
# ---------------------------------------------------------------------------


def test_parse_rss_returns_all_items() -> None:
    """Sample XML with 3 items → 3 posting dicts."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    assert len(postings) == 3


def test_parse_rss_posting_keys() -> None:
    """Each posting dict has the required keys."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    required_keys = {
        "portal_id",
        "state",
        "rfp_id",
        "title",
        "agency",
        "posted_date",
        "due_date",
        "source_url",
        "description",
        "scope_text",
        "district_id",
    }
    for p in postings:
        assert required_keys.issubset(p.keys()), f"Missing keys: {required_keys - p.keys()}"


def test_parse_rss_portal_id_format() -> None:
    """portal_id is 'bonfire_<slug>'."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    for p in postings:
        assert p["portal_id"] == "bonfire_dallasisd"


def test_parse_rss_district_id_set() -> None:
    """district_id is the machine identifier, not a STATE_ fallback."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    for p in postings:
        assert p["district_id"] == "dallas_isd"


def test_parse_rss_rfp_id_extracted() -> None:
    """rfp_id is the numeric ID from the opportunity URL."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    assert postings[0]["rfp_id"] == "111001"
    assert postings[1]["rfp_id"] == "111002"
    assert postings[2]["rfp_id"] == "111003"


def test_parse_rss_due_date_extracted() -> None:
    """due_date is parsed from 'Project closes …' in description."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    assert postings[0]["due_date"] == "2026-07-15"
    assert postings[1]["due_date"] == "2026-08-01"
    assert postings[2]["due_date"] == "2026-07-20"


def test_parse_rss_posted_date_extracted() -> None:
    """posted_date is parsed from the <pubDate> element."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    assert postings[0]["posted_date"] == "2026-06-09"
    assert postings[1]["posted_date"] == "2026-06-10"
    assert postings[2]["posted_date"] == "2026-06-11"


def test_parse_rss_empty_feed_returns_empty() -> None:
    """A feed with no <item> elements returns []."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _EMPTY_XML)
    assert postings == []


def test_parse_rss_malformed_xml_returns_empty() -> None:
    """Malformed XML returns [] without raising."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _MALFORMED_XML)
    assert postings == []


def test_parse_rss_agency_is_district_name() -> None:
    """agency is the human-readable district name."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    for p in postings:
        assert p["agency"] == "Dallas ISD"


def test_parse_rss_scope_text_always_empty() -> None:
    """scope_text is always '' (not available in RSS)."""
    postings = _parse_rss("dallasisd", "TX", "dallas_isd", "Dallas ISD", _SAMPLE_XML)
    for p in postings:
        assert p["scope_text"] == ""


# ---------------------------------------------------------------------------
# fetch_bonfire_opportunities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_bonfire_opportunities_success() -> None:
    """200 OK with valid RSS → returns parsed postings."""
    http = _make_http_mock(response_text=_SAMPLE_XML)
    postings = await fetch_bonfire_opportunities(_DALLAS_ENTRY, http)
    assert len(postings) == 3
    assert postings[0]["portal_id"] == "bonfire_dallasisd"


@pytest.mark.asyncio
async def test_fetch_bonfire_opportunities_http_error_returns_empty() -> None:
    """HTTP connection error → returns [] without raising."""
    http = _make_http_mock(raise_error=httpx.ConnectError("connection refused"))
    postings = await fetch_bonfire_opportunities(_DALLAS_ENTRY, http)
    assert postings == []


@pytest.mark.asyncio
async def test_fetch_bonfire_opportunities_non_200_returns_empty() -> None:
    """Non-200 response → returns []."""
    http = _make_http_mock(response_text="", status_code=404)
    postings = await fetch_bonfire_opportunities(_DALLAS_ENTRY, http)
    assert postings == []


@pytest.mark.asyncio
async def test_fetch_bonfire_opportunities_malformed_xml_returns_empty() -> None:
    """200 OK with malformed XML → returns [] without raising."""
    http = _make_http_mock(response_text=_MALFORMED_XML)
    postings = await fetch_bonfire_opportunities(_DALLAS_ENTRY, http)
    assert postings == []


@pytest.mark.asyncio
async def test_fetch_bonfire_opportunities_uses_browser_ua() -> None:
    """Requests include a browser-ish User-Agent header."""
    captured: dict[str, str] = {}

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def capture_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.get("headers", {})
        captured["user_agent"] = headers.get("User-Agent", "")
        mock_resp: MagicMock = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.text = _EMPTY_XML
        return mock_resp

    inner.request.side_effect = capture_request
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    await fetch_bonfire_opportunities(_DALLAS_ENTRY, http)
    assert "Mozilla" in captured.get("user_agent", ""), "Expected a browser User-Agent"


# ---------------------------------------------------------------------------
# fetch_all_bonfire_opportunities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_all_bonfire_opportunities_aggregates() -> None:
    """fetch_all returns results from all registry entries."""
    http = _make_http_mock(response_text=_SAMPLE_XML)
    all_postings = await fetch_all_bonfire_opportunities(http)
    # 3 items per entry × number of active registry entries
    assert len(all_postings) == 3 * len(BONFIRE_REGISTRY)


@pytest.mark.asyncio
async def test_fetch_all_bonfire_opportunities_error_isolation() -> None:
    """An error on one slug does not abort collection for the others."""
    slugs_attempted: set[str] = set()

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def side_effect(method: str, url: str, **kwargs: Any) -> httpx.Response:
        # Record each unique slug URL attempted (ignoring retries).
        for entry in BONFIRE_REGISTRY:
            if entry["slug"] in url:
                slugs_attempted.add(entry["slug"])
        if "dallasisd" in url:
            raise httpx.ConnectError("simulated failure")
        mock_resp: MagicMock = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.text = _SAMPLE_XML
        return mock_resp

    # Disable retries by passing an empty backoff sequence so the Dallas error
    # is surfaced immediately without additional requests.
    inner.request.side_effect = side_effect
    http = ScoutHttpClient(
        rate_limit=100.0,
        backoff=(),
        _inner=typing.cast(httpx.AsyncClient, inner),
    )

    all_postings = await fetch_all_bonfire_opportunities(http)
    # All slugs were attempted (slugs_attempted includes dallas on its 1 try).
    assert slugs_attempted == {e["slug"] for e in BONFIRE_REGISTRY}
    # Dallas was skipped; others returned 3 items each.
    assert len(all_postings) == 3 * (len(BONFIRE_REGISTRY) - 1)


# ---------------------------------------------------------------------------
# BONFIRE_REGISTRY structure
# ---------------------------------------------------------------------------


def test_bonfire_registry_required_districts_present() -> None:
    """BONFIRE_REGISTRY must include all five confirmed active districts."""
    slugs = {e["slug"] for e in BONFIRE_REGISTRY}
    required = {"dallasisd", "fortbendisd", "cps", "u-46", "austinisd"}
    assert required.issubset(slugs)


def test_bonfire_registry_entries_have_required_keys() -> None:
    """Each registry entry has slug, district, state, name."""
    required = {"slug", "district", "state", "name"}
    for entry in BONFIRE_REGISTRY:
        assert required.issubset(entry.keys()), f"Entry missing keys: {entry}"


def test_bonfire_registry_no_inactive_slugs() -> None:
    """katyisd is not in the active registry (slug does not resolve)."""
    slugs = {e["slug"] for e in BONFIRE_REGISTRY}
    assert "katyisd" not in slugs


# ---------------------------------------------------------------------------
# PORTAL_REGISTRY Bonfire entries
# ---------------------------------------------------------------------------


def test_portal_registry_contains_bonfire_entries() -> None:
    """PORTAL_REGISTRY must have a bonfire_<slug> entry for each active district."""
    for entry in BONFIRE_REGISTRY:
        key = f"bonfire_{entry['slug']}"
        assert key in PORTAL_REGISTRY, f"Missing portal key: {key}"
        assert PORTAL_REGISTRY[key]["type"] == "rss"


def test_portal_registry_bonfire_entries_have_bonfire_entry_key() -> None:
    """Each Bonfire portal entry carries the original _bonfire_entry dict."""
    for entry in BONFIRE_REGISTRY:
        key = f"bonfire_{entry['slug']}"
        portal = PORTAL_REGISTRY[key]
        assert "_bonfire_entry" in portal
        assert portal["_bonfire_entry"]["slug"] == entry["slug"]


@pytest.mark.asyncio
async def test_fetch_portal_postings_rss_dispatches_to_bonfire() -> None:
    """fetch_portal_postings with type=rss calls the Bonfire adapter."""
    http = _make_http_mock(response_text=_SAMPLE_XML)
    portal_id = "bonfire_dallasisd"
    portal = PORTAL_REGISTRY[portal_id]
    # The sample XML has 2 literacy-relevant items (literacy + tutoring/dyslexia)
    # and 1 non-literacy item (office furniture) → expect 2 results.
    results = await fetch_portal_postings(portal_id, portal, http)
    assert isinstance(results, list)
    # At least the two literacy items must pass the filter.
    assert len(results) >= 2
    for posting in results:
        assert posting["portal_id"] == "bonfire_dallasisd"


@pytest.mark.asyncio
async def test_fetch_portal_postings_rss_filters_non_literacy() -> None:
    """Non-literacy Bonfire items are filtered out by fetch_portal_postings."""
    xml_no_literacy = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Reference #: RFP-100. Name: Office Furniture Purchase</title>
      <description>Description: Procurement for office furniture. Project closes Jul 01, 2026.</description>
      <pubDate>Mon, 01 Jun 2026 08:00:00 -0500</pubDate>
      <link>https://dallasisd.bonfirehub.com/opportunities/999001</link>
    </item>
  </channel>
</rss>"""
    http = _make_http_mock(response_text=xml_no_literacy)
    portal_id = "bonfire_dallasisd"
    portal = PORTAL_REGISTRY[portal_id]
    results = await fetch_portal_postings(portal_id, portal, http)
    assert results == []


# ---------------------------------------------------------------------------
# mapping.py district_id override
# ---------------------------------------------------------------------------


def test_posting_to_finding_uses_district_id_from_bonfire_posting() -> None:
    """A posting with district_id → finding districtId is district_id, not STATE_TX."""
    posting: dict[str, Any] = {
        "portal_id": "bonfire_dallasisd",
        "state": "TX",
        "rfp_id": "111001",
        "title": "K-12 Literacy Curriculum and Assessment Platform",
        "agency": "Dallas ISD",
        "posted_date": "2026-06-09",
        "due_date": "2026-07-15",
        "source_url": "https://dallasisd.bonfirehub.com/opportunities/111001",
        "description": "Comprehensive K-12 literacy curriculum and reading assessment.",
        "scope_text": "",
        "district_id": "dallas_isd",
    }
    finding = posting_to_finding(posting)
    assert finding["districtId"] == "dallas_isd"


def test_posting_to_finding_falls_back_to_state_when_no_district_id() -> None:
    """A posting without district_id falls back to STATE_<XX>."""
    posting: dict[str, Any] = {
        "portal_id": "CA_eprocurement",
        "state": "CA",
        "rfp_id": "RFP-001",
        "title": "Literacy Assessment Tool",
        "agency": "CA Dept of Education",
        "posted_date": "",
        "due_date": "",
        "source_url": "https://example.com/rfp",
        "description": "Literacy assessment.",
        "scope_text": "",
    }
    finding = posting_to_finding(posting)
    assert finding["districtId"] == "STATE_CA"
