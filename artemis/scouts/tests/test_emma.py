"""Tests for the eMMA (Maryland) adapter.

All HTTP is mocked — no live network calls are made.

Coverage:
- emma.py: _parse_emma_date (slash format, long format, edge cases)
- emma.py: _strip_tags
- emma.py: _parse_emma_html (valid table, reCAPTCHA redirect, login redirect, empty)
- emma.py: fetch_emma_opportunities (success, HTTP error, non-200, reCAPTCHA response)
- portals.py: PORTAL_REGISTRY eMMA entry + emma dispatch via fetch_portal_postings
"""

from __future__ import annotations

import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.procurement.emma import (
    EMMA_SEARCH_URL,
    PORTAL_ID,
    STATE,
    _parse_emma_date,
    _parse_emma_html,
    _strip_tags,
    fetch_emma_opportunities,
)
from artemis.scouts.procurement.portals import PORTAL_REGISTRY, fetch_portal_postings

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_http_mock(
    response_text: str = "",
    status_code: int = 200,
    raise_error: Exception | None = None,
) -> ScoutHttpClient:
    """Return a ScoutHttpClient with a mocked inner httpx.AsyncClient."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = response_text
    mock_resp.headers = httpx.Headers({"content-type": "text/html; charset=utf-8"})

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    if raise_error is not None:
        inner.request.side_effect = raise_error
    else:
        inner.request.return_value = mock_resp

    return ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))


# ---------------------------------------------------------------------------
# Sample HTML — simulates a valid (session-authenticated) iValua table response
# ---------------------------------------------------------------------------

_SAMPLE_TABLE_HTML = """
<html><body>
<table>
  <tr>
    <th>Solicitation ID</th><th>Title</th><th>Agency</th>
    <th>Category</th><th>Open Date</th><th>Close Date</th><th>Status</th>
  </tr>
  <tr>
    <td><a href="/page.aspx/en/rfp/detail/MD-2026-001">MD-2026-001</a></td>
    <td>K-12 Literacy Curriculum and Assessment Platform</td>
    <td>Montgomery County Public Schools</td>
    <td>Educational Services</td>
    <td>06/01/2026</td>
    <td>07/15/2026</td>
    <td>Open</td>
  </tr>
  <tr>
    <td><a href="/page.aspx/en/rfp/detail/MD-2026-002">MD-2026-002</a></td>
    <td>Reading Intervention Program for Elementary Students</td>
    <td>Prince George's County Public Schools</td>
    <td>Educational Services</td>
    <td>06/05/2026</td>
    <td>07/20/2026</td>
    <td>Open</td>
  </tr>
  <tr>
    <td><a href="/page.aspx/en/rfp/detail/MD-2026-003">MD-2026-003</a></td>
    <td>Office Furniture and Fixtures</td>
    <td>Department of General Services</td>
    <td>Furniture</td>
    <td>06/08/2026</td>
    <td>07/10/2026</td>
    <td>Open</td>
  </tr>
</table>
</body></html>
"""

_BROWSER_CHECK_HTML = """
<html><body>
<title>Browser check: eMaryland Marketplace Advantage (eMMA)</title>
<script>window.ivCaptcha.init({action: 'browser_check', publicKey: '6LcT1NUU...'});</script>
</body></html>
"""

_LOGIN_REDIRECT_HTML = """
<html><body>
<title>Login: eMaryland Marketplace Advantage (eMMA)</title>
<form action="/page.aspx/en/usr/login">
</form>
</body></html>
"""

_EMPTY_TABLE_HTML = """
<html><body>
<table>
  <tr><th>Solicitation ID</th><th>Title</th><th>Agency</th></tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# _parse_emma_date
# ---------------------------------------------------------------------------


def test_parse_emma_date_slash_format() -> None:
    """MM/DD/YYYY → ISO YYYY-MM-DD."""
    assert _parse_emma_date("06/01/2026") == "2026-06-01"


def test_parse_emma_date_slash_format_leading_zeros() -> None:
    """Single-digit month/day are zero-padded."""
    assert _parse_emma_date("1/5/2026") == "2026-01-05"


def test_parse_emma_date_long_format() -> None:
    """'January 15, 2026' → ISO YYYY-MM-DD."""
    assert _parse_emma_date("January 15, 2026") == "2026-01-15"


def test_parse_emma_date_abbreviated_month() -> None:
    """'Jun 5, 2026' → ISO YYYY-MM-DD."""
    assert _parse_emma_date("Jun 5, 2026") == "2026-06-05"


def test_parse_emma_date_empty_returns_empty() -> None:
    """Empty string returns ''."""
    assert _parse_emma_date("") == ""


def test_parse_emma_date_garbage_returns_empty() -> None:
    """Unparseable string returns ''."""
    assert _parse_emma_date("not a date") == ""


def test_parse_emma_date_all_months() -> None:
    """All twelve month names map correctly."""
    months = [
        ("January", "01"),
        ("February", "02"),
        ("March", "03"),
        ("April", "04"),
        ("May", "05"),
        ("June", "06"),
        ("July", "07"),
        ("August", "08"),
        ("September", "09"),
        ("October", "10"),
        ("November", "11"),
        ("December", "12"),
    ]
    for name, num in months:
        assert _parse_emma_date(f"{name} 10, 2026") == f"2026-{num}-10", f"Failed for {name}"


# ---------------------------------------------------------------------------
# _strip_tags
# ---------------------------------------------------------------------------


def test_strip_tags_removes_html() -> None:
    """HTML tags are removed and entities decoded."""
    assert _strip_tags("<b>Hello &amp; World</b>") == "Hello & World"


def test_strip_tags_collapses_whitespace() -> None:
    """Multiple spaces/newlines are collapsed to single space."""
    result = _strip_tags("  foo  <br/>  bar  ")
    assert result == "foo bar"


def test_strip_tags_empty_returns_empty() -> None:
    assert _strip_tags("") == ""


# ---------------------------------------------------------------------------
# _parse_emma_html
# ---------------------------------------------------------------------------


def test_parse_emma_html_browser_check_returns_empty() -> None:
    """Response containing 'browser_check' returns []."""
    result = _parse_emma_html(_BROWSER_CHECK_HTML)
    assert result == []


def test_parse_emma_html_login_redirect_returns_empty() -> None:
    """Response containing 'usr/login' returns []."""
    result = _parse_emma_html(_LOGIN_REDIRECT_HTML)
    assert result == []


def test_parse_emma_html_valid_table_returns_postings() -> None:
    """Valid table with 3 data rows → 3 posting dicts."""
    # Header row is skipped (< 2 td); 3 data rows returned
    result = _parse_emma_html(_SAMPLE_TABLE_HTML)
    assert len(result) == 3


def test_parse_emma_html_posting_keys() -> None:
    """Each posting has all required keys."""
    result = _parse_emma_html(_SAMPLE_TABLE_HTML)
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


def test_parse_emma_html_portal_id_and_state() -> None:
    """portal_id = 'emma_MD', state = 'MD'."""
    result = _parse_emma_html(_SAMPLE_TABLE_HTML)
    for p in result:
        assert p["portal_id"] == PORTAL_ID
        assert p["state"] == STATE


def test_parse_emma_html_rfp_ids_extracted() -> None:
    """rfp_id is extracted from the first cell."""
    result = _parse_emma_html(_SAMPLE_TABLE_HTML)
    rfp_ids = [p["rfp_id"] for p in result]
    assert "MD-2026-001" in rfp_ids
    assert "MD-2026-002" in rfp_ids


def test_parse_emma_html_dates_parsed() -> None:
    """posted_date and due_date are ISO-formatted from MM/DD/YYYY cells."""
    result = _parse_emma_html(_SAMPLE_TABLE_HTML)
    first = result[0]
    assert first["posted_date"] == "2026-06-01"
    assert first["due_date"] == "2026-07-15"


def test_parse_emma_html_source_url_extracted() -> None:
    """source_url is extracted from href in the first cell."""
    result = _parse_emma_html(_SAMPLE_TABLE_HTML)
    first = result[0]
    assert "MD-2026-001" in first["source_url"]


def test_parse_emma_html_scope_text_always_empty() -> None:
    """scope_text is always ''."""
    result = _parse_emma_html(_SAMPLE_TABLE_HTML)
    for p in result:
        assert p["scope_text"] == ""


def test_parse_emma_html_district_id_always_empty() -> None:
    """district_id is always '' (state-wide portal)."""
    result = _parse_emma_html(_SAMPLE_TABLE_HTML)
    for p in result:
        assert p["district_id"] == ""


def test_parse_emma_html_empty_table_returns_empty() -> None:
    """Table with only a header row (no data rows) returns []."""
    result = _parse_emma_html(_EMPTY_TABLE_HTML)
    assert result == []


def test_parse_emma_html_no_table_returns_empty() -> None:
    """HTML with no <table> / <tr> elements returns []."""
    result = _parse_emma_html("<html><body><p>No table here.</p></body></html>")
    assert result == []


# ---------------------------------------------------------------------------
# fetch_emma_opportunities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_emma_opportunities_browser_check_returns_empty() -> None:
    """200 response with reCAPTCHA page → []."""
    http = _make_http_mock(response_text=_BROWSER_CHECK_HTML)
    result = await fetch_emma_opportunities(http)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_emma_opportunities_http_error_returns_empty() -> None:
    """HTTP connection error → [] without raising."""
    http = _make_http_mock(raise_error=httpx.ConnectError("connection refused"))
    result = await fetch_emma_opportunities(http)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_emma_opportunities_non_200_returns_empty() -> None:
    """Non-200 HTTP status → []."""
    http = _make_http_mock(response_text="", status_code=403)
    result = await fetch_emma_opportunities(http)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_emma_opportunities_valid_html_returns_postings() -> None:
    """Valid table HTML → 3 postings returned."""
    http = _make_http_mock(response_text=_SAMPLE_TABLE_HTML)
    result = await fetch_emma_opportunities(http)
    assert len(result) == 3
    for p in result:
        assert p["state"] == "MD"
        assert p["portal_id"] == PORTAL_ID


@pytest.mark.asyncio
async def test_fetch_emma_opportunities_sends_browser_ua() -> None:
    """Request includes a browser-ish User-Agent header."""
    captured: dict[str, str] = {}

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def capture_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.get("headers", {})
        captured["user_agent"] = headers.get("User-Agent", "")
        mock_resp: MagicMock = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.text = _BROWSER_CHECK_HTML
        mock_resp.headers = httpx.Headers({"content-type": "text/html"})
        return mock_resp

    inner.request.side_effect = capture_request
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    await fetch_emma_opportunities(http)
    assert "Mozilla" in captured.get("user_agent", ""), "Expected a browser User-Agent"


@pytest.mark.asyncio
async def test_fetch_emma_opportunities_requests_correct_url() -> None:
    """GET is sent to the eMMA public search URL."""
    captured: dict[str, str] = {}

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    async def capture_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = str(url)
        captured["method"] = method
        mock_resp: MagicMock = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.text = _BROWSER_CHECK_HTML
        mock_resp.headers = httpx.Headers({"content-type": "text/html"})
        return mock_resp

    inner.request.side_effect = capture_request
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    await fetch_emma_opportunities(http)
    assert captured.get("url") == EMMA_SEARCH_URL
    assert captured.get("method") == "GET"


# ---------------------------------------------------------------------------
# PORTAL_REGISTRY eMMA entry
# ---------------------------------------------------------------------------


def test_portal_registry_contains_emma_entry() -> None:
    """PORTAL_REGISTRY has an entry for eMMA."""
    assert PORTAL_ID in PORTAL_REGISTRY
    entry = PORTAL_REGISTRY[PORTAL_ID]
    assert entry["state"] == "MD"
    assert entry["type"] == "emma"


@pytest.mark.asyncio
async def test_fetch_portal_postings_dispatches_to_emma() -> None:
    """fetch_portal_postings with type=emma calls the eMMA adapter."""
    http = _make_http_mock(response_text=_BROWSER_CHECK_HTML)
    portal = PORTAL_REGISTRY[PORTAL_ID]
    # reCAPTCHA response → [] returned gracefully
    result = await fetch_portal_postings(PORTAL_ID, portal, http)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_portal_postings_emma_filters_non_literacy() -> None:
    """Non-literacy eMMA postings are filtered by fetch_portal_postings."""
    html_no_literacy = """
<html><body>
<table>
  <tr>
    <td><a href="/rfp/MD-999">MD-999</a></td>
    <td>Office Furniture for Administrative Building</td>
    <td>Department of General Services</td>
    <td>Furniture</td>
    <td>06/01/2026</td>
    <td>07/01/2026</td>
    <td>Open</td>
  </tr>
</table>
</body></html>
"""
    http = _make_http_mock(response_text=html_no_literacy)
    portal = PORTAL_REGISTRY[PORTAL_ID]
    result = await fetch_portal_postings(PORTAL_ID, portal, http)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_portal_postings_emma_passes_literacy() -> None:
    """Literacy-relevant eMMA postings pass through the filter."""
    html_literacy = """
<html><body>
<table>
  <tr>
    <td><a href="/rfp/MD-001">MD-001</a></td>
    <td>K-12 Literacy Curriculum RFP</td>
    <td>Montgomery County PS</td>
    <td>Educational Services</td>
    <td>06/01/2026</td>
    <td>07/15/2026</td>
    <td>Open</td>
  </tr>
</table>
</body></html>
"""
    http = _make_http_mock(response_text=html_literacy)
    portal = PORTAL_REGISTRY[PORTAL_ID]
    result = await fetch_portal_postings(PORTAL_ID, portal, http)
    assert len(result) == 1
    assert result[0]["portal_id"] == PORTAL_ID
