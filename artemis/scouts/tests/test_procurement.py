"""Tests for the D7 Procurement Scout.

All HTTP is mocked — no live network calls are made.

Coverage:
- portals.py (fetch_portal_postings, PORTAL_REGISTRY, LITERACY_KEYWORDS)
- mapping.py (days_to_close, posting_to_finding)
- scout.py (ProcurementScout._gather_findings, scout_type)
"""

from __future__ import annotations

import datetime
import typing
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.base import ScoutConfig
from artemis.scouts.procurement.mapping import days_to_close, posting_to_finding
from artemis.scouts.procurement.portals import (
    PORTAL_REGISTRY,
    fetch_portal_postings,
)
from artemis.scouts.procurement.scout import ProcurementScout

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_LITERACY_HTML = """
<html><body>
<table>
  <tr><td>RFP-001</td><td>Reading Assessment Platform</td><td>Dept of Education</td></tr>
  <tr><td>RFP-002</td><td>Literacy Curriculum Materials</td><td>School District</td></tr>
  <tr><td>RFP-003</td><td>Office Furniture Procurement</td><td>Facilities Dept</td></tr>
</table>
</body></html>
"""

_NON_LITERACY_HTML = """
<html><body>
<table>
  <tr><td>RFP-100</td><td>Office Furniture Purchase</td><td>Facilities</td></tr>
  <tr><td>RFP-101</td><td>Janitorial Services Contract</td><td>Operations</td></tr>
</table>
</body></html>
"""


def _make_http_mock(
    response_text: str = "",
    status_code: int = 200,
    raise_error: Exception | None = None,
) -> ScoutHttpClient:
    """Return a ScoutHttpClient whose inner httpx.AsyncClient is mocked."""
    mock_resp: MagicMock = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = response_text

    inner: AsyncMock = AsyncMock(spec=httpx.AsyncClient)

    if raise_error is not None:
        inner.request.side_effect = raise_error
    else:
        inner.request.return_value = mock_resp

    return ScoutHttpClient(rate_limit=100.0, _inner=typing.cast(httpx.AsyncClient, inner))


def _make_posting(
    portal_id: str = "CA_eprocurement",
    state: str = "CA",
    rfp_id: str = "RFP-001",
    title: str = "Reading Assessment Platform",
    agency: str = "Dept of Education",
    posted_date: str = "",
    due_date: str = "",
    description: str = "Procurement for reading assessment tools.",
    scope_text: str = "",
) -> dict[str, Any]:
    return {
        "portal_id": portal_id,
        "state": state,
        "rfp_id": rfp_id,
        "title": title,
        "agency": agency,
        "posted_date": posted_date,
        "due_date": due_date,
        "source_url": "https://example.com/rfp",
        "description": description,
        "scope_text": scope_text,
    }


# ---------------------------------------------------------------------------
# mapping.py tests
# ---------------------------------------------------------------------------


def test_posting_to_finding_always_hot() -> None:
    """urgency must always be 'hot' for procurement postings."""
    posting = _make_posting()
    finding = posting_to_finding(posting)
    assert finding["urgency"] == "hot"


def test_posting_to_finding_rfp_literacy_always_present() -> None:
    """RFP_LITERACY_POSTED must always be in reasonCodes."""
    posting = _make_posting(title="Literacy Program", description="reading tools")
    finding = posting_to_finding(posting)
    assert "RFP_LITERACY_POSTED" in finding["reasonCodes"]


def test_posting_to_finding_assessment_adds_reason_code() -> None:
    """'assessment' in title/description adds RFP_ASSESSMENT_POSTED."""
    posting = _make_posting(title="Reading Assessment Platform", description="assessment tools")
    finding = posting_to_finding(posting)
    assert "RFP_ASSESSMENT_POSTED" in finding["reasonCodes"]


def test_posting_to_finding_tutoring_adds_reason_code() -> None:
    """'tutoring' in title/description adds RFP_TUTORING_POSTED."""
    posting = _make_posting(title="Literacy Tutoring Services", description="tutoring program")
    finding = posting_to_finding(posting)
    assert "RFP_TUTORING_POSTED" in finding["reasonCodes"]


def test_posting_to_finding_deadline_critical_14_days() -> None:
    """Due in exactly 14 days → RFP_DEADLINE_CRITICAL in reasonCodes."""
    due = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    posting = _make_posting(due_date=due)
    finding = posting_to_finding(posting)
    assert "RFP_DEADLINE_CRITICAL" in finding["reasonCodes"]


def test_posting_to_finding_no_deadline_critical_15_days() -> None:
    """Due in 15 days → no RFP_DEADLINE_CRITICAL."""
    due = (datetime.date.today() + datetime.timedelta(days=15)).isoformat()
    posting = _make_posting(due_date=due)
    finding = posting_to_finding(posting)
    assert "RFP_DEADLINE_CRITICAL" not in finding["reasonCodes"]


def test_posting_to_finding_efficacy_language() -> None:
    """'efficacy' in scope_text → RFP_EFFICACY_LANGUAGE in reasonCodes."""
    posting = _make_posting(scope_text="Vendor must demonstrate program efficacy with data.")
    finding = posting_to_finding(posting)
    assert "RFP_EFFICACY_LANGUAGE" in finding["reasonCodes"]


def test_posting_to_finding_outcomes_based_language() -> None:
    """'outcomes-based' in scope_text → RFP_OUTCOMES_BASED_LANGUAGE in reasonCodes."""
    posting = _make_posting(scope_text="This is an outcomes-based contract requiring results.")
    finding = posting_to_finding(posting)
    assert "RFP_OUTCOMES_BASED_LANGUAGE" in finding["reasonCodes"]


def test_posting_to_finding_district_id() -> None:
    """districtId must be 'STATE_CA' for a CA portal posting."""
    posting = _make_posting(state="CA")
    finding = posting_to_finding(posting)
    assert finding["districtId"] == "STATE_CA"


def test_posting_to_finding_discovered_by() -> None:
    """discoveredBy must be 'procurement_scout'."""
    posting = _make_posting()
    finding = posting_to_finding(posting)
    assert finding["discoveredBy"] == "procurement_scout"


def test_posting_to_finding_metadata_has_rfp_id() -> None:
    """metadata must contain rfp_id."""
    posting = _make_posting(rfp_id="RFP-XYZ")
    finding = posting_to_finding(posting)
    assert finding["metadata"]["rfp_id"] == "RFP-XYZ"


def test_posting_to_finding_metadata_has_days_to_close() -> None:
    """metadata must contain days_to_close (int or None)."""
    due = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    posting = _make_posting(due_date=due)
    finding = posting_to_finding(posting)
    assert "days_to_close" in finding["metadata"]
    assert isinstance(finding["metadata"]["days_to_close"], int)


def test_days_to_close_returns_none_on_bad_date() -> None:
    """Empty string and invalid dates return None."""
    assert days_to_close("") is None
    assert days_to_close("not-a-date") is None
    assert days_to_close("2024-13-99") is None


def test_days_to_close_returns_int() -> None:
    """Valid future date returns a positive integer."""
    future = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    result = days_to_close(future)
    assert isinstance(result, int)
    assert result == 10


# ---------------------------------------------------------------------------
# portals.py tests
# ---------------------------------------------------------------------------


def test_portal_registry_has_state_and_bonfire_portals() -> None:
    """PORTAL_REGISTRY must include statewide portals plus Bonfire entries.

    Phase 1: 9 original statewide portals (html_scrape stubs).
    Phase 2: +2 live adapters — eMMA (emma_MD) and TX ESBD (esbd_TX).
    Total non-Bonfire entries: 11.
    """
    from artemis.scouts.procurement.bonfire import BONFIRE_REGISTRY

    bonfire_keys = {f"bonfire_{e['slug']}" for e in BONFIRE_REGISTRY}
    state_keys = set(PORTAL_REGISTRY.keys()) - bonfire_keys
    # 9 original statewide portals + 2 Phase-2 live adapters (eMMA, ESBD) = 11.
    assert len(state_keys) == 11
    # All active Bonfire districts must have a portal entry.
    assert bonfire_keys.issubset(set(PORTAL_REGISTRY.keys()))
    # Phase-2 adapters must be present.
    assert "emma_MD" in state_keys
    assert "esbd_TX" in state_keys


def test_portal_registry_has_required_states() -> None:
    """PORTAL_REGISTRY must include all 9 required states."""
    states = {v["state"] for v in PORTAL_REGISTRY.values()}
    required = {"CA", "GA", "TX", "FL", "IL", "IN", "MD", "MI", "MO"}
    assert required.issubset(states)


async def test_fetch_portal_postings_filters_non_literacy() -> None:
    """Items without literacy keywords must not be returned."""
    http = _make_http_mock(response_text=_NON_LITERACY_HTML)
    portal = PORTAL_REGISTRY["CA_eprocurement"]
    result = await fetch_portal_postings("CA_eprocurement", portal, http)
    assert result == []


async def test_fetch_portal_postings_returns_empty_on_http_error() -> None:
    """HTTP error → fetch_portal_postings returns empty list (does not raise)."""
    http = _make_http_mock(raise_error=httpx.ConnectError("connection refused"))
    portal = PORTAL_REGISTRY["CA_eprocurement"]
    result = await fetch_portal_postings("CA_eprocurement", portal, http)
    assert result == []


async def test_fetch_portal_postings_returns_list_on_success() -> None:
    """200 OK with literacy HTML → returns a non-empty list of posting dicts."""
    http = _make_http_mock(response_text=_LITERACY_HTML)
    portal = PORTAL_REGISTRY["CA_eprocurement"]
    result = await fetch_portal_postings("CA_eprocurement", portal, http)
    assert isinstance(result, list)
    # At least one literacy-relevant row should be returned
    assert len(result) >= 1
    # Each posting must have the expected keys
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
    }
    for posting in result:
        assert required_keys.issubset(posting.keys())


# ---------------------------------------------------------------------------
# scout.py tests
# ---------------------------------------------------------------------------


def test_procurement_scout_type_class_var() -> None:
    """ProcurementScout.scout_type must be 'procurement_scout'."""
    assert ProcurementScout.scout_type == "procurement_scout"


async def test_gather_findings_returns_list() -> None:
    """_gather_findings() must return a list (minimal mock, no postings)."""
    http = _make_http_mock(response_text=_NON_LITERACY_HTML)
    scout = ProcurementScout(
        ScoutConfig(),
        portals=["CA_eprocurement"],
        _http_client=http,
    )
    findings = await scout._gather_findings()
    assert isinstance(findings, list)


async def test_gather_findings_deduplicates_by_rfp_id() -> None:
    """Same (state, rfp_id) from multiple portals → emitted only once."""
    # Build a mock that returns the same posting from two "different" portals.
    posting_a = _make_posting(portal_id="CA_eprocurement", state="CA", rfp_id="DUP-001")
    posting_b = _make_posting(portal_id="GA_procurement", state="CA", rfp_id="DUP-001")

    async def fake_fetch(
        portal_id: str,
        portal: dict[str, Any],
        http: ScoutHttpClient,
        pdf_open_fn: Any = None,
    ) -> list[dict[str, Any]]:
        if portal_id == "CA_eprocurement":
            return [posting_a]
        if portal_id == "GA_procurement":
            return [posting_b]
        return []

    http = _make_http_mock()
    scout = ProcurementScout(
        ScoutConfig(),
        portals=["CA_eprocurement", "GA_procurement"],
        _http_client=http,
    )

    with patch(
        "artemis.scouts.procurement.scout.fetch_portal_postings",
        side_effect=fake_fetch,
    ):
        findings = await scout._gather_findings()

    # Only one finding despite two portals returning the same (state, rfp_id).
    assert len(findings) == 1


async def test_gather_findings_continues_on_portal_error() -> None:
    """An exception from one portal must not stop collection from remaining portals."""
    call_count = 0

    async def fake_fetch(
        portal_id: str,
        portal: dict[str, Any],
        http: ScoutHttpClient,
        pdf_open_fn: Any = None,
    ) -> list[dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        if portal_id == "CA_eprocurement":
            raise RuntimeError("Portal temporarily down")
        # Second portal returns one literacy posting.
        return [_make_posting(portal_id=portal_id, state="GA", rfp_id="GA-001")]

    http = _make_http_mock()
    scout = ProcurementScout(
        ScoutConfig(),
        portals=["CA_eprocurement", "GA_procurement"],
        _http_client=http,
    )

    with patch(
        "artemis.scouts.procurement.scout.fetch_portal_postings",
        side_effect=fake_fetch,
    ):
        findings = await scout._gather_findings()

    # Both portals were attempted.
    assert call_count == 2
    # One finding from the successful portal.
    assert len(findings) == 1
    assert findings[0]["metadata"]["state"] == "GA"


async def test_gather_findings_all_urgency_hot() -> None:
    """Every finding emitted by _gather_findings must have urgency == 'hot'."""
    posting = _make_posting(state="TX", rfp_id="TX-001")

    async def fake_fetch(
        portal_id: str,
        portal: dict[str, Any],
        http: ScoutHttpClient,
        pdf_open_fn: Any = None,
    ) -> list[dict[str, Any]]:
        return [posting]

    http = _make_http_mock()
    scout = ProcurementScout(
        ScoutConfig(),
        portals=["TX_smartbuy"],
        _http_client=http,
    )

    with patch(
        "artemis.scouts.procurement.scout.fetch_portal_postings",
        side_effect=fake_fetch,
    ):
        findings = await scout._gather_findings()

    assert len(findings) >= 1
    for f in findings:
        assert f["urgency"] == "hot"


def test_posting_to_finding_measurable_growth_in_scope() -> None:
    """'measurable growth' in scope_text → RFP_EFFICACY_LANGUAGE."""
    posting = _make_posting(
        scope_text="Contractor must show measurable growth in student outcomes."
    )
    finding = posting_to_finding(posting)
    assert "RFP_EFFICACY_LANGUAGE" in finding["reasonCodes"]


def test_posting_to_finding_performance_guarantee_in_scope() -> None:
    """'performance guarantee' in scope_text → RFP_OUTCOMES_BASED_LANGUAGE."""
    posting = _make_posting(scope_text="Contract includes a performance guarantee clause.")
    finding = posting_to_finding(posting)
    assert "RFP_OUTCOMES_BASED_LANGUAGE" in finding["reasonCodes"]


def test_posting_to_finding_source_type_is_procurement_portal() -> None:
    """sourceType must be 'procurement_portal'."""
    posting = _make_posting()
    finding = posting_to_finding(posting)
    assert finding["sourceType"] == "procurement_portal"


def test_posting_to_finding_district_id_lowercase_state_upcased() -> None:
    """districtId must uppercase a lowercase state value."""
    posting = _make_posting(state="tx")
    finding = posting_to_finding(posting)
    assert finding["districtId"] == "STATE_TX"


def test_days_to_close_past_date_returns_negative() -> None:
    """A date in the past returns a negative integer (already overdue)."""
    past = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    result = days_to_close(past)
    assert result is not None
    assert result < 0


def test_posting_to_finding_no_reason_codes_duplicated() -> None:
    """Reason codes list must not contain duplicates."""
    due = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    posting = _make_posting(
        title="Reading Assessment and Tutoring",
        description="assessment tutoring literacy",
        due_date=due,
        scope_text="efficacy outcomes-based performance guarantee",
    )
    finding = posting_to_finding(posting)
    codes = finding["reasonCodes"]
    assert len(codes) == len(set(codes)), f"Duplicate reason codes found: {codes}"
