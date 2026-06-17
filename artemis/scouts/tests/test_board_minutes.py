"""Tests for the D6 Board Minutes Scout.

All external I/O is mocked — no real HTTP, no real Playwright, no pypdfium2.

Coverage:
- mapping.py (≥10 tests)
- client.py (≥3 tests)
- scout.py (≥10 tests)
"""

from __future__ import annotations

import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import ScoutConfig
from artemis.scouts.board_minutes.client import fetch_boarddocs, fetch_granicus
from artemis.scouts.board_minutes.mapping import meeting_item_to_finding
from artemis.scouts.board_minutes.scout import (
    _DEFAULT_WATCH_LIST,
    BoardMinutesScout,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_PINELLAS: dict[str, Any] = {
    "district_id": "FL_pinellas",
    "state": "FL",
    "boarddocs_url": "https://go.boarddocs.com/fl/pinellas/Board.nsf/Public",
    "granicus_url": None,
    "district_site_url": None,
}

_DALLAS: dict[str, Any] = {
    "district_id": "TX_dallas",
    "state": "TX",
    "boarddocs_url": "https://go.boarddocs.com/tx/dallasisd/Board.nsf/Public",
    "granicus_url": "https://dallasisd.granicus.com/ViewPublisher.php?view_id=3",
    "district_site_url": None,
}

_BALTIMORE: dict[str, Any] = {
    "district_id": "MD_baltimore_city",
    "state": "MD",
    "boarddocs_url": None,
    "granicus_url": "https://baltimorecity.granicus.com/ViewPublisher.php?view_id=4",
    "district_site_url": "https://www.baltimorecityschools.org/board-education/board-minutes",
}


def _make_item(
    title: str = "Regular Board Meeting",
    text: str = "The board discussed literacy curriculum.",
    date: str = "2025-01-15",
    source_url: str = "https://go.boarddocs.com/fl/pinellas/minutes.pdf",
    speaker_attribution: str | None = "Supt. Jane Smith, 2025-01-15 board meeting",
) -> dict[str, Any]:
    return {
        "title": title,
        "text": text,
        "date": date,
        "source_url": source_url,
        "speaker_attribution": speaker_attribution,
    }


def _make_http_mock(
    html: str = "",
    status_code: int = 200,
    content: bytes = b"",
) -> tuple[ScoutHttpClient, AsyncMock]:
    """Return a (ScoutHttpClient, inner_mock) pair for assertion."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = html
    mock_resp.content = content
    mock_resp.raise_for_status.return_value = None

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.return_value = mock_resp

    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))
    return http, inner


# ===========================================================================
# mapping.py tests (≥10)
# ===========================================================================


def test_meeting_item_to_finding_rfp_approved_hot() -> None:
    """'rfp authorized' for literacy → PROCUREMENT_LITERACY_RFP + urgency hot."""
    item = _make_item(text="The board approved rfp authorization for literacy vendor.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "PROCUREMENT_LITERACY_RFP" in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_meeting_item_to_finding_obc_discussion_standard() -> None:
    """'obc' discussion → PROCUREMENT_ELA_ADOPTION + standard urgency (OBC = adoption intent)."""
    item = _make_item(text="The board discussed the obc literacy contract proposal.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "PROCUREMENT_ELA_ADOPTION" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_meeting_item_to_finding_superintendent_transition_not_relevant() -> None:
    """'superintendent transition' without literacy keywords → filtered (handled by leadership scout)."""
    item = _make_item(text="The board reviewed superintendent transition plans for the district.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    # Superintendent transitions without literacy content are out of scope for this scout.
    assert finding is None


def test_meeting_item_to_finding_esser_reference() -> None:
    """'esser' + reading context → DISTRICT_STRATEGIC_LITERACY enrichment (budget context signal)."""
    item = _make_item(text="Discussion of esser fund expiration impact on reading programs.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "DISTRICT_STRATEGIC_LITERACY" in finding["reasonCodes"]
    assert finding["urgency"] == "enrichment"


def test_meeting_item_to_finding_vendor_review() -> None:
    """'vendor review' → VENDOR_DISSATISFACTION + standard (pre-RFP intent signal)."""
    item = _make_item(text="Annual vendor review for tutoring and reading curriculum.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "VENDOR_DISSATISFACTION" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_meeting_item_to_finding_budget_pressure() -> None:
    """'budget' + reading program → PROCUREMENT_ELA_ADOPTION (reading program budget = adoption context)."""
    item = _make_item(text="Board discussed budget cut impacts on reading programs.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    # Budget impacts on reading programs are an ELA adoption / procurement intent signal.
    assert finding["reasonCodes"][0] in ("PROCUREMENT_ELA_ADOPTION", "DISTRICT_STRATEGIC_LITERACY")


def test_meeting_item_to_finding_irrelevant_returns_none() -> None:
    """Items with no literacy keywords must return None."""
    item = _make_item(
        title="Facilities Update",
        text="The board approved new parking lot construction at Central High.",
    )
    result = meeting_item_to_finding(item, _PINELLAS)
    assert result is None


def test_meeting_item_to_finding_district_id() -> None:
    """districtId in finding must come from district['district_id']."""
    item = _make_item()
    finding = meeting_item_to_finding(item, _DALLAS)
    assert finding is not None
    assert finding["districtId"] == "TX_dallas"


def test_meeting_item_to_finding_speaker_attribution_in_metadata() -> None:
    """speaker_attribution must be present in finding metadata."""
    item = _make_item(speaker_attribution="Supt. Jane Smith, 2025-01-15 board meeting")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert (
        finding["metadata"]["speaker_attribution"] == "Supt. Jane Smith, 2025-01-15 board meeting"
    )


def test_meeting_item_to_finding_unknown_speaker_fallback() -> None:
    """None speaker_attribution → 'Board agenda item, <date> board meeting'."""
    item = _make_item(speaker_attribution=None, date="2025-03-10")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    attr: str = finding["metadata"]["speaker_attribution"]
    # Fallback is "Board agenda item, <date> board meeting" (not "Unknown speaker,")
    assert "2025-03-10" in attr
    assert "board meeting" in attr


def test_meeting_item_to_finding_source_type_field() -> None:
    """metadata must include source_type field."""
    item = _make_item()
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "source_type" in finding["metadata"]


def test_meeting_item_to_finding_default_urgency_literacy_keyword() -> None:
    """A plain 'literacy curriculum' item → DISTRICT_STRATEGIC_LITERACY + standard urgency."""
    item = _make_item(text="Board approved literacy curriculum update for K-3.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "DISTRICT_STRATEGIC_LITERACY" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_meeting_item_to_finding_obc_approved_standard() -> None:
    """'outcomes-based' + 'reading vendor' → PROCUREMENT_ELA_ADOPTION + standard."""
    item = _make_item(text="The board approved the outcomes-based contract with reading vendor.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "PROCUREMENT_ELA_ADOPTION" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_meeting_item_to_finding_state_in_metadata() -> None:
    """metadata must include state from district config."""
    item = _make_item()
    finding = meeting_item_to_finding(item, _DALLAS)
    assert finding is not None
    assert finding["metadata"]["state"] == "TX"


# ===========================================================================
# client.py tests (≥3)
# ===========================================================================


async def test_fetch_boarddocs_calls_http() -> None:
    """fetch_boarddocs must make an HTTP GET to boarddocs_url."""
    html = "<html><body>No PDF links here. Literacy board agenda.</body></html>"
    http, inner_mock = _make_http_mock(html=html)

    items = await fetch_boarddocs(_PINELLAS, http)

    # Should return at least the fallback HTML item.
    assert isinstance(items, list)
    # The inner httpx client must have been called.
    assert inner_mock.request.called


async def test_fetch_boarddocs_returns_empty_on_error() -> None:
    """fetch_boarddocs must return [] when HTTP raises."""
    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)
    inner.request.side_effect = httpx.ConnectError("connection refused")
    http = ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))

    items = await fetch_boarddocs(_PINELLAS, http)
    assert items == []


async def test_fetch_granicus_calls_http() -> None:
    """fetch_granicus must make an HTTP GET to granicus_url."""
    html = "<html><body>Reading curriculum agenda. No PDFs.</body></html>"
    http, inner_mock = _make_http_mock(html=html)

    items = await fetch_granicus(_DALLAS, http)

    assert isinstance(items, list)
    assert inner_mock.request.called


async def test_fetch_boarddocs_returns_empty_for_missing_url() -> None:
    """fetch_boarddocs must return [] when boarddocs_url is None."""
    district: dict[str, Any] = {
        "district_id": "MD_baltimore_city",
        "state": "MD",
        "boarddocs_url": None,
        "granicus_url": "https://example.com",
        "district_site_url": None,
    }
    http, _ = _make_http_mock()
    items = await fetch_boarddocs(district, http)
    assert items == []


# ===========================================================================
# scout.py tests (≥10)
# ===========================================================================


async def test_gather_findings_returns_list() -> None:
    """_gather_findings must return a list (possibly empty)."""

    async def _fake_fetch_items(district: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    scout = BoardMinutesScout(
        ScoutConfig(),
        watch_list=[_PINELLAS],
    )
    # Replace the bound method at instance level with a compatible one.
    scout._fetch_district_items = _fake_fetch_items  # type: ignore[method-assign]
    findings = await scout._gather_findings()
    assert isinstance(findings, list)


async def test_gather_findings_deduplicates_by_url() -> None:
    """Duplicate (district_id, source_url) pairs must appear only once."""
    dup_url = "https://go.boarddocs.com/fl/pinellas/minutes.pdf"
    duplicate_items = [
        _make_item(text="literacy rfp authorized budget", source_url=dup_url),
        _make_item(text="literacy rfp authorized budget", source_url=dup_url),
    ]

    async def _fake_fetch_items(district: dict[str, Any]) -> list[dict[str, Any]]:
        return duplicate_items

    scout = BoardMinutesScout(ScoutConfig(), watch_list=[_PINELLAS])
    scout._fetch_district_items = _fake_fetch_items  # type: ignore[method-assign]

    findings = await scout._gather_findings()
    urls = [f["metadata"]["source_url"] for f in findings]
    assert urls.count(dup_url) <= 1


async def test_gather_findings_prefers_boarddocs_over_granicus() -> None:
    """When BoardDocs returns items, Granicus fetcher must NOT be called."""
    boarddocs_called: list[bool] = []
    granicus_called: list[bool] = []

    async def _fake_boarddocs(
        district: dict[str, Any],
        http: ScoutHttpClient,
        pdf_open_fn: Any = None,
    ) -> list[dict[str, Any]]:
        boarddocs_called.append(True)
        return [_make_item(text="literacy rfp approved")]

    async def _fake_granicus(
        district: dict[str, Any],
        http: ScoutHttpClient,
        pdf_open_fn: Any = None,
    ) -> list[dict[str, Any]]:
        granicus_called.append(True)
        return [_make_item(text="granicus literacy item")]

    with (
        patch(
            "artemis.scouts.board_minutes.scout.fetch_boarddocs",
            side_effect=_fake_boarddocs,
        ),
        patch(
            "artemis.scouts.board_minutes.scout.fetch_granicus",
            side_effect=_fake_granicus,
        ),
    ):
        scout = BoardMinutesScout(ScoutConfig(), watch_list=[_DALLAS])
        await scout._gather_findings()

    assert boarddocs_called
    assert not granicus_called


async def test_gather_findings_falls_back_to_granicus() -> None:
    """When BoardDocs returns [], Granicus must be called."""
    granicus_called: list[bool] = []

    async def _fake_boarddocs(
        district: dict[str, Any],
        http: ScoutHttpClient,
        pdf_open_fn: Any = None,
    ) -> list[dict[str, Any]]:
        return []

    async def _fake_granicus(
        district: dict[str, Any],
        http: ScoutHttpClient,
        pdf_open_fn: Any = None,
    ) -> list[dict[str, Any]]:
        granicus_called.append(True)
        return [_make_item(text="literacy granicus fallback")]

    with (
        patch(
            "artemis.scouts.board_minutes.scout.fetch_boarddocs",
            side_effect=_fake_boarddocs,
        ),
        patch(
            "artemis.scouts.board_minutes.scout.fetch_granicus",
            side_effect=_fake_granicus,
        ),
    ):
        scout = BoardMinutesScout(ScoutConfig(), watch_list=[_DALLAS])
        await scout._gather_findings()

    assert granicus_called


async def test_gather_findings_continues_on_district_error() -> None:
    """A per-district exception must not abort collection for other districts."""
    call_count: list[int] = [0]

    async def _fake_fetch_items(district: dict[str, Any]) -> list[dict[str, Any]]:
        call_count[0] += 1
        if district["district_id"] == "FL_pinellas":
            raise RuntimeError("Network error")
        return [_make_item(text="literacy curriculum reading")]

    scout = BoardMinutesScout(ScoutConfig(), watch_list=[_PINELLAS, _DALLAS])
    scout._fetch_district_items = _fake_fetch_items  # type: ignore[method-assign]

    findings = await scout._gather_findings()
    # Both districts were attempted.
    assert call_count[0] == 2
    # The Dallas district's finding survived.
    assert len(findings) >= 1


async def test_gather_findings_skips_none_mappings() -> None:
    """Items that map to None (irrelevant) must not appear in findings."""

    async def _fake_fetch_items(district: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _make_item(title="Parking lot update", text="New asphalt approved for lot B."),
        ]

    scout = BoardMinutesScout(ScoutConfig(), watch_list=[_PINELLAS])
    scout._fetch_district_items = _fake_fetch_items  # type: ignore[method-assign]

    findings = await scout._gather_findings()
    assert findings == []


async def test_gather_findings_filters_irrelevant_items() -> None:
    """Non-literacy items are filtered; literacy items are kept."""

    async def _fake_fetch_items(district: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            _make_item(
                title="Grounds Crew Report",
                text="Lawn mowing schedule updated.",
                source_url="https://go.boarddocs.com/fl/pinellas/grounds.pdf",
            ),
            _make_item(
                title="Reading Adoption",
                text="Board approved literacy curriculum.",
                source_url="https://go.boarddocs.com/fl/pinellas/reading.pdf",
            ),
        ]

    scout = BoardMinutesScout(ScoutConfig(), watch_list=[_PINELLAS])
    scout._fetch_district_items = _fake_fetch_items  # type: ignore[method-assign]

    findings = await scout._gather_findings()
    assert len(findings) == 1
    assert (
        "Reading Adoption" in findings[0]["evidence"]
        or "literacy" in findings[0]["evidence"].lower()
    )


def test_default_watch_list_has_entries() -> None:
    """_DEFAULT_WATCH_LIST must be non-empty."""
    assert len(_DEFAULT_WATCH_LIST) > 0


def test_default_watch_list_has_required_keys() -> None:
    """Every entry in _DEFAULT_WATCH_LIST must have district_id and state."""
    for entry in _DEFAULT_WATCH_LIST:
        assert "district_id" in entry, f"Missing district_id in {entry}"
        assert "state" in entry, f"Missing state in {entry}"


def test_board_minutes_scout_type_class_var() -> None:
    """BoardMinutesScout.scout_type must equal 'board_minutes_scout'."""
    assert BoardMinutesScout.scout_type == "board_minutes_scout"


async def test_gather_findings_discovered_by_field() -> None:
    """All findings must have discoveredBy='board_minutes_scout'."""

    async def _fake_fetch_items(district: dict[str, Any]) -> list[dict[str, Any]]:
        return [_make_item(text="literacy rfp approved curriculum")]

    scout = BoardMinutesScout(ScoutConfig(), watch_list=[_PINELLAS])
    scout._fetch_district_items = _fake_fetch_items  # type: ignore[method-assign]

    findings = await scout._gather_findings()
    assert all(f["discoveredBy"] == "board_minutes_scout" for f in findings)


async def test_gather_findings_source_type_field() -> None:
    """All findings must have sourceType='board_minutes'."""

    async def _fake_fetch_items(district: dict[str, Any]) -> list[dict[str, Any]]:
        return [_make_item(text="board reading curriculum assessment")]

    scout = BoardMinutesScout(ScoutConfig(), watch_list=[_PINELLAS])
    scout._fetch_district_items = _fake_fetch_items  # type: ignore[method-assign]

    findings = await scout._gather_findings()
    assert all(f["sourceType"] == "board_minutes" for f in findings)


async def test_gather_findings_vendor_accountability() -> None:
    """'vendor accountability' + literacy → VENDOR_DISSATISFACTION (canonical code)."""

    async def _fake_fetch_items(district: dict[str, Any]) -> list[dict[str, Any]]:
        return [_make_item(text="vendor accountability metrics for literacy software performance.")]

    scout = BoardMinutesScout(ScoutConfig(), watch_list=[_PINELLAS])
    scout._fetch_district_items = _fake_fetch_items  # type: ignore[method-assign]

    findings = await scout._gather_findings()
    assert len(findings) >= 1
    assert "VENDOR_DISSATISFACTION" in findings[0]["reasonCodes"]


# ===========================================================================
# Pre-RFP intent signal tests (new — canonical reason codes)
# ===========================================================================


def test_pre_rfp_strategic_literacy() -> None:
    """Strategic plan with literacy pillar → DISTRICT_STRATEGIC_LITERACY + standard."""
    item = _make_item(text="Board adopts 5-year strategic plan with literacy as top priority.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "DISTRICT_STRATEGIC_LITERACY" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_pre_rfp_proficiency_gap() -> None:
    """State assessment results with reading gap → DISTRICT_PROFICIENCY_GAP + standard."""
    item = _make_item(
        text="Student performance on state assessments shows reading proficiency gap in K-3."
    )
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "DISTRICT_PROFICIENCY_GAP" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_pre_rfp_ela_adoption_committee() -> None:
    """ELA adoption committee → PROCUREMENT_ELA_ADOPTION + standard."""
    item = _make_item(
        text="Board forms ELA adoption committee for K-5 language arts instructional materials."
    )
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "PROCUREMENT_ELA_ADOPTION" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_pre_rfp_tx_hb1416_waiver() -> None:
    """TX HB 1416 tutoring waiver → TX_HB1416_WAIVER + hot."""
    item = _make_item(text="Board approves submission of HB 1416 tutoring waiver to TEA.")
    finding = meeting_item_to_finding(item, _DALLAS)
    assert finding is not None
    assert "TX_HB1416_WAIVER" in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_pre_rfp_mtss_strain() -> None:
    """MTSS staffing shortage discussion → DISTRICT_MTSS_STRAIN + standard."""
    item = _make_item(
        text="Discussion of MTSS intervention staffing shortages for Tier 2 reading support."
    )
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "DISTRICT_MTSS_STRAIN" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_pre_rfp_dll_expansion() -> None:
    """Dual language program expansion → DISTRICT_DLL_EXPANSION + standard."""
    item = _make_item(
        text="Board votes to expand dual language program at three elementary schools."
    )
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "DISTRICT_DLL_EXPANSION" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_pre_rfp_vendor_dissatisfaction_review() -> None:
    """Vendor evaluation/efficacy review → VENDOR_DISSATISFACTION + standard."""
    item = _make_item(text="Board agenda: efficacy review of current reading software vendor.")
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "VENDOR_DISSATISFACTION" in finding["reasonCodes"]
    assert finding["urgency"] == "standard"


def test_pre_rfp_vendor_nrenewal_hot() -> None:
    """Vendor non-renewal vote → VENDOR_DISSATISFACTION + hot."""
    item = _make_item(
        text="Board votes non-renewal of iReady reading software contract for next year."
    )
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    assert "VENDOR_DISSATISFACTION" in finding["reasonCodes"]
    assert finding["urgency"] == "hot"


def test_pre_rfp_esser_context() -> None:
    """ESSER fund expiration with reading context → enrichment (budget context, not hot)."""
    item = _make_item(
        text="Discussion of esser fund expiration and impact on reading intervention programs."
    )
    finding = meeting_item_to_finding(item, _PINELLAS)
    assert finding is not None
    # ESSER is enrichment context — it is not a discrete buying signal on its own.
    assert finding["urgency"] == "enrichment"
