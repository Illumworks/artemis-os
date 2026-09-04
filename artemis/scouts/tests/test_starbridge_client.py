"""The Starbridge client, rewritten against the API that actually exists.

What was here before was fiction: every endpoint and field carried "TODO:
confirm with Starbridge team", and the base URL pointed at api.starbridge.io --
a host that does not resolve, on a domain belonging to someone else. The company
is starbridge.ai. Not one row could ever have come back.

Confirmed 2026-09-04 against the published OpenAPI 3.1 spec and exercised live:
68 bridges, 174,544 rows, 695 signals.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from artemis.scouts.starbridge.client import (
    StarbridgeClient,
    StarbridgeUnavailableError,
    _cell_values,
    signal_to_item,
)


def _signal(
    *, title: str = "Universal Reading Screener RFP", by_id: bool = False, **cells: Any
) -> dict[str, Any]:
    values = {
        "Match Score": 5,
        "Match reasoning": "KSDE is purchasing a statewide universal reading screener.",
        "Summarized Relevance": "- Statewide screener required.\n- PreK-8.",
        "Buyer Name": "Kansas State Department of Education",
        "Source Url": "https://supplier.sok.ks.gov/bid",
        **cells,
    }
    columns = [{"columnId": f"col-{i}", "name": n} for i, n in enumerate(values)]
    keyed = (
        {f"col-{i}": {"value": v} for i, v in enumerate(values.values())}
        if by_id
        else {n: {"value": v} for n, v in values.items()}
    )
    return {
        "bridge": {"name": "RFPs - State & State DOE", "filterType": "RFP", "columns": columns},
        "row": {"rowId": "r-1", "name": title, "columns": keyed},
    }


# ── the join that silently never ran ─────────────────────────────────────────


def test_row_cells_are_read_by_column_name() -> None:
    """Live rows come back keyed by NAME, not columnId."""
    assert _cell_values(_signal())["Buyer Name"] == "Kansas State Department of Education"


def test_id_keyed_rows_are_also_understood() -> None:
    """A debug line printing `names.get(key, key)` fell back to echoing the key.

    That made an id-keyed join look like it worked when it had never run, and
    every field came back None. The spec is explicitly "subject to change before
    finalization of a 1.0", so both spellings are accepted rather than assumed.
    """
    assert _cell_values(_signal(by_id=True))["Buyer Name"] == (
        "Kansas State Department of Education"
    )


def test_a_signal_normalises_to_the_fields_a_finding_needs() -> None:
    item = signal_to_item(_signal())

    assert item.buyer_name == "Kansas State Department of Education"
    assert item.match_score == 5
    assert item.item_type == "rfp"
    assert item.source_url == "https://supplier.sok.ks.gov/bid"
    assert item.summary and "Statewide screener" in item.summary


def test_the_fuller_relevance_text_wins_over_the_one_line_reasoning() -> None:
    item = signal_to_item(_signal())
    assert item.summary and item.summary.startswith("- ")


def test_a_missing_relevance_column_falls_back_rather_than_emptying() -> None:
    item = signal_to_item(_signal(**{"Summarized Relevance": None}))
    assert item.summary == "KSDE is purchasing a statewide universal reading screener."


def test_an_unparseable_score_does_not_crash_the_signal() -> None:
    assert signal_to_item(_signal(**{"Match Score": "n/a"})).match_score is None


# ── duplication is inherent to the feed ──────────────────────────────────────


def _client(payload: Any, status: int = 200) -> StarbridgeClient:
    http = MagicMock()
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = payload
    http.get = AsyncMock(return_value=resp)
    return StarbridgeClient(api_key="k", _http=http)


@pytest.mark.asyncio
async def test_overlapping_bridges_reporting_one_event_yield_one_signal() -> None:
    """50 raw rows carried 28 distinct titles when measured live.

    Without this the same Kansas RFP reaches the queue four times and reads as
    four separate opportunities.
    """
    payload = {"result": [_signal(), _signal(), _signal(title="Hawaii LMS RFP")]}

    items = await _client(payload).top_signals(limit=50)

    assert [i.title for i in items] == ["Universal Reading Screener RFP", "Hawaii LMS RFP"]


@pytest.mark.asyncio
async def test_an_empty_feed_is_an_empty_list_not_an_error() -> None:
    assert await _client({"result": []}).top_signals() == []


# ── unreachable is not quiet ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rejected_key_raises_rather_than_reporting_no_signals() -> None:
    with pytest.raises(StarbridgeUnavailableError) as excinfo:
        await _client({}, status=401).top_signals()

    assert "API key was rejected" in str(excinfo.value)
    assert "not an empty result" in str(excinfo.value)


@pytest.mark.asyncio
async def test_rate_limiting_names_itself() -> None:
    with pytest.raises(StarbridgeUnavailableError) as excinfo:
        await _client({}, status=429).top_signals()
    assert "Rate limited" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_empty_key_is_refused_before_any_request() -> None:
    with pytest.raises(ValueError, match="STARBRIDGE_API_KEY not set"):
        await StarbridgeClient(api_key="", _http=MagicMock()).top_signals()


def test_the_base_url_is_the_company_that_exists() -> None:
    """api.starbridge.io does not resolve, and .io is someone else's domain."""
    from artemis.scouts.starbridge.client import _STARBRIDGE_BASE

    assert "starbridge.ai" in _STARBRIDGE_BASE
    assert "starbridge.io" not in _STARBRIDGE_BASE
