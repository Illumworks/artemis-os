"""Argus dispatch tool for Callie -- gated to agent_id='callie' only.

Callie owns this tool. No other agent sees it.  It runs research_district
synchronously in-turn and returns the dossier so Callie can summarise it
in the same response, crediting Argus ("Here's what Argus dug up...").

v1: synchronous-in-turn -- the full research run happens before Callie replies.
    This is acceptable because research_dimensions uses asyncio.gather for the
    parallel tool fetches, so total wall time is bounded by the slowest single
    source rather than their sum.

v2 (future): background the research_district call, then notify Callie via
    the Artemis hub DM when the dossier lands ("Argus is back with findings
    on {district}."). See docs/argus-marketing-researcher-plan.md for context.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_logger = logging.getLogger(__name__)

_SURFACE = "[surface:marketing-os]"
_AGENT_GATE = "[agent:callie]"

# ── Tool definition ────────────────────────────────────────────────────────────

DISPATCH_RESEARCH = Tool(
    name="dispatch_research",
    description=(
        "Ask Argus (Callie's dedicated research agent) to research a district in depth. "
        "Returns a dossier of findings: current vendor, procurement timing, district profile, "
        "decision-makers, competitor commitments, and a recommended outreach angle. "
        "Each finding carries source='Argus' so attribution is grounded. "
        "Use when Jon asks Callie to dig into a district or a qualified signal. "
        f"{_SURFACE} {_AGENT_GATE} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "required": ["district_key"],
        "properties": {
            "district_key": {
                "type": "string",
                "description": (
                    "Stable district identifier -- district_id from a signal "
                    "(e.g. 'TX-001') or a normalised district name slug. "
                    "Used as the drawer key; must match the signal's district_id "
                    "if one exists so findings accumulate correctly."
                ),
            },
            "signal_id": {
                "type": "integer",
                "description": (
                    "Optional signal ID that triggered this research. "
                    "When provided, every finding is linked back to the signal "
                    "as evidence so the provenance chain is preserved."
                ),
            },
            "signal": {
                "type": "object",
                "description": (
                    "Optional triggering signal dict (from get_signal). "
                    "Provides state, headline, and provenance context to focus "
                    "Argus's research. Pass the full get_signal output."
                ),
            },
        },
    },
)

# ── Tool implementation ────────────────────────────────────────────────────────


async def _dispatch_research(inp: dict[str, Any]) -> str:
    """Run research_district and return a text dossier Callie can quote from."""
    import json

    district_key = str(inp.get("district_key") or "").strip()
    if not district_key:
        return "Error: district_key is required"

    signal_id_raw = inp.get("signal_id")
    triggering_signal_id: str | None = (
        str(int(signal_id_raw)) if signal_id_raw is not None else None
    )
    signal: dict[str, Any] | None = inp.get("signal") or None

    # If signal dict not provided but signal_id is, try to fetch the signal row
    if signal is None and triggering_signal_id is not None:
        try:
            import artemis.db as _db
            from artemis.marketing import repository as _repo

            async with _db.SessionLocal() as _session:
                sig_row = await _repo.get_signal(_session, int(triggering_signal_id))
            signal = {
                "headline": sig_row.headline or "",
                "state": sig_row.state or "",
                "district_id": sig_row.district_id or "",
                "source_url": sig_row.source_url or "",
            }
        except Exception as exc:
            _logger.warning(
                "dispatch_research: could not fetch signal_id=%s -- %s (continuing without signal context)",
                triggering_signal_id,
                exc,
            )

    _logger.info(
        "dispatch_research: starting for district_key=%r signal_id=%r",
        district_key,
        triggering_signal_id,
    )

    try:
        import artemis.db as _db
        from artemis.argus.flow import research_district

        async with _db.SessionLocal() as session:
            summary = await research_district(
                session,
                district_key=district_key,
                signal=signal,
                triggering_signal_id=triggering_signal_id,
            )
            await session.commit()
    except Exception as exc:
        _logger.error(
            "dispatch_research: research_district failed for district_key=%r -- %s",
            district_key,
            exc,
            exc_info=True,
        )
        return f"Argus ran into an error researching {district_key!r}: {exc}"

    # Pull the written findings back from the drawer for Callie to read
    findings_text = _format_dossier(district_key, summary)
    return findings_text


def _format_dossier(district_key: str, summary: dict[str, Any]) -> str:
    """Format the research summary into a human-readable dossier for Callie."""
    new_findings: int = summary.get("new_findings", 0)
    gap_dims: list[str] = summary.get("gap_dimensions", [])
    existing_dims: list[str] = summary.get("existing_dimensions", [])
    angle: str | None = summary.get("recommended_angle")

    lines: list[str] = [
        f"Argus research dossier: {district_key}",
        "",
        f"Researched {new_findings} new dimension(s): {', '.join(gap_dims) if gap_dims else 'none (all fresh)'}",
    ]
    if existing_dims:
        lines.append(f"Previously known: {', '.join(existing_dims)}")

    if angle:
        lines.append("")
        lines.append("Recommended angle:")
        lines.append(f"  {angle}")

    lines.append("")
    lines.append(
        "Findings are written to the district drawer (workspace:marketing scope). "
        "Source: Argus on all findings."
    )

    return "\n".join(lines)


# ── Registry helper ────────────────────────────────────────────────────────────


def register_argus_tools(registry: AuthorizedToolRegistry) -> None:
    """Register Argus tools into the provided registry.

    Called only when agent_id == 'callie' (enforced in tool_registry.py).
    Layer 1: Callie calls this without confirmation -- the tool reads from the
    drawer and triggers a research run, but all writes stay within the
    workspace:marketing scope she already has full access to.
    """
    registry.register(DISPATCH_RESEARCH, _dispatch_research, layer=1)
