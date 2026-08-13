"""Unit tests for the Argus core loop.

All tests are UNIT tests -- no DB, no env vars, no real HTTP calls.
Tools, the LLM synthesis, and the memory write are all mocked.

Test plan (mirrors the spec):
  D1 -- dispatch_research is present in a callie registry, absent from other agent registries.
  D2 -- research_dimensions calls the right tool fetchers per gap dimension (mocked).
  D3 -- DistrictFindings produced by research_dimensions are tagged source="Argus" (or "Argus/*").
  D4 -- A tool failure on one dimension does not crash the rest of the research.
  D5 -- research_district writes through the drawer (dedup delegated to memory pipeline).
  D6 -- _parse_synthesis_output correctly parses LLM JSON lines into DistrictFindings.
  D7 -- _parse_synthesis_output skips unparseable lines without raising.
  D8 -- research_dimensions returns a fallback "insufficient data" finding when LLM misses a dim.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from artemis.argus.drawer import Dimension, DistrictFinding
from artemis.argus.research import (
    PRIMARY_DIMENSIONS,
    _gather_tool_results,
    _parse_synthesis_output,
    research_dimensions,
    stub_research_dimensions,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    return session


def _make_callie_registry() -> Any:
    """Build an AuthorizedToolRegistry as if agent_id='callie'."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    return build_authorized_tool_registry(
        available_surfaces={"marketing-os"},
        agent_id="callie",
    )


def _make_artemis_registry() -> Any:
    """Build an AuthorizedToolRegistry as if agent_id='artemis'."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    return build_authorized_tool_registry(
        available_surfaces={"marketing-os", "okr"},
        agent_id="artemis",
    )


# ── D1: dispatch_research gating ──────────────────────────────────────────────


def test_dispatch_research_present_for_callie() -> None:
    """dispatch_research is registered when agent_id='callie'."""
    registry = _make_callie_registry()
    assert "dispatch_research" in registry, "dispatch_research must be present in Callie's registry"


def test_dispatch_research_absent_for_artemis() -> None:
    """dispatch_research is NOT registered when agent_id='artemis'."""
    registry = _make_artemis_registry()
    assert "dispatch_research" not in registry, (
        "dispatch_research must NOT appear in Artemis's registry"
    )


def test_dispatch_research_absent_for_kai() -> None:
    """dispatch_research is NOT registered when agent_id='kai'."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    registry = build_authorized_tool_registry(available_surfaces=set(), agent_id="kai")
    assert "dispatch_research" not in registry


def test_dispatch_research_absent_for_unknown_agent() -> None:
    """dispatch_research is NOT registered for an unknown agent_id."""
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry

    registry = build_authorized_tool_registry(
        available_surfaces={"marketing-os"},
        agent_id="some_other_agent",
    )
    assert "dispatch_research" not in registry


# ── D6: _parse_synthesis_output ───────────────────────────────────────────────


def test_parse_synthesis_output_valid_json_lines() -> None:
    """_parse_synthesis_output parses well-formed JSON lines into DistrictFindings."""
    raw = "\n".join(
        [
            json.dumps(
                {
                    "dimension": "current_vendor",
                    "value": "Uses Lexia Reading Core5",
                    "source": "Argus/news_api",
                    "url": "https://example.com/article",
                }
            ),
            json.dumps(
                {
                    "dimension": "procurement_timing",
                    "value": "RFP expected Q1 FY2027",
                    "source": "Argus/board_minutes",
                    "url": None,
                }
            ),
            json.dumps(
                {
                    "dimension": "recommended_angle",
                    "value": "Position Amira as complement to Lexia. Timing is now.",
                    "source": "Argus",
                    "url": None,
                }
            ),
        ]
    )
    findings = _parse_synthesis_output(raw, "TX-001")
    dims = {f.dimension for f in findings}
    assert "current_vendor" in dims
    assert "procurement_timing" in dims
    assert "recommended_angle" in dims
    # source must start with "Argus"
    for f in findings:
        assert f.source.startswith("Argus"), f"Bad source: {f.source!r} for dim={f.dimension}"


def test_parse_synthesis_output_skips_bad_lines() -> None:
    """_parse_synthesis_output skips unparseable lines and continues."""
    raw = "\n".join(
        [
            "this is not json",
            json.dumps(
                {
                    "dimension": "district_profile",
                    "value": "Enrollment 5000",
                    "source": "Argus",
                    "url": None,
                }
            ),
            "```",
            "{ broken json",
        ]
    )
    findings = _parse_synthesis_output(raw, "TX-001")
    assert len(findings) == 1
    assert findings[0].dimension == "district_profile"


def test_parse_synthesis_output_deduplicates_same_dimension() -> None:
    """_parse_synthesis_output keeps only the first occurrence of a duplicate dimension."""
    raw = "\n".join(
        [
            json.dumps(
                {"dimension": "current_vendor", "value": "First", "source": "Argus", "url": None}
            ),
            json.dumps(
                {"dimension": "current_vendor", "value": "Second", "source": "Argus", "url": None}
            ),
        ]
    )
    findings = _parse_synthesis_output(raw, "TX-001")
    vendor_findings = [f for f in findings if f.dimension == "current_vendor"]
    assert len(vendor_findings) == 1
    assert vendor_findings[0].value == "First"


def test_parse_synthesis_output_prefixes_non_argus_source() -> None:
    """_parse_synthesis_output ensures source always starts with 'Argus'."""
    raw = json.dumps(
        {
            "dimension": "decision_makers",
            "value": "Dr. Smith, Superintendent",
            "source": "news_api",  # missing Argus/ prefix
            "url": None,
        }
    )
    findings = _parse_synthesis_output(raw, "TX-001")
    assert len(findings) == 1
    assert findings[0].source.startswith("Argus")


# ── D7: empty/garbage input ───────────────────────────────────────────────────


def test_parse_synthesis_output_empty_string() -> None:
    """_parse_synthesis_output returns empty list for empty input."""
    findings = _parse_synthesis_output("", "TX-001")
    assert findings == []


def test_parse_synthesis_output_all_garbage() -> None:
    """_parse_synthesis_output returns empty list when all lines are garbage."""
    findings = _parse_synthesis_output("foo\nbar\n```\n---", "TX-001")
    assert findings == []


# ── D2: research_dimensions calls right tool per dimension ────────────────────


@pytest.mark.asyncio
async def test_research_dimensions_calls_synthesis_with_tool_results() -> None:
    """research_dimensions gathers tools then passes results to LLM synthesis."""
    today = datetime.now(UTC).date().isoformat()

    # Mock _gather_tool_results to return synthetic data
    mock_tool_results = {
        "news_api": [
            {"title": "District adopts Lexia", "link": "https://x.com", "published": today}
        ],
        "board_minutes": [],
    }
    # Mock _run_synthesis to return pre-built findings
    expected_findings = [
        DistrictFinding(
            dimension=Dimension.CURRENT_VENDOR,
            value="Lexia Reading Core5 adopted 2025",
            source="Argus/news_api",
            url="https://x.com",
            researched_at=today,
        )
    ]

    with (
        patch(
            "artemis.argus.research._gather_tool_results",
            new_callable=AsyncMock,
            return_value=mock_tool_results,
        ),
        patch(
            "artemis.argus.research._run_synthesis",
            new_callable=AsyncMock,
            return_value=expected_findings,
        ),
    ):
        result = await research_dimensions(
            "TX-001",
            [Dimension.CURRENT_VENDOR],
            signal={"headline": "New reading program", "state": "TX"},
        )

    vendor_findings = [f for f in result if f.dimension == Dimension.CURRENT_VENDOR]
    assert len(vendor_findings) == 1
    assert vendor_findings[0].value == "Lexia Reading Core5 adopted 2025"
    assert vendor_findings[0].source.startswith("Argus")


@pytest.mark.asyncio
async def test_research_dimensions_skips_no_tool_dims_without_crashing() -> None:
    """Dimensions with no tool mapping (prior_amira_relationship) get a fallback finding."""
    with (
        patch(
            "artemis.argus.research._gather_tool_results", new_callable=AsyncMock, return_value={}
        ),
        patch("artemis.argus.research._run_synthesis", new_callable=AsyncMock, return_value=[]),
    ):
        result = await research_dimensions(
            "TX-001",
            [Dimension.PRIOR_AMIRA_RELATIONSHIP],
        )

    assert len(result) == 1
    assert result[0].dimension == Dimension.PRIOR_AMIRA_RELATIONSHIP
    assert result[0].source.startswith("Argus")
    assert "CRM" in result[0].value or "no" in result[0].value.lower()


# ── D3: source tag on all findings ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_research_dimensions_all_findings_tagged_argus() -> None:
    """Every finding from research_dimensions carries source='Argus' (or 'Argus/*')."""
    today = datetime.now(UTC).date().isoformat()

    synth_findings = [
        DistrictFinding(
            dimension=Dimension.CURRENT_VENDOR,
            value="V1",
            source="Argus/news_api",
            researched_at=today,
        ),
        DistrictFinding(
            dimension=Dimension.PROCUREMENT_TIMING,
            value="Q1",
            source="Argus/board_minutes",
            researched_at=today,
        ),
        DistrictFinding(
            dimension=Dimension.DISTRICT_PROFILE,
            value="Large",
            source="Argus/usaspending",
            researched_at=today,
        ),
        DistrictFinding(
            dimension=Dimension.DECISION_MAKERS,
            value="Dr. Smith",
            source="Argus/news_api",
            researched_at=today,
        ),
        DistrictFinding(
            dimension=Dimension.COMPETITOR_COMMITMENTS,
            value="None found",
            source="Argus",
            researched_at=today,
        ),
        DistrictFinding(
            dimension=Dimension.RECOMMENDED_ANGLE,
            value="Go now",
            source="Argus",
            researched_at=today,
        ),
    ]

    with (
        patch(
            "artemis.argus.research._gather_tool_results", new_callable=AsyncMock, return_value={}
        ),
        patch(
            "artemis.argus.research._run_synthesis",
            new_callable=AsyncMock,
            return_value=synth_findings,
        ),
    ):
        result = await research_dimensions("TX-002", PRIMARY_DIMENSIONS)

    for f in result:
        assert f.source.startswith("Argus"), (
            f"Finding dim={f.dimension!r} has source={f.source!r}, expected 'Argus' prefix"
        )


# ── D4: tool failure on one dimension does not crash ─────────────────────────


@pytest.mark.asyncio
async def test_research_dimensions_tool_failure_does_not_crash() -> None:
    """A failure in one tool fetch is caught; other dimensions still produce findings."""
    today = datetime.now(UTC).date().isoformat()

    async def failing_news(district_key, signal):
        raise RuntimeError("news API timeout")

    async def ok_state_doe(district_key, signal):
        return [
            {"title": "Literacy grant awarded", "link": "https://state.edu", "published": today}
        ]

    partial_tool_results = {
        "state_doe": [
            {"title": "Literacy grant awarded", "link": "https://state.edu", "published": today}
        ],
    }
    synth_result = [
        DistrictFinding(
            dimension=Dimension.DISTRICT_PROFILE,
            value="State DOE literacy grant recipient",
            source="Argus/state_doe",
            researched_at=today,
        )
    ]

    # _gather_tool_results handles per-tool failures internally; mock it to return partial data
    with (
        patch(
            "artemis.argus.research._gather_tool_results",
            new_callable=AsyncMock,
            return_value=partial_tool_results,
        ),
        patch(
            "artemis.argus.research._run_synthesis",
            new_callable=AsyncMock,
            return_value=synth_result,
        ),
    ):
        result = await research_dimensions(
            "TX-003",
            [Dimension.DISTRICT_PROFILE, Dimension.CURRENT_VENDOR],
        )

    # Should not have crashed; some findings should be present
    assert len(result) >= 1
    for f in result:
        assert f.source.startswith("Argus")


@pytest.mark.asyncio
async def test_gather_tool_results_catches_individual_tool_failure() -> None:
    """_gather_tool_results catches per-tool exceptions and returns partial results."""
    import artemis.argus.research as _research_mod

    async def raises(*args, **kwargs):
        raise RuntimeError("boom")

    async def ok_returns(*args, **kwargs):
        return [{"title": "something", "link": "https://x.com", "published": "2026-06-17"}]

    with (
        patch.object(_research_mod, "_fetch_news", raises),
        patch.object(_research_mod, "_fetch_state_doe", ok_returns),
    ):
        # District profile uses state_doe; current_vendor uses news_api (which will fail)
        results = await _gather_tool_results(
            "TX-004",
            [Dimension.CURRENT_VENDOR, Dimension.DISTRICT_PROFILE],
            signal={"state": "TX"},
        )

    # news_api failed -> its result should be [] (not raise)
    assert results.get("news_api") == [] or "news_api" not in results or results["news_api"] == []
    # state_doe succeeded -> should have data
    assert len(results.get("state_doe", [])) >= 1


# ── D5: writes through drawer (memory pipeline) ───────────────────────────────
# This is covered by the existing test_argus_foundation.py (T1/T3).
# D5 adds an integration assertion: research_district with a mocked research_fn
# ends up calling write_district_findings (not raw session.execute).


@pytest.mark.asyncio
async def test_research_district_writes_through_drawer_pipeline() -> None:
    """research_district writes through write_district_findings (memory pipeline)."""
    from artemis.argus.flow import research_district

    session = _mock_session()
    today = datetime.now(UTC).date().isoformat()

    async def fake_research(dk, dims, sig):
        return [
            DistrictFinding(dimension=d, value=f"val_{d}", source="Argus", researched_at=today)
            for d in dims
            if d != Dimension.RECOMMENDED_ANGLE
        ]

    with (
        patch("artemis.argus.flow.read_district_drawer", new_callable=AsyncMock, return_value={}),
        patch(
            "artemis.argus.flow.write_district_findings",
            new_callable=AsyncMock,
            return_value=[1, 2, 3],
        ) as mock_write,
    ):
        await research_district(
            session,
            district_key="TX-005",
            _research_fn=fake_research,
        )

    mock_write.assert_called_once()
    # Must NOT have called session.execute directly for writes
    session.execute.assert_not_called()


# ── D8: fallback for LLM-missed dimensions ────────────────────────────────────


@pytest.mark.asyncio
async def test_research_dimensions_fills_missing_dims_with_fallback() -> None:
    """If the LLM skips a dimension, research_dimensions inserts an 'insufficient data' finding."""
    # LLM only returns one dim; the other should get a fallback
    today = datetime.now(UTC).date().isoformat()

    with (
        patch(
            "artemis.argus.research._gather_tool_results", new_callable=AsyncMock, return_value={}
        ),
        patch(
            "artemis.argus.research._run_synthesis",
            new_callable=AsyncMock,
            return_value=[
                DistrictFinding(
                    dimension=Dimension.CURRENT_VENDOR,
                    value="Lexia",
                    source="Argus",
                    researched_at=today,
                ),
                # PROCUREMENT_TIMING intentionally missing from LLM output
            ],
        ),
    ):
        result = await research_dimensions(
            "TX-006",
            [Dimension.CURRENT_VENDOR, Dimension.PROCUREMENT_TIMING],
        )

    dims_found = {f.dimension for f in result}
    assert Dimension.CURRENT_VENDOR in dims_found
    assert Dimension.PROCUREMENT_TIMING in dims_found  # must have a fallback

    # Fallback should note insufficient data
    procurement_finding = next(f for f in result if f.dimension == Dimension.PROCUREMENT_TIMING)
    assert (
        "insufficient" in procurement_finding.value.lower()
        or "no data" in procurement_finding.value.lower()
    )
    assert procurement_finding.source.startswith("Argus")


# ── Backwards compat: stub still importable ───────────────────────────────────


@pytest.mark.asyncio
async def test_stub_research_dimensions_still_importable() -> None:
    """stub_research_dimensions is still importable and returns stub-tagged findings."""
    result = await stub_research_dimensions("TX-007", [Dimension.CURRENT_VENDOR])
    assert len(result) == 1
    assert result[0].source == "Argus/stub"
    assert "[STUB]" in result[0].value


# ── Search-term resolution (2026-08-12) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_gather_tool_results_searches_on_the_district_name_not_the_key() -> None:
    """Keyword-search fetchers must receive the district NAME, not its drawer key.

    Every ``_fetch_*`` helper except ``_fetch_board_minutes`` uses its first
    argument as a literal search string, and a real drawer key is usually an
    NCES id. Measured against live sources: ``_fetch_news("11414", …)``
    returned 0 items while ``_fetch_news("FORT WORTH ISD", …)`` returned 15
    on-topic ones. The first two real Argus runs produced 12 "Insufficient
    data" findings out of 14 because of exactly this.

    ARGUS-2: ``_fetch_board_minutes`` is the one deliberate exception. It is
    not a keyword search -- it looks up ``districts.boarddocs_url`` off the
    RAW drawer key via the same id-based seam ``_resolve_search_term`` uses
    for the name, so it must receive the raw key, not the resolved name (a
    district name is not unique across ``districts`` and re-resolving by
    name could land on a different district's row -- see
    ``_resolve_district_row``'s docstring).
    """
    from unittest.mock import AsyncMock, patch

    from artemis.argus import research as research_mod

    seen_news: list[str] = []
    seen_board: list[str] = []

    async def spy_news(term: str, signal: object) -> list[dict[str, object]]:
        seen_news.append(term)
        return []

    async def spy_board(term: str, signal: object) -> list[dict[str, object]]:
        seen_board.append(term)
        return []

    with (
        patch.object(research_mod, "_resolve_search_term", new=AsyncMock(return_value="FORT WORTH ISD")),
        patch.object(research_mod, "_fetch_news", new=spy_news),
        patch.object(research_mod, "_fetch_board_minutes", new=spy_board),
    ):
        await research_mod._gather_tool_results("11414", [research_mod.Dimension.CURRENT_VENDOR], {"state": "TX"})

    assert seen_news == ["FORT WORTH ISD"], (
        f"news_api must be handed the resolved name, got {seen_news}"
    )
    assert seen_board == ["11414"], (
        f"board_minutes must be handed the RAW drawer key, got {seen_board}"
    )


@pytest.mark.asyncio
async def test_resolve_search_term_falls_back_to_the_key_on_failure() -> None:
    """A lookup failure must degrade to today's behaviour, never raise.

    A research pass with a poor search term is worth strictly more than one
    that crashed.
    """
    from unittest.mock import MagicMock, patch

    import artemis.db as _db
    from artemis.argus.research import _resolve_search_term

    broken = MagicMock(side_effect=RuntimeError("db down"))
    with patch.object(_db, "SessionLocal", broken):
        assert await _resolve_search_term("11414") == "11414"
