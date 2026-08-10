"""Argus core flow — research_district.

Entry point for all Argus research runs.  Callie will eventually call this via
the planned P4 delegate primitive; for now it is a plain async function.

Flow
----
1. READ-EXISTING   — existing district drawer + triggering signal context.
2. IDENTIFY GAPS   — which dimensions are unknown or stale.
3. RESEARCH        — call the research interface for gap dimensions.
4. SYNTHESISE      — build a recommended angle from all findings.
5. WRITE           — write through write_district_findings (rides memory pipeline).

The function is deliberately not a tool registration — it is a Python-level
coroutine intended to be called by Callie's dispatch layer once that is built.

Scope: all writes go to workspace:marketing scope (ARGUS_SCOPE in drawer.py).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.argus.drawer import (
    Dimension,
    DistrictFinding,
    read_district_drawer,
    write_district_findings,
)
from artemis.argus.research import (
    PRIMARY_DIMENSIONS,
    identify_gaps,
    research_dimensions,
    stub_research_dimensions,
)

_logger = logging.getLogger(__name__)


async def research_district(
    session: AsyncSession,
    *,
    district_key: str,
    signal: dict[str, Any] | None = None,
    triggering_signal_id: str | None = None,
    required_dimensions: list[str] | None = None,
    _research_fn: Any = None,
) -> dict[str, Any]:
    """Research a district and persist findings through the memory pipeline.

    Parameters
    ----------
    session:
        Active AsyncSession.  Transaction management is the caller's responsibility.
    district_key:
        Stable identifier for the district — use district_id from the signal
        (e.g. "TX-001") or a normalised name slug.
    signal:
        Optional triggering signal dict (the qualified signal that triggered the
        dig-deeper).  Accepted as a plain dict so this module does NOT import
        terminal's signal-tool code.  Pass the output of signal_queue.get as-is.
    triggering_signal_id:
        String signal_id to attach as evidence on every written observation so
        the provenance chain back to the signal is preserved.
    required_dimensions:
        Which dimensions to check/fill.  Defaults to PRIMARY_DIMENSIONS.
    _research_fn:
        Injection point for tests.  If provided, overrides stub_research_dimensions.
        Signature: async (district_key, dimensions, signal) -> list[DistrictFinding].

    Returns a summary dict:
        {
            "district_key": str,
            "existing_dimensions": list[str],
            "gap_dimensions": list[str],
            "new_findings": int,
            "written_obs_ids": list[int],
            "recommended_angle": str | None,
        }
    """
    _logger.info("Argus: research_district started for district_key=%r", district_key)

    # ── 1. READ-EXISTING ─────────────────────────────────────────────────────
    existing = await read_district_drawer(session, district_key)
    existing_dims = list(existing.keys())
    _logger.debug(
        "Argus: district_key=%r has %d existing dimensions: %s",
        district_key,
        len(existing_dims),
        existing_dims,
    )

    # ── 2. IDENTIFY GAPS ─────────────────────────────────────────────────────
    gaps = identify_gaps(
        existing,
        required_dimensions=required_dimensions,
    )
    if not gaps:
        _logger.info(
            "Argus: district_key=%r — all dimensions present and fresh; nothing to research.",
            district_key,
        )
        return {
            "district_key": district_key,
            "existing_dimensions": existing_dims,
            "gap_dimensions": [],
            "new_findings": 0,
            "written_obs_ids": [],
            "recommended_angle": existing.get(Dimension.RECOMMENDED_ANGLE, {}).value
            if Dimension.RECOMMENDED_ANGLE in existing
            else None,
        }

    _logger.info(
        "Argus: district_key=%r — %d gap dimensions to research: %s",
        district_key,
        len(gaps),
        gaps,
    )

    # ── 3. RESEARCH ──────────────────────────────────────────────────────────
    research_fn = _research_fn or research_dimensions
    try:
        raw_findings: list[DistrictFinding] = await research_fn(district_key, gaps, signal)
    except Exception:
        _logger.error(
            "Argus: research step failed for district_key=%r (non-fatal; will write no findings)",
            district_key,
            exc_info=True,
        )
        raw_findings = []

    # ── 4. SYNTHESISE ────────────────────────────────────────────────────────
    # Build or update the recommended angle from ALL available information
    # (existing + newly researched).  This ensures the angle is always the
    # freshest synthesis even when only a subset of dimensions were re-researched.
    all_findings_by_dim: dict[str, DistrictFinding] = dict(existing)
    for f in raw_findings:
        all_findings_by_dim[f.dimension] = f

    # Remove the recommended angle from the raw research batch if it was
    # produced by the stub — we synthesise it ourselves here.
    research_findings_to_write = [
        f for f in raw_findings if f.dimension != Dimension.RECOMMENDED_ANGLE
    ]

    angle = _synthesise_recommended_angle(district_key, all_findings_by_dim, signal)

    # Include the synthesised angle as a finding to write.
    if angle is not None:
        research_findings_to_write.append(angle)

    # ── 5. WRITE ─────────────────────────────────────────────────────────────
    written_ids: list[int] = []
    if research_findings_to_write:
        written_ids = await write_district_findings(
            session,
            district_key,
            research_findings_to_write,
            triggering_signal_id=triggering_signal_id,
        )
    _logger.info(
        "Argus: district_key=%r — wrote %d observations (ids=%s)",
        district_key,
        len(written_ids),
        written_ids,
    )

    return {
        "district_key": district_key,
        "existing_dimensions": existing_dims,
        "gap_dimensions": gaps,
        "new_findings": len(research_findings_to_write),
        "written_obs_ids": written_ids,
        "recommended_angle": angle.value if angle else None,
    }


# ── Synthesis helper ──────────────────────────────────────────────────────────


def _synthesise_recommended_angle(
    district_key: str,
    findings_by_dim: dict[str, DistrictFinding],
    signal: dict[str, Any] | None,
) -> DistrictFinding | None:
    """Build a recommended angle from all available findings.

    This is a rule-based heuristic synthesis that Argus can run without an LLM
    call, keeping the foundation fast and dependency-free.  The real production
    version should call an LLM (haiku-class) once all research dimensions are
    populated.

    TODO: replace the heuristic with an LLM synthesis call once the full research
    wiring is in place.  Use the existing model adapter (artemis.agent.client).

    Returns None if there is not enough information to synthesise an angle.
    """
    from datetime import UTC, datetime

    parts: list[str] = []

    vendor = findings_by_dim.get(Dimension.CURRENT_VENDOR)
    if vendor and "[STUB]" not in vendor.value:
        parts.append(f"Current vendor: {vendor.value}.")

    timing = findings_by_dim.get(Dimension.PROCUREMENT_TIMING)
    if timing and "[STUB]" not in timing.value:
        parts.append(f"Procurement window: {timing.value}.")

    prior = findings_by_dim.get(Dimension.PRIOR_AMIRA_RELATIONSHIP)
    if prior and "[STUB]" not in prior.value:
        parts.append(f"Prior Amira relationship: {prior.value}.")

    competitor = findings_by_dim.get(Dimension.COMPETITOR_COMMITMENTS)
    if competitor and "[STUB]" not in competitor.value:
        parts.append(f"Competitor commitments: {competitor.value}.")

    if not parts:
        # Not enough real data to synthesise yet; skip so we don't write a
        # content-hash-stable stub angle that would block a real one later.
        return None

    # Build a minimal angle summary
    signal_headline = signal.get("headline", "") if signal else ""
    angle_text = (
        f"Recommended angle for {district_key}: "
        + " ".join(parts)
        + (f" Triggered by: {signal_headline}." if signal_headline else "")
    )

    return DistrictFinding(
        dimension=Dimension.RECOMMENDED_ANGLE,
        value=angle_text,
        source="Argus",
        url=None,
        researched_at=datetime.now(UTC).date().isoformat(),
        raw_notes={"synthesis": "heuristic", "dims_used": list(findings_by_dim.keys())},
    )
