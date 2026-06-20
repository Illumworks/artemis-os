"""Tests for the D2 Legislative Scout (LegiScan API integration).

All HTTP is mocked — no live API calls are made.

Coverage:
- client.py (≥7 tests)
- mapping.py (≥7 tests)
- scout.py (≥6 tests)
"""

from __future__ import annotations

import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import ScoutConfig
from artemis.scouts.legislative.client import (
    STATUS_ENROLLED,
    STATUS_INTRODUCED,
    STATUS_PASSED,
    Bill,
    BillSummary,
    LegiScanClient,
)
from artemis.scouts.legislative.mapping import bill_to_finding
from artemis.scouts.legislative.scout import LegislativeScout

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

# Mirrors a real LegiScan getSearch result entry: bill_number (not "number"),
# relevance + state, and NO numeric status (that comes from getBill).
_BILL_SUMMARY_RAW: dict[str, Any] = {
    "bill_id": 123,
    "bill_number": "HB 1",
    "title": "Florida Literacy Instruction Act",
    "relevance": 95,
    "state": "FL",
    "last_action": "Referred to committee",
    "last_action_date": "2024-01-15",
    "url": "https://legiscan.com/FL/bill/HB1/2024",
}

_BILL_RAW: dict[str, Any] = {
    "bill_id": 123,
    "bill_number": "HB 1",
    "title": "Florida Literacy Instruction Act",
    "description": "An act relating to literacy instruction and reading curriculum.",
    "status": STATUS_INTRODUCED,
    "last_action": "Referred to committee",
    "state": "FL",
    "body": "House",
    "session": {"session_id": 1, "state_id": 10, "year_start": 2024},
}

_SEARCH_RESPONSE: dict[str, Any] = {
    "status": "OK",
    "searchresult": {
        # LegiScan keys results by number ("0","1",…) alongside a "summary" entry,
        # NOT under a "results" list.
        "summary": {"page": "1 of 1", "count": 1},
        "0": _BILL_SUMMARY_RAW,
    },
}

_GET_BILL_RESPONSE: dict[str, Any] = {
    "status": "OK",
    "bill": _BILL_RAW,
}


def _make_http_mock(response_json: dict[str, Any], status_code: int = 200) -> ScoutHttpClient:
    """Return a ScoutHttpClient whose inner httpx.AsyncClient is mocked."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_json
    mock_resp.raise_for_status.return_value = None

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.return_value = mock_resp

    http = ScoutHttpClient(rate_limit=1.0, _inner=typing.cast(httpx.AsyncClient, inner))
    return http


def _make_legiscan_client(
    search_response: dict[str, Any] | None = None,
    bill_response: dict[str, Any] | None = None,
    api_key: str = "test-key",
    dry_run: bool = False,
) -> LegiScanClient:
    """Build a LegiScanClient backed by a mock ScoutHttpClient.

    When both search_response and bill_response are needed the mock uses
    side_effect to alternate responses.
    """
    responses = []
    if search_response is not None:
        responses.append(search_response)
    if bill_response is not None:
        responses.append(bill_response)

    if len(responses) == 1:
        http = _make_http_mock(responses[0])
    else:
        # Multiple calls: configure side_effect on inner.request
        mock_resps = []
        for resp_json in responses:
            mock_resp: MagicMock = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = resp_json
            mock_resp.raise_for_status.return_value = None
            mock_resps.append(mock_resp)

        inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
        inner.request.side_effect = mock_resps
        http = ScoutHttpClient(rate_limit=1.0, _inner=typing.cast(httpx.AsyncClient, inner))

    return LegiScanClient(api_key=api_key, dry_run=dry_run, _http=http)


def _make_bill(
    status: int = STATUS_INTRODUCED,
    title: str = "Literacy Act",
    description: str = "A bill about reading.",
    state: str = "FL",
    bill_id: int = 1,
    bill_number: str = "HB 1",
) -> Bill:
    return Bill(
        bill_id=bill_id,
        bill_number=bill_number,
        title=title,
        description=description,
        status=status,
        last_action="Filed",
        state=state,
        body="House",
        session={"session_id": 1, "state_id": 10, "year_start": 2024},
    )


# ===========================================================================
# client.py tests (≥7)
# ===========================================================================


async def test_search_builds_correct_url_params() -> None:
    """search() must include key, op=getSearch, state, and query in request params."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = _SEARCH_RESPONSE
    mock_resp.raise_for_status.return_value = None

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.return_value = mock_resp

    http = ScoutHttpClient(rate_limit=1.0, _inner=typing.cast(httpx.AsyncClient, inner))
    client = LegiScanClient(api_key="mykey", _http=http)

    await client.search("FL", ["literacy", "reading"])

    call_kwargs = inner.request.call_args[1]
    params: dict[str, Any] = call_kwargs["params"]
    assert params["key"] == "mykey"
    assert params["op"] == "getSearch"
    assert params["state"] == "FL"
    assert "literacy" in params["query"]
    assert "reading" in params["query"]


async def test_get_bill_builds_correct_url_params() -> None:
    """get_bill() must send op=getBill and id=bill_id."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = _GET_BILL_RESPONSE
    mock_resp.raise_for_status.return_value = None

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.return_value = mock_resp

    http = ScoutHttpClient(rate_limit=1.0, _inner=typing.cast(httpx.AsyncClient, inner))
    client = LegiScanClient(api_key="mykey", _http=http)

    await client.get_bill(123)

    params: dict[str, Any] = inner.request.call_args[1]["params"]
    assert params["op"] == "getBill"
    assert params["id"] == 123
    assert params["key"] == "mykey"


def test_client_uses_rate_limit_one() -> None:
    """ScoutHttpClient constructed by LegiScanClient must use rate_limit=1.0."""
    # Create a real client (no mock) and inspect the limiter interval.
    # We do this without making any requests.
    client = LegiScanClient(api_key="key")
    # The limiter interval should be 1 / 1.0 = 1.0 seconds
    assert client._http._limiter._interval == pytest.approx(1.0)


async def test_missing_api_key_dry_run_false_raises() -> None:
    """When api_key is empty and dry_run=False, search() must raise ValueError."""
    client = LegiScanClient(api_key="", dry_run=False)
    with pytest.raises(ValueError, match="LEGISCAN_API_KEY"):
        await client.search("FL", ["literacy"])


async def test_missing_api_key_dry_run_true_returns_empty() -> None:
    """When api_key is empty and dry_run=True, search() must return []."""
    client = LegiScanClient(api_key="", dry_run=True)
    result = await client.search("FL", ["literacy"])
    assert result == []


async def test_search_parses_bill_summary_list() -> None:
    """search() must return a list of BillSummary objects from a 200 OK response."""
    client = _make_legiscan_client(search_response=_SEARCH_RESPONSE)
    summaries = await client.search("FL", ["literacy"])
    assert len(summaries) == 1
    assert isinstance(summaries[0], BillSummary)
    assert summaries[0].bill_id == 123
    assert summaries[0].number == "HB 1"  # read from "bill_number" via alias
    assert summaries[0].state == "FL"
    assert summaries[0].status == 0  # getSearch carries no status; defaulted


async def test_get_bill_parses_bill_model() -> None:
    """get_bill() must return a Bill object from a 200 OK getBill response."""
    client = _make_legiscan_client(bill_response=_GET_BILL_RESPONSE)
    bill = await client.get_bill(123)
    assert isinstance(bill, Bill)
    assert bill.bill_id == 123
    assert bill.bill_number == "HB 1"
    assert bill.state == "FL"
    assert bill.status == STATUS_INTRODUCED


async def test_search_returns_empty_on_non_ok_status() -> None:
    """search() must return [] when LegiScan returns status != 'OK'."""
    error_response: dict[str, Any] = {"status": "ERROR", "alert": {"message": "Too many requests"}}
    client = _make_legiscan_client(search_response=error_response)
    result = await client.search("FL", ["literacy"])
    assert result == []


# ===========================================================================
# mapping.py tests (≥7)
# ===========================================================================


def test_mapping_bill_introduced_reason_code() -> None:
    """Status 1 (Introduced) should yield BILL_INTRODUCED reason code."""
    bill = _make_bill(status=STATUS_INTRODUCED)
    finding = bill_to_finding(bill, "FL")
    assert "BILL_INTRODUCED" in finding["reasonCodes"]


def test_mapping_bill_passed_chamber_for_status_3() -> None:
    """Status 3 (Enrolled/passed chamber) should yield BILL_PASSED_CHAMBER."""
    bill = _make_bill(status=STATUS_ENROLLED)
    finding = bill_to_finding(bill, "FL")
    assert "BILL_PASSED_CHAMBER" in finding["reasonCodes"]


def test_mapping_bill_enacted_for_status_4_plus() -> None:
    """Status 4+ should yield BILL_ENACTED reason code."""
    bill = _make_bill(status=STATUS_PASSED)
    finding = bill_to_finding(bill, "FL")
    assert "BILL_ENACTED" in finding["reasonCodes"]


def test_mapping_dyslexia_mandate_when_dyslexia_in_title() -> None:
    """STATE_DYSLEXIA_MANDATE should appear when 'dyslexia' is in the title."""
    bill = _make_bill(title="Dyslexia Screening Requirement Act", description="A screening bill.")
    finding = bill_to_finding(bill, "FL")
    assert "STATE_DYSLEXIA_MANDATE" in finding["reasonCodes"]


def test_mapping_obc_legislation_when_outcomes_based_in_title() -> None:
    """STATE_OBC_LEGISLATION should appear when 'outcomes-based' is in the title."""
    bill = _make_bill(title="Outcomes-Based Contract Framework Act")
    finding = bill_to_finding(bill, "FL")
    assert "STATE_OBC_LEGISLATION" in finding["reasonCodes"]


def test_mapping_urgency_hot_for_status_3_plus() -> None:
    """Urgency should be 'hot' for bills with status >= 3."""
    bill = _make_bill(status=STATUS_ENROLLED)
    finding = bill_to_finding(bill, "FL")
    assert finding["urgency"] == "hot"


def test_mapping_urgency_enrichment_for_status_1_no_content_keywords() -> None:
    """Urgency should be 'standard' for status=1 (introduced)."""
    bill = _make_bill(
        status=STATUS_INTRODUCED,
        title="A Bill About Schools",
        description="General education bill.",
    )
    finding = bill_to_finding(bill, "FL")
    # Status 1 = standard (not enrichment — enrichment is the fallback)
    assert finding["urgency"] == "standard"


def test_mapping_district_id_uses_state_upper() -> None:
    """districtId should be 'STATE_FL' for state='FL'."""
    bill = _make_bill(state="FL")
    finding = bill_to_finding(bill, "FL")
    assert finding["districtId"] == "STATE_FL"


def test_mapping_district_id_lowercase_state_upcased() -> None:
    """districtId should uppercase a lowercase state argument."""
    bill = _make_bill(state="tx")
    finding = bill_to_finding(bill, "tx")
    assert finding["districtId"] == "STATE_TX"


def test_mapping_biliteracy_reason_code() -> None:
    """STATE_BILITERACY_INITIATIVE should appear when 'biliteracy' in description."""
    bill = _make_bill(description="Establishes a biliteracy seal program.")
    finding = bill_to_finding(bill, "CA")
    assert "STATE_BILITERACY_INITIATIVE" in finding["reasonCodes"]


def test_mapping_discovered_by_is_legislative_scout() -> None:
    """discoveredBy must always be 'legislative_scout'."""
    finding = bill_to_finding(_make_bill(), "FL")
    assert finding["discoveredBy"] == "legislative_scout"


def test_mapping_source_type_is_legiscan() -> None:
    """sourceType must always be 'legiscan'."""
    finding = bill_to_finding(_make_bill(), "FL")
    assert finding["sourceType"] == "legiscan"


def test_mapping_metadata_contains_bill_id_and_state() -> None:
    """metadata must include bill_id, bill_number, state, status_code."""
    bill = _make_bill(bill_id=42, bill_number="SB 100", status=STATUS_INTRODUCED)
    finding = bill_to_finding(bill, "TX")
    meta = finding["metadata"]
    assert meta["bill_id"] == 42
    assert meta["bill_number"] == "SB 100"
    assert meta["state"] == "TX"
    assert meta["status_code"] == STATUS_INTRODUCED


# ===========================================================================
# scout.py tests (≥6)
# ===========================================================================


async def test_gather_findings_empty_api_key_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_gather_findings() must return [] when api_key is not set.

    The scout falls back to ``LEGISCAN_API_KEY`` when ``api_key=""``, so clear it
    to exercise the genuinely-no-key path (otherwise a loaded .env makes a live call).
    """
    monkeypatch.delenv("LEGISCAN_API_KEY", raising=False)
    scout = LegislativeScout(ScoutConfig(), api_key="")
    findings = await scout._gather_findings()
    assert findings == []


async def test_gather_findings_calls_search_for_each_priority_state() -> None:
    """_gather_findings() must call client.search() once per priority state."""
    mock_client = AsyncMock(spec=LegiScanClient)
    mock_client.search.return_value = []

    scout = LegislativeScout(
        ScoutConfig(),
        api_key="test-key",
        priority_states=["FL", "TX", "CA"],
        _client=typing.cast(LegiScanClient, mock_client),
    )
    await scout._gather_findings()

    assert mock_client.search.call_count == 3
    states_called = [call.args[0] for call in mock_client.search.call_args_list]
    assert set(states_called) == {"FL", "TX", "CA"}


async def test_gather_findings_skips_state_on_exception() -> None:
    """A per-state exception must be caught; collection continues for other states."""
    mock_client = AsyncMock(spec=LegiScanClient)
    # FL raises, TX succeeds with empty results
    mock_client.search.side_effect = [RuntimeError("API failure"), []]

    scout = LegislativeScout(
        ScoutConfig(),
        api_key="test-key",
        priority_states=["FL", "TX"],
        _client=typing.cast(LegiScanClient, mock_client),
    )
    findings = await scout._gather_findings()
    # Should not raise; TX returned []
    assert findings == []
    assert mock_client.search.call_count == 2


async def test_run_once_calls_emit_signals_with_findings() -> None:
    """run_once() must call emit_signals when _gather_findings returns results."""
    bill = _make_bill(status=STATUS_INTRODUCED)
    mock_client = AsyncMock(spec=LegiScanClient)
    mock_client.search.return_value = [
        BillSummary(
            bill_id=1,
            number="HB 1",
            title="Literacy Act",
            status=STATUS_INTRODUCED,
        )
    ]
    mock_client.get_bill.return_value = bill

    # Mock the emit_signals call via the BaseScout _client (httpx)
    emit_resp: MagicMock = MagicMock(spec=httpx.Response)
    emit_resp.status_code = 200
    emit_resp.json.return_value = {
        "runId": "r1",
        "status": "ok",
        "createdCount": 1,
        "skippedCount": 0,
        "errors": [],
    }
    emit_resp.raise_for_status.return_value = None
    http_client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    http_client.post.return_value = emit_resp

    scout = LegislativeScout(
        ScoutConfig(api_url="http://localhost:8000"),
        api_key="test-key",
        priority_states=["FL"],
        _client=typing.cast(LegiScanClient, mock_client),
    )
    scout._client = typing.cast(httpx.AsyncClient, http_client)

    result = await scout.run_once()
    assert result.status == "ok"
    assert http_client.post.called


async def test_findings_include_discovered_by_legislative_scout() -> None:
    """Every finding from _gather_findings must have discoveredBy='legislative_scout'."""
    bill = _make_bill(status=STATUS_INTRODUCED)
    mock_client = AsyncMock(spec=LegiScanClient)
    mock_client.search.return_value = [
        BillSummary(
            bill_id=1,
            number="HB 1",
            title="Literacy Act",
            status=STATUS_INTRODUCED,
        )
    ]
    mock_client.get_bill.return_value = bill

    scout = LegislativeScout(
        ScoutConfig(),
        api_key="test-key",
        priority_states=["FL"],
        _client=typing.cast(LegiScanClient, mock_client),
    )
    findings = await scout._gather_findings()
    assert len(findings) == 1
    assert findings[0]["discoveredBy"] == "legislative_scout"


async def test_emit_signals_payload_has_correct_scout_type() -> None:
    """emit_signals payload must set scoutType to 'legislative_scout'."""
    emit_resp: MagicMock = MagicMock(spec=httpx.Response)
    emit_resp.status_code = 200
    emit_resp.json.return_value = {
        "runId": "r2",
        "status": "ok",
        "createdCount": 1,
        "skippedCount": 0,
        "errors": [],
    }
    emit_resp.raise_for_status.return_value = None
    http_client: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    http_client.post.return_value = emit_resp

    scout = LegislativeScout(ScoutConfig(api_url="http://localhost:8000"), api_key="key")
    scout._client = typing.cast(httpx.AsyncClient, http_client)

    findings = [{"discoveredBy": "legislative_scout", "sourceType": "legiscan"}]
    await scout.emit_signals(findings)

    payload: dict[str, Any] = http_client.post.call_args[1]["json"]
    assert payload["scoutType"] == "legislative_scout"


def test_legislative_scout_scout_type_class_var() -> None:
    """LegislativeScout.scout_type class variable must equal 'legislative_scout'."""
    assert LegislativeScout.scout_type == "legislative_scout"


async def test_gather_findings_passes_keywords_to_search() -> None:
    """_gather_findings() must forward keywords to client.search()."""
    mock_client = AsyncMock(spec=LegiScanClient)
    mock_client.search.return_value = []

    custom_keywords = ["dyslexia", "reading"]
    scout = LegislativeScout(
        ScoutConfig(),
        api_key="test-key",
        priority_states=["FL"],
        keywords=custom_keywords,
        _client=typing.cast(LegiScanClient, mock_client),
    )
    await scout._gather_findings()

    _, called_keywords = mock_client.search.call_args[0][:2]
    assert called_keywords == custom_keywords
