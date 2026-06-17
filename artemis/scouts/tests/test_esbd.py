"""Tests for the TX ESBD adapter.

All HTTP is mocked — no live network calls are made.

Coverage:
- esbd.py: _parse_esbd_date (MM/DD/YYYY, empty, garbage)
- esbd.py: _parse_esbd_json (valid payload, missing lines, empty list, malformed items)
- esbd.py: fetch_esbd_opportunities (success with JSON, HTML shell response,
           HTTP error, non-200)
- portals.py: PORTAL_REGISTRY ESBD entry + esbd dispatch via fetch_portal_postings
"""

from __future__ import annotations

import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.procurement.esbd import (
    ESBD_SERVICE_URL,
    PORTAL_ID,
    STATE,
    _parse_esbd_date,
    _parse_esbd_json,
    fetch_esbd_opportunities,
)
from artemis.scouts.procurement.portals import PORTAL_REGISTRY, fetch_portal_postings

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_http_mock(
    response_text: str = "",
    status_code: int = 200,
    content_type: str = "text/html;charset=utf-8",
    raise_error: Exception | None = None,
) -> ScoutHttpClient:
    """Return a ScoutHttpClient with a mocked inner httpx.AsyncClient."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = response_text
    mock_resp.headers = httpx.Headers({"content-type": content_type})

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    if raise_error is not None:
        inner.request.side_effect = raise_error
    else:
        inner.request.return_value = mock_resp

    return ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))


def _make_json_http_mock(payload: dict[str, Any]) -> ScoutHttpClient:
    """Return a ScoutHttpClient that serves a JSON payload."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = httpx.Headers({"content-type": "application/json"})
    mock_resp.json.return_value = payload

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.return_value = mock_resp

    return ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

_SAMPLE_PAYLOAD: dict[str, Any] = {
    "lines": [
        {
            "internalid": "12345",
            "eventTitle": "K-12 Literacy Curriculum and Instructional Materials",
            "agencyName": "Texas Education Agency",
            "solicitationId": "TEA-2026-001",
            "startDate": "06/01/2026",
            "endDate": "07/15/2026",
            "nigpCode": "915",
            "description": "Literacy curriculum for K-12 schools.",
        },
        {
            "internalid": "12346",
            "eventTitle": "Reading Intervention Assessment Platform",
            "agencyName": "Houston-Galveston Area Council (H-GAC)",
            "solicitationId": "HGAC-2026-002",
            "startDate": "06/05/2026",
            "endDate": "07/20/2026",
            "nigpCode": "920",
            "description": "Assessment platform for reading interventions.",
        },
        {
            "internalid": "12347",
            "eventTitle": "Office Furniture for State Agency Buildings",
            "agencyName": "Texas Facilities Commission",
            "solicitationId": "TFC-2026-003",
            "startDate": "06/08/2026",
            "endDate": "07/10/2026",
            "nigpCode": "615",
            "description": "Office furniture procurement.",
        },
    ],
    "totalRecordsFound": 3,
    "recordsPerPage": 25,
    "page": 1,
}

_EMPTY_PAYLOAD: dict[str, Any] = {
    "lines": [],
    "totalRecordsFound": 0,
    "recordsPerPage": 25,
    "page": 1,
}

_NS_HTML_SHELL = (
    "<!DOCTYPE html><html lang='en-US'><head><script>"
    "window.applicationStartTime = Date.now();"
    "</script></head><body><div id='main'></div></body></html>"
)


# ---------------------------------------------------------------------------
# _parse_esbd_date
# ---------------------------------------------------------------------------


def test_parse_esbd_date_slash_format() -> None:
    """MM/DD/YYYY → ISO YYYY-MM-DD."""
    assert _parse_esbd_date("06/01/2026") == "2026-06-01"


def test_parse_esbd_date_single_digit() -> None:
    """Single-digit month/day are zero-padded."""
    assert _parse_esbd_date("1/5/2026") == "2026-01-05"


def test_parse_esbd_date_empty_returns_empty() -> None:
    """Empty string returns ''."""
    assert _parse_esbd_date("") == ""


def test_parse_esbd_date_garbage_returns_empty() -> None:
    """Unparseable string returns ''."""
    assert _parse_esbd_date("not a date") == ""


def test_parse_esbd_date_none_like_returns_empty() -> None:
    """None-coerced string returns ''."""
    assert _parse_esbd_date("None") == ""


# ---------------------------------------------------------------------------
# _parse_esbd_json
# ---------------------------------------------------------------------------


def test_parse_esbd_json_returns_all_lines() -> None:
    """Sample payload with 3 items → 3 posting dicts."""
    result = _parse_esbd_json(_SAMPLE_PAYLOAD)
    assert len(result) == 3


def test_parse_esbd_json_required_keys() -> None:
    """Each posting has all required keys."""
    result = _parse_esbd_json(_SAMPLE_PAYLOAD)
    required = {
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
    for p in result:
        assert required.issubset(p.keys()), f"Missing keys: {required - p.keys()}"


def test_parse_esbd_json_portal_id_and_state() -> None:
    """portal_id = 'esbd_TX', state = 'TX'."""
    result = _parse_esbd_json(_SAMPLE_PAYLOAD)
    for p in result:
        assert p["portal_id"] == PORTAL_ID
        assert p["state"] == STATE


def test_parse_esbd_json_rfp_id_prefers_solicitation_id() -> None:
    """rfp_id uses solicitationId when present, falls back to internalid."""
    result = _parse_esbd_json(_SAMPLE_PAYLOAD)
    assert result[0]["rfp_id"] == "TEA-2026-001"
    assert result[1]["rfp_id"] == "HGAC-2026-002"


def test_parse_esbd_json_rfp_id_falls_back_to_internalid() -> None:
    """rfp_id falls back to internalid when solicitationId is missing."""
    payload: dict[str, Any] = {
        "lines": [
            {
                "internalid": "99999",
                "eventTitle": "Reading Assessment",
                "agencyName": "TEA",
                "startDate": "06/01/2026",
                "endDate": "07/01/2026",
            }
        ]
    }
    result = _parse_esbd_json(payload)
    assert result[0]["rfp_id"] == "99999"


def test_parse_esbd_json_dates_parsed() -> None:
    """posted_date and due_date are ISO-formatted."""
    result = _parse_esbd_json(_SAMPLE_PAYLOAD)
    assert result[0]["posted_date"] == "2026-06-01"
    assert result[0]["due_date"] == "2026-07-15"


def test_parse_esbd_json_source_url_includes_internalid() -> None:
    """source_url is txsmartbuy.gov/esbd/<internalid>."""
    result = _parse_esbd_json(_SAMPLE_PAYLOAD)
    assert "12345" in result[0]["source_url"]
    assert "txsmartbuy.gov" in result[0]["source_url"]


def test_parse_esbd_json_scope_text_empty() -> None:
    """scope_text is always ''."""
    result = _parse_esbd_json(_SAMPLE_PAYLOAD)
    for p in result:
        assert p["scope_text"] == ""


def test_parse_esbd_json_district_id_empty() -> None:
    """district_id is always '' (state-wide portal)."""
    result = _parse_esbd_json(_SAMPLE_PAYLOAD)
    for p in result:
        assert p["district_id"] == ""


def test_parse_esbd_json_empty_lines() -> None:
    """Empty lines list → []."""
    result = _parse_esbd_json(_EMPTY_PAYLOAD)
    assert result == []


def test_parse_esbd_json_missing_lines_key() -> None:
    """Missing 'lines' key → []."""
    result = _parse_esbd_json({"totalRecordsFound": 0})
    assert result == []


def test_parse_esbd_json_non_dict_lines() -> None:
    """Non-dict 'lines' value → []."""
    result = _parse_esbd_json({"lines": "not a list"})
    assert result == []


def test_parse_esbd_json_skips_items_without_rfp_id() -> None:
    """Items with no internalid AND no solicitationId are skipped."""
    payload: dict[str, Any] = {
        "lines": [
            {"eventTitle": "Orphan Entry with no ID"},
            {
                "internalid": "77777",
                "eventTitle": "Valid Entry",
                "agencyName": "TEA",
                "startDate": "06/01/2026",
                "endDate": "07/01/2026",
            },
        ]
    }
    result = _parse_esbd_json(payload)
    assert len(result) == 1
    assert result[0]["rfp_id"] == "77777"


# ---------------------------------------------------------------------------
# fetch_esbd_opportunities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_esbd_opportunities_html_shell_returns_empty() -> None:
    """When NS returns HTML shell (no session), result is []."""
    http = _make_http_mock(
        response_text=_NS_HTML_SHELL,
        content_type="text/html;charset=utf-8",
    )
    result = await fetch_esbd_opportunities(http, keyword="literacy")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_esbd_opportunities_http_error_returns_empty() -> None:
    """HTTP connection error → [] without raising."""
    http = _make_http_mock(raise_error=httpx.ConnectError("connection refused"))
    result = await fetch_esbd_opportunities(http, keyword="literacy")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_esbd_opportunities_non_200_returns_empty() -> None:
    """Non-200 HTTP status → []."""
    http = _make_http_mock(response_text="", status_code=403)
    result = await fetch_esbd_opportunities(http, keyword="literacy")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_esbd_opportunities_valid_json_returns_postings() -> None:
    """Valid JSON response → 3 postings."""
    http = _make_json_http_mock(_SAMPLE_PAYLOAD)
    result = await fetch_esbd_opportunities(http, keyword="literacy")
    assert len(result) == 3
    for p in result:
        assert p["state"] == "TX"
        assert p["portal_id"] == PORTAL_ID


@pytest.mark.asyncio
async def test_fetch_esbd_opportunities_empty_json_returns_empty() -> None:
    """JSON with empty lines → []."""
    http = _make_json_http_mock(_EMPTY_PAYLOAD)
    result = await fetch_esbd_opportunities(http, keyword="literacy")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_esbd_opportunities_sends_browser_ua() -> None:
    """Request includes a browser-ish User-Agent header."""
    captured: dict[str, str] = {}

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def capture_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.get("headers", {})
        captured["user_agent"] = headers.get("User-Agent", "")
        mock_resp: MagicMock = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.headers = httpx.Headers({"content-type": "text/html"})
        mock_resp.text = _NS_HTML_SHELL
        return mock_resp

    inner.request.side_effect = capture_request
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    await fetch_esbd_opportunities(http, keyword="literacy")
    assert "Mozilla" in captured.get("user_agent", ""), "Expected a browser User-Agent"


@pytest.mark.asyncio
async def test_fetch_esbd_opportunities_requests_service_url() -> None:
    """GET is sent to the ESBD service URL (not the SPA page URL)."""
    captured: dict[str, str] = {}

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def capture_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = str(url)
        captured["method"] = method
        mock_resp: MagicMock = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.headers = httpx.Headers({"content-type": "text/html"})
        mock_resp.text = _NS_HTML_SHELL
        return mock_resp

    inner.request.side_effect = capture_request
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    await fetch_esbd_opportunities(http, keyword="literacy")
    assert ESBD_SERVICE_URL in captured.get("url", "")


# ---------------------------------------------------------------------------
# PORTAL_REGISTRY ESBD entry
# ---------------------------------------------------------------------------


def test_portal_registry_contains_esbd_entry() -> None:
    """PORTAL_REGISTRY has an entry for TX ESBD."""
    assert PORTAL_ID in PORTAL_REGISTRY
    entry = PORTAL_REGISTRY[PORTAL_ID]
    assert entry["state"] == "TX"
    assert entry["type"] == "esbd"


@pytest.mark.asyncio
async def test_fetch_portal_postings_dispatches_to_esbd() -> None:
    """fetch_portal_postings with type=esbd calls the ESBD adapter."""
    # NS HTML shell → [] returned gracefully
    http = _make_http_mock(
        response_text=_NS_HTML_SHELL,
        content_type="text/html;charset=utf-8",
    )
    portal = PORTAL_REGISTRY[PORTAL_ID]
    result = await fetch_portal_postings(PORTAL_ID, portal, http)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_portal_postings_esbd_filters_non_literacy() -> None:
    """Non-literacy ESBD postings are filtered by fetch_portal_postings."""
    non_literacy_payload: dict[str, Any] = {
        "lines": [
            {
                "internalid": "55555",
                "eventTitle": "Office Furniture and Fixtures",
                "agencyName": "Texas Facilities Commission",
                "solicitationId": "TFC-001",
                "startDate": "06/01/2026",
                "endDate": "07/01/2026",
            }
        ]
    }
    http = _make_json_http_mock(non_literacy_payload)
    portal = PORTAL_REGISTRY[PORTAL_ID]
    result = await fetch_portal_postings(PORTAL_ID, portal, http)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_portal_postings_esbd_passes_literacy() -> None:
    """Literacy-relevant ESBD postings pass through the filter."""
    literacy_payload: dict[str, Any] = {
        "lines": [
            {
                "internalid": "66666",
                "eventTitle": "K-12 Literacy Assessment Software",
                "agencyName": "Texas Education Agency",
                "solicitationId": "TEA-001",
                "startDate": "06/01/2026",
                "endDate": "07/15/2026",
                "description": "Assessment software for literacy programs.",
            }
        ]
    }
    http = _make_json_http_mock(literacy_payload)
    portal = PORTAL_REGISTRY[PORTAL_ID]
    result = await fetch_portal_postings(PORTAL_ID, portal, http)
    assert len(result) == 1
    assert result[0]["portal_id"] == PORTAL_ID
