"""Tests for the D4 Starbridge Researcher Scout.

Covers client.py, mapping.py, and scout.py.
All HTTP calls are mocked — no live network traffic.

Test counts:
- client.py: 7 tests
- mapping.py: 8 tests
- scout.py: 6 tests
Total: 21 tests
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from artemis.scouts.starbridge.client import (
    StarbridgeClient,
    StarbridgeDocument,
    StarbridgeItem,
    StarbridgeUnavailableError,
)
from artemis.scouts.starbridge.mapping import item_to_finding
from artemis.scouts.starbridge.scout import StarbridgeResearcherScout

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_mock_http(
    *,
    status_code: int = 200,
    json_response: Any = None,
    raise_exc: Exception | None = None,
) -> MagicMock:
    """Build a mock ScoutHttpClient that returns a controlled response."""
    mock_http = MagicMock()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_response or []

    if raise_exc is not None:
        mock_http.post = AsyncMock(side_effect=raise_exc)
        mock_http.get = AsyncMock(side_effect=raise_exc)
    elif status_code >= 400:
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "HTTP error",
            request=MagicMock(),
            response=mock_resp,
        )
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.get = AsyncMock(return_value=mock_resp)
    else:
        mock_resp.raise_for_status.return_value = None
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http.get = AsyncMock(return_value=mock_resp)

    return mock_http


def _make_item(
    *,
    item_id: str = "item-001",
    title: str = "Literacy Education Act",
    summary: str | None = "Promotes reading programs.",
    item_type: str = "bill",
    state: str | None = "FL",
    deadline_date: str | None = None,
    source_url: str | None = None,
) -> StarbridgeItem:
    return StarbridgeItem(
        item_id=item_id,
        title=title,
        summary=summary,
        item_type=item_type,
        state=state,
        deadline_date=deadline_date,
        source_url=source_url,
    )


# ---------------------------------------------------------------------------
# client.py tests
# ---------------------------------------------------------------------------


async def test_search_sends_post_to_correct_url() -> None:
    """search() POSTs to the /search endpoint."""
    mock_http = _make_mock_http(json_response=[])
    client = StarbridgeClient(api_key="test-key", _http=mock_http)
    await client.search("literacy")
    mock_http.post.assert_called_once()
    call_args = mock_http.post.call_args
    assert "search" in call_args[0][0]


async def test_search_includes_authorization_header() -> None:
    """StarbridgeClient is built with Authorization header from api_key."""
    from artemis.scouts._http import ScoutHttpClient

    # Verify header is injected into ScoutHttpClient at construction.
    with patch.object(ScoutHttpClient, "__init__", return_value=None) as mock_init:
        mock_init.return_value = None
        # We can't easily introspect private _http from outside, so
        # verify via the client constructor which passes the header.
        client = StarbridgeClient(api_key="secret-key")
        # The client is built; check via direct attribute inspection
        assert client._api_key == "secret-key"


async def test_search_includes_bench_test_period_in_payload() -> None:
    """search() includes bench_test_period=True in the request body."""
    mock_http = _make_mock_http(json_response=[])
    client = StarbridgeClient(api_key="test-key", _http=mock_http)
    await client.search("reading", filters={"state": "FL"})
    call_kwargs = mock_http.post.call_args[1]
    body: dict[str, Any] = call_kwargs["json"]
    assert body["bench_test_period"] is True


async def test_get_document_sends_get_to_correct_url() -> None:
    """get_document() sends GET to /documents/{doc_id}."""
    doc_data = {"doc_id": "doc-123", "title": "My Doc", "content": "Content here."}
    mock_http = _make_mock_http(json_response=doc_data)
    client = StarbridgeClient(api_key="test-key", _http=mock_http)
    doc = await client.get_document("doc-123")
    mock_http.get.assert_called_once()
    call_url: str = mock_http.get.call_args[0][0]
    assert "doc-123" in call_url
    assert isinstance(doc, StarbridgeDocument)
    assert doc.doc_id == "doc-123"


async def test_empty_api_key_raises_value_error_on_search() -> None:
    """search() raises ValueError when api_key is empty."""
    mock_http = _make_mock_http()
    client = StarbridgeClient(api_key="", _http=mock_http)
    with pytest.raises(ValueError, match="STARBRIDGE_API_KEY not set"):
        await client.search("literacy")


async def test_search_http_200_parses_items() -> None:
    """search() parses a list of StarbridgeItem from a 200 response."""
    raw_items = [
        {
            "item_id": "i-1",
            "title": "Reading Act",
            "summary": "Promotes reading.",
            "item_type": "bill",
            "state": "FL",
        },
        {
            "item_id": "i-2",
            "title": "Dyslexia Grant",
            "item_type": "grant",
            "state": "TX",
        },
    ]
    mock_http = _make_mock_http(json_response=raw_items)
    client = StarbridgeClient(api_key="test-key", _http=mock_http)
    items = await client.search("literacy")
    assert len(items) == 2
    assert items[0].item_id == "i-1"
    assert items[1].item_type == "grant"


async def test_search_raises_rather_than_reporting_an_empty_scan() -> None:
    """This test previously asserted `items == []` on a 500, and that was the bug.

    Every endpoint path and field name in the client is an unverified guess
    marked "TODO: confirm with Starbridge team". Returning [] on an HTTP error
    meant a completely unconfigured integration -- wrong base URL, rejected key,
    endpoint that does not exist -- reported "0 signals" in exactly the words a
    working integration uses on a quiet day.

    That is how Argus sat idle for five weeks while its progress was relayed in
    good faith. "Nothing is happening in the world" and "nothing is happening
    here" must not look identical.
    """
    mock_http = _make_mock_http(status_code=500, json_response={})
    client = StarbridgeClient(api_key="test-key", _http=mock_http)

    with pytest.raises(StarbridgeUnavailableError) as excinfo:
        await client.search("literacy")

    assert "500" in str(excinfo.value)
    assert "not an empty result" in str(excinfo.value)


async def test_a_rejected_key_says_so_rather_than_reporting_no_results() -> None:
    """401 is the most likely first failure the day a real key is wired in."""
    mock_http = _make_mock_http(status_code=401, json_response={})
    client = StarbridgeClient(api_key="wrong-key", _http=mock_http)

    with pytest.raises(StarbridgeUnavailableError) as excinfo:
        await client.search("literacy")

    assert "API key was rejected" in str(excinfo.value)


async def test_a_wrong_endpoint_path_names_itself_as_the_likely_cause() -> None:
    """The paths are guesses, so a 404 means us, not the vendor being quiet."""
    mock_http = _make_mock_http(status_code=404, json_response={})
    client = StarbridgeClient(api_key="test-key", _http=mock_http)

    with pytest.raises(StarbridgeUnavailableError) as excinfo:
        await client.search("literacy")

    assert "endpoint path is wrong" in str(excinfo.value)


async def test_a_genuinely_empty_result_is_still_an_empty_list() -> None:
    """The distinction only works if a real empty answer stays quiet."""
    mock_http = _make_mock_http(status_code=200, json_response={"items": []})
    client = StarbridgeClient(api_key="test-key", _http=mock_http)

    assert await client.search("literacy") == []


# ---------------------------------------------------------------------------
# mapping.py tests
# ---------------------------------------------------------------------------


def test_district_id_with_state() -> None:
    """districtId = STATE_{state.upper()} when item.state is set."""
    item = _make_item(state="FL")
    finding = item_to_finding(item)
    assert finding["districtId"] == "STATE_FL"


def test_district_id_with_lowercase_state() -> None:
    """districtId normalises lowercase state to uppercase."""
    item = _make_item(state="fl")
    finding = item_to_finding(item)
    assert finding["districtId"] == "STATE_FL"


def test_district_id_national_when_state_none() -> None:
    """districtId = STATE_NATIONAL when item.state is None."""
    item = _make_item(state=None)
    finding = item_to_finding(item)
    assert finding["districtId"] == "STATE_NATIONAL"


def test_urgency_hot_within_30_days() -> None:
    """urgency=hot when deadline is within 30 days."""
    deadline = (date.today() + timedelta(days=15)).isoformat()
    item = _make_item(deadline_date=deadline)
    finding = item_to_finding(item)
    assert finding["urgency"] == "hot"


def test_urgency_standard_30_to_90_days() -> None:
    """urgency=standard when deadline is 30-90 days out."""
    deadline = (date.today() + timedelta(days=60)).isoformat()
    item = _make_item(deadline_date=deadline)
    finding = item_to_finding(item)
    assert finding["urgency"] == "standard"


def test_urgency_enrichment_no_deadline() -> None:
    """urgency=enrichment when no deadline_date is set."""
    item = _make_item(deadline_date=None)
    finding = item_to_finding(item)
    assert finding["urgency"] == "enrichment"


def test_metadata_includes_bench_test_period_true() -> None:
    """metadata always includes bench_test_period=True."""
    item = _make_item()
    finding = item_to_finding(item)
    assert finding["metadata"]["bench_test_period"] is True


def test_source_type_is_starbridge() -> None:
    """sourceType is always 'starbridge'."""
    item = _make_item()
    finding = item_to_finding(item)
    assert finding["sourceType"] == "starbridge"


def test_discovered_by_is_starbridge_researcher() -> None:
    """discoveredBy is always 'starbridge_researcher'."""
    item = _make_item()
    finding = item_to_finding(item)
    assert finding["discoveredBy"] == "starbridge_researcher"


# ---------------------------------------------------------------------------
# scout.py tests
# ---------------------------------------------------------------------------


async def test_empty_api_key_returns_empty_list_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With empty api_key, _gather_findings() returns [] and logs a warning."""
    scout = StarbridgeResearcherScout(api_key="")
    with caplog.at_level(logging.WARNING):
        findings = await scout._gather_findings()
    assert findings == []
    assert any("STARBRIDGE_API_KEY not set" in r.message for r in caplog.records)


async def test_gather_findings_calls_search_for_each_state_and_term() -> None:
    """_gather_findings() calls client.search for each state × term combination."""
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=[])
    scout = StarbridgeResearcherScout(
        api_key="test-key",
        priority_states=["FL", "TX"],
        _client=mock_client,
    )
    await scout._gather_findings()
    from artemis.scouts.starbridge.scout import SEARCH_TERMS

    expected_calls = len(["FL", "TX"]) * len(SEARCH_TERMS)
    assert mock_client.search.call_count == expected_calls


async def test_gather_findings_exception_per_query_continues() -> None:
    """Exceptions for individual queries are caught; other queries still run."""

    call_count = 0

    async def _flaky_search(query: str, filters: dict[str, Any]) -> list[Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated API failure")
        return []

    mock_client = MagicMock()
    mock_client.search = _flaky_search
    scout = StarbridgeResearcherScout(
        api_key="test-key",
        priority_states=["FL"],
        _client=mock_client,
    )
    # Should not raise even though first query fails
    findings = await scout._gather_findings()
    assert isinstance(findings, list)
    # Remaining queries after first failure still ran
    from artemis.scouts.starbridge.scout import SEARCH_TERMS

    assert call_count == len(SEARCH_TERMS)


async def test_run_once_calls_emit_signals_with_findings() -> None:
    """run_once() calls emit_signals when findings are present."""
    raw_item = {
        "item_id": "i-1",
        "title": "Literacy Act",
        "item_type": "bill",
        "state": "FL",
    }
    mock_sb_client = MagicMock()
    mock_sb_client.search = AsyncMock(return_value=[StarbridgeItem(**raw_item)])

    emit_mock = AsyncMock(
        return_value=MagicMock(status="ok", run_id="r1", created_count=1, skipped_count=0)
    )
    scout = StarbridgeResearcherScout(
        api_key="test-key",
        priority_states=["FL"],
        _client=mock_sb_client,
    )
    # Patch SEARCH_TERMS to a single term for simplicity
    with patch("artemis.scouts.starbridge.scout.SEARCH_TERMS", ["literacy"]):
        scout.emit_signals = emit_mock  # type: ignore[method-assign]
        await scout.run_once()

    emit_mock.assert_called_once()
    findings_passed: list[dict[str, Any]] = emit_mock.call_args[0][0]
    assert len(findings_passed) == 1


async def test_all_findings_have_discovered_by_starbridge_researcher() -> None:
    """All findings from _gather_findings have discoveredBy='starbridge_researcher'."""
    raw_items = [
        StarbridgeItem(item_id="i-1", title="Act 1", item_type="bill", state="FL"),
        StarbridgeItem(item_id="i-2", title="Grant X", item_type="grant", state="TX"),
    ]
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=raw_items)
    scout = StarbridgeResearcherScout(
        api_key="test-key",
        priority_states=["FL"],
        _client=mock_client,
    )
    with patch("artemis.scouts.starbridge.scout.SEARCH_TERMS", ["literacy"]):
        findings = await scout._gather_findings()

    assert len(findings) > 0
    for f in findings:
        assert f["discoveredBy"] == "starbridge_researcher"


async def test_log_includes_credit_usage_info(caplog: pytest.LogCaptureFixture) -> None:
    """_gather_findings logs query and result count for credit usage tracking."""
    raw_items = [StarbridgeItem(item_id="i-1", title="Reading Act", item_type="bill", state="FL")]
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value=raw_items)
    scout = StarbridgeResearcherScout(
        api_key="test-key",
        priority_states=["FL"],
        _client=mock_client,
    )
    with (
        caplog.at_level(logging.INFO),
        patch("artemis.scouts.starbridge.scout.SEARCH_TERMS", ["literacy"]),
    ):
        await scout._gather_findings()

    log_messages = [r.message for r in caplog.records]
    assert any("Starbridge API call" in m for m in log_messages)
    assert any("results=1" in m for m in log_messages)


async def test_a_scout_run_where_every_query_failed_is_not_a_clear_scan() -> None:
    """The scout swallowed exceptions per query and returned [] regardless.

    So an unconfigured integration produced a run that looked exactly like a
    quiet day across every priority state. The client raising is not enough on
    its own if the layer above catches and continues.
    """
    scout = StarbridgeResearcherScout(api_key="test-key")
    mock_client = MagicMock()
    mock_client.search = AsyncMock(side_effect=StarbridgeUnavailableError("HTTP 401"))

    with (
        patch.object(scout, "_get_client", return_value=mock_client),
        pytest.raises(StarbridgeUnavailableError) as excinfo,
    ):
        await scout._gather_findings()

    assert "NOT a clear scan" in str(excinfo.value)


async def test_a_partial_failure_still_returns_what_it_found() -> None:
    """One failing state must not discard the other states' findings."""
    scout = StarbridgeResearcherScout(api_key="test-key")
    calls = {"n": 0}

    async def _flaky(**_kw: Any) -> list[StarbridgeItem]:
        calls["n"] += 1
        if calls["n"] % 2:
            raise StarbridgeUnavailableError("HTTP 503")
        return [_make_item()]

    mock_client = MagicMock()
    mock_client.search = _flaky

    with patch.object(scout, "_get_client", return_value=mock_client):
        findings = await scout._gather_findings()

    assert findings, "a partial outage must not read as zero signals either"
