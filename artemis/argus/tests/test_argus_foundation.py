"""Unit tests for the Argus foundation.

All tests are UNIT tests — no DB, no env vars required.  The memory pipeline
(write_observation, write_drawer, link_evidence) and the research step are
mocked.  This keeps the test suite runnable in the worktree (no .env).

Test coverage:
  T1 — drawer round-trip: write_district_findings calls write_observation + write_drawer
       (mocked); read_district_drawer parses observations back to DistrictFindings.
  T2 — research_district reads existing drawer → identifies gaps → calls research fn
       → writes findings tagged source="Argus" with provenance.
  T3 — confirm write goes THROUGH the memory pipeline (write_observation is called,
       not a hand-rolled INSERT) so dedup is delegated, not reimplemented.
  T4 — finding already present (all dimensions fresh) → research step NOT called,
       no new observations written.
  T5 — identify_gaps: missing dimensions are returned; fresh dimensions are skipped;
       stale dimensions (older than STALENESS_DAYS) are returned.
  T6 — _finding_to_content / _content_to_finding round-trip.
  T7 — write_district_findings failure on one finding does not abort the others.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from artemis.argus.drawer import (
    ARGUS_CATEGORY,
    ARGUS_SCOPE,
    Dimension,
    DistrictFinding,
    _content_to_finding,
    _finding_to_content,
    read_district_drawer,
    write_district_findings,
)
from artemis.argus.flow import research_district
from artemis.argus.research import (
    PRIMARY_DIMENSIONS,
    STALENESS_DAYS,
    identify_gaps,
)


# ── Shared fixtures ────────────────────────────────────────────────────────────


def _mock_session() -> AsyncMock:
    """Return an AsyncMock that quacks like an AsyncSession."""
    session = AsyncMock()
    return session


def _make_finding(
    dimension: str = Dimension.CURRENT_VENDOR,
    value: str = "Lexia Reading Core5",
    source: str = "Argus",
    url: str | None = None,
    researched_at: str | None = None,
) -> DistrictFinding:
    return DistrictFinding(
        dimension=dimension,
        value=value,
        source=source,
        url=url,
        researched_at=researched_at or datetime.now(UTC).date().isoformat(),
    )


# ── T6: content round-trip ────────────────────────────────────────────────────


def test_finding_to_content_format() -> None:
    """_finding_to_content produces the expected bracketed format."""
    f = _make_finding(
        dimension=Dimension.CURRENT_VENDOR,
        value="Lexia Reading Core5",
        source="Argus/news_api",
        url="https://example.com/board-minutes",
        researched_at="2026-06-17",
    )
    content = _finding_to_content("TX-001", f)
    assert content.startswith("[Argus|current_vendor|TX-001]")
    assert "Lexia Reading Core5" in content
    assert "source: Argus/news_api" in content
    assert "url: https://example.com/board-minutes" in content
    assert "researched_at: 2026-06-17" in content


def test_content_to_finding_round_trip() -> None:
    """_content_to_finding inverts _finding_to_content correctly."""
    f = _make_finding(
        dimension=Dimension.PROCUREMENT_TIMING,
        value="RFP opens Q1 FY2027",
        source="Argus",
        url=None,
        researched_at="2026-06-17",
    )
    content = _finding_to_content("TX-001", f)
    recovered = _content_to_finding("TX-001", content)
    assert recovered is not None
    assert recovered.dimension == Dimension.PROCUREMENT_TIMING
    assert recovered.value == "RFP opens Q1 FY2027"
    assert recovered.source == "Argus"
    assert recovered.url is None
    assert recovered.researched_at == "2026-06-17"


def test_content_to_finding_returns_none_for_non_argus_content() -> None:
    """_content_to_finding returns None for observations not written by Argus."""
    result = _content_to_finding("TX-001", "Qualified signal 42: Some headline. Source: news.")
    assert result is None


def test_content_to_finding_returns_none_for_wrong_district() -> None:
    """_content_to_finding returns None when district_key in content doesn't match."""
    f = _make_finding(dimension=Dimension.CURRENT_VENDOR, value="X")
    content = _finding_to_content("TX-001", f)
    # Try to parse as a different district
    result = _content_to_finding("TX-002", content)
    assert result is None


# ── T5: identify_gaps ─────────────────────────────────────────────────────────


def test_identify_gaps_all_missing() -> None:
    """All dimensions missing → all returned as gaps."""
    gaps = identify_gaps({})
    assert set(gaps) == set(PRIMARY_DIMENSIONS)


def test_identify_gaps_all_present_and_fresh() -> None:
    """All dimensions present with today's date → no gaps."""
    today = datetime.now(UTC).date().isoformat()
    existing = {
        dim: _make_finding(dimension=dim, researched_at=today) for dim in PRIMARY_DIMENSIONS
    }
    gaps = identify_gaps(existing)
    assert gaps == []


def test_identify_gaps_stale_dimension_returned() -> None:
    """A dimension older than STALENESS_DAYS is returned as a gap."""
    stale_date = (datetime.now(UTC) - timedelta(days=STALENESS_DAYS + 10)).date().isoformat()
    today = datetime.now(UTC).date().isoformat()
    existing = {
        dim: _make_finding(dimension=dim, researched_at=today) for dim in PRIMARY_DIMENSIONS
    }
    # Make one dimension stale
    existing[Dimension.CURRENT_VENDOR] = _make_finding(
        dimension=Dimension.CURRENT_VENDOR, researched_at=stale_date
    )
    gaps = identify_gaps(existing)
    assert Dimension.CURRENT_VENDOR in gaps
    # Other dimensions should NOT be in gaps
    for dim in PRIMARY_DIMENSIONS:
        if dim != Dimension.CURRENT_VENDOR:
            assert dim not in gaps


def test_identify_gaps_custom_required_dimensions() -> None:
    """Only the requested dimensions are checked."""
    gaps = identify_gaps({}, required_dimensions=[Dimension.DECISION_MAKERS])
    assert gaps == [Dimension.DECISION_MAKERS]


# ── T1 / T3: write_district_findings calls write_observation (memory pipeline) ─


@pytest.mark.asyncio
async def test_write_district_findings_calls_write_observation_and_write_drawer() -> None:
    """write_district_findings must call write_observation and write_drawer —
    not a hand-rolled INSERT — so dedup is delegated to the memory layer (T3).
    """
    session = _mock_session()

    # write_drawer returns a Drawer-like mock with id
    fake_drawer = MagicMock()
    fake_drawer.id = 101

    # write_observation returns an Observation-like mock with id
    fake_obs = MagicMock()
    fake_obs.id = 201

    with (
        patch(
            "artemis.argus.drawer.write_drawer", new_callable=AsyncMock, return_value=fake_drawer
        ) as mock_wd,
        patch(
            "artemis.argus.drawer.write_observation", new_callable=AsyncMock, return_value=fake_obs
        ) as mock_wo,
        patch("artemis.argus.drawer.link_evidence", new_callable=AsyncMock) as mock_le,
    ):
        findings = [_make_finding(dimension=Dimension.CURRENT_VENDOR, value="Lexia")]
        ids = await write_district_findings(session, "TX-001", findings)

    # write_observation must have been called (T3: dedup delegated to memory pipeline)
    mock_wo.assert_called_once()
    wo_kwargs = mock_wo.call_args
    assert wo_kwargs is not None

    # write_drawer must have been called
    mock_wd.assert_called_once()

    # link_evidence must have been called at least once (drawer link)
    assert mock_le.call_count >= 1

    # Returned ids should include the mocked observation id
    assert 201 in ids


@pytest.mark.asyncio
async def test_write_district_findings_with_signal_links_signal_evidence() -> None:
    """When triggering_signal_id is provided, link_evidence is called with signal_queue."""
    session = _mock_session()
    fake_drawer = MagicMock()
    fake_drawer.id = 102
    fake_obs = MagicMock()
    fake_obs.id = 202

    with (
        patch(
            "artemis.argus.drawer.write_drawer", new_callable=AsyncMock, return_value=fake_drawer
        ),
        patch(
            "artemis.argus.drawer.write_observation", new_callable=AsyncMock, return_value=fake_obs
        ),
        patch("artemis.argus.drawer.link_evidence", new_callable=AsyncMock) as mock_le,
    ):
        findings = [_make_finding(dimension=Dimension.PROCUREMENT_TIMING, value="Q1 FY2027")]
        await write_district_findings(session, "TX-001", findings, triggering_signal_id="42")

    # There should be a call with source_kind="signal_queue" and source_id="42"
    signal_calls = [
        c for c in mock_le.call_args_list if c.kwargs.get("source_kind") == "signal_queue"
    ]
    assert len(signal_calls) == 1
    assert signal_calls[0].kwargs["source_id"] == "42"


# ── T1: drawer read round-trip ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_district_drawer_parses_argus_observations() -> None:
    """read_district_drawer returns parsed DistrictFindings for Argus-written rows."""
    session = _mock_session()

    # Build fake observation rows with Argus content format
    finding = _make_finding(
        dimension=Dimension.DECISION_MAKERS,
        value="Dr. Jane Smith, Superintendent",
        source="Argus",
        url="https://district.example.com/staff",
        researched_at="2026-06-17",
    )
    content = _finding_to_content("TX-001", finding)

    fake_obs = MagicMock()
    fake_obs.id = 300
    fake_obs.content = content
    fake_obs.category = ARGUS_CATEGORY

    # Also include a non-Argus observation that should be ignored
    other_obs = MagicMock()
    other_obs.id = 301
    other_obs.content = "Qualified signal 5: some headline. Source: news."
    other_obs.category = "signal_qualification"

    # Mock the DB execute result
    fake_result = MagicMock()
    fake_result.scalars.return_value.return_value = None  # unused path

    async def fake_execute(stmt):
        result = MagicMock()
        result.scalars.return_value = iter([fake_obs, other_obs])
        return result

    session.execute = fake_execute

    findings = await read_district_drawer(session, "TX-001")
    assert Dimension.DECISION_MAKERS in findings
    assert findings[Dimension.DECISION_MAKERS].value == "Dr. Jane Smith, Superintendent"
    assert findings[Dimension.DECISION_MAKERS].source == "Argus"
    assert findings[Dimension.DECISION_MAKERS].url == "https://district.example.com/staff"
    # Non-Argus observation should not appear
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_read_district_drawer_returns_empty_on_db_error() -> None:
    """read_district_drawer returns {} when the DB query raises — never propagates."""
    session = _mock_session()
    session.execute = AsyncMock(side_effect=RuntimeError("db error"))

    result = await read_district_drawer(session, "TX-001")
    assert result == {}


# ── T2: research_district end-to-end (mocked pipeline + research step) ────────


@pytest.mark.asyncio
async def test_research_district_reads_existing_identifies_gaps_calls_research_writes() -> None:
    """research_district: reads existing → identifies gaps → calls research fn → writes."""
    session = _mock_session()

    # Existing drawer: only CURRENT_VENDOR present (fresh)
    today = datetime.now(UTC).date().isoformat()
    existing_finding = _make_finding(
        dimension=Dimension.CURRENT_VENDOR,
        value="Lexia",
        researched_at=today,
    )

    # Stub out read_district_drawer to return existing
    fake_read_result = {Dimension.CURRENT_VENDOR: existing_finding}

    # Stub research fn: returns one finding per gap dimension
    async def fake_research(
        district_key: str, dimensions: list[str], signal: Any
    ) -> list[DistrictFinding]:
        return [
            DistrictFinding(
                dimension=dim,
                value=f"Researched value for {dim}",
                source="Argus",
                researched_at=today,
            )
            for dim in dimensions
        ]

    # Mock write_district_findings
    fake_obs = MagicMock()
    fake_obs.id = 400
    fake_drawer = MagicMock()
    fake_drawer.id = 500

    with (
        patch(
            "artemis.argus.flow.read_district_drawer",
            new_callable=AsyncMock,
            return_value=fake_read_result,
        ),
        patch(
            "artemis.argus.flow.write_district_findings",
            new_callable=AsyncMock,
            return_value=[400, 401, 402, 403, 404, 405],
        ) as mock_write,
    ):
        result = await research_district(
            session,
            district_key="TX-001",
            signal={"headline": "District RFP for reading software"},
            triggering_signal_id="99",
            _research_fn=fake_research,
        )

    # Confirm gaps were identified: all dims except CURRENT_VENDOR
    assert Dimension.CURRENT_VENDOR not in result["gap_dimensions"]
    # All other primary dims should be gaps
    for dim in PRIMARY_DIMENSIONS:
        if dim != Dimension.CURRENT_VENDOR:
            assert dim in result["gap_dimensions"]

    # write_district_findings must have been called (T3: writes through pipeline)
    mock_write.assert_called_once()
    write_call = mock_write.call_args
    assert write_call.args[1] == "TX-001"  # district_key
    # triggering_signal_id should be forwarded
    assert write_call.kwargs.get("triggering_signal_id") == "99"

    # All written findings must be tagged source="Argus"
    written_findings: list[DistrictFinding] = write_call.args[2]
    for f in written_findings:
        if f.dimension != Dimension.RECOMMENDED_ANGLE:
            assert f.source == "Argus", (
                f"Expected source='Argus', got {f.source!r} for dim={f.dimension}"
            )


@pytest.mark.asyncio
async def test_research_district_findings_have_argus_source_tag() -> None:
    """Every finding written by research_district carries source='Argus' (or 'Argus/*')."""
    session = _mock_session()
    today = datetime.now(UTC).date().isoformat()

    async def fake_research(
        district_key: str, dimensions: list[str], signal: Any
    ) -> list[DistrictFinding]:
        return [
            DistrictFinding(
                dimension=dim,
                value=f"Value for {dim}",
                source="Argus",
                researched_at=today,
            )
            for dim in dimensions
        ]

    with (
        patch("artemis.argus.flow.read_district_drawer", new_callable=AsyncMock, return_value={}),
        patch(
            "artemis.argus.flow.write_district_findings",
            new_callable=AsyncMock,
            return_value=[1, 2],
        ) as mock_write,
    ):
        await research_district(
            session,
            district_key="TX-002",
            _research_fn=fake_research,
        )

    written_findings: list[DistrictFinding] = mock_write.call_args.args[2]
    for f in written_findings:
        assert f.source.startswith("Argus"), (
            f"Finding dimension={f.dimension!r} has source={f.source!r}, expected 'Argus' prefix"
        )


# ── T4: already present → no re-research ─────────────────────────────────────


@pytest.mark.asyncio
async def test_research_district_skips_research_when_all_dims_fresh() -> None:
    """When all dimensions are present and fresh, the research fn is NOT called."""
    session = _mock_session()
    today = datetime.now(UTC).date().isoformat()

    full_drawer = {
        dim: _make_finding(dimension=dim, researched_at=today) for dim in PRIMARY_DIMENSIONS
    }

    research_called = False

    async def fake_research(
        district_key: str, dimensions: list[str], signal: Any
    ) -> list[DistrictFinding]:
        nonlocal research_called
        research_called = True
        return []

    with patch(
        "artemis.argus.flow.read_district_drawer", new_callable=AsyncMock, return_value=full_drawer
    ):
        result = await research_district(
            session,
            district_key="TX-003",
            _research_fn=fake_research,
        )

    assert not research_called, "Research fn should NOT be called when all dims are fresh"
    assert result["gap_dimensions"] == []
    assert result["new_findings"] == 0
    assert result["written_obs_ids"] == []


# ── T7: write failure on one finding does not abort others ────────────────────


@pytest.mark.asyncio
async def test_write_district_findings_skips_failed_finding() -> None:
    """If writing one finding raises, the error is caught and other findings proceed."""
    session = _mock_session()
    fake_drawer = MagicMock()
    fake_drawer.id = 600
    fake_obs_ok = MagicMock()
    fake_obs_ok.id = 700

    call_count = 0

    async def fake_write_drawer(session, scope, content, source, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated drawer write failure")
        return fake_drawer

    async def fake_write_obs(session, scope, content, **kwargs):
        return fake_obs_ok

    with (
        patch("artemis.argus.drawer.write_drawer", side_effect=fake_write_drawer),
        patch(
            "artemis.argus.drawer.write_observation",
            new_callable=AsyncMock,
            return_value=fake_obs_ok,
        ),
        patch("artemis.argus.drawer.link_evidence", new_callable=AsyncMock),
    ):
        findings = [
            _make_finding(dimension=Dimension.CURRENT_VENDOR, value="Vendor A"),
            _make_finding(dimension=Dimension.DECISION_MAKERS, value="Dr. Smith"),
        ]
        ids = await write_district_findings(session, "TX-004", findings)

    # First finding failed → should not be in returned ids
    # Second finding succeeded → should be in returned ids
    assert 700 in ids
    assert len(ids) == 1


# ── T3 explicit: write_observation is the write path, not raw SQL ─────────────


@pytest.mark.asyncio
async def test_write_goes_through_memory_pipeline_not_raw_sql() -> None:
    """Confirm write_district_findings uses write_observation, not session.execute directly.

    This test explicitly verifies T3: Argus does not reimplement dedup by
    writing raw SQL — it calls write_observation which owns the ON CONFLICT logic.
    """
    session = _mock_session()
    fake_drawer = MagicMock(id=800)
    fake_obs = MagicMock(id=900)

    with (
        patch(
            "artemis.argus.drawer.write_drawer", new_callable=AsyncMock, return_value=fake_drawer
        ) as mock_wd,
        patch(
            "artemis.argus.drawer.write_observation", new_callable=AsyncMock, return_value=fake_obs
        ) as mock_wo,
        patch("artemis.argus.drawer.link_evidence", new_callable=AsyncMock),
    ):
        await write_district_findings(
            session,
            "TX-005",
            [_make_finding(dimension=Dimension.DISTRICT_PROFILE, value="Enrollment: 5000")],
        )

    # The memory pipeline functions must have been called
    assert mock_wd.called, "write_drawer (memory pipeline) was not called"
    assert mock_wo.called, "write_observation (memory pipeline dedup) was not called"

    # Session.execute must NOT have been called directly for INSERT
    # (it will be called internally by write_observation, but NOT by drawer.py directly)
    session.execute.assert_not_called()
