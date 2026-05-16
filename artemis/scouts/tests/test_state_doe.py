"""Tests for the D5 State DoE Scout.

All external I/O is mocked — no real HTTP, no real Playwright, no real pypdfium2.

Coverage:
- mapping.py  (≥10 tests)
- sources.py  (≥7 tests)
- scout.py    (≥5 tests)
"""

from __future__ import annotations

import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import ScoutConfig
from artemis.scouts.state_doe.mapping import (
    GUBERNATORIAL_EO_LITERACY,
    STATE_BILITERACY_INITIATIVE,
    STATE_DYSLEXIA_MANDATE,
    STATE_GUIDANCE_ISSUED,
    STATE_MANDATE_ISSUED,
    STATE_OBC_LEGISLATION,
    item_to_finding,
)
from artemis.scouts.state_doe.scout import StateDoEScout
from artemis.scouts.state_doe.sources import (
    STATE_DOE_SOURCES,
    fetch_doe_html,
    fetch_doe_rss,
    fetch_state_board_agenda,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>FL DoE News</title>
    <item>
      <title>New Reading Guidance Issued</title>
      <link>https://www.fldoe.org/news/reading-guidance</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <description>The Florida DoE has issued updated reading guidance for K-3.</description>
    </item>
    <item>
      <title>Dyslexia Screening Mandate Announced</title>
      <link>https://www.fldoe.org/news/dyslexia-mandate</link>
      <pubDate>Tue, 02 Jan 2024 09:00:00 GMT</pubDate>
      <description>New dyslexia screening mandate for all public school students.</description>
    </item>
  </channel>
</rss>
"""

_EMPTY_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel><title>Empty</title></channel>
</rss>
"""


def _make_http_mock(
    *,
    status_code: int = 200,
    text: str = "",
    json_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock ScoutHttpClient that returns a fixed response."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = text
    mock_resp.content = text.encode()
    mock_resp.headers = headers or {}
    if json_data is not None:
        mock_resp.json.return_value = json_data

    client: MagicMock = MagicMock()
    client.get = AsyncMock(return_value=mock_resp)
    client.post = AsyncMock(return_value=mock_resp)
    client.aclose = AsyncMock()
    return client


def _make_scout(
    *,
    priority_states: list[str] | None = None,
    http_mock: MagicMock | None = None,
    scraper_page: Any | None = None,
    pdf_open_fn: Any = None,
) -> StateDoEScout:
    """Construct a StateDoEScout with optional injection points."""
    scout = StateDoEScout(
        ScoutConfig(dry_run=True),
        priority_states=priority_states or ["FL"],
        _http_client=typing.cast(
            ScoutHttpClient, http_mock or _make_http_mock(text=_EMPTY_RSS_XML)
        ),
        _scraper_page=scraper_page,
        _pdf_open_fn=pdf_open_fn,
    )
    return scout


# Minimal FakePage for Playwright injection (mirrors the pattern in test_scraper.py)
class FakePage:
    """Minimal BrowserPage stand-in for tests."""

    def __init__(self, html: str = "<html><body></body></html>") -> None:
        self._html = html
        self.goto = AsyncMock(return_value=None)
        self.content = AsyncMock(return_value=html)
        self.evaluate = AsyncMock(return_value=[])


# ---------------------------------------------------------------------------
# mapping.py tests
# ---------------------------------------------------------------------------


def test_item_to_finding_eo_returns_hot_gubernatorial() -> None:
    """EO text maps to GUBERNATORIAL_EO_LITERACY reason code + hot urgency."""
    item = {
        "title": "Governor Signs Executive Order on Literacy",
        "summary": "A new executive order requires literacy screening.",
        "_source_type": "governor_rss",
    }
    finding = item_to_finding(item, "FL")
    assert GUBERNATORIAL_EO_LITERACY in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_item_to_finding_mandate_returns_hot() -> None:
    """'mandate' in text → STATE_MANDATE_ISSUED + hot urgency."""
    item = {
        "title": "State Issues Reading Mandate for All K-3",
        "summary": "A binding mandate for literacy.",
        "_source_type": "doe_rss",
    }
    finding = item_to_finding(item, "IN")
    assert STATE_MANDATE_ISSUED in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_item_to_finding_guidance_returns_standard() -> None:
    """'guidance' in text → STATE_GUIDANCE_ISSUED + standard urgency."""
    item = {
        "title": "DoE Issues Updated Reading Guidance",
        "summary": "Non-binding guidance for educators.",
        "_source_type": "doe_rss",
    }
    finding = item_to_finding(item, "MD")
    assert STATE_GUIDANCE_ISSUED in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_item_to_finding_dyslexia_tag() -> None:
    """'dyslexia' in text adds STATE_DYSLEXIA_MANDATE to reason codes."""
    item = {
        "title": "New Dyslexia Screening Program",
        "summary": "Statewide dyslexia screening rollout.",
        "_source_type": "doe_html",
    }
    finding = item_to_finding(item, "TX")
    assert STATE_DYSLEXIA_MANDATE in finding["reasonCodes"]


def test_item_to_finding_biliteracy_tag() -> None:
    """'biliteracy' in text adds STATE_BILITERACY_INITIATIVE to reason codes."""
    item = {
        "title": "Biliteracy Seal Program Expanded",
        "summary": "The state seal of biliteracy is now available for more students.",
        "_source_type": "doe_rss",
    }
    finding = item_to_finding(item, "IL")
    assert STATE_BILITERACY_INITIATIVE in finding["reasonCodes"]


def test_item_to_finding_obc_tag() -> None:
    """'outcomes-based' in text adds STATE_OBC_LEGISLATION to reason codes."""
    item = {
        "title": "Outcomes-Based Contract Framework Introduced",
        "summary": "New outcomes-based contracting rules.",
        "_source_type": "doe_rss",
    }
    finding = item_to_finding(item, "MO")
    assert STATE_OBC_LEGISLATION in finding["reasonCodes"]


def test_item_to_finding_default_enrichment() -> None:
    """No matching keywords → enrichment urgency + STATE_GUIDANCE_ISSUED default."""
    item = {
        "title": "School Calendar Update",
        "summary": "The state has updated the academic calendar.",
        "_source_type": "doe_rss",
    }
    finding = item_to_finding(item, "MI")
    assert finding["urgency"] == "enrichment"
    assert STATE_GUIDANCE_ISSUED in finding["reasonCodes"]


def test_item_to_finding_discovered_by() -> None:
    """discoveredBy is always 'state_doe_scout'."""
    item = {"title": "Anything", "_source_type": "doe_rss"}
    finding = item_to_finding(item, "FL")
    assert finding["discoveredBy"] == "state_doe_scout"


def test_item_to_finding_district_id() -> None:
    """districtId is 'STATE_FL' for state='FL'."""
    item = {"title": "Test", "_source_type": "doe_rss"}
    finding = item_to_finding(item, "FL")
    assert finding["districtId"] == "STATE_FL"


def test_item_to_finding_district_id_lowercase_state() -> None:
    """districtId is uppercased even when state is passed in lowercase."""
    item = {"title": "Test", "_source_type": "doe_rss"}
    finding = item_to_finding(item, "tx")
    assert finding["districtId"] == "STATE_TX"


def test_item_to_finding_governor_rss_source_type() -> None:
    """Items from governor_rss get sourceType='governor_press'."""
    item = {
        "title": "Governor Announces Literacy Initiative",
        "_source_type": "governor_rss",
    }
    finding = item_to_finding(item, "FL")
    assert finding["sourceType"] == "governor_press"


def test_item_to_finding_doe_rss_source_type() -> None:
    """Items from doe_rss get sourceType='state_doe'."""
    item = {
        "title": "DoE Update",
        "_source_type": "doe_rss",
    }
    finding = item_to_finding(item, "FL")
    assert finding["sourceType"] == "state_doe"


def test_item_to_finding_mandate_not_if_recommended() -> None:
    """'mandate' with 'recommended' is not treated as binding mandate → no STATE_MANDATE_ISSUED."""
    item = {
        "title": "Recommended mandate approach for literacy",
        "summary": "The state recommends following the guidance mandate.",
        "_source_type": "doe_rss",
    }
    finding = item_to_finding(item, "FL")
    # "mandate" present but "recommended" co-occurs → should NOT be hot mandate
    assert STATE_MANDATE_ISSUED not in finding["reasonCodes"]


def test_item_to_finding_metadata_has_state_and_source_url() -> None:
    """metadata contains 'state' and 'source_url'."""
    item = {
        "title": "Guidance Notice",
        "link": "https://example.com/notice",
        "_source_type": "doe_rss",
    }
    finding = item_to_finding(item, "FL")
    assert finding["metadata"]["state"] == "FL"
    assert finding["metadata"]["source_url"] == "https://example.com/notice"


# ---------------------------------------------------------------------------
# sources.py tests
# ---------------------------------------------------------------------------


async def test_fetch_doe_rss_parses_items() -> None:
    """fetch_doe_rss() parses a valid RSS feed into a list of dicts."""
    mock_http = _make_http_mock(text=_RSS_XML)
    items = await fetch_doe_rss("FL", typing.cast(ScoutHttpClient, mock_http))
    assert len(items) == 2
    assert items[0]["title"] == "New Reading Guidance Issued"
    assert items[0]["link"] == "https://www.fldoe.org/news/reading-guidance"
    assert items[0]["_source_type"] == "doe_rss"


async def test_fetch_doe_rss_returns_empty_on_http_error() -> None:
    """fetch_doe_rss() returns [] when the HTTP call raises an exception."""
    mock_http: MagicMock = MagicMock()
    mock_http.get = AsyncMock(side_effect=RuntimeError("connection refused"))
    items = await fetch_doe_rss("FL", typing.cast(ScoutHttpClient, mock_http))
    assert items == []


async def test_rss_fetch_no_crash_on_empty_xml() -> None:
    """fetch_doe_rss() returns [] when the XML body is empty."""
    mock_http = _make_http_mock(text="")
    items = await fetch_doe_rss("FL", typing.cast(ScoutHttpClient, mock_http))
    assert items == []


async def test_fetch_doe_rss_returns_empty_on_non_200() -> None:
    """fetch_doe_rss() returns [] on a non-200 HTTP status."""
    mock_http = _make_http_mock(status_code=503, text="")
    items = await fetch_doe_rss("FL", typing.cast(ScoutHttpClient, mock_http))
    assert items == []


async def test_fetch_doe_html_uses_scraper() -> None:
    """fetch_doe_html() calls the scraper's fetch_html and returns items."""
    html = '<html><body><a href="/news/1">Literacy Guidance Update</a></body></html>'
    fake_session = MagicMock()
    fake_session.fetch_html = AsyncMock(return_value=html)

    items = await fetch_doe_html("FL", fake_session)
    assert len(items) >= 1
    titles = [it["title"] for it in items]
    assert any("Literacy Guidance Update" in t for t in titles)


async def test_fetch_state_board_agenda_calls_http() -> None:
    """fetch_state_board_agenda() makes a GET request and returns a list."""
    mock_http = _make_http_mock(text="Board meeting agenda text about literacy standards.")
    items = await fetch_state_board_agenda("FL", typing.cast(ScoutHttpClient, mock_http))
    assert len(items) == 1
    assert items[0]["_source_type"] == "state_board"
    assert "FL" in items[0]["title"]


def test_state_doe_sources_has_required_states() -> None:
    """STATE_DOE_SOURCES must include FL, IN, MD, MO, MI, IL, TX."""
    required = {"FL", "IN", "MD", "MO", "MI", "IL", "TX"}
    assert required.issubset(set(STATE_DOE_SOURCES.keys()))


def test_state_doe_sources_has_doe_rss_or_scrape_for_each() -> None:
    """Every state must have at least a doe_rss or doe_scrape_url entry."""
    for state, config in STATE_DOE_SOURCES.items():
        has_url = bool(config.get("doe_rss")) or bool(config.get("doe_scrape_url"))
        assert has_url, f"State {state} has neither doe_rss nor doe_scrape_url"


# ---------------------------------------------------------------------------
# scout.py tests
# ---------------------------------------------------------------------------


async def test_gather_findings_returns_list() -> None:
    """_gather_findings() returns a list (possibly empty)."""
    scout = _make_scout(priority_states=["FL"])
    findings = await scout._gather_findings()
    assert isinstance(findings, list)


async def test_gather_findings_empty_when_all_sources_empty() -> None:
    """_gather_findings() returns [] when all sources return empty lists."""
    # Make all http.get calls return 404 so all sources return []
    mock_http_404 = _make_http_mock(status_code=404, text="")
    scout = _make_scout(
        priority_states=["FL"],
        http_mock=mock_http_404,
        scraper_page=FakePage(html="<html></html>"),
    )
    findings = await scout._gather_findings()
    assert findings == []


async def test_gather_findings_deduplicates_by_url() -> None:
    """Same URL from two sources appears only once in findings."""
    # Build an RSS with two items sharing the same link
    duplicate_rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <title>Guidance Item A</title>
      <link>https://example.com/same-url</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <description>Literacy guidance update.</description>
    </item>
    <item>
      <title>Guidance Item B (duplicate URL)</title>
      <link>https://example.com/same-url</link>
      <pubDate>Mon, 01 Jan 2024 13:00:00 GMT</pubDate>
      <description>Same link, different title.</description>
    </item>
  </channel>
</rss>
"""
    # RSS returns two items with the same link; governor + agenda return 404
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = duplicate_rss
    mock_resp.content = duplicate_rss.encode()
    mock_resp.headers = {}

    # 404 response for governor + agenda calls
    mock_resp_404: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp_404.status_code = 404
    mock_resp_404.text = ""
    mock_resp_404.content = b""
    mock_resp_404.headers = {}

    http_mock: MagicMock = MagicMock()
    # First call (doe_rss) returns the duplicate RSS; subsequent calls get 404
    http_mock.get = AsyncMock(side_effect=[mock_resp, mock_resp_404, mock_resp_404])

    scout = _make_scout(priority_states=["FL"], http_mock=http_mock)
    findings = await scout._gather_findings()
    urls = [f["metadata"]["source_url"] for f in findings]
    assert urls.count("https://example.com/same-url") == 1


async def test_gather_findings_continues_on_state_error() -> None:
    """An error on one state is logged but other states are still processed."""
    # Make FL fail by raising on the first get() call, but IN succeeds with empty RSS
    mock_resp_empty: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp_empty.status_code = 200
    mock_resp_empty.text = _EMPTY_RSS_XML
    mock_resp_empty.content = _EMPTY_RSS_XML.encode()
    mock_resp_empty.headers = {}

    http_mock: MagicMock = MagicMock()
    # FL get() calls: first raises (doe_rss for FL)
    # IN get() calls succeed with empty RSS
    http_mock.get = AsyncMock(
        side_effect=[
            RuntimeError("FL network error"),  # FL doe_rss
            mock_resp_empty,  # IN doe_rss → empty
            mock_resp_empty,  # IN governor_rss → empty
            mock_resp_empty,  # IN state_board → empty (returns item)
        ]
    )

    scout = _make_scout(
        priority_states=["FL", "IN"],
        http_mock=http_mock,
        scraper_page=FakePage(html="<html></html>"),
    )
    # Should not raise; FL is skipped, IN is processed
    findings = await scout._gather_findings()
    assert isinstance(findings, list)


async def test_gather_findings_collects_from_governor_rss() -> None:
    """Governor RSS items are mapped with sourceType='governor_press'."""
    gov_rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Governor News</title>
    <item>
      <title>Governor Issues Executive Order on Literacy</title>
      <link>https://www.flgov.com/eo/literacy-eo</link>
      <pubDate>Wed, 03 Jan 2024 10:00:00 GMT</pubDate>
      <description>Executive order mandating literacy screening.</description>
    </item>
  </channel>
</rss>
"""
    empty_resp: MagicMock = MagicMock(spec=httpx.Response)
    empty_resp.status_code = 200
    empty_resp.text = _EMPTY_RSS_XML
    empty_resp.content = _EMPTY_RSS_XML.encode()
    empty_resp.headers = {}

    gov_resp: MagicMock = MagicMock(spec=httpx.Response)
    gov_resp.status_code = 200
    gov_resp.text = gov_rss
    gov_resp.content = gov_rss.encode()
    gov_resp.headers = {}

    resp_404: MagicMock = MagicMock(spec=httpx.Response)
    resp_404.status_code = 404
    resp_404.text = ""
    resp_404.content = b""
    resp_404.headers = {}

    http_mock: MagicMock = MagicMock()
    # Call order: FL doe_rss (empty) → FL governor_rss (gov) → FL state_board (404)
    http_mock.get = AsyncMock(side_effect=[empty_resp, gov_resp, resp_404])

    scout = _make_scout(
        priority_states=["FL"],
        http_mock=http_mock,
        scraper_page=FakePage(html="<html></html>"),
    )
    findings = await scout._gather_findings()

    governor_findings = [f for f in findings if f["sourceType"] == "governor_press"]
    assert len(governor_findings) >= 1
    assert governor_findings[0]["urgency"] == "hot"  # "executive order" → hot


def test_state_doe_scout_type_class_var() -> None:
    """StateDoEScout.scout_type must be 'state_doe_scout'."""
    assert StateDoEScout.scout_type == "state_doe_scout"
