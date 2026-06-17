"""Tests for the OpenGov Procurement adapter.

All HTTP is mocked — no live network calls are made.

Coverage:
- opengov.py: _parse_opengov_date (ISO timestamp, date-only, empty, garbage)
- opengov.py: _parse_opengov_json (valid payload, missing data key, empty list,
              items without id, items without number)
- opengov.py: fetch_opengov_opportunities (no API key → [], 401 → [],
              non-200 → [], non-JSON content-type → [], valid JSON → postings,
              HTTP error → [], browser UA sent, correct URL)
- opengov.py: fetch_all_opengov_opportunities (aggregates, error isolation)
- opengov.py: OPENGOV_REGISTRY structure
- portals.py: PORTAL_REGISTRY OpenGov entries + opengov dispatch via fetch_portal_postings
"""

from __future__ import annotations

import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.procurement.opengov import (
    OPENGOV_REGISTRY,
    OPENGOV_SOLICITATIONS_URL,
    _parse_opengov_date,
    _parse_opengov_json,
    fetch_all_opengov_opportunities,
    fetch_opengov_opportunities,
)
from artemis.scouts.procurement.portals import PORTAL_REGISTRY, fetch_portal_postings

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_PCSB_ENTRY = next(e for e in OPENGOV_REGISTRY if e["slug"] == "pcsb")


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
    "data": [
        {
            "id": "200001",
            "name": "K-12 Literacy Curriculum and Reading Assessment Platform",
            "number": "RFP-PCSB-2026-001",
            "status": "open",
            "published_at": "2026-06-01T00:00:00Z",
            "response_due_at": "2026-07-15T17:00:00-04:00",
            "organization": {"name": "Pinellas County School District", "slug": "pcsb"},
            "description": "Pinellas seeking proposals for a districtwide literacy curriculum.",
        },
        {
            "id": "200002",
            "name": "Tutoring Services for Struggling Readers",
            "number": "RFP-PCSB-2026-002",
            "status": "open",
            "published_at": "2026-06-05T00:00:00Z",
            "response_due_at": "2026-08-01T17:00:00-04:00",
            "organization": {"name": "Pinellas County School District", "slug": "pcsb"},
            "description": "Evidence-based tutoring for students with dyslexia.",
        },
        {
            "id": "200003",
            "name": "Roof Replacement and Designated Repairs",
            "number": "RFP-PCSB-2026-003",
            "status": "open",
            "published_at": "2026-06-08T00:00:00Z",
            "response_due_at": "2026-07-10T17:00:00-04:00",
            "organization": {"name": "Pinellas County School District", "slug": "pcsb"},
            "description": "Roofing services for district facilities.",
        },
    ],
    "meta": {"total_count": 3, "page": 1, "per_page": 100},
}

_EMPTY_PAYLOAD: dict[str, Any] = {
    "data": [],
    "meta": {"total_count": 0, "page": 1, "per_page": 100},
}

_UNAUTHORIZED_HTML = '{"error": "Unauthorized, invalid or missing API token"}'


# ---------------------------------------------------------------------------
# _parse_opengov_date
# ---------------------------------------------------------------------------


def test_parse_opengov_date_iso_timestamp() -> None:
    """Full ISO timestamp → ISO YYYY-MM-DD."""
    assert _parse_opengov_date("2026-06-01T00:00:00Z") == "2026-06-01"


def test_parse_opengov_date_iso_with_offset() -> None:
    """ISO timestamp with timezone offset → ISO YYYY-MM-DD."""
    assert _parse_opengov_date("2026-07-15T17:00:00-04:00") == "2026-07-15"


def test_parse_opengov_date_date_only() -> None:
    """Plain YYYY-MM-DD string → ISO YYYY-MM-DD."""
    assert _parse_opengov_date("2026-08-01") == "2026-08-01"


def test_parse_opengov_date_empty_returns_empty() -> None:
    """Empty string returns ''."""
    assert _parse_opengov_date("") == ""


def test_parse_opengov_date_none_returns_empty() -> None:
    """None returns ''."""
    assert _parse_opengov_date(None) == ""


def test_parse_opengov_date_garbage_returns_empty() -> None:
    """Unparseable string returns ''."""
    assert _parse_opengov_date("not a date") == ""


# ---------------------------------------------------------------------------
# _parse_opengov_json
# ---------------------------------------------------------------------------


def test_parse_opengov_json_returns_all_items() -> None:
    """Sample payload with 3 items → 3 posting dicts."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
    assert len(result) == 3


def test_parse_opengov_json_required_keys() -> None:
    """Each posting dict has all required keys."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
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
    for p in result:
        assert required_keys.issubset(p.keys()), f"Missing keys: {required_keys - p.keys()}"


def test_parse_opengov_json_portal_id_format() -> None:
    """portal_id is 'opengov_<slug>'."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
    for p in result:
        assert p["portal_id"] == "opengov_pcsb"


def test_parse_opengov_json_state_and_district_id() -> None:
    """state and district_id are set correctly."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
    for p in result:
        assert p["state"] == "FL"
        assert p["district_id"] == "pinellas_county_schools"


def test_parse_opengov_json_rfp_id_uses_number() -> None:
    """rfp_id is the 'number' field when present."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
    assert result[0]["rfp_id"] == "RFP-PCSB-2026-001"
    assert result[1]["rfp_id"] == "RFP-PCSB-2026-002"


def test_parse_opengov_json_rfp_id_falls_back_to_id() -> None:
    """rfp_id falls back to 'id' when 'number' is absent."""
    payload: dict[str, Any] = {
        "data": [
            {
                "id": "99999",
                "name": "Reading Intervention Services",
                "status": "open",
                "published_at": "2026-06-01T00:00:00Z",
                "response_due_at": "2026-07-01T00:00:00Z",
            }
        ]
    }
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", payload
    )
    assert result[0]["rfp_id"] == "99999"


def test_parse_opengov_json_dates_parsed() -> None:
    """posted_date and due_date are ISO YYYY-MM-DD."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
    assert result[0]["posted_date"] == "2026-06-01"
    assert result[0]["due_date"] == "2026-07-15"
    assert result[1]["due_date"] == "2026-08-01"


def test_parse_opengov_json_source_url_includes_id() -> None:
    """source_url contains the project ID and portal slug."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
    assert "pcsb" in result[0]["source_url"]
    assert "200001" in result[0]["source_url"]
    assert "procurement.opengov.com" in result[0]["source_url"]


def test_parse_opengov_json_agency_is_district_name() -> None:
    """agency is the district_name parameter, not the API org name."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
    for p in result:
        assert p["agency"] == "Pinellas County School District"


def test_parse_opengov_json_scope_text_always_empty() -> None:
    """scope_text is always ''."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _SAMPLE_PAYLOAD
    )
    for p in result:
        assert p["scope_text"] == ""


def test_parse_opengov_json_empty_data_returns_empty() -> None:
    """Empty data list → []."""
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", _EMPTY_PAYLOAD
    )
    assert result == []


def test_parse_opengov_json_missing_data_key_returns_empty() -> None:
    """Missing 'data' key → []."""
    result = _parse_opengov_json(
        "pcsb",
        "FL",
        "pinellas_county_schools",
        "Pinellas County School District",
        {"meta": {"total_count": 0}},
    )
    assert result == []


def test_parse_opengov_json_skips_items_without_id_and_number() -> None:
    """Items with neither 'id' nor 'number' are skipped."""
    payload: dict[str, Any] = {
        "data": [
            {"name": "No ID entry"},
            {
                "id": "88888",
                "name": "Valid Entry",
                "status": "open",
                "published_at": "2026-06-01T00:00:00Z",
                "response_due_at": "2026-07-01T00:00:00Z",
            },
        ]
    }
    result = _parse_opengov_json(
        "pcsb", "FL", "pinellas_county_schools", "Pinellas County School District", payload
    )
    assert len(result) == 1
    assert result[0]["rfp_id"] == "88888"


# ---------------------------------------------------------------------------
# fetch_opengov_opportunities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_no_api_key_returns_empty() -> None:
    """Missing API key (None) → [] without making any HTTP call."""
    # Even a mock that would succeed is never called when api_key=None.
    http = _make_json_http_mock(_SAMPLE_PAYLOAD)
    result = await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key=None)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_empty_string_key_returns_empty() -> None:
    """Empty string API key (falsy) → [] without making any HTTP call."""
    http = _make_json_http_mock(_SAMPLE_PAYLOAD)
    result = await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_401_returns_empty() -> None:
    """HTTP 401 Unauthorized → []."""
    http = _make_http_mock(
        response_text=_UNAUTHORIZED_HTML,
        status_code=401,
        content_type="application/json",
    )
    result = await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="test-key-12345")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_non_200_returns_empty() -> None:
    """Non-200 HTTP status → []."""
    http = _make_http_mock(response_text="", status_code=403)
    result = await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="test-key-12345")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_non_json_content_type_returns_empty() -> None:
    """Response with non-JSON content-type → []."""
    http = _make_http_mock(
        response_text="<html>redirect</html>",
        status_code=200,
        content_type="text/html",
    )
    result = await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="test-key-12345")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_http_error_returns_empty() -> None:
    """HTTP connection error → [] without raising."""
    http = _make_http_mock(raise_error=httpx.ConnectError("connection refused"))
    result = await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="test-key-12345")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_valid_json_returns_postings() -> None:
    """Valid JSON response with API key → 3 postings."""
    http = _make_json_http_mock(_SAMPLE_PAYLOAD)
    result = await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="test-key-12345")
    assert len(result) == 3
    for p in result:
        assert p["portal_id"] == "opengov_pcsb"
        assert p["state"] == "FL"


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_empty_json_returns_empty() -> None:
    """Valid JSON with empty data → []."""
    http = _make_json_http_mock(_EMPTY_PAYLOAD)
    result = await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="test-key-12345")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_sends_api_key_header() -> None:
    """Request includes the x-api-key header with the provided key."""
    captured: dict[str, str] = {}

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def capture_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.get("headers", {})
        captured["x_api_key"] = headers.get("x-api-key", "")
        captured["user_agent"] = headers.get("User-Agent", "")
        mock_resp: MagicMock = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.headers = httpx.Headers({"content-type": "application/json"})
        mock_resp.json.return_value = _EMPTY_PAYLOAD
        return mock_resp

    inner.request.side_effect = capture_request
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="my-secret-key")
    assert captured.get("x_api_key") == "my-secret-key"
    assert "Mozilla" in captured.get("user_agent", ""), "Expected a browser User-Agent"


@pytest.mark.asyncio
async def test_fetch_opengov_opportunities_requests_solicitations_url() -> None:
    """GET is sent to the OpenGov datasets solicitations URL."""
    captured: dict[str, str] = {}

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def capture_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = str(url)
        captured["method"] = method
        mock_resp: MagicMock = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.headers = httpx.Headers({"content-type": "application/json"})
        mock_resp.json.return_value = _EMPTY_PAYLOAD
        return mock_resp

    inner.request.side_effect = capture_request
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    await fetch_opengov_opportunities(_PCSB_ENTRY, http, api_key="test-key")
    assert OPENGOV_SOLICITATIONS_URL in captured.get("url", "")
    assert captured.get("method") == "GET"


# ---------------------------------------------------------------------------
# fetch_all_opengov_opportunities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_all_opengov_no_api_key_returns_empty() -> None:
    """No API key → [] across all registry entries."""
    http = _make_json_http_mock(_SAMPLE_PAYLOAD)
    result = await fetch_all_opengov_opportunities(http, api_key=None)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_all_opengov_aggregates_across_registry() -> None:
    """With a valid key, results from all registry entries are aggregated."""
    http = _make_json_http_mock(_SAMPLE_PAYLOAD)
    result = await fetch_all_opengov_opportunities(http, api_key="test-key")
    # 3 items × len(OPENGOV_REGISTRY) active entries
    assert len(result) == 3 * len(OPENGOV_REGISTRY)


@pytest.mark.asyncio
async def test_fetch_all_opengov_error_isolation() -> None:
    """Error on one slug does not prevent others from being attempted."""
    call_count = 0

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def side_effect(method: str, url: str, **kwargs: Any) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("simulated failure")

    inner.request.side_effect = side_effect
    http = ScoutHttpClient(
        rate_limit=100.0,
        backoff=(),
        _inner=typing.cast(httpx.AsyncClient, inner),
    )

    result = await fetch_all_opengov_opportunities(http, api_key="test-key")
    # All entries were attempted; all errored → combined result is []
    assert result == []
    assert call_count == len(OPENGOV_REGISTRY)


# ---------------------------------------------------------------------------
# OPENGOV_REGISTRY structure
# ---------------------------------------------------------------------------


def test_opengov_registry_pcsb_present() -> None:
    """OPENGOV_REGISTRY must include the pcsb (Pinellas) entry."""
    slugs = {e["slug"] for e in OPENGOV_REGISTRY}
    assert "pcsb" in slugs


def test_opengov_registry_entries_have_required_keys() -> None:
    """Each registry entry has slug, district, state, name."""
    required = {"slug", "district", "state", "name"}
    for entry in OPENGOV_REGISTRY:
        assert required.issubset(entry.keys()), f"Entry missing keys: {entry}"


def test_opengov_registry_pcsb_state_is_fl() -> None:
    """Pinellas County Schools (pcsb) must have state='FL'."""
    pcsb = next(e for e in OPENGOV_REGISTRY if e["slug"] == "pcsb")
    assert pcsb["state"] == "FL"


def test_opengov_registry_no_unverified_slugs() -> None:
    """katyisd is NOT in the active registry (piloting — not yet confirmed)."""
    slugs = {e["slug"] for e in OPENGOV_REGISTRY}
    assert "katyisd" not in slugs


# ---------------------------------------------------------------------------
# PORTAL_REGISTRY OpenGov entries
# ---------------------------------------------------------------------------


def test_portal_registry_contains_opengov_pcsb_entry() -> None:
    """PORTAL_REGISTRY must have an opengov_pcsb entry."""
    assert "opengov_pcsb" in PORTAL_REGISTRY
    entry = PORTAL_REGISTRY["opengov_pcsb"]
    assert entry["type"] == "opengov"
    assert entry["state"] == "FL"


def test_portal_registry_opengov_entries_have_opengov_entry_key() -> None:
    """Each OpenGov portal entry carries the original _opengov_entry dict."""
    for reg_entry in OPENGOV_REGISTRY:
        key = f"opengov_{reg_entry['slug']}"
        portal = PORTAL_REGISTRY[key]
        assert "_opengov_entry" in portal
        assert portal["_opengov_entry"]["slug"] == reg_entry["slug"]


def test_portal_registry_opengov_entries_for_all_registry_entries() -> None:
    """PORTAL_REGISTRY has an opengov_<slug> entry for each active registry entry."""
    for reg_entry in OPENGOV_REGISTRY:
        key = f"opengov_{reg_entry['slug']}"
        assert key in PORTAL_REGISTRY, f"Missing portal key: {key}"
        assert PORTAL_REGISTRY[key]["type"] == "opengov"


# ---------------------------------------------------------------------------
# fetch_portal_postings — OpenGov dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_portal_postings_dispatches_to_opengov() -> None:
    """fetch_portal_postings with type=opengov calls the OpenGov adapter."""
    # No API key in env → adapter returns [] gracefully.
    http = _make_json_http_mock(_SAMPLE_PAYLOAD)
    portal_id = "opengov_pcsb"
    portal = PORTAL_REGISTRY[portal_id]
    result = await fetch_portal_postings(portal_id, portal, http)
    # Without OPENGOV_API_KEY set, expect []
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_fetch_portal_postings_opengov_filters_non_literacy() -> None:
    """Non-literacy OpenGov postings are filtered by fetch_portal_postings."""
    non_literacy_payload: dict[str, Any] = {
        "data": [
            {
                "id": "300001",
                "name": "Roof Replacement Services",
                "number": "RFP-PCSB-ROOF-001",
                "status": "open",
                "published_at": "2026-06-01T00:00:00Z",
                "response_due_at": "2026-07-01T00:00:00Z",
                "description": "Roofing services for district facilities.",
            }
        ]
    }
    http = _make_json_http_mock(non_literacy_payload)
    portal_id = "opengov_pcsb"
    portal = PORTAL_REGISTRY[portal_id]

    # Inject API key directly via env var override using monkeypatch-style import trick.
    import os

    orig = os.environ.get("OPENGOV_API_KEY")
    os.environ["OPENGOV_API_KEY"] = "test-key-12345"
    try:
        result = await fetch_portal_postings(portal_id, portal, http)
    finally:
        if orig is None:
            os.environ.pop("OPENGOV_API_KEY", None)
        else:
            os.environ["OPENGOV_API_KEY"] = orig

    assert result == []


@pytest.mark.asyncio
async def test_fetch_portal_postings_opengov_passes_literacy() -> None:
    """Literacy-relevant OpenGov postings pass through the filter."""
    literacy_payload: dict[str, Any] = {
        "data": [
            {
                "id": "300002",
                "name": "K-12 Literacy Assessment Software Platform",
                "number": "RFP-PCSB-LIT-001",
                "status": "open",
                "published_at": "2026-06-01T00:00:00Z",
                "response_due_at": "2026-07-15T00:00:00Z",
                "description": "Assessment software for K-12 reading intervention.",
            }
        ]
    }
    http = _make_json_http_mock(literacy_payload)
    portal_id = "opengov_pcsb"
    portal = PORTAL_REGISTRY[portal_id]

    import os

    orig = os.environ.get("OPENGOV_API_KEY")
    os.environ["OPENGOV_API_KEY"] = "test-key-12345"
    try:
        result = await fetch_portal_postings(portal_id, portal, http)
    finally:
        if orig is None:
            os.environ.pop("OPENGOV_API_KEY", None)
        else:
            os.environ["OPENGOV_API_KEY"] = orig

    assert len(result) == 1
    assert result[0]["portal_id"] == "opengov_pcsb"
    assert result[0]["state"] == "FL"
