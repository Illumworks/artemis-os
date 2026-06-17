"""Argus research interface — identifies gaps and fetches missing dimensions.

This module defines the interface between the Argus flow and the underlying
research tools.  The production implementation is STUBBED: it returns a
placeholder DistrictFinding for each requested dimension.

TODO (wiring):  The real implementation should call:
  - artemis.tools.news.news_api.search  (Google News RSS, no API key required)
    → vendor news, board decisions, leadership appointments, reading-score coverage
  - artemis.tools.board_minutes.board_minutes.fetch (BoardDocs)
    → procurement / RFP timing, curriculum adoption votes, decision-maker names
  - artemis.tools.procurement.procurement.search (state procurement portals)
    → active RFPs, contract awards, fiscal-year windows
  - artemis.tools.usaspending.usaspending.search (USASpending.gov, no key)
    → Title I / federal grant eligibility and funding levels
  - artemis.tools.state_doe (State DOE APIs / NCES)
    → enrollment, reading scores, district profile
  - artemis.scouts._http.ScoutHttpClient (direct HTTP for sources without a tool)
    → any source-specific scraping not covered by the above

Until those wires are connected, stub_research_dimensions returns placeholder
findings so the flow module and tests can exercise the full pipeline.

The contract each real implementation must satisfy:
  - Accept (district_key, dimensions, signal) as positional/keyword args.
  - Return a list[DistrictFinding].  Empty list = nothing found.
  - NEVER raise — catch internally and log; return partial results.
  - Tag every finding with source="Argus" (or "Argus/<tool-name>") + url if available.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from artemis.argus.drawer import Dimension, DistrictFinding

_logger = logging.getLogger(__name__)


# ── Stub implementation ───────────────────────────────────────────────────────


async def stub_research_dimensions(
    district_key: str,
    dimensions: list[str],
    signal: dict[str, Any] | None = None,
) -> list[DistrictFinding]:
    """Return placeholder DistrictFindings for each requested dimension.

    This is the STUB implementation.  Replace with real tool calls (see module
    docstring) before Argus goes into production.

    Each stub finding carries source="Argus/stub" so downstream consumers and
    tests can distinguish stub output from real research.
    """
    _logger.info(
        "Argus stub_research_dimensions: district_key=%r, dimensions=%r (stub — no real fetch)",
        district_key,
        dimensions,
    )
    now_date = datetime.now(UTC).date().isoformat()
    findings: list[DistrictFinding] = []

    for dim in dimensions:
        # Produce a clearly-labeled stub value so nothing looks like real data.
        findings.append(
            DistrictFinding(
                dimension=dim,
                value=f"[STUB] No real research performed for dimension '{dim}' (district: {district_key}).",
                source="Argus/stub",
                url=None,
                researched_at=now_date,
                raw_notes={"stub": True, "signal": signal},
            )
        )

    return findings


# ── Gap identification ────────────────────────────────────────────────────────


# Dimensions that are always researched on a first pass.  Callers can override.
PRIMARY_DIMENSIONS: list[str] = [
    Dimension.CURRENT_VENDOR,
    Dimension.PROCUREMENT_TIMING,
    Dimension.DISTRICT_PROFILE,
    Dimension.DECISION_MAKERS,
    Dimension.PRIOR_AMIRA_RELATIONSHIP,
    Dimension.COMPETITOR_COMMITMENTS,
    Dimension.RECOMMENDED_ANGLE,
]

# How many days before a finding is considered stale and should be refreshed.
STALENESS_DAYS = 90


def identify_gaps(
    existing: dict[str, DistrictFinding],
    *,
    required_dimensions: list[str] | None = None,
    as_of_date: str | None = None,
) -> list[str]:
    """Return a list of dimension names that are missing or stale.

    existing         — dict[dimension → DistrictFinding] from read_district_drawer.
    required_dims    — which dimensions to check (defaults to PRIMARY_DIMENSIONS).
    as_of_date       — ISO date string to compare against researched_at (default: today).

    A dimension is considered stale when its researched_at date is older than
    STALENESS_DAYS from as_of_date.  Missing dimensions (not in existing) are
    always included in the gap list.
    """
    dims = required_dimensions if required_dimensions is not None else PRIMARY_DIMENSIONS
    today_str = as_of_date or datetime.now(UTC).date().isoformat()
    today = _parse_date(today_str)

    gaps: list[str] = []
    for dim in dims:
        if dim not in existing:
            gaps.append(dim)
            continue
        finding = existing[dim]
        researched_date = _parse_date(finding.researched_at)
        if researched_date is None:
            # Cannot parse date → treat as stale
            gaps.append(dim)
            continue
        if today is not None and (today - researched_date).days > STALENESS_DAYS:
            _logger.debug(
                "Argus: dimension=%r for district_key is stale (researched_at=%r)",
                dim,
                finding.researched_at,
            )
            gaps.append(dim)
    return gaps


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse an ISO date/datetime string, return None on failure."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S+00:00"):
        try:
            return datetime.strptime(date_str[:19], fmt[:len(date_str[:19])])
        except ValueError:
            continue
    return None
