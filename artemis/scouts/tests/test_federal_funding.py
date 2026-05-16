"""Tests for the D3 Federal Funding Scout.

Covers: client.py (FederalRegisterClient, GrantsGovClient, EdGovRssClient),
mapping.py (reason codes, urgency tiers, sourceType), and scout.py
(gather_findings concurrency, error isolation, deduplication, run_once).

All HTTP calls are mocked — no live network traffic.
"""

from __future__ import annotations

import typing
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from artemis.scouts.base import ScoutConfig
from artemis.scouts.federal_funding.client import (
    EdGovRssClient,
    FederalRegisterClient,
    FedRegDocument,
    GrantOpportunity,
    GrantsGovClient,
    RssItem,
)
from artemis.scouts.federal_funding.mapping import (
    fed_reg_to_finding,
    grant_to_finding,
    rss_item_to_finding,
)
from artemis.scouts.federal_funding.scout import FederalFundingScout

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>ED.gov Press Releases</title>
    <item>
      <title>Department Announces $200M in Reading Grants</title>
      <link>https://www.ed.gov/press/2024/reading-grants</link>
      <description>The Department of Education today announced...</description>
      <pubDate>Mon, 15 Jan 2024 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>ESSER Funds Extended for Rural Districts</title>
      <link>https://www.ed.gov/press/2024/esser-rural</link>
      <description>Rural districts will receive additional ESSER allocations.</description>
      <pubDate>Tue, 16 Jan 2024 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

_EMPTY_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel><title>ED.gov Press Releases</title></channel>
</rss>
"""


def _make_httpx_response(
    *,
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    text: str = "",
) -> MagicMock:
    """Build a mock httpx.Response."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = text
    if json_data is not None:
        mock_resp.json.return_value = json_data
    return mock_resp


def _make_scout_http_client(
    *,
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    text: str = "",
) -> MagicMock:
    """Build a mock ScoutHttpClient."""
    resp = _make_httpx_response(status_code=status_code, json_data=json_data, text=text)
    client: MagicMock = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    client.aclose = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# client.py — FederalRegisterClient
# ---------------------------------------------------------------------------


async def test_fed_reg_client_builds_correct_url() -> None:
    """search() calls GET with 'documents.json' and keyword params."""
    mock_http = _make_scout_http_client(json_data={"results": []})
    client = FederalRegisterClient(_http=typing.cast(Any, mock_http))
    await client.search(["literacy", "reading"])
    mock_http.get.assert_called_once()
    args, kwargs = mock_http.get.call_args
    assert args[0] == "documents.json"
    params: dict[str, str] = kwargs.get("params", {})
    assert "conditions[term]" in params
    assert "literacy" in params["conditions[term]"]


async def test_fed_reg_client_returns_documents() -> None:
    """search() parses and returns FedRegDocument instances."""
    mock_http = _make_scout_http_client(
        json_data={
            "results": [
                {
                    "document_number": "2024-12345",
                    "title": "Comprehensive Literacy Grant",
                    "abstract": "CLSD funding for states.",
                    "publication_date": "2024-01-15",
                    "html_url": "https://www.federalregister.gov/documents/2024/01/15/2024-12345",
                }
            ]
        }
    )
    client = FederalRegisterClient(_http=typing.cast(Any, mock_http))
    docs = await client.search(["literacy"])
    assert len(docs) == 1
    assert docs[0].document_number == "2024-12345"
    assert docs[0].title == "Comprehensive Literacy Grant"


async def test_fed_reg_client_returns_empty_on_non_200() -> None:
    """Non-200 response returns empty list without raising."""
    mock_http = _make_scout_http_client(status_code=404)
    client = FederalRegisterClient(_http=typing.cast(Any, mock_http))
    docs = await client.search(["literacy"])
    assert docs == []


async def test_fed_reg_client_returns_empty_on_missing_results() -> None:
    """Response with no 'results' key returns empty list."""
    mock_http = _make_scout_http_client(json_data={})
    client = FederalRegisterClient(_http=typing.cast(Any, mock_http))
    docs = await client.search(["literacy"])
    assert docs == []


# ---------------------------------------------------------------------------
# client.py — GrantsGovClient
# ---------------------------------------------------------------------------


async def test_grants_gov_client_posts_to_correct_url() -> None:
    """search() issues a POST to the Grants.gov search endpoint."""
    mock_http = _make_scout_http_client(json_data={"oppHits": []})
    client = GrantsGovClient(_http=typing.cast(Any, mock_http))
    await client.search(["literacy"])
    mock_http.post.assert_called_once()
    args, _ = mock_http.post.call_args
    assert "grants.gov" in args[0]


async def test_grants_gov_client_body_contains_keyword_and_statuses() -> None:
    """POST body includes keyword and oppStatuses."""
    mock_http = _make_scout_http_client(json_data={"oppHits": []})
    client = GrantsGovClient(_http=typing.cast(Any, mock_http))
    await client.search(["literacy", "reading"])
    _, kwargs = mock_http.post.call_args
    body: dict[str, Any] = kwargs.get("json", {})
    assert "literacy" in body["keyword"]
    assert "reading" in body["keyword"]
    assert "oppStatuses" in body


async def test_grants_gov_client_sets_auth_header_when_key_provided() -> None:
    """When api_key is set, GrantsGovClient is built with Authorization header."""
    # The header is baked into ScoutHttpClient at construction; we verify it
    # by checking that the client is constructed with the key.
    with patch("artemis.scouts.federal_funding.client.ScoutHttpClient") as mock_cls:
        mock_instance = _make_scout_http_client(json_data={"oppHits": []})
        mock_cls.return_value = mock_instance
        client = GrantsGovClient(api_key="my-secret-key")
        _, kwargs = mock_cls.call_args
        headers: dict[str, str] = kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer my-secret-key"
        await client.aclose()


async def test_grants_gov_client_no_auth_header_when_no_key() -> None:
    """Without api_key, ScoutHttpClient is built with empty headers."""
    with patch("artemis.scouts.federal_funding.client.ScoutHttpClient") as mock_cls:
        mock_instance = _make_scout_http_client(json_data={"oppHits": []})
        mock_cls.return_value = mock_instance
        client = GrantsGovClient(api_key="")
        _, kwargs = mock_cls.call_args
        headers: dict[str, str] = kwargs.get("headers", {})
        assert "Authorization" not in headers
        await client.aclose()


async def test_grants_gov_client_returns_empty_on_non_200() -> None:
    """Non-200 response returns empty list without raising."""
    mock_http = _make_scout_http_client(status_code=503)
    client = GrantsGovClient(_http=typing.cast(Any, mock_http))
    opps = await client.search(["literacy"])
    assert opps == []


async def test_grants_gov_client_parses_opportunities() -> None:
    """search() parses oppHits into GrantOpportunity models."""
    mock_http = _make_scout_http_client(
        json_data={
            "oppHits": [
                {
                    "id": "ED-GRANTS-012024-001",
                    "title": "Comprehensive Literacy Grant FY2024",
                    "agencyName": "Dept. of Education",
                    "closeDate": "2024-03-31",
                    "awardFloor": 100000,
                    "synopsis": "Supports comprehensive literacy programs.",
                }
            ]
        }
    )
    client = GrantsGovClient(_http=typing.cast(Any, mock_http))
    opps = await client.search(["literacy"])
    assert len(opps) == 1
    assert opps[0].opportunity_id == "ED-GRANTS-012024-001"
    assert opps[0].close_date == "2024-03-31"
    assert opps[0].award_floor == 100000


# ---------------------------------------------------------------------------
# client.py — EdGovRssClient
# ---------------------------------------------------------------------------


async def test_ed_gov_rss_client_parses_items() -> None:
    """fetch() parses RSS XML into RssItem instances."""
    mock_http = _make_scout_http_client(text=_RSS_XML)
    client = EdGovRssClient(_http=typing.cast(Any, mock_http))
    items = await client.fetch()
    assert len(items) == 2
    assert items[0].title == "Department Announces $200M in Reading Grants"
    assert items[0].link == "https://www.ed.gov/press/2024/reading-grants"
    assert items[1].pub_date == "Tue, 16 Jan 2024 09:00:00 GMT"


async def test_ed_gov_rss_client_empty_feed() -> None:
    """fetch() returns empty list when RSS has no items."""
    mock_http = _make_scout_http_client(text=_EMPTY_RSS_XML)
    client = EdGovRssClient(_http=typing.cast(Any, mock_http))
    items = await client.fetch()
    assert items == []


async def test_ed_gov_rss_client_returns_empty_on_non_200() -> None:
    """Non-200 response returns empty list without raising."""
    mock_http = _make_scout_http_client(status_code=502, text="")
    client = EdGovRssClient(_http=typing.cast(Any, mock_http))
    items = await client.fetch()
    assert items == []


async def test_ed_gov_rss_client_returns_empty_on_malformed_xml() -> None:
    """Malformed XML returns empty list without raising."""
    mock_http = _make_scout_http_client(text="<not valid xml <<")
    client = EdGovRssClient(_http=typing.cast(Any, mock_http))
    items = await client.fetch()
    assert items == []


# ---------------------------------------------------------------------------
# mapping.py — reason codes and urgency
# ---------------------------------------------------------------------------


def test_mapping_federal_grant_open_reason_code() -> None:
    """A plain open grant with no special keywords gets FEDERAL_GRANT_OPEN."""
    grant = GrantOpportunity(
        opportunity_id="G001",
        title="Education Technology Grant",
        agency_name="Dept. of Education",
        close_date=None,
    )
    finding = grant_to_finding(grant)
    assert "FEDERAL_GRANT_OPEN" in finding["reasonCodes"]


def test_mapping_clsd_announcement_reason_code_from_title() -> None:
    """Title containing 'comprehensive literacy' triggers CLSD_ANNOUNCEMENT."""
    grant = GrantOpportunity(
        opportunity_id="G002",
        title="Comprehensive Literacy State Development Grant",
        agency_name="Dept. of Education",
        close_date=None,
    )
    finding = grant_to_finding(grant)
    assert "CLSD_ANNOUNCEMENT" in finding["reasonCodes"]


def test_mapping_clsd_announcement_reason_code_from_clsd_keyword() -> None:
    """Title containing 'clsd' triggers CLSD_ANNOUNCEMENT."""
    grant = GrantOpportunity(
        opportunity_id="G003",
        title="CLSD Supplemental Funding 2024",
        agency_name="Dept. of Education",
        close_date=None,
    )
    finding = grant_to_finding(grant)
    assert "CLSD_ANNOUNCEMENT" in finding["reasonCodes"]


def test_mapping_esser_cliff_reference_reason_code() -> None:
    """Text containing 'esser' triggers ESSER_CLIFF_REFERENCE."""
    doc = FedRegDocument(
        document_number="DOC-001",
        title="ESSER Fund Deadline Extension Notice",
        abstract="Districts must obligate ESSER funds by September 2024.",
        publication_date="2024-01-15",
        html_url="https://federalregister.gov/example",
    )
    finding = fed_reg_to_finding(doc)
    assert "ESSER_CLIFF_REFERENCE" in finding["reasonCodes"]


def test_mapping_urgency_hot_for_close_date_within_30_days() -> None:
    """urgency=hot when close_date within 30 days AND literacy keyword in title."""
    close_date = (date.today() + timedelta(days=15)).isoformat()
    grant = GrantOpportunity(
        opportunity_id="G004",
        title="Literacy Curriculum Grant — Final Deadline",
        agency_name="Dept. of Education",
        close_date=close_date,
    )
    finding = grant_to_finding(grant)
    assert finding["urgency"] == "hot"


def test_mapping_urgency_standard_for_30_to_90_days() -> None:
    """urgency=standard when close_date is 31-90 days out."""
    close_date = (date.today() + timedelta(days=60)).isoformat()
    grant = GrantOpportunity(
        opportunity_id="G005",
        title="Reading Improvement Grant",
        agency_name="Dept. of Education",
        close_date=close_date,
    )
    finding = grant_to_finding(grant)
    assert finding["urgency"] == "standard"


def test_mapping_urgency_enrichment_when_no_deadline() -> None:
    """urgency=enrichment when close_date is absent."""
    grant = GrantOpportunity(
        opportunity_id="G006",
        title="Title I Discretionary Grant",
        agency_name="Dept. of Education",
        close_date=None,
    )
    finding = grant_to_finding(grant)
    assert finding["urgency"] == "enrichment"


def test_mapping_source_type_federal_register() -> None:
    """fed_reg_to_finding() returns sourceType='federal_register'."""
    doc = FedRegDocument(
        document_number="DOC-002",
        title="Reading First Grant Notice",
        abstract="Funding for reading programs.",
        publication_date="2024-01-15",
        html_url="https://federalregister.gov/example2",
    )
    finding = fed_reg_to_finding(doc)
    assert finding["sourceType"] == "federal_register"
    assert finding["discoveredBy"] == "federal_funding_scout"


def test_mapping_source_type_grants_gov() -> None:
    """grant_to_finding() returns sourceType='grants_gov'."""
    grant = GrantOpportunity(
        opportunity_id="G007",
        title="Dyslexia Intervention Grant",
        agency_name="Dept. of Education",
        close_date=None,
    )
    finding = grant_to_finding(grant)
    assert finding["sourceType"] == "grants_gov"
    assert finding["discoveredBy"] == "federal_funding_scout"


def test_mapping_source_type_district_press_for_rss() -> None:
    """rss_item_to_finding() returns sourceType='district_press'."""
    item = RssItem(
        title="ED Announces Literacy Initiative",
        link="https://www.ed.gov/press/2024/literacy",
        description="A new program to support literacy.",
        pub_date="Mon, 15 Jan 2024 12:00:00 GMT",
    )
    finding = rss_item_to_finding(item)
    assert finding["sourceType"] == "district_press"
    assert finding["discoveredBy"] == "federal_funding_scout"


def test_mapping_federal_grant_deadline_reason_code() -> None:
    """FEDERAL_GRANT_DEADLINE fires when close within 90 days + literacy keyword."""
    close_date = (date.today() + timedelta(days=45)).isoformat()
    grant = GrantOpportunity(
        opportunity_id="G008",
        title="Literacy Assessment Grant",
        agency_name="Dept. of Education",
        close_date=close_date,
    )
    finding = grant_to_finding(grant)
    assert "FEDERAL_GRANT_DEADLINE" in finding["reasonCodes"]


def test_mapping_urgency_enrichment_when_beyond_90_days() -> None:
    """urgency=enrichment when close_date is more than 90 days out."""
    close_date = (date.today() + timedelta(days=120)).isoformat()
    grant = GrantOpportunity(
        opportunity_id="G009",
        title="Literacy Research Grant — Long Window",
        agency_name="Dept. of Education",
        close_date=close_date,
    )
    finding = grant_to_finding(grant)
    assert finding["urgency"] == "enrichment"


# ---------------------------------------------------------------------------
# scout.py — FederalFundingScout
# ---------------------------------------------------------------------------


def _make_fed_reg_client(docs: list[FedRegDocument]) -> MagicMock:
    client: MagicMock = MagicMock(spec=FederalRegisterClient)
    client.search = AsyncMock(return_value=docs)
    return client


def _make_grants_client(opps: list[GrantOpportunity]) -> MagicMock:
    client: MagicMock = MagicMock(spec=GrantsGovClient)
    client.search = AsyncMock(return_value=opps)
    return client


def _make_rss_client(items: list[RssItem]) -> MagicMock:
    client: MagicMock = MagicMock(spec=EdGovRssClient)
    client.fetch = AsyncMock(return_value=items)
    return client


async def test_gather_findings_calls_all_three_sources() -> None:
    """_gather_findings() calls all three source clients."""
    fed_reg = _make_fed_reg_client([])
    grants = _make_grants_client([])
    rss = _make_rss_client([])
    scout = FederalFundingScout(
        ScoutConfig(),
        _fed_reg_client=typing.cast(FederalRegisterClient, fed_reg),
        _grants_client=typing.cast(GrantsGovClient, grants),
        _rss_client=typing.cast(EdGovRssClient, rss),
    )
    await scout._gather_findings()
    fed_reg.search.assert_called_once()
    grants.search.assert_called_once()
    rss.fetch.assert_called_once()


async def test_gather_findings_continues_when_one_source_raises() -> None:
    """If one source raises, others still contribute findings."""
    failing_fed_reg: MagicMock = MagicMock(spec=FederalRegisterClient)
    failing_fed_reg.search = AsyncMock(side_effect=RuntimeError("simulated failure"))

    grants = _make_grants_client(
        [
            GrantOpportunity(
                opportunity_id="G001",
                title="Reading First Grant",
                agency_name="Dept. of Education",
            )
        ]
    )
    rss = _make_rss_client([])

    scout = FederalFundingScout(
        ScoutConfig(),
        _fed_reg_client=typing.cast(FederalRegisterClient, failing_fed_reg),
        _grants_client=typing.cast(GrantsGovClient, grants),
        _rss_client=typing.cast(EdGovRssClient, rss),
    )
    findings = await scout._gather_findings()
    # Should have the grant finding even though fed_reg failed
    assert len(findings) == 1
    assert findings[0]["sourceType"] == "grants_gov"


async def test_run_once_calls_emit_signals_with_findings() -> None:
    """run_once() calls emit_signals when findings are present."""
    fed_reg = _make_fed_reg_client(
        [
            FedRegDocument(
                document_number="DOC-001",
                title="Literacy Grant Notice",
                abstract="Federal support for literacy.",
                publication_date="2024-01-15",
                html_url="https://federalregister.gov/001",
            )
        ]
    )
    grants = _make_grants_client([])
    rss = _make_rss_client([])

    emit_resp: MagicMock = MagicMock(spec=httpx.Response)
    emit_resp.status_code = 200
    emit_resp.json.return_value = {
        "runId": "r42",
        "status": "ok",
        "createdCount": 1,
        "skippedCount": 0,
        "errors": [],
    }
    emit_resp.raise_for_status.return_value = None
    api_client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    api_client.post.return_value = emit_resp

    scout = FederalFundingScout(
        ScoutConfig(enabled=True),
        _fed_reg_client=typing.cast(FederalRegisterClient, fed_reg),
        _grants_client=typing.cast(GrantsGovClient, grants),
        _rss_client=typing.cast(EdGovRssClient, rss),
        _client=typing.cast(httpx.AsyncClient, api_client),
    )
    result = await scout.run_once()
    assert result.status == "ok"
    assert result.run_id == "r42"


async def test_findings_contain_discovered_by() -> None:
    """Every finding carries discoveredBy='federal_funding_scout'."""
    fed_reg = _make_fed_reg_client(
        [
            FedRegDocument(
                document_number="D1",
                title="ESSER Fund Notice",
                abstract="ESSER funding information.",
                publication_date="2024-01-15",
                html_url="https://federalregister.gov/d1",
            )
        ]
    )
    grants = _make_grants_client([])
    rss = _make_rss_client([])

    scout = FederalFundingScout(
        ScoutConfig(),
        _fed_reg_client=typing.cast(FederalRegisterClient, fed_reg),
        _grants_client=typing.cast(GrantsGovClient, grants),
        _rss_client=typing.cast(EdGovRssClient, rss),
    )
    findings = await scout._gather_findings()
    assert all(f["discoveredBy"] == "federal_funding_scout" for f in findings)


async def test_deduplication_same_title_from_two_sources() -> None:
    """Same title appearing in two sources produces only one finding."""
    shared_title = "Comprehensive Literacy Grant"
    fed_reg = _make_fed_reg_client(
        [
            FedRegDocument(
                document_number="D1",
                title=shared_title,
                abstract="CLSD funding.",
                publication_date="2024-01-15",
                html_url="https://federalregister.gov/d1",
            )
        ]
    )
    # Grants.gov returns same title
    grants = _make_grants_client(
        [
            GrantOpportunity(
                opportunity_id="G001",
                title=shared_title,
                agency_name="Dept. of Education",
            )
        ]
    )
    rss = _make_rss_client([])

    scout = FederalFundingScout(
        ScoutConfig(),
        _fed_reg_client=typing.cast(FederalRegisterClient, fed_reg),
        _grants_client=typing.cast(GrantsGovClient, grants),
        _rss_client=typing.cast(EdGovRssClient, rss),
    )
    findings = await scout._gather_findings()
    # Deduplicated: only one finding despite appearing in two sources
    assert len(findings) == 1


async def test_gather_findings_returns_empty_when_all_sources_empty() -> None:
    """Returns empty list when all three sources yield nothing."""
    scout = FederalFundingScout(
        ScoutConfig(),
        _fed_reg_client=typing.cast(FederalRegisterClient, _make_fed_reg_client([])),
        _grants_client=typing.cast(GrantsGovClient, _make_grants_client([])),
        _rss_client=typing.cast(EdGovRssClient, _make_rss_client([])),
    )
    findings = await scout._gather_findings()
    assert findings == []


async def test_scout_type_class_var() -> None:
    """FederalFundingScout.scout_type is 'federal_funding_scout'."""
    assert FederalFundingScout.scout_type == "federal_funding_scout"
